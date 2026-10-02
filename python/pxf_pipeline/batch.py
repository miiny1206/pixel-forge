"""Run a set of frames through an image model and back, resumably.

    python -m pxf_pipeline.batch run <name> <frame,...|anim:N|all>   send, downscale, place, remix
    python -m pxf_pipeline.batch requeue <name> <frame,...>          ask the model again next run
    python -m pxf_pipeline.batch redo <name>                         rerun everything after the model
    python -m pxf_pipeline.batch status <name>

One frame per request, no reference image (see run()). Per frame:

  1. stock crop (crops.json box) -> `pxf sheet` at the project's scale on a flat grey canvas
  2. the image model edits it (backend.py; project "engine": "chat" or "images")
  3. `pxf downscale` back onto the grid, against the stock palette (+ project "refs")
  4. the crop is placed into the full stock frame, and `pxf remix --alpha` keeps the edit
     only inside the stock figure mask (project "masks" hook)
  5. project "after_remix" hooks fix what the generic steps cannot know
  6. the frame is copied to every "twin" (a frame that is the same drawing, see colour_map)

A frame whose stock outfit mostly survived (leftover.py score above "leftover_max") is sent
again, up to "tries" times, and the best try is kept.

Refusals: a frame the model's safety system declines is recorded as `refused` and is NEVER
resent - not with other wording, not with another crop. It keeps whatever the project falls
back to (stock or a rule-painted version) until a human decides; see transplant/graft/hold
for ways to fill it from frames that did come back.

Everything lands in <work>/runs/<name>/; state.json records what is done, so a rerun continues.
"""
import json, os, shutil, subprocess, sys, time

from .project import current
from . import crops as EX, leftover as LO, pxfbin
from .sheetio import pixels

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOWNSCALE = ['--tol', '24', '--resolve', '--grain', '--register', '--metric', 'redmean']


def pxf(*args):
    return pxfbin.run(*args)


def W(p):
    return pxfbin.path(p)


def frames_of(spec):
    P = current()
    box = P.crops()
    if spec == 'all':
        fr = sorted(int(k) for k in box)
    elif spec.startswith('anim:'):
        a = P.anims()[int(spec[5:])]
        fr = sorted({s['frame'] for s in a['steps'][:a['last'] + 1] if s['frame'] >= 0})
    else:
        fr = sorted(int(x) for x in spec.split(','))
    missing = [f for f in fr if str(f) not in box]
    return [f for f in fr if str(f) in box], missing


def finished():
    """frames that already have a finished version in some run"""
    import glob
    P = current()
    return {int(os.path.basename(p)[6:9]) for p in glob.glob(P.work + 'runs/*/bake/frame_*.png')
            if '/runs/smooth/' not in p}            # smooth output is derived, not a model frame


def colour_map(a, b):
    """stock frame a -> stock frame b as the same drawing: same size and transparency, and
    all but <1% of the opaque pixels follow one colour -> colour mapping. A game may ship a
    frame twice, re-encoded with every colour one RGB565 step off, plus a few pixels that
    really change (the eyes); drawing the two separately made them flicker.
    Returns (map of changed colours, {pixel index: b's colour} for the real changes) or None."""
    from collections import Counter
    from PIL import Image
    P = current()
    A, B = Image.open(P.stock(a)).convert('RGBA'), Image.open(P.stock(b)).convert('RGBA')
    if A.size != B.size:
        return None
    pa, pb = pixels(A), pixels(B)
    votes, opaque = {}, 0
    for p, q in zip(pa, pb):
        if (p[3] >= 128) != (q[3] >= 128):
            return None
        if p[3] >= 128:
            opaque += 1
            votes.setdefault(p[:3], Counter())[q[:3]] += 1
    m = {k: c.most_common(1)[0][0] for k, c in votes.items()}
    fix = {i: q for i, (p, q) in enumerate(zip(pa, pb)) if p[3] >= 128 and m[p[:3]] != q[:3]}
    by, bh = P.crops()[str(b)][1::2]
    line = by + int(0.3 * bh)                    # face and hair only: never paste clothing
    if len(fix) > 0.01 * opaque or any(i // A.size[0] >= line for i in fix):
        return None
    return {k: v for k, v in m.items() if k != v}, fix


def twins(frames):
    """representative -> frames that are the same drawing (colour_map). Only frames of the
    same size are compared, so this stays cheap."""
    from PIL import Image
    P = current()
    by, out = {}, {}
    for f in frames:
        size = Image.open(P.stock(f)).size
        for r in by.get(size, []):
            if colour_map(r, f) is not None:
                out[r].append(f)
                break
        else:
            by.setdefault(size, []).append(f)
            out[f] = [f]
    return out


def recolour(src, dst, cm):
    """the finished frame of the representative, turned into its twin: colours mapped, and
    the twin's own few real changes copied from its stock frame"""
    from PIL import Image
    cmap, fix = cm
    im = Image.open(src).convert('RGBA')
    px = [cmap.get(p[:3], p[:3]) + (p[3],) for p in pixels(im)]
    for i, q in fix.items():
        px[i] = q
    im.putdata(px)
    im.save(dst)


class Refused(Exception):
    """the model's safety filter declined the frame. Never retried, never re-sent with other
    wording or a different crop."""


class QuotaOut(Exception):
    """every credential for the model is rate limited (HTTP 429 / RESOURCE_EXHAUSTED); the
    run stops instead of failing every remaining frame in a second each"""


def engine():
    return os.environ.get('PXF_ENGINE') or current().get('engine', 'chat')


def edit(d):
    """one model edit of d/in.png -> d/out.png, retried twice when the answer simply has no
    image (text only, or empty with no finish reason - seen on 4 of 17 frames). An answer
    that names a safety or block reason is a refusal and is not retried."""
    P = current()
    env = P.child_env()
    env['PYTHONPATH'] = PKG_DIR + os.pathsep + env.get('PYTHONPATH', '')
    for attempt in range(3):
        r = subprocess.run([sys.executable, '-m', 'pxf_pipeline.backend', engine(), d + 'in.png', d + 'out.png',
                            '--prompt-file', d + 'prompt.txt'], capture_output=True, text=True, env=env)
        if not r.returncode:
            return attempt
        msg = r.stdout + r.stderr
        if 'RESOURCE_EXHAUSTED' in msg or 'cooling down' in msg or 'HTTP 429' in msg:
            raise QuotaOut(msg.strip()[-300:])
        if any(w in msg.upper() for w in ('SAFETY', 'BLOCK', 'PROHIBITED', 'MODERATION')):
            raise Refused(msg.strip()[:300])
    raise RuntimeError('no image after 3 tries: ' + msg.strip()[-300:])


def finish_frame(name, f, group):
    """model output -> baked frames, for one representative and its twins: downscale, place,
    mask, remix, project hooks, recolour. No request is made, so it can be rerun on every
    saved out.png after a pipeline fix (`redo`)."""
    P = current()
    root = P.work + 'runs/%s/' % name
    d = root + 'f%03d/' % f
    refs = []
    for r in P.get('refs', []):
        if os.path.exists(P.work + r):           # an optional palette folder may be absent
            refs += ['--ref', W(P.work + r)]
    pxf('downscale', W(d + 'out.png'), W(d + 'down'), '--manifest', W(d + 'in.json'),
        *refs, '--ref', W(d + 'crop'), '--bg', P.get('bg', '808080'),
        *P.get('downscale_flags', DOWNSCALE), '--confidence', W(d + 'conf'), '--report', W(d + 'rep.json'))
    x = json.load(open(d + 'rep.json'))['frames'][0]
    EX.place(d + 'down', d + 'placed', [f])
    EX.masks(d + 'masks', [f], grow=P.get('mask_grow', 0))
    pxf('remix', W(d + 'placed'), W(d + 'final'), '--orig', W(P.work + 'all'),
        '--mask', W(d + 'masks'), '--alpha')
    final = d + 'final/frame_%03d.png' % f
    for hook in P.hook('after_remix', []):
        hook(final, f, d)
    os.makedirs(root + 'bake', exist_ok=True)
    for g in group:
        cm = colour_map(f, g) if g != f else ({}, {})
        recolour(final, root + 'bake/frame_%03d.png' % g, cm)
    return x


def redo(name):
    """rerun finish_frame on every frame of a run that already has a model output"""
    import contextlib, io
    P = current()
    sp = P.work + 'runs/%s/state.json' % name
    st = json.load(open(sp))
    n = 0
    for f, v in st['frames'].items():
        if v['status'] != 'ok':
            continue
        with contextlib.redirect_stdout(io.StringIO()):
            x = finish_frame(name, int(f), v['twins'] or [int(f)])
        v.update(weak=x['weak_pixels'], grid_fit=round(x['grid_fit'], 3), alpha=x['alpha_agreement'])
        n += 1
    json.dump(st, open(sp, 'w'), indent=1)
    print('%s: %d frame(s) redone' % (name, n))


def run(name, spec):
    """One frame per request, no reference image. Measured with a Gemini image model: with
    several frames or a reference image it redraws the layout (copied the reference
    outright); one frame alone keeps pose, size and place, grid fit 96-97% at scale 1.000,
    155-295 weak px - better than a sheet-based image-edit model's 76-88%."""
    P = current()
    root = P.work + 'runs/%s/' % name
    os.makedirs(root, exist_ok=True)
    sp = root + 'state.json'
    st = json.load(open(sp)) if os.path.exists(sp) else {}
    if 'reps' not in st:
        frames, missing = frames_of(spec)
        done = finished()
        todo = [f for f in frames if f not in done]
        st = {'spec': spec, 'engine': engine(), 'reps': {str(k): v for k, v in twins(todo).items()},
              'no_crop': missing, 'already': sorted(set(frames) & done), 'frames': {}}
        json.dump(st, open(sp, 'w'), indent=1)
    reps = {int(k): v for k, v in st['reps'].items()}
    print('%s: %d frames to do as %d requests (%d already finished elsewhere, %d without crop)'
          % (name, sum(len(v) for v in reps.values()), len(reps), len(st['already']), len(st['no_crop'])))
    prompt = open(P.path(P.get('prompt_file', 'prompt.txt')), encoding='utf-8').read()
    leftover_max, tries_max = P.get('leftover_max', 28), P.get('tries', 3)
    os.makedirs(root + 'bake', exist_ok=True)
    for i, (f, group) in enumerate(sorted(reps.items())):
        if str(f) in st['frames'] and st['frames'][str(f)]['status'] != 'failed':
            continue                                 # failed ones are tried again on a rerun
        d = root + 'f%03d/' % f
        t = time.time()
        try:
            EX.crops(d + 'crop', [f])
            pxf('sheet', W(d + 'crop'), W(d + 'in.png'), '--scale', P.get('scale', 5), '--gap', P.get('gap', 8),
                '--rows', 1, '--bg', P.get('bg', '808080'), '--canvas', '%dx%d' % ((P.get('canvas', 1024),) * 2))
            open(d + 'prompt.txt', 'w', encoding='utf-8').write(prompt)
            tries, best = 0, None
            for attempt in range(tries_max):
                tries += edit(d)
                x = finish_frame(name, f, group)
                sc = LO.score(root + 'bake/frame_%03d.png' % f, f, d + 'masks/frame_%03d.png' % f)
                shutil.copy(d + 'out.png', d + 'out_try%d.png' % attempt)
                if best is None or sc < best[0]:
                    best = (sc, attempt, x)
                if sc < leftover_max:
                    break
            last = attempt
            sc, attempt, x = best
            if attempt != last:                       # a worse later try is on disk: rebuild the kept one
                shutil.copy(d + 'out_try%d.png' % attempt, d + 'out.png')
                x = finish_frame(name, f, group)
            v = {'status': 'ok', 'retries': tries, 'leftover': sc, 'kept_try': attempt, 'weak': x['weak_pixels'],
                 'opaque': x['opaque'], 'grid_fit': round(x['grid_fit'], 3), 'alpha': x['alpha_agreement'],
                 'twins': group, 'engine': engine()}
        except Refused as e:
            v = {'status': 'refused', 'reason': str(e)[:300], 'twins': group}
        except QuotaOut:
            print('quota exhausted at frame %d - stopping; rerun the same command later' % f, flush=True)
            break
        except Exception as e:
            v = {'status': 'failed', 'reason': str(e)[-300:], 'twins': group}
        st['frames'][str(f)] = v
        json.dump(st, open(sp, 'w'), indent=1)
        print('[%d/%d] frame %d%s: %s (%.0fs)' % (i + 1, len(reps), f, ' +%s' % group[1:] if len(group) > 1 else '',
              v['status'] + (' grid %.0f%% weak %d' % (100 * v['grid_fit'], v['weak']) if v['status'] == 'ok'
                             else ' - ' + v['reason'][:120]), time.time() - t), flush=True)
    status(name)


def requeue(name, frames):
    """send frames back to the model on the next run (QA found a wrong drawing). The state
    entry is dropped so run() sees them as not done; the baked file stays until the new one
    replaces it. Takes representatives or their twins."""
    sp = current().work + 'runs/%s/state.json' % name
    st = json.load(open(sp))
    rep_of = {g: int(r) for r, grp in st['reps'].items() for g in grp}
    done = []
    for f in frames:
        r = rep_of.get(f)
        if r is not None and str(r) in st['frames']:
            v = st['frames'].pop(str(r))
            st.setdefault('requeued', {})[str(r)] = v
            done.append(r)
    json.dump(st, open(sp, 'w'), indent=1)
    print('%s: requeued %s' % (name, sorted(set(done))))


def status(name):
    st = json.load(open(current().work + 'runs/%s/state.json' % name))
    by = {}
    for f, v in st['frames'].items():
        by.setdefault(v['status'], []).append(int(f))
    print('%s: %d/%d done | %s' % (name, len(st['frames']), len(st['reps']),
          ' | '.join('%s %d %s' % (k, len(v), sorted(v)) for k, v in sorted(by.items()))))


def main():
    a = sys.argv[1:]
    if len(a) == 3 and a[0] == 'run':
        run(a[1], a[2])
    elif len(a) == 3 and a[0] == 'requeue':
        requeue(a[1], [int(v) for v in a[2].split(',')])
    elif len(a) == 2 and a[0] == 'redo':
        redo(a[1])
    elif len(a) == 2 and a[0] == 'status':
        status(a[1])
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
