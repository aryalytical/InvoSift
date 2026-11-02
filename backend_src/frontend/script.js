"use strict";

/* ------------------------------------------------------------------ helpers */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let toastTimer = null;
function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (isErr ? " err" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), isErr ? 6000 : 2800);
}

async function api(path, options = {}) {
  let res;
  try {
    res = await fetch(path, options);
  } catch (e) {
    throw new Error("Cannot reach the backend. Make sure the application is running (python run.py).");
  }
  if (!res.ok) {
    let detail = `Request failed (${res.status}).`;
    try { const j = await res.json(); if (j.detail) detail = typeof j.detail === "string" ? j.detail : detail; } catch (_) {}
    const err = new Error(detail); err.status = res.status; throw err;
  }
  return res;
}
const apiJson = async (path, options) => (await api(path, options)).json();

async function download(url, fallbackName) {
  try {
    const res = await api(url);
    const cd = res.headers.get("Content-Disposition") || "";
    const m = /filename="?([^";]+)"?/.exec(cd);
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = m ? m[1] : fallbackName;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  } catch (e) { toast(e.message, true); }
}

const money = (v, cur) => (v == null ? "-" : (cur ? cur + " " : "") + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
function statusBadge(s) {
  const map = { completed: ["ok", "Extracted"], needs_review: ["warn", "Needs Review"], failed: ["err", "Failed"], processing: ["proc", "Processing"] };
  const [cls, label] = map[s] || ["proc", s];
  return `<span class="badge ${cls}">${label}</span>`;
}

/* ------------------------------------------------------------------ routing */
const views = ["upload", "dashboard", "detail", "settings"];
function show(view) {
  views.forEach((v) => ($("#view-" + v).hidden = v !== view));
  $$("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === (view === "detail" ? "dashboard" : view)));
}
async function route() {
  const hash = location.hash || "#/upload";
  const [path, query] = hash.slice(2).split("?");
  const parts = path.split("/");
  if (parts[0] === "dashboard") { show("dashboard"); await loadDashboard(); }
  else if (parts[0] === "settings") { show("settings"); await loadStatus(); }
  else if (parts[0] === "invoice" && parts[1]) { show("detail"); await loadDetail(Number(parts[1]), /edit=1/.test(query || "")); }
  else { show("upload"); }
}
window.addEventListener("hashchange", route);

/* ------------------------------------------------------------------ health */
const STATUS_ROWS = [
  ["backend", "Backend", "Running", "Not running"],
  ["ollama", "Ollama", "Connected", "Not reachable"],
  ["model", "Vision Model", "Available", "Not installed"],
  ["ocr", "OCR", "Available", "Not available"],
  ["database", "Database", "Connected", "Error"],
];
async function getHealth() {
  try { return await apiJson("/api/health"); } catch (_) { return { backend: false, ollama: false, model: false, ocr: false, database: false, details: {} }; }
}
async function refreshDot() {
  const h = await getHealth();
  const ok = h.backend && h.ollama && h.model && h.ocr && h.database;
  const dot = $("#healthDot");
  dot.className = "health-dot " + (ok ? "ok" : "bad");
  dot.title = ok ? "All systems ready" : "Something needs attention - open Status";
}
async function loadStatus() {
  $("#statusList").innerHTML = `<li><span><span class="spin"></span>Checking...</span></li>`;
  const h = await getHealth();
  $("#statusList").innerHTML = STATUS_ROWS.map(([k, label, good, bad]) =>
    `<li><span>${label}</span><span class="${h[k] ? "good" : "bad"}">${h[k] ? "&#10003; " + good : "&#10007; " + bad}</span></li>`).join("");
  const d = h.details || {};
  const help = [];
  if (!h.backend) help.push("The backend is not reachable. Start it with:  python run.py");
  if (h.backend && !h.ollama) help.push(`Ollama is not running at ${d.ollama_host || "http://localhost:11434"}.\nPlease start Ollama (open the Ollama app, or run "ollama serve").`);
  if (h.ollama && !h.model) help.push(`The model "${d.model_name}" is not installed.\nRun:  ollama pull ${d.model_name}`);
  if (h.backend && !h.ocr) help.push("No OCR engine found. Run:  pip install paddleocr paddlepaddle   (or install Tesseract).");
  if (h.backend && !h.database) help.push("The database could not be opened. Check the DATABASE_PATH setting and folder permissions.");
  const box = $("#statusHelp");
  box.textContent = help.join("\n\n");
  box.classList.toggle("show", help.length > 0);
  if (h.backend && d.model_name) {
    $("#statusList").insertAdjacentHTML("beforeend",
      `<li><span class="muted">Model in use</span><span>${esc(d.model_name)}</span></li><li><span class="muted">OCR engine</span><span>${esc(d.ocr_engine || "none")}</span></li>`);
  }
  refreshDot();
}
$("#recheckBtn").addEventListener("click", loadStatus);

/* ------------------------------------------------------------------ upload */
const dz = $("#dropzone"), fileInput = $("#fileInput");
$("#chooseBtn").addEventListener("click", (e) => { e.stopPropagation(); fileInput.click(); });
dz.addEventListener("click", () => fileInput.click());
dz.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
dz.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) uploadFile(f); });
fileInput.addEventListener("change", () => { if (fileInput.files[0]) uploadFile(fileInput.files[0]); fileInput.value = ""; });

let uploading = false;
async function uploadFile(file) {
  if (uploading) return;
  const box = $("#uploadStatus");
  const ext = (file.name.split(".").pop() || "").toLowerCase();
  if (!["pdf", "png", "jpg", "jpeg"].includes(ext)) {
    box.innerHTML = `<div class="msg-err">Unsupported file type. Please upload a PDF, PNG, JPG or JPEG file.</div>`; return;
  }
  uploading = true;
  const t0 = Date.now();
  box.innerHTML = `<div class="progress"><strong><span class="spin"></span>Extracting invoice data...</strong>
    <div class="bar"><i></i></div><div class="muted" id="progMsg">Uploading and reading ${esc(file.name)}</div></div>`;
  const timer = setInterval(() => {
    const s = Math.round((Date.now() - t0) / 1000);
    const m = $("#progMsg");
    if (m) m.textContent = `Reading text (OCR) and analysing with the local AI model... ${s}s elapsed. Local models can take a minute or more.`;
  }, 1000);
  const fd = new FormData(); fd.append("file", file);
  try {
    const inv = await apiJson("/api/invoices/upload", { method: "POST", body: fd });
    box.innerHTML = "";
    toast(inv.needs_review ? "Extracted - please review the highlighted items" : "Invoice extracted successfully");
    location.hash = `#/invoice/${inv.id}`;
  } catch (e) {
    box.innerHTML = `<div class="msg-err">${esc(e.message)}</div>`;
    refreshDot();
  } finally { clearInterval(timer); uploading = false; }
}

/* ------------------------------------------------------------------ dashboard */
let searchTimer = null;
$("#searchBox").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadDashboard, 250); });
$("#exportAllBtn").addEventListener("click", () => download("/api/invoices/export/excel", "invoices_consolidated.xlsx"));
$("#exportAllCsvBtn").addEventListener("click", () => download("/api/invoices/export/csv", "invoices_all.csv"));

async function loadDashboard() {
  try {
    const q = $("#searchBox").value.trim();
    const [stats, list] = await Promise.all([apiJson("/api/stats"), apiJson("/api/invoices" + (q ? `?search=${encodeURIComponent(q)}` : ""))]);
    const cards = [
      ["Total Invoices", stats.total_invoices], ["Processed Today", stats.processed_today],
      ["Successful Extractions", stats.successful_extractions], ["Needs Review", stats.needs_review],
      ["Total Invoice Value", money(stats.total_invoice_value)],
    ];
    $("#statCards").innerHTML = cards.map(([l, v]) => `<div class="card"><div class="label">${l}</div><div class="value">${esc(v)}</div></div>`).join("");
    $("#historyTable tbody").innerHTML = list.map((i) => `
      <tr data-id="${i.id}">
        <td>${esc(i.invoice_number || "-")}<div class="muted" style="font-size:12px">${esc(i.original_filename)}</div></td>
        <td>${esc(i.supplier_name || "-")}</td><td>${esc(i.invoice_date || "-")}</td>
        <td class="num">${money(i.grand_total, i.currency)}</td><td>${statusBadge(i.status)}</td>
        <td><div class="actions">
          <a class="btn small" href="#/invoice/${i.id}">View</a>
          <a class="btn small" href="#/invoice/${i.id}?edit=1">Edit</a>
          <select class="btn small exp" aria-label="Export invoice ${i.id}">
            <option value="">Export...</option><option value="json">JSON</option><option value="csv">CSV</option><option value="excel">Excel</option>
          </select>
          <button class="btn small danger del">Delete</button></div></td>
      </tr>`).join("");
    $("#historyEmpty").hidden = list.length > 0;
    $("#historyTable").hidden = list.length === 0;
    $$("#historyTable select.exp").forEach((sel) => sel.addEventListener("change", () => {
      const id = sel.closest("tr").dataset.id; const kind = sel.value; sel.value = "";
      if (kind) download(`/api/invoices/${id}/export/${kind}`, `invoice_${id}.${kind === "excel" ? "xlsx" : kind}`);
    }));
    $$("#historyTable button.del").forEach((b) => b.addEventListener("click", () => deleteInvoice(Number(b.closest("tr").dataset.id), loadDashboard)));
  } catch (e) { toast(e.message, true); }
}

async function deleteInvoice(id, after) {
  if (!confirm("Delete this invoice and its stored file? This cannot be undone.")) return;
  try {
    await api(`/api/invoices/${id}`, { method: "DELETE" });
    toast("Invoice deleted");
    after();
  } catch (e) { toast(e.message, true); }
}

/* ------------------------------------------------------------------ detail */
const SECTIONS = [
  ["Invoice", [["invoice_number", "Invoice number"], ["invoice_date", "Invoice date"], ["due_date", "Due date"],
    ["purchase_order_number", "Purchase order no."], ["reference_number", "Reference no."], ["currency", "Currency"],
    ["invoice_type", "Invoice type"], ["payment_terms", "Payment terms", "area"]]],
  ["Supplier", [["supplier.name", "Name"], ["supplier.tax_id", "Tax ID / GSTIN"], ["supplier.registration_number", "Registration no."],
    ["supplier.email", "Email"], ["supplier.phone", "Phone"], ["supplier.address", "Address", "area"]]],
  ["Customer", [["customer.name", "Name"], ["customer.tax_id", "Tax ID / GSTIN"], ["customer.registration_number", "Registration no."],
    ["customer.email", "Email"], ["customer.phone", "Phone"], ["customer.address", "Address", "area"]]],
  ["Financials", [["financials.subtotal", "Subtotal", "num"], ["financials.taxable_amount", "Taxable amount", "num"], ["financials.discount", "Discount", "num"],
    ["financials.cgst", "CGST", "num"], ["financials.sgst", "SGST", "num"], ["financials.igst", "IGST", "num"], ["financials.tax", "Total tax", "num"],
    ["financials.other_charges", "Other charges", "num"], ["financials.shipping", "Shipping", "num"], ["financials.round_off", "Round-off", "num"],
    ["financials.grand_total", "Grand total", "num"], ["financials.amount_paid", "Amount paid", "num"], ["financials.balance_due", "Balance due", "num"]]],
];
const ITEM_COLS = [["item_code", "Item code", "text"], ["description", "Description", "text"], ["hsn_sac", "HSN/SAC", "text"],
  ["quantity", "Qty", "num"], ["unit", "Unit", "text"], ["unit_price", "Unit price", "num"], ["discount", "Discount", "num"],
  ["tax_rate", "Tax %", "num"], ["tax", "Tax", "num"], ["total", "Total", "num"]];
const getPath = (o, p) => p.split(".").reduce((a, k) => (a == null ? null : a[k]), o);
const setPath = (o, p, v) => { const ks = p.split("."); const last = ks.pop(); ks.reduce((a, k) => a[k], o)[last] = v; };

let current = null, editing = false;

async function loadDetail(id, startEditing) {
  $("#infoPanel").innerHTML = `<div class="muted"><span class="spin"></span>Loading...</div>`;
  $("#detailActions").innerHTML = ""; $("#previewBox").innerHTML = "";
  try {
    current = await apiJson(`/api/invoices/${id}`);
  } catch (e) {
    $("#infoPanel").innerHTML = `<div class="msg-err">${esc(e.message)}</div>`; return;
  }
  editing = !!startEditing && current.status !== "failed";
  renderPreview(current);
  renderDetail();
}

function renderPreview(inv) {
  const box = $("#previewBox");
  $("#pageInfo").textContent = inv.page_count > 1 ? `${inv.page_count} pages` : "";
  const isPdf = /\.pdf$/i.test(inv.original_filename);
  if (isPdf) {
    box.innerHTML = Array.from({ length: inv.page_count }, (_, n) =>
      `<div class="pg">Page ${n + 1}</div><img loading="lazy" alt="Invoice page ${n + 1}" src="/api/invoices/${inv.id}/page/${n + 1}">`).join("");
  } else {
    box.innerHTML = `<img alt="Invoice image" src="/api/invoices/${inv.id}/file">`;
  }
}

function renderDetail() {
  const inv = current, d = inv.data, flags = (inv.validation && inv.validation.field_flags) || {};
  const failed = inv.status === "failed";

  $("#detailActions").innerHTML = failed
    ? `<button class="btn danger" id="delBtn">Delete</button>`
    : (editing
      ? `<button class="btn primary" id="saveBtn">Save changes</button><button class="btn" id="cancelBtn">Cancel</button>`
      : `<button class="btn" id="editBtn">Edit</button>`) +
      `<button class="btn" data-exp="json">Export JSON</button><button class="btn" data-exp="csv">Export CSV</button>
       <button class="btn" data-exp="excel">Export Excel</button><button class="btn danger" id="delBtn">Delete</button>`;

  let html = `<div class="panel-head"><h2>Extracted information</h2>${statusBadge(inv.status)}</div>`;
  html += `<div class="muted" style="margin:-6px 0 12px;font-size:12.5px">${esc(inv.original_filename)}`
    + (inv.ocr_engine ? ` &middot; OCR: ${esc(inv.ocr_engine)}` + (inv.ocr_mean_confidence != null ? ` (avg. text confidence ${Math.round(inv.ocr_mean_confidence * 100)}%)` : "") : "")
    + (inv.manually_edited ? " &middot; manually corrected" : "") + `</div>`;
  const timing = inv.processing_times || {};
  if (Object.keys(timing).length) {
    html += `<div class="section"><h3>Processing time</h3><div class="timing-grid">${Object.entries(timing).map(([k,v]) => `<div><span>${esc(k.replaceAll("_", " "))}</span><strong>${(Number(v)/1000).toFixed(2)}s</strong></div>`).join("")}</div></div>`;
  }

  if (failed) {
    html += `<div class="msg-err" style="max-width:none;margin:0">${esc(inv.error_message || "Processing failed.")}</div>`;
    $("#infoPanel").innerHTML = html;
    $("#delBtn").addEventListener("click", () => deleteInvoice(inv.id, () => (location.hash = "#/dashboard")));
    return;
  }

  // Validation
  const issues = inv.validation.issues || [];
  html += `<div class="section"><h3>Validation ${inv.needs_review ? '<span class="badge warn">Needs Review</span>' : '<span class="badge ok">Looks good</span>'}</h3>`;
  html += issues.length ? `<ul class="issues">${issues.map((i) => `<li class="${i.level}">${i.level === "ok" ? "&#10003;" : "&#9888;"} ${esc(i.message)}</li>`).join("")}</ul>`
    : `<div class="muted">No validation results.</div>`;
  html += `</div>`;

  // Fields
  for (const [title, fields] of SECTIONS) {
    html += `<div class="section"><h3>${title}</h3><div class="grid2">`;
    for (const [path, label, kind] of fields) {
      const val = getPath(d, path);
      const cls = "field" + (flags[path] === "review" ? " review" : "") + (kind === "area" ? " span2" : "");
      const input = kind === "area"
        ? `<textarea rows="2" data-path="${path}" ${editing ? "" : "disabled"}>${esc(val)}</textarea>`
        : `<input data-path="${path}" data-kind="${kind || "text"}" value="${esc(val)}" ${kind === "num" ? 'inputmode="decimal"' : ""} ${editing ? "" : "disabled"}>`;
      html += `<div class="${cls}"><label>${label}</label>${input}</div>`;
    }
    html += `</div></div>`;
  }

  // Items
  html += `<div class="section"><h3>Line items (${d.items.length})</h3><div class="table-wrap"><table class="data items-table" id="itemsTable"><thead><tr>`
    + ITEM_COLS.map(([, l, k]) => `<th class="${k === "num" ? "num" : ""}">${l}</th>`).join("") + (editing ? "<th></th>" : "") + `</tr></thead><tbody>`
    + d.items.map((it, n) => itemRow(it, n, flags)).join("") + `</tbody></table></div>`
    + (editing ? `<button class="btn small" id="addItemBtn" style="margin-top:8px">+ Add line item</button>` : "")
    + (!d.items.length && !editing ? `<div class="muted">No line items were detected.</div>` : "") + `</div>`;

  $("#infoPanel").innerHTML = html;

  $("#delBtn").addEventListener("click", () => deleteInvoice(inv.id, () => (location.hash = "#/dashboard")));
  $$("[data-exp]").forEach((b) => b.addEventListener("click", () => {
    const k = b.dataset.exp; download(`/api/invoices/${inv.id}/export/${k}`, `invoice_${inv.id}.${k === "excel" ? "xlsx" : k}`);
  }));
  const edit = $("#editBtn"); if (edit) edit.addEventListener("click", () => { editing = true; renderDetail(); });
  const cancel = $("#cancelBtn"); if (cancel) cancel.addEventListener("click", () => { editing = false; renderDetail(); });
  const save = $("#saveBtn"); if (save) save.addEventListener("click", saveChanges);
  const add = $("#addItemBtn"); if (add) add.addEventListener("click", () => {
    $("#itemsTable tbody").insertAdjacentHTML("beforeend", itemRow({}, $$("#itemsTable tbody tr").length, {}));
    bindRemove();
  });
  bindRemove();
}

function itemRow(it, n, flags) {
  const bad = flags[`items.${n + 1}`] === "review";
  return `<tr>` + ITEM_COLS.map(([k, , kind]) =>
    `<td><input data-item="${k}" data-kind="${kind}" class="${kind === "num" ? "num" : ""}${bad ? " review" : ""}" value="${esc(it[k])}" ${editing ? "" : "disabled"}></td>`).join("")
    + (editing ? `<td><button class="btn small danger rm" title="Remove line">&times;</button></td>` : "") + `</tr>`;
}
function bindRemove() { $$("#itemsTable .rm").forEach((b) => (b.onclick = () => b.closest("tr").remove())); }

function parseNum(raw, label) {
  const s = String(raw).trim();
  if (s === "") return null;
  const n = Number(s.replace(/[,\s]/g, "").replace(/^[^\d.-]+/, ""));
  if (Number.isNaN(n)) throw new Error(`"${s}" is not a valid number${label ? " for " + label : ""}.`);
  return n;
}

function collectForm() {
  const data = JSON.parse(JSON.stringify(current.data));
  $$("#infoPanel [data-path]").forEach((el) => {
    const path = el.dataset.path;
    let v;
    if (el.dataset.kind === "num") v = parseNum(el.value, el.closest(".field").querySelector("label").textContent);
    else v = el.value.trim() === "" ? null : el.value.trim();
    setPath(data, path, v);
  });
  data.items = $$("#itemsTable tbody tr").map((tr) => {
    const it = {};
    $$("[data-item]", tr).forEach((el) => {
      it[el.dataset.item] = el.dataset.kind === "num" ? parseNum(el.value, "line item " + el.dataset.item) : (el.value.trim() || null);
    });
    return it;
  }).filter((it) => Object.values(it).some((v) => v !== null));
  return data;
}

async function saveChanges() {
  const btn = $("#saveBtn");
  let data;
  try { data = collectForm(); } catch (e) { toast(e.message, true); return; }
  btn.disabled = true; btn.innerHTML = `<span class="spin"></span>Saving...`;
  try {
    current = await apiJson(`/api/invoices/${current.id}`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ data, mark_reviewed: true }),
    });
    editing = false; renderDetail(); toast("Changes saved");
  } catch (e) { toast(e.message, true); btn.disabled = false; btn.textContent = "Save changes"; }
}

/* ------------------------------------------------------------------ boot */
refreshDot();
route();
