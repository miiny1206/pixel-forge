"""Chest bounce drawn into the frames (the game has no physics; sprite games animate it by
hand, a pixel or two per frame, lagging the body).

The chest is found per frame: the bikini top is the upper cluster of dark rows between her
face and her waist; the region is its bounding box plus the skin just above and below. The
region is moved `d` px vertically (rows shifted inside the region only, the row that opens
up repeats its neighbour), d following a slow wave over the animation's loop.

    bounce.py <anim> [amplitude=1]       runs/smooth/bake in place
"""
import json, math, sys
from PIL import Image

from _paths import WORK as HERE
B = json.load(open(HERE + 'crops.json'))
OUT = HERE + 'runs/smooth/bake/'


def runs(px, y, x0, x1):
    out, cur = [], None
    for x in range(x0, x1):
        dark = px[x, y][3] >= 128 and sum(px[x, y][:3]) < 120
        if dark and cur is None:
            cur = x
        if not dark and cur is not None:
            out.append((cur, x))
            cur = None
    if cur is not None:
        out.append((cur, x1))
    return out


def chest(im, f):
    """(x0, y0, x1, y1): the bikini top plus the bust above it. Measured on idle: the eyes
    are dark rows at 25% of her height and the straps are 1 px lines, the cups are the first
    rows below 35% with a solid dark run of 5+ px; the sword is dark too, so only runs
    joined to that one (gaps up to 6 px: the cleavage) and no wider than 40% of her count."""
    x, y, w, h = B[str(f)]
    px = im.load()
    rows = []
    for yy in range(y + int(0.35 * h), y + int(0.65 * h)):
        rs = runs(px, yy, x, x + w)
        big = [r for r in rs if r[1] - r[0] >= 5]
        if not big:
            if rows:
                break
            continue
        if rows:                                     # follow the cups, not the longest run (the sword)
            plo, phi = rows[-1][1], rows[-1][2]
            near = [r for r in rs if r[1] >= plo - 1 and r[0] <= phi + 1]
            if not near:
                break
            lo, hi = max(min(r[0] for r in near), plo - 2), min(max(r[1] for r in near), phi + 2)
        else:
            lo, hi = max(big, key=lambda r: r[1] - r[0])
        grown = not rows                             # join the other cup on the first row only
        while grown:                                 # grow over the other cup (cleavage gap)
            grown = False
            for r in rs:
                if r[1] >= lo - 6 and r[0] <= hi + 6 and (r[0] < lo or r[1] > hi) \
                        and max(hi, r[1]) - min(lo, r[0]) <= 0.4 * w:
                    lo, hi = min(lo, r[0]), max(hi, r[1])
                    grown = True
        if rows and hi - lo > 1.3 * (rows[0][2] - rows[0][1]) + 2:
            break                                    # the forearm guard / sword row below the cups
        rows.append((yy, lo, hi))
        if len(rows) >= 5:
            break
    if not rows:
        return None
    y0 = rows[0][0]
    return min(r[1] for r in rows) - 1, y0 - 8, max(r[2] for r in rows) + 1, rows[-1][0] + 3


def shift(im, box, d):
    x0, y0, x1, y1 = box
    px = im.load()
    src = {(x, y): px[x, y] for x in range(x0, x1) for y in range(y0, y1)}
    for x in range(x0, x1):
        for y in range(y0, y1):
            sy = min(max(y - d, y0), y1 - 1)
            px[x, y] = src[(x, sy)]


def main():
    n = int(sys.argv[1])
    amp = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
    a = json.load(open(HERE + 'anim.json'))['anims'][n]
    steps = [s['frame'] for s in a['steps'][:a['last'] + 1]]
    period = 8 if len(steps) % 8 == 0 else len(steps)
    frames = list(dict.fromkeys(steps))
    # one box for the whole loop: per-frame boxes wander (the sword touches the cups in
    # some frames), and the same area has to move for the bounce to read as one motion.
    # Median per edge, in each frame's own crop-box coordinates.
    rel = []
    for f in frames:
        b = chest(Image.open(OUT + 'frame_%03d.png' % f).convert('RGBA'), f)
        if b:
            x, y = B[str(f)][:2]
            rel.append((b[0] - x, b[1] - y, b[2] - x, b[3] - y))
    if not rel:
        print('anim %d: no chest found' % n)
        return
    med = tuple(sorted(r[i] for r in rel)[len(rel) // 2] for i in range(4))
    seen = set()
    for i, f in enumerate(steps):
        if f in seen:
            continue
        seen.add(f)
        d = round(amp * math.sin(2 * math.pi * (i % period) / period))
        x, y = B[str(f)][:2]
        box = (med[0] + x, med[1] + y, med[2] + x, med[3] + y)
        p = OUT + 'frame_%03d.png' % f
        im = Image.open(p).convert('RGBA')
        if d:
            shift(im, box, d)
            im.save(p)
        print('frame %d: d %+d box %s' % (f, d, box))


if __name__ == '__main__':
    main()
