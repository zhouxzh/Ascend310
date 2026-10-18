/**
 * Install both Ascend310 DSH plugins into a DSH profile from packed tarballs.
 *
 * Why not `dsh plugin add <folder>`? A folder install is a pnpm `link:`; Node
 * then resolves the plugin from its real path in this repository, where the
 * DSH host packages (`@deepseek-ai/dsh-tools`, Client APIs) are not present, so
 * the plugin fails at import time. Packing first makes pnpm install a real copy
 * inside the profile, where the DSH loader can resolve those packages.
 *
 * Usage:
 *   node scripts/install-dsh-plugins.mjs
 *   node scripts/install-dsh-plugins.mjs --profile desktop
 *   node scripts/install-dsh-plugins.mjs --check
 */

import { existsSync, mkdirSync, readFileSync } from 'node:fs';
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

const pluginDirs = [
	join(repoRoot, 'tools', 'dsh-plugin-ascend310b-writing'),
	join(repoRoot, 'tools', 'dsh-plugin-ascend310b-debug')
];
const dshHome = process.env.DSH_HOME ?? join(homedir(), '.dsh');
const profileDir = join(dshHome, 'profiles', profile);
const packDir = join(dshHome, 'plugin-packs');

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

/** Run npm pack and return the tarball path. */
function pack(pluginDir) {
	const result = spawnSync('npm', ['pack', '--pack-destination', packDir], {
		cwd: pluginDir,
		encoding: 'utf8'
	});
	if (result.status !== 0) {
		console.error((result.stderr || result.stdout).trim());
		return '';
	}
	const name = result.stdout.trim().split(/\r?\n/u).find((line) => line.endsWith('.tgz'));
	return name ? join(packDir, name) : '';
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

function installedSpecifier(name) {
	try {
		const profileManifest = JSON.parse(readFileSync(join(profileDir, 'package.json'), 'utf8'));
		return profileManifest.dependencies?.[name] ?? '';
	} catch {
		return '';
	}
}

const plugins = [];
for (const pluginDir of pluginDirs) {
	const manifestPath = join(pluginDir, 'package.json');
	if (!existsSync(manifestPath)) {
		console.error(`  缺少插件清单：${manifestPath}`);
		process.exit(1);
	}
	try {
		plugins.push({ dir: pluginDir, pkg: JSON.parse(readFileSync(manifestPath, 'utf8')) });
	} catch (error) {
		console.error(`  ${manifestPath} 不是有效 JSON：${error.message}`);
		process.exit(1);
	}
}

if (!checkOnly) {
	mkdirSync(packDir, { recursive: true });
	const tarballs = [];
	for (const { dir, pkg } of plugins) {
		console.log(`\n打包 ${pkg.name}@${pkg.version}`);
		const tarball = pack(dir);
		if (tarball === '') process.exit(1);
		tarballs.push(tarball);
		console.log(`  ${tarball}`);
	}

	// Remove old link/file specs first; otherwise pnpm can keep an existing
	// `link:` at the same version and report "Already up to date".
	spawnSync(dsh, ['plugin', '--profile', profile, 'remove', ...plugins.map((entry) => entry.pkg.name)], {
		stdio: 'ignore',
		env: process.env
	});
	console.log(`\n安装到 profile ${profile}`);
	const added = spawnSync(dsh, ['plugin', '--profile', profile, 'add', ...tarballs], {
		stdio: 'inherit',
		env: process.env
	});
	if ((added.status ?? 1) !== 0) process.exit(1);
}

let failed = false;
for (const { pkg } of plugins) {
	const installed = installedManifest(pkg.name);
	const specifier = installedSpecifier(pkg.name);
	if (installed === null) {
		console.error(`  未安装：${pkg.name}`);
		failed = true;
		continue;
	}
	if (specifier.startsWith('link:')) {
		console.error(`  ${pkg.name} 仍是 link: 安装，Node 从仓库真实路径无法解析 DSH host 包；请重跑本脚本。`);
		failed = true;
		continue;
	}
	console.log(`  OK  ${pkg.name} ${installed.version}  (${specifier})`);
}

if (checkOnly) {
	console.log(`\nDSH profile：${profileDir}`);
	console.log(failed ? '插件安装不完整。' : '两个插件均已按 tarball 方式安装。');
} else if (!failed) {
	console.log('\n安装完成。重启 DSH，并在插件页确认两个插件已启用。');
	console.log('项目级 skill 由仓库 .dsh/skills 自动加载，无需另装。');
}
process.exit(failed ? 1 : 0);
