"""The project's hooks into the core pipeline (see project.json "hooks").

masks         her figure in a stock frame, for `pxf remix` (core default: every opaque pixel).
              bodymask.figure (face -> body -> tights/boots -> the skirt between), the boss
              frames' own masks (bossmask.py), grown a few px over the shoulder armour's spikes
              and without limit over the dark stockings/boots in the lower half of her box.
after_remix   run on each frame right after remix, in this order:
              keep_effects  stock effect pixels back where the edit left holes
              boss_backfill the scene back behind her in the boss frames
              no_sash       the old blue skirt sash stripped
"""
import json, os, sys
from PIL import Image

from _paths import WORK


def effect(v):
    """slash arcs and the sash are purple / magenta / blue (blue well above green); the
    shoulder armour is grey, dark and red. Growing over an arc let remix clear it where
    the model had not drawn it: holes in the slash on 179, 192, 246."""
    import frame_out as F
    r, g, b = F.rgb(v)
    return b > g + 30


def masks(out, frames, grow=0):
    """grow: extend the figure up to this many px over the stock drawing (4-connected
    steps through opaque pixels only). The shoulder armour's spikes and jewel stick out of
    bodymask's figure; without this they stayed stock after the model removed the armour."""
    import qretex_form as QRF
    import frame_out as F
    import bodymask as BM
    fr = QRF.load()
    os.makedirs(out, exist_ok=True)
    for f in frames:
        w, h, px = fr[f]
        own = WORK + 'bossmask/frame_%03d.png' % f
        if os.path.exists(own):                     # anim 37: bodymask grabs the boss (bossmask.py)
            Image.open(own).convert('RGB').point(lambda v: 255 if v >= 128 else 0).save(
                os.path.join(out, 'frame_%03d.png' % f))
            print('frame %3d: bossmask' % f)
            continue
        r = BM.figure(fr[f])
        body = set(r[3]) if r else set()
        opaque = {i for i, v in enumerate(px) if v != QRF.KEY} if hasattr(QRF, 'KEY') else None
        if opaque is None:
            import frame_out as F
            opaque = {i for i, v in enumerate(px) if v != F.KEY}
        edge = set(body)
        for _ in range(grow):
            nxt = set()
            for i in edge:
                x, y = i % w, i // w
                for j in (i - 1 if x else -1, i + 1 if x < w - 1 else -1, i - w, i + w):
                    if 0 <= j < w * h and j in opaque and j not in body and not effect(px[j]):
                        nxt.add(j)
            body |= nxt
            edge = nxt
        # stockings and boots: bodymask missed her lower legs in 53 (light cones confused
        # it) and the boots in 325/330, so they stayed stock. Grow without limit over dark,
        # unsaturated pixels joined to the figure, in the lower 55% of her box only (the
        # katana and hair outlines above are left to the model's own drawing).
        bx, by, bw, bh = json.load(open(WORK + 'crops.json'))[str(f)]
        low = by + int(0.45 * bh)
        dark = lambda i: i in opaque and i // w >= low and bx <= i % w < bx + bw and \
            (lambda c: sum(c) < 200 and max(c) - min(c) < 60)(F.rgb(px[i]))
        stack = [i for i in body if i // w >= low]
        while stack:
            i = stack.pop()
            x = i % w
            for j in (i - 1 if x else -1, i + 1 if x < w - 1 else -1, i - w, i + w,
                      i - w - 1 if x else -1, i - w + 1 if x < w - 1 else -1,
                      i + w - 1 if x else -1, i + w + 1 if x < w - 1 else -1):
                if 0 <= j < w * h and j not in body and dark(j):
                    body.add(j)
                    stack.append(j)
        im = Image.new('RGB', (w, h))
        im.putdata([(255, 255, 255) if i in body else (0, 0, 0) for i in range(w * h)])
        im.save(os.path.join(out, 'frame_%03d.png' % f))
        print('frame %3d: figure %4d px' % (f, len(body)))


def keep_effects(path, f, d=None):
    """where the edit left a hole but the stock pixel is part of a magenta / purple slash
    (red and blue both well above green), put the stock pixel back: an arc passing behind
    her head is inside her figure mask, and the model drew background there (179). The
    blue sash has red near green, so it does not come back. Same for the stage light
    cones of anim 53, which bodymask swallowed into her figure."""
    from PIL import Image
    im = Image.open(path).convert('RGBA')
    st = Image.open(WORK + 'all/frame_%03d.png' % f).convert('RGBA').load()
    px, (w, h) = im.load(), im.size
    def cone(q):                        # stage light cones (53): cyan / yellow / white, g >= 240;
        return q[3] >= 128 and q[1] >= 240   # her brightest skin is (255,235,222)

    def lone_cone(x, y):
        """a cone pixel whose opaque 4-neighbours are cone too: the dotted light, not the
        white skirt trim, which always borders black skirt"""
        if not cone(st[x, y]):
            return False
        for i, j in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if 0 <= x + i < w and 0 <= y + j < h and st[x + i, y + j][3] >= 128 and not cone(st[x + i, y + j]):
                return False
        return True

    def dot(x, y):
        """an isolated stock pixel (no opaque 4-neighbour): dotted effects such as the
        magic circle in 147, drawn in plain browns that no colour rule can tell from her.
        Clothing is never a lone pixel."""
        return all(not (0 <= x + i < w and 0 <= y + j < h) or st[x + i, y + j][3] < 128
                   for i, j in ((1, 0), (-1, 0), (0, 1), (0, -1)))

    n = 0
    for y in range(h):
        for x in range(w):
            r, g, b, a = st[x, y]
            if px[x, y][3] < 128 and a >= 128 and ((r > g + 30 and b > g + 30) or lone_cone(x, y)
                                                   or dot(x, y)):
                px[x, y] = st[x, y]
                n += 1
    if n:
        im.save(path)
    return n


def boss_backfill(path, f, d=None):
    import bossmask
    return bossmask.backfill(path, f)


def no_sash(path, f, d=None):
    import nosash
    nosash.clean(path, path, f)
