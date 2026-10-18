/**
 * Audit the generated textbook site (`src/.vuepress/dist`), not just its source.
 *
 * The manuscript audit answers "is the Markdown correct"; the render audit
 * answers "did the PDF print it". Neither sees what a reader actually opens in
 * a browser: a page that VuePress never emitted, a link that points at a page
 * which was not generated, an asset with a stale hash, or a Markdown marker that
 * survived into the HTML because the plugin did not run. Those failures are
 * invisible in the sources, so this module reads the built site the same way a
 * reader would.
 *
 * @module dsh-plugin-ascend310b-writing/site
 */

import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, join, relative, sep } from 'node:path';

import { listPartFiles } from './audit-core.js';

const SKIP_DIRS = new Set(['node_modules']);

/**
 * Walk a directory and return every file path relative to it.
 *
 * @param {string} directory - the absolute directory.
 * @param {string} [prefix] - the accumulated relative prefix.
 * @param {string[]} [out] - the accumulator.
 * @returns {string[]} Relative file paths with `/`.
 */
function walk(directory, prefix = '', out = []) {
	let entries;
	try {
		entries = readdirSync(directory, { withFileTypes: true });
	} catch {
		return out;
	}
	for (const entry of entries) {
		const rel = prefix === '' ? entry.name : `${prefix}/${entry.name}`;
		if (entry.isDirectory()) {
			if (!SKIP_DIRS.has(entry.name)) walk(join(directory, entry.name), rel, out);
		} else if (entry.isFile()) {
			out.push(rel);
		}
	}
	return out;
}

/** Read the site base path from the VuePress config, defaulting to `/`. */
function readBase(root) {
	try {
		const config = readFileSync(join(root, 'src/.vuepress/config.ts'), 'utf8');
		const match = /base:\s*["']([^"']+)["']/u.exec(config);
		if (match !== null) return match[1].endsWith('/') ? match[1] : `${match[1]}/`;
	} catch {
		// Fall through to the root base.
	}
	return '/';
}

/** Strip tags so a page that only contains navigation is detected as empty. */
function textLength(html) {
	return html
		.replace(/<script\b[\s\S]*?<\/script>/giu, ' ')
		.replace(/<style\b[\s\S]*?<\/style>/giu, ' ')
		.replace(/<[^>]+>/gu, ' ')
		.replace(/\s+/gu, ' ')
		.trim().length;
}

/**
 * Audit the built site against the manuscript sources.
 *
 * @param {object} options - inputs.
 * @param {string} options.root - repository root.
 * @param {object} options.settings - resolved layout.
 * @returns {{ findings: Array<object>, available: boolean, pages: number, assets: number }} The result.
 */
export function collectSiteFindings({ root, settings }) {
	const distIndex = settings.site?.distIndex ?? 'src/.vuepress/dist/index.html';
	const dist = dirname(join(root, distIndex));
	const findings = [];
	if (!existsSync(join(dist, 'index.html'))) {
		return {
			findings: [
				{
					rule: 'site-not-built',
					severity: 'error',
					file: relative(root, dist).split(sep).join('/'),
					line: 1,
					message: '站点产物不存在；先运行 pnpm run docs:build，再审计网页教材'
				}
			],
			available: false,
			pages: 0,
			assets: 0
		};
	}

	const files = walk(dist);
	const htmlFiles = files.filter((file) => file.endsWith('.html'));
	const fileSet = new Set(files);
	const base = readBase(root);
	const pageCache = new Map();
	const readPage = (rel) => {
		if (!pageCache.has(rel)) {
			try {
				pageCache.set(rel, readFileSync(join(dist, rel), 'utf8'));
			} catch {
				pageCache.set(rel, '');
			}
		}
		return pageCache.get(rel);
	};

	// --- expected pages -----------------------------------------------------
	for (const part of settings.parts) {
		if (part.files !== undefined || part.key === 'presentation') continue;
		for (const { file } of listPartFiles(root, part)) {
			// `listPartFiles` returns absolute paths; the site drops `src/` and
			// maps README.md to index.html.
			const repoRel = relative(root, file).split(sep).join('/');
			const pageRel = repoRel
				.replace(/^src\//u, '')
				.replace(/README\.md$/u, 'index.html')
				.replace(/\.md$/u, '.html');
			if (fileSet.has(pageRel)) continue;
			findings.push({
				rule: 'site-page-missing',
				severity: 'error',
				file: repoRel,
				line: 1,
				message: `网页产物缺少对应页面：${pageRel}（重新构建站点）`
			});
		}
	}

	// --- links, assets, fragments ------------------------------------------
	let broken = 0;
	let missingAssets = 0;
	for (const htmlRel of htmlFiles) {
		const html = readPage(htmlRel);
		for (const match of html.matchAll(/\b(href|src)="([^"]+)"/gu)) {
			const [, attribute, rawTarget] = match;
			if (!rawTarget.startsWith(base)) continue;
			const target = decodeURIComponent(rawTarget.slice(base.length).split('?')[0]);
			const [pathPart, fragment] = target.split('#');
			const cleaned = pathPart === '' ? 'index.html' : pathPart;
			const resolved = cleaned.endsWith('/') || cleaned === '' ? `${cleaned}index.html` : cleaned;
			const exists = fileSet.has(resolved);
			if (!exists) {
				const rule = attribute === 'src' ? 'site-asset-missing' : 'site-link-broken';
				if (rule === 'site-asset-missing') missingAssets += 1;
				else broken += 1;
				findings.push({
					rule,
					severity: 'error',
					file: relative(root, join(dist, htmlRel)).split(sep).join('/'),
					line: 1,
					message: `${attribute} 指向不存在的产物：${rawTarget}`
				});
				continue;
			}
			if (fragment && resolved.endsWith('.html')) {
				const targetHtml = readPage(resolved);
				const present =
					targetHtml.includes(`id="${fragment}"`) ||
					targetHtml.includes(`id='${fragment}'`) ||
					targetHtml.includes(`name="${fragment}"`);
				if (!present) {
					broken += 1;
					findings.push({
						rule: 'site-fragment-missing',
						severity: 'error',
						file: relative(root, join(dist, htmlRel)).split(sep).join('/'),
						line: 1,
						message: `链接锚点 ${rawTarget} 在目标页中不存在`
					});
				}
			}
		}
	}

	// --- rendered content ---------------------------------------------------
	for (const htmlRel of htmlFiles) {
		// 404 and the short author page are navigation-only by design.
		if (htmlRel === '404.html' || htmlRel === 'portfolio.html') continue;
		const html = readPage(htmlRel);
		const repoFile = relative(root, join(dist, htmlRel)).split(sep).join('/');
		if (!/<h1\b/iu.test(html) || textLength(html) < 200) {
			findings.push({
				rule: 'site-empty-page',
				severity: 'warning',
				file: repoFile,
				line: 1,
				message: '页面没有 h1 或正文过短，可能只渲染了导航骨架'
			});
		}
		// Code samples legitimately contain `{{ ... }}` and `{#...}`; only the
		// running text is checked for Markdown that failed to render.
		const prose = html.replace(/<pre\b[\s\S]*?<\/pre>/giu, ' ').replace(/<code\b[\s\S]*?<\/code>/giu, ' ');
		for (const marker of ['{#src-', ':::']) {
			if (!prose.includes(marker)) continue;
			findings.push({
				rule: 'site-unrendered',
				severity: 'error',
				file: repoFile,
				line: 1,
				message: `正文里残留未渲染标记 ${marker}`
			});
			break;
		}
	}

	// --- delivery metadata --------------------------------------------------
	for (const meta of ['sitemap.xml', 'robots.txt']) {
		if (fileSet.has(meta)) continue;
		findings.push({
			rule: 'site-sitemap-missing',
			severity: 'warning',
			file: relative(root, dist).split(sep).join('/'),
			line: 1,
			message: `站点缺少 ${meta}`
		});
	}

	findings.sort((left, right) => left.file.localeCompare(right.file) || left.rule.localeCompare(right.rule));
	return {
		findings,
		available: true,
		pages: htmlFiles.length,
		assets: files.length - htmlFiles.length,
		broken,
		missingAssets
	};
}

/**
 * Summarise a site audit in one line.
 *
 * @param {object} site - the result of {@link collectSiteFindings}.
 * @returns {string} The summary.
 */
export function describeSite(site) {
	if (!site.available) return '网页教材：尚未构建。';
	return `网页教材：${site.pages} 页、${site.assets} 个资源（断链 ${site.broken}、缺失资源 ${site.missingAssets}）。`;
}
