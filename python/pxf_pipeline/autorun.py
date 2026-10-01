"""Keep a long run going across rate-limit windows, unattended.

Every PROBE_EVERY seconds one tiny text-only chat call to the image model is made (backend
.available()), which the endpoint answers 429 while the credentials cool down. When it
answers, `batch run <name> all` runs until done or until the quota is out again, then
`finish` bakes. Ends when no frame is left to do. A 429 message is not a countdown (a gateway
may repeat the last error it saw for hours), hence polling instead of sleeping for it.

    python -m pxf_pipeline.autorun <name>        log -> <work>/runs/<name>.log
"""
import json, os, subprocess, sys, time

from .project import current
from . import backend

PROBE_EVERY = 1800
PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def left(name):
    """frames never tried, or failed only because the quota was out. A frame that failed
    for another reason (no image after 3 tries, a refusal) is not waited for."""
    st = json.load(open(current().work + 'runs/%s/state.json' % name))
    out = []
    for f in st['reps']:
        v = st['frames'].get(f)
        if v is None or (v['status'] == 'failed' and ('RESOURCE_EXHAUSTED' in v.get('reason', '')
                                                      or 'cooling down' in v.get('reason', ''))):
            out.append(f)
    return out


def main():
    P = current()
    name = sys.argv[1]
    env = P.child_env()
    env['PYTHONPATH'] = PKG_DIR + os.pathsep + env.get('PYTHONPATH', '')
    log = open(P.work + 'runs/%s.log' % name, 'a')
    while left(name):
        if not backend.available():
            log.write('%s quota still out, %d frames left\n' % (time.strftime('%H:%M'), len(left(name))))
            log.flush()
            time.sleep(PROBE_EVERY)
            continue
        log.write('%s quota back, resuming\n' % time.strftime('%H:%M'))
        log.flush()
        subprocess.run([sys.executable, '-u', '-m', 'pxf_pipeline.batch', 'run', name, 'all'],
                       env=env, stdout=log, stderr=log)
        subprocess.run([sys.executable, '-u', '-m', 'pxf_pipeline.finish'], env=env, stdout=log, stderr=log)
    log.write('%s all frames done\n' % time.strftime('%H:%M'))


if __name__ == '__main__':
    main()
