/**
 * Discovery and inventory tests.
 *
 * These cover the parts that must not depend on a live network: CIDR safety,
 * MAC identity, address changes crossing an inventory record, and system
 * changes (driver/CANN/kernel) being retained as history.
 *
 * Usage: node test/discovery.mjs
 */

import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { expandCidr, parseIPv4, probeCandidate } from '../lib/discovery.js';
import {
	boardIdForMac,
	inventoryPath,
	inventoryTargets,
	normalizeMac,
	observeBoard,
	readInventory,
	saveInventory
} from '../lib/inventory.js';

const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};
const equal = (actual, expected, message) => {
	if (actual !== expected) failures.push(`${message}（期望 ${JSON.stringify(expected)}，实际 ${JSON.stringify(actual)}）`);
};

equal(parseIPv4('10.0.0.100'), 167772260, 'IPv4 解析应得到无符号整数');
equal(normalizeMac('C0:74:2B:FD:DD:7A'), 'c0:74:2b:fd:dd:7a', 'MAC 应统一为小写');
equal(normalizeMac('not-a-mac'), null, '非法 MAC 应返回 null');
equal(boardIdForMac('c0:74:2b:fd:dd:7a'), 'ascend-c0742bfddd7a', 'target id 应由 MAC 稳定生成');

check(expandCidr('10.0.0.0/30').length === 2, '/30 应只有两个主机地址');
let rejected = false;
try {
	expandCidr('8.8.8.0/24');
} catch {
	rejected = true;
}
check(rejected, '发现扫描必须拒绝公网 CIDR');

const probeText = [
	'===ABS_BEGIN:hostname===',
	'orangepiaipro',
	'===ABS_END:hostname===',
	'===ABS_BEGIN:mac===',
	'eth0=c0:74:2b:fd:dd:7a',
	'===ABS_END:mac===',
	'===ABS_BEGIN:machine_id===',
	'ec7cf77f',
	'===ABS_END:machine_id===',
	'===ABS_BEGIN:npu_smi_info===',
	'| 0       310B4             | OK            | 12.8         45                0    / 0              |',
	'| 0       0                 | 0000:00:00.0  | 0            2433 / 7564                            |',
	'===ABS_END:npu_smi_info===',
	'===ABS_BEGIN:cann_version_cfg===',
	'runtime_running_version=[7.6.0.1.220:8.0.0]',
	'===ABS_END:cann_version_cfg===',
	'===ABS_BEGIN:driver_version_info===',
	'Version=25.2.0',
	'===ABS_END:driver_version_info==='
].join('\n');
const fakeExec = async () => ({ code: 0, stdout: probeText, stderr: '', timedOut: false });
const candidate = await probeCandidate(
	{ host: '192.0.2.10', user: 'HwHiAiUser', timeoutMs: 1000, exec: fakeExec }
);
equal(candidate.ok, true, '可解析的探测输出应成功');
equal(candidate.mac, 'c0:74:2b:fd:dd:7a', '发现结果应记录 MAC');
equal(candidate.system.driverVersion, '25.2.0', '发现结果应记录驱动版本');
equal(candidate.system.cannVersion, '8.0.0', '发现结果应记录 CANN 版本');

let inventory = readInventory(join(tmpdir(), 'no-such-ascend-inventory.json'));
const first = observeBoard(inventory, {
	ip: '10.0.0.100',
	user: 'HwHiAiUser',
	mac: 'c0:74:2b:fd:dd:7a',
	hostname: 'orangepiaipro',
	system: candidate.system
}, '2026-10-18T01:00:00.000Z');
inventory = first.inventory;
equal(first.status, 'new', '第一次发现应为 new');
equal(first.board.mac, 'c0:74:2b:fd:dd:7a', '资产表必须记录 MAC');

const moved = observeBoard(inventory, {
	ip: '10.0.0.120',
	user: 'HwHiAiUser',
	mac: 'c0:74:2b:fd:dd:7a',
	hostname: 'orangepiaipro',
	system: candidate.system
}, '2026-10-18T01:05:00.000Z');
equal(moved.status, 'ip-changed', '同一 MAC 换 IP 应归并为 ip-changed');
equal(moved.inventory.boards['c0:74:2b:fd:dd:7a'].lastIp, '10.0.0.120', '资产表应更新当前 IP');
const hostnameProbe = observeBoard(moved.inventory, {
	ip: null,
	user: 'HwHiAiUser',
	mac: 'c0:74:2b:fd:dd:7a',
	hostname: 'orangepiaipro',
	system: candidate.system
}, '2026-10-18T01:06:00.000Z');
equal(hostnameProbe.inventory.boards['c0:74:2b:fd:dd:7a'].lastIp, '10.0.0.120', '主机名探测不能把当前 IP 清空');
equal(hostnameProbe.status, 'unchanged', '没有新 IP 的主机名探测不应伪造网络变化');

const changedSystem = { ...candidate.system, driverVersion: '25.5.0', cannVersion: '9.2.0' };
const changed = observeBoard(moved.inventory, {
	ip: '10.0.0.120',
	user: 'HwHiAiUser',
	mac: 'c0:74:2b:fd:dd:7a',
	hostname: 'orangepiaipro',
	system: changedSystem
}, '2026-10-18T02:00:00.000Z');
equal(changed.status, 'system-changed', '驱动/CANN 变化应标为 system-changed');
check(changed.changes.some((field) => field.name === 'driverVersion'), '变更记录应包含驱动');
check(changed.changes.some((field) => field.name === 'cannVersion'), '变更记录应包含 CANN');
equal(changed.inventory.boards['c0:74:2b:fd:dd:7a'].changes.length, 1, '系统变更应写入历史');

const directory = mkdtempSync(join(tmpdir(), 'ascend-inventory-'));
try {
	const path = join(directory, 'inventory.json');
	saveInventory(path, changed.inventory);
	const loaded = readInventory(path);
	equal(loaded.boards['c0:74:2b:fd:dd:7a'].mac, 'c0:74:2b:fd:dd:7a', '资产表应能持久化 MAC');
	equal(inventoryPath({}, { DSH_HOME: directory }), join(directory, 'ascend310b-inventory.json'), '默认资产表路径应位于 DSH_HOME');
	const targets = inventoryTargets(loaded, 'HwHiAiUser');
	equal(targets[0].id, 'ascend-c0742bfddd7a', '资产表应生成稳定 target id');
	equal(targets[0].host, '10.0.0.120', '动态 target 应使用最近一次 IP');
	check(targets[0].note.includes('c0:74:2b:fd:dd:7a'), 'target 备注应保留 MAC');
} finally {
	rmSync(directory, { recursive: true, force: true });
}

if (failures.length > 0) {
	console.error('discovery tests FAILED:');
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('discovery tests passed');
