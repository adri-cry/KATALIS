// Produce docs/preview-chart.svg from live data using the dashboard's own
// chart renderer, so the preview shows the same geometry the page does.
//
//   node tools/make_preview_svg.mjs [base-url]
import { readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const ui = require("../web/app.js");

const BASE = process.argv[2] || "http://127.0.0.1:8770";

const response = await fetch(`${BASE}/api/market?symbol=BBCA.JK&range=6mo`);
if (!response.ok) throw new Error(`GET /api/market: HTTP ${response.status}`);
const chart = await response.json();

const svg = ui.renderCandles(chart.bars, {
  prevClose: chart.prev_close,
  interval: chart.interval,
  timezone: chart.timezone,
});

await writeFile(new URL("../docs/preview-chart.svg", import.meta.url), svg, "utf-8");
console.log(`docs/preview-chart.svg  ${chart.bars.length} bar  ${svg.length} byte`);

// Keep the payload next to it so the picture can be rebuilt without the server.
const watchlist = await (await fetch(`${BASE}/api/watchlist`)).json();
const health = await (await fetch(`${BASE}/api/health`)).json();
await writeFile(new URL("../docs/preview-data.json", import.meta.url),
  JSON.stringify({ watch: watchlist, chart, health }, null, 2), "utf-8");
console.log("docs/preview-data.json disimpan");
