// Headless check of the dashboard's renderers.
//
// There is no browser here, so the page cannot be opened and screenshotted.
// What can be checked is what matters: the renderers turn a real analysis
// payload into HTML without throwing, the HTML shell actually contains every
// element the script reaches for, and the numbers on screen are the numbers
// the API returned.
//
//   node tools/check_ui.mjs [base-url]

import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const ui = require("../web/app.js");

const BASE = process.argv[2] || "http://127.0.0.1:8770";
const results = [];
const check = (name, condition, detail = "") =>
  results.push({ name, ok: Boolean(condition), detail });

async function json(path, body) {
  const response = await fetch(BASE + path, {
    method: body ? "POST" : "GET",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  return { status: response.status, payload: await response.json() };
}

// ---- a real analysis to render -------------------------------------------------
// AI is opt-in here so a test run never bills the operator's model. Set
// CHECK_AI=1 to exercise the AI branch (against the stub, or accepting a call).
const withAI = process.env.CHECK_AI === "1";
const { payload: analysis } = await json("/api/analyze",
  { symbol: "BBCA.JK", holding_days: 5, ai: withAI });
ui.state.analysis = analysis;

const candles = ui.renderCandles(analysis.bars);
check("grafik: SVG terbentuk", candles.startsWith("<svg") && candles.endsWith("</svg>"));
check("grafik: ada batang lilin", (candles.match(/<rect /g) || []).length > 40);
check("grafik: ada garis SMA20", candles.includes("<polyline"));
check("grafik: harga terakhir tampil", candles.includes(ui.rp(analysis.bars.at(-1).c)));

// The daily chart must show the line an intraday move is judged against.
const withPrev = ui.renderCandles(analysis.bars, { prevClose: analysis.price, interval: "1d" });
check("grafik: garis penutupan sebelumnya", withPrev.includes("penutupan sebelumnya"));
const axisTexts = (withPrev.match(/<text /g) || []).length;
check("grafik: sumbu waktu berlabel", axisTexts >= 9, `${axisTexts} teks`);

// Hovering a candle must read out its OHLC, which needs the geometry the
// pointer handler converts coordinates with.
check("grafik: grup crosshair ada", withPrev.includes('id="xk-cross"'));
check("grafik: geometri untuk hitung kursor",
  /data-step="[\d.]+"/.test(withPrev) && /data-high="[\d.]+"/.test(withPrev)
  && /data-left="[\d.]+"/.test(withPrev));
check("grafik: label harga di sumbu kanan", withPrev.includes('class="xk-ytext"'));

// Intraday now exists, which is what makes a move like BUVA's visible.
const intraday = (await json("/api/market?symbol=BBCA.JK&range=1d")).payload;
const intraChart = ui.renderCandles(intraday.bars,
  { prevClose: intraday.prev_close, interval: intraday.interval, timezone: intraday.timezone });
check("1H: bar 5 menit tersedia", intraday.interval === "5m" && intraday.bars.length > 5);
check("1H: sumbu jam tampil", /\d{1,2}[.:]\d{2}/.test(intraChart));
check("1H: penanda terakhir berdenyut", intraChart.includes("<animate"));

const roles = ui.viewRoles();
check("dewan: lima peran dirender", (roles.match(/<article class="role/g) || []).length === 5);
check("dewan: nama peran muncul", roles.includes("Analis Teknikal") && roles.includes("Analis Bandar"));
check("dewan: peran tanpa data ditandai", analysis.roles.some((r) => !r.available)
  ? roles.includes("tanpa data") : true);
check("dewan: sumber data ditandai", roles.includes("proksi") || roles.includes("data idx"));

const debate = ui.viewDebate();
check("debat: dua kolom", debate.includes("Kasus bullish") && debate.includes("Kasus bearish"));
check("debat: berisi argumen", (debate.match(/<li>/g) || []).length >= 2);

const risk = ui.viewRisk();
check("risiko: tiga sikap", ["Agresif", "Neutral", "Konservatif"].every((n) => risk.includes(n)));

// ---- the AI layer, whichever state it is in -----------------------------------
const advice = ui.viewAdvice();
if (analysis.ai) {
  check("AI: tesis dirender", advice.includes("Tesis") && advice.length > 800);
  check("AI: rating AI tampil", advice.includes(`rating-${analysis.ai.rating}`));
  check("AI: risiko dan pembatal tesis", advice.includes("Risiko") && advice.includes("Yang membatalkan tesis"));
  check("AI: rencana AI tampil", advice.includes("Rencana AI"));
  check("AI: disclaimer ikut dirender", advice.toLowerCase().includes("bukan nasihat investasi"));
  check("AI: strip di kartu putusan", ui.aiStrip(analysis).includes(`AI: ${analysis.ai.rating}`));
} else {
  check("AI dimatikan: tidak ada blok dan tidak ada error",
    analysis.ai === null && analysis.ai_error === null);
  check("AI nonaktif: tab menjelaskan cara mengaktifkan", advice.includes("OPENAI_API_KEY"));
  check("AI nonaktif: strip menyebut nonaktif", ui.aiStrip(analysis).toLowerCase().includes("nonaktif"));
  check("AI nonaktif: analisis tetap lengkap", analysis.roles.length === 5);
}

// ---- the shell must contain every element the wiring reaches for at boot -------
// Panel forms (#p-add, #settleâ€¦) are created by the renderers, so only the ids
// wireEvents() touches are expected to be in the static HTML.
const html = await readFile(new URL("../web/index.html", import.meta.url), "utf8");
const source = await readFile(new URL("../web/app.js", import.meta.url), "utf8");
ui.state.screener = { result: null, busy: false, symbols: "", maxPicks: 5, shortlist: 15, ai: false, record: false };
const screenIdle = ui.viewScreener();
check("pemindai: panel kosong dan AI mati secara default",
  screenIdle.includes('id="screen-form"') && screenIdle.includes("universe.txt / watchlist")
  && !screenIdle.includes('id="screen-ai" type="checkbox" checked'));
ui.state.screener.result = {
  as_of: "2026-10-27", universe: 2, picks: [], picks_source: "model", market_note: "dua saham melemah",
  ai: { used: true, dropped: [], unassessed: [] }, rejected: [
    { symbol: "ZZZZ.JK", stage: "gerbang", reasons: ["<img src=x>"] },
  ], errors: [], recorded: [], elapsed_s: 1.2, disclaimer: "bukan nasihat",
  candidates: [{ symbol: "BBCA.JK", name: "<script>alert(1)</script>", pick: false,
    mechanical: { rating: "Overweight", composite: 0.42 }, ai: { rating: "Hold" },
    plan: { entry: 9000, stop: 8500, target: 10000 }, flags: ["tanpa data: berita"] }],
};
const screenHtml = ui.viewScreener();
check("pemindai: nol pilihan dan rating mesin/model tampil",
  screenHtml.includes("Nol pilihan adalah hasil yang sah") && screenHtml.includes("Overweight")
  && screenHtml.includes("Hold") && screenHtml.includes("model"));
check("pemindai: entry/stop/target hasil mesin tampil",
  screenHtml.includes("9.000") && screenHtml.includes("8.500") && screenHtml.includes("10.000"));
check("pemindai: alasan penolakan dan escape tampil",
  screenHtml.includes("Ditolak (1)") && screenHtml.includes("gerbang")
  && !screenHtml.includes("<script>") && !screenHtml.includes("<img src=x>"));
ui.state.screener.result = null;
check("analisis: progress, durasi, dan keterangan AI tersedia",
  source.includes("analysisProgressView") && source.includes("padStart(2, \"0\")")
  && source.includes("halaman ter-refresh") && source.includes('role="status"')
  && source.includes("/api/analyze/status?id=")
  && source.includes("Older running server has only the original synchronous endpoint")
  && source.includes("/api/analyze/start"));
const wiring = source.match(/function wireEvents\(\)\s*\{([\s\S]*?)\n\}/)?.[1] || "";
const ids = [...new Set([...wiring.matchAll(/\$\("#([a-z0-9-]+)"\)/g)].map((match) => match[1]))];
const missing = ids.filter((id) => !html.includes(`id="${id}"`));
check(`shell: ${ids.length} elemen awal ada di HTML`, ids.length >= 5 && missing.length === 0, missing.join(", "));

const journal = await json("/api/journal");
check("jurnal: entri terbaca", Array.isArray(journal.payload.entries));

const portfolio = await json("/api/portfolio");
check("portofolio: ekuitas berupa angka", typeof portfolio.payload.equity === "number");
check("portofolio: daftar posisi berupa larik", Array.isArray(portfolio.payload.positions));

// ---- report -------------------------------------------------------------------
let failed = 0;
for (const item of results) {
  console.log(`${item.ok ? "PASS" : "FAIL"}  ${item.name}${item.detail ? `   [${item.detail}]` : ""}`);
  failed += item.ok ? 0 : 1;
}
console.log(`\n${results.length - failed}/${results.length} UI checks passed`);
process.exit(failed ? 1 : 0);
