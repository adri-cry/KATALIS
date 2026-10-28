// Offline checks for the browser side of KATALIS.
//
// No server, no network, no DOM: this exercises the pure functions the
// dashboard is built from — the chart renderer, the crosshair geometry, the
// number and date formatters, and the rating parser. CI runs it on every push;
// tools/check_ui.mjs covers the live end-to-end path instead.
//
//   node tools/test_pure.mjs
import { createRequire } from "node:module";

// app.js is CommonJS while this file is ESM (.mjs); bridge the two.
const require = createRequire(import.meta.url);
const ui = require("../web/app.js");

const checks = [];
const check = (name, condition, detail = "") => checks.push({ name, ok: Boolean(condition), detail });
const near = (a, b, tolerance = 1e-9) => Math.abs(a - b) <= tolerance;

// ---- formatting -------------------------------------------------------------
check("format: ribuan gaya Indonesia", ui.rp(5025) === "5.025", ui.rp(5025));
check("format: nol tetap nol", ui.rp(0) === "0", ui.rp(0));
check("format: kosong jadi tanda pisah", ui.rp(null) === "—");
check("format: persen positif diberi tanda", ui.pct(1.5) === "+1,50%", ui.pct(1.5));
check("format: persen negatif", ui.pct(-2.25) === "-2,25%", ui.pct(-2.25));

// ---- escape -----------------------------------------------------------------
check("escape: tag HTML dinetralkan", ui.esc("<b>x</b>") === "&lt;b&gt;x&lt;/b&gt;");
check("escape: tanda kutip aman", ui.esc('a"b') === "a&quot;b");

// ---- chart ------------------------------------------------------------------
const bars = Array.from({ length: 40 }, (_, i) => ({
  t: 1792000000 + i * 86400,
  o: 100 + i, h: 104 + i, l: 96 + i, c: 101 + i, v: 1000 + i * 25,
}));

const svg = ui.renderCandles(bars, { prevClose: 120, interval: "1d", timezone: "Asia/Jakarta" });
check("grafik: SVG utuh", svg.startsWith("<svg") && svg.endsWith("</svg>"));
check("grafik: satu lilin per bar", (svg.match(/<rect /g) || []).length >= bars.length * 2);
check("grafik: SMA20 digambar", svg.includes("<polyline"));
check("grafik: garis penutupan sebelumnya", svg.includes("penutupan sebelumnya"));
check("grafik: label sumbu waktu", (svg.match(/<text /g) || []).length >= 9);
check("grafik: penanda terakhir berdenyut", svg.includes("<animate"));
check("grafik: grup crosshair ada", svg.includes('id="xk-cross"'));
check("grafik: geometri untuk kursor", /data-step="[\d.]+"/.test(svg) && /data-high="[\d.]+"/.test(svg));
check("grafik: seri kosong ditangani", ui.renderCandles([]).includes("Tidak ada bar"));

const flat = bars.map((bar) => ({ ...bar, o: 100, h: 100, l: 100, c: 100 }));
const flatSvg = ui.renderCandles(flat);
check("grafik: harga datar tidak membagi nol", flatSvg.includes("<svg") && !flatSvg.includes("NaN"));

// ---- crosshair geometry -----------------------------------------------------
ui.state.bars = bars;
const written = {};
const stub = (selector) => ({
  style: {},
  setAttribute(key, value) { written[`${selector} ${key}`] = value; },
  set textContent(value) { written[`${selector} text`] = value; },
  querySelector: (child) => stub(child),
});
globalThis.document = { querySelector: () => null, createElement: () => stub("#tip") };

const geometry = {
  dataset: { left: "8", top: "14", plotw: "828", priceh: "212", step: "27.6",
             high: "140", low: "95", count: String(bars.length) },
  querySelector: (selector) => stub(selector),
  getScreenCTM: () => null,
};
ui.showCross(geometry, 10, undefined);
check("crosshair: garis di lilin ke-11",
  written[".xk-v x1"] === (8 + 10 * 27.6 + 13.8).toFixed(2), written[".xk-v x1"]);
check("crosshair: label harga = penutupan",
  written[".xk-ytext text"] === String(bars[10].c), written[".xk-ytext text"]);
ui.showCross(geometry, 9999, undefined);
check("crosshair: indeks di luar dijepit ke lilin terakhir",
  written[".xk-col x"] === (8 + 39 * 27.6).toFixed(2), written[".xk-col x"]);
ui.hideCross(geometry);

// ---- date labels ------------------------------------------------------------
check("tanggal: harian memakai UTC", ui.axisLabel(1792000000, "1d", "Asia/Jakarta").length > 0);
check("tanggal: intraday memuat jam", /\d{1,2}[.:]\d{2}/.test(ui.axisLabel(1792003600, "5m", "Asia/Jakarta")));
check("tanggal: zona waktu tak dikenal tidak kosong",
  ui.axisLabel(1792003600, "5m", "Bukan/Zona").length > 0);
check("tanggal: label lengkap harian", ui.fullLabel(1792000000, "1d", "Asia/Jakarta").length > 5);

// ---- views that need only a payload ----------------------------------------
ui.state.analysis = null;
check("panel kosong: menyebut tombol Analisis", ui.viewIdle().includes("Analisis"));
check("panel kosong: menyebut simbol", ui.viewIdle().includes(ui.state.symbol));
check("dewan: bergantung pada analisis", ui.viewRoles().includes("Analisis"));

// ---- screener panel ----------------------------------------------------------
ui.state.screener = { result: null, busy: false, symbols: "", maxPicks: 5, shortlist: 15, ai: false, record: false };
const emptyScreen = ui.viewScreener();
check("pemindai: panel memuat form dan AI default mati",
  emptyScreen.includes('id="screen-form"') && emptyScreen.includes("screen-ai")
  && !emptyScreen.includes('id="screen-ai" type="checkbox" checked'));
ui.state.screener.result = {
  as_of: "2026-10-27", universe: 2, picks: [], picks_source: "model", market_note: "netral",
  ai: { used: true, dropped: [], unassessed: [] }, rejected: [
    { symbol: "ZZZZ.JK", stage: "gerbang", reasons: ["<img src=x>"] },
  ], errors: [], recorded: [], elapsed_s: 1.2, disclaimer: "bukan nasihat",
  candidates: [{ symbol: "BBCA.JK", name: "<script>alert(1)</script>", pick: false,
    mechanical: { rating: "Overweight", composite: 0.42 }, ai: { rating: "Hold" },
    plan: { entry: 9000, stop: 8500, target: 10000 }, flags: ["tanpa data: berita"] }],
};
const screenHtml = ui.viewScreener();
check("pemindai: hasil nol pilihan, sumber model, dan rating dirender",
  screenHtml.includes("Nol pilihan adalah hasil yang sah") && screenHtml.includes("model")
  && screenHtml.includes("Overweight") && screenHtml.includes("Hold"));
check("pemindai: level entry/stop/target dirender dari hasil mesin",
  screenHtml.includes("9.000") && screenHtml.includes("8.500") && screenHtml.includes("10.000"));
check("pemindai: alasan penolakan dirender", screenHtml.includes("Ditolak (1)") && screenHtml.includes("gerbang"));
check("pemindai: teks tidak tepercaya di-escape", !screenHtml.includes("<script>")
  && !screenHtml.includes("<img src=x>") && screenHtml.includes("&lt;script&gt;"));
ui.state.screener.result = null;

// ---- decision summary + simulator -------------------------------------------
const guideFixture = {
  policy: "Pemetaan deterministik; bukan probabilitas.", action: "Tunggu", status: "sinyal_berbeda",
  why: "Teknikal bearish berbeda dari AI Buy.", next_action: "Tunggu sinyal baru.",
  machine_rating: "Hold", machine_action: "Tunggu", machine_confidence: 0.74,
  machine_confidence_note: "Keyakinan adalah skor heuristik, bukan probabilitas keuntungan.",
  ai_rating: "Buy", ai_action: "Beli", ai_conviction: 0.8,
  ai_confidence_note: "Keyakinan model bukan probabilitas profit.",
  as_of: "2026-10-27", stale_snapshot: false, staleness_days: 0,
  technical: { label: "negatif", strength: "kuat", trend: "turun", score: -1.2,
    available: true, supporting: [], opposing: ["harga di bawah SMA20"] },
  conflicts: [{ explanation: "Teknikal berbeda dari AI." }],
  data_quality: { status: "cukup", available_roles: 5, total_roles: 5, sessions: 100,
    as_of: "2026-10-27", staleness_days: 0, stale_snapshot: false,
    missing_roles: ["Berita"], proxy_roles: ["Teknikal"], warnings: [] },
  risk: { flags: ["ATR tinggi"], high_risk: true, reward_risk: 1.5,
    supporting: ["volume naik"], opposing: ["harga lemah"], ai_risks: ["volatilitas"],
    explanation: "R:R bukan peluang berhasil." },
};
const summaryHtml = ui.renderDecisionSummary({ guidance: guideFixture });
check("ringkasan: konflik tampil sebagai Tunggu dan alasan dua arah", summaryHtml.includes("Tunggu")
  && summaryHtml.includes("Teknikal bearish berbeda") && summaryHtml.includes("Buy · Beli"));
check("ringkasan: confidence bukan probabilitas profit", summaryHtml.includes("bukan probabilitas")
  && summaryHtml.includes("Data cukup"));
check("ringkasan: tanggal snapshot dan freshness tampil", summaryHtml.includes("Tanggal snapshot:")
  && summaryHtml.includes("2026-10-27"));
check("ringkasan: missing/proxy quality are disclosed", summaryHtml.includes("Tanpa data")
  && summaryHtml.includes("proksi harga/volume"));
const simHtml = ui.renderSimulator({ analysis_id: "server-id", symbol: "BBCA.JK",
  guidance: { action: "Beli" }, trader: { entry: 1000, stop: 950, target: 1100, lots: 2 } });
check("simulator: buy-side uses locked machine levels, user enters amount/size", simHtml.includes("Entry mesin")
  && simHtml.includes('id="sim-capital"') && simHtml.includes('id="sim-lots"')
  && !simHtml.includes('id="sim-entry"'));
const noBuySim = ui.renderSimulator({ analysis_id: "server-id", guidance: { action: "Tunggu", next_action: "Tunggu data." },
  trader: { entry: 1000, stop: 950, target: 1100 } });
check("simulator: Tunggu tidak membuka skenario transaksi beli", noBuySim.includes("Tunggu data")
  && !noBuySim.includes('id="sim-form"'));
const simResultHtml = ui.renderSimulationResult({ result: { entry_notional: 200000,
  capital_share_pct: 40, target_pnl_on_capital_pct: 4, stop_pnl_on_capital_pct: -2,
  gross_target_pnl: 20000, gross_stop_pnl: -10000,
  fees_included: false, gross_reward_risk: 2, direction: "long" } });
check("simulator: labels gross P/L and non-probability risk", simResultHtml.includes("kotor, biaya belum dihitung")
  && simResultHtml.includes("bukan probabilitas berhasil"));

// ---- a job belongs to the symbol it was started for --------------------------
// Painting a verdict belonging to another symbol over the current chart was the
// bug: switching symbols mid-analysis must not leak B's verdict onto A.
check("job: same symbol belongs", ui.analysisBelongsTo({ symbol: "BBCA.JK" }, "BBCA.JK") === true);
check("job: another symbol does not belong",
  ui.analysisBelongsTo({ symbol: "BBCA.JK" }, "BBRI.JK") === false);
check("job: legacy job without a symbol stays visible",
  ui.analysisBelongsTo({}, "BBRI.JK") === true
  && ui.analysisBelongsTo({ symbol: "BBCA.JK" }, "") === true);
check("job: no job belongs to nothing", ui.analysisBelongsTo(null, "BBCA.JK") === false);

// ---- report -----------------------------------------------------------------
let failed = 0;
for (const item of checks) {
  console.log(`${item.ok ? "PASS" : "FAIL"}  ${item.name}${item.detail && !item.ok ? `   [${item.detail}]` : ""}`);
  failed += item.ok ? 0 : 1;
}
console.log(`\n${checks.length - failed}/${checks.length} cek murni lolos`);
process.exit(failed ? 1 : 0);
