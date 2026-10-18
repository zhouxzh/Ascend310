/**
 * Debugging knowledge for Ascend 310B programs.
 *
 * Every entry is written as a procedure with a stopping rule, not as folklore.
 * The vocabulary follows the repository's evidence rules: a failure is reported
 * at the layer where it was observed, and "not tested" never becomes
 * "unsupported".
 *
 * The entries are data. `plan.js` selects and renders them, so a new symptom is
 * a table row rather than new control flow.
 *
 * @module dsh-plugin-ascend310b-debug/knowledge
 */

/**
 * Probe buckets. A board probe answers exactly one of these questions; when a
 * step's probe is unavailable, the step is `untested` rather than passed.
 */
export const PROBE_BUCKETS = Object.freeze({
	device: {
		id: 'device',
		question: 'npu-smi 是否列出设备，芯片名与健康状态是什么？',
		commands: ['npu-smi info', 'npu-smi info -t board -i 0'],
		passes: '设备行存在，芯片名为 310B1/310B4，且设备节点 /dev/davinci* 存在。',
		fails: '无设备行、设备不存在、驱动未加载或 dmesg 出现复位循环。'
	},
	cann: {
		id: 'cann',
		question: 'CANN Toolkit、ATC 与 PyACL 是否在同一个 shell 里可用？',
		commands: [
			'source /usr/local/Ascend/ascend-toolkit/set_env.sh',
			'command -v atc && atc --version',
			"python -c 'import sys, acl; print(sys.executable, \"acl ok\")'",
			'cat /usr/local/Ascend/ascend-toolkit/latest/version.cfg'
		],
		passes: 'atc 可解析，import acl 成功，且打印出的解释器就是服务真正使用的解释器。',
		fails: 'import acl 失败、atc 不在 PATH、或交互 shell 成功而服务入口失败。'
	},
	conversion: {
		id: 'conversion',
		question: 'ONNX 到 OM 的转换是否以正确的 SoC 与输入合同完成？',
		commands: [
			'soc_version 来自 npu-smi，例如 Ascend310B4',
			'atc --framework=5 --model=<model.onnx> --output=<model> --soc_version=<SOC> --input_shape=<contract>',
			'ls -l <model>.om && sha256sum <model>.om'
		],
		passes: 'ATC 退出码为 0，OM 存在且摘要被记录。',
		fails: '非零退出、算子不支持、输入形状不匹配或 SoC 指定错误。'
	},
	load: {
		id: 'load',
		question: 'OM 能否被 ACL 真正加载，而不是只检查文件存在？',
		commands: [
			'python -c "import acl; print(acl.__file__)"',
			'运行案例自带的 OM 加载/推理入口',
			'npu-smi info（推理前后各一次，用于内存与温度采样）'
		],
		passes: 'ACL 加载成功并完成一次推理调用。',
		fails: '加载报错、段错误、设备重置或内存不释放。'
	},
	contract: {
		id: 'contract',
		question: '输入输出合同（名称、shape、dtype、byte size）是否与模型一致？',
		commands: [
			'检查模型导出脚本记录的输入输出描述',
			'对比运行时实际送入的数组 shape 与 dtype',
			'记录输入与输出的 byte size'
		],
		passes: '描述符的名称、维度和字节数与运行时一致。',
		fails: '维度错位、dtype 不符、输出数量不一致。'
	},
	numerical: {
		id: 'numerical',
		question: '同一输入的 NPU 输出与参考输出是否在容差内一致？',
		commands: ['固定一条代表性输入', '记录参考输出与 NPU 输出', '计算最大绝对误差与相对误差'],
		passes: '误差在案例声明的容差内，并记录输入哈希。',
		fails: '误差超限或输出长度不一致。'
	},
	task: {
		id: 'task',
		question: '任务级指标是否达标（识别率、EER、Rank-1、BLEU、帧率等）？',
		commands: ['按案例的评估脚本运行固定数据集', '记录样本数、线程数与预处理协议'],
		passes: '指标在声明的协议下复现。',
		fails: '指标不达标或协议不完整。'
	},
	performance: {
		id: 'performance',
		question: '性能是否在固定测量协议下稳定？',
		commands: ['记录 warmup、计时循环数、重复次数与线程数', '报告 P50/P95，而不是单次最好值'],
		passes: '协议完整，重复之间稳定。',
		fails: '单次测量、缺少 warmup 或把 20T 数字用于 8T。'
	},
	service: {
		id: 'service',
		question: '服务是否真的在 NPU 路径上工作（而不是只有健康检查通过）？',
		commands: ['GET /api/health 与 /api/bootstrap', '静态首页加载', '一次真实推理请求', 'npu-smi 推理前后采样'],
		passes: '健康检查、静态页和真实推理请求都成功，且 NPU 有活动。',
		fails: 'HTTP 通过但推理失败，或进程没有 CANN 环境。'
	},
	sanity: {
		id: 'sanity',
		question: '本地静态检查是否通过（语法、测试、构建、资产一致性）？',
		commands: ['python -m py_compile <files>', 'pytest <target>', 'npm test && npm run build'],
		passes: '静态检查通过，且没有把本地通过写成板端通过。',
		fails: '语法错误、单测失败、前端产物与源不一致。'
	}
});

/** The fixed gate order used by every debugging plan. */
export const GATE_ORDER = Object.freeze([
	'sanity',
	'device',
	'cann',
	'conversion',
	'load',
	'contract',
	'numerical',
	'task',
	'performance',
	'service'
]);

/**
 * Version predicates.
 *
 * `cann-at-least` states a release line the entry applies from. It is used only
 * to add a version-scoped note; the entry's procedure stays valid without it.
 */
export const VERSION_PREDICATES = Object.freeze({
	'cann-at-least-8.3': {
		id: 'cann-at-least-8.3',
		describe: 'CANN 8.3 及以上',
		test: (facts) => atLeast(facts?.cannVersion?.value, 8, 3),
		note:
			'CANN 8.3 起部分 VPC 接口的可用型号收窄：书中记录 310B（Atlas 200I A2）在 8.3.RC1 不支持某类 VPC 接口。涉及 VPC 的代码要按版本查接口型号约束，而不是照搬 310P 示例。'
	},
	'cann-at-least-9': {
		id: 'cann-at-least-9',
		describe: 'CANN 9.0 及以上',
		test: (facts) => atLeast(facts?.cannVersion?.value, 9, 0),
		note:
			'CANN 9.0.X 把若干 Transformer 相关接口标记为废弃，并记录了通信类已知问题。升级项目要先静态搜索废弃接口，再按调用路径迁移；版本说明中的特性不能写成已实测性能。'
	},
	'cann-9.2-atlas-route': {
		id: 'cann-9.2-atlas-route',
		describe: 'Atlas 路线的 CANN 9.2 组合',
		test: (facts) => atLeast(facts?.cannVersion?.value, 9, 2),
		note:
			'该组合记录为固件 7.8.0.5.216 + 驱动/HDK 25.5.0 + CANN 9.2.0-beta.2，并且升级后必须恢复 HDMI/DRM，否则升级不算完成。'
	},
	'driver-at-least-25': {
		id: 'driver-at-least-25',
		describe: '驱动 25.5 及以上',
		test: (facts) => atLeast(facts?.driverVersion?.value, 25, 5),
		note:
			'新版驱动下 npu-smi 的版式可能与旧版不同：有的版本打印 Ascend310B4，有的只打印 310B4。ATC 的 --soc_version 始终要按实际芯片名补齐 Ascend 前缀。'
	}
});

/** Compare a version text against a major.minor floor. */
function atLeast(text, major, minor) {
	if (typeof text !== 'string') return false;
	const match = /(\d+)(?:\.(\d+))?/.exec(text);
	if (match === null) return false;
	const gotMajor = Number(match[1]);
	const gotMinor = Number(match[2] ?? 0);
	return gotMajor > major || (gotMajor === major && gotMinor >= minor);
}

/**
 * Function areas, used for matching and for grouping in the panel.
 */
export const AREAS = Object.freeze({
	pyacl: { id: 'pyacl', label: 'PyACL / ACL 运行时' },
	atc: { id: 'atc', label: 'ATC 模型转换' },
	dvpp: { id: 'dvpp', label: 'DVPP 视频与图像' },
	camera: { id: 'camera', label: '摄像头与 V4L2' },
	framework: { id: 'framework', label: 'MindSpore / torch_npu' },
	service: { id: 'service', label: '服务与前端' },
	env: { id: 'env', label: '环境与版本' },
	performance: { id: 'performance', label: '性能与资源' },
	quant: { id: 'quant', label: '量化与精度' }
});

/**
 * The debugging entries.
 *
 * Fields:
 * - `area`: which function area this belongs to.
 * - `stage`: the gate where this failure manifests, so a plan can order it.
 * - `symptoms`: regular expressions matched against the user's description.
 * - `rootCause`: the mechanism, stated as a hypothesis to confirm.
 * - `steps`: ordered actions; each step names its probe bucket and stopping rule.
 * - `commands`: exact non-destructive commands.
 * - `versionPredicates`: predicate ids adding version-scoped notes.
 * - `references`: repository documents that carry the recorded evidence.
 */
export const ENTRIES = Object.freeze([
	{
		id: 'acl-import-failed',
		title: 'import acl 失败（PyACL 不可用）',
		area: 'pyacl',
		stage: 'cann',
		symptoms: ['no module named acl', 'import acl', 'pyacl', 'acl unavailable', 'acl 初始化', 'acl init'],
		rootCause:
			'CANN 环境从未在该进程的 shell 中 source，或解释器与 CANN 不匹配。本地开发机没有 Ascend 运行时也会得到同样的报错。',
		steps: [
			{ probe: 'cann', do: '在启动进程的同一个 shell 里 source /usr/local/Ascend/ascend-toolkit/set_env.sh，再 import acl。', stop: '若同一 shell 成功，问题在启动入口而不是安装。' },
			{ probe: 'cann', do: '打印 sys.executable 与 sys.path，确认解释器就是服务实际使用的那个。', stop: '若解释器与预期不同，修启动脚本而不是反复重装 CANN。' },
			{ probe: 'cann', do: '检查 PYTHONNOUSERSITE 与会话用户 site 的关系。板端有效的 MindSpore/MindNLP 轮子可能只装在当前用户的 site 里。', stop: '隔离环境与真实启动环境都必须记录，不能只测其中一个。' },
			{ probe: 'device', do: '确认设备层正常：npu-smi info 与 /dev/davinci*。', stop: '设备层失败时先修驱动，不要用 CPU 回退掩盖。' }
		],
		commands: [
			'source /usr/local/Ascend/ascend-toolkit/set_env.sh',
			"python -c 'import sys; print(sys.executable); print(sys.path)'",
			"python -c 'import acl; print(\"acl: ok\")'",
			'npu-smi info'
		],
		references: ['src/appendix/appendix7.md', 'samples/chapter4/README.md']
	},
	{
		id: 'atc-failure',
		title: 'ATC 转换失败或算子不支持',
		area: 'atc',
		stage: 'conversion',
		symptoms: ['atc', 'onnx', 'om', '算子不支持', 'e19999', '转换失败', 'soc_version', 'input_shape'],
		rootCause:
			'目标 SoC、输入形状/布局、算子集合或 CANN 版本与该模型不匹配。转换失败与模型能否运行是两个独立结论。',
		steps: [
			{ probe: 'device', do: '从 npu-smi 读出实际芯片名，构造成 --soc_version（310B4 -> Ascend310B4）。', stop: 'SoC 名必须以板端为准，不能沿用其他设备的示例。' },
			{ probe: 'cann', do: '确认 atc 来自本次 source 的 Toolkit，记录 atc --version 与 CANN version.cfg。', stop: '版本不完整时先补版本记录，否则失败无法归因。' },
			{ probe: 'conversion', do: '用模型自己的输入合同重新转换，保存完整命令与退出码。', stop: '失败时保留原始日志，不要手工伪造 OM。' },
			{ probe: 'conversion', do: '若特定算子失败，缩小到最小图复现，再判断是算子缺失还是精度模式问题。', stop: '不要用另一个模型的 OM 顶替。' }
		],
		commands: [
			'npu-smi info',
			'source /usr/local/Ascend/ascend-toolkit/set_env.sh',
			'atc --version',
			'atc --framework=5 --model=<model.onnx> --output=<model> --soc_version=Ascend310B4 --input_shape=<name:1,3,H,W>'
		],
		versionPredicates: ['cann-at-least-8.3'],
		references: ['src/book/chapter2.md', 'src/appendix/appendix7.md', 'samples/case2/scripts/convert_onnx_to_om.py']
	},
	{
		id: 'om-load-failure',
		title: 'OM 存在但 ACL 加载失败',
		area: 'pyacl',
		stage: 'load',
		symptoms: ['om 加载', 'load model', 'acl.mdl', 'execute failed', '推理失败', 'segmentation', 'exit 139', '段错误'],
		rootCause:
			'文件存在不等于可执行：可能 SoC 不匹配、CANN 版本不匹配、内存不足，或输入描述符与转换时不同。段错误通常属于运行时或驱动层，而不是模型层。',
		steps: [
			{ probe: 'load', do: '用最小入口加载 OM 并记录 ACL 错误码与日志。', stop: '先确认加载本身通过，再谈精度。' },
			{ probe: 'device', do: '加载前后各采一次 npu-smi，记录内存与温度变化。', stop: '内存不回落或设备重置时按资源泄漏处理。' },
			{ probe: 'contract', do: '核对转换时与运行时的输入名称、shape、dtype、byte size。', stop: '描述符不一致时不要改容差，先修合同。' },
			{ probe: 'cann', do: '确认 OM 生成与加载使用同一版本线；跨版本组合必须有记录。', stop: '无法确认时标记 untested，不要写成硬件不支持。' }
		],
		commands: [
			'python -c "import acl; print(acl.__file__)"',
			'npu-smi info',
			'ls -l <model>.om && sha256sum <model>.om'
		],
		references: ['src/book/chapter4.md', 'samples/case9/docs']
	},
	{
		id: 'device-missing',
		title: '设备缺失、驱动未加载或设备节点不存在',
		area: 'env',
		stage: 'device',
		symptoms: ['device not found', 'no device', '设备不存在', '驱动', 'driver', 'devmm', 'davinci', 'reset', '复位'],
		rootCause: '驱动/固件与内核不配套、设备节点未创建，或设备进入复位循环。',
		steps: [
			{ probe: 'device', do: '记录 npu-smi 输出、/dev/davinci*、/dev/devmm*、dmesg 尾部。', stop: '无设备行时先修驱动层，不要继续调模型。' },
			{ probe: 'device', do: '确认驱动与固件是同一套 Ascend HDK 的配套组合。', stop: '覆盖升级顺序固定为先固件、再驱动、最后 CANN。' },
			{ probe: 'device', do: 'Alarm 属于已知板况：记录为诊断背景，但不作为单独阻断项。', stop: '只有在设备缺失、加载失败或复位循环时才阻断。' }
		],
		commands: ['npu-smi info', 'ls -l /dev/davinci* /dev/devmm* 2>/dev/null', 'lsmod | grep -E "drv|ascend|hisi"', 'dmesg -T | tail -n 120'],
		references: ['src/appendix/appendix7.md', 'AGENTS.md']
	},
	{
		id: 'health-alarm',
		title: 'npu-smi Health 显示 Alarm',
		area: 'env',
		stage: 'device',
		symptoms: ['health', 'alarm', '告警', '温度', 'temperature'],
		rootCause:
			'该板 Health: Alarm 是仓库记录的已知板况。它本身不是 ATC、ACL、精度或性能失败的证据。',
		steps: [
			{ probe: 'device', do: '记录 Health、温度、功耗与内存，写入诊断背景。', stop: '不要因为 Alarm 单独阻止转换、推理或性能测量。' },
			{ probe: 'device', do: '继续检查具体失败：acl 缺失、设备不存在、ATC 非零退出、OM 缺失、推理不一致、退出码 139、设备复位、资源泄漏。', stop: '用具体失败替代告警作为判据。' }
		],
		commands: ['npu-smi info'],
		references: ['AGENTS.md', 'src/appendix/appendix2.md']
	},
	{
		id: 'mixed-framework-symbols',
		title: 'MindSpore / MindSpore Lite 混装导致 undefined symbol',
		area: 'framework',
		stage: 'cann',
		symptoms: ['undefined symbol', 'libmindspore', 'mindspore lite', 'abi', 'import order', '混装'],
		rootCause:
			'MindSpore 与 MindSpore Lite 是分别版本化的运行时。同一进程或同一环境混用不同发布线会出现未定义符号，这是该组合的环境不匹配，不是 310B 硬件不支持。',
		steps: [
			{ probe: 'cann', do: '在全新进程里分别 import，记录每个库的路径与版本。', stop: '先定位是哪个库带入了错误版本。' },
			{ probe: 'cann', do: '两个运行时不要在同一进程里混用；按案例的启动脚本分别启动。', stop: '混装报错时改环境组合，不要改结论为“不支持”。' },
			{ probe: 'cann', do: 'MindIR 版本高于 Lite 支持的版本时，属于版本不配套，重试同一文件没有意义。', stop: '要么改导出端版本，要么换支持该 MindIR 版本的 Lite。' }
		],
		commands: [
			"python -c 'import mindspore, sys; print(sys.executable); print(mindspore.__version__, mindspore.__file__)'",
			"python -c 'import mindspore_lite as mslite; print(mslite.__version__)'"
		],
		references: ['src/book/chapter5.md', 'samples/chapter5/docs/01-board-validation-record-20261016.md']
	},
	{
		id: 'camera-v4l2',
		title: '摄像头打不开、画面错尺寸或预览卡顿',
		area: 'camera',
		stage: 'service',
		symptoms: [
			'camera',
			'v4l2',
			'video0',
			'摄像头',
			'mjpg',
			'preview',
			'预览',
			'摄像头权限',
			'venc',
			'vdec',
			'dvpp',
			'iommu_map',
			'507018',
			'h264',
			'h265'
		],
		rootCause:
			'用户不在 video 组、设备被占用、像素格式或实际分辨率与预期不同，或预览请求没有串行化。',
		steps: [
			{ probe: 'service', do: '确认启动用户属于 video 组，并且重新登录（或重新插拔设备）使组生效。', stop: '改完组权限必须重新登录，不是重启服务即可。' },
			{ probe: 'service', do: '枚举设备与格式，记录实际分辨率、FPS 和像素格式。', stop: '以设备实际返回的尺寸为准，不要用请求值写报告。' },
			{ probe: 'service', do: '优先 MJPG、减小 V4L2 缓冲、串行轮询预览，并在预览或任务结束时释放设备。', stop: '设备未释放会导致下一个进程打不开。' }
		],
		commands: ['id', 'v4l2-ctl --list-devices', 'v4l2-ctl -d /dev/video0 --list-formats-ext'],
		versionPredicates: ['driver-at-least-25'],
		references: ['samples/case8/README.md', 'src/experiment/case8.md']
	},
	{
		id: 'service-npu-unavailable',
		title: '服务健康检查通过但 NPU 推理失败',
		area: 'service',
		stage: 'service',
		symptoms: ['health check', 'api', 'http 500', '健康检查', '服务', 'uvicorn', 'app.py', '进程'],
		rootCause:
			'服务进程没有 CANN 环境。这样 HTTP 端口可以正常应答，但任何真实推理都会失败。',
		steps: [
			{ probe: 'service', do: '用案例的启动脚本重启服务（它带 import acl 预检），不要用裸 python app.py 绕过。', stop: '只重启被确认的 PID。' },
			{ probe: 'service', do: '依次验证监听端口、GET /api/health、GET /api/bootstrap、静态首页，再发一次真实推理请求。', stop: '静态页通过不代表 NPU 可用。' },
			{ probe: 'device', do: '推理前后各采一次 npu-smi，确认设备真的有工作。', stop: '没有 NPU 活动时按启动环境问题处理。' }
		],
		commands: [
			'source /usr/local/miniconda3/etc/profile.d/conda.sh && conda activate base',
			'source /usr/local/Ascend/ascend-toolkit/set_env.sh',
			'bash scripts/run_palmprint_service.sh --host 0.0.0.0 --port 7860',
			'npu-smi info'
		],
		references: ['AGENTS.md', 'samples/case4/README.md']
	},
	{
		id: 'large-model-timeout',
		title: '大模型请求超时或 TBE 子进程长时间不返回',
		area: 'framework',
		stage: 'task',
		symptoms: ['timeout', '超时', 'tbe', 'hang', '卡住', '长时间', 'gateway timeout'],
		rootCause:
			'超时可能是客户端过短、编译/算子调优耗时，或真的卡死。把超时当作崩溃会污染结论。',
		steps: [
			{ probe: 'task', do: '区分客户端超时与服务端未返回：同时记录客户端超时参数和服务端 watchdog。', stop: '只有服务端任务也超时才按卡住处理。' },
			{ probe: 'task', do: '保留首事件时间、总耗时 P50/P95 与吞吐，而不是只留成功/失败。', stop: '超时未确认前标记 timeout-confounded，不写成模型崩溃。' },
			{ probe: 'device', do: '检查推理期间的内存与设备状态。', stop: '内存耗尽与超时是不同机制。' }
		],
		commands: ['npu-smi info', 'tail -n 120 <service log>'],
		references: ['src/experiment/case9.md', 'samples/case9/docs']
	},
	{
		id: 'cann-version-drift',
		title: 'CANN 子包版本漂移或跨发布线组合',
		area: 'env',
		stage: 'cann',
		symptoms: ['cann version', 'version.cfg', '版本不配套', 'toolkit', 'ops', 'nnal', '子包', 'nt_compiler'],
		rootCause:
			'Toolkit、算子包（ops）与加速库（NNAL）可以分层升级。跨发布线组合会让 atc、runtime 与已生成 OM 处在不同版本线上。',
		steps: [
			{ probe: 'cann', do: '分别记录 Toolkit version.cfg、compiler/version.info 和驱动版本；固件只有在有可信外部证据时才记录，不要只写一个“CANN 版本”。', stop: '缺少任何一项关键版本时不要把组合写成已验证。' },
			{ probe: 'cann', do: '优先使用同一发布线的 Toolkit 与 ops；跨版本组合需要单独的回归报告。', stop: '编译通过不代表废弃接口可以长期保留。' },
			{ probe: 'conversion', do: '升级 CANN 后重新跑一次 ATC 与 OM 加载，旧 OM 不自动获得新版本行为。', stop: '升级后的验收按五道门顺序执行。' }
		],
		commands: [
			'cat /usr/local/Ascend/ascend-toolkit/latest/version.cfg',
			'sed -n "1,20p" /usr/local/Ascend/ascend-toolkit/latest/compiler/version.info',
			'cat /var/davinci/driver/version.info 2>/dev/null || true'
		],
		versionPredicates: ['cann-at-least-9', 'cann-9.2-atlas-route'],
		references: ['src/appendix/appendix7.md']
	},
	{
		id: 'memory-growth',
		title: '推理循环内存持续增长或设备资源不释放',
		area: 'performance',
		stage: 'performance',
		symptoms: ['memory leak', '内存增长', '泄漏', '资源不释放', 'channel', '通道', '句柄'],
		rootCause:
			'DVPP 通道、ACL 句柄或设备内存没有在结束时释放；长时间循环会累积。',
		steps: [
			{ probe: 'performance', do: '固定循环次数，在开始、每 N 次和结束时记录 npu-smi 内存。', stop: '用斜率判断增长，而不是比较两次瞬时值。' },
			{ probe: 'performance', do: '检查 DVPP 通道的创建/销毁配对，以及异常分支是否也释放。', stop: '每个通道的创建都要有对应的销毁路径。' },
			{ probe: 'load', do: '确认 ACL 资源在循环外复用，而不是每次调用都新建。', stop: '资源复用与泄漏是两个不同问题，分别测量。' }
		],
		commands: ['npu-smi info', '# 在循环中固定间隔重复采样'],
		references: ['src/book/chapter6.md', 'src/book/chapter8.md']
	},
	{
		id: 'quantization-accuracy',
		title: '量化后精度下降或数值不一致',
		area: 'quant',
		stage: 'numerical',
		symptoms: ['量化', 'quant', '精度下降', 'accuracy', '误差', 'fp16', 'int8'],
		rootCause:
			'精度模式、白名单节点、校准数据或算子实现改变了数值路径。精度问题必须定位到层，而不是笼统归因。',
		steps: [
			{ probe: 'numerical', do: '先固定输入哈希与参考实现，测最大绝对误差。', stop: '没有参考输出的比较不是数值验证。' },
			{ probe: 'contract', do: '确认比较的是同一层输出：输入合同、预处理与后处理必须一致。', stop: '预处理差异会伪装成量化误差。' },
			{ probe: 'task', do: '数值通过后再看任务级指标；数值烟测不是 EER/Rank-1/mAP。', stop: '不要把数值一致性写成精度达标。' }
		],
		commands: ['# 使用案例自带的数值比较脚本，记录输入哈希与容差'],
		references: ['src/book/chapter9.md', 'samples/chapter9/README.md']
	},
	{
		id: 'stale-sample-command',
		title: '样例文档里的命令在当前代码树上不存在',
		area: 'env',
		stage: 'sanity',
		symptoms: ['no such file', 'command not found', '找不到文件', '入口不存在', 'sample_run', 'requirementstxt'],
		rootCause:
			'样例 README 有一层独立于代码的演进历史：迁移或清理后，README 仍指向已经改名或删除的入口。照搬这类命令会把"文件不存在"误读成环境故障。',
		steps: [
			{ probe: 'sanity', do: '先确认被引用的文件真的存在，再判断依赖或权限问题。', stop: '文件不存在时改用样例目录里实际存在的入口。' },
			{ probe: 'sanity', do: '以该样例目录的 README 与 docs/ 为运行入口，已知偏差包括：case2 的依赖文件名是 requirements.txt；chapter2 的 sample_run.sh 与 model/resnet50.onnx 不存在；case4 的旧入口 palmprint_service.py / scripts/run_palmprint_service.sh 已由 `python -m palmprint_workbench.api` 取代。', stop: '不要为了让旧命令"跑通"而新建兼容脚本。' },
			{ probe: 'sanity', do: '如果命令是在别的目录执行的，先确认工作目录：多个样例依赖相对路径。', stop: '相对路径错误也是"文件不存在"的常见来源。' }
		],
		commands: ['ls <被引用的路径>', 'python -m py_compile <入口文件>'],
		references: ['samples/case2/README.md', 'samples/case4/AGENTS.md', 'samples/chapter2/sample_resnet_quick_start/README.md']
	},
	{
		id: 'mindir-lite-version-gate',
		title: 'MindIR 版本高于 MindSpore Lite 支持范围',
		area: 'framework',
		stage: 'load',
		symptoms: ['mindir', 'mindspore lite', 'maximum mind ir version', '版本不配套', 'fail to support'],
		rootCause:
			'MindSpore 导出的 MindIR 版本与 Lite 运行时支持的版本不同，加载会在格式检查阶段被拒绝。重试同一个文件没有意义。',
		steps: [
			{ probe: 'load', do: '记录导出端 MindSpore 版本与 Lite 运行时版本，并确认 MindIR 版本。', stop: '版本记录不完整时不要写成硬件不支持。' },
			{ probe: 'load', do: '两种修法只能选一种：改导出端版本让 MindIR 降版本，或换支持该 MindIR 版本的 Lite。', stop: '不要反复重试同一文件。' },
			{ probe: 'load', do: 'Lite 与完整 MindSpore 分开进程运行；同一进程混用会出现未定义符号。', stop: '混装报错属于环境组合问题。' }
		],
		commands: [
			"python -c 'import mindspore; print(mindspore.__version__)'",
			"python -c 'import mindspore_lite as mslite; print(mslite.__version__)'"
		],
		references: ['samples/chapter5/README.md', 'src/book/chapter5.md']
	},
	{
		id: 'performance-not-reproducible',
		title: '性能数字无法复现或跨板混用',
		area: 'performance',
		stage: 'performance',
		symptoms: ['性能', 'latency', 'p50', 'p95', 'fps', '吞吐', 'throughput', 'benchmark'],
		rootCause:
			'缺少 warmup 与重复次数、线程数不同，或把 20T 的数字当成 8T 的结果。',
		steps: [
			{ probe: 'performance', do: '固定模型、精度、硬件档位、输入、warmup、计时次数、重复次数与线程数。', stop: '少任何一项就标记 untested，不写成性能结论。' },
			{ probe: 'device', do: '在同一次运行里记录 npu-smi 状态作为背景。', stop: 'Alarm 作为背景记录，不作为阻断。' },
			{ probe: 'performance', do: '报告 P50/P95 与重复间波动，保留原始报告路径。', stop: '8T 与 20T 的结果不得合并排名。' }
		],
		commands: ['npu-smi info', '# 使用案例自带 benchmark，记录完整参数'],
		references: ['AGENTS.md', 'src/book/chapter8.md']
	}
]);

/** Group entries by area for panel rendering. */
export function entriesByArea() {
	const groups = new Map();
	for (const entry of ENTRIES) {
		const list = groups.get(entry.area) ?? [];
		list.push(entry);
		groups.set(entry.area, list);
	}
	return groups;
}
