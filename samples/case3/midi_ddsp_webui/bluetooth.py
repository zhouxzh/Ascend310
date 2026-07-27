from __future__ import annotations

import re
import shutil
import subprocess
import time


MAC_PATTERN = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
DEVICE_LINE_PATTERN = re.compile(
    r"^Device\s+(?P<address>(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})\s+(?P<name>.+)$"
)

AUDIO_UUID_HINTS = (
    "audio sink",
    "advanced audio distribution",
    "a/v remote control",
    "headset",
    "handsfree",
)
AUDIO_ICON_HINTS = (
    "audio",
    "speaker",
    "headset",
    "headphones",
)
AUDIO_NAME_HINTS = (
    "audio",
    "speaker",
    "sound",
    "headphone",
    "headset",
    "earbud",
    "edifier",
    "jbl",
    "bose",
    "sony",
    "huawei",
    "xiaomi",
    "漫步者",
    "音箱",
    "喇叭",
    "耳机",
)


def normalize_address(address: str) -> str:
    value = address.strip().upper()
    if not MAC_PATTERN.fullmatch(value):
        raise ValueError("Bluetooth address must use MAC format XX:XX:XX:XX:XX:XX")
    return value


def parse_bool(value: str) -> bool | None:
    normalized = value.strip().lower()
    if normalized in {"yes", "true", "on"}:
        return True
    if normalized in {"no", "false", "off"}:
        return False
    return None


def parse_bluetooth_controller(output: str) -> dict[str, object] | None:
    controller: dict[str, object] = {
        "address": "",
        "name": "",
        "powered": None,
        "discovering": None,
        "pairable": None,
        "discoverable": None,
    }
    for line in output.splitlines():
        text = line.strip()
        if text.startswith("Controller "):
            parts = text.split(maxsplit=2)
            if len(parts) >= 2 and MAC_PATTERN.fullmatch(parts[1]):
                controller["address"] = parts[1].upper()
            if len(parts) >= 3:
                controller["name"] = parts[2]
            continue
        if ":" not in text:
            continue
        key, value = text.split(":", 1)
        key = key.strip().lower()
        if key == "name":
            controller["name"] = value.strip()
        elif key == "powered":
            controller["powered"] = parse_bool(value)
        elif key == "discovering":
            controller["discovering"] = parse_bool(value)
        elif key == "pairable":
            controller["pairable"] = parse_bool(value)
        elif key == "discoverable":
            controller["discoverable"] = parse_bool(value)
    if not controller["address"] and not controller["name"]:
        return None
    return controller


def parse_bluetooth_device_lines(output: str) -> list[tuple[str, str]]:
    devices: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in output.splitlines():
        match = DEVICE_LINE_PATTERN.match(line.strip())
        if match is None:
            continue
        address = normalize_address(match.group("address"))
        if address in seen:
            continue
        devices.append((address, match.group("name").strip()))
        seen.add(address)
    return devices


def _is_audio_device(payload: dict[str, object]) -> bool:
    fields = [
        str(payload.get("name", "")),
        str(payload.get("alias", "")),
        str(payload.get("icon", "")),
        *[str(item) for item in payload.get("uuids", [])],
    ]
    text = " ".join(fields).lower()
    return any(hint in text for hint in AUDIO_UUID_HINTS + AUDIO_ICON_HINTS + AUDIO_NAME_HINTS)


def parse_bluetooth_device_info(
    address: str,
    output: str,
    fallback_name: str = "",
) -> dict[str, object]:
    normalized_address = normalize_address(address)
    payload: dict[str, object] = {
        "address": normalized_address,
        "name": fallback_name,
        "alias": fallback_name,
        "icon": "",
        "paired": False,
        "bonded": False,
        "trusted": False,
        "blocked": False,
        "connected": False,
        "rssi": None,
        "uuids": [],
        "is_audio": False,
        "status": "available",
    }

    uuids: list[str] = []
    for line in output.splitlines():
        text = line.strip()
        if not text:
            continue
        if text.startswith("Device "):
            match = DEVICE_LINE_PATTERN.match(text)
            if match is not None and not payload["name"]:
                payload["name"] = match.group("name").strip()
                payload["alias"] = payload["name"]
            continue
        if ":" not in text:
            continue
        key, value = text.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "name":
            payload["name"] = value
        elif key == "alias":
            payload["alias"] = value
        elif key == "icon":
            payload["icon"] = value
        elif key == "paired":
            payload["paired"] = bool(parse_bool(value))
        elif key == "bonded":
            payload["bonded"] = bool(parse_bool(value))
        elif key == "trusted":
            payload["trusted"] = bool(parse_bool(value))
        elif key == "blocked":
            payload["blocked"] = bool(parse_bool(value))
        elif key == "connected":
            payload["connected"] = bool(parse_bool(value))
        elif key == "rssi":
            try:
                payload["rssi"] = int(value)
            except ValueError:
                payload["rssi"] = None
        elif key == "uuid":
            uuids.append(value)

    payload["uuids"] = uuids
    payload["is_audio"] = _is_audio_device(payload)
    if payload["blocked"]:
        payload["status"] = "blocked"
    elif payload["connected"]:
        payload["status"] = "connected"
    elif payload["paired"] or payload["trusted"] or payload["bonded"]:
        payload["status"] = "paired"
    return payload


def _bluetoothctl_path() -> str:
    path = shutil.which("bluetoothctl")
    if path is None:
        raise RuntimeError("bluetoothctl is not available on this system")
    return path


def _command_error(command: list[str], result: subprocess.CompletedProcess[str]) -> str:
    output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    return output or f"{' '.join(command)} exited with code {result.returncode}"


def run_bluetoothctl(
    args: list[str],
    *,
    timeout: float = 5.0,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    command = [_bluetoothctl_path(), *args]
    try:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"bluetoothctl {' '.join(args)} timed out") from exc
    if check and result.returncode != 0:
        raise RuntimeError(_command_error(command, result))
    return result


def run_bluetoothctl_script(
    commands: list[str],
    *,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    command = [_bluetoothctl_path()]
    script = "\n".join(commands + ["quit"]) + "\n"
    try:
        result = subprocess.run(
            command,
            input=script,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("bluetoothctl command sequence timed out") from exc
    if result.returncode != 0:
        raise RuntimeError(_command_error(command, result))
    return result


def query_bluetooth_audio_devices() -> dict[str, object]:
    show = run_bluetoothctl(["show"], check=False)
    devices = run_bluetoothctl(["devices"])
    results = []
    for address, name in parse_bluetooth_device_lines(devices.stdout):
        info = run_bluetoothctl(["info", address], check=False)
        if info.returncode == 0:
            results.append(parse_bluetooth_device_info(address, info.stdout, name))
        else:
            results.append(
                {
                    "address": address,
                    "name": name,
                    "alias": name,
                    "icon": "",
                    "paired": False,
                    "bonded": False,
                    "trusted": False,
                    "blocked": False,
                    "connected": False,
                    "rssi": None,
                    "uuids": [],
                    "is_audio": _is_audio_device({"name": name, "uuids": []}),
                    "status": "available",
                }
            )

    results.sort(
        key=lambda item: (
            not bool(item.get("connected")),
            not bool(item.get("is_audio")),
            str(item.get("name", "")).lower(),
            str(item.get("address", "")),
        )
    )
    return {
        "controller": parse_bluetooth_controller(show.stdout),
        "devices": results,
    }


def wait_for_bluetooth_device(
    address: str,
    field: str,
    expected: object,
    *,
    timeout: float = 10.0,
    interval: float = 0.5,
) -> dict[str, object]:
    normalized_address = normalize_address(address)
    deadline = time.monotonic() + timeout
    last_device: dict[str, object] = {
        "address": normalized_address,
        "name": "",
        "alias": "",
        "icon": "",
        "paired": False,
        "bonded": False,
        "trusted": False,
        "blocked": False,
        "connected": False,
        "rssi": None,
        "uuids": [],
        "is_audio": False,
        "status": "available",
    }
    while True:
        info = run_bluetoothctl(["info", normalized_address], check=False)
        if info.returncode == 0:
            last_device = parse_bluetooth_device_info(normalized_address, info.stdout)
            if last_device.get(field) == expected:
                return last_device
        if time.monotonic() >= deadline:
            return last_device
        time.sleep(interval)


def scan_bluetooth_audio_devices(duration_seconds: float = 8.0) -> dict[str, object]:
    duration = min(max(float(duration_seconds), 2.0), 30.0)
    process = subprocess.Popen(
        [_bluetoothctl_path()],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        if process.stdin is None:
            raise RuntimeError("Unable to open bluetoothctl input")
        process.stdin.write("power on\n")
        process.stdin.write("agent on\n")
        process.stdin.write("default-agent\n")
        process.stdin.write("scan on\n")
        process.stdin.flush()
        time.sleep(duration)
        process.stdin.write("scan off\n")
        process.stdin.write("quit\n")
        process.stdin.flush()
        stdout, stderr = process.communicate(timeout=5.0)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.communicate(timeout=2.0)
        raise RuntimeError("bluetoothctl scan timed out") from exc
    finally:
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
    if process.returncode != 0:
        output = "\n".join(part.strip() for part in (stdout, stderr) if part.strip())
        raise RuntimeError(output or f"bluetoothctl scan exited with code {process.returncode}")
    return query_bluetooth_audio_devices()


def _run_pactl(args: list[str], *, timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["pactl", *args],
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _pulse_names(address: str) -> tuple[str, str]:
    identifier = normalize_address(address).replace(":", "_")
    return f"bluez_card.{identifier}", f"bluez_sink.{identifier}.a2dp_sink"


def _pulse_card_block(cards_output: str, card_name: str) -> str | None:
    blocks = re.split(r"\n(?=Card #)", cards_output)
    for block in blocks:
        if re.search(rf"^\s*Name:\s+{re.escape(card_name)}\s*$", block, re.MULTILINE):
            return block
    return None


def _pulse_card_profile(block: str) -> str | None:
    match = re.search(r"^\s*Active Profile:\s+(\S+)\s*$", block, re.MULTILINE)
    return match.group(1) if match else None


def _pulse_card_profiles(block: str) -> list[str]:
    profiles: list[str] = []
    in_profiles = False
    for line in block.splitlines():
        text = line.strip()
        if text == "Profiles:":
            in_profiles = True
            continue
        if not in_profiles:
            continue
        if text.startswith("Active Profile:") or text == "Ports:":
            break
        if ":" in text:
            profile = text.split(":", 1)[0].strip()
            if profile and profile not in profiles:
                profiles.append(profile)
    return profiles


def _pulse_a2dp_sink(address: str) -> str | None:
    _card_name, sink_name = _pulse_names(address)
    result = _run_pactl(["list", "short", "sinks"], timeout=5.0)
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == sink_name:
            return sink_name
    return None


def _set_default_pulse_sink(address: str) -> str | None:
    sink_name = _pulse_a2dp_sink(address)
    if sink_name is None:
        return None
    _run_pactl(["set-default-sink", sink_name], timeout=5.0)
    return sink_name


def select_a2dp_profile(address: str) -> dict[str, object]:
    if shutil.which("pactl") is None:
        return {"selected": None, "error": "pactl is not available"}
    card_name, _sink_name = _pulse_names(address)
    errors: list[str] = []
    deadline = time.monotonic() + 12.0
    while True:
        cards = _run_pactl(["list", "cards"], timeout=5.0)
        if cards.returncode == 0:
            block = _pulse_card_block(cards.stdout, card_name)
            if block is not None:
                active_profile = _pulse_card_profile(block)
                if active_profile == "a2dp_sink":
                    sink = _set_default_pulse_sink(address)
                    return {"selected": active_profile, "sink": sink, "error": None}

                profiles = _pulse_card_profiles(block)
                candidates = [profile for profile in ("a2dp_sink", "a2dp-sink") if profile in profiles]
                candidates.extend(
                    profile
                    for profile in profiles
                    if "a2dp" in profile and "sink" in profile and profile not in candidates
                )
                for profile in candidates:
                    result = _run_pactl(["set-card-profile", card_name, profile], timeout=5.0)
                    if result.returncode == 0:
                        sink = _set_default_pulse_sink(address)
                        return {"selected": profile, "sink": sink, "error": None}
                    errors.append(
                        _command_error(["pactl", "set-card-profile", card_name, profile], result)
                    )
            elif _pulse_a2dp_sink(address) is not None:
                sink = _set_default_pulse_sink(address)
                return {"selected": "a2dp_sink", "sink": sink, "error": None}
        else:
            errors.append(_command_error(["pactl", "list", "cards"], cards))

        if time.monotonic() >= deadline:
            break
        time.sleep(0.5)

    sink = _set_default_pulse_sink(address)
    if sink is not None:
        return {"selected": "a2dp_sink", "sink": sink, "error": None}
    if not errors:
        errors.append(f"PulseAudio card {card_name} did not appear")
    return {"selected": None, "sink": None, "error": " | ".join(errors)}


def connect_bluetooth_audio_device(
    address: str,
    *,
    pair: bool = True,
    trust: bool = True,
) -> dict[str, object]:
    normalized_address = normalize_address(address)
    run_bluetoothctl(["power", "on"], timeout=10.0, check=False)
    run_bluetoothctl(["agent", "on"], timeout=10.0, check=False)
    run_bluetoothctl(["default-agent"], timeout=10.0, check=False)

    info = run_bluetoothctl(["info", normalized_address], check=False)
    device = (
        parse_bluetooth_device_info(normalized_address, info.stdout)
        if info.returncode == 0
        else wait_for_bluetooth_device(normalized_address, "address", normalized_address, timeout=2.0)
    )
    errors: list[str] = []
    if pair and not device["paired"]:
        pair_result: subprocess.CompletedProcess[str] | None = None
        try:
            pair_result = run_bluetoothctl(
                ["--agent", "NoInputNoOutput", "pair", normalized_address],
                timeout=45.0,
                check=False,
            )
        except RuntimeError as exc:
            errors.append(str(exc))
        device = wait_for_bluetooth_device(
            normalized_address,
            "paired",
            True,
            timeout=8.0,
        )
        if pair_result is not None and pair_result.returncode != 0 and not device["paired"]:
            errors.append(_command_error(["bluetoothctl", "pair", normalized_address], pair_result))

    if trust and not device["trusted"]:
        trust_result = run_bluetoothctl(
            ["trust", normalized_address],
            timeout=10.0,
            check=False,
        )
        device = wait_for_bluetooth_device(
            normalized_address,
            "trusted",
            True,
            timeout=3.0,
        )
        if trust_result.returncode != 0 and not device["trusted"]:
            errors.append(_command_error(["bluetoothctl", "trust", normalized_address], trust_result))

    connect_result = run_bluetoothctl(
        ["connect", normalized_address],
        timeout=30.0,
        check=False,
    )
    device = wait_for_bluetooth_device(
        normalized_address,
        "connected",
        True,
        timeout=10.0,
    )
    if connect_result.returncode != 0 and not device["connected"]:
        errors.append(_command_error(["bluetoothctl", "connect", normalized_address], connect_result))
    profile = select_a2dp_profile(normalized_address) if device["connected"] else {"selected": None, "error": None}
    if not device["connected"]:
        output = "\n".join(part for part in errors if part)
        if output:
            output = (
                output
                + "\n请确认蓝牙喇叭处于配对模式，并且没有被手机或其他电脑占用。"
            )
        if not output:
            output = (
                f"Bluetooth device {normalized_address} did not connect; "
                f"paired={device.get('paired')}, trusted={device.get('trusted')}, "
                f"connected={device.get('connected')}"
            )
        raise RuntimeError(output or f"Bluetooth device {normalized_address} did not connect")
    return {
        "device": device,
        "profile": profile,
    }


def disconnect_bluetooth_audio_device(address: str) -> dict[str, object]:
    normalized_address = normalize_address(address)
    run_bluetoothctl_script([f"disconnect {normalized_address}"], timeout=15.0)
    info = run_bluetoothctl(["info", normalized_address], check=False)
    return {
        "device": parse_bluetooth_device_info(normalized_address, info.stdout),
        "profile": {"selected": None, "error": None},
    }
