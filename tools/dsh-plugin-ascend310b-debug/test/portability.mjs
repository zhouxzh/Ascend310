/**
 * Portability guard.
 *
 * The plugin ships as a package, so no executable source or bundled skill may
 * contain a machine-specific path, board address, or user name. Deployment
 * facts such as board IPs, SSH users and the checkout root belong in the
 * profile's `cordis.patch.yml`, never in the package.
 *
 * Usage: node test/portability.mjs
 */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');

/** Extensions that must stay portable. */
const CODE = new Set(['.js', '.mjs', '.cjs', '.ps1']);
/** Documentation may show placeholders, so it is scanned for a narrower set. */
const DOCS = new Set(['.md', '.yml', '.yaml', '.json']);

/**
 * Collect every file below a directory.
 *
 * @param {string} directory - directory to walk.
 * @param {string[]} [found] - accumulator.
 * @returns {string[]} absolute file paths.
 */
function walk(directory, found = []) {
	for (const name of readdirSync(directory)) {
		if (name === 'node_modules' || name === '.git') continue;
		const path = join(directory, name);
		if (statSync(path).isDirectory()) walk(path, found);
		else found.push(path);
	}
	return found;
}

const failures = [];
const files = walk(root);

for (const file of files) {
	const extension = file.slice(file.lastIndexOf('.'));
	const isCode = CODE.has(extension);
	const isDoc = DOCS.has(extension);
	if (!isCode && !isDoc) continue;

	const text = readFileSync(file, 'utf8');
	const where = relative(root, file).replaceAll('\\', '/');
	const lines = text.split(/\r?\n/);

	// A checkout path or a package created under a home directory is deployment
	// state, not something another user can resolve.
	lines.forEach((line, index) => {
		if (/[A-Za-z]:[\\/](?:Users|Github|work|home|Documents|Programs)[\\/]/i.test(line)) {
			failures.push(`${where}:${index + 1} 含有本机绝对路径：${line.trim()}`);
		}
		if (isCode && /\b[A-Za-z]:[\\/]/.test(line)) {
			failures.push(`${where}:${index + 1} 代码中出现盘符路径：${line.trim()}`);
		}
	});

	// A specific board address signals deployment state leaking into the package.
	lines.forEach((line, index) => {
		const address = /\b192\.168\.\d{1,3}\.\d{1,3}\b/.exec(line);
		if (address !== null) {
			failures.push(`${where}:${index + 1} 出现固定板卡地址 ${address[0]}`);
		}
	});

	// The local user name and an absolute DSH install path are deployment facts.
	// The user name is assembled from code points so this guard does not match
	// its own source line. A documented `$env:USERPROFILE\.dsh\...` path needs no
	// exemption: the drive-letter check above already covers hardcoded ones.
	const forbiddenUser = String.fromCharCode(122, 104, 111, 117, 120);
	const userPattern = new RegExp(forbiddenUser, 'i');
	lines.forEach((line, index) => {
		if (userPattern.test(line)) failures.push(`${where}:${index + 1} 出现本机用户名`);
	});
}

// The bundled skill must carry valid frontmatter with a name and a description.
const skillPath = join(root, 'skills', 'ascend310b-debug', 'SKILL.md');
let skill = null;
try {
	skill = readFileSync(skillPath, 'utf8');
} catch {
	failures.push('skills/ascend310b-debug/SKILL.md 不存在');
}
if (skill !== null) {
	if (!skill.startsWith('---\n')) failures.push('SKILL.md 必须以 YAML frontmatter 开头');
	const frontmatter = skill.slice(4, skill.indexOf('\n---', 3));
	if (!/^name:\s*\S+/m.test(frontmatter)) failures.push('SKILL.md frontmatter 缺少 name');
	if (!/^description:\s*\S+/m.test(frontmatter)) failures.push('SKILL.md frontmatter 缺少 description');
	const declared = /^name:\s*(\S+)/m.exec(frontmatter)?.[1];
	if (declared !== 'ascend310b-debug') {
		failures.push(`SKILL.md 的 name 必须与目录名一致（${declared} ≠ ascend310b-debug）`);
	}
}

// Every bundled file the manifest promises must exist, and the package must
// carry everything a file-copy installation needs.
const manifest = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
for (const entry of manifest.files ?? []) {
	const path = join(root, entry);
	try {
		statSync(path);
	} catch {
		failures.push(`package.json files 列出的 ${entry} 不存在`);
	}
}
if (manifest.name !== 'dsh-plugin-ascend310b-debug') {
	failures.push(`package.json name 必须是 dsh-plugin-ascend310b-debug，实际为 ${manifest.name}`);
}
if (manifest.private !== true) {
	failures.push('package.json 必须保持 private=true：本插件以源码复制安装，不发布到公共 npm');
}
if (manifest.license !== 'CC0-1.0') {
	failures.push(`package.json license 必须是 CC0-1.0，实际为 ${manifest.license}`);
}
try {
	statSync(join(root, 'LICENSE'));
} catch {
	failures.push('包内缺少 LICENSE；直接复制安装会丢失许可证');
}
if (manifest.engines?.node !== '>=18') {
	failures.push('package.json engines.node 必须声明 >=18');
}
if (manifest.dsh?.bundle?.patch !== './cordis.patch.yml') {
	failures.push('package.json 缺少 dsh.bundle.patch 声明');
}
if (manifest.dsh?.client?.platform !== 'web') {
	failures.push('package.json 缺少 dsh.client.platform=web');
}
const entryPoints = [
	manifest.main,
	typeof manifest.exports?.['.']?.default === 'string' ? manifest.exports['.'].default : null,
	typeof manifest.exports?.['./client']?.default === 'string' ? manifest.exports['./client'].default : null
].filter((entry) => typeof entry === 'string');
for (const entry of entryPoints) {
	try {
		statSync(join(root, entry));
	} catch {
		failures.push(`package.json 入口 ${entry} 不存在`);
	}
}

if (failures.length > 0) {
	console.error(`portability test FAILED (${failures.length} 处):`);
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('portability test passed');
console.log(`  scanned files : ${files.length}`);
console.log(`  skill         : ${skill === null ? '(missing)' : 'ascend310b-debug/SKILL.md'}`);
console.log(`  manifest files: ${(manifest.files ?? []).join(', ')}`);
