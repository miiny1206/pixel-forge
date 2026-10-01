"""Run the `pxf` CLI.

The binary is PXF_BIN, else the project's "pxf" key, else `pxf` on PATH, else
target/release/pxf[.exe] in this repository. A Windows build (`pxf.exe`) called from WSL gets
its path arguments converted with `wslpath -w`, so one Windows build serves both sides.
"""
import os, shutil, subprocess

from . import env

_bin = None
HERE = os.path.dirname(os.path.abspath(__file__))


def binary():
    global _bin
    if _bin:
        return _bin
    cands = [env.get('PXF_BIN')]
    try:
        from .project import current
        p = current().get('pxf')
        cands.append(current().path(p) if p else None)
    except SystemExit:
        pass
    cands.append(shutil.which('pxf'))
    repo = os.path.dirname(os.path.dirname(HERE))
    cands += [os.path.join(repo, 'target', 'release', n) for n in ('pxf', 'pxf.exe')]
    for c in cands:
        if c and os.path.isfile(c):
            _bin = c
            return c
    raise SystemExit('pxf not found: build it with `cargo build --release` or set PXF_BIN')


def _wsl_exe(b):
    return b.lower().endswith('.exe') and os.name == 'posix' and shutil.which('wslpath')


def win(p):
    return subprocess.run(['wslpath', '-w', os.path.abspath(p)], capture_output=True, text=True,
                          check=True).stdout.strip()


def path(p):
    """a filesystem path as the pxf binary wants it"""
    return win(p) if _wsl_exe(binary()) else str(p)


def run(*args):
    """pxf <args>; arguments that are paths must already be passed through path()"""
    b = binary()
    r = subprocess.run([b] + [str(a) for a in args], capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    if r.returncode:
        raise RuntimeError(out)
    return out
