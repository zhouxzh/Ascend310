import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";

import {
  presentationCatalog,
  presentationSections,
} from "../src/presentation/catalog.mjs";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, "..");
const presentDir = path.join(repoRoot, "src", "presentation");
const screenshotDir = path.join(presentDir, "screenshots");
const errors = [];
const warnings = [];

function fail(message) {
  errors.push(message);
}

function warn(message) {
  warnings.push(message);
}

function readText(filePath) {
  return fs.readFileSync(filePath, "utf8");
}

function parseYamlScalar(frontmatter, key) {
  const match = frontmatter.match(new RegExp(`^${key}:\\s*["']?(.*?)["']?\\s*$`, "m"));
  return match?.[1]?.trim() ?? null;
}

function sourceTitle(filePath) {
  const text = readText(filePath);
  const frontmatterMatch = text.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  let inFence = false;
  let h1 = null;
  for (const line of text.split(/\r?\n/)) {
    if (/^\s*(?:```|~~~)/.test(line)) {
      inFence = !inFence;
      continue;
    }
    if (!inFence) {
      const match = line.match(/^#\s+(.+?)(?:\s+\{#[^}]+\})?\s*$/);
      if (match) {
        h1 = match[1].trim();
        break;
      }
    }
  }
  const frontmatterTitle = frontmatterMatch
    ? parseYamlScalar(frontmatterMatch[1], "title")
    : null;
  return {
    frontmatter: frontmatterMatch ? frontmatterMatch[1] : null,
    title: frontmatterTitle ?? h1,
  };
}

function deckFrontmatter(text) {
  const match = text.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  if (!match) return null;
  const lines = Object.fromEntries(
    match[1]
      .split(/\r?\n/)
      .map((line) => line.match(/^([A-Za-z][A-Za-z0-9_-]*):\s*(.*?)\s*$/))
      .filter(Boolean)
      .map((line) => [line[1], line[2].replace(/^["']|["']$/g, "")]),
  );
  return { raw: match[1], values: lines };
}

const sectionIds = new Set(presentationSections.map((section) => section.id));
const ids = new Set();
const slugs = new Set();
const orders = new Set();
const canonicalKinds = {
  book: { label: "理论章节", expected: 10, count: 0 },
  experiment: { label: "实践案例", expected: 9, count: 0 },
  appendix: { label: "附录", expected: 7, count: 0 },
};

for (const entry of presentationCatalog) {
  if (!entry.id || ids.has(entry.id)) fail(`重复或缺失课件 id：${entry.id}`);
  if (!entry.slug || slugs.has(entry.slug)) fail(`重复或缺失课件 slug：${entry.slug}`);
  if (!Number.isInteger(entry.order) || orders.has(entry.order)) {
    fail(`重复或缺失课件排序：${entry.id} -> ${entry.order}`);
  }
  if (!sectionIds.has(entry.section)) fail(`${entry.id} 使用了未知分区：${entry.section}`);
  ids.add(entry.id);
  slugs.add(entry.slug);
  orders.add(entry.order);

  if (Object.hasOwn(canonicalKinds, entry.section)) canonicalKinds[entry.section].count += 1;

  for (const source of entry.sources ?? []) {
    if (!fs.existsSync(path.join(repoRoot, source))) {
      fail(`${entry.id} 引用的教材路径不存在：${source}`);
    }
  }
  for (const sample of entry.samples ?? []) {
    if (!fs.existsSync(path.join(repoRoot, sample))) {
      fail(`${entry.id} 引用的样例路径不存在：${sample}`);
    }
  }

  const deckPath = path.join(presentDir, entry.source);
  if (!fs.existsSync(deckPath)) {
    fail(`${entry.id} 缺少课件源文件：${entry.source}`);
    continue;
  }

  const text = readText(deckPath);
  const frontmatter = deckFrontmatter(text);
  if (!frontmatter) {
    fail(`${entry.id} 缺少 frontmatter：${entry.source}`);
    continue;
  }
  for (const key of ["marp", "theme", "paginate", "size"]) {
    if (!frontmatter.values[key]) fail(`${entry.id} frontmatter 缺少 ${key}`);
  }
  if (frontmatter.values.marp !== "true") fail(`${entry.id} 的 marp 必须为 true`);
  if (frontmatter.values.theme !== "ascend310") fail(`${entry.id} 的 theme 必须为 ascend310`);
  if (frontmatter.values.paginate !== "true") fail(`${entry.id} 的 paginate 必须为 true`);
  if (frontmatter.values.size !== "16:9") fail(`${entry.id} 的 size 必须为 16:9`);

  if (entry.status === "scaffold" && !text.includes(`<!-- presentation-scaffold: ${entry.id} -->`)) {
    fail(`${entry.id} 标记为 scaffold，但缺少 scaffold 标记`);
  }
  if (entry.status === "ready" && text.includes("<!-- presentation-scaffold:")) {
    fail(`${entry.id} 已标记 ready，但仍保留 scaffold 标记`);
  }

  if (!["overview", "archive"].includes(entry.section) && entry.status !== "legacy") {
    const published = sourceTitle(path.join(repoRoot, entry.sources[0]));
    if (!published.title) {
      warn(`${entry.id} 无法从 ${entry.sources[0]} 读取公开标题`);
    } else if (published.title !== entry.title) {
      fail(
        `${entry.id} 标题与教材不一致：catalog=${JSON.stringify(entry.title)}，source=${JSON.stringify(published.title)}`,
      );
    }
    if (frontmatter.values.footer !== entry.title) {
      fail(`${entry.id} footer 与冻结标题不一致：${frontmatter.values.footer}`);
    }
    const coverHeading = text.match(/#\s+(.+?)\s*$/m)?.[1]?.trim();
    if (coverHeading !== entry.title) {
      fail(`${entry.id} 封面标题不一致：${coverHeading}`);
    }
  }
}

for (const [section, kind] of Object.entries(canonicalKinds)) {
  if (kind.count !== kind.expected) {
    fail(`${kind.label}数量应为 ${kind.expected}，实际为 ${kind.count}`);
  }
}

const planPath = path.join(screenshotDir, "capture-plan.json");
const manifestPath = path.join(screenshotDir, "capture-manifest.json");
if (!fs.existsSync(planPath)) fail("缺少截图采集计划 capture-plan.json");
if (!fs.existsSync(manifestPath)) fail("缺少截图清单 capture-manifest.json");

const plan = fs.existsSync(planPath) ? JSON.parse(readText(planPath)) : { captures: [] };
const manifest = fs.existsSync(manifestPath)
  ? JSON.parse(readText(manifestPath))
  : { captures: [] };
const plannedIds = new Set();
const outputPaths = new Set();
const plannedDecks = new Set();
for (const capture of plan.captures ?? []) {
  if (!capture.id || plannedIds.has(capture.id)) fail(`截图计划存在重复或缺失 id：${capture.id}`);
  if (!ids.has(capture.deck)) fail(`${capture.id} 指向未知课件：${capture.deck}`);
  if (!outputPaths.has(capture.output)) {
    if (!capture.output?.toLowerCase().endsWith(".png")) {
      fail(`${capture.id} 截图输出必须是 PNG：${capture.output}`);
    }
  } else {
    fail(`${capture.id} 截图输出路径重复：${capture.output}`);
  }
  if (!["browser", "native"].includes(capture.kind ?? "browser")) {
    fail(`${capture.id} 截图类型必须是 browser 或 native`);
  }
  if (!["board-8t", "board-20t"].includes(capture.board)) {
    fail(`${capture.id} 的开发板档位无效：${capture.board}`);
  }
  if (!capture.privacy) fail(`${capture.id} 缺少隐私分类`);
  if (!capture.evidenceBoundary) fail(`${capture.id} 缺少证据边界`);
  if ((capture.kind ?? "browser") === "native") {
    if (!Array.isArray(capture.applicationFiles) || capture.applicationFiles.length === 0) {
      fail(`${capture.id} 的原生截图缺少 applicationFiles`);
    } else {
      for (const source of capture.applicationFiles) {
        if (!fs.existsSync(path.join(repoRoot, source))) {
          fail(`${capture.id} 的应用源码不存在：${source}`);
        }
      }
    }
  }
  plannedIds.add(capture.id);
  plannedDecks.add(capture.deck);
  outputPaths.add(capture.output);
}

for (const entry of presentationCatalog.filter(
  (item) => item.section === "experiment" && item.status !== "legacy",
)) {
  if (!plannedDecks.has(entry.id)) {
    fail(`${entry.id} 还没有登记前端或运行界面截图任务`);
  }
}

for (const boardId of ["board-8t", "board-20t"]) {
  const snapshot = manifest.boardSnapshots?.[boardId];
  if (!snapshot) {
    fail(`截图清单缺少板端快照：${boardId}`);
    continue;
  }
  for (const key of ["capturedAt", "hostname", "soc", "tier", "cann", "driver", "health", "firmware"]) {
    if (!snapshot[key]) fail(`${boardId} 快照缺少 ${key}`);
  }
}

const capturedIds = new Set();
for (const capture of manifest.captures ?? []) {
  if (!capture.id || capturedIds.has(capture.id)) fail(`截图清单存在重复或缺失 id：${capture.id}`);
  if (!ids.has(capture.deck)) fail(`${capture.id} 指向未知课件：${capture.deck}`);
  if (!["board-8t", "board-20t"].includes(capture.board)) {
    fail(`${capture.id} 的开发板档位无效：${capture.board}`);
  }
  if (!capture.privacy) fail(`${capture.id} 缺少隐私分类`);
  if (!capture.evidenceBoundary) fail(`${capture.id} 缺少证据边界`);
  if (!/^[a-f0-9]{64}$/u.test(capture.applicationDigest ?? "")) {
    fail(`${capture.id} 缺少应用资产组合 SHA-256`);
  }
  if (!Array.isArray(capture.applicationAssets) || capture.applicationAssets.length === 0) {
    fail(`${capture.id} 缺少应用资产生效清单`);
  }
  const screenshotPath = path.resolve(screenshotDir, capture.output ?? "");
  const relative = path.relative(screenshotDir, screenshotPath);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    fail(`${capture.id} 截图路径越界：${capture.output}`);
  } else if (!fs.existsSync(screenshotPath)) {
    fail(`${capture.id} 截图文件不存在：${capture.output}`);
  } else {
    const bytes = fs.readFileSync(screenshotPath);
    if (capture.bytes !== bytes.length) {
      fail(`${capture.id} 字节数不一致：manifest=${capture.bytes}，file=${bytes.length}`);
    }
    const digest = createHash("sha256").update(bytes).digest("hex");
    if (capture.sha256 !== digest) fail(`${capture.id} SHA-256 不一致`);
  }
  capturedIds.add(capture.id);
}

const historicalManifestPath = path.join(screenshotDir, "historical-derivations.json");
if (fs.existsSync(historicalManifestPath)) {
  const historical = JSON.parse(readText(historicalManifestPath));
  for (const asset of historical.assets ?? []) {
    for (const key of ["source", "sourceSha256", "output", "outputSha256"]) {
      if (!asset[key]) fail(`历史截图派生记录 ${asset.id ?? "?"} 缺少 ${key}`);
    }
    for (const [kind, file] of [
      ["source", asset.source],
      ["output", asset.output],
    ]) {
      if (!file) continue;
      const absolute = path.join(repoRoot, file);
      if (!fs.existsSync(absolute)) {
        fail(`历史截图派生 ${asset.id} 的 ${kind} 不存在：${file}`);
        continue;
      }
      const bytes = fs.readFileSync(absolute);
      const expectedHash = kind === "source" ? asset.sourceSha256 : asset.outputSha256;
      if (expectedHash !== createHash("sha256").update(bytes).digest("hex")) {
        fail(`历史截图派生 ${asset.id} 的 ${kind} SHA-256 不一致`);
      }
      if (kind === "output" && asset.outputBytes !== bytes.length) {
        fail(`历史截图派生 ${asset.id} 的输出字节数不一致`);
      }
    }
  }
}

const statusCounts = presentationCatalog.reduce((counts, entry) => {
  counts[entry.status] = (counts[entry.status] ?? 0) + 1;
  return counts;
}, {});

console.log("课件目录检查");
console.log(
  `  课件 ${presentationCatalog.length} 份；${Object.entries(statusCounts)
    .map(([status, count]) => `${status}=${count}`)
    .join("，")}`,
);
console.log(
  `  覆盖：理论 ${canonicalKinds.book.count}/10，案例 ${canonicalKinds.experiment.count}/9，附录 ${canonicalKinds.appendix.count}/7`,
);
console.log(
  `  截图：计划 ${plan.captures?.length ?? 0}，已登记 ${manifest.captures?.length ?? 0}`,
);
if (warnings.length > 0) {
  console.log(`\n警告 ${warnings.length}`);
  for (const warning of warnings) console.log(`  ! ${warning}`);
}
if (errors.length > 0) {
  console.log(`\n错误 ${errors.length}`);
  for (const error of errors) console.log(`  x ${error}`);
  process.exitCode = 1;
} else {
  console.log("\n检查通过。");
}
