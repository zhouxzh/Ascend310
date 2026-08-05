"""Normalize the accepted Case 3 schematics for the book and PDF build."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageOps


ROOT = Path(__file__).resolve().parents[3]
IMAGE_ROOT = ROOT / "src" / "experiment" / "img3"

HORIZONTAL = {
    "case3-three-workflows.png",
    "case3-hardware-connections.png",
    "case3-runtime-boundary.png",
    "case3-ddsp-synthesis-flow.png",
    "case3-piano-ddsp-architecture.png",
    "case3-ddsp-vst-architecture.png",
}
PORTRAIT = {
    "case3-midi-ddsp-architecture.png",
    "case3-model-deployment-pipeline.png",
    "case3-backend-architecture.png",
    "case3-ddsp-vst-sequence.png",
}


def write_grayscale_contact_sheet(names: list[str]) -> Path:
    output = ROOT / "tmp" / "case3-schematic-checks" / "grayscale-contact-sheet.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    cell_width, cell_height = 800, 500
    sheet = Image.new("RGB", (cell_width * 2, cell_height * 5), "white")
    draw = ImageDraw.Draw(sheet)
    for index, name in enumerate(names):
        with Image.open(IMAGE_ROOT / name) as source:
            preview = ImageOps.grayscale(source.convert("RGB")).convert("RGB")
        preview.thumbnail((740, 430), Image.Resampling.LANCZOS)
        column, row = index % 2, index // 2
        left = column * cell_width + (cell_width - preview.width) // 2
        top = row * cell_height + 45 + (430 - preview.height) // 2
        sheet.paste(preview, (left, top))
        draw.text((column * cell_width + 20, row * cell_height + 15), name, fill="black")
    sheet.save(output, format="PNG", optimize=True)
    return output


def connect_harmonic_distribution(image: Image.Image) -> None:
    """Complete the accepted GPT layout's missing harmonic-control branch."""
    if image.size != (1774, 887):
        return
    draw = ImageDraw.Draw(image)
    width = 5
    points = [(823, 373), (823, 421), (464, 421)]
    draw.line(points, fill=(0, 0, 0), width=width, joint="curve")


def normalize(path: Path, target: tuple[int, int]) -> None:
    with Image.open(path) as source:
        image = source.convert("RGB")
    if path.name == "case3-ddsp-synthesis-flow.png":
        connect_harmonic_distribution(image)
    if image.size != target:
        image.thumbnail(target, Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", target, "white")
        left = (target[0] - image.width) // 2
        top = (target[1] - image.height) // 2
        canvas.paste(image, (left, top))
        image = canvas
    image.save(path, format="PNG", optimize=True, dpi=(300, 300))


def main() -> None:
    expected = HORIZONTAL | PORTRAIT
    missing = sorted(name for name in expected if not (IMAGE_ROOT / name).is_file())
    if missing:
        raise FileNotFoundError(f"Missing accepted Case 3 schematics: {missing}")
    for name in sorted(HORIZONTAL):
        normalize(IMAGE_ROOT / name, (2048, 1280))
    for name in sorted(PORTRAIT):
        normalize(IMAGE_ROOT / name, (1536, 2048))
    for name in sorted(expected):
        with Image.open(IMAGE_ROOT / name) as image:
            print(f"{name}: {image.size[0]}x{image.size[1]} {image.info.get('dpi')}")
    print(f"grayscale contact sheet: {write_grayscale_contact_sheet(sorted(expected))}")


if __name__ == "__main__":
    main()
