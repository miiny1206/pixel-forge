"""Preview sheets for the mask/paint scripts' `preview` commands.

    sheet(dst, tiles, Z)        tiles of (w, h, [rgb or None]), bottom-aligned, zoomed Z times
    view_sheet(frames, dst, scale)   (w, h, RGB565 px) frames side by side
"""
from PIL import Image

import frame_out as F


def sheet(dst, tiles, Z=3):
    W = sum(t[0] + 8 for t in tiles)
    H = max(t[1] for t in tiles)
    img = Image.new('RGBA', (W, H), (80, 80, 80, 255))
    x0 = 0
    for w, h, px in tiles:
        for y in range(h):
            for x in range(w):
                c = px[y * w + x]
                if c:
                    img.putpixel((x0 + x, H - h + y), tuple(c) + (255,))
        x0 += w + 8
    img.resize((W * Z, H * Z), Image.NEAREST).save(dst)


def view_sheet(frs, path, scale=1, bg=(80, 80, 80), key=F.KEY):
    frs = [f for f in frs if f[0] > 0]
    sheet(path, [(w, h, [None if v == key else F.rgb(v) for v in px]) for w, h, px in frs], scale)
