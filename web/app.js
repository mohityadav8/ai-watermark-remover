/* ai-watermark-remover web UI — talks to the stdlib HTTP service (server.py).
 * Endpoints used: GET /health, GET /capabilities, POST /inspect/batch,
 * POST /clean/batch. Response shapes match service/scripts/server.py. */

"use strict";

const LS_BASE = "wm.apiBase";
const LS_KEY = "wm.apiKey";

const state = {
  mode: "inspect",            // "inspect" | "clean"
  files: [],                  // File[]
  apiBase: localStorage.getItem(LS_BASE) || "/api",
  apiKey: localStorage.getItem(LS_KEY) || "",
  running: false,
};

const $ = (id) => document.getElementById(id);

/* ------------------------------------------------------------------ *
 *  HTTP helpers
 * ------------------------------------------------------------------ */
function apiUrl(path) {
  const base = state.apiBase.replace(/\/+$/, "");
  return `${base}${path}`;
}

function authHeaders(extra = {}) {
  const h = { ...extra };
  if (state.apiKey) h["Authorization"] = `Bearer ${state.apiKey}`;
  return h;
}

async function api(path, opts = {}) {
  const res = await fetch(apiUrl(path), {
    ...opts,
    headers: authHeaders(opts.headers || {}),
  });
  const text = await res.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
  if (!res.ok) {
    const msg = (data && (data.error || data.message)) || `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return data;
}

/* base64 <-> bytes (chunked so large files don't blow the call stack) */
function bytesToB64(bytes) {
  let bin = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(bin);
}
function b64ToBytes(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}
function fileToB64(file) {
  return file.arrayBuffer().then((buf) => bytesToB64(new Uint8Array(buf)));
}

/* ------------------------------------------------------------------ *
 *  Health + capabilities
 * ------------------------------------------------------------------ */
async function checkHealth() {
  setStatus("wait", "connecting…");
  try {
    const h = await api("/health");
    setStatus("up", `online · v${h.version || "?"}`);
    $("ver").textContent = h.version ? `v${h.version}` : "";
    loadCapabilities();
  } catch (e) {
    setStatus("down", "offline — check Service URL");
  }
}

function setStatus(cls, text) {
  $("statusDot").className = `dot ${cls}`;
  $("statusText").textContent = text;
}

async function loadCapabilities() {
  try {
    const c = await api("/capabilities");
    renderPills(c);
    renderCaps(c);
  } catch { /* non-fatal */ }
}

function renderPills(caps) {
  // Static supported-format pills + a couple of live capability flags.
  const formats = ["TXT", "MD", "HTML", "PNG", "JPEG", "WEBP", "SVG",
                   "PDF", "DOCX", "XLSX", "PPTX", "EPUB", "MP4", "MP3"];
  const el = $("pills");
  el.innerHTML = "";
  formats.forEach((f) => {
    const s = document.createElement("span");
    s.className = "pill";
    s.textContent = f;
    el.appendChild(s);
  });
}

function renderCaps(caps) {
  const el = $("capsList");
  el.innerHTML = "";
  const rows = [];
  const tools = caps.tools || {};
  rows.push(["exiftool", tools.exiftool, "metadata strip"]);
  rows.push(["qpdf", tools.qpdf, "PDF structural rewrite"]);
  rows.push(["ghostscript", tools.ghostscript, "PDF deep-image pass"]);
  rows.push(["c2patool", tools.c2patool, "C2PA inspection"]);
  const px = caps.pixel_backends || {};
  rows.push(["ctrlregen", px.ctrlregen, "pixel watermark removal"]);
  const sc = caps.scorers || {};
  rows.push(["synthid score", sc.synthid || sc.synthid_http, "image scoring"]);
  rows.push(["stylometry", sc.stylometry, "text AI-likelihood"]);

  rows.forEach(([name, on, val]) => {
    const d = document.createElement("div");
    d.className = "cap";
    d.innerHTML =
      `<span class="d ${on ? "on" : "off"}"></span>` +
      `<span class="name">${name}</span>` +
      `<span class="val ${on ? "on" : ""}">${on ? val : "not available"}</span>`;
    el.appendChild(d);
  });
}

/* ------------------------------------------------------------------ *
 *  File handling
 * ------------------------------------------------------------------ */
function addFiles(list) {
  for (const f of list) state.files.push(f);
  refreshFileUI();
}
function refreshFileUI() {
  const n = state.files.length;
  $("fileCount").textContent = n ? `${n} file${n > 1 ? "s" : ""} queued` : "";
  $("runBtn").disabled = n === 0 || state.running;
  $("clearBtn").style.display = n ? "inline-block" : "none";
}
function clearAll() {
  state.files = [];
  $("fileInput").value = "";
  $("ledger").innerHTML = "";
  $("summary").style.display = "none";
  refreshFileUI();
}

/* ------------------------------------------------------------------ *
 *  Run: inspect or clean (batched)
 * ------------------------------------------------------------------ */
function collectCleanOptions() {
  return {
    also_layer_a_text: $("opt_also_layer_a").checked,
    nfkc: $("opt_nfkc").checked,
    aggressive_homoglyphs: $("opt_aggr").checked,
    keep_non_ai_metadata: $("opt_keepmeta").checked,
    detect_before: $("opt_detect").checked,
    detect_after: $("opt_detect").checked,
    deep_images: $("opt_deep").value,
  };
}

async function run() {
  if (!state.files.length || state.running) return;
  state.running = true;
  $("runBtn").disabled = true;
  $("scanbar").style.display = "block";
  $("ledger").innerHTML = "";
  $("summary").style.display = "none";

  try {
    const encoded = await Promise.all(
      state.files.map(async (f) => ({ file: await fileToB64(f), name: f.name }))
    );

    let results;
    if (state.mode === "inspect") {
      const body = { files: encoded.map((e) => ({ ...e, detect: false })) };
      const r = await api("/inspect/batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      results = r.results || [];
    } else {
      const options = collectCleanOptions();
      const body = { files: encoded.map((e) => ({ ...e, options })) };
      const r = await api("/clean/batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      results = r.results || [];
    }

    renderResults(results);
  } catch (e) {
    $("ledger").innerHTML =
      `<div class="rowcard open"><div class="rowhead">` +
      `<span class="badge err">error</span>` +
      `<span class="fname">${escapeHtml(e.message)}</span></div></div>`;
  } finally {
    state.running = false;
    $("scanbar").style.display = "none";
    refreshFileUI();
  }
}

/* ------------------------------------------------------------------ *
 *  Rendering results
 * ------------------------------------------------------------------ */
function renderResults(results) {
  const ledger = $("ledger");
  ledger.innerHTML = "";
  let filesWithMarks = 0, totalMarks = 0, cleaned = 0, errors = 0;

  results.forEach((r) => {
    const summary = summarize(r, state.mode);
    if (summary.error) errors++;
    else if (state.mode === "inspect" && summary.found) { filesWithMarks++; totalMarks += summary.count; }
    else if (state.mode === "clean" && summary.changed) { cleaned++; totalMarks += summary.count; }

    ledger.appendChild(buildCard(r, summary));
  });

  // summary strip
  const s = $("summary");
  s.innerHTML = "";
  s.style.display = "flex";
  const stats = [];
  if (state.mode === "inspect") {
    stats.push(statEl(results.length, "files scanned"));
    stats.push(statEl(filesWithMarks, "with marks", filesWithMarks > 0));
    stats.push(statEl(totalMarks, "marks total", totalMarks > 0));
  } else {
    stats.push(statEl(results.length, "files processed"));
    stats.push(statEl(cleaned, "cleaned", cleaned > 0));
    stats.push(statEl(totalMarks, "marks removed", totalMarks > 0));
  }
  stats.forEach((x) => s.appendChild(x));
  if (state.mode === "clean" && cleaned > 0) {
    const sp = document.createElement("span"); sp.className = "spacer"; s.appendChild(sp);
    const dlAll = document.createElement("button");
    dlAll.className = "dl"; dlAll.textContent = "download all";
    dlAll.onclick = () => results.forEach((r) => { if (r.ok && r.cleaned) downloadCleaned(r); });
    s.appendChild(dlAll);
  }
  if (errors) {
    const sp = document.createElement("span"); sp.className = "spacer"; s.appendChild(sp);
    s.appendChild(statEl(errors, "errors", true));
  }
}

function statEl(num, label, warn) {
  const d = document.createElement("div");
  d.className = "stat" + (warn ? " warn" : "");
  d.innerHTML = `<b>${num}</b>${label}`;
  return d;
}

/* Decide the badge + a "count" of marks for a single result. Best-effort:
   report fields differ by file kind, so we read the ones we know and fall
   back to the raw JSON expander for everything else. */
function summarize(r, mode) {
  if (!r.ok) return { error: true, badge: "err", badgeText: r.error || "error", count: 0 };

  const rep = r.report || {};
  if (mode === "inspect") {
    const count = markCountInspect(rep);
    const found = !!r.suspicious || count > 0 ||
      !!rep.has_c2pa || !!rep.has_ai_metadata;
    return {
      found, count,
      badge: found ? "found" : "clean",
      badgeText: found ? (count ? `${count} mark${count > 1 ? "s" : ""}` : "suspicious") : "clean",
    };
  } else {
    const count = markCountClean(rep);
    const changed = count > 0 || !!rep.has_c2pa || !!rep.has_ai_metadata ||
      (Array.isArray(rep.actions) && rep.actions.length > 0);
    return {
      changed, count,
      badge: changed ? "found" : "clean",
      badgeText: changed ? (count ? `−${count}` : "cleaned") : "already clean",
    };
  }
}

function markCountInspect(rep) {
  // Text inspect reports carry suspicious_total (invisible-char count).
  // Containers/images report suspicious_total: 0 but list findings instead.
  const total = typeof rep.suspicious_total === "number" ? rep.suspicious_total : 0;
  if (total > 0) return total;
  if (Array.isArray(rep.findings)) return rep.findings.length;
  return 0;
}
function markCountClean(rep) {
  // Text clean report: stats.removed_count / replaced_count are the real
  // numbers (other stats keys are lengths — never sum those). Containers use
  // an `actions` list; images use `actions` too.
  const st = rep.stats;
  if (st && typeof st === "object") {
    const removed = typeof st.removed_count === "number" ? st.removed_count : 0;
    const replaced = typeof st.replaced_count === "number" ? st.replaced_count : 0;
    if (removed || replaced) return removed + replaced;
    // fall back to counting the removed-carrier dict
    if (st.removed && typeof st.removed === "object") {
      return Object.values(st.removed).reduce((a, v) => a + (typeof v === "number" ? v : 0), 0);
    }
  }
  if (Array.isArray(rep.actions)) return rep.actions.length;
  return 0;
}

function buildCard(r, summary) {
  const card = document.createElement("div");
  card.className = "rowcard";

  const head = document.createElement("div");
  head.className = "rowhead";
  head.innerHTML =
    `<span class="badge ${summary.badge}">${escapeHtml(summary.badgeText)}</span>` +
    `<span class="fname">${escapeHtml(r.name || "file")}</span>` +
    `<span class="kindtag">${escapeHtml(r.kind || "")}</span>`;

  // download button for successful cleans
  if (state.mode === "clean" && r.ok && r.cleaned) {
    const dl = document.createElement("button");
    dl.className = "dl";
    dl.textContent = "download";
    dl.onclick = (e) => { e.stopPropagation(); downloadCleaned(r); };
    head.appendChild(dl);
  }

  const chev = document.createElement("span");
  chev.className = "chev"; chev.textContent = "▸";
  head.appendChild(chev);
  head.onclick = () => card.classList.toggle("open");
  card.appendChild(head);

  // body
  const body = document.createElement("div");
  body.className = "rowbody";
  body.appendChild(renderFindings(r));
  card.appendChild(body);

  return card;
}

function renderFindings(r) {
  const box = document.createElement("div");
  if (!r.ok) {
    box.innerHTML = `<div class="note">${escapeHtml(r.error || "processing failed")}</div>`;
    return box;
  }
  const rep = r.report || {};
  const rows = [];

  // Common high-signal fields.
  addRow(rows, "kind", r.kind);
  if (rep.format) addRow(rows, "format", rep.format);
  if ("suspicious" in r) addRow(rows, "suspicious", r.suspicious ? "yes" : "no", r.suspicious);
  if ("has_c2pa" in rep) addRow(rows, "C2PA manifest", rep.has_c2pa ? "present" : "none", rep.has_c2pa);
  if ("has_ai_metadata" in rep) addRow(rows, "AI metadata", rep.has_ai_metadata ? "present" : "none", rep.has_ai_metadata);
  if (typeof rep.suspicious_total === "number" && rep.suspicious_total > 0)
    addRow(rows, "invisible marks", rep.suspicious_total, true);

  // Inspect findings list (containers/images).
  if (Array.isArray(rep.findings) && rep.findings.length) {
    addRow(rows, "findings", rep.findings.join(" · "), true);
  }

  // Text CLEAN stats: per-carrier removals.
  const st = rep.stats;
  if (st && typeof st === "object") {
    if (typeof st.removed_count === "number" && st.removed_count > 0)
      addRow(rows, "invisible marks removed", st.removed_count, true);
    if (st.removed && typeof st.removed === "object") {
      for (const [name, n] of Object.entries(st.removed)) {
        if (typeof n === "number" && n > 0) addRow(rows, `· ${name}`, n, true);
      }
    }
    if (st.nfkc_changed) addRow(rows, "NFKC normalized", "yes", true);
  }

  // Container/image CLEAN: actions + residuals + byte deltas.
  if (Array.isArray(rep.actions) && rep.actions.length) {
    rep.actions.forEach((a) => addRow(rows, "action", a, true));
  }
  if ("still_has_c2pa" in rep) addRow(rows, "residual C2PA", rep.still_has_c2pa ? "yes" : "no", rep.still_has_c2pa);
  if ("still_has_ai_metadata" in rep) addRow(rows, "residual AI metadata", rep.still_has_ai_metadata ? "yes" : "no", rep.still_has_ai_metadata);
  if (rep.bytes_in != null && rep.bytes_out != null)
    addRow(rows, "size", `${rep.bytes_in} → ${rep.bytes_out} bytes`);

  // Stylometry.
  const sty = rep.stylometry;
  if (sty && typeof sty.score === "number") {
    addRow(rows, "stylometry (AI-likelihood)", sty.score.toFixed(2), sty.score >= 0.65);
  }
  // Warnings (e.g. missing qpdf/ghostscript).
  if (Array.isArray(rep.warnings) && rep.warnings.length) {
    rep.warnings.forEach((w) => addRow(rows, "warning", w));
  }
  if (Array.isArray(rep.notes) && rep.notes.length) {
    rep.notes.forEach((n) => addRow(rows, "note", n));
  }

  const table = document.createElement("div");
  table.className = "findings";
  if (rows.length === 0) {
    table.innerHTML = `<div class="note">No structured findings — see raw report below.</div>`;
  } else {
    rows.forEach((row) => table.appendChild(row));
  }
  box.appendChild(table);

  // raw JSON expander (always available, honest completeness)
  const det = document.createElement("details");
  det.innerHTML = `<summary style="cursor:pointer;font-family:var(--mono);font-size:12px;color:var(--muted);margin-top:12px">raw report</summary>`;
  const pre = document.createElement("pre");
  pre.className = "raw";
  pre.textContent = JSON.stringify(rep, null, 2);
  det.appendChild(pre);
  box.appendChild(det);

  return box;
}

function addRow(rows, k, v, hit) {
  const d = document.createElement("div");
  d.className = "finding";
  const isZero = v === 0 || v === "0" || v === "none" || v === "no";
  const vcls = hit ? "hit" : (isZero ? "zero" : "");
  d.innerHTML = `<span class="k">${escapeHtml(String(k))}</span>` +
                `<span class="v ${vcls}">${escapeHtml(String(v))}</span>`;
  rows.push(d);
}

function downloadCleaned(r) {
  const bytes = b64ToBytes(r.cleaned);
  const blob = new Blob([bytes]);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = cleanedName(r.name || "file");
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}
function cleanedName(name) {
  const dot = name.lastIndexOf(".");
  if (dot <= 0) return `${name}.cleaned`;
  return `${name.slice(0, dot)}.cleaned${name.slice(dot)}`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ------------------------------------------------------------------ *
 *  Mode + wiring
 * ------------------------------------------------------------------ */
function setMode(m) {
  state.mode = m;
  $("modeInspect").classList.toggle("active", m === "inspect");
  $("modeClean").classList.toggle("active", m === "clean");
  $("cleanOptions").style.display = m === "clean" ? "block" : "none";
  $("modeHint").textContent = m === "inspect"
    ? "Inspect: report marks without changing anything."
    : "Clean: strip the marks and give you the cleaned file back.";
  $("runBtn").textContent = m === "inspect" ? "Inspect" : "Clean";
  $("ledger").innerHTML = "";
  $("summary").style.display = "none";
}

function wire() {
  // mode
  $("modeInspect").onclick = () => setMode("inspect");
  $("modeClean").onclick = () => setMode("clean");

  // dropzone
  const dz = $("dropzone");
  dz.onclick = () => $("fileInput").click();
  dz.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") $("fileInput").click(); };
  $("fileInput").onchange = (e) => addFiles(e.target.files);
  ["dragenter", "dragover"].forEach((ev) =>
    dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) =>
    dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
  dz.addEventListener("drop", (e) => { if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files); });

  // actions
  $("runBtn").onclick = run;
  $("clearBtn").onclick = clearAll;

  // settings popover
  $("apiBase").value = state.apiBase;
  $("apiKey").value = state.apiKey;
  $("openSettings").onclick = () => $("settingsPop").classList.add("show");
  $("closeSettings").onclick = () => $("settingsPop").classList.remove("show");
  $("settingsPop").addEventListener("click", (e) => {
    if (e.target === $("settingsPop")) $("settingsPop").classList.remove("show");
  });
  $("saveSettings").onclick = () => {
    state.apiBase = $("apiBase").value.trim() || "/api";
    state.apiKey = $("apiKey").value.trim();
    localStorage.setItem(LS_BASE, state.apiBase);
    localStorage.setItem(LS_KEY, state.apiKey);
    $("settingsPop").classList.remove("show");
    checkHealth();
  };

  setMode("inspect");
  checkHealth();
}

document.addEventListener("DOMContentLoaded", wire);
