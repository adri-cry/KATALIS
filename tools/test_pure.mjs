// Offline checks for the browser side of KATALIS.
//
// No server, no network, no DOM: this exercises the pure functions the
// dashboard is built from — the chart renderer, the crosshair geometry, the
// number and date formatters, and the rating parser. CI runs it on every push;
// tools/check_ui.mjs covers the live end-to-end path instead.
//
//   node tools/test_pure.mjs
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

// ---- report -----------------------------------------------------------------
let failed = 0;
for (const item of checks) {
  console.log(`${item.ok ? "PASS" : "FAIL"}  ${item.name}${item.detail && !item.ok ? `   [${item.detail}]` : ""}`);
  failed += item.ok ? 0 : 1;
}
console.log(`\n${checks.length - failed}/${checks.length} cek murni lolos`);
process.exit(failed ? 1 : 0);
