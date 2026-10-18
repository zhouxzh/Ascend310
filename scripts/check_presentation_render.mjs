import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import puppeteer from "puppeteer";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, "..");
const presentationDir = path.join(
  repoRoot,
  "src",
  ".vuepress",
  "public",
  "presentation",
);
const includeArchive = process.argv.includes("--include-archive");
const builtFiles = fs
  .readdirSync(presentationDir)
  .filter((name) => name.endsWith(".html"))
  .sort();
const files = includeArchive
  ? builtFiles
  : builtFiles.filter((name) => !name.startsWith("legacy-"));

if (files.length === 0) {
  console.error("没有已构建的放映页；先运行 pnpm run docs:slides。");
  process.exit(1);
}

const chromeCandidates = [
  process.env.PUPPETEER_EXECUTABLE_PATH,
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].filter(Boolean);
const executablePath = chromeCandidates.find((candidate) => fs.existsSync(candidate));
const browser = await puppeteer.launch({
  headless: true,
  ...(executablePath ? { executablePath } : {}),
  args: ["--no-sandbox", "--disable-setuid-sandbox"],
});
const issues = [];
let slides = 0;

try {
  for (const file of files) {
    const page = await browser.newPage();
    await page.setViewport({ width: 1280, height: 720, deviceScaleFactor: 1 });
    await page.goto(pathToFileURL(path.join(presentationDir, file)).href, {
      waitUntil: "load",
    });
    await page.evaluate(() => document.fonts.ready);
    const result = await page.evaluate(() => {
      const sections = [...document.querySelectorAll("section")];
      return sections.map((section, index) => ({
        index: index + 1,
        clientWidth: section.clientWidth,
        clientHeight: section.clientHeight,
        scrollWidth: section.scrollWidth,
        scrollHeight: section.scrollHeight,
        images: [...section.querySelectorAll("img")].map((image) => ({
          src: image.getAttribute("src"),
          complete: image.complete,
          naturalWidth: image.naturalWidth,
        })),
      }));
    });
    slides += result.length;
    for (const slide of result) {
      if (slide.scrollHeight > slide.clientHeight + 1) {
        issues.push(`${file} 第 ${slide.index} 页纵向溢出：${slide.scrollHeight}px > ${slide.clientHeight}px`);
      }
      if (slide.scrollWidth > slide.clientWidth + 1) {
        issues.push(`${file} 第 ${slide.index} 页横向溢出：${slide.scrollWidth}px > ${slide.clientWidth}px`);
      }
      for (const image of slide.images) {
        if (!image.complete || image.naturalWidth === 0) {
          issues.push(`${file} 第 ${slide.index} 页图片未加载：${image.src}`);
        }
      }
    }
    await page.close();
  }
} finally {
  await browser.close();
}

console.log(
  `已渲染核对 ${files.length} 份${includeArchive ? "（含旧版归档）" : ""}放映页，共 ${slides} 页。`,
);
if (issues.length > 0) {
  console.error(`发现 ${issues.length} 个渲染问题：`);
  for (const issue of issues) console.error(`  x ${issue}`);
  process.exitCode = 1;
} else {
  console.log("未发现 1280×720 页面裁切或图片加载失败。");
}
