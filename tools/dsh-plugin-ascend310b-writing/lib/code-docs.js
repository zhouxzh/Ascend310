/**
 * Code-documentation coverage and manuscript-to-code synchronization.
 *
 * The manuscript audit already proves that path tokens resolve. This module
 * answers the next question: does every sample area have a runnable README,
 * detailed docs where code exists, and a case-index entry that still points at
 * the real source/sample pair?
 *
 * Findings use the same shape as the manuscript audit so the panel and push
 * gate can display them without a second schema.
 */

import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, sep } from 'node:path';

import { listFiles } from './code.js';

const SAMPLE_DIR = /^(?:chapter|case|appendix)\d+$/u;
const CODE_EXTENSIONS = new Set([
	'.c', '.cc', '.cpp', '.cu', '.h', '.hpp', '.js', '.mjs', '.ts', '.tsx',
	'.py', '.sh', '.ps1', '.go', '.rs', '.java', '.kt', '.swift'
]);

function relPath(root, path) {
	return relative(root, path).split(sep).join('/');
}

function isCode(path) {
	const dot = path.lastIndexOf('.');
	return dot >= 0 && CODE_EXTENSIONS.has(path.slice(dot).toLowerCase());
}

function newest(root, paths) {
	let latest = 0;
	for (const path of paths) {
		try {
			latest = Math.max(latest, statSync(join(root, path)).mtimeMs);
		} catch {
			// A file can disappear during a concurrent edit; the next refresh sees it.
		}
	}
	return latest === 0 ? null : new Date(latest).toISOString();
}

function isMeaningfulDoc(path) {
	// docs/README.md is an index, not an explanation of the code. It still counts
	// as a docs entry, but not as evidence that the code has been documented.
	return path !== 'docs/README.md';
}

function firstHeading(text) {
	return text.split(/\r?\n/u).map((line) => /^#\s+(.+?)\s*$/u.exec(line)?.[1]).find(Boolean) ?? null;
}

/**
 * Build the code-docs and cross-artifact report.
 *
 * @param {object} options - inputs.
 * @param {string} options.root - repository root.
 * @returns {object} report with summary, entries, findings and rendered text.
 */
export function collectCodeDocs({ root }) {
	const samplesRoot = join(root, 'samples');
	const findings = [];
	const entries = [];
	let sampleNames = [];
	try {
		sampleNames = readdirSync(samplesRoot, { withFileTypes: true })
			.filter((entry) => entry.isDirectory() && SAMPLE_DIR.test(entry.name))
			.map((entry) => entry.name)
			.sort();
	} catch {
		findings.push({
			file: 'samples',
			line: 1,
			severity: 'error',
			rule: 'code-docs-samples-missing',
			message: '没有找到 samples 目录，无法检查代码文档覆盖。'
		});
	}

	for (const name of sampleNames) {
		const sampleRel = `samples/${name}`;
		const absolute = join(samplesRoot, name);
		const files = [...listFiles(absolute)];
		const readme = files.includes('README.md');
		const docs = files.filter((file) => file.startsWith('docs/') && file.endsWith('.md'));
		const meaningfulDocs = docs.filter(isMeaningfulDoc);
		const code = files.filter(isCode);
		const codeTimes = code.map((file) => `${sampleRel}/${file}`);
		const docTimes = docs.map((file) => `${sampleRel}/${file}`);
		const newestCodeAt = newest(root, codeTimes);
		const newestDocsAt = newest(root, docTimes);
		const issues = [];

		if (!readme) {
			issues.push('README 缺失');
			findings.push({
				file: `${sampleRel}/README.md`,
				line: 1,
				severity: 'error',
				rule: 'code-docs-readme-missing',
				message: `${sampleRel} 有代码但没有 README.md；读者无法确认入口和前置条件。`
			});
		}
		if (code.length > 0 && meaningfulDocs.length === 0) {
			issues.push('缺少详细 docs');
			findings.push({
				file: `${sampleRel}/docs/README.md`,
				line: 1,
				severity: 'warning',
				rule: 'code-docs-detailed-missing',
				message: `${sampleRel} 有 ${code.length} 个代码文件，但 docs/ 下没有代码说明文档。`
			});
		}
		if (newestCodeAt !== null && newestDocsAt !== null && newestCodeAt > newestDocsAt) {
			issues.push('docs 落后于代码');
			findings.push({
				file: `${sampleRel}/docs`,
				line: 1,
				severity: 'warning',
				rule: 'code-docs-stale',
				message: `${sampleRel} 的代码比 docs 更新；请确认 README/docs 是否遗漏了最新行为。`
			});
		}

		entries.push({
			sample: sampleRel,
			readme,
			docs: docs.length,
			meaningfulDocs: meaningfulDocs.length,
			codeFiles: code.length,
			status: readme ? (meaningfulDocs.length > 0 ? 'complete' : 'readme-only') : 'missing',
			newestCodeAt,
			newestDocsAt,
			issues
		});
	}

	// Validate the machine-readable case index against the files it names.
	const indexFile = join(root, 'samples', 'case-index.json');
	let indexedCases = 0;
	if (!existsSync(indexFile)) {
		findings.push({
			file: 'samples/case-index.json',
			line: 1,
			severity: 'warning',
			rule: 'case-index-missing',
			message: '没有 samples/case-index.json，无法核对案例的 source/sample 映射。'
		});
	} else {
		try {
			const index = JSON.parse(readFileSync(indexFile, 'utf8'));
			for (const entry of Array.isArray(index.cases) ? index.cases : []) {
				indexedCases += 1;
				const id = String(entry.id ?? `case${indexedCases}`);
				const source = String(entry.source ?? '');
				const sample = String(entry.sample ?? '');
				if (!existsSync(join(root, source))) {
					findings.push({
						file: source || 'samples/case-index.json',
						line: 1,
						severity: 'error',
						rule: 'case-index-source-missing',
						message: `${id} 的 source 不存在：${source || '(空)'}`
					});
				}
				if (!existsSync(join(root, sample))) {
					findings.push({
						file: sample || 'samples/case-index.json',
						line: 1,
						severity: 'error',
						rule: 'case-index-sample-missing',
						message: `${id} 的 sample 不存在：${sample || '(空)'}`
					});
					continue;
				}
				if (!existsSync(join(root, sample, 'README.md'))) {
					findings.push({
						file: `${sample}/README.md`,
						line: 1,
						severity: 'warning',
						rule: 'case-index-readme-missing',
						message: `${id} 的样例目录没有 README.md。`
					});
				}
				if (source !== '' && existsSync(join(root, source))) {
					const heading = firstHeading(readFileSync(join(root, source), 'utf8'));
					const title = String(entry.title_zh ?? '');
					if (heading !== null && title !== '' && !heading.includes(title)) {
						findings.push({
							file: source,
							line: 1,
							severity: 'warning',
							rule: 'case-index-title-drift',
							message: `${id} 的案例标题与 case-index 不一致：${heading} ≠ ${title}`
						});
					}
				}
			}
		} catch (error) {
			findings.push({
				file: 'samples/case-index.json',
				line: 1,
				severity: 'error',
				rule: 'case-index-invalid',
				message: `case-index.json 无法解析：${String(error?.message ?? error)}`
			});
		}
	}

	const summary = {
		samples: entries.length,
		readmes: entries.filter((entry) => entry.readme).length,
		withDocs: entries.filter((entry) => entry.meaningfulDocs > 0).length,
		readmeOnly: entries.filter((entry) => entry.status === 'readme-only').length,
		missingReadme: entries.filter((entry) => entry.status === 'missing').length,
		codeFiles: entries.reduce((sum, entry) => sum + entry.codeFiles, 0),
		docsFiles: entries.reduce((sum, entry) => sum + entry.docs, 0),
		findings: findings.length,
		indexedCases
	};
	return {
		generatedAt: new Date().toISOString(),
		summary,
		entries,
		findings,
		text: [
			`代码文档覆盖 · ${root}`,
			`样例目录 ${summary.samples} 个，README ${summary.readmes} 个，详细 docs ${summary.withDocs} 个，代码文件 ${summary.codeFiles} 个，docs 文件 ${summary.docsFiles} 个。`,
			...entries.map((entry) => `${entry.sample}  ${entry.status}  code=${entry.codeFiles} docs=${entry.docs}${entry.issues.length > 0 ? `  (${entry.issues.join('；')})` : ''}`),
			...findings.map((finding) => `[${finding.severity}] ${finding.rule} ${finding.file}: ${finding.message}`)
		].join('\n')
	};
}
