import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";

import { presentationsById } from "../src/presentation/catalog.mjs";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, "..");
const screenshotDir = path.join(repoRoot, "src", "presentation", "screenshots");
const planPath = path.join(screenshotDir, "capture-plan.json");
const manifestPath = path.join(screenshotDir, "capture-manifest.json");
const catalog = presentationsById();
const args = process.argv.slice(2);
const write = args.includes("--write");
const id = args.find((argument) => argument.startsWith("--id="))?.slice("--id=".length);
const fileArgument = args.find((argument) => argument.startsWith("--file="))?.slice("--file=".length);

if (!id || !fileArgument) {
  console.error("Usage: node scripts/register_presentation_screenshot.mjs --id=<plan-id> --file=<png> [--write]");
  process.exit(1);
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function sha256(buffer) {
  return createHash("sha256").update(buffer).digest("hex");
}

function pngDimensions(buffer) {
  const signature = "89504e470d0a1a0a";
  if (buffer.subarray(0, 8).toString("hex") !== signature) {
    throw new Error("Source image is not a PNG file.");
  }
  if (buffer.subarray(12, 16).toString("ascii") !== "IHDR") {
    throw new Error("PNG is missing its IHDR header.");
  }
  return {
    width: buffer.readUInt32BE(16),
    height: buffer.readUInt32BE(20),
  };
}

const plan = readJson(planPath);
const manifest = readJson(manifestPath);
const capture = (plan.captures ?? []).find((entry) => entry.id === id);
if (!capture) throw new Error(`Unknown capture id: ${id}`);
if (!catalog.has(capture.deck)) throw new Error(`${id}: unknown deck ${capture.deck}`);
if (!capture.output?.toLowerCase().endsWith(".png")) {
  throw new Error(`${id}: capture output must be a PNG path.`);
}

const applicationFiles = capture.applicationFiles ?? [];
if ((capture.kind ?? "browser") === "native" && applicationFiles.length === 0) {
  throw new Error(`${id}: native captures require applicationFiles.`);
}
const applicationAssets = applicationFiles.map((relativePath) => {
  const absolutePath = path.resolve(repoRoot, relativePath);
  if (!fs.existsSync(absolutePath) || !fs.statSync(absolutePath).isFile()) {
    throw new Error(`${id}: application file does not exist: ${relativePath}`);
  }
  const content = fs.readFileSync(absolutePath);
  return {
    type: "source",
    path: relativePath,
    bytes: content.length,
    sha256: sha256(content),
  };
});
const applicationDigest = sha256(
  Buffer.from(
    applicationAssets
      .map((asset) => `source\t${asset.path}\t${asset.sha256}`)
      .join("\n"),
  ),
);

const sourcePath = path.resolve(repoRoot, fileArgument);
if (!fs.existsSync(sourcePath) || !fs.statSync(sourcePath).isFile()) {
  throw new Error(`Source screenshot does not exist: ${sourcePath}`);
}
const destinationPath = path.resolve(screenshotDir, capture.output);
const relative = path.relative(screenshotDir, destinationPath);
if (relative.startsWith("..") || path.isAbsolute(relative)) {
  throw new Error(`${id}: capture output escapes screenshots directory.`);
}

const bytes = fs.readFileSync(sourcePath);
const dimensions = pngDimensions(bytes);
const record = {
  id,
  kind: capture.kind ?? "browser",
  deck: capture.deck,
  board: capture.board,
  description: capture.description ?? "",
  page: capture.url ?? capture.page ?? "board-desktop",
  viewport: {
    width: dimensions.width,
    height: dimensions.height,
    deviceScaleFactor: 1,
  },
  capturedAt: new Date().toISOString(),
  output: capture.output.replaceAll("\\", "/"),
  bytes: bytes.length,
  sha256: sha256(bytes),
  privacy: capture.privacy,
  evidenceBoundary: capture.evidenceBoundary,
  applicationDigest,
  applicationAssets,
  dataManifest: capture.dataManifest ?? null,
};

console.log(`登记 ${id}`);
console.log(`  来源：${sourcePath}`);
console.log(`  输出：${capture.output}`);
console.log(`  尺寸：${dimensions.width}x${dimensions.height}`);
console.log(`  SHA-256：${record.sha256}`);

if (!write) {
  console.log("这是预览；确认截图内容和边界后加 --write。");
  process.exit(0);
}

fs.mkdirSync(path.dirname(destinationPath), { recursive: true });
if (path.normalize(sourcePath) !== path.normalize(destinationPath)) {
  fs.copyFileSync(sourcePath, destinationPath);
}
const records = new Map((manifest.captures ?? []).map((entry) => [entry.id, entry]));
records.set(id, record);
manifest.captures = [...records.values()].sort((left, right) =>
  left.id.localeCompare(right.id),
);
fs.writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
console.log(`截图清单已更新：${path.relative(repoRoot, manifestPath)}`);
