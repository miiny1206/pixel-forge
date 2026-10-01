"""The extra palette references this example's downscale uses (project "refs").

  <work>/src/          a few stock frames whose palette is the outfit's (idle 0, 1 and
                       attack 38, 40 here): copied from <work>/all
  <work>/skin_ref.png  four in-between skin shades the stock ramp lacks. Without them the
                       model's shin shadow (216,128,112) still went to the gold trim
                       (206,158,115) even with --metric redmean: tan legs on a dozen frames.

    make_refs.py [frame,...]
"""
import os, shutil, sys
from PIL import Image

from _paths import WORK

SKIN = [(165, 83, 78), (216, 128, 112), (218, 125, 115), (247, 172, 160)]


def main():
    frames = [int(v) for v in sys.argv[1].split(',')] if len(sys.argv) > 1 else [0, 1, 38, 40]
    os.makedirs(WORK + 'src', exist_ok=True)
    for f in frames:
        shutil.copy(WORK + 'all/frame_%03d.png' % f, WORK + 'src/src%03d.png' % f)
    im = Image.new('RGBA', (len(SKIN), 1))
    im.putdata([c + (255,) for c in SKIN])
    im.save(WORK + 'skin_ref.png')
    print('src/: %d frames, skin_ref.png: %d shades' % (len(frames), len(SKIN)))


if __name__ == '__main__':
    main()
