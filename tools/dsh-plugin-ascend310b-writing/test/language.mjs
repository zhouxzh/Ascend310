/**
 * Regression tests for the learner-facing Chinese prose rule.
 *
 * Technical English inside Chinese prose and code is expected. An English-only
 * README, manuscript, learner record or slide deck must not pass merely because
 * it avoids Chinese punctuation, which is the blind spot this test locks down.
 */

import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { auditLearnerRecord, auditRepo } from '../lib/audit-core.js';
import { collectCodeDocs } from '../lib/code-docs.js';
import { auditCoursewareText } from '../lib/courseware.js';
import { analyzeProseLanguage } from '../lib/prose-language.js';

const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};

const english = [
	'This guide explains how the shared chat service starts on the edge board.',
	'The browser sends a request through the gateway and the worker runs the model.',
	'Use the health endpoint to check readiness before sending a chat request.',
	'Keep all evidence for each board separate and record the exact model revision.',
	'For more information, read the deployment guide and the validation report.',
	'For more information, read the deployment guide and the validation report.',
	'For more information, read the deployment guide and the validation report.',
	'For more information, read the deployment guide and the validation report.'
].join(' ');

const pure = analyzeProseLanguage(`# User Guide\n\n${english}\n`);
check(pure.isEnglishDominant, 'pure English prose must be detected');
check(pure.cjkChars === 0 && pure.latinWords > 50, 'language counts must exclude punctuation');

const mixed = analyzeProseLanguage([
	'# 服务启动指南',
	'',
	'浏览器通过网关提交 `POST /v1/chat/completions` 请求，worker 在 NPU 上执行MindSpore模型推理。',
	'',
	'```bash',
	'curl http://127.0.0.1:7861/v1/models',
	'echo this English command is code, not prose',
	'```',
	'',
	'先检查健康状态，再发送一条中文请求。不要把HTTP 200当成模型质量通过。'
].join('\n'));
check(!mixed.isEnglishDominant, 'Chinese prose with technical English and code must pass');

const root = join(tmpdir(), 'tw-language-' + Date.now());
mkdirSync(join(root, 'src', 'book'), { recursive: true });
mkdirSync(join(root, 'tmp', 'records'), { recursive: true });
mkdirSync(join(root, 'samples', 'case1', 'docs'), { recursive: true });
writeFileSync(
	join(root, 'src', 'book', 'chapter1.md'),
	['---', 'title: "第1章：英文正文测试"', '---', '', `# 第1章：英文正文测试`, '', english, ''].join('\n'),
	'utf8'
);
const manuscript = auditRepo({ root, maxFindings: 200 });
check(
	manuscript.findings.some((finding) => finding.rule === 'prose-language' && finding.severity === 'error'),
	'English manuscript body must produce prose-language error'
);

writeFileSync(
	join(root, 'tmp', 'records', 'english.md'),
	['# English Record', '', english, ''].join('\n'),
	'utf8'
);
const record = auditLearnerRecord({ root, file: 'tmp/records/english.md' });
check(
	record.findings.some((finding) => finding.rule === 'prose-language' && finding.severity === 'error'),
	'English learner record must produce prose-language error'
);

writeFileSync(join(root, 'samples', 'case1', 'README.md'), ['# English README', '', english, ''].join('\n'), 'utf8');
writeFileSync(
	join(root, 'samples', 'case1', 'docs', 'README.md'),
	['# 工程文档', '', '本目录保存案例的部署、验证和限制说明。', '', '- [部署说明](01-deployment.md)', ''].join('\n'),
	'utf8'
);
writeFileSync(
	join(root, 'samples', 'case1', 'docs', '01-deployment.md'),
	['# 部署说明', '', '在Ascend310B开发板上运行预检，成功后启动服务并检查健康状态。', ''].join('\n'),
	'utf8'
);
writeFileSync(join(root, 'samples', 'case1', 'app.py'), 'print("ok")\n', 'utf8');
const codeDocs = collectCodeDocs({ root });
check(
	codeDocs.findings.some(
		(finding) =>
			finding.rule === 'prose-language' &&
			finding.file === 'samples/case1/README.md' &&
			finding.severity === 'warning'
	),
	'English sample README must produce a code-docs language warning'
);

const deck = [
	'---',
	'marp: true',
	'theme: ascend310',
	'---',
	'',
	...Array.from({ length: 8 }, (_, index) => [
		`## Slide ${index + 1}`,
		'',
		english,
		'',
		'<!--',
		'讲师备注：说明本页。',
		'-->',
		'',
		'---',
		''
	].flat())
].join('\n');
const courseware = auditCoursewareText({ file: 'src/presentation/01-english.md', text: deck });
check(
	courseware.some((finding) => finding.rule === 'prose-language' && finding.severity === 'error'),
	'English slide deck must produce prose-language error'
);

rmSync(root, { recursive: true, force: true });

if (failures.length > 0) {
	console.error(`language test FAILED (${failures.length} 处):`);
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('language test passed');
console.log('  中文正文、技术英文、代码剔除、样例 README、学习记录与课件均已覆盖');
