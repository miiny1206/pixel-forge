"""Aseprite <-> a pxf frame directory, for hand edits. Thin wrapper over aseprite/*.lua.

    python -m pxf_pipeline.asebridge to   <pxf dir> <out.aseprite> [--layers d1,d2] [--unaligned]
    python -m pxf_pipeline.asebridge from <in.aseprite> <pxf dir> [--layers art,overlay]

`to` places every frame at its .ani anchor (from manifest.json) so they line up; <= 255
colours becomes Indexed mode with the palette locked. Extra --layers directories come in as
reference layers, which `from` never flattens into the art, even if they are visible. Then
`pxf export <pxf dir> <name>.spr` as usual. The round trip dir -> .aseprite -> dir is
byte-identical on a 379-frame sheet.

The Aseprite binary is ASEPRITE (environment / .env), else `aseprite` on PATH. A Windows
Aseprite called from WSL gets Windows paths; it cannot write to WSL's /tmp, so keep the
directories on a Windows drive.
"""
import os, shutil, subprocess, sys

from . import env

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'aseprite')


def exe():
    a = env.get('ASEPRITE') or shutil.which('aseprite') or shutil.which('Aseprite')
    if not a:
        raise SystemExit('Aseprite not found: set ASEPRITE in .env')
    return a


def path(p):
    if exe().lower().endswith('.exe') and os.name == 'posix' and shutil.which('wslpath'):
        return subprocess.run(['wslpath', '-w', os.path.abspath(p)], capture_output=True, text=True,
                              check=True).stdout.strip()
    return os.path.abspath(p)


def run(script, **params):
    cmd = [exe(), '-b']
    for k, v in params.items():
        cmd += ['--script-param', '%s=%s' % (k, v)]
    cmd += ['--script', path(os.path.join(HERE, script))]
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    print(out)
    if r.returncode or 'error' in out.lower():
        raise SystemExit('aseprite failed')


def opt(name):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else None


def main():
    if len(sys.argv) < 4 or sys.argv[1] not in ('to', 'from'):
        raise SystemExit(__doc__)
    a, b = sys.argv[2], sys.argv[3]
    layers = opt('--layers')
    if sys.argv[1] == 'to':
        p = {'dir': path(a), 'out': path(b)}
        if layers:
            p['layers'] = ';'.join(path(d) for d in layers.split(','))
        if '--unaligned' in sys.argv:
            p['aligned'] = '0'
        run('to_aseprite.lua', **p)
    else:
        p = {'in': path(a), 'dir': path(b)}
        if layers:
            p['layers'] = layers.replace(',', ';')
        run('from_aseprite.lua', **p)


if __name__ == '__main__':
    main()
