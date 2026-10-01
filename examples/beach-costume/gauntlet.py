"""One bracer on her back forearm through the dash (anim 12, frames 88-95).

Each dash frame was redrawn on its own (GPT or Gemini), so the black bracer on the arm behind
the sword came back a different shape every frame -- orange trim in some, none in others -- and
in game it flickers while she runs. Her arm sits where the stock arm is, so the stock bracer goes
back: inside a box around it (read off the stock frames), every stock pixel that is bracer
(dark, or its orange trim) replaces what the model drew. Skin and the blade are never copied.

91 and 95 also kept a scrap of the stock shoe on the trailing foot (bottom-left corner): its
two greys become skin -- outline where the pixel touches the background, shade inside.

Runs on bake_all/ from finish.py, after swordfix.py.

    gauntlet.py                    bake_all/ in place
    gauntlet.py preview <out.png>
"""
import sys
from PIL import Image

from _paths import WORK as HERE
OUT = HERE + 'bake_all/'
BOX = {88: (38, 30, 47, 45), 89: (38, 30, 47, 45), 90: (38, 30, 47, 45), 91: (49, 29, 59, 45),
       92: (38, 30, 47, 45), 93: (38, 30, 48, 45), 94: (38, 30, 48, 45), 95: (51, 30, 61, 45)}


def bracer(p):
    r, g, b = p[:3]
    return r + g + b < 200 or (r > 150 and 50 < g < 190 and b < 100)


def fix(im, stock):
    px, sp = im.load(), stock.load()
    x0, y0, x1, y1 = BOX[fix.f]
    n = 0
    for x in range(x0, min(x1, im.size[0])):
        for y in range(y0, min(y1, im.size[1])):
            s = sp[x, y]
            if s[3] >= 128 and bracer(s) and px[x, y] != s:
                px[x, y] = s; n += 1
    return n


SHOE = {91: (0, 56, 14, 72), 95: (0, 59, 14, 75)}
SHOE_DARK, SHOE_MID = (66, 56, 82), (115, 113, 132)
OUTLINE, SHADE, SKIN = (132, 65, 66, 255), (239, 150, 140, 255), (247, 174, 165, 255)


def bare(im, f):
    px = im.load()
    w, h = im.size
    x0, y0, x1, y1 = SHOE[f]
    edge = lambda x, y: any(not (0 <= x + i < w and 0 <= y + j < h) or px[x + i, y + j][3] < 128
                            for i, j in ((1, 0), (-1, 0), (0, 1), (0, -1)))
    todo = [(x, y) for x in range(x0, x1) for y in range(y0, min(y1, h))
            if px[x, y][3] >= 128 and px[x, y][:3] in (SHOE_DARK, SHOE_MID)]
    new = {(x, y): OUTLINE if edge(x, y) else (SHADE if px[x, y][:3] == SHOE_DARK else SKIN) for x, y in todo}
    for q, c in new.items():
        px[q] = c
    return len(new)


def main():
    prev = sys.argv[1:2] == ['preview']
    tiles = []
    for f in BOX:
        name = 'frame_%03d.png' % f
        a = Image.open(OUT + name).convert('RGBA')
        s = Image.open(HERE + 'all/' + name).convert('RGBA')
        b = a.copy(); fix.f = f; n = fix(b, s)
        if f in SHOE:
            n += bare(b, f)
        if prev:
            tiles.append((s, a, b))
        else:
            b.save(OUT + name); print('%s: %d px' % (name, n))
    if prev:
        W = sum(t[0].size[0] + 4 for t in tiles); H = max(t[0].size[1] for t in tiles)
        sh = Image.new('RGBA', (W, 3 * (H + 4)), (0, 200, 200, 255)); x = 0
        for t in tiles:
            for r, im in enumerate(t):
                sh.paste(im, (x, r * (H + 4)), im)
            x += t[0].size[0] + 4
        sh.resize((sh.size[0] * 3, sh.size[1] * 3), Image.NEAREST).save(sys.argv[2])


if __name__ == '__main__':
    main()
