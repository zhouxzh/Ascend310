/**
 * Audit the figures that carry the structure of the textbook: raster images and
 * their Graphviz `.dot` sources.
 *
 * Two different failure classes live here.
 *
 * Images are auditing targets for both outputs. A 2 MB screenshot makes the
 * web page slow and the PDF large; an image with no alternative text is
 * invisible to a reader that cannot see it; an image nobody references is
 * dead weight in the repository; and Markdown that points at a missing file
 * renders as a broken box in BOTH the site and the PDF.
 *
 * Structure diagrams are edited as `.dot` and rendered to PNG. That pair can
 * drift: the `.dot` changes and the PNG is not rebuilt, or the `.dot` is
 * deleted while the PNG survives. Graphviz also silently falls back to a font
 * without CJK glyphs, which turns every Chinese label into boxes — a defect
 * that only shows up when the diagram is already in the book.
 *
 * @module dsh-plugin-ascend310b-writing/figures
 */

import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, extname, join, relative, resolve, sep } from 'node:path';

const IMAGE_EXTENSIONS = new Set(['.png', '.jpg', '.jpeg', '.webp']);
const HEAVY_IMAGE_BYTES = 1.5 * 1024 * 1024;
const WIDE_IMAGE_PX = 3200;
const TALL_IMAGE_PX = 2400;
// A Graphviz label can hold several lines separated by `\l`; the box is only
// cramped when one line is long, so the limit applies per line.
const LONG_DOT_LABEL = 36;
const CJK = /[\u4e00-\u9fff]/u;
const CJK_FONTS = /(?:Noto|Source Han|思源|SimHei|SimSun|Microsoft YaHei|WenQuanYi|PingFang|Heiti|黑体)/iu;

/**
 * Read the pixel size of a PNG or JPEG from its header.
 *
 * @param {string} file - absolute path.
 * @returns {{ width: number, height: number } | null} The size, when known.
 */
export function imageSize(file) {
	let buffer;
	try {
		buffer = readFileSync(file);
	} catch {
		return null;
	}
	if (buffer.length > 24 && buffer[0] === 0x89 && buffer.toString('ascii', 1, 4) === 'PNG') {
		return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
	}
	if (buffer.length > 4 && buffer[0] === 0xff && buffer[1] === 0xd8) {
		let offset = 2;
		while (offset + 9 < buffer.length) {
			if (buffer[offset] !== 0xff) {
				offset += 1;
				continue;
			}
			const marker = buffer[offset + 1];
			const length = buffer.readUInt16BE(offset + 2);
			// SOF0..SOF15, excluding DHT(c4), JPG(c8) and DAC(cc).
			if (marker >= 0xc0 && marker <= 0xcf && ![0xc4, 0xc8, 0xcc].includes(marker)) {
				return { height: buffer.readUInt16BE(offset + 5), width: buffer.readUInt16BE(offset + 7) };
			}
			offset += 2 + length;
		}
	}
	return null;
}

/** List files under a directory, skipping generated site trees. */
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
			if (['.vuepress', 'node_modules', '.git'].includes(entry.name)) continue;
			walk(join(directory, entry.name), rel, out);
		} else if (entry.isFile()) {
			out.push({ absolute: join(directory, entry.name), relative: rel });
		}
	}
	return out;
}

/**
 * Collect every Markdown body the figure audit should look at.
 *
 * @param {object} settings - resolved layout.
 * @returns {Array<{ file: string, text: string }>} Manuscripts.
 */
function manuscripts(settings, root) {
	const out = [];
	for (const part of settings.parts) {
		if (part.files !== undefined) continue;
		const directory = join(root, part.dir);
		let names;
		try {
			names = readdirSync(directory);
		} catch {
			continue;
		}
		for (const name of names) {
			if (!name.endsWith('.md')) continue;
			try {
				out.push({ file: `${part.dir}/${name}`, text: readFileSync(join(directory, name), 'utf8') });
			} catch {
				// Unreadable files are surfaced by other checks.
			}
		}
	}
	return out;
}

/**
 * Audit images and structure-diagram sources under the manuscript parts.
 *
 * @param {object} options - inputs.
 * @param {string} options.root - repository root.
 * @param {object} options.settings - resolved layout.
 * @returns {{ findings: Array<object>, images: number, dots: number, heavy: number }} The result.
 */
export function collectFigureFindings({ root, settings }) {
	const docs = manuscripts(settings, root);
	const referenced = new Set();
	const findings = [];

	// --- alternative text and missing targets ------------------------------
	for (const { file, text } of docs) {
		text.split(/\r?\n/u).forEach((line, index) => {
			if (/!\[\s*\]\(/u.test(line)) {
				findings.push({
					rule: 'image-alt-missing',
					severity: 'warning',
					file,
					line: index + 1,
					message: '图片缺少替代文字；网页与 PDF 都需要 alt 才能被朗读和检索'
				});
			}
			for (const match of line.matchAll(/!\[[^\]]*\]\(([^)\s]+)/gu)) {
				const target = match[1].replace(/^<|>$/gu, '');
				if (/^(?:https?:|data:)/iu.test(target)) continue;
				referenced.add(resolve(dirname(join(root, file)), decodeURIComponent(target)).toLowerCase());
			}
		});
	}

	// --- raster figures -----------------------------------------------------
	const imageFiles = [];
	for (const part of settings.parts) {
		if (part.files !== undefined) continue;
		const directory = join(root, part.dir);
		for (const entry of walk(directory)) {
			if (!IMAGE_EXTENSIONS.has(extname(entry.relative).toLowerCase())) continue;
			imageFiles.push(entry.absolute);
		}
	}
	let heavy = 0;
	for (const file of imageFiles) {
		const rel = relative(root, file).split(sep).join('/');
		let bytes = 0;
		try {
			bytes = statSync(file).size;
		} catch {
			continue;
		}
		if (bytes > HEAVY_IMAGE_BYTES) {
			heavy += 1;
			findings.push({
				rule: 'image-too-heavy',
				severity: 'warning',
				file: rel,
				line: 1,
				message: `图片 ${(bytes / 1024 / 1024).toFixed(2)} MB，超过 ${(HEAVY_IMAGE_BYTES / 1024 / 1024).toFixed(1)} MB；网页会变慢、PDF 会变大，先压缩再引用`
			});
		}
		const size = imageSize(file);
		if (size !== null && (size.width > WIDE_IMAGE_PX || size.height > TALL_IMAGE_PX)) {
			findings.push({
				rule: 'image-too-wide',
				severity: 'info',
				file: rel,
				line: 1,
				message: `图片 ${size.width}×${size.height} 超过 ${WIDE_IMAGE_PX}×${TALL_IMAGE_PX}；网页显示尺寸与 PDF 页面都会被缩小`
			});
		}
		if (!referenced.has(resolve(file).toLowerCase())) {
			findings.push({
				rule: 'image-orphan',
				severity: 'info',
				file: rel,
				line: 1,
				message: '没有任何正文引用这张图片'
			});
		}
	}

	// --- Graphviz sources ---------------------------------------------------
	const dotFiles = [];
	for (const part of settings.parts) {
		if (part.files !== undefined) continue;
		const directory = join(root, part.dir);
		for (const entry of walk(directory)) {
			if (entry.relative.toLowerCase().endsWith('.dot')) dotFiles.push(entry.absolute);
		}
	}
	for (const file of dotFiles) {
		const rel = relative(root, file).split(sep).join('/');
		let text;
		try {
			text = readFileSync(file, 'utf8');
		} catch {
			continue;
		}
		const png = file.replace(/\.dot$/iu, '.png');
		if (!existsSync(png)) {
			findings.push({
				rule: 'dot-pair-missing',
				severity: 'error',
				file: rel,
				line: 1,
				message: '结构图源文件没有同名 PNG；正文引用的是 PNG，读者会看到缺图'
			});
		} else {
			try {
				if (statSync(png).mtimeMs < statSync(file).mtimeMs) {
					findings.push({
						rule: 'dot-png-stale',
						severity: 'error',
						file: rel,
						line: 1,
						message: 'PNG 早于 .dot；修改结构图后必须重新渲染'
					});
				}
			} catch {
				// Stat failures are reported elsewhere.
			}
		}
		if (!/^\s*(?:strict\s+)?(?:di)?graph\b/mu.test(text)) {
			findings.push({
				rule: 'dot-invalid',
				severity: 'error',
				file: rel,
				line: 1,
				message: '缺少 graph/digraph 声明，Graphviz 无法解析'
			});
		}
		if (CJK.test(text) && !CJK_FONTS.test(text)) {
			findings.push({
				rule: 'dot-cjk-font',
				severity: 'warning',
				file: rel,
				line: 1,
				message: '含中文但没有指定中文字体（fontname）；渲染时中文可能变成方框'
			});
		}
		for (const match of text.matchAll(/label\s*=\s*"([^"]*)"/gu)) {
			const lines = match[1].split(/\\l|\\n|\n/u).map((line) => line.trim()).filter(Boolean);
			const longest = lines.reduce((max, line) => ([...line].length > [...max].length ? line : max), '');
			if ([...longest].length <= LONG_DOT_LABEL) continue;
			findings.push({
				rule: 'dot-label-too-long',
				severity: 'warning',
				file: rel,
				line: 1,
				message: `节点标签单行 ${[...longest].length} 字，超过 ${LONG_DOT_LABEL} 字；图里会挤成一团：${longest.slice(0, 30)}`
			});
		}
		if (!referenced.has(resolve(png).toLowerCase())) {
			findings.push({
				rule: 'dot-orphan',
				severity: 'info',
				file: rel,
				line: 1,
				message: '没有正文引用这张结构图对应的 PNG'
			});
		}
	}

	findings.sort((left, right) => left.file.localeCompare(right.file) || left.rule.localeCompare(right.rule));
	// Inspectorial rules (orphans, oversized dimensions) can legitimately hit
	// dozens of files at once. Keep the first few as concrete examples and
	// collapse the rest into one line, so the audit stays readable.
	const INFO_CAP = 8;
	const shown = new Map();
	const capped = [];
	for (const finding of findings) {
		if (finding.severity !== 'info') {
			capped.push(finding);
			continue;
		}
		const count = (shown.get(finding.rule) ?? 0) + 1;
		shown.set(finding.rule, count);
		if (count <= INFO_CAP) capped.push(finding);
	}
	for (const [rule, count] of shown) {
		if (count <= INFO_CAP) continue;
		capped.push({
			rule: `${rule}-more`,
			severity: 'info',
			file: 'src',
			line: 1,
			message: `${rule} 另有 ${count - INFO_CAP} 条同类提示（共 ${count} 条）`
		});
	}
	return { findings: capped, images: imageFiles.length, dots: dotFiles.length, heavy };
}

/**
 * Summarise a figure audit in one line.
 *
 * @param {object} figures - the result of {@link collectFigureFindings}.
 * @returns {string} The summary.
 */
export function describeFigures(figures) {
	return `图表：${figures.images} 张图片、${figures.dots} 个 .dot 源（问题 ${figures.findings.length}）。`;
}
