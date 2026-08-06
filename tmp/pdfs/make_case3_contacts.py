from pathlib import Path

from PIL import Image, ImageDraw


root = Path(__file__).parent / "case3-correct"
pages = sorted(root.glob("page-*.jpg"), key=lambda path: int(path.stem.split("-")[1]))
for group_index in range(3):
    group = pages[group_index * 13 : (group_index + 1) * 13]
    sheet = Image.new("RGB", (1280, 1820), "white")
    draw = ImageDraw.Draw(sheet)
    for index, path in enumerate(group):
        image = Image.open(path).convert("RGB")
        image.thumbnail((300, 425), Image.Resampling.LANCZOS)
        left = 20 + (index % 4) * 320
        top = 35 + (index // 4) * 455
        draw.text((left, top - 22), path.stem, fill="black")
        sheet.paste(image, (left, top))
    sheet.save(root / f"contact-{group_index + 1}.jpg", quality=88)
