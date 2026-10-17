/**
 * Tutorial writing guard — Harness host half.
 *
 * Registers five model-facing tools over {@link ./audit-core.js},
 * {@link ./checks.js} and {@link ./doctor.js}, and, when the composition
 * carries a Web server, one read-only HTTP route the browser panel reads.
 * The tools work in every composition; the route is optional.
 *
 * @module dsh-plugin-ascend310b-writing
 */

import { defineTool } from '@deepseek-ai/dsh-tools';

import { PARTS, auditRepo, progressReport, resolveSettings, sortFindings } from './audit-core.js';
import {
	collectArtifactFindings,
	collectCommitFindings,
	collectFrozenNameFindings,
	collectRenderFindings,
	describeCommit,
	describeFrozenNames,
	evaluatePushGate
} from './checks.js';
import { diagnose } from './doctor.js';

/** Cordis plugin name used by loader diagnostics. */
export const name = 'dsh-plugin-ascend310b-writing';

/** The host services this plugin needs before it may mount. */
export const inject = ['tools'];

/** Exact route the browser panel reads its report from. */
const REPORT_PATH = '/ascend310b-writing/report.json';

/** Milliseconds a computed report stays cached for the HTTP route. */
const REPORT_TTL_MS = 5000;

/** The scope values both tools accept. */
const SCOPES = ['all', ...PARTS.map((part) => part.key)];

/**
 * Shared parameter spec for the part filter. An optional parameter omits
 * `required` entirely: the parameter-schema compiler rejects an explicit
 * `required: false`.
 */
const SCOPE_PARAMETER = {
	type: 'string',
	enum: SCOPES,
	description: 'Which manuscript part to inspect. Defaults to "all".'
};

/** Shared parameter spec for the repository root override. */
const ROOT_PARAMETER = {
	type: 'string',
	description:
		'Absolute repository root to inspect. Defaults to the configured repoRoot, then TUTORIAL_WRITING_ROOT; ' +
		'with none of the three set the call fails and names them, rather than guessing a path.'
};

/**
 * Parameter spec for the optional committed-tree check. Naming a revision runs
 * the reference check against what that commit actually contains, which is the
 * question a push asks and the working tree cannot answer.
 */
const REV_PARAMETER = {
	type: 'string',
	description:
		'Also verify that every relative Markdown reference resolves inside this commit, e.g. "HEAD" or ' +
		'"HEAD~1". Use it before pushing: a file present in the working tree but excluded from the commit ' +
		'shows up here as a broken link.'
};

/** Finding severity values, as the runtime narrowing set for both schemas. */
const SEVERITIES = ['error', 'warning', 'info'];

/** JSON Schema for one audit finding, shared by the tool output and the route. */
const FINDING_SCHEMA = {
	type: 'object',
	additionalProperties: false,
	properties: {
		rule: { type: 'string', required: true },
		severity: { type: 'string', required: true, enum: [...SEVERITIES] },
		file: { type: 'string', required: true },
		line: { type: 'integer', required: true },
		message: { type: 'string', required: true }
	}
};

/**
 * Resolve the repository root from a call argument, the plugin config, or the
 * environment, in that order.
 *
 * There is deliberately no built-in path. A hardcoded checkout would be host
 * coupling shipped inside a portable package, and the wrong root silently
 * audits the wrong book. A caller with nothing configured gets an error naming
 * the three ways to set one, and a machine that wants persistence puts the
 * value in its own profile config rather than in this code.
 *
 * @param {object} config - the plugin's resolved configuration.
 * @param {string} [argument] - the per-call override.
 * @returns {string} The absolute repository root to inspect.
 * @throws {Error} When no root is configured anywhere.
 */
function resolveRoot(config, argument) {
	if (typeof argument === 'string' && argument.trim() !== '') return argument.trim();
	if (typeof config?.repoRoot === 'string' && config.repoRoot.trim() !== '') return config.repoRoot.trim();
	const fromEnv = process.env.TUTORIAL_WRITING_ROOT;
	if (typeof fromEnv === 'string' && fromEnv.trim() !== '') return fromEnv.trim();
	throw new Error(
		'dsh-plugin-ascend310b-writing: 未配置仓库根目录。请任选其一——' +
			'① 给工具传 root 参数；' +
			'② 在 profile 的 cordis.patch.yml 里为 id `dsh-plugin-ascend310b-writing` 设置 config.repoRoot（本机持久值应放在这里）；' +
			'③ 设置环境变量 TUTORIAL_WRITING_ROOT。'
	);
}

/**
 * Resolve the layout for one repository: built-in defaults, then the
 * repository's own .tutorial-writing.json, then this plugin's configuration.
 *
 * @param {string} root - the repository root.
 * @returns {object} The compiled settings.
 */
function settingsFor(config, root) {
	return resolveSettings({ root, layout: config?.layout });
}

/**
 * Run the manuscript audit together with the cross-artifact checks and narrow
 * the merged result to the shape both surfaces share.
 *
 * The frozen-name cross-check always runs: it is a manuscript naming rule that
 * happens to live in AGENTS.md. The committed-tree check runs only when the
 * caller names a revision, because it answers a publishing question.
 *
 * @param {object} options - audit inputs.
 * @param {string} options.root - the repository root.
 * @param {string} options.scope - the part filter.
 * @param {number} options.maxFindings - cap on returned findings.
 * @param {string} [options.rev] - also check this commit's tree for broken references.
 * @returns {object} The tool-shaped audit result.
 */
function runAudit({ root, scope, maxFindings, rev }) {
	const settings = settingsFor(config, root);
	const frozen = collectFrozenNameFindings({ root, settings });
	const extraFindings = [...frozen.findings];
	const notes = [describeFrozenNames(frozen)];
	if (typeof rev === 'string' && rev.trim() !== '') {
		const commit = collectCommitFindings({ root, rev: rev.trim() });
		extraFindings.push(...commit.findings);
		notes.push(describeCommit(commit, rev.trim()));
	}
	const report = auditRepo({ root, scope, maxFindings, extraFindings, settings });
	return {
		text: [report.text, '', ...notes].join('\n'),
		summary: report.summary,
		findings: report.findings
	};
}

/**
 * Register the writing tools, and the panel's report route when a Web server
 * exists in this composition.
 *
 * @param {object} ctx - the registrant context carrying the tool registry.
 * @param {object} config - the plugin's resolved configuration.
 */
export function apply(ctx, config) {
	ctx.tools.register(
		defineTool({
			name: 'ascend310b_writing_audit',
			description:
				'Audit the tutorial manuscript for the repository\'s frozen naming rules and Markdown hygiene. ' +
				'Checks chapter/case/appendix title shape and punctuation, heading-level jumps, explicit heading ' +
				'anchor prefixes, missing image and link targets, sidebar coverage, Chinese half-width punctuation, ' +
				'trailing whitespace, CRLF line endings, and a missing final newline. ' +
				'It also cross-checks the frozen-name tables in AGENTS.md against the titles the manuscripts ' +
				'actually publish, in both directions. Pass `rev` before pushing to additionally verify that ' +
				'every relative reference resolves inside that commit. ' +
				'Run it before asking for a review, and after adding or renaming a chapter, case, or appendix.',
			parameters: {
				scope: SCOPE_PARAMETER,
				root: ROOT_PARAMETER,
				rev: REV_PARAMETER,
				maxFindings: {
					type: 'integer',
					description: 'Maximum number of findings to return, after ordering by severity. Defaults to 400.'
				}
			},
			output: {
				schema: {
					type: 'object',
					additionalProperties: false,
					properties: {
						text: { type: 'string', required: true },
						summary: {
							type: 'object',
							additionalProperties: false,
							required: true,
							properties: {
								files: { type: 'integer', required: true },
								errors: { type: 'integer', required: true },
								warnings: { type: 'integer', required: true },
								infos: { type: 'integer', required: true },
								truncated: { type: 'boolean', required: true }
							}
						},
						findings: { type: 'array', required: true, items: FINDING_SCHEMA }
					}
				},
				render: (_args, value) => [{ type: 'text', text: value.text }]
			},
			execute(args) {
				const root = resolveRoot(config, args.root);
				const scope = SCOPES.includes(args.scope) ? args.scope : 'all';
				const maxFindings = Number.isInteger(args.maxFindings) && args.maxFindings > 0 ? args.maxFindings : 400;
				return Promise.resolve(runAudit({ root, scope, maxFindings, rev: args.rev }));
			},
			presentCall: (args) => ({
				card: 'generic',
				title: '写作审计',
				kind: 'other',
				rawInput: args
			})
		})
	);

	ctx.tools.register(
		defineTool({
			name: 'ascend310b_writing_render',
			description:
				'Inspect the LaTeX build log for defects no Markdown check can see: a character missing from its ' +
				'font (which silently breaks that glyph in the PDF), an undefined cross-reference or citation, a ' +
				'hard LaTeX error, and boxes that overrun the page width badly enough to matter. ' +
				'It also reports whether the log is older than the newest manuscript, and whether the generated ' +
				'LaTeX has fallen behind its source: every explicit `{#id}` in the Markdown must appear as a ' +
				'`\\label{id}`, so a heading whose anchor never reached the PDF — because the line before it was ' +
				'not blank, or because an unescaped `_` swallowed the attribute block — is caught here. ' +
				'One missing glyph or anchor can break cross-references while the source still looks correct. ' +
				'Run it after ./convert-vuepress.sh, and fix the source Markdown rather than the generated LaTeX.',
			parameters: {
				root: ROOT_PARAMETER,
				logPath: {
					type: 'string',
					description: 'Explicit path to the build log. Defaults to <root>/latex/book.log.'
				}
			},
			output: {
				schema: {
					type: 'object',
					additionalProperties: false,
					properties: {
						text: { type: 'string', required: true },
						available: { type: 'boolean', required: true },
						stats: {
							type: 'object',
							additionalProperties: false,
							required: true,
							properties: {
								hardErrors: { type: 'integer', required: true },
								missingCharacters: { type: 'integer', required: true },
								undefinedReferences: { type: 'integer', required: true },
								overfull: { type: 'integer', required: true },
								stale: { type: 'boolean', required: true }
							}
						},
						findings: { type: 'array', required: true, items: FINDING_SCHEMA }
					}
				},
				render: (_args, value) => [{ type: 'text', text: value.text }]
			},
			execute(args) {
				const root = resolveRoot(config, args.root);
				const settings = settingsFor(config, root);
	const report = collectRenderFindings({ root, logPath: args.logPath, settings });
				// Freshness belongs here: a stale artifact is a build problem even
				// when the log itself is clean, and this is the build-facing tool.
				const artifacts = collectArtifactFindings({ root, settings });
				const findings = sortFindings([...report.findings, ...artifacts.findings]);
				const stats = report.stats ?? {
					hardErrors: 0,
					missingCharacters: 0,
					undefinedReferences: 0,
					overfull: 0,
					stale: false
				};
				return Promise.resolve({
					text: [
						report.text,
						'',
						// Not "stale": a missing anchor is either a stale artifact or a
						// heading the converter could not express, and the findings below
						// say which. The summary must not collapse the two.
						`产物新鲜度：${artifacts.pairs} 对源/产物、${artifacts.anchors} 个源锚点，` +
							`${artifacts.findings.length} 条产物问题（原因见下）。`
					].join('\n'),
					available: report.available,
					stats: {
						hardErrors: stats.hardErrors,
						missingCharacters: stats.missingCharacters,
						undefinedReferences: stats.undefinedReferences,
						overfull: stats.overfull,
						stale: stats.stale
					},
					findings
				});
			},
			presentCall: (args) => ({
				card: 'generic',
				title: '渲染体检',
				kind: 'other',
				rawInput: args
			})
		})
	);

	ctx.tools.register(
		defineTool({
			name: 'ascend310b_writing_push_check',
			description:
				'Decide whether this repository may be pushed, separating what can be checked mechanically from what ' +
				'only a person can assert. Blocking items: the manuscript audit (including the AGENTS.md frozen-name ' +
				'cross-check) and the coherence of the references inside the commit that would be pushed. Advisory ' +
				'items: manuscripts left uncommitted (a push publishes the commit, not your working tree), the LaTeX ' +
				'render verdict, and whether the generated artifacts have fallen behind their source. ' +
				'It always ends by naming the one thing no check can prove: that the user has confirmed the local ' +
				'preview. Run it before every push.',
			parameters: {
				root: ROOT_PARAMETER,
				rev: REV_PARAMETER
			},
			output: {
				schema: {
					type: 'object',
					additionalProperties: false,
					properties: {
						text: { type: 'string', required: true },
						ready: { type: 'boolean', required: true },
						blockingFailed: { type: 'array', required: true, items: { type: 'string' } },
						items: {
							type: 'array',
							required: true,
							items: {
								type: 'object',
								additionalProperties: false,
								properties: {
									id: { type: 'string', required: true },
									blocking: { type: 'boolean', required: true },
									ok: { type: 'boolean', required: true },
									detail: { type: 'string', required: true }
								}
							}
						}
					}
				},
				render: (_args, value) => [{ type: 'text', text: value.text }]
			},
			execute(args) {
				const root = resolveRoot(config, args.root);
				const rev = typeof args.rev === 'string' && args.rev.trim() !== '' ? args.rev.trim() : 'HEAD';
				const gate = evaluatePushGate({ root, rev, settings: settingsFor(config, root) });
				return Promise.resolve({
					text: gate.text,
					ready: gate.ready,
					blockingFailed: gate.blockingFailed,
					items: gate.items.map((item) => ({
						id: item.id,
						blocking: item.blocking,
						ok: item.ok,
						detail: item.detail
					}))
				});
			},
			presentCall: (args) => ({
				card: 'generic',
				title: '推送前检查',
				kind: 'other',
				rawInput: args
			})
		})
	);

	ctx.tools.register(
		defineTool({
			name: 'ascend310b_writing_progress',
			description:
				'Report writing progress for the tutorial manuscript: per-chapter Chinese character counts, ' +
				'heading, figure and code-block counts, estimated reading time, and any manuscript missing from ' +
				'the VuePress sidebar. Use it to size remaining work or to summarize the book.',
			parameters: {
				scope: SCOPE_PARAMETER,
				root: ROOT_PARAMETER
			},
			output: {
				schema: {
					type: 'object',
					additionalProperties: false,
					properties: {
						text: { type: 'string', required: true },
						totals: {
							type: 'object',
							additionalProperties: false,
							required: true,
							properties: {
								files: { type: 'integer', required: true },
								cjk: { type: 'integer', required: true },
								words: { type: 'integer', required: true },
								figures: { type: 'integer', required: true },
								headings: { type: 'integer', required: true },
								codeBlocks: { type: 'integer', required: true },
								readingMinutes: { type: 'integer', required: true }
							}
						},
						missingFromSidebar: { type: 'array', required: true, items: { type: 'string' } }
					}
				},
				render: (_args, value) => [{ type: 'text', text: value.text }]
			},
			execute(args) {
				const root = resolveRoot(config, args.root);
				const scope = SCOPES.includes(args.scope) ? args.scope : 'all';
				const report = progressReport({ root, scope, settings: settingsFor(config, root) });
				return Promise.resolve({
					text: report.text,
					totals: report.totals,
					missingFromSidebar: report.missingFromSidebar
				});
			},
			presentCall: (args) => ({
				card: 'generic',
				title: '写作进度',
				kind: 'other',
				rawInput: args
			})
		})
	);

	ctx.tools.register(
		defineTool({
			name: 'ascend310b_writing_toolchain',
			description:
				'Report what this machine can do with this repository: which external tools the LaTeX/PDF build needs are ' +
				'installed (bash, pandoc, latexmk, xelatex, python3, pnpm), which are missing, the per-platform command ' +
				'that installs what is missing, how this host will invoke the shell (Git Bash / WSL / POSIX), and which ' +
				'capabilities are therefore available. Run it first after installing the plugin, on a new machine, or ' +
				'whenever a build fails for an environment reason. It runs no build and writes nothing. Manuscript ' +
				'checks need none of these tools; only LaTeX/PDF output does.',
			parameters: {
				root: ROOT_PARAMETER
			},
			output: {
				schema: {
					type: 'object',
					additionalProperties: false,
					properties: {
						text: { type: 'string', required: true },
						platform: { type: 'string', required: true },
						missingRequired: { type: 'array', required: true, items: { type: 'string' } },
						missingOptional: { type: 'array', required: true, items: { type: 'string' } },
						capabilities: {
							type: 'array',
							required: true,
							items: {
								type: 'object',
								additionalProperties: false,
								properties: {
									id: { type: 'string', required: true },
									available: { type: 'boolean', required: true },
									detail: { type: 'string', required: true }
								}
							}
						}
					}
				},
				render: (_args, value) => [{ type: 'text', text: value.text }]
			},
			execute(args) {
				// A missing repository root is not an environment failure, so the
				// toolchain half still answers; only the repository section degrades.
				let root = null;
				try {
					root = resolveRoot(config, args.root);
				} catch {
					root = null;
				}
				const report = diagnose({ root, overrides: config?.toolchain ?? {} });
				return Promise.resolve({
					text: report.text,
					platform: process.platform,
					missingRequired: report.missingRequired,
					missingOptional: report.missingOptional,
					capabilities: report.capabilities.map((capability) => ({
						id: capability.id,
						available: capability.available,
						detail: capability.detail
					}))
				});
			},
			presentCall: (args) => ({
				card: 'generic',
				title: '工具链体检',
				kind: 'other',
				rawInput: args
			})
		})
	);

	// The browser panel needs the same data over HTTP. A composition without a
	// Web server (headless, ACP) simply keeps the tools and skips the route.
	ctx.inject(['webServer'], (scope) => {
		let cache;
		const report = (fresh) => {
			const now = Date.now();
			if (!fresh && cache !== undefined && now - cache.at < REPORT_TTL_MS) return cache.value;
			const root = resolveRoot(config);
			// The panel lists the same findings the tool reports, so the frozen-name
			// cross-check is merged here too. The LaTeX render check stays out: it
			// describes a build, not the manuscript the panel is about.
			const settings = settingsFor(config, root);
			const frozen = collectFrozenNameFindings({ root, settings });
			const audit = auditRepo({
				root,
				scope: 'all',
				maxFindings: 400,
				extraFindings: frozen.findings,
				settings
			});
			const progress = progressReport({ root, scope: 'all', settings });
			const value = {
				ok: true,
				root,
				generatedAt: new Date().toISOString(),
				parts: PARTS.map((part) => ({ key: part.key, label: part.label })),
				audit: { summary: audit.summary, findings: audit.findings },
				progress: {
					totals: progress.totals,
					parts: progress.parts,
					missingFromSidebar: progress.missingFromSidebar
				}
			};
			cache = { at: now, value };
			return value;
		};
		const handler = (request, response) => {
			try {
				const fresh = (request.url ?? '').includes('fresh=1');
				const body = JSON.stringify(report(fresh));
				response.writeHead(200, {
					'content-type': 'application/json; charset=utf-8',
					'cache-control': 'no-store',
					'content-length': Buffer.byteLength(body)
				});
				response.end(body);
			} catch (error) {
				const body = JSON.stringify({ ok: false, error: String(error?.message ?? error) });
				response.writeHead(500, {
					'content-type': 'application/json; charset=utf-8',
					'cache-control': 'no-store',
					'content-length': Buffer.byteLength(body)
				});
				response.end(body);
			}
		};
		scope.effect(
			() => scope.webServer.register({ kind: 'exact', path: REPORT_PATH, handler }),
			'ascend310b-writing: report route'
		);
	});
}
