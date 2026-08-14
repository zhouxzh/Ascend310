"""Small board-side helpers for retaining CANN conversion provenance."""

from __future__ import annotations

import os
from pathlib import Path
import shutil


def parse_version_info(path: Path) -> str | None:
    """Return a stable CANN identifier from a toolkit ``version.info`` file."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    values: dict[str, str] = {}
    for line in lines:
        key, separator, value = line.partition("=")
        if separator and key.strip() and value.strip():
            values[key.strip()] = value.strip()
    directory_version = values.get("version_dir")
    package_version = values.get("Version")
    if directory_version and package_version:
        return f"CANN toolkit {directory_version}; package {package_version}"
    if directory_version:
        return f"CANN toolkit {directory_version}"
    if package_version:
        return f"CANN package {package_version}"
    return None


def cann_version() -> str:
    """Discover the active CANN toolkit without relying on ``atc --version``.

    Some CANN 8 ATC builds treat ``--version`` as a conversion invocation and
    return an error.  The installed toolkit's version file is the durable
    conversion provenance instead.
    """
    roots: list[Path] = []
    for name in ("ASCEND_TOOLKIT_HOME", "ASCEND_HOME"):
        value = os.environ.get(name)
        if value:
            roots.append(Path(value))
    atc = shutil.which("atc")
    if atc is not None:
        resolved = Path(atc).resolve()
        roots.extend(resolved.parents)
    roots.extend(
        (
            Path("/usr/local/Ascend/ascend-toolkit/latest"),
            Path("/usr/local/Ascend/ascend-toolkit"),
        )
    )
    seen: set[Path] = set()
    for root in roots:
        try:
            resolved_root = root.resolve()
        except OSError:
            resolved_root = root
        if resolved_root in seen:
            continue
        seen.add(resolved_root)
        for candidate in (
            resolved_root / "version.info",
            resolved_root / "toolkit" / "version.info",
        ):
            version = parse_version_info(candidate)
            if version is not None:
                return version
    return "unknown"
