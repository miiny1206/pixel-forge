"""The single-sprite commands (forge.py) as an MCP server over stdio, so an agent can draw,
animate, edit and look at sprites without writing a script.

    claude mcp add -s user pixel-forge -- pixel-forge mcp

Every tool answers with the files it wrote, the pipeline's log and a zoomed preview the
agent can see. Paths may be absolute or relative to the directory the client started the
server in. The model endpoint comes from `.env` (see env.py); nothing from it is ever
returned.
"""
import contextlib, io, os

from PIL import Image as PILImage
from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import forge

server = MCPServer('pixel-forge', instructions=(
    'Pixel art that stays pixel art: every result is on one grid, on a palette, with real '
    'transparency. create draws from text (a model call, ~1 min); create_set draws a matching '
    'set in one picture; animate turns a finished sprite into a loop on the same canvas; edit '
    'changes one thing; pixelize turns a picture you already have (another tool\'s render, an '
    'upscale) into a sprite without a model call; look shows any PNG zoomed. Palette specs: '
    'a preset (see palettes), comma-separated hex, or a PNG/folder whose colours are the palette.'))


def _path(p):
    return os.path.abspath(os.path.expanduser(p))


def _size(spec):
    return forge.size(spec) if spec else None


def _run(fn, *args, **kwargs):
    """fn with its prints captured (stdout is the MCP channel) and its failures as tool errors"""
    log = io.StringIO()
    try:
        with contextlib.redirect_stdout(log):
            fn(*args, **kwargs)
    except (SystemExit, RuntimeError, OSError, ValueError) as e:
        raise ToolError(('%s\n%s' % (log.getvalue().strip(), e)).strip()) from e
    return log.getvalue().strip()


def _preview(path, side=768):
    """a PNG enlarged by a whole factor to about `side` px, so every art pixel stays square"""
    im = PILImage.open(path).convert('RGBA')
    buf = io.BytesIO()
    forge.zoomed(im, max(1, side // max(im.size))).save(buf, 'PNG')
    return Image(data=buf.getvalue(), format='png')


def _answer(log, files, preview):
    return ['%s\n\nfiles:\n%s' % (log, '\n'.join(files)), _preview(preview)]


@server.tool()
def create(outdir: str, prompt: str, size: str = '32x32', variants: int = 1, palette: str = '',
           colors: int = 16, style: str = '', name: str = 'sprite', reuse: bool = False):
    """Draw a new sprite from text: NAME.png at exactly WxH, transparent, on `palette` (or
    reduced to `colors`). variants > 1 draws K candidates NAME_1..K. reuse=True redoes only
    the pixel steps on the picture already in outdir/raw (another size or palette, no model
    call)."""
    outdir = _path(outdir)
    log = _run(forge.create, outdir, prompt, forge.size(size), variants, palette or None, colors,
               style or None, name, reuse=reuse)
    tags = ['%s_%d' % (name, i) for i in range(1, variants + 1)] if variants > 1 else [name]
    files = [os.path.join(outdir, t + '.png') for t in tags]
    shown = os.path.join(outdir, name + '_variants.png') if variants > 1 else files[0]
    return _answer(log, files, shown)


@server.tool()
def create_set(outdir: str, items: list[str], theme: str = '', size: str = '32x32',
               palette: str = '', colors: int = 16, style: str = '', reuse: bool = False):
    """Draw a whole set (icons, items, tiles) in ONE picture so it shares one hand and one
    palette. Each item is "name=description" or just a description; one NAME.png per item."""
    outdir = _path(outdir)
    named = [t.partition('=') for t in items]
    names = [k.strip() if sep else '%02d' % (i + 1) for i, (k, sep, _) in enumerate(named)]
    what = [v.strip() if sep else k.strip() for k, sep, v in named]
    log = _run(forge.create_set, outdir, what, forge.size(size), names, palette or None, colors,
               style or None, theme=theme or None, reuse=reuse)
    files = [p for p in (os.path.join(outdir, n + '.png') for n in names) if os.path.isfile(p)]
    return _answer(log, files, os.path.join(outdir, 'set_contact.png'))


@server.tool()
def animate(sprite: str, outdir: str, prompt: str, frames: int = 4, pad: str = '',
            palette: str = '', fps: float = 8, lock: str = '', min_region: int = 8,
            redraw_first: bool = False):
    """One finished sprite -> a looping animation on the same canvas: frames/00.png..,
    sheet.png + sheet.json, anim.gif/webp. pad "N" or "L,T,R,B" grows the canvas for motion
    that leaves the sprite's box; lock "x,y,w,h;..." copies those boxes (a face, a held prop)
    from the sprite into every frame. Keep frames small (4-8)."""
    outdir = _path(outdir)
    log = _run(forge.animate, _path(sprite), outdir, prompt, frames, None, pad or None,
               palette or None, fps, not redraw_first, None, min_region, lock or None)
    fdir = os.path.join(outdir, 'frames')
    shown = os.path.join(outdir, 'frames_contact.png')
    forge.contact([PILImage.open(os.path.join(fdir, f)) for f in sorted(os.listdir(fdir))], shown)
    files = [os.path.join(outdir, f) for f in ('sheet.png', 'sheet.json', 'anim.gif', 'anim.webp')]
    return _answer(log, files, shown)


@server.tool()
def edit(sprite: str, out: str, prompt: str, palette: str = '', lock: str = '',
         min_region: int = 8):
    """Change one thing on a sprite, same canvas and grid. A new colour must be allowed in
    through `palette`. Answers with before | after."""
    src, out = _path(sprite), _path(out)
    log = _run(forge.edit, src, out, prompt, palette or None, None, min_region, lock or None)
    shown = os.path.splitext(out)[0] + '_work/before_after.png'
    forge.contact([PILImage.open(src), PILImage.open(out)], shown)
    return _answer(log, [out], shown)


@server.tool()
def pixelize(picture: str, out: str, size: str = '', palette: str = '', colors: int = 16,
             bg: str = ''):
    """A picture you already have (another generator's render, a soft upscale) -> a real
    sprite: snapped to the grid it was drawn on, flat background keyed out, fitted into
    size WxH if given, put on a palette. No model call. bg is the background hex (default:
    the border colour). Not for art that is already one pixel per pixel."""
    out = _path(out)
    log = _run(forge.pixelize, _path(picture), out, _size(size), palette or None, colors,
               bg.lstrip('#') or None)
    return _answer(log, [out], out)


@server.tool()
def bundle(framedir: str, fps: float = 8, zoom: int = 8, out: str = ''):
    """PNG frames in a folder -> sheet.png + sheet.json (horizontal strip), anim.gif/webp and
    zoomed previews."""
    sheet = forge.bundle(_path(framedir), fps, zoom, _path(out) if out else None)
    return _answer('', [sheet], sheet)


@server.tool()
def look(path: str, size: int = 768):
    """Show a PNG enlarged by a whole factor to about `size` px, pixels kept square, with
    its dimensions and colour count."""
    p = _path(path)
    im = PILImage.open(p)
    return ['%s: %dx%d, %d colours' % (p, im.width, im.height, len(forge.colours_of(im))),
            _preview(p, size)]


@server.tool()
def palettes() -> str:
    """The preset palettes by name."""
    return '\n'.join('%s: %s' % kv for kv in forge.PRESETS.items())


def main():
    server.run()


if __name__ == '__main__':
    main()
