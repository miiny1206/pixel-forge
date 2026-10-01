"""Smoke test on synthetic data only (no game files needed).

    python3 tests/smoke_test.py            (from the repository root)

Builds a tiny .spr and .ani in code, then checks:
  - sheetio reads and writes them back byte for byte, and the TEA-sealed name survives a rename
  - grow.py pads a frame and moves its .ani steps so it lands on the same screen pixels
  - if a pxf binary is found: `pxf info`, and import -> export reproduces the .spr exactly
"""
import os, shutil, struct, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'python'))

from pxf_pipeline import sheetio as M, grow as G  # noqa: E402

PER = 4


def sealed(name):
    return M.tea_encrypt((name.encode() + b'\0').ljust(64, b'\0'))


def make_spr(name):
    frs = []
    for i, (w, h) in enumerate([(6, 5), (4, 7), (-1, -1), (5, 5)]):
        if w < 0:
            frs.append((w, h, (M.KEY,)))     # what the shipped placeholders hold
            continue
        px = [M.KEY] * (w * h)
        for y in range(1, h - 1):
            for x in range(1, w - 1):
                px[y * w + x] = M.to565(40 * i + 20, 200 - 30 * y, 10 * x)
        frs.append((w, h, tuple(px)))
    return M.build(sealed(name), frs)


def make_ani(name):
    A = 2
    frame = [0, 1, -1, -1, 3, 1, 3, -1]
    last = [1, 2]
    xyz = []
    for k in range(A * PER):
        xyz += [3 + k % 2, 0, 4 + k % 3]
    xs, zs = xyz[0::3], xyz[2::3]
    sizes = {0: 6, 1: 4, 3: 5}
    for k, f in enumerate(frame):             # y = w - x
        if f >= 0:
            xyz[3 * k + 1] = sizes[f] - xs[k]
    flag = [3] * (A * PER)
    iv = [M.ANI_MAGIC, A] + frame + last + xyz + flag
    return sealed(name) + struct.pack('<%di' % len(iv), *iv)


def screen(spr, ani, a, s):
    """the pixels a step draws, as {(sx, sy): colour} around an anchor at (100, 100)"""
    fr = M.frames(spr)
    an = M.Ani(ani, PER)
    k = a * PER + s
    w, h, px = fr[an.frame[k]]
    x0, y0 = 100 - an.x[k], 100 - an.z[k]
    return {(x0 + i % w, y0 + i // w): v for i, v in enumerate(px) if v != M.KEY}


def main():
    spr, ani = make_spr('Test.spr'), make_ani('Test.ani')
    assert M.build(spr, M.frames(spr)) == spr, 'spr round trip'
    assert M.Ani(ani, PER).blob() == ani, 'ani round trip'
    assert M.header_name(M.renamed(spr, 'Other.spr')) == 'Other.spr', 'rename'
    im = M.to_image(M.frames(spr)[0])
    assert M.from_image(im) == M.frames(spr)[0], 'png round trip'

    grow = {1: (2, 3, 1, 0)}
    spr2, ani2 = G.spr(spr, grow), G.ani(ani, grow, PER)
    w, h, _ = M.frames(spr2)[1]
    assert (w, h) == (4 + 3, 7 + 3), (w, h)
    for a, s in ((0, 1), (1, 1)):
        assert screen(spr, ani, a, s) == screen(spr2, ani2, a, s), 'grown frame moved on screen'
    an2 = M.Ani(ani2, PER)
    for k, f in enumerate(an2.frame):
        if f >= 0:
            assert an2.y[k] == M.frames(spr2)[f][0] - an2.x[k], 'y = w - x broken'
    print('sheetio + grow: ok')

    from pxf_pipeline import pxfbin
    try:
        pxfbin.binary()
    except SystemExit:
        print('pxf binary not found: CLI checks skipped (cargo build --release)')
        return
    d = tempfile.mkdtemp(prefix='pxf_smoke_', dir=os.environ.get('PXF_TMP') or None)
    try:
        p = os.path.join(d, 'Test.spr')
        open(p, 'wb').write(spr)
        pxfbin.run('info', pxfbin.path(p))
        print('pxf info: ok')
        pxfbin.run('import', pxfbin.path(p), pxfbin.path(os.path.join(d, 'out')), '--colorkey', 'cyan')
        q = os.path.join(d, 'Test2.spr')
        pxfbin.run('export', pxfbin.path(os.path.join(d, 'out')), pxfbin.path(q), '--keep-header')
        assert open(q, 'rb').read() == spr, 'pxf import -> export is not byte-identical'
        print('pxf import -> export: byte-identical')
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == '__main__':
    main()
    print('smoke test passed')
