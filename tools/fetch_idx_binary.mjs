// Fetch the prebuilt idx binary for this platform and verify its checksum.
//
// Building from source needs the MSVC linker (link.exe), which a machine
// without Visual Studio Build Tools does not have. Every release publishes a
// binary per platform plus SHA256SUMS, so this avoids the toolchain entirely.
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const VENDOR = join(HERE, "..", "vendor");

const REPO = "fzn0x/idx";
const ASSET = "idx-0.8.1-x86_64-pc-windows-msvc.zip";
const ZIP = join(VENDOR, ASSET);

await mkdir(VENDOR, { recursive: true });

const release = await (await fetch(`https://api.github.com/repos/${REPO}/releases/tags/v0.8.1`,
  { headers: { "User-Agent": "katalis" } })).json();
const asset = release.assets.find((entry) => entry.name === ASSET);
if (!asset) throw new Error(`asset ${ASSET} not found in v0.8.1`);

process.stdout.write(`downloading ${ASSET} … `);
const response = await fetch(asset.browser_download_url, { headers: { "User-Agent": "katalis" } });
if (!response.ok) throw new Error(`HTTP ${response.status}`);
const buffer = Buffer.from(await response.arrayBuffer());
await writeFile(ZIP, buffer);
console.log(`${(buffer.length / 1024 / 1024).toFixed(2)} MB`);

// The published checksum is authoritative: refuse to install a binary whose
// bytes do not match it.
const sumsText = await (await fetch(
  `https://github.com/${REPO}/releases/download/v0.8.1/SHA256SUMS`,
  { headers: { "User-Agent": "katalis" } })).text();
const expected = (sumsText.split("\n").find((line) => line.includes(ASSET)) || "")
  .trim().split(/\s+/)[0]?.toLowerCase();
const actual = createHash("sha256").update(buffer).digest("hex");
if (!expected) throw new Error("no checksum published for this asset");
if (expected !== actual) throw new Error(`checksum mismatch: expected ${expected}, got ${actual}`);
console.log(`sha256 verified  ${actual.slice(0, 16)}…`);
