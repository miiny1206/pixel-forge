"""Carry a finished frame onto a stock frame of nearly the same pose.

Many frames are the same drawing as a frame that did come back, shifted by a pixel or three
and with a few parts moved (an idle loop, a held pose). Where the two STOCK frames agree
(after the best shift), the finished frame's pixel is taken, transparency included; where they
disagree the stock pixel stays and is reported, so what is left to fix is exactly the part the
artist drew differently - nothing is guessed.

Only pixels inside the stock figure mask (the project's masks hook) are replaced, as with remix.

    python -m pxf_pipeline.transplant <run> <frame>:<donor>[,...]      e.g. idle 0:243,1:242
writes runs/<run>/final/frame_NNN.png and runs/<run>/transplant/{diff_NNN.png, report.json}
"""
import json, os, sys
from PIL import Image

from .project import current

TOL = 30   # summed RGB distance under which two stock pixels count as the same


def rgba(p):
    return Image.open(p).convert('RGBA')


def same(p, q):
    if (p[3] >= 128) != (q[3] >= 128):
        return False
    return p[3] < 128 or sum(abs(p[i] - q[i]) for i in range(3)) <= TOL


def at(px, size, x, y):
    return px[x, y] if 0 <= x < size[0] and 0 <= y < size[1] else (0, 0, 0, 0)


def start(f, g):
    """shift that puts g's crop box bottom-centre on f's: frames of different moves sit
    at different places in their canvases, further apart than the search reaches"""
    B = current().crops()
    (xf, yf, wf, hf), (xg, yg, wg, hg) = B[str(f)], B[str(g)]
    return (xf + wf // 2) - (xg + wg // 2), (yf + hf) - (yg + hg)


def best_shift(a, b, r=4, c=(0, 0)):
    """(dx,dy) such that b[x-dx, y-dy] best matches a[x, y] over their opaque union,
    searched within r of c"""
    pa, pb = a.load(), b.load()
    best = None
    for dy in range(c[1] - r, c[1] + r + 1):
        for dx in range(c[0] - r, c[0] + r + 1):
            d = n = 0
            for y in range(a.size[1]):
                for x in range(a.size[0]):
                    p, q = pa[x, y], at(pb, b.size, x - dx, y - dy)
                    if p[3] >= 128 or q[3] >= 128:
                        n += 1
                        d += not same(p, q)
            if best is None or d / n < best[0]:
                best = (d / n, dx, dy)
    return best


def main():
    from . import crops as C
    P = current()
    run, pairs = sys.argv[1], [tuple(int(v) for v in p.split(':')) for p in sys.argv[2].split(',')]
    root = P.work + 'runs/%s/' % run
    os.makedirs(root + 'transplant', exist_ok=True)
    rep = {}
    for f, g in pairs:
        sf, sg, fg = rgba(P.stock(f)), rgba(P.stock(g)), rgba(root + 'final/frame_%03d.png' % g)
        miss, dx, dy = best_shift(sf, sg, c=start(f, g))
        w, h = sf.size
        C.masks(root + 'transplant/masks', [f], grow=0)
        m = Image.open(root + 'transplant/masks/frame_%03d.png' % f).convert('L').load()
        pf, pg, pfg = sf.load(), sg.load(), fg.load()
        out = sf.copy()
        po = out.load()
        diff = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        pd = diff.load()
        took = kept = fig = 0
        for y in range(h):
            for x in range(w):
                if m[x, y] < 128:
                    continue
                fig += 1
                if same(pf[x, y], at(pg, sg.size, x - dx, y - dy)):
                    po[x, y] = at(pfg, fg.size, x - dx, y - dy)
                    took += 1
                else:
                    kept += 1
                    pd[x, y] = (255, 0, 255, 255)
        out.save(root + 'final/frame_%03d.png' % f)
        diff.save(root + 'transplant/diff_%03d.png' % f)
        rep[f] = {'donor': g, 'shift': [dx, dy], 'stock_mismatch': round(miss, 3),
                  'figure': fig, 'taken': took, 'kept_stock': kept}
        print('frame %d <- %d shift %+d,%+d: %d px taken, %d px kept stock (to check)' % (f, g, dx, dy, took, kept))
    json.dump(rep, open(root + 'transplant/report.json', 'w'), indent=1)


if __name__ == '__main__':
    main()
