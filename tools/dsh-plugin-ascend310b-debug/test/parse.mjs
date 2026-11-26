/**
 * Probe parser tests.
 *
 * Usage: node test/parse.mjs
 */

import { buildAudioProbeScript, buildCameraProbeScript, buildIdentityProbeScript, buildProbeScript, buildSystemProbeScript, parseIpAddress, parseLsblkRecords, parseMacEntries, parseMacSection, parseNpuSmiInfo, parseOsRelease, parseProbeOutput, parseStorageFacts, parseVersionCfg, parentDiskName } from '../lib/probe.js';
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
equal(
	parseMacSection('eth0=c0:74:2b:fd:dd:7a\nwlan0=aa:bb:cc:dd:ee:ff\n'),
	'c0:74:2b:fd:dd:7a',
	'MAC 解析应优先 eth0，而不是任意接口'
);
equal(
	parseMacEntries('eth0=c0:74:2b:fd:dd:7a\nwlan0=aa:bb:cc:dd:ee:ff\neth1=c0:74:2b:fd:dd:7b\n').length,
	3,
	'应保留同一块板的全部网口 MAC 别名'
);
equal(
	parseMacSection('docker0=02:42:ac:11:00:02\nbond0=02:42:ac:11:00:03\neth0=c0:74:2b:fd:dd:7a\n'),
	'c0:74:2b:fd:dd:7a',
	'不能用容器、bond 或虚拟网卡 MAC 作为板卡身份'
);
// --- storage topology and capacity ---------------------------------------
const lsblkText = [
	'NAME="mmcblk0" TYPE="disk" SIZE="63864569856" RO="0" RM="1" MODEL="SD64G" TRAN="mmc" FSTYPE="" MOUNTPOINTS=""',
	'NAME="mmcblk0p1" TYPE="part" SIZE="536870912" RO="0" RM="1" MODEL="" TRAN="" FSTYPE="vfat" MOUNTPOINTS="/boot"',
	'NAME="mmcblk0p2" TYPE="part" SIZE="63327698944" RO="0" RM="1" MODEL="" TRAN="" FSTYPE="ext4" MOUNTPOINTS="/"',
	'NAME="nvme0n1" TYPE="disk" SIZE="1024209543168" RO="0" RM="0" MODEL="Fast NVMe SSD" TRAN="nvme" FSTYPE="" MOUNTPOINTS=""',
	'NAME="nvme0n1p1" TYPE="part" SIZE="1024209543168" RO="0" RM="0" MODEL="" TRAN="" FSTYPE="ext4" MOUNTPOINTS="/data"'
].join('\n');
const dfText = [
	'Filesystem Type 1024-blocks Used Available Capacity Mounted-on',
	'/dev/root ext4 61831424 21345280 37377920 37% /',
	'/dev/mmcblk0p1 vfat 523776 12000 511776 3% /boot',
	'/dev/nvme0n1p1 ext4 1000204886 250000000 700204886 26% /data',
	'tmpfs tmpfs 1048576 0 1048576 0% /run'
].join('\n');
const lsblkRecords = parseLsblkRecords(lsblkText);
equal(lsblkRecords.length, 5, 'lsblk 应解析为五个块设备记录');
equal(parentDiskName('mmcblk0p2'), 'mmcblk0', 'mmcblk 分区应归到父设备');
equal(parentDiskName('nvme0n1p1'), 'nvme0n1', 'NVMe 分区应归到父设备');
const storage = parseStorageFacts(lsblkText, dfText, 'NAME=mmcblk0 TYPE=SD DEVICE_NAME=SC64G');
equal(storage.length, 3, '应保留 SD、NVMe 和 boot 文件系统并忽略 tmpfs');
equal(storage.find((item) => item.mount === '/')?.kind, 'SD卡', 'RM/mmc type 应识别为 SD 卡');
equal(storage.find((item) => item.mount === '/')?.source, '/dev/mmcblk0p2', 'df 的 /dev/root 应通过 lsblk 挂载点还原真实分区');
equal(storage.find((item) => item.mount === '/')?.model, 'SD64G', 'SD 卡型号应来自 mmc 设备名');
equal(storage.find((item) => item.mount === '/data')?.kind, 'NVMe固态硬盘', 'nvme 应识别为固态硬盘');
equal(storage.find((item) => item.mount === '/')?.availableBytes, 37377920, '应保留剩余容量字节数');

const sataUsage = '/dev/sda1 ext4 1000 100 900 10% /data';
const sataDisk = 'NAME="sda" TYPE="disk" RM="0" MODEL="Samsung 870 EVO" TRAN="sata"';
equal(parseStorageFacts(`${sataDisk} ROTA="0"`, sataUsage)[0].kind, 'SATA固态硬盘', 'ROTA=0 应识别不含 SSD 关键词的 SATA 固态硬盘');
equal(parseStorageFacts(`${sataDisk} ROTA="1"`, sataUsage)[0].kind, 'SATA硬盘', 'ROTA=1 应识别 SATA 机械硬盘');
equal(parseStorageFacts(sataDisk, sataUsage)[0].kind, 'SATA存储（类型未知）', '缺失 ROTA 时不能由型号猜测 SATA 介质类型');
equal(parseStorageFacts('NAME="sda" TYPE="disk" RM="0" ROTA="1" TRAN="sas"', sataUsage)[0].kind, '块设备', 'sd 设备名不应把 SAS 误报成 SATA');
equal(parseStorageFacts('NAME="sda" TYPE="disk" RM="1" ROTA="0" TRAN=""', sataUsage)[0].kind, '块设备', '可移除标志本身不应证明 USB 传输');
equal(parseStorageFacts('NAME="sda" TYPE="disk" RM="0" TRAN="usb"', sataUsage)[0].kind, 'USB存储', '明确 USB 传输应保留 USB 分类');
equal(parseStorageFacts(lsblkText, dfText, 'NAME=mmcblk0 TYPE=MMC')[0].kind, 'eMMC', '明确 MMC 类型应识别为 eMMC');
equal(parseStorageFacts(lsblkText, dfText)[0].kind, 'MMC存储（类型未知）', '缺失 MMC 类型时不能由 RM 推断 SD 或 eMMC');
for (const [source, filesystem] of [['server:/data', 'nfs4'], ['//server/share', 'cifs'], ['user@server:/data', 'fuse.sshfs']]) {
	const network = parseStorageFacts('', `${source} ${filesystem} 1000 100 900 10% /network`)[0];
	equal(network.kind, '网络文件系统', `${filesystem} 应与本地块设备区分`);
	equal(network.disk, null, `${filesystem} 不应伪造本地磁盘名称`);
}

const identityScript = buildIdentityProbeScript();
check(identityScript.includes('abs_section mac'), '轻量发现脚本必须读取网口 MAC');
check(identityScript.includes('abs_section machine_id'), '轻量发现脚本必须保留 machine-id 作为历史证据');
check(!identityScript.includes('abs_section ascend_env'), '轻量发现脚本不应等待完整 CANN/Python 环境检查');
check(!identityScript.includes('abs_section video_devices'), '轻量发现脚本不应枚举音视频设备');
equal(parseIpAddress('10.0.0.100 2001:db8::1\n'), '10.0.0.100', 'IP 解析应优先使用 IPv4');
const macProbe = parseProbeOutput(
	'===ABS_BEGIN:mac===\neth0=c0:74:2b:fd:dd:7a\nwlan0=aa:bb:cc:dd:ee:ff\n===ABS_END:mac===\n===ABS_BEGIN:machine_id===\nabc123\n===ABS_END:machine_id===\n'
).facts;
equal(macProbe.macAddress.value, 'c0:74:2b:fd:dd:7a', '环境报告应保留规范 MAC');
equal(macProbe.macAddresses.value.length, 2, '环境报告应保留全部 MAC 别名');
equal(macProbe.macInterfaces.value[1].iface, 'wlan0', '环境报告应保留接口名到 MAC 的映射');

// --- end-to-end transcripts ----------------------------------------------
const eight = parseProbeOutput(PROBE_8T_CANN80);
equal(eight.board.soc, 'Ascend310B4', '8T 记录应识别 SoC');
equal(eight.board.tier, '8T', '310B4 应映射到 8T');
equal(eight.facts.cannVersion.value, '8.0.0', '应从 runtime_running_version 取出 CANN 8.0.0');
equal(eight.facts.driverVersion.value, '25.2.0', '应读出驱动版本');
equal(eight.facts.aclImport.value, 'ok', 'import acl 成功应记为 ok');
equal(eight.facts.socTier.evidenceLabel, 'documented', '档位映射应标记为 documented');
equal(eight.missingCore.length, 0, 'SoC、CANN、驱动齐全时 missingCore 应为空');

const withDevices = parseProbeOutput(`${PROBE_8T_CANN80}
===ABS_BEGIN:sound_cards===
 0 [AudioPCI    ]: AudioPCI - Ensoniq AudioPCI
===ABS_END:sound_cards===
===ABS_BEGIN:audio_playback===
card 0: AudioPCI [AudioPCI], device 0: playback
===ABS_END:audio_playback===
===ABS_BEGIN:audio_capture===
card 0: AudioPCI [AudioPCI], device 0: capture
===ABS_END:audio_capture===
===ABS_BEGIN:audio_sinks===
0  alsa_output.pci-0000_00_1b.0.analog-stereo
===ABS_END:audio_sinks===
===ABS_BEGIN:audio_sources===
1  alsa_input.usb-046d_0825-02.mono-fallback
===ABS_END:audio_sources===
===ABS_BEGIN:usb_devices===
Bus 001 Device 003: ID 046d:0825 Logitech, Inc. Webcam C270
===ABS_END:usb_devices===
===ABS_BEGIN:video_devices===
HD Webcam C270 (usb-0000:00:14.0-1):
	/dev/video0
===ABS_END:video_devices===
`);
equal(withDevices.facts.soundCards.value.includes('AudioPCI'), true, '声卡块应保留');
equal(withDevices.facts.audioPlayback.value.includes('playback'), true, '播放设备块应保留');
equal(withDevices.facts.audioCapture.value.includes('capture'), true, '录音设备块应保留');
equal(withDevices.facts.audioSinks.value.includes('alsa_output'), true, '音频 sink 应保留');
equal(withDevices.facts.audioSources.value.includes('alsa_input'), true, '音频 source 应保留');
equal(withDevices.facts.usbDevices.value.includes('Webcam'), true, 'USB 摄像头应保留');
equal(withDevices.facts.videoDevices.value.includes('/dev/video0'), true, 'V4L2 视频节点应保留');

const withStorage = parseProbeOutput(`${PROBE_8T_CANN80}
===ABS_BEGIN:storage_devices===
${lsblkText}
===ABS_END:storage_devices===
===ABS_BEGIN:storage_usage===
${dfText}
===ABS_END:storage_usage===
===ABS_BEGIN:storage_mmc===
NAME=mmcblk0 TYPE=SD DEVICE_NAME=SC64G
===ABS_END:storage_mmc===
`);
equal(withStorage.facts.storage.value.length, 3, '完整探测应产出结构化存储事实');
equal(withStorage.board.storage.length, 3, '板卡摘要应携带存储容量供前端显示');
equal(withStorage.board.storage[0].totalBytes > 0, true, '存储总量必须是板端实际字节数');
const emptyStorage = parseProbeOutput(`${PROBE_8T_CANN80}
===ABS_BEGIN:storage_usage===
===ABS_END:storage_usage===
`);
equal(emptyStorage.facts.storage.value.length, 0, '已采集但为空的存储段必须产生显式空数组以覆盖旧事实');
equal(emptyStorage.board.storage.length, 0, '空存储事实应与板卡摘要一致');
equal(parseProbeOutput(PROBE_8T_CANN80).facts.storage, undefined, '未采集 storage_usage 时不得产生清除旧事实的空数组');
const ignoredStorage = parseProbeOutput('===ABS_BEGIN:storage_usage===\ntmpfs tmpfs 1000 100 900 10% /run\n===ABS_END:storage_usage===');
equal(ignoredStorage.facts.storage.value.length, 0, '仅含忽略文件系统的已采集结果也应显式覆盖旧事实');

const twenty = parseProbeOutput(PROBE_20T_CANN80);
equal(twenty.board.tier, '20T', '310B1 应映射到 20T');
equal(twenty.facts.soc.value, 'Ascend310B1', '20T 板 SoC 应为 Ascend310B1');

const unified = parseProbeOutput(`${PROBE_20T_CANN80}
===ABS_BEGIN:ascend_env===
present=true
prefix=/home/HwHiAiUser/.conda/envs/ascend
executable=/home/HwHiAiUser/.conda/envs/ascend/bin/python
version=3.9.25
abi=cpython-39-aarch64-linux-gnu
isolated=false
policy_isolated=true
packages=mindspore:true,fastapi:true,sherpa_onnx:true
acl=ok
aclruntime=ok
mindspore_context=ok
===ABS_END:ascend_env===
`);
equal(unified.facts.ascendEnvPresent.value, true, '应识别统一 ascend 环境');
equal(unified.facts.ascendEnvVersion.value, '3.9.25', '应记录 ascend Python 版本');
equal(unified.facts.ascendEnvIsolated.value, false, '应记录裸解释器 user-site 状态');
equal(unified.facts.ascendEnvPolicyIsolated.value, true, '应记录启动策略 user-site 隔离');
equal(unified.facts.ascendEnvAclImport.value, 'ok', '应记录 ascend 内 PyACL 导入');
equal(unified.facts.ascendEnvAclruntime.value, 'ok', '应记录 ascend 内 aclruntime 导入');
equal(unified.facts.ascendEnvMindsporeContext.value, 'ok', '应记录 MindSpore Ascend context');

const atlas = parseProbeOutput(PROBE_ATLAS_CANN92);
equal(atlas.facts.firmwareVersion.value, '7.8.0.5.216', '应从 npu-smi board 读出固件');
equal(atlas.facts.driverVersion.value, '25.5.0', '应从 npu-smi board 读出驱动');
equal(atlas.facts.cannVersion.value, '9.2.0-beta.2', '应读出 CANN 9.2.0-beta.2');
const cannInfoOnly = parseProbeOutput(`${PROBE_8T_CANN80}
===ABS_BEGIN:cann_version_cfg===
NOT_FOUND
===ABS_END:cann_version_cfg===
===ABS_BEGIN:cann_version_info===
--- /usr/local/Ascend/ascend-toolkit/latest/compiler/version.info
Version=9.1.0
version_dir=cann
timestamp=20260730_231653901
===ABS_END:cann_version_info===
`);
equal(cannInfoOnly.facts.cannVersion.value, '9.1.0', 'CANN version.info must be a fallback when version.cfg is absent');
equal(cannInfoOnly.facts.cannVersion.source, 'cann_version_info', 'CANN fallback evidence source must identify compiler/version.info');

// --- honest gaps ----------------------------------------------------------
const empty = parseProbeOutput('===ABS_BEGIN:hostname===\nfoo\n===ABS_END:hostname===\n');
equal(empty.board, null, '没有设备行时 board 应为 null，而不是编造一个型号');
check(empty.missingCore.includes('soc'), '缺 SoC 时应列入 missingCore');
check(empty.missingCore.includes('cannVersion'), '缺 CANN 时应列入 missingCore');

const transcript = parseProbeOutput('npu-smi info\n| 0  310B4 | OK |\n| 0  0 | 0000:00:00.0 | 0  100 / 800 |\n');
equal(transcript.board?.soc, 'Ascend310B4', '无节标记的纯终端记录也应能识别设备行');

const audioRoutes = parseProbeOutput([
	'===ABS_BEGIN:audio_sources===',
	'2\tbluez_input.00_11_22_33_44_55\tPipeWire\ts16le 1ch 16000Hz\tSUSPENDED',
	'===ABS_END:audio_sources===',
	'===ABS_BEGIN:pulse_cards===',
	'1\tbluez_card.00_11_22_33_44_55\tmodule-bluez5-device.c',
	'===ABS_END:pulse_cards===',
	'===ABS_BEGIN:bluetooth_controller===',
	'Controller 00:11:22:33:44:55 bluez',
	'===ABS_END:bluetooth_controller===',
	'===ABS_BEGIN:bluetooth_devices===',
	'Device 00:11:22:33:44:55 Headset',
	'===ABS_END:bluetooth_devices===',
	'===ABS_BEGIN:bluetooth_info===',
	'Device 00:11:22:33:44:55 (public)',
	'\tConnected: yes',
	'===ABS_END:bluetooth_info==='
].join('\n')).facts;
check(audioRoutes.audioSources?.value.includes('bluez_input.'), '探测快照应保留蓝牙 Pulse source');
check(audioRoutes.pulseCards?.value.includes('bluez_card.'), '探测快照应保留蓝牙 Pulse card');
check(audioRoutes.bluetoothController?.value.includes('Controller 00:11:22:33:44:55'), '探测快照应保留蓝牙控制器');
check(audioRoutes.bluetoothDevices?.value.includes('Device 00:11:22:33:44:55'), '探测快照应保留已配对蓝牙设备');
check(audioRoutes.bluetoothInfo?.value.includes('Connected: yes'), '探测快照应保留蓝牙连接状态');

const videoInfo = parseProbeOutput([
	'===ABS_BEGIN:video_details===',
	'DEVICE=/dev/video0',
	'Driver name      : uvcvideo',
	'Card type        : USB Composite Device: USB Camera',
	'Device Caps      : 0x04200001',
	'\tVideo Capture',
	'DEVICE=/dev/video1',
	'Driver name      : uvcvideo',
	'Card type        : USB Composite Device: USB Camera',
	'Device Caps      : 0x04a00000',
	'\tMetadata Capture',
	'===ABS_END:video_details==='
].join('\n')).facts;
check(videoInfo.videoDetails?.value.includes('Card type'), '探测快照应保留每个 V4L2 节点的设备名称');
check(videoInfo.videoDetails?.value.includes('Metadata Capture'), '探测快照应保留 V4L2 节点能力');

const videoFormats = parseProbeOutput([
	'===ABS_BEGIN:video_formats===',
	'DEVICE=/dev/video0',
	"	[0]: 'MJPG' (Motion-JPEG, compressed)",
	'		Size: Discrete 1280x720',
	'			Interval: Discrete 0.033s (30.000 fps)',
	'===ABS_END:video_formats==='
].join('\n')).facts;
check(videoFormats.videoFormats?.value.includes("'MJPG'"), '探测快照应保留实际 FourCC');
check(videoFormats.videoFormats?.value.includes('1280x720'), '探测快照应保留实际支持分辨率');
check(videoFormats.videoFormats?.value.includes('30.000 fps'), '探测快照应保留实际支持帧率');

// --- generated script -----------------------------------------------------
const script = buildProbeScript();
for (const section of ['npu_smi_info', 'cann_version_cfg', 'driver_version_info', 'acl_import', 'ascend_env', 'devices', 'sound_cards', 'audio_playback', 'audio_capture', 'audio_sinks', 'audio_sources', 'pulse_cards', 'pulse_card_details', 'bluetooth_controller', 'bluetooth_devices', 'bluetooth_info', 'usb_devices', 'video_devices', 'video_details', 'video_formats', 'mac', 'machine_id', 'ip_address', 'primary_ip']) {
	check(script.includes(`abs_section ${section}`), `探测脚本应包含 ${section} 节`);
}
check(script.includes('.conda/envs/ascend'), '探测脚本应检查统一 ascend 环境');
check(script.includes('PYTHONNOUSERSITE=1'), 'ascend 环境检查必须禁用 user-site');
check(!script.includes('abs_section firmware_version_info'), '默认探测不应请求不可读的固件版本文件');
check(!script.includes('abs_section firmware_access'), '默认探测不应把固件文件权限作为版本探测项');
check(!/(^|\s)(rm|mv|dd|mkfs)(\s|$)/.test(script), '探测脚本不应包含写操作');
check(script.includes('read-only environment probe'), '脚本应自述为只读探测');
check(script.includes('NO_PAIRED_DEVICES'), '没有已配对蓝牙设备时应与采集失败区分');
const cameraScript = buildCameraProbeScript();
for (const section of ['video_devices', 'video_details', 'video_formats', 'usb_devices']) {
	check(cameraScript.includes(`abs_section ${section}`), `摄像头专用探测应包含 ${section}`);
}
check(!cameraScript.includes('npu_smi_info'), '摄像头专用探测不应重复执行整板环境探测');
check(cameraScript.includes('timeout 5 v4l2-ctl'), '摄像头格式命令必须有板端超时保护');
const audioScript = buildAudioProbeScript();
check(audioScript.includes('abs_section audio_sources'), '音频专用刷新应包含 Pulse source');
check(audioScript.includes('abs_section bluetooth_info'), '音频专用刷新应包含蓝牙连接详情');
check(!audioScript.includes('abs_section npu_smi_info'), '音频专用刷新不应执行 NPU 探测');
check(!audioScript.includes('abs_section video_formats'), '音频专用刷新不应枚举摄像头格式');
const systemScript = buildSystemProbeScript();
check(systemScript.includes('abs_section npu_smi_info'), '系统刷新应包含 NPU 信息');
check(systemScript.includes('abs_section cann_version_cfg'), '系统刷新应包含 CANN 版本');
for (const section of ['storage_devices', 'storage_usage', 'storage_mmc']) {
	check(systemScript.includes(`abs_section ${section}`), `系统刷新必须采集 ${section}`);
}
check(systemScript.includes('RM,ROTA,MODEL'), '系统存储采集应读取 ROTA 以区分 SATA 介质');
check(script.includes('RM,ROTA,MODEL'), '完整存储采集应读取 ROTA 以区分 SATA 介质');
check(!systemScript.includes('abs_section ascend_env'), '系统刷新不应执行耗时的 Python/conda 环境导入');
check(!systemScript.includes('abs_section audio_sources'), '系统刷新不应枚举音频');
check(!systemScript.includes('abs_section video_formats'), '系统刷新不应枚举摄像头');

if (failures.length > 0) {
	console.error('parse tests FAILED:');
	for (const failure of failures) console.error(`  - ${failure}`);
	process.exit(1);
}
console.log('parse tests passed');
