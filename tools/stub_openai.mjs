// A stand-in for the OpenAI chat-completions endpoint, used to prove the AI
// path end to end without a real key. It answers the same envelope shape and
// returns one JSON object, exactly as the client expects.
//
//   node tools/stub_openai.mjs [port]
import { createServer } from "node:http";

const PORT = Number(process.argv[2] || 8899);

const ANSWER = {
  rating: "Underweight",
  conviction: 0.72,
  thesis:
    "Tekanan jual terlihat dari tiga sisi sekaligus. Harga 5.025 berada di bawah SMA20 " +
    "dan SMA50, sementara RSI 27,1 menunjukkan pelemahan yang belum tentu berbalik.\n\n" +
    "Yang lebih penting: asing net jual 1,06 miliar saham dalam 20 sesi dan " +
    "bandarmologi membaca distribution dengan fase markdown, net -7,47% dari nilai " +
    "transaksi. Valuasi memang murah (P/E 10,6x, ROE 21,8%), tetapi murah bukan alasan " +
    "untuk melawan arus dana.\n\n" +
    "Karena itu Underweight, bukan Sell: kualitas emiten dan dividen 7,6% membatasi " +
    "penurunan, sehingga pengurangan bertahap lebih masuk akal daripada keluar penuh.",
  risks: [
    "Aliran asing berbalik net beli akan membatalkan tekanan utama tesis ini.",
    "RSI 27,1 sudah jenuh jual; rebound teknikal bisa terjadi lebih dulu.",
    "Data berita tidak tersedia pekan ini, sehingga katalis negatif mungkin belum terbaca.",
  ],
  action: { entry: 5025, stop: 5450, target: 4310, sizing_note: "Kurangi eksposur bertahap, bukan keluar sekaligus." },
  what_would_change: [
    "Asing net beli minimal 3 sesi beruntun.",
    "Harga menutup kembali di atas SMA20.",
    "Bandarmologi berubah menjadi accumulation.",
  ],
  confidence_note: "Empat dari lima peran punya data; peran berita kosong harus dianggap risiko.",
};

const BAD_KEY_ANSWER = { error: { message: "Incorrect API key provided: sk-test***", type: "invalid_request_error" } };

createServer((request, response) => {
  if (request.method !== "POST" || !request.url.endsWith("/chat/completions")) {
    response.writeHead(404, { "Content-Type": "application/json" });
    return response.end(JSON.stringify({ error: { message: "not found" } }));
  }

  const auth = request.headers.authorization || "";
  let body = "";
  request.on("data", (chunk) => { body += chunk; });
  request.on("end", () => {
    // A key ending in "-bad" exercises the refusal path.
    if (auth.endsWith("-bad")) {
      response.writeHead(401, { "Content-Type": "application/json" });
      return response.end(JSON.stringify(BAD_KEY_ANSWER));
    }

    let parsed = {};
    try { parsed = JSON.parse(body); } catch { /* keep empty */ }
    const model = parsed.model || "gpt-4o-mini";
    const isGatewayModel = model.startsWith("glm");

    // A gateway serving a non-OpenAI model: no native JSON mode, and the reply
    // may come wrapped in markdown. Both are what this stub exists to emulate.
    if (isGatewayModel && parsed.response_format) {
      response.writeHead(400, { "Content-Type": "application/json" });
      return response.end(JSON.stringify({
        error: {
          message: `response_format is not supported for model ${model}`,
          type: "invalid_request_error",
          param: "response_format",
        },
      }));
    }

    const content = JSON.stringify(ANSWER, null, 2);
    const envelope = {
      id: "chatcmpl-stub",
      object: "chat.completion",
      created: Math.floor(Date.now() / 1000),
      model,
      choices: [{
        index: 0,
        message: {
          role: "assistant",
          content: isGatewayModel ? "```json\n" + content + "\n```" : content,
        },
        finish_reason: "stop",
      }],
      usage: { prompt_tokens: 1180, completion_tokens: 320, total_tokens: 1500 },
    };
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify(envelope));
  });
}).listen(PORT, "127.0.0.1", () => {
  console.log(`stub OpenAI listening on http://127.0.0.1:${PORT}/v1`);
});
