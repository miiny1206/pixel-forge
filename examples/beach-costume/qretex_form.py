"""The rule painter, for the character's alternate form.

qretex.py paints the normal form: it finds her waist by the red skirt and measures everything
along her body from there, so the same body pixel is the same colour in every frame. The alternate-form
dress has no red skirt, so the landmark here is the dress itself: the largest piece of clothing
the outfit mask finds, whose centre of mass sits at her waist in every pose. The axis still runs
from her hair down to that centre and is snapped to an eighth of a turn, so standing frames are
exactly vertical and a knocked-down frame rotates with her.

Top above the waist, wrap below, bare skin outside - the same outfit as the normal form, painted
by the same rules, so the two costumes match.

    qretex_form.py preview <out.png> <frame,...>
"""
import os, sys, math, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
import frame_out as F, qmask as QM, legs as L, qretex as R

KEY = F.KEY
BAND = 15             # the alternate-form drawing is about half again as tall as the normal one
WRAP = 14
SHOULDER = 13
HALF = 17             # how wide her body is, either side of the axis
REACH = 42            # her legs swing much further out than that when she lunges, and the feet
                      # dropping out of reach is what left a boot on one frame and a bare foot
                      # on the next - the shimmer, in one pixel block
CUTS = [0.20, 0.34, 0.50]    # her dress is much darker than the normal form's white blouse


def effect(v):
    """Her slashes and the spotlight on her victory pose. Same test as the normal form, and for
    the same reason it has to keep the lightness bound: her geta and the dark blue of her hakama
    are in the same hue range, and without it one foot kept its clog while the other went bare."""
    h_, l, s_ = QM.hls(v)
    return 60 <= h_ <= 300 and s_ >= 0.10 and l >= 0.35


def level(v):
    r, g, b = [c / 255 for c in F.rgb(v)]
    import colorsys
    l = colorsys.rgb_to_hls(r, g, b)[1]
    return sum(1 for c in CUTS if l > c)


def load(name="char_form.spr", ark=None):
    """frames of a sheet from the pristine source archive"""
    if ark is None:
        from _paths import SOURCE_ARCHIVE as ark
    with zipfile.ZipFile(ark) as z:
        return F.frames(z.read(name))


def axis(fr, m):
    """Her body's direction, and the waist along it: the middle of the dress."""
    w, h, px = fr
    hx0 = head(fr)
    blobs = R.blobs(m, w, h)
    if hx0 is None or not blobs:
        return None
    near = [b for b in blobs
            if abs(sum(i % w for i in b) / len(b) - hx0[0]) < ARMS
            and abs(sum(i // w for i in b) / len(b) - hx0[1]) < TALL]
    if not near:
        return None                                    # nothing of hers in reach of her head
    big = [b for b in near if len(b) >= 60] or near

    def far(b):
        cx = sum(i % w for i in b) / len(b) - hx0[0]
        cy = sum(i // w for i in b) / len(b) - hx0[1]
        return cx * cx + cy * cy

    dress = min(big, key=far)                          # the cloth closest under her head. Biggest
                                                       # is not safe: standing on the demon she
                                                       # calls up, its helmet trim is bigger than
                                                       # she is, and the top landed on its forehead
    hx = hx0
    cx = sum(i % w for i in dress) / len(dress), sum(i // w for i in dress) / len(dress)
    dx, dy = cx[0] - hx[0], cx[1] - hx[1]
    if dx * dx + dy * dy < 4:
        return None
    a = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
    return cx, (math.cos(a), math.sin(a))


def outfit(fr, m0, ax):
    """Which pixels are her clothes.

    qmask.mask already knows: it drops her skin and her hair, and its opening step takes the
    katana and the scabbard back out, which matters far more here than on the normal form. What
    it does not know is her body's direction - it cuts by rows of the frame - so the band along
    her axis is applied on top of it, and below the wrap everything of hers that is not skin,
    hair or a glow counts as legwear whether the mask found it or not, because her tabi and her
    geta reach past the row where it stops looking.
    """
    w, h, px = fr
    (ox, oy), (ux, uy) = ax
    m = bytearray(w * h)
    add = bytearray(w * h)
    for i, v in enumerate(px):
        if v == KEY or QM.skin(v) or QM.hair(v) or effect(v):
            continue
        dx, dy = i % w - ox, i // w - oy
        u = round(dx * ux + dy * uy)
        if u < -(BAND + SHOULDER) or abs(-dx * uy + dy * ux) > (REACH if u > WRAP else HALF):
            continue
        if m0[i] or u > WRAP:
            m[i] = 1
        elif wear(v):
            add[i] = 1                                 # cloth the mask missed - see below
    for blob in R.blobs(add, w, h):
        if len(blob) > CLOTH or thin(blob, w):
            continue                                   # her katana: long and two pixels wide
        if not any(m[j] or (px[j] != KEY and QM.skin(px[j]))
                   for j in around(blob, w, h)):
            continue                                   # and not against what she wears or her
        for i in blob:
            m[i] = 1
    return trim(fr, m0, m)


CLOTH = 400           # the biggest piece the mask ever drops in one go


def wear(v):
    """Dark cloth or gold trim: what her dress is made of.

    qmask.mask erodes to get the katana out, and the erosion eats thin parts of the dress with
    it - the hem, the sash ends, a sleeve seen edge on. Those came out of the painting still
    black, which is the old outfit showing through in the middle of an attack.
    """
    h_, l, s_ = QM.hls(v)
    return not (QM.skin(v) or QM.hair(v) or effect(v)) and (l < 0.45 or s_ < 0.35 or QM.gold(v))


def thin(blob, w):
    xs = [i % w for i in blob]
    ys = [i // w for i in blob]
    dx, dy = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
    return min(dx, dy) <= 4 and max(dx, dy) >= 25


def around(blob, w, h):
    out = set()
    for i in blob:
        x, y = i % w, i // w
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h:
                out.add(ny * w + nx)
    return out - set(blob)


FOOT = 120            # the biggest a clog or a tabi gets, in pixels


def trim(base, m0, m):
    """Throw away what the legs rule swept up besides her feet.

    Below the wrap the rule takes everything of hers that is not skin, hair or a glow, because
    her clogs reach past where qmask.mask stops looking. Her sweeps and the petal clouds of her
    finisher are neither skin nor hair, and the pink and the grey of them are outside the hue
    band that effect() knows, and they are drawn all around her - so no distance from her body
    tells them apart. Their size does: a clog is a few dozen pixels stuck to her foot, a cloud is
    thousands, and a petal is loose in the air. So each piece the legs rule added on its own has
    to be small and has to touch her.
    """
    w, h, px = base
    extra = bytearray(1 if (m[i] and not m0[i]) else 0 for i in range(w * h))
    for blob in R.blobs(extra, w, h):
        if len(blob) <= FOOT and any(touches(base, m0, i) for i in blob):
            continue
        for i in blob:
            m[i] = 0
    return m


def touches(base, m0, i):
    """Is this pixel against her skin? Her clog is; the demon she calls up is not."""
    w, h, px = base
    x, y = i % w, i // w
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nx, ny = x + dx, y + dy
        if 0 <= nx < w and 0 <= ny < h:
            j = ny * w + nx
            if px[j] != KEY and QM.skin(px[j]):
                return True                            # her own foot, not the summon's armour
                                                       # leaning against her dress
    return False


BIG = 3500            # more pixels of cloth than she has anywhere in the sheet
ARMS = 40             # how far from her head her own clothes ever get, across her body
TALL = 70             # and down it. Her own pixels, not a share of the frame: the frames that
                      # hold a summon are three times as wide as the ones that hold only her


def head(fr):
    """Where her head is.

    Her hair is the obvious landmark, but hue alone finds the demon too: its shoulder armour is
    the same teal, and on the frames where she stands on it the "hair" came out on the demon and
    the outfit was painted onto its helmet. So the hair is split into pieces and the piece with
    the most skin against it wins - her face is against her hair, the armour is against armour.
    """
    w, h, px = fr
    m = bytearray(w * h)
    for i, v in enumerate(px):
        if v != KEY and QM.hair(v):
            m[i] = 1
    blobs = [b for b in R.blobs(m, w, h) if len(b) >= 20]
    if not blobs:
        return None

    def face(b):
        n = 0
        for i in b:
            x, y = i % w, i // w
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    v = px[ny * w + nx]
                    if v != KEY and QM.skin(v):
                        n += 1
                        break
        return n

    b = max(blobs, key=face)
    return sum(i % w for i in b) / len(b), sum(i // w for i in b) / len(b)


def mine(fr, m):
    """Drop the pieces of the outfit mask that are not hers.

    qmask.mask reads colour and rows of the frame, so on the frames where she calls the demon up
    it happily marks the demon's armour, and on the finisher it marks the petal cloud. Both are
    drawn away from her head and both are far bigger than anything she wears, so both go.
    """
    w, h, px = fr
    at = head(fr)
    if at is None:
        return m
    hx, hy = at
    m = bytearray(0 if (m[i] and px[i] != KEY and glow(px[i])) else m[i]
                  for i in range(w * h))               # cut the slash away from her clothes
    out = bytearray(w * h)
    for b in R.blobs(m, w, h):
        cx = sum(i % w for i in b) / len(b)
        cy = sum(i // w for i in b) / len(b)
        if len(b) > BIG or abs(cx - hx) > ARMS or abs(cy - hy) > TALL:
            continue
        if not any(against(fr, i) for i in b):         # she is inside her clothes: every piece
            continue                                   # of them lies against her skin or hair
        for i in b:
            out[i] = 1
    return out


def glow(v):
    """A lit effect rather than cloth: bright and saturated.

    Her dress is dark and her sash is barely saturated; the slash she swings is neither. This
    matters before the blobs are counted, not after: the slash is drawn touching her, so without
    it her dress and the slash are one piece, the piece is far too big to be clothes, and the
    whole outfit was thrown away - which is how a lunge came out with bare legs and the old black
    top still on.
    """
    h_, l, s_ = QM.hls(v)
    return l >= 0.35 and s_ >= 0.30 and not QM.gold(v) and not QM.skin(v)


def against(fr, i):
    w, h, px = fr
    x, y = i % w, i // w
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nx, ny = x + dx, y + dy
        if 0 <= nx < w and 0 <= ny < h:
            v = px[ny * w + nx]
            if v != KEY and (QM.skin(v) or QM.hair(v)):
                return True
    return False


def prep(fr):
    """The frame with her dress's tights, garters and thigh pads already off, its outfit mask,
    and her body axis. Split out from the painting so the bake can look at every frame's axis
    before it commits to one (a stripe pattern whose origin wanders flickers)."""
    base = L.polish(L.strip_gear(L.bare(fr)))          # tights, garters, pads, then stray specks
    m0 = mine(base, QM.mask(base))
    return base, m0, axis(base, m0)


def paint(fr, frac=None):
    base, m0, ax = prep(fr)
    return draw(base, m0, ax, frac)


def draw(base, m0, ax, frac=None):
    w, h, px = base
    if ax is None:
        return base
    (ox, oy), (ux, uy) = ax
    o = ox * ux + oy * uy
    if frac is not None:
        o = math.floor(o) + frac                       # the phase lock
    m = outfit(base, m0, ax)
    skin, garment = set(), []
    for i in range(w * h):
        if not m[i]:
            continue
        u = round((i % w) * ux + (i // w) * uy - o)
        if -BAND <= u <= WRAP:
            garment.append((i, u))
        else:
            skin.add(i)
    out = list(px)
    for i in skin:                                     # shoulders and legs: her own skin ramp
        out[i] = L.RAMP[min(3, level(px[i]) + 1)]
    for i, u in garment:
        ramp = R.BLUE if u < 0 and ((-u) // R.STRIPE) % 2 == 0 else R.WHITE
        out[i] = ramp[level(px[i])]
    return w, h, out


def main():
    a = sys.argv[1:]
    if len(a) < 3 or a[0] != "preview":
        print(__doc__)
        return 1
    import preview as T
    fr = load()
    tiles = []
    for k in [int(x) for x in a[2].split(",")]:
        w, h, out = paint(fr[k])
        tiles += [(w, h, [None if v == KEY else F.rgb(v) for v in fr[k][2]]),
                  (w, h, [None if v == KEY else F.rgb(v) for v in out])]
    T.sheet(a[1], tiles, Z=8)
    print(a[1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
