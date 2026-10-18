/**
 * Board environment probing for Ascend 310B.
 *
 * The probe is split in two so both halves are testable without a board:
 *
 * 1. `buildProbeScript()` emits a read-only shell script. Every command is a
 *    query (`npu-smi`, version files, `uname`, `python -c`) — it writes nothing,
 *    installs nothing, and needs no elevated privilege beyond reading the
 *    driver version file. This is the same command set the repository already
 *    uses in `collect_system_status.sh` and appendix 7.
 * 2. `parseProbeOutput()` turns that script's section-marked stdout into
 *    facts. The same parser consumes a saved file, so a user who has already
 *    collected a board snapshot never needs to run anything again.
 *
 * Every fact carries the section it came from, so a report can always be
 * traced back to raw board output.
 *
 * @module dsh-plugin-ascend310b-debug/probe
 */

import { TIERS, assessBoardScope } from './compat.js';

/** Prefix marking a section of the probe output. */
export const SECTION_PREFIX = 'ABS';

/**
 * The read-only probe script.
 *
 * Section markers are chosen so that a shell script, a saved text file, and a
 * pasted terminal transcript all parse identically. `npu-smi` location is
 * discovered through several candidate paths because a 310B image does not
 * always put it on the non-interactive `PATH`.
 */
export const PROBE_SCRIPT = `#!/bin/bash
# Ascend 310B read-only environment probe (no writes, no installs).
abs_section() { printf '===ABS_BEGIN:%s===\\n' "$1"; }
abs_end() { printf '===ABS_END:%s===\\n' "$1"; }
abs_run() { name="$1"; shift; abs_section "$name"; "$@" 2>&1 || true; abs_end "$name"; }

# CANN cannot be sourced inside a subshell with "set -e": the vendor script is
# not written for that and would abort the probe before the sourced checks run.
CANN_SET_ENV=""
for candidate in /usr/local/Ascend/ascend-toolkit/set_env.sh /usr/local/Ascend/ascend-toolkit/latest/set_env.sh /usr/local/Ascend/ascend-toolkit/8.0.0/set_env.sh "$HOME/Ascend/ascend-toolkit/set_env.sh"; do
  if [ -r "$candidate" ]; then CANN_SET_ENV="$candidate"; break; fi
done
CONDA_SH=""
for candidate in /usr/local/miniconda3/etc/profile.d/conda.sh /opt/miniconda3/etc/profile.d/conda.sh "$HOME/miniconda3/etc/profile.d/conda.sh" "$HOME/anaconda3/etc/profile.d/conda.sh"; do
  if [ -r "$candidate" ]; then CONDA_SH="$candidate"; break; fi
done
abs_source_cann() {
  if [ -z "$CANN_SET_ENV" ]; then echo "set_env.sh 未找到"; return 1; fi
  if [ -n "$CONDA_SH" ]; then . "$CONDA_SH" >/dev/null 2>&1 || true; conda activate base >/dev/null 2>&1 || true; fi
  # shellcheck disable=SC1090
  . "$CANN_SET_ENV" >/dev/null 2>&1 || true
  return 0
}
abs_find_atc() {
  command -v atc 2>/dev/null || true
  for candidate in /usr/local/Ascend/ascend-toolkit/latest/bin/atc /usr/local/Ascend/ascend-toolkit/8.0.0/aarch64-linux/bin/atc; do
    if [ -x "$candidate" ]; then printf '%s\\n' "$candidate"; return 0; fi
  done
  return 0
}
abs_atc_version() {
  ATC_BIN="$(abs_find_atc | head -n 1)"
  if [ -z "$ATC_BIN" ]; then echo "atc not on PATH (CANN set_env.sh 尚未 source)"; return 0; fi
  # "atc --version" refuses the flag on some CANN builds and prints a usage error;
  # in that case the executable is still evidence that ATC is installed.
  echo "path=$ATC_BIN"
  "$ATC_BIN" --version 2>&1 | head -n 5 || true
}

abs_section uname; uname -a 2>&1 || true; abs_end uname
abs_section arch; uname -m 2>&1 || true; abs_end arch
abs_section kernel; uname -r 2>&1 || true; abs_end kernel
abs_section os_release; test -r /etc/os-release && cat /etc/os-release || echo "MISSING /etc/os-release"; abs_end os_release
abs_section hostname; hostname 2>&1 || true; abs_end hostname
abs_section ip_address; hostname -I 2>/dev/null || true; abs_end ip_address
abs_section date; date --iso-8601=seconds 2>&1 || true; abs_end date
abs_section id; id 2>&1 || true; abs_end id

NPU_SMI="$(command -v npu-smi 2>/dev/null || true)"
if [ -z "$NPU_SMI" ]; then
  for candidate in /usr/local/sbin/npu-smi /usr/local/bin/npu-smi /usr/sbin/npu-smi /usr/bin/npu-smi /usr/local/Ascend/driver/tools/npu-smi; do
    if [ -x "$candidate" ]; then NPU_SMI="$candidate"; break; fi
  done
fi
abs_section npu_smi_path; if [ -n "$NPU_SMI" ]; then printf '%s\\n' "$NPU_SMI"; else echo "NOT_FOUND"; fi; abs_end npu_smi_path
abs_section npu_smi_version; if [ -n "$NPU_SMI" ]; then "$NPU_SMI" -v 2>&1 || true; else echo "NOT_FOUND"; fi; abs_end npu_smi_version
abs_section npu_smi_info; if [ -n "$NPU_SMI" ]; then "$NPU_SMI" info 2>&1 || true; else echo "NOT_FOUND"; fi; abs_end npu_smi_info
abs_section npu_smi_board; if [ -n "$NPU_SMI" ]; then "$NPU_SMI" info -t board -i 0 2>&1 || true; else echo "NOT_FOUND"; fi; abs_end npu_smi_board

abs_section os_sp; sed -n 's/^[[:space:]]*Version:[[:space:]]*//p' /proc/version 2>/dev/null || true; abs_end os_sp
abs_section dt_model; sed -n 's/^[[:space:]]*Model[[:space:]]*:[[:space:]]*//p; s/^[[:space:]]*Hardware[[:space:]]*:[[:space:]]*//p' /proc/device-tree/model 2>/dev/null || true; cat /proc/device-tree/model 2>/dev/null || true; abs_end dt_model

abs_section ascend_toolkit; readlink -f /usr/local/Ascend/ascend-toolkit/latest 2>/dev/null || echo "UNRESOLVED /usr/local/Ascend/ascend-toolkit/latest"; abs_end ascend_toolkit
abs_section ascend_driver; readlink -f /usr/local/Ascend/driver 2>/dev/null || echo "UNRESOLVED /usr/local/Ascend/driver"; abs_end ascend_driver
abs_section ascend_firmware; readlink -f /usr/local/Ascend/firmware 2>/dev/null || echo "UNRESOLVED /usr/local/Ascend/firmware"; abs_end ascend_firmware

abs_section cann_version_cfg; cat /usr/local/Ascend/ascend-toolkit/latest/version.cfg 2>/dev/null || cat /usr/local/Ascend/ascend-toolkit/version.cfg 2>/dev/null || echo "NOT_FOUND"; abs_end cann_version_cfg
abs_section cann_version_info; for f in /usr/local/Ascend/ascend-toolkit/latest/compiler/version.info /usr/local/Ascend/ascend-toolkit/latest/version.info; do if [ -r "$f" ]; then echo "--- $f"; sed -n '1,40p' "$f"; fi; done 2>/dev/null; abs_end cann_version_info
abs_section driver_version_info; for f in /var/davinci/driver/version.info /usr/local/Ascend/driver/version.info /usr/local/Ascend/driver/version.cfg /etc/ascend_install.info; do if [ -r "$f" ]; then echo "--- $f"; sed -n '1,40p' "$f"; fi; done 2>/dev/null; abs_end driver_version_info
abs_section firmware_version_info; for f in /usr/local/Ascend/firmware/version.info /usr/local/Ascend/firmware/version.cfg; do if [ -r "$f" ]; then echo "--- $f"; sed -n '1,40p' "$f"; fi; done 2>/dev/null; abs_end firmware_version_info

abs_section env; for v in ASCEND_HOME_PATH ASCEND_TOOLKIT_HOME ASCEND_AICPU_PATH ASCEND_OPP_PATH LD_LIBRARY_PATH PYTHONNOUSERSITE; do printf '%s=' "$v"; printenv "$v" 2>/dev/null || true; done; abs_end env
abs_section set_env; for f in /usr/local/Ascend/ascend-toolkit/set_env.sh /usr/local/Ascend/ascend-toolkit/latest/set_env.sh; do if [ -r "$f" ]; then echo "PRESENT $f"; fi; done; abs_end set_env
abs_section atc_version; abs_atc_version; abs_end atc_version

# A non-interactive SSH shell has not sourced CANN, so "import acl" and "atc"
# fail there for reasons that have nothing to do with the board. The same two
# checks therefore run again after sourcing set_env.sh + conda, and the report
# keeps both results so the difference stays visible.
abs_section atc_version_sourced
abs_source_cann && abs_atc_version
abs_end atc_version_sourced
abs_section acl_import_sourced
if abs_source_cann; then
  for p in python3 python; do
    if command -v "$p" >/dev/null 2>&1; then
      echo "executable=$("$p" -c 'import sys; print(sys.executable)' 2>&1)"
      "$p" -c "import acl; print('PyACL import: ok')" 2>&1 || true
      break
    fi
  done
fi
abs_end acl_import_sourced

abs_section python; for p in python3 python; do if command -v "$p" >/dev/null 2>&1; then "$p" -c "import sys; print('executable=' + sys.executable); print('version=' + sys.version.split()[0]); print('prefix=' + sys.prefix)" 2>&1; break; fi; done; abs_end python
abs_section acl_import; for p in python3 python; do if command -v "$p" >/dev/null 2>&1; then "$p" -c "import acl; print('PyACL import: ok')" 2>&1; break; fi; done 2>&1 || true; abs_end acl_import
abs_section conda; echo "CONDA_PREFIX=\${CONDA_PREFIX-}"; echo "CONDA_DEFAULT_ENV=\${CONDA_DEFAULT_ENV-}"; abs_end conda

abs_section devices; ls -l /dev/davinci* /dev/devmm* /dev/hisi_hdc 2>/dev/null || echo "NOT_FOUND /dev/davinci*"; abs_end devices
abs_section mac; for p in /sys/class/net/*; do iface=$(basename "$p"); [ "$iface" = lo ] && continue; if [ -r "$p/address" ]; then printf '%s=' "$iface"; cat "$p/address" 2>/dev/null || true; fi; done; abs_end mac
abs_section machine_id; cat /etc/machine-id 2>/dev/null || echo "NOT_FOUND"; abs_end machine_id
abs_section modules; lsmod 2>/dev/null | grep -E 'drv|ascend|hisi' || echo "NO_MATCH_OR_PERMISSION"; abs_end modules
abs_section health; if [ -n "$NPU_SMI" ]; then "$NPU_SMI" info 2>/dev/null | sed -n '1,12p'; else echo "NOT_FOUND"; fi; abs_end health
`;

/**
 * Return the read-only board probe script.
 *
 * The script has no capture or output-file branch on purpose: every command it
 * runs only reads board state, and the caller captures stdout over SSH.
 *
 * @returns {string} shell script text.
 */
export function buildProbeScript() {
	return PROBE_SCRIPT;
}

/**
 * Parse section-marked probe output into a raw section map.
 *
 * @param {string} text - probe stdout, a saved snapshot, or a transcript.
 * @returns {object} map of section name to unparsed text.
 */
export function parseSections(text) {
	const sections = {};
	if (typeof text !== 'string') return sections;
	const pattern = new RegExp(`===${SECTION_PREFIX}_BEGIN:([A-Za-z0-9_]+)===([\\s\\S]*?)===${SECTION_PREFIX}_END:\\1===`, 'g');
	let match;
	while ((match = pattern.exec(text)) !== null) {
		sections[match[1]] = match[2].replace(/^\r?\n/, '').replace(/\r?\n[ \t]*$/, '');
	}
	// A plain transcript without markers still yields the two most useful
	// sections, so pasting a terminal session is a supported input.
	if (Object.keys(sections).length === 0 && text.trim() !== '') {
		sections.__raw__ = text.trim();
		if (/npu-smi|310B[14]/i.test(text)) sections.npu_smi_info = text.trim();
	}
	return sections;
}

/** Read one `key=value` line out of a section. */
function envValue(sections, name, key) {
	const text = sections[name];
	if (typeof text !== 'string') return null;
	const match = new RegExp(`^${key}=(.*)$`, 'm').exec(text);
	const value = match?.[1]?.trim() ?? '';
	return value === '' ? null : value;
}

/** Parse `/etc/os-release` into a small object. */
export function parseOsRelease(text) {
	const result = {};
	if (typeof text !== 'string') return result;
	for (const line of text.split(/\r?\n/)) {
		const match = /^([A-Z_]+)=(.*)$/.exec(line.trim());
		if (match === null) continue;
		result[match[1]] = match[2].replace(/^"(.*)"$/, '$1');
	}
	return result;
}

/**
 * Parse the Ascend 310B device rows out of `npu-smi info`.
 *
 * The tool prints device facts on two lines: the first carries the chip name,
 * health, power and temperature, and the continuation line carries the
 * memory usage (and the AICore percentage). The column layout also differs
 * between driver versions, and some boards print only `310B4`. Both the
 * two-line pairing and a single-line layout are handled.
 *
 * @param {string} text - `npu-smi info` output.
 * @returns {object[]} one entry per device row.
 */
export function parseNpuSmiInfo(text) {
	const devices = [];
	if (typeof text !== 'string') return devices;
	const seen = new Set();
	const lines = text.split(/\r?\n/);
	let pending = null;
	for (const line of lines) {
		const chipMatch = /\b(?:Ascend)?(310B[0-9A-Za-z]*)\b/.exec(line);
		if (chipMatch === null) {
			if (pending === null) continue;
			// Memory usage is printed on the row after the device row. A `0 / 0`
			// pair on the device row itself is the hugepages counter, not memory.
			const memory = /(\d+)\s*\/\s*(\d+)/.exec(line);
			if (memory === null) continue;
			pending.memoryUsedMb = Number(memory[1]);
			pending.memoryTotalMb = Number(memory[2]);
			pending.rawLine = `${pending.rawLine} || ${line.trim()}`;
			pending = null;
			continue;
		}
		const chip = chipMatch[1];
		pending = null;
		if (seen.has(chip)) continue;
		seen.add(chip);
		const healthMatch = /\b(OK|Alarm|Warning|Critical)\b/.exec(line);
		// The 310B `npu-smi info` device table prints bare numbers in fixed
		// columns (`| 0  310B4 | OK | 12.8  45  0 / 0 |`), so unit suffixes
		// cannot be relied on. Power and temperature are the two single-decimal
		// numbers in the metrics column; driver versions there carry three
		// segments and are therefore not mistaken for measurements.
		const metricsCell = line
			.split('|')
			.map((cell) => cell.trim())
			.filter((cell) => cell !== '')[2];
		const measurements = (metricsCell ?? '')
			.match(/\d+(?:\.\d+)?(?![\d./])/g)
			?.map((value) => Number(value))
			.filter((value) => Number.isFinite(value));
		const tempMatch =
			/\b(\d{1,3}(?:\.\d+)?)\s*C\b/.exec(line) ??
			(measurements !== undefined && measurements.length >= 2 ? [`${measurements[1]}`, `${measurements[1]}`] : null);
		const powerMatch =
			/\b(\d+(?:\.\d+)?)\s*W\b/.exec(line) ??
			(measurements !== undefined && measurements.length >= 1 ? [`${measurements[0]}`, `${measurements[0]}`] : null);
		const indexMatch = /^\s*(\d+)\s*\|/.exec(line) ?? /^\|\s*(\d+)\s/.exec(line);
		const socVersion = chip.startsWith('Ascend') ? chip : `Ascend${chip}`;
		const memory = /(\d+)\s*\/\s*(\d+)/.exec(line);
		const device = {
			chip,
			socVersion,
			index: indexMatch === null ? devices.length : Number(indexMatch[1]),
			health: healthMatch === null ? null : healthMatch[1],
			temperatureC: tempMatch === null ? null : Number(tempMatch[1]),
			powerW: powerMatch === null ? null : Number(powerMatch[1]),
			memoryUsedMb: memory === null || `${memory[1]} / ${memory[2]}` === '0 / 0' ? null : Number(memory[1]),
			memoryTotalMb: memory === null || `${memory[1]} / ${memory[2]}` === '0 / 0' ? null : Number(memory[2]),
			rawLine: line.trim()
		};
		devices.push(device);
		pending = device;
	}
	return devices;
}

/** First non-empty value matching a regular expression across sections. */
function findVersion(sections, names, pattern) {
	for (const name of names) {
		const text = sections[name];
		if (typeof text !== 'string') continue;
		const match = pattern.exec(text);
		if (match !== null) {
			const value = (match[1] ?? match[0]).trim();
			if (value !== '') return { value, section: name, evidence: match[0].trim() };
		}
	}
	return null;
}

/** Extract the first non-loopback IPv4 address from `hostname -I`. */
export function parseIpAddress(text) {
	if (typeof text !== 'string') return null;
	const addresses = text.match(/\b(?:\d{1,3}\.){3}\d{1,3}\b/g) ?? [];
	return addresses.find((address) => !address.startsWith('127.') && !address.startsWith('169.254.')) ?? addresses[0] ?? null;
}

/** Extract the physical MAC address, preferring eth0 over other interfaces. */
export function parseMacSection(text) {
	if (typeof text !== 'string') return null;
	const found = [];
	for (const rawLine of text.split(/\r?\n/)) {
		const match = /^([A-Za-z0-9_.:-]+)=([0-9a-f]{2}(?::[0-9a-f]{2}){5})$/i.exec(rawLine.trim());
		if (match !== null) found.push({ iface: match[1], mac: match[2].toLowerCase() });
	}
	return (found.find((item) => item.iface === 'eth0') ?? found[0])?.mac ?? null;
}

/** Read `key=value` from the CANN `version.cfg` content. */
export function parseVersionCfg(text) {
	const result = {};
	if (typeof text !== 'string') return result;
	for (const line of text.split(/\r?\n/)) {
		const match = /^([A-Za-z0-9_]+)\s*=\s*\[(.*)\]$/.exec(line.trim());
		if (match === null) continue;
		result[match[1]] = match[2].trim();
	}
	return result;
}

/** Extract `Version=...` style values from a driver/firmware version file. */
export function parseInfoFile(text) {
	const result = {};
	if (typeof text !== 'string') return result;
	for (const rawLine of text.split(/\r?\n/)) {
		const line = rawLine.replace(/^---\s*/, '').trim();
		const match = /^([A-Za-z][A-Za-z0-9 _./-]*?)\s*[:=]\s*(.+)$/.exec(line);
		if (match === null) continue;
		const key = match[1].trim().replace(/\s+/g, '');
		const value = match[2].trim();
		// `npu-smi` prints NA for an unpopulated field; that is "not stated",
		// which must stay unknown rather than become the string "NA".
		if (value !== '' && /^(?:N\/?A|UNKNOWN|NOT_FOUND)$/i.test(value) === false) result[key] = value;
	}
	return result;
}

/**
 * Assemble a facts report from probe sections.
 *
 * A fact is omitted entirely when it was not collected. That is the point: the
 * report distinguishes "this board says X" from "nobody looked".
 *
 * @param {object} sections - section map from {@link parseSections}.
 * @returns {object} facts plus the derived board/tier summary.
 */
export function buildEnvironmentReport(sections) {
	const facts = {};
	const set = (key, value, source, evidence, evidenceLabel = 'board-output') => {
		if (value === null || value === undefined || value === '') return;
		facts[key] = { value, source, evidence, evidenceLabel };
	};

	const os = parseOsRelease(sections.os_release);
	set(
		'os',
		[os.PRETTY_NAME ?? os.NAME, os.VERSION].filter(Boolean).join(' ').trim() || null,
		'os_release',
		os.PRETTY_NAME ?? null
	);
	set('osVersion', os.VERSION ?? null, 'os_release', os.VERSION ?? null);
	set('kernel', cleanLine(sections.kernel), 'kernel', cleanLine(sections.kernel));
	set('arch', cleanLine(sections.arch), 'arch', cleanLine(sections.arch));
	set('hostname', cleanLine(sections.hostname), 'hostname', cleanLine(sections.hostname));
	set('ipAddress', parseIpAddress(sections.ip_address), 'ip_address', cleanLine(sections.ip_address));
	set('probeTime', cleanLine(sections.date), 'date', cleanLine(sections.date));
	set('loginUser', cleanLine(sections.id), 'id', cleanLine(sections.id));
	set('boardModel', cleanLine(sections.dt_model)?.replace(/\u0000/g, '').trim() || null, 'dt_model', cleanLine(sections.dt_model));
	set('macAddress', parseMacSection(sections.mac), 'mac', cleanLine(sections.mac));
	set(
		'machineId',
		cleanLine(sections.machine_id) === 'NOT_FOUND' ? null : cleanLine(sections.machine_id),
		'machine_id',
		cleanLine(sections.machine_id)
	);

	const devices = parseNpuSmiInfo(sections.npu_smi_info ?? sections.__raw__ ?? '');
	const device = devices[0] ?? null;
	const otherDevice = device === null ? detectAnyDevice(sections.npu_smi_info ?? sections.__raw__ ?? '') : null;
	if (device !== null) {
		set('soc', device.socVersion, 'npu_smi_info', device.rawLine);
		set('chipName', device.chip, 'npu_smi_info', device.rawLine);
		set('health', device.health, 'npu_smi_info', device.rawLine);
		if (device.temperatureC !== null) set('temperatureC', device.temperatureC, 'npu_smi_info', device.rawLine);
		if (device.powerW !== null) set('powerW', device.powerW, 'npu_smi_info', device.rawLine);
		if (device.memoryTotalMb !== null) {
			set('memoryUsedMb', device.memoryUsedMb, 'npu_smi_info', device.rawLine);
			set('memoryTotalMb', device.memoryTotalMb, 'npu_smi_info', device.rawLine);
		}
		facts.socTier = {
			value: TIERS[device.socVersion]?.tier ?? 'unknown',
			source: 'derived',
			evidence: `由 npu-smi 的芯片名 ${device.chip} 映射`,
			evidenceLabel: TIERS[device.socVersion] ? 'documented' : 'inferred'
		};
	} else if (otherDevice !== null) {
		// Recording the chip is what lets the scope check say "this is not a
		// 310B" instead of "nothing was detected".
		set('soc', otherDevice.socVersion, 'npu_smi_info', otherDevice.rawLine);
		set('chipName', otherDevice.chip, 'npu_smi_info', otherDevice.rawLine);
		facts.socTier = {
			value: 'unknown',
			source: 'derived',
			evidence: `${otherDevice.chip} 不在 310B1/310B4 映射内`,
			evidenceLabel: 'inferred'
		};
	}

	const npuSmiPath = cleanLine(sections.npu_smi_path);
	set('npuSmiPath', npuSmiPath === 'NOT_FOUND' ? null : npuSmiPath, 'npu_smi_path', npuSmiPath);
	set(
		'npuSmiVersion',
		findVersion(sections, ['npu_smi_version'], /(?:version|Ver\.?|npu-smi)\D{0,12}(\d+\.\d+(?:\.\d+)?)/i)?.value ?? null,
		'npu_smi_version',
		cleanLine(sections.npu_smi_version)
	);

	const cfg = parseVersionCfg(sections.cann_version_cfg);
	const toolkitVersion =
		cfg.runtime_running_version?.split(':').pop()?.trim() ??
		findVersion(sections, ['cann_version_info'], /runtime_running_version[^0-9]*(\d+\.\d+(?:\.\d+)?)/i)?.value ??
		null;
	set('cannVersion', toolkitVersion, 'cann_version_cfg', cfg.runtime_running_version ?? null);
	set('cannPackageVersion', cfg.version ?? null, 'cann_version_cfg', cfg.version ?? null);
	set(
		'cannLatestPath',
		cleanLine(sections.ascend_toolkit)?.startsWith('UNRESOLVED') === true ? null : cleanLine(sections.ascend_toolkit),
		'ascend_toolkit',
		cleanLine(sections.ascend_toolkit)
	);

	const info = parseInfoFile(sections.driver_version_info);
	const driverVersion =
		firstMatchingValue(info, ['Version', 'version', 'DriverVersion', 'DRIVER_VERSION', 'driverVersion']) ??
		findVersion(sections, ['npu_smi_board', 'npu_smi_info'], /Driver\s*Version\s*[:=]\s*([0-9][0-9A-Za-z.\-]*)/i)?.value ??
		null;
	set('driverVersion', driverVersion, 'driver_version_info', info.Version ?? null);

	const firmwareInfo = parseInfoFile(sections.firmware_version_info);
	const firmwareVersion =
		firstMatchingValue(firmwareInfo, ['FirmwareVersion', 'firmware', 'Firmware', 'Version']) ??
		findVersion(sections, ['npu_smi_board', 'npu_smi_info'], /Firmware\s*Version\s*[:=]\s*([0-9][0-9A-Za-z.\-]*)/i)?.value ??
		null;
	set('firmwareVersion', firmwareVersion, 'firmware_version_info', firmwareInfo.FirmwareVersion ?? null);

	set(
		'ascendDriverPath',
		cleanLine(sections.ascend_driver)?.startsWith('UNRESOLVED') === true ? null : cleanLine(sections.ascend_driver),
		'ascend_driver',
		cleanLine(sections.ascend_driver)
	);
	set(
		'ascendFirmwarePath',
		cleanLine(sections.ascend_firmware)?.startsWith('UNRESOLVED') === true ? null : cleanLine(sections.ascend_firmware),
		'ascend_firmware',
		cleanLine(sections.ascend_firmware)
	);

	set('ascendHomePath', envValue(sections, 'env', 'ASCEND_HOME_PATH'), 'env', envValue(sections, 'env', 'ASCEND_HOME_PATH'));
	set('toolkitHome', envValue(sections, 'env', 'ASCEND_TOOLKIT_HOME'), 'env', envValue(sections, 'env', 'ASCEND_TOOLKIT_HOME'));
	set('ldLibraryPathSet', envValue(sections, 'env', 'LD_LIBRARY_PATH') !== null, 'env', 'LD_LIBRARY_PATH');
	set('pythonNoUserSite', envValue(sections, 'env', 'PYTHONNOUSERSITE'), 'env', envValue(sections, 'env', 'PYTHONNOUSERSITE'));
	set('setEnvPresent', /PRESENT/.test(sections.set_env ?? ''), 'set_env', cleanLine(sections.set_env));
	set('condaPrefix', envValue(sections, 'conda', 'CONDA_PREFIX'), 'conda', envValue(sections, 'conda', 'CONDA_PREFIX'));
	set('condaEnv', envValue(sections, 'conda', 'CONDA_DEFAULT_ENV'), 'conda', envValue(sections, 'conda', 'CONDA_DEFAULT_ENV'));

	const pythonText = sections.python ?? '';
	set('pythonExecutable', envValue(sections, 'python', 'executable'), 'python', pythonText.split('\n')[0] ?? null);
	set('pythonVersion', envValue(sections, 'python', 'version'), 'python', pythonText.split('\n')[1] ?? null);
	const aclText = (sections.acl_import ?? '').trim();
	const atcText = cleanLine(sections.atc_version);
	set('atcVersion', atcText !== null && !/not on PATH/.test(atcText) ? atcText : null, 'atc_version', atcText);
	set('atcOnPath', atcText !== null && !/not on PATH/.test(atcText), 'atc_version', atcText);

	// A shell that has not sourced CANN is not evidence against the board, so the
	// sourced result decides the verdict and the raw shell result is kept beside
	// it. `aclImportBare` records the false negative instead of hiding it.
	const aclSourcedText = (sections.acl_import_sourced ?? '').trim();
	const aclSourcedOk = /PyACL import: ok|acl: ok/.test(aclSourcedText);
	const aclBareOk = /PyACL import: ok|acl: ok/.test(aclText);
	if (aclSourcedText !== '') {
		set(
			'aclImportSourced',
			aclSourcedOk ? 'ok' : 'failed',
			'acl_import_sourced',
			aclSourcedText,
			'board-output'
		);
		set('aclImportBare', aclBareOk ? 'ok' : 'failed', 'acl_import', aclText, 'board-output');
		facts.aclImport = {
			value: aclSourcedOk ? 'ok' : 'failed',
			source: 'acl_import_sourced',
			evidence: aclSourcedText,
			evidenceLabel: 'board-output'
		};
		if (!aclBareOk && aclSourcedOk) {
			facts.aclImportNote = {
				value: '裸 SSH 环境未 source CANN，source 后 import acl 成功：这是 shell 环境差异，不是板卡缺陷。',
				source: 'derived',
				evidence: 'shell-environment-only',
				evidenceLabel: 'inferred'
			};
		}
	} else {
		set('aclImport', aclBareOk ? 'ok' : 'failed', 'acl_import', aclText, 'board-output');
	}

	const atcSourcedText = cleanLine(sections.atc_version_sourced);
	if (atcSourcedText !== null && !/set_env\.sh 未找到/.test(atcSourcedText)) {
		const atcPath = /^path=(.+)$/m.exec(sections.atc_version_sourced ?? '')?.[1]?.trim() ?? null;
		const versionText = (sections.atc_version_sourced ?? '')
			.split(/\r?\n/)
			.map((line) => line.trim())
			.find((line) => line.startsWith('path=') === false && line !== '');
		// `atc --version` fails on some CANN builds; the resolved executable is
		// then the version-independent evidence that ATC is installed.
		set('atcVersion', versionText ?? atcPath, 'atc_version_sourced', atcPath ?? atcSourcedText);
		set('atcPath', atcPath, 'atc_version_sourced', atcPath);
		set('atcOnPath', atcPath !== null, 'atc_version_sourced', atcPath ?? atcSourcedText);
	} else if (aclSourcedText === '' && atcText !== null && /not on PATH/.test(atcText)) {
		set(
			'atcEnvironmentNote',
			{ value: 'atc 只在 source set_env.sh 后可见。', source: 'derived' },
			'atc_version',
			atcText
		);
	}

	set(
		'deviceNodes',
		/\/dev\/davinci/.test(sections.devices ?? '') ? 'present' : 'not-found',
		'devices',
		cleanLine(sections.devices)
	);
	set(
		'kernelModules',
		/NO_MATCH_OR_PERMISSION/.test(sections.modules ?? '') ? 'not-listed' : 'listed',
		'modules',
		cleanLine(sections.modules)
	);

	return {
		facts,
		devices,
		board: device === null ? null : {
			soc: device.socVersion,
			chip: device.chip,
			tier: TIERS[device.socVersion]?.tier ?? 'unknown',
			tierEvidence: TIERS[device.socVersion] === undefined ? 'inferred' : 'documented',
			health: device.health,
			temperatureC: device.temperatureC,
			powerW: device.powerW,
			memoryUsedMb: device.memoryUsedMb,
			memoryTotalMb: device.memoryTotalMb
		},
		scope: assessBoardScope({
			facts,
			board: device !== null ? { chip: device.chip } : otherDevice === null ? null : { chip: otherDevice.chip }
		}),
		sections,
		missingCore: ['soc', 'cannVersion', 'driverVersion'].filter((key) => facts[key] === undefined)
	};
}

/**
 * Detect a device row for any accelerator, not just 310B.
 *
 * `parseNpuSmiInfo` deliberately only accepts `310B*` rows because its fields
 * feed 310B-specific advice. The scope check still needs to see what chip is
 * really installed, so this reader accepts any chip token in the first column
 * of a healthy `npu-smi info` row and normalizes the common Ascend spelling.
 *
 * @param {string} text - `npu-smi info` output.
 * @returns {object|null} first device row with `chip`, `socVersion` and `rawLine`.
 */
export function detectAnyDevice(text) {
	if (typeof text !== 'string') return null;
	for (const line of text.split(/\r?\n/)) {
		if (!/\b(?:OK|Alarm|Warning|Critical)\b/.test(line)) continue;
		const cells = line
			.split('|')
			.map((cell) => cell.trim())
			.filter((cell) => cell !== '');
		const head = cells[0] ?? '';
		const match = /^\d+\s+([A-Za-z0-9][A-Za-z0-9._-]*)$/.exec(head);
		if (match === null) continue;
		const chip = match[1];
		// The 310B rows are parsed with their metrics elsewhere.
		if (/^310B/i.test(chip)) continue;
		const socVersion = /^Ascend/i.test(chip)
			? chip
			: /^\d{3}[A-Za-z]\d?$/.test(chip) || /^\d{3}[A-Za-z]/.test(chip)
				? `Ascend${chip}`
				: chip;
		return { chip, socVersion, rawLine: line.trim() };
	}
	return null;
}

/** First value whose key matches one of the requested names. */
function firstMatchingValue(info, names) {
	for (const name of names) {
		if (typeof info[name] === 'string' && info[name].trim() !== '') return info[name].trim();
	}
	return null;
}

/** Trim a text block down to its first meaningful line. */
export function cleanLine(text) {
	if (typeof text !== 'string') return null;
	const line = text
		.split(/\r?\n/)
		.map((item) => item.trim())
		.find((item) => item !== '');
	return line ?? null;
}

/**
 * Parse probe output end to end.
 *
 * @param {string} text - probe stdout, saved snapshot, or transcript.
 * @returns {object} environment report.
 */
export function parseProbeOutput(text) {
	return buildEnvironmentReport(parseSections(text));
}
