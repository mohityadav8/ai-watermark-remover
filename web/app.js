/* ai-watermark-remover — UI logic.
 * Talks to the engine: GET /health, /capabilities; POST /inspect/batch, /clean/batch.
 * The "Paste text" reveal is done client-side for instant feedback; the actual
 * clean is performed by the engine so it matches the CLI/service exactly. */

"use strict";

const LS_BASE = "wm.apiBase";
const LS_KEY = "wm.apiKey";

const state = {
  apiBase: localStorage.getItem(LS_BASE) || "/api",
  apiKey: localStorage.getItem(LS_KEY) || "",
  files: [],
  running: false,
  tab: "text",
};

const $ = (id) => document.getElementById(id);

/* ============================ HTTP ============================ */
const apiUrl = (p) => `${state.apiBase.replace(/\/+$/, "")}${p}`;
function authHeaders(extra = {}) {
  const h = { ...extra };
  if (state.apiKey) h["Authorization"] = `Bearer ${state.apiKey}`;
  return h;
}
async function api(path, opts = {}) {
  const res = await fetch(apiUrl(path), { ...opts, headers: authHeaders(opts.headers || {}) });
  const txt = await res.text();
  let data; try { data = txt ? JSON.parse(txt) : {}; } catch { data = { raw: txt }; }
  if (!res.ok) throw new Error((data && (data.error || data.message)) || `HTTP ${res.status}`);
  return data;
}
function bytesToB64(bytes) { let s = ""; const c = 0x8000; for (let i = 0; i < bytes.length; i += c)s += String.fromCharCode.apply(null, bytes.subarray(i, i + c)); return btoa(s); }
function b64ToBytes(b) { const bin = atob(b); const o = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++)o[i] = bin.charCodeAt(i); return o; }
const strToB64 = (s) => bytesToB64(new TextEncoder().encode(s));
const b64ToStr = (b) => new TextDecoder().decode(b64ToBytes(b));
const fileToB64 = (f) => f.arrayBuffer().then((buf) => bytesToB64(new Uint8Array(buf)));

/* ==================== health + capabilities ==================== */
async function checkHealth() {
  setStatus("wait", "connecting…");
  try {
    const h = await api("/health");
    setStatus("up", `online · v${h.version || "?"}`);
    loadCaps();
  } catch { setStatus("down", "offline — is the engine running?"); }
}
function setStatus(cls, text) { $("statusDot").className = `dot ${cls}`; $("statusText").textContent = text; }
async function loadCaps() {
  try { renderCaps(await api("/capabilities")); } catch { }
}
function renderCaps(c) {
  const el = $("capsList"); el.innerHTML = "";
  const t = c.tools || {}, px = c.pixel_backends || {}, sc = c.scorers || {};
  const rows = [
    ["exiftool", t.exiftool, "metadata strip"],
    ["qpdf", t.qpdf, "PDF rewrite"],
    ["ghostscript", t.ghostscript, "PDF deep images"],
    ["c2patool", t.c2patool, "C2PA inspect"],
    ["ctrlregen", px.ctrlregen, "pixel removal"],
    ["stylometry", sc.stylometry, "AI-likelihood"],
  ];
  rows.forEach(([n, on, v]) => {
    const d = document.createElement("div"); d.className = "cap";
    d.innerHTML = `<span class="d ${on ? "on" : "off"}"></span><span class="name">${n}</span><span class="val ${on ? "on" : ""}">${on ? v : "n/a"}</span>`;
    el.appendChild(d);
  });
}

/* ==================== invisible-char detection ==================== */
// Map a codepoint to a short label, or null if it's ordinary text.
// `space` marks characters that are "look-alike spaces" (shown lime, not red).
function classify(cp) {
  const named = {
    0x200B: "ZWSP", 0x200C: "ZWNJ", 0x200D: "ZWJ", 0x2060: "WJ", 0xFEFF: "BOM",
    0x00AD: "SHY", 0x061C: "ALM", 0x200E: "LRM", 0x200F: "RLM",
    0x202A: "LRE", 0x202B: "RLE", 0x202C: "PDF", 0x202D: "LRO", 0x202E: "RLO",
    0x2066: "LRI", 0x2067: "RLI", 0x2068: "FSI", 0x2069: "PDI",
    0x180E: "MVS", 0x034F: "CGJ", 0x115F: "FILL", 0x1160: "FILL", 0x3164: "FILL", 0xFFA0: "FILL",
    0x00A0: "NBSP", 0x202F: "NNBSP", 0x205F: "MMSP", 0x3000: "IDSP", 0x1680: "OGSP",
  };
  const spaceSet = new Set([0x00A0, 0x202F, 0x205F, 0x3000, 0x1680]);
  if (cp in named) return { label: named[cp], space: spaceSet.has(cp) };
  if (cp >= 0x2000 && cp <= 0x200A) return { label: "SP", space: true };          // en/em/thin spaces
  if (cp >= 0xE0000 && cp <= 0xE007F) return { label: "TAG", space: false };       // tag chars
  if (cp >= 0xE000 && cp <= 0xF8FF) return { label: "PUA", space: false };         // private use
  if (cp >= 0xF0000 && cp <= 0xFFFFD) return { label: "PUA", space: false };
  if (cp >= 0x100000 && cp <= 0x10FFFD) return { label: "PUA", space: false };
  if (cp >= 0xFDD0 && cp <= 0xFDEF) return { label: "NONCHAR", space: false };     // noncharacters
  if ((cp & 0xFFFF) === 0xFFFE || (cp & 0xFFFF) === 0xFFFF) return { label: "NONCHAR", space: false };
  if (cp >= 0xFFF9 && cp <= 0xFFFB) return { label: "IAA", space: false };         // interlinear annotation
  // C0/C1 controls except tab(9) newline(10) carriage-return(13)
  if ((cp <= 0x08) || (cp === 0x0B) || (cp === 0x0C) || (cp >= 0x0E && cp <= 0x1F) || (cp >= 0x7F && cp <= 0x9F))
    return { label: "CTRL", space: false };
  return null;
}
function esc(s) { return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

// Render text into the reveal box + return {total, counts}
function analyze(text) {
  const counts = {};
  let total = 0;
  let html = "";
  let buf = "";
  const flush = () => { if (buf) { html += esc(buf); buf = ""; } };
  for (const ch of text) {                   // iterate by code point
    const cp = ch.codePointAt(0);
    const hit = classify(cp);
    if (hit) {
      flush();
      total++;
      counts[hit.label] = (counts[hit.label] || 0) + 1;
      const cls = hit.space ? "glyph space" : "glyph";
      const title = `${hit.label} · U+${cp.toString(16).toUpperCase().padStart(4, "0")}`;
      html += `<span class="${cls}" title="${title}">${hit.label}</span>`;
    } else {
      buf += ch;
    }
  }
  flush();
  return { total, counts, html };
}

function updateReveal() {
  const text = $("textInput").value;
  const box = $("revealBox");
  const { total, counts, html } = analyze(text);

  if (!text) {
    box.innerHTML = `<span class="empty">Your text will appear here with any hidden characters marked.</span>`;
  } else {
    box.innerHTML = html || `<span class="empty">(only whitespace)</span>`;
  }

  const verdict = $("verdict");
  verdict.className = "verdict " + (total > 0 ? "dirty" : "clean");
  verdict.innerHTML = `<span class="n">${total}</span> hidden character${total === 1 ? "" : "s"}`;

  const chips = $("chips"); chips.innerHTML = "";
  Object.entries(counts).sort((a, b) => b[1] - a[1]).forEach(([k, n]) => {
    const s = document.createElement("span"); s.className = "chip";
    s.innerHTML = `${k} <b>×${n}</b>`; chips.appendChild(s);
  });

  $("cleanTextBtn").disabled = text.length === 0 || state.running;
  // hide any previous output when input changes
  $("textOutWrap").style.display = "none";
  $("stampText").classList.remove("show");
}

async function cleanText() {
  const text = $("textInput").value;
  if (!text || state.running) return;
  state.running = true;
  $("cleanTextBtn").disabled = true;
  $("textHint").textContent = "cleaning…";
  try {
    const body = { files: [{ file: strToB64(text), name: "pasted.txt", options: {} }] };
    const r = await api("/clean/batch", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const res = r.results && r.results[0];
    if (!res || !res.ok) throw new Error((res && res.error) || "clean failed");
    const cleaned = b64ToStr(res.cleaned);
    const removed = (res.report && res.report.stats && res.report.stats.removed_count) || 0;
    $("textOutput").textContent = cleaned;
    $("textRemoved").textContent = `${removed} hidden character${removed === 1 ? "" : "s"} removed`;
    $("textOutWrap").style.display = "block";
    $("textHint").textContent = "";
    // stamp
    const stamp = $("stampText"); stamp.classList.remove("show"); void stamp.offsetWidth; stamp.classList.add("show");
    $("textOutput").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (e) {
    $("textHint").textContent = `⚠ ${e.message}`;
  } finally {
    state.running = false; $("cleanTextBtn").disabled = $("textInput").value.length === 0;
  }
}

function copyText() {
  const t = $("textOutput").textContent;
  navigator.clipboard.writeText(t).then(() => {
    const b = $("copyTextBtn"); const old = b.textContent; b.textContent = "Copied ✓";
    setTimeout(() => b.textContent = old, 1400);
  }).catch(() => { $("textHint").textContent = "couldn't copy — select and copy manually"; });
}

const EXAMPLE = "This\u200bsentence\u00adlooks\u200cnormal\u200dbut\u2060it\u200bis\u00adfull"
  + " of\u200bhidden\u200cmarks.\u202eSome\u202c even flip direction.\uFEFF";

/* ==================== files ==================== */
function addFiles(list) { for (const f of list) state.files.push(f); refreshFiles(); }
function removeFile(i) { state.files.splice(i, 1); refreshFiles(); }
function refreshFiles() {
  const chips = $("fileChips"); chips.innerHTML = "";
  state.files.forEach((f, i) => {
    const c = document.createElement("span"); c.className = "fchip";
    c.innerHTML = `${esc(f.name)} <span class="x" title="remove">✕</span>`;
    c.querySelector(".x").onclick = () => removeFile(i);
    chips.appendChild(c);
  });
  const n = state.files.length;
  $("fileHint").textContent = n ? `${n} file${n > 1 ? "s" : ""} queued` : "";
  $("inspectBtn").disabled = n === 0 || state.running;
  $("cleanBtn").disabled = n === 0 || state.running;
  $("clearFilesBtn").style.display = n ? "inline-block" : "none";
}
function clearFiles() { state.files = []; $("fileInput").value = ""; $("ledger").innerHTML = ""; $("summary").style.display = "none"; refreshFiles(); }

async function runFiles(mode) {
  if (!state.files.length || state.running) return;
  state.running = true;
  $("inspectBtn").disabled = $("cleanBtn").disabled = true;
  $("scanbar").style.display = "block"; $("ledger").innerHTML = ""; $("summary").style.display = "none";
  try {
    const enc = await Promise.all(state.files.map(async f => ({ file: await fileToB64(f), name: f.name })));
    let results;
    if (mode === "inspect") {
      const r = await api("/inspect/batch", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ files: enc })
      });
      results = r.results || [];
    } else {
      const r = await api("/clean/batch", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ files: enc.map(e => ({ ...e, options: { also_layer_a_text: true } })) })
      });
      results = r.results || [];
    }
    renderResults(results, mode);
  } catch (e) {
    $("ledger").innerHTML = `<div class="rowcard open"><div class="rowhead"><span class="badge err">error</span><span class="fname">${esc(e.message)}</span></div></div>`;
  } finally {
    state.running = false; $("scanbar").style.display = "none"; refreshFiles();
  }
}

function renderResults(results, mode) {
  const ledger = $("ledger"); ledger.innerHTML = "";
  let withMarks = 0, total = 0, cleaned = 0, errors = 0;
  results.forEach(r => {
    const sm = summarize(r, mode);
    if (sm.error) errors++;
    else if (mode === "inspect" && sm.found) { withMarks++; total += sm.count; }
    else if (mode === "clean" && sm.changed) { cleaned++; total += sm.count; }
    ledger.appendChild(buildCard(r, sm, mode));
  });
  const s = $("summary"); s.innerHTML = ""; s.style.display = "flex";
  const add = (n, l, hot) => { const d = document.createElement("div"); d.className = "stat" + (hot ? " hot" : ""); d.innerHTML = `<b data-count="${n}">0</b>${l}`; s.appendChild(d); };
  if (mode === "inspect") { add(results.length, "scanned"); add(withMarks, "with marks", withMarks > 0); add(total, "marks total", total > 0); }
  else { add(results.length, "processed"); add(cleaned, "cleaned", cleaned > 0); add(total, "marks removed", total > 0); }
  if (mode === "clean" && cleaned > 0) {
    const sp = document.createElement("span"); sp.className = "spacer"; s.appendChild(sp);
    const b = document.createElement("button"); b.className = "dl"; b.textContent = "download all";
    b.onclick = () => results.forEach(r => { if (r.ok && r.cleaned) downloadCleaned(r); }); s.appendChild(b);
  }
  if (errors) { const sp = document.createElement("span"); sp.className = "spacer"; s.appendChild(sp); add(errors, "errors", true); }
  animateCounts(s);
}
function animateCounts(scope) {
  scope.querySelectorAll("b[data-count]").forEach(el => {
    const target = +el.getAttribute("data-count"); if (!target) { el.textContent = "0"; return; }
    const dur = 500, t0 = performance.now();
    const step = (t) => { const p = Math.min(1, (t - t0) / dur); el.textContent = Math.round(p * target); if (p < 1) requestAnimationFrame(step); };
    requestAnimationFrame(step);
  });
}

function summarize(r, mode) {
  if (!r.ok) return { error: true, badge: "err", badgeText: r.error || "error", count: 0 };
  const rep = r.report || {};
  if (mode === "inspect") {
    const count = markCountInspect(rep);
    const found = !!r.suspicious || count > 0 || !!rep.has_c2pa || !!rep.has_ai_metadata;
    return { found, count, badge: found ? "found" : "clean", badgeText: found ? (count ? `${count} mark${count > 1 ? "s" : ""}` : "suspicious") : "clean" };
  }
  const count = markCountClean(rep);
  const changed = count > 0 || !!rep.has_c2pa || !!rep.has_ai_metadata || (Array.isArray(rep.actions) && rep.actions.length > 0);
  return { changed, count, badge: changed ? "found" : "clean", badgeText: changed ? (count ? `−${count}` : "cleaned") : "already clean" };
}
function markCountInspect(rep) {
  const total = typeof rep.suspicious_total === "number" ? rep.suspicious_total : 0;
  if (total > 0) return total;
  if (Array.isArray(rep.findings)) return rep.findings.length;
  return 0;
}
function markCountClean(rep) {
  const st = rep.stats;
  if (st && typeof st === "object") {
    const rc = typeof st.removed_count === "number" ? st.removed_count : 0;
    const pc = typeof st.replaced_count === "number" ? st.replaced_count : 0;
    if (rc || pc) return rc + pc;
    if (st.removed && typeof st.removed === "object") return Object.values(st.removed).reduce((a, v) => a + (typeof v === "number" ? v : 0), 0);
  }
  if (Array.isArray(rep.actions)) return rep.actions.length;
  return 0;
}

function buildCard(r, sm, mode) {
  const card = document.createElement("div"); card.className = "rowcard";
  const head = document.createElement("div"); head.className = "rowhead";
  head.innerHTML = `<span class="badge ${sm.badge}">${esc(sm.badgeText)}</span><span class="fname">${esc(r.name || "file")}</span><span class="kindtag">${esc(r.kind || "")}</span>`;
  if (mode === "clean" && r.ok && r.cleaned) {
    const dl = document.createElement("button"); dl.className = "dl"; dl.textContent = "download";
    dl.onclick = (e) => { e.stopPropagation(); downloadCleaned(r); }; head.appendChild(dl);
  }
  const chev = document.createElement("span"); chev.className = "chev"; chev.textContent = "▸"; head.appendChild(chev);
  head.onclick = () => card.classList.toggle("open");
  card.appendChild(head);
  const body = document.createElement("div"); body.className = "rowbody"; body.appendChild(renderFindings(r)); card.appendChild(body);
  return card;
}
function renderFindings(r) {
  const box = document.createElement("div");
  if (!r.ok) { box.innerHTML = `<div class="finding"><span class="v hit">${esc(r.error || "processing failed")}</span></div>`; return box; }
  const rep = r.report || {}; const rows = [];
  const add = (k, v, hit) => { const isZero = v === 0 || v === "none" || v === "no"; const d = document.createElement("div"); d.className = "finding"; d.innerHTML = `<span class="k">${esc(k)}</span><span class="v ${hit ? "hit" : (isZero ? "zero" : "")}">${esc(String(v))}</span>`; rows.push(d); };
  add("kind", r.kind);
  if (rep.format) add("format", rep.format);
  if ("suspicious" in r) add("suspicious", r.suspicious ? "yes" : "no", r.suspicious);
  if ("has_c2pa" in rep) add("C2PA manifest", rep.has_c2pa ? "present" : "none", rep.has_c2pa);
  if ("has_ai_metadata" in rep) add("AI metadata", rep.has_ai_metadata ? "present" : "none", rep.has_ai_metadata);
  if (typeof rep.suspicious_total === "number" && rep.suspicious_total > 0) add("invisible marks", rep.suspicious_total, true);
  if (Array.isArray(rep.findings) && rep.findings.length) add("findings", rep.findings.join(" · "), true);
  const st = rep.stats;
  if (st && typeof st === "object") {
    if (typeof st.removed_count === "number" && st.removed_count > 0) add("invisible marks removed", st.removed_count, true);
    if (st.removed && typeof st.removed === "object") for (const [n, c] of Object.entries(st.removed)) if (typeof c === "number" && c > 0) add(`· ${n}`, c, true);
    if (st.nfkc_changed) add("NFKC normalized", "yes", true);
  }
  if (Array.isArray(rep.actions)) rep.actions.forEach(a => add("action", a, true));
  if ("still_has_c2pa" in rep) add("residual C2PA", rep.still_has_c2pa ? "yes" : "no", rep.still_has_c2pa);
  if ("still_has_ai_metadata" in rep) add("residual AI metadata", rep.still_has_ai_metadata ? "yes" : "no", rep.still_has_ai_metadata);
  if (rep.bytes_in != null && rep.bytes_out != null) add("size", `${rep.bytes_in} → ${rep.bytes_out} bytes`);
  const sty = rep.stylometry;
  if (sty && typeof sty.score === "number") add("stylometry (AI-likelihood)", sty.score.toFixed(2), sty.score >= 0.65);
  if (Array.isArray(rep.warnings)) rep.warnings.forEach(w => add("warning", w));
  if (Array.isArray(rep.notes)) rep.notes.forEach(n => add("note", n));

  const table = document.createElement("div");
  if (rows.length === 0) { table.innerHTML = `<div class="finding"><span class="v zero">no structured findings — see raw report</span></div>`; }
  else rows.forEach(x => table.appendChild(x));
  box.appendChild(table);

  const det = document.createElement("details"); det.className = "raw";
  det.innerHTML = `<summary>raw report</summary>`;
  const pre = document.createElement("pre"); pre.className = "rawpre"; pre.textContent = JSON.stringify(rep, null, 2);
  det.appendChild(pre); box.appendChild(det);
  return box;
}
function downloadCleaned(r) {
  const blob = new Blob([b64ToBytes(r.cleaned)]); const url = URL.createObjectURL(blob);
  const a = document.createElement("a"); a.href = url; a.download = cleanedName(r.name || "file");
  document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 4000);
}
function cleanedName(name) { const d = name.lastIndexOf("."); return d <= 0 ? `${name}.cleaned` : `${name.slice(0, d)}.cleaned${name.slice(d)}`; }

/* ==================== tabs + wiring ==================== */
function setTab(t) {
  state.tab = t;
  $("tabText").classList.toggle("active", t === "text");
  $("tabFiles").classList.toggle("active", t === "files");
  $("panelText").style.display = t === "text" ? "block" : "none";
  $("panelFiles").style.display = t === "files" ? "block" : "none";
}

function wire() {
  // tabs
  $("tabText").onclick = () => setTab("text");
  $("tabFiles").onclick = () => setTab("files");

  // text tool
  $("textInput").addEventListener("input", updateReveal);
  $("cleanTextBtn").onclick = cleanText;
  $("clearTextBtn").onclick = () => { $("textInput").value = ""; updateReveal(); $("textHint").textContent = ""; };
  $("copyTextBtn").onclick = copyText;
  $("loadExample").onclick = () => { $("textInput").value = EXAMPLE; updateReveal(); };

  // dropzone
  const dz = $("dropzone");
  dz.onclick = () => $("fileInput").click();
  dz.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") $("fileInput").click(); };
  $("fileInput").onchange = (e) => addFiles(e.target.files);
  ["dragenter", "dragover"].forEach(ev => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
  ["dragleave", "drop"].forEach(ev => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
  dz.addEventListener("drop", (e) => { if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files); });
  $("inspectBtn").onclick = () => runFiles("inspect");
  $("cleanBtn").onclick = () => runFiles("clean");
  $("clearFilesBtn").onclick = clearFiles;

  // settings
  $("apiBase").value = state.apiBase; $("apiKey").value = state.apiKey;
  $("openSettings").onclick = () => $("settingsPop").classList.add("show");
  $("closeSettings").onclick = () => $("settingsPop").classList.remove("show");
  $("settingsPop").addEventListener("click", (e) => { if (e.target === $("settingsPop")) $("settingsPop").classList.remove("show"); });
  $("saveSettings").onclick = () => {
    state.apiBase = $("apiBase").value.trim() || "/api";
    state.apiKey = $("apiKey").value.trim();
    localStorage.setItem(LS_BASE, state.apiBase); localStorage.setItem(LS_KEY, state.apiKey);
    $("settingsPop").classList.remove("show"); checkHealth();
  };

  updateReveal();
  checkHealth();
}
document.addEventListener("DOMContentLoaded", wire);