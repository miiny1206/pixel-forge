"""Set up a project's work directory, from a folder of PNG frames or from a game archive.

    python -m pxf_pipeline.prepare

PNG frames (project key "frames"): a directory of PNGs with alpha. Each subdirectory is one
animation, its files in name order; PNGs directly in the directory form one animation.
Frames of an animation are aligned on their bottom centre (the usual sprite anchor), so
canvases may differ in size.

  <work>/all/          every frame as frame_NNN.png
  <work>/frames.json   frame number -> original relative path (used by export)
  <work>/anim.json     one animation per subdirectory, steps in file order

Game archive (project keys "source_archive", "sheet", "ani"):

  <work>/all/        every frame of the project's sheet as frame_NNN.png (+ manifest.json),
                     via `pxf import <source_archive> --only <sheet>`
  <work>/anim.json   the sheet's animation table, via `pxf ani --only <ani> --json`

Both:

  <work>/crops.json  frame -> [x, y, w, h] sent to the model. The project's "crops" hook
                     (a callable returning that dict) wins; the default is the opaque
                     bounding box of every frame the .ani plays.

Existing files are left alone; delete them to rebuild.
"""
import json, os, shutil

from PIL import Image

from .project import current
from . import pxfbin


def opaque_boxes(P):
    played = {s['frame'] for a in P.anims() for s in a['steps'][:a['last'] + 1] if s['frame'] >= 0}
    out = {}
    for f in sorted(played):
        p = P.stock(f)
        if not os.path.exists(p):
            continue
        b = Image.open(p).convert('RGBA').getchannel('A').point(lambda v: 255 if v >= 128 else 0).getbbox()
        if b:
            out[str(f)] = [b[0], b[1], b[2] - b[0], b[3] - b[1]]
    return out


def from_frames(P):
    """a folder of PNGs -> all/, frames.json, anim.json"""
    src = P.path(P.get('frames'))
    groups = []
    loose = sorted(f for f in os.listdir(src) if f.lower().endswith('.png'))
    if loose:
        groups.append(loose)
    for d in sorted(e for e in os.listdir(src) if os.path.isdir(os.path.join(src, e))):
        files = sorted(f for f in os.listdir(os.path.join(src, d)) if f.lower().endswith('.png'))
        if files:
            groups.append([d + '/' + f for f in files])
    if not groups:
        raise SystemExit('no PNG frames in ' + src)
    os.makedirs(P.work + 'all', exist_ok=True)
    names, anims, n = {}, [], 0
    for files in groups:
        steps = []
        for rel in files:
            im = Image.open(os.path.join(src, rel)).convert('RGBA')
            im.save(P.stock(n))
            w, h = im.size
            # anchor at the bottom centre: top-left = anchor - (x, z); y is the mirrored x
            steps.append({'frame': n, 'x': w // 2, 'y': w - w // 2, 'z': h, 'flag': 0})
            names[str(n)] = rel
            n += 1
        anims.append({'last': len(steps) - 1, 'steps': steps})
    json.dump(names, open(P.work + 'frames.json', 'w'), indent=1)
    json.dump({'per': max(len(a['steps']) for a in anims), 'anims': anims},
              open(P.work + 'anim.json', 'w'))
    print('%d frame(s) in %d animation(s) from %s' % (n, len(anims), src))


def main():
    P = current()
    os.makedirs(P.work, exist_ok=True)
    if P.get('frames'):
        if not os.path.isdir(P.work + 'all'):
            from_frames(P)
        if not os.path.exists(P.work + 'crops.json'):
            boxes = P.hook('crops', opaque_boxes)(P)
            json.dump({str(k): v for k, v in boxes.items()}, open(P.work + 'crops.json', 'w'))
            print('crops.json: %d frames' % len(boxes))
        return
    ark, sheet, ani = P.path(P.get('source_archive')), P.get('sheet'), P.get('ani')
    if not os.path.isdir(P.work + 'all'):
        tmp = P.work + 'import'
        print(pxfbin.run('import', pxfbin.path(ark), pxfbin.path(tmp), '--only', sheet))
        shutil.move(os.path.join(tmp, os.path.splitext(sheet)[0]), P.work + 'all')
        shutil.rmtree(tmp, ignore_errors=True)
    if not os.path.exists(P.work + 'anim.json'):
        print(pxfbin.run('ani', pxfbin.path(ark), '--only', ani, '--json', pxfbin.path(P.work + 'anim.json')))
    if not os.path.exists(P.work + 'crops.json'):
        boxes = P.hook('crops', opaque_boxes)(P)
        json.dump({str(k): v for k, v in boxes.items()}, open(P.work + 'crops.json', 'w'))
        print('crops.json: %d frames' % len(boxes))


if __name__ == '__main__':
    main()
