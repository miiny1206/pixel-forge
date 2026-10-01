"""Her own mask and crop box in the boss finisher (anim 37), where bodymask.figure grabs the boss.

The boss is drawn the same in most of these frames; a frame without her (281, 282, 267) placed
at the best shift (found by a one-off alignment search, recorded in REF below) matches everything but her, the flickering aura
and the slashes. So her mask is: pixels that differ from that reference, minus effect colours
(purple/magenta aura, the red beam), taking the piece that holds her teal hair plus the pieces
next to it (a sword or leg cut off by an effect).
260-262 are a different scene with no reference and a background as dark as her stockings: her
box is set by hand and her mask is what in it is not a scene colour, grown 3 px over the dark.

Writes bossmask/frame_NNN.png (white = her; expand.masks uses it instead of bodymask) and prints
her crop box; `bossmask.py apply` writes the boxes into crops.json.

    bossmask.py [frame,...]        build masks + review/boss/m_NNN.png
    bossmask.py apply              + crops.json boxes (backup crops.json.before_boss)
"""
import json, os, shutil, sys
from PIL import Image, ImageChops

from _paths import WORK as HERE
OUT = HERE + 'bossmask/'
PAD = 4
CLIP = 60
CLIPS = {278: 110, 279: 110}          # her far boot is further than 60 px from her hair
# frame: (reference frame, dx, dy) - the reference's top-left in this frame
REF = {268: (267, 111, 3), 271: (281, 92, -4), 272: (281, 49, -19), 273: (282, 59, 3),
       277: (281, 52, 9), 278: (281, 80, 17), 279: (282, 90, 37), 280: (282, 5, 52),
       287: (281, 0, 81), 288: (282, 0, 95), 289: (281, -3, 91)}
# alone on transparency, but bodymask finds no face (back view, lying): 5 frames that had
# no crop box at all and stayed rule-painted. Her mask: every opaque pixel not an effect.
ALONE = [107, 169, 209, 219, 245]
# no reference: her box by hand (x0, y0, x1, y1) and the scene's colours
HAND = {260: (183, 195, 258, 374), 261: (183, 195, 258, 378), 262: (183, 195, 258, 372)}
# her box by hand where the reference diff also catches the boss (289: he is drawn differently)
CLIPBOX = {289: (128, 278, 198, 425), 278: (330, 0, 480, 170)}
CLIPOUT = {278: (320, 114, 425, 170)}  # the boss's shoulder plate under her boots
SCENE = {(8, 0, 0), (16, 8, 8), (33, 4, 8), (49, 16, 24), (66, 4, 82), (132, 4, 90), (24, 8, 8)}


def rgba(f):
    return Image.open(HERE + 'all/frame_%03d.png' % f).convert('RGBA')


def effect(p):
    r, g, b = p[:3]
    return (r > g + 30 and b > g + 30) or (r > 2 * g + 40 and r > 2 * b + 20)     # aura; red beam/slash


def hair(p):
    return p[1] > p[0] + 25 and p[2] > p[0] + 40 and p[1] > 80


def pieces(pts):
    pts, out = set(pts), []
    while pts:
        s = pts.pop(); st, pc = [s], [s]
        while st:
            x, y = st.pop()
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    q = (x + dx, y + dy)
                    if q in pts:
                        pts.discard(q); st.append(q); pc.append(q)
        out.append(pc)
    return out


def bbox(pc):
    xs, ys = [p[0] for p in pc], [p[1] for p in pc]
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


def near(a, b, d):
    return a[0] - d < b[2] and b[0] - d < a[2] and a[1] - d < b[3] and b[1] - d < a[3]


def by_ref(f):
    g, dx, dy = REF[f]
    a = rgba(f); px = a.load()
    ref = Image.new('RGBA', a.size, (0, 0, 0, 0)); ref.paste(rgba(g), (dx, dy)); pr = ref.load()
    w, h = a.size
    diff = [(x, y) for y in range(h) for x in range(w) if px[x, y][3] >= 128 and not effect(px[x, y])
            and (pr[x, y][3] < 128 or sum(abs(px[x, y][i] - pr[x, y][i]) for i in range(3)) > 40)]
    teal = [q for q in diff if hair(px[q])]
    hb = bbox(max(pieces(teal), key=len))            # her hair; the spear and beam reach far
    if f in CLIPBOX:
        c = CLIPBOX[f]
        diff = [q for q in diff if c[0] <= q[0] < c[2] and c[1] <= q[1] < c[3]]
    if f in CLIPOUT:
        c = CLIPOUT[f]
        diff = [q for q in diff if not (c[0] <= q[0] < c[2] and c[1] <= q[1] < c[3])]
    cl = CLIPS.get(f, CLIP)
    diff = [q for q in diff if hb[0] - cl <= q[0] < hb[2] + cl and hb[1] - cl <= q[1] < hb[3] + cl]
    pcs = sorted(pieces(diff), key=lambda pc: -sum(hair(px[q]) for q in pc))
    her = list(pcs[0]); box = bbox(her)
    grown = True
    while grown:                                     # pieces touching her box, repeatedly
        grown = False
        for pc in pcs[1:]:
            if len(pc) >= 6 and pc[0] not in set(her) and near(bbox(pc), box, 3):
                b = bbox(pc)
                if (b[2] - b[0]) * (b[3] - b[1]) > 4 * (box[2] - box[0]) * (box[3] - box[1]):
                    continue                         # an aura sheet, not a limb
                her += pc; box = bbox(her); grown = True
        pcs = [pc for pc in pcs if pc[0] not in set(her)] if grown else pcs
    return a, close(set(her), a)


def close(her, a, n=2):
    """fill holes up to 2n px (stocking black that matched the aura's shadow)"""
    px, (w, h) = a.load(), a.size
    ring = lambda s: {(x + dx, y + dy) for (x, y) in s for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
    big = set(her)
    for _ in range(n):
        big |= ring(big)
    out = big
    for _ in range(n):
        out = {q for q in out if all(r in out for r in ring([q]))} | her
    return {q for q in out if 0 <= q[0] < w and 0 <= q[1] < h and px[q][3] >= 128}


def by_hand(f):
    a = rgba(f); px = a.load()
    x0, y0, x1, y1 = HAND[f]
    from collections import Counter
    out = Counter(px[x, y][:3] for y in range(a.size[1]) for x in range(a.size[0])
                  if not (x0 <= x < x1 and y0 <= y < y1) and px[x, y][3] >= 128)
    scene = SCENE | {c for c, n in out.items() if n >= 20}     # she is only inside her box
    core = {(x, y) for y in range(y0, min(y1, a.size[1])) for x in range(x0, x1)
            if px[x, y][3] >= 128 and px[x, y][:3] not in scene and not effect(px[x, y])}
    top = y0 + int(0.45 * (y1 - y0))                 # above her head only her ponytail counts
    teal = {q for q in core if hair(px[q])}
    tealn = {(x + dx, y + dy) for (x, y) in teal for dx in range(-3, 4) for dy in range(-3, 4)}
    core = {q for q in core if q[1] >= top or q in tealn}
    her = set(core)
    for _ in range(3):
        her |= {(x + dx, y + dy) for (x, y) in her for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                if x0 <= x + dx < x1 and y0 <= y + dy < min(y1, a.size[1]) and px[x + dx, y + dy][3] >= 128
                and not effect(px[x + dx, y + dy]) and (y + dy >= top or (x + dx, y + dy) in tealn)}
    # her lower legs are scene black: below the skirt, everything within the skirt's width
    legs = y0 + int(0.75 * (y1 - y0))
    xs = [x for (x, y) in her if legs - 10 <= y < legs]
    if xs:
        lo, hi = min(xs) - 4, max(xs) + 4
        her |= {(x, y) for y in range(legs, min(y1, a.size[1])) for x in range(max(lo, x0), min(hi, x1))
                if px[x, y][3] >= 128 and not effect(px[x, y])}
    return a, her


def by_alone(f):
    a = rgba(f); px = a.load()
    pts = [(x, y) for y in range(a.size[1]) for x in range(a.size[0])
           if px[x, y][3] >= 128 and not effect(px[x, y])]
    pcs = sorted(pieces(pts), key=len, reverse=True)
    her = list(pcs[0]); box = bbox(her)
    for pc in pcs[1:]:
        if len(pc) >= 4 and near(bbox(pc), box, 4):
            her += pc
    return a, close(set(her), a)


def build(f):
    a, her = (by_ref if f in REF else by_alone if f in ALONE else by_hand)(f)
    w, h = a.size
    m = Image.new('L', a.size, 0); mp = m.load()
    for q in her:
        mp[q] = 255
    os.makedirs(OUT, exist_ok=True)
    m.save(OUT + 'frame_%03d.png' % f)
    x0, y0, x1, y1 = bbox(list(her))
    box = [max(0, x0 - PAD), max(0, y0 - PAD)]
    box += [min(w, x1 + PAD) - box[0], min(h, y1 + PAD) - box[1]]
    bg = Image.new('RGBA', a.size, (230, 200, 140, 255)); bg.alpha_composite(a)
    red = Image.new('RGBA', a.size, (0, 255, 0, 255))
    v = Image.composite(red, bg, m.point(lambda t: 150 if t else 0)).crop((box[0], box[1], box[0] + box[2], box[1] + box[3]))
    s = bg.crop((box[0], box[1], box[0] + box[2], box[1] + box[3]))
    o = Image.new('RGB', (2 * box[2] + 2, box[3])); o.paste(s, (0, 0)); o.paste(v, (box[2] + 2, 0))
    os.makedirs(HERE + 'review/boss', exist_ok=True)
    o.resize((o.width * 4, o.height * 4), Image.NEAREST).save(HERE + 'review/boss/m_%03d.png' % f)
    print('frame %d: her %d px, box %s' % (f, len(her), box))
    return box


if __name__ == '__main__':
    a = sys.argv[1:]
    fr = [int(v) for v in a[0].split(',')] if a and a[0] != 'apply' else sorted(list(REF) + list(HAND) + ALONE)
    boxes = {f: build(f) for f in fr}
    if 'apply' in a:
        p = HERE + 'crops.json'
        if not os.path.exists(p + '.before_boss'):
            shutil.copy(p, p + '.before_boss')
        B = json.load(open(p))
        B.update({str(f): b for f, b in boxes.items()})
        json.dump(B, open(p, 'w'))
        print('crops.json: %d boxes' % len(boxes))


def backfill(path, f):
    """260-262: she stands in front of the scene, so nothing behind her may turn transparent.
    Her mask's margin over the dark background came back as the model's background, which
    remix --alpha made transparent: a sand halo around her in game. Put the scene back there -
    scene colours only: where the stock pixel was her old skirt, the scene's black instead."""
    if f not in HAND:
        return 0
    from collections import Counter
    im = Image.open(path).convert('RGBA')
    a = rgba(f)
    st = a.load()
    px, (w, h) = im.load(), im.size
    x0, y0, x1, y1 = HAND[f]
    out = Counter(st[x, y][:3] for y in range(h) for x in range(w)
                  if not (x0 <= x < x1 and y0 <= y < y1) and st[x, y][3] >= 128)
    scene = SCENE | {c for c, n in out.items() if n >= 20}
    n = 0
    for y in range(h):
        for x in range(w):
            if px[x, y][3] < 128 and st[x, y][3] >= 128:
                px[x, y] = st[x, y] if st[x, y][:3] in scene else (8, 0, 0, 255)
                n += 1
    if n:
        im.save(path)
    return n
