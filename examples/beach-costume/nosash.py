"""Strip the blue skirt sash that survives in finished frames, below 30% of her figure box
(crops.json) - the hair band is the same blue family and sits above that; the waist bow,
which the model sometimes keeps, sits below. Pieces of fewer than 40 px left hanging below
the line once the sash is gone are removed with it. Idempotent.

    nosash.py <run> <frame,...>        runs/<run>/final -> runs/<run>/nosash
    nosash.py --dir DIR                every frame_NNN.png in DIR, in place
"""
import json
import os, sys
from PIL import Image

from _paths import WORK as HERE
SASH = {(49, 40, 140), (49, 44, 140), (107, 113, 231), (57, 73, 189), (57, 77, 189)}


def comps(px, w, h):
    seen, out = set(), []
    for y in range(h):
        for x in range(w):
            if px[x, y][3] >= 128 and (x, y) not in seen:
                st, c = [(x, y)], []
                seen.add((x, y))
                while st:
                    p = st.pop()
                    c.append(p)
                    for dx in (-1, 0, 1):
                        for dy in (-1, 0, 1):
                            q = (p[0] + dx, p[1] + dy)
                            if 0 <= q[0] < w and 0 <= q[1] < h and q not in seen and px[q][3] >= 128:
                                seen.add(q)
                                st.append(q)
                out.append(c)
    return out


def clean(src, dst, f):
    by, bh = json.load(open(HERE + 'crops.json'))[str(f)][1::2]
    line = by + int(0.3 * bh)
    im = Image.open(src).convert('RGBA')
    px, (w, h) = im.load(), im.size
    n = m = 0
    if True:
        for y in range(line, h):
            for x in range(w):
                if px[x, y][3] >= 128 and px[x, y][:3] in SASH:
                    px[x, y] = (0, 0, 0, 0)
                    n += 1
        for c in comps(px, w, h):
            if n and len(c) < 40 and min(p[1] for p in c) >= line:
                for p in c:
                    px[p] = (0, 0, 0, 0)
                    m += 1
        im.save(dst)
        print('frame %d: sash %d px, loose %d px' % (f, n, m))


def main():
    if sys.argv[1] == '--dir':
        d = sys.argv[2]
        for name in sorted(os.listdir(d)):
            if name.startswith('frame_') and name.endswith('.png'):
                clean(os.path.join(d, name), os.path.join(d, name), int(name[6:9]))
        return
    root = HERE + 'runs/%s/' % sys.argv[1]
    os.makedirs(root + 'nosash', exist_ok=True)
    for f in [int(v) for v in sys.argv[2].split(',')]:
        clean(root + 'final/frame_%03d.png' % f, root + 'nosash/frame_%03d.png' % f, f)


if __name__ == '__main__':
    main()
