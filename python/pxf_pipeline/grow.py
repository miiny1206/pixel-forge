"""Bigger canvases for chosen frames, without moving anything on screen.

Every frame keeps its stock canvas size, because the .ani places it by its top-left. When a
frame has to show more than its stock canvas holds (hold.py putting a larger neighbour in its
place), the canvas grows instead: transparent padding (left, top, right, bottom), and every
.ani step that shows the frame gets x += left, z += top, y += right. A frame's top-left is
anchor - (x, z) and y = w - x is the mirrored side, so the stock art, and every other sheet
that shares the .ani, stays exactly where it was on screen. Measured on the example: stock and
alternate-colour sheets pixel-identical on screen before and after.

Apply it to every sheet sharing the .ani (they must all grow alike) and to the .ani itself,
from the pristine archive each time so it never stacks. Project key:

    "grow": {"221": [9, 1, 25, 2], "222": [3, 10, 0, 0]}
"""
from . import sheetio


def table(P=None):
    if P is None:
        from .project import current
        P = current()
    return {int(k): tuple(v) for k, v in P.get('grow', {}).items()}


def frame(w, h, px, pad):
    """(w, h, px) of one frame -> padded (w, h, px)"""
    l, t, r, b = pad
    W, H = w + l + r, h + t + b
    out = [sheetio.KEY] * (W * H)
    for y in range(h):
        out[(y + t) * W + l:(y + t) * W + l + w] = px[y * w:(y + 1) * w]
    return W, H, tuple(out)


def spr(blob, grow):
    """whole .spr blob -> same with the `grow` frames padded"""
    frs = sheetio.frames(blob)
    for i, pad in grow.items():
        w, h, px = frs[i]
        if w > 0:
            frs[i] = frame(w, h, px, pad)
    return sheetio.build(blob, frs)


def ani(blob, grow, per):
    """an .ani -> same with the steps that show a grown frame moved to match"""
    a = sheetio.Ani(blob, per)
    n = 0
    for k, f in enumerate(a.frame):
        if f in grow:
            l, t, r, b = grow[f]
            a.x[k] += l
            a.y[k] += r
            a.z[k] += t
            n += 1
    if grow and not n:
        raise SystemExit('no step shows a grown frame')
    return a.blob()
