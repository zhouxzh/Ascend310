#!/usr/bin/env node
/**
 * Toolchain doctor, runnable right after installing the plugin.
 *
 * It answers the install-time question — "is anything missing, and how do I
 * install it?" — without needing DSH, a profile, or the model. Use it in a
 * fresh clone, on a new machine, or when a build fails for an environment
 * reason.
 *
 * Usage:
 *   node scripts/doctor.mjs [repoRoot] [--json]
 *
 * Exits 0 when the LaTeX/PDF path is available, 1 otherwise, so an install
 * script can gate on it.
 */

import { existsSync } from 'node:fs';
import { join, resolve } from 'node:path';

import { diagnose } from '../lib/doctor.js';

const argv = process.argv.slice(2);
const asJson = argv.includes('--json');
const positional = argv.find((argument) => !argument.startsWith('--'));

// Prefer an explicit argument, then the environment, then the working
// directory. Nothing is hardcoded, and a wrong directory only shows up as
// "the repository's entry points are absent".
const fromEnv = process.env.TUTORIAL_WRITING_ROOT;
const root = resolve(positional ?? (fromEnv !== undefined && fromEnv.trim() !== '' ? fromEnv : process.cwd()));

const report = diagnose({ root });

if (asJson) {
	process.stdout.write(
		`${JSON.stringify(
			{
				root,
				platform: process.platform,
				adapter: report.adapter.label,
				python: report.python,
				missingRequired: report.missingRequired,
				missingOptional: report.missingOptional,
				installCommand: report.installCommand,
				capabilities: report.capabilities.map((capability) => ({
					id: capability.id,
					available: capability.available,
					detail: capability.detail
				})),
				components: report.components.map((component) => ({
					id: component.id,
					required: component.required,
					ok: component.ok === true,
					name: component.name ?? null,
					version: component.version ?? null
				})),
				repository: report.files
			},
			null,
			2
		)}\n`
	);
} else {
	process.stdout.write(`${report.text}\n`);
	if (!report.files.converter && !existsSync(join(root, 'convert-vuepress.sh'))) {
		process.stdout.write(
			`\n提示：${root} 下没有 convert-vuepress.sh。\n` +
				'请把仓库根目录作为参数传入：node scripts/doctor.mjs <仓库根目录>\n'
		);
	}
}

const latex = report.capabilities.find((capability) => capability.id === 'latex');
process.exit(latex.available ? 0 : 1);
