"""Sea spray on the beach effects: foam on the edges of the water, and droplets flung off them.

Works on anything already recoloured to sea water (beachfx.sea / beachsheets): a water pixel
that touches transparency is its edge and is whitened toward foam, and a few edge pixels throw
a 1-2 px droplet 2-5 px outward into the empty space. Deterministic (seeded by the frame), so
a re-bake draws the same spray, and no pixel outside `allowed` is ever touched.

    foam.apply(im, seed, allowed=None)    im: RGBA PIL image, in place; allowed(x, y) -> bool
"""
import colorsys, random

FOAM = (236, 248, 255)
DROP = ((255, 255, 255), (200, 234, 255), (150, 210, 250))


def water(p):
    if p[3] < 128:
        return False
    h, s, v = colorsys.rgb_to_hsv(p[0] / 255, p[1] / 255, p[2] / 255)
    return 190 <= h * 360 <= 235 and s >= 0.3


def apply(im, seed, allowed=None, rate=0.07, rim=True):
    px = im.load()
    w, h = im.size
    ok = allowed or (lambda x, y: True)
    rnd = random.Random(seed)
    edges = []
    for y in range(h):
        for x in range(w):
            if not water(px[x, y]) or not ok(x, y):
                continue
            out = [(dx, dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                   if not (0 <= x + dx < w and 0 <= y + dy < h) or px[x + dx, y + dy][3] < 128]
            if out:
                edges.append((x, y, out))
    for x, y, out in (edges if rim else ()):          # foam rim, lighter where the water is bright
        r, g, b, a = px[x, y]
        k = 0.45 + 0.35 * (max(r, g, b) / 255)
        px[x, y] = tuple(round(c + (f - c) * k) for c, f in zip((r, g, b), FOAM)) + (a,)
    n = 0
    for x, y, out in edges:
        if rnd.random() >= rate:
            continue
        dx, dy = rnd.choice(out)
        dist = rnd.randint(2, 5)
        jx, jy = (rnd.randint(-1, 1), 0) if dy else (0, rnd.randint(-1, 1))
        tx, ty = x + dx * dist + jx, y + dy * dist + jy
        col = rnd.choice(DROP) + (255,)
        for qx, qy in ((tx, ty),) + (((tx + dx, ty + dy),) if rnd.random() < 0.35 else ()):
            if 0 <= qx < w and 0 <= qy < h and px[qx, qy][3] < 128 and ok(qx, qy):
                px[qx, qy] = col
                n += 1
    return len(edges), n
