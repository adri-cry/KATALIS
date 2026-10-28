/* KATALIS dashboard — plain DOM, no framework, no build. */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const state = {
  symbol: "BBCA.JK",
  range: "6mo",
  tab: "roles",
  analysis: null,
  analysisJob: null,
  analysisTimer: null,
  analysisCache: {},
  journal: null,
  portfolio: null,
  watch: null,
  screener: { result: null, busy: false, symbols: "", maxPicks: 5, shortlist: 15, ai: false, record: false },
  health: null,
  chart: null,
  bars: [],
  live: null,
  stream: null,
  poll: null,
  lastFetch: 0,
  hoverIndex: null,
  hoverY: null,
};

/* Preferences that survive a refresh. A blocked storage (private mode) must
   fall back to the defaults rather than break the page. */
const prefs = {
  _read(key, fallback) {
    try {
      const value = localStorage.getItem(key);
      return value === null ? fallback : value;
    } catch { return fallback; }
  },
  _write(key, value) {
    try { localStorage.setItem(key, value); } catch { /* private mode */ }
  },
  get symbol() { return this._read("katalis.symbol", "BBCA.JK"); },
  set symbol(value) { this._write("katalis.symbol", value); },
  get auto() { return this._read("katalis.auto", "0") === "1"; },
  set auto(value) { this._write("katalis.auto", value ? "1" : "0"); },
  get ai() { return this._read("katalis.ai", "1") === "1"; },
  set ai(value) { this._write("katalis.ai", value ? "1" : "0"); },
  get analysisJob() {
    try { return JSON.parse(sessionStorage.getItem("katalis.analysisJob") || "null"); }
    catch { return null; }
  },
  set analysisJob(value) {
    try {
      if (value) sessionStorage.setItem("katalis.analysisJob", JSON.stringify(value));
      else sessionStorage.removeItem("katalis.analysisJob");
    } catch { /* storage may be unavailable */ }
  },
};

/* ---------- formatting ---------- */

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const rp = (n) => (n == null || Number.isNaN(n) ? "—" :
  Number(n).toLocaleString("id-ID", { maximumFractionDigits: 0 }));

const num = (n, d = 2) => (n == null || Number.isNaN(n) ? "—" :
  Number(n).toLocaleString("id-ID", { minimumFractionDigits: d, maximumFractionDigits: d }));

const pct = (n, d = 2) => (n == null || Number.isNaN(n) ? "—" :
  `${n > 0 ? "+" : ""}${Number(n).toLocaleString("id-ID", { minimumFractionDigits: d, maximumFractionDigits: d })}%`);

const tone = (n) => (n == null ? "flat" : n > 0 ? "up" : n < 0 ? "down" : "flat");
const clamp01 = (n) => Math.max(0, Math.min(1, n));

/* ---------- api ---------- */

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.error) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

function toast(message, isError = false) {
  let node = $(".toast");
  if (!node) {
    node = document.createElement("div");
    node.className = "toast";
    node.setAttribute("role", "status");
    node.setAttribute("aria-live", "polite");
    document.body.appendChild(node);
  }
  node.textContent = message;
  node.classList.toggle("err", isError);
  node.classList.add("show");
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => node.classList.remove("show"), 3800);
}

function normaliseSymbol(raw) {
  const value = String(raw || "").trim().toUpperCase();
  if (!value) return "";
  if (/^[A-Z]{4}$/.test(value)) return `${value}.JK`;
  return value;
}

/* ---------- boot ---------- */

async function boot() {
  wireEvents();
  if ($$("#tabs button").some((button) => button.dataset.tab === "screener")) {
    const tab = document.querySelector('#tabs button[data-tab="screener"]');
    if (tab) tab.title = "Saring beberapa saham sekaligus lewat gerbang, dewan analis, dan model opsional.";
  }
  tickClock();
  setInterval(tickClock, 1000);

  try {
    state.health = await api("/api/health");
    paintHealth();
  } catch (error) {
    const mode = $("#chip-mode");
    if (mode) { mode.textContent = "server tidak merespons"; mode.className = "chip"; mode.title = error.message; }
    toast(`Server tidak menjawab: ${error.message}`, true);
  }

  state.symbol = normaliseSymbol(prefs.symbol);
  $("#symbol").value = state.symbol;
  $("#opt-ai").checked = prefs.ai;
  $("#opt-auto").checked = prefs.auto;

  await Promise.all([loadWatchlist(), loadBenchmark(), loadPortfolio()]);

  // The watchlist has no stream of its own: one poll every 30 seconds keeps
  // eight cards current, and the server caches each symbol for 30 seconds.
  setInterval(loadWatchlist, 30000);
  setInterval(loadBenchmark, 60000);
  window.addEventListener("beforeunload", stopLive);

  const savedJob = prefs.analysisJob;

  // Opening the dashboard used to run a full analysis, which — once the model
  // was connected — meant one paid call per refresh, for a symbol nobody asked
  // about. It now loads prices only; the desk runs on the button, or on open
  // when the operator ticks "otomatis".
  if (savedJob) {
    await openSymbol(state.symbol);
    const resumed = await resumeAnalysisJob(savedJob);
    if (!resumed) prefs.analysisJob = null;
  } else {
    await openSymbol(state.symbol, { analyse: prefs.auto });
  }
}

function tickClock() {
  const now = new Date();
  $("#clock").textContent = now.toLocaleString("id-ID", {
    timeZone: "Asia/Jakarta", hour: "2-digit", minute: "2-digit", second: "2-digit",
  }) + " WIB";
  paintStamp();
}

function paintHealth() {
  const health = state.health;
  if (!health) return;
  const mode = $("#chip-mode");
  mode.textContent = "live · yahoo";
  mode.className = "chip chip-live";
  const idx = $("#chip-idx");
  if (health.idx && health.idx.available) {
    idx.textContent = `idx ${health.idx.version || ""}`.trim();
    idx.className = "chip chip-warn";
    idx.title = health.idx.path;
  } else {
    idx.textContent = "idx · mode proksi";
    idx.className = "chip";
    idx.title = health.idx?.hint || "Biner idx tidak ditemukan; peran data IDX memakai proksi harga.";
  }
  const ai = $("#chip-ai");
  if (ai) {
    if (health.llm && health.llm.configured) {
      ai.textContent = `AI · ${health.llm.model}`;
      ai.className = "chip chip-warn";
      const warn = (health.llm.warnings || [])[0];
      ai.title = warn || `${health.llm.endpoint} — lapisan penasihat aktif`;
      if (warn) ai.textContent = `AI · ${health.llm.model} ⚠`;
    } else {
      ai.textContent = "AI · mati";
      ai.className = "chip";
      const warn = (health.llm?.warnings || [])[0];
      ai.title = warn || health.llm?.hint || "Isi OPENAI_API_KEY untuk mengaktifkan penasihat AI.";
    }
  }
}

async function refreshHealth() {
  try {
    state.health = await api("/api/health");
    paintHealth();
    return state.health;
  } catch {
    return state.health;
  }
}

function diagnosticsBlock() {
  const info = state.health?.llm?.diagnostics;
  if (!info) return "";
  const rows = [
    ["Berkas .env", info.env_exists ? "ditemukan" : "tidak ditemukan"],
    ["Baris OPENAI_API_KEY", info.key_in_file ? "ada" : "tidak ada"],
    ["Panjang kunci", info.key_length ? `${info.key_length} karakter` : "kosong"],
    ["Model", info.model],
    ["Endpoint", info.endpoint],
  ];
  const warnings = (info.warnings || []).map((item) => `<li>${esc(item)}</li>`).join("");
  return `<div class="diag">
    <table>${rows.map(([key, value]) =>
      `<tr><th>${esc(key)}</th><td>${esc(value)}</td></tr>`).join("")}</table>
    ${warnings ? `<ul class="diag-warn">${warnings}</ul>` : ""}
    <button class="primary" id="reload-ai" style="margin-top:12px">Periksa ulang .env</button>
  </div>`;
}

/* ---------- watchlist ---------- */

function wireWatchlist() {
  $("#watchlist").addEventListener("click", (event) => {
    const row = event.target.closest(".wrow");
    if (!row) return;
    const remove = event.target.closest("[data-remove]");
    if (remove) return void removeWatch(remove.dataset.remove);
    if (event.target.closest(".rowmain")) openSymbol(row.dataset.symbol);
  });
}

async function removeWatch(symbol) {
  try {
    await api("/api/watchlist", { method: "POST", body: JSON.stringify({ remove: symbol }) });
    toast(`${symbol.replace(".JK", "")} dihapus dari watchlist.`);
    await loadWatchlist();
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadWatchlist() {
  const body = $("#watchlist");
  if (!state.watch) body.innerHTML = '<div class="skeleton"></div><div class="skeleton"></div>';
  try {
    const previous = state.watch?.quotes || [];
    const payload = await api("/api/watchlist");
    state.watch = payload;
    const focusSym = document.activeElement?.closest?.(".wrow")?.dataset.symbol ?? null;
    const focusDel = document.activeElement?.classList?.contains("del") ?? false;
    body.innerHTML = payload.quotes.map(watchRow).join("") || '<p class="muted pad">Watchlist kosong.</p>';
    if (focusSym) {
      const row = body.querySelector(`.wrow[data-symbol="${CSS.escape(focusSym)}"]`);
      (focusDel ? row?.querySelector(".del") : row?.querySelector(".rowmain"))?.focus();
    }
    paintQuick(payload.symbols);
    flashChanged(previous, payload.quotes);
  } catch (error) {
    if (!state.watch) body.innerHTML = `<p class="muted pad">Gagal memuat: ${esc(error.message)}</p>`;
  }
}

/* Rows whose price moved since the last poll get a brief tint — the reason the
   card is worth watching without a refresh. */
function flashChanged(before, after) {
  const seen = new Map((before || []).map((quote) => [quote.symbol, quote.price]));
  (after || []).forEach((quote) => {
    const previous = seen.get(quote.symbol);
    if (previous == null || quote.price == null || quote.price === previous) return;
    const row = document.querySelector(`.wrow[data-symbol="${CSS.escape(quote.symbol)}"]`);
    if (row) flash(row, quote.price > previous);
  });
}

function sparkline(values) {
  if (!values || values.length < 2) return "";
  const min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
  const points = values.map((v, i) =>
    `${(i / (values.length - 1) * 100).toFixed(2)},${(18 - (v - min) / span * 16).toFixed(2)}`).join(" ");
  const colour = values[values.length - 1] >= values[0] ? "#22c55e" : "#ef4444";
  return `<svg viewBox="0 0 100 20" preserveAspectRatio="none"><polyline points="${points}"
          fill="none" stroke="${colour}" stroke-width="1.2" vector-effect="non-scaling-stroke"/></svg>`;
}

const xIcon = `<svg viewBox="0 0 10 10" width="9" height="9" aria-hidden="true">
  <path d="M1 1l8 8M9 1l-8 8" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" fill="none"/></svg>`;

function watchRow(quote) {
  const del = `<button class="del" data-remove="${esc(quote.symbol)}"
    aria-label="Hapus ${esc(quote.symbol.replace(".JK", ""))} dari watchlist" title="Hapus">${xIcon}</button>`;
  if (quote.error) {
    return `<div class="wrow" data-symbol="${esc(quote.symbol)}">
      <button class="rowmain" data-symbol="${esc(quote.symbol)}" aria-label="Lihat ${esc(quote.symbol)}">
        <div><div class="sym">${esc(quote.symbol)}</div><div class="nm">tidak tersedia</div></div>
        <div class="chg flat">—</div>
      </button>${del}</div>`;
  }
  const on = quote.symbol === state.symbol ? " on" : "";
  return `<div class="wrow${on}" data-symbol="${esc(quote.symbol)}">
    <button class="rowmain" data-symbol="${esc(quote.symbol)}" aria-label="Lihat ${esc(quote.symbol.replace(".JK", ""))}">
      <div><div class="sym">${esc(quote.symbol.replace(".JK", ""))}</div>
           <div class="nm">${esc(quote.name || "")}</div></div>
      <div><div class="px">${rp(quote.price)}</div>
           <div class="chg ${tone(quote.change_pct)}">${pct(quote.change_pct)}</div></div>
    </button>${del}
    ${sparkline(quote.spark)}
  </div>`;
}

function paintQuick(symbols) {
  $("#quick").innerHTML = symbols.slice(0, 8).map((symbol) =>
    `<button data-symbol="${esc(symbol)}">${esc(symbol.replace(".JK", ""))}</button>`).join("");
  $$("#quick button").forEach((button) => button.addEventListener("click", () =>
    openSymbol(button.dataset.symbol)));
}

/* ---------- market ---------- */

async function loadBenchmark() {
  try {
    const payload = await api("/api/benchmark");
    const indicators = payload.indicators || {};
    $("#ihsg-price").textContent = rp(payload.price);
    const change = indicators.change_pct ?? 0;
    const node = $("#ihsg-chg");
    node.textContent = pct(change);
    node.className = tone(change);
  } catch { /* the index is decoration; the page works without it */ }
}

async function openSymbol(symbol, { analyse = false } = {}) {
  state.symbol = normaliseSymbol(symbol);
  prefs.symbol = state.symbol;
  $("#symbol").value = state.symbol;
  $$(".wrow").forEach((row) => row.classList.toggle("on", row.dataset.symbol === state.symbol));
  startLive(state.symbol);        // the stream starts first: a live price often lands before the chart
  // Chart and desk both use the server-side market cache, but neither needs the
  // other to finish. Run them together instead of making analysis wait for the
  // chart HTTP round-trip first.
  const tasks = [loadMarket(state.symbol, state.range)];
  if (analyse) {
    tasks.push(runAnalysis());
  } else if (analysisBelongsTo(state.analysisJob, state.symbol)) {
    // A job is still running for the symbol being selected: show its progress.
    paintAnalysisProgress(state.analysisJob);
  } else if (state.analysisCache[state.symbol]) {
    // Its analysis finished while another symbol was on screen; the verdict was
    // kept here instead of being painted onto the wrong chart.
    const cached = state.analysisCache[state.symbol];
    delete state.analysisCache[state.symbol];
    state.analysis = cached;
    paintVerdict(cached);
  } else {
    // Selecting a symbol costs nothing; running the desk is a separate click.
    paintIdleVerdict();
  }
  await Promise.all(tasks);
}

async function loadMarket(symbol, range, { fresh = false } = {}) {
  if (!state.bars.length) $("#chart").innerHTML = '<div class="skeleton tall"></div>';
  try {
    const payload = await api(
      `/api/market?symbol=${encodeURIComponent(symbol)}&range=${range}${fresh ? "&fresh=1" : ""}`);
    paintChart(payload);
  } catch (error) {
    $("#chart").innerHTML = `<p class="muted pad">${esc(error.message)}</p>`;
  }
}

function paintChart(payload) {
  state.chart = payload;
  state.bars = (payload.bars || []).map((bar) => ({ ...bar }));
  state.lastFetch = Date.now();

  const indicators = payload.indicators || {};
  $("#chart-title").textContent = payload.symbol || state.symbol;
  const basis = payload.interval && payload.interval !== "1d" ? ` · bar ${payload.interval}` : "";
  $("#chart-sub").textContent =
    [payload.name, payload.exchange, payload.currency].filter(Boolean).join(" · ") + basis;

  paintLiveHead();
  paintChartSvg();
  paintMetrics(indicators);
  paintStamp();
}

function paintLiveHead() {
  const live = state.live;
  const price = live?.price ?? state.chart?.price;
  const change = live?.change_pct ?? state.chart?.indicators?.change_pct;
  const node = $("#chart-price");
  if (!node) return;
  node.textContent = rp(price);
  node.className = `chart-price ${tone(change)}`;
  const badge = $("#chart-chg");
  if (badge) {
    badge.textContent = pct(change);
    badge.className = `chart-chg ${tone(change)}`;
  }
}

/* The desk's plan belongs to the analysed symbol only; a chart of another
   symbol must not inherit its lines. */
function currentPlan() {
  const analysis = state.analysis;
  if (!analysis || analysis.symbol !== state.symbol) return null;
  return analysis.trader || null;
}

function paintChartSvg() {
  $("#chart").innerHTML = renderCandles(state.bars, {
    prevClose: state.live?.prev_close ?? state.chart?.prev_close,
    plan: currentPlan(),
    interval: state.chart?.interval,
    timezone: state.chart?.timezone,
  });
  const svg = $("#chart svg");
  if (!svg) return;
  attachHover(svg);
  // A tick re-renders the chart every few seconds; the crosshair the cursor is
  // holding must survive that, so it is redrawn from the last position.
  if (state.hoverIndex != null) showCross(svg, state.hoverIndex, state.hoverY);
}

function paintMetrics(indicators) {
  const ind = indicators || {};
  const tile = ([key, value, cls]) =>
    `<div class="metric"><div class="k">${key}</div><div class="v ${cls}">${value}</div></div>`;
  const main = [
    ["Terakhir", rp(ind.last), tone(ind.change_pct)],
    ["Perubahan", pct(ind.change_pct), tone(ind.change_pct)],
    ["RSI 14", num(ind.rsi14, 1), ""],
    ["ATR", `${num(ind.atr_pct, 2)}%`, ""],
  ];
  const more = [
    ["MACD hist", num(ind.macd_hist, 1), tone(ind.macd_hist)],
    ["Vol 20h", rp(ind.vol_avg20), ""],
    ["Posisi 52m", ind.pos_52w == null ? "—" : `${Math.round(ind.pos_52w * 100)}%`, ""],
    ["Drawdown", `${num(ind.max_drawdown_pct, 1)}%`, "down"],
  ];
  $("#metrics").innerHTML =
    main.map(tile).join("") +
    `<details class="metric-more"><summary>4 indikator lain</summary>
      <div class="metrics-sub">${more.map(tile).join("")}</div></details>`;
}

function paintStamp() {
  const node = $("#chart-stamp");
  if (!node) return;
  const live = state.live;
  const seconds = state.lastFetch ? Math.max(0, Math.round((Date.now() - state.lastFetch) / 1000)) : null;
  const parts = [];
  if (state.stream) parts.push('<span class="pulse"></span>streaming 5 detik');
  if (seconds != null) parts.push(`chart ${seconds}s lalu`);
  if (live?.age != null) parts.push(`tick ${live.age}s`);
  if (state.chart?.cached) parts.push("dari cache");
  node.innerHTML = parts.join(" · ");
}

/* ---------- live stream ---------- */

function setLiveBadge(connected) {
  const node = $("#chart-live");
  if (!node) return;
  node.classList.toggle("on", Boolean(connected));
  node.textContent = connected ? "LIVE" : "terputus";
}

function startLive(symbol) {
  stopLive();
  state.live = null;
  setLiveBadge(false);

  if (typeof EventSource === "undefined") {
    // Older browser: poll instead. Same handler, same UI.
    const tick = async () => {
      try {
        const payload = await api(`/api/tick?symbol=${encodeURIComponent(symbol)}`);
        setLiveBadge(true);
        applyTick(payload);
      } catch { setLiveBadge(false); }
      state.poll = setTimeout(tick, 8000);
    };
    tick();
    return;
  }

  const source = new EventSource(`/api/stream?symbol=${encodeURIComponent(symbol)}`);
  state.stream = source;
  source.onopen = () => setLiveBadge(true);
  source.onerror = () => setLiveBadge(false);      // EventSource reconnects on its own
  source.onmessage = (event) => {
    let payload;
    try { payload = JSON.parse(event.data); } catch { return; }
    if (payload.error) { setLiveBadge(false); return; }
    setLiveBadge(true);
    applyTick(payload);
  };
}

function stopLive() {
  if (state.stream) { state.stream.close(); state.stream = null; }
  if (state.poll) { clearTimeout(state.poll); state.poll = null; }
}

function flash(node, up) {
  if (!node) return;
  const cls = up ? "flash-up" : "flash-down";
  node.classList.remove("flash-up", "flash-down");
  void node.offsetWidth;                            // restart the animation
  node.classList.add(cls);
}

function applyTick(tick) {
  if (!tick || tick.symbol !== state.symbol) return;
  const previous = state.live?.price ?? state.chart?.price;
  state.live = tick;

  const node = $("#chart-price");
  paintLiveHead();
  if (previous != null && tick.price != null && tick.price !== previous) {
    flash(node, tick.price > previous);
  }

  // Keep the last candle honest: today's close, and the extremes the price has
  // touched since the bar opened. A 5-minute bar from the tick can also be a
  // new bar the chart does not have yet.
  if (state.bars.length && tick.price != null) {
    const last = state.bars[state.bars.length - 1];
    last.c = tick.price;
    last.h = Math.max(last.h, tick.price);
    last.l = Math.min(last.l, tick.price);
    if (state.chart?.interval && state.chart.interval !== "1d" && tick.bar && tick.bar.t > last.t) {
      state.bars.push({ ...tick.bar });
      state.bars = state.bars.slice(-400);
    }
    paintChartSvg();
  }

  // The watchlist card shows the same instrument; keep it in step.
  const row = document.querySelector(`.wrow[data-symbol="${CSS.escape(tick.symbol)}"]`);
  if (row) {
    const priceNode = row.querySelector(".px");
    const changeNode = row.querySelector(".chg");
    if (priceNode) { priceNode.textContent = rp(tick.price); flash(priceNode, tick.change_pct >= 0); }
    if (changeNode) {
      changeNode.textContent = pct(tick.change_pct);
      changeNode.className = `chg ${tone(tick.change_pct)}`;
    }
  }
  paintStamp();
}

function renderCandles(bars, options = {}) {
  if (!bars || !bars.length) return '<p class="muted pad">Tidak ada bar.</p>';
  const W = 900, H = 344, left = 8, right = 64, top = 14, bottom = 30;
  const priceH = 212, gap = 18, volH = H - top - bottom - priceH - gap;
  const plotW = W - left - right;
  const high = Math.max(...bars.map((b) => b.h));
  const low = Math.min(...bars.map((b) => b.l));
  const span = high - low || 1;
  const yPrice = (v) => top + (high - v) / span * priceH;
  const maxVol = Math.max(...bars.map((b) => b.v), 1);
  const step = plotW / bars.length;
  const bodyW = Math.max(1.4, step * 0.62);
  const parts = [];

  for (let i = 0; i <= 4; i += 1) {
    const value = low + span * (i / 4), y = yPrice(value).toFixed(1);
    parts.push(`<line x1="${left}" y1="${y}" x2="${left + plotW}" y2="${y}" stroke="#1a2836" stroke-width="1"/>`);
    parts.push(`<text x="${left + plotW + 6}" y="${Number(y) + 3.5}" fill="var(--muted)" font-size="10"
                font-family="ui-monospace, monospace">${rp(value)}</text>`);
  }

  // Yesterday's close: the line an intraday move is judged against.
  const previous = options.prevClose;
  if (previous && previous >= low && previous <= high) {
    const y = yPrice(previous).toFixed(1);
    parts.push(`<line x1="${left}" y1="${y}" x2="${left + plotW}" y2="${y}" stroke="#64748b"
                stroke-width="1" stroke-dasharray="2 4"/>`);
    parts.push(`<text x="${left + 4}" y="${Number(y) - 4}" fill="var(--muted)" font-size="9.5"
                font-family="ui-monospace, monospace">penutupan sebelumnya ${rp(previous)}</text>`);
  }

  // The desk's committed levels drawn where they are decided — on the price.
  // ponytail: a level outside the visible span clamps to the edge, and a tag
  // can collide with the last-price tag when levels sit close together;
  // offset stacked tags if that ever bothers anyone.
  const plan = options.plan;
  const planLine = (value, colour, dashed) => {
    if (value == null || !Number.isFinite(Number(value))) return;
    const y = Math.max(top, Math.min(top + priceH, yPrice(Number(value))));
    parts.push(`<line x1="${left}" y1="${y.toFixed(1)}" x2="${left + plotW}" y2="${y.toFixed(1)}"
                stroke="${colour}" stroke-width="1" ${dashed ? 'stroke-dasharray="5 4" ' : ""}stroke-opacity="0.85"/>`);
    parts.push(`<rect x="${left + plotW + 2}" y="${(y - 8).toFixed(1)}" width="58" height="16" rx="3" fill="${colour}"/>`);
    parts.push(`<text x="${left + plotW + 31}" y="${(y + 3.5).toFixed(1)}" text-anchor="middle" fill="#0a0e13"
                font-size="10" font-weight="700" font-family="ui-monospace, monospace">${rp(Number(value))}</text>`);
  };
  if (plan) {
    planLine(plan.entry, "#f0b429", false);
    planLine(plan.stop, "#ef4444", true);
    planLine(plan.target, "#22c55e", true);
  }

  bars.forEach((bar, index) => {
    const x = left + index * step + (step - bodyW) / 2;
    const up = bar.c >= bar.o;
    const colour = up ? "#22c55e" : "#ef4444";
    const yHigh = yPrice(bar.h), yLow = yPrice(bar.l);
    const yOpen = yPrice(bar.o), yClose = yPrice(bar.c);
    parts.push(`<line x1="${(x + bodyW / 2).toFixed(1)}" y1="${yHigh.toFixed(1)}"
                x2="${(x + bodyW / 2).toFixed(1)}" y2="${yLow.toFixed(1)}" stroke="${colour}" stroke-width="1"/>`);
    const y = Math.min(yOpen, yClose), height = Math.max(1, Math.abs(yClose - yOpen));
    parts.push(`<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bodyW.toFixed(1)}"
                height="${height.toFixed(1)}" fill="${colour}" fill-opacity="0.9"/>`);
    const volY = top + priceH + gap + (volH - bar.v / maxVol * volH);
    parts.push(`<rect x="${x.toFixed(1)}" y="${volY.toFixed(1)}" width="${bodyW.toFixed(1)}"
                height="${(bar.v / maxVol * volH).toFixed(1)}" fill="${colour}" fill-opacity="0.28"/>`);
  });

  if (bars.length >= 20) {
    const points = [];
    for (let i = 19; i < bars.length; i += 1) {
      const slice = bars.slice(i - 19, i + 1);
      const average = slice.reduce((sum, bar) => sum + bar.c, 0) / slice.length;
      points.push(`${(left + i * step + step / 2).toFixed(1)},${yPrice(average).toFixed(1)}`);
    }
    parts.push(`<polyline points="${points.join(" ")}" fill="none" stroke="#f0b429" stroke-width="1.3"
                stroke-opacity="0.85"/>`);
  }

  const last = bars[bars.length - 1];
  const lastY = yPrice(last.c);
  parts.push(`<line x1="${left}" y1="${lastY.toFixed(1)}" x2="${left + plotW}" y2="${lastY.toFixed(1)}"
              stroke="#f0b429" stroke-width="1" stroke-dasharray="4 4" stroke-opacity="0.6"/>`);
  parts.push(`<circle cx="${(left + plotW - 2).toFixed(1)}" cy="${lastY.toFixed(1)}" r="3.2" fill="#f0b429">
                <animate attributeName="r" values="3;5.4;3" dur="1.8s" repeatCount="indefinite"/>
                <animate attributeName="opacity" values="1;0.35;1" dur="1.8s" repeatCount="indefinite"/>
              </circle>`);
  parts.push(`<rect x="${left + plotW + 2}" y="${(lastY - 8).toFixed(1)}" width="58" height="16" rx="3"
              fill="#f0b429"/>`);
  parts.push(`<text x="${left + plotW + 31}" y="${(lastY + 4).toFixed(1)}" text-anchor="middle"
              fill="#241a02" font-size="10" font-weight="700"
              font-family="ui-monospace, monospace">${rp(last.c)}</text>`);

  // A time axis. Without it the chart was a wall of candles with no reference.
  const marks = [0, 0.25, 0.5, 0.75, 1];
  marks.forEach((fraction, position) => {
    const index = Math.min(bars.length - 1, Math.round(fraction * (bars.length - 1)));
    const x = left + index * step + step / 2;
    const anchor = position === 0 ? "start" : position === marks.length - 1 ? "end" : "middle";
    parts.push(`<text x="${x.toFixed(1)}" y="${H - 8}" text-anchor="${anchor}" fill="var(--muted)"
                font-size="9.5" font-family="ui-monospace, monospace">${esc(
                  axisLabel(bars[index].t, options.interval, options.timezone))}</text>`);
  });

  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="grafik harga"
      data-left="${left}" data-top="${top}" data-plotw="${plotW.toFixed(2)}"
      data-priceh="${priceH}" data-step="${step.toFixed(3)}"
      data-high="${high}" data-low="${low}" data-count="${bars.length}">${parts.join("")}
    <g id="xk-cross" pointer-events="none" style="display:none">
      <rect class="xk-col" x="0" y="${top}" width="${step.toFixed(2)}" height="${priceH}"
            fill="#e8eff5" opacity="0.055"/>
      <line class="xk-v" x1="0" y1="${top}" x2="0" y2="${top + priceH}" stroke="#8fa3b3"
            stroke-width="1" stroke-dasharray="3 3" opacity="0.8"/>
      <line class="xk-h" x1="${left}" y1="0" x2="${left + plotW}" y2="0" stroke="#8fa3b3"
            stroke-width="1" stroke-dasharray="3 3" opacity="0.8"/>
      <circle class="xk-dot" cx="0" cy="0" r="3.2" fill="#f0b429" stroke="#0a0e13" stroke-width="1"/>
      <rect class="xk-ybox" x="${left + plotW + 2}" y="0" width="58" height="16" rx="3" fill="#22323f"/>
      <text class="xk-ytext" x="${left + plotW + 31}" y="0" text-anchor="middle" fill="#e8eff5"
            font-size="10" font-family="ui-monospace, monospace"></text>
    </g>
  </svg>`;
}

/* ---------- crosshair ---------- */

function toLocal(svg, clientX, clientY) {
  const matrix = svg.getScreenCTM();
  if (!matrix) return null;
  const point = svg.createSVGPoint();
  point.x = clientX;
  point.y = clientY;
  return point.matrixTransform(matrix.inverse());
}

function attachHover(svg) {
  const move = (event) => {
    const source = event.touches ? event.touches[0] : event;
    if (!source) return;
    const local = toLocal(svg, source.clientX, source.clientY);
    if (!local) return;
    const data = svg.dataset;
    const step = Number(data.step) || 1;
    const index = Math.round((local.x - Number(data.left) - step / 2) / step);
    state.hoverIndex = index;
    state.hoverY = local.y;
    showCross(svg, index, local.y);
  };
  const leave = () => {
    state.hoverIndex = null;
    state.hoverY = null;
    hideCross(svg);
  };
  svg.addEventListener("mousemove", move);
  svg.addEventListener("mouseleave", leave);
  svg.addEventListener("touchstart", move, { passive: true });
  svg.addEventListener("touchmove", move, { passive: true });
  svg.addEventListener("touchend", leave);
}

function showCross(svg, index, localY) {
  const cross = svg.querySelector("#xk-cross");
  if (!cross) return;
  const count = state.bars.length;
  if (!count) return hideCross(svg);

  // The pointer can sit in the right-hand price margin, past the last candle.
  // Clamping keeps the readout on the edge instead of hiding it.
  const at = Math.max(0, Math.min(count - 1, Number.isFinite(index) ? Math.trunc(index) : 0));
  const bar = state.bars[at];

  const data = svg.dataset;
  const left = Number(data.left), top = Number(data.top), plotW = Number(data.plotw);
  const priceH = Number(data.priceh), step = Number(data.step);
  const high = Number(data.high), low = Number(data.low);
  const span = (high - low) || 1;
  const yOf = (value) => top + (high - value) / span * priceH;

  const centre = left + at * step + step / 2;
  const y = Math.max(top, Math.min(top + priceH, Number.isFinite(localY) ? localY : yOf(bar.c)));

  cross.style.display = "";
  const column = cross.querySelector(".xk-col");
  column.setAttribute("x", (left + at * step).toFixed(2));
  column.setAttribute("width", Math.max(1, step).toFixed(2));

  const vertical = cross.querySelector(".xk-v");
  vertical.setAttribute("x1", centre.toFixed(2));
  vertical.setAttribute("x2", centre.toFixed(2));

  const horizontal = cross.querySelector(".xk-h");
  horizontal.setAttribute("y1", y.toFixed(2));
  horizontal.setAttribute("y2", y.toFixed(2));

  const dot = cross.querySelector(".xk-dot");
  dot.setAttribute("cx", centre.toFixed(2));
  dot.setAttribute("cy", yOf(bar.c).toFixed(2));

  const price = high - (y - top) / priceH * span;
  cross.querySelector(".xk-ybox").setAttribute("y", (y - 8).toFixed(2));
  const label = cross.querySelector(".xk-ytext");
  label.setAttribute("y", (y + 3.6).toFixed(2));
  label.textContent = rp(price);

  showTip(svg, at, centre, yOf(bar.c));
}

function tipPlanRows(price) {
  const plan = currentPlan();
  if (!plan) return "";
  const row = (label, level) => {
    if (level == null || !Number.isFinite(Number(level))) return "";
    const delta = price - Number(level);
    const sign = delta >= 0 ? "+" : "";
    return `<div><span class="muted">${label}</span> ${rp(Number(level))}
      <span class="${tone(delta)}">${sign}${num(delta, 0)} (${sign}${num(delta / Number(level) * 100, 1)}%)</span></div>`;
  };
  const body = [row("entry", plan.entry), row("stop", plan.stop), row("target", plan.target)].filter(Boolean).join("");
  return body ? `<div class="xk-plan">${body}</div>` : "";
}

function showTip(svg, index, localX, localY) {
  const host = $("#chart");
  if (!host) return;
  let tip = $("#xk-tip");
  if (!tip) {
    tip = document.createElement("div");
    tip.id = "xk-tip";
    tip.className = "xk-tip";
    host.appendChild(tip);
  }

  const bar = state.bars[index];
  const previous = index > 0 ? state.bars[index - 1].c : bar.o;
  const change = previous ? (bar.c / previous - 1) * 100 : null;
  const style = tone(change);
  tip.innerHTML = `
    <div class="xk-when">${esc(fullLabel(bar.t, state.chart?.interval, state.chart?.timezone))}</div>
    <div class="xk-ohlc">
      <span>O</span><b>${rp(bar.o)}</b>
      <span>H</span><b class="up">${rp(bar.h)}</b>
      <span>L</span><b class="down">${rp(bar.l)}</b>
      <span>C</span><b class="${style}">${rp(bar.c)}</b>
    </div>
    <div class="xk-meta">
      <span class="${style}">${pct(change)}</span>
      <span class="muted">vol ${rp(bar.v)}</span>
    </div>
    ${tipPlanRows(bar.c)}`;
  tip.style.display = "block";

  const matrix = svg.getScreenCTM();
  if (!matrix) return;
  const point = svg.createSVGPoint();
  point.x = localX;
  point.y = localY;
  const screen = point.matrixTransform(matrix);
  const bounds = host.getBoundingClientRect();
  const x = screen.x - bounds.left;
  const y = screen.y - bounds.top;
  const width = tip.offsetWidth || 180;
  const left = x + 16 + width > bounds.width ? x - width - 16 : x + 16;
  tip.style.left = `${Math.max(4, left)}px`;
  tip.style.top = `${Math.max(4, Math.min(bounds.height - 90, y - 48))}px`;
}

function hideCross(svg) {
  const cross = svg.querySelector("#xk-cross");
  if (cross) cross.style.display = "none";
  const tip = $("#xk-tip");
  if (tip) tip.style.display = "none";
}

function fullLabel(stamp, interval, timeZone) {
  const date = new Date(stamp * 1000);
  const intraday = interval && interval !== "1d";
  try {
    const day = date.toLocaleDateString("id-ID",
      { timeZone: intraday ? timeZone : "UTC", day: "2-digit", month: "short", year: "numeric" });
    if (!intraday) return day;
    const time = date.toLocaleTimeString("id-ID",
      { timeZone, hour: "2-digit", minute: "2-digit" });
    return `${day} · ${time}`;
  } catch {
    return date.toISOString().slice(0, intraday ? 16 : 10).replace("T", " · ");
  }
}

function axisLabel(stamp, interval, timeZone) {
  const date = new Date(stamp * 1000);
  const intraday = interval && interval !== "1d";
  try {
    return intraday
      ? date.toLocaleTimeString("id-ID", { timeZone, hour: "2-digit", minute: "2-digit" })
      : date.toLocaleDateString("id-ID", { timeZone: "UTC", day: "2-digit", month: "short" });
  } catch {
    // A timezone name the browser does not know must not blank the axis.
    return intraday ? date.toISOString().slice(11, 16) : date.toISOString().slice(5, 10);
  }
}

/* ---------- analysis ---------- */

function analysisProgressView(job) {
  const labels = {
    queue: "Menyiapkan analisis…",
    market: "Mengambil harga dan menghitung peran analis…",
    ai: "Menunggu model AI menyusun tesis dan risiko…",
    save: "Menyimpan hasil analisis…",
  };
  const startedAt = job.started_at ? (job.started_at > 1e12 ? job.started_at : job.started_at * 1000) : Date.now();
  const seconds = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
  return `<div class="analysis-progress" role="status" aria-live="polite">
    <span class="analysis-spinner" aria-hidden="true"></span>
    <div class="analysis-progress-body">
      <strong>${esc(job.message || labels[job.stage] || "Analisis sedang berjalan…")}</strong>
      <p class="muted">${esc(job.symbol || state.symbol)} · berjalan ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}</p>
      ${job.stage === "ai" ? '<p class="muted">Model mengerjakan jawaban secara streaming — label di atas berubah saat mulai menimbang bukti dan mulai menulis. Berhenti berarti menunggu layanan model.</p>' : ""}
      <div class="analysis-progress-track"><i></i></div>
    </div>
  </div>`;
}

/* A job carries the symbol it was started for. A job without one predates this
   field, and is treated as belonging to whatever is on screen. */
function analysisBelongsTo(job, symbol) {
  if (!job) return false;
  return !job.symbol || !symbol || job.symbol === symbol;
}

function paintAnalysisProgress(job) {
  if (!analysisBelongsTo(job, state.symbol)) return;   // never paint A's progress over B's chart
  const verdict = $("#verdict");
  if (verdict) verdict.innerHTML = analysisProgressView(job);
  const button = $("#analyse");
  if (button) {
    button.disabled = true;
    button.textContent = "Menganalisis…";
  }
}

function paintJobError(job, message) {
  if (!analysisBelongsTo(job, state.symbol)) {
    toast(`Analisis ${job?.symbol || "sebelumnya"} gagal: ${message}`, true);
    return;
  }
  if ($("#verdict")) {
    $("#verdict").innerHTML = `<div class="empty"><p>${esc(message)}</p><p class="muted">Tekan Analisis untuk mencoba lagi.</p></div>`;
  }
}

function finishAnalysisJob() {
  if (state.analysisTimer) {
    clearTimeout(state.analysisTimer);
    clearInterval(state.analysisTimer);
  }
  state.analysisTimer = null;
  state.analysisJob = null;
  prefs.analysisJob = null;
  const button = $("#analyse");
  if (button) {
    button.disabled = false;
    button.textContent = "Analisis";
  }
}

async function pollAnalysisJob(job) {
  if (!job?.id) return;
  state.analysisJob = job;
  paintAnalysisProgress(job);
  try {
    const status = await api(`/api/analyze/status?id=${encodeURIComponent(job.id)}`);
    Object.assign(job, status);
    state.analysisJob = job;
    prefs.analysisJob = job;
    if (status.status === "complete") {
      const payload = await api(`/api/analyze/result?id=${encodeURIComponent(job.id)}`);
      finishAnalysisJob();
      if (!analysisBelongsTo(payload, state.symbol)) {
        // The operator moved on. Keep the result instead of painting it onto
        // another symbol's chart, and hand it back when they return.
        state.analysisCache[payload.symbol] = payload;
        toast(`Analisis ${payload.symbol} selesai — pilih simbol itu untuk melihat hasilnya.`);
        return;
      }
      state.analysis = payload;
      paintVerdict(payload);
      paintPanel(state.tab);
      if (payload.recorded) toast(`Keputusan tersimpan di jurnal (#${payload.recorded}).`);
      return;
    }
    if (status.status === "error") {
      const message = status.error || status.message || "Analisis gagal";
      finishAnalysisJob();
      toast(`Analisis gagal: ${message}`, true);
      paintJobError(job, message);
      return;
    }
    paintAnalysisProgress(job);
  } catch (error) {
    if (error.message.includes("tidak ditemukan") || error.message.includes("404")) {
      finishAnalysisJob();
      paintJobError(job, "Pekerjaan analisis tidak ditemukan. Server mungkin sempat dimulai ulang; silakan tekan Analisis untuk menjalankan kembali.");
      return;
    }
    if (error.message.includes("500")) {
      finishAnalysisJob();
      toast(`Analisis gagal: ${error.message}`, true);
      paintJobError(job, error.message);
      return;
    }
    // A transient poll/network failure must not discard the in-flight job.
  }
  state.analysisTimer = setTimeout(() => pollAnalysisJob(job), 1200);
}

async function resumeAnalysisJob(saved = prefs.analysisJob) {
  if (!saved?.id) return false;
  state.analysisJob = saved;
  paintAnalysisProgress(saved);
  await pollAnalysisJob(saved);
  return true;
}

async function runAnalysis() {
  if (state.analysisJob) {
    if (!analysisBelongsTo(state.analysisJob, state.symbol)) {
      toast(`Analisis ${state.analysisJob.symbol} masih berjalan. Tunggu sampai selesai.`, true);
    }
    return;
  }
  const button = $("#analyse");
  if (button) { button.disabled = true; button.textContent = "Menyiapkan…"; }
  const job = { symbol: state.symbol, stage: "queue", status: "running", started_at: Date.now() / 1000 };
  state.analysisJob = job;
  paintAnalysisProgress(job);
  try {
    let started;
    try {
      started = await api("/api/analyze/start", {
        method: "POST",
        body: JSON.stringify({ symbol: state.symbol, holding_days: 5, range: state.range, ai: prefs.ai }),
      });
    } catch (error) {
      if (!/unknown endpoint|404/i.test(error.message)) throw error;
      // Older running server has only the original synchronous endpoint.
      // Keep the visible progress UI and fall back instead of surfacing 404.
      Object.assign(job, { stage: "legacy", message: "Server belum diperbarui; analisis tetap berjalan, tetapi status tidak dapat dipulihkan setelah refresh." });
      state.analysisJob = job;
      paintAnalysisProgress(job);
      state.analysisTimer = setInterval(() => paintAnalysisProgress(job), 1000);
      const payload = await api("/api/analyze", {
        method: "POST",
        body: JSON.stringify({ symbol: state.symbol, holding_days: 5, range: state.range, ai: prefs.ai }),
      });
      finishAnalysisJob();
      state.analysis = payload;
      paintVerdict(payload);
      paintPanel(state.tab);
      if (payload.recorded) toast(`Keputusan tersimpan di jurnal (#${payload.recorded}).`);
      return;
    }
    Object.assign(job, started);
    state.analysisJob = job;
    prefs.analysisJob = job;
    await pollAnalysisJob(job);
  } catch (error) {
    finishAnalysisJob();
    toast(`Analisis gagal dimulai: ${error.message}`, true);
    paintJobError(job, error.message);
  }
}

function viewIdle() {
  const costs = prefs.ai && state.health?.llm?.configured;
  return `<div class="empty">
    <span class="empty-mark">◎</span>
    <p><strong>${esc(state.symbol)}</strong> siap dianalisis.</p>
    <p class="muted">Harga dan indikator sudah dimuat — ini tidak memakai biaya.
    Tekan <strong>Analisis</strong> untuk menjalankan lima peran${costs ? " dan satu panggilan model" : ""}.</p>
  </div>`;
}

function paintIdleVerdict() {
  $("#verdict").innerHTML = viewIdle();
}

function decisionBucket(action) {
  return action === "Beli" ? "buy" : action === "Hindari" ? "avoid" : action === "Tunggu" ? "wait" : "data";
}

function renderDecisionSummary(data) {
  const guide = data.guidance;
  if (!guide) return `<section class="decision-summary decision-data"><div><h3>Ringkasan keputusan belum tersedia</h3><p class="muted">Mulai ulang server KATALIS agar API memuat kebijakan ringkasan terbaru.</p></div></section>`;
  const quality = guide.data_quality || {};
  const technical = guide.technical;
  const risk = guide.risk || {};
  const list = (items, empty) => items?.length
    ? `<ul>${items.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>`
    : `<p class="muted">${esc(empty)}</p>`;
  const missing = quality.missing_roles || [];
  const proxies = quality.proxy_roles || [];
  const flags = risk.flags || [];
  return `<section class="decision-summary decision-${decisionBucket(guide.action)}" aria-labelledby="decision-title">
    <div class="decision-head">
      <div><p class="decision-label">Ringkasan keputusan</p><h3 id="decision-title">${esc(guide.action || "Data belum cukup")}</h3></div>
      <span class="decision-quality">Data ${esc(quality.status || "terbatas")} · ${rp(quality.available_roles || 0)}/${rp(quality.total_roles || 0)} peran · ${rp(quality.sessions || 0)} sesi</span>
    </div>
    <p class="decision-why">${esc(guide.why || "Alasan belum tersedia.")}</p>
    <div class="decision-columns">
      <div><h4>Yang mendukung</h4>${list(risk.supporting, "Belum ada bukti pendukung yang tersedia.")}</div>
      <div><h4>Risiko yang perlu dipahami</h4>${list([...(risk.opposing || []), ...(risk.ai_risks || [])].slice(0, 4), "Tidak ada butir risiko tambahan yang tersedia; ini bukan berarti bebas risiko.")}</div>
    </div>
    <div class="decision-comparison" aria-label="Perbandingan analisis">
      <div><span>Teknikal</span><strong>${technical?.available ? `${esc(technical.label)} · tren ${esc(technical.trend)} · sinyal ${esc(technical.strength)}` : "Data tidak tersedia"}</strong><small>Skor satu peran, bukan keputusan gabungan.</small></div>
      <div><span>Gabungan mesin</span><strong>${esc(guide.machine_rating || "—")} · ${esc(guide.machine_action || "—")}</strong><small>Rating dari semua peran yang memiliki data.</small></div>
      <div><span>AI</span><strong>${guide.ai_rating ? `${esc(guide.ai_rating)} · ${esc(guide.ai_action || "—")}` : (guide.ai_error ? "Tidak tersedia" : "Belum dijalankan")}</strong><small>Model membaca ringkasan bukti; bukan sumber level harga.</small></div>
    </div>
    <p class="decision-next"><strong>Langkah berikutnya:</strong> ${esc(guide.next_action || "Tinjau data dan risiko sebelum mengambil keputusan.")}</p>
    <details class="decision-detail">
      <summary>Kualitas data, keyakinan, dan arti angka</summary>
      <p>${esc(guide.policy || "")}</p>
      <p>${esc(guide.machine_confidence_note || "")}${guide.machine_confidence != null ? ` Skor heuristik mesin: ${Math.round(guide.machine_confidence * 100)}%.` : ""}</p>
      ${guide.ai_rating ? `<p>${esc(guide.ai_confidence_note || "")}${guide.ai_conviction != null ? ` Keyakinan model: ${Math.round(guide.ai_conviction * 100)}%.` : ""}</p>` : ""}
      ${quality.as_of ? `<p><strong>Tanggal snapshot:</strong> ${esc(quality.as_of)}${quality.staleness_days != null ? ` · ${rp(quality.staleness_days)} hari lalu` : ""}.</p>` : `<p><strong>Tanggal snapshot tidak diketahui.</strong> Ringkasan belum memberi label Beli.</p>`}
       ${missing.length ? `<p><strong>Tanpa data:</strong> ${missing.map(esc).join(", ")}. Ini tidak dihitung netral.</p>` : ""}
      ${proxies.length ? `<p><strong>Memakai proksi harga/volume:</strong> ${proxies.map(esc).join(", ")}.</p>` : ""}
      ${quality.warnings?.length ? `<ul>${quality.warnings.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>` : ""}
      ${flags.length ? `<p><strong>Batas risiko:</strong> ${flags.map(esc).join("; ")}.</p>` : ""}
      <p>${esc(risk.explanation || "R:R membandingkan jarak target dan stop; bukan peluang keuntungan.")}</p>
      ${technical ? `<div class="decision-columns"><div><h4>Bukti teknikal mendukung</h4>${list(technical.supporting, "Tidak ada butir pendukung teknikal.")}</div><div><h4>Bukti teknikal menentang</h4>${list(technical.opposing, "Tidak ada butir penentang teknikal.")}</div></div>` : ""}
    </details>
    <p class="decision-disclaimer">Ringkasan berbasis aturan yang dijelaskan; bukan probabilitas profit, jaminan hasil, atau nasihat investasi.</p>
  </section>`;
}

function renderSimulator(data) {
  const plan = data.trader || {};
  if (data.guidance?.action !== "Beli") {
    return `<details class="simulator"><summary>Simulasi potensi untung/rugi</summary><p class="muted">Simulator ditampilkan saat ringkasan menyatakan Beli. Saat ini: ${esc(data.guidance?.action || "Data belum cukup")}. ${esc(data.guidance?.next_action || "Tinjau data lebih dulu.")}</p></details>`;
  }
  if (!data.analysis_id || [plan.entry, plan.stop, plan.target].some((value) => value == null)) {
    return `<details class="simulator"><summary>Simulasi potensi untung/rugi</summary><p class="muted">Level mesin atau id analisis belum tersedia. Jalankan analisis terbaru.</p></details>`;
  }
  if (Number(plan.target) <= Number(plan.entry)) {
    return `<details class="simulator"><summary>Simulasi potensi untung/rugi</summary><p class="muted">Rencana mesin mengarah turun. Simulator jual kosong dinonaktifkan karena aplikasi tidak memverifikasi apakah akun/broker Anda dapat melakukan short selling.</p></details>`;
  }
  const capital = state.portfolio?.equity > 0 ? Math.floor(state.portfolio.equity) : "";
  const lots = Number.isInteger(plan.lots) && plan.lots > 0 ? plan.lots : 1;
  return `<details class="simulator">
    <summary>Simulasi potensi untung/rugi</summary>
    <p class="muted">Entry, stop, dan target dikunci dari rencana mesin. Pilih modal dan lot untuk melihat skenario sampai stop atau target.</p>
    <div class="sim-levels"><span>Entry mesin <b>Rp${rp(plan.entry)}</b></span><span>Stop mesin <b>Rp${rp(plan.stop)}</b></span><span>Target mesin <b>Rp${rp(plan.target)}</b></span></div>
    <form id="sim-form" data-symbol="${esc(data.symbol)}" data-analysis-id="${esc(data.analysis_id)}">
      <label>Modal (Rp)<input id="sim-capital" type="number" min="1" step="1000" value="${esc(capital)}" required></label>
      <label>Jumlah lot (1 lot = 100 saham)<input id="sim-lots" type="number" min="1" step="1" value="${lots}" required></label>
      <label>Biaya per sisi (%) <span class="muted">opsional, isi tarif broker</span><input id="sim-fee" type="number" min="0" max="100" step="0.01" placeholder="belum dimasukkan"></label>
      <button class="primary" type="submit">Hitung skenario</button>
    </form>
    <div id="sim-result" class="sim-result" aria-live="polite"></div>
    <p class="sim-disclaimer">Estimasi skenario, bukan prediksi. Biaya, pajak, slippage, likuiditas, dan harga eksekusi dapat berbeda. Ketersediaan jual kosong/short tidak diperiksa.</p>
  </details>`;
}

function renderSimulationResult(payload) {
  const result = payload.result || {};
  const pnl = (value) => value == null ? "—" : `Rp${rp(value)}`;
  const target = result.fees_included ? result.net_target_pnl : result.gross_target_pnl;
  const stop = result.fees_included ? result.net_stop_pnl : result.gross_stop_pnl;
  const rewardRisk = result.fees_included ? result.net_reward_risk : result.gross_reward_risk;
  return `<div class="sim-result-grid">
    <div><span>Nilai pembelian</span><strong>Rp${rp(result.entry_notional)}</strong><small>${rp(result.capital_share_pct)}% dari modal</small></div>
    <div><span>Jika mencapai target</span><strong class="up">${pnl(target)}</strong><small>${num(result.target_pnl_on_capital_pct, 2)}% dari modal · ${result.fees_included ? "setelah biaya input" : "kotor, biaya belum dihitung"}</small></div>
    <div><span>Jika mencapai stop</span><strong class="down">${pnl(stop)}</strong><small>${num(result.stop_pnl_on_capital_pct, 2)}% dari modal · ${result.fees_included ? "setelah biaya input" : "kotor, biaya belum dihitung"}</small></div>
    <div><span>Rasio skenario untung:rugi</span><strong>${rewardRisk == null ? "—" : `${num(rewardRisk, 2)}:1`}</strong><small>bukan probabilitas berhasil</small></div>
  </div>
  ${result.capital_covers_entry === false ? `<p class="sim-warning">Nilai pembelian melebihi modal yang dimasukkan.</p>` : ""}
  ${result.direction === "short" ? `<p class="sim-warning">Ini skenario arah turun saja. Ketersediaan dan aturan short selling tidak diperiksa; jangan anggap bisa dieksekusi.</p>` : ""}
  ${result.fees_included ? `<p class="muted">Biaya pulang-pergi terhitung: target Rp${rp(result.target_round_trip_fees)}, stop Rp${rp(result.stop_round_trip_fees)}. Pajak/slippage belum termasuk.</p>` : `<p class="muted">Tanpa tarif broker, angka di atas adalah estimasi kotor, bukan keuntungan bersih.</p>`}`;
}

function wireDecisionWidgets(data) {
  const form = $("#sim-form");
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $("button[type=submit]", form);
    const output = $("#sim-result");
    button.disabled = true;
    button.textContent = "Menghitung…";
    output.textContent = "";
    try {
      const payload = await api("/api/simulate", {
        method: "POST",
        body: JSON.stringify({
          symbol: form.dataset.symbol,
          analysis_id: form.dataset.analysisId,
          lots: Number($("#sim-lots").value),
          capital: Number($("#sim-capital").value),
          fee_rate_pct: $("#sim-fee").value === "" ? null : Number($("#sim-fee").value),
        }),
      });
      output.innerHTML = renderSimulationResult(payload);
    } catch (error) {
      output.innerHTML = `<p class="sim-warning">${esc(error.message)}</p>`;
    } finally {
      button.disabled = false;
      button.textContent = "Hitung skenario";
    }
  });
}

function paintVerdict(data) {  const decision = data.decision || {};
  const plan = data.trader || {};
  const budget = plan.value ? `Rp${rp(plan.value)}` : "—";
  const lots = plan.lots == null ? "—" : `${rp(plan.lots)} lot`;
  const sources = [
    ["yahoo", "Yahoo Finance"],
    data.sources?.idx?.available ? ["idx", "idx CLI"] : ["proxy", "proksi harga"],
  ];

  $("#verdict").innerHTML = `
    <div class="rating-wrap">
      <span class="rating-pill rating-${esc(decision.rating)}">${esc(decision.rating || "—")}</span>
      <div class="rating-price">${rp(data.price)}</div>
      <div class="rating-name">${esc(data.name || "")} · ${esc(data.symbol)} · per ${esc(data.as_of)}</div>
    </div>
    <div class="confidence">
      <div class="row"><span>Skor keyakinan mesin · bukan peluang profit</span><span>${Math.round((decision.confidence || 0) * 100)}%</span></div>
      <div class="bar"><i class="fill" style="width:${clamp01(decision.confidence || 0) * 100}%"></i></div>
    </div>
    <div class="plan">
      <div><div class="k">Entry</div><div class="v">${rp(plan.entry)}</div></div>
      <div><div class="k">Stop</div><div class="v down">${rp(plan.stop)}</div></div>
      <div><div class="k">Target</div><div class="v up">${rp(plan.target)}</div></div>
      <div><div class="k">R : R</div><div class="v">${plan.reward_risk ?? "—"}</div></div>
      <div><div class="k">Ukuran</div><div class="v">${lots}</div></div>
      <div><div class="k">Nilai</div><div class="v">${budget}</div></div>
      ${plan.lots == null ? `<div style="grid-column:1/-1"><div class="k">Catatan</div>
        <div style="font-size:12px;color:#c3d2df;margin-top:2px">${esc(plan.note || "")}</div></div>` : ""}
    </div>
    <div class="verdict-note">
      ${esc(decision.summary || "")}
      <div class="src">
        ${sources.map(([kind, label]) => `<span class="tag ${kind}">${esc(label)}</span>`).join("")}
        <span class="tag">${data.recorded ? `jurnal #${data.recorded}` : "sudah tercatat hari ini"}</span>
      </div>
      <div class="next-step"><button class="linklike" data-open-tab="roles">Buka Dewan Analisis →</button></div>
    </div>
    ${renderDecisionSummary(data)}
    ${renderSimulator(data)}
    ${aiStrip(data)}`;
  wireDecisionWidgets(data);
}

function aiStrip(data) {  const ai = data.ai;
  if (!ai) {
    return data.ai_error
      ? `<div class="ai-strip"><span class="tag">AI gagal</span>
         <span class="muted">${esc(data.ai_error)}</span></div>`
      : `<div class="ai-strip"><span class="tag">AI nonaktif</span>
         <span class="muted">Isi OPENAI_API_KEY untuk lapisan penasihat.</span></div>`;
  }
  const relation = ai.agrees_with_desk
    ? "sejalan dengan mesin"
    : `berbeda dari mesin (${esc(ai.mechanical_rating)})`;
  return `<div class="ai-strip">
    <span class="rating-pill rating-${esc(ai.rating)}" style="font-size:10px;padding:3px 9px">
      AI: ${esc(ai.rating)}</span>
    <span class="muted">${relation} · keyakinan model ${Math.round((ai.conviction || 0) * 100)}% · bukan peluang profit</span>
  </div>`;
}

/* ---------- panels ---------- */

function paintPanel(tab) {
  const panel = $("#panel");
  if (tab === "roles") return void (panel.innerHTML = viewRoles());
  if (tab === "advice") {
    panel.innerHTML = viewAdvice();
    const reload = $("#reload-ai");
    if (reload) {
      reload.addEventListener("click", async () => {
        reload.disabled = true;
        reload.textContent = "Memeriksa…";
        const health = await refreshHealth();
        paintPanel("advice");
        toast(health?.llm?.configured
          ? `AI aktif: ${health.llm.model}`
          : `AI masih mati${health?.llm?.warnings?.[0] ? " — " + health.llm.warnings[0] : ""}`,
          !health?.llm?.configured);
      });
    }
    return;
  }
  if (tab === "debate") return void (panel.innerHTML = viewDebate());
  if (tab === "risk") return void (panel.innerHTML = viewRisk());
  if (tab === "screener") {
    panel.innerHTML = viewScreener();
    wireScreener();
    return;
  }
  if (tab === "journal") return void loadJournal();
  if (tab === "portfolio") return void loadPortfolio(true);
}

function needsAnalysis() {
  return '<p class="muted pad">Jalankan <strong>Analisis</strong> dulu untuk melihat bagian ini.</p>';
}

function viewRoles() {
  const data = state.analysis;
  if (!data) return needsAnalysis();
  const cards = (data.roles || []).map((role) => {
    const width = Math.abs(role.score) / 2 * 50;
    const left = role.score >= 0 ? 50 : 50 - width;
    const colour = role.score > 0 ? "#22c55e" : role.score < 0 ? "#ef4444" : "#94a3b8";
    const tagClass = role.source === "idx" ? "idx" : role.source === "proxy" ? "proxy" : "";
    const tag = role.available
      ? `<span class="tag ${tagClass}">${role.source === "idx" ? "data idx" : role.source === "proxy" ? "proksi" : "data"}</span>`
      : '<span class="tag">tanpa data</span>';
    const list = (items) => items.length
      ? `<ul>${items.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>` : "";
    return `<article class="role ${role.available ? "" : "off"}">
      <div class="role-head">
        <h3>${esc(role.name)}</h3>
        <div>${tag} <span class="scoreval" style="color:${colour}">${role.score > 0 ? "+" : ""}${num(role.score, 2)}</span></div>
      </div>
      <p class="headline">${esc(role.headline)}</p>
      ${role.warning ? `<p class="muted" style="font-size:11.5px;margin:-4px 0 8px">${esc(role.warning)}</p>` : ""}
      <div class="scorebar"><i style="left:${left}%;width:${width}%;background:${colour}"></i></div>
      ${list(role.bull.filter(() => role.stance !== "neutral"))}
      ${list(role.bear.filter(() => role.stance !== "neutral"))}
    </article>`;
  }).join("");
  return `<div class="role-grid">${cards}</div>`;
}

function viewDebate() {
  const data = state.analysis;
  if (!data) return needsAnalysis();
  const bull = (data.debate?.bull || []).map((item) => `<li>${esc(item)}</li>`).join("");
  const bear = (data.debate?.bear || []).map((item) => `<li>${esc(item)}</li>`).join("");
  return `<div class="two-col">
    <div class="debate-col bull"><h3>Kasus bullish</h3><ul>${bull}</ul></div>
    <div class="debate-col bear"><h3>Kasus bearish</h3><ul>${bear}</ul></div>
  </div>
  <p class="muted pad">${esc(data.manager?.verdict || "")}</p>`;
}

function viewRisk() {
  const data = state.analysis;
  if (!data) return needsAnalysis();
  const cards = (data.risk || []).map((item) => `
    <article class="risk"><h3>${esc(item.name)}</h3>
      <div class="stance">${esc(item.stance)}</div>
      <p>${esc(item.note)}</p></article>`).join("");
  return `<div class="risk-grid">${cards}</div>`;
}

function viewScreener() {
  const screen = state.screener;
  const result = screen.result;
  const candidates = result?.candidates || [];
  const rejected = result?.rejected || [];
  const errors = result?.errors || [];
  const rows = candidates.map((row) => {
    const plan = row.plan || {};
    const ai = row.ai;
    return `<tr class="${row.pick ? "screen-picked" : ""}">
      <td><strong>${row.pick ? "★ " : ""}${esc(row.symbol.replace(/\.JK$/, ""))}</strong>
        <span class="muted screen-name">${esc(row.name || "")}</span></td>
      <td><span class="rating-pill rating-${esc(row.mechanical?.rating)}">${esc(row.mechanical?.rating || "—")}</span></td>
      <td class="num">${num(row.mechanical?.composite, 2)}</td>
      <td>${ai ? `<span class="rating-pill rating-${esc(ai.rating)}">${esc(ai.rating)}</span>` : '<span class="muted">—</span>'}</td>
      <td class="num">${rp(plan.entry)}</td><td class="num down">${rp(plan.stop)}</td><td class="num up">${rp(plan.target)}</td>
      <td class="screen-flags">${(row.flags || []).map((flag) => `<span class="tag">${esc(flag)}</span>`).join(" ")}</td>
      <td><button class="ghost row-go" data-symbol="${esc(row.symbol)}">Analisis</button></td>
    </tr>`;
  }).join("");
  const outcome = !result ? "" : `<section class="screen-results" aria-live="polite">
    <div class="screen-summary">
      <div class="stat"><div class="k">Universe</div><div class="v">${rp(result.universe)}</div></div>
      <div class="stat"><div class="k">Pilihan</div><div class="v">${rp(result.picks?.length || 0)}</div></div>
      <div class="stat"><div class="k">Ditolak</div><div class="v">${rp(rejected.length)}</div></div>
      <div class="stat"><div class="k">Gagal data</div><div class="v">${rp(errors.length)}</div></div>
      <div class="stat"><div class="k">Waktu</div><div class="v">${num(result.elapsed_s, 1)} dtk</div></div>
      <div class="stat"><div class="k">Sumber pilihan</div><div class="v">${esc(result.picks_source || "—")}</div></div>
    </div>
    ${result.market_note ? `<p class="screen-market"><strong>Catatan pasar:</strong> ${esc(result.market_note)}</p>` : ""}
    ${result.ai?.error ? `<p class="screen-error">AI gagal: ${esc(result.ai.error)} — pilihan kembali ke urutan mesin.</p>` : ""}
    ${result.picks?.length ? `<p class="screen-pickline">Pilihan: <strong>${result.picks.map((symbol) => esc(symbol)).join(", ")}</strong></p>`
      : `<p class="screen-pickline muted">Tidak ada yang layak dipilih pada run ini. Nol pilihan adalah hasil yang sah.</p>`}
    <div class="screen-table-wrap"><table class="screen-table"><thead><tr>
      <th>Saham</th><th>Rating mesin</th><th class="num">Komposit</th><th>Rating AI</th>
      <th class="num">Entry</th><th class="num">Stop</th><th class="num">Target</th><th>Catatan data</th><th>Aksi</th>
    </tr></thead><tbody>${rows || '<tr><td colspan="9" class="muted">Tidak ada kandidat lolos ke dewan.</td></tr>'}</tbody></table></div>
    ${rejected.length ? `<details class="screen-details"><summary>Ditolak (${rejected.length})</summary><ul>${rejected.map((item) =>
      `<li><strong>${esc(item.symbol)}</strong> <span class="tag">${esc(item.stage)}</span> — ${esc((item.reasons || []).join("; "))}</li>`).join("")}</ul></details>` : ""}
    ${errors.length ? `<details class="screen-details" open><summary>Gagal mengambil data (${errors.length})</summary><ul>${errors.map((item) =>
      `<li><strong>${esc(item.symbol)}</strong> — ${esc(item.error)}</li>`).join("")}</ul></details>` : ""}
    ${result.recorded?.length ? `<p class="muted screen-recorded">Jurnal: ${result.recorded.map((item) =>
      `${esc(item.symbol)} ${item.id ? `tercatat #${rp(item.id)}` : "sudah tercatat hari ini"}`).join(" · ")}</p>` : ""}
    ${result.disclaimer ? `<p class="muted screen-disclaimer">${esc(result.disclaimer)}</p>` : ""}
  </section>`;

  return `<div class="screen-panel">
    <div class="screen-head"><div><h2>Pemindai saham</h2>
      <p class="muted">Saring universe bertahap: gerbang harga → dewan analis → model opsional. Tidak ada pilihan juga jawaban yang sah.</p></div>
      <span class="tag">${esc(screen.busy ? "memindai…" : result ? `tanggal ${result.as_of}` : "siap")}</span></div>
    <form id="screen-form" class="screen-controls">
      <label class="screen-universe">Kode saham <span class="muted">(opsional; kosong = universe.txt / watchlist)</span>
        <input id="screen-symbols" value="${esc(screen.symbols)}" placeholder="BBCA, BBRI, TLKM" autocomplete="off" spellcheck="false"></label>
      <label>Max pilihan <input id="screen-top" type="number" min="1" max="10" value="${esc(screen.maxPicks)}"></label>
      <details class="screen-adv"><summary>Opsi lanjutan</summary>
        <label>Shortlist <input id="screen-shortlist" type="number" min="1" max="40" value="${esc(screen.shortlist)}"></label>
      </details>
      <label class="toggle"><input id="screen-ai" type="checkbox" ${screen.ai ? "checked" : ""}> pakai AI (1 panggilan)</label>
      <label class="toggle"><input id="screen-record" type="checkbox" ${screen.record ? "checked" : ""}> catat pilihan ke jurnal</label>
      <button id="screen-run" class="primary" type="submit" ${screen.busy ? "disabled" : ""}>${screen.busy ? "Memindai…" : "Mulai pemindaian"}</button>
    </form>
    <p class="muted screen-caveat">Pemindai tidak memprediksi hasil. Periksa UMA/suspensi secara terpisah; data harga dapat tertunda. AI mati secara default agar tidak ada panggilan berbiaya tanpa pilihan Anda.</p>
    ${outcome}
  </div>`;
}

function wireScreener() {
  const form = $("#screen-form");
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const screen = state.screener;
    screen.symbols = $("#screen-symbols").value.trim();
    screen.maxPicks = Math.max(1, Math.min(10, Number($("#screen-top").value) || 5));
    screen.shortlist = Math.max(1, Math.min(40, Number($("#screen-shortlist").value) || 15));
    screen.ai = $("#screen-ai").checked;
    screen.record = $("#screen-record").checked;
    screen.busy = true;
    paintPanel("screener");
    try {
      const symbols = screen.symbols ? screen.symbols.split(/[\s,;]+/).filter(Boolean) : [];
      screen.result = await api("/api/screen", {
        method: "POST",
        body: JSON.stringify({ symbols, max_picks: screen.maxPicks, shortlist: screen.shortlist,
          ai: screen.ai, record: screen.record, holding_days: 5 }),
      });
      toast(screen.result.picks.length
        ? `Pemindaian selesai: ${screen.result.picks.length} pilihan (${screen.result.picks_source}).`
        : "Pemindaian selesai: tidak ada pilihan yang lolos.");
    } catch (error) {
      screen.result = null;
      toast(`Pemindaian gagal: ${error.message}`, true);
    } finally {
      screen.busy = false;
      paintPanel("screener");
    }
  });
}

function paragraphs(text) {
  return String(text || "")
    .split(/\n\s*\n/)
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => `<p>${esc(part)}</p>`)
    .join("");
}

function bulletList(items, empty) {
  if (!items || !items.length) return `<p class="muted">${esc(empty)}</p>`;
  return `<ul>${items.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>`;
}

function viewAdvice() {
  const data = state.analysis;
  if (!data) return needsAnalysis();
  const ai = data.ai;
  const guidance = data.guidance;

  if (!ai) {
    const hint = data.ai_error
      ? `<p class="muted">Panggilan AI gagal: <strong>${esc(data.ai_error)}</strong></p>`
      : `<p class="muted">Isi <code>OPENAI_API_KEY</code> di <code>katalis/.env</code>,
         lalu tekan <em>Periksa ulang .env</em> — berkas itu dibaca ulang tanpa restart.</p>`;
    return `<div class="advice-empty">
      <h3>Lapisan penasihat AI belum aktif</h3>
      ${hint}
      ${diagnosticsBlock()}
      <p class="muted" style="margin-top:10px">Angka, indikator, dan rating mesin tetap
      dihitung tanpa AI — lapisan ini hanya menambah tesis dan risiko, tidak menggantikannya.</p>
    </div>`;
  }

  const relation = ai.agrees_with_desk
    ? `<span class="tag">sejalan dengan rating gabungan</span>`
    : `<span class="tag">beda dari rating gabungan: ${esc(ai.mechanical_rating)}</span>`;
  const action = ai.action || {};
  const machinePlan = data.trader || {};
  const level = (label, value) => `<div><div class="k">${label}</div>
    <div class="v">${value == null ? "—" : rp(value)}</div></div>`;

  return `
    <div class="advice-head">
      <span class="rating-pill rating-${esc(ai.rating)}">${esc(ai.rating)}</span>
      <span class="tag">${esc(ai.model || "AI")}</span>
      <span class="tag">keyakinan model ${Math.round((ai.conviction || 0) * 100)}% · bukan peluang profit</span>
      ${ai.usage?.total_tokens ? `<span class="tag">${rp(ai.usage.total_tokens)} token</span>` : ""}
      ${relation}
    </div>
    <p class="muted advice-disagreement">${esc(guidance?.why || "Ringkasan gabungan tersedia di panel utama.")} Keputusan akhir tetap transparan: jika sinyal material berbeda, ringkasan memilih Tunggu; angka keyakinan bukan probabilitas profit.</p>
    <div class="two-col advice-grid">
      <article class="advice-block">
        <h3>Tesis</h3>
        ${paragraphs(ai.thesis) || '<p class="muted">Model tidak menulis tesis.</p>'}
        ${ai.confidence_note ? `<p class="muted" style="margin-top:10px">${esc(ai.confidence_note)}</p>` : ""}
      </article>
      <div class="advice-side">
        <article class="advice-block">
          <h3>Risiko</h3>
          ${bulletList(ai.risks, "Model tidak menyebut risiko.")}
        </article>
        <article class="advice-block">
          <h3>Yang membatalkan tesis</h3>
          ${bulletList(ai.what_would_change, "Tidak disebutkan.")}
        </article>
        <article class="advice-block">
          <h3>Level dari mesin</h3>
          <p class="muted">Entry, stop, dan target di bawah ini ditetapkan aturan deterministik KATALIS, bukan keluaran AI.</p>
          <div class="plan" style="margin-top:0">
            ${level("Entry", machinePlan.entry)}
            ${level("Stop", machinePlan.stop)}
            ${level("Target", machinePlan.target)}
          </div>
          <p class="muted">Entry: patokan harga rencana. Jika harga menyentuh Stop, risiko per saham sekitar Rp${rp(machinePlan.risk_per_share)} sebelum biaya/slippage. Jika mencapai Target, keuntungan kotor per saham sekitar Rp${rp(Math.abs((machinePlan.target || 0) - (machinePlan.entry || 0)))}. Harga eksekusi dapat berbeda.</p>
          <p class="muted" style="margin-top:8px">AI dapat memberi rating dan alasan yang berbeda, tetapi tidak mengubah level harga mesin.</p>
        </article>
      </div>
    </div>
    <p class="advice-foot">${esc(ai.disclaimer || "")} · dibuat ${esc(ai.generated_at || "")}</p>`;
}

/* ---------- journal ---------- */

function statCards(scores) {
  return Object.entries(scores || {}).map(([rating, value]) => `
    <div class="stat"><div class="k">${esc(rating)}</div>
      <div class="v">${value.count} <span class="muted" style="font-size:12px">· ${value.hit_rate == null ? "tanpa arah" : `${Math.round(value.hit_rate * 100)}% tepat`}</span></div>
      <div class="muted" style="font-size:12px">rata-rata alpha ${pct(value.mean_alpha * 100)}</div></div>`).join("");
}

async function loadJournal() {
  const panel = $("#panel");
  panel.innerHTML = '<p class="muted pad">Memuat jurnal…</p>';
  try {
    const payload = await api("/api/journal");
    state.journal = payload;
    const stats = payload.stats || {};
    const pill = (rating) => `<span class="rating-pill rating-${esc(rating)}"
        style="font-size:10px;padding:3px 9px">${esc(rating)}</span>`;
    const rows = (payload.entries || []).map((entry) => {
      const summary = entry.guidance?.why || entry.decision_summary || entry.summary?.summary || "Alasan lama tidak tersimpan.";
      const savedAction = entry.guidance?.action || "";
      const provenance = entry.trader ? `Level mesin saat dicatat: Entry ${rp(entry.trader.entry)}, stop ${rp(entry.trader.stop)}, target ${rp(entry.trader.target)}.` : "Level mesin tidak tersedia pada rekaman lama.";
      const aiNote = entry.ai_summary?.thesis || "";
      const aiRisk = entry.ai_summary?.risks || [];
      return `<tr>
        <td class="num">${esc(entry.trade_date)}</td>
        <td>${esc(entry.symbol.replace(".JK", ""))}</td>
        <td>${pill(entry.rating)}</td>
        <td>${entry.ai_rating ? pill(entry.ai_rating) : '<span class="muted">—</span>'}</td>
        <td class="num">${rp(entry.price)}</td>
        <td class="num">${entry.exit_price == null ? "—" : rp(entry.exit_price)}</td>
        <td class="num ${tone(entry.raw_return)}">${entry.raw_return == null ? "—" : pct(entry.raw_return * 100)}</td>
        <td class="num ${tone(entry.benchmark_return)}">${entry.benchmark_return == null ? "—" : pct(entry.benchmark_return * 100)}</td>
        <td class="num ${tone(entry.alpha)}">${entry.alpha == null ? "—" : pct(entry.alpha * 100)}</td>
        <td>${entry.settled ? "dinilai" : "menunggu"}</td>
        <td><details class="journal-detail"><summary>Lihat alasan</summary><p><strong>${esc(savedAction)}</strong> ${esc(summary)}</p><p>${esc(provenance)}</p><p>Rating mesin ${esc(entry.rating)}${entry.ai_rating ? ` · rating AI ${esc(entry.ai_rating)}` : " · tanpa rating AI"}; hasil sesudah ${rp(entry.holding_days)} sesi dibandingkan dengan IHSG.</p>${aiNote ? `<p><strong>Catatan AI saat itu:</strong> ${esc(aiNote)}</p>` : ""}${aiRisk.length ? `<p><strong>Risiko AI:</strong> ${aiRisk.map(esc).join("; ")}</p>` : ""}</details></td>
      </tr>`;
    }).join("");

    panel.innerHTML = `
      <div class="toolbar">
        <button class="primary" id="settle">Nilai keputusan yang jatuh tempo</button>
        <span class="muted">${stats.settled || 0} dinilai · ${stats.pending || 0} menunggu
          ${stats.ai_settled ? `· ${stats.ai_settled} punya rating AI` : ""}</span>
      </div>
      ${Object.keys(stats.by_rating || {}).length ? `<div class="statgrid">${statCards(stats.by_rating)}</div>` : ""}
      ${stats.ai_settled ? `<div class="group-label">Rekam jejak AI</div>
        <div class="statgrid">${statCards(stats.by_rating_ai)}</div>` : ""}
      <table>
        <thead><tr><th>Tanggal</th><th>Kode</th><th>Mesin</th><th>AI</th><th class="num">Harga</th>
        <th class="num">Keluar</th><th class="num">Return</th><th class="num">IHSG</th>
        <th class="num">Alpha</th><th>Status</th><th>Alasan saat keputusan</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="11" class="muted">Belum ada keputusan. Jalankan analisis dulu.</td></tr>'}</tbody>
      </table>`;

    $("#settle").addEventListener("click", settleJournal);
  } catch (error) {
    panel.innerHTML = `<p class="muted pad">Gagal memuat jurnal: ${esc(error.message)}</p>`;
  }
}

async function settleJournal() {
  const button = $("#settle");
  if (button) { button.disabled = true; button.textContent = "Menilai…"; }
  try {
    const result = await api("/api/settle", { method: "POST", body: "{}" });
    const done = result.settled?.length || 0;
    const waiting = result.waiting?.length || 0;
    toast(done ? `${done} keputusan dinilai${waiting ? `, ${waiting} belum jatuh tempo` : ""}.`
      : `Belum ada yang bisa dinilai${waiting ? ` — ${waiting} menunggu jendela 5 sesi` : ""}.`);
    await loadJournal();
  } catch (error) {
    toast(`Gagal menilai: ${error.message}`, true);
  } finally {
    if (button) { button.disabled = false; button.textContent = "Nilai keputusan yang jatuh tempo"; }
  }
}

/* ---------- portfolio ---------- */

async function loadPortfolio(render = false) {
  try {
    const payload = await api("/api/portfolio");
    state.portfolio = payload;
    if (render) paintPortfolio(payload);
  } catch (error) {
    if (render) $("#panel").innerHTML = `<p class="muted pad">${esc(error.message)}</p>`;
  }
}

function paintPortfolio(payload) {
  const rows = (payload.positions || []).map((position) => `
    <tr>
      <td>${esc(position.symbol.replace(".JK", ""))}</td>
      <td class="num">${rp(position.quantity)}</td>
      <td class="num">${rp(position.avg_price)}</td>
      <td class="num">${rp(position.price)}</td>
      <td class="num">${rp(position.value)}</td>
      <td class="num ${tone(position.pnl)}">${rp(position.pnl)} (${pct(position.pnl_pct * 100)})</td>
    </tr>`).join("");

  $("#panel").innerHTML = `
    <div class="statgrid">
      <div class="stat"><div class="k">Ekuitas</div><div class="v">Rp${rp(payload.equity)}</div></div>
      <div class="stat"><div class="k">Nilai saham</div><div class="v">Rp${rp(payload.market_value)}</div></div>
      <div class="stat"><div class="k">Kas</div><div class="v">Rp${rp(payload.cash)}</div></div>
      <div class="stat"><div class="k">Laba/rugi</div>
        <div class="v ${tone(payload.pnl)}">Rp${rp(payload.pnl)}</div></div>
    </div>
    <div class="toolbar">
      <input id="p-symbol" placeholder="BBCA" aria-label="kode">
      <input id="p-qty" placeholder="lot (100)" aria-label="jumlah lot">
      <input id="p-avg" placeholder="harga rata-rata" aria-label="harga rata-rata">
      <button class="primary" id="p-add">Tambah / ubah</button>
      <input id="p-cash" placeholder="kas (Rp)" aria-label="kas" value="${payload.cash || ""}">
      <button class="ghost" id="p-set-cash">Simpan kas</button>
    </div>
    <table>
      <thead><tr><th>Kode</th><th class="num">Jumlah</th><th class="num">Rata-rata</th>
      <th class="num">Harga</th><th class="num">Nilai</th><th class="num">Laba/rugi</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="6" class="muted">Belum ada posisi.</td></tr>'}</tbody>
    </table>`;

  $("#p-add").addEventListener("click", async () => {
    const symbol = normaliseSymbol($("#p-symbol").value);
    const lots = Number($("#p-qty").value);
    const average = Number($("#p-avg").value);
    if (!symbol || !lots || !average) return toast("Isi kode, jumlah lot, dan harga rata-rata.", true);
    const existing = state.portfolio?.positions?.some((position) => position.symbol === symbol);
    if (existing && !confirm(`Posisi ${symbol} sudah ada. Timpa dengan ${lots} lot @ ${rp(average)}?`)) return;
    try {
      await api("/api/portfolio", {
        method: "POST",
        body: JSON.stringify({ symbol, quantity: lots * 100, avg_price: average }),
      });
      toast(`${symbol}: ${lots} lot dicatat.`);
      await loadPortfolio(true);
    } catch (error) { toast(error.message, true); }
  });

  $("#p-set-cash").addEventListener("click", async () => {
    try {
      await api("/api/cash", { method: "POST", body: JSON.stringify({ value: Number($("#p-cash").value) || 0 }) });
      toast("Kas disimpan.");
      await loadPortfolio(true);
    } catch (error) { toast(error.message, true); }
  });
}

/* ---------- events ---------- */

function activateTab(button) {
  $$("#tabs button").forEach((other) => {
    const on = other === button;
    other.classList.toggle("on", on);
    other.setAttribute("aria-selected", String(on));
    other.tabIndex = on ? 0 : -1;
  });
  state.tab = button.dataset.tab;
  paintPanel(state.tab);
}

function openTab(name) {
  const button = document.querySelector(`#tabs button[data-tab="${name}"]`);
  if (button) button.click();
}

function wireEvents() {
  wireWatchlist();
  document.addEventListener("click", (event) => {
    const opener = event.target.closest("[data-open-tab]");
    if (opener) openTab(opener.dataset.openTab);
    const go = event.target.closest(".row-go");
    if (go) {
      openSymbol(go.dataset.symbol);
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  });
  $("#analyse").addEventListener("click", () => openSymbol($("#symbol").value, { analyse: true }));
  $("#symbol").addEventListener("keydown", (event) => {
    if (event.key === "Enter") openSymbol($("#symbol").value, { analyse: true });
  });

  $("#opt-ai").addEventListener("change", (event) => {
    prefs.ai = event.target.checked;
    toast(prefs.ai
      ? "Analisis akan memakai AI: satu panggilan model per tekan."
      : "Analisis tanpa AI: hanya mesin deterministik, tanpa biaya.");
  });

  $("#opt-auto").addEventListener("change", (event) => {
    prefs.auto = event.target.checked;
    toast(prefs.auto
      ? "Analisis otomatis aktif: setiap halaman dibuka memakai satu panggilan model."
      : "Analisis otomatis dimatikan.");
  });

  $$("#ranges button").forEach((button) => button.addEventListener("click", async () => {
    $$("#ranges button").forEach((other) => other.classList.remove("on"));
    button.classList.add("on");
    state.range = button.dataset.range;
    await loadMarket(state.symbol, state.range);
  }));

  const chartRefresh = $("#chart-refresh");
  if (chartRefresh) {
    chartRefresh.addEventListener("click", async () => {
      chartRefresh.classList.add("spin");
      await loadMarket(state.symbol, state.range, { fresh: true });
      await loadBenchmark();
      setTimeout(() => chartRefresh.classList.remove("spin"), 400);
    });
  }

  $$("#tabs button").forEach((button) => {
    button.setAttribute("aria-selected", String(button.classList.contains("on")));
    button.tabIndex = button.classList.contains("on") ? 0 : -1;
    button.addEventListener("click", () => activateTab(button));
    button.addEventListener("keydown", (event) => {
      const dir = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
      if (!dir) return;
      event.preventDefault();
      const tabs = $$("#tabs button");
      const next = tabs[(tabs.indexOf(button) + dir + tabs.length) % tabs.length];
      next.focus();
      activateTab(next);
    });
  });

  $("#refresh").addEventListener("click", () => { loadWatchlist(); loadBenchmark(); });

  $("#add-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const symbol = normaliseSymbol($("#add-symbol").value);
    if (!symbol) return;
    if (!/^[A-Z]{1,6}(\.JK)?$/.test(symbol)) return toast(`"${symbol}" bukan kode saham yang valid.`, true);
    try {
      await api("/api/watchlist", { method: "POST", body: JSON.stringify({ add: [symbol] }) });
      $("#add-symbol").value = "";
      await loadWatchlist();
      toast(`${symbol} ditambahkan ke watchlist.`);
    } catch (error) { toast(error.message, true); }
  });
}

if (typeof window !== "undefined") {
  boot();
}

/* Exported for the headless render check (tools/check_ui.mjs). The dashboard
   itself never reads these. */
if (typeof module !== "undefined") {
  module.exports = { renderCandles, viewRoles, viewDebate, viewRisk, viewAdvice, viewIdle,
                     aiStrip, paintVerdict, renderDecisionSummary, renderSimulator,
                      renderSimulationResult, showCross, hideCross, axisLabel, fullLabel,
                     viewScreener, state, prefs, esc, rp, pct, analysisBelongsTo };
}
