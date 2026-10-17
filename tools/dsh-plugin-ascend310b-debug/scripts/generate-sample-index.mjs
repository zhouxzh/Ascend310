/**
 * Regenerate data/sample-index.json from a textbook checkout.
 *
 * The index is generated, not hand-maintained: it scans samples/<name>/README.md
 * and records each program's title, entry command, test command, debugging
 * archetype and capabilities. Run it after the samples tree changes, then run
 * the plugin tests, which fail when the committed index no longer matches a
 * fresh scan.
 *
 * Usage:
 *   node scripts/generate-sample-index.mjs <repoRoot>
 *   DSH_ASCEND310B_ROOT=<repoRoot> node scripts/generate-sample-index.mjs
 */

import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { indexSampleIndex } from '../lib/report.js';

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(process.argv[2] ?? process.env.DSH_ASCEND310B_ROOT ?? process.cwd());

/**
 * Read and parse a JSON file.
 *
 * @param {string} path - file path.
 * @returns {object|null} parsed JSON, or null when absent or invalid.
 */
function readJson(path) {
	try {
		return JSON.parse(readFileSync(path, 'utf8'));
	} catch {
		return null;
	}
}

const caseIndex = readJson(join(repoRoot, 'samples', 'case-index.json'));
const index = indexSampleIndex({ root: join(repoRoot, 'samples'), caseIndex });
const target = join(here, '..', 'data', 'sample-index.json');
mkdirSync(dirname(target), { recursive: true });
writeFileSync(target, JSON.stringify(index, null, 2) + '\n', 'utf8');

const withEntry = index.entries.filter((entry) => entry.entry !== null).length;
const withTest = index.entries.filter((entry) => entry.test !== null).length;
console.log('wrote ' + target);
console.log('  samples    : ' + index.entries.length);
console.log('  with entry : ' + withEntry);
console.log('  with test  : ' + withTest);
console.log('  archetypes : ' + (index.archetypes.join(', ') || '(none)'));
