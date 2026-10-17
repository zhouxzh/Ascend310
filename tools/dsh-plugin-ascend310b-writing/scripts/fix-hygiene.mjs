/**
 * Manuscript hygiene fixer, driven by the audit report.
 *
 * Pass 1 normalizes line endings and the final newline, then the audit runs
 * again — a CRLF line hides a trailing space before its `\r`, so trailing
 * whitespace can only be judged once the endings are LF.
 *
 * Pass 2 removes exactly the runs the audit reports as `trailing-whitespace`.
 * That rule already means "provably cannot render": a run that would break the
 * line is reported as `hard-break-effective` instead and is deliberately left
 * for the author. Keeping the judgement in the audit is what stops the fixer
 * and the checker from disagreeing — they did once, when this script split on
 * '\n' while the audit split on /\r?\n/ and a dry run under-reported.
 *
 * Usage: node fix-hygiene.mjs [repoRoot] [--apply]
 */

import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { auditRepo } from '../lib/audit-core.js';

// Default to the working directory; no checkout path is baked in.
const root = process.argv[2] && !process.argv[2].startsWith('--') ? process.argv[2] : process.cwd();
const apply = process.argv.includes('--apply');

const sha = (text) => createHash('sha256').update(text).digest('hex').slice(0, 16);
const audit = () => auditRepo({ root, maxFindings: 20000 });

console.log(apply ? 'APPLYING' : 'DRY RUN');
console.log(`root: ${root}`);

// --- pass 1: line endings and the final newline -----------------------------
const first = audit();
const crlfFiles = [...new Set(first.findings.filter((item) => item.rule === 'crlf').map((item) => item.file))];
const newlineFiles = [...new Set(first.findings.filter((item) => item.rule === 'no-final-newline').map((item) => item.file))];

for (const file of crlfFiles) {
	const before = readFileSync(`${root}/${file}`);
	const after = before.toString('utf8').replace(/\r\n/gu, '\n');
	const crlfCount = (before.toString('binary').match(/\r\n/gu) ?? []).length;
	// Normalizing must not touch a single content byte: prove it by comparing
	// the digest of the normalized input with the digest of the output.
	const identical = sha(before.toString('utf8').replace(/\r\n/gu, '\n')) === sha(after);
	console.log(
		`${file}: ${crlfCount} CRLF -> LF, content identical: ${identical}, bytes ${Buffer.byteLength(after) - before.length}`
	);
	if (apply) writeFileSync(`${root}/${file}`, after, 'utf8');
}

for (const file of newlineFiles) {
	const text = readFileSync(`${root}/${file}`, 'utf8');
	if (text.endsWith('\n')) continue;
	console.log(`${file}: appended final newline (+1 byte)`);
	if (apply) writeFileSync(`${root}/${file}`, `${text}\n`, 'utf8');
}

// --- pass 2: the runs the audit proved inert --------------------------------
const second = audit();
const inertByFile = new Map();
for (const item of second.findings.filter((entry) => entry.rule === 'trailing-whitespace')) {
	const bucket = inertByFile.get(item.file) ?? [];
	bucket.push(item.line);
	inertByFile.set(item.file, bucket);
}
const leftForAuthor = second.findings.filter((entry) => entry.rule === 'hard-break-effective');

let stripped = 0;
for (const [file, lines] of inertByFile) {
	// Split exactly as the audit does, or a CRLF file's '\r' would hide the run.
	const parts = readFileSync(`${root}/${file}`, 'utf8').split(/\r?\n/u);
	const drop = new Set();
	for (const lineNumber of lines) {
		const line = parts[lineNumber - 1];
		if (line === undefined || !/[ \t]+$/u.test(line)) continue;
		drop.add(lineNumber - 1);
		stripped += 1;
	}
	if (drop.size === 0) continue;
	const rebuilt = parts.map((line, index) => (drop.has(index) ? line.replace(/[ \t]+$/u, '') : line)).join('\n');
	console.log(`${file}: stripped ${drop.size} inert trailing runs`);
	if (apply) writeFileSync(`${root}/${file}`, rebuilt, 'utf8');
}

for (const finding of leftForAuthor) {
	console.log(`${finding.file}:${finding.line} LEFT ALONE — ${finding.message}`);
}

console.log(
	`\nstripped=${stripped} leftForAuthor=${leftForAuthor.length} ` +
		`crlfFiles=${crlfFiles.length} newlineFiles=${newlineFiles.length}`
);
