"""Frames go to the model as crops around the figure and come back into the full frame.

crops.json (in the work dir) maps frame -> [x, y, w, h], the box sent to the model. The
default, written by `prepare`, is the opaque bounding box of each frame; a project can write
its own (the beach-costume example crops around her figure only, leaving after-images out).

Coming back, two things are built for `pxf remix`:

  placed/   full-size frames: the stock frame with the downscaled crop written over its box,
            transparency included
  masks/    white where the STOCK frame has the figure, black elsewhere (the project's
            "masks" hook, default: every opaque pixel inside the crop box)

then `pxf remix placed/ out/ --orig all/ --mask masks/ --alpha`: inside the figure the edit
wins, transparency too; outside it (effects, other characters) the stock art is kept. The
silhouette can only shrink, never grow past where the artist drew, so the .ani offsets
stay right.

    python -m pxf_pipeline.crops crops <outdir> <frame,...>
    python -m pxf_pipeline.crops place <downdir> <outdir> <frame,...>
    python -m pxf_pipeline.crops masks <outdir> <frame,...>
"""
import glob, os, sys
from PIL import Image

from .project import current


def crops(out, frames):
    P = current()
    box = P.crops()
    os.makedirs(out, exist_ok=True)
    for f in frames:
        x, y, w, h = box[str(f)]
        Image.open(P.stock(f)).crop((x, y, x + w, y + h)).save(os.path.join(out, 'crop_%03d.png' % f))
    print('cropped %d frame(s) -> %s' % (len(frames), out))


def place(down, out, frames):
    P = current()
    box = P.crops()
    os.makedirs(out, exist_ok=True)
    for f in frames:
        base = Image.open(P.stock(f)).convert('RGBA')
        crop = Image.open(os.path.join(down, 'crop_%03d.png' % f)).convert('RGBA')
        x, y, w, h = box[str(f)]
        if crop.size != (w, h):
            raise SystemExit('frame %d: crop is %s, box is %dx%d' % (f, crop.size, w, h))
        base.paste(crop, (x, y))            # paste, not alpha_composite: transparency is data
        base.save(os.path.join(out, 'frame_%03d.png' % f))
    print('placed %d frame(s) -> %s' % (len(frames), out))


def opaque_masks(out, frames, grow=0):
    """default figure mask: every opaque stock pixel inside the crop box"""
    P = current()
    box = P.crops()
    os.makedirs(out, exist_ok=True)
    for f in frames:
        s = Image.open(P.stock(f)).convert('RGBA')
        x, y, w, h = box[str(f)]
        m = Image.new('L', s.size, 0)
        a = s.getchannel('A').point(lambda v: 255 if v >= 128 else 0)
        m.paste(a.crop((x, y, x + w, y + h)), (x, y))
        m.convert('RGB').save(os.path.join(out, 'frame_%03d.png' % f))


def masks(out, frames, grow=0):
    current().hook('masks', opaque_masks)(out, frames, grow=grow)


def figure_mask(f):
    """frame f's figure mask: the one its run used, else built now into runs/smooth/masks"""
    P = current()
    for p in glob.glob(P.work + 'runs/*/f%03d/masks/frame_%03d.png' % (f, f)):
        return Image.open(p).convert('L')
    d = P.work + 'runs/smooth/masks/'
    masks(d, [f])
    return Image.open(d + 'frame_%03d.png' % f).convert('L')


def main():
    a = sys.argv[1:]
    if a and a[0] == 'place' and len(a) == 4:
        place(a[1], a[2], [int(x) for x in a[3].split(',')])
    elif a and a[0] == 'crops' and len(a) == 3:
        crops(a[1], [int(x) for x in a[2].split(',')])
    elif a and a[0] == 'masks' and len(a) == 3:
        masks(a[1], [int(x) for x in a[2].split(',')])
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
