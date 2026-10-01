"""Last pass over the finished frames (after smooth.py and bounce.py), from the QA review.

Two defects the review found on walk (anim 1), both where smooth.py's carried pixels meet
the model's own drawing, or where the model left a scrap:
  - specks: a pixel no 8-neighbour matches within TOL, with 6+ opaque neighbours - dark
    dots on her thighs. It takes the most common neighbour colour.
  - scraps: a piece under MIN_PIECE px not joined (8-connected) to the largest piece - the
    red blob beside her legs in 9/10, blue sash bits in 16. Removed.
  - sash: blue pieces (the stock sash lies partly outside the remix mask, so it survived
    as stock pixels in 24, 25, 48, 54, 56, 69). A blue piece that touches her teal hair
    is the hair band and stays - position alone cannot tell, she is upside down in some
    frames. The rest is cleared; holes it leaves inside her are filled from neighbours.
Apart from the sash, pixels that are exactly the stock pixel are never touched: the tassel
ends, effects and anything the artist drew stay as drawn.

    cleanup.py [frame,...]         runs/smooth/bake in place (all frames if none given)
"""
import glob, os, sys
from collections import Counter
from PIL import Image

from _paths import WORK as HERE
OUT = HERE + 'runs/smooth/bake/'
TOL = 24
MIN_PIECE = 25


def near(a, b):
    return sum((a[i] - b[i]) ** 2 for i in range(3)) <= TOL * TOL


def blue(p):
    """sash blues, the navy shadow (16,24,82) included; the slash effect's dark purple
    (24,0,57) and her teal hair (57,97,123) stay out"""
    return p[3] >= 128 and p[2] > p[0] + 50 and p[2] > p[1] + 30 and p[2] >= 70


def hair(p):
    """teal by hue, not brightness: her hair shadow (57,97,123) failed a g > 100 test,
    so hair bands lost their contact and were cleared as sash (74, 321)"""
    return p[3] >= 128 and p[1] > p[0] + 25 and p[2] > p[0] + 40 and not blue(p)


def unsash(px, w, h):
    seen, cleared = set(), []
    for y in range(h):
        for x in range(w):
            if (x, y) in seen or not blue(px[x, y]):
                continue
            stack, piece, touches = [(x, y)], [], 0
            seen.add((x, y))
            while stack:
                a, b = stack.pop()
                piece.append((a, b))
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        q = (a + dx, b + dy)
                        if not (0 <= q[0] < w and 0 <= q[1] < h) or q in seen:
                            continue
                        if blue(px[q]):
                            seen.add(q)
                            stack.append(q)
                        elif hair(px[q]):
                            touches += 1
            if touches < 2:
                cleared += piece
    for q in cleared:
        px[q] = (0, 0, 0, 0)
    hole = set(cleared)
    for _ in range(4):                               # fill inward where it was inside her
        fill = []
        for (x, y) in hole:
            nb = [px[x + dx, y + dy] for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                  if (dx or dy) and 0 <= x + dx < w and 0 <= y + dy < h
                  and (x + dx, y + dy) not in hole and px[x + dx, y + dy][3] >= 128]
            if len(nb) >= 5:
                fill.append(((x, y), Counter(q[:3] for q in nb).most_common(1)[0][0]))
        for q, c in fill:
            px[q] = c + (255,)
            hole.discard(q)
    return len(cleared)


def clean(f):
    p = OUT + 'frame_%03d.png' % f
    im = Image.open(p).convert('RGBA')
    st = Image.open(HERE + 'all/frame_%03d.png' % f).convert('RGBA').load()
    px, (w, h) = im.load(), im.size
    op = lambda x, y: 0 <= x < w and 0 <= y < h and px[x, y][3] >= 128
    stock = lambda x, y: px[x, y] == st[x, y]
    sash = unsash(px, w, h)

    specks = []
    for y in range(h):
        for x in range(w):
            if not op(x, y) or stock(x, y):
                continue
            nb = [px[x + dx, y + dy][:3] for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                  if (dx or dy) and op(x + dx, y + dy)]
            if len(nb) >= 6 and not any(near(px[x, y], q) for q in nb):
                specks.append((x, y, Counter(nb).most_common(1)[0][0]))
    for x, y, c in specks:                          # decided on the frame as it was
        px[x, y] = c + (255,)

    seen, pieces = set(), []
    for y in range(h):
        for x in range(w):
            if op(x, y) and (x, y) not in seen:
                stack, piece = [(x, y)], []
                seen.add((x, y))
                while stack:
                    a, b = stack.pop()
                    piece.append((a, b))
                    for dx in (-1, 0, 1):
                        for dy in (-1, 0, 1):
                            q = (a + dx, b + dy)
                            if q not in seen and op(*q):
                                seen.add(q)
                                stack.append(q)
                pieces.append(piece)
    scraps = 0
    if pieces:
        big = max(len(pc) for pc in pieces)
        for pc in pieces:
            if len(pc) < MIN_PIECE and len(pc) < big and not all(stock(*q) for q in pc):
                for q in pc:
                    if not stock(*q):
                        px[q] = (0, 0, 0, 0)
                        scraps += 1
    im.save(p)
    return len(specks), scraps + sash


def main():
    fr = [int(v) for v in sys.argv[1].split(',')] if len(sys.argv) > 1 else \
        sorted(int(os.path.basename(p)[6:9]) for p in glob.glob(OUT + 'frame_*.png'))
    tot = [0, 0]
    for f in fr:
        s, c = clean(f)
        tot[0] += s
        tot[1] += c
        if s or c:
            print('frame %d: %d specks, %d scrap/sash px' % (f, s, c))
    print('%d frame(s): %d specks, %d scrap px' % (len(fr), tot[0], tot[1]))


if __name__ == '__main__':
    main()
