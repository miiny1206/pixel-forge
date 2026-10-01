"""Make each animation one drawing instead of N separate redraws.

An image model draws every frame on its own, so consecutive idle frames differed by 28-44%
where the stock art differs by 7-15% (breathing): in game the character shimmered and jumped.
Here each animation is walked in play order from a key frame. For the next frame, wherever the
STOCK art did not change from the previous frame (after the best 1-3 px shift), the pixel is
carried over from the previous finished frame; only where the artist really redrew something
is the model's own drawing of that frame used. So the finished loop changes about as much as
the stock loop, and a good hand or shading in the key frame stays.

Only the figure (the remix mask) is touched; effects outside it are stock anyway. A frame
shared by several animations is settled by the first one that reaches it and is then a fixed
point for the others. Frames in runs/graft/bake are fixed points too (copied as they are).

    python -m pxf_pipeline.smooth                every animation -> runs/smooth/bake
    python -m pxf_pipeline.smooth 0,3            only these animations
"""
import glob, os, sys
from PIL import Image

from .project import current
from . import transplant as T
from .crops import figure_mask as mask_of

CARRY = 64     # RGB distance: carry only where the model's own pixel is already near


def sources():
    """frame -> the model's finished frame, same precedence as finish.py (later run wins,
    runs/graft wins over all)"""
    P = current()
    src = {}
    for p in sorted(glob.glob(P.work + 'runs/*/bake/frame_*.png'), key=lambda p: ('/runs/graft/' in p, p)):
        if '/runs/smooth/' not in p:
            src[int(os.path.basename(p)[6:9])] = p
    return src


def close(p, q):
    """both transparent, or both opaque and within CARRY. Flat stock areas (black
    stockings, a skirt) look unchanged even where a leg moved; carrying there on stock
    evidence alone drew streaks of the key frame's legs across walk frames. With the
    model's own pixel as a second witness, only shading jitter is ironed out, never shape."""
    if (p[3] >= 128) != (q[3] >= 128):
        return False
    return p[3] < 128 or sum((p[i] - q[i]) ** 2 for i in range(3)) <= CARRY * CARRY


def textured(pa, sa, pb, sb, x, y, dx, dy):
    """the 3x3 stock patch around (x,y) is identical in both frames and holds 3+ colours:
    strong evidence it is the same spot of the body. Flat patches prove nothing, so they
    fall back to close()."""
    cols = set()
    for j in (-1, 0, 1):
        for i in (-1, 0, 1):
            q = T.at(pb, sb, x + i, y + j)
            if q != T.at(pa, sa, x + i - dx, y + j - dy):
                return False
            cols.add(q)
    return len(cols) >= 3


def carry(a, b, res_a, gem_b):
    """finished b: res_a where stock a -> b did not change, gem_b where it did"""
    P = current()
    sa, sb = T.rgba(P.stock(a)), T.rgba(P.stock(b))
    miss, dx, dy = T.best_shift(sb, sa, r=3, c=T.start(b, a))
    ra, pa, pb = res_a.load(), sa.load(), sb.load()
    out = gem_b.copy()
    po = out.load()
    m = mask_of(b).load()
    w, h = sb.size
    kept = changed = 0
    for y in range(h):
        for x in range(w):
            if m[x, y] < 128:
                continue
            prev = T.at(ra, res_a.size, x - dx, y - dy)
            if T.same(pb[x, y], T.at(pa, sa.size, x - dx, y - dy)) and \
                    (close(po[x, y], prev) or textured(pa, sa.size, pb, sb.size, x, y, dx, dy)):
                po[x, y] = prev
                kept += 1
            else:
                changed += 1
    return out, kept, changed


def medoid(seq, src, sample=10):
    """the frame whose drawing agrees best with the others: the model sometimes keeps a
    detail in only half the frames and the key decides it for the whole loop, so the key
    should be the typical drawing, not whichever frame plays first. Compared on up to
    `sample` evenly spread frames, as fraction of differing pixels."""
    pick = seq if len(seq) <= sample else [seq[i * len(seq) // sample] for i in range(sample)]
    imgs = {f: T.rgba(src[f]) for f in pick}
    best = None
    for f in pick:
        tot = sum(T.best_shift(imgs[f], imgs[g], r=3, c=T.start(f, g))[0] for g in pick if g != f)
        if best is None or tot < best[0]:
            best = (tot, f)
    return best[1]


def main():
    P = current()
    out_dir = P.work + 'runs/smooth/bake/'
    anims = P.anims()
    which = [int(v) for v in sys.argv[1].split(',')] if len(sys.argv) > 1 else range(len(anims))
    src = sources()
    os.makedirs(out_dir, exist_ok=True)
    done = {int(os.path.basename(p)[6:9]) for p in glob.glob(out_dir + 'frame_*.png')}
    # grafted frames are already one drawing with their neighbours: carrying the old
    # neighbours back into them drew a ghost double. They are fixed points.
    for p in glob.glob(P.work + 'runs/graft/bake/frame_*.png'):
        f = int(os.path.basename(p)[6:9])
        if f not in done:
            Image.open(p).save(out_dir + 'frame_%03d.png' % f)
            done.add(f)
    for n in which:
        a = anims[n]
        seq = []
        for s in a['steps'][:a['last'] + 1]:
            if s['frame'] in src and (not seq or seq[-1] != s['frame']) and s['frame'] not in seq:
                seq.append(s['frame'])
        if not seq:
            continue
        free = [f for f in seq if f not in done]
        if free and not any(f in done for f in seq):
            k = medoid(seq, src)
            i = seq.index(k)
            seq = seq[i:] + seq[:i]                  # walk the loop starting at the key
        log = []
        prev = None
        for f in seq:
            if f in done:
                prev = f
                continue
            if prev is None:
                Image.open(src[f]).save(out_dir + 'frame_%03d.png' % f)      # the key: as drawn
                log.append('%d key' % f)
            else:
                res, kept, changed = carry(prev, f, T.rgba(out_dir + 'frame_%03d.png' % prev), T.rgba(src[f]))
                res.save(out_dir + 'frame_%03d.png' % f)
                log.append('%d carried %d%%' % (f, round(100 * kept / max(kept + changed, 1))))
            done.add(f)
            prev = f
        print('anim %d: %s' % (n, ', '.join(log) or 'all already settled'))


if __name__ == '__main__':
    main()
