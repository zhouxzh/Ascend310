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

import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, readdirSync, renameSync, statSync } from 'node:fs';
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

function runDsh(dshArgs, options = {}) {
	if (process.platform === 'win32') {
		const installRoot = process.env.LOCALAPPDATA
			? join(process.env.LOCALAPPDATA, 'Programs', 'DeepSeek Harness')
			: '';
		const exe = join(installRoot, 'DeepSeek Harness.exe');
		const cli = join(
			installRoot,
			'resources',
			'app.asar',
			'dsh',
			'node_modules',
			'@deepseek-ai',
			'dsh-desktop-host',
			'lib',
			'cli.js'
		);
		if (existsSync(exe)) {
			return spawnSync(exe, ['--expose-internals', cli, ...dshArgs], {
				...options,
				env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' }
			});
		}
	}
	return spawnSync(dsh, dshArgs, { ...options, shell: process.platform === 'win32' });
}
if (dsh === '') {
	console.error('找不到 dsh CLI。请先安装 DSH 桌面版，或把 dsh 加入 PATH。');
	process.exit(1);
}

/** Run npm pack and return the tarball path. */
function pack(pluginDir) {
	// Spawning npm.cmd from Node is unreliable on Windows (and quoting through
	// cmd.exe breaks the destination path). Execute npm's JS entry point with the
	// current Node binary instead.
	const npmCli = join(dirname(process.execPath), 'node_modules', 'npm', 'bin', 'npm-cli.js');
	const useCli = existsSync(npmCli);
	const command = useCli ? process.execPath : 'npm';
	const commandArgs = useCli
		? [npmCli, 'pack', '--pack-destination', packDir]
		: ['pack', '--pack-destination', packDir];
	const result = spawnSync(command, commandArgs, {
		cwd: pluginDir,
		encoding: 'utf8'
	});
	if (result.error) {
		console.error(result.error.message);
		return '';
	}
	if (result.status !== 0) {
		console.error(((result.stderr || '') + (result.stdout || '')).trim());
		return '';
	}
	const name = (result.stdout ?? '')
		.trim()
		.split(/\r?\n/u)
		.find((line) => line.endsWith('.tgz'));
	if (!name) return '';
	const tarball = join(packDir, name);
	// A same-name/same-version tarball makes pnpm reuse its cached copy and
	// silently keep an older package. Name every pack by its content hash so a
	// code change is always a new specifier.
	const hash = createHash('sha256').update(readFileSync(tarball)).digest('hex').slice(0, 12);
	const hashed = join(packDir, `${name.replace(/\.tgz$/u, '')}-${hash}.tgz`);
	renameSync(tarball, hashed);
	return hashed;
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

/** Hash the files declared by the package manifest, excluding local deps. */
function treeHash(root, entries) {
	const hash = createHash('sha256');
	const files = [];
	const walk = (relativePath) => {
		const absolute = join(root, relativePath);
		if (!existsSync(absolute)) return;
		const stats = statSync(absolute);
		if (stats.isDirectory()) {
			for (const name of readdirSync(absolute).sort()) {
				if (name === 'node_modules' || name.endsWith('.tgz') || name.endsWith('.tmpdir')) continue;
				walk(join(relativePath, name));
			}
			return;
		}
		if (stats.isFile() && !relativePath.endsWith('.tmp')) files.push([relativePath, absolute]);
	};
	for (const entry of entries ?? []) walk(entry);
	for (const [relativePath, absolute] of files) {
		hash.update(relativePath.replaceAll('\\', '/'));
		hash.update('\0');
		hash.update(readFileSync(absolute));
		hash.update('\0');
	}
	return { hash: hash.digest('hex'), files: files.length };
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

	console.log(`\n安装到 profile ${profile}`);
	const added = runDsh(['plugin', '--profile', profile, 'add', ...tarballs], {
		stdio: 'inherit'
	});
	// The installed-file verification below is the authoritative check.
	if ((added.status ?? 1) !== 0) {
		console.warn(
			`  dsh plugin add 返回 ${added.status}（${added.error?.message ?? '无错误信息'}），继续核对安装结果`
		);
	}
}

let failed = false;
for (const { dir, pkg } of plugins) {
	const installed = installedManifest(pkg.name);
	const specifier = installedSpecifier(pkg.name);
	if (installed === null) {
		console.error(`  未安装：${pkg.name}`);
		failed = true;
		continue;
	}
	if (installed.version !== pkg.version) {
		console.error(`  ${pkg.name} 版本不一致：安装 ${installed.version ?? '未知'}，源码 ${pkg.version}`);
		failed = true;
		continue;
	}
	if (specifier.startsWith('link:')) {
		console.error(`  ${pkg.name} 仍是 link: 安装，Node 从仓库真实路径无法解析 DSH host 包；请重跑本脚本。`);
		failed = true;
		continue;
	}
	const installedRoot = join(profileDir, 'node_modules', pkg.name);
	const missing = (installed.files ?? []).filter(
		(entry) => !existsSync(join(installedRoot, entry))
	);
	if (missing.length > 0) {
		console.error(`  ${pkg.name} 安装副本缺少 package.json files 中的：${missing.join('、')}`);
		failed = true;
		continue;
	}
	const sourceHash = treeHash(dir, pkg.files);
	const installedHash = treeHash(installedRoot, pkg.files);
	if (sourceHash.hash !== installedHash.hash || sourceHash.files !== installedHash.files) {
		console.error(
			`  ${pkg.name} 安装内容与源码不一致（source ${sourceHash.files} files, installed ${installedHash.files} files）。`
		);
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
