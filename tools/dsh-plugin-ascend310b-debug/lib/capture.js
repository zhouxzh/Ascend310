/**
 * Host-side browser screenshot capture for tutorial UIs.
 *
 * The boards expose HTTP pages; the screenshot itself is rendered by a browser
 * on the DSH host. We use the installed Edge/Chrome headless CLI instead of
 * adding Playwright to a file-copy plugin.
 *
 * @module dsh-plugin-ascend310b-debug/capture
 */

import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, unlinkSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { inflateSync } from 'node:zlib';
import { dirname, basename, join } from 'node:path';
import { tmpdir } from 'node:os';

import { execFileCapture } from './shell.js';

const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

/** Validate a PNG and reject a constant-colour/blank image. */
function inspectPng(path) {
	const data = readFileSync(path);
	if (data.length < 1024 || !data.subarray(0, 8).equals(PNG_SIGNATURE)) {
		throw new Error('截图不是有效的 PNG（文件过小或签名不匹配）。');
	}
	let offset = 8;
	let width = 0;
	let height = 0;
	let bitDepth = 0;
	let colorType = 0;
	const idat = [];
	while (offset + 12 <= data.length) {
		const length = data.readUInt32BE(offset);
		const type = data.toString('ascii', offset + 4, offset + 8);
		const start = offset + 8;
		const end = start + length;
		if (end + 4 > data.length) throw new Error('PNG chunk 越界。');
		if (type === 'IHDR') {
			width = data.readUInt32BE(start);
			height = data.readUInt32BE(start + 4);
			bitDepth = data[start + 8];
			colorType = data[start + 9];
			if (data[start + 12] !== 0) throw new Error('不支持交错 PNG 截图的非空校验。');
		} else if (type === 'IDAT') {
			idat.push(data.subarray(start, end));
		} else if (type === 'IEND') {
			break;
		}
		offset = end + 4;
	}
	if (width < 1 || height < 1 || idat.length === 0) throw new Error('PNG 缺少有效图像数据。');
	if (bitDepth !== 8 || (colorType !== 2 && colorType !== 6)) {
		return { width, height, nonblank: true, sha256: createHash('sha256').update(data).digest('hex') };
	}
	const bytesPerPixel = colorType === 6 ? 4 : 3;
	const rowBytes = width * bytesPerPixel;
	const raw = inflateSync(Buffer.concat(idat));
	let position = 0;
	let previous = Buffer.alloc(rowBytes);
	let first = null;
	let nonblank = false;
	for (let y = 0; y < height; y += 1) {
		const filter = raw[position++];
		const row = Buffer.from(raw.subarray(position, position + rowBytes));
		position += rowBytes;
		for (let x = 0; x < row.length; x += 1) {
			const left = x >= bytesPerPixel ? row[x - bytesPerPixel] : 0;
			const up = previous[x];
			const upperLeft = x >= bytesPerPixel ? previous[x - bytesPerPixel] : 0;
			let reconstructed = row[x];
			if (filter === 1) reconstructed += left;
			else if (filter === 2) reconstructed += up;
			else if (filter === 3) reconstructed += Math.floor((left + up) / 2);
			else if (filter === 4) {
				const p = left + up - upperLeft;
				const pa = Math.abs(p - left);
				const pb = Math.abs(p - up);
				const pc = Math.abs(p - upperLeft);
				reconstructed += pa <= pb && pa <= pc ? left : pb <= pc ? up : upperLeft;
			} else if (filter !== 0) {
				throw new Error(`PNG 使用了未知过滤器 ${filter}。`);
			}
			row[x] = reconstructed & 0xff;
			if (first === null) first = row[x];
			else if (row[x] !== first) nonblank = true;
		}
		previous = row;
	}
	return {
		width,
		height,
		nonblank,
		sha256: createHash('sha256').update(data).digest('hex')
	};
}

const WINDOWS_BROWSER_SUFFIXES = [
	'Microsoft\\Edge\\Application\\msedge.exe',
	'Google\\Chrome\\Application\\chrome.exe'
];

const COMMON_BROWSERS = Object.freeze([
	...(typeof process.env.ProgramFiles === 'string'
		? WINDOWS_BROWSER_SUFFIXES.map((suffix) => join(process.env.ProgramFiles, suffix))
		: []),
	...(typeof process.env['ProgramFiles(x86)'] === 'string'
		? WINDOWS_BROWSER_SUFFIXES.map((suffix) => join(process.env['ProgramFiles(x86)'], suffix))
		: []),
	'/usr/bin/microsoft-edge',
	'/usr/bin/google-chrome',
	'/usr/bin/chromium',
	'/usr/bin/chromium-browser'
]);

/** Return the first configured/installed browser path. */
export function findBrowser(config = {}) {
	const requested = [
		config.browserPath,
		process.env.DSH_ASCEND310B_BROWSER,
		...COMMON_BROWSERS
	].filter((value) => typeof value === 'string' && value.trim() !== '');
	for (const candidate of requested) {
		if (existsSync(candidate)) return candidate;
	}
	return null;
}

/**
 * Capture one HTTP(S) page to a PNG.
 *
 * @param {object} options - capture options.
 * @param {string} options.url - page URL.
 * @param {string} options.output - destination PNG path.
 * @param {number} [options.width] - viewport width.
 * @param {number} [options.height] - viewport height.
 * @param {number} [options.waitMs] - virtual time budget.
 * @param {string} [options.browserPath] - browser executable.
 * @param {number} [options.timeoutMs] - process timeout.
 * @returns {Promise<object>} capture result.
 */
export async function captureScreenshot({
	url,
	output,
	width = 1440,
	height = 900,
	waitMs = 1000,
	browserPath,
	timeoutMs = 30000
}) {
	let parsed;
	try {
		parsed = new URL(url);
	} catch {
		throw new Error('截图 URL 不合法。');
	}
	if (!/^https?:$/.test(parsed.protocol)) throw new Error('截图只接受 HTTP(S) URL。');
	if (parsed.username || parsed.password) throw new Error('截图 URL 不能包含凭据。');
	if (typeof output !== 'string' || output.trim() === '') throw new Error('截图输出路径为空。');
	if (!Number.isInteger(width) || width < 320 || width > 7680) throw new Error('截图宽度必须在 320–7680 之间。');
	if (!Number.isInteger(height) || height < 240 || height > 4320) throw new Error('截图高度必须在 240–4320 之间。');
	if (!Number.isInteger(waitMs) || waitMs < 0 || waitMs > 60000) throw new Error('截图等待时间必须在 0–60000 ms 之间。');

	const browser = findBrowser({ browserPath });
	if (browser === null) {
		throw new Error('未找到 Edge/Chrome。请设置 DSH_ASCEND310B_BROWSER 或配置 browserPath。');
	}
	mkdirSync(dirname(output), { recursive: true });
	const userDataDir = mkdtempSync(join(tmpdir(), 'dsh-ascend-capture-'));
	const started = Date.now();
	try {
		if (existsSync(output)) unlinkSync(output);
		const args = [
			'--headless=new',
			'--disable-gpu',
			'--hide-scrollbars',
			'--no-first-run',
			'--no-default-browser-check',
			'--disable-background-networking',
			`--window-size=${width},${height}`,
			`--virtual-time-budget=${waitMs}`,
			`--user-data-dir=${userDataDir}`,
			`--screenshot=${output}`,
			parsed.toString()
		];
		const result = await execFileCapture({
			file: browser,
			args,
			timeoutMs
		});
		if (result.code !== 0 || result.timedOut === true) {
			throw new Error(`浏览器截图失败（exit ${result.code}）：${result.stderr.trim().split('\n')[0] ?? ''}`);
		}
		if (!existsSync(output)) throw new Error('浏览器返回成功但没有生成截图文件。');
		const png = inspectPng(output);
		if (png.nonblank !== true) throw new Error('浏览器生成了空白 PNG；请检查页面是否实际渲染。');
		return {
			url: parsed.toString(),
			path: output,
			width: png.width,
			height: png.height,
			bytes: statSync(output).size,
			sha256: png.sha256,
			durationMs: Date.now() - started,
			browser: basename(browser),
			stderr: result.stderr
		};
	} finally {
		rmSync(userDataDir, { recursive: true, force: true });
	}
}
