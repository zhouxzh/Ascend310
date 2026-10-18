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
	collectAssetFindings,
	collectCommitFindings,
	collectFrozenNameFindings,
	collectLatexChapterFindings,
	collectRenderFindings,
	describeCommit,
	describeFrozenNames,
	evaluatePushGate
} from './checks.js';
import { diagnose } from './doctor.js';
import { collectVuepressFindings } from './vuepress.js';
import { collectCodeFindings } from './code.js';
import { collectDeckFindings } from './deck.js';
import { collectSiteFindings, describeSite } from './site.js';
import { collectFigureFindings, describeFigures } from './figures.js';
import { collectCoursewareFindings } from './courseware.js';
import { analyzeStyle, formatStyle } from './style.js';
import { planBuild, resolveBuildEnvironment, runBuild } from './build.js';

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

/** The build targets the build tool accepts. */
const BUILD_TARGETS = ['latex', 'site', 'all'];

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
/**
 * One line describing the artifacts the manuscript audit does not cover.
 *
 * @param {object} assets - the result of the asset collector.
 * @returns {string} The summary line.
 */
/**
 * One line describing the site wiring the manuscript audit does not cover.
 *
 * @param {object} vuepress - the result of the VuePress collector.
 * @returns {string} The summary line.
 */
/**
 * One line describing the manuscript-to-code cross-check.
 *
 * @param {object} code - the result of the code collector.
 * @returns {string} The summary line.
 */
/**
 * One line describing the presentation-deck checks.
 *
 * @param {object} deck - the result of the deck collector.
 * @returns {string} The summary line.
 */
function describeDeck(deck) {
	const blocking = deck.findings.filter((finding) => finding.severity === 'error').length;
	return `课件版式：${deck.decks} 份、${deck.slides} 页（${blocking} 页溢出/口径问题）。`;
}

function describeCode(code) {
	const missing = code.findings.filter((finding) => finding.rule === 'sample-path-missing').length;
	const generated = code.findings.length - missing;
	return `代码交叉检查：${code.manuscripts} 个文件、${code.tokens} 处路径引用（${missing} 处失效、${generated} 处运行时产物）。`;
}

function describeVuepress(vuepress) {
	if (!vuepress.available) return '站点配置：没有找到 src/.vuepress 的配置文件，跳过。';
	const broken = vuepress.findings.filter((finding) => finding.severity === 'error').length;
	const drift = vuepress.findings.length - broken;
	return `站点配置：${vuepress.pages} 条路由、${vuepress.decks} 个放映页链接（${broken} 条断链、${drift} 条漂移）。`;
}

function describeAssets(assets) {
	const slides = `${assets.decks} 个配套演示（放映页过期 ${assets.slidesStale}、未构建 ${assets.slidesMissing}）`;
	const site = !assets.site.available
		? '站点尚未构建'
		: assets.site.stale
			? `站点 dist 早于 ${assets.site.newestSource?.file ?? '最新源'}`
			: '站点 dist 是新的';
	return `交叉产物：${slides}；${site}。`;
}

function settingsFor(config, root) {
	return resolveSettings({ root, layout: config?.layout });
}

/**
 * Render the build preflight: what would run, and why it cannot.
 *
 * @param {object} input - the plan pieces.
 * @param {object} input.plan - the plan from the build module.
 * @param {string} input.root - the repository root.
 * @param {string} input.target - the requested target.
 * @param {object} input.environment - the resolved build environment.
 * @returns {string} The report text.
 */
function formatBuildPlan({ plan, root, target, environment }) {
	const lines = [`构建预检 · ${root} · target=${target}`, '', `环境适配器：${environment.adapter.label}`];
	for (const component of environment.components) {
		if (!['bash', 'pandoc', 'latexmk', 'xelatex', 'python3', 'pnpm', 'node'].includes(component.id)) continue;
		const mark = component.ok ? 'OK  ' : component.required ? '缺失' : '可选';
		lines.push(`  [${mark}] ${component.id.padEnd(8)} ${component.ok ? component.version ?? '' : component.reason ?? ''}`);
	}
	lines.push(`  · ${environment.adapter.pythonPlan().note}`);

	lines.push('');
	if (plan.ok) lines.push('预检通过，可以构建。');
	else {
		lines.push('现在不能构建：');
		for (const blocker of plan.blockers) lines.push(`  ✗ ${blocker}`);
	}

	if (plan.steps.length > 0) {
		lines.push('', '将要执行的步骤：');
		for (const step of plan.steps) {
			lines.push(`  · ${step.label}`);
			lines.push(`    ${step.command} ${step.args.map((arg) => (arg.includes(' ') ? JSON.stringify(arg) : arg)).join(' ')}`);
			if (step.note !== undefined) lines.push(`    ${step.note}`);
		}
	}
	for (const note of plan.notes) lines.push(`  · ${note}`);

	lines.push('', '这是预检：没有执行任何命令，也没有写入任何文件。');
	lines.push('确认构建请带 confirm: true —— 它会启动一个后台任务，用 job_output 读输出、job_kill 停止。');
	return lines.join('\n');
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
function runAudit({ root, scope, maxFindings, rev, config }) {
	const settings = settingsFor(config, root);
	const frozen = collectFrozenNameFindings({ root, settings });
	const extraFindings = [...frozen.findings];
	const notes = [describeFrozenNames(frozen)];
	if (typeof rev === 'string' && rev.trim() !== '') {
		const commit = collectCommitFindings({ root, rev: rev.trim() });
		extraFindings.push(...commit.findings);
		notes.push(describeCommit(commit, rev.trim()));
	}
	const assets = collectAssetFindings({ root, settings });
	extraFindings.push(...assets.findings);
	notes.push(describeAssets(assets));
	const vuepress = collectVuepressFindings({ root, settings, frozenRows: frozen.tableRows });
	extraFindings.push(...vuepress.findings);
	notes.push(describeVuepress(vuepress));
	const code = collectCodeFindings({ root, settings });
	extraFindings.push(...code.findings);
	notes.push(describeCode(code));
	const deck = collectDeckFindings({ root, settings });
	extraFindings.push(...deck.findings);
	notes.push(describeDeck(deck));
	const courseware = collectCoursewareFindings({ root, settings });
	extraFindings.push(...courseware.findings);
	const latexMap = collectLatexChapterFindings({ root, settings });
	extraFindings.push(...latexMap.findings);
	const site = collectSiteFindings({ root, settings });
	extraFindings.push(...site.findings);
	notes.push(describeSite(site));
	const figures = collectFigureFindings({ root, settings });
	extraFindings.push(...figures.findings);
	notes.push(describeFigures(figures));
	if (latexMap.available) {
		notes.push(`PDF 章节表：${latexMap.chapters} 章，与源文比对 ${latexMap.findings.length} 处不一致。`);
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
				return Promise.resolve(runAudit({ root, scope, maxFindings, rev: args.rev, config }));
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

	ctx.tools.register(
		defineTool({
			name: 'ascend310b_writing_style',
			description:
				'Report where the prose reads like generated marketing copy instead of a textbook, and why. It groups ' +
				'candidates into 营销腔 (product-launch adjectives), 空转套话 (filler that can be deleted whole), ' +
				'元话语 (telling the reader what you are about to say) and 对话残留 (chat phrasing that leaked into the ' +
				'manuscript), and also reports paragraph/sentence shape and paired constructions. Every hit is a ' +
				'candidate for a human to read once — none of it is an error, nothing here blocks a push, and a phrase ' +
				'may be exactly right in its own sentence. Use it when asked to 润色 or 去 AI 味, then rewrite following ' +
				'the skill and re-run ascend310b_writing_audit to confirm the structure still holds.',
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
						files: { type: 'integer', required: true },
						cjk: { type: 'integer', required: true },
						hits: { type: 'integer', required: true },
						density: { type: 'number', required: true }
					}
				},
				render: (_args, value) => [{ type: 'text', text: value.text }]
			},
			execute(args) {
				const root = resolveRoot(config, args.root);
				const analysis = analyzeStyle({
					root,
					settings: settingsFor(config, root),
					scope: SCOPES.includes(args.scope) ? args.scope : 'all'
				});
				return Promise.resolve({
					text: formatStyle({ analysis }),
					files: analysis.totals.files,
					cjk: analysis.totals.cjk,
					hits: analysis.totals.hits,
					density: analysis.totals.density
				});
			},
			presentCall: (args) => ({
				card: 'generic',
				title: '文风体检',
				kind: 'other',
				rawInput: args
			})
		})
	);
	// The build tool runs as a platform background job: a full book build takes
	// minutes, and the caller should keep its turn.
	//
	// Registration copies the shipped shell tools exactly, because the obvious
	// version does not work: `ctx.tools.register` must be called through the
	// OUTER context. Registering through the injected scope's own `tools` throws
	// inside the callback, which Cordis swallows — the tool then silently never
	// appears. The synchronous `ctx.get("jobs")` check registers a variant
	// without the service first, and the inject callback replaces it.
	const buildTool = (jobs) =>
		defineTool({
			name: 'ascend310b_writing_build',
			description:
				'Build the book through the repository\'s own converters, then verify what was produced. ' +
				'Without confirm: true it only runs a preflight and reports the exact command it would run — nothing ' +
				'is executed and nothing is written. With confirm: true it starts a background job (read it with ' +
				'job_output, stop it with job_kill) that runs convert-vuepress.sh for the LaTeX/PDF path and/or ' +
				'pnpm run docs:build for the site, then immediately reads the fresh log for missing glyphs and checks ' +
				'the generated anchors against the source. Use it instead of hand-assembling the shell invocation: it ' +
				'discovers whether the shell is Git Bash or WSL, spells the checkout path for that shell, provides a ' +
				'temporary python3 when the host has only a conda interpreter, and retries the known PDF file-lock ' +
				'failure from the already-generated book.xdv.',
			parameters: {
				target: {
					type: 'string',
					enum: [...BUILD_TARGETS],
					description: 'What to build. Defaults to "latex".'
				},
				confirm: {
					type: 'boolean',
					description: 'Must be true to actually build. Without it the call is a read-only preflight.'
				},
				root: ROOT_PARAMETER
			},
			output: {
				schema: {
					type: 'object',
					additionalProperties: false,
					properties: {
						text: { type: 'string', required: true },
						started: { type: 'boolean', required: true },
						jobId: { type: 'string', required: true },
						target: { type: 'string', required: true },
						blockers: { type: 'array', required: true, items: { type: 'string' } }
					}
				},
				render: (_args, value) => [{ type: 'text', text: value.text }]
			},
			execute(args, exec) {
				const root = resolveRoot(config, args.root);
				const settings = settingsFor(config, root);
				const environment = resolveBuildEnvironment({ overrides: config?.toolchain ?? {} });
				const target = BUILD_TARGETS.includes(args.target) ? args.target : 'latex';
				const plan = planBuild({
					root,
					target,
					adapter: environment.adapter,
					components: environment.components,
					settings
				});

				if (args.confirm !== true) {
					return Promise.resolve({
						text: formatBuildPlan({ plan, root, target, environment }),
						started: false,
						jobId: '',
						target,
						blockers: plan.blockers
					});
				}
				if (jobs === undefined) {
					throw new Error(
						'这个组成没有后台任务服务（jobs），构建任务无法启动。' +
							'它需要 dsh-jobs-local 与 dsh-tool-jobs；缺少时请改用 ./convert-vuepress.sh ' +
							'+ ascend310b_writing_render（预检本身仍然可用，不带 confirm 即可）。'
					);
				}
				if (!plan.ok) {
					throw new Error(`无法开始构建（预检未通过）：${plan.blockers.join('；')}`);
				}

				let running = null;
				const jobId = jobs.start({
					kind: 'ascend310b-build',
					label: `${target} 构建`,
					...(exec?.agent === undefined ? {} : { owner: exec.agent.id }),
					run(handle) {
						const done = runBuild({
							plan,
							root,
							adapter: environment.adapter,
							settings,
							onChunk: (text, channel) => handle.append(text, { channel }),
							onSpawn: (child) => {
								running = child;
							}
						})
							.then((result) => ({
								status: result.ok ? 'completed' : 'failed',
								detail: `${result.detail}\n${result.summary.text}`
							}))
							.catch((error) => ({ status: 'failed', detail: String(error?.message ?? error) }));
						return {
							done,
							cancel() {
								try {
									running?.kill();
								} catch {
									// The process may have exited between the read and the kill.
								}
							}
						};
					}
				});

				return Promise.resolve({
					text: [
						`已启动后台构建任务：${jobId}（target=${target}）`,
						'',
						'它会在完成后自行结束；用 job_output 读输出，用 job_kill 停止。',
						'构建结束后同一个任务里会跑渲染体检与产物锚点核对，结论随任务一起给你。',
						'',
						formatBuildPlan({ plan, root, target, environment })
					].join('\n'),
					started: true,
					jobId: String(jobId),
					target,
					blockers: []
				});
			},
			presentCall: (args) => ({
				card: 'generic',
				title: '构建',
				kind: 'other',
				rawInput: args
			})
		});

	// A composition without the job service still gets the tool, and the call
	// explains what is missing instead of the tool simply not existing.
	let foregroundOnly = ctx.get('jobs') === undefined ? ctx.tools.register(buildTool(undefined)) : undefined;
	ctx.inject(['jobs'], (jobCtx) => {
		foregroundOnly?.();
		foregroundOnly = undefined;
		const unregister = ctx.tools.register(buildTool(jobCtx.jobs));
		jobCtx.effect(
			() => () => {
				unregister();
			},
			'tutorial-writing: build tool'
		);
	});
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
			const assets = collectAssetFindings({ root, settings });
			const vuepress = collectVuepressFindings({ root, settings, frozenRows: frozen.tableRows });
			const code = collectCodeFindings({ root, settings });
			const deck = collectDeckFindings({ root, settings });
			const courseware = collectCoursewareFindings({ root, settings });
			const latexMap = collectLatexChapterFindings({ root, settings });
			const site = collectSiteFindings({ root, settings });
			const figures = collectFigureFindings({ root, settings });
			const audit = auditRepo({
				root,
				scope: 'all',
				maxFindings: 400,
				extraFindings: [...frozen.findings, ...assets.findings, ...vuepress.findings, ...code.findings, ...deck.findings, ...courseware.findings, ...latexMap.findings, ...site.findings, ...figures.findings],
				settings
			});
			const progress = progressReport({ root, scope: 'all', settings });
			const value = {
				ok: true,
				root,
				jobsAvailable: ctx.get('jobs') !== undefined,
				// Registration is otherwise invisible until someone calls the tool;
				// reporting it here lets the verification script prove it happened.
				toolRegistered: (() => {
					try {
						return ctx.tools.get('ascend310b_writing_build') !== undefined;
					} catch {
						return false;
					}
				})(),
				generatedAt: new Date().toISOString(),
				parts: settings.parts.map((part) => ({ key: part.key, label: part.label })),
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
