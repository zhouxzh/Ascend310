/**
 * Host-side browser screenshot capture for tutorial UIs.
 *
 * The boards expose HTTP pages; the screenshot itself is rendered by a browser
 * on the DSH host. We use the installed Edge/Chrome headless CLI instead of
 * adding Playwright to a file-copy plugin.
 *
 * @module dsh-plugin-ascend310b-debug/capture
 */

import { existsSync, mkdirSync, mkdtempSync, rmSync, statSync } from 'node:fs';
import { dirname, basename, join } from 'node:path';
import { tmpdir } from 'node:os';

import { execFileCapture } from './shell.js';

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
		return {
			url: parsed.toString(),
			path: output,
			width,
			height,
			bytes: statSync(output).size,
			durationMs: Date.now() - started,
			browser: basename(browser),
			stderr: result.stderr
		};
	} finally {
		rmSync(userDataDir, { recursive: true, force: true });
	}
}
