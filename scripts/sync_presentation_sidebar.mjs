import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  presentationSections,
  presentationsBySection,
} from "../src/presentation/catalog.mjs";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, "..");
const sidebarPath = path.join(repoRoot, "src", ".vuepress", "sidebar.ts");
const beginMarker = "// BEGIN GENERATED PRESENTATION SIDEBAR";
const endMarker = "// END GENERATED PRESENTATION SIDEBAR";
const write = process.argv.includes("--write");
const check = process.argv.includes("--check");

function renderBlock() {
  const groups = presentationSections.map((section) => {
    const entries = presentationsBySection(section.id).filter((entry) => entry.build);
    const children = entries
      .map((entry) => {
        const suffix = entry.status === "scaffold" ? "（制作中）" : "";
        return `        { text: ${JSON.stringify(`${entry.shortTitle}${suffix}`)}, link: "/presentation/${entry.slug}.html" },`;
      })
      .join("\n");
    return `  {
    text: ${JSON.stringify(section.sidebarTitle)},
    collapsible: true,
    children: [
${children}
    ],
  },`;
  });
  return `${beginMarker}
const presentationChildren = [
${groups.join("\n")}
];
${endMarker}`;
}

const original = fs.readFileSync(sidebarPath, "utf8");
const start = original.indexOf(beginMarker);
const end = original.indexOf(endMarker);
if (start === -1 || end === -1 || end < start) {
  throw new Error(`Presentation sidebar markers are missing in ${sidebarPath}`);
}
const expected = `${original.slice(0, start)}${renderBlock()}${original.slice(end + endMarker.length)}`;
const current = original.slice(start, end + endMarker.length);
const generated = renderBlock();

if (check) {
  if (current !== generated) {
    console.error("presentation sidebar 已落后于 catalog.mjs。");
    console.error("运行 node scripts/sync_presentation_sidebar.mjs --write");
    process.exitCode = 1;
  } else {
    console.log("presentation sidebar 与 catalog.mjs 一致。");
  }
} else if (write) {
  if (expected !== original) {
    fs.writeFileSync(sidebarPath, expected, "utf8");
    console.log(`已更新 ${path.relative(repoRoot, sidebarPath)}`);
  } else {
    console.log("presentation sidebar 无变化。");
  }
} else {
  console.log(generated);
  console.log("这是预览；用 --write 更新，或用 --check 检查。");
}
