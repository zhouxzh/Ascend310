/**
 * Focused learner-record audit regression tests.
 *
 * Usage: node test/record-audit.mjs
 */

import { existsSync, mkdirSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { auditLearnerRecord } from '../lib/audit-core.js';

const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};
const rules = (report) => new Set(report.findings.map((finding) => finding.rule));
const expectThrow = (callback, message) => {
	try {
		callback();
		failures.push(message);
	} catch {
		// Expected refusal.
	}
};

const root = join(tmpdir(), `record-audit-${Date.now()}`);
const records = join(root, 'tmp', 'records');
mkdirSync(records, { recursive: true });
mkdirSync(join(root, 'tmp', 'assets'), { recursive: true });
writeFileSync(join(root, 'tmp', 'assets', 'image.png'), 'fixture', 'utf8');
writeFileSync(join(root, 'tmp', 'assets', 'linked.md'), '# Linked\n', 'utf8');

writeFileSync(
	join(records, 'clean.md'),
	[
		'# 学习记录',
		'',
		'## 实验步骤',
		'',
		'![采集结果](../assets/image.png)',
		'',
		'参见[辅助记录](../assets/linked.md)。',
		'',
		'```bash',
		'echo ok',
		'```',
		''
	].join('\n'),
	'utf8'
);
const clean = auditLearnerRecord({ root, file: 'tmp/records/clean.md' });
check(clean.summary.files === 1, `clean record should count one file, got ${clean.summary.files}`);
check(clean.summary.errors === 0 && clean.summary.warnings === 0, `clean record must be clean, got ${clean.text}`);
check(clean.findings.length === 0, 'clean record must return no findings');

writeFileSync(
	join(records, 'spacing.md'),
	[
		'# 学习记录',
		'',
		'使用 Python 3.9运行，共 5 个样本。',
		'`使用 Python 3.9` 保持原样。',
		''
	].join('\n'),
	'utf8'
);
const spacing = auditLearnerRecord({ root, file: 'tmp/records/spacing.md' });
const spacingFindings = spacing.findings.filter((finding) => finding.rule === 'cjk-latin-space');
check(spacingFindings.length === 1, `one aggregated spacing finding expected, got ${spacing.text}`);
check(
	spacingFindings[0]?.line === 3 && spacingFindings[0]?.message.includes('2 处'),
	`record spacing finding must report two occurrences, got: ${spacingFindings[0]?.message}`
);

writeFileSync(
	join(records, 'duplicates.md'),
	[
		'# 学习记录',
		'',
		'## 参考资料',
		'',
		'第一节。',
		'',
		'## 参考资料',
		'',
		'参考资料：',
		'',
		'- 示例。',
		''
	].join('\n'),
	'utf8'
);
const duplicates = auditLearnerRecord({ root, file: 'tmp/records/duplicates.md' });
const duplicateRules = rules(duplicates);
check(duplicateRules.has('duplicate-heading'), 'duplicate record headings must be reported');
check(duplicateRules.has('duplicate-section-label'), 'duplicate record section labels must be reported');

writeFileSync(
	join(records, 'hygiene.md'),
	[
		'# 学习记录',
		'',
		'### 跳级标题',
		'',
		'## 1. 手写编号',
		'',
		'中文,标点  ',
		'',
		'```',
		'echo missing-language',
		'```'
	].join('\r\n'),
	'utf8'
);
const hygiene = auditLearnerRecord({ root, file: 'tmp/records/hygiene.md' });
const hygieneRules = rules(hygiene);
for (const rule of ['heading-jump', 'heading-manual-number', 'cjk-halfwidth-punct', 'trailing-whitespace', 'code-fence-language', 'crlf', 'no-final-newline']) {
	check(hygieneRules.has(rule), `hygiene record must report ${rule}: ${hygiene.text}`);
}
check(hygiene.summary.errors >= 2, 'manual numbering and unlabeled code fence must be errors');
check(hygiene.summary.warnings >= 5, 'hygiene warnings must be counted');

writeFileSync(
	join(records, 'targets.md'),
	[
		'# 目标检查',
		'',
		'![缺图](missing.png)',
		'[缺链接](missing.md)',
		'![越界图](../../../outside.png)',
		'[越界链接](../../../outside.md)',
		''
	].join('\n'),
	'utf8'
);
const targets = auditLearnerRecord({ root, file: 'tmp/records/targets.md' });
const targetRules = rules(targets);
for (const rule of ['image-missing', 'link-missing', 'image-outside-root', 'link-outside-root']) {
	check(targetRules.has(rule), `target record must report ${rule}: ${targets.text}`);
}

writeFileSync(join(records, 'invalid.md'), Buffer.from([0xc3, 0x28]));
const invalid = auditLearnerRecord({ root, file: 'tmp/records/invalid.md' });
check(invalid.summary.errors === 1, `invalid UTF-8 must have one error, got ${invalid.text}`);
check(invalid.findings[0]?.rule === 'utf8-invalid', `invalid UTF-8 must report utf8-invalid, got ${invalid.text}`);

expectThrow(() => auditLearnerRecord({ root, file: '../tmp/records/clean.md' }), 'traversal path must be refused');
expectThrow(() => auditLearnerRecord({ root, file: 'tmp/records/../records/clean.md' }), 'nested traversal path must be refused');
expectThrow(() => auditLearnerRecord({ root, file: 'tmp\\records\\clean.md' }), 'backslash path must be refused');
expectThrow(() => auditLearnerRecord({ root, file: join(root, 'tmp', 'records', 'clean.md') }), 'absolute path must be refused');
expectThrow(() => auditLearnerRecord({ root, file: 'src/book/chapter1.md' }), 'arbitrary Markdown must be refused');

const outside = join(tmpdir(), `record-audit-outside-${Date.now()}`);
mkdirSync(outside, { recursive: true });
writeFileSync(join(outside, 'escaped.md'), '# 外部记录\n', 'utf8');
const escapeLink = join(root, 'tmp', 'escape');
try {
	symlinkSync(outside, escapeLink, process.platform === 'win32' ? 'junction' : 'dir');
	expectThrow(
		() => auditLearnerRecord({ root, file: 'tmp/escape/escaped.md' }),
		'symlink escaping the repository must be refused'
	);
} catch (error) {
	failures.push(`test fixture could not create an escaping directory link: ${error.message}`);
}

// Raw host registration and execution smoke: no UI/client wrapper involved.
const here = dirname(fileURLToPath(import.meta.url));
const plugin = join(here, '..');
const stubRoot = join(plugin, 'node_modules', '@deepseek-ai', 'dsh-tools');
if (existsSync(stubRoot)) {
	failures.push(`raw host smoke refuses to overwrite existing ${stubRoot}`);
} else {
	mkdirSync(stubRoot, { recursive: true });
	writeFileSync(
		join(stubRoot, 'package.json'),
		JSON.stringify({ name: '@deepseek-ai/dsh-tools', version: '0.0.0-stub', type: 'module', main: './index.js' }),
		'utf8'
	);
	writeFileSync(join(stubRoot, 'index.js'), 'export const defineTool = (tool) => tool;\n', 'utf8');
	try {
		const host = await import(`../lib/index.js?record-audit=${Date.now()}`);
		const registered = [];
		host.apply(
			{
				tools: { register(tool) { registered.push(tool); return () => {}; }, get() { return undefined; } },
				get() { return undefined; },
				inject() {},
				effect() {}
			},
			{ repoRoot: root }
		);
		const tool = registered.find((entry) => entry.name === 'ascend310b_writing_record_audit');
		check(tool !== undefined, 'host must register ascend310b_writing_record_audit');
		check(tool?.parameters.file?.required === true, 'record tool file parameter must be required');
		const raw = await tool?.execute({ file: 'tmp/records/clean.md' });
		check(raw?.summary.errors === 0, `raw registered record tool must audit the clean file: ${raw?.text}`);
		check(
			Object.keys(raw ?? {}).sort().join(',') === 'findings,summary,text',
			`raw registered record tool must match its output schema, got ${Object.keys(raw ?? {}).join(',')}`
		);
	} catch (error) {
		failures.push(`raw host registration smoke failed: ${error.stack ?? error.message}`);
	} finally {
		rmSync(join(plugin, 'node_modules'), { recursive: true, force: true });
	}
}

rmSync(root, { recursive: true, force: true });
rmSync(outside, { recursive: true, force: true });

if (failures.length > 0) {
	console.error('record-audit test FAILED:');
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('record-audit test passed');
console.log('  clean record, hygiene rules, target containment, UTF-8 and raw host registration verified');
