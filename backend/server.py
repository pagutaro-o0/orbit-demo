from pathlib import Path
import io
import re
import sqlite3

import pandas as pd
from flask import Flask, send_from_directory, request, jsonify

# -----------------------------
# パス設定
# -----------------------------
BASE_DIR = Path(__file__).resolve().parent.parent   # surgery-cost/
DB_PATH = BASE_DIR / "surgDB.db"

app = Flask(__name__, static_folder=str(BASE_DIR), static_url_path="")


# -----------------------------
# 画面表示
# -----------------------------
@app.get("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


# （/cases.html, /app.js, /styles.css などを配信）
@app.get("/<path:path>")
def static_files(path):
    return send_from_directory(BASE_DIR, path)


# -----------------------------
# DB接続
# -----------------------------
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def table_columns(conn, table_name: str):
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {r["name"] for r in rows}

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS surg_cases (
      case_id TEXT PRIMARY KEY,
      patient_id TEXT,
      patient_name TEXT,
      surg_date TEXT,
      age INTEGER,
      dept TEXT,
      surg_procedure TEXT,
      disease TEXT,
      remarks TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS case_usage (
      usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
      case_id TEXT NOT NULL,
      free_item_name TEXT NOT NULL,
      quantity INTEGER,
      unit TEXT,
      memo TEXT,
      FOREIGN KEY (case_id) REFERENCES surg_cases(case_id)
    )
    """)

    cur.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS idx_case_usage_unique
    ON case_usage(case_id, free_item_name, memo)
    """)

    conn.commit()
    conn.close()

# -----------------------------
# ヘッダー定義（実CSVに合わせた）
# -----------------------------
REQUIRED_COLUMNS = [
    "症例ID",
    "患者番号",
    "患者氏名(漢字)",
    "年齢",
    "手術実施日",
    "実施診療科",
    "確定術式フリー検索",
    "術後病名",
    "リマークス（看護）",
]


def normalize_headers(df: pd.DataFrame) -> pd.DataFrame:
    # 全角空白→半角、前後空白除去
    df.columns = [str(c).replace("\u3000", " ").strip() for c in df.columns]
    return df


def validate_headers(df: pd.DataFrame):
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError("必須列が不足しています: " + ", ".join(missing))


def to_iso_date(val):
    if pd.isna(val):
        return None
    s = str(val).strip()
    if not s:
        return None
    dt = pd.to_datetime(s, errors="coerce")
    if pd.isna(dt):
        raise ValueError(f"手術実施日の形式が不正です: {s}")
    return dt.strftime("%Y-%m-%d")


def parse_int_safe(val):
    if pd.isna(val):
        return None
    s = str(val).strip()
    if s == "":
        return None
    m = re.search(r"\d+", s)
    return int(m.group()) if m else None


# -----------------------------
# surg_cases 用整形
# -----------------------------
def build_surg_cases(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame()
    out["case_id"] = df["症例ID"].astype(str).str.strip()
    out["patient_id"] = df["患者番号"].astype(str).str.strip()
    out["patient_name"] = df["患者氏名(漢字)"].astype(str).str.strip()
    out["surg_date"] = df["手術実施日"].apply(to_iso_date)
    out["age"] = df["年齢"].apply(parse_int_safe)
    out["dept"] = df["実施診療科"].astype(str).str.strip()
    out["surg_procedure"] = df["確定術式フリー検索"].astype(str).str.strip()
    out["disease"] = df["術後病名"].astype(str).str.strip()
    out["remarks"] = df["リマークス（看護）"].astype(str).fillna("").str.strip()

    # 空のcase_id除外
    out = out[out["case_id"] != ""].copy()

    # 同一症例IDが複数あれば先頭を採用
    out = out.drop_duplicates(subset=["case_id"], keep="first")
    return out


# -----------------------------
# case_usage 用整形（Rロジック寄せ）
# -----------------------------
def parse_usage_from_remarks(case_id: str, remarks: str):
    """
    例:
      ★サージセル[2]枚
      ★洗浄[生理食塩水250ml][1]本
      ★クリップ[3]個
    を抽出して case_usage 行にする
    """
    results = []

    if remarks is None or (isinstance(remarks, float) and pd.isna(remarks)):
        return results

    text = str(remarks)

    # 半角/全角カンマ/読点区切り
    parts = re.split(r"[,\u3001，]", text)

    for p in parts:
        p = p.strip()
        if not p.startswith("★"):
            continue

        memo = p

        # 末尾の [数値] + 単位 を quantity/unit として取得
        # 例: ★洗浄[生理食塩水250ml][1]本
        m = re.match(r"^★\s*(.*?)(?:\[(\d+(?:\.\d+)?)\])\s*([^\]]*)\s*$", p)
        if m:
            left = (m.group(1) or "").strip()
            qty_str = m.group(2)
            unit = (m.group(3) or "").strip() or None

            # free_item_name は最初の [ の前を使う（Rコード寄せ）
            item_name = left.split("[")[0].strip()

            quantity = float(qty_str) if "." in qty_str else int(qty_str)

            if item_name:
                results.append({
                    "case_id": case_id,
                    "free_item_name": item_name,
                    "quantity": quantity,
                    "unit": unit,
                    "memo": memo,
                })
            continue

        # フォールバック（[]がない/崩れている）
        m2 = re.match(r"^★\s*(.*?)\s*$", p)
        if m2:
            item_name = (m2.group(1) or "").strip()
            if item_name:
                results.append({
                    "case_id": case_id,
                    "free_item_name": item_name,
                    "quantity": None,
                    "unit": None,
                    "memo": memo,
                })

    return results


def build_case_usage(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, r in df.iterrows():
        case_id = str(r["症例ID"]).strip()
        if not case_id:
            continue
        rows.extend(parse_usage_from_remarks(case_id, r.get("リマークス（看護）", "")))

    out = pd.DataFrame(rows, columns=["case_id", "free_item_name", "quantity", "unit", "memo"])
    if len(out) == 0:
        return out

    # 完全重複を除外
    out = out.drop_duplicates(subset=["case_id", "free_item_name", "memo"], keep="first")
    return out


# -----------------------------
# CSVインポートAPI
# -----------------------------
@app.post("/api/import-csv")
def import_csv():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "ファイルが選択されていません"}), 400

    f = request.files["file"]
    if not f.filename.lower().endswith(".csv"):
        return jsonify({"ok": False, "error": "CSVファイルを選択してください"}), 400

    try:
        # Shift_JIS（Windows系CSVは cp932）
        raw = f.read()
        text = raw.decode("cp932")
        df = pd.read_csv(io.StringIO(text), dtype=str)

        df = normalize_headers(df)
        validate_headers(df)

        surg_cases_df = build_surg_cases(df)
        case_usage_df = build_case_usage(df)

        conn = get_conn()
        cur = conn.cursor()

        try:
            cur.execute("BEGIN")

            cols = table_columns(conn, "surg_cases")
            has_remarks = "remarks" in cols

            # surg_cases: case_id で UPSERT
            for _, row in surg_cases_df.iterrows():
                if has_remarks:
                    cur.execute("""
                        INSERT INTO surg_cases
                        (case_id, patient_id, patient_name, surg_date, age, dept, surg_procedure, disease, remarks)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(case_id) DO UPDATE SET
                            patient_id=excluded.patient_id,
                            patient_name=excluded.patient_name,
                            surg_date=excluded.surg_date,
                            age=excluded.age,
                            dept=excluded.dept,
                            surg_procedure=excluded.surg_procedure,
                            disease=excluded.disease,
                            remarks=excluded.remarks
                    """, (
                        row["case_id"],
                        row["patient_id"],
                        row["patient_name"],
                        row["surg_date"],
                        row["age"],
                        row["dept"],
                        row["surg_procedure"],
                        row["disease"],
                        row["remarks"],
                    ))
                else:
                    cur.execute("""
                        INSERT INTO surg_cases
                        (case_id, patient_id, patient_name, surg_date, age, dept, surg_procedure, disease)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(case_id) DO UPDATE SET
                            patient_id=excluded.patient_id,
                            patient_name=excluded.patient_name,
                            surg_date=excluded.surg_date,
                            age=excluded.age,
                            dept=excluded.dept,
                            surg_procedure=excluded.surg_procedure,
                            disease=excluded.disease
                    """, (
                        row["case_id"],
                        row["patient_id"],
                        row["patient_name"],
                        row["surg_date"],
                        row["age"],
                        row["dept"],
                        row["surg_procedure"],
                        row["disease"],
                    ))

            # case_usage: 対象case_idを一旦削除して再登録（重複防止）
            target_case_ids = surg_cases_df["case_id"].astype(str).tolist()
            if target_case_ids:
                placeholders = ",".join(["?"] * len(target_case_ids))
                cur.execute(f"DELETE FROM case_usage WHERE case_id IN ({placeholders})", target_case_ids)

            for _, row in case_usage_df.iterrows():
                cur.execute("""
                    INSERT INTO case_usage (case_id, free_item_name, quantity, unit, memo)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    row["case_id"],
                    row["free_item_name"],
                    str(row["quantity"]) if pd.notna(row["quantity"]) else None,
                    row["unit"],
                    row["memo"],
                ))

            conn.commit()

        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        return jsonify({
            "ok": True,
            "message": "CSVインポート完了",
            "imported_cases": int(len(surg_cases_df)),
            "imported_usage_rows": int(len(case_usage_df)),
        })

    except UnicodeDecodeError:
        return jsonify({"ok": False, "error": "文字コードの読み取りに失敗しました（Shift_JIS / CP932想定）"}), 400
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"インポート処理でエラー: {e}"}), 500


# -----------------------------
# 症例一覧API（追加）
# -----------------------------
@app.get("/api/cases")
def api_cases():
    try:
        conn = get_conn()
        cols = table_columns(conn, "surg_cases")

        has_remarks = "remarks" in cols
        has_deleted = "deleted" in cols

        # 列がない環境でも落ちないように SELECT を組み立てる
        select_sql = f"""
            SELECT
              case_id,
              patient_id,
              patient_name,
              surg_date,
              age,
              dept,
              disease,
              surg_procedure,
              {"COALESCE(remarks, '') AS remarks" if has_remarks else "'' AS remarks"},
              {"COALESCE(deleted, 0) AS deleted" if has_deleted else "0 AS deleted"}
            FROM surg_cases
            ORDER BY surg_date DESC, patient_id ASC
        """
        rows = conn.execute(select_sql).fetchall()
        conn.close()

        cases = []
        for r in rows:
            cases.append({
                "case_id": r["case_id"],
                "patient_id": r["patient_id"],
                "patient_name": r["patient_name"],
                "surg_date": r["surg_date"],
                "age": r["age"],
                "dept": r["dept"],
                "disease": r["disease"],
                "surg_procedure": r["surg_procedure"],
                "remarks": r["remarks"],
                "deleted": bool(r["deleted"]),
            })

        return jsonify({"ok": True, "cases": cases})

    except Exception as e:
        return jsonify({"ok": False, "error": f"/api/cases エラー: {e}"}), 500


# -----------------------------
# 消耗品一覧API（追加）
# -----------------------------
@app.get("/api/case-usage")
def api_case_usage_get():
    case_id = (request.args.get("case_id") or "").strip()
    if not case_id:
        return jsonify({"ok": False, "error": "case_id is required"}), 400

    try:
        conn = get_conn()
        rows = conn.execute("""
            SELECT
              case_id,
              COALESCE(free_item_name, '') AS free_item_name,
              COALESCE(quantity, 0) AS quantity,
              COALESCE(unit, '') AS unit,
              COALESCE(memo, '') AS memo
            FROM case_usage
            WHERE CAST(case_id AS TEXT) = ?
            ORDER BY rowid
        """, (str(case_id),)).fetchall()
        conn.close()

        out = []
        for r in rows:
            out.append({
                "case_id": r["case_id"],
                "free_item_name": r["free_item_name"],
                "quantity": r["quantity"],
                "unit": r["unit"],
                "memo": r["memo"],
            })

        return jsonify({"ok": True, "rows": out})

    except Exception as e:
        return jsonify({"ok": False, "error": f"/api/case-usage(GET) エラー: {e}"}), 500


# -----------------------------
# 消耗品保存API（追加・全置換）
# -----------------------------
@app.post("/api/case-usage")
def api_case_usage_post():
    case_id = (request.args.get("case_id") or "").strip()
    if not case_id:
        return jsonify({"ok": False, "error": "case_id is required"}), 400

    try:
        data = request.get_json(silent=True) or {}
        rows = data.get("rows", [])
        if not isinstance(rows, list):
            return jsonify({"ok": False, "error": "rows must be a list"}), 400

        conn = get_conn()
        cur = conn.cursor()

        # その症例の既存行を削除
        cur.execute("DELETE FROM case_usage WHERE CAST(case_id AS TEXT) = ?", (str(case_id),))

        # 再登録
        for x in rows:
            free_item_name = str(x.get("free_item_name", "")).strip()
            if not free_item_name:
                continue

            q = x.get("quantity", 0)
            try:
                # 小数が来ても一旦float→intに寄せる（必要ならfloat保存に変えてOK）
                quantity = int(float(q))
            except Exception:
                quantity = 0

            unit = str(x.get("unit", "")).strip()
            memo = str(x.get("memo", "")).strip()

            cur.execute("""
                INSERT INTO case_usage (case_id, free_item_name, quantity, unit, memo)
                VALUES (?, ?, ?, ?, ?)
            """, (str(case_id), free_item_name, quantity, unit, memo))

        conn.commit()
        conn.close()

        return jsonify({"ok": True, "message": "saved"})

    except Exception as e:
        return jsonify({"ok": False, "error": f"/api/case-usage(POST) エラー: {e}"}), 500


# -----------------------------
# 動作確認用（任意）
# -----------------------------
@app.get("/api/health")
def health():
    return jsonify({"ok": True, "db_path": str(DB_PATH)})


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)