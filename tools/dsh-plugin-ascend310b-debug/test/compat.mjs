/**
 * Version-pairing verdict tests.
 *
 * These pin the distinction the plugin exists to protect: a combination the
 * repository recorded is `observed-match`, a documented release line is
 * `documented-pairing`/`documented-unpaired`, and anything unrecorded stays
 * `untested` instead of becoming `unsupported`.
 *
 * Usage: node test/compat.mjs
 */

import { assessVersionPairing, compareMajorMinor, matchObservedStacks, parseVersion, releaseLineFor } from '../lib/compat.js';
import { parseProbeOutput } from '../lib/probe.js';
import { PROBE_20T_CANN80, PROBE_8T_CANN80, PROBE_ATLAS_CANN92 } from './fixtures.mjs';

const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};
const equal = (actual, expected, message) => {
	if (actual !== expected) failures.push(`${message}（期望 ${JSON.stringify(expected)}，实际 ${JSON.stringify(actual)}）`);
};

// --- version parsing ------------------------------------------------------
// CANN 7.0.0 + driver 23.0 is deliberately NOT used as the "unknown" case: the
// repository has recorded that pair on the 8T board, so it must stay a match.
const older = assessVersionPairing({
	soc: { value: 'Ascend310B4' },
	cannVersion: { value: '7.0.0' },
	driverVersion: { value: '23.0' }
});
equal(older.verdict, 'observed-match', 'CANN 7.0 + 驱动 23.0 是仓库记录过的 8T 组合，不能被判成未知');

equal(parseVersion('8.3.RC1').minor, 3, '8.3.RC1 的主次版本应为 8.3');
equal(parseVersion('9.2.0-beta.2').minor, 2, '9.2.0-beta.2 的主次版本应为 9.2');
equal(compareMajorMinor('8.0.0', '8.0'), 0, '8.0.0 与 8.0 应视为同一版本线');
equal(compareMajorMinor('8.5', '9.0'), -1, '8.5 应小于 9.0');
equal(parseVersion('not-a-version'), null, '不可解析的版本应返回 null');

equal(releaseLineFor('8.0.0')?.line, '8.0', '8.0.0 应落在 8.0 发布线');
equal(releaseLineFor('8.3.0.1.200')?.line, '8.3.RC1', '8.3.0.1.200 应落在 8.3.RC1 发布线');
equal(releaseLineFor('9.2.0-beta.2')?.line, '9.2.X', '9.2.0-beta.2 应落在 9.2.X 发布线');
equal(releaseLineFor('7.0.0')?.line, undefined, '未收录的 7.0 不应返回发布线');

// --- observed matches -----------------------------------------------------
const eight = parseProbeOutput(PROBE_8T_CANN80);
const eightVerdict = assessVersionPairing(eight.facts);
equal(eightVerdict.verdict, 'observed-match', '8T + CANN 8.0.0 + 驱动 25.2 是仓库记录过的组合');
equal(eightVerdict.evidence, 'observed-pass', '记录过的组合应带 observed-pass 证据');
check(eightVerdict.observedStacks.length > 0, '应给出匹配的已记录组合');

const twenty = parseProbeOutput(PROBE_20T_CANN80);
const twentyVerdict = assessVersionPairing(twenty.facts);
equal(twentyVerdict.verdict, 'observed-match', '20T + CANN 8.0.0 + 驱动 25.2 是仓库记录过的隔离环境组合');

const atlas = parseProbeOutput(PROBE_ATLAS_CANN92);
const atlasVerdict = assessVersionPairing(atlas.facts);
equal(atlasVerdict.verdict, 'observed-match', 'Atlas + 固件 7.8.0.5.216 + 驱动 25.5 + CANN 9.2 是仓库记录过的升级组合');

// --- documented / unpaired / unknown -------------------------------------
const pairedFacts = {
	soc: { value: 'Ascend310B4' },
	cannVersion: { value: '9.0.0' },
	driverVersion: { value: '25.3' }
};
const paired = assessVersionPairing(pairedFacts);
equal(paired.verdict, 'documented-pairing', 'CANN 9.0.X + 驱动 25.3 应判为文档配套');
equal(paired.evidence, 'documented', '文档配套的证据级别应为 documented');

const unpaired = assessVersionPairing({
	soc: { value: 'Ascend310B4' },
	cannVersion: { value: '9.0.0' },
	driverVersion: { value: '99.9' }
});
equal(unpaired.verdict, 'documented-unpaired', '驱动不在记录线内应判为 documented-unpaired');
equal(unpaired.evidence, 'inferred', '不配套判定只能是 inferred，不能升级为 observed');
check(
	unpaired.reasons.some((reason) => reason.includes('当前记录不覆盖')),
	'unpaired 的理由必须限定为“当前记录不覆盖”，而不是判定不支持'
);
check(
	unpaired.reasons.every((reason) => !reason.includes('不支持')),
	'unpaired 的理由不得出现“不支持”的结论'
);

const unknown = assessVersionPairing({
	soc: { value: 'Ascend310B4' },
	cannVersion: { value: '6.0.0' },
	driverVersion: { value: '25.2.0' }
});
equal(unknown.verdict, 'undocumented-cann', '未收录的 CANN 版本应判为 undocumented-cann');
equal(unknown.evidence, 'untested', '未收录版本不能判成失败');
check(
	unknown.reasons.some((reason) => reason.includes('不是 observed-fail')),
	'undocumented-cann 必须写明缺少记录不等于不支持'
);

const incomplete = assessVersionPairing({ soc: { value: 'Ascend310B4' } });
equal(incomplete.verdict, 'incomplete', '缺 CANN/驱动时应判为 incomplete');
equal(incomplete.evidence, 'untested', '缺数据时证据级别为 untested');

// --- match narrowing ------------------------------------------------------
equal(matchObservedStacks({ soc: { value: 'Ascend310B1' }, cannVersion: { value: '8.0.0' }, driverVersion: { value: '25.2.0' } }).length, 1, '20T 只应匹配 20T 记录');
equal(matchObservedStacks({ soc: { value: 'Ascend310B1' }, cannVersion: { value: '8.0.0' }, driverVersion: { value: '23.0' } }).length, 0, 'SoC 为 20T 时不应命中 8T 的记录');
equal(matchObservedStacks({ soc: { value: 'Ascend310B4' }, cannVersion: { value: '9.2.0-beta.2' }, driverVersion: { value: '25.5.0' } }).length, 1, 'Atlas 组合应命中一条记录');

if (failures.length > 0) {
	console.error('compat tests FAILED:');
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('compat tests passed');
