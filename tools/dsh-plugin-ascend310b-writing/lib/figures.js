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

import { createHash } from 'node:crypto';
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, extname, join, relative, resolve, sep } from 'node:path';

const IMAGE_EXTENSIONS = new Set(['.png', '.jpg', '.jpeg', '.webp']);
const WARN_IMAGE_BYTES = 1 * 1024 * 1024;
const ERROR_IMAGE_BYTES = 1.5 * 1024 * 1024;
const WARN_IMAGE_WIDTH = 1600;
const WARN_IMAGE_HEIGHT = 1000;
const ERROR_IMAGE_WIDTH = 2400;
const ERROR_IMAGE_HEIGHT = 1500;
const DEFAULT_GRAPHVIZ_DPI = 96;
const IMAGE_BASELINE = 'docs/image-audit-baseline.json';
// A Graphviz label can hold several lines separated by `\l`; the box is only
// cramped when one line is long, so the limit applies per line.
const LONG_DOT_LABEL = 44;
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
	if (
		buffer.length >= 30 &&
		buffer.toString('ascii', 0, 4) === 'RIFF' &&
		buffer.toString('ascii', 8, 12) === 'WEBP'
	) {
		const chunk = buffer.toString('ascii', 12, 16);
		if (chunk === 'VP8X') {
			return {
				width: 1 + buffer.readUIntLE(24, 3),
				height: 1 + buffer.readUIntLE(27, 3)
			};
		}
		if (chunk === 'VP8L' && buffer[20] === 0x2f) {
			const bits = buffer.readUInt32LE(21);
			return {
				width: 1 + (bits & 0x3fff),
				height: 1 + ((bits >>> 14) & 0x3fff)
			};
		}
		if (chunk === 'VP8 ' && buffer[23] === 0x9d && buffer[24] === 0x01 && buffer[25] === 0x2a) {
			return {
				width: buffer.readUInt16LE(26) & 0x3fff,
				height: buffer.readUInt16LE(28) & 0x3fff
			};
		}
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
	const baseline = new Map();
	try {
		const payload = JSON.parse(readFileSync(join(root, IMAGE_BASELINE), 'utf8'));
		for (const entry of payload.entries ?? []) {
			if (typeof entry.rule === 'string' && typeof entry.file === 'string') {
				baseline.set(`${entry.rule}\0${entry.file.replaceAll('\\', '/')}`, false);
			}
		}
	} catch {
		// A missing or malformed baseline must not hide new findings.
	}

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
	const imageHashes = new Map();
	for (const file of imageFiles) {
		const rel = relative(root, file).split(sep).join('/');
		let bytes = 0;
		try {
			bytes = statSync(file).size;
			const hash = createHash('sha256').update(readFileSync(file)).digest('hex');
			const group = imageHashes.get(hash) ?? [];
			group.push(rel);
			imageHashes.set(hash, group);
		} catch {
			continue;
		}
		if (bytes > ERROR_IMAGE_BYTES) {
			heavy += 1;
			findings.push({
				rule: 'image-too-heavy',
				severity: 'error',
				file: rel,
				line: 1,
				message: `图片 ${(bytes / 1024 / 1024).toFixed(2)} MB，超过 ${(ERROR_IMAGE_BYTES / 1024 / 1024).toFixed(1)} MB 硬上限；先压缩或拆图再引用`
			});
		} else if (bytes > WARN_IMAGE_BYTES) {
			heavy += 1;
			findings.push({
				rule: 'image-too-heavy',
				severity: 'warning',
				file: rel,
				line: 1,
				message: `图片 ${(bytes / 1024 / 1024).toFixed(2)} MB，超过 ${(WARN_IMAGE_BYTES / 1024 / 1024).toFixed(1)} MB；应压缩后再引用`
			});
		}
		const size = imageSize(file);
		if (size !== null) {
			if (size.width > ERROR_IMAGE_WIDTH || size.height > ERROR_IMAGE_HEIGHT) {
				findings.push({
					rule: 'image-too-wide',
					severity: 'error',
					file: rel,
					line: 1,
					message: `图片 ${size.width}×${size.height} 超过 ${ERROR_IMAGE_WIDTH}×${ERROR_IMAGE_HEIGHT} 硬上限；必须拆图或重新生成`
				});
			} else if (size.width > WARN_IMAGE_WIDTH || size.height > WARN_IMAGE_HEIGHT) {
				findings.push({
					rule: 'image-too-wide',
					severity: 'warning',
					file: rel,
					line: 1,
					message: `图片 ${size.width}×${size.height} 超过教材目标 ${WARN_IMAGE_WIDTH}×${WARN_IMAGE_HEIGHT}；网页显示时会明显缩小`
				});
			}
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
	for (const group of imageHashes.values()) {
		if (group.length < 2) continue;
		findings.push({
			rule: 'image-duplicate',
			severity: 'warning',
			file: group[0],
			line: 1,
			message: `图片字节完全相同：${group.join(', ')}；有不同板端/状态标签时不能当作独立证据`
		});
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
		const sizeMatch = /\bsize\s*=\s*"([0-9.]+)\s*,\s*([0-9.]+)(?:\s*!)?\s*"/u.exec(text);
		if (sizeMatch === null) {
			findings.push({
				rule: 'dot-size-missing',
				severity: 'warning',
				file: rel,
				line: 1,
				message: 'DOT 没有显式 graph size；生成结果不可按教材/课件画布验收'
			});
		} else {
			const dpiMatch = /\bdpi\s*=\s*([0-9]+)/u.exec(text);
			const dpi = dpiMatch === null ? DEFAULT_GRAPHVIZ_DPI : Number(dpiMatch[1]);
			const width = Math.round(Number(sizeMatch[1]) * dpi);
			const height = Math.round(Number(sizeMatch[2]) * dpi);
			if (width > ERROR_IMAGE_WIDTH || height > ERROR_IMAGE_HEIGHT) {
				findings.push({
					rule: 'dot-canvas-too-large',
					severity: 'error',
					file: rel,
					line: 1,
					message: `DOT 画布约 ${width}×${height}px，超过 ${ERROR_IMAGE_WIDTH}×${ERROR_IMAGE_HEIGHT} 硬上限`
				});
			} else if (width > WARN_IMAGE_WIDTH || height > WARN_IMAGE_HEIGHT) {
				findings.push({
					rule: 'dot-canvas-too-large',
					severity: 'warning',
					file: rel,
					line: 1,
					message: `DOT 画布约 ${width}×${height}px，超过教材目标 ${WARN_IMAGE_WIDTH}×${WARN_IMAGE_HEIGHT}`
				});
			}
		}
		// Only rendered labels need a CJK font. Comments routinely contain
		// Chinese explanations and must not trigger the font rule.
		const rendered = text
			.replace(/\/\*[\s\S]*?\*\//gu, ' ')
			.replace(/\/\/[^\n]*/gu, ' ')
			.replace(/^\s*#[^\n]*/gmu, ' ');
		if (CJK.test(rendered) && !CJK_FONTS.test(text)) {
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

	for (const finding of findings) {
		const key = `${finding.rule}\0${finding.file.replaceAll('\\', '/')}`;
		if (finding.severity !== 'error' || !baseline.has(key)) continue;
		baseline.set(key, true);
		finding.severity = 'warning';
		finding.message += `；已列入 ${IMAGE_BASELINE} 历史基线，修复前仍为 warning`;
	}
	for (const [key, matched] of baseline) {
		if (matched) continue;
		const [rule, file] = key.split('\0');
		findings.push({
			rule: 'image-baseline-stale',
			severity: 'info',
			file,
			line: 1,
			message: `${IMAGE_BASELINE} 中的 ${rule} 记录已不再命中；修复后请删除该基线项`
		});
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
