/**
 * Shared fixtures for the Ascend 310B debug plugin tests.
 *
 * The device snippets are copied from real `npu-smi` layouts recorded in the
 * repository (chapter 5 / appendix 1) so the parser is tested against the
 * two-line table it will actually receive, not an idealized one-liner.
 */

export const NPU_SMI_310B4 = [
	'+--------------------------------------------------------------------------------------------+',
	'| npu-smi 25.2.0                       Version: 25.2.0                                        |',
	'+---------------------------+---------------+------------------------------------------------+',
	'| NPU     Name              | Health        | Power(W)     Temp(C)           Hugepages-Usage(page) |',
	'| Chip    Device            | Bus-Id        | AICore(%)    Memory-Usage(MB)                          |',
	'+===========================+===============+================================================+',
	'| 0       310B4             | OK            | 12.8         45                0    / 0              |',
	'| 0       0                 | 0000:00:00.0  | 0            2433 / 7564                            |',
	'+===========================+===============+================================================+'
].join('\n');

export const NPU_SMI_310B1 = [
	'| npu-smi 25.2.0                       Version: 25.2.0                                        |',
	'| 0       Ascend310B1       | Alarm         | 14.1         52                0    / 0              |',
	'| 0       0                 | 0000:00:00.0  | 3            1914 / 23673                           |'
].join('\n');

/** A full probe transcript for an Orange Pi AI Pro 8T running CANN 8.0.0. */
export const PROBE_8T_CANN80 = [
	'===ABS_BEGIN:uname===',
	'Linux orangepiaipro 5.10.0+ #1 SMP PREEMPT aarch64 GNU/Linux',
	'===ABS_END:uname===',
	'===ABS_BEGIN:arch===',
	'aarch64',
	'===ABS_END:arch===',
	'===ABS_BEGIN:kernel===',
	'5.10.0+',
	'===ABS_END:kernel===',
	'===ABS_BEGIN:os_release===',
	'PRETTY_NAME="Ubuntu 22.04.5 LTS"',
	'NAME="Ubuntu"',
	'VERSION="22.04.5 LTS (Jammy Jellyfish)"',
	'===ABS_END:os_release===',
	'===ABS_BEGIN:hostname===',
	'orangepiaipro',
	'===ABS_END:hostname===',
	'===ABS_BEGIN:date===',
	'2026-10-17T09:40:00+08:00',
	'===ABS_END:date===',
	'===ABS_BEGIN:npu_smi_path===',
	'/usr/local/sbin/npu-smi',
	'===ABS_END:npu_smi_path===',
	'===ABS_BEGIN:npu_smi_version===',
	'npu-smi 25.2.0',
	'===ABS_END:npu_smi_version===',
	'===ABS_BEGIN:npu_smi_info===',
	NPU_SMI_310B4,
	'===ABS_END:npu_smi_info===',
	'===ABS_BEGIN:ascend_toolkit===',
	'/usr/local/Ascend/ascend-toolkit/8.0.0',
	'===ABS_END:ascend_toolkit===',
	'===ABS_BEGIN:ascend_driver===',
	'/usr/local/Ascend/driver',
	'===ABS_END:ascend_driver===',
	'===ABS_BEGIN:cann_version_cfg===',
	'version=[7.6.0.1.220]',
	'runtime_running_version=[7.6.0.1.220:8.0.0]',
	'===ABS_END:cann_version_cfg===',
	'===ABS_BEGIN:driver_version_info===',
	'--- /var/davinci/driver/version.info',
	'Version=25.2.0',
	'===ABS_END:driver_version_info===',
	'===ABS_BEGIN:firmware_version_info===',
	'===ABS_END:firmware_version_info===',
	'===ABS_BEGIN:env===',
	'ASCEND_HOME_PATH=/usr/local/Ascend/ascend-toolkit/latest',
	'LD_LIBRARY_PATH=/usr/local/Ascend/ascend-toolkit/latest/lib64',
	'===ABS_END:env===',
	'===ABS_BEGIN:set_env===',
	'PRESENT /usr/local/Ascend/ascend-toolkit/set_env.sh',
	'===ABS_END:set_env===',
	'===ABS_BEGIN:atc_version===',
	'ATC (Ascend Tensor Compiler) 8.0.0',
	'===ABS_END:atc_version===',
	'===ABS_BEGIN:python===',
	'executable=/usr/local/miniconda3/bin/python',
	'version=3.9.2',
	'prefix=/usr/local/miniconda3',
	'===ABS_END:python===',
	'===ABS_BEGIN:acl_import===',
	'PyACL import: ok',
	'===ABS_END:acl_import===',
	'===ABS_BEGIN:conda===',
	'CONDA_PREFIX=/usr/local/miniconda3',
	'CONDA_DEFAULT_ENV=base',
	'===ABS_END:conda===',
	'===ABS_BEGIN:devices===',
	'crw-rw-rw- 1 root root 240, 0 Oct 17 09:00 /dev/davinci0',
	'===ABS_END:devices===',
	'===ABS_BEGIN:modules===',
	'drv_davinci            1024  2',
	'===ABS_END:modules==='
].join('\n');

/** A probe transcript for an Ascend 310B1 / 20T board. */
export const PROBE_20T_CANN80 = [
	'===ABS_BEGIN:kernel===',
	'5.10.0+',
	'===ABS_END:kernel===',
	'===ABS_BEGIN:hostname===',
	'orangepiaipro-20t',
	'===ABS_END:hostname===',
	'===ABS_BEGIN:npu_smi_info===',
	NPU_SMI_310B1,
	'===ABS_END:npu_smi_info===',
	'===ABS_BEGIN:cann_version_cfg===',
	'version=[7.6.0.1.220]',
	'runtime_running_version=[7.6.0.1.220:8.0.0]',
	'===ABS_END:cann_version_cfg===',
	'===ABS_BEGIN:driver_version_info===',
	'Version=25.2.0',
	'===ABS_END:driver_version_info===',
	'===ABS_BEGIN:acl_import===',
	'PyACL import: ok',
	'===ABS_END:acl_import==='
].join('\n');

/** A probe transcript for the official Atlas board after the 9.2 upgrade. */
export const PROBE_ATLAS_CANN92 = [
	'===ABS_BEGIN:kernel===',
	'6.6.0-72.0.0.76.h914.eulerosv2r15.ascend.aarch64',
	'===ABS_END:kernel===',
	'===ABS_BEGIN:npu_smi_info===',
	'| 0       Ascend 310B4       | OK            | 13.0         43                0    / 0              |',
	'| 0       0                  | 0000:00:00.0  | 0            900 / 15610                           |',
	'===ABS_END:npu_smi_info===',
	'===ABS_BEGIN:npu_smi_board===',
	'Firmware Version : 7.8.0.5.216',
	'Driver Version   : 25.5.0',
	'===ABS_END:npu_smi_board===',
	'===ABS_BEGIN:cann_version_cfg===',
	'version=[9.2.0-beta.2]',
	'runtime_running_version=[9.2.0-beta.2]',
	'===ABS_END:cann_version_cfg===',
	'===ABS_BEGIN:acl_import===',
	'PyACL import: ok',
	'===ABS_END:acl_import==='
].join('\n');
