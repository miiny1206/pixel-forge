"""Set up a project's work directory from the game archive.

    python -m pxf_pipeline.prepare

  <work>/all/        every frame of the project's sheet as frame_NNN.png (+ manifest.json),
                     via `pxf import <source_archive> --only <sheet>`
  <work>/anim.json   the sheet's .ani, via `pxf ani --only <ani> --json`
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


def main():
    P = current()
    os.makedirs(P.work, exist_ok=True)
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
