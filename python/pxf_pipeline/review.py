"""Review sheets: per animation, stock above and the frame as it will be baked below, zoomed,
each column labelled with its frame number - what a QA pass looks at.

    python -m pxf_pipeline.review <anim,...> [--zoom 4]      -> <work>/review/anim_NN.png

Reads the baked candidates the way finish.py gathers them (runs/smooth wins), so rerun
smooth first if the runs changed.
"""
import glob, os, sys
from PIL import Image, ImageDraw

from .project import current

BG = (230, 200, 140, 255)


def finished():
    P = current()
    src = {}
    paths = sorted((p for p in glob.glob(P.work + 'runs/*/bake/frame_*.png') if '/runs/smooth/' not in p),
                   key=lambda p: ('/runs/graft/' in p, p))
    for p in paths + sorted(glob.glob(P.work + 'runs/smooth/bake/frame_*.png')):
        src[int(os.path.basename(p)[6:9])] = p
    return src


def sheet(n, zoom=4, per_row=8):
    P = current()
    a = P.anims()[n]
    frames = list(dict.fromkeys(s['frame'] for s in a['steps'][:a['last'] + 1]))
    src = finished()
    frames = [f for f in frames if f in src]
    if not frames:
        return None
    cells = []
    for f in frames:
        s = Image.open(P.stock(f)).convert('RGBA')
        r = Image.open(src[f]).convert('RGBA')
        w, h = s.size
        c = Image.new('RGBA', (w, 2 * h + 2), BG)
        c.alpha_composite(s, (0, 0))
        c.alpha_composite(r, (0, h + 2))
        cells.append((f, c.resize((w * zoom, (2 * h + 2) * zoom), Image.NEAREST)))
    cw = max(c.width for _, c in cells) + 8
    ch = max(c.height for _, c in cells) + 22
    rows = (len(cells) + per_row - 1) // per_row
    out = Image.new('RGBA', (cw * min(per_row, len(cells)), ch * rows), (60, 60, 60, 255))
    d = ImageDraw.Draw(out)
    for i, (f, c) in enumerate(cells):
        x, y = (i % per_row) * cw, (i // per_row) * ch
        d.text((x + 4, y + 4), 'frame %d' % f, fill=(255, 255, 255, 255))
        out.alpha_composite(c, (x + 4, y + 20))
    os.makedirs(P.work + 'review/', exist_ok=True)
    p = P.work + 'review/anim_%02d.png' % n
    out.convert('RGB').save(p)
    return p, frames


def main():
    zoom = int(sys.argv[sys.argv.index('--zoom') + 1]) if '--zoom' in sys.argv else 4
    for n in [int(v) for v in sys.argv[1].split(',')]:
        r = sheet(n, zoom)
        print(r[0] + ' ' + str(r[1]) if r else 'anim %d: nothing finished' % n)


if __name__ == '__main__':
    main()
