/**
 * Browser layout check for VuePress pages.
 *
 * This complements the Markdown/image audit: it opens the actual local page,
 * checks for page-level horizontal scrolling, finds images/SVGs/tables that
 * escape the viewport, and measures how far source images are downscaled.
 * Desktop image scaling above 2.5x is a warning; above 3x is a hard failure.
 * Mobile downscaling is reported as a warning only. It does not replace human
 * visual review.
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
		await page.evaluate(async () => {
			const content = document.querySelector('.theme-hope-content') || document.querySelector('[vp-content]') || document.querySelector('main') || document;
			const images = [...content.querySelectorAll('img')];
			for (const image of images) image.loading = 'eager';
			await Promise.allSettled(images.map((image) => image.decode()));
		});
		const result = await page.evaluate(() => {
			const root = document.documentElement;
			const hard = [];
			const warnings = [];
			const content = document.querySelector('.theme-hope-content') || document.querySelector('[vp-content]') || document.querySelector('main') || document;
			for (const image of content.querySelectorAll('img')) {
				const rect = image.getBoundingClientRect();
				if (image.naturalWidth <= 0 || rect.width <= 0) continue;
				const scale = image.naturalWidth / rect.width;
				const name = new URL(image.currentSrc || image.src, location.href).pathname.split('/').pop();
				const detail = {
					type: scale > 3 ? 'image-overscaled-severe' : 'image-overscaled',
					element: 'img',
					source: name,
					naturalWidth: image.naturalWidth,
					renderedWidth: Math.round(rect.width),
					scale: Number(scale.toFixed(2))
				};
				if (scale > 3 && innerWidth >= 1024) hard.push(detail);
				else if (scale > 2.5) warnings.push(detail);
				if (rect.height > innerHeight * 1.8) {
					warnings.push({
						type: 'image-too-tall',
						element: 'img',
						source: name,
						renderedHeight: Math.round(rect.height),
						viewportHeight: innerHeight
					});
				}
			}
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

const issueText = (issue) => {
	if (issue.source) {
		if (issue.scale !== undefined) {
			return `${issue.source} 原图 ${issue.naturalWidth}px，显示 ${issue.renderedWidth}px，缩小 ${issue.scale}×`;
		}
		if (issue.renderedHeight !== undefined) {
			return `${issue.source} 显示高度 ${issue.renderedHeight}px，视口 ${issue.viewportHeight}px`;
		}
		return issue.source;
	}
	if (issue.clientWidth !== undefined) return `${issue.element} ${issue.clientWidth}..${issue.scrollWidth}`;
	return `${issue.element} ${issue.left ?? ''}..${issue.right ?? ''}`;
};

if (jsonMode) {
	console.log(JSON.stringify({ ok: hardFailures.length === 0, results, hardFailures }, null, 2));
} else {
	for (const result of results) {
		console.log(`页面：${result.url}`);
		console.log(`  viewport ${result.viewport.width}x${result.viewport.height}: document ${result.document.scrollWidth}/${result.document.clientWidth}`);
		for (const issue of result.hard) console.error(`  x ${issue.type} ${issueText(issue)}`);
		for (const issue of result.warnings) console.warn(`  ! ${issue.type} ${issueText(issue)}`);
	}
	console.log(hardFailures.length === 0 ? '页面排版检查通过。' : `页面排版失败：${hardFailures.length} 个硬问题。`);
}
process.exitCode = hardFailures.length === 0 ? 0 : 1;
