/**
 * Verify that every relative Markdown reference inside a committed tree
 * resolves to a path that commit also contains.
 *
 * A thin CLI over `collectCommitFindings`, so the rule has exactly one
 * implementation: the same collector backs the `ascend310b_writing_audit` tool's `rev`
 * parameter. The manuscript audit reads the working tree, where a file
 * excluded from the commit still exists; this reads the commit itself, which
 * is what a reader of the published repository sees.
 *
 * Usage: node scripts/check-commit-links.mjs [repoRoot] [rev]
 */

import { collectCommitFindings, describeCommit } from '../lib/checks.js';

// Default to the working directory: a developer runs this from the repository
// they mean, and a hardcoded checkout would be host coupling shipped inside a
// package that is meant to be usable elsewhere.
const root = process.argv[2] ?? process.cwd();
const rev = process.argv[3] ?? 'HEAD';

const result = collectCommitFindings({ root, rev });
console.log(describeCommit(result, rev));

if (result.findings.length > 0) {
	const grouped = new Map();
	for (const finding of result.findings) {
		const bucket = grouped.get(finding.message) ?? [];
		bucket.push(`${finding.file}:${finding.line}`);
		grouped.set(finding.message, bucket);
	}
	console.log('\n无法解析的引用：');
	for (const [message, sources] of grouped) {
		console.log(`  ${message}`);
		for (const source of sources.slice(0, 3)) console.log(`      被 ${source} 引用`);
		if (sources.length > 3) console.log(`      …共 ${sources.length} 处`);
	}
	process.exit(1);
}
console.log('所有已提交正文的本地引用都能在提交树里找到。');
