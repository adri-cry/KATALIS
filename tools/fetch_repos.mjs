// Download both source repositories as zip archives into vendor/.
//
// Node's fetch is used because git, curl and PowerShell can all fail on a
// schannel TLS credential error on Windows. After this, unpack them with:
//     python tools/extract_repos.py
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const VENDOR = join(HERE, "..", "vendor");

const targets = [
  ["TauricResearch/TradingAgents", "main", "TradingAgents.zip"],
  ["fzn0x/idx", "main", "idx.zip"],
];

await mkdir(VENDOR, { recursive: true });

for (const [repo, ref, name] of targets) {
  const url = `https://codeload.github.com/${repo}/zip/refs/heads/${ref}`;
  process.stdout.write(`${repo} … `);
  const response = await fetch(url, {
    headers: { "User-Agent": "katalis-build" },
    redirect: "follow",
  });
  if (!response.ok) throw new Error(`${repo}: HTTP ${response.status}`);
  const buffer = Buffer.from(await response.arrayBuffer());
  await writeFile(join(VENDOR, name), buffer);
  console.log(`${(buffer.length / 1024 / 1024).toFixed(1)} MB -> vendor/${name}`);
}

console.log("\nBerikutnya: python tools/extract_repos.py");
