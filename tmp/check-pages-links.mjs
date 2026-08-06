import { access, readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { load } from "../node_modules/.pnpm/cheerio@1.1.2/node_modules/cheerio/dist/esm/index.js";

const dist = path.resolve("src/.vuepress/dist");
const base = "/Ascend310/";

async function walk(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) files.push(...await walk(absolute));
    else files.push(absolute);
  }
  return files;
}

async function exists(file) {
  try {
    await access(file);
    return true;
  } catch {
    return false;
  }
}

function splitReference(reference) {
  const withoutQuery = reference.split("?", 1)[0];
  const hashIndex = withoutQuery.indexOf("#");
  if (hashIndex < 0) return { pathname: withoutQuery, fragment: "" };
  return {
    pathname: withoutQuery.slice(0, hashIndex),
    fragment: decodeURIComponent(withoutQuery.slice(hashIndex + 1)),
  };
}

function isExternal(reference) {
  return /^(?:[a-z][a-z\d+.-]*:|\/\/)/i.test(reference);
}

async function resolveTarget(htmlFile, reference) {
  const { pathname, fragment } = splitReference(reference);
  let relative;
  if (!pathname) {
    relative = path.relative(dist, htmlFile);
  } else if (pathname.startsWith(base)) {
    relative = pathname.slice(base.length);
  } else if (pathname.startsWith("/")) {
    relative = pathname.slice(1);
  } else {
    relative = path.join(path.dirname(path.relative(dist, htmlFile)), pathname);
  }

  try {
    relative = decodeURIComponent(relative);
  } catch {
    return { error: "invalid percent encoding" };
  }

  const normalized = path.normalize(relative || "index.html");
  if (normalized.startsWith(`..${path.sep}`) || path.isAbsolute(normalized)) {
    return { error: "escapes dist root" };
  }

  const candidates = [path.join(dist, normalized)];
  if (normalized.endsWith(path.sep) || normalized === ".") {
    candidates.push(path.join(dist, normalized, "index.html"));
  } else if (!path.extname(normalized)) {
    candidates.push(path.join(dist, `${normalized}.html`));
    candidates.push(path.join(dist, normalized, "index.html"));
  }

  const target = candidates.find((candidate) => false) ?? await (async () => {
    for (const candidate of candidates) if (await exists(candidate)) return candidate;
    return undefined;
  })();
  if (!target) return { error: "target missing", candidates };

  if (fragment && path.extname(target).toLowerCase() === ".html") {
    const targetHtml = await readFile(target, "utf8");
    const targetDocument = load(targetHtml);
    const escaped = fragment.replaceAll("\\", "\\\\").replaceAll('"', '\\"');
    if (targetDocument(`[id="${escaped}"]`).length === 0) {
      return { error: `anchor #${fragment} missing`, candidates: [target] };
    }
  }

  return { target };
}

const htmlFiles = (await walk(dist)).filter((file) => file.endsWith(".html"));
const failures = [];
let checked = 0;

for (const htmlFile of htmlFiles) {
  const document = load(await readFile(htmlFile, "utf8"));
  const references = [];
  document("a[href], [src]").each((_, element) => {
    const attribute = element.attribs.href === undefined ? "src" : "href";
    references.push({ attribute, reference: element.attribs[attribute] });
  });
  document("[srcset]").each((_, element) => {
    for (const candidate of element.attribs.srcset.split(",")) {
      references.push({ attribute: "srcset", reference: candidate.trim().split(/\s+/, 1)[0] });
    }
  });

  for (const { attribute, reference } of references) {
    if (!reference || isExternal(reference)) continue;
    checked += 1;
    const result = await resolveTarget(htmlFile, reference);
    if (result.error) {
      failures.push({
        file: path.relative(dist, htmlFile),
        attribute,
        reference,
        error: result.error,
      });
    }
  }
}

console.log(JSON.stringify({ htmlFiles: htmlFiles.length, checked, failures }, null, 2));
if (failures.length > 0) process.exitCode = 1;
