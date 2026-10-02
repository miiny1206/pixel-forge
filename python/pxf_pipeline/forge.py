"""Single-sprite commands: no project.json, one sprite or one animation at a time.

    python -m pxf_pipeline create  <outdir> --prompt TEXT [--size WxH] [--variants K]
                                   [--palette SPEC | --colors N] [--style TEXT]
                                   [--name NAME] [--engine images|chat] [--model M] [--reuse]
    python -m pxf_pipeline create  <outdir> --items "name=what|name=what|..."|items.txt
                                   [--prompt THEME] [--size WxH] [--palette SPEC | --colors N] ...
    python -m pxf_pipeline animate <sprite.png> <outdir> --prompt TEXT --frames N
                                   [--rows R] [--pad N|L,T,R,B] [--palette SPEC]
                                   [--fps F] [--redraw-first] [--min-region N]
                                   [--lock x,y,w,h[;...]] [--model M]
    python -m pxf_pipeline edit    <sprite.png> <out.png> --prompt TEXT
                                   [--palette SPEC] [--min-region N] [--lock x,y,w,h[;...]]
                                   [--model M]
    python -m pxf_pipeline bundle  <framedir> [--fps F] [--zoom K] [--out DIR]
    python -m pxf_pipeline palettes

create   draws a new sprite from text. The model's picture is snapped back onto the grid it
         actually drew (`pxf snap`), the flat background is keyed out, the art is fitted
         into WxH and put on a palette. --variants draws K candidates side by side.
         With --items it draws a whole set (icons, items, tiles) in one picture, so the
         set shares one style and one palette; one item per line in a file, or
         `|`-separated, each optionally `name=description`; --prompt is then the theme.
         --reuse redoes the pixel steps on the pictures already in raw/ without asking
         the model again (another size, palette or colour count).
animate  turns one finished sprite into N frames of the same canvas. The sprite is laid
         out N times on one sheet at a known scale and the model redraws the copies in a
         single request, so every frame is drawn together and stays one design; the grid
         is then read back by vote (`pxf downscale`), not guessed. Frame 1 is the input,
         pixel for pixel, unless --redraw-first. --pad makes room for motion that leaves
         the sprite's box (a jump, a swing). Differences from the input smaller than
         --min-region connected pixels (default 8) are redraw noise and are put back, so
         only real motion moves; 0 keeps everything the model drew. --lock copies the
         given boxes (sprite pixels, after --pad) from the input into every frame.
edit     one sprite, one change, same canvas, back on the sprite's own grid.
bundle   frames -> sheet.png + sheet.json (a horizontal strip), anim.gif, anim.webp and
         zoomed previews; animate and create call it on their output.

--palette SPEC  a preset name (`palettes` lists them), comma-separated hex, a .hex/.txt
                file (one colour per line), a GIMP .gpl, or PNGs (file or folder) whose
                opaque colours are the palette. For animate/edit it adds to the sprite's
                own colours: a new colour must be allowed to appear.
--colors N      for create without a palette: reduce to N colours (default 16).

The background the model is asked to paint is picked as the candidate furthest from the
palette, so keying it out cannot eat a colour the art uses.
"""
import glob, json, math, os, shutil, sys
from PIL import Image

from . import backend, pxfbin
from .sheetio import pixels

PRESETS = {
    'pico8': '000000,1d2b53,7e2553,008751,ab5236,5f574f,c2c3c7,fff1e8,'
             'ff004d,ffa300,ffec27,00e436,29adff,83769c,ff77a8,ffccaa',
    'sweetie16': '1a1c2c,5d275d,b13e53,ef7d57,ffcd75,a7f070,38b764,257179,'
                 '29366f,3b5dc9,41a6f6,73eff7,f4f4f4,94b0c2,566c86,333c57',
    'gameboy': '0f380f,306230,8bac0f,9bbc0f',
}
BACKGROUNDS = [('ff00ff', 'magenta'), ('00ff00', 'pure green'), ('00ffff', 'cyan'), ('0000ff', 'pure blue')]
STYLE = ('Pixel art sprite. Chunky square pixels on one strict grid, every pixel the same size. '
         'Flat colours, a dark outline, no anti-aliasing, no gradients, no blur, no dithering noise. '
         'No text, no letters, no border, no ground shadow, no scenery.')
SHEET_SIDE = 1024


# --- colours ------------------------------------------------------------------------------

def hexrgb(h):
    h = h.strip().lstrip('#')
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def tohex(c):
    return '%02x%02x%02x' % tuple(c[:3])


def dist(a, b):
    """redmean colour distance: closer to how far apart two colours look than plain RGB"""
    r = (a[0] + b[0]) / 2
    dr, dg, db = a[0] - b[0], a[1] - b[1], a[2] - b[2]
    return math.sqrt((2 + r / 256) * dr * dr + 4 * dg * dg + (2 + (255 - r) / 256) * db * db)


def colours_of(im):
    return sorted({p[:3] for p in pixels(im.convert('RGBA')) if p[3] >= 128})


def palette(spec):
    """[(r, g, b)] for a --palette SPEC, None for no spec"""
    if not spec:
        return None
    if spec in PRESETS:
        spec = PRESETS[spec]
    if os.path.isdir(spec) or spec.lower().endswith('.png'):
        files = sorted(glob.glob(os.path.join(spec, '**', '*.png'), recursive=True)) if os.path.isdir(spec) else [spec]
        return sorted({c for f in files for c in colours_of(Image.open(f))})
    if os.path.isfile(spec):
        out = []
        for line in open(spec, encoding='utf-8'):
            line = line.strip()
            if not line or line.startswith(('#', 'GIMP', 'Name', 'Columns')):
                continue
            parts = line.split()
            if spec.lower().endswith('.gpl') and len(parts) >= 3 and all(p.isdigit() for p in parts[:3]):
                out.append(tuple(int(p) for p in parts[:3]))
            else:
                out.append(hexrgb(parts[0]))
        return out
    return [hexrgb(h) for h in spec.split(',') if h.strip()]


def pick_background(pal):
    if not pal:
        return BACKGROUNDS[0]
    return max(BACKGROUNDS, key=lambda b: min(dist(hexrgb(b[0]), c) for c in pal))


def key_out(im, bg, tol=110):
    """the painted background -> transparent. Every pixel near it goes, not only the ones
    touching the border: a ring or a gap between legs is background too, and the background
    was chosen far from every colour the art may use."""
    im = im.convert('RGBA')
    bgc = hexrgb(bg)
    im.putdata([(0, 0, 0, 0) if p[3] < 128 or dist(p, bgc) < tol else p[:3] + (255,) for p in pixels(im)])
    return im


def flatten(path, bg):
    """the generated picture on the background colour it was asked for, in place: a model
    that answered with transparency instead gets the same flat background `pxf snap` keys"""
    im = Image.open(path)
    if im.mode in ('RGBA', 'LA', 'P'):
        im = im.convert('RGBA')
        out = Image.new('RGBA', im.size, hexrgb(bg) + (255,))
        out.alpha_composite(im)
        out.convert('RGB').save(path)


def to_palette(im, pal):
    cache = {}

    def near(c):
        if c not in cache:
            cache[c] = min(pal, key=lambda p: dist(p, c))
        return cache[c]
    return Image.frombytes('RGBA', im.size, b''.join(
        bytes(near(p[:3]) + (255,)) if p[3] >= 128 else b'\0\0\0\0' for p in pixels(im.convert('RGBA'))))


def reduce_colours(im, n):
    """n colours without dithering, chosen from the opaque pixels only"""
    im = im.convert('RGBA')
    opaque = [p[:3] for p in pixels(im) if p[3] >= 128]
    if len(set(opaque)) <= n:
        return im
    strip = Image.new('RGB', (len(opaque), 1))
    strip.putdata(opaque)
    q = strip.quantize(n, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    flat = q.getpalette()[:3 * n]
    return to_palette(im, [tuple(flat[i:i + 3]) for i in range(0, len(flat), 3)])


# --- geometry -----------------------------------------------------------------------------

def trim(im):
    box = im.getchannel('A').getbbox()
    return im.crop(box) if box else im


def shrink(im, w, h):
    """pixel art down to w x h: each target pixel is the majority of the source pixels it
    covers (opaque only if at least half are), never an average, so no new colours appear"""
    sw, sh = im.size
    src = im.load()
    out = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    dst = out.load()
    for y in range(h):
        y0, y1 = y * sh // h, max((y + 1) * sh // h, y * sh // h + 1)
        for x in range(w):
            x0, x1 = x * sw // w, max((x + 1) * sw // w, x * sw // w + 1)
            votes, opaque, total = {}, 0, 0
            for yy in range(y0, y1):
                for xx in range(x0, x1):
                    p = src[xx, yy]
                    total += 1
                    if p[3] >= 128:
                        opaque += 1
                        votes[p] = votes.get(p, 0) + 1
            if votes and opaque * 2 >= total:
                dst[x, y] = max(votes, key=votes.get)
    return out


def fit(im, w, h, margin=1):
    """the art, trimmed, centred on a w x h canvas; shrunk (never enlarged) if it does not fit"""
    art = trim(im)
    aw, ah = art.size
    room_w, room_h = max(w - 2 * margin, 1), max(h - 2 * margin, 1)
    if aw > room_w or ah > room_h:
        f = min(room_w / aw, room_h / ah)
        art = trim(shrink(art, max(1, round(aw * f)), max(1, round(ah * f))))
    out = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    out.paste(art, ((w - art.width) // 2, (h - art.height) // 2), art)
    return out


def stabilize(frame, base, min_region=8, tol=48):
    """`frame` with every small scattered difference from `base` put back.

    A model redraws the whole cell, so pixels that should not move come back a shade off
    or one block over, and the loop shimmers. Real motion changes a connected patch (an arm,
    a closed eye); redraw noise is specks. Patches of differing pixels smaller than
    min_region (8-connected) take the base pixel."""
    w, h = base.size
    a, b = frame.load(), base.load()

    def differs(x, y):
        p, q = a[x, y], b[x, y]
        if (p[3] >= 128) != (q[3] >= 128):
            return True
        return p[3] >= 128 and dist(p, q) > tol
    diff = {(x, y) for y in range(h) for x in range(w) if differs(x, y)}
    out = frame.copy()
    o = out.load()
    seen = set()
    for start in diff:
        if start in seen:
            continue
        patch, todo = [], [start]
        seen.add(start)
        while todo:
            x, y = todo.pop()
            patch.append((x, y))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    n = (x + dx, y + dy)
                    if n in diff and n not in seen:
                        seen.add(n)
                        todo.append(n)
        if len(patch) < min_region:
            for x, y in patch:
                o[x, y] = b[x, y]
    return out


def boxes(spec):
    """[(x, y, w, h)] from "x,y,w,h;x,y,w,h" """
    return [tuple(int(v) for v in b.split(',')) for b in (spec or '').split(';') if b.strip()]


def lock(frame, base, regions):
    """`frame` with every pixel inside the regions taken from `base`: what must not move
    (a face, a logo, a held prop) is copied, not trusted to the redraw"""
    out = frame.copy()
    for x, y, w, h in regions:
        out.paste(base.crop((x, y, x + w, y + h)), (x, y))
    return out


def pad(im, spec):
    if not spec:
        return im
    v = [int(x) for x in str(spec).split(',')]
    l, t, r, b = (v * 4)[:4] if len(v) == 1 else v
    out = Image.new('RGBA', (im.width + l + r, im.height + t + b), (0, 0, 0, 0))
    out.paste(im, (l, t))
    return out


def size(spec):
    w, h = spec.lower().split('x')
    return int(w), int(h)


# --- output -------------------------------------------------------------------------------

def zoomed(im, k):
    return im.resize((im.width * k, im.height * k), Image.NEAREST)


def gif_frames(frames):
    """RGBA frames on one shared palette, index 0 transparent: a GIF has a single key
    colour, so it is reserved instead of hoping no art colour collides with it"""
    cols = sorted({p[:3] for f in frames for p in pixels(f) if p[3] >= 128})[:255]
    index = {c: i + 1 for i, c in enumerate(cols)}
    flat = [0, 0, 0] + [v for c in cols for v in c]
    out = []
    for f in frames:
        p = Image.new('P', f.size)
        p.putdata([index.get(px[:3], 1) if px[3] >= 128 else 0 for px in pixels(f)])
        p.putpalette(flat + [0] * (768 - len(flat)))
        p.info['transparency'] = 0
        out.append(p)
    return out


def bundle(framedir, fps=8, zoom=8, out=None):
    files = sorted(f for f in glob.glob(os.path.join(framedir, '*.png')))
    if not files:
        raise SystemExit('no PNG frames in %s' % framedir)
    frames = [Image.open(f).convert('RGBA') for f in files]
    w, h = max(f.width for f in frames), max(f.height for f in frames)
    # frames of different sizes share a bottom-centre anchor, the usual sprite convention
    canvas = []
    for f in frames:
        c = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        c.paste(f, ((w - f.width) // 2, h - f.height))
        canvas.append(c)
    out = out or framedir
    os.makedirs(out, exist_ok=True)
    sheet = Image.new('RGBA', (w * len(canvas), h), (0, 0, 0, 0))
    for i, f in enumerate(canvas):
        sheet.paste(f, (i * w, 0))
    sheet.save(os.path.join(out, 'sheet.png'))
    json.dump({'frame_width': w, 'frame_height': h, 'frames': len(canvas), 'fps': fps,
               'files': [os.path.basename(f) for f in files]},
              open(os.path.join(out, 'sheet.json'), 'w'), indent=1)
    ms = int(round(1000 / fps))
    for name, k in (('anim', 1), ('anim@%dx' % zoom, zoom)):
        big = [zoomed(f, k) for f in canvas]
        g = gif_frames(big)
        g[0].save(os.path.join(out, name + '.gif'), save_all=True, append_images=g[1:],
                  duration=ms, loop=0, disposal=2, transparency=0)
        big[0].save(os.path.join(out, name + '.webp'), save_all=True, append_images=big[1:],
                    duration=ms, loop=0, lossless=True)
    return os.path.join(out, 'sheet.png')


def contact(images, path, zoom=6, gap=8):
    big = [zoomed(im, zoom) for im in images]
    w = sum(b.width for b in big) + gap * (len(big) + 1)
    h = max(b.height for b in big) + 2 * gap
    sheet = Image.new('RGBA', (w, h), (40, 40, 48, 255))
    x = gap
    for b in big:
        sheet.paste(b, (x, gap), b)
        x += b.width + gap
    sheet.save(path)


# --- commands -----------------------------------------------------------------------------

def _generate(src, text, engine, model, reuse):
    if reuse and os.path.isfile(src):
        print('%s: reusing the picture already generated' % src)
        return
    _, secs = backend.generate(src, text, engine, model)
    print('%s: generated in %.0fs' % (src, secs))


def create(outdir, prompt, dims=(32, 32), variants=1, pal_spec=None, colours=16, style=None,
           name='sprite', engine='images', model=None, reuse=False):
    pal = palette(pal_spec)
    bg, bgname = pick_background(pal)
    w, h = dims
    text = ('%s. %s Roughly %dx%d pixels, the subject centred and filling most of the picture, '
            'on a perfectly flat solid %s (#%s) background.' % (prompt.rstrip('. '), style or STYLE, w, h, bgname, bg))
    raw = os.path.join(outdir, 'raw')
    os.makedirs(raw, exist_ok=True)
    done = []
    for i in range(1, variants + 1):
        tag = '%s_%d' % (name, i) if variants > 1 else name
        src = os.path.join(raw, tag + '.png')
        _generate(src, text, engine, model, reuse)
        flatten(src, bg)
        snapdir = os.path.join(raw, 'snap')
        pxfbin.run('snap', pxfbin.path(src), pxfbin.path(snapdir), '--flatten', bg, '--colors', 48)
        art = key_out(Image.open(os.path.join(snapdir, tag + '.png')), bg)
        art = fit(art, w, h)
        art = to_palette(art, pal) if pal else reduce_colours(art, colours)
        dst = os.path.join(outdir, tag + '.png')
        art.save(dst)
        zoomed(art, 8).save(os.path.join(raw, tag + '@8x.png'))
        print('%s: %dx%d, %d colours' % (dst, w, h, len(colours_of(art))))
        done.append(art)
    if variants > 1:
        contact(done, os.path.join(outdir, name + '_variants.png'))
    return done


def components(im):
    """the 8-connected opaque blobs of `im`, each a list of (x, y)"""
    w, h = im.size
    a = im.getchannel('A').load()
    seen, out = set(), []
    for y in range(h):
        for x in range(w):
            if a[x, y] < 128 or (x, y) in seen:
                continue
            blob, todo = [], [(x, y)]
            seen.add((x, y))
            while todo:
                cx, cy = todo.pop()
                blob.append((cx, cy))
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in seen and a[nx, ny] >= 128:
                            seen.add((nx, ny))
                            todo.append((nx, ny))
            out.append(blob)
    return out


def split_cells(im, rows, cols):
    """one image per cell of a rows x cols layout. Each blob goes to the cell its centre of
    mass falls in, so a sprite that pokes over its cell line stays whole."""
    w, h = im.size
    src = im.load()
    cells = [Image.new('RGBA', im.size, (0, 0, 0, 0)) for _ in range(rows * cols)]
    for blob in components(im):
        cx = sum(x for x, _ in blob) / len(blob)
        cy = sum(y for _, y in blob) / len(blob)
        cell = cells[min(int(cy * rows / h), rows - 1) * cols + min(int(cx * cols / w), cols - 1)]
        dst = cell.load()
        for x, y in blob:
            dst[x, y] = src[x, y]
    return cells


def create_set(outdir, items, dims=(32, 32), names=None, pal_spec=None, colours=16, style=None,
               engine='images', model=None, theme=None, reuse=False):
    """several sprites drawn together in ONE picture, so they share one hand, outline weight
    and palette: an icon set, a family of items, a tile set"""
    pal = palette(pal_spec)
    bg, bgname = pick_background(pal)
    w, h = dims
    n = len(items)
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    names = names or ['%02d' % (i + 1) for i in range(n)]
    listing = ' '.join('Cell %d: %s.' % (i + 1, t.rstrip('. ')) for i, t in enumerate(items))
    text = ('A set of %d separate pixel-art sprites%s laid out in a grid of %d rows and %d columns of '
            'equal cells, read left to right, top to bottom, every sprite centred in its own cell '
            'with empty space between them, all drawn in exactly the same style, scale and palette. '
            '%s Leave any extra cells empty. %s Each sprite roughly %dx%d pixels. Perfectly flat '
            'solid %s (#%s) background.' % (n, ' (%s)' % theme if theme else '', rows, cols, listing,
                                            style or STYLE, w, h, bgname, bg))
    raw = os.path.join(outdir, 'raw')
    os.makedirs(raw, exist_ok=True)
    src = os.path.join(raw, 'set.png')
    _generate(src, text, engine, model, reuse)
    flatten(src, bg)
    snapdir = os.path.join(raw, 'snap')
    pxfbin.run('snap', pxfbin.path(src), pxfbin.path(snapdir), '--flatten', bg, '--colors', 64)
    sheet = key_out(Image.open(os.path.join(snapdir, 'set.png')), bg)
    # one colour reduction over the whole set, not per sprite: the set keeps one palette
    sheet = to_palette(sheet, pal) if pal else reduce_colours(sheet, colours)
    done = []
    for name, cell in zip(names, split_cells(sheet, rows, cols)):
        if not cell.getchannel('A').getbbox():
            print('%s: the model left its cell empty' % name)
            continue
        art = fit(cell, w, h)
        art.save(os.path.join(outdir, name + '.png'))
        done.append(art)
    contact(done, os.path.join(outdir, 'set_contact.png'))
    print('%s: %d sprites %dx%d, %d colours' % (outdir, len(done), w, h,
                                               len({c for a in done for c in colours_of(a)})))
    return done


def _sheet_prompt(n, rows, cols, k, bgname, bg, task):
    return ('This picture is a sprite sheet: %d cells in %d row(s) of %d, read left to right, '
            'top to bottom, each cell a pixel-art sprite where every art pixel is a %dx%d block. %s '
            'Keep every cell exactly where it is and the same size, keep the %dx%d pixel blocks, '
            'keep the same palette, outline and character design. Draw nothing outside the cells. '
            'Keep the flat solid %s (#%s) background. No text, no numbers, no frame borders.'
            % (n, rows, cols, k, k, task, k, k, bgname, bg))


def _redraw(sprite, n, rows, task, extra, model, work, keep_first, min_region=8, locked=()):
    base = sprite.convert('RGBA')
    pal = sorted(set(colours_of(base)) | set(extra or []))
    bg, bgname = pick_background(pal)
    cols = math.ceil(n / rows)
    gap = 4
    k = max(1, SHEET_SIDE // max(cols * (base.width + gap) - gap, rows * (base.height + gap) - gap))
    cells = os.path.join(work, 'cells')
    shutil.rmtree(cells, ignore_errors=True)
    os.makedirs(cells)
    for i in range(n):
        base.save(os.path.join(cells, '%02d.png' % i))
    sheet, manifest = os.path.join(work, 'sheet.png'), os.path.join(work, 'sheet.json')
    pxfbin.run('sheet', pxfbin.path(cells), pxfbin.path(sheet), '--scale', k, '--gap', gap,
               '--rows', rows, '--bg', bg, '--canvas', '%dx%d' % (SHEET_SIDE, SHEET_SIDE))
    answer = os.path.join(work, 'answer.png')
    nbytes, secs = backend.images(sheet, answer, _sheet_prompt(n, rows, cols, k, bgname, bg, task), model)
    print('model answered in %.0fs' % secs)
    back = os.path.join(work, 'frames')
    shutil.rmtree(back, ignore_errors=True)
    pxfbin.run('downscale', pxfbin.path(answer), pxfbin.path(back), '--manifest', pxfbin.path(manifest),
               '--palette', ','.join(tohex(c) for c in pal), '--bg', bg, '--colorkey', 'none',
               '--tol', 24, '--resolve', '--grain', '--register', '--metric', 'redmean')
    frames = [key_out(Image.open(os.path.join(back, '%02d.png' % i)), bg) for i in range(n)]
    if keep_first:
        frames[0] = base
    if min_region:
        frames = [stabilize(f, base, min_region) for f in frames]
    return [lock(f, base, locked) for f in frames]


def animate(src, outdir, prompt, n, rows=None, pad_spec=None, pal_spec=None, fps=8,
            keep_first=True, model=None, min_region=8, lock_spec=None):
    base = pad(Image.open(src).convert('RGBA'), pad_spec)
    rows = rows or max(1, round(math.sqrt(n)))
    task = ('Redraw the cells as the %d consecutive frames of one smooth looping animation: %s. '
            'Cell 1 is frame 1 and stays exactly as it is; each next cell is the next moment, and '
            'the last cell leads back into the first.' % (n, prompt.rstrip('. ')))
    work = os.path.join(outdir, 'work')
    os.makedirs(work, exist_ok=True)
    frames = _redraw(base, n, rows, task, palette(pal_spec), model, work, keep_first, min_region,
                     boxes(lock_spec))
    fdir = os.path.join(outdir, 'frames')
    shutil.rmtree(fdir, ignore_errors=True)
    os.makedirs(fdir)
    for i, f in enumerate(frames):
        f.save(os.path.join(fdir, '%02d.png' % i))
    bundle(fdir, fps, out=outdir)
    print('%s: %d frames %dx%d, %d fps' % (outdir, n, base.width, base.height, fps))
    return frames


def edit(src, dst, prompt, pal_spec=None, model=None, min_region=8, lock_spec=None):
    base = Image.open(src).convert('RGBA')
    task = 'Change the sprite: %s. Change only that; everything else stays as it is.' % prompt.rstrip('. ')
    work = os.path.splitext(dst)[0] + '_work'
    os.makedirs(work, exist_ok=True)
    out = _redraw(base, 1, 1, task, palette(pal_spec), model, work, keep_first=False,
                  min_region=min_region, locked=boxes(lock_spec))[0]
    out.save(dst)
    print('%s: %dx%d' % (dst, out.width, out.height))
    return out


# --- cli ----------------------------------------------------------------------------------

def _opt(argv, name, default=None, cast=str):
    return cast(argv[argv.index(name) + 1]) if name in argv else default


def main(command, argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    pos = []
    i = 0
    while i < len(argv):
        if argv[i].startswith('--'):
            i += 1 if argv[i] in ('--redraw-first', '--reuse') else 2
        else:
            pos.append(argv[i])
            i += 1
    if command == 'palettes':
        for k, v in PRESETS.items():
            print('%-10s %s' % (k, v))
        return
    if command == 'bundle' and pos:
        print(bundle(pos[0], _opt(argv, '--fps', 8, float), _opt(argv, '--zoom', 8, int), _opt(argv, '--out')))
        return
    prompt = _opt(argv, '--prompt')
    items = _opt(argv, '--items')
    if command == 'create' and pos and items:
        if os.path.isfile(items):
            items = [l.strip() for l in open(items, encoding='utf-8') if l.strip() and not l.startswith('#')]
        else:
            items = [t.strip() for t in items.split('|') if t.strip()]
        named = [t.partition('=') for t in items]
        names = [k.strip() if sep else None for k, sep, _ in named]
        items = [v.strip() if sep else k.strip() for k, sep, v in named]
        names = [nm or '%02d' % (i + 1) for i, nm in enumerate(names)]
        create_set(pos[0], items, size(_opt(argv, '--size', '32x32')), names, _opt(argv, '--palette'),
                   _opt(argv, '--colors', 16, int), _opt(argv, '--style'), _opt(argv, '--engine', 'images'),
                   _opt(argv, '--model'), prompt, '--reuse' in argv)
        return
    if command == 'create' and pos and prompt:
        create(pos[0], prompt, size(_opt(argv, '--size', '32x32')), _opt(argv, '--variants', 1, int),
               _opt(argv, '--palette'), _opt(argv, '--colors', 16, int), _opt(argv, '--style'),
               _opt(argv, '--name', 'sprite'), _opt(argv, '--engine', 'images'), _opt(argv, '--model'),
               '--reuse' in argv)
        return
    if command == 'animate' and len(pos) >= 2 and prompt and '--frames' in argv:
        animate(pos[0], pos[1], prompt, _opt(argv, '--frames', cast=int), _opt(argv, '--rows', None, int),
                _opt(argv, '--pad'), _opt(argv, '--palette'), _opt(argv, '--fps', 8, float),
                '--redraw-first' not in argv, _opt(argv, '--model'), _opt(argv, '--min-region', 8, int),
                _opt(argv, '--lock'))
        return
    if command == 'edit' and len(pos) >= 2 and prompt:
        edit(pos[0], pos[1], prompt, _opt(argv, '--palette'), _opt(argv, '--model'),
             _opt(argv, '--min-region', 8, int), _opt(argv, '--lock'))
        return
    raise SystemExit(__doc__)
