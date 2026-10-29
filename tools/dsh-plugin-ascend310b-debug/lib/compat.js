/**
 * Version pairing and evidence classification for Ascend 310B.
 *
 * Two rules govern every value in this module:
 *
 * 1. Nothing here is presented as a board observation. An entry is either a
 *    vendor/documented pairing (`documented`) or a combination this repository
 *    actually recorded (`observed-pass` / `observed-fail`). A plugin that
 *    hardcodes "your CANN version" would be guessing; the board probe answers
 *    that, and this module only judges what the probe returned.
 * 2. A missing entry is `unknown`, never `unsupported`. The repository's own
 *    rule is that a documented family mapping plus a plausible route earns a
 *    bounded smoke test, not a categorical denial.
 *
 * Source of the pairings: repository route table and CANN/HDK relationship
 * table in `src/appendix/appendix7.md`, plus the recorded case8/case9 reports.
 *
 * @module dsh-plugin-ascend310b-debug/compat
 */

/** Evidence labels shared by every report in this plugin. */
export const EVIDENCE = Object.freeze({
	OBSERVED_PASS: 'observed-pass',
	OBSERVED_FAIL: 'observed-fail',
	DOCUMENTED: 'documented',
	INFERRED: 'inferred',
	UNTESTED: 'untested'
});

/**
 * Ascend 310B compute tiers.
 *
 * The chip line (310B1/310B4) is what `npu-smi` prints; the 20T/8T tier is the
 * documented family mapping from the `npu-smi` reference. The mapping is
 * prior evidence, not a substitute for reading the actual board string.
 */
export const TIERS = Object.freeze({
	'Ascend310B1': { tier: '20T', note: '310B1 对应 20T 档位（npu-smi 参考给出的产品族映射）' },
	'Ascend310B4': { tier: '8T', note: '310B4 对应 8T 档位（npu-smi 参考给出的产品族映射）' }
});

/** Display name shown in the sidebar and in generated titles. */
export const PLUGIN_DISPLAY_NAME = '昇腾310B调试';

/** SoCs this plugin's procedures were written for. */
export const SUPPORTED_SOCS = Object.freeze(Object.keys(TIERS));

/**
 * Decide whether a probed device is inside this plugin's scope.
 *
 * This is a plugin boundary, not a vendor compatibility claim: the debugging
 * procedures, sample index and version tables here describe Ascend 310B1/310B4
 * only, so applying them to another accelerator would produce confident but
 * wrong advice. `applicable: 'unknown'` is used when no SoC was read, because an
 * unprobed device is not evidence either way.
 *
 * @param {object} environment - environment report from the probe parser.
 * @returns {object} scope verdict.
 */
export function assessBoardScope(environment) {
	const soc = environment?.facts?.soc?.value ?? null;
	const board = environment?.board ?? null;
	const chip = board?.chip ?? environment?.facts?.chipName?.value ?? null;
	const socText = typeof soc === 'string' ? soc.trim() : '';

	if (socText === '') {
		return {
			applicable: 'unknown',
			family: null,
			soc: null,
			chip,
			supportedSocs: [...SUPPORTED_SOCS],
			evidence: EVIDENCE.UNTESTED,
			message: '尚未读到 SoC，无法确认是否为昇腾 310B。先运行一次板端探测。'
		};
	}

	if (SUPPORTED_SOCS.includes(socText)) {
		const tier = TIERS[socText]?.tier ?? 'unknown';
		return {
			applicable: true,
			family: '昇腾310B',
			soc: socText,
			chip,
			tier,
			supportedSocs: [...SUPPORTED_SOCS],
			evidence: EVIDENCE.DOCUMENTED,
			message: `已确认是昇腾 310B（${socText}，${tier}），插件内的调试程序适用。`
		};
	}

	const looksAscend = /^Ascend/i.test(socText);
	return {
		applicable: false,
		family: looksAscend ? socText : '非昇腾设备',
		soc: socText,
		chip,
		supportedSocs: [...SUPPORTED_SOCS],
		evidence: EVIDENCE.DOCUMENTED,
		message: looksAscend
			? `检测到 ${socText}，不是昇腾 310B1/310B4。本插件的调试程序、样例索引和版本配套表不适用于该芯片。`
			: `检测到 ${socText}，不是昇腾设备。本插件只用于昇腾 310B1/310B4。`
	};
}

/**
 * Combinations the repository actually recorded, with the report that carries
 * the observation. These raise confidence but never replace a fresh probe.
 */
export const OBSERVED_STACKS = Object.freeze([
	{
		id: 'orangepi-aipro-live-8t-cann8',
		board: 'Orange Pi AI Pro（香橙派 AI Pro，hostname orangepiaipro）',
		soc: 'Ascend310B4',
		tier: '8T',
		system: 'Ubuntu 22.04.3 LTS（Jammy），内核 5.10.0+，aarch64',
		driver: '25.2.0',
		firmware: '未在读到的版本文件中记录',
		cann: '8.0.0',
		status: EVIDENCE.OBSERVED_PASS,
		note:
			'本插件对有源板卡的一次只读探测记录：npu-smi 25.2.0，Health Alarm，温度 78C，' +
			'内存 1896/15610 MB，/dev/davinci0 与 drv_davinci 均在，source CANN 后 import acl 成功、atc 位于 ' +
			'/usr/local/Ascend/ascend-toolkit/latest/bin/atc。设备层与 CANN 层为 observed-pass，' +
			'模型加载、数值与性能仍是未验证。',
		source: '插件 live 探测（ascend_probe → /ascend310b-debug/probe，2026-10-17）'
	},
	{
		id: 'orangepi-aipro-cann8',
		board: 'Orange Pi AI Pro（香橙派 AI Pro）',
		soc: 'Ascend310B4',
		tier: '8T',
		system: 'Ubuntu 22.04.3/22.04.5，系统日期戳 20241128 或 20250925，B309',
		driver: '23.0 或 25.2',
		firmware: '7.3.3.0.b309（B309）',
		cann: '8.0',
		status: EVIDENCE.OBSERVED_PASS,
		note: '路线 A 在 CANN 7.0 升级到 8.0 后记录了 ACL、最小 VENC、OM、CANN VENC、case8 WebRTC 与回归。',
		source: 'src/appendix/appendix7.md 路线 A 表；samples/chapter5/docs/01-board-validation-record-20261016.md'
	},
	{
		id: 'orangepi-aipro-cann7-baseline',
		board: 'Orange Pi AI Pro（香橙派 AI Pro）',
		soc: 'Ascend310B4',
		tier: '8T',
		system: 'Ubuntu 22.04.3，系统日期戳 20241128，B309',
		driver: '23.0',
		firmware: '7.3.3.0.b309（B309）',
		cann: '7.0',
		status: EVIDENCE.OBSERVED_PASS,
		scope: 'VENC only',
		note: 'CANN 7.0 基线在 VENC 上通过；升级到 8.0 后同一组合继续通过。',
		source: 'samples/case8/docs/05-known-issues-and-version-compatibility.md'
	},
	{
		id: 'orangepi-aipro-cann8.3rc1',
		board: 'Orange Pi AI Pro（香橙派 AI Pro）',
		soc: 'Ascend310B4',
		tier: '8T',
		system: 'Ubuntu 22.04.5，内核 5.10.0+',
		driver: '23.0.0',
		firmware: '未在报告中记录',
		cann: '8.3.RC1',
		status: EVIDENCE.OBSERVED_PASS,
		note: 'CANN 8.3.RC1（8.3.0.1.200）下记录了 VENC/VDEC/VPC 实测常量与推理通过；Health 仍为 Alarm。',
		source: 'src/book/chapter6.md；samples/case3/doc/10-benchmark-results.md'
	},
	{
		id: 'orangepi-aipro-20t-cann8',
		board: 'Orange Pi AI Pro 20T（hostname orangepiaipro-20t）',
		soc: 'Ascend310B1',
		tier: '20T',
		system: 'Ubuntu 版本未记录；aarch64',
		driver: '25.2',
		firmware: '未在报告中记录',
		cann: '8.0',
		status: EVIDENCE.OBSERVED_PASS,
		note: '统一 ascend 环境（Python 3.9.25）继承并继续验证 MindSpore 2.4.10 + MindNLP 0.4.1 机器门。',
		source: 'samples/case9/README.md；samples/case9/docs/56-mindspore-20t-isolated-rerun-20261016.md'
	},
	{
		id: 'atlas-310b-hdk25.5-cann9.2',
		board: '华为官方 Atlas 310B（Atlas 200I A2）',
		soc: 'Ascend310B4',
		tier: '8T',
		system: 'Ubuntu 22.04.5',
		driver: '25.5.0',
		firmware: '7.8.0.5.216',
		cann: '9.2.0-beta.2',
		status: EVIDENCE.OBSERVED_PASS,
		note: '该组合记录了 ACL、OM、摄像头、CANN VENC、WebRTC 与回归通过；HDMI/DRM 恢复是升级完成条件。',
		source: 'src/appendix/appendix7.md 路线 B；samples/case8/docs/05-known-issues-and-version-compatibility.md'
	},
	{
		id: 'atlas-310b-npu-smi-25.5',
		board: '官方 Atlas 310B4',
		soc: 'Ascend310B4',
		tier: '8T',
		driver: 'npu-smi 25.5.0 报告',
		firmware: '未在报告中记录',
		cann: '未在报告中记录',
		status: EVIDENCE.OBSERVED_PASS,
		note: 'case8 README 记录 npu-smi 25.5.0、Ascend 310B4、Health OK；CANN 版本在该报告里未记录。',
		source: 'samples/case8/README.md'
	}
]);

/**
 * CANN release lines and the Ascend HDK lines documented for them.
 *
 * `hdk` lists the HDK identifiers the release notes pair with this CANN line.
 * It is a release-line relationship, not a promise for one board SKU.
 */
export const CANN_RELEASE_LINES = Object.freeze([
	{
		line: '8.0',
		match: { major: 8, minor: 0 },
		hdk: [],
		documented: 'CANN 8.0 的配套以对应 8.0 发布包和目标产品下载页列出的 HDK 为准；本项目香橙派路线固定原有驱动/固件组合。',
		note: '本项目在香橙派 AI Pro 8T 上把 CANN 7.0 升级到 8.0，驱动和固件保持不变。'
	},
	{
		line: '8.3.RC1',
		match: { major: 8, minor: 3 },
		hdk: ['23.0'],
		documented: 'CANN 8.3.RC1（8.3.0.1.200）在本项目的香橙派 8T 上与驱动 23.0.0 组合完成过 VENC/VDEC/VPC 与推理实测。',
		note: '该书也记录：CANN 8.3 起部分 VPC 接口的可用型号收窄，310B（Atlas 200I A2）在 8.3.RC1 不支持某类 VPC 接口。'
	},
	{
		line: '8.5.X',
		match: { major: 8, minor: 5 },		hdk: ['26.0.RC1', '25.5.X', '25.3.X', '25.2.X', '25.0.X'],
		documented: 'CANN 8.5.X 与上列 HDK 版本线配套，具体以对应小版本说明为准。'
	},
	{
		line: '9.0.X',
		match: { major: 9, minor: 0 },
		hdk: ['25.7.RC1', '26.0.RC1', '26.0.RC3', '25.5.X', '25.3.X', '25.2.X', '25.0.X'],
		documented: 'CANN 9.0.X 与上列 HDK 版本线配套；Toolkit、ops、NNAL 可分层升级。'
	},
	{
		line: '9.2.X',
		match: { major: 9, minor: 2 },
		hdk: ['25.5.0'],
		documented: '本项目 Atlas 路线用 HDK 25.5.0、固件 7.8.0.5.216、驱动 25.5.0 与 CANN 9.2.0-beta.2 完成实测。',
		note: '其他 9.2.X 发布包仍需按目标产品下载页重新核对。'
	}
]);

/** Parsed version: numeric segments plus a comparable string tail. */
export function parseVersion(text) {
	if (typeof text !== 'string') return null;
	const match = /(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[.\-]?([0-9A-Za-z.]+))?/.exec(text.trim());
	if (match === null) return null;
	const tail = (match[4] ?? '').replace(/[^0-9A-Za-z.]/g, '');
	return {
		raw: text.trim(),
		major: Number(match[1]),
		minor: Number(match[2] ?? 0),
		patch: Number(match[3] ?? 0),
		tail,
		short: `${Number(match[1])}.${Number(match[2] ?? 0)}`
	};
}

/**
 * Compare two versions on major/minor only.
 *
 * @param {string} left - version text.
 * @param {string} right - version text.
 * @returns {number|null} -1, 0, 1, or null when either side is unparsable.
 */
export function compareMajorMinor(left, right) {
	const a = parseVersion(left);
	const b = parseVersion(right);
	if (a === null || b === null) return null;
	if (a.major !== b.major) return a.major < b.major ? -1 : 1;
	if (a.minor !== b.minor) return a.minor < b.minor ? -1 : 1;
	return 0;
}

/**
 * Describe the CANN release line a version belongs to.
 *
 * @param {string} cann - CANN version text from the board.
 * @returns {object|null} the release-line entry, or null when unparsable.
 */
export function releaseLineFor(cann) {
	const parsed = parseVersion(cann);
	if (parsed === null) return null;
	return (
		CANN_RELEASE_LINES.find(
			(entry) => entry.match.major === parsed.major && entry.match.minor === parsed.minor
		) ?? null
	);
}

/**
 * Match the probed environment against the repository's observed stacks.
 *
 * Matching requires the SoC to agree and every one of driver/CANN that the probe
 * actually reported to agree as well. A field the probe did not report is
 * skipped rather than treated as a mismatch: the npu-smi table carries no
 * firmware column on most 310B images, so requiring firmware would reject a
 * combination this repository genuinely recorded.
 *
 * @param {object} facts - flat facts map from the environment report.
 * @returns {object[]} matching stacks, best first.
 */
export function matchObservedStacks(facts) {
	const soc = facts?.soc?.value ?? null;
	const driver = facts?.driverVersion?.value ?? null;
	const cann = facts?.cannVersion?.value ?? null;
	const firmware = facts?.firmwareVersion?.value ?? null;
	const os = facts?.os?.value ?? null;
	const matches = [];
	for (const stack of OBSERVED_STACKS) {
		if (soc === null || stack.soc !== soc) continue;
		if (driver !== null && !versionExact(driver, stack.driver)) continue;
		if (cann !== null && !versionExact(cann, stack.cann)) continue;
		if (firmware !== null && /^[0-9]/u.test(String(stack.firmware)) && !versionExact(firmware, stack.firmware)) continue;
		if (os !== null && /^[0-9]/u.test(String(stack.system)) && !versionExact(os, stack.system)) continue;
		matches.push(stack);
	}
	return matches;
}

/**
 * Whether a probed version agrees with a recorded version text.
 *
 * Exact string match wins; otherwise every version-like token in the recorded
 * text is compared on major.minor. Recorded entries may legitimately list more
 * than one observed revision over time (for example `23.0 或 25.2`), so a match
 * against any listed token counts.
 *
 * @param {string} probed - version read from the board.
 * @param {string} recorded - version text recorded in the repository.
 * @returns {boolean} true when they refer to the same release.
 */
function versionAgrees(probed, recorded) {
	if (typeof probed !== 'string' || typeof recorded !== 'string') return false;
	if (probed.trim() === recorded.trim()) return true;
	const probedVersion = parseVersion(probed);
	if (probedVersion === null) return false;
	const tokens = recorded.match(/\d+(?:\.(?:\d+|[Xx])){1,3}(?:[.\-]?[0-9A-Za-z.]+)?/g) ?? [];
	for (const token of tokens) {
		const recordedVersion = parseVersion(token.replace(/[Xx]/g, '0'));
		if (recordedVersion === null) continue;
		if (probedVersion.major !== recordedVersion.major || probedVersion.minor !== recordedVersion.minor) continue;
		const patchToken = token.split('.')[2];
		if (patchToken !== undefined && !/[Xx]/u.test(patchToken) && probedVersion.patch !== recordedVersion.patch) continue;
		return true;
	}
	return tokens.length === 0 && compareMajorMinor(probed, recorded) === 0;
}

/** Exact observed tuple matching: wildcards and patch drift are not accepted. */
function versionExact(probed, recorded) {
	if (typeof probed !== 'string' || typeof recorded !== 'string') return false;
	if (probed.trim() === recorded.trim()) return true;
	const probedVersion = parseVersion(probed);
	if (probedVersion === null) return false;
	const tokens = recorded.match(/\d+(?:\.\d+){1,3}(?:[.\-]?[0-9A-Za-z.]+)?/g) ?? [];
	for (const token of tokens) {
		const recordedVersion = parseVersion(token);
		if (recordedVersion === null) continue;
		if (
			probedVersion.major === recordedVersion.major &&
			probedVersion.minor === recordedVersion.minor &&
			probedVersion.patch === recordedVersion.patch &&
			(recordedVersion.tail === '' || probedVersion.tail.toLowerCase() === recordedVersion.tail.toLowerCase())
		) {
			return true;
		}
	}
	return false;
}

/**
 * Judge the version pairing of a probed environment.
 *
 * The result never claims more than the evidence supports:
 *
 * - `observed-match`: the probed tuple reproduces a combination this repository
 *   recorded as passing.
 * - `documented-pairing`: the CANN release line is documented, and the driver
 *   major.minor is one of the documented HDK lines for that release.
 * - `documented-unpaired`: the CANN release line is documented but the probed
 *   driver major.minor is not listed for it; still `inferred`, never a denial.
 * - `undocumented-cann`: the CANN version is outside the release lines this
 *   plugin knows; requires the vendor compatibility query.
 * - `incomplete`: driver or CANN was not probed, so no pairing claim is made.
 *
 * @param {object} facts - flat facts map from the environment report.
 * @returns {object} pairing verdict with evidence, reasons and next actions.
 */
export function assessVersionPairing(facts) {
	const cann = facts?.cannVersion?.value ?? null;
	const driver = facts?.driverVersion?.value ?? null;
	const firmware = facts?.firmwareVersion?.value ?? null;

	if (cann === null || driver === null) {
		const missing = [cann === null ? 'cannVersion' : null, driver === null ? 'driverVersion' : null].filter(Boolean);
		return {
			verdict: 'incomplete',
			evidence: EVIDENCE.UNTESTED,
			reasons: [`未采集到 ${missing.join('、')}，无法判断版本配套。`],
			nextActions: [
				'在板端执行 ascend_probe 的默认只读脚本（npu-smi、驱动版本文件、CANN version.cfg），把输出交给工具解析；当前 310B 默认不采集固件版本，固件需要时由有权限的用户另行提供外部证据。',
				'不要把未采集的版本写成一个推测值；缺失项应保留为未采集。'
			],
			releaseLine: null,
			observedStacks: []
		};
	}

	const release = releaseLineFor(cann);
	const observed = matchObservedStacks(facts);
	if (observed.length > 0) {
		return {
			verdict: 'observed-match',
			evidence: EVIDENCE.OBSERVED_PASS,
			reasons: [
				`探测到的组合与仓库已记录的组合 ${observed[0].id} 一致：${observed[0].board} / ${observed[0].soc} / CANN ${observed[0].cann} / 驱动 ${observed[0].driver}。`,
				observed[0].scope === undefined
					? '这是完整组合记录。'
					: `该记录只覆盖 ${observed[0].scope}，不能外推为其它功能的 observed-pass。`,
				observed[0].note
			],
			nextActions: [
				'仍然要在本板重跑最小门：npu-smi 设备层、import acl、一次代表性模型加载与数值烟测。',
				'性能与精度数字必须来自本次运行的报告，不能引用已记录组合的旧数字。'
			],
			releaseLine: release,
			observedStacks: observed
		};
	}

	if (release === null) {
		return {
			verdict: 'undocumented-cann',
			evidence: EVIDENCE.UNTESTED,
			reasons: [
				`CANN ${cann} 不在本插件收录的发布线（8.0、8.5.X、9.0.X、9.2.X）内，因此不判断兼容性。`,
				'缺少记录不等于不支持：这一项是未验证，不是 observed-fail。'
			],
			nextActions: [
				'用昇腾版本配套查询助手按 310B 产品、操作系统、CPU 架构筛选该 CANN 版本对应的驱动/固件。',
				'确认配套后只做最小的设备层与 ACL 烟测，再决定是否扩大验证范围。'
			],
			releaseLine: null,
			observedStacks: []
		};
	}

	const paired = release.hdk.some((hdk) => versionAgrees(driver, hdk));
	const reasons = [
		`CANN ${cann} 属于 ${release.line} 发布线。${release.documented}`,
		firmware !== null
			? `板端固件 ${firmware}；固件与驱动由同一套 Ascend HDK 页面提供，但不是同一个版本号。`
			: '本次未采集固件版本；版本记录必须把固件与驱动分开，不能用驱动号代替固件号。'
	];
	if (paired) {
		reasons.push(`驱动 ${driver} 的 major.minor 在 ${release.line} 记录的 HDK 线内。`);
	} else {
		reasons.push(
			release.hdk.length === 0
				? `本项目未记录 CANN ${release.line} 的 HDK 列表，无法判断驱动 ${driver} 是否配套（未验证，不是不支持）。`
				: `驱动 ${driver} 的 major.minor 不在 ${release.line} 记录的 HDK 线（${release.hdk.join('、')}）内；只能说明当前记录不覆盖该组合。`
		);
	}

	return {
		verdict: paired ? 'documented-pairing' : 'documented-unpaired',
		evidence: paired ? EVIDENCE.DOCUMENTED : EVIDENCE.INFERRED,
		reasons,
		nextActions: paired
			? [
					'按升级后的统一验收顺序走：设备与驱动、CANN 与 PyACL、代表性模型、服务与前端、回归与性能。',
					'记录完整组合（系统日期戳、驱动、固件、Toolkit、ops/NNAL、Python、框架插件）后再写"某 CANN 环境"。'
				]
			: [
					'到昇腾版本配套查询助手核对目标产品的驱动、固件与 CANN 组合后再继续；本插件不替代该查询。',
					'若组合确认无误，先做最小 smoke：import acl、atc --version、一次 OM 加载，失败时按具体层次记录，不写成"硬件不支持"。'
				],
		releaseLine: release,
		observedStacks: []
	};
}

/** Unified conda environment contract for every case on an Ascend 310B board. */
export const UNIFIED_ENV_NAME = 'ascend';
export const UNIFIED_PYTHON_VERSION = '3.9.25';
export const UNIFIED_PYTHON_ABI_PREFIX = 'cpython-39';
export const UNIFIED_REQUIRED_PACKAGES = Object.freeze([
	'mindspore',
	'mindnlp',
	'fastapi',
	'httpx',
	'pydantic',
	'numpy',
	'cv2',
	'torch',
	'torchvision',
	'onnx',
	'onnxruntime',
	'sherpa_onnx',
	'aiortc',
	'av',
	'gradio',
	'faiss',
	'PySide6',
	'pyqtgraph',
	'aiohttp',
	'mido',
	'sounddevice',
	'python_multipart',
	'cryptography',
	'pandas',
	'tokenizers',
	'PIL',
	'huggingface_hub',
	'periphery',
	'usb',
	'rtmidi'
]);

/**
 * Check the shared runtime contract.
 *
 * This is separate from the CANN/driver pairing verdict: a missing env is an
 * application deployment failure, not evidence against the NPU stack.
 */
export function assessUnifiedEnvironment(facts) {
	const presentFact = facts?.ascendEnvPresent;
	if (presentFact === undefined) {
		return {
			verdict: 'incomplete',
			evidence: EVIDENCE.UNTESTED,
			reasons: ['探针没有提供 ascend 统一环境检查结果；需要重新运行 ascend_probe。'],
			nextActions: [`创建并固定 Python ${UNIFIED_PYTHON_VERSION} 的 conda 环境 ${UNIFIED_ENV_NAME}，禁止案例使用 base 或 pip --user。`]
		};
	}
	if (presentFact.value !== true) {
		return {
			verdict: 'failed',
			evidence: EVIDENCE.OBSERVED_FAIL,
			reasons: [`未找到 conda 环境 ${UNIFIED_ENV_NAME}；案例不得回退到 base 或 user-site。`],
			nextActions: [`创建 conda 环境 ${UNIFIED_ENV_NAME}，解释器固定为 Python ${UNIFIED_PYTHON_VERSION}。`]
		};
	}
	const version = facts?.ascendEnvVersion?.value ?? null;
	const abi = facts?.ascendEnvAbi?.value ?? null;
	// The policy probe is the launch contract. The direct interpreter field is
	// diagnostic only and must not make a non-isolated launch pass.
	const isolated = facts?.ascendEnvPolicyIsolated?.value === true;
	const acl = facts?.ascendEnvAclImport?.value ?? null;
	const aclruntime = facts?.ascendEnvAclruntime?.value ?? null;
	const mindsporeContext = facts?.ascendEnvMindsporeContext?.value ?? null;
	const packageText = facts?.ascendEnvPackages?.value ?? '';
	const importText = facts?.ascendEnvImports?.value ?? null;
	const importState = importText === null ? null : new Map(String(importText).split(',').filter(Boolean).map((item) => {
		const separator = item.indexOf(':');
		return separator < 0 ? [item, false] : [item.slice(0, separator), item.slice(separator + 1) === 'ok'];
	}));
	const packageState = new Map(
		String(packageText)
			.split(',')
			.filter(Boolean)
			.map((item) => {
				const [name, value] = item.split(':');
				return [name, value === 'true'];
			})
	);
	const missingPackages = UNIFIED_REQUIRED_PACKAGES.filter((name) => packageState.get(name) !== true);
	const missingImports = importState === null ? [] : UNIFIED_REQUIRED_PACKAGES.filter((name) => importState.get(name) !== true);
	const problems = [];
	if (version !== UNIFIED_PYTHON_VERSION) {
		problems.push(`Python 版本为 ${version ?? '未采集'}，统一环境要求 ${UNIFIED_PYTHON_VERSION}。`);
	}
	if (typeof abi !== 'string' || !abi.startsWith(UNIFIED_PYTHON_ABI_PREFIX)) {
		problems.push(`Python ABI 为 ${abi ?? '未采集'}，统一环境要求 ${UNIFIED_PYTHON_ABI_PREFIX}。`);
	}
	if (!isolated) problems.push('ascend 环境未证明禁用了 user-site。');
	if (acl !== 'ok') problems.push(`ascend 环境中 CANN source 后 PyACL 导入为 ${acl ?? '未采集'}。`);
	if (aclruntime !== 'ok') problems.push(`ascend 环境中 CANN source 后 aclruntime 导入为 ${aclruntime ?? '未采集'}。`);
	if (mindsporeContext !== 'ok') problems.push(`ascend 环境中 MindSpore Ascend context 为 ${mindsporeContext ?? '未采集'}。`);
	if (missingPackages.length > 0) {
		problems.push(`ascend 环境缺少统一案例包：${missingPackages.join('、')}。`);
	}
	if (missingImports.length > 0) {
		problems.push(`ascend 环境关键包实际导入失败：${missingImports.join('、')}。`);
	}
	if (problems.length === 0) {
		return {
			verdict: 'passed',
			evidence: EVIDENCE.OBSERVED_PASS,
			reasons: [
				`${UNIFIED_ENV_NAME} 使用 Python ${version}，ABI ${abi}，已禁用 user-site。`,
				'CANN source 后 PyACL 与 aclruntime 可导入，MindSpore Ascend context 可设置，统一案例关键包齐全。'
			],
			nextActions: ['继续逐个案例运行板端测试；全部通过后再生成不可变锁文件并清理旧环境。']
		};
	}
	return {
		verdict: 'failed',
		evidence: EVIDENCE.OBSERVED_FAIL,
		reasons: problems,
		nextActions: [
			'修复 ascend 环境；不要通过 base、user-site 或案例专用环境掩盖缺失依赖。',
			'所有案例测试通过后再锁定完整依赖，并保留旧环境作为回滚直到验收结束。'
		]
	};
}

/** The five acceptance gates the repository documents for a full-stack upgrade. */
export const ACCEPTANCE_GATES = Object.freeze([
	'设备与驱动',
	'CANN 与 PyACL',
	'代表性模型',
	'服务与前端',
	'回归与性能'
]);

/** Upgrade order the repository documents for a full-stack change. */
export const UPGRADE_ORDER = Object.freeze([
	'停止案例服务和自动启动单元，保留串口或本地控制台。',
	'保存当前驱动、固件、CANN、框架与模型的可回退材料。',
	'覆盖升级顺序固定为：先固件、再驱动、最后 CANN 软件。',
	'每一步失败即停止；升级过程中不断电、不复位。',
	'重启后先过设备门（npu-smi、驱动加载、设备节点），再安装或加载 CANN。',
	`最后按五道门验收：${ACCEPTANCE_GATES.join('、')}。`
]);
