"""Beach colours for the effects drawn inside her own sheet (slash arcs, the ultimate's aura,
petals, the ground-slam magic circle): magenta / pink / purple -> sea water (navy .. ocean blue ..
azure, pale pinks to foam), the brightness kept, so the shapes and the animation are exactly the artist's.

Pixels outside her figure (her remix mask grown by 2 px) are touched, and inside it only the
effect joined to them - her skin, eyes, bikini and the tassel stay. Runs on bake_all/ from finish.py, before bkbake writes the sheet,
so it only reaches char_form_beach.spr (worn with the costume), never the stock sheet.

    beachfx.py [frame,...]           bake_all/ in place
    beachfx.py preview <out.png> <frame,...>
"""
import colorsys, glob, os, sys
from PIL import Image, ImageFilter

from _paths import WORK as HERE
OUT = HERE + 'bake_all/'


# the sheathed sword's greyish purples: outside her mask when it trails behind her (the
# dash), where they were taken for effect and turned sea blue -- she "glowed" while running.
# The effects themselves are saturated magentas and (24,0,57); none of these.
SWORD = {(66, 56, 82), (33, 24, 49), (107, 97, 123), (49, 40, 140)}


def sea(p):
    r, g, b, a = p
    if a < 128 or p[:3] in SWORD:
        return p
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    h *= 360
    if not (255 <= h <= 350 and s >= 0.25):
        return p
    # sea water, graded by brightness (2026-09-26: the user wanted real ocean blue, not the
    # turquoise of a hue shift): dark -> deep navy, mid -> ocean blue, bright -> azure,
    # the palest pinks (low saturation, bright) -> white foam with a blue tint
    # lighter sea (2026-09-27, the user found the navy too heavy): shallow water, the darks
    # a clear mid blue, the lights azure/turquoise
    t = v                                            # 0 dark .. 1 bright
    nh = 208 - 16 * t                                # blue 208 .. turquoise-azure 192
    ns = 0.72 - 0.3 * t if s > 0.35 else 0.14        # pale pinks become foam
    nv = 0.55 + 0.45 * t if s > 0.35 else max(v, 0.92)
    nr, ng, nb = colorsys.hsv_to_rgb(nh / 360, ns, nv)
    return (round(nr * 255), round(ng * 255), round(nb * 255), a)


def figure(f):
    for p in (glob.glob(HERE + 'bossmask/frame_%03d.png' % f) +
              glob.glob(HERE + 'runs/*/f%03d/masks/frame_%03d.png' % (f, f))):
        return Image.open(p).convert('L').filter(ImageFilter.MaxFilter(5))
    import json
    if str(f) not in json.load(open(HERE + 'crops.json')):
        im = Image.open(HERE + 'all/frame_%03d.png' % f)
        return Image.new('L', im.size, 0)            # no crop box: an effect-only frame (189)
    from pxf_pipeline.crops import figure_mask
    return figure_mask(f).filter(ImageFilter.MaxFilter(5))


def recolour(im, f):
    m = figure(f).load()
    px = im.load()
    w, h = im.size
    n, done = 0, []
    for y in range(h):
        for x in range(w):
            if m[x, y] >= 128:
                continue
            q = sea(px[x, y])
            if q != px[x, y]:
                px[x, y] = q
                n += 1
                done.append((x, y))
    # inside her margin: effect pixels joined (8-connected) to the recoloured effect outside -
    # an arc passing behind her, aura touching her legs. Her eyes and skin never join it.
    seen = set(done)
    while done:
        x, y = done.pop()
        for i in (-1, 0, 1):
            for j in (-1, 0, 1):
                t = (x + i, y + j)
                if t in seen or not (0 <= t[0] < w and 0 <= t[1] < h):
                    continue
                seen.add(t)
                q = sea(px[t])
                if q != px[t]:
                    px[t] = q
                    n += 1
                    done.append(t)
    import foam                                      # sea spray: foam rims and droplets
    foam.apply(im, f, lambda x, y: m[x, y] < 128)
    return n


def main():
    a = sys.argv[1:]
    if a and a[0] == 'preview':
        cells = []
        from pxf_pipeline import review                                # the frames as gathered, before beachfx
        src = review.finished()
        for f in [int(v) for v in a[2].split(',')]:
            im = Image.open(src.get(f, HERE + 'all/frame_%03d.png' % f)).convert('RGBA')
            new = im.copy(); recolour(new, f)
            c = Image.new('RGB', (im.width * 2 + 4, im.height), (40, 40, 40))
            for k, x in enumerate((im, new)):
                bg = Image.new('RGBA', im.size, (230, 200, 140, 255)); bg.alpha_composite(x)
                c.paste(bg, (k * (im.width + 4), 0))
            cells.append(c)
        W = sum(c.width + 8 for c in cells); H = max(c.height for c in cells)
        o = Image.new('RGB', (W, H), (20, 20, 20)); x = 0
        for c in cells:
            o.paste(c, (x, 0)); x += c.width + 8
        s = min(1.5, 2400 / W)
        o.resize((int(W * s), int(H * s)), Image.NEAREST).save(a[1])
        return
    fr = [int(v) for v in a[0].split(',')] if a else \
        sorted(int(os.path.basename(p)[6:9]) for p in glob.glob(OUT + 'frame_*.png'))
    tot = 0
    for f in fr:
        im = Image.open(OUT + 'frame_%03d.png' % f).convert('RGBA')
        n = recolour(im, f)
        if n:
            im.save(OUT + 'frame_%03d.png' % f)
            tot += n
    print('beachfx: %d frame(s), %d effect px recoloured' % (len(fr), tot))


if __name__ == '__main__':
    main()
