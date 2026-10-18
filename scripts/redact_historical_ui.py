"""Create privacy-safe derivatives of historical case UI captures."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "src" / "experiment"
OUTPUT_DIR = ROOT / "src" / "presentation" / "screenshots" / "cases" / "historical"
MANIFEST_PATH = ROOT / "src" / "presentation" / "screenshots" / "historical-derivations.json"

ENTRIES = [
    {
        "id": "case3-touch-performance",
        "source": "img3/case3-ui-touch-performance.png",
        "output": "case3-touch-performance-redacted.png",
        "crop_top": 230,
        "crop_bottom": 55,
        "redactions": [],
    },
    {
        "id": "case3-midi-keyboard",
        "source": "img3/case3-ui-midi-keyboard.png",
        "output": "case3-midi-keyboard-redacted.png",
        "crop_top": 230,
        "crop_bottom": 55,
        "redactions": [],
    },
    {
        "id": "case3-midi-ddsp-library",
        "source": "img3/case3-ui-midi-ddsp-library.png",
        "output": "case3-midi-ddsp-library-redacted.png",
        "crop_top": 180,
        "crop_bottom": 55,
        "redactions": [(20, 738, 900, 822)],
    },
    {
        "id": "case3-midi-ddsp-render",
        "source": "img3/case3-ui-midi-ddsp-render.png",
        "output": "case3-midi-ddsp-render-redacted.png",
        "crop_top": 180,
        "crop_bottom": 55,
        "redactions": [],
    },
    {
        "id": "case3-vst-tone",
        "source": "img3/case3-ui-ddsp-vst-tone.png",
        "output": "case3-vst-tone-redacted.png",
        "crop_top": 180,
        "crop_bottom": 55,
        "redactions": [(1035, 188, 1905, 306)],
    },
    {
        "id": "case3-vst-gate",
        "source": "img3/case3-ui-ddsp-vst-gate.png",
        "output": "case3-vst-gate-redacted.png",
        "crop_top": 180,
        "crop_bottom": 55,
        "redactions": [(1035, 188, 1905, 306)],
    },
    {
        "id": "case5-hantek-dashboard",
        "source": "img5/case5-hantek-dashboard-1920x1080.png",
        "output": "case5-hantek-dashboard-1920x1010.png",
        "crop_top": 80,
        "crop_bottom": 70,
        "redactions": [],
    },
]
LABEL = "设备信息已脱敏"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def font() -> ImageFont.ImageFont:
    for candidate in (
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    ):
        if candidate.exists():
            return ImageFont.truetype(str(candidate), 20)
    return ImageFont.load_default()


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    label_font = font()
    records = []
    for entry in ENTRIES:
        source_path = SOURCE_DIR / entry["source"]
        output_path = OUTPUT_DIR / entry["output"]
        if not source_path.is_file():
            raise FileNotFoundError(source_path)

        with Image.open(source_path) as image:
            source_width, source_height = image.size
            prepared = image.convert("RGB")
            draw = ImageDraw.Draw(prepared)
            for left, top, right, bottom in entry["redactions"]:
                draw.rectangle((left, top, right, bottom), fill=(247, 249, 250))
                draw.text(
                    (left + 16, top + 12),
                    LABEL,
                    fill=(70, 85, 90),
                    font=label_font,
                )
            cropped = prepared.crop(
                (0, entry["crop_top"], source_width, source_height - entry["crop_bottom"])
            )
            cropped.save(output_path, format="PNG", optimize=True)

        records.append(
            {
                "id": entry["id"],
                "source": str(source_path.relative_to(ROOT)).replace("\\", "/"),
                "sourceSha256": sha256(source_path),
                "output": str(output_path.relative_to(ROOT)).replace("\\", "/"),
                "outputSha256": sha256(output_path),
                "outputBytes": output_path.stat().st_size,
                "transform": {
                    "cropTopPx": entry["crop_top"],
                    "cropBottomPx": entry["crop_bottom"],
                    "redactions": entry["redactions"],
                    "label": LABEL,
                },
                "evidence": "historical UI acceptance capture; not a current board deployment",
            }
        )

    manifest = {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "policy": "Derived copies remove the header/footer environment strip and known device fields. Original files are not linked from the deck.",
        "assets": records,
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(records)} redacted assets and {MANIFEST_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
