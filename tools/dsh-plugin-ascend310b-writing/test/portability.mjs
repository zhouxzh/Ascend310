/**
 * Portability guard for the plugin package.
 *
 * Two things are checked, and they are the two halves of one rule: a package
 * that claims to be reusable must neither depend on this host nor crash when
 * its optional evidence is absent.
 *
 * 1. No shipped executable file names a host path: a checkout, a user home, the
 *    DSH runtime directory, the application install directory, a profile
 *    layout, or a fixed port. Those belong in the sibling
 *    `dsh-plugin-ascend310b-writing-tools/` directory, which is machine-local by
 *    design, or in a profile's own config layer.
 * 2. With no `AGENTS.md`, no `latex/` tree and no Git repository, every checker
 *    reports what it cannot see instead of throwing.
 *
 * Repository coupling — `src/book`, `sidebar.ts`, the frozen-name tables — is
 * deliberate and not checked here.
 *
 * Usage: node test/portability.mjs
 */

import { mkdirSync, readFileSync, readdirSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

import { auditRepo } from '../lib/audit-core.js';
import {
	collectArtifactFindings,
	collectCommitFindings,
	collectFrozenNameFindings,
	collectRenderFindings,
	evaluatePushGate
} from '../lib/checks.js';

const pluginRoot = join(dirname(fileURLToPath(import.meta.url)), '..');
const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};

// --- 1) no host coupling in the shipped executable surface ------------------
// Patterns are built from strings so this scanner cannot match its own source,
// and this file is skipped anyway: it *is* the pattern dictionary, so its own
// regex literals would always match themselves.
const SCANNER = fileURLToPath(import.meta.url);

/**
 * Paths that are specific to a person or to this DSH installation. These are
 * forbidden everywhere, including tests: they cannot be right on another host.
 */
const MACHINE_PATTERNS = [
	// Any word ending in a letter followed by `:` and a separator would match a
	// bare drive pattern (`prefix:\s*`, `http://`), so require the preceded
	// character to be a non-word one.
	{ name: '用户主目录（Windows）', pattern: new RegExp('(?<![A-Za-z0-9_])[A-Za-z]:[\\\\/]Users[\\\\/]', 'iu') },
	{ name: '用户主目录（Unix/MSYS）', pattern: new RegExp('/(?:c/Users|Users|home)/') },
	{ name: 'DSH 运行时私有目录', pattern: new RegExp('dsh-runtimes') },
	{ name: '应用安装路径', pattern: new RegExp('DeepSeek Harness') },
	{ name: 'profile 目录布局', pattern: new RegExp('profiles[\\\\/](?:desktop|web)') },
	{ name: '硬编码端口', pattern: new RegExp('\\b19[0-9]{3}\\b') }
];

/**
 * A drive path that is not a documented install root for the tool being
 * probed. Runtime code may name `C:\Program Files\Git\…` or `C:\texlive\…`
 * because those are where the software installs; it may not name a checkout.
 *
 * The lookahead tolerates an escaped backslash: in source, `'C:\\texlive'`
 * carries two, so the character after `C:\` is another backslash.
 *
 * @type {{ name: string, pattern: RegExp }}
 */
const CHECKOUT_PATTERN = {
	name: '非软件安装根的盘符路径',
	pattern: new RegExp('(?<![A-Za-z0-9_])[A-Za-z]:[\\\\/](?!\\\\?(?:Program Files|texlive|Windows))', 'u')
};

/** Directories whose code must satisfy the stricter rule. */
const RUNTIME_DIRS = ['lib', 'drivers', 'scripts'];

/** Every executable file the package ships. */
function shippedFiles(dir, out = []) {
	let entries;
	try {
		entries = readdirSync(dir, { withFileTypes: true });
	} catch {
		return out;
	}
	for (const entry of entries) {
		if (entry.name === 'node_modules') continue;
		const full = join(dir, entry.name);
		if (entry.isDirectory()) {
			shippedFiles(full, out);
			continue;
		}
		if (full === SCANNER) continue;
		if (entry.name.endsWith('.js') || entry.name.endsWith('.mjs')) out.push(full);
	}
	return out;
}

const scanned = shippedFiles(pluginRoot);
check(scanned.length >= 10, `expected to scan the shipped executable files, got ${scanned.length}`);

const coupled = [];
for (const file of scanned) {
	const relativePath = relative(pluginRoot, file).split(sep).join('/');
	const strict = RUNTIME_DIRS.some((dir) => relativePath.startsWith(`${dir}/`));
	const lines = readFileSync(file, 'utf8').split(/\r?\n/u);
	lines.forEach((line, index) => {
		// A comment cannot couple anything: the strict rule targets executable
		// path literals, and a doc example such as `D:\a\b` is not one.
		const isComment = /^\s*(?:\*|\/\/|\/\*)/u.test(line);
		const patterns = strict && !isComment ? [...MACHINE_PATTERNS, CHECKOUT_PATTERN] : MACHINE_PATTERNS;
		for (const { name, pattern } of patterns) {
			if (!pattern.test(line)) continue;
			coupled.push(`${relativePath}:${index + 1} [${name}] ${line.trim().slice(0, 80)}`);
		}
	});
}
check(
	coupled.length === 0,
	`shipped code must not name a host path (move it to the -tools directory, or derive it):\n      ${coupled.join('\n      ')}`
);

// --- 2) honest degradation with none of the optional evidence ---------------
const bare = join(tmpdir(), `tw-portable-${Date.now()}`);
mkdirSync(bare, { recursive: true });

const frozen = collectFrozenNameFindings({ root: bare });
check(frozen.available === false, 'a missing AGENTS.md must report unavailable, not throw');
check(frozen.findings.length === 0, 'a missing AGENTS.md must not invent findings');
check(frozen.rows === 0, 'a missing AGENTS.md must report zero frozen-name rows');

const render = collectRenderFindings({ root: bare });
check(render.available === false, 'a missing build log must report unavailable, not throw');
check(render.findings.length === 0, 'a missing build log must not invent findings');
check(render.text.includes('convert-vuepress.sh'), 'a missing build log must say how to produce one');

const artifacts = collectArtifactFindings({ root: bare });
check(artifacts.pairs === 0, 'a missing latex/ tree must report zero source/artifact pairs');
check(artifacts.findings.length === 0, 'a missing latex/ tree must not invent findings');

const commit = collectCommitFindings({ root: bare, rev: 'HEAD' });
check(
	commit.findings.length === 1 && commit.findings[0].rule === 'commit-unreadable',
	'a directory that is not a Git repository must produce one commit-unreadable finding, not throw'
);

const audit = auditRepo({ root: bare });
check(audit.summary.files === 0, 'an empty directory must audit zero files');
check(audit.findings.length === 0, 'an empty directory must produce no findings');

const gate = evaluatePushGate({ root: bare, rev: 'HEAD' });
check(gate.ready === false, 'a gate cannot pass without a readable commit');
check(gate.blockingFailed.includes('commit-tree'), 'the unreadable commit must be the blocking item');
check(typeof gate.text === 'string' && gate.text.length > 0, 'the gate must still render a verdict');

rmSync(bare, { recursive: true, force: true });

if (failures.length > 0) {
	console.error('portability test FAILED:');
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('portability test passed');
console.log(`  已扫描可移植面：${scanned.length} 个可执行文件，0 处主机耦合`);
console.log('  无 AGENTS.md / latex/ / git 时：四类检查全部如实降级，gate 明确报告 blocked');
