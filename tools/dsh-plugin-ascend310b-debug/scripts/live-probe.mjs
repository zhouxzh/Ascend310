/**
 * Live board probe from the command line.
 *
 * The desktop host caches imported modules, so a changed probe script only takes
 * effect after the app restarts. This script runs the same probe directly, which
 * is how a probe change is verified against a real board before that restart.
 *
 * Nothing here is board-specific: the target is taken from the arguments and no
 * address, user or checkout path is baked in.
 *
 * Usage:
 *   node scripts/live-probe.mjs <user@host> [sshPort] [timeoutMs]
 *   set DSH_ASCEND310B_TARGET=user@host && node scripts/live-probe.mjs
 */

import { assessUnifiedEnvironment } from '../lib/compat.js';
import { buildProbeScript, parseProbeOutput } from '../lib/probe.js';
import { runRemoteScript } from '../lib/shell.js';

const selector = process.argv[2] ?? process.env.DSH_ASCEND310B_TARGET ?? '';
if (selector.trim() === '') {
	console.error('usage: node scripts/live-probe.mjs <user@host> [sshPort] [timeoutMs]');
	console.error('   or: set DSH_ASCEND310B_TARGET=user@host && node scripts/live-probe.mjs');
	process.exit(2);
}

const at = selector.lastIndexOf('@');
const target = {
	id: selector,
	label: selector,
	transport: 'ssh',
	user: at > 0 ? selector.slice(0, at) : null,
	host: at > 0 ? selector.slice(at + 1) : selector,
	port: Number.isInteger(Number(process.argv[3])) && Number(process.argv[3]) > 0 ? Number(process.argv[3]) : null,
	identityFile: null
};
const timeoutMs = Number.isInteger(Number(process.argv[4])) ? Number(process.argv[4]) : 90000;

const result = await runRemoteScript({ script: buildProbeScript(), target, timeoutMs });
if (result.code !== 0 && result.stdout.trim() === '') {
	console.error(`SSH 探测失败（exit ${result.code}）：${result.stderr.trim() || '没有输出'}`);
	process.exit(1);
}

const report = parseProbeOutput(result.stdout);
const facts = report.facts;
const unified = assessUnifiedEnvironment(facts);
const show = (key) => (facts[key] === undefined ? '未采集' : facts[key].value);

console.log(`目标        : ${result.display ?? selector}（exit ${result.code}${result.timedOut ? '，超时' : ''}）`);
console.log(`板卡        : ${report.board === null ? '未识别' : `${report.board.chip} / ${report.board.soc} / ${report.board.tier} / Health ${report.board.health}`}`);
console.log(`系统        : ${show('hostname')} · ${show('os')} · kernel ${show('kernel')}`);
console.log(`CANN        : ${show('cannVersion')}（包 ${show('cannPackageVersion')}）`);
console.log(`驱动        : ${show('driverVersion')}`);
console.log(`npu-smi     : ${show('npuSmiVersion')}`);
console.log(`Python      : ${show('pythonExecutable')} ${show('pythonVersion')}`);
console.log(`ascend 环境 : ${show('ascendEnvPresent')} · ${show('ascendEnvVersion')} · ${show('ascendEnvAbi')} · 启动策略隔离 ${show('ascendEnvPolicyIsolated')} · 裸解释器隔离 ${show('ascendEnvIsolated')}`);
console.log(`ascend ACL  : ${show('ascendEnvAclImport')}；aclruntime ${show('ascendEnvAclruntime')}；MindSpore context ${show('ascendEnvMindsporeContext')}；统一环境判定 ${unified.verdict}`);
console.log(`ascend 包   : ${show('ascendEnvPackages')}`);
console.log(`import acl  : source 后 ${show('aclImportSourced')}；裸 shell ${show('aclImportBare')}（面板取值 ${show('aclImport')}）`);
console.log(`atc         : ${show('atcVersion')}（在 PATH: ${show('atcOnPath')}）`);
console.log(`设备节点    : ${show('deviceNodes')}；内核模块 ${show('kernelModules')}`);
if (report.missingCore.length > 0) console.log(`缺失关键项  : ${report.missingCore.join('、')}`);
if (facts.aclImportNote !== undefined) console.log(`提示        : ${facts.aclImportNote.value}`);
if (result.stderr.trim() !== '') console.log(`stderr      : ${result.stderr.trim().slice(0, 400)}`);
