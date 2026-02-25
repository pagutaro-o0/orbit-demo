/* ========= Helpers ========= */
const qs  = (s, el = document) => el.querySelector(s);
const qsa = (s, el = document) => Array.from(el.querySelectorAll(s));

function todayISO() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${dd}`;
}

function toJPDate(iso) {
  if (!iso) return "";
  const [y, m, d] = String(iso).split("-");
  return `${Number(y)}/${Number(m)}/${Number(d)}`;
}

function getUrlParam(key) {
  const u = new URL(location.href);
  return u.searchParams.get(key);
}

/* ========= Header ========= */
function renderAppHeader({ active = "cases" } = {}) {
  const el = qs("#appHeader");
  if (!el) return;

  const a = (key) => (key === active ? "active" : "");
  el.innerHTML = `
    <div class="topbar">
      <div class="logo">📈</div>
      <div class="app-title">ORBIT</div>
      <div class="nav">
        <a class="${a("import")}" href="./index.html">データインポート</a>
        <a class="${a("cases")}" href="./cases.html">患者一覧</a>
      </div>
    </div>
  `;
}

/* ========= 互換用（旧HTMLから呼ばれても落ちないように） ========= */
function seedIfEmpty() {
  // localStorage廃止済みのため何もしない
}

/* ========= API =========
   サーバー側で以下のAPIがある前提：
   - GET  /api/cases
   - GET  /api/case-usage?case_id=...
   - POST /api/case-usage?case_id=...
*/
async function apiGetCases() {
  const res = await fetch("/api/cases");
  let data = {};
  try {
    data = await res.json();
  } catch {
    throw new Error("症例一覧APIの応答が不正です");
  }
  if (!res.ok || !data.ok) {
    throw new Error(data.error || "症例一覧の取得に失敗しました");
  }
  return Array.isArray(data.cases) ? data.cases : [];
}

async function apiGetUsageByCaseId(caseId) {
  const res = await fetch(`/api/case-usage?case_id=${encodeURIComponent(caseId)}`);
  let data = {};
  try {
    data = await res.json();
  } catch {
    throw new Error("消耗品一覧APIの応答が不正です");
  }
  if (!res.ok || !data.ok) {
    throw new Error(data.error || "消耗品一覧の取得に失敗しました");
  }

  const rows = Array.isArray(data.rows) ? data.rows : [];
  return rows.map((u) => ({
    ...u,
    // 旧キー(item_name)が来ても表示できるように互換対応
    free_item_name: String(u.free_item_name || u.item_name || "").trim(),
    quantity: Number(u.quantity) || 0,
    unit: String(u.unit || "").trim(),
    memo: String(u.memo || "").trim(),
  }));
}

async function apiSetUsageForCaseId(caseId, lines) {
  const normalized = (lines || [])
    .map((l) => ({
      free_item_name: String(l.free_item_name || l.item_name || "").trim(),
      quantity: Number(l.quantity) || 0,
      unit: String(l.unit || "").trim(),
      memo: String(l.memo || "").trim(),
    }))
    .filter((x) => x.free_item_name !== "");

  const res = await fetch(`/api/case-usage?case_id=${encodeURIComponent(caseId)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rows: normalized }),
  });

  let data = {};
  try {
    data = await res.json();
  } catch {
    throw new Error("消耗品保存APIの応答が不正です");
  }
  if (!res.ok || !data.ok) {
    throw new Error(data.error || "消耗品の保存に失敗しました");
  }
  return data;
}

/* ========= 旧名互換ラッパー（既存HTMLの呼び出し名を維持） ========= */
async function getCases() {
  return await apiGetCases();
}

async function getUsageByCaseId(caseId) {
  return await apiGetUsageByCaseId(caseId);
}

async function setUsageForCaseId(caseId, lines) {
  return await apiSetUsageForCaseId(caseId, lines);
}

/* ========= CSV parsing（フロント互換用：今は主に未使用） ========= */
function parseCSV(text) {
  const rows = [];
  let cur = "";
  let inQ = false;
  const line = [];

  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    const next = text[i + 1];

    if (ch === '"') {
      if (inQ && next === '"') {
        cur += '"';
        i++;
      } else {
        inQ = !inQ;
      }
      continue;
    }
    if (!inQ && ch === ",") {
      line.push(cur);
      cur = "";
      continue;
    }
    if (!inQ && ch === "\n") {
      line.push(cur);
      cur = "";
      rows.push(line.splice(0));
      continue;
    }
    if (ch === "\r") continue;
    cur += ch;
  }

  if (cur.length || line.length) {
    line.push(cur);
    rows.push(line.splice(0));
  }
  return rows;
}

function normHeader(s) {
  return (s || "")
    .replace(/\uFEFF/g, "") // BOM除去
    .replace(/[ 　]+/g, "") // 半角/全角スペース除去
    .trim();
}

function toISODateFromMaybeJP(s) {
  const t = (s || "").trim();
  if (!t) return "";

  if (t.includes("-")) {
    const [y, m, d] = t.split("-");
    return `${y.padStart(4, "0")}-${m.padStart(2, "0")}-${d.padStart(2, "0")}`;
  }
  if (t.includes("/")) {
    const [y, m, d] = t.split("/");
    return `${y.padStart(4, "0")}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
  }
  return t;
}

function buildHeaderIndex(headerRow) {
  const header = headerRow.map(normHeader);
  const map = new Map();
  header.forEach((h, i) => map.set(h, i));

  const find = (...cands) => {
    for (const c of cands) {
      const k = normHeader(c);
      if (map.has(k)) return map.get(k);
    }
    return -1;
  };

  const COL = {
    case_id: find("症例ID", "症例ＩＤ", "case_id", "caseId"),
    patient_id: find("患者番号", "患者ID", "patient_id", "patientId"),
    patient_name: find("患者氏名（漢字）", "患者氏名(漢字)", "患者氏名", "patient_name", "patientName"),
    surg_date: find("手術実施日", "手術日", "実施日", "surg_date", "surgDate"),
    age: find("年齢", "age"),
    dept: find("実施診療科", "診療科", "dept"),
    surg_procedure: find("確定術式フリー検索", "確定術式", "術式", "surg_procedure", "procedure"),
    disease: find("術後病名", "病名", "disease"),
    remarks: find("リマークス（看護）", "リマークス(看護)", "リマークス", "remarks"),
  };

  const required = [
    "case_id",
    "patient_id",
    "patient_name",
    "surg_date",
    "age",
    "dept",
    "surg_procedure",
    "disease",
  ];
  for (const k of required) {
    if (COL[k] === -1) throw new Error(`CSV見出しが見つかりません: ${k}`);
  }
  return COL;
}

function normalizeCaseId(v) {
  return String(v ?? "").trim();
}

/* 旧実装互換のため残す（現在はサーバーimportを使う前提で未使用） */
function importCasesFromCSV(csvText) {
  const rows = parseCSV(csvText);
  if (rows.length < 2) throw new Error("CSVにデータがありません");

  const COL = buildHeaderIndex(rows[0]);

  const imported = [];
  for (let i = 1; i < rows.length; i++) {
    const r = rows[i];
    if (r.length === 1 && !r[0]) continue;

    const caseIdRaw = normalizeCaseId(r[COL.case_id]);
    if (!caseIdRaw) continue;

    imported.push({
      case_id: caseIdRaw,
      patient_id: Number(String(r[COL.patient_id] || "").trim()) || String(r[COL.patient_id] || "").trim(),
      patient_name: String(r[COL.patient_name] || "").trim(),
      surg_date: toISODateFromMaybeJP(String(r[COL.surg_date] || "")),
      age: Number(String(r[COL.age] || "").trim()) || null,
      dept: String(r[COL.dept] || "").trim(),
      surg_procedure: String(r[COL.surg_procedure] || "").trim(),
      disease: String(r[COL.disease] || "").trim(),
      remarks: COL.remarks !== -1 ? String(r[COL.remarks] || "").trim() : "",
      deleted: false,
    });
  }

  // localStorage廃止後はここを使わない想定
  // 互換性のため件数だけ返す
  return { imported: imported.length, total: imported.length };
}

/* ========= Remarks utility ========= */
// ★付きだけ抽出して [{name, qty}] を返す
function extractStarNameQty(text) {
  const t = String(text || "");
  const re = /★\s*([^\n★]+)/g; // ★から次の★/改行まで
  const out = [];
  let m;

  while ((m = re.exec(t)) !== null) {
    const block = m[1].trim(); // 例: 生理食塩水250ml[[1]本,標本摘出...]

    const name = block
      .split("[[")[0]
      .split(",")[0]
      .trim();

    let qty = 1;
    const q = block.match(/\[\[\s*\[?\s*(\d+)\s*\]?\s*[^\]]*\]\]/);
    if (q) qty = Number(q[1]);

    if (name) out.push({ name, qty });
  }

  // 同名は合算
  const map = new Map();
  for (const x of out) {
    map.set(x.name, (map.get(x.name) || 0) + x.qty);
  }
  return Array.from(map.entries()).map(([name, qty]) => ({ name, qty }));
}

/* =========================================================
   Flask API連携（インポート画面）
   - index.html で「インポートする」ボタンがあればAPI呼び出しを差し替え
   ========================================================= */
document.addEventListener("DOMContentLoaded", () => {
  const path = (location.pathname || "").toLowerCase();
  const isImportPage = path.endsWith("/index.html") || path === "/" || path.endsWith("/");
  renderAppHeader({ active: isImportPage ? "import" : "cases" });

  const fileInput = qs("#csvFile") || qs('input[type="file"]');

  // 「インポートする」ボタン
  const importBtn =
    qs("#importBtn") ||
    qsa("button").find((b) => (b.textContent || "").includes("インポートする"));

  const patientListBtn =
    qs("#goPatientsBtn") ||
    qsa("button").find((b) => (b.textContent || "").includes("患者一覧へ"));

  // 結果表示エリア
  let resultBox = qs("#importResult");
  if (!resultBox && importBtn) {
    resultBox = document.createElement("div");
    resultBox.id = "importResult";
    resultBox.style.marginTop = "12px";
    resultBox.style.padding = "10px 12px";
    resultBox.style.border = "1px solid #2c3e66";
    resultBox.style.background = "#fff";
    resultBox.style.whiteSpace = "pre-wrap";
    resultBox.style.fontSize = "14px";

    const parent = importBtn.parentElement || document.body;
    parent.appendChild(resultBox);
  }

  function setResult(msg, isError = false) {
    if (!resultBox) return;
    resultBox.textContent = msg;
    resultBox.style.color = isError ? "#b00020" : "#111";
  }

  // インポートページ以外では何もしない
  if (!fileInput || !importBtn) return;

  importBtn.addEventListener("click", async (e) => {
    e.preventDefault();

    if (!fileInput.files || fileInput.files.length === 0) {
      setResult("CSVファイルを選択してください。", true);
      return;
    }

    const file = fileInput.files[0];
    const formData = new FormData();
    formData.append("file", file);

    const originalText = importBtn.textContent;
    importBtn.disabled = true;
    importBtn.textContent = "インポート中...";

    try {
      const res = await fetch("/api/import-csv", {
        method: "POST",
        body: formData,
      });

      let data = {};
      try {
        data = await res.json();
      } catch {
        throw new Error("サーバー応答をJSONとして読めませんでした");
      }

      if (!res.ok || !data.ok) {
        throw new Error(data.error || "インポートに失敗しました");
      }

      setResult(
        `✅ ${data.message || "CSVインポート完了"}\n症例: ${data.imported_cases ?? 0}件\n物品: ${data.imported_usage_rows ?? 0}件`,
        false
      );
    } catch (err) {
      console.error(err);
      setResult(`❌ ${err.message || "インポートに失敗しました"}`, true);
    } finally {
      importBtn.disabled = false;
      importBtn.textContent = originalText;
    }
  });

  // 「患者一覧へ」ボタン
  if (patientListBtn) {
    patientListBtn.addEventListener("click", (e) => {
      e.preventDefault();
      location.href = "./cases.html";
    });
  }
});