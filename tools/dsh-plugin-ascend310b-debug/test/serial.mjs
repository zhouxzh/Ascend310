/**
 * Serial transport parser and validation tests.
 *
 * The hardware session is tested separately against a real COM port; this file
 * keeps the prompt/marker/network parsing deterministic and offline.
 */

import { cleanSerialOutput, extractMarkedOutput, formatSerialError, parseSerialNetwork, parseSerialPortList, validateSerialSettings } from '../lib/serial.js';

const failures = [];
const check = (condition, message) => {
	if (!condition) failures.push(message);
};
const equal = (actual, expected, message) => check(actual === expected, `${message}: ${JSON.stringify(actual)} != ${JSON.stringify(expected)}`);

const transcript = [
	'orangepiaipro-20t login: ',
	'Password: ',
	'ASC_TOKEN_BEGIN_1',
	'orangepiaipro-20t',
	'ASC_TOKEN_END_1',
	'ASC_TOKEN_BEGIN_2',
	'10.0.0.101 172.17.0.1',
	'ASC_TOKEN_END_2',
	'ASC_TOKEN_BEGIN_3',
	'2: wlan0    inet 10.0.0.101/24 brd 10.0.0.255 scope global wlan0',
	'3: docker0  inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0',
	'ASC_TOKEN_END_3',
	'ASC_TOKEN_BEGIN_4',
	'default via 10.0.0.1 dev wlan0 proto dhcp metric 600',
	'10.0.0.0/24 dev wlan0 proto kernel scope link src 10.0.0.101',
	'ASC_TOKEN_END_4'
].join('\r\n');

const section = (n) => extractMarkedOutput(transcript, `ASC_TOKEN_BEGIN_${n}`);
const network = parseSerialNetwork({
	hostname: section(1),
	addresses: section(2),
	interfaces: section(3),
	routes: section(4),
	macs: 'eth0=c0:74:2b:fd:dd:7a\nwlan0=c0:74:2b:fd:dd:7b\n'
}, transcript);

equal(network.hostname, 'orangepiaipro-20t', 'hostname');
equal(network.ipAddress, '10.0.0.101', 'default route IP must win over docker address');
equal(network.interfaces.length, 2, 'interfaces parsed');
equal(network.macAddress, 'c0:74:2b:fd:dd:7a', 'stable eth0 MAC should match network probe identity');
equal(network.defaultRoute, 'default via 10.0.0.1 dev wlan0 proto dhcp metric 600', 'default route');
equal(cleanSerialOutput('\u001b[32mgreen\u001b[0m\r\n'), 'green', 'ANSI/CR cleaned');
equal(extractMarkedOutput(transcript, 'ASC_TOKEN_BEGIN_2'), '10.0.0.101 172.17.0.1', 'marked output');
const candidates = parseSerialPortList('__SERIAL_LIST__{"ports":[{"port":"COM14","label":"CH343"},{"port":"COM23","label":"CH343"}]}');
equal(candidates.length, 2, 'auto candidate list parsed');
equal(candidates[0].port, 'COM14', 'first candidate port');

check(validateSerialSettings({ port: 'auto', baudRate: 115200, user: 'HwHiAiUser', password: 'x', timeoutMs: 1000 }).ok, 'auto serial setting');
check(validateSerialSettings({ port: 'COM23', baudRate: 115200, user: 'HwHiAiUser', password: 'x', timeoutMs: 1000 }).ok, 'valid Windows serial setting');
check(validateSerialSettings({ port: '/dev/ttyUSB0', baudRate: 115200, user: 'root', password: 'x', timeoutMs: 1000 }).ok, 'valid POSIX serial setting');
check(!validateSerialSettings({ port: 'COM', baudRate: 115200, user: 'root', password: 'x', timeoutMs: 1000 }).ok, 'invalid port rejected');
check(!validateSerialSettings({ port: 'COM23', baudRate: 0, user: 'root', password: 'x', timeoutMs: 1000 }).ok, 'invalid baud rejected');
check(
	formatSerialError('COM7', '<S S="Error">UnauthorizedAccessException: access denied</S>').includes('占用'),
	'busy serial errors must be explained without CLIXML'
);

if (failures.length > 0) {
	console.error(`serial test FAILED (${failures.length} 处):`);
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('serial test passed');
console.log(`  parsed ${network.interfaces.length} interfaces, ip=${network.ipAddress}`);
