/**
 * Code-docs coverage test.
 *
 * This checks the live repository layout, not a synthetic string: every sample
 * directory is inspected for README/docs/code, and the case index is checked
 * against the files it names. The result is intentionally allowed to contain
 * warnings because some samples still need detailed engineering docs.
 */

import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { collectCodeDocs } from '../lib/code-docs.js';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const report = collectCodeDocs({ root });
const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};

check(report.summary.samples > 0, '至少应发现一个 samples/chapter|case 目录');
check(report.summary.readmes > 0, '应发现样例 README');
check(report.summary.docsFiles > 0, '应发现样例 docs Markdown');
check(report.entries.some((entry) => entry.sample === 'samples/case1'), '应覆盖 case1');
check(report.entries.some((entry) => entry.sample === 'samples/case4'), '应覆盖 case4');
check(report.entries.some((entry) => entry.codeFiles > 0), '应统计代码文件');
check(report.text.includes('代码文档覆盖'), '报告应包含代码文档标题');
check(Array.isArray(report.findings), 'findings 必须是数组');

if (failures.length > 0) {
	console.error(`code-docs test FAILED (${failures.length} 处):`);
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('code-docs test passed');
console.log(`  sample dirs : ${report.summary.samples}`);
console.log(`  readmes     : ${report.summary.readmes}`);
console.log(`  with docs   : ${report.summary.withDocs}`);
console.log(`  code files  : ${report.summary.codeFiles}`);
console.log(`  docs files  : ${report.summary.docsFiles}`);
console.log(`  sync issues : ${report.summary.findings}`);
