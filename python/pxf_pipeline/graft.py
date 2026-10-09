"""Fill a frame from a finished frame of the same pose - no request is made.

Two modes, both writing runs/graft/bake/frame_NNN.png (finish.py lets them win over the
runs, and smooth.py treats them as fixed points):

graft (default) - for a frame the model refused that shares a pose with one that came back.
    Donor and target are aligned on their STOCK figures (transplant.best_shift, figure masks
    only, so a moving effect or a second character cannot win the alignment). Wherever the
    target's stock pixel matches the donor's and the redraw changed that donor pixel, the
    donor's finished pixel is taken, transparency too. Everything else stays stock - effects,
    and the parts the artist drew differently. Limited to the donor's figure mask.

--stamp - for a held pose that was redrawn frame by frame (the stock art holds one pose while
    only an aura moves, but each redraw drew the body a little differently, so it flickered).
    The target's figure is cleared and the donor's whole finished figure is put in its place.

In both modes a pixel that is now transparent but was opaque in the stock frame is a hole
where the old drawing covered an effect; it is grown in from the effect colours around it
(pixels outside the figure mask), and left transparent when there are none (real background).
--no-fill turns that off.

    python -m pxf_pipeline.graft <frame>:<donor>[,...] [--stamp] [--no-fill]
The donor's finished frame is read from runs/graft/bake (a stamped donor keeps its stamp),
else from <work>/bake_all - run `finish` first.
"""
import os, sys
from collections import Counter
from PIL import Image

from .project import current
from . import transplant as T
from .crops import figure_mask


def finished(g):
    P = current()
    for p in (P.work + 'runs/graft/bake/frame_%03d.png' % g, P.work + 'bake_all/frame_%03d.png' % g):
        if os.path.exists(p):
            return T.rgba(p)
    raise SystemExit('donor %d has no finished frame (run finish first)' % g)


def align(sf, tm, sg, gm):
    """(dx, dy, mismatch): donor pixel (x-dx, y-dy) lands on target pixel (x, y), searched
    around the shift that puts the two figure boxes' bottom-centres together"""
    cf = Image.new('RGBA', sf.size, (0, 0, 0, 0)); cf.paste(sf, (0, 0), tm)
    cg = Image.new('RGBA', sg.size, (0, 0, 0, 0)); cg.paste(sg, (0, 0), gm)
    ta, ga = tm.getbbox(), gm.getbbox()
    if not ta or not ga:
        raise SystemExit('empty figure mask')
    c = ((ta[0] + ta[2]) // 2 - (ga[0] + ga[2]) // 2, ta[3] - ga[3])
    miss, dx, dy = T.best_shift(cf.crop(ta), cg.crop(ga), r=6,
                                c=(c[0] - ta[0] + ga[0], c[1] - ta[1] + ga[1]))
    return dx + ta[0] - ga[0], dy + ta[1] - ga[1], miss


def fill_holes(out, st, tm, rounds=40):
    """grow holes (transparent now, opaque in stock) in from >= 3 neighbouring effect pixels
    (opaque, outside the figure mask); returns how many were filled"""
    po, ps, pm = out.load(), st.load(), tm.load()
    w, h = out.size
    effect = lambda x, y: po[x, y][3] >= 128 and pm[x, y] < 128
    hole = {(x, y) for y in range(h) for x in range(w) if po[x, y][3] < 128 and ps[x, y][3] >= 128}
    n = 0
    for _ in range(rounds):
        fill = []
        for (x, y) in hole:
            nb = [po[x + i, y + j] for i in (-1, 0, 1) for j in (-1, 0, 1)
                  if (i or j) and 0 <= x + i < w and 0 <= y + j < h and effect(x + i, y + j)]
            if len(nb) >= 3:
                fill.append(((x, y), Counter(nb).most_common(1)[0][0]))
        if not fill:
            break
        for q, c in fill:
            po[q] = c
            pm[q] = 0                    # filled pixels count as effect for the next round
            hole.discard(q)
        n += len(fill)
    return n


def graft(f, g, stamp=False, fill=True):
    P = current()
    sf, sg, fg = T.rgba(P.stock(f)), T.rgba(P.stock(g)), finished(g)
    tm, gm = figure_mask(f), figure_mask(g)
    dx, dy, miss = align(sf, tm, sg, gm)
    pf, pg, pd, pt, pgm = sf.load(), sg.load(), fg.load(), tm.load(), gm.load()
    out = sf.copy(); po = out.load()
    w, h = sf.size
    n = 0
    if stamp:
        for y in range(h):
            for x in range(w):
                if pt[x, y] >= 128:
                    po[x, y] = (0, 0, 0, 0)
    for y in range(h):
        for x in range(w):
            gx, gy = x - dx, y - dy
            if not (0 <= gx < gm.size[0] and 0 <= gy < gm.size[1]) or pgm[gx, gy] < 128:
                continue
            r = T.at(pd, fg.size, gx, gy)
            if stamp:
                po[x, y] = r
                n += 1
            elif pt[x, y] >= 128 and T.same(pf[x, y], pg[gx, gy]) and r != pg[gx, gy]:
                po[x, y] = r
                n += 1
    holes = fill_holes(out, sf, tm.copy()) if fill else 0
    d = P.work + 'runs/graft/bake/'
    os.makedirs(d, exist_ok=True)
    out.save(d + 'frame_%03d.png' % f)
    print('frame %d <- %s %d shift %+d,%+d figure mismatch %.2f: %d px, %d holes filled'
          % (f, 'stamp' if stamp else 'graft', g, dx, dy, miss, n, holes))
    return out


def main():
    a = sys.argv[1:]
    pairs = [x for x in a if not x.startswith('--')]
    if len(pairs) != 1:
        raise SystemExit(__doc__)
    for p in pairs[0].split(','):
        f, g = (int(v) for v in p.split(':'))
        graft(f, g, stamp='--stamp' in a, fill='--no-fill' not in a)


if __name__ == '__main__':
    main()
