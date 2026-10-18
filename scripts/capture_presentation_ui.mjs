import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";

import puppeteer from "puppeteer";

import { presentationsById } from "../src/presentation/catalog.mjs";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, "..");
const screenshotDir = path.join(repoRoot, "src", "presentation", "screenshots");
const planPath = path.join(screenshotDir, "capture-plan.json");
const manifestPath = path.join(screenshotDir, "capture-manifest.json");
const catalog = presentationsById();

const args = process.argv.slice(2);
const write = args.includes("--write");
const idArgument = args.find((argument) => argument.startsWith("--id="));
const selectedIds = idArgument
  ? new Set(idArgument.slice("--id=".length).split(",").map((value) => value.trim()).filter(Boolean))
  : null;

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function sha256(buffer) {
  return createHash("sha256").update(buffer).digest("hex");
}

function validateRelativePng(relativePath) {
  if (!relativePath || path.extname(relativePath).toLowerCase() !== ".png") {
    throw new Error(`Capture output must end in .png: ${relativePath}`);
  }
  const absolute = path.resolve(screenshotDir, relativePath);
  const relative = path.relative(screenshotDir, absolute);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error(`Capture output escapes screenshots directory: ${relativePath}`);
  }
  return absolute;
}

function resolveOrigin(url) {
  return url.replace(/\{\{([A-Z0-9_]+)\}\}/g, (whole, variable) => {
    const value = process.env[variable];
    if (!value) {
      throw new Error(`Missing environment variable for capture URL: ${variable}`);
    }
    return value.replace(/\/+$/, "");
  });
}

function validateUrl(rawUrl) {
  const parsed = new URL(rawUrl);
  if (!["http:", "https:"].includes(parsed.protocol)) {
    throw new Error(`Unsupported capture URL protocol: ${parsed.protocol}`);
  }
  if (parsed.username || parsed.password) {
    throw new Error("Capture URLs must not embed credentials.");
  }
  return parsed;
}

async function runActions(page, actions = []) {
  for (const action of actions) {
    switch (action.type) {
      case "click":
        await page.click(action.selector);
        break;
      case "waitForSelector":
        await page.waitForSelector(action.selector, {
          visible: action.visible !== false,
          timeout: action.timeoutMs,
        });
        break;
      case "wait":
        await new Promise((resolve) => setTimeout(resolve, action.ms ?? 500));
        break;
      case "press":
        await page.keyboard.press(action.key);
        break;
      case "type":
        await page.type(action.selector, action.text ?? "", {
          delay: action.delayMs ?? 0,
        });
        break;
      case "select":
        await page.select(action.selector, action.value);
        break;
      default:
        throw new Error(`Unsupported capture action type: ${action.type}`);
    }
  }
}

function plannedCaptures(plan) {
  const captures = plan.captures ?? [];
  if (!selectedIds) return captures;
  const known = new Set(captures.map((capture) => capture.id));
  const unknown = [...selectedIds].filter((id) => !known.has(id));
  if (unknown.length > 0) {
    throw new Error(`Unknown capture id(s): ${unknown.join(", ")}`);
  }
  return captures.filter((capture) => selectedIds.has(capture.id));
}

function validateCapture(capture, plan) {
  if (!capture.id) throw new Error("Capture is missing id.");
  if (!capture.deck || !catalog.has(capture.deck)) {
    throw new Error(`${capture.id}: unknown presentation deck id: ${capture.deck}`);
  }
  if (!["board-8t", "board-20t"].includes(capture.board)) {
    throw new Error(`${capture.id}: board must be board-8t or board-20t.`);
  }
  const kind = capture.kind ?? "browser";
  if (!["browser", "native"].includes(kind)) {
    throw new Error(`${capture.id}: kind must be browser or native.`);
  }
  if (!capture.output) {
    throw new Error(`${capture.id}: output is required.`);
  }
  if (kind === "browser" && !capture.url) {
    throw new Error(`${capture.id}: browser captures require url.`);
  }
  if (kind === "native" && capture.url) {
    throw new Error(`${capture.id}: native captures must not declare a browser url.`);
  }
  if (!capture.privacy) {
    throw new Error(`${capture.id}: privacy classification is required.`);
  }
  const viewport = {
    ...plan.defaults?.viewport,
    ...capture.viewport,
  };
  if (!viewport.width || !viewport.height || !viewport.deviceScaleFactor) {
    throw new Error(`${capture.id}: viewport is incomplete.`);
  }
  return {
    ...capture,
    kind,
    resolvedViewport: viewport,
    outputPath: validateRelativePng(capture.output),
  };
}

const plan = readJson(planPath);
const manifest = readJson(manifestPath);
const configured = plannedCaptures(plan).map((capture) => validateCapture(capture, plan));
const captures = configured.filter((capture) => capture.kind === "browser");
const native = configured.filter((capture) => capture.kind === "native");

for (const capture of native) {
  console.log(`跳过原生窗口 ${capture.id}：需在板端图形会话截图后登记清单。`);
}

if (captures.length === 0) {
  console.log("No browser UI captures are configured.");
  process.exit(0);
}

for (const capture of captures) {
  if (write) {
    const resolvedUrl = resolveOrigin(capture.url);
    validateUrl(resolvedUrl);
    console.log(`采集 ${capture.id}: ${resolvedUrl}`);
  } else {
    console.log(`预览 ${capture.id}: ${capture.url}`);
  }
}

if (!write) {
  console.log("这是预览；设置 BOARD_8T_ORIGIN、BOARD_20T_ORIGIN 并确认数据场景后加 --write。");
  process.exit(0);
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
const previous = new Map((manifest.captures ?? []).map((capture) => [capture.id, capture]));

try {
  for (const capture of captures) {
    const page = await browser.newPage();
    try {
      const pendingAssets = [];
      page.on("response", (response) => {
        const type = response.request().resourceType();
        if (!["document", "script", "stylesheet"].includes(type)) return;
        pendingAssets.push(
          response
            .buffer()
            .then((buffer) => ({
              type,
              url: response.url(),
              status: response.status(),
              bytes: buffer.length,
              sha256: sha256(buffer),
            }))
            .catch(() => null),
        );
      });
      await page.setViewport(capture.resolvedViewport);
      const resolvedUrl = resolveOrigin(capture.url);
      await page.goto(resolvedUrl, {
        waitUntil: capture.waitUntil ?? plan.defaults?.waitUntil ?? "networkidle2",
        timeout: capture.timeoutMs ?? plan.defaults?.timeoutMs ?? 30000,
      });
      if (capture.waitForSelector) {
        await page.waitForSelector(capture.waitForSelector, {
          visible: true,
          timeout: capture.timeoutMs ?? plan.defaults?.timeoutMs ?? 30000,
        });
      }
      await runActions(page, capture.actions);
      for (const selector of capture.hideSelectors ?? []) {
        await page.addStyleTag({
          content: `${selector} { visibility: hidden !important; }`,
        });
      }
      await new Promise((resolve) =>
        setTimeout(resolve, capture.settleMs ?? plan.defaults?.settleMs ?? 500),
      );
      const applicationAssets = [
        ...new Map(
          (await Promise.all(pendingAssets))
            .filter(Boolean)
            .map((asset) => [`${asset.type}:${asset.url}`, asset]),
        ).values(),
      ].sort((left, right) => left.url.localeCompare(right.url));
      const applicationDigest = sha256(
        Buffer.from(
          applicationAssets
            .map((asset) => `${asset.type}\t${asset.url}\t${asset.sha256}`)
            .join("\n"),
        ),
      );

      fs.mkdirSync(path.dirname(capture.outputPath), { recursive: true });
      await page.screenshot({
        path: capture.outputPath,
        type: "png",
        fullPage: capture.fullPage === true,
      });
      const bytes = fs.readFileSync(capture.outputPath);
      previous.set(capture.id, {
        id: capture.id,
        kind: capture.kind,
        deck: capture.deck,
        board: capture.board,
        description: capture.description ?? "",
        page: new URL(resolvedUrl).pathname,
        origin: new URL(resolvedUrl).origin,
        viewport: capture.resolvedViewport,
        capturedAt: new Date().toISOString(),
        output: capture.output.replaceAll("\\", "/"),
        bytes: bytes.length,
        sha256: sha256(bytes),
        privacy: capture.privacy,
        evidenceBoundary: capture.evidenceBoundary ?? "",
        applicationDigest,
        applicationAssets,
      });
      console.log(`已保存 ${capture.output}`);
    } finally {
      await page.close();
    }
  }
} finally {
  await browser.close();
}

manifest.captures = [...previous.values()].sort((left, right) => left.id.localeCompare(right.id));
fs.writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
console.log(`截图清单已更新：${path.relative(repoRoot, manifestPath)}`);
