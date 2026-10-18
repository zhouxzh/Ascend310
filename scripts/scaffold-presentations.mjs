import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { presentationCatalog, presentationSections } from "../src/presentation/catalog.mjs";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, "..");
const sourceDir = path.join(repoRoot, "src", "presentation");
const write = process.argv.includes("--write");
const force = process.argv.includes("--force");

const sectionTitle = new Map(
  presentationSections.map((section) => [section.id, section.title]),
);

function sourceLines(entry) {
  const lines = [
    `对应教材：\`${entry.sources[0]}\``,
  ];
  if (entry.samples.length > 0) {
    lines.push(`配套样例：\`${entry.samples[0]}\``);
  }
  if (entry.sources.length > 1) {
    lines.push(`关联索引：${entry.sources.slice(1).map((item) => `\`${item}\``).join("、")}`);
  }
  return lines;
}

function scaffold(entry) {
  const plan = entry.lessonPlan
    .map((item, index) => `${index + 1}. ${item}`)
    .join("\n");
  const sources = sourceLines(entry);
  const isCase = entry.section === "experiment";
  const visualPage = isCase
    ? `

---

<!-- _class: screenshot -->

## 前端界面证据

> 本页预留真实前端截图；采集后必须写明开发板档位、页面路径、视口、隐私场景和截图清单编号。

<div class="source">待接入：\`src/presentation/screenshots/capture-manifest.json\`；来源：\`${entry.samples[0]}\`</div>

<!--
授课提示：截图只展示合成数据或匿名数据。先让学生识别页面上的输入、运行状态和结果区域。
-->`
    : `

---

<!-- _class: visual -->

## 本章运行证据

> 本页预留本章最关键的板端运行界面、日志或结果图。没有实测证据时保留待采集说明，不把示意图写成通过结论。

<div class="source">来源：\`${entry.sources[0]}\`；运行入口：\`${entry.samples[0] ?? "无独立样例"}\`</div>

<!--
授课提示：先指出这是什么证据，再解释它能证明什么、不能证明什么。
-->`;

  return `---
marp: true
theme: ascend310
paginate: true
size: 16:9
header: "《昇腾310B实战》教材配套演示"
footer: "${entry.title}"
---

<!-- presentation-scaffold: ${entry.id} -->

<!-- _class: cover -->

# ${entry.title}

${sectionTitle.get(entry.section)} · 教师授课版

${sources.join("\n\n")}

<!--
授课提示：用一句话说明本讲要解决的工程问题，再核对教材和样例路径。
-->

---

## 本讲教学主线

${plan}

课堂结束时，学生应能沿着这 ${entry.lessonPlan.length} 个节点复述设计、运行入口和证据边界。

<div class="source">教材：\`${entry.sources[0]}\`；样例：\`${entry.samples[0] ?? "无独立样例"}\`</div>

<!--
授课提示：逐项带读，不逐字念稿；每讲完一个节点就指出对应教材小节。
-->
${visualPage}

---

## 配套源码与课堂产出

| 项目 | 本讲入口 |
|---|---|
| 教材正文 | \`${entry.sources[0]}\` |
| 可运行样例 | \`${entry.samples[0] ?? "无独立样例"}\` |
| 截图清单 | \`src/presentation/screenshots/capture-manifest.json\` |
| 课件清单 | \`src/presentation/catalog.mjs\` |

课堂产出必须包含实际运行的命令、界面或日志，以及失败时停在哪一层。

<div class="source">课件制作中：${entry.id}；完成正文、截图和讲师备注后移除 scaffold 标记。</div>

<!--
授课提示：最后留下一个可检查的课堂任务；学生提交运行入口、结果和未完成边界。
-->
`;
}

let created = 0;
let skipped = 0;
for (const entry of presentationCatalog) {
  if (entry.status !== "scaffold" || !entry.build) continue;
  const target = path.join(sourceDir, entry.source);
  if (fs.existsSync(target) && !force) {
    skipped += 1;
    continue;
  }
  console.log(`${write ? "写入" : "预览"} ${path.relative(repoRoot, target)}`);
  if (write) {
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, scaffold(entry), "utf8");
  }
  created += 1;
}

console.log(
  `${write ? "已生成" : "将生成"} ${created} 份课件框架，跳过 ${skipped} 份已有文件。`,
);
if (!write) {
  console.log("这是预览；确认后运行 node scripts/scaffold-presentations.mjs --write");
}
