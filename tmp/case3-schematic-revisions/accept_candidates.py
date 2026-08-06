from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parents[2]
WORK = Path(__file__).resolve().parent
FONT = Path(r"C:\Windows\Fonts\msyh.ttc")


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = Path(r"C:\Windows\Fonts\msyhbd.ttc") if bold else FONT
    return ImageFont.truetype(str(path), size=size)


def arrow(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[int, int]],
    *,
    fill: str,
    width: int = 4,
    head: int = 10,
) -> None:
    draw.line(points, fill=fill, width=width, joint="curve")
    x1, y1 = points[-2]
    x2, y2 = points[-1]
    if x1 == x2:
        direction = 1 if y2 > y1 else -1
        triangle = [(x2, y2), (x2 - head, y2 - direction * head), (x2 + head, y2 - direction * head)]
    else:
        direction = 1 if x2 > x1 else -1
        triangle = [(x2, y2), (x2 - direction * head, y2 - head), (x2 - direction * head, y2 + head)]
    draw.polygon(triangle, fill=fill)


def dashed_line(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    *,
    fill: str,
    width: int = 3,
    dash: int = 10,
    gap: int = 8,
) -> None:
    x1, y1 = start
    x2, y2 = end
    if x1 == x2:
        step = 1 if y2 >= y1 else -1
        for y in range(y1, y2, step * (dash + gap)):
            y_end = y + step * dash
            if step > 0:
                y_end = min(y_end, y2)
            else:
                y_end = max(y_end, y2)
            draw.line((x1, y, x2, y_end), fill=fill, width=width)
    else:
        step = 1 if x2 >= x1 else -1
        for x in range(x1, x2, step * (dash + gap)):
            x_end = x + step * dash
            if step > 0:
                x_end = min(x_end, x2)
            else:
                x_end = max(x_end, x2)
            draw.line((x, y1, x_end, y2), fill=fill, width=width)


def centered_text(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    text_font: ImageFont.FreeTypeFont,
    fill: str = "#17202A",
) -> None:
    left, top, right, bottom = box
    bounds = draw.textbbox((0, 0), text, font=text_font)
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    draw.text(
        ((left + right - width) / 2, (top + bottom - height) / 2 - bounds[1]),
        text,
        font=text_font,
        fill=fill,
    )


def normalize(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    fitted = ImageOps.contain(image.convert("RGB"), size, method=Image.Resampling.LANCZOS)
    output = Image.new("RGB", size, "white")
    output.paste(fitted, ((size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2))
    return output


def architecture() -> None:
    source = WORK / "case3-ddsp-vst-architecture-v2.png"
    image = Image.open(source).convert("RGB")
    draw = ImageDraw.Draw(image)

    npu_background = image.getpixel((1050, 175))
    blue = "#1769AA"
    purple = "#6F3BB8"

    # Remove the generated ambiguous state input and reuse the upper route for
    # the shifted physical frequency that feeds harmonic synthesis.
    draw.rectangle((1080, 104, 1306, 210), fill=npu_background)
    draw.rectangle((1307, 104, 1430, 220), fill="white")
    draw.rectangle((1124, 150, 1160, 236), fill=npu_background)
    arrow(draw, [(990, 258), (990, 181), (1412, 181), (1412, 294)], fill=blue, width=4, head=9)
    draw.rounded_rectangle((1080, 139, 1227, 174), radius=7, fill="#F4FAFF", outline=blue, width=2)
    centered_text(draw, (1080, 139, 1227, 174), "移调后 f0_hz", font(17, bold=True), fill="#102A43")

    # Remove the spurious CPU-to-Feature loop and extend the NPU enclosure so
    # the recurrent state is visibly separate from the audio path.
    draw.rectangle((640, 453, 1307, 526), fill=npu_background)
    draw.rectangle((700, 376, 725, 453), fill=npu_background)
    draw.rectangle((1308, 463, 1430, 526), fill="white")
    dashed_line(draw, (657, 379), (764, 379), fill=blue)
    dashed_line(draw, (640, 455), (640, 520), fill=blue)
    dashed_line(draw, (1307, 455), (1307, 520), fill=blue)
    dashed_line(draw, (640, 520), (1307, 520), fill=blue)
    dashed_line(draw, (1307, 40), (1307, 520), fill=blue)

    state_box = (925, 420, 1050, 460)
    state_out_box = (1165, 420, 1300, 460)
    for box in (state_box, state_out_box):
        draw.rounded_rectangle(box, radius=9, fill="#F7F0FF", outline=purple, width=3)
    centered_text(draw, state_box, "state [512]", font(16, bold=True))
    centered_text(draw, state_out_box, "state_out [512]", font(15, bold=True))
    arrow(draw, [(1050, 440), (1110, 440), (1110, 383)], fill=purple, width=4, head=9)
    arrow(draw, [(1174, 383), (1174, 440), (1165, 440)], fill=purple, width=4, head=9)
    arrow(draw, [(1235, 460), (1235, 500), (985, 500), (985, 460)], fill=purple, width=4, head=9)

    output = normalize(image, (2048, 1280))
    output.save(ROOT / "src/experiment/img3/case3-ddsp-vst-architecture.png", dpi=(300, 300))


def sequence() -> None:
    source = WORK / "case3-ddsp-vst-sequence-v2.png"
    image = Image.open(source).convert("RGB")
    draw = ImageDraw.Draw(image)
    green_background = image.getpixel((700, 740))

    # The model-loading request belongs to PyACL, not PulseAudio.
    draw.rectangle((520, 770, 754, 823), fill=green_background)
    arrow(draw, [(520, 801), (638, 801)], fill="#111111", width=3, head=8)
    draw.text((527, 773), "加载 Feature OM + Control OM", font=font(13, bold=True), fill="#17202A")

    # Limit the failure bracket to validation/resource/model/audio setup (6-10).
    draw.rectangle((760, 905, 778, 1035), fill=green_background)
    draw.line((767, 905, 778, 905), fill="#D92323", width=2)

    number_font = font(17, bold=True)
    for number, y in ((9, 800), (10, 883), (14, 1265), (17, 1513), (18, 1582), (19, 1650)):
        text_value = f"{number}."
        draw.rectangle((25, y - 22, 55, y + 20), fill=image.getpixel((30, y)))
        bounds = draw.textbbox((0, 0), text_value, font=number_font)
        draw.text((43 - (bounds[2] - bounds[0]), y - 13), text_value, font=number_font, fill="#17202A")

    output = normalize(image, (1536, 2048))
    output.save(ROOT / "src/experiment/img3/case3-ddsp-vst-sequence.png", dpi=(300, 300))


def grayscale_check() -> None:
    architecture_image = Image.open(ROOT / "src/experiment/img3/case3-ddsp-vst-architecture.png").convert("L")
    sequence_image = Image.open(ROOT / "src/experiment/img3/case3-ddsp-vst-sequence.png").convert("L")
    architecture_thumb = ImageOps.contain(architecture_image, (1200, 750), method=Image.Resampling.LANCZOS)
    sequence_thumb = ImageOps.contain(sequence_image, (600, 1200), method=Image.Resampling.LANCZOS)
    sheet = Image.new("L", (1800, 1200), "white")
    sheet.paste(architecture_thumb, (0, (1200 - architecture_thumb.height) // 2))
    sheet.paste(sequence_thumb, (1200, (1200 - sequence_thumb.height) // 2))
    sheet.save(WORK / "ddsp-vst-grayscale-check.png")


if __name__ == "__main__":
    architecture()
    sequence()
    grayscale_check()
