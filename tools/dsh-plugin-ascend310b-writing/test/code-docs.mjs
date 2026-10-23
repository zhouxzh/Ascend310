/**
 * Code-docs coverage test.
 *
 * This checks the live repository layout, not a synthetic string: every sample
 * directory is inspected for README/docs/code, and the case index is checked
 * against the files it names. The result is intentionally allowed to contain
 * warnings because some samples still need detailed engineering docs.
 */

import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
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

const fixtureRoot = join(tmpdir(), `tw-code-docs-numbering-${Date.now()}`);
mkdirSync(join(fixtureRoot, 'samples', 'case1', 'docs'), { recursive: true });
writeFileSync(join(fixtureRoot, 'samples', 'case1', 'README.md'), '# Case 1\n\n## 1. 使用\n', 'utf8');
writeFileSync(join(fixtureRoot, 'samples', 'case1', 'app.py'), 'print("ok")\n', 'utf8');
writeFileSync(join(fixtureRoot, 'samples', 'case1', 'docs', 'README.md'), '# Docs\n', 'utf8');
writeFileSync(
	join(fixtureRoot, 'samples', 'case1', 'docs', '01-architecture.md'),
	'# 架构\n\n## 1. 组件\n\n### 1.1 数据流\n',
	'utf8'
);
const numberingReport = collectCodeDocs({ root: fixtureRoot });
const manualDocs = numberingReport.findings.filter(
	(finding) => finding.rule === 'code-docs-manual-heading-number'
);
check(manualDocs.length === 2, `README/docs 手写编号应各汇总一条，实际 ${manualDocs.length}`);
check(manualDocs.every((finding) => finding.severity === 'error'), 'manual docs numbering must block the audit');
const docsHeading = manualDocs.find((finding) => finding.file.endsWith('01-architecture.md'));
check(docsHeading?.message.includes('2 个 H2-H6'), `docs 编号汇总数量不正确：${docsHeading?.message}`);
check(
	manualDocs.some((finding) => finding.file.endsWith('README.md') && finding.message.includes('1 个 H2-H6')),
	'README 的手写编号也必须被检查'
);
rmSync(fixtureRoot, { recursive: true, force: true });

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
