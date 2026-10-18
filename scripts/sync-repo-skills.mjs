/**
 * Sync the skills shipped by this repository's plugins into the project-scoped
 * skill directory that DSH reads first.
 *
 * DSH resolves skill roots in rank order:
 *
 *   100  <projectRoot>/.dsh/skills
 *   200  <projectRoot>/.agents/skills
 *   300  Config.customSkills…
 *
 * A project-scoped skill therefore wins over the user-level copy in
 * `$DSH_HOME/skills`, which means a fresh clone gets the repository's rules
 * without a manual install. The plugin directories stay the single source of
 * truth (they are what npm publishes); this script generates the project copy
 * and `--check` fails when the two drift apart.
 *
 * Usage:
 *   node scripts/sync-repo-skills.mjs          # write .dsh/skills
 *   node scripts/sync-repo-skills.mjs --check  # verify only, exit 1 on drift
 */

import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(scriptDir, '..');
const targetRoot = join(repoRoot, '.dsh', 'skills');
const checkOnly = process.argv.includes('--check');

const sources = [
	join(repoRoot, 'tools', 'dsh-plugin-ascend310b-writing', 'skills'),
	join(repoRoot, 'tools', 'dsh-plugin-ascend310b-debug', 'skills')
];

const skills = [];
for (const sourceRoot of sources) {
	if (!existsSync(sourceRoot)) continue;
	for (const entry of readdirSync(sourceRoot, { withFileTypes: true })) {
		if (!entry.isDirectory()) continue;
		const file = join(sourceRoot, entry.name, 'SKILL.md');
		if (existsSync(file)) skills.push({ name: entry.name, source: file });
	}
}
if (skills.length === 0) {
	console.error('没有找到任何插件 skill。');
	process.exit(1);
}

let drift = 0;
for (const { name, source } of skills) {
	const targetDir = join(targetRoot, name);
	const target = join(targetDir, 'SKILL.md');
	const shipped = readFileSync(source, 'utf8');
	const installed = existsSync(target) ? readFileSync(target, 'utf8') : null;
	if (installed === shipped) {
		console.log(`  OK    .dsh/skills/${name}`);
		continue;
	}
	drift += 1;
	if (checkOnly) {
		console.log(`  差异  .dsh/skills/${name}  ${installed === null ? '未生成' : '内容不同'}`);
		continue;
	}
	mkdirSync(targetDir, { recursive: true });
	writeFileSync(target, shipped, 'utf8');
	console.log(`  已同步 .dsh/skills/${name}`);
}

if (checkOnly && drift > 0) {
	console.error(`\n${drift} 个项目 skill 与插件内版本不一致；运行 node scripts/sync-repo-skills.mjs。`);
	process.exit(1);
}
console.log(
	`\n${skills.length} 个项目 skill ${checkOnly ? '已核对' : '已同步'}（${targetRoot}）。` +
		' DSH 以 rank 100 读取该目录，优先于用户目录。'
);
