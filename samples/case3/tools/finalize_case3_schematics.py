"""Normalize the accepted Case 3 schematics for the book and PDF build."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


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


def relabel_hardware_connections(image: Image.Image) -> None:
    """Replace brand-specific labels in the accepted hardware schematic."""
    if image.size != (2048, 1280):
        raise ValueError(f"Unexpected hardware schematic size: {image.size}")

    draw = ImageDraw.Draw(image)
    font_path = Path(r"C:\Windows\Fonts\msyhbd.ttc")

    def replace(box: tuple[int, int, int, int], text: str, size: int) -> None:
        draw.rectangle(box, fill="white")
        font = ImageFont.truetype(str(font_path), size=size)
        bounds = draw.textbbox((0, 0), text, font=font)
        width = bounds[2] - bounds[0]
        height = bounds[3] - bounds[1]
        x = box[0] + (box[2] - box[0] - width) // 2
        y = box[1] + (box[3] - box[1] - height) // 2 - bounds[1]
        draw.text((x, y), text, font=font, fill="black")

    replace((245, 285, 525, 340), "触摸屏", 34)
    replace((720, 175, 1260, 235), "摄像头", 36)
    replace((1440, 295, 1815, 350), "音箱", 34)
    replace((210, 645, 525, 705), "MIDI 键盘", 34)
    replace((920, 385, 1240, 440), "USB 音频输入", 30)
    replace((1240, 435, 1470, 485), "USB 音频输出", 28)
    replace((535, 695, 730, 775), "USB MIDI 输入", 27)
    replace((765, 950, 1325, 1020), "按设备标识显式路由；禁止使用输出监听源", 28)


def relabel_hardware_file(path: Path) -> None:
    with Image.open(path) as source:
        image = source.convert("RGB")
    relabel_hardware_connections(image)
    image.save(path, format="PNG", optimize=True, dpi=(300, 300))


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
