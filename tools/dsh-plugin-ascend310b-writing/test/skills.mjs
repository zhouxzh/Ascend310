/**
 * Validate the writing plugin's skill split.
 *
 * The umbrella skill routes common rules; each writing target has its own
 * specialized skill. This test keeps the frontmatter, scope and routing honest.
 */

import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const skillsRoot = join(root, 'skills');
const expected = [
	'ascend310b-writing',
	'ascend310b-code-docs',
	'ascend310b-curriculum-planning',
	'ascend310b-writing-academic',
	'ascend310b-writing-case',
	'ascend310b-writing-docs',
	'ascend310b-writing-dot',
	'ascend310b-writing-latex',
	'ascend310b-writing-markdown',
	'ascend310b-writing-presentation',
	'ascend310b-writing-theory',
	'ascend310b-writing-vuepress'
];
const external = ['scientific-writing', 'nature-polishing'];
const supportOnly = ['nature-shared'];
const allowed = new Set([...expected, ...external, ...supportOnly]);
const failures = [];
const actual = readdirSync(skillsRoot, { withFileTypes: true })
	.filter((entry) => entry.isDirectory())
	.map((entry) => entry.name)
	.sort();

for (const name of expected) if (!actual.includes(name)) failures.push(`缺少 writing skill：${name}`);
for (const name of [...external, ...supportOnly]) {
	if (!actual.includes(name)) failures.push(`缺少 vendored skill/support：${name}`);
}
for (const name of actual) if (!allowed.has(name)) failures.push(`未登记的 writing skill：${name}`);

for (const name of expected) {
	const path = join(skillsRoot, name, 'SKILL.md');
	let text = '';
	try {
		text = readFileSync(path, 'utf8');
	} catch {
		failures.push(`${name}: SKILL.md 不存在`);
		continue;
	}
	if (!text.startsWith('---\n')) failures.push(`${name}: 缺少 frontmatter`);
	const end = text.indexOf('\n---', 4);
	if (end < 0) {
		failures.push(`${name}: frontmatter 未闭合`);
		continue;
	}
	const frontmatter = text.slice(4, end);
	const declared = /^name:\s*(\S+)/m.exec(frontmatter)?.[1];
	if (declared !== name) failures.push(`${name}: name 不匹配（${declared}）`);
	if (!/^description:\s*\S+/m.test(frontmatter)) failures.push(`${name}: 缺少 description`);
	if (text.length < 500) failures.push(`${name}: 内容过短`);
	if (!text.includes('310B')) failures.push(`${name}: 缺少 310B 仓库范围`);
	if (!/^## /m.test(text)) failures.push(`${name}: 缺少工作流章节`);
}

for (const name of external) {
	const path = join(skillsRoot, name, 'SKILL.md');
	let text = '';
	try {
		text = readFileSync(path, 'utf8');
	} catch {
		failures.push(`${name}: SKILL.md 不存在`);
		continue;
	}
	if (!text.startsWith('---\n')) failures.push(`${name}: 缺少 frontmatter`);
	const end = text.indexOf('\n---', 4);
	if (end < 0) {
		failures.push(`${name}: frontmatter 未闭合`);
		continue;
	}
	const frontmatter = text.slice(4, end);
	const declared = /^name:\s*(\S+)/m.exec(frontmatter)?.[1];
	if (declared !== name) failures.push(`${name}: name 不匹配（${declared}）`);
	if (!/^description:\s*\S+/m.test(frontmatter)) failures.push(`${name}: 缺少 description`);
	if (text.length < 2000) failures.push(`${name}: 内容过短`);
	if (!existsSync(join(skillsRoot, name, 'SOURCE.md'))) {
		failures.push(`${name}: 缺少上游来源说明`);
	}
	if (!text.includes('Ascend310 local routing guard') || !text.includes('Chinese learner material')) {
		failures.push(`${name}: 缺少将英文 skill 隔离出中文教材任务的本地路由门`);
	}
}
if (existsSync(join(skillsRoot, 'nature-shared', 'SKILL.md'))) {
	failures.push('nature-shared: 支持目录不能包含 SKILL.md，否则会暴露为独立 skill');
}
for (const required of ['core', 'journal-formats', 'scripts', 'manifest.yaml', 'LICENSE']) {
	if (!existsSync(join(skillsRoot, 'nature-shared', required))) {
		failures.push(`nature-shared: 缺少支持文件 ${required}`);
	}
}

const umbrella = readFileSync(join(skillsRoot, 'ascend310b-writing', 'SKILL.md'), 'utf8');
for (const name of expected.slice(1)) {
	if (!umbrella.includes(`\`${name}\``)) failures.push(`总 skill 未路由到 ${name}`);
}
const presentation = readFileSync(join(skillsRoot, 'ascend310b-writing-presentation', 'SKILL.md'), 'utf8');
if (!presentation.includes('GitHub Pages')) failures.push('Presentation skill 未规定 GitHub Pages 教材链接');
if (!presentation.includes('https://github.com/zhouxzh/Ascend310/tree/main/samples/')) {
	failures.push('Presentation skill 未规定 GitHub samples 链接');
}
if (!presentation.includes('https://310b.cn/experiment/case9.html')) {
	failures.push('Presentation skill 未锁定正式教材域名');
}
if (!presentation.includes('../experiment/*.md')) failures.push('Presentation skill 未明确禁止本地教材相对链接');
if (!umbrella.includes('已发布 Presentation')) failures.push('总 skill 未同步已发布课件链接规则');
if (!umbrella.includes('主内容 skill + 必要辅助 skill')) failures.push('总 skill 未允许按需组合主内容 skill 与辅助 skill');
if (umbrella.includes('一次写作任务只加载本 skill 加')) failures.push('总 skill 仍错误禁止案例正文与制作辅助 skill 组合');
for (const required of ['writing-case', 'writing-dot', 'writing-vuepress', 'writing-latex']) {
	if (!umbrella.includes(required)) failures.push(`总 skill 未说明跨对象辅助路由：${required}`);
}
const vuepress = readFileSync(join(skillsRoot, 'ascend310b-writing-vuepress', 'SKILL.md'), 'utf8');
if (!vuepress.includes('vuepress-theme-hope')) failures.push('VuePress skill 未锁定 hope 主题');
if (!vuepress.includes('base: /')) failures.push('VuePress skill 未锁定站点根路径');
if (!vuepress.includes('hostname: https://310b.cn')) failures.push('VuePress skill 未锁定正式域名');
if (!vuepress.includes('navbar.ts')) failures.push('VuePress skill 未覆盖 navbar 模板');
if (!vuepress.includes('BEGIN GENERATED PRESENTATION SIDEBAR')) {
	failures.push('VuePress skill 未保护生成侧栏');
}
if (!umbrella.includes('读者稿与生产备注')) failures.push('总 skill 未区分读者稿与生产备注');
const docsSkill = readFileSync(join(skillsRoot, 'ascend310b-writing-docs', 'SKILL.md'), 'utf8');
for (const required of ['samples/caseN/docs/NN-troubleshooting.md', 'CASE<N>-ISS-YYYYMMDD-NN', '根因状态', '回归结果', 'code-docs-manual-heading-number']) {
	if (!docsSkill.includes(required)) failures.push(`Docs skill 缺少测试问题台账规则：${required}`);
}
if (!docsSkill.includes('不得创建根 `docs/troubleshooting.md`')) failures.push('Docs skill 未禁止把案例台账放进根 docs');
if (!docsSkill.includes('根目录 `docs/` 仅供维护者') || !docsSkill.includes('不进入 VuePress')) failures.push('Docs skill 未说明根 docs 的维护者边界');
const codeDocs = readFileSync(join(skillsRoot, 'ascend310b-code-docs', 'SKILL.md'), 'utf8');
if (!codeDocs.includes('ascend310b-writing-case')) failures.push('Code docs 未把教学叙事交给 writing-case');
if (codeDocs.includes('ascend310b-board')) failures.push('Code docs 引用了不存在的 ascend310b-board skill');
const dotSkill = readFileSync(join(skillsRoot, 'ascend310b-writing-dot', 'SKILL.md'), 'utf8');
if (!dotSkill.includes('只负责辅助图像资产')) failures.push('DOT skill 未说明其辅助生产范围');
const latex = readFileSync(join(skillsRoot, 'ascend310b-writing-latex', 'SKILL.md'), 'utf8');
if (!latex.includes('不是正文写作提纲')) failures.push('LaTeX skill 未区分生成编号与案例教学结构');
for (const required of ['heading-manual-number', '1.1', '4.1', '自动编号']) {
	if (!umbrella.includes(required)) failures.push(`总 skill 缺少章节编号规则：${required}`);
}
const caseSkill = readFileSync(join(skillsRoot, 'ascend310b-writing-case', 'SKILL.md'), 'utf8');
for (const required of ['面向初学者', '学习目标', '先理解：核心概念', '先跑通：最小闭环', '形成性', '迁移练习', '工程 docs', '教师示范', '提示下练习', '独立任务', '报告式']) {
	if (!caseSkill.includes(required)) failures.push(`Case skill 缺少教学型案例规则：${required}`);
}
if (!existsSync(join(skillsRoot, 'ascend310b-writing-case', 'references', 'case-teaching-plan.md'))) {
	failures.push('Case skill 缺少 case-teaching-plan.md 教学设计模板');
}
if (!caseSkill.includes('不得链接或依赖任何其他仓库文档')) failures.push('Case skill 未保护 case4 自包含规则');
const theorySkill = readFileSync(join(skillsRoot, 'ascend310b-writing-theory', 'SKILL.md'), 'utf8');
if (!theorySkill.includes('worked example') || !theorySkill.includes('理解检查')) failures.push('Theory skill 未要求概念示例与理解检查');
const curriculumSkill = readFileSync(join(skillsRoot, 'ascend310b-curriculum-planning', 'SKILL.md'), 'utf8');
if (!curriculumSkill.includes('形成性检查点') || !curriculumSkill.includes('不适用的非教学参考对象须说明理由')) failures.push('Curriculum skill 未要求教学检查点及非教学对象说明');
const academicSkill = readFileSync(join(skillsRoot, 'ascend310b-writing-academic', 'SKILL.md'), 'utf8');
if (!academicSkill.includes('有界实验小节') || !academicSkill.includes('不要求每个概念解释')) failures.push('Academic skill 未将实验报告门限定到证据段落');
for (const name of ['ascend310b-writing', 'ascend310b-writing-markdown', 'ascend310b-writing-academic']) {
	const text = readFileSync(join(skillsRoot, name, 'SKILL.md'), 'utf8');
	for (const required of ['中西文', 'cjk-latin-space', '使用Python3.9']) {
		if (!text.includes(required)) failures.push(`${name}: 缺少作者中西文无空格规则：${required}`);
	}
}
for (const name of ['ascend310b-writing', 'ascend310b-writing-markdown']) {
	const text = readFileSync(join(skillsRoot, name, 'SKILL.md'), 'utf8');
	for (const required of ['表格、列表与段落', 'table-density', '编号列表', '概念']) {
		if (!text.includes(required)) failures.push(`${name}: 缺少表格/列表/段落选择规则：${required}`);
	}
}
const languageSkills = [
	'ascend310b-writing',
	'ascend310b-writing-case',
	'ascend310b-writing-theory',
	'ascend310b-writing-docs',
	'ascend310b-writing-presentation',
	'ascend310b-writing-markdown',
	'ascend310b-writing-academic',
	'ascend310b-writing-dot',
	'ascend310b-code-docs',
	'ascend310b-curriculum-planning'
];
for (const name of languageSkills) {
	const text = readFileSync(join(skillsRoot, name, 'SKILL.md'), 'utf8');
	for (const required of ['简体中文', 'prose-language']) {
		if (!text.includes(required)) failures.push(`${name}: 缺少中文正文硬规则：${required}`);
	}
}
for (const name of ['nature-polishing', 'scientific-writing']) {
	const text = readFileSync(join(skillsRoot, 'ascend310b-writing', 'SKILL.md'), 'utf8');
	if (!text.includes(name)) failures.push(`总 skill 未隔离英文专用 vendored skill：${name}`);
}
const numberingSkills = {
	'ascend310b-writing-case': ['heading-manual-number', '1.1', '4.1'],
	'ascend310b-writing-theory': ['heading-manual-number', '不手写章节编号'],
	'ascend310b-writing-markdown': ['1.1', '4.1', '自动编号'],
	'ascend310b-writing-latex': ['4.1', '4.2.1', '自动编号'],
	'ascend310b-writing-vuepress': ['1.1', '4.1', '自动编号']
};

for (const [name, requiredItems] of Object.entries(numberingSkills)) {
	const text = readFileSync(join(skillsRoot, name, 'SKILL.md'), 'utf8');
	for (const required of requiredItems) {
		if (!text.includes(required)) failures.push(`${name}: 缺少章节编号规则：${required}`);
	}
}
const imageStandards = {
	'ascend310b-writing': ['1600×1000', '2400×1500', '1.5 MB'],
	'ascend310b-writing-vuepress': ['1600×1000', '2400×1500', '2.5', '3 倍'],
	'ascend310b-writing-markdown': ['1600×1000', '2400×1500'],
	'ascend310b-writing-dot': ['1600×1000', '2400×1500']
};
for (const [name, requiredItems] of Object.entries(imageStandards)) {
	const text = readFileSync(join(skillsRoot, name, 'SKILL.md'), 'utf8');
	for (const required of requiredItems) {
		if (!text.includes(required)) failures.push(`${name}: 缺少图片尺寸规则：${required}`);
	}
}
const troubleshootingTemplate = join(skillsRoot, 'ascend310b-writing-docs', 'references', 'troubleshooting-template.md');
if (!existsSync(troubleshootingTemplate)) {
	failures.push('Docs skill 缺少 troubleshooting-template.md');
} else {
	const template = readFileSync(troubleshootingTemplate, 'utf8');
	for (const required of ['测试环境快照', '问题索引', '复现步骤', '原始证据', '根因状态', '回归验证', '剩余边界']) {
		if (!template.includes(required)) failures.push(`troubleshooting template 缺少：${required}`);
	}
}
const academic = readFileSync(join(skillsRoot, 'ascend310b-writing-academic', 'SKILL.md'), 'utf8');
for (const required of ['教材润色模式', 'Revision notes', '可直接替换的润色正文', '不能代替人工改写']) {
	if (!academic.includes(required)) failures.push(`Academic skill 缺少润色协议：${required}`);
}
const readerFacing = [
	'ascend310b-writing-case',
	'ascend310b-writing-dot',
	'ascend310b-writing-presentation',
	'ascend310b-writing-theory',
	'ascend310b-writing-markdown'
];
for (const name of readerFacing) {
	const text = readFileSync(join(skillsRoot, name, 'SKILL.md'), 'utf8');
	if (!text.includes('.dot') || !text.includes('字体名')) {
		failures.push(`${name}: 未禁止把 DOT 源路径或字体名写入读者正文`);
	}
}

const humanReviewSkills = [
	'ascend310b-writing',
	'ascend310b-writing-theory',
	'ascend310b-writing-case',
	'ascend310b-writing-docs',
	'ascend310b-writing-markdown',
	'ascend310b-writing-presentation',
	'ascend310b-writing-vuepress'
];
for (const name of humanReviewSkills) {
	const text = readFileSync(join(skillsRoot, name, 'SKILL.md'), 'utf8');
	if (!text.includes('human-review: pending')) failures.push(`${name}: 未声明 human-review: pending`);
	if (!text.includes('外部任务') && !text.includes('审查元数据')) failures.push(`${name}: 未将 human-review 状态限定在正文外`);
	if (!text.includes('check_page_layout.mjs')) failures.push(`${name}: 未要求检查本地页面排版`);
}
for (const name of ['ascend310b-writing', 'ascend310b-writing-theory', 'ascend310b-writing-case', 'ascend310b-writing-docs', 'ascend310b-writing-markdown', 'ascend310b-writing-vuepress']) {
	const text = readFileSync(join(skillsRoot, name, 'SKILL.md'), 'utf8');
	if (!text.includes('docs:dev')) failures.push(`${name}: 未要求启动 docs:dev 本地网页`);
}

if (failures.length > 0) {
	console.error(`writing skills test FAILED (${failures.length} 处):`);
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log(`writing skills test passed (${expected.length} 个本地 skills + ${external.length} 个 vendored skills)`);
console.log('  umbrella: theory / case / presentation / docs / code-docs / academic / curriculum / vuepress / latex / markdown / dot');
console.log('  vendored: scientific-writing / nature-polishing（nature-shared 为支持目录）');
