"""Outfit mask for the character's base form, the one the costume slot actually draws.

The alternate-form rules do not transfer: this outfit is a white blouse, a red skirt, striped socks and
pink shoes, and dropping those colours into a rule set repaints her eye whites and the blade.

There is a better source of truth here. char_costume.spr is the same character in the same poses
wearing the maid costume, and char.ani and char_costume.ani pair the frames one to one. Align the
two by their anchors and every pixel that differs is, by construction, something the costume
changed - the clothes. The costume also dyes her hair blonde, so the teal hair and her skin come
back out afterwards, and the usual opening drops the sword.

    qmask01.py preview <out.png> <frame,...>
"""
import os, sys, json, struct, zipfile, colorsys

HERE = os.path.dirname(os.path.abspath(__file__))
import frame_out as F, qmask as QM

KEY = F.KEY
# the mask is the difference between the normal sheet and the maid costume, and baking writes the
# beach outfit over that costume - so once a bake has run, the live ark can no longer produce the
# mask that made it. Always read the pair from the pristine source archive.
from _paths import SOURCE_ARCHIVE as ARK
FF = 21                                                    # frames per anim in both .Ani files


def hls(v):
    r, g, b = [c / 255 for c in F.rgb(v)]
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    return h * 360, l, s


def skin(v):
    """Her tan, which sits at hue 19-33. The sword's orange flare lands there too but has no
    blue in it at all, so a floor on blue keeps the two apart."""
    h, l, s = hls(v)
    return 14 <= h <= 42 and s > 0.35 and l > 0.30 and F.rgb(v)[2] >= 45


def hair(v):
    h, l, s = hls(v)
    return 130 <= h <= 215 and s > 0.12


def _ani(z, name):
    b = z.read(name)
    v = struct.unpack_from("<%di" % ((len(b) - 64) // 4), b, 64)
    a = v[1]
    assert 2 + a + 5 * a * FF == len(v), name
    return a, v[2:2 + a * FF], v[2 + a * FF + a:2 + a * FF + a + a * FF * 3]


def pairs():
    """normal frame -> (costume frame, normal anchor, costume anchor)."""
    with zipfile.ZipFile(ARK) as z:
        a1, i1, o1 = _ani(z, "char.ani")
        a2, i2, o2 = _ani(z, "char_costume.ani")
    out = {}
    for an in range(min(a1, a2)):
        for f in range(FF):
            k = an * FF + f
            x, y = i1[k], i2[k]
            if x < 0 or y < 0 or x in out:
                continue
            out[x] = (y, (o1[k * 3], o1[k * 3 + 2]), (o2[k * 3], o2[k * 3 + 2]))
    return out


def sprites():
    with zipfile.ZipFile(ARK) as z:
        return F.frames(z.read("char.spr")), F.frames(z.read("char_costume.spr"))


def diff(fr, cos, anchors):
    (cx1, cy1), (cx2, cy2) = anchors
    w, h, p = fr
    w2, h2, p2 = cos
    m = bytearray(w * h)
    for y in range(h):
        for x in range(w):
            if p[y * w + x] == KEY:
                continue
            X, Y = x - cx1 + cx2, y - cy1 + cy2
            o = p2[Y * w2 + X] if 0 <= X < w2 and 0 <= Y < h2 else KEY
            if o != p[y * w + x]:
                m[y * w + x] = 1
    return m


def mask(fr, cos, anchors, shoes=0.84):
    w, h, px = fr
    m = diff(fr, cos, anchors)
    for i, v in enumerate(px):
        if v == KEY or skin(v) or hair(v):
            m[i] = 0
    m = QM.opening(m, w, h)                                # the blade and the ribbon tail go
    for y in range(int(shoes * h), h):                     # soles are thin, add them back after
        for x in range(w):
            i = y * w + x
            v = px[i]
            if v != KEY and not skin(v) and not hair(v):
                m[i] = 1
    return m


def main():
    a = sys.argv[1:]
    if len(a) < 3 or a[0] != "preview":
        print(__doc__)
        return 1
    import preview as T
    nrm, cos = sprites()
    pr = pairs()
    tiles = []
    for k in [int(x) for x in a[2].split(",")]:
        y, an, ac = pr[k]
        f = nrm[k]
        w, h, px = f
        m = mask(f, cos[y], (an, ac))
        g = [None if v == KEY else F.rgb(v) for v in px]
        tiles += [(w, h, g), (w, h, [(255, 0, 255) if m[i] else g[i] for i in range(w * h)])]
    T.sheet(a[1], tiles, Z=6)
    print(a[1], "mask px:", [sum(mask(nrm[k], cos[pr[k][0]], (pr[k][1], pr[k][2])))
                             for k in [int(x) for x in a[2].split(",")]])
    return 0


if __name__ == "__main__":
    sys.exit(main())


RAMP = [0x7286, 0xd388, 0xed2c, 0xf631]                    # her tan, shadow -> highlight


def red(v):
    h, l, s = hls(v)
    return (h >= 330 or h <= 12) and s > 0.40 and 0.15 < l < 0.70


def hem(fr, m):
    """The bottom of the red skirt: below it the outfit is socks and shoes, which the beach
    version simply does not have, so nothing down there is worth spending the model on. Her shoes
    are pink and answer to the same colour test, so it is the largest red blob that counts, not
    the lowest red pixel."""
    w, h, px = fr
    seen = bytearray(w * h)
    best = None
    for i0 in range(w * h):
        if seen[i0] or not m[i0] or not red(px[i0]):
            continue
        st, blob = [i0], []
        seen[i0] = 1
        while st:
            i = st.pop()
            blob.append(i)
            x, y = i % w, i // w
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                j = ny * w + nx
                if 0 <= nx < w and 0 <= ny < h and not seen[j] and m[j] and red(px[j]):
                    seen[j] = 1
                    st.append(j)
        if best is None or len(blob) > len(best):
            best = blob
    return max(i // w for i in best) if best else int(0.62 * h)


def split(fr, cos, anchors):
    """(what the model paints, what gets turned into bare skin)."""
    w, h, px = fr
    m = mask(fr, cos, anchors)
    y0 = hem(fr, m)
    top = bytearray(w * h)
    legs = set()
    for i in range(w * h):
        if not m[i]:
            continue
        if i // w <= y0:
            top[i] = 1
        else:
            legs.add(i)
    return top, legs


def barelegs(fr, cells):
    """Socks and shoes off, shaded as her own legs - the same cylinder the alternate-form sheet uses.

    The stripes on the socks are black and run the whole way across the shin, so treating any
    dark pixel on the silhouette as outline kept every stripe. Only the two pixels that end a
    run can be outline; everything between them is fabric and becomes skin.
    """
    w, h, px = fr
    px = list(px)
    if not cells:
        return w, h, px
    def outer(i):
        x, y = i % w, i // w
        return any(not (0 <= x + dx < w and 0 <= y + dy < h) or px[(y + dy) * w + x + dx] == KEY
                   for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))
    for y in range(h):
        x = 0
        while x < w:
            i = y * w + x
            if i not in cells:
                x += 1
                continue
            x0 = x
            while x < w and (y * w + x) in cells:
                x += 1
            n = x - x0
            for k in range(n):
                j = y * w + x0 + k
                if k in (0, n - 1) and hls(px[j])[1] < 0.18 and outer(j):
                    continue                              # the drawn silhouette stays
                t = 2 if n <= 2 else (0 if k >= n - 1 else 1 if k >= n - 2 else 3)
                if hls(px[j])[1] > 0.60 and t < 3:
                    t += 1
                px[j] = RAMP[t]
    return w, h, px
