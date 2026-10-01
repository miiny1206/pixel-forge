"""Every finished frame -> one directory -> the game, in one step.

    python -m pxf_pipeline.finish [run ...]

1. For each run named on the command line that has runs/<run>/aseprite/<run>.aseprite, its
   frames are read back out of Aseprite into runs/<run>/bake (reference layers are never
   flattened in; see asebridge.py).
2. smooth.py rebuilds runs/smooth from scratch, so each animation is one drawing.
3. finish.after_smooth steps run (project commands, e.g. a cleanup pass on runs/smooth/bake).
4. Every runs/*/bake is gathered into <work>/bake_all: later runs win, runs/graft wins over
   them, runs/smooth wins over all. A frame still identical to stock is left out, so the
   bake step can treat it as "not redrawn" (e.g. rule-paint it) instead of shipping the old
   outfit. Frames listed in finish.keep_stock (a JSON list in the work dir) and
   finish.always_ship are copied from stock on purpose.
5. finish.post steps run on bake_all, in order (character-specific fixes).
6. hold.py, when the project has a "hold" table.
7. finish.bake: the command that writes the game files; "{bake_all}" is replaced.

Project:

    "finish": {
      "after_smooth": [["steps/cleanup.py"]],
      "keep_stock": "keep_stock.json",
      "always_ship": [189],
      "post": [["steps/beachfx.py"], ["steps/swordfix.py"]],
      "bake": ["bake/bkbake.py", "--frames", "{bake_all}"]
    }

Each step is [script, args...], a Python file relative to the project, run with the same
interpreter and PXF_PROJECT set.
"""
import glob, json, os, shutil, subprocess, sys
from PIL import Image

from .project import current

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def step(P, cmd, bake_all, quiet=False):
    env = P.child_env()
    env['PYTHONPATH'] = PKG_DIR + os.pathsep + env.get('PYTHONPATH', '')
    args = [a.replace('{bake_all}', bake_all).replace('{work}', P.work) for a in cmd]
    subprocess.run([sys.executable, P.path(args[0])] + args[1:], check=True, env=env,
                   stdout=subprocess.DEVNULL if quiet else None)


def module(P, name, *args, quiet=False):
    env = P.child_env()
    env['PYTHONPATH'] = PKG_DIR + os.pathsep + env.get('PYTHONPATH', '')
    subprocess.run([sys.executable, '-m', 'pxf_pipeline.' + name] + list(args), check=True, env=env,
                   stdout=subprocess.DEVNULL if quiet else None)


def identical(a, s):
    """same size and the same RGBA everywhere (ImageChops.difference on RGBA compares alpha
    only, so it is not used here)"""
    return a.size == s.size and a.tobytes() == s.tobytes()


def main():
    P = current()
    cfg = P.get('finish', {})
    out = P.work + 'bake_all/'
    for run in sys.argv[1:]:
        r = P.work + 'runs/%s/' % run
        module(P, 'asebridge', 'from', r + 'aseprite/%s.aseprite' % run, r + 'bake')
    shutil.rmtree(P.work + 'runs/smooth', ignore_errors=True)
    module(P, 'smooth', quiet=True)
    for cmd in cfg.get('after_smooth', []):
        step(P, cmd, out, quiet=True)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    same = 0
    paths = sorted((p for p in glob.glob(P.work + 'runs/*/bake/frame_*.png') if '/runs/smooth/' not in p),
                   key=lambda p: ('/runs/graft/' in p, p))
    for p in paths + sorted(glob.glob(P.work + 'runs/smooth/bake/frame_*.png')):
        name = os.path.basename(p)
        a, s = Image.open(p).convert('RGBA'), Image.open(P.work + 'all/' + name).convert('RGBA')
        if identical(a, s):
            same += 1
            continue
        shutil.copy(p, out + name)
    ks = cfg.get('keep_stock')
    keep = json.load(open(P.work + ks)) if ks and os.path.exists(P.work + ks) else []
    for f in keep:
        shutil.copy(P.stock(f), out + 'frame_%03d.png' % f)
    for f in cfg.get('always_ship', []):
        if not os.path.exists(out + 'frame_%03d.png' % f):
            shutil.copy(P.stock(f), out + 'frame_%03d.png' % f)
    for cmd in cfg.get('post', []):
        step(P, cmd, out)
    if P.get('hold'):
        module(P, 'hold')
    n = len(os.listdir(out))
    print('%d finished frame(s) gathered, %d identical to stock left out' % (n, same))
    if cfg.get('bake'):
        step(P, cfg['bake'], out)


if __name__ == '__main__':
    main()
