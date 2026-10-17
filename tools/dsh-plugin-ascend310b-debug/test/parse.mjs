/**
 * Probe parser tests.
 *
 * Usage: node test/parse.mjs
 */

import { buildProbeScript, parseNpuSmiInfo, parseOsRelease, parseProbeOutput, parseVersionCfg } from '../lib/probe.js';
import { NPU_SMI_310B1, NPU_SMI_310B4, PROBE_8T_CANN80, PROBE_20T_CANN80, PROBE_ATLAS_CANN92 } from './fixtures.mjs';

const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};
const equal = (actual, expected, message) => {
	if (actual !== expected) failures.push(`${message}（期望 ${JSON.stringify(expected)}，实际 ${JSON.stringify(actual)}）`);
};

// --- npu-smi device table -------------------------------------------------
const devices = parseNpuSmiInfo(NPU_SMI_310B4);
equal(devices.length, 1, '8T 输出应解析出一行设备');
equal(devices[0].socVersion, 'Ascend310B4', '裸 310B4 应补上 Ascend 前缀');
equal(devices[0].health, 'OK', '应读出 Health');
equal(devices[0].powerW, 12.8, '应读出功耗');
equal(devices[0].temperatureC, 45, '应读出温度');
equal(devices[0].memoryUsedMb, 2433, '内存来自续行，不是 hugepages 的 0 / 0');
equal(devices[0].memoryTotalMb, 7564, '续行内存总量应为 7564');

const devices20t = parseNpuSmiInfo(NPU_SMI_310B1);
equal(devices20t[0].socVersion, 'Ascend310B1', '已带前缀的 Ascend310B1 不应重复加前缀');
equal(devices20t[0].health, 'Alarm', 'Alarm 应被读出而不是丢弃');
equal(devices20t[0].memoryTotalMb, 23673, '20T 内存总量应为 23673');

// --- version files --------------------------------------------------------
const cfg = parseVersionCfg('version=[7.6.0.1.220]\nruntime_running_version=[7.6.0.1.220:8.0.0]\n');
equal(cfg.runtime_running_version, '7.6.0.1.220:8.0.0', '应解析 version.cfg 的键值');
const os = parseOsRelease('PRETTY_NAME="Ubuntu 22.04.5 LTS"\nNAME="Ubuntu"\n');
equal(os.PRETTY_NAME, 'Ubuntu 22.04.5 LTS', '应去掉 os-release 的引号');

// --- end-to-end transcripts ----------------------------------------------
const eight = parseProbeOutput(PROBE_8T_CANN80);
equal(eight.board.soc, 'Ascend310B4', '8T 记录应识别 SoC');
equal(eight.board.tier, '8T', '310B4 应映射到 8T');
equal(eight.facts.cannVersion.value, '8.0.0', '应从 runtime_running_version 取出 CANN 8.0.0');
equal(eight.facts.driverVersion.value, '25.2.0', '应读出驱动版本');
equal(eight.facts.aclImport.value, 'ok', 'import acl 成功应记为 ok');
equal(eight.facts.socTier.evidenceLabel, 'documented', '档位映射应标记为 documented');
equal(eight.missingCore.length, 0, 'SoC、CANN、驱动齐全时 missingCore 应为空');

const twenty = parseProbeOutput(PROBE_20T_CANN80);
equal(twenty.board.tier, '20T', '310B1 应映射到 20T');
equal(twenty.facts.soc.value, 'Ascend310B1', '20T 板 SoC 应为 Ascend310B1');

const atlas = parseProbeOutput(PROBE_ATLAS_CANN92);
equal(atlas.facts.firmwareVersion.value, '7.8.0.5.216', '应从 npu-smi board 读出固件');
equal(atlas.facts.driverVersion.value, '25.5.0', '应从 npu-smi board 读出驱动');
equal(atlas.facts.cannVersion.value, '9.2.0-beta.2', '应读出 CANN 9.2.0-beta.2');

// --- honest gaps ----------------------------------------------------------
const empty = parseProbeOutput('===ABS_BEGIN:hostname===\nfoo\n===ABS_END:hostname===\n');
equal(empty.board, null, '没有设备行时 board 应为 null，而不是编造一个型号');
check(empty.missingCore.includes('soc'), '缺 SoC 时应列入 missingCore');
check(empty.missingCore.includes('cannVersion'), '缺 CANN 时应列入 missingCore');

const transcript = parseProbeOutput('npu-smi info\n| 0  310B4 | OK |\n| 0  0 | 0000:00:00.0 | 0  100 / 800 |\n');
equal(transcript.board?.soc, 'Ascend310B4', '无节标记的纯终端记录也应能识别设备行');

// --- generated script -----------------------------------------------------
const script = buildProbeScript();
for (const section of ['npu_smi_info', 'cann_version_cfg', 'driver_version_info', 'acl_import', 'devices']) {
	check(script.includes(`abs_section ${section}`), `探测脚本应包含 ${section} 节`);
}
check(!/(^|\s)(rm|mv|dd|mkfs)(\s|$)/.test(script), '探测脚本不应包含写操作');
check(script.includes('read-only environment probe'), '脚本应自述为只读探测');

if (failures.length > 0) {
	console.error('parse tests FAILED:');
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('parse tests passed');
