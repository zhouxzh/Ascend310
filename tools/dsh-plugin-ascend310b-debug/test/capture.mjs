/**
 * Browser screenshot smoke test.
 *
 * Starts a local HTTP page and captures it with the same headless Edge/Chrome
 * path used by `ascend_ui_capture`. If no browser is installed, the test is
 * skipped rather than failing a headless package checkout.
 *
 * Usage: node test/capture.mjs
 */

import { createServer } from 'node:http';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { captureScreenshot, findBrowser } from '../lib/capture.js';

const browser = findBrowser();
if (browser === null) {
	console.log('capture test skipped: no Edge/Chrome executable');
	process.exit(0);
}

const server = createServer((_request, response) => {
	response.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
	response.end('<!doctype html><html><body style="margin:0;background:#123;color:white;font:32px sans-serif"><h1>310B CAPTURE TEST</h1></body></html>');
});

await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const address = server.address();
const dir = mkdtempSync(join(tmpdir(), 'dsh-ascend-capture-test-'));
const output = join(dir, 'capture.png');
try {
	const result = await captureScreenshot({
		url: `http://127.0.0.1:${address.port}/`,
		output,
		width: 800,
		height: 600,
		waitMs: 250,
		browserPath: browser,
		timeoutMs: 30000
	});
	const png = readFileSync(output);
	if (!existsSync(output) || png.length < 1000) throw new Error(`screenshot too small: ${png.length}`);
	if (png.subarray(1, 4).toString('ascii') !== 'PNG') throw new Error('output is not a PNG');
	if (result.width !== 800 || result.height !== 600) throw new Error('capture dimensions mismatch');
	console.log('capture test passed');
	console.log(`  browser : ${result.browser}`);
	console.log(`  bytes   : ${result.bytes}`);
	console.log(`  time    : ${result.durationMs} ms`);
} finally {
	server.close();
	rmSync(dir, { recursive: true, force: true });
}
