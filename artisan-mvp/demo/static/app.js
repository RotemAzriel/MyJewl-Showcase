// Aureon guided builder: ring type -> style -> one question per screen -> Review -> build -> Reveal.
//
// One client-side state object (`state`) holds every answer. Each key maps 1:1 onto
// canonical_spec_v0_1 (demo/funnel.py: STATE_TO_SPEC). Every change is validated with
// POST /api/plan (no Rhino); the build runs only from Review with POST /api/build.
// The 3D view appears only on the Reveal, through the swappable viewer in viewers.js.

import { createViewer, preloadViewer } from "/static/viewers.js";

const params = new URLSearchParams(location.search);
const PREFER_VIEWER = params.get("viewer") || "ijewel";
const STORE_KEY = "aureon.funnel.v3";

const COPY = {
  type: { q: "What kind of ring?", lede: "Pick a ring type. Every ring is built to your stone's exact measurements, and you can change anything before you create it." },
  style: { q: "Choose a style", lede: "Each style is built to your stone's exact measurements. Fine-tune the small diamonds below if you like." },
  shape: { q: "What shape is the stone?", lede: "Pick the shape of the center stone." },
  stone: { q: "How big is the stone?", lede: "Measurements from the stone's grading report give the exact fit. Carat is shown as a guide." },
  metal: { q: "Which metal?", lede: "Weight and price are worked out for the metal you choose." },
  size: { q: "What ring size?", lede: "US or EU sizes. Not sure? There are tips below." },
  band: { q: "How wide should the band be?", lede: "This is the width you see on the finger." },
  review: { q: "Review your ring", lede: "Check the details, then create the ring. It takes a few seconds." },
};

const SHAPE_SVG = {
  round: '<circle cx="24" cy="24" r="17"/><circle cx="24" cy="24" r="9" class="t"/>',
  oval: '<ellipse cx="24" cy="24" rx="12.5" ry="18"/><ellipse cx="24" cy="24" rx="6.5" ry="10" class="t"/>',
  cushion: '<rect x="7" y="7" width="34" height="34" rx="11"/><rect x="15" y="15" width="18" height="18" rx="5" class="t"/>',
  princess: '<rect x="8" y="8" width="32" height="32" rx="1"/><path d="M8 8L40 40M40 8L8 40" class="t"/>',
  emerald: '<path d="M17 5h14l5 5v28l-5 5H17l-5-5V10z"/><path d="M19 11h10l2 2v22l-2 2H19l-2-2V13z" class="t"/>',
  radiant: '<path d="M15 8h18l5 5v22l-5 5H15l-5-5V13z"/><path d="M18 14h12l2 2v16l-2 2H18l-2-2V16z" class="t"/>',
  pear: '<path d="M24 4c7 9 14 17 14 26a14 14 0 0 1-28 0c0-9 7-17 14-26z"/><path d="M24 17c3 4 6 8 6 13a6 6 0 0 1-12 0c0-5 3-9 6-13z" class="t"/>',
  marquise: '<path d="M24 3c11 10 11 32 0 42C13 35 13 13 24 3z"/><path d="M24 14c4.5 5 4.5 15 0 20-4.5-5-4.5-15 0-20z" class="t"/>',
};

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n, d = 2) => (typeof n === "number" && isFinite(n) ? n.toFixed(d) : "–");
const trim0 = (n) => (typeof n === "number" ? String(+n.toFixed(2)) : String(n ?? ""));

let CAT = null;         // GET /api/styles
let state = null;       // the single funnel state
let plan = null;        // last POST /api/plan response
let planFor = "";       // JSON of the state that `plan` answers
let step = "style";
let result = null;      // last POST /api/build response
let health = { rhino: null };
let viewer = null;
const viewerHost = document.createElement("div");
viewerHost.className = "viewer-host";
const ui = { carat: 1, maxStep: 0, advancedBand: false, fineTune: false, startedAt: null };

// ---------------------------------------------------------------- state
function stepIds() { return CAT.steps.map((s) => s.id); }
function stepIndex(id) { return stepIds().indexOf(id); }
function styleOf(id = state.style) { return CAT.styles.find((s) => s.id === id) || CAT.styles[0]; }
function typeOf(id = state.ring_type) { return CAT.ring_types.find((t) => t.id === id) || CAT.ring_types[0]; }
function stylesOfType(t = typeOf()) { return CAT.styles.filter((s) => s.ring_type === t.id); }
function coverageOf(id = state.coverage) { return CAT.coverages.find((c) => c.id === id) || CAT.coverages[0]; }
function alloyOf(id = state.alloy) { return CAT.alloys.find((a) => a.id === id) || CAT.alloys[0]; }
function bandOf(id = state.band) { return CAT.bands.find((b) => b.id === id) || CAT.bands[1]; }
function shapeLabel(id = state.shape) { return CAT.shapes[id] ? CAT.shapes[id].label : id; }

function save() {
  try { sessionStorage.setItem(STORE_KEY, JSON.stringify({ state, ui: { carat: ui.carat, maxStep: ui.maxStep } })); } catch (e) { /* private mode */ }
}
function restore() {
  try {
    const raw = JSON.parse(sessionStorage.getItem(STORE_KEY) || "null");
    if (raw && raw.state && CAT.styles.some((s) => s.id === raw.state.style)) {
      state = { ...CAT.defaults, ...raw.state };
      state.ring_type = styleOf(state.style).ring_type;
      ui.carat = raw.ui ? raw.ui.carat : 1;
      ui.maxStep = raw.ui ? raw.ui.maxStep || 0 : 0;
      return true;
    }
  } catch (e) { /* ignore */ }
  return false;
}
function setTypicalDims(carat) {
  const dims = CAT.shapes[state.shape].typical[String(carat)];
  if (dims) Object.assign(state, dims);
}
function update(patch) {
  Object.assign(state, patch);
  save();
  validateSoon();
}
function num(value) {
  const s = String(value).trim().replace(",", ".");
  if (s === "") return "";
  const n = Number(s);
  return isFinite(n) ? n : s;
}

// ---------------------------------------------------------------- server
let timer = null, inflight = null;
function validateSoon() { clearTimeout(timer); timer = setTimeout(validate, 140); }
async function validate() {
  clearTimeout(timer);
  const body = JSON.stringify(state);
  if (body === planFor && plan) return plan;
  const req = (async () => {
    try {
      const res = await fetch("/api/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body });
      const p = await res.json();
      if (body === JSON.stringify(state)) { plan = p; planFor = body; }
    } catch (e) {
      plan = { ok: false, issues: [{ step: "review", level: "block", text: "The ring builder isn't responding.", fix: "Check that the Aureon demo server is running, then try again.", rule: "" }] };
      planFor = "";
    }
    refreshDynamic();
    return plan;
  })();
  inflight = req;
  return req;
}
async function checkHealth() {
  try { health = await (await fetch("/api/health")).json(); } catch (e) { health = { rhino: false, error: "server offline" }; }
  const el = $("#status");
  el.className = "status " + (health.rhino ? "ok" : "down");
  el.title = health.rhino ? `Connected: ${health.server}` : "Open Rhino with RhinoArtisan on this computer (MCP on 127.0.0.1:9280).";
  $("span", el).textContent = health.rhino ? "Builder ready" : "Builder offline";
  if (step === "review") refreshDynamic();
}

// ---------------------------------------------------------------- issues
function issuesFor(id) {
  if (!plan || !plan.issues) return [];
  if (id === "review") return plan.issues.filter((i) => i.level !== "info");
  return plan.issues.filter((i) => i.step === id);
}
function blocking(id) { return issuesFor(id).filter((i) => i.level === "block"); }
function messagesHtml(id) {
  const icons = { block: "!", warn: "!", tip: "i", note: "i", info: "i" };
  return issuesFor(id).map((i) => {
    const go = id === "review" && i.step !== "review" && stepIndex(i.step) >= 0
      ? `<button class="go" type="button" data-goto="${esc(i.step)}">${i.level === "block" ? "Fix" : "Change"}</button>` : "";
    return `<div class="msg ${esc(i.level)}" role="${i.level === "block" ? "alert" : "note"}"><span class="ico" aria-hidden="true">${icons[i.level] || "i"}</span>`
      + `<span class="txt">${esc(i.text)}${i.fix ? `<span class="fix">${esc(i.fix)}</span>` : ""}</span>${go}</div>`;
  }).join("");
}

// ---------------------------------------------------------------- navigation
function goTo(id, { push = true } = {}) {
  step = id;
  const idx = stepIndex(id);
  if (idx > ui.maxStep) ui.maxStep = idx;
  save();
  if (push) history.pushState({ step: id }, "", "#" + id);
  render();
  window.scrollTo(0, 0);
  const h = $("#screen h1");
  if (h) h.focus({ preventScroll: true });
  if (id === "review") { preloadViewer(PREFER_VIEWER); checkHealth(); }
}
async function next() {
  if (step === "reveal") return startOver();
  if (step === "building") return;
  await validate();
  if (blocking(step).length) { refreshDynamic(); return; }
  if (step === "review") return build();
  goTo(stepIds()[stepIndex(step) + 1]);
}
function back() {
  if (step === "reveal") return goTo("review");
  const idx = stepIndex(step);
  if (idx > 0) goTo(stepIds()[idx - 1]);
}
function startOver() {
  state = { ...CAT.defaults };
  ui.carat = 1; ui.maxStep = 0; ui.advancedBand = false;
  result = null; plan = null; planFor = "";
  save();
  goTo("type");
  validate();
}

// ---------------------------------------------------------------- render
function render() {
  const layout = $("#layout");
  layout.classList.toggle("wide", step === "building" || step === "reveal");
  $("#summary").hidden = step === "building" || step === "reveal" || step === "review";
  $("#nav").hidden = step === "building";
  const screen = $("#screen");
  const fn = { type: renderType, style: renderStyle, shape: renderShape, stone: renderStone, metal: renderMetal, size: renderSize,
    band: renderBand, review: renderReview, building: renderBuilding, reveal: renderReveal }[step];
  screen.innerHTML = "";
  const panel = document.createElement("div");
  panel.className = "step-panel";
  screen.appendChild(panel);
  fn(panel);
  renderProgress();
  refreshDynamic();
}

function header(id) {
  const n = stepIndex(id) + 1, total = CAT.steps.length;
  return `<div class="eyebrow">Step ${n} of ${total}</div><h1 tabindex="-1">${esc(COPY[id].q)}</h1><p class="lede">${esc(COPY[id].lede)}</p>`;
}

function renderProgress() {
  const ids = stepIds();
  const cur = step === "building" || step === "reveal" ? ids.length : stepIndex(step);
  $("#progress-steps").innerHTML = CAT.steps.map((s, i) => {
    const cls = i < cur ? "done" : i === cur ? "current" : "";
    const can = i <= ui.maxStep && step !== "building" && i !== cur;
    return `<li class="${cls}"><button type="button" data-goto="${s.id}" ${can ? "" : "disabled"} ${i === cur ? 'aria-current="step"' : ""}>`
      + `<span class="bar"></span><span class="lbl">${esc(s.title)}</span></button></li>`;
  }).join("");
  $("#progress-compact").textContent = step === "reveal" ? "Your ring is ready"
    : step === "building" ? "Creating your ring…" : `Step ${cur + 1} of ${ids.length} · ${CAT.steps[cur].title}`;
}

function optionGroup(el, attr, onPick) {
  el.addEventListener("click", (ev) => {
    const btn = ev.target.closest(`[${attr}]`);
    if (!btn) return;
    for (const b of el.querySelectorAll(`[${attr}]`)) b.setAttribute("aria-checked", String(b === btn));
    onPick(btn.getAttribute(attr));
  });
  el.addEventListener("dblclick", (ev) => { if (ev.target.closest(`[${attr}]`)) next(); });
}

function card(item, attr, checked) {
  return `<button type="button" class="option style-card" role="radio" aria-checked="${checked}" ${attr}="${esc(item.id)}">
      <div class="thumb">${item.thumbnail ? `<img src="${esc(item.thumbnail)}" alt="" loading="lazy">` : `<span class="ph">${esc(item.name)}</span>`}</div>
      <div class="body"><span class="name">${esc(item.name)}</span><span class="meta">${esc(item.blurb)}</span></div></button>`;
}

function pickType(id) {
  const t = typeOf(id);
  const patch = { ring_type: t.id };
  if (styleOf().ring_type !== t.id) patch.style = t.styles[0];
  if (!t.shapes.includes(state.shape)) {
    patch.shape = t.shapes[0];
    const dims = CAT.shapes[patch.shape].typical[String(ui.carat || 1)];
    if (dims && state.size_mode !== "report") Object.assign(patch, dims);
  }
  if (t.id !== state.ring_type) patch.melee = null;
  update(patch);
}

function renderType(panel) {
  panel.innerHTML = header("type") + `<div class="grid styles-grid types-grid" role="radiogroup" aria-label="Ring types">${
    CAT.ring_types.map((t) => card(t, "data-type", t.id === state.ring_type)).join("")
  }</div><div class="messages" id="msgs"></div>`;
  optionGroup($(".types-grid", panel), "data-type", pickType);
}

function meleeDefault(s = styleOf()) {
  const d = CAT.melee.defaults;
  if (s.halo === "single") return d.single;
  if (s.halo === "hidden") return d.hidden;
  if (s.shank === "pave") {
    const width = plan && plan.band_mm ? plan.band_mm.width : bandOf().width;
    return Math.min(d.pave, Math.floor((width - 2 * CAT.melee.wall_mm) * 10 + 1e-9) / 10);
  }
  return null;
}

function fineTuneHtml() {
  const t = typeOf(), s = styleOf();
  if (!t.fine_tune.length) return "";
  const melee = state.melee ?? meleeDefault(s);
  const what = s.halo === "single" ? "halo" : s.halo === "hidden" ? "hidden halo" : "band";
  const meleeRow = `<div class="hint">Small diamonds in the ${what}</div><div class="chips" id="melee">${
    CAT.melee.choices.map((m) => `<button type="button" class="chip" data-melee="${m}" aria-pressed="${Number(melee) === m}">${m.toFixed(1)} mm${m === meleeDefault(s) ? " ·&nbsp;default" : ""}</button>`).join("")}</div>`;
  const coverageRow = s.shank === "pave" ? `<div class="hint">How far the band's diamonds run</div><div class="chips" id="coverage">${
    CAT.coverages.map((c) => `<button type="button" class="chip" data-coverage="${c.id}" aria-pressed="${c.id === state.coverage}" title="${esc(c.blurb)}">${esc(c.label)}</button>`).join("")}</div>` : "";
  return `<details class="helper" id="fine" ${ui.fineTune ? "open" : ""}><summary>Fine-tune</summary>${meleeRow}${coverageRow}
    <p class="hint">Sizes are diameters. Everything else is fitted to your stone automatically.</p></details>`;
}

function renderStyle(panel) {
  const t = typeOf();
  panel.innerHTML = header("style") + `<div class="grid styles-grid" role="radiogroup" aria-label="${esc(t.name)} styles">${
    stylesOfType(t).map((s) => card(s, "data-style", s.id === state.style)).join("")
  }</div>${fineTuneHtml()}<div class="messages" id="msgs"></div>`;
  optionGroup($(".styles-grid", panel), "data-style", (id) => { update({ style: id }); renderFine(panel); });
  wireFine(panel);
}

function renderFine(panel) {
  const old = $("#fine", panel);
  if (!old) return;
  old.outerHTML = fineTuneHtml();
  wireFine(panel);
}

function wireFine(panel) {
  const fine = $("#fine", panel);
  if (!fine) return;
  fine.addEventListener("toggle", (ev) => { ui.fineTune = ev.target.open; });
  const melee = $("#melee", panel);
  if (melee) melee.addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-melee]");
    if (!b) return;
    const m = Number(b.dataset.melee);
    update({ melee: m === meleeDefault() ? null : m });
    renderFine(panel);
  });
  const cov = $("#coverage", panel);
  if (cov) cov.addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-coverage]");
    if (!b) return;
    update({ coverage: b.dataset.coverage });
    renderFine(panel);
  });
}

function renderShape(panel) {
  const t = typeOf(), st = styleOf();
  panel.innerHTML = header("shape") + `<div class="grid shapes-grid" role="radiogroup" aria-label="Stone shapes">${
    Object.entries(CAT.shapes).map(([id, s]) => {
      const by = !t.shapes.includes(id) ? t.name : (st.exclude_shapes || []).includes(id) ? st.name : "";
      return `<button type="button" class="option shape-tile" role="radio" aria-checked="${id === state.shape}" data-shape="${id}" ${by ? `aria-disabled="true" title="Not available with the ${esc(by)}"` : ""}>
      <svg viewBox="0 0 48 48" aria-hidden="true"><g fill="#F3F6FA" stroke="#0B0A08" stroke-width="1.3" stroke-linejoin="round">${SHAPE_SVG[id]}</g></svg>
      <span class="name">${esc(s.label)}</span>${by ? `<span class="meta">Not with ${esc(by)}</span>` : ""}</button>`;
    }).join("")
  }</div><style>.shape-tile .t{fill:none;stroke:#8C877B;stroke-width:.8}</style><div class="messages" id="msgs"></div>`;
  $(".shapes-grid", panel).addEventListener("click", (ev) => {
    const b = ev.target.closest('[aria-disabled="true"]');
    if (b) { ev.stopImmediatePropagation(); ev.preventDefault(); }
  }, true);
  optionGroup($(".shapes-grid", panel), "data-shape", (id) => {
    state.shape = id;
    if (state.size_mode !== "report") setTypicalDims(ui.carat || 1);
    update({});
  });
}

function renderStone(panel) {
  const round = state.shape === "round";
  const report = state.size_mode === "report";
  const example = round ? "6.52 - 6.55 x 3.98 mm" : "8.02 x 5.51 x 3.45 mm";
  const field = (key, label) => `<div class="field"><label for="f-${key}">${label}</label><div class="unit">
    <input id="f-${key}" data-key="${key}" inputmode="decimal" autocomplete="off" value="${esc(trim0(state[key]))}"><span>mm</span></div></div>`;
  panel.innerHTML = header("stone") + `<div class="card">
    <div class="seg" role="group" aria-label="How to enter the size">
      <button type="button" data-mode="mm" aria-pressed="${!report}">Type millimetres</button>
      <button type="button" data-mode="report" aria-pressed="${report}">From a GIA report</button>
    </div>
    ${report ? `
      <div class="fields two">
        <div class="field"><label for="f-report_no">Report number <span style="color:var(--graphite-2)">(optional)</span></label>
          <input id="f-report_no" data-text="report_no" inputmode="numeric" autocomplete="off" value="${esc(state.report_no)}" placeholder="e.g. 2141438171"></div>
        <div class="field"><label for="f-meas">Measurements</label>
          <input id="f-meas" data-text="report_measurements" autocomplete="off" value="${esc(state.report_measurements)}" placeholder="${example}"></div>
      </div>
      <p class="hint">Copy the Measurements line exactly as printed. We don't look reports up online.</p>`
    : `
      <div class="fields ${round ? "two" : ""}">
        ${round ? field("length", "Diameter") : field("length", "Length") + field("width", "Width")}
        ${field("depth", "Depth")}
      </div>
      <div class="hint">Typical size for</div>
      <div class="chips" id="chips">${CAT.carat_chips.map((c) => `<button type="button" class="chip" data-carat="${c}" aria-pressed="${ui.carat === c}">${c} ct</button>`).join("")}</div>`}
    <div class="carat" id="carat"></div>
  </div><div class="messages" id="msgs"></div>`;

  panel.querySelectorAll("[data-mode]").forEach((b) => b.addEventListener("click", () => {
    if (state.size_mode === b.dataset.mode) return;
    update({ size_mode: b.dataset.mode });
    render();
  }));
  panel.querySelectorAll("input[data-key]").forEach((input) => input.addEventListener("input", () => {
    ui.carat = null;
    const patch = { [input.dataset.key]: num(input.value) };
    if (round && input.dataset.key === "length") patch.width = patch.length;
    update(patch);
    panel.querySelectorAll("[data-carat]").forEach((c) => c.setAttribute("aria-pressed", "false"));
  }));
  panel.querySelectorAll("input[data-text]").forEach((input) => input.addEventListener("input", () => update({ [input.dataset.text]: input.value })));
  const chips = $("#chips", panel);
  if (chips) chips.addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-carat]");
    if (!b) return;
    ui.carat = Number(b.dataset.carat);
    setTypicalDims(ui.carat);
    update({});
    render();
  });
}

function renderMetal(panel) {
  panel.innerHTML = header("metal") + `<div class="grid metals-grid" role="radiogroup" aria-label="Metals">${
    CAT.alloys.map((a) => `<button type="button" class="option metal-tile" role="radio" aria-checked="${a.id === state.alloy}" data-alloy="${esc(a.id)}">
      <span class="swatch" style="background:radial-gradient(circle at 35% 30%, #fff 0%, ${a.swatch} 45%, ${a.swatch} 100%)"></span>
      <span class="name">${esc(a.label)}</span></button>`).join("")
  }</div><div class="messages" id="msgs"></div>`;
  optionGroup($(".metals-grid", panel), "data-alloy", (id) => update({ alloy: id }));
}

const US_RANGE = [3, 13], EU_RANGE = [44, 70];
const usToEu = (us) => Math.round(Math.PI * (11.63 + 0.8128 * us));
const euToUs = (eu) => Math.min(US_RANGE[1], Math.max(US_RANGE[0], Math.round(((eu / Math.PI - 11.63) / 0.8128) * 4) / 4));
const insideMm = () => (state.size_standard === "US" ? 11.63 + 0.8128 * Number(state.size) : Number(state.size) / Math.PI);

function renderSize(panel) {
  const us = state.size_standard === "US";
  const common = us ? [5, 6, 6.5, 7, 8, 9] : [49, 52, 54, 57, 60, 62];
  panel.innerHTML = header("size") + `<div class="card">
    <div class="seg" role="group" aria-label="Size system">
      <button type="button" data-std="US" aria-pressed="${us}">US</button><button type="button" data-std="EU" aria-pressed="${!us}">EU</button>
    </div>
    <div class="sizer">
      <button type="button" id="minus" aria-label="Smaller size">−</button>
      <output id="size-out" aria-live="polite"></output>
      <button type="button" id="plus" aria-label="Larger size">+</button>
    </div>
    <div class="chips" id="common">${common.map((s) => `<button type="button" class="chip" data-size="${s}" aria-pressed="${Number(state.size) === s}">${state.size_standard} ${s}</button>`).join("")}</div>
    <details class="helper"><summary>Not sure of the size?</summary><ul>
      <li>Measure a ring that already fits: enter the inside diameter and we'll convert it.
        <div class="inline"><div class="field"><label for="f-dia">Inside diameter</label><div class="unit"><input id="f-dia" inputmode="decimal" placeholder="17.3"><span>mm</span></div></div>
        <button type="button" class="btn-sm" id="use-dia">Use this</button></div></li>
      <li>The most requested sizes are US 6 to 7 (EU 52 to 54).</li>
      <li>Measure late in the day, when fingers are largest.</li>
      <li>A plain band can usually be resized by a jeweler later.</li>
    </ul></details>
  </div><div class="messages" id="msgs"></div>`;
  const showSize = () => {
    $("#size-out", panel).innerHTML = `${esc(state.size_standard)} ${esc(trim0(state.size))}<small>${fmt(insideMm(), 1)} mm inside</small>`;
    panel.querySelectorAll("[data-size]").forEach((c) => c.setAttribute("aria-pressed", String(Number(state.size) === Number(c.dataset.size))));
  };
  const setSize = (v) => {
    const [lo, hi] = us ? US_RANGE : EU_RANGE;
    update({ size: Math.min(hi, Math.max(lo, v)) });
    showSize();
  };
  const stepBy = us ? 0.25 : 1;
  $("#minus", panel).addEventListener("click", () => setSize(Number(state.size) - stepBy));
  $("#plus", panel).addEventListener("click", () => setSize(Number(state.size) + stepBy));
  $("#common", panel).addEventListener("click", (ev) => { const b = ev.target.closest("[data-size]"); if (b) setSize(Number(b.dataset.size)); });
  panel.querySelectorAll("[data-std]").forEach((b) => b.addEventListener("click", () => {
    if (b.dataset.std === state.size_standard) return;
    const size = b.dataset.std === "EU" ? usToEu(Number(state.size)) : euToUs(Number(state.size));
    update({ size_standard: b.dataset.std, size });
    render();
  }));
  $("#use-dia", panel).addEventListener("click", () => {
    const d = Number(String($("#f-dia", panel).value).replace(",", "."));
    if (!(d > 10 && d < 26)) { $("#f-dia", panel).focus(); return; }
    setSize(us ? Math.round(((d - 11.63) / 0.8128) * 4) / 4 : Math.round(Math.PI * d));
  });
  showSize();
}

function bandSvg(width) {
  const h = 6 + width * 7;  // visual only
  return `<svg viewBox="0 0 96 70" aria-hidden="true"><defs><linearGradient id="bg${width}" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#F1DFAE"/><stop offset=".5" stop-color="#C8A96A"/><stop offset="1" stop-color="#8C6A35"/></linearGradient></defs>
    <rect x="6" y="${35 - h / 2}" width="84" height="${h}" rx="${h / 2.2}" fill="url(#bg${width})"/></svg>`;
}

function renderBand(panel) {
  const cathedral = styleOf().shank === "cathedral";
  panel.innerHTML = header("band") + `<div class="grid bands-grid" role="radiogroup" aria-label="Band widths">${
    CAT.bands.map((b) => `<button type="button" class="option band-card" role="radio" aria-checked="${b.id === state.band}" data-band="${b.id}">
      ${bandSvg(b.width)}<span class="name">${esc(b.label)}</span><span class="meta">${esc(b.blurb)}</span></button>`).join("")
  }</div>
  <details class="helper" id="adv" ${ui.advancedBand ? "open" : ""}><summary>Advanced: exact millimetres</summary>
    <div class="fields two">
      <div class="field"><label for="f-bw">Width</label><div class="unit"><input id="f-bw" data-band-key="band_width" inputmode="decimal" placeholder="${esc(trim0(plan && plan.band_mm ? plan.band_mm.width : bandOf().width))}" value="${esc(trim0(state.band_width ?? ""))}"><span>mm</span></div></div>
      <div class="field"><label for="f-bt">Thickness</label><div class="unit"><input id="f-bt" data-band-key="band_thickness" inputmode="decimal" placeholder="${esc(trim0(plan && plan.band_mm ? plan.band_mm.thickness : bandOf().thickness))}" value="${esc(trim0(state.band_thickness ?? ""))}"><span>mm</span></div></div>
    </div>
    <p class="hint">Leave empty to use the ${esc(bandOf().label)} preset.${cathedral ? " Cathedral bands start at 2.0 mm wide." : ""}</p>
  </details><div class="messages" id="msgs"></div>`;
  optionGroup($(".bands-grid", panel), "data-band", (id) => { update({ band: id, band_width: null, band_thickness: null }); render(); });
  $("#adv", panel).addEventListener("toggle", (ev) => { ui.advancedBand = ev.target.open; });
  panel.querySelectorAll("[data-band-key]").forEach((input) => input.addEventListener("input", () => {
    update({ [input.dataset.bandKey]: input.value.trim() === "" ? null : num(input.value) });
  }));
}

function stoneText() {
  const d = (plan && plan.stone_mm) || state;
  const dims = state.shape === "round" ? `${trim0(d.length)} × ${trim0(d.depth)} mm` : `${trim0(d.length)} × ${trim0(d.width)} × ${trim0(d.depth)} mm`;
  return `${shapeLabel()} · ${dims}`;
}
function accentText(st = state, r = null) {
  const s = styleOf(st.style);
  const mm = (plan && plan.melee_mm) || {};
  const m = r && r.metrics && r.metrics.melee_sizes_mm && r.metrics.melee_sizes_mm.length ? r.metrics.melee_sizes_mm.join(" / ") : null;
  const parts = [];
  if (s.halo === "single") parts.push(`Halo of ${trim0(m || mm.halo || meleeDefault(s))} mm diamonds`);
  if (s.halo === "hidden") parts.push(`Hidden halo of ${trim0(m || mm.halo || meleeDefault(s))} mm diamonds`);
  if (s.shank === "pave") parts.push(`Pavé band, ${coverageOf(st.coverage).label.toLowerCase()}${s.halo === "none" ? `, ${trim0(mm.pave || meleeDefault(s))} mm diamonds` : ""}`);
  return parts.join(" · ");
}

function prongText() {
  const s = styleOf();
  if (s.halo === "single") return `${plan && plan.prong_count != null ? plan.prong_count : 4} prongs inside the halo`;
  if (s.halo === "hidden") return "4 prongs over the hidden halo";
  if (s.head === "bezel") return "Bezel rim, no prongs";
  const n = plan && plan.prong_count != null ? plan.prong_count : s.prong_count;
  const pointed = CAT.shapes[state.shape] && CAT.shapes[state.shape].pointed;
  return `${n} prongs${pointed ? " with V-tips" : ""}${s.head === "basket" ? " on a basket rail" : ""}`;
}

function renderReview(panel) {
  const s = styleOf(), a = alloyOf(), b = bandOf();
  const row = (k, v, sub, target) => `<div class="review-row"><span class="k">${k}</span><span class="v">${v}${sub ? `<small>${sub}</small>` : ""}</span>
    <button type="button" class="link" data-goto="${target}" aria-label="Change ${k.toLowerCase()}">Change</button></div>`;
  const accents = accentText();
  panel.innerHTML = header("review") + `<div class="card"><div class="review-list">
      ${row("Ring type", esc(typeOf().name), "", "type")}
      ${row("Style", esc(s.name), esc(prongText()), "style")}
      ${accents ? row("Small diamonds", esc(accents), "Fine-tune on the style step", "style") : ""}
      ${row("Stone shape", esc(shapeLabel()), "", "shape")}
      ${row("Stone size", `<span id="rv-stone">${esc(stoneText())}</span>`, `<span id="rv-carat"></span>`, "stone")}
      ${row("Metal", esc(a.label), "", "metal")}
      ${row("Ring size", `${esc(state.size_standard)} ${esc(trim0(state.size))}`, `${fmt(insideMm(), 2)} mm inside`, "size")}
      ${row("Band", esc(b.label), `<span id="rv-band"></span>`, "band")}
    </div></div>
    <div class="messages" id="msgs"></div>
    <p class="fine">Built to these measurements in about five seconds. The checks are design checks: your caster confirms the model before casting.</p>
    <details class="tech" id="tech"><summary>Details for the workshop</summary><div id="tech-body"></div></details>`;
}

function techHtml() {
  if (!plan) return "";
  const rules = (plan.issues || []).filter((i) => i.rule).map((i) => `<li><code>${esc(i.rule)}</code> ${esc(i.text)}</li>`).join("");
  const steps = (plan.steps || []).map((s) => `<li><code>${esc(s.tool)}</code> ${esc(s.purpose)}</li>`).join("");
  const spec = plan.spec ? JSON.stringify(plan.spec, null, 2) : "";
  return `${rules ? `<p>Rules behind the messages:</p><ul>${rules}</ul>` : "<p>No rule findings.</p>"}
    ${steps ? `<p>Build plan (${(plan.steps || []).length} RhinoArtisan steps):</p><ol>${steps}</ol>` : ""}
    <p>Manufacturing sign-off: all five geometry gates stay <b>pending</b> until a calibrated shop profile validates them.</p>
    ${spec ? `<p>Canonical spec v0.1 (<a href="data:application/json,${encodeURIComponent(spec)}" download="aureon_spec.json">download</a>):</p><pre>${esc(spec)}</pre>` : ""}`;
}

// ---------------------------------------------------------------- dynamic bits
function refreshDynamic() {
  if (!CAT) return;
  const msgs = $("#msgs");
  if (msgs) msgs.innerHTML = messagesHtml(step);
  const carat = $("#carat");
  if (carat) {
    const c = plan && plan.carat_estimate;
    const d = plan && plan.stone_mm;
    const read = state.size_mode === "report" && d && typeof d.length === "number"
      ? `<span>Read as ${trim0(d.length)} × ${trim0(d.width)} × ${trim0(d.depth)} mm</span>` : "";
    carat.innerHTML = c ? `<b>≈ ${c.toFixed(2)} ct</b><span>estimated from the measurements</span>${read}` : `<span>Enter the measurements to see the carat estimate.</span>`;
  }
  if (step === "review") {
    const rc = $("#rv-carat"); if (rc) rc.textContent = [plan && plan.carat_estimate ? `≈ ${plan.carat_estimate.toFixed(2)} ct` : "", state.report_no ? `GIA ${state.report_no}` : ""].filter(Boolean).join(" · ");
    const rs = $("#rv-stone"); if (rs) rs.textContent = stoneText();
    const rb = $("#rv-band"); if (rb && plan && plan.band_mm) rb.textContent = `${fmt(plan.band_mm.width, 1)} mm wide`;
    const tb = $("#tech-body"); if (tb) tb.innerHTML = techHtml();
  }
  renderSummary();
  renderNav();
}

function renderNav() {
  const backBtn = $("#back"), nextBtn = $("#next"), why = $("#why");
  why.textContent = "";
  nextBtn.className = "btn";
  if (step === "reveal") {
    backBtn.textContent = "Change something"; backBtn.disabled = false;
    nextBtn.textContent = "Start a new ring"; nextBtn.disabled = false;
    return;
  }
  backBtn.textContent = "Back";
  backBtn.disabled = stepIndex(step) <= 0;
  const blocks = blocking(step);
  if (step === "review") {
    nextBtn.textContent = "Create my ring";
    nextBtn.className = "btn gold";
    const offline = health.rhino === false;
    nextBtn.disabled = blocks.length > 0 || offline;
    why.textContent = blocks.length ? `Fix ${blocks.length === 1 ? "the item" : `${blocks.length} items`} above first.`
      : offline ? "The builder is offline: open Rhino on this computer." : "";
    return;
  }
  nextBtn.textContent = "Next";
  nextBtn.disabled = blocks.length > 0;
  if (blocks.length) why.textContent = "Fix the item above to continue.";
}

function renderSummary() {
  const el = $("#summary");
  if (el.hidden) return;
  const s = styleOf(), idx = stepIndex(step);
  const rows = [["Type", typeOf().name]];
  if (idx >= 1) rows.push(["Style", s.name]);
  if (idx >= 2) rows.push(["Shape", shapeLabel()]);
  if (idx >= 3 && plan && plan.carat_estimate) rows.push(["Stone", `≈ ${plan.carat_estimate.toFixed(2)} ct`]);
  if (idx >= 4) rows.push(["Metal", alloyOf().label]);
  if (idx >= 5) rows.push(["Size", `${state.size_standard} ${trim0(state.size)}`]);
  if (idx >= 6) rows.push(["Band", bandOf().label]);
  el.innerHTML = `<h2>Your ring</h2><div class="thumb">${s.thumbnail ? `<img src="${esc(s.thumbnail)}" alt="">` : ""}</div>
    <dl>${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
    <p class="fine">Built to the stone's exact measurements.</p>`;
}

// ---------------------------------------------------------------- build
function narrationLines() {
  const s = styleOf();
  const pointed = CAT.shapes[state.shape].pointed;
  const head = s.halo === "single" ? "Framing the stone with its halo of diamonds"
    : s.halo === "hidden" ? "Tucking the hidden halo under the stone"
    : s.head === "bezel" ? "Wrapping the bezel rim around the stone"
    : s.head === "basket" ? "Building the basket and its rail" : "Raising the prongs";
  return [
    s.shank === "pave" ? `Setting small diamonds along the band, size ${state.size_standard} ${trim0(state.size)}`
      : `Shaping the ${bandOf().label.toLowerCase()} band to size ${state.size_standard} ${trim0(state.size)}`,
    `Placing the ${shapeLabel().toLowerCase()} stone at its exact height`,
    pointed && s.head !== "bezel" && s.halo === "none" ? `${head}, with V-tips at the point${state.shape === "pear" ? "" : "s"}` : head,
    "Joining everything into one piece",
    `Weighing the ${alloyOf().label.toLowerCase()} and pricing it`,
    "Running the design checks",
  ];
}

function renderBuilding(panel) {
  const lines = narrationLines();
  panel.innerHTML = `<div class="building"><div class="box">
    <svg class="gem-spin" viewBox="0 0 48 48" aria-hidden="true"><g fill="none" stroke="#A88649" stroke-width="1.4" stroke-linejoin="round">
      <path d="M8 18l8-9h16l8 9-16 22z"/><path d="M8 18h32M16 9l-2 9 10 22 10-22-2-9M14 18l10-9 10 9"/></g></svg>
    <h1 tabindex="-1">Creating your ring</h1>
    <p class="lede" style="margin:0 auto">Built to your stone, in your metal, at your size.</p>
    <ol class="narration" id="narration">${lines.map((l) => `<li><i></i><span>${esc(l)}</span></li>`).join("")}</ol>
    <div class="meter" aria-hidden="true"><span id="meter"></span></div>
    <div id="build-error"></div>
  </div></div>`;
}

let narrationTimer = null;
function narrate(t0) {
  const items = [...document.querySelectorAll("#narration li")];
  const times = [0, 0.8, 1.8, 3.0, 4.2, 5.4];
  clearInterval(narrationTimer);
  narrationTimer = setInterval(() => {
    const t = (performance.now() - t0) / 1000;
    let active = 0;
    times.forEach((s, i) => { if (t >= s) active = i; });
    items.forEach((li, i) => { li.className = i < active ? "done" : i === active ? "active" : ""; });
    const meter = $("#meter");
    if (meter) meter.style.width = `${Math.min(94, 100 * (1 - Math.exp(-t / 3.2)))}%`;
  }, 120);
}

async function build() {
  if (step === "building") return;
  const p = await validate();
  if (!p || blocking("review").length) { refreshDynamic(); return; }
  step = "building";
  history.pushState({ step: "review" }, "", "#review");
  render();
  ui.startedAt = performance.now();
  narrate(ui.startedAt);
  let status = 0, body = null;
  try {
    const res = await fetch("/api/build", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(state) });
    status = res.status;
    body = await res.json();
  } catch (e) {
    body = null;
  }
  clearInterval(narrationTimer);
  if (body && body.status === "built") {
    document.querySelectorAll("#narration li").forEach((li) => { li.className = "done"; });
    const meter = $("#meter"); if (meter) meter.style.width = "100%";
    result = { ...body, client_seconds: (performance.now() - ui.startedAt) / 1000, state: { ...state } };
    await new Promise((r) => setTimeout(r, 350));
    step = "reveal";
    history.replaceState({ step: "reveal" }, "", "#reveal");
    render();
    showModel();
    return;
  }
  buildFailed(status, body);
}

function buildFailed(status, body) {
  const reason = status === 409 ? "Another ring is being built right now. Try again in a moment."
    : !body ? "The builder can't be reached. Check that Rhino with RhinoArtisan is open on this computer."
    : status === 422 ? "Something in the details needs a change first."
    : "The builder stopped at a step it couldn't finish.";
  const detail = body ? (body.error || (body.errors || []).join("; ")) : "";
  document.querySelectorAll("#narration li.active").forEach((li) => { li.className = ""; });
  $("#build-error").innerHTML = `<div class="messages"><div class="msg block" role="alert"><span class="ico">!</span>
    <span class="txt">We couldn't finish this ring.<span class="fix">${esc(reason)}</span></span></div></div>
    ${detail ? `<details class="tech"><summary>Details for the workshop</summary><p><code>${esc(detail)}</code></p></details>` : ""}
    <div style="display:flex;gap:10px;justify-content:center;margin-top:18px">
      <button class="btn ghost" type="button" id="err-back">Back to review</button><button class="btn" type="button" id="err-retry">Try again</button></div>`;
  $("#err-back").addEventListener("click", () => goTo("review"));
  $("#err-retry").addEventListener("click", () => { step = "review"; build(); });
}

// ---------------------------------------------------------------- reveal
function priceText(p) {
  if (!p || p.mounting_total == null) return "–";
  const sym = { EUR: "€", USD: "$", GBP: "£", ILS: "₪" }[p.currency] || "";
  return `${sym}${Math.round(Number(p.mounting_total)).toLocaleString()}${sym ? "" : ` ${p.currency || ""}`}`;
}

function checksHtml(r) {
  const m = r.metrics || {}, d = r.derived || {};
  const gate = (m.qa_mesh_closedness || {}).status;
  const finger = typeof m.measured_finger_diameter_mm === "number" && Math.abs(m.measured_finger_diameter_mm - d.finger_diameter_mm) <= 0.02;
  const drift = Math.abs((m.culet_z_mm ?? NaN) - d.expected_culet_z_mm);
  const st = r.state || state;
  const top = m.measured_inner_top_mm;
  const clearHole = typeof top !== "number" || d.finger_diameter_mm / 2 - top <= 0.02;
  const checks = [
    [m.metal_bodies === 1, "One solid piece of metal", `${m.metal_bodies ?? "–"} piece`],
    [m.metal_all_solid === true, "Closed shape, ready to print or cast", m.metal_all_solid ? "closed" : "open"],
    [m.culet_inside_metal === false, "Stone clears the metal below it", `${fmt(m.culet_clearance_mm)} mm gap`],
    [finger, `Fits ring size ${st.size_standard} ${trim0(st.size)}`, `${fmt(m.measured_finger_diameter_mm, 2)} mm inside`],
    [clearHole, "Nothing reaches into the finger hole", typeof top === "number" ? "clear" : "not measured"],
    [gate === "pass", "Print file is sealed", gate === "pass" ? "sealed" : (gate || "not run")],
    [isFinite(drift) && drift <= 0.02, "Stone sits at its planned height", `${fmt(drift, 3)} mm off`],
  ];
  return checks.map(([ok, label, metric]) => `<li><span class="dot ${ok ? "" : "bad"}"></span><span>${esc(label)}</span><span class="m">${esc(metric)}</span></li>`).join("");
}

function renderReveal(panel) {
  const r = result;
  if (!r) { panel.innerHTML = ""; goTo("review", { push: false }); return; }
  const st = r.state, s = styleOf(st.style), a = alloyOf(st.alloy), b = bandOf(st.band);
  const m = r.metrics || {};
  const failures = m.qa_failures || [];
  const names = {
    stl: ["STL", "for casting or printing"], "3dm": ["3DM", "editable in Rhino"], glb: ["GLB", "3D for the web"],
    png: ["PNG", "picture"], manifest: ["Build report", "JSON, with every measurement"],
  };
  const order = ["stl", "3dm", "glb", "png", "manifest"];
  const files = order.filter((k) => r.files && r.files[k]);
  const thumb = s.thumbnail ? `<img src="${esc(s.thumbnail)}" alt="">` : "";
  const dmm = r.stone_mm || {};
  panel.innerHTML = `<div class="reveal">
    <div>
      <div class="stage" id="stage"><div class="loading" id="stage-loading">${thumb || "Lighting the stone…"}</div></div>
      <p class="fine">Drag to turn · pinch or scroll to zoom</p>
    </div>
    <div>
      <div class="eyebrow">${failures.length ? "Built, with a flag" : "Your ring is ready"}</div>
      <h1 tabindex="-1">${esc(s.name)} in ${esc(a.label)}</h1>
      ${accentText(st, r) ? `<p class="lede" style="margin-bottom:2px">${esc(accentText(st, r))}</p>` : ""}
      <p class="lede" style="margin-bottom:6px">${esc(shapeLabel(st.shape))} ${trim0(dmm.length)} × ${trim0(st.shape === "round" ? dmm.depth : dmm.width)}${st.shape === "round" ? "" : ` × ${trim0(dmm.depth)}`} mm · ${esc(st.size_standard)} ${esc(trim0(st.size))} · ${esc(b.label)} band</p>
      ${failures.length ? `<div class="messages"><div class="msg block" role="alert"><span class="ico">!</span><span class="txt">A design check flagged this ring, so don't send it to casting yet.<span class="fix">Go back and try a wider band or another style; the workshop details below say what was found.</span></span></div></div>` : ""}
      <div class="figures">
        <div class="figure"><div class="k">Mounting price</div><div class="v">${esc(priceText(m.price))}</div><div class="s">${m.melee_count ? "Metal, casting, small diamonds and setting. Center stone not included." : "Metal and casting. Stone not included."}</div></div>
        <div class="figure"><div class="k">Metal weight</div><div class="v">${fmt(m.metal_weight_g)} g</div><div class="s">${esc(a.label)}</div></div>
        <div class="figure"><div class="k">Center stone</div><div class="v">${r.artisan_carat ? `${Number(r.artisan_carat).toFixed(2)} ct` : r.carat_estimate ? `≈ ${r.carat_estimate.toFixed(2)} ct` : "–"}</div><div class="s">Estimated from the measurements${st.report_no ? ` · GIA ${esc(st.report_no)}` : ""}</div></div>
        ${m.melee_count ? `<div class="figure"><div class="k">Small diamonds</div><div class="v">${m.melee_count}</div><div class="s">${fmt(m.melee_carat, 2)} ct total · ${esc((m.melee_sizes_mm || []).join(" / "))} mm</div></div>` : ""}
        <div class="figure"><div class="k">Built in</div><div class="v">${fmt(m.build_seconds, 1)} s</div><div class="s">Exact to the stone</div></div>
      </div>
      <h3>Design checks</h3>
      <ul class="checks">${checksHtml(r)}</ul>
      <p class="fine">These are design checks, not a manufacturing certification. Your caster confirms the model before casting.</p>
      <h3>Files for your caster</h3>
      <div class="downloads">${files.map((k) => `<a href="${esc(r.files[k])}" ${k === "stl" || k === "3dm" ? "download" : 'target="_blank" rel="noopener"'}>${esc(names[k][0])}<small>${esc(names[k][1])}</small></a>`).join("")}</div>
      <details class="tech"><summary>Details for the workshop</summary>
        <p>Job <code>${esc(r.job_id)}</code> · ${esc(prongText())} · head kernel <code>${esc((r.derived || {}).head_kernel || "")}</code> · ${esc((r.derived || {}).shank_style || "")} shank${m.union_strategy ? ` · union ${esc(m.union_strategy)}` : ""}</p>
        ${failures.length ? `<p>QA findings: ${failures.map(esc).join(" · ")}</p>` : "<p>No QA defect detected (this is not a pass certificate).</p>"}
        <p>Geometry gates: all five <b>pending</b> until a calibrated shop profile validates them.</p>
        <p id="viewer-name"></p>
      </details>
    </div></div>`;
  const stage = $("#stage", panel);
  stage.insertBefore(viewerHost, stage.firstChild);
}

async function showModel() {
  const url = result && result.files && result.files.glb;
  const loading = $("#stage-loading");
  if (!url) { if (loading) loading.textContent = "No 3D file came back from the builder."; return; }
  try {
    if (!viewer) viewer = await createViewer(viewerHost, { prefer: PREFER_VIEWER });
    try {
      await viewer.show(url);
    } catch (err) {
      if (viewer.name === "model-viewer") throw err;
      console.warn("[aureon] falling back to model-viewer:", err);
      viewer.dispose();
      viewer = await createViewer(viewerHost, { prefer: "model-viewer" });
      await viewer.show(url);
    }
    const tag = $("#viewer-name");
    if (tag) tag.textContent = `3D view: ${viewer.label}.`;
    if (loading) loading.hidden = true;
  } catch (err) {
    console.error(err);
    if (loading) loading.innerHTML = "The 3D view couldn't load here.<br>The files below are complete.";
  }
}

// ---------------------------------------------------------------- boot
function wire() {
  $("#next").addEventListener("click", next);
  $("#back").addEventListener("click", back);
  $("#home").addEventListener("click", () => { if (step !== "building") startOver(); });
  document.addEventListener("click", (ev) => {
    const t = ev.target.closest("[data-goto]");
    if (t && !t.disabled && step !== "building") goTo(t.dataset.goto);
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && ev.target.matches("input") && !$("#next").disabled) { ev.preventDefault(); next(); }
  });
  window.addEventListener("popstate", (ev) => {
    if (step === "building") return;
    const target = (ev.state && ev.state.step) || "type";
    if (target === "reveal" && !result) return goTo("review", { push: false });
    goTo(target, { push: false });
  });
}

async function boot() {
  CAT = await (await fetch("/api/styles")).json();
  if (!restore()) state = { ...CAT.defaults };
  const fromHash = location.hash.slice(1);
  step = stepIndex(fromHash) >= 0 && stepIndex(fromHash) <= ui.maxStep ? fromHash : "type";
  history.replaceState({ step }, "", "#" + step);
  wire();
  render();
  validate();
  checkHealth();
  setInterval(checkHealth, 15000);
  window.aureon = { get state() { return state; }, get plan() { return plan; }, get result() { return result; }, goTo, get viewer() { return viewer; } };
}

boot();
