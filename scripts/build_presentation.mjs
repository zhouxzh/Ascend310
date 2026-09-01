import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, "..");
const sourceDir = path.join(repoRoot, "src", "presentation");
const stagingDir = path.join(repoRoot, "tmp", "presentation-build");
const outputDir = path.join(repoRoot, "src", ".vuepress", "public", "presentation");
const outputAssetDir = path.join(outputDir, "assets");
const themePath = path.join(sourceDir, "theme.css");
const repositoryWebRoot = "https://github.com/zhouxzh/Ascend310";
const marpCliPath = path.join(repoRoot, "node_modules", "@marp-team", "marp-cli", "marp-cli.js");

const imagePattern = /!\[([^\]]*)\]\(([^)]+)\)/g;
const htmlImagePattern = /(<img\b[^>]*\bsrc=["'])([^"']+)(["'][^>]*>)/gi;
const markdownLinkPattern = /(\]\()((?:<[^>]+>)|(?:[^)\s]+))([^)]*)\)/g;
const htmlLinkPattern = /(<a\b[^>]*\bhref=["'])([^"']+)(["'])/gi;

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function assetKey(relativePath) {
  const extension = path.extname(relativePath);
  const stem = extension ? relativePath.slice(0, -extension.length) : relativePath;
  const readable = stem
    .replaceAll("\\", "__")
    .replaceAll("/", "__")
    .replace(/[^A-Za-z0-9_.-]/g, "_");
  // Keep non-ASCII filenames readable while preventing collisions such as
  // `流程图.png` and `主线图.png` becoming the same staged filename.
  const digest = createHash("sha256").update(relativePath).digest("hex").slice(0, 10);
  return `${readable}__${digest}${extension}`;
}

function stageImage(target, fileName) {
  const cleanTarget = target.replace(/[?#].*$/, "");
  const absoluteSource = path.resolve(sourceDir, cleanTarget);
  if (!fs.existsSync(absoluteSource) || !fs.statSync(absoluteSource).isFile()) {
    throw new Error(`${fileName}: image does not exist: ${target}`);
  }

  const relativeSource = path.relative(repoRoot, absoluteSource);
  const key = assetKey(relativeSource);
  const destination = path.join(outputAssetDir, key);
  ensureDir(path.dirname(destination));
  fs.copyFileSync(absoluteSource, destination);
  return `assets/${key}`;
}

function repositoryLink(target) {
  if (!target || /^(?:https?:|mailto:|data:|#|assets\/)/i.test(target)) {
    return null;
  }
  const match = target.match(/^([^#]+)(#.*)?$/);
  const pathTarget = (match?.[1] ?? target).replace(/^<|>$/g, "");
  if (!pathTarget.startsWith(".")) {
    return null;
  }
  let absoluteTarget;
  try {
    absoluteTarget = path.resolve(sourceDir, decodeURIComponent(pathTarget));
  } catch {
    return null;
  }
  if (!fs.existsSync(absoluteTarget)) {
    return null;
  }
  const relativeTarget = path.relative(repoRoot, absoluteTarget).replaceAll("\\", "/");
  const kind = fs.statSync(absoluteTarget).isDirectory() ? "tree" : "blob";
  return `${repositoryWebRoot}/${kind}/main/${relativeTarget}${match?.[2] ?? ""}`;
}

function stageMarkdown(fileName) {
  const sourcePath = path.join(sourceDir, fileName);
  let markdown = fs.readFileSync(sourcePath, "utf8");
  markdown = markdown.replace(imagePattern, (whole, alt, rawTarget) => {
    const trimmedTarget = rawTarget.trim();
    const targetMatch = trimmedTarget.match(/^<([^>]+)>(.*)$/s);
    const targetParts = (targetMatch ? targetMatch[2] : trimmedTarget).trim().split(/\s+/);
    const target = targetMatch ? targetMatch[1] : targetParts.shift();
    if (!target || /^(?:https?:|data:|#|assets\/)/i.test(target)) {
      return whole;
    }

    const stagedTarget = stageImage(target, fileName);
    return `![${alt}](${[stagedTarget, ...targetParts].join(" ")})`;
  });

  markdown = markdown.replace(htmlImagePattern, (whole, prefix, target, suffix) => {
    if (!target || /^(?:https?:|data:|#|assets\/)/i.test(target)) {
      return whole;
    }
    return `${prefix}${stageImage(target, fileName)}${suffix}`;
  });

  // Relative source links point at the generated presentation directory. Turn
  // them into stable repository links while retaining the visible source path.
  markdown = markdown.replace(markdownLinkPattern, (whole, prefix, targetToken, suffix) => {
    const target = targetToken.replace(/^<|>$/g, "");
    const rewritten = repositoryLink(target);
    return rewritten ? `${prefix}${rewritten}${suffix})` : whole;
  });
  markdown = markdown.replace(htmlLinkPattern, (whole, prefix, target, suffix) => {
    const rewritten = repositoryLink(target);
    return rewritten ? `${prefix}${rewritten}${suffix}` : whole;
  });

  fs.writeFileSync(path.join(stagingDir, fileName), markdown, "utf8");
}

function runMarp() {
  if (!fs.existsSync(marpCliPath)) {
    throw new Error(`Marp CLI is not installed: ${marpCliPath}`);
  }
  const result = spawnSync(
    process.execPath,
    [
      marpCliPath,
      "-I",
      stagingDir,
      "--theme-set",
      themePath,
      "--theme",
      "ascend310",
      "--html",
      "--output",
      outputDir,
    ],
    { cwd: repoRoot, stdio: "inherit", shell: false },
  );
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

function cleanOutput() {
  ensureDir(outputDir);
  // The output directory is generated and ignored by Git. Remove only its
  // generated HTML/assets children so stale decks cannot survive a rebuild.
  for (const entry of fs.readdirSync(outputDir)) {
    const isGeneratedDeck = /\.html$/i.test(entry);
    if (isGeneratedDeck || entry === "assets") {
      fs.rmSync(path.join(outputDir, entry), { recursive: true, force: true });
    }
  }
}

function main() {
  fs.rmSync(stagingDir, { recursive: true, force: true });
  ensureDir(stagingDir);
  cleanOutput();
  ensureDir(outputAssetDir);

  const markdownFiles = fs
    .readdirSync(sourceDir)
    .filter((name) => /^\d{2}-.*\.md$/i.test(name));
  for (const fileName of markdownFiles) {
    stageMarkdown(fileName);
  }
  runMarp();
  console.log(`Built ${markdownFiles.length} presentation sources into ${outputDir}`);
}

main();
