/**
 * The repository layout, in one place and overridable.
 *
 * This plugin knows one book's shape: which directories hold manuscripts, how a
 * numbered file is recognised, where the frozen-name tables and the VuePress
 * sidebar live, and where the converter writes its output. That is *repository*
 * coupling — the plugin's whole purpose — but it does not have to be compiled
 * in: another book can describe its own layout without editing this package.
 *
 * Precedence, lowest to highest: {@link DEFAULT_LAYOUT}, a `.tutorial-writing.json`
 * at the repository root, then the `layout` object from the plugin's own
 * configuration. Arrays and nested objects replace wholesale rather than
 * merging, so a restated `parts` list is exactly the parts the audit will use —
 * no half-merged layouts.
 *
 * Pattern fields are regular-expression *sources* so a repository can restate
 * them in JSON; {@link compileLayout} turns them into `RegExp` objects and
 * rejects a layout that is malformed.
 *
 * @module dsh-plugin-ascend310b-writing/settings
 */

import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

/** The optional layout file a repository may carry at its root. */
export const LAYOUT_FILE = '.tutorial-writing.json';

/**
 * The layout this plugin was built for, written as data.
 *
 * A part lists its numbered manuscripts through `filePattern`. `extras` names
 * part-owned documents that carry no frozen number — the section's own README,
 * which the sidebar reaches through the section `link:` rather than through a
 * child entry.
 */
export const DEFAULT_LAYOUT = {
	/** Where the manuscripts live; also the path `git status` is scoped to. */
	sourceRoot: 'src',
	/** The frozen-name tables the audit cross-checks. */
	agentsFile: 'AGENTS.md',
	/** The VuePress sidebar the coverage rule reads. */
	sidebarFile: 'src/.vuepress/sidebar.ts',
	/** A file that is not numbered but is generated (the book's preface). */
	preface: { source: 'src/book/README.md', generated: 'latex/chapters/preface.tex' },
	/** The converter's output directory per part key. */
	generatedDirs: { book: 'latex/chapters', experiment: 'latex/cases', appendix: 'latex/appendices' },
	/** The build log and PDF the render check reads. */
	latex: { log: 'latex/book.log', pdf: 'latex/book.pdf' },
	parts: [
		{
			key: 'book',
			label: '理论教程',
			dir: 'src/book',
			index: 'src/book/README.md',
			filePattern: '^chapter(\\d+)\\.md$',
			extras: ['README.md'],
			titlePattern: '^第(\\d+)章：\\S',
			titleHint: '第N章：标题（第与数字之间不加空格，用全角冒号）',
			titleInFrontmatter: true
		},
		{
			key: 'experiment',
			label: '实践案例',
			dir: 'src/experiment',
			index: 'src/experiment/README.md',
			filePattern: '^case(\\d+)\\.md$',
			extras: ['README.md'],
			titlePattern: '^案例 (\\d+)：\\S',
			titleHint: '案例 N：标题（案例与数字之间一个半角空格，用全角冒号）',
			titleInFrontmatter: false
		},
		{
			key: 'appendix',
			label: '附录',
			dir: 'src/appendix',
			index: 'src/appendix/README.md',
			filePattern: '^appendix(\\d+)\\.md$',
			extras: ['README.md'],
			titlePattern: '^附录 (\\d+)：\\S',
			titleHint: '附录 N：标题（附录与数字之间一个半角空格，用全角冒号）',
			titleInFrontmatter: false
		},
		{
			key: 'misc',
			label: '仓库级文档',
			dir: 'src',
			// Site-level pages named explicitly: the home page and the author page
			// are frontmatter-driven, reachable from the sidebar root entry and the
			// home `actions`, and own no numbered title.
			files: ['README.md', 'portfolio.md'],
			titleInFrontmatter: true
		}
	]
};

/**
 * Turn a pattern field into a `RegExp`, leaving an already-compiled one alone.
 *
 * @param {string | RegExp | undefined} pattern - the source or the compiled pattern.
 * @param {string} where - a label naming the field, used in the error.
 * @returns {RegExp | undefined} The compiled pattern.
 * @throws {Error} When the source is not a valid regular expression.
 */
function compilePattern(pattern, where) {
	if (pattern === undefined || pattern instanceof RegExp) return pattern;
	if (typeof pattern !== 'string') throw new Error(`布局字段 ${where} 必须是正则字符串`);
	try {
		return new RegExp(pattern, 'u');
	} catch (error) {
		throw new Error(`布局字段 ${where} 不是合法的正则表达式：${error.message}`);
	}
}

/**
 * Validate a merged layout and compile its patterns.
 *
 * @param {object} layout - the merged, still-string-patterned layout.
 * @returns {object} The same layout with `RegExp` patterns.
 * @throws {Error} When a required field is missing or a part is unusable.
 */
export function compileLayout(layout) {
	if (!Array.isArray(layout.parts) || layout.parts.length === 0) {
		throw new Error('布局必须包含至少一个 part');
	}
	const seen = new Set();
	const parts = layout.parts.map((part, index) => {
		const where = `parts[${index}]`;
		if (typeof part?.key !== 'string' || part.key === '') throw new Error(`布局字段 ${where}.key 必须是非空字符串`);
		if (typeof part?.dir !== 'string' || part.dir === '') throw new Error(`布局字段 ${where}.dir 必须是非空字符串`);
		if (seen.has(part.key)) throw new Error(`布局里有两个 part 共用 key "${part.key}"`);
		seen.add(part.key);
		return {
			...part,
			filePattern: compilePattern(part.filePattern, `${where}.filePattern`),
			titlePattern: compilePattern(part.titlePattern, `${where}.titlePattern`)
		};
	});
	return { ...layout, parts };
}

/** The built-in layout, compiled once. */
export const DEFAULT_SETTINGS = compileLayout(DEFAULT_LAYOUT);

/**
 * Read the repository's own layout file, if it has one.
 *
 * @param {string | undefined} root - the repository root.
 * @returns {object | undefined} The parsed layout, or undefined when absent.
 * @throws {Error} When the file exists but is not JSON.
 */
function readLayoutFile(root) {
	if (typeof root !== 'string' || root === '') return undefined;
	const path = join(root, LAYOUT_FILE);
	if (!existsSync(path)) return undefined;
	try {
		return JSON.parse(readFileSync(path, 'utf8'));
	} catch (error) {
		throw new Error(`${LAYOUT_FILE} 不是合法 JSON：${error.message}`);
	}
}

/**
 * Resolve the layout for one repository.
 *
 * @param {object} [options] - inputs.
 * @param {string} [options.root] - the repository root, used to find the layout file.
 * @param {object} [options.layout] - a layout from the plugin's configuration; wins.
 * @returns {object} The compiled settings.
 */
export function resolveSettings({ root, layout } = {}) {
	const layers = [DEFAULT_LAYOUT, readLayoutFile(root), layout].filter(
		(layer) => layer !== undefined && layer !== null
	);
	const merged = layers.reduce((accumulator, layer) => ({ ...accumulator, ...layer }), {});
	return compileLayout(merged);
}
