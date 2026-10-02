"""How much of the stock outfit survived in a finished frame: the share of the figure (the
remix mask) below LINE of its crop box - under the face and hair - whose pixel is still
exactly the stock pixel. Parts that stay stock by design (weapon, hands) count too, so what
matters is a frame standing out from the other frames of its animation, not the number.

batch.py retries a frame whose score is above the project's "leftover_max" (default 28:
good frames measured 8-25, a frame whose model kept the old jacket 34).

    python -m pxf_pipeline.leftover <bakedir> <frame,...>
"""
import sys
from PIL import Image

from .project import current
from .sheetio import pixels

LINE = 0.3


def score(path, f, mask_path):
    P = current()
    B = P.crops()
    a = pixels(Image.open(path).convert('RGBA'))
    s = pixels(Image.open(P.stock(f)).convert('RGBA'))
    m = pixels(Image.open(mask_path).convert('L'))
    w = Image.open(path).width
    line = B[str(f)][1] + int(LINE * B[str(f)][3])
    n = t = 0
    for i, (q, r, k) in enumerate(zip(a, s, m)):
        if k > 127 and i // w >= line and r[3] >= 128:
            t += 1
            n += q == r
    return round(100 * n / max(t, 1))


def main():
    d = sys.argv[1]
    for f in [int(v) for v in sys.argv[2].split(',')]:
        print(f, score('%s/frame_%03d.png' % (d, f), f, '%s/../f%03d/masks/frame_%03d.png' % (d, f, f)))


if __name__ == '__main__':
    main()
