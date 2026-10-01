"""Bake the costume into its own sheet in the game archive, and the town sheet with it.

Source is always the stock art in the pristine SOURCE_ARCHIVE, never the archive on disk, so
running this twice gives the same bytes and an earlier attempt can never be painted over a
second time. A frame_NNN.png in --frames DIR (the finished, remixed frames) replaces frame NNN;
every other drawn frame gets the rule paint of bkpaint.py, so a frame no model drew still
wears the costume instead of the old outfit.

No phase lock is needed here: nothing has a repeating pattern. The cloth ramp is chosen by
pinned luminance cuts and the skin by erosion depth, both properties of the pixel rather
than of where the body axis happens to land.

Project keys:
    "sheet": "char_form.spr", "ani": "char_form.ani", "per": 50
    "bake": {"name": "char_form_beach.spr",
             "shared": ["char_form_alt.spr"],      sheets that share the .ani (for grow)
             "town": {"archive": ..., "src": ..., "name": ...}}   optional town sheet
    "grow": {...}                                       see pxf_pipeline/grow.py

    bkbake.py                bake
    bkbake.py --frames DIR   same, with finished frames
    bkbake.py restore        put the target archive back from its first backup
"""
import os, shutil, sys, zipfile

from _paths import P as PROJECT, SOURCE_ARCHIVE, TARGET_ARCHIVE
import bkpaint as P
from pxf_pipeline import grow as G, sheetio as M

CFG = PROJECT.get('bake', {})
SRC = PROJECT.get('sheet')
NAME = CFG.get('name')
BAK = TARGET_ARCHIVE + '.before_bake' if TARGET_ARCHIVE else None


def png565(path, size):
    """RGBA png -> .spr words; transparency is the colour key, and an opaque pixel that
    would land on the key is moved one blue step off it"""
    from PIL import Image
    im = Image.open(path)
    if im.size != size:
        raise SystemExit('%s is %s, the stock frame is %s' % (path, im.size, size))
    return M.from_image(im)[2]


def bake(over=None):
    grow = G.table(PROJECT)
    with zipfile.ZipFile(SOURCE_ARCHIVE) as z:
        stock = G.spr(z.read(SRC), grow)              # grown canvases (grow.py)
        shared = {n: G.spr(z.read(n), grow) for n in [SRC] + CFG.get('shared', [])}
        shared[PROJECT.get('ani')] = G.ani(z.read(PROJECT.get('ani')), grow, PROJECT.get('per', 50))
    fr = M.frames(stock)
    out = []
    n = m = 0
    for i, (w, h, px) in enumerate(fr):
        png = over and os.path.join(over, 'frame_%03d.png' % i)
        if png and os.path.exists(png):
            px = png565(png, (w, h))
            m += 1
        elif w > 8:                                  # 8 px or less is a spacer, nothing drawn
            (w, h, px), c = P.paint((w, h, px))
            n += c > 0
        out.append((w, h, px))
    blob = M.renamed(M.build(stock, out), NAME)

    data, names = M.read_ark(TARGET_ARCHIVE)
    if not os.path.exists(BAK):
        shutil.copy2(TARGET_ARCHIVE, BAK)
    data[NAME] = blob
    data.update(shared)                              # the sheets sharing the .ani, and the .ani
    if NAME not in names:
        names.append(NAME)
    M.write_ark(TARGET_ARCHIVE, data, names, deflate=[NAME])
    print('%s: %d frames, %d painted, %d from %s' % (NAME, len(fr), n, m, over))
    if CFG.get('town'):
        town(blob)


def town(beach_blob):
    """the town sheet: same slots, its own (smaller) sizes; every town frame whose size
    matches the baked frame takes the baked pixels"""
    t = CFG['town']
    ark = PROJECT.path(t['archive'])
    beach = M.frames(beach_blob)
    data, names = M.read_ark(ark)
    if not os.path.exists(ark + '.before_bake'):
        shutil.copy2(ark, ark + '.before_bake')
    tf = M.frames(data[t['src']])
    out = [beach[i] if w > 8 and i < len(beach) and beach[i][:2] == (w, h) else (w, h, px)
           for i, (w, h, px) in enumerate(tf)]
    data[t['name']] = M.renamed(M.build(data[t['src']], out), t['name'])
    if t['name'] not in names:
        names.append(t['name'])
    M.write_ark(ark, data, names, deflate=[t['name']])
    print('%s: %d frames written into %s' % (t['name'], len(tf), os.path.basename(ark)))


def restore():
    if not os.path.exists(BAK):
        raise SystemExit('no ' + BAK)
    shutil.copy2(BAK, TARGET_ARCHIVE)
    print('%s restored from %s' % (os.path.basename(TARGET_ARCHIVE), os.path.basename(BAK)))


if __name__ == '__main__':
    a = sys.argv[1:]
    if a[:1] == ['restore']:
        restore()
    else:
        bake(a[1] if a[:1] == ['--frames'] else None)
