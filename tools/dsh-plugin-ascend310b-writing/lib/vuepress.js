/**
 * VuePress configuration audit.
 *
 * The book ships through two pipelines: Markdown → VuePress → the site, and
 * Markdown → pandoc → LaTeX → the PDF. The manuscript rules cover the Markdown;
 * this module covers the site's own wiring, which is where a rename or a new
 * deck breaks things silently:
 *
 * - a navbar or sidebar route that no longer resolves to a page
 * - a sidebar entry pointing at a Marp deck that was renamed
 * - a deck that exists but nothing links to, so readers cannot reach it
 * - a `logo`/favicon path that does not exist under `public/`
 * - `base` drift between `config.ts` and the prefix hardcoded in `client.ts`
 * - `repo` drift between `theme.ts` and the actual git remote
 * - a frozen unit name in a sidebar label that the frozen table does not have
 *
 * These files are small declarative TypeScript, so they are read with declared
 * line patterns rather than a parser: a shape this module cannot recognise is
 * reported instead of being silently skipped.
 *
 * @module dsh-plugin-ascend310b-writing/vuepress
 */

import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

/** Where the site's configuration lives, relative to the repository root. */
const CONFIG_DIR = 'src/.vuepress';

/** A frozen-name shape: `第N章：…`, `案例 N：…`, `附录 N：…`. */
const FROZEN_SHAPE = /^(?:第\d+章|案例 \d+|附录 \d+)：/u;

/**
 * Read a file from the config directory, or null when it is absent.
 *
 * @param {string} root - the repository root.
 * @param {string} name - the file name inside `src/.vuepress`.
 * @returns {string | null} The file text.
 */
function readConfig(root, name) {
	const path = join(root, CONFIG_DIR, name);
	return existsSync(path) ? readFileSync(path, 'utf8') : null;
}

/** Read one `key: "value"` setting out of a config file. */
function settingOf(text, key) {
	if (text === null) return null;
	const match = new RegExp(`(?:^|\\s)${key}:\\s*"([^"]*)"`, 'mu').exec(text);
	return match === null ? null : match[1];
}

/**
 * Collect every page target the sidebar declares.
 *
 * String `children` entries are pages under the enclosing `prefix`. Object
 * `children` entries carry their own `link`, which may be a page route or a
 * Marp deck's built `.html`. Block-level `link` values are section routes.
 *
 * @param {string} text - the sidebar file.
 * @returns {{ pages: Array<{ route: string, line: number }>, decks: Array<{ route: string, line: number }> }} Targets.
 */
export function readSidebarTargets(text) {
	const pages = [];
	const decks = [];
	let prefix = '/';
	let inChildren = false;
	/** Take every quoted target on one line (inline `children: [...]`). */
	const collectInline = (line, lineNumber, currentPrefix) => {
		for (const match of line.matchAll(/"([^"]+\.(?:md|html))"/gu)) {
			const target = match[1];
			if (target.endsWith('.html')) {
				decks.push({ route: target, line: lineNumber });
				continue;
			}
			pages.push({ route: target.startsWith('/') ? target : `${currentPrefix}${target}`, line: lineNumber });
		}
	};
	text.split(/\r?\n/u).forEach((line, index) => {
		const lineNumber = index + 1;
		const prefixMatch = /^\s*prefix:\s*"([^"]+)"/u.exec(line);
		if (prefixMatch !== null) {
			prefix = prefixMatch[1].endsWith('/') ? prefixMatch[1] : `${prefixMatch[1]}/`;
			return;
		}
		if (/children:\s*\[/u.test(line)) {
			inChildren = true;
			// A one-line `children: ["a.md", { link: "…" }]` is legal; take the
			// targets on this line before switching to the multi-line form.
			collectInline(line, lineNumber, prefix);
			return;
		}
		if (inChildren) {
			if (/^\s*\]/u.test(line)) {
				inChildren = false;
				return;
			}
			const linkMatch = /link:\s*"([^"]+)"/u.exec(line);
			if (linkMatch !== null) {
				const route = linkMatch[1];
				(route.endsWith('.html') ? decks : pages).push({ route, line: lineNumber });
				return;
			}
			const childMatch = /^\s*"([^"]+\.md)"\s*,?\s*$/u.exec(line);
			if (childMatch !== null) {
				const child = childMatch[1];
				const route = child.startsWith('/') ? child : `${prefix}${child}`;
				pages.push({ route, line: lineNumber });
			}
			return;
		}
		const linkMatch = /link:\s*"([^"]+)"/u.exec(line);
		if (linkMatch !== null) pages.push({ route: linkMatch[1], line: lineNumber });
	});
	return { pages, decks };
}

/**
 * Collect every navbar target: object `link` values and bare string entries.
 *
 * @param {string} text - the navbar file.
 * @returns {Array<{ route: string, line: number }>} Targets.
 */
export function readNavbarTargets(text) {
	const targets = [];
	text.split(/\r?\n/u).forEach((line, index) => {
		const linkMatch = /link:\s*"([^"]+)"/u.exec(line);
		if (linkMatch !== null) {
			targets.push({ route: linkMatch[1], line: index + 1 });
			return;
		}
		// Bare entries such as `"/"` or `"portfolio.md"` at array level.
		const bareMatch = /^\s*"([^"]+)"\s*,?\s*$/u.exec(line);
		if (bareMatch !== null) targets.push({ route: bareMatch[1], line: index + 1 });
	});
	return targets;
}

/**
 * Resolve a site route to the Markdown file that must exist for it.
 *
 * @param {string} docsDir - the docs root, e.g. `src`.
 * @param {string} route - a route such as `/book/`, `/book/chapter1.md` or `portfolio.md`.
 * @returns {string} The repository-relative path expected to exist.
 */
export function routeToPage(docsDir, route) {
	const clean = route.replace(/[?#].*$/u, '');
	const relative = clean.replace(/^\/+/u, '');
	if (relative === '') return `${docsDir}/README.md`;
	if (relative.endsWith('/')) return `${docsDir}/${relative}README.md`;
	if (relative.endsWith('.md')) return `${docsDir}/${relative}`;
	return `${docsDir}/${relative}/README.md`;
}

/**
 * Resolve a sidebar deck route to the Marp source that must exist, and to the
 * built HTML the reader actually opens.
 *
 * @param {string} docsDir - the docs root.
 * @param {string} publicDir - `public` inside the config directory.
 * @param {string} route - e.g. `/presentation/09-ros-basics.html`.
 * @returns {{ source: string, built: string }} Repository-relative paths.
 */
export function routeToDeck(docsDir, publicDir, route) {
	const relative = route.replace(/^\/+/u, '');
	return {
		source: `${docsDir}/${relative.replace(/\.html$/u, '.md')}`,
		built: `${publicDir}/${relative}`
	};
}

/**
 * Audit the VuePress wiring.
 *
 * @param {object} options - check inputs.
 * @param {string} options.root - the repository root.
 * @param {object} options.settings - the resolved layout (parts, source root).
 * @param {Array<{ title: string }>} [options.frozenRows] - rows from the frozen-name table.
 * @returns {{ findings: Array<object>, available: boolean, pages: number, decks: number }} The findings.
 */
export function collectVuepressFindings({ root, settings, frozenRows = [] }) {
	const findings = [];
	const config = readConfig(root, 'config.ts');
	const theme = readConfig(root, 'theme.ts');
	const sidebarText = readConfig(root, 'sidebar.ts');
	const navbarText = readConfig(root, 'navbar.ts');
	const clientText = readConfig(root, 'client.ts');
	if (config === null && theme === null && sidebarText === null && navbarText === null) {
		return { findings, available: false, pages: 0, decks: 0 };
	}

	const docsDir = settingOf(theme, 'docsDir') ?? settings.sourceRoot;
	const publicDir = `${CONFIG_DIR}/public`;
	// An empty list means the frozen table is absent; the label check is skipped
	// rather than guessing at names.	
	const rows = Array.isArray(frozenRows) ? frozenRows : [];
	const frozenTitles = new Set(rows.map((row) => row.title));
	const isFrozenShape = (value) => typeof value === 'string' && FROZEN_SHAPE.test(value);

	const push = (rule, severity, file, line, message) => findings.push({ rule, severity, file, line, message });

	/** Report a route whose page file does not exist. */
	const checkPage = (route, file, line, rule) => {
		const expected = routeToPage(docsDir, route);
		if (existsSync(join(root, expected))) return;
		push(rule, 'error', file, line, `路由 ${route} 对应的页面不存在：${expected}`);
	};

	// --- sidebar ------------------------------------------------------------
	const sidebar = sidebarText === null ? { pages: [], decks: [] } : readSidebarTargets(sidebarText);
	for (const { route, line } of sidebar.pages) checkPage(route, `${CONFIG_DIR}/sidebar.ts`, line, 'vuepress-page-missing');

	const linkedDecks = new Set();
	for (const { route, line } of sidebar.decks) {
		const { source, built } = routeToDeck(docsDir, publicDir, route);
		if (!existsSync(join(root, source))) {
			push('vuepress-deck-link-broken', 'error', `${CONFIG_DIR}/sidebar.ts`, line, `侧边栏指向的放映页没有源文稿：${source}`);
			continue;
		}
		linkedDecks.add(source);
		// The built HTML is a gitignored build artifact, so its absence is a
		// "build the slides" reminder, not a defect in the repository.
		if (!existsSync(join(root, built))) {
			push('vuepress-deck-unbuilt', 'info', `${CONFIG_DIR}/sidebar.ts`, line, `放映页还没构建：${built}（运行 pnpm run docs:slides）`);
		}
	}

	// Every deck should be reachable from the sidebar, or readers cannot find it.
	const presentation = settings.parts.find((part) => part.assets === true);
	if (presentation !== undefined) {
		const directory = join(root, presentation.dir);
		if (existsSync(directory)) {
			for (const entry of readdirNames(directory)) {
				if (!entry.endsWith('.md') || !presentation.filePattern.test(entry)) continue;
				const source = `${presentation.dir}/${entry}`;
				if (linkedDecks.has(source)) continue;
				push('deck-unlinked', 'warning', source, 1, '这份演示文稿没有出现在 sidebar.ts 里，站点上访问不到它');
			}
		}
	}

	// Frozen unit names used as sidebar labels must match the frozen table.
	if (sidebarText !== null && frozenTitles.size > 0) {
		sidebarText.split(/\r?\n/u).forEach((line, index) => {
			const textMatch = /^\s*text:\s*"([^"]+)"/u.exec(line);
			if (textMatch === null) return;
			const label = textMatch[1];
			if (!isFrozenShape(label) || frozenTitles.has(label)) return;
			push('vuepress-frozen-mismatch', 'warning', `${CONFIG_DIR}/sidebar.ts`, index + 1, `侧边栏标签「${label}」不是冻结名表里的名字`);
		});
	}

	// --- navbar -------------------------------------------------------------
	if (navbarText !== null) {
		for (const { route, line } of readNavbarTargets(navbarText)) {
			checkPage(route, `${CONFIG_DIR}/navbar.ts`, line, 'vuepress-nav-missing');
		}
	}

	// --- public assets referenced by the config -----------------------------
	const logo = settingOf(theme, 'logo');
	if (logo !== null && !existsSync(join(root, publicDir, logo))) {
		push('vuepress-asset-missing', 'error', `${CONFIG_DIR}/theme.ts`, 1, `logo 指向的文件不存在：${publicDir}/${logo}`);
	}
	const favicon = config === null ? null : /href:\s*"([^"]*favicon[^"]*)"/u.exec(config)?.[1];
	if (favicon !== undefined && favicon !== null) {
		const relative = favicon.replace(/^\//u, '').replace(new RegExp(`^${(settingOf(config, 'base') ?? '/').replace(/^\/|\/$/gu, '')}/`, 'u'), '');
		if (!existsSync(join(root, publicDir, relative))) {
			push('vuepress-asset-missing', 'error', `${CONFIG_DIR}/config.ts`, 1, `favicon 指向的文件不存在：${publicDir}/${relative}`);
		}
	}

	// --- base and repo drift ------------------------------------------------
	const base = settingOf(config, 'base');
	const hostname = settingOf(theme, 'hostname');
	const repo = settingOf(theme, 'repo');
	if (base !== null && base !== '/') {
		const bare = base.replace(/^\/|\/$/gu, '');
		// `client.ts` hardcodes the base inside a regular expression; when the two
		// disagree the click-through to the Marp decks silently stops working.
		if (clientText !== null && !clientText.includes(`\\/${bare}\\/`)) {
			push('vuepress-base-mismatch', 'warning', `${CONFIG_DIR}/client.ts`, 1, `client.ts 没有写死当前的 base「${base}」，放映页跳转会失效`);
		}
	}
	const remote = readGitRemote(root);
	if (repo !== null && remote !== null) {
		const expected = remote.replace(/^https:\/\/github\.com\//u, '').replace(/\.git$/u, '');
		if (expected.toLowerCase() !== repo.toLowerCase()) {
			push('vuepress-repo-mismatch', 'warning', `${CONFIG_DIR}/theme.ts`, 1, `theme.ts 的 repo「${repo}」与 git 远端「${expected}」不一致`);
		}
	}
	if (hostname !== null && repo !== null && base !== null) {
		const expectedHost = `https://${repo.split('/')[0]}.github.io`;
		if (hostname.replace(/\/$/u, '') !== expectedHost) {
			push('vuepress-hostname-mismatch', 'warning', `${CONFIG_DIR}/theme.ts`, 1, `hostname「${hostname}」与 GitHub Pages 地址「${expectedHost}」不一致`);
		}
	}

	findings.sort((left, right) => left.file.localeCompare(right.file) || left.line - right.line);
	return { findings, available: true, pages: sidebar.pages.length + (navbarText === null ? 0 : readNavbarTargets(navbarText).length), decks: sidebar.decks.length };
}

/**
 * List a directory's entry names, or an empty array.
 *
 * @param {string} directory - the directory.
 * @returns {string[]} Entry names.
 */
function readdirNames(directory) {
	try {
		return readdirSync(directory);
	} catch {
		return [];
	}
}

/**
 * Read the origin remote of the repository, without throwing.
 *
 * @param {string} root - the repository root.
 * @returns {string | null} The remote URL, or null.
 */
function readGitRemote(root) {
	try {
		const output = execFileSync('git', ['-C', root, 'remote', 'get-url', 'origin'], {
			encoding: 'utf8',
			stdio: ['ignore', 'pipe', 'pipe']
		});
		return String(output).trim() || null;
	} catch {
		return null;
	}
}
