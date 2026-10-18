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

const args = process.argv.slice(2);
const includeArchive = args.includes("--include-archive");
const jsonOutput = args.includes("--json");
const deckIndex = args.indexOf("--deck");
const deckFilter =
  deckIndex >= 0 && args[deckIndex + 1]
    ? path.basename(args[deckIndex + 1]).replace(/\.(?:md|html)$/u, "")
    : "";

const builtFiles = fs.existsSync(presentationDir)
  ? fs
      .readdirSync(presentationDir)
      .filter((name) => name.endsWith(".html"))
      .sort()
  : [];
let files = includeArchive
  ? builtFiles
  : builtFiles.filter((name) => !name.startsWith("legacy-"));
if (deckFilter !== "") {
  files = files.filter((name) => name === `${deckFilter}.html`);
}

const issues = [];
let slides = 0;

function finish(code) {
  if (jsonOutput) {
    console.log(
      JSON.stringify({
        files: files.length,
        slides,
        issues,
      }),
    );
  } else if (code === 0) {
    console.log(
      `已渲染核对 ${files.length} 份${includeArchive ? "（含旧版归档）" : ""}放映页，共 ${slides} 页。`,
    );
    console.log("未发现 1280×720 页面裁切或图片加载失败。");
  } else {
    console.error(`发现 ${issues.length} 个渲染问题：`);
    for (const issue of issues) {
      console.error(`  x ${issue.file} 第 ${issue.page} 页${issue.detail}`);
    }
  }
  process.exitCode = code;
}

if (files.length === 0) {
  issues.push({
    file: deckFilter === "" ? "(presentation)" : `${deckFilter}.html`,
    page: 0,
    type: "no-decks",
    detail:
      deckFilter === ""
        ? "没有已构建的放映页；先运行 pnpm run docs:slides。"
        : `没有找到 ${deckFilter}.html；先运行 pnpm run docs:slides。`,
  });
  finish(1);
} else {
  const chromeCandidates = [
    process.env.PUPPETEER_EXECUTABLE_PATH,
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
  ].filter(Boolean);
  const executablePath = chromeCandidates.find((candidate) =>
    fs.existsSync(candidate),
  );
  const browser = await puppeteer.launch({
    headless: true,
    ...(executablePath ? { executablePath } : {}),
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  });

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
          issues.push({
            file,
            page: slide.index,
            type: "overflow-y",
            detail: `纵向溢出 ${slide.scrollHeight}px > ${slide.clientHeight}px`,
          });
        }
        if (slide.scrollWidth > slide.clientWidth + 1) {
          issues.push({
            file,
            page: slide.index,
            type: "overflow-x",
            detail: `横向溢出 ${slide.scrollWidth}px > ${slide.clientWidth}px`,
          });
        }
        for (const image of slide.images) {
          if (!image.complete || image.naturalWidth === 0) {
            issues.push({
              file,
              page: slide.index,
              type: "image-missing",
              detail: `图片未加载：${image.src}`,
            });
          }
        }
      }
      await page.close();
    }
  } finally {
    await browser.close();
  }

  finish(issues.length === 0 ? 0 : 1);
}
