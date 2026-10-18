/**
 * Validate the reusable DOT canvas templates.
 *
 * Graphviz is optional on a reader's machine, so a missing `dot` is reported
 * as a skip. When it is available, the rendered PNG must stay inside the
 * textbook/presentation budget and the architecture template must be close to
 * a 16:9 frame.
 */

import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const templates = join(root, 'skills', 'ascend310b-writing-dot', 'templates');
const failures = [];
const architecture = readFileSync(join(templates, 'architecture-slide.dot'), 'utf8');
const textbook = readFileSync(join(templates, 'architecture-textbook.dot'), 'utf8');
const flow = readFileSync(join(templates, 'flow-textbook.dot'), 'utf8');

if (!architecture.includes('ratio="fill"')) failures.push('architecture-slide.dot 未强制 16:9 fill');
if (!architecture.includes('size="8.5,4.8"')) failures.push('architecture-slide.dot 未声明演示画布');
if (!architecture.includes('fontsize=16')) failures.push('architecture-slide.dot 节点字号小于教材可读下限');
if ((architecture.match(/fontname="Noto Sans CJK SC"/gu) ?? []).length < 3) {
	failures.push('architecture-slide.dot 未统一 graph/node/edge 字体');
}
if (!textbook.includes('size="7.2,4.2"') || !textbook.includes('ratio="compress"')) {
	failures.push('architecture-textbook.dot 未声明教材画布');
}
if ((textbook.match(/\{ rank=same;/gu) ?? []).length < 3) {
	failures.push('architecture-textbook.dot 未使用两列纵向布局');
}
if (!textbook.includes('fontsize=16')) failures.push('architecture-textbook.dot 节点字号小于教材可读下限');
if (!flow.includes('rankdir=TB')) failures.push('flow-textbook.dot 未使用纵向流程方向');
if (!flow.includes('fontsize=16')) failures.push('flow-textbook.dot 节点字号小于教材可读下限');
if (!flow.includes('fontname="Noto Sans CJK SC"')) failures.push('模板未声明中文字体');

const dot = spawnSync('dot', ['-V'], { encoding: 'utf8' });
if (dot.error) {
	console.log('dot template test skipped: Graphviz not installed');
} else {
	const work = mkdtempSync(join(tmpdir(), 'ascend-dot-template-'));
	const render = (name) => {
		const source = join(templates, name);
		const output = join(work, name.replace(/\.dot$/u, '.png'));
		const result = spawnSync('dot', ['-Tpng', source, '-o', output], { encoding: 'utf8' });
		if (result.status !== 0) {
			failures.push(`${name}: dot failed: ${(result.stderr || result.stdout || '').trim()}`);
			return null;
		}
		const bytes = readFileSync(output);
		if (bytes.toString('ascii', 1, 4) !== 'PNG') {
			failures.push(`${name}: output is not PNG`);
			return null;
		}
		return {
			width: bytes.readUInt32BE(16),
			height: bytes.readUInt32BE(20)
		};
	};
	try {
		const architectureSize = render('architecture-slide.dot');
		const textbookSize = render('architecture-textbook.dot');
		const flowSize = render('flow-textbook.dot');
		if (architectureSize !== null) {
			if (architectureSize.width > 1280 || architectureSize.height > 720) {
				failures.push(`architecture-slide.dot: ${architectureSize.width}x${architectureSize.height} exceeds 1280x720`);
			}
			const ratio = architectureSize.width / architectureSize.height;
			if (ratio < 1.5 || ratio > 2.0) {
				failures.push(`architecture-slide.dot: aspect ratio ${ratio.toFixed(2)} is not slide-like`);
			}
		}
		if (textbookSize !== null && (textbookSize.width > 1600 || textbookSize.height > 1000)) {
			failures.push(`architecture-textbook.dot: ${textbookSize.width}x${textbookSize.height} exceeds textbook budget`);
		}
		if (flowSize !== null && (flowSize.width > 1600 || flowSize.height > 1000)) {
			failures.push(`flow-textbook.dot: ${flowSize.width}x${flowSize.height} exceeds textbook budget`);
		}
	} finally {
		rmSync(work, { recursive: true, force: true });
	}
}

if (failures.length > 0) {
	console.error(`dot template test FAILED (${failures.length} 处):`);
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('dot template test passed');
