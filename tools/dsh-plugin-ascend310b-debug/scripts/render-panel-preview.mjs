/**
 * Render the panel to a standalone HTML preview.
 *
 * The browser half is a plain module that registers React elements, so the same
 * tree can be drawn into HTML here without a browser. This is a preview of the
 * panel's structure and wording; the real panel runs inside DSH with the client
 * slot registry.
 *
 * Usage: node scripts/render-panel-preview.mjs <probe transcript> <output.html>
 */

import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { parseProbeOutput } from '../lib/probe.js';
import { assessVersionPairing } from '../lib/compat.js';
import { loadSampleIndex } from '../lib/report.js';
import { entriesByArea } from '../lib/knowledge.js';

const here = dirname(fileURLToPath(import.meta.url));
const pluginRoot = join(here, '..');

const transcriptPath = process.argv[2];
const outPath = process.argv[3] ?? join(pluginRoot, 'data', 'panel-preview.html');
const svgPath = process.argv[4] ?? outPath.replace(/\.html?$/i, '') + '.svg';
if (!transcriptPath) {
	console.error('usage: node scripts/render-panel-preview.mjs <probe transcript> [output.html] [output.svg]');
	process.exit(2);
}

const report = parseProbeOutput(readFileSync(transcriptPath, 'utf8'));
const scope = report.scope ?? null;
const facts = report.facts;
const fact = (key) => (facts[key] === undefined ? null : facts[key].value);
const pairing = assessVersionPairing(facts);
const index = loadSampleIndex(JSON.parse(readFileSync(join(pluginRoot, 'data', 'sample-index.json'), 'utf8')));
const areas = [...entriesByArea()].map(([area, entries]) => ({ area, entries }));
const knowledgeTotal = areas.reduce((total, area) => total + area.entries.length, 0);
const sampleTotals = {
	total: index.entries.length,
	withEntry: index.entries.filter((entry) => typeof entry.entry === 'string' && entry.entry !== '').length,
	withTest: index.entries.filter((entry) => typeof entry.test === 'string' && entry.test !== '').length
};

const rows = [
	['芯片', `${report.board?.chip ?? '未采集'} / ${report.board?.soc ?? '未采集'}`],
	['档位', report.board?.tier ?? '未采集'],
	['健康', report.board?.health ?? '未采集'],
	['操作系统', fact('os')],
	['内核', fact('kernel')],
	['架构', fact('arch')],
	['npu-smi', fact('npuSmiVersion')],
	['CANN', fact('cannVersion')],
	['驱动', fact('driverVersion')],
	['固件', fact('firmwareVersion')],
	['Python', [fact('pythonExecutable'), fact('pythonVersion')].filter(Boolean).join(' ') || null],
	['aclImport（面板取值）', fact('aclImport')],
	['aclImportSourced（source CANN 后）', fact('aclImportSourced')],
	['aclImportBare（裸 SSH）', fact('aclImportBare')],
	['ATC', fact('atcVersion')],
	['设备节点', fact('deviceNodes')]
];

const escape = (value) =>
	String(value ?? '未采集').replace(/[&<>"]/g, (character) => ({
		'&': '&amp;',
		'<': '&lt;',
		'>': '&gt;',
		'"': '&quot;'
	}[character]));

const html = `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>昇腾310B调试面板预览</title>
<style>
	:root { color-scheme: light dark; }
	body { margin: 0; padding: 24px; font: 14px/1.6 "Segoe UI", "Microsoft YaHei", sans-serif; background: #f6f7f9; }
	.panel { max-width: 900px; margin: 0 auto; border: 1px solid #d5d9e0; border-radius: 8px; background: #fff; overflow: hidden; }
	.title { padding: 12px 16px; border-bottom: 1px solid #e4e7ec; font-weight: 600; }
	.section { padding: 14px 16px; border-bottom: 1px solid #eef0f3; }
	.section:last-child { border-bottom: 0; }
	.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px; }
	.card { border: 1px solid #e4e7ec; border-radius: 6px; padding: 8px 10px; }
	.card .label { color: #667085; font-size: 12px; }
	.card .value { font-weight: 600; margin-top: 2px; word-break: break-word; }
	.card .missing { color: #b54708; }
	table { width: 100%; border-collapse: collapse; }
	th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #eef0f3; vertical-align: top; }
	th { color: #667085; font-weight: 500; width: 190px; }
	.badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px; background: #ecfdf3; color: #027a48; border: 1px solid #abefc6; }
	.badge.untested { background: #fffaeb; color: #b54708; border-color: #fedf89; }
	.pills { display: flex; flex-wrap: wrap; gap: 6px; }
	.pill { border: 1px solid #e4e7ec; border-radius: 999px; padding: 2px 10px; font-size: 12px; color: #344054; }
	.note { color: #667085; font-size: 12px; margin-top: 6px; }
</style>
</head>
<body>
<div class="panel">
	<div class="title">昇腾310B调试</div>
	<div class="section">
		<div><b>适用范围：</b>${scope?.applicable === true ? '昇腾 310B（支持）' : scope?.applicable === false ? '非昇腾 310B（不适用）' : '未确认（先探测）'}</div>
		${scope === null ? '' : `<div class="note">${escape(scope.message)}</div>`}
	</div>
	<div class="section">
		<div class="cards">
			<div class="card"><div class="label">芯片</div><div class="value">${escape(report.board?.chip)}</div></div>
			<div class="card"><div class="label">档位</div><div class="value">${escape(report.board?.tier)}</div></div>
			<div class="card"><div class="label">健康</div><div class="value">${escape(report.board?.health)}</div></div>
			<div class="card"><div class="label">温度</div><div class="value">${escape(report.board?.temperatureC === null ? null : `${report.board?.temperatureC} ℃`)}</div></div>
			<div class="card"><div class="label">内存</div><div class="value">${escape(report.board?.memoryUsedMb === null ? null : `${report.board?.memoryUsedMb} / ${report.board?.memoryTotalMb} MB`)}</div></div>
			<div class="card"><div class="label">CANN</div><div class="value">${escape(fact('cannVersion'))}</div></div>
			<div class="card"><div class="label">驱动</div><div class="value">${escape(fact('driverVersion'))}</div></div>
			<div class="card"><div class="label">固件</div><div class="value">${escape(fact('firmwareVersion'))}</div></div>
		</div>
	</div>
	<div class="section">
		<div>版本配套：<span class="badge${pairing.evidence === 'observed-pass' ? '' : ' untested'}">${escape(pairing.verdict)} · ${escape(pairing.evidence)}</span></div>
		<div class="note">${escape(pairing.reasons?.[0])}</div>
		${pairing.reasons?.slice(1).map((reason) => `<div class="note">${escape(reason)}</div>`).join('\n\t\t') ?? ''}
	</div>
	<div class="section">
		<table>
			${rows.map(([label, value]) => `<tr><th>${escape(label)}</th><td>${escape(value)}</td></tr>`).join('\n\t\t\t')}
		</table>
	</div>
	<div class="section">
		<div class="label">调试入口（知识库 ${knowledgeTotal} 条）</div>
		<div class="pills">
			${areas.map((area) => `<span class="pill">${escape(area.area)} · ${area.entries.length}</span>`).join('\n\t\t\t')}
		</div>
	</div>
	<div class="section">
		<table>
			<tr><th>样例索引</th><td>${sampleTotals.total} 条（有入口 ${sampleTotals.withEntry} 条，有测试命令 ${sampleTotals.withTest} 条）</td></tr>
			<tr><th>已识别路径</th><td>${escape(index.archetypes.join('、'))}</td></tr>
			<tr><th>探测时间</th><td>${escape(fact('probeTime'))}</td></tr>
		</table>
	</div>
</div>
</body>
</html>
`;

writeFileSync(outPath, html, 'utf8');

// The SVG is drawn here rather than converted from the HTML so the preview
// stays readable without a browser or any extra dependency.
const svgEscape = (value) =>
	String(value ?? '未采集').replace(/[&<>"]/g, (character) => ({
		'&': '&amp;',
		'<': '&lt;',
		'>': '&gt;',
		'"': '&quot;'
	}[character]));

const lines = [];
lines.push(['h1', '昇腾310B调试']);
lines.push(['h2', `适用范围：${scope?.applicable === true ? '昇腾 310B（支持）' : scope?.applicable === false ? '非昇腾 310B（不适用）' : '未确认（先探测）'}`]);
if (scope !== null && scope.applicable !== true) lines.push(['p', scope.message]);
lines.push(['h2', `版本配套：${pairing.verdict} · ${pairing.evidence}`]);
lines.push(['p', pairing.reasons?.[0] ?? '']);
lines.push(['h2', `板卡：${report.board?.chip ?? '未采集'} / ${report.board?.soc ?? '未采集'} / ${report.board?.tier ?? '未采集'} / Health ${report.board?.health ?? '未采集'}`]);
if (report.board !== null) {
	lines.push(['p', `温度 ${report.board.temperatureC ?? '未采集'} ℃ · 功率 ${report.board.powerW ?? '未采集'} W · 内存 ${report.board.memoryUsedMb ?? '未采集'} / ${report.board.memoryTotalMb ?? '未采集'} MB`]);
}
lines.push(['h2', '环境事实']);
for (const [label, value] of rows) lines.push(['p', `${label}：${value ?? '未采集'}`]);
lines.push(['h2', `调试入口：知识库 ${knowledgeTotal} 条，覆盖 ${areas.length} 个领域`]);
lines.push(['p', areas.map((area) => `${area.area}(${area.entries.length})`).join(' · ')]);
lines.push(['h2', `样例索引：${sampleTotals.total} 条`]);
lines.push(['p', `有入口 ${sampleTotals.withEntry} 条 · 有测试命令 ${sampleTotals.withTest} 条 · 已识别路径 ${index.archetypes.join('、') || '未采集'}`]);

let y = 60;
const drawn = [];
for (const [kind, text] of lines) {
	if (text === '') continue;
	const size = kind === 'h1' ? 20 : kind === 'h2' ? 15 : 13;
	if (kind === 'h2') y += 14;
	drawn.push(
		`<text x="28" y="${y}" font-size="${size}" font-weight="${kind === 'p' ? 400 : 600}" ` +
		`fill="${kind === 'p' ? '#344054' : '#101828'}">${svgEscape(text)}</text>`
	);
	y += kind === 'p' ? 22 : 28;
}
const height = y + 16;
const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="900" height="${height}" viewBox="0 0 900 ${height}" font-family="'Segoe UI','Microsoft YaHei',sans-serif">
<rect width="900" height="${height}" fill="#ffffff"/>
<rect x="12" y="12" width="876" height="${height - 24}" rx="8" fill="none" stroke="#d5d9e0"/>
${drawn.join('\n')}
</svg>
`;
writeFileSync(svgPath, svg, 'utf8');

console.log(`panel preview written: ${outPath}`);
console.log(`panel preview written: ${svgPath}`);
console.log(`  board   : ${report.board?.chip ?? '?'} / ${report.board?.soc ?? '?'} / ${report.board?.tier ?? '?'}`);
console.log(`  pairing : ${pairing.verdict} (${pairing.evidence})`);
console.log(`  facts   : ${rows.filter(([, value]) => value !== null).length}/${rows.length} collected`);
