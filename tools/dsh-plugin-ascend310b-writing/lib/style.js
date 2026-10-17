/**
 * Prose-style analysis: find the writing that reads like generated marketing
 * copy rather than a textbook.
 *
 * This module is deliberately *not* a rule set like the others. It never
 * produces an error, it never blocks a push, and it does not claim a phrase is
 * wrong — a phrase is wrong or right only in its sentence. What it does is
 * narrow a 250,000-character book down to the few dozen sentences that a human
 * should read once, which is the part that actually improves writing.
 *
 * The lexicon was calibrated against this book rather than assumed:
 *
 * - 首先 (44 occurrences) is **excluded**: the sample showed every one is a
 *   legitimate procedure word ("首先调用 acl.init"). Reporting it would train
 *   the author to ignore the tool.
 * - 营销腔 dominates the real signal: 极致 17, 出色 8, 无缝 7, plus 杀手锏 /
 *   全能型选手 / 护城河 / 脱颖而出. Those are product-launch words in a
 *   teaching text.
 * - 空转套话 and 元话语 are rarer but unambiguous when they appear.
 *
 * A hit is a candidate for *reading*, not a defect. The skill carries the
 * rewrite guidance; the judgment stays with the author.
 *
 * @module dsh-plugin-ascend310b-writing/style
 */

import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

/**
 * The three groups of phrasing worth reading again.
 *
 * `note` is what the rewrite should do instead — it is guidance, not a rule.
 */
export const LEXICON = {
	营销腔: {
		note: '换成可验证的事实：数字、接口名、行为对比。形容词堆砌删掉不影响信息量。',
		phrases: [
			'极致', '出色', '卓越', '非凡', '完美', '无缝', '一站式', '全方位',
			'赋能', '助力', '打造', '引领', '重塑', '颠覆', '革命性', '里程碑',
			'杀手锏', '出众', '脱颖而出', '遥遥领先', '如虎添翼', '不二之选', '护城河',
			'全能型', '极佳', '强大', '卓越性能', '实力显著'
		]
	},
	空转套话: {
		note: '整句删掉通常不丢信息；若确实要提醒，就把提醒本身写具体（"注意：此处需先 source set_env.sh"）。',
		phrases: [
			'综上所述', '总而言之', '简而言之', '众所周知', '不难发现', '由此可见',
			'值得注意的是', '值得一提的是', '需要指出的是', '毋庸置疑', '显而易见'
		]
	},
	元话语: {
		note: '直接进入内容；读者不需要被告知"接下来要讲什么"。',
		phrases: ['本文将', '本节将', '下面让我们', '让我们一起来', '跟随笔者', '读者可以', '接下来我们将']
	},
	对话残留: {
		note: '这是从对话里带进正文的称呼或收尾语，教材里应删掉或改成直接陈述。',
		phrases: [
			'正如您所指出的', '正如您所说', '您提到', '您所问', '您的问题', '希望对您有帮助',
			'希望这个回答', '如有需要', '以上回答', '如果您还有',
			// Public 中文去 AI 味 checklists list these as chat openers that survive
			// into finished documents far too often.
			'问得好', '你说得对', '当然可以', '下面是改写', '下面是修改'
		]
	},
	名词化空动词: {
		// 把动作名词化，再用一个空动词把它激活——中文 AI 文本的通病。
		// 来源：anti-vibe-writing 的「名词化动词搭子」。
		note: '直接写动词："优化算法"而不是"对算法进行优化"；"提升效率"而不是"使效率得到提升"。',
		phrases: [
			'对算法进行', '进行分析', '进行优化', '进行测试', '进行验证', '进行配置', '进行部署',
			'进行训练', '进行推理', '进行说明', '进行对比', '加以说明', '加以解决', '给予支持',
			'采取措施', '做出贡献', '起到了', '发挥了'
		]
	},
	程度虚词: {
		note: '删掉几乎不损失信息；每千字超过 5 个就值得通读一遍。',
		phrases: ['非常', '极其', '相当', '十分', '特别', '格外', '极度']
	},
	无源权威: {
		// 教材里的伪引用：给判断挂一个不存在的靠山。命中率低，一旦出现就是硬伤。
		note: '说得清是谁说的、哪来的数据就写清楚（附链接或出处）；说不清就删掉这层铺垫，直接给判断。',
		phrases: ['研究表明', '数据显示', '业内普遍认为', '专家指出', '调查显示', '据统计', '相关研究显示']
	},
	机器残留: {
		// 从模型输出里直接粘进正文的 token 或角标。见到就是硬伤，没有判断余地。
		note: '从模型输出里带出来的 token / 角标，直接删。',
		phrases: ['oaicite', 'turn0search', 'turn0view', '[cite:', '<|endoftext|>']
	}
};

/**
 * 排版痕迹：数得清、判得准的机器写法。来源是公开的中文去 AI 味清单
 * （anti-vibe-writing、stop-slop-zh），它们都把破折号和省略号列在最前。
 */
export const TYPOGRAPHY = [
	{
		name: '破折号',
		pattern: /——/gu,
		note: '一段里出现两次以上基本是机器手笔；按它在干的活换成句号、逗号、括号或冒号。'
	},
	{
		name: '三点省略号',
		pattern: /(?<![.\w/])\.\.\.(?![.\w])/gu,
		note: '中文省略号用「……」，不用三个半角点。'
	},
	{
		name: '六角括号',
		pattern: /【[^】]*】/gu,
		note: '【】段头是营销/公众号排版；教材里直接说事。'
	}
];

/** 对举句式：中文 AI 文本的高频节奏，两处以上连用才提示。 */
const PAIRED = [
	{ name: '不仅…而且', pattern: /不仅[^。；\n]{0,40}(?:而且|更|还)/u },
	{ name: '既…又', pattern: /既[^。；\n]{0,30}又[^。；\n]{0,20}/u },
	{ name: '不是…而是', pattern: /不是[^。；\n]{0,30}而是/u },
	// 三短句排比是中文 AI 最容易被认出的节奏，公开清单把它排在最前。
	{ name: '三连排比', pattern: /更[^，。；、\n]{1,8}、更[^，。；、\n]{1,8}/u }
];

/**
 * Count Chinese characters the same way the audit does.
 *
 * @param {string} text - the source.
 * @returns {number} The character count.
 */
function countCjk(text) {
	return (text.match(/[\u4e00-\u9fff]/gu) ?? []).length;
}

/**
 * Remove fenced blocks and inline code so typography counts prose only.
 *
 * Measured on the real book: all 71 three-dot ellipses were inside code
 * (`venc_send_frame(...)`, `bgr[..., ::-1]`), so counting the raw text would
 * report 71 false positives for a rule that has zero real hits.
 *
 * @param {string} text - the source.
 * @returns {string} The text with code replaced by spaces.
 */
export function stripCode(text) {
	return text
		.replace(/^\s*(```|~~~)[\s\S]*?^\s*\1[^\n]*$/gmu, ' ')
		.replace(/`[^`\n]*`/gu, ' ');
}

/**
 * Split prose into paragraphs, ignoring frontmatter, headings and code fences.
 *
 * The statistics below describe running text; counting a code block as a
 * paragraph would make every chapter look uniform.
 *
 * @param {string} text - the source.
 * @returns {string[]} Paragraph texts.
 */
export function proseParagraphs(text) {
	const paragraphs = [];
	let inFence = false;
	let buffer = [];
	const flush = () => {
		const joined = buffer.join('').trim();
		if (joined !== '') paragraphs.push(joined);
		buffer = [];
	};
	for (const line of text.split(/\r?\n/u)) {
		if (/^\s*```/u.test(line)) {
			inFence = !inFence;
			flush();
			continue;
		}
		if (inFence) continue;
		if (/^\s*(?:#{1,6}\s|\||---|>\s*$|<!--)/u.test(line) || line.trim() === '') {
			flush();
			continue;
		}
		// A list item starts a new paragraph for length purposes.
		if (/^\s*(?:[-*+]|\d+[.)])\s+/u.test(line)) flush();
		buffer.push(line.trim());
	}
	flush();
	return paragraphs;
}

/**
 * Summarise a list of numbers.
 *
 * @param {number[]} values - the samples.
 * @returns {{ mean: number, stdev: number, min: number, max: number }} The summary.
 */
function stats(values) {
	if (values.length === 0) return { mean: 0, stdev: 0, min: 0, max: 0 };
	const mean = values.reduce((sum, value) => sum + value, 0) / values.length;
	const variance = values.reduce((sum, value) => sum + (value - mean) ** 2, 0) / values.length;
	return {
		mean: Math.round(mean * 10) / 10,
		stdev: Math.round(Math.sqrt(variance) * 10) / 10,
		min: Math.min(...values),
		max: Math.max(...values)
	};
}

/**
 * Analyse one manuscript.
 *
 * @param {object} options - inputs.
 * @param {string} options.file - the repository-relative path.
 * @param {string} options.text - the source.
 * @returns {object} Per-file analysis: hits, density and shape statistics.
 */
export function analyzeText({ file, text }) {
	const lines = text.split(/\r?\n/u);
	const hits = [];
	for (const [group, entry] of Object.entries(LEXICON)) {
		for (const phrase of entry.phrases) {
			lines.forEach((line, index) => {
				// One report per line per phrase: a paragraph that repeats a word
				// five times is one thing for the author to look at, not five.
				const occurrences = line.split(phrase).length - 1;
				if (occurrences === 0) return;
				hits.push({
					file,
					line: index + 1,
					phrase,
					group,
					note: entry.note,
					count: occurrences,
					snippet: line.trim().slice(0, 120)
				});
			});
		}
	}
	const paired = [];
	for (const rule of PAIRED) {
		let ranges = 0;
		for (const line of lines) if (rule.pattern.test(line)) ranges += 1;
		if (ranges > 0) paired.push({ name: rule.name, count: ranges });
	}

	const paragraphs = proseParagraphs(text);
	const paragraphLengths = paragraphs.map((paragraph) => paragraph.length);
	const sentences = paragraphs.flatMap((paragraph) => paragraph.split(/[。！？!?]/u).filter((part) => part.trim() !== ''));
	const sentenceLengths = sentences.map((sentence) => sentence.length);
	const cjk = countCjk(text);
	const density = cjk === 0 ? 0 : Math.round((hits.length * 1000 * 100) / cjk) / 100;

	// Typographic machine traces, counted on prose only: a `...` inside an inline
	// code sample is the language, not a writing habit.
	const proseText = stripCode(text);
	const typography = [];
	for (const rule of TYPOGRAPHY) {
		const matches = proseText.match(rule.pattern) ?? [];
		if (matches.length > 0) typography.push({ name: rule.name, count: matches.length, note: rule.note, samples: matches.slice(0, 3) });
	}
	// A single long dash is a legitimate pause; two in one paragraph is a habit.
	const dashHeavy = proseParagraphs(proseText).filter((paragraph) => (paragraph.match(/——/gu) ?? []).length >= 2).length;

	return {
		file,
		cjk,
		hits,
		paired,
		density,
		typography,
		dashHeavy,
		paragraphs: { count: paragraphs.length, ...stats(paragraphLengths) },
		sentences: { count: sentences.length, ...stats(sentenceLengths) }
	};
}

/**
 * Analyse every manuscript in the configured parts.
 *
 * Decks are included: they are the most marketing-prone surface of the book
 * (they are, after all, promotional summaries), and they are short.
 *
 * @param {object} options - inputs.
 * @param {string} options.root - the repository root.
 * @param {object} options.settings - the resolved layout.
 * @param {string} [options.scope] - a part key, or `all`.
 * @param {string} [options.file] - restrict to one repository-relative file.
 * @returns {{ files: Array<object>, totals: object }} The analysis.
 */
export function analyzeStyle({ root, settings, scope = 'all', file }) {
	const files = [];
	for (const part of settings.parts) {
		if (part.files !== undefined) continue;
		if (scope !== 'all' && scope !== part.key) continue;
		const directory = join(root, part.dir);
		if (!existsSync(directory)) continue;
		for (const entry of readdirDir(directory)) {
			if (!entry.endsWith('.md')) continue;
			const rel = `${part.dir}/${entry}`;
			if (file !== undefined && rel !== file) continue;
			let text;
			try {
				text = readFileSync(join(root, rel), 'utf8');
			} catch {
				continue;
			}
			files.push(analyzeText({ file: rel, text }));
		}
	}
	const totals = {
		files: files.length,
		cjk: files.reduce((sum, entry) => sum + entry.cjk, 0),
		hits: files.reduce((sum, entry) => sum + entry.hits.length, 0),
		byGroup: Object.fromEntries(
			Object.keys(LEXICON).map((group) => [
				group,
				files.reduce((sum, entry) => sum + entry.hits.filter((hit) => hit.group === group).length, 0)
			])
		),
		// Typography is counted separately: it is a density signal, not a phrase list.
		byTypography: Object.fromEntries(
			TYPOGRAPHY.map((rule) => {
				const total = files.reduce(
					(sum, entry) => sum + (entry.typography.find((item) => item.name === rule.name)?.count ?? 0),
					0
				);
				return [rule.name, total];
			})
		),
		dashHeavyParagraphs: files.reduce((sum, entry) => sum + entry.dashHeavy, 0)
	};
	totals.density = totals.cjk === 0 ? 0 : Math.round((totals.hits * 1000 * 100) / totals.cjk) / 100;
	return { files, totals };
}

/**
 * Render the style report.
 *
 * @param {object} options - inputs.
 * @param {{ files: Array<object>, totals: object }} options.analysis - the analysis.
 * @param {number} [options.maxHits] - how many individual sentences to show.
 * @returns {string} The report.
 */
export function formatStyle({ analysis, maxHits = 30 }) {
	const { totals, files } = analysis;
	const lines = [
		'文风体检 · 只提示，不判定对错',
		'',
		`  ${totals.files} 个文件、${totals.cjk} 字；命中 ${totals.hits} 处，` +
			`密度 ${totals.density} 次/千字`,
		`  分项：${Object.entries(totals.byGroup).map(([group, count]) => `${group} ${count}`).join(' · ')}`,
		'',
		`  排版痕迹：${Object.entries(totals.byTypography).map(([name, count]) => `${name} ${count}`).join(' · ')}；` +
			`一段内两个以上破折号 ${totals.dashHeavyParagraphs} 段`,
		''
	];
	const ranked = files
		.filter((entry) => entry.hits.length > 0)
		.sort((left, right) => right.density - left.density);
	lines.push('密度最高的文件');
	for (const entry of ranked.slice(0, 8)) {
		lines.push(`  ${entry.file.padEnd(34)} ${String(entry.hits.length).padStart(3)} 处  ${entry.density} 次/千字`);
	}
	if (ranked.length === 0) lines.push('  （没有命中：这份稿子没有明显的套话腔）');

	const shown = ranked.flatMap((entry) => entry.hits).slice(0, maxHits);
	if (shown.length > 0) {
		lines.push('', '建议优先读一遍的句子（按分组给出改写方向）');
		for (const group of Object.keys(LEXICON)) {
			const groupHits = shown.filter((hit) => hit.group === group);
			if (groupHits.length === 0) continue;
			lines.push('', `【${group}】${LEXICON[group].note}`);
			for (const hit of groupHits) {
				lines.push(`  ${hit.file}:${hit.line}  「${hit.phrase}」${hit.count > 1 ? ` ×${hit.count}` : ''}`);
				lines.push(`    ${hit.snippet}`);
			}
		}
	}

	const shapes = files.filter((entry) => entry.paragraphs.count >= 8);
	if (shapes.length > 0) {
		lines.push('', '文字形状（仅供参考：段落长度过于均匀是机械感的一个来源）');
		for (const entry of shapes.slice(0, 6)) {
			lines.push(
				`  ${entry.file.padEnd(34)} 段 ${entry.paragraphs.count} 个，长度 ${entry.paragraphs.mean}±${entry.paragraphs.stdev}` +
					`（${entry.paragraphs.min}–${entry.paragraphs.max}）；句 ${entry.sentences.count} 个，长度 ${entry.sentences.mean}±${entry.sentences.stdev}`
			);
		}
	}
	const typographic = files
		.filter((entry) => entry.typography.length > 0)
		.sort((left, right) => {
			const total = (item) => item.typography.reduce((sum, rule) => sum + rule.count, 0);
			return total(right) - total(left);
		});
	if (typographic.length > 0) {
		lines.push('', '排版痕迹（密度比单次出现更能说明问题）');
		for (const entry of typographic.slice(0, 6)) {
			const detail = entry.typography.map((rule) => `${rule.name} ${rule.count}`).join('、');
			const heavy = entry.dashHeavy > 0 ? `；一段内两个以上破折号 ${entry.dashHeavy} 段` : '';
			lines.push(`  ${entry.file.padEnd(34)} ${detail}${heavy}`);
		}
	}

	const pairedTotal = files.reduce((sum, entry) => sum + entry.paired.reduce((inner, rule) => inner + rule.count, 0), 0);
	if (pairedTotal > 0) {
		const detail = files
			.filter((entry) => entry.paired.length > 0)
			.slice(0, 5)
			.map((entry) => `${entry.file}（${entry.paired.map((rule) => `${rule.name} ${rule.count}`).join('、')}）`)
			.join('；');
		lines.push('', `对举句式 ${pairedTotal} 处：${detail}`);
	}

	lines.push(
		'',
		'这些都是"值得读一遍"的候选，不是错误：同一个词在这个句子里可能恰好准确。',
		'改写方向见技能文档的「去 AI 味」一节；改完请复跑 ascend310b_writing_audit 确认没破坏结构。'
	);
	return lines.join('\n');
}

/** List a directory's entry names, or an empty array. */
function readdirDir(directory) {
	try {
		return readdirSync(directory);
	} catch {
		return [];
	}
}
