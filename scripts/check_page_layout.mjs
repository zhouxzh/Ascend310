/**
 * Browser layout check for VuePress pages.
 *
 * This complements the Markdown/image audit: it opens the actual local page,
 * checks for page-level horizontal scrolling, and finds images/SVGs/tables
 * that escape the viewport. It does not replace human visual review.
 *
 * Usage:
 *   node scripts/check_page_layout.mjs --url <url> [--viewports 1440x1000,390x844] [--json]
 */

import { existsSync } from 'node:fs';
import puppeteer from 'puppeteer';

const argv = process.argv.slice(2);
const value = (name, fallback = '') => {
	const index = argv.indexOf(name);
	return index >= 0 && argv[index + 1] ? argv[index + 1] : fallback;
};
const jsonMode = argv.includes('--json');
const url = value('--url');
if (!url) {
	console.error('usage: node scripts/check_page_layout.mjs --url <local page URL> [--viewports 1440x1000,390x844] [--json]');
	process.exit(2);
}
const viewports = value('--viewports', '1440x1000,390x844')
	.split(',')
	.map((item) => item.trim())
	.filter(Boolean)
	.map((item) => {
		const match = /^(\d+)x(\d+)$/u.exec(item);
		if (!match) throw new Error(`invalid viewport: ${item}`);
		return { width: Number(match[1]), height: Number(match[2]) };
	});

const chromeCandidates = [
	process.env.PUPPETEER_EXECUTABLE_PATH,
	'C:/Program Files/Google/Chrome/Application/chrome.exe',
	'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
	'/usr/bin/google-chrome',
	'/usr/bin/chromium'
].filter(Boolean);
const executablePath = chromeCandidates.find((path) => existsSync(path));
if (!executablePath && process.platform === 'win32') {
	console.error('Chrome/Edge executable not found; set PUPPETEER_EXECUTABLE_PATH');
	process.exit(2);
}

const browser = await puppeteer.launch({
	headless: true,
	...(executablePath ? { executablePath } : {}),
	args: ['--no-sandbox', '--disable-setuid-sandbox']
});
const results = [];
try {
	for (const viewport of viewports) {
		const page = await browser.newPage();
		await page.setViewport({ ...viewport, deviceScaleFactor: 1 });
		await page.goto(url, { waitUntil: 'networkidle0', timeout: 30000 });
		await page.evaluate(() => document.fonts?.ready);
		const result = await page.evaluate(() => {
			const root = document.documentElement;
			const hard = [];
			const warnings = [];
			const elements = [...document.querySelectorAll('img, svg, pre, table')];
			for (const element of elements) {
				const rect = element.getBoundingClientRect();
				const label = element.tagName.toLowerCase();
				if (rect.right > root.clientWidth + 2 || rect.left < -2) {
					hard.push({
						type: 'element-outside-viewport',
						element: label,
						className: element.className?.baseVal ?? element.className ?? '',
						left: Math.round(rect.left),
						right: Math.round(rect.right),
						viewportWidth: root.clientWidth
					});
				}
				if ((label === 'pre' || label === 'table') && element.scrollWidth > element.clientWidth + 2) {
					warnings.push({
						type: 'internal-horizontal-scroll',
						element: label,
						className: element.className?.baseVal ?? element.className ?? '',
						clientWidth: element.clientWidth,
						scrollWidth: element.scrollWidth
					});
				}
			}
			return {
				url: location.href,
				viewport: { width: innerWidth, height: innerHeight },
				document: {
					clientWidth: root.clientWidth,
					scrollWidth: root.scrollWidth,
					horizontalOverflow: root.scrollWidth > root.clientWidth + 2
				},
				hard,
				warnings
			};
		});
		results.push(result);
		await page.close();
	}
} finally {
	await browser.close();
}

const hardFailures = results.flatMap((result) =>
	result.document.horizontalOverflow
		? [{ type: 'page-horizontal-overflow', viewport: result.viewport }]
		: []
).concat(results.flatMap((result) => result.hard.map((issue) => ({ ...issue, viewport: result.viewport }))));

if (jsonMode) {
	console.log(JSON.stringify({ ok: hardFailures.length === 0, results, hardFailures }, null, 2));
} else {
	for (const result of results) {
		console.log(`页面：${result.url}`);
		console.log(`  viewport ${result.viewport.width}x${result.viewport.height}: document ${result.document.scrollWidth}/${result.document.clientWidth}`);
		for (const issue of result.hard) console.error(`  x ${issue.type} ${issue.element} ${issue.left}..${issue.right}`);
		for (const issue of result.warnings) console.warn(`  ! ${issue.type} ${issue.element} ${issue.clientWidth}..${issue.scrollWidth}`);
	}
	console.log(hardFailures.length === 0 ? '页面排版检查通过。' : `页面排版失败：${hardFailures.length} 个硬问题。`);
}
process.exitCode = hardFailures.length === 0 ? 0 : 1;
