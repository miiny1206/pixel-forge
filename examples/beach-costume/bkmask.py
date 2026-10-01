"""Per-frame garment masks for the character's alternate-form sheet, for pxf remix / recolor.

The mask says which pixels may be repainted. Everything outside it is copied from the shipped
art, so the silhouette and the .ani offsets cannot move whatever is done inside.

Where the frame of reference comes from
---------------------------------------
Her face, found from the hair piece with the most skin near it. Measured over the sheet this
lands on 351 of the 358 frames that hold a character; the seven it misses are the summon frames
where she is a thumbnail on a demon's head. Skin alone would find her hands too, and the hair
centroid is dragged half a head sideways by the ponytail, so it is the pair that works.

Scale is NOT measured. Measuring face-to-feet over the 273 near-vertical frames gives a median
of 63 px with a p25-p75 of 56-75, so her drawn size really is constant; the outliers (frames
251-259 all report 21) are poses where the legs are hidden and the lowest cloth is her waist.
Deriving scale from the pose imports that error into every frame, so SPAN is a constant and the
pose only supplies a direction.

The region
----------
Bands along the body axis, as a share of SPAN measured down from the face centre, intersected
with her own connected pixels and with the classes that are cloth. Skin and hair are never in
the mask, so her face, arms and legs stay the artist's whatever the model or the painter does.

    bkmask.py build <outdir>          mask PNGs, one per frame, + index.json
    bkmask.py preview <out.png> <frames>
"""
import json, math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
import bikini as B, frame_out as F, qretex_form as QRF

KEY = F.KEY
SPAN = 63.0          # her drawn height, face centre to feet; see the note above
EMPTY = 400          # fewer opaque pixels than this and the frame is an effect, not a character
CHEST = (0.10, 0.34)  # down the axis, as a share of SPAN; the white bust panel sits at 0.28
HIP = (0.34, 0.56)    # the skirt measures 0.36
REACH = 55           # her own pixels never get further than this from her face
HALF = 0.26          # half her torso; the arms and the katana are wider out than this
CLOTH = ('cand', 'skirt', 'panel', 'gold')


def classes(fr):
    return [None if v == KEY else B.cls(v) for v in fr[2]]


def face(fr, c, r=2):
    """Centre of the skin against the hair piece with the most skin near it. The radius is 2,
    not adjacency: the artist draws a 1 px outline between hair and cheek on the turned-away
    poses, and a strict adjacency test scores those frames zero and picks the wrong piece."""
    w, h, px = fr
    best = None
    for b in B.blobs({i for i, k in enumerate(c) if k == 'hair'}, w, h):
        if len(b) < 20:
            continue
        s = set(b)
        for _ in range(r):
            s |= {j for i in s for j in B.nb8(i, w, h)}
        t = {j for j in s if c[j] == 'skin'}
        if best is None or len(t) > len(best):
            best = t
    if not best:
        return None
    return B.mid(B.grow(best, lambda j: c[j] == 'skin', w, h), w)


def frame_of(fr, c):
    """(origin, unit vector down her body). Direction is the face to the middle of her own
    pixels; near-vertical stays vertical so the common standing frames never wobble."""
    w, h, px = fr
    f = face(fr, c)
    if f is None:
        return None
    fi = int(f[1]) * w + int(f[0])
    # capped at REACH of the face: on the summon frames the demon's armour is dark cloth and
    # its trim is gold, so an uncapped grow walks straight off her and onto it, and every
    # measurement taken in this frame of reference is then measuring the demon.
    body = B.grow([fi], lambda j: c[j] in ('skin', 'hair') + CLOTH
                  and abs(j % w - f[0]) <= REACH and abs(j // w - f[1]) <= REACH,
                  w, h, B.nb8)
    if len(body) < 200:
        return None
    # direction from her CLOTH only. Including the hair costs accuracy that shows up as flicker:
    # the ponytail is a third of her pixels and drags the centroid sideways, which on frames
    # 33/34 and 190/191 tipped the axis to 180 / -158 / -45 / -90 degrees. Measured over the 423
    # animation steps, the step-to-step change in mask area goes 0.09 -> 0.08 median and the
    # number of steps that jump by more than half goes 40 -> 23. A fixed vertical axis is worse
    # than both (0.11 median, 88 jumps): she does lunge, and the torso really does lean.
    pts = [i for i in body if c[i] in CLOTH]
    if len(pts) < 30:
        return f, (0.0, 1.0), body
    cx, cy = B.mid(pts, w)
    dx, dy = cx - f[0], cy - f[1]
    if math.hypot(dx, dy) < 3:
        return f, (0.0, 1.0), body
    a = math.atan2(dy, dx)
    if abs(a - math.pi / 2) < math.radians(30):
        a = math.pi / 2
    else:
        a = round(a / (math.pi / 8)) * (math.pi / 8)
    return f, (math.cos(a), math.sin(a)), body


# The bikini, as bands down her body. Measured landmarks: the white bust panel sits at t=0.28,
# the skirt at t=0.36, her skin (face, arms) at t=0.19, her hair at t=-0.05.
BRA = (0.12, 0.32)
MID = (0.32, 0.42)
BOT = (0.42, 0.56)
CLOTH_L, SKIN_L = 1, 2


def build_zones(fr):
    """Per pixel: 0 leave alone, 1 black cloth, 2 bare skin. Only ever set on her own cloth,
    so her face, arms, hands and the katana are outside this by construction."""
    w, h, px = fr
    if sum(1 for v in px if v != KEY) < EMPTY:
        return None
    c = classes(fr)
    fo = frame_of(fr, c)
    if fo is None:
        return None
    (ox, oy), (ux, uy), body = fo
    z = bytearray(w * h)
    for i in body:
        if c[i] not in CLOTH:
            continue
        dx, dy = i % w - ox, i // w - oy
        t = (dx * ux + dy * uy) / SPAN
        v = abs(-dx * uy + dy * ux) / SPAN
        if BRA[0] <= t < BRA[1] and v <= HALF:
            z[i] = CLOTH_L
        elif BOT[0] <= t < BOT[1] and v <= HALF:
            z[i] = CLOTH_L
        elif MID[0] <= t < MID[1] and v <= HALF:
            z[i] = SKIN_L
        elif t >= BOT[1]:
            z[i] = SKIN_L                 # below the bottoms: bare legs, the skirt goes
    return z


def build_mask(fr):
    """1 where the garment may be repainted, else 0. None when there is no character."""
    w, h, px = fr
    if sum(1 for v in px if v != KEY) < EMPTY:
        return None
    c = classes(fr)
    fo = frame_of(fr, c)
    if fo is None:
        return None
    (ox, oy), (ux, uy), body = fo
    m = bytearray(w * h)
    for i in body:
        if c[i] not in CLOTH:
            continue
        dx, dy = i % w - ox, i // w - oy
        t = (dx * ux + dy * uy) / SPAN
        v = abs(-dx * uy + dy * ux) / SPAN
        if v > HALF:
            continue
        if CHEST[0] <= t < CHEST[1] or HIP[0] <= t < HIP[1]:
            m[i] = 1
    return m


def main():
    a = sys.argv[1:]
    fr = QRF.load()
    if a and a[0] == 'preview':
        import preview as T
        tiles = []
        for k in [int(x) for x in a[2].split(',')]:
            w, h, px = fr[k]
            m = build_mask(fr[k])
            base = [None if v == KEY else F.rgb(v) for v in px]
            over = [None if v == KEY else ((255, 0, 128) if m and m[i] else F.rgb(v))
                    for i, v in enumerate(px)]
            tiles += [(w, h, base), (w, h, over)]
        T.sheet(a[1], tiles, Z=int(os.environ.get('Z', 3)))
        print(a[1])
        return 0
    if a and a[0] == 'build':
        d = a[1]
        os.makedirs(d, exist_ok=True)
        idx = {'span': SPAN, 'chest': CHEST, 'hip': HIP, 'frames': {}}
        n = 0
        for k, (w, h, px) in enumerate(fr):
            m = build_mask((w, h, px))
            if m is None or not any(m):
                idx['frames'][k] = 0
                continue
            rows = b''.join(b'\xff\xff\xff\xff' if v else b'\x00\x00\x00\x00' for v in m)
            F.png_write(os.path.join(d, 'frame_%03d.png' % k), w, h, rows)
            idx['frames'][k] = sum(m)
            n += 1
        json.dump(idx, open(os.path.join(d, 'index.json'), 'w'), indent=1)
        got = [v for v in idx['frames'].values() if v]
        print('%d masks in %s; %d px median, %d min, %d max'
              % (n, d, sorted(got)[len(got) // 2], min(got), max(got)))
        return 0
    print(__doc__)
    return 1


if __name__ == '__main__':
    sys.exit(main())
