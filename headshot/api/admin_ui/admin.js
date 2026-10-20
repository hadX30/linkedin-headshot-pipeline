// Malameh traces dashboard. Plain ES module; all data comes from /admin/api with a Bearer token.
// Every string from the API is inserted with textContent (prompts and model output are untrusted).

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const TOKEN_KEY = "malameh.adminToken";
const state = { days: 30, view: "chart", offset: 0, limit: 25, overview: null, openRun: null, poll: null, scenarios: [] };

// ── storage (per-viewer convenience only) ─────────────────────────────────────
function getToken() { try { return sessionStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; } }
function setToken(v) { try { v ? sessionStorage.setItem(TOKEN_KEY, v) : sessionStorage.removeItem(TOKEN_KEY); } catch { /* private mode */ } }

// ── DOM helper ────────────────────────────────────────────────────────────────
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
const svg = (tag, attrs = {}) => {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
};

// ── formatting ────────────────────────────────────────────────────────────────
const usd = (v, digits) => {
  if (v == null) return "—";
  const d = digits ?? (Math.abs(v) >= 100 ? 0 : Math.abs(v) >= 1 ? 2 : 4);
  return "$" + Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
};
const int = (v) => (v == null ? "—" : Number(v).toLocaleString("en-US"));
const compact = (v) => (v == null ? "—" : Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(v));
const pct = (v) => (v == null ? "—" : `${Math.round(v * 100)}%`);
const ms = (v) => (v == null ? "—" : v < 1000 ? `${Math.round(v)} ms` : `${(v / 1000).toFixed(1)} s`);
const when = (iso) => (iso ? new Date(iso).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—");
const STATUS_LABEL = { delivered: "Delivered", manual_review: "Manual review", partial: "Partial", error: "Error", running: "Running", queued: "Queued" };
const STAGE_LABEL = { analyze: "1 · Analyze", prompt: "2 · Prompt", generate: "3 · Generate", validate: "4 · Validate" };
const statusEl = (s) => h("span", { class: `status status-${s}` }, STATUS_LABEL[s] || s);
const modeChip = (m) => h("span", { class: `chip ${m === "live" ? "chip-live" : ""}` }, m === "live" ? "Live" : "Mock");

// ── API ───────────────────────────────────────────────────────────────────────
class Unauthorized extends Error {}
async function api(path, opts = {}) {
  const res = await fetch(`/admin/api${path}`, { ...opts, headers: { ...(opts.headers || {}), Authorization: `Bearer ${getToken()}` } });
  if (res.status === 401) throw new Unauthorized();
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* not json */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (res.status === 204) return null;
  const type = res.headers.get("content-type") || "";
  return type.includes("application/json") ? res.json() : res.blob();
}

// ── gate ──────────────────────────────────────────────────────────────────────
function showGate(message = "") {
  $("#app").hidden = true;
  $("#gate").hidden = false;
  $("#gateError").textContent = message;
  $("#gateToken").focus();
}
$("#gateForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  setToken($("#gateToken").value.trim());
  try {
    await enter();
  } catch (err) {
    showGate(err instanceof Unauthorized ? "That token is not valid." : err.message);
  }
});
$("#signOut").addEventListener("click", () => { setToken(""); $("#gateToken").value = ""; showGate(); });

// ── segmented controls ────────────────────────────────────────────────────────
function setChecked(group, attr, value) {
  $$(`button[${attr}]`, group).forEach((b) => b.setAttribute("aria-checked", String(b.getAttribute(attr) === String(value))));
}

$("#period").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-days]");
  if (!b) return;
  state.days = Number(b.dataset.days);
  setChecked($("#period"), "data-days", state.days);
  await loadOverview();
});
$("#dailyView").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-view]");
  if (!b) return;
  state.view = b.dataset.view;
  setChecked($("#dailyView"), "data-view", state.view);
  $("#dailyChart").hidden = state.view !== "chart";
  $("#dailyLegend").hidden = state.view !== "chart";
  $("#dailyTable").hidden = state.view !== "table";
});

$$(".topbar .segmented button[data-mode]").forEach((b) =>
  b.addEventListener("click", async () => {
    const mode = b.dataset.mode;
    if (mode === state.overview?.mode) return;
    if (mode === "live" && !confirm("Switch to LIVE mode? Every new upload from the website will call Claude and OpenAI and be billed.")) return;
    try {
      await api("/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode }) });
      await loadOverview();
    } catch (err) { alert(err.message); }
  }),
);

// ── overview ──────────────────────────────────────────────────────────────────
async function loadAll() {
  await loadOverview();
  await loadRuns();
  state.scenarios = await api("/scenarios");
}

async function loadOverview() {
  ["#tiles", "#dailyChart", "#stageTable", "#modelTable"].forEach((s) => $(s).classList.add("is-loading"));
  try {
    const o = await api(`/overview?days=${state.days}`);
    state.overview = o;
    renderHeader(o);
    renderTiles(o);
    renderDaily(o);
    renderStages(o.stats.by_stage);
    renderModels(o);
  } finally {
    ["#tiles", "#dailyChart", "#stageTable", "#modelTable"].forEach((s) => $(s).classList.remove("is-loading"));
  }
}

function renderHeader(o) {
  $("#storeBadge").textContent = o.store === "supabase" ? "Store: Supabase" : "Store: local SQLite";
  setChecked($(".topbar .segmented"), "data-mode", o.mode);
  $(".topbar .segmented button[data-mode='live']").disabled = !o.live_available;
  $(".topbar .segmented button[data-mode='live']").title = o.live_available ? "" : "Set ANTHROPIC_API_KEY and OPENAI_API_KEY on the API to enable live mode";
  const note = $("#modeNote");
  note.hidden = false;
  note.textContent = o.mode === "live"
    ? `Live mode: website uploads call ${o.models.llm} and ${o.models.image} and are billed.`
    : `Mock mode: website uploads use fixtures and a local stand-in image. Nothing is billed; costs shown for mock runs are estimates for ${o.models.llm} and ${o.models.image}.`;
}

function renderTiles(o) {
  const s = o.stats;
  const tile = (label, value, note) => h("div", { class: "tile" },
    h("span", { class: "tile-label" }, label), h("span", { class: "tile-value" }, value), note ? h("span", { class: "tile-note" }, note) : null);
  $("#tiles").replaceChildren(
    tile("Runs", compact(s.runs), `${int(s.finished)} finished`),
    tile("Delivery rate", pct(s.delivery_rate), "of finished runs"),
    tile("Live spend", usd(s.cost.live_usd, 2), `${usd(s.cost.avg_per_delivered_live)} per delivered`),
    tile("Mock estimate", usd(s.cost.mock_estimated_usd, 2), `${usd(s.cost.avg_per_delivered_mock)} per delivered`),
    tile("Avg duration", ms(s.avg_duration_ms), "upload to final state"),
    tile("Avg attempts", s.avg_attempts == null ? "—" : String(s.avg_attempts), `max ${o.max_attempts}`),
  );
}

// Daily cost: grouped columns (live, mock) per day. Not stacked: billed + estimated must not add up.
function renderDaily(o) {
  const days = [];
  const byDay = new Map(o.stats.daily.map((d) => [d.day, d]));
  const end = new Date();
  for (let i = o.days - 1; i >= 0; i--) {
    const d = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth(), end.getUTCDate() - i));
    const key = d.toISOString().slice(0, 10);
    const row = byDay.get(key) || { day: key, runs: 0, live_usd: 0, mock_usd: 0 };
    days.push({ ...row, live_usd: Number(row.live_usd) || 0, mock_usd: Number(row.mock_usd) || 0 });
  }

  // table twin
  $("#dailyTable").replaceChildren(table(
    [["Day"], ["Runs", "num"], ["Live", "num"], ["Mock (est.)", "num"]],
    days.filter((d) => d.runs).reverse().map((d) => [d.day, int(d.runs), usd(d.live_usd), usd(d.mock_usd)]),
    "No runs in this period.",
  ));

  const host = $("#dailyChart");
  const W = Math.max(320, host.clientWidth || 640), H = 220;
  const m = { top: 8, right: 4, bottom: 24, left: 52 };
  const iw = W - m.left - m.right, ih = H - m.top - m.bottom;
  const max = Math.max(...days.map((d) => Math.max(d.live_usd, d.mock_usd)), 0);
  const ticks = niceTicks(max);
  const top = ticks[ticks.length - 1] || 1;
  const y = (v) => m.top + ih - (v / top) * ih;
  const band = iw / days.length;
  const bw = Math.max(1, Math.min(12, (band - 6) / 2));

  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `Daily cost, last ${o.days} days` });
  const grid = svg("g", { class: "grid" });
  for (const t of ticks) {
    grid.append(svg("line", { x1: m.left, x2: W - m.right, y1: y(t), y2: y(t) }));
    const label = svg("text", { x: m.left - 8, y: y(t) + 4, "text-anchor": "end" });
    label.textContent = usd(t, t < 1 && t > 0 ? 2 : 0);
    root.append(label);
  }
  root.prepend(grid);
  root.append(svg("line", { class: "baseline", x1: m.left, x2: W - m.right, y1: y(0), y2: y(0) }));

  const labelIdx = new Set([0, Math.floor((days.length - 1) / 2), days.length - 1]);
  days.forEach((d, i) => {
    const x0 = m.left + i * band;
    const cx = x0 + band / 2;
    const g = svg("g", { class: "day", tabindex: "0", "aria-label": `${d.day}: live ${usd(d.live_usd)}, mock ${usd(d.mock_usd)}` });
    g.append(svg("rect", { class: "hit", x: x0, y: m.top, width: band, height: ih }));
    if (d.live_usd > 0) g.append(column(cx - bw - 1, y(d.live_usd), bw, y(0) - y(d.live_usd), "bar-1"));
    if (d.mock_usd > 0) g.append(column(cx + 1, y(d.mock_usd), bw, y(0) - y(d.mock_usd), "bar-2"));
    const show = (ev) => { g.classList.add("is-active"); showTip(ev, d.day, [["var(--series-1)", "Live", usd(d.live_usd)], ["var(--series-2)", "Mock (est.)", usd(d.mock_usd)], [null, "Runs", int(d.runs)]]); };
    g.addEventListener("pointermove", show);
    g.addEventListener("focus", show);
    g.addEventListener("pointerleave", () => { g.classList.remove("is-active"); hideTip(); });
    g.addEventListener("blur", () => { g.classList.remove("is-active"); hideTip(); });
    root.append(g);
    if (labelIdx.has(i)) {
      const t = svg("text", { x: cx, y: H - 6, "text-anchor": i === 0 ? "start" : i === days.length - 1 ? "end" : "middle" });
      if (i === 0) t.setAttribute("x", x0);
      if (i === days.length - 1) t.setAttribute("x", x0 + band);
      t.textContent = new Date(d.day + "T00:00:00Z").toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
      root.append(t);
    }
  });
  host.replaceChildren(root);
}

// column with a 4px rounded top and a square base
function column(x, yTop, w, hgt, cls) {
  const r = Math.min(4, w / 2, hgt);
  const yb = yTop + hgt;
  const d = `M${x},${yb} V${yTop + r} Q${x},${yTop} ${x + r},${yTop} H${x + w - r} Q${x + w},${yTop} ${x + w},${yTop + r} V${yb} Z`;
  return svg("path", { d, class: cls });
}

function niceTicks(max) {
  if (max <= 0) return [0, 0.25, 0.5, 0.75, 1];
  const raw = max / 4;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((f) => f * pow).find((s) => s >= raw);
  const out = [];
  for (let v = 0; v <= max + step * 0.999; v += step) out.push(Number(v.toFixed(6)));
  return out;
}

function showTip(ev, title, rows) {
  const tip = $("#tooltip");
  tip.replaceChildren(
    h("div", { class: "tt-title" }, title),
    ...rows.map(([color, name, val]) => h("div", { class: "tt-row" },
      h("span", { class: "tt-key", style: color ? `background:${color}` : "" }), h("span", { class: "tt-name" }, name), h("span", { class: "tt-val" }, val))),
  );
  tip.hidden = false;
  const r = ev.clientX != null ? { x: ev.clientX, y: ev.clientY } : (() => { const b = ev.target.getBoundingClientRect(); return { x: b.left + b.width / 2, y: b.top }; })();
  const tw = tip.offsetWidth, th = tip.offsetHeight;
  let left = r.x + 14, topPos = r.y - th - 10;
  if (left + tw > innerWidth - 8) left = r.x - tw - 14;
  if (topPos < 8) topPos = r.y + 16;
  tip.style.left = `${left}px`;
  tip.style.top = `${topPos}px`;
}
function hideTip() { $("#tooltip").hidden = true; }

function table(cols, rows, emptyText) {
  if (!rows.length) return h("p", { class: "empty" }, emptyText);
  return h("table", {},
    h("thead", {}, h("tr", {}, cols.map(([label, cls]) => h("th", { class: cls }, label)))),
    h("tbody", {}, rows.map((r) => h("tr", {}, r.map((cell, i) => h("td", { class: cols[i][1] }, cell))))),
  );
}

function renderStages(rows) {
  const order = ["analyze", "prompt", "generate", "validate"];
  const sorted = [...rows].sort((a, b) => order.indexOf(a.stage) - order.indexOf(b.stage) || a.mode.localeCompare(b.mode));
  $("#stageTable").replaceChildren(table(
    [["Stage"], ["Mode"], ["Calls", "num"], ["Errors", "num"], ["Avg time", "num"], ["Cost", "num"]],
    sorted.map((r) => [STAGE_LABEL[r.stage] || r.stage, modeChip(r.mode), int(r.calls), int(r.errors), ms(r.avg_duration_ms), usd(r.cost_usd)]),
    "No calls in this period.",
  ));
}

function renderModels(o) {
  const unpriced = [];
  if (!o.models.llm_priced) unpriced.push(o.models.llm);
  if (!o.models.image_priced) unpriced.push(o.models.image);
  $("#modelNote").textContent = `Prices checked ${o.prices_checked_on}. Image: ${o.models.image}, ${o.models.image_size}, ${o.models.image_quality} quality.`
    + (unpriced.length ? ` No price entry for ${unpriced.join(", ")}; their cost shows as —.` : "");
  $("#modelTable").replaceChildren(table(
    [["Model"], ["Provider"], ["Mode"], ["Calls", "num"], ["Input tokens", "num"], ["Output tokens", "num"], ["Cost", "num"]],
    o.stats.by_model.map((r) => [r.model, r.provider, modeChip(r.mode), int(r.calls), int(r.input_tokens), int(r.output_tokens), usd(r.cost_usd)]),
    "No calls in this period.",
  ));
}

// ── runs list ─────────────────────────────────────────────────────────────────
async function loadRuns() {
  const q = new URLSearchParams({ limit: state.limit, offset: state.offset });
  if ($("#filterStatus").value) q.set("status", $("#filterStatus").value);
  if ($("#filterMode").value) q.set("mode", $("#filterMode").value);
  $("#runsTable").classList.add("is-loading");
  try {
    const { runs, total } = await api(`/runs?${q}`);
    const cols = [["Time"], ["Mode"], ["Status"], ["Scenario / reason"], ["Attempts", "num"], ["Duration", "num"], ["Cost", "num"]];
    if (!runs.length) {
      $("#runsTable").replaceChildren(h("p", { class: "empty" }, "No runs yet. Upload a photo on the website or start a test run."));
    } else {
      $("#runsTable").replaceChildren(h("table", {},
        h("thead", {}, h("tr", {}, cols.map(([l, c]) => h("th", { class: c }, l)))),
        h("tbody", {}, runs.map((r) => h("tr", {
          class: "row-link", tabindex: "0", "aria-label": `Open run ${r.id}`,
          onclick: () => openRun(r.id),
          onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openRun(r.id); } },
        },
          h("td", {}, when(r.created_at)),
          h("td", {}, modeChip(r.mode)),
          h("td", {}, statusEl(r.status)),
          h("td", { class: "muted" }, [r.scenario, r.reason].filter(Boolean).join(" · ") || "—"),
          h("td", { class: "num" }, int(r.attempts)),
          h("td", { class: "num" }, ms(r.duration_ms)),
          h("td", { class: "num" }, usd(r.cost_usd), h("span", { class: "est", title: r.cost_estimated ? "Estimate: mock run, not billed" : "" }, r.cost_estimated ? "est." : "")),
        ))),
      ));
    }
    $("#runsCount").textContent = total ? `${state.offset + 1}–${Math.min(state.offset + state.limit, total)} of ${int(total)}` : "";
    $("#prevPage").disabled = state.offset === 0;
    $("#nextPage").disabled = state.offset + state.limit >= total;
  } finally {
    $("#runsTable").classList.remove("is-loading");
  }
}
$("#filterStatus").addEventListener("change", () => { state.offset = 0; loadRuns(); });
$("#filterMode").addEventListener("change", () => { state.offset = 0; loadRuns(); });
$("#prevPage").addEventListener("click", () => { state.offset = Math.max(0, state.offset - state.limit); loadRuns(); });
$("#nextPage").addEventListener("click", () => { state.offset += state.limit; loadRuns(); });

// ── run detail ────────────────────────────────────────────────────────────────
const objectUrls = [];
function releaseUrls() { while (objectUrls.length) URL.revokeObjectURL(objectUrls.pop()); }

async function openRun(id) {
  state.openRun = id;
  clearTimeout(state.poll);
  $("#drawer").hidden = false;
  document.body.style.overflow = "hidden";
  await renderRun(id, true);
}
function closeDrawer() {
  state.openRun = null;
  clearTimeout(state.poll);
  releaseUrls();
  $("#drawer").hidden = true;
  document.body.style.overflow = "";
}
$$("[data-close]").forEach((el) => el.addEventListener("click", closeDrawer));
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!$("#modal").hidden) closeModal();
  else if (!$("#drawer").hidden) closeDrawer();
});
$("#deleteRun").addEventListener("click", async () => {
  const id = state.openRun;
  if (!id || !confirm("Delete this run, its trace and its images? This cannot be undone.")) return;
  try {
    await api(`/runs/${id}`, { method: "DELETE" });
    closeDrawer();
    await Promise.all([loadRuns(), loadOverview()]);
  } catch (err) { alert(err.message); }
});

async function renderRun(id, first = false) {
  let data;
  try { data = await api(`/runs/${id}`); } catch (err) {
    $("#drawerBody").replaceChildren(h("p", { class: "callout callout-error" }, err.message));
    return;
  }
  if (state.openRun !== id) return;
  const { run, spans } = data;
  const finished = ["delivered", "manual_review", "rejected", "error"].includes(run.status);

  $("#drawerTitle").replaceChildren(h("span", {}, "Run "), statusEl(run.status));
  $("#drawerSub").textContent = `${run.id} · from ${run.source}`;

  const body = [];
  body.push(h("div", { class: "facts" },
    fact("Mode", run.mode === "live" ? "Live (billed)" : "Mock (free)"),
    fact("Scenario", run.scenario ? run.scenario.replaceAll("_", " ") : "—"),
    fact("Stage", finished ? "Finished" : (STAGE_LABEL[run.stage] || "Queued")),
    fact("Attempts", int(run.attempts)),
    fact(run.cost_estimated ? "Cost (estimate)" : "Cost (billed)", usd(run.cost_usd)),
    fact("Duration", ms(run.duration_ms)),
    fact("Started", when(run.created_at)),
    fact("Input", run.input ? `${run.input.width ?? "?"}×${run.input.height ?? "?"} ${run.input.format ?? ""}`.trim() : "—"),
  ));
  if (run.reason && run.status !== "delivered") body.push(h("p", { class: "callout" }, `Reason: ${run.reason}`));
  if (run.error) body.push(h("p", { class: "callout callout-error" }, run.error));

  // images
  const attempts = run.result?.attempts || [];
  const files = [{ name: run.input?.stored_as || "input.jpg", label: "Input", note: "" }];
  for (const a of attempts) if (a.file) files.push({ name: a.file, label: `Attempt ${a.attempt}`, note: a.action === "deliver" ? "Delivered" : a.action === "retry" ? "Retried" : a.action === "manual_review" ? "Manual review" : "" });
  const gallery = h("div", { class: "gallery" });
  body.push(section("Images", gallery));
  files.forEach((f) => gallery.append(shot(run.id, f)));

  // reviews
  if (attempts.some((a) => a.checks)) body.push(section("Quality review", reviewTable(attempts)));

  // analysis
  if (run.analysis) body.push(section("Stage 1 analysis", codeBlock("analysis.json", JSON.stringify(run.analysis, null, 2))));

  // spans
  const list = h("div", { class: "section" });
  spans.forEach((s) => list.append(spanEl(s)));
  body.push(section(`Calls (${spans.length})`, spans.length ? list : h("p", { class: "muted" }, "No calls yet.")));

  const scrollTop = $("#drawerBody").scrollTop;
  releaseUrls();
  $("#drawerBody").replaceChildren(...body);
  if (!first) $("#drawerBody").scrollTop = scrollTop;

  if (!finished) state.poll = setTimeout(() => renderRun(id), 1500);
  else if (!first) { loadRuns(); loadOverview(); }
}

const fact = (label, value) => h("div", { class: "fact" }, h("span", {}, label), h("span", {}, value));
const section = (title, ...content) => h("section", { class: "section" }, h("h3", {}, title), ...content);

function shot(runId, f) {
  const img = h("img", { alt: `${f.label} image` });
  const btn = h("button", { type: "button", "aria-label": `Open ${f.label} full size` }, img);
  api(`/runs/${runId}/files/${f.name}`).then((blob) => {
    const url = URL.createObjectURL(blob);
    objectUrls.push(url);
    img.src = url;
    btn.addEventListener("click", () => window.open(url, "_blank", "noopener"));
  }).catch(() => { img.alt = "Image unavailable"; });
  return h("figure", { class: "shot" }, btn, h("figcaption", {}, h("span", {}, f.label), h("span", {}, f.note)));
}

function reviewTable(attempts) {
  const rows = attempts.filter((a) => a.checks);
  const names = { face_match: "Face match", attire_accuracy: "Attire", background: "Background", realism: "Realism" };
  const cell = (c) => h("span", { class: c.passed ? "pass" : "fail" }, `${c.passed ? "✓" : "✕"} ${c.score}`);
  const head = [["Check"], ["Threshold", "num"], ...rows.map((a) => [`Attempt ${a.attempt}`, "num"])];
  const body = Object.keys(names).map((k) => [names[k], String(rows[0].checks[k].threshold), ...rows.map((a) => cell(a.checks[k]))]);
  body.push(["Cultural gate", "pass/fail", ...rows.map((a) => h("span", { class: a.cultural.passed ? "pass" : "fail" }, a.cultural.passed ? "✓ Pass" : "✕ Fail"))]);
  body.push(["Decision", "", ...rows.map((a) => `${a.action}${a.reason && a.reason !== "passed" ? ` (${a.reason})` : ""}`)]);
  const wrap = h("div", { class: "table-wrap" }, table(head, body, ""));
  const notes = rows.filter((a) => a.guidance).map((a) => codeBlock(`Attempt ${a.attempt} retry guidance`, a.guidance));
  return h("div", { class: "section" }, wrap, ...notes);
}

function spanEl(s) {
  const tokensIn = s.usage ? (s.usage.input_tokens || 0) + (s.usage.text_input_tokens || 0) + (s.usage.image_input_tokens || 0) + (s.usage.cache_read_tokens || 0) + (s.usage.cache_write_tokens || 0) : null;
  const tokensOut = s.usage ? s.usage.output_tokens || 0 : null;
  const summary = h("summary", {},
    h("span", { class: "span-seq" }, `#${s.seq}`),
    h("span", {}, h("strong", {}, STAGE_LABEL[s.stage] || s.stage), s.attempt ? h("span", { class: "muted" }, ` · try ${s.attempt}`) : null),
    h("span", { class: "span-model", title: `${s.provider} · ${s.model}` }, `${s.provider} · ${s.model}`),
    h("span", { class: "span-num" }, s.status === "error" ? h("span", { class: "fail" }, "✕ Error") : ms(s.duration_ms)),
    h("span", { class: "span-num muted" }, tokensIn == null ? "—" : `${compact(tokensIn)} → ${compact(tokensOut)}`),
    h("span", { class: "span-num" }, s.cost_usd == null ? "—" : usd(s.cost_usd)),
  );
  const parts = [];
  const req = s.request || {};
  if (req.system) parts.push(codeBlock("System prompt", req.system));
  const content = Array.isArray(req.content) ? req.content : [];
  const userText = content.filter((p) => p.text).map((p) => p.text).join("\n\n");
  if (userText) parts.push(codeBlock("User message", userText));
  if (req.prompt) parts.push(codeBlock("Image prompt", req.prompt));
  const images = content.filter((p) => p.image).map((p) => `${p.image} (${p.media_type})`);
  const meta = { images: images.length ? images : undefined, image: req.image, max_tokens: req.max_tokens, params: req.params };
  if (Object.values(meta).some((v) => v != null)) parts.push(codeBlock("Request details", JSON.stringify(meta, null, 2)));
  const res = s.response || {};
  if (res.text) parts.push(codeBlock("Response text", res.text));
  if (res.parsed) parts.push(codeBlock("Parsed response", JSON.stringify(res.parsed, null, 2)));
  const rest = { ...res };
  delete rest.text; delete rest.parsed;
  if (Object.keys(rest).length) parts.push(codeBlock("Response details", JSON.stringify(rest, null, 2)));
  if (s.usage) parts.push(codeBlock(`Usage${s.cost_estimated ? " (simulated)" : ""}`, JSON.stringify(s.usage, null, 2)));
  if (s.error) parts.push(codeBlock("Error", s.error));
  return h("details", { class: "span" }, summary, h("div", { class: "span-body" }, parts));
}

function codeBlock(title, text) {
  const pre = h("pre", {}, text);
  const btn = h("button", { class: "copy", type: "button" }, "Copy");
  btn.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(text); btn.textContent = "Copied"; } catch { btn.textContent = "Copy failed"; }
    setTimeout(() => { btn.textContent = "Copy"; }, 1200);
  });
  return h("div", { class: "block" }, h("div", { class: "block-head" }, h("span", {}, title), btn), pre);
}

// ── new test run ──────────────────────────────────────────────────────────────
function openModal() {
  const sel = $("#runScenario");
  sel.replaceChildren(...state.scenarios.map((s) => h("option", { value: s.name }, s.name)));
  $("#runMode").value = "mock";
  $("#runMode").querySelector("option[value='live']").disabled = !state.overview?.live_available;
  $("#runError").textContent = "";
  syncScenario();
  $("#modal").hidden = false;
  $("#runFile").focus();
}
function closeModal() { $("#modal").hidden = true; $("#runForm").reset(); }
function syncScenario() {
  const mock = $("#runMode").value === "mock";
  $("#scenarioField").hidden = !mock;
  const s = state.scenarios.find((x) => x.name === $("#runScenario").value);
  $("#scenarioHelp").textContent = mock ? (s?.description || "") : "Live runs call the real APIs and are billed.";
}
$("#newRun").addEventListener("click", openModal);
$$("[data-close-modal]").forEach((el) => el.addEventListener("click", closeModal));
$("#runMode").addEventListener("change", syncScenario);
$("#runScenario").addEventListener("change", syncScenario);
$("#runForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = $("#runFile").files[0];
  if (!file) return;
  const form = new FormData();
  form.append("image", file);
  form.append("mode", $("#runMode").value);
  if ($("#runMode").value === "mock") form.append("scenario", $("#runScenario").value);
  const submit = $("#runForm button[type='submit']");
  submit.disabled = true;
  try {
    const { run_id } = await api("/runs", { method: "POST", body: form });
    closeModal();
    await loadRuns();
    openRun(run_id);
  } catch (err) {
    $("#runError").textContent = err.message;
  } finally {
    submit.disabled = false;
  }
});

// ── boot ──────────────────────────────────────────────────────────────────────
setChecked($("#period"), "data-days", state.days);
setChecked($("#dailyView"), "data-view", state.view);
let resizeTimer;
addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => state.overview && renderDaily(state.overview), 150); });

// Show the dashboard first so the chart measures its real width, then load.
async function enter() {
  $("#gate").hidden = true;
  $("#app").hidden = false;
  await loadAll();
}

(async () => {
  if (!getToken()) return showGate();
  try {
    await enter();
  } catch (err) {
    showGate(err instanceof Unauthorized ? "" : err.message);
  }
})();
