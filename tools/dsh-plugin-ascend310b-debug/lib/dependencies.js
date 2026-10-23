/**
 * Case-level system-program inventory shown in the debug panel.
 *
 * This is documentation metadata, not an installation claim. The panel must
 * display the source and required/recommended level; actual apt state is only
 * known after a board-side check or an explicit install run.
 */

import { readFileSync } from 'node:fs';

const DATA_URL = new URL('../data/case-dependencies.json', import.meta.url);

/** Load the curated case dependency table; return an empty table if absent. */
export function loadCaseDependencies() {
	try {
		const value = JSON.parse(readFileSync(DATA_URL, 'utf8'));
		const cases = Array.isArray(value?.cases) ? value.cases : [];
		return cases
			.filter((item) => item !== null && typeof item === 'object' && typeof item.id === 'string')
			.map((item) => ({
				id: item.id,
				title: typeof item.title === 'string' ? item.title : item.id,
				device: typeof item.device === 'string' ? item.device : '',
				groups: Array.isArray(item.groups) ? item.groups.filter((group) => typeof group === 'string') : [],
				apt: Array.isArray(item.apt)
					? item.apt
							.filter((pkg) => pkg !== null && typeof pkg === 'object' && typeof pkg.name === 'string')
							.map((pkg) => ({
								name: pkg.name,
								purpose: typeof pkg.purpose === 'string' ? pkg.purpose : '',
								level: typeof pkg.level === 'string' ? pkg.level : 'recommended',
								source: typeof pkg.source === 'string' ? pkg.source : ''
							}))
					: [],
				python: Array.isArray(item.python) ? item.python.filter((name) => typeof name === 'string') : [],
				source: typeof item.source === 'string' ? item.source : ''
			}));
	} catch {
		return [];
	}
}
