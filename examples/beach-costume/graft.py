"""Frames Gemini refused (140, 142-146: the kneeling ground slam) get the bikini from a redrawn
frame of the same pose - no request is made. Donor and target are aligned on their STOCK
drawings (transplant.best_shift); wherever the target's stock pixel matches the donor's and the
redraw changed that donor pixel, the donor's finished pixel is taken (transparency too: the
skirt goes). Everything else stays stock - effects, and the parts the artist drew differently.

    graft.py <frame>:<donor>[,...] [--stamp]    -> runs/graft/bake/frame_NNN.png
"""
import os, sys
from PIL import Image

from _paths import WORK as HERE
from pxf_pipeline import transplant as T

OUT = HERE + 'runs/graft/bake/'


def graft(f, g):
    sf, sg = T.rgba(HERE + 'all/frame_%03d.png' % f), T.rgba(HERE + 'all/frame_%03d.png' % g)
    fg = T.rgba(HERE + 'bake_all/frame_%03d.png' % g)
    import glob
    own = HERE + 'bossmask/frame_%03d.png' % f
    if not os.path.exists(own):                      # her remix mask from the run that drew her
        own = (glob.glob(HERE + 'runs/*/f%03d/masks/frame_%03d.png' % (f, f)) + [own])[0]
    if os.path.exists(own):
        # align on her alone: the boss (273) or the moving aura (251-259) would win the
        # whole-frame alignment
        import json
        B = json.load(open(HERE + 'crops.json'))
        tm = Image.open(own).convert('L')
        tb = tm.getbbox()
        x, y, w, h = B[str(g)]
        cf = Image.new('RGBA', sf.size, (0, 0, 0, 0)); cf.paste(sf, (0, 0), tm)
        cf = cf.crop(tb)
        cg = sg.crop((x, y, x + w, y + h))
        c0 = ((tb[0] + tb[2]) // 2 - (x + w // 2), tb[3] - (y + h))
        miss, ex, ey = T.best_shift(cf, cg, r=8, c=(c0[0] - tb[0] + x, c0[1] - tb[1] + y))
        dx, dy = ex + tb[0] - x, ey + tb[1] - y
        tmask = tm.load()
    else:
        miss, dx, dy = T.best_shift(sf, sg, r=6, c=T.start(f, g))
        tmask = None
    import glob
    mp = glob.glob(HERE + 'runs/*/f%03d/masks/frame_%03d.png' % (g, g))[0]
    pm = Image.open(mp).convert('L').load()         # the donor's figure: her, not the magic circle
    msz = sg.size
    pf, pg, pd = sf.load(), sg.load(), fg.load()
    out = sf.copy(); po = out.load()
    w, h = sf.size
    took = 0
    for y in range(h):
        for x in range(w):
            q = T.at(pg, sg.size, x - dx, y - dy)
            r = T.at(pd, fg.size, x - dx, y - dy)
            gx, gy = x - dx, y - dy
            inside = 0 <= gx < msz[0] and 0 <= gy < msz[1] and pm[gx, gy] >= 128
            if tmask is not None and tmask[x, y] < 128:
                continue
            if inside and T.same(pf[x, y], q) and r != q:
                po[x, y] = r
                took += 1
    holes = fill_holes(out, sf)
    os.makedirs(OUT, exist_ok=True)
    out.save(OUT + 'frame_%03d.png' % f)
    print('frame %d <- %d shift %+d,%+d stock mismatch %.2f: %d px taken, %d holes filled' % (f, g, dx, dy, miss, took, holes))


def aura(p):
    return p[3] >= 128 and p[2] > p[1] + 20 and p[0] > p[1] + 20    # pink / magenta / dark purple


def fill_holes(out, st):
    """where the old skirt covered an effect (the pink aura, 251-259), taking the skirt away
    leaves a hole: transparent now, opaque in the stock frame. In game the stage floor showed
    through, changing every frame. Grown in from the surrounding effect colours; a hole with
    no effect around it is real background and stays."""
    from collections import Counter
    po, ps = out.load(), st.load()
    w, h = out.size
    for y in range(h):                               # the aura itself behind her: stock pixel back
        for x in range(w):
            if po[x, y][3] < 128 and aura(ps[x, y]):
                po[x, y] = ps[x, y]
    hole = {(x, y) for y in range(h) for x in range(w) if po[x, y][3] < 128 and ps[x, y][3] >= 128}
    n = 0
    for _ in range(40):
        fill = []
        for (x, y) in hole:
            nb = [po[x + i, y + j] for i in (-1, 0, 1) for j in (-1, 0, 1)
                  if (i or j) and 0 <= x + i < w and 0 <= y + j < h and aura(po[x + i, y + j])]
            if len(nb) >= 3:
                fill.append(((x, y), Counter(nb).most_common(1)[0][0]))
        if not fill:
            break
        for q, c in fill:
            po[q] = c
            hole.discard(q)
        n += len(fill)
    return n




def stamp(f, g):
    """251-259: the stock art holds one pose while the aura moves, but each frame had been
    redrawn on its own (GPT and Gemini mixed), so her skin and shape jumped every frame and
    the matched-pixel graft left skirt trim where the stock edge moved by a pixel. Here the
    target's old figure is cleared and donor g's whole finished figure is put in its place;
    what is left empty is filled with the aura around it. Aligned on her pixels only."""
    import glob
    mask = lambda k: Image.open((glob.glob(HERE + 'bossmask/frame_%03d.png' % k) +
                                 glob.glob(HERE + 'runs/*/f%03d/masks/frame_%03d.png' % (k, k)))[0]).convert('L')
    sf, sg = T.rgba(HERE + 'all/frame_%03d.png' % f), T.rgba(HERE + 'all/frame_%03d.png' % g)
    gp = HERE + 'runs/graft/bake/frame_%03d.png' % g      # a stamped donor keeps its stamp
    fg = T.rgba(gp if os.path.exists(gp) else HERE + 'bake_all/frame_%03d.png' % g)
    tm, gm = mask(f), mask(g)
    cf = Image.new('RGBA', sf.size, (0, 0, 0, 0)); cf.paste(sf, (0, 0), tm)
    cg = Image.new('RGBA', sg.size, (0, 0, 0, 0)); cg.paste(sg, (0, 0), gm)
    ta, ga = tm.getbbox(), gm.getbbox()
    c = ((ta[0] + ta[2]) // 2 - (ga[0] + ga[2]) // 2, ta[3] - ga[3])
    miss, dx, dy = T.best_shift(cf.crop(ta), cg.crop(ga), r=6, c=(c[0] - ta[0] + ga[0], c[1] - ta[1] + ga[1]))
    dx, dy = dx + ta[0] - ga[0], dy + ta[1] - ga[1]
    out = sf.copy(); po = out.load()
    pt, pgm, pd = tm.load(), gm.load(), fg.load()
    w, h = sf.size
    for y in range(h):
        for x in range(w):
            if pt[x, y] >= 128:
                po[x, y] = (0, 0, 0, 0)
    n, stamped = 0, set()
    for y in range(h):
        for x in range(w):
            gx, gy = x - dx, y - dy
            if 0 <= gx < gm.size[0] and 0 <= gy < gm.size[1] and pgm[gx, gy] >= 128:
                po[x, y] = pd[gx, gy]
                n += 1
                stamped.add((x, y))
    import cleanup                                   # the old sash streaming out past her mask:
    for y in range(h):                               # cleanup.py would clear it to a hole later,
        for x in range(w):                           # so it is made a hole now and filled with aura
            if (x, y) not in stamped and cleanup.blue(po[x, y]):
                po[x, y] = (0, 0, 0, 0)
    holes = fill_holes(out, sf)
    os.makedirs(OUT, exist_ok=True)
    out.save(OUT + 'frame_%03d.png' % f)
    print('frame %d <- stamp %d shift %+d,%+d her mismatch %.2f: %d px, %d holes filled' % (f, g, dx, dy, miss, n, holes))


if __name__ == '__main__':
    fn = stamp if '--stamp' in sys.argv else graft
    for p in sys.argv[1].split(','):
        fn(*(int(v) for v in p.split(':')))
