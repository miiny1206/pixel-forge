"""Paint the beach outfit with rules instead of a model, so every frame comes out the same.

The client has no layers: .spr frames are flat RGB565, .col is collision boxes, and nothing in
Client.exe loads a palette per item. Clothes are painted into each frame. That is why letting the
model redraw each frame on its own made her shimmer - 121 independent drawings of the same outfit
never agree, and at 12 frames a second the disagreement is the jitter.

So the outfit is painted by rule here. Which pixels are clothes is already known exactly (the
difference against the maid costume). What colour each one becomes is decided by two things that
do not change between frames:

  * where the pixel sits on her body, measured from the .Ani anchor, not from the frame - so a
    stripe stays on the same part of her when the frame grows, shrinks or shifts
  * how dark the artist drew it, cut at fixed thresholds - so shading survives and never drifts

Same body pixel, same colour, every frame. The legwear goes to bare skin the same way.

    qretex.py preview <out.png> <frame,...>
"""
import os, sys, math, colorsys

HERE = os.path.dirname(os.path.abspath(__file__))
import frame_out as F, qmask01 as Q1

KEY = F.KEY


def to565(r, g, b):
    return (r >> 3) << 11 | (g >> 2) << 5 | (b >> 3)


WHITE = [to565(*c) for c in ((120, 124, 140), (186, 196, 214), (228, 236, 246), (255, 255, 255))]
BLUE = [to565(*c) for c in ((8, 66, 120), (0, 110, 180), (16, 150, 214), (120, 206, 244))]
# the artist's red is darker than her white at the same place on the cloth, so one set of cuts
# would flatten one of them; each family is cut where its own four tones fall
CUTS = {"red": [0.22, 0.34, 0.46], "pale": [0.34, 0.58, 0.82]}
STRIPE = 2                                                 # stripe height in body pixels


def level(v):
    r, g, b = [c / 255 for c in F.rgb(v)]
    l = colorsys.rgb_to_hls(r, g, b)[1]
    return sum(1 for c in CUTS["red" if Q1.red(v) else "pale"] if l > c)


def effect(v):
    """The pale blue and violet her skill effects are drawn in. They are not saturated enough to
    be told apart from cloth by saturation alone, and painting them turned her green bamboo slash
    into a blue one - but nothing she wears is tinted toward green, blue or violet.

    They are all glows, so they are all light. Without that last condition the dark blue-grey
    shading of her socks and boots read as effect too, and the legwear stayed on her."""
    h_, l, s_ = Q1.hls(v)
    return 60 <= h_ <= 300 and s_ >= 0.10 and l >= 0.35


def cloth(v):
    """Her clothes are white, grey or red; her skin, her hair and her effects are neither."""
    h_, l, s_ = Q1.hls(v)
    if Q1.skin(v) or Q1.hair(v) or effect(v):
        return False
    return Q1.red(v) or (s_ < 0.25 and l > 0.25) or (s_ < 0.45 and l > 0.70)


def outfit(fr, ax):
    """Which pixels are clothes.

    This used to be the difference against the maid costume: exact, but it only exists for the
    121 frames the two .Ani files pair up, and the town sheet draws 56 frames that are not among
    them - which is why she still ran around town in socks. It also saw nothing where the two
    outfits agreed, her white sleeves above all, and a flood through cloth colours does no better
    because her knee and her ankle show skin and cut the legwear off from the skirt.

    So cloth is decided by colour, and kept only between her shoulders and her feet, measured
    along her body. Her eye whites are the same white as the blouse but sit well above the
    shoulder line, and the red ribbon in her hair sits above it too, so neither is ever touched.
    """
    w, h, px = fr
    (ox, oy), (ux, uy), top = ax
    m = bytearray(w * h)
    for i, v in enumerate(px):
        if v == KEY:
            continue
        dx, dy = i % w - ox, i // w - oy
        u = round(dx * ux + dy * uy - top)             # zero at the top edge of the belt
        out_of = abs(-dx * uy + dy * ux)               # how far out from her body
        if u < -(BAND + SHOULDER) or out_of > (REACH if u > WRAP else HALF):
            continue                                   # her skill effects sweep far wider than
                                                       # she is, and the white ones are the same
                                                       # white as the blouse - only the distance
                                                       # tells them apart
        h_, l, s_ = Q1.hls(v)
        if u > WRAP:
            # below the wrap there is nothing of hers but legs, so everything that is not her
            # skin or her hair is legwear - the black stripes that are too dark to read as cloth,
            # the greys between them, the pink shoes. Her sword's glow is the one thing down
            # there that is not fabric, and it is far more saturated than anything she wears.
            if (not Q1.skin(v) and not Q1.hair(v) and not effect(v)
                    and (s_ < 0.45 or Q1.red(v))):
                m[i] = 1
        elif cloth(v):
            m[i] = 1
    return m


def blobs(m, w, h):
    seen = bytearray(w * h)
    out = []
    for i0 in range(w * h):
        if seen[i0] or not m[i0]:
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
                if 0 <= nx < w and 0 <= ny < h and not seen[j] and m[j]:
                    seen[j] = 1
                    st.append(j)
        out.append(blob)
    return out


def legwear(fr, m, wl):
    """The socks and shoes. Her ankle shows skin, which cuts the outfit in two there, so this
    goes by pieces: any piece sitting entirely below the waist is legwear. The waist is where the
    skirt's red starts, so the skirt itself always reaches it and is never mistaken for a sock -
    and her shoes, which are pink and would otherwise read as skirt, are caught correctly. A
    crouch works like a stand because nothing here is a fixed height."""
    w, h, px = fr
    got = set()
    for blob in blobs(m, w, h):
        if min(i // w for i in blob) > wl:
            got |= set(blob)
    return got


SNAP = 8               # directions the body axis may take: every 45 degrees


def axis(fr):
    """Her body's own up-down direction, and where the waist sits along it.

    Rows of the frame are not her body: knocked down and drawn lying on her side, a band of rows
    cuts her across instead of along and the top lands on her hip. So the direction comes from
    the drawing - the middle of her hair down to the middle of the skirt - and is then snapped to
    the nearest eighth of a turn, so every standing frame is exactly vertical and a centroid that
    wobbles half a pixel cannot tilt the outfit. The origin is the edge of the belt along that
    direction, an integer the artist drew, not a centre of mass that drifts with her hair.
    """
    import math
    w, h, px = fr
    hair = [i for i, v in enumerate(px) if v != KEY and Q1.hair(v)]
    red = [i for i, v in enumerate(px) if v != KEY and Q1.red(v) and not Q1.skin(v)]
    if not hair or not red:
        return None
    rm = bytearray(w * h)
    for i in red:
        rm[i] = 1
    red = max(blobs(rm, w, h), key=len)                 # the belt, not the red ribbon in her hair
    hx = sum(i % w for i in hair) / len(hair), sum(i // w for i in hair) / len(hair)
    rx = sum(i % w for i in red) / len(red), sum(i // w for i in red) / len(red)
    dx, dy = rx[0] - hx[0], rx[1] - hx[1]
    if dx * dx + dy * dy < 4:
        return None
    a = round(math.atan2(dy, dx) / (2 * math.pi / SNAP)) * (2 * math.pi / SNAP)
    ux, uy = math.cos(a), math.sin(a)
    cx = sum(i % w for i in red) / len(red)
    cy = sum(i // w for i in red) / len(red)
    top = min((i % w - cx) * ux + (i // w - cy) * uy for i in red)   # where the belt starts
    return (cx, cy), (ux, uy), top


# How far the top reaches up from the belt and the wrap down from it, in her own pixels. These
# are constants, not a share of each frame: a share of a frame changes with every crouch, and the
# outfit growing and shrinking frame to frame is exactly the shimmer this file exists to remove.
BAND = 11
WRAP = 10
SHOULDER = 10         # how far above the top her shoulders are bared
HALF = 13             # how wide her body is, either side of the axis
REACH = 22            # and how far her legs get from it when she kicks or is thrown. It cannot
                      # be opened up much: the white sweep of her attacks is the same white as
                      # the blouse and no colour test tells them apart - only this distance does,
                      # and at 30 whole sweeps were painted as skin


def lock(ax, frac):
    """Put the origin on a fixed fraction of a pixel.

    The waist is found to within about half a pixel - the belt's top edge is an integer, but her
    body axis and the centroid it starts from are not - and half a pixel is enough to move
    round(u) by one. One pixel moves the stripe pattern by a whole stripe and the hem of the wrap
    by a row, which is the unevenness left in the first in-game test. So the bake measures the
    fraction once per animation and hands it to every frame of that animation: inside an
    animation the outfit cannot move at all.
    """
    (ox, oy), (ux, uy), top = ax
    o = ox * ux + oy * uy + top
    if frac is not None:
        o = math.floor(o) + frac
    return (ox, oy), (ux, uy), o - (ox * ux + oy * uy)


def origin(ax):
    (ox, oy), (ux, uy), top = ax
    return ox * ux + oy * uy + top


def paint(fr, frac=None):
    """Her frame with the beach outfit painted in.

    Everything is decided against the waist and measured along her own axis, so the same pixel of
    her body gets the same colour in every frame no matter what the pose does to the drawing.
    A band above the waist is the striped top, a band below it is the wrap, and the rest of the
    old outfit becomes skin: bare shoulders above, bare legs and feet below.
    """
    w, h, px = fr
    ax = axis(fr)
    if ax is None:                                     # nothing to measure against: leave as is
        return w, h, list(px)
    ax = lock(ax, frac)
    m = outfit(fr, ax)
    (ox, oy), (ux, uy), top = ax
    band, wrap = BAND, WRAP
    skin, garment = set(), []
    for i in range(w * h):
        if not m[i] or px[i] == KEY:
            continue
        u = round((i % w - ox) * ux + (i // w - oy) * uy - top)   # along her body, belt at zero
        if -band <= u <= wrap:
            garment.append((i, u))
        elif u > wrap or u >= -band - SHOULDER:
            skin.add(i)                                # bare shoulders above, bare legs below
                                                       # and anything further up - the ribbon in
                                                       # her hair - is left exactly as drawn
    out = Q1.barelegs(fr, skin)[2]
    for i, u in garment:
        ramp = BLUE if u < 0 and ((-u) // STRIPE) % 2 == 0 else WHITE
        out[i] = ramp[level(px[i])]
    return w, h, out


def main():
    a = sys.argv[1:]
    if len(a) < 3 or a[0] != "preview":
        print(__doc__)
        return 1
    import preview as T
    nrm, cos = Q1.sprites()
    pr = Q1.pairs()
    tiles = []
    for k in [int(x) for x in a[2].split(",")]:
        w, h, out = paint(nrm[k])
        tiles += [(w, h, [None if v == KEY else F.rgb(v) for v in nrm[k][2]]),
                  (w, h, [None if v == KEY else F.rgb(v) for v in out])]
    T.sheet(a[1], tiles, Z=10)
    print(a[1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
