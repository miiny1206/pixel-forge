"""Put the stock sword back where a model drew it as a glinting light chain.

The dash (anim 12) came back from GPT (89, 90, 92, 93, 95) and Gemini (88) with the sheathed
sword behind her redrawn as a pale blue/white sparkle; in game she "glows" while running.
Deterministic, no model: where the stock frame has sword colours and the finished pixel is a
cool/pale one, the stock pixel goes back; pale cool pixels where the stock frame is empty are
cleared. Skin (warm) never matches; her hair sits where the stock hair is, not sword colours.

Runs on bake_all/ from finish.py, after beachfx.

    swordfix.py                    bake_all/ in place
    swordfix.py preview <out.png>
"""
import colorsys, os, sys
from PIL import Image

from _paths import WORK as HERE
OUT = HERE + 'bake_all/'
FRAMES = list(range(88, 96))          # the whole dash
SWORD = {(16, 8, 8), (24, 16, 33), (33, 24, 49), (49, 40, 140), (66, 56, 82), (107, 97, 123)}


def cool(p):
    """Glint: light blue, or white with a cool cast. Her skin highlights are warm."""
    r, g, b = p[:3]
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    h *= 360
    return (170 <= h <= 260 and s >= 0.15 and v >= 0.5) or (v >= 0.8 and (s < 0.06 or (150 <= h <= 270 and s < 0.2)))


def fix(im, stock):
    """Only around the glint (grown 2 px), so the rest of her drawing is never touched."""
    px, sp = im.load(), stock.load()
    w, h = im.size
    glint = [(x, y) for y in range(h) for x in range(w)
             if px[x, y][3] >= 128 and cool(px[x, y])
             and (sp[x, y][3] < 128 or sp[x, y][:3] in SWORD)]
    zone = set()
    for x, y in glint:
        for i in range(-2, 3):
            for j in range(-2, 3):
                zone.add((x + i, y + j))
    n = 0
    for x, y in zone:
        if not (0 <= x < w and 0 <= y < h):
            continue
        p, s = px[x, y], sp[x, y]
        if s[3] >= 128 and s[:3] in SWORD and p != s and (p[3] < 128 or cool(p)):
            px[x, y] = s; n += 1
        elif s[3] < 128 and p[3] >= 128 and cool(p):
            px[x, y] = (0, 0, 0, 0); n += 1
    return n


def main():
    if sys.argv[1:2] == ['preview']:
        tiles = []
        for f in FRAMES:
            name = 'frame_%03d.png' % f
            src = OUT + name if os.path.exists(OUT + name) else HERE + 'runs/smooth/bake/' + name
            a = Image.open(src).convert('RGBA'); s = Image.open(HERE + 'all/' + name).convert('RGBA')
            b = a.copy(); fix(b, s)
            tiles.append((s, a, b))
        W = sum(t[0].size[0] + 4 for t in tiles); H = max(t[0].size[1] for t in tiles)
        sh = Image.new('RGBA', (W, 3 * (H + 4)), (90, 90, 110, 255)); x = 0
        for t in tiles:
            for r, im in enumerate(t):
                sh.paste(im, (x, r * (H + 4)), im)
            x += t[0].size[0] + 4
        sh.resize((sh.size[0] * 2, sh.size[1] * 2), Image.NEAREST).save(sys.argv[2])
        return
    for f in FRAMES:
        name = 'frame_%03d.png' % f
        if not os.path.exists(OUT + name):
            continue
        a = Image.open(OUT + name).convert('RGBA')
        n = fix(a, Image.open(HERE + 'all/' + name).convert('RGBA'))
        a.save(OUT + name)
        print('%s: %d px' % (name, n))


if __name__ == '__main__':
    main()
