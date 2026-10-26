// Geometry check for the candlestick crosshair, without a browser.
//
// The hover readout is the part of the chart a screenshot cannot prove: the
// pointer has to land on the right candle. This stubs the SVG so the maths can
// be asserted directly.
//
//   node tools/check_crosshair.js
const ui = require("../web/app.js");

const bars = Array.from({ length: 30 }, (_, i) => ({
  t: 1792000000 + i * 86400, o: 100 + i, h: 102 + i, l: 99 + i, c: 101 + i, v: 1000,
}));
ui.state.bars = bars;

const written = {};
const stub = (selector) => ({
  style: {},
  setAttribute(key, value) { written[`${selector} ${key}`] = value; },
  set textContent(value) { written[`${selector} text`] = value; },
  querySelector: (child) => stub(child),
});

// showTip measures the tooltip against the page; with no #chart it returns
// immediately, which keeps this check to the geometry it is about.
global.document = { querySelector: () => null, createElement: () => stub("#tip") };

const svg = {
  dataset: { left: "8", top: "14", plotw: "828", priceh: "212", step: "27.6",
             high: "140", low: "95", count: "30" },
  querySelector: (selector) => stub(selector),
  getScreenCTM: () => null,
};

ui.showCross(svg, 10, undefined);

const expectedX = 8 + 10 * 27.6 + 27.6 / 2;
const expectedColumn = 8 + 10 * 27.6;
const expectations = [
  ["x garis bidik", written[".xk-v x1"], expectedX.toFixed(2)],
  ["x kolom sorotan", written[".xk-col x"], expectedColumn.toFixed(2)],
  ["label harga di sumbu", written[".xk-ytext text"], String(bars[10].c)],
  ["y titik penutupan", written[".xk-dot cy"], (14 + (140 - 111) / 45 * 212).toFixed(2)],
];

let failed = 0;
for (const [name, actual, expected] of expectations) {
  const ok = String(actual) === String(expected);
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}: ${actual} (harap ${expected})`);
  failed += ok ? 0 : 1;
}

// A cursor outside the data must clamp rather than draw off-canvas.
ui.showCross(svg, 999, undefined);
const clamped = written[".xk-col x"];
const lastColumn = (8 + 29 * 27.6).toFixed(2);
const clampOk = clamped === lastColumn;
console.log(`${clampOk ? "PASS" : "FAIL"}  indeks di luar rentang dijepit: ${clamped}`);
failed += clampOk ? 0 : 1;

console.log(`\n${expectations.length + 1 - failed}/${expectations.length + 1} cek crosshair lolos`);
process.exit(failed ? 1 : 0);
