import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";

import { presentationCatalog } from "../src/presentation/catalog.mjs";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, "..");
const sourceDir = path.join(repoRoot, "src", "presentation");
const stagingDir = path.join(repoRoot, "tmp", "presentation-build");
const stagingAssetDir = path.join(stagingDir, "assets");
const outputDir = path.join(repoRoot, "src", ".vuepress", "public", "presentation");
const generatedOutputDir = path.join(repoRoot, "tmp", "presentation-output");
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

function stageImage(target, sourceName) {
  const cleanTarget = target.replace(/[?#].*$/, "");
  const sourceBaseDir = path.dirname(path.join(sourceDir, sourceName));
  const absoluteSource = path.resolve(sourceBaseDir, cleanTarget);
  if (!fs.existsSync(absoluteSource) || !fs.statSync(absoluteSource).isFile()) {
    throw new Error(`${sourceName}: image does not exist: ${target}`);
  }

  const relativeSource = path.relative(repoRoot, absoluteSource);
  const key = assetKey(relativeSource);
  const destination = path.join(stagingAssetDir, key);
  ensureDir(path.dirname(destination));
  fs.copyFileSync(absoluteSource, destination);
  return `assets/${key}`;
}

function repositoryLink(target, sourceName) {
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
    const sourceBaseDir = path.dirname(path.join(sourceDir, sourceName));
    absoluteTarget = path.resolve(sourceBaseDir, decodeURIComponent(pathTarget));
  } catch (error) {
    throw new Error(`${sourceName}: invalid relative link ${target}: ${error.message}`);
  }
  if (!fs.existsSync(absoluteTarget)) {
    throw new Error(`${sourceName}: relative link target does not exist: ${target}`);
  }
  const relativeTarget = path.relative(repoRoot, absoluteTarget).replaceAll("\\", "/");
  const kind = fs.statSync(absoluteTarget).isDirectory() ? "tree" : "blob";
  return `${repositoryWebRoot}/${kind}/main/${relativeTarget}${match?.[2] ?? ""}`;
}

function stageMarkdown(entry) {
  const sourcePath = path.join(sourceDir, entry.source);
  if (!fs.existsSync(sourcePath)) {
    throw new Error(`${entry.id}: presentation source does not exist: ${entry.source}`);
  }

  let markdown = fs.readFileSync(sourcePath, "utf8");
  markdown = markdown.replace(imagePattern, (whole, alt, rawTarget) => {
    const trimmedTarget = rawTarget.trim();
    const targetMatch = trimmedTarget.match(/^<([^>]+)>(.*)$/s);
    const targetParts = (targetMatch ? targetMatch[2] : trimmedTarget).trim().split(/\s+/);
    const target = targetMatch ? targetMatch[1] : targetParts.shift();
    if (!target || /^(?:https?:|data:|#|assets\/)/i.test(target)) {
      return whole;
    }

    const stagedTarget = stageImage(target, entry.source);
    return `![${alt}](${[stagedTarget, ...targetParts].join(" ")})`;
  });

  markdown = markdown.replace(htmlImagePattern, (whole, prefix, target, suffix) => {
    if (!target || /^(?:https?:|data:|#|assets\/)/i.test(target)) {
      return whole;
    }
    return `${prefix}${stageImage(target, entry.source)}${suffix}`;
  });

  // Relative source links point at the generated presentation directory. Turn
  // them into stable repository links while retaining the visible source path.
  markdown = markdown.replace(markdownLinkPattern, (whole, prefix, targetToken, suffix) => {
    const target = targetToken.replace(/^<|>$/g, "");
    const rewritten = repositoryLink(target, entry.source);
    return rewritten ? `${prefix}${rewritten}${suffix})` : whole;
  });
  markdown = markdown.replace(htmlLinkPattern, (whole, prefix, target, suffix) => {
    const rewritten = repositoryLink(target, entry.source);
    return rewritten ? `${prefix}${rewritten}${suffix}` : whole;
  });

  fs.writeFileSync(path.join(stagingDir, `${entry.slug}.md`), markdown, "utf8");
}

function runMarp(targetOutputDir) {
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
      targetOutputDir,
    ],
    { cwd: repoRoot, stdio: "inherit", shell: false },
  );
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error(`Marp CLI exited with status ${result.status ?? 1}`);
  }
}

function publishOutput() {
  // The dev server can hold the presentation directory open on Windows, which
  // makes directory renames fail with EPERM. The complete temporary build is
  // still created before this point, so copying it over the live directory keeps
  // the important guarantee: a Marp failure never deletes the last good build.
  fs.mkdirSync(outputDir, { recursive: true });
  fs.cpSync(generatedOutputDir, outputDir, { recursive: true, force: true });

  const generatedHtml = new Set(
    fs.readdirSync(generatedOutputDir).filter((name) => name.endsWith(".html")),
  );
  for (const name of fs.readdirSync(outputDir)) {
    if (name.endsWith(".html") && !generatedHtml.has(name)) {
      fs.rmSync(path.join(outputDir, name), { force: true });
    }
  }
  fs.rmSync(generatedOutputDir, { recursive: true, force: true });
}

function selectedPresentations() {
  const onlyArgument = process.argv.find((argument) => argument.startsWith("--only="));
  if (!onlyArgument) {
    return presentationCatalog.filter((entry) => entry.build);
  }

  const requested = new Set(
    onlyArgument
      .slice("--only=".length)
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean),
  );
  const known = new Set(presentationCatalog.map((entry) => entry.slug));
  const unknown = [...requested].filter((slug) => !known.has(slug));
  if (unknown.length > 0) {
    throw new Error(`Unknown presentation slug(s): ${unknown.join(", ")}`);
  }
  return presentationCatalog.filter((entry) => entry.build && requested.has(entry.slug));
}

function validatePresentations(entries) {
  const ids = new Set();
  const slugs = new Set();
  for (const entry of entries) {
    if (!entry.id || ids.has(entry.id)) {
      throw new Error(`Presentation catalog has a missing or duplicate id: ${entry.id}`);
    }
    if (!entry.slug || slugs.has(entry.slug)) {
      throw new Error(`Presentation catalog has a missing or duplicate slug: ${entry.slug}`);
    }
    ids.add(entry.id);
    slugs.add(entry.slug);
  }
}

function main() {
  const markdownFiles = selectedPresentations();
  validatePresentations(markdownFiles);
  if (markdownFiles.length === 0) {
    throw new Error("No presentation sources selected.");
  }

  fs.rmSync(stagingDir, { recursive: true, force: true });
  ensureDir(stagingDir);
  ensureDir(stagingAssetDir);

  // Stage and validate every source and image before deleting the last good
  // generated decks. A missing source must not turn into a blank presentation
  // directory.
  for (const entry of markdownFiles) {
    stageMarkdown(entry);
  }

  fs.rmSync(generatedOutputDir, { recursive: true, force: true });
  ensureDir(generatedOutputDir);
  if (
    process.argv.some((argument) => argument.startsWith("--only=")) &&
    fs.existsSync(outputDir)
  ) {
    fs.cpSync(outputDir, generatedOutputDir, { recursive: true, force: true });
  }
  fs.cpSync(stagingAssetDir, path.join(generatedOutputDir, "assets"), {
    recursive: true,
    force: true,
  });
  runMarp(generatedOutputDir);
  publishOutput();
  console.log(
    `Built ${markdownFiles.length} presentation sources into ${outputDir}`,
  );
}

main();
