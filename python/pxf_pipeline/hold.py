"""Frames no model would draw, shown as a finished neighbour instead.

When a frame is refused and has no donor pose for transplant/graft, the choice is either the
rule-painted fallback (which may flash the old outfit between redrawn frames) or holding a
finished neighbour. Each held frame is filled with the finished frame placed where that frame
stands on screen: the .ani puts a frame's top-left at anchor - (x, z), so the source is pasted
at the difference of the two frames' (x, z). If the source does not fit the stock canvas,
grow the canvas first (grow.py) or the overflow is cut.

Project keys: "hold": {"221": 223}, optionally "grow". Runs on <work>/bake_all, last.
"""
from PIL import Image

from .project import current
from .grow import table


def main():
    P = current()
    out = P.work + 'bake_all/'
    grow = table(P)
    off = {}
    for a in P.anims():
        for st in a['steps']:
            off.setdefault(st['frame'], (st['x'], st['z']))
    for dst, src in ((int(k), int(v)) for k, v in P.get('hold', {}).items()):
        s = Image.open(out + 'frame_%03d.png' % src).convert('RGBA')
        w, h = Image.open(P.stock(dst)).size
        l, t, r, b = grow.get(dst, (0, 0, 0, 0))
        c = Image.new('RGBA', (w + l + r, h + t + b), (0, 0, 0, 0))
        at = (off[dst][0] + l - off[src][0], off[dst][1] + t - off[src][1])
        c.paste(s, at)
        c.save(out + 'frame_%03d.png' % dst)
        cut = at[0] < 0 or at[1] < 0 or at[0] + s.size[0] > c.size[0] or at[1] + s.size[1] > c.size[1]
        print('frame_%03d <- frame_%03d%s' % (dst, src, ' (cut: grow this canvas)' if cut else ''))


if __name__ == '__main__':
    main()
