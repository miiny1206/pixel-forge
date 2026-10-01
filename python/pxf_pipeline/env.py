"""Settings from the environment, with an optional `.env` file.

A `.env` next to the project file, in the current directory, or named by PXF_ENV_FILE is
read once; variables already set in the environment win. Values are never printed.
"""
import os

_loaded = False


def _read(path):
    for line in open(path, encoding='utf-8'):
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in '"\'':
            v = v[1:-1]
        os.environ.setdefault(k, v)


def load(extra_dirs=()):
    global _loaded
    if _loaded:
        return
    _loaded = True
    cands = [os.environ.get('PXF_ENV_FILE')] + [os.path.join(d, '.env') for d in extra_dirs] + ['.env']
    for p in cands:
        if p and os.path.isfile(p):
            _read(p)


def get(name, default=None, required=False):
    load()
    v = os.environ.get(name, default)
    if required and not v:
        raise SystemExit('%s is not set (see .env.example)' % name)
    return v
