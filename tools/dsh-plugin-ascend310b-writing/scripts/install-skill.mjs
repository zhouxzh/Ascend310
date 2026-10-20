/**
 * Install the skills shipped inside this plugin into the DSH home.
 *
 * A plugin can carry a `skills/` directory, but DSH discovers skills from
 * `$DSH_HOME/skills/<name>/SKILL.md` (default `~/.dsh/skills`); it does not copy
 * them out of an installed plugin automatically. This script is that missing
 * step: it installs every skill in `skills/`, and `--check` reports drift
 * without writing, so a release can verify that the running instructions match
 * the ones in the repository.
 *
 * Usage:
 *   node scripts/install-skill.mjs           # install / update
 *   node scripts/install-skill.mjs --check   # verify only, exit 1 on drift
 */

import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = dirname(fileURLToPath(import.meta.url));
const sourceRoot = resolve(scriptDir, '..', 'skills');
const dshHome = process.env.DSH_HOME ?? join(homedir(), '.dsh');
const targetRoot = join(dshHome, 'skills');
const checkOnly = process.argv.includes('--check');

if (!existsSync(sourceRoot)) {
	console.error(`没有找到插件 skills 目录：${sourceRoot}`);
	process.exit(1);
}

/** Collect references and templates too; future skills may not be one file. */
function walk(directory, found = []) {
	for (const entry of readdirSync(directory, { withFileTypes: true })) {
		const path = join(directory, entry.name);
		if (entry.isDirectory()) walk(path, found);
		else if (entry.isFile()) found.push(path);
	}
	return found;
}

const supportDirectories = new Set(['nature-shared']);
const skills = readdirSync(sourceRoot, { withFileTypes: true })
	.filter(
		(entry) =>
			entry.isDirectory() &&
			(existsSync(join(sourceRoot, entry.name, 'SKILL.md')) || supportDirectories.has(entry.name))
	)
	.map((entry) => entry.name);

if (skills.length === 0) {
	console.error(`${sourceRoot} 下没有包含 SKILL.md 的 skill`);
	process.exit(1);
}

let drift = 0;
for (const name of skills) {
	const sourceDir = join(sourceRoot, name);
	const targetDir = join(targetRoot, name);
	let skillDrift = 0;
	for (const source of walk(sourceDir)) {
		const rel = relative(sourceDir, source).replaceAll('\\', '/');
		const target = join(targetDir, rel);
		const installed = existsSync(target) ? readFileSync(target, 'utf8') : null;
		const shipped = readFileSync(source, 'utf8');
		if (installed === shipped) continue;
		skillDrift += 1;
		if (checkOnly) {
			console.log(`  差异  ${name}/${rel}  ${installed === null ? '未安装' : '内容不同'}`);
			continue;
		}
		mkdirSync(dirname(target), { recursive: true });
		writeFileSync(target, shipped, 'utf8');
	}
	drift += skillDrift;
	if (skillDrift === 0) console.log(`  OK    ${name}`);
	else if (!checkOnly) console.log(`  已${checkOnly ? '更新' : '安装'} ${name} -> ${targetDir}`);
}

if (checkOnly && drift > 0) {
	console.error(`\n${drift} 个 skill 文件与插件内版本不一致；运行 node scripts/install-skill.mjs 安装。`);
	process.exit(1);
}
console.log(`\n${skills.length} 个 skill ${checkOnly ? '已核对' : '已安装'}（${targetRoot}）。`);
console.log('DSH 在会话启动时读取 skill；更新后请新建会话使新内容生效。');
