"""Group frames into sheets that fit the model's canvas at a given scale.

Scale 5 is measured, not chosen: on the same four action frames sent as one sheet to an
image-edit model, scale 3/4/5 gave 1286-1999 / 954-1126 / 305-653 weak pixels per frame and
76-91 / 87-96 / 91.5-97.5% silhouette agreement. The model keeps the grid only when a source
pixel is ~6 px of its output.

Layout is simulated exactly as `pxf sheet` does it: files in name order, split into `rows`
chunks of ceil(n/rows), strip width = widest row, height = row heights + gaps, times scale,
must fit the canvas. Frames of one animation are kept together where they fit - consecutive
frames of a move are where a changing outfit would show.

    python -m pxf_pipeline.plan [--scale 5]          writes <work>/sheets.json
"""
import json, sys

from .project import current

CANVAS, GAP = 1024, 8


def fits(sizes, scale, canvas=None, gap=None):
    """rows count that makes this group fit, or None. sizes in frame-number order."""
    canvas = canvas or CANVAS
    gap = GAP if gap is None else gap
    n = len(sizes)
    for rows in (1, 2, 3):
        if rows > n:
            break
        per = -(-n // rows)
        chunks = [sizes[i:i + per] for i in range(0, n, per)]
        sw = max(sum(w for w, h in c) + gap * (len(c) - 1) for c in chunks)
        sh = sum(max(h for w, h in c) for c in chunks) + gap * (len(chunks) - 1)
        if sw * scale <= canvas and sh * scale <= canvas:
            return rows
    return None


def anim_of(P):
    """frame -> the first animation that plays it"""
    out = {}
    for n, a in enumerate(P.anims()):
        for s in a['steps'][:a['last'] + 1]:
            if s['frame'] >= 0:
                out.setdefault(s['frame'], n)
    return out


def main():
    P = current()
    scale = int(sys.argv[sys.argv.index('--scale') + 1]) if '--scale' in sys.argv else P.get('scale', 5)
    B = P.crops()
    J = anim_of(P)
    by = {}
    for f in sorted(int(k) for k in B):
        by.setdefault(J.get(f, -1), []).append(f)
    sheets = []
    for a in sorted(by):
        cur = []
        for f in by[a]:
            trial = sorted(cur + [f])
            if cur and fits([tuple(B[str(g)][2:]) for g in trial], scale) is None:
                sheets.append(cur)
                cur = [f]
            else:
                cur = trial
        if cur:
            sheets.append(cur)
    # merge small leftovers of different animations while they fit
    sheets.sort(key=len)
    merged = []
    for s in sheets:
        for m in merged:
            trial = sorted(m + s)
            if len(trial) <= 6 and fits([tuple(B[str(g)][2:]) for g in trial], scale):
                m[:] = trial
                break
        else:
            merged.append(list(s))
    out = [{'frames': s, 'rows': fits([tuple(B[str(g)][2:]) for g in s], scale)} for s in merged]
    bad = [s for s in out if s['rows'] is None]
    if bad:
        raise SystemExit('frames too big for the canvas alone at scale %d: %s (tighter crops or a smaller '
                         'scale)' % (scale, sorted(f for s in bad for f in s['frames'])))
    n = [len(s['frames']) for s in out]
    print('%d frames -> %d sheets at scale %d (frames per sheet: %s)' % (
        sum(n), len(out), scale, ' '.join('%dx%d' % (k, n.count(k)) for k in sorted(set(n)))))
    json.dump({'scale': scale, 'sheets': out}, open(P.work + 'sheets.json', 'w'), indent=0)


if __name__ == '__main__':
    main()
