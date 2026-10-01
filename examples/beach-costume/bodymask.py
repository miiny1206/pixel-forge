"""Her whole figure in one frame of the sheet: bkmask's body plus the tights and boots.

bkmask grows her body from the face through skin, hair and cloth classes and caps it at
55 px, so it leaves out exactly what the costume replaces: the tights and boots (not in
those classes) and a leg stretched past 55 px. legs.region finds tights and boots by their
own colours in any pose, but also finds the after-images in frames 269-285, up to 368 px
from her face. So a legs piece counts only if it lies within GAP px of her body: a real leg
is attached to her, an after-image stands apart.

GAP is 12, not 1-3: the skirt is in neither set, so it sits between body and tights like a
10-15 px moat. With GAP 1-5, 31-37 frames kept under half their tights. The skirt is also the
thing being removed, so it has to be in the mask anyway: the BRIDGE is every opaque pixel
within GAP of both her body and a kept leg piece.

    bodymask.py measure [gap ...]    coverage and crop sizes; at GAP also writes crops.json
"""
import json, math, sys
import bkmask as M, legs as L, qretex_form as QRF

from _paths import WORK as HERE
GAP = 12
# A tights piece is hers if its nearest pixel is within REACH of her face. Measured over the
# 955 pieces of >= 8 px outside the after-image frames: nearest distance p50 29, p90 49 (the
# hip sits 21-35 px down); the after-images in 269-285 start at 53. Distance to her BODY was
# tried first and failed: the skirt and cape sit between torso and tights, 10-20+ px, so
# frames 72, 82, 181, 251 lost one or both legs.
REACH = 50


def near_body(w, h, body, gap):
    """body dilated by `gap` (chessboard distance)"""
    out = set()
    for i in body:
        x, y = i % w, i // w
        for dy in range(-gap, gap + 1):
            for dx in range(-gap, gap + 1):
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    out.add(ny * w + nx)
    return out


BETWEEN = 20


def dist_map(w, h, src, limit):
    """{pixel: chessboard distance to the nearest `src` pixel}, up to `limit`"""
    d = {i: 0 for i in src}
    front = list(src)
    for step in range(1, limit + 1):
        nxt = []
        for i in front:
            x, y = i % w, i // w
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    nx, ny = x + dx, y + dy
                    j = ny * w + nx
                    if 0 <= nx < w and 0 <= ny < h and j not in d:
                        d[j] = step
                        nxt.append(j)
        front = nxt
    return d


def pieces(w, h, cells):
    seen = set()
    for s in cells:
        if s in seen:
            continue
        comp, st = [], [s]
        seen.add(s)
        while st:
            k = st.pop()
            comp.append(k)
            x, y = k % w, k // w
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    nx, ny = x + dx, y + dy
                    j = ny * w + nx
                    if 0 <= nx < w and 0 <= ny < h and j in cells and j not in seen:
                        seen.add(j)
                        st.append(j)
        yield comp


def figure(fr, gap=GAP):
    """(origin, body, legs kept, whole figure) or None when no face is found"""
    w, h, px = fr
    r = M.frame_of(fr, M.classes(fr))
    if not r:
        return None
    origin, _, body = r
    ox, oy = origin
    # A piece is hers if it is near her face, or near her body, or near a piece already kept
    # (grown until stable). Each rule alone lost a pose: body distance lost 72/82/181/251
    # (skirt and cape in between), face distance lost the kicks 207-216 (bare thigh, so the
    # tights start at the knee). The after-images are far from all three.
    comps = [c for c in pieces(w, h, L.region(fr))]
    zone = near_body(w, h, body, gap)
    keep, left = set(), []
    for comp in comps:
        if min(math.hypot(k % w - ox, k // w - oy) for k in comp) <= REACH or any(k in zone for k in comp):
            keep.update(comp)
        else:
            left.append(comp)
    grew = True
    while grew and left:
        grew = False
        kz = near_body(w, h, keep, gap)
        rest = []
        for comp in left:
            if any(k in kz for k in comp):
                keep.update(comp)
                grew = True
            else:
                rest.append(comp)
        left = rest
    # the skirt: every opaque pixel lying between her body and a kept leg, i.e. whose
    # distance to the body plus distance to the legs is at most BETWEEN. A fixed GAP around
    # each could not span the cape+skirt in frame 181.
    db = dist_map(w, h, body, BETWEEN)
    dl = dist_map(w, h, keep, BETWEEN)
    bridge = {i for i in db if i in dl and db[i] + dl[i] <= BETWEEN and px[i] != M.KEY}
    return origin, body, keep, body | keep | bridge


def char_frames():
    """frames that show her: frames.json "char" in the work dir when present (a hand list that
    leaves effect-only frames out), else every frame the .ani plays"""
    import os
    p = HERE + 'frames.json'
    if os.path.exists(p):
        return json.load(open(p))['char']
    a = json.load(open(HERE + 'anim.json'))['anims']
    return sorted({s['frame'] for x in a for s in x['steps'][:x['last'] + 1] if s['frame'] >= 0})


def crops(project=None):
    """the project's "crops" hook: boxes around her whole figure"""
    return {str(k): v for k, v in measure(GAP).items()}


def measure(gap):
    fr = QRF.load()
    covs, boxes, lost = [], {}, []
    for f in char_frames():
        w, h, px = fr[f]
        r = figure(fr[f], gap)
        if not r:
            continue
        (ox, oy), body, keep, m = r
        seed = {i for i, v in enumerate(px) if v in L.SEED and math.hypot(i % w - ox, i // w - oy) <= 110}
        c = 100 * len(seed & m) / len(seed) if seed else 100.0
        covs.append(c)
        if c < 50:
            lost.append((f, round(c), len(seed)))
        xs = [i % w for i in m]; ys = [i // w for i in m]
        x0, y0 = max(min(xs) - 4, 0), max(min(ys) - 4, 0)
        boxes[f] = (x0, y0, min(max(xs) + 5, w) - x0, min(max(ys) + 5, h) - y0)
    covs.sort()
    ws = sorted(b[2] for b in boxes.values()); hs = sorted(b[3] for b in boxes.values())
    print('gap %d: seed coverage min %.0f%% p10 %.0f%% median %.0f%%, frames under 50%%: %d %s' % (
        gap, covs[0], covs[len(covs)//10], covs[len(covs)//2], len(lost), lost[:8]))
    print('        crop width median %d max %d | height median %d max %d' % (ws[len(ws)//2], ws[-1], hs[len(hs)//2], hs[-1]))
    return boxes


if __name__ == '__main__':
    if sys.argv[1:2] == ['measure']:
        for g in (int(x) for x in (sys.argv[2:] or ['1', '3', '5'])):
            b = measure(g)
            if g == GAP:
                json.dump({str(k): v for k, v in b.items()}, open(HERE + 'crops.json', 'w'))
    else:
        print(__doc__)
