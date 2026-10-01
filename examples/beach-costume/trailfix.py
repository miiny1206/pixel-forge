"""Leg-swing trail left in get-up frame 220 (anim 19).

The stock sheet draws the kick-up as a dark crescent the colour of her stockings; the bikini
redraw kept it, beachfx.py then tinted it sea blue, and in game it swept over her head as she
stood up. Bare legs leave no such trail, so it is cut: its three blues (y < 40, where the arc
is), the black streaks at its root (x >= 40, y < 20), then any scrap under 8 pixels.

Frame 223 (end of the get-up, also shown at 221/222 by hold.py) keeps the stock's small
slash streak off to her right, alone on the floor in game: every scrap under 100 px is cut.

Runs on bake_all/ from finish.py, after swordfix.py.
"""
from PIL import Image

from _paths import WORK
OUT = WORK + 'bake_all/'
TRAIL = {(49, 109, 155), (173, 201, 221), (202, 220, 235)}


def scraps(p, w, h, size):
    """Clear every 8-connected piece smaller than `size`; returns pixels cleared."""
    n = 0
    seen = set()
    for x in range(w):
        for y in range(h):
            if not p[x, y][3] or (x, y) in seen:
                continue
            comp, st = [], [(x, y)]
            seen.add((x, y))
            while st:
                a, b = st.pop()
                comp.append((a, b))
                for da in (-1, 0, 1):
                    for db in (-1, 0, 1):
                        q = (a + da, b + db)
                        if 0 <= q[0] < w and 0 <= q[1] < h and q not in seen and p[q][3]:
                            seen.add(q)
                            st.append(q)
            if len(comp) < size:
                for q in comp:
                    p[q] = (0, 0, 0, 0)
                n += len(comp)
    return n


def main():
    path = OUT + 'frame_220.png'
    im = Image.open(path).convert('RGBA')
    w, h = im.size
    p = im.load()
    n = 0
    for x in range(w):
        for y in range(h):
            c = p[x, y]
            if c[3] and ((c[:3] in TRAIL and y < 40) or (c[:3] == (0, 0, 0) and x >= 40 and y < 20)):
                p[x, y] = (0, 0, 0, 0)
                n += 1
    n += scraps(p, w, h, 8)
    im.save(path)
    print('frame_220: trail cut (%d px)' % n)
    path = OUT + 'frame_223.png'
    im = Image.open(path).convert('RGBA')
    n = scraps(im.load(), im.size[0], im.size[1], 100)
    im.save(path)
    print('frame_223: %d px of streak cut' % n)


if __name__ == '__main__':
    main()
