/**
 * Package-content guard.
 *
 * The plugin is installed from an npm tarball, and npm only ships paths listed
 * in `files`. A relative import to a directory that exists in the repository
 * but is omitted from the tarball works in tests and fails only after install —
 * exactly how `lib/doctor.js -> ../drivers/index.mjs` disappeared and the whole
 * plugin stopped registering.
 *
 * Usage: node test/package-files.mjs
 */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
const included = new Set(pkg.files ?? []);

const failures = [];
const files = [];
function walk(directory) {
	for (const entry of readdirSync(directory, { withFileTypes: true })) {
		const full = join(directory, entry.name);
		if (entry.isDirectory()) walk(full);
		else if (entry.isFile() && /\.(?:js|mjs)$/u.test(entry.name)) files.push(full);
	}
}
walk(join(root, 'lib'));

for (const file of files) {
	const source = readFileSync(file, 'utf8');
	for (const match of source.matchAll(/from\s+['"](\.\.[^'"]+)['"]/gu)) {
		const target = resolve(dirname(file), match[1]);
		const relative = target.slice(root.length + 1).split(/[\\/]/u)[0];
		if (!included.has(relative)) {
			failures.push(`${file.slice(root.length + 1)} 引用 ${match[1]}，但 package.json files 未包含 ${relative}/`);
		}
	}
}

for (const entry of included) {
	const full = join(root, entry);
	if (!statSync(full, { throwIfNoEntry: false })) {
		failures.push(`package.json files 列出的 ${entry} 不存在`);
	}
}

if (failures.length > 0) {
	console.error('package files test FAILED:');
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('package files test passed');
console.log(`  tarball 声明 ${included.size} 个顶层路径，lib 的跨目录导入均可解析`);
