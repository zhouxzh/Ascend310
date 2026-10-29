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

import {
	expandCidr,
	inferLocalPrivateCidrs,
	parseIPv4,
	probeCandidate,
	resolveDiscoveryCidrs,
	resolveDiscoveryCidrsForScan
} from '../lib/discovery.js';
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

const fakeInterfaces = {
	Loopback: [
		{ family: 'IPv4', address: '127.0.0.1', internal: true },
		{ family: 'IPv6', address: '::1', internal: true }
	],
	Hotspot: [
		{ family: 'IPv4', address: '10.73.137.1', internal: false },
		{ family: 'IPv6', address: 'fe80::1', internal: false }
	],
	Wifi: [{ family: 4, address: '10.36.43.156', internal: false }],
	Lab22: [{ family: 'IPv4', address: '10.42.8.235', netmask: '255.255.252.0', internal: false }],
	'vEthernet (WSL (Hyper-V firewall))': [{ family: 'IPv4', address: '172.24.144.1', netmask: '255.255.240.0', internal: false }],
	Public: [{ family: 'IPv4', address: '8.8.8.8', internal: false }]
};
const inferred = inferLocalPrivateCidrs(fakeInterfaces);
check(inferred.includes('10.73.137.0/24'), '应自动包含热点 /24 网段');
check(inferred.includes('10.36.43.0/24'), '应自动包含其他活动私有 /24 网段');
check(inferred.includes('10.42.8.0/22'), '应保留活动网卡的真实 /22 前缀');
check(!inferred.includes('8.8.8.0/24'), '自动推导不得包含公网 /24');
check(!inferred.includes('172.24.144.0/20'), '自动推导不得扫描 WSL/Hyper-V 虚拟网卡');
check(!inferred.includes('127.0.0.0/24'), '自动推导不得包含回环 /24');
const configuredCidrs = resolveDiscoveryCidrs(null, ['10.55.3.0/24'], fakeInterfaces);
equal(configuredCidrs.join(','), '10.55.3.0/24', '显式配置的扫描网段应优先且不自动扩散');
const autoCidrs = resolveDiscoveryCidrs(null, [], fakeInterfaces);
check(autoCidrs.includes('10.73.137.0/24'), '未配置网段时应使用活动热点网段');
check(!autoCidrs.includes('10.55.3.0/24'), '未配置网段时不应凭空加入配置值');
const routedCidrs = await resolveDiscoveryCidrsForScan(null, [], fakeInterfaces, async () => '10.36.43.156');
equal(routedCidrs.join(','), '10.36.43.0/24', '默认扫描应只选择当前默认路由所在网段');
const noRouteCidrs = await resolveDiscoveryCidrsForScan(null, [], fakeInterfaces, async () => null);
check(noRouteCidrs.length > 1, '无法取得默认路由时应保留活动网段作为回退');
const explicitCidrs = resolveDiscoveryCidrs(['10.0.0.0/30'], ['10.55.3.0/24'], fakeInterfaces);
equal(explicitCidrs.join(','), '10.0.0.0/30', '单次显式 CIDR 应覆盖自动与配置网段');

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
	system: candidate.system,
	serial: { status: 'reachable', port: 'COM7', user: 'HwHiAiUser', ip: '10.0.0.100', hostname: 'orangepiaipro' }
}, '2026-10-18T01:00:00.000Z');
inventory = first.inventory;
equal(first.status, 'new', '第一次发现应为 new');
equal(first.board.mac, 'c0:74:2b:fd:dd:7a', '资产表必须记录 MAC');
equal(first.board.serial?.port, 'COM7', '串口发现状态必须写入资产表');
const switchedInterface = observeBoard(inventory, {
	ip: '10.0.0.110',
	user: 'HwHiAiUser',
	mac: 'aa:bb:cc:dd:ee:ff',
	macs: ['aa:bb:cc:dd:ee:ff', 'c0:74:2b:fd:dd:7a'],
	hostname: 'orangepiaipro',
	system: candidate.system
}, '2026-10-18T01:03:00.000Z');
equal(switchedInterface.board.id, first.board.id, '有线切无线后应沿用同一逻辑板卡 ID');
equal(switchedInterface.board.macs.length, 2, '资产表应保留有线与无线 MAC 别名');
equal(switchedInterface.status, 'ip-changed', '网口切换但 MAC 别名匹配时应只记录 IP 变化');
inventory = switchedInterface.inventory;
const otherBoard = observeBoard({
	...inventory,
	boards: { ...inventory.boards }
}, {
	ip: '10.0.0.111',
	user: 'HwHiAiUser',
	mac: 'c0:74:2b:fd:df:83',
	hostname: 'orangepiaipro',
	system: candidate.system
}, '2026-10-18T01:04:00.000Z');
check(otherBoard.board.id !== first.board.id, '不同 MAC 的板即使 machine-id/hostname 相同也必须保持独立');

const legacyMerge = observeBoard({
	version: 1,
	boards: {
		'c0:74:2b:fd:dd:7a': {
			id: 'ascend-c0742bfddd7a',
			mac: 'c0:74:2b:fd:dd:7a',
			lastSeen: '2026-10-18T00:01:00.000Z',
			system: { soc: 'Ascend310B4' },
			changes: [],
			history: []
		},
		'aa:bb:cc:dd:ee:ff': {
			id: 'ascend-aabbccddeeff',
			mac: 'aa:bb:cc:dd:ee:ff',
			lastSeen: '2026-10-18T00:02:00.000Z',
			system: { soc: 'Ascend310B4' },
			changes: [],
			history: []
		}
	}
}, {
	ip: '10.0.0.130',
	mac: 'c0:74:2b:fd:dd:7a',
	macs: ['c0:74:2b:fd:dd:7a', 'aa:bb:cc:dd:ee:ff'],
	hostname: 'orangepiaipro',
	system: { soc: 'Ascend310B4' }
}, '2026-10-18T01:05:00.000Z');
equal(Object.keys(legacyMerge.inventory.boards).length, 1, '同时观察到多个已知 MAC 时应合并旧的重复记录');
equal(legacyMerge.board.macs.length, 2, '合并后的旧记录应保留全部 MAC 别名');

const moved = observeBoard(inventory, {
	ip: '10.0.0.120',
	user: 'HwHiAiUser',
	mac: 'c0:74:2b:fd:dd:7a',
	hostname: 'orangepiaipro',
	system: candidate.system
}, '2026-10-18T01:05:00.000Z');
equal(moved.status, 'ip-changed', '同一 MAC 换 IP 应归并为 ip-changed');
equal(moved.inventory.boards['c0:74:2b:fd:dd:7a'].lastIp, '10.0.0.120', '资产表应更新当前 IP');
equal(moved.board.serial?.port, 'COM7', '后续网络发现不能清空串口状态');
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

const partial = observeBoard(changed.inventory, {
	ip: '10.0.0.120',
	user: 'HwHiAiUser',
	mac: 'c0:74:2b:fd:dd:7a',
	hostname: 'orangepiaipro',
	system: { hostname: 'orangepiaipro' }
}, '2026-10-18T02:05:00.000Z');
equal(partial.status, 'unchanged', '缺失的系统字段不能伪造变更');
equal(partial.board.system.driverVersion, '25.5.0', '缺失驱动字段应保留上次证据');
equal(partial.board.system.cannVersion, '9.2.0', '缺失 CANN 字段应保留上次证据');

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
