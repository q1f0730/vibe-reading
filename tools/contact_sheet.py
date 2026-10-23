"""把 ui_rewamp_shots.py 的截图拼成两张 jpg：desktop / mobile。
python3 tools/contact_sheet.py OUTDIR
每格 = 一张截图按宽缩放，长图只取前 max_h（保留首屏 + 一部分正文），下方印文件名。"""
import pathlib, sys
from PIL import Image, ImageDraw, ImageFont

OUT = pathlib.Path(sys.argv[1])
FONT = None
for f in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"):
    if pathlib.Path(f).exists():
        FONT = ImageFont.truetype(f, 22); break


def sheet(kind, cols, tile_w, max_h):
    files = sorted(OUT.glob(f"*-{kind}.png"))
    tiles = []
    for f in files:
        im = Image.open(f).convert("RGB"); s = tile_w / im.width
        im = im.resize((tile_w, int(im.height * s)), Image.LANCZOS)
        if im.height > max_h: im = im.crop((0, 0, tile_w, max_h))
        tiles.append((f.stem.replace(f"-{kind}", ""), im))
    pad, cap = 24, 40
    rows = [tiles[i:i + cols] for i in range(0, len(tiles), cols)]
    H = pad + sum(max(t[1].height for t in r) + cap + pad for r in rows)
    W = pad + cols * (tile_w + pad)
    canvas = Image.new("RGB", (W, H), (26, 23, 21)); d = ImageDraw.Draw(canvas); y = pad
    for r in rows:
        x = pad
        for name, im in r:
            canvas.paste(im, (x, y)); d.rectangle([x - 1, y - 1, x + im.width, y + im.height], outline=(70, 62, 55))
            d.text((x, y + im.height + 8), name, fill=(232, 226, 214), font=FONT); x += tile_w + pad
        y += max(t[1].height for t in r) + cap + pad
    out = OUT / f"contact-{kind}.jpg"; canvas.save(out, quality=86, optimize=True); return out, len(tiles)


if __name__ == "__main__":
    print(sheet("desktop", 4, 720, 1100))
    print(sheet("mobile", 7, 390, 1500))
