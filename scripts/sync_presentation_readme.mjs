import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  presentationCatalog,
  presentationSections,
  presentationUrl,
} from "../src/presentation/catalog.mjs";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, "..");
const readmePath = path.join(repoRoot, "src", "presentation", "README.md");
const beginMarker = "<!-- BEGIN GENERATED PRESENTATION INDEX -->";
const endMarker = "<!-- END GENERATED PRESENTATION INDEX -->";
const write = process.argv.includes("--write");
const check = process.argv.includes("--check");
const base = "https://zhouxzh.github.io/Ascend310";

const statusLabel = {
  ready: "可用",
  scaffold: "制作中",
  legacy: "旧版归档",
};

function renderSection(section) {
  const entries = presentationCatalog
    .filter((entry) => entry.section === section.id)
    .sort((left, right) => left.order - right.order);
  const rows = entries
    .map(
      (entry) =>
        `| ${entry.title} | ${statusLabel[entry.status] ?? entry.status} | [打开放映版](${presentationUrl(entry, base)}) |`,
    )
    .join("\n");
  return `### ${section.title}

| 单元 | 状态 | 放映版 |
|---|---|---|
${rows}`;
}

function renderBlock() {
  return `${beginMarker}
${presentationSections.map(renderSection).join("\n\n")}
${endMarker}`;
}

const original = fs.readFileSync(readmePath, "utf8");
const start = original.indexOf(beginMarker);
const end = original.indexOf(endMarker);
if (start === -1 || end === -1 || end < start) {
  throw new Error(`Presentation README markers are missing in ${readmePath}`);
}
const generated = renderBlock();
const current = original.slice(start, end + endMarker.length);
const expected = `${original.slice(0, start)}${generated}${original.slice(end + endMarker.length)}`;

if (check) {
  if (current !== generated) {
    console.error("presentation README 索引已落后于 catalog.mjs。");
    console.error("运行 node scripts/sync_presentation_readme.mjs --write");
    process.exitCode = 1;
  } else {
    console.log("presentation README 索引与 catalog.mjs 一致。");
  }
} else if (write) {
  if (expected !== original) {
    fs.writeFileSync(readmePath, expected, "utf8");
    console.log(`已更新 ${path.relative(repoRoot, readmePath)}`);
  } else {
    console.log("presentation README 索引无变化。");
  }
} else {
  console.log(generated);
  console.log("这是预览；用 --write 更新，或用 --check 检查。");
}
