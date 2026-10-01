"""Outfit mask for locked editing: the pixels the model is allowed to repaint.

frame_out.clothing_mask finds the solid dress blobs. On its own it missed the thin gold trim
(1-2px lines die in its erosion step) and its skin test counted that gold as skin. Here:
  * skin is only the pink ramp (hue <= 15 or >= 345), so gold is no longer skin
  * gold trim within reach of the dress blobs is added back after the erosion
  * shoes: dark pixels in the bottom band become editable so sandals can be drawn
  * skin and hair are always removed, so hands, arms, legs and face stay the artist's

    qmask.py preview <out.png> <frame,...>
"""
import sys, colorsys
import frame_out as F, legs as L

KEY = F.KEY


def hls(v):
    r, g, b = [c / 255 for c in F.rgb(v)]
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    return h * 360, l, s


def skin(v):
    h, l, s = hls(v)
    return (h <= 15 or h >= 345) and s > 0.25 and l > 0.35


def hair(v):
    h, l, s = hls(v)
    return 140 <= h <= 210 and s > 0.20


def gold(v):
    h, l, s = hls(v)
    return 16 <= h <= 50 and s >= 0.45 and l < 0.85


def blue(v):
    h, l, s = hls(v)
    return 200 <= h <= 250 and s > 0.30 and l < 0.75 and not hair(v)


_base = F.is_cloth
def cloth(v):
    return (_base(v) or blue(v) or gold(v)) and not skin(v) and not hair(v)


def near(m, w, h, r):
    out = bytearray(m)
    for _ in range(r):
        g = bytearray(out)
        for y in range(h):
            for x in range(w):
                if out[y * w + x]:
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < w and 0 <= ny < h:
                            g[ny * w + nx] = 1
        out = g
    return out


def opening(m, w, h, r=2):
    """Erode then dilate: thin appendages die, thick areas survive. This is what takes the katana
    and its scabbard back out of the mask while leaving the dress and its trim in."""
    e = bytearray(m)
    for _ in range(r):
        g = bytearray(e)
        for y in range(h):
            for x in range(w):
                if e[y * w + x] and not all(0 <= x + dx < w and 0 <= y + dy < h and e[(y + dy) * w + x + dx]
                                            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                    g[y * w + x] = 0
        e = g
    d = near(e, w, h, r)
    return bytearray(1 if (d[i] and m[i]) else 0 for i in range(w * h))


def mask(fr, top=0.22, bot=0.84, shoes=0.80):
    w, h, px = fr
    F.is_cloth = cloth
    try:
        m = bytearray(F.clothing_mask(fr, top, bot))
    finally:
        F.is_cloth = _base
    reach = near(m, w, h, 4)
    for y in range(h):
        for x in range(w):
            i = y * w + x
            v = px[i]
            if v == KEY:
                continue
            if top * h <= y <= bot * h and reach[i] and (gold(v) or blue(v)):
                m[i] = 1                                   # thin trim the erosion dropped
    m = opening(m, w, h)                                   # sword and scabbard back out
    for y in range(int(shoes * h), h):                     # boots and soles are thin; add them after
        for x in range(w):
            i = y * w + x
            v = px[i]
            if v != KEY and hls(v)[1] < 0.30 and not skin(v) and not hair(v):
                m[i] = 1
    for i, v in enumerate(px):
        if v != KEY and (skin(v) or hair(v)):
            m[i] = 0
    return m


if __name__ == "__main__":
    import preview as T
    fr = L.load()
    tiles = []
    for k in [int(x) for x in sys.argv[3].split(",")]:
        f = L.bare(fr[k]); w, h, px = f
        m = mask(f)
        g = [None if v == KEY else F.rgb(v) for v in px]
        tiles += [(w, h, g), (w, h, [(255, 0, 255) if m[i] else g[i] for i in range(w * h)])]
    T.sheet(sys.argv[2], tiles, Z=5)
    print(sys.argv[2])
