from pathlib import Path

from PIL import Image, ImageDraw


source_dir = Path(__file__).parent / "case3"
pages = sorted(source_dir.glob("page-*.png"))
thumb_width = 340
label_height = 28
columns = 4
rows = 2

for sheet_index in range(0, len(pages), columns * rows):
    batch = pages[sheet_index : sheet_index + columns * rows]
    with Image.open(batch[0]) as sample:
        thumb_height = round(sample.height * thumb_width / sample.width)

    sheet = Image.new(
        "RGB",
        (columns * thumb_width, rows * (thumb_height + label_height)),
        "#d9dde3",
    )
    draw = ImageDraw.Draw(sheet)

    for index, path in enumerate(batch):
        page_number = int(path.stem.split("-")[-1])
        with Image.open(path) as page:
            thumbnail = page.convert("RGB").resize(
                (thumb_width, thumb_height), Image.Resampling.LANCZOS
            )
        x = (index % columns) * thumb_width
        y = (index // columns) * (thumb_height + label_height)
        sheet.paste(thumbnail, (x, y + label_height))
        draw.text((x + 8, y + 6), f"PDF page {page_number}", fill="black")

    output = source_dir / f"contact-{sheet_index // (columns * rows) + 1}.png"
    sheet.save(output, optimize=True)
