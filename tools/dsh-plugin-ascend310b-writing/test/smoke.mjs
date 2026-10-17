/**
 * Standalone smoke run for the audit engine.
 *
 * Usage: node test/smoke.mjs [repoRoot]
 */

import { auditRepo, progressReport } from '../lib/audit-core.js';

// Default to the working directory; no checkout path is baked in.
const root = process.argv[2] ?? process.cwd();

const audit = auditRepo({ root });
console.log('=== summary ===');
console.log(JSON.stringify(audit.summary, null, 2));

const rules = new Map();
for (const finding of audit.findings) {
	rules.set(finding.rule, (rules.get(finding.rule) ?? 0) + 1);
}
console.log('=== findings by rule ===');
for (const [rule, count] of [...rules].sort((a, b) => b[1] - a[1])) {
	console.log(`${String(count).padStart(5)}  ${rule}`);
}

console.log('=== all non-anchor findings ===');
for (const finding of audit.findings.filter((item) => item.rule !== 'anchor-prefix')) {
	console.log(`${finding.severity[0].toUpperCase()} ${finding.file}:${finding.line} [${finding.rule}] ${finding.message}`);
}

const progress = progressReport({ root });
console.log('=== progress totals ===');
console.log(JSON.stringify(progress.totals, null, 2));
console.log(progress.text.split('\n').slice(0, 30).join('\n'));
