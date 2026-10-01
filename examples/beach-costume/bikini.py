"""Black bikini for the character's alternate form, painted from rules.

What may change is decided per stock colour, what it becomes per body zone:

  candidate   dark and nearly grey (lightness < DARK, chroma <= CHROMA): her dress, sleeves,
              tights and boots. The slash arcs, the cape and the shadow of her hair are dark
              too but saturated, so they are never candidates - that alone was the peach blob
              painted over the slash on the old version.
  panel       near-white and grey: the white panel on her chest.
  trim        gold on or between candidates (stocking bands, the dress piping); goes with
              the cloth it sits on, or it would float on bare skin.

Zones along her body axis (qretex_form.axis: head -> middle of the dress, snapped to 45 deg):

  u < SKIRT_TOP     torso: panel -> bikini top (black ramp), candidates -> skin
  SKIRT_TOP..WRAP   skirt: stays exactly as drawn
  u > WRAP          legs: candidates -> skin
  |v| > HALF above the skirt: arms - gauntlets stay

When the axis cannot be found the frame is left as drawn. Every choice falls back to
"keep the artist's pixel", so a wrong guess shows the old outfit, never a blob.

Skin is shaded by depth inside the region it replaces (edge -> shadow), lifted one step where
the stock cloth was lighter than the region's median, so knees and shins keep the artist's
highlights, and one step toward the light on the left edge as on her face.

    bikini.py preview <out.png> <frame,...>
"""
import os, sys, math, colorsys

HERE = os.path.dirname(os.path.abspath(__file__))
import frame_out as F, qmask as QM, qretex_form as QRF, legs as L

KEY = F.KEY
DARK = 0.36
CHROMA = 40
NECK = 0.12          # along face -> feet, as a share of it
SKIRT = 0.40
LEGS = 0.55
HALF = 13
WARM = 8             # her skirt is the only warm dark cloth; tights, top and boots are neutral or cool
THIN = 2             # limbs are more than 2*THIN across
LINE = 12            # a contour is this much darker than both sides

SKIN = L.RAMP                                  # her own skin, shadow -> highlight
TOP = [0x1041, 0x2102, 0x2945, 0x39c7]         # black cloth, shadow -> sheen
OUT_SKIN = 0x4904                              # outline where bare skin meets the background


def c565(v):
    return F.rgb(v)


def info(v):
    r, g, b = c565(v)
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    return h * 360, l, s, max(r, g, b) - min(r, g, b)


_cls = {}


def cls(v):
    if v in _cls:
        return _cls[v]
    h, l, s, chroma = info(v)
    if QM.skin(v):
        c = 'skin'
    elif QM.hair(v):
        c = 'hair'
    elif l < DARK and chroma <= CHROMA:
        r, g, b = c565(v)
        c = 'skirt' if r - max(g, b) >= WARM and lum(v) >= 20 else 'cand'
    elif l > 0.55 and chroma <= 36:
        c = 'panel'
    elif QM.gold(v):
        c = 'gold'
    else:
        c = 'keep'
    _cls[v] = c
    return c


def nb4(i, w, h):
    x, y = i % w, i // w
    if x > 0: yield i - 1
    if x < w - 1: yield i + 1
    if y > 0: yield i - w
    if y < h - 1: yield i + w


def depth(region, w, h):
    """4-connected distance from the edge of the region, 1 on the edge."""
    d = {i: 0 for i in region}
    edge = [i for i in region if any(j not in region for j in nb4(i, w, h))
            or i % w in (0, w - 1) or i // w in (0, h - 1)]
    for i in edge:
        d[i] = 1
    front = edge
    k = 1
    while front:
        k += 1
        nxt = []
        for i in front:
            for j in nb4(i, w, h):
                if j in d and d[j] == 0:
                    d[j] = k
                    nxt.append(j)
        front = nxt
    return d


def lum(v):
    r, g, b = c565(v)
    return 0.299 * r + 0.587 * g + 0.114 * b


def nb8(i, w, h):
    x, y = i % w, i // w
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if (dx or dy) and 0 <= x + dx < w and 0 <= y + dy < h:
                yield i + dy * w + dx


def grow(seed, ok, w, h, conn=nb4):
    reg, st = set(seed), list(seed)
    while st:
        i = st.pop()
        for j in conn(i, w, h):
            if j not in reg and ok(j):
                reg.add(j)
                st.append(j)
    return reg


def blobs(sel, w, h):
    seen, out = set(), []
    for s in sel:
        if s not in seen:
            b = grow([s], lambda j: j in sel, w, h)
            seen |= b
            out.append(b)
    return out


def mid(b, w):
    return sum(i % w for i in b) / len(b), sum(i // w for i in b) / len(b)


def face(fr, c):
    """Her face: the skin against the hair piece that has the most skin against it. The hair
    centroid is no use as a head point, the ponytail drags it half a head sideways."""
    w, h, px = fr
    hair = [b for b in blobs({i for i, k in enumerate(c) if k == 'hair'}, w, h) if len(b) >= 20]
    best = None
    for b in hair:
        touch = {j for i in b for j in nb4(i, w, h) if c[j] == 'skin'}
        if best is None or len(touch) > len(best):
            best = touch
    if not best:
        return None
    sk = grow(best, lambda j: c[j] == 'skin', w, h)
    return mid(sk, w)


def axis(fr, c):
    """Face -> feet. Feet are the lowest cloth of her own body (8-connected to her face,
    the slash is never cloth so it does not join). Near-vertical snaps to vertical,
    otherwise to the nearest 45 degrees."""
    w, h, px = fr
    f = face(fr, c)
    if f is None:
        return None
    fi = int(f[1]) * w + int(f[0])
    body = grow([fi], lambda j: c[j] is not None and c[j] != 'keep' or c[j] == 'keep' and not QRF.glow(px[j]),
                w, h, nb8)
    cloth = [i for i in body if c[i] == 'cand']
    if not cloth:
        return None
    y1 = max(i // w for i in cloth)
    feet = mid([i for i in cloth if i // w >= y1 - 5], w)
    dx, dy = feet[0] - f[0], feet[1] - f[1]
    L_ = math.hypot(dx, dy)
    if L_ < 20:
        return None
    a = math.atan2(dy, dx)
    if abs(a - math.pi / 2) < math.radians(25):
        a = math.pi / 2
    else:
        a = round(a / (math.pi / 4)) * (math.pi / 4)
    return f, (math.cos(a), math.sin(a)), L_


def contour(px, reg, i, w, h):
    """A line the artist drew inside the cloth: darker than the pixels either side of it."""
    x, y = i % w, i // w
    l = lum(px[i])
    for a, b in ((i - 1, i + 1), (i - w, i + w)):
        if a in reg and b in reg and abs(a % w - x) <= 1 and abs(b % w - x) <= 1 \
                and lum(px[a]) > l + LINE and lum(px[b]) > l + LINE:
            return True
    return False


def paint(fr, ax=None):
    w, h, px = fr
    c = [None if v == KEY else cls(v) for v in px]
    if ax is None:
        ax = axis(fr, c)
    if ax is None:
        return fr, None
    (ox, oy), (ux, uy), span = ax
    # gold that sits on the cloth goes with it
    for i, k in enumerate(c):
        if k == 'gold' and sum(1 for j in nb4(i, w, h) if c[j] == 'cand') >= 2:
            c[i] = 'cand'

    # dark pieces that are mostly bordered by hair are its shadow
    for b in blobs({i for i, k in enumerate(c) if k == 'cand'}, w, h):
        edge = [c[j] for i in b for j in nb4(i, w, h) if j not in b]
        if edge and sum(1 for k in edge if k == 'hair') >= 0.4 * len(edge):
            for i in b:
                c[i] = 'keep'

    def zone(i):
        dx, dy = i % w - ox, i // w - oy
        t = (dx * ux + dy * uy) / span
        v = -dx * uy + dy * ux
        if t < NECK:
            return 'head'
        if t > LEGS:
            return 'legs'
        if abs(v) > HALF:
            return 'arms'
        return 'torso' if t < SKIRT else 'skirt'

    to_skin = set()
    to_top = []
    for i, k in enumerate(c):
        if k == 'cand' and lum(px[i]) < 20 and any(c[j] == 'skirt' for j in nb4(i, w, h)):
            continue                                 # the skirt's own outline
        if k == 'cand':
            z = zone(i)
            if z in ('legs', 'torso'):
                to_skin.add(i)
        elif k == 'panel' and zone(i) == 'torso':
            to_top.append(i)

    out = list(px)
    # bikini top: black ramp by the panel's own light, pinned cuts
    for i in to_top:
        l = lum(px[i])
        out[i] = TOP[0 if l < 150 else 1 if l < 200 else 2]
    # skin, region by region
    limbs = set()
    for reg in blobs(to_skin, w, h):
        d = depth(reg, w, h)
        core = {i for i in reg if d[i] > THIN}       # erode...
        limbs |= {j for i in core for j in grow([i], lambda j: j in reg and
                  abs(j % w - i % w) <= THIN and abs(j // w - i // w) <= THIN, w, h, nb8)}
    for reg in blobs(limbs, w, h):                   # ...and dilate: scabbards and straps drop out
        d = depth(reg, w, h)
        for i in reg:
            if contour(px, reg, i, w, h):
                out[i] = OUT_SKIN                    # the artist's own contour, kept as a contour
                continue
            if any(px[j] == KEY for j in nb4(i, w, h)):
                out[i] = OUT_SKIN
                continue
            x, y = i % w, i // w
            dl = 0
            while x - dl - 1 >= 0 and (i - dl - 1) in reg:
                dl += 1
            dr = 0
            while x + dr + 1 < w and (i + dr + 1) in reg:
                dr += 1
            m = min(dl, dr, d[i] - 1)
            k = 0 if m == 0 else 1 if m == 1 else 2
            run = dl + dr + 1
            if run >= 5 and 0.15 <= dl / run <= 0.45 and d[i] >= 2:
                k = 3                                # light from the left, as on her face
            out[i] = SKIN[k]
    return (w, h, out), ax


def main():
    a = sys.argv[1:]
    if len(a) < 3 or a[0] != 'preview':
        print(__doc__)
        return 1
    import preview as T
    fr = QRF.load()
    tiles = []
    for k in [int(x) for x in a[2].split(',')]:
        (w, h, out), ax = paint(fr[k])
        tiles += [(w, h, [None if v == KEY else F.rgb(v) for v in fr[k][2]]),
                  (w, h, [None if v == KEY else F.rgb(v) for v in out])]
    T.sheet(a[1], tiles, Z=int(os.environ.get('Z', 2)))
    print(a[1])
    return 0


if __name__ == '__main__':
    sys.exit(main())
