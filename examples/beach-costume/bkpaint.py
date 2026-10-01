"""Paint the masked garment black, by luminance rank. The recipe pxf recolor uses.

Every pixel keeps its place in the region's luminance order and is remapped onto a ramp, so
the artist's own form shading carries into the new cloth instead of being flattened - the lit
side of the bust stays the lit side of the fabric.

The thresholds are pinned, not split by population. A population split moves when the region
changes size, and this region changes size on every frame of every animation, so a population
split is a guarantee of flicker. Pinned cuts bin the same luminance to the same ramp step in
every one of the 350 frames.

Alpha is never written, so the silhouette and the .ani offsets are safe by construction.

    bkpaint.py preview <out.png> <frames>
    bkpaint.py check
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
import bikini as B, bkmask as M, frame_out as F, legs as L, qretex_form as QRF

KEY = F.KEY
def r565(h):
    """RGB888 hex -> the RGB565 word the .spr stores. The ramp is quoted in the pixel-forge
    README as 888 hex; feeding those words straight in tinted the cloth olive."""
    return ((h >> 19 & 31) << 11) | ((h >> 10 & 63) << 5) | (h >> 3 & 31)


RAMP = [r565(0x100808), r565(0x211410), r565(0x292429)]   # shadow, cloth, sheen
CUTS = (40.0, 78.0)                    # pinned luminance cuts, 0..255


SKIN = L.RAMP                          # her own skin, shadow -> highlight
OUTLINE = L.OUTLINE                     # the silhouette edge, the colour legs.py leaves it
LINE = 12                              # a contour is this much darker than both sides
DARK = 45                              # and no brighter than this


def contour(px, reg, i, w):
    """A line the artist drew inside the cloth: darker than the pixels on both sides. Kept as a
    line rather than shaded over, so the fold between her legs survives becoming skin - without
    it the kneeling frames collapse into one blob, which is what the shipped beach sprite does."""
    x = i % w
    l = B.lum(px[i])
    if l > DARK:
        return False          # a contour is dark in its own right, not merely darker than
    for a, b in ((i - 1, i + 1), (i - w, i + w)):   # its neighbours - without this every
        if a in reg and b in reg and abs(a % w - x) <= 1 and abs(b % w - x) <= 1 \
                and B.lum(px[a]) > l + LINE and B.lum(px[b]) > l + LINE:   # dip in the stock
            return True                                                     # shading became a
    return False                                                            # blotch on her leg


def skin_fill(px, reg, w, h, out):
    """Round the region like a limb: dark at both edges, lit a third of the way in from the
    left, which is where the artist puts the light on her face and arms."""
    d = B.depth(reg, w, h)
    for i in reg:
        if contour(px, reg, i, w):
            out[i] = OUTLINE
            continue
        if any(px[j] == KEY for j in B.nb4(i, w, h)):
            out[i] = OUTLINE
            continue
        dl = 0
        while (i - dl - 1) in reg and (i - dl - 1) % w < i % w:
            dl += 1
        dr = 0
        while (i + dr + 1) in reg and (i + dr + 1) % w > i % w:
            dr += 1
        run = dl + dr + 1
        k = 0 if min(dl, dr, d[i] - 1) == 0 else 1 if min(dl, dr, d[i] - 1) == 1 else 2
        if run >= 5 and 0.15 <= dl / run <= 0.45 and d[i] >= 2:
            k = 3
        out[i] = SKIN[k]


def paint(fr, z=None):
    """Zones are read off the SHIPPED frame, then applied to the bare-legged one. legs.bare
    already takes the thighhighs and boots off and re-shades them onto her own skin ramp,
    keeping the artist's light-to-dark order, and it decides by colour rather than position so
    a crouch, a jump and a run all come out the same. Reusing it beats anything measured here:
    feet sit on the .ani anchor line, where drift is the most visible thing in the game."""
    w, h, px = fr
    if z is None:
        z = M.build_zones(fr)
    if not z:
        return fr, 0
    out = list(L.polish(L.strip_gear(L.bare(fr)))[2])
    for i, v in enumerate(z):
        if v == M.CLOTH_L:
            l = B.lum(px[i])
            out[i] = RAMP[0 if l < CUTS[0] else 1 if l < CUTS[1] else 2]
    # only the midriff still needs inventing; the legs came back as skin from legs.bare
    sk = {i for i, v in enumerate(z) if v == M.SKIN_L and out[i] == px[i]}
    for reg in B.blobs(sk, w, h):
        skin_fill(px, reg, w, h, out)
    return (w, h, out), sum(1 for a, b in zip(px, out) if a != b)


def main():
    a = sys.argv[1:]
    fr = QRF.load()
    if a and a[0] == 'preview':
        import preview as T
        tiles = []
        for k in [int(x) for x in a[2].split(',')]:
            w, h, px = fr[k]
            (_, _, o), n = paint(fr[k])
            tiles += [(w, h, [None if v == KEY else F.rgb(v) for v in px]),
                      (w, h, [None if v == KEY else F.rgb(v) for v in o])]
        T.sheet(a[1], tiles, Z=int(os.environ.get('Z', 3)))
        print(a[1])
        return 0
    if a and a[0] == 'check':
        alpha = bins = 0
        tot = []
        for k, f in enumerate(fr):
            zz = M.build_zones(f)
            if not zz:
                continue
            (w, h, o), n = paint(f, zz)
            alpha += sum(1 for x, y in zip(f[2], o) if (x == KEY) != (y == KEY))
            bins += sum(1 for i, v in enumerate(o) if v != f[2][i] and v not in RAMP + SKIN + [OUTLINE, L.OUTLINE])
            tot.append(n)
        tot.sort()
        print('frames %d | painted px median %d min %d max %d' % (len(tot), tot[len(tot) // 2], tot[0], tot[-1]))
        print('alpha changed: %d | pixels written off the ramp: %d' % (alpha, bins))
        return 0
    print(__doc__)
    return 1


if __name__ == '__main__':
    sys.exit(main())
