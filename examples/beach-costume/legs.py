"""Take the character's black thighhighs and boots off, deterministically.

Nobody wears tights to the beach, and the AI is worst exactly where they are: feet sit on the
anchor line, so any drift there is obvious. So this is pure palette work.

Measured over the idle frames, six colours belong to the tights and boots and appear essentially
nowhere else on her (20c6/20e6 boot navy, 4a49/4a69 grey highlight, 2905/2925 dark grey). Those
are the seeds. From them the region grows outward through the dark tones the tights share with
the rest of the outfit (the near-black she is outlined with, the boot midtones), bounded so it
cannot run up into the skirt. Every pixel it claims is re-shaded onto her own skin ramp, keeping
the artist's light-to-dark order, and the outline around the silhouette is left black.

Colour decides, not position, so a crouch, a jump and a run all come out the same.

    legs.py preview <out.png> [frame,frame,...]
"""
import os, sys, zipfile, colorsys

HERE = os.path.dirname(os.path.abspath(__file__))
import frame_out as F

KEY = F.KEY
# learned by `legs.py learn`: the 96 frames where both legs come out as their own blob give a
# per-colour count of "inside the legs" vs "everywhere else". >=83% of one colour's pixels being
# in a leg makes it a seed; the rest of the darks are only followed, never started from.
SEED = {0x41cb, 0x2906, 0x20e6, 0x3986, 0x426a, 0x4a49, 0x18c5, 0x20c5, 0x4a69}
GROW = SEED | {0x20c6, 0x1085, 0x2124, 0x3145, 0x3a09, 0x2905, 0x1083, 0x1041, 0x0841,
               0x51c7, 0x0842, 0x3124, 0x41c9, 0x3944, 0x2926, 0x41ca, 0x6acd, 0x0000,
               0x6aed, 0x2925, 0x1884, 0x1082, 0x1842, 0x1904, 0x1905}
REACH = 24                                                 # how far the grow may spread
RAMP = [0x8228, 0xc32b, 0xf4d1, 0xfe36]                    # her skin, shadow -> highlight
# brightness cuts measured over the whole sheet. They used to sit too low, so nearly every tight
# pixel landed on the darkest skin tone and the calves came out muddy brown in game.
CUTS = [0.040, 0.120, 0.200]
OUTLINE = 0x1041


def lum(v):
    r, g, b = [c / 255 for c in F.rgb(v)]
    return colorsys.rgb_to_hls(r, g, b)[1]


def region(fr):
    """Seed on the tights' own colours, then spread a bounded distance through shared darks."""
    w, h, px = fr
    cur = {i for i, v in enumerate(px) if v in SEED}
    got = set(cur)
    for _ in range(REACH):
        nxt = set()
        for i in cur:
            x, y = i % w, i // w
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if not (0 <= nx < w and 0 <= ny < h):
                    continue
                j = ny * w + nx
                if j not in got and px[j] in GROW:
                    got.add(j)
                    nxt.add(j)
        if not nxt:
            break
        cur = nxt
    return got


def rim(cells, w, h):
    """Cells with a neighbour outside the region: the drawn outline, which must survive."""
    out = set()
    for i in cells:
        x, y = i % w, i // w
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if not (0 <= nx < w and 0 <= ny < h) or ny * w + nx not in cells:
                out.add(i)
                break
    return out


def silhouette(cells, w, h, px):
    """Region cells that touch transparency: the real edge of her body.

    `rim` is every cell with a neighbour outside the region, which includes the boundary the
    region shares with the skirt above it and the one the two legs share with each other.
    Keeping OUTLINE on all of those drew hard black strokes down the middle of the bare
    thighs and across the shins - the single ugliest thing about the costume in game. A line
    only reads as a drawn edge where there is nothing behind it."""
    out = set()
    for i in cells:
        x, y = i % w, i // w
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if not (0 <= nx < w and 0 <= ny < h) or px[ny * w + nx] == KEY:
                out.add(i)
                break
    return out


def bare(fr, edge_only=False):
    """Return the frame with the tights and boots re-shaded as bare skin.

    With `edge_only`, black survives only on the silhouette - see `silhouette`.

    The tights are near-black and nearly flat, so mapping their brightness onto the skin ramp
    gave one flat mid-tone: in game the calves read as brown boots she never took off. There is
    no frame anywhere on the sheet where these legs are bare, so there is nothing to copy from;
    the shading has to be built. Her own art lights everything from the upper left, so each
    horizontal run of reclaimed pixels is shaded as a cylinder - lit across most of its width,
    one step of shadow, then the darkest tone against the right edge - and whatever the tights
    had as a highlight is allowed to pull a pixel one step brighter, which keeps the knees and
    the creases the artist drew from flattening out.
    """
    w, h, px = fr
    px = list(px)
    cells = region((w, h, px))
    if not cells:
        return w, h, px
    edge = silhouette(cells, w, h, px) if edge_only else rim(cells, w, h)
    keep = {i for i in edge if px[i] == OUTLINE}
    for y in range(h):
        x = 0
        while x < w:
            i = y * w + x
            if i not in cells or i in keep:
                x += 1
                continue
            x0 = x
            while x < w and (y * w + x) in cells and (y * w + x) not in keep:
                x += 1
            run = range(x0, x)
            n = x - x0
            for k, xx in enumerate(run):
                j = y * w + xx
                if n <= 2:
                    t = 2                      # too thin to shade: her base tone
                elif k >= n - 1:
                    t = 0                      # the edge that faces away from the light
                elif k >= n - 2:
                    t = 1
                else:
                    t = 3
                if lum(px[j]) > 0.25 and t < 3:
                    t += 1                     # a highlight the artist drew stays a highlight
                px[j] = RAMP[t]
    return w, h, px


def load(spr="char_form.spr"):
    from _paths import SOURCE_ARCHIVE
    return F.frames(zipfile.ZipFile(SOURCE_ARCHIVE).read(spr))


def main():
    a = sys.argv[1:]
    if len(a) < 2 or a[0] != "preview":
        print(__doc__)
        return 1
    from preview import view_sheet as sheet
    fr = load()
    picks = [int(x) for x in a[2].split(",")] if len(a) > 2 else [0, 1, 2, 38, 39, 40]
    sheet([fr[i] for i in picks] + [bare(fr[i]) for i in picks], a[1], scale=4)
    print("preview ->", a[1])
    return 0


if __name__ == "__main__":
    sys.exit(main())


def _skin(v):
    r, g, b = [c / 255 for c in F.rgb(v)]
    hh, l, s = colorsys.rgb_to_hls(r, g, b)
    return (hh * 360 <= 15 or hh * 360 >= 345) and s > 0.25 and l > 0.35


def strip_gear(fr, _=0):
    """Her thighs still carry the dress's gold garters and grey pads. Both are islands sitting in
    bare skin, so a gold or grey blob whose border is mostly skin becomes skin too. The sword's
    gold fittings border the dark blade instead, so they survive."""
    w, h, px = fr
    px = list(px)
    def family(v, y=0):
        r, g, b = [c / 255 for c in F.rgb(v)]
        hh, l, s = colorsys.rgb_to_hls(r, g, b)
        hh *= 360
        if 16 <= hh <= 50 and s >= 0.40:
            return "gold"
        if s < 0.22 and l > 0.22:
            return "pad"
        if y >= 0.82 * h and l < 0.32 and not (hh <= 15 or hh >= 345):
            return "shoe"          # the dress's boots, where the model did not draw sandals
        return None
    seen = bytearray(w * h)
    for i0 in range(w * h):
        if seen[i0] or px[i0] == KEY:
            continue
        fam = family(px[i0], i0 // w)
        if not fam:
            continue
        st, blob, border = [i0], [], []
        seen[i0] = 1
        while st:
            i = st.pop()
            blob.append(i)
            x, y = i % w, i // w
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if not (0 <= nx < w and 0 <= ny < h):
                    continue
                j = ny * w + nx
                if px[j] == KEY:
                    continue
                if family(px[j], ny) == fam:
                    if not seen[j]:
                        seen[j] = 1
                        st.append(j)
                else:
                    border.append(px[j])
        if len(blob) > (700 if fam == "shoe" else 400) or not border:
            continue
        need = 0.30 if fam == "shoe" else 0.55       # a boot only touches the ankle, so it has
        if sum(1 for v in border if _skin(v)) / len(border) < need:   # less skin around it
            continue
        for i in blob:
            r, g, b = [c / 255 for c in F.rgb(px[i])]
            px[i] = RAMP[sum(1 for c in CUTS if colorsys.rgb_to_hls(r, g, b)[1] > c)]
    return w, h, px


def despeckle(fr, region):
    """Single stray pixels the model left inside the outfit: a pixel matching none of its four
    neighbours takes the most common neighbour instead."""
    w, h, px = fr
    px = list(px)
    for i in region:
        x, y = i % w, i // w
        if px[i] == KEY:
            continue
        nb = [px[(y + dy) * w + x + dx] for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
              if 0 <= x + dx < w and 0 <= y + dy < h]
        if nb and px[i] not in nb:
            px[i] = max(set(nb), key=nb.count)
    return w, h, px


def polish(fr):
    """Single leftover pixels of the pads and boot trim, the ones too small to be a blob: a
    non-skin pixel with at least three skin neighbours takes her skin instead. The sword and the
    tassels sit against dark, so they never reach three."""
    w, h, px = fr
    px = list(px)
    out = list(px)
    for i, v in enumerate(px):
        if v == KEY or _skin(v) or v in RAMP:
            continue
        x, y = i % w, i // w
        nb = [px[(y + dy) * w + x + dx] for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
              if 0 <= x + dx < w and 0 <= y + dy < h]
        sk = [n for n in nb if _skin(n) or n in RAMP]
        if len(sk) >= 3:
            out[i] = max(set(sk), key=sk.count)
    return w, h, out
