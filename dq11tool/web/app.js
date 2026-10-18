"use strict";
// DQXI 3DS translation studio - frontend

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function api(path, opts = {}) {
  const o = { headers: {}, ...opts };
  if (o.json !== undefined) { o.method = o.method || "POST"; o.body = JSON.stringify(o.json); o.headers["Content-Type"] = "application/json"; }
  const r = await fetch(path, o);
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.error) || r.statusText);
  return data;
}

const STATUS = ["untranslated", "draft", "translated", "reviewed"];
const state = {
  view: "editor", filters: { category: "", status: "all", q: "", page: 1, per: 50, file: "", sort: "", show_hidden: false, pc: "" },
  items: [], total: 0, selected: null, detail: null, categories: [], glossary: [], fonts: {}, previewFont: "3D14",
};

// ------------------------------------------------------------------ rendering helpers
function renderTagged(text, { ruby = true } = {}) {
  // decoded game text -> HTML with ruby, variable chips and page breaks
  let out = "";
  const re = /\{\{|\}\}|\{([^{}]*)\}/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    out += esc(text.slice(last, m.index));
    last = re.lastIndex;
    if (m[0] === "{{") { out += "{"; continue; }
    if (m[0] === "}}") { out += "}"; continue; }
    const tag = m[1];
    if (tag.startsWith("ruby:")) {
      const [base, reading] = tag.slice(5).split("|");
      out += ruby ? `<ruby>${esc(base)}<rt>${esc(reading)}</rt></ruby>` : esc(base);
    } else if (tag.startsWith("v:")) out += `<span class="chip var" title="runtime variable (name, item, number…)">◆${esc(tag.slice(2))}</span>`;
    else if (tag === "CR") out += `<span class="chip cr" title="page break (wait for button)">▼</span>`;
    else out += `<span class="chip ctl">${esc(tag)}</span>`;
  }
  out += esc(text.slice(last));
  return out;
}

function pct(a, b) { return b ? (100 * a / b) : 0; }

// ------------------------------------------------------------------ jobs
let jobTimer = null;
async function pollJob(onDone) {
  clearInterval(jobTimer);
  const bar = $("#jobbar");
  const tick = async () => {
    let j;
    try { j = await api("/api/job"); } catch { return; }
    if (j.running) {
      bar.classList.remove("hidden");
      $("#job-name").textContent = { extract: "Extracting", build: "Building", translate: "Draft translating", pcimport: "PC translation" }[j.name] || j.name;
      $("#job-phase").textContent = j.phase || "";
      $("#job-progress").style.width = pct(j.done, j.total) + "%";
      $("#job-count").textContent = j.total ? `${j.done} / ${j.total}` : "";
      $("#job-stop").classList.toggle("hidden", !j.cancellable);
      if (j.name === "translate" && state.view === "editor" && Date.now() - (state.lastCatRefresh || 0) > 5000) {
        state.lastCatRefresh = Date.now();
        loadCategories();
      }
    } else {
      clearInterval(jobTimer);
      bar.classList.add("hidden");
      if (onDone) onDone(j);
    }
  };
  await tick();
  jobTimer = setInterval(tick, 700);
}

// ------------------------------------------------------------------ views
function show(view) {
  state.view = view;
  $$(".view").forEach((v) => v.classList.add("hidden"));
  $(`#view-${view}`).classList.remove("hidden");
  $$("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  if (view === "glossary") loadGlossary();
  if (view === "project") loadProject();
  if (view === "build") loadBuild();
  if (view === "editor") { loadCategories(); loadList(); }
}
$$("#nav button").forEach((b) => b.addEventListener("click", () => { if (state.extracted) show(b.dataset.view); }));

// ------------------------------------------------------------------ setup
async function init() {
  const s = await api("/api/state");
  state.extracted = s.extracted;
  state.config = s.config;
  $("#workdir").textContent = s.workdir;
  if (s.config.rom_path) $("#rom-path").value = s.config.rom_path;
  if (s.job && s.job.running && (s.job.name === "extract" || !s.extracted)) {
    show("setup");
    pollJob(() => init());
    return;
  }
  if (!s.extracted) { show("setup"); return; }
  show("editor");
  if (s.job && s.job.running) pollJob(s.job.name === "translate" ? afterTranslateJob : null);
}

$("#rom-browse").onclick = async () => {
  const r = await api("/api/browse", { json: { kind: "rom" } });
  if (r.path) $("#rom-path").value = r.path;
};
$("#extract-btn").onclick = async () => {
  $("#setup-msg").className = "msg";
  $("#setup-msg").textContent = "";
  try {
    await api("/api/extract", { json: { rom_path: $("#rom-path").value, dump_files: $("#dump-files").checked } });
    $("#extract-btn").disabled = true;
    pollJob((j) => {
      $("#extract-btn").disabled = false;
      if (j.error) { $("#setup-msg").className = "msg err"; $("#setup-msg").textContent = j.error; }
      else init();
    });
  } catch (e) { $("#setup-msg").className = "msg err"; $("#setup-msg").textContent = e.message; }
};

// ------------------------------------------------------------------ categories / list
async function loadCategories() {
  const r = await api("/api/categories");
  state.categories = r.categories;
  const t = r.total;
  $("#overall-done").style.width = pct(t.done || 0, t.total) + "%";
  $("#overall-draft").style.width = pct(t.draft || 0, t.total) + "%";
  $("#overall-text").textContent = `${(t.done || 0).toLocaleString()} translated · ${(t.draft || 0).toLocaleString()} draft · ${t.total.toLocaleString()} strings`;
  const ul = $("#categories");
  const showHidden = state.filters.show_hidden;
  const all = { name: "", total: t.total, done: t.done || 0, draft: t.draft || 0 };
  const list = [all, ...r.categories.filter((c) => showHidden || !c.hidden)];
  ul.innerHTML = list.map((c) => `
    <li data-cat="${esc(c.name)}" class="${c.name === state.filters.category ? "active" : ""} ${c.hidden ? "internal" : ""}">
      <div class="name"><span>${esc(c.name || "All contexts")}</span><small title="translated / total${c.draft ? ` (+${c.draft} drafts)` : ""}">${c.done}${c.draft ? `<span class="draftn">+${c.draft}</span>` : ""}/${c.total}</small></div>
      <div class="bar"><span class="d" style="width:${pct(c.done, c.total)}%"></span><span class="w" style="width:${pct(c.draft, c.total)}%"></span></div>
    </li>`).join("");
  $$("#categories li").forEach((li) => li.onclick = () => {
    state.filters.category = li.dataset.cat; state.filters.page = 1; state.filters.file = "";
    $$("#categories li").forEach((x) => x.classList.toggle("active", x === li));
    loadFiles(); loadList();
  });
}

async function loadFiles() {
  const r = await api("/api/files" + (state.filters.category ? "?category=" + encodeURIComponent(state.filters.category) : ""));
  const sel = $("#file-filter");
  sel.innerHTML = `<option value="">All files (${r.length})</option>` + r.map((f) =>
    `<option value="${esc(f.file)}" ${f.file === state.filters.file ? "selected" : ""}>${esc(f.file.replace(/^\//, ""))} (${f.n})</option>`).join("");
}

let listSeq = 0;
async function loadList(keepSelection = false) {
  const f = state.filters;
  const qs = new URLSearchParams({ page: f.page, per: f.per, status: f.status, q: f.q, sort: f.sort,
    category: f.category, file: f.file, show_hidden: f.show_hidden ? "1" : "0", pc: f.pc });
  const seq = ++listSeq;
  $("#rows").classList.add("loading");
  let r;
  try {
    r = await api("/api/strings?" + qs);
  } catch (e) {
    if (seq !== listSeq) return;
    $("#rows").classList.remove("loading");
    $("#rows").innerHTML = `<div class="msg err" style="padding:20px">Could not load the list: ${esc(e.message)}</div>`;
    clearDetail();
    return;
  }
  if (seq !== listSeq) return;   // a newer request (other context/filter) is on its way
  $("#rows").classList.remove("loading");
  state.items = r.items; state.total = r.total;
  const pages = Math.max(1, Math.ceil(r.total / f.per));
  $("#list-count").textContent = `${r.total.toLocaleString()} strings`;
  $("#page-info").textContent = `${f.page} / ${pages}`;
  $("#prev-page").disabled = f.page <= 1;
  $("#next-page").disabled = f.page >= pages;
  renderRows();
  if (!r.items.length) clearDetail();
  else if (!keepSelection && !r.items.some((i) => i.id === state.selected)) select(r.items[0].id);
}

function clearDetail() {
  state.selected = null; state.detail = null;
  $("#detail").innerHTML = `<div class="muted" style="padding:20px">Select a string on the left.</div>`;
}

function pcBadge(it) {
  if (it.origin === "pc") return ` · <span class="pcb" title="official translation from the PC version">PC</span>`;
  if (it.pc_kind === "exact") return ` · <span class="pcb" title="identical line in the PC version">PC =</span>`;
  if (it.pc_kind === "fuzzy") return ` · <span class="pcb sim" title="a similar line exists in the PC version">PC ≈${Math.round(it.pc_score * 100)}%</span>`;
  return "";
}

function renderPc(d) {
  if (!d.pc || !d.pc.length) return "";
  return `<h4>Official PC translation</h4><div class="pcs">${d.pc.map((p, i) => `
    <div class="pc">
      <div class="pc-head"><span class="pcb ${p.kind === "exact" ? "" : "sim"}">${p.kind === "exact" ? "identical Japanese" : `similar · ${Math.round(p.score * 100)}%`}</span>
        <button class="pc-use" data-i="${i}" title="Put this English into the editor">Use</button></div>
      ${p.kind === "exact" ? "" : `<div class="pc-jp" title="Japanese: red = only in the 3DS line, green = only in the PC line">${p.diff.map(([op, t]) =>
        op === "equal" ? esc(t) : `<span class="${op}">${esc(t)}</span>`).join("")}</div>`}
      <div class="pc-en">${renderTagged(p.en)}</div>
    </div>`).join("")}</div>`;
}

function renderRows() {
  $("#rows").innerHTML = state.items.map((it) => {
    const over = it.too_wide ? " · <span class='warn'>too wide</span>" : "";
    const probs = it.problems && it.problems.length ? ` · <span class="warn">⚠ ${it.problems.length}</span>` : "";
    return `<div class="rowitem ${it.id === state.selected ? "sel" : ""}" data-id="${it.id}">
      <span class="st st-${it.status}"></span>
      <div class="jp">${renderTagged(it.text, { ruby: false })}</div>
      <div class="en ${it.translation ? "" : "none"}">${it.translation ? renderTagged(it.translation) : "untranslated"}</div>
      <div class="meta"><span>${esc(it.category)}</span>${it.occurrences > 1 ? `<span>×${it.occurrences}</span>` : ""}${pcBadge(it)}${over}${probs}</div>
    </div>`;
  }).join("") || `<div class="muted" style="padding:20px">Nothing matches.</div>`;
  $$("#rows .rowitem").forEach((el) => el.onclick = () => select(+el.dataset.id));
}

$("#search").addEventListener("input", debounce(() => { state.filters.q = $("#search").value; state.filters.page = 1; loadList(); }, 300));
$$("#status-filter button").forEach((b) => b.onclick = () => {
  $$("#status-filter button").forEach((x) => x.classList.toggle("active", x === b));
  state.filters.status = b.dataset.status; state.filters.page = 1; loadList();
});
$("#show-hidden").onchange = (e) => { state.filters.show_hidden = e.target.checked; loadCategories(); loadList(); };
$("#file-filter").onchange = (e) => { state.filters.file = e.target.value; state.filters.page = 1; loadList(); };
$("#sort").onchange = (e) => { state.filters.sort = e.target.value; state.filters.page = 1; loadList(); };
$("#pc-filter").onchange = (e) => { state.filters.pc = e.target.value; state.filters.page = 1; loadList(); };
$("#prev-page").onclick = () => { state.filters.page--; loadList(); };
$("#next-page").onclick = () => { state.filters.page++; loadList(); };

function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

// ------------------------------------------------------------------ detail / editing
async function select(id) {
  state.selected = id;
  $$("#rows .rowitem").forEach((el) => el.classList.toggle("sel", +el.dataset.id === id));
  let d;
  const ctx = new URLSearchParams({ category: state.filters.category || "", file: state.filters.file || "" });
  try { d = await api(`/api/string/${id}?${ctx}`); } catch (e) { if (state.selected === id) clearDetail(); return; }
  if (state.selected !== id) return;
  state.detail = d;
  if (d.game_font) state.previewFont = d.game_font;    // preview in the font the game uses
  renderDetail();
}

function glossaryHits(text) {
  return state.glossary.filter((g) => g.jp && text.includes(g.jp));
}

function renderDetail() {
  const d = state.detail;
  const hits = glossaryHits(d.text.replace(/\{ruby:([^|{}]*)\|[^{}]*\}/g, "$1"));
  const cap = d.capacity ? `<span>max <b>${d.capacity / 2 - 1}</b> characters (executable)</span>` : "";
  $("#detail").innerHTML = `
    <h4>Japanese <span class="muted">· ${esc(d.category)}</span></h4>
    <div class="src">${renderTagged(d.text)}</div>
    <div class="ctx">${esc(d.context || "")}${d.occurrences > 1 ? ` · used ${d.occurrences}×` : ""}</div>
    ${hits.length ? `<div class="gloss-hits">${hits.map((g) => `<span class="chip" title="glossary">${esc(g.jp)} → ${esc(g.en)}</span>`).join("")}</div>` : ""}
    <h4>English</h4>
    <textarea id="tr" spellcheck="true" placeholder="Type the English translation…  ({v:XX} = name/number, {CR} + newline = next page)">${esc(d.translation || "")}</textarea>
    <div class="actions">
      <button id="save-next" class="primary" title="Ctrl+Enter">Save &amp; next</button>
      <button id="save" title="Ctrl+S">Save</button>
      <select id="status">${STATUS.map((s, i) => `<option value="${i}" ${i === d.status ? "selected" : ""}>${s}</option>`).join("")}</select>
      <button id="wrap" title="Re-flow line breaks to fit the original box (Ctrl+Shift+W)">Auto-wrap</button>
      <button id="copy-tags" title="Insert the variable tags of the source">Tags</button>
      <button id="draft1" title="Machine draft for this string">✦ Draft</button>
      <span class="spacer"></span>
      <button id="prev-item" title="Alt+↑">↑</button><button id="next-item" title="Alt+↓">↓</button>
    </div>
    ${d.origin === "pc" ? `<div class="muted small">This text is the official PC translation.</div>` : ""}
    <div class="stats" id="stats"></div>
    <div class="box-ctl" id="box-ctl"></div>
    <ul class="problems" id="problems"></ul>
    ${renderPc(d)}
    <div class="preview-head"><h4>In-game preview</h4>
      <select id="pfont" title="Font of the preview (the game uses ${esc(d.game_font || "3D14")} here)">${["3D14", "3D12", "2D14", "2D9", "2D14SH", "credit2D"].map((f) =>
        `<option value="${f}" ${f === state.previewFont ? "selected" : ""}>${f}${f === d.game_font ? " (in game)" : ""}</option>`).join("")}</select></div>
    <div class="preview-wrap"><canvas id="preview"></canvas></div>
    ${d.windows_2d ? `<div class="preview-head"><h4>2D mode (top screen; lower screen condensed)</h4></div>
    <div class="preview-wrap"><canvas id="preview2d"></canvas></div>` : ""}
    ${d.neighbors && d.neighbors.length > 1 ? `<h4>Surrounding lines (same file)</h4>
      <div class="neighbors">${d.neighbors.map((n) => `<div class="${n.current ? "cur" : ""}" data-id="${n.id}">
        <span class="jp">${renderTagged(n.text, { ruby: false })}</span><span class="en">${n.translation ? renderTagged(n.translation) : '<i class="muted">—</i>'}</span></div>`).join("")}</div>` : ""}
    <h4>Note</h4>
    <textarea class="note" id="note" placeholder="Translator notes">${esc(d.note || "")}</textarea>
    <h4>Occurrences (${d.occurrences})</h4>
    <ul class="occ">${d.occurrences_list.map((o) => `<li>${esc(o.category)} · ${esc(o.context || "")}${o.override ? ` <b>override:</b> ${esc(o.override)}` : ""}
      ${d.occurrences > 1 ? `<button data-eid="${o.id}" class="ovr">${o.override ? "edit override" : "different translation here…"}</button>` : ""}</li>`).join("")}</ul>`;
  const ta = $("#tr");
  ta.addEventListener("input", debounce(updateLive, 150));
  ta.addEventListener("keydown", editorKeys);
  $("#save").onclick = () => save(false);
  $("#save-next").onclick = () => save(true);
  $("#wrap").onclick = autoWrap;
  $("#copy-tags").onclick = () => {
    const tags = (d.text.match(/\{v:[0-9A-Fa-f]+\}/g) || []).join(" ");
    insertAtCursor(ta, tags);
    updateLive();
  };
  $("#draft1").onclick = () => draftTranslate([d.id]);
  $("#prev-item").onclick = () => move(-1);
  $("#next-item").onclick = () => move(1);
  $("#pfont").onchange = (e) => { state.previewFont = e.target.value; updateLive(); };
  renderBoxCtl();
  $$(".neighbors div").forEach((el) => el.onclick = () => select(+el.dataset.id));
  $$(".ovr").forEach((b) => b.onclick = () => editOverride(+b.dataset.eid));
  $$(".pc-use").forEach((b) => b.onclick = () => { ta.value = d.pc[+b.dataset.i].en; updateLive(); ta.focus(); });
  updateLive();
  // don't steal focus from the search box / filters when a list refresh auto-selects a row
  setTimeout(() => {
    const a = document.activeElement;
    if (!a || !a.matches("input, select, textarea") || $("#detail").contains(a)) ta.focus();
  }, 0);
}

function insertAtCursor(ta, text) {
  const s = ta.selectionStart, e = ta.selectionEnd;
  ta.value = ta.value.slice(0, s) + text + ta.value.slice(e);
  ta.selectionStart = ta.selectionEnd = s + text.length;
  ta.focus();
}

function editorKeys(e) {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save(true); }
  else if (e.key.toLowerCase() === "s" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save(false); }
  else if (e.key.toLowerCase() === "w" && e.ctrlKey && e.shiftKey) { e.preventDefault(); autoWrap(); }
  else if (e.altKey && e.key === "ArrowDown") { e.preventDefault(); move(1); }
  else if (e.altKey && e.key === "ArrowUp") { e.preventDefault(); move(-1); }
}

async function move(delta) {
  const idx = state.items.findIndex((i) => i.id === state.selected);
  const n = idx + delta;
  if (n >= 0 && n < state.items.length) return select(state.items[n].id);
  if (delta > 0 && state.filters.page * state.filters.per < state.total) {
    state.filters.page++; await loadList(true); if (state.items.length) select(state.items[0].id);
  } else if (delta < 0 && state.filters.page > 1) {
    state.filters.page--; await loadList(true); if (state.items.length) select(state.items[state.items.length - 1].id);
  }
}

async function save(next) {
  const d = state.detail;
  const tr = $("#tr").value;
  let status = +$("#status").value;
  if (tr.trim() && status < 2 && tr !== (d.translation || "")) status = 2;  // edited by hand -> translated
  if (!tr.trim()) status = 0;
  const r = await api("/api/string/" + d.id, { json: { translation: tr, status, note: $("#note").value } });
  const i = state.items.findIndex((x) => x.id === d.id);
  if (i >= 0) state.items[i] = { ...state.items[i], ...r };
  renderRows();
  loadCategories();
  if (next) move(1); else { state.detail = { ...d, ...r }; $("#status").value = r.status; updateProblems(r.problems || []); }
}

// window size set by hand: for texts that cannot be shortened. Layout panes are widened in
// the exported game files; for table texts the automatic size is only an estimate.
function renderBoxCtl() {
  const d = state.detail, custom = d.box_auto_width != null;
  const pane = d.kind === "bflyt", help = d.help_window;
  $("#box-ctl").innerHTML = `<span title="${help ? `Menu help window: shared by all menu help texts. The exporter widens it (text and frame) to the widest size set on any of them, at most ${d.help_max}px; it keeps 3 lines.`
      : pane ? "Layout pane: the exporter widens the pane in the game files to this width (lines are only checked)."
      : "The automatic size is the widest Japanese line of this text table, an estimate of the real window. It only changes the checks here, not the game."}">
    ${help ? "Help window" : "Window"} <input id="bw" type="number" min="8" max="${help ? d.help_max : 1000}" value="${d.box_width}"> px ×
    <input id="bl" type="number" min="1" max="${help ? 3 : 30}" value="${d.box_lines}"> lines</span>
    <button id="box-set">Set</button><button id="box-fit" title="Make the window as large as the current translation">Fit to text</button>
    ${custom ? `<button id="box-auto">Automatic (${d.box_auto_width}px × ${d.box_auto_lines})</button><span class="custom">${help ? "shared help window widened" : "custom size"}</span>` : ""}
    ${!pane && !help ? `<span class="muted">checks only</span>` : ""}`;
  const setBox = async (width, lines) => {
    const r = await api(`/api/string/${d.id}/box`, { json: { width, lines } });
    if (state.detail !== d) return;
    Object.assign(d, r);
    const i = state.items.findIndex((x) => x.id === d.id);
    if (i >= 0) { state.items[i] = { ...state.items[i], ...r }; renderRows(); }
    renderBoxCtl(); updateLive(); updateProblems(r.problems || []);
  };
  $("#box-set").onclick = () => setBox(+$("#bw").value || null, +$("#bl").value || null);
  $("#box-fit").onclick = async () => {
    const font = await loadFont(d.game_font || state.previewFont), lines = layout($("#tr").value, font);
    setBox(Math.max(d.box_width, ...lines.map((l) => lineWidth(font, l))), Math.max(d.box_lines, lines.length));
  };
  if (custom) $("#box-auto").onclick = () => setBox(null, null);
}

async function autoWrap() {
  const r = await api("/api/wrap", { json: { text: $("#tr").value, source: state.detail.text, font: state.previewFont, id: state.detail.id } });
  $("#tr").value = r.text;
  updateLive();
}

async function editOverride(eid) {
  const o = state.detail.occurrences_list.find((x) => x.id === eid);
  const body = `<h3>Translation for this occurrence only</h3><p class="muted">${esc(o.context || "")}</p>
    <textarea id="ovr-text" style="width:100%;min-height:90px">${esc(o.override || state.detail.translation || "")}</textarea>
    <p class="muted">Leave empty to use the shared translation.</p>`;
  if (await modal(body)) {
    await api(`/api/entry/${eid}/override`, { json: { text: $("#ovr-text").value } });
    select(state.detail.id);
  }
}

function modal(html) {
  return new Promise((res) => {
    $("#modal-body").innerHTML = html;
    $("#modal").classList.remove("hidden");
    $("#modal-ok").onclick = () => { $("#modal").classList.add("hidden"); res(true); };
    $("#modal-cancel").onclick = () => { $("#modal").classList.add("hidden"); res(false); };
  });
}

// ------------------------------------------------------------------ live stats + preview
function loadFont(name) {
  if (!state.fonts[name]) {
    state.fonts[name] = (async () => {
      const meta = await api(`/api/font/${name}/meta`);
      meta.images = await Promise.all(Array.from({ length: meta.sheets }, (_, i) => {
        const img = new Image();
        img.src = `/api/font/${name}/sheet${i}.png?v=${meta.key}`;
        return img.decode().then(() => img);
      }));
      return meta;
    })();
    state.fonts[name].catch(() => { delete state.fonts[name]; });
  }
  return state.fonts[name];
}

function layout(text, font) {
  // -> lines of units: {ch} | {v} | {wait}; {CR} is a "wait for button" marker, not a line break
  const lines = [[]];
  const re = /\{\{|\}\}|\{([^{}]*)\}|\n|[\s\S]/g;
  let m;
  while ((m = re.exec(text || ""))) {
    const t = m[0];
    const line = lines[lines.length - 1];
    if (t === "\n") { lines.push([]); continue; }
    if (t === "{{" || t === "}}") { line.push({ ch: t[0] }); continue; }
    if (m[1] !== undefined) {
      const tag = m[1];
      if (tag === "CR") line.push({ wait: true });
      else if (tag.startsWith("ruby:")) for (const c of tag.slice(5).split("|")[0]) line.push({ ch: c });
      else if (tag.startsWith("v:")) line.push({ v: tag.slice(2) });
      continue;
    }
    line.push({ ch: t });
  }
  return lines;
}

function glyphWidth(font, u) {
  if (u.wait) return 0;
  if (u.v !== undefined) return font.glyphs[77] ? Math.round(font.glyphs[77][5] * 8 * 2 / 3) : 50;  // a typical name
  const g = font.glyphs[u.ch.codePointAt(0)];
  return g ? g[5] : font.cell_w;
}

const lineWidth = (font, line) => line.reduce((a, u) => a + glyphWidth(font, u), 0);

let previewSeq = 0;
async function drawPreview(text, srcText, box, canvasId = "#preview", fontName = null) {
  const seq = canvasId === "#preview" ? ++previewSeq : previewSeq;
  const font = await loadFont(fontName || state.previewFont);
  const canvas = $(canvasId);
  if (!canvas || seq !== previewSeq) return;
  const srcLines = layout(srcText, font);
  // a fixed window (2D mode) is not sized like the Japanese text
  const boxW = box && box.fixed ? box.width : Math.max(box && box.width || 0, ...srcLines.map((l) => lineWidth(font, l)), 60);
  const boxLines = Math.max(box && box.lines || 0, srcLines.length, 1);
  const lines = layout(text, font);
  const lh = font.line_feed + 2, pad = 8, scale = 2;
  const shown = Math.max(lines.length, boxLines);
  const totalW = Math.max(boxW, ...lines.map((l) => lineWidth(font, l))) + pad * 2 + 24;
  const totalH = shown * lh + pad * 2 + 4;
  canvas.width = totalW * scale;
  canvas.height = totalH * scale;
  canvas.style.width = (totalW * scale) + "px";
  const ctx = canvas.getContext("2d");
  ctx.imageSmoothingEnabled = false;
  ctx.scale(scale, scale);
  // message window sized like the original text
  ctx.fillStyle = "#0b0d16"; ctx.strokeStyle = "#e9e9f2"; ctx.lineWidth = 1.5;
  roundRect(ctx, 1, 1, boxW + pad * 2, boxLines * lh + pad * 2 - 2, 5); ctx.fill(); ctx.stroke();
  lines.forEach((line, li) => {
    let x = pad;
    const y = pad + li * lh;
    const lw = lineWidth(font, line);
    for (const u of line) {
      if (u.wait) {  // the blinking "next" arrow
        ctx.fillStyle = "#e8b84a";
        ctx.beginPath(); ctx.moveTo(x + 2, y + 6); ctx.lineTo(x + 8, y + 6); ctx.lineTo(x + 5, y + 10); ctx.fill();
        continue;
      }
      if (u.v !== undefined) {
        const w = glyphWidth(font, u);
        ctx.fillStyle = "#6b4fb0"; ctx.fillRect(x, y + 2, w - 2, font.height - 4);
        ctx.fillStyle = "#fff"; ctx.font = "9px sans-serif"; ctx.fillText("◆" + u.v, x + 2, y + font.height - 5);
        x += w; continue;
      }
      const g = font.glyphs[u.ch.codePointAt(0)];
      if (!g) { ctx.fillStyle = "#e86a5a"; ctx.fillRect(x, y, font.cell_w - 2, font.height); x += font.cell_w; continue; }
      const [sheet, sx, sy, left, gw, cw] = g;
      if (gw) ctx.drawImage(font.images[sheet], sx, sy, gw, font.cell_h, x + left, y, gw, font.cell_h);
      x += cw;
    }
    if (lw > boxW) { ctx.fillStyle = "rgba(232,106,90,.4)"; ctx.fillRect(pad + boxW, y, lw - boxW, lh - 2); }
    if (li >= boxLines) { ctx.fillStyle = "rgba(232,106,90,.25)"; ctx.fillRect(1, y, Math.max(lw, boxW) + pad * 2, lh - 1); }
  });
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
}

async function updateLive() {
  const d = state.detail;
  if (!d || !$("#tr")) return;
  const font = await loadFont(state.previewFont);
  if (state.detail !== d || !$("#tr")) return;   // another string was selected meanwhile
  const text = $("#tr").value;
  const srcLines = layout(d.text, font), lines = layout(text, font);
  const sw = Math.max(...srcLines.map((l) => lineWidth(font, l)));
  const tw = Math.max(...lines.map((l) => lineWidth(font, l)));
  const boxW = d.box_width || Math.max(sw, 40), boxL = d.box_lines || srcLines.length;
  const chars = text.replace(/\{[^{}]*\}/g, "").length;
  const capInfo = d.capacity ? `<span class="${chars > d.capacity / 2 - 1 ? "over" : ""}">limit <b>${d.capacity / 2 - 1}</b> chars</span>` : "";
  $("#stats").innerHTML = `<span title="widest line of the translation / width of the window this text is shown in (Japanese: ${sw}px)">width <b class="${tw > boxW ? "over" : ""}">${text ? tw : 0}px</b> / ${boxW}px</span>
    <span>lines <b class="${text && lines.length > boxL ? "over" : ""}">${text ? lines.length : 0}</b> / ${boxL}</span>
    <span title="rough budget the auto-translation aims for">${chars} / ~${d.max_chars || "?"} chars</span>${capInfo}`;
  drawPreview(text, d.text, { width: boxW, lines: boxL });
  const probs2d = [];
  for (const w of d.windows_2d || []) {
    drawPreview(text, d.text, { width: w.width, lines: boxL, fixed: true }, "#preview2d", w.font);
    const f2 = await loadFont(w.font);
    const w2 = text ? Math.max(...layout(text, f2).map((l) => lineWidth(f2, l))) : 0;
    if (w2 > w.width) probs2d.push(`too wide for the 2D-mode message window (${w2}px > ${w.width}px)`);
  }
  // client-side checks (the server validates on save too)
  const need = d.text.match(/\{v:[0-9A-Fa-f]+\}/g) || [];
  const have = text.match(/\{v:[0-9A-Fa-f]+\}/g) || [];
  const probs = [];
  if (text) {
    const miss = need.filter((t) => !have.includes(t)); if (miss.length) probs.push("missing tags: " + [...new Set(miss)].join(" "));
    const extra = have.filter((t) => !need.includes(t)); if (extra.length) probs.push("unknown tags: " + [...new Set(extra)].join(" "));
    const missingGlyphs = [...new Set([...text.replace(/\{[^{}]*\}/g, "")].filter((c) => c !== "\n" && !font.glyphs[c.codePointAt(0)]))];
    if (missingGlyphs.length) probs.push("characters not in the game font: " + missingGlyphs.join(" "));
    if (tw > boxW) probs.push(`too wide for its window (${tw}px > ${boxW}px)`);
    if (lines.length > boxL) probs.push(`more lines than its window has (${lines.length} > ${boxL})`);
    probs.push(...probs2d);
  }
  updateProblems(probs);
}

function updateProblems(list) { $("#problems").innerHTML = list.map((p) => `<li>${esc(p)}</li>`).join(""); }

// ------------------------------------------------------------------ machine drafts
async function draftTranslate(ids, scope) {
  const { settings } = await api("/api/mt/settings");
  const svc = settings.backend === "openai" ? `${settings.model || "(no model set)"} via ${settings.base_url || "(no URL)"}`
    : settings.backend === "claude" ? "Claude" : "Google Translate";
  const ready = settings.backend === "google" || settings.backend === "claude" || (settings.model && settings.base_url && settings.has_key);
  const body = `<h3>Auto-translate</h3>
    <p class="muted">${ids ? `${ids.length} string(s)` : `All untranslated strings and drafts that are too wide in <b>${esc(scope.category || "all contexts")}</b>`} will get a <b>draft</b>
    translation (yellow). Edit and save a draft to mark it as translated. Existing translations are not touched.</p>
    <p>Service: <b>${esc(svc)}</b></p>
    ${ready ? "" : `<p class="msg err">The service is not fully configured yet (model, URL or API key missing). Open <b>Project → Auto-translate</b> first.</p>`}`;
  if (!(await modal(body))) return;
  if (!ready) { show("project"); return; }
  try {
    await api("/api/mt", { json: { ids, scope: scope && { ...scope, include_too_wide: true } } });
    pollJob(afterTranslateJob);
  } catch (e) { alert(e.message); }
}
$("#mt-view").onclick = () => draftTranslate(null, { category: state.filters.category });

function afterTranslateJob(j) {
  const msg = j.error ? "Auto-translate failed: " + j.error
    : `Auto-translate ${j.result.cancelled ? "stopped" : "finished"}: ${j.result.translated} drafts, ${j.result.failed} failed` +
      (j.result.tag_warnings ? `, ${j.result.tag_warnings} with tag warnings` : "") +
      (j.result.shortened ? `, ${j.result.shortened} shortened to fit` : "") +
      (j.result.too_wide ? `, ${j.result.too_wide} still too wide (filter the list by ⚠ / sort by length)` : "") +
      (j.result.errors && j.result.errors.length ? ` — last error: ${j.result.errors[j.result.errors.length - 1]}` : "");
  const el = $("#mt-msg");
  if (el) { el.textContent = msg; el.className = j.error ? "msg err" : "msg"; }
  $("#mt-stop") && $("#mt-stop").classList.add("hidden");
  if (state.view === "editor") { loadCategories(); loadList(true); if (state.selected) select(state.selected); }
  else if (state.view !== "project") alert(msg);
}

$("#job-stop").onclick = () => api("/api/job/cancel", { json: {} });

// ------------------------------------------------------------------ glossary
async function loadGlossary() {
  state.glossary = await api("/api/glossary");
  const body = $("#gloss-body");
  const rowHtml = (g) => `<tr><td><input value="${esc(g.jp)}"></td><td><input value="${esc(g.en)}"></td><td><button class="del">✕</button></td></tr>`;
  body.innerHTML = state.glossary.map(rowHtml).join("");
  const bindDel = () => $$("#gloss-body .del").forEach((b) => b.onclick = () => b.closest("tr").remove());
  bindDel();
  $("#gloss-add").onclick = () => { body.insertAdjacentHTML("beforeend", rowHtml({ jp: "", en: "" })); bindDel(); };
  $("#gloss-save").onclick = async () => {
    const items = $$("#gloss-body tr").map((tr) => ({ jp: $$("input", tr)[0].value.trim(), en: $$("input", tr)[1].value.trim() })).filter((g) => g.jp);
    await api("/api/glossary", { json: items });
    state.glossary = items;
    $("#gloss-msg").textContent = `Saved ${items.length} terms.`;
  };
}

// ------------------------------------------------------------------ project
let mtPresets = [];
async function loadProject() {
  const s = await api("/api/state");
  const c = s.config;
  const rows = [["Game image", c.rom_path], ["Product code", c.product_code], ["Title ID", c.title_id],
    ["Workspace", s.workdir], ["Extracted", c.extracted_at ? new Date(c.extracted_at * 1000).toLocaleString() : "–"]];
  $("#project-info").innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v || "–")}</dd>`).join("");
  // auto-translate settings
  const [backends, ms, cats] = await Promise.all([api("/api/mt/backends"), api("/api/mt/settings"), api("/api/categories")]);
  mtPresets = ms.presets;
  const st = ms.settings;
  $("#mt-backend").innerHTML = backends.map((b) => `<option value="${b.id}" ${b.id === st.backend ? "selected" : ""}>${esc(b.name)}</option>`).join("");
  $("#mt-preset").innerHTML = mtPresets.map((p) => `<option value="${p.id}" ${p.id === st.preset ? "selected" : ""}>${esc(p.name)}</option>`).join("");
  $("#mt-base").value = st.base_url || "";
  $("#mt-model").value = st.model || "";
  if (st.model) fillModelSelect([]);
  $("#mt-conc").value = st.concurrency;
  $("#mt-batch").value = st.batch_size;
  $("#mt-remember").checked = !!st.remember_key;
  $("#mt-key").value = "";
  setKeyState(st);
  toggleBackendFields();
  const visible = cats.categories.filter((x) => !x.hidden);
  const untranslated = (x) => x.total - x.done - x.draft;
  const all = visible.reduce((a, x) => a + untranslated(x), 0);
  $("#mt-scope").innerHTML = `<option value="">Everything untranslated (~${all.toLocaleString()})</option>` + visible.map((x) =>
    `<option value="${esc(x.name)}">${esc(x.name)} (${untranslated(x).toLocaleString()} untranslated)</option>`).join("");
  updateTooWide();
  loadPcStatus();
  const j = await api("/api/job");
  $("#mt-stop").classList.toggle("hidden", !(j.running && j.name === "translate"));
}

function setKeyState(st) {
  const hint = (mtPresets.find((p) => p.id === $("#mt-preset").value) || {}).key_hint;
  $("#mt-key-state").textContent = st.has_key ? (st.remember_key ? "A key is saved on this computer." : "A key is set for this session.")
    + " Leave empty to keep it." : (hint ? "Get a key: " + hint : "");
}

function toggleBackendFields() {
  const b = $("#mt-backend").value;
  $("#mt-openai").classList.toggle("hidden", b !== "openai");
  $("#mt-key-field").classList.toggle("hidden", b === "google");
  $("#mt-remember").closest("label").classList.toggle("hidden", b === "google");
}

function mtFormSettings() {
  return {
    backend: $("#mt-backend").value, preset: $("#mt-preset").value, base_url: $("#mt-base").value.trim(),
    model: $("#mt-model").value.trim(), concurrency: +$("#mt-conc").value || 4, batch_size: +$("#mt-batch").value || 30,
    remember_key: $("#mt-remember").checked,
  };
}

async function saveMtSettings() {
  const r = await api("/api/mt/settings", { json: { ...mtFormSettings(), api_key: $("#mt-key").value } });
  $("#mt-key").value = "";
  setKeyState(r.settings);
  return r.settings;
}

function fillModelSelect(models) {
  const cur = $("#mt-model").value.trim();
  const list = cur && !models.includes(cur) ? [cur, ...models] : models;
  $("#mt-model-select").innerHTML = (list.length ? "" : `<option value="">— no models returned —</option>`) +
    list.map((m) => `<option value="${esc(m)}" ${m === cur ? "selected" : ""}>${esc(m)}</option>`).join("");
  if (!cur && list.length) $("#mt-model").value = $("#mt-model-select").value;
}

$("#mt-model-select").onchange = () => { if ($("#mt-model-select").value) $("#mt-model").value = $("#mt-model-select").value; };
$("#mt-model").oninput = () => {
  const sel = $("#mt-model-select"), v = $("#mt-model").value.trim();
  if ([...sel.options].some((o) => o.value === v)) sel.value = v;
};
async function updateTooWide() {
  const el = $("#mt-too-wide-n");
  el.textContent = "(counting…)";
  try {
    const cat = $("#mt-scope").value;
    const r = await api("/api/mt/too-wide" + (cat ? "?category=" + encodeURIComponent(cat) : ""));
    if ($("#mt-scope").value === cat) el.textContent = `(${r.count.toLocaleString()} in this scope)`;
  } catch { el.textContent = ""; }
}
$("#mt-scope").onchange = updateTooWide;
$("#mt-backend").onchange = toggleBackendFields;
$("#mt-preset").onchange = () => {
  const p = mtPresets.find((x) => x.id === $("#mt-preset").value);
  if (p && p.base_url) $("#mt-base").value = p.base_url;
  setKeyState({});
};
$("#mt-load-models").onclick = async () => {
  $("#mt-set-msg").className = "msg"; $("#mt-set-msg").textContent = "Loading models…";
  try {
    const r = await api("/api/mt/models", { json: { base_url: $("#mt-base").value.trim(), api_key: $("#mt-key").value } });
    $("#mt-models").innerHTML = r.models.map((m) => `<option value="${esc(m)}">`).join("");
    // preselect the newest DeepSeek flash model if nothing is chosen yet
    if (!$("#mt-model").value) {
      const ds = r.models.filter((m) => /deepseek/i.test(m) && /flash/i.test(m)).sort().reverse();
      if (ds.length) $("#mt-model").value = ds[0];
    }
    fillModelSelect(r.models);
    $("#mt-set-msg").textContent = `${r.models.length} models available` +
      (r.models.some((m) => /deepseek/i.test(m)) ? " — DeepSeek: " + r.models.filter((m) => /deepseek/i.test(m)).join(", ") : "");
  } catch (e) { $("#mt-set-msg").className = "msg err"; $("#mt-set-msg").textContent = e.message; }
};
$("#mt-save").onclick = async () => {
  try { await saveMtSettings(); $("#mt-set-msg").className = "msg"; $("#mt-set-msg").textContent = "Saved."; }
  catch (e) { $("#mt-set-msg").className = "msg err"; $("#mt-set-msg").textContent = e.message; }
};
$("#mt-test").onclick = async () => {
  $("#mt-set-msg").className = "msg"; $("#mt-set-msg").textContent = "Testing…";
  $("#mt-test-out").innerHTML = "";
  try {
    await saveMtSettings();
    const r = await api("/api/mt/test", { json: { settings: mtFormSettings() } });
    $("#mt-set-msg").textContent = "Works (nothing was saved):";
    $("#mt-test-out").innerHTML = `<div class="neighbors">${r.results.map((x) =>
      `<div><span class="jp">${renderTagged(x.japanese, { ruby: false })}</span><span class="en">${renderTagged(x.english)}</span></div>`).join("")}</div>`;
  } catch (e) { $("#mt-set-msg").className = "msg err"; $("#mt-set-msg").textContent = e.message; }
};
$("#mt-start").onclick = async () => {
  try {
    const st = await saveMtSettings();
    if (st.backend === "openai" && (!st.model || !st.base_url || !st.has_key)) throw new Error("Set base URL, model and API key first.");
    const scope = { category: $("#mt-scope").value, include_too_wide: $("#mt-too-wide").checked };
    const overwrite = $("#mt-overwrite").checked;
    if (overwrite && !(await modal("<h3>Replace existing translations?</h3><p>Existing drafts <b>and finished translations</b> in this scope will be replaced by machine drafts.</p>"))) return;
    await api("/api/mt", { json: { scope, overwrite } });
    $("#mt-msg").className = "msg"; $("#mt-msg").textContent = "Running… (progress is shown at the top)";
    $("#mt-stop").classList.remove("hidden");
    pollJob(afterTranslateJob);
  } catch (e) { $("#mt-msg").className = "msg err"; $("#mt-msg").textContent = e.message; }
};
$("#mt-stop").onclick = () => { api("/api/job/cancel", { json: {} }); $("#mt-msg").textContent = "Stopping after the running requests…"; };
async function loadPcStatus() {
  try {
    const st = await api("/api/pc/status");
    if (!$("#pc-path").value) $("#pc-path").value = st.imported_from || st.default_path;
    if (!$("#pc-jp").value && st.jp_pak) $("#pc-jp").value = st.jp_pak;
    $("#pc-info").innerHTML = st.lines ? [["PC lines imported", `${st.lines.toLocaleString()} (${st.with_jp.toLocaleString()} with Japanese)`],
      ["Identical Japanese (official English used)", `${st.exact.toLocaleString()} strings · ${st.official.toLocaleString()} filled in`],
      ["Similar PC line (suggestion + AI reference)", st.similar.toLocaleString()]].map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")
      : `<dt>Status</dt><dd>not imported yet</dd>`;
  } catch (e) { $("#pc-info").innerHTML = `<dt>Status</dt><dd>${esc(e.message)}</dd>`; }
}
async function startPc(rematchOnly) {
  $("#pc-msg").className = "msg"; $("#pc-msg").textContent = "";
  try {
    await api("/api/pc/import", { json: { path: $("#pc-path").value.trim(), jp_pak: $("#pc-jp").value.trim(), rematch_only: rematchOnly } });
    $("#pc-msg").textContent = "Running… (progress is shown at the top)";
    pollJob((j) => {
      $("#pc-msg").className = j.error ? "msg err" : "msg";
      $("#pc-msg").textContent = j.error ? j.error : `Done: ${j.result.exact.toLocaleString()} identical (${j.result.applied.toLocaleString()} filled in), ${j.result.fuzzy.toLocaleString()} similar, ${j.result.none.toLocaleString()} without a match.`;
      loadPcStatus();
    });
  } catch (e) { $("#pc-msg").className = "msg err"; $("#pc-msg").textContent = e.message; }
}
$("#pc-import").onclick = () => startPc(false);
$("#pc-rematch").onclick = () => startPc(true);
$("#pc-jp-browse").onclick = async () => { const r = await api("/api/browse", { json: { kind: "pak" } }); if (r.path) $("#pc-jp").value = r.path; };
$("#pc-browse").onclick = async () => { const r = await api("/api/browse", { json: { kind: "dir" } }); if (r.path) $("#pc-path").value = r.path; };
$("#reextract").onclick = () => { show("setup"); };
$("#open-workdir").onclick = () => api("/api/open-folder", { json: {} });
$("#import-btn").onclick = async () => {
  const f = $("#import-file").files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append("file", f);
  fd.append("overwrite", $("#import-overwrite").checked ? "1" : "0");
  try {
    const r = await api("/api/translations/import", { method: "POST", body: fd });
    $("#import-msg").textContent = `Imported ${r.imported}, skipped ${r.skipped}.`;
  } catch (e) { $("#import-msg").textContent = e.message; }
};

// ------------------------------------------------------------------ build
async function loadBuild() {
  const d = await api("/api/build/defaults");
  $("#out-dir").value = d.output_dir;
}
$("#out-browse").onclick = async () => { const r = await api("/api/browse", { json: { kind: "dir" } }); if (r.path) $("#out-dir").value = r.path; };
$("#build-btn").onclick = async () => {
  const opts = {};
  ["make_3ds", "make_layeredfs", "make_cia", "include_drafts", "font_fixes", "latin_keyboard", "verify", "keep_update_partitions"].forEach((k) => opts[k] = $("#o-" + k).checked);
  opts.expand_text_buffers = parseFloat($("#o-expand_text_buffers").value) || 1;
  $("#build-result").innerHTML = "";
  $("#build-msg").className = "msg";
  try {
    await api("/api/build", { json: { options: opts, output_dir: $("#out-dir").value } });
    $("#build-btn").disabled = true;
    $("#build-msg").textContent = "Building…";
    pollJob((j) => {
      $("#build-btn").disabled = false;
      if (j.error) { $("#build-msg").className = "msg err"; $("#build-msg").textContent = j.error; return; }
      $("#build-msg").textContent = `Finished in ${j.result.seconds}s`;
      renderBuildResult(j.result);
    });
  } catch (e) { $("#build-msg").className = "msg err"; $("#build-msg").textContent = e.message; }
};

function renderBuildResult(r) {
  const outs = Object.entries(r.outputs).map(([k, p]) => `<li><b>${k}</b>: <code>${esc(p)}</code> <button data-path="${esc(p)}" class="openf">Open folder</button></li>`).join("");
  const warn = r.warnings.map((w) => `<tr><td>${esc(w.text)}</td><td>${esc(w.problem)}</td></tr>`).join("");
  const skip = r.skipped.map((w) => `<tr><td>${esc(w.text)}</td><td>${esc(w.reason)}</td></tr>`).join("");
  $("#build-result").innerHTML = `<div class="result">
    <p>Applied <b>${r.applied.toLocaleString()}</b> strings to <b>${r.files_changed}</b> files.</p>
    <ul class="outputs">${outs}</ul>
    ${r.skipped_count ? `<h3>Skipped (${r.skipped_count})</h3><table>${skip}</table>` : ""}
    ${r.warning_count ? `<h3>Warnings (${r.warning_count})</h3><table>${warn}</table>` : ""}</div>`;
  $$(".openf").forEach((b) => b.onclick = () => api("/api/open-folder", { json: { path: b.dataset.path } }));
}

// ------------------------------------------------------------------ start
api("/api/glossary").then((g) => state.glossary = g).catch(() => {});
init().catch((e) => { document.body.insertAdjacentHTML("beforeend", `<p class="msg err" style="padding:20px">${esc(e.message)}</p>`); });
