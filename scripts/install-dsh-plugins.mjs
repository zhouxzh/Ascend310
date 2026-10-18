/**
 * Install both Ascend310 DSH plugins into a DSH profile.
 *
 * This is the new-machine path. It does not copy files or create a hand-made
 * junction: it calls the official `dsh plugin add <absolute path>` command,
 * which forwards the install to pnpm inside the selected profile. The profile
 * records the plugin as a dependency, so uninstalling and reinstalling are
 * ordinary DSH/pnpm operations.
 *
 * Usage:
 *   node scripts/install-dsh-plugins.mjs
 *   node scripts/install-dsh-plugins.mjs --profile desktop
 *   node scripts/install-dsh-plugins.mjs --check
 */

import { existsSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const scriptDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(scriptDir, '..');
const args = process.argv.slice(2);
const checkOnly = args.includes('--check');
const profileIndex = args.indexOf('--profile');
const profile = profileIndex >= 0 && args[profileIndex + 1] ? args[profileIndex + 1] : 'desktop';

const plugins = [
	join(repoRoot, 'tools', 'dsh-plugin-ascend310b-writing'),
	join(repoRoot, 'tools', 'dsh-plugin-ascend310b-debug')
];
const dshHome = process.env.DSH_HOME ?? join(homedir(), '.dsh');
const profileDir = join(dshHome, 'profiles', profile);

/** Locate the DSH CLI without hard-coding a machine path. */
function findDsh() {
	if (process.env.DSH_BIN && existsSync(process.env.DSH_BIN)) return process.env.DSH_BIN;
	const onPath = spawnSync(process.platform === 'win32' ? 'where' : 'which', ['dsh'], {
		encoding: 'utf8'
	});
	if (onPath.status === 0) {
		const first = onPath.stdout.split(/\r?\n/u).find(Boolean);
		if (first) return first.trim();
	}
	if (process.platform === 'win32' && process.env.LOCALAPPDATA) {
		const bundled = join(
			process.env.LOCALAPPDATA,
			'Programs',
			'DeepSeek Harness',
			'resources',
			'runtime',
			'cli',
			'bin',
			'dsh.cmd'
		);
		if (existsSync(bundled)) return bundled;
	}
	return '';
}

const dsh = findDsh();
if (dsh === '') {
	console.error('找不到 dsh CLI。请先安装 DSH 桌面版，或把 dsh 加入 PATH。');
	process.exit(1);
}

function installedManifest(name) {
	const manifest = join(profileDir, 'node_modules', name, 'package.json');
	if (!existsSync(manifest)) return null;
	try {
		return JSON.parse(readFileSync(manifest, 'utf8'));
	} catch {
		return null;
	}
}

let failed = false;
const valid = [];
for (const pluginDir of plugins) {
	const manifestPath = join(pluginDir, 'package.json');
	if (!existsSync(manifestPath)) {
		console.error(`  缺少插件清单：${manifestPath}`);
		failed = true;
		continue;
	}
	try {
		valid.push({ dir: pluginDir, pkg: JSON.parse(readFileSync(manifestPath, 'utf8')) });
	} catch (error) {
		console.error(`  ${manifestPath} 不是有效 JSON：${error.message}`);
		failed = true;
	}
}

// Add both in one pnpm transaction. Installing them one at a time makes pnpm
// prune the previous plugin because it is not yet in the dependency tree.
if (!checkOnly && valid.length > 0) {
	console.log(`\n安装 ${valid.map((entry) => entry.pkg.name).join(' + ')} → profile ${profile}`);
	const result = spawnSync(
		dsh,
		['plugin', '--profile', profile, 'add', ...valid.map((entry) => entry.dir)],
		{ stdio: 'inherit', env: process.env }
	);
	if ((result.status ?? 1) !== 0) failed = true;
}

for (const { pkg } of valid) {
	const installed = installedManifest(pkg.name);
	if (installed === null) {
		console.error(`  未安装：${pkg.name}`);
		failed = true;
		continue;
	}
	console.log(`  OK  ${pkg.name} ${installed.version}`);
}

if (checkOnly) {
	console.log(`\nDSH profile：${profileDir}`);
	console.log(failed ? '插件不完整。' : '两个插件均已安装。');
} else if (!failed) {
	console.log('\n安装完成。重启 DSH，并在插件页确认两个插件已启用。');
	console.log('项目级 skill 由仓库 .dsh/skills 自动加载，无需另装。');
}
process.exit(failed ? 1 : 0);
