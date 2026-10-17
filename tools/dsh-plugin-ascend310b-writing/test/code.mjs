/**
 * Manuscript ↔ code cross-check.
 *
 * The resolution rules are the whole point, so the fixture pins each branch:
 * a token that resolves at the repository root, one that resolves only relative
 * to the case's own sample directory, one that resolves only as a nested suffix,
 * a generic `scripts/…` path in a text with no sample directory (must be left
 * alone), a template placeholder, a gitignored runtime output, and a path that
 * is genuinely gone. The drift half needs real history, so the fixture makes two
 * commits with distinct dates.
 *
 * Usage: node test/code.mjs
 */

import { execFileSync } from 'node:child_process';
import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { collectCodeDrift, collectCodeFindings, collectDriftPairs, sampleDirFor } from '../lib/code.js';
import { resolveSettings } from '../lib/settings.js';

const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};

const root = join(tmpdir(), `tw-code-${Date.now()}`);
const write = (relativePath, text) => {
	const full = join(root, relativePath);
	mkdirSync(join(full, '..'), { recursive: true });
	writeFileSync(full, text, 'utf8');
};
const git = (args, env = {}) =>
	execFileSync('git', args, {
		cwd: root,
		stdio: ['ignore', 'pipe', 'pipe'],
		env: { ...process.env, ...env }
	});

mkdirSync(root, { recursive: true });
git(['init', '-q']);
git(['config', 'user.email', 'test@example.com']);
git(['config', 'user.name', 'test']);

write('.gitignore', 'samples/case1/outputs/\n');
write('src/experiment/README.md', '# 案例索引\n');
// case1 is a manuscript part with a sample directory; the tokens below cover
// every resolution branch.
write(
	'src/experiment/case1.md',
	[
		'---',
		'title: "案例 1：测试"',
		'---',
		'',
		'# 案例 1：测试',
		'',
		'仓库根路径 `samples/case1/scripts/run.py`。',
		'样例目录相对路径 `scripts/nested/tool.py`。',
		'嵌套后缀 `scripts/deep.py`。',
		'运行时产物 `samples/case1/outputs/report.json`。',
		'模板占位 `samples/caseN/README.md`。',
		'真正失效的 `samples/case1/gone.py`。',
		''
	].join('\n')
);
// An appendix has no sample directory: a generic `scripts/x.sh` must not be judged.
write(
	'src/appendix/appendix1.md',
	[
		'---',
		'title: "附录 1：测试"',
		'---',
		'',
		'# 附录 1：测试',
		'',
		'通用示例命令 `scripts/generic.sh` 不属于仓库样例。',
		''
	].join('\n')
);
write('samples/case1/scripts/run.py', 'print(1)\n');
write('samples/case1/scripts/nested/tool.py', 'print(2)\n');
write('samples/case1/SSDLite/scripts/deep.py', 'print(3)\n');
// Deliberately absent: a runtime output is exactly what is not in the repository.
mkdirSync(join(root, 'samples/case1/outputs'), { recursive: true });

const settings = resolveSettings({ root });
check(sampleDirFor(root, 'src/experiment/case1.md') === 'samples/case1', 'the case sample directory must be found');
check(sampleDirFor(root, 'src/appendix/appendix1.md') === null, 'an appendix has no sample directory by default');

const report = collectCodeFindings({ root, settings });
const rules = report.findings.map((finding) => finding.rule);
check(report.findings.every((finding) => finding.file.startsWith('src/')), 'findings point at the manuscript, not the sample');
const missing = report.findings.filter((finding) => finding.rule === 'sample-path-missing');
check(missing.length === 1, `exactly one genuinely missing path expected, got ${missing.length}: ${missing.map((f) => f.message).join(' | ')}`);
check(
	missing[0]?.message.includes('samples/case1/gone.py'),
	'the missing finding must name the gone file'
);
check(
	report.findings.some((finding) => finding.rule === 'sample-path-generated' && finding.severity === 'info'),
	'a reference into a gitignored path must be information, not an error'
);
check(
	!report.findings.some((finding) => finding.message.includes('outputs/report.json') && finding.severity === 'error'),
	'a gitignored runtime output must not be an error'
);
check(
	!report.findings.some((finding) => finding.message.includes('generic.sh')),
	'a generic scripts/ path in a text without a sample directory must be left alone'
);
check(
	!report.findings.some((finding) => finding.message.includes('caseN')),
	'a template placeholder must be skipped'
);
check(report.manuscripts >= 2, 'both manuscripts must be scanned');

// --- drift ------------------------------------------------------------------
git(['add', '.']);
git(['commit', '-q', '-m', 'docs first'], {
	GIT_AUTHOR_DATE: '2026-01-01T00:00:00+0000',
	GIT_COMMITTER_DATE: '2026-01-01T00:00:00+0000'
});
// The sample changes after the chapter that describes it.
write('samples/case1/scripts/run.py', 'print("changed")\n');
git(['add', '.']);
git(['commit', '-q', '-m', 'code later'], {
	GIT_AUTHOR_DATE: '2026-02-01T00:00:00+0000',
	GIT_COMMITTER_DATE: '2026-02-01T00:00:00+0000'
});

const pairs = collectDriftPairs({ root, settings });
check(pairs.length >= 1, `expected at least one manuscript/code pair, got ${pairs.length}`);
const drift = collectCodeDrift({ root, pairs });
check(
	drift.some((finding) => finding.rule === 'doc-code-drift' && finding.file === 'src/experiment/case1.md'),
	`code changed after the chapter must be reported, got: ${drift.map((f) => `${f.rule}@${f.file}`).join(',')}`
);
check(
	drift.every((finding) => finding.severity === 'warning'),
	'drift is a warning: the prose may still be correct'
);

// git keeps read-only files in .git, which Windows will not delete first try.
try {
	rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
} catch {
	// A leftover temp directory is harmless; the assertions already ran.
}

if (failures.length > 0) {
	console.error('code test FAILED:');
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('code test passed');
console.log('  解析：仓库根 / 样例目录 / 嵌套后缀 / 通用 scripts 跳过 / 占位符跳过 / gitignore 产物降级为 info');
console.log('  漂移：代码在正文之后改过会报 warning');
