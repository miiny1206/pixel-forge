"""Export game frames as 4x PNGs plus a clothing mask, ready for AI inpainting.

The mask marks only the outfit, so the AI redraws clothes and leaves the pose, face, hair and
weapon exactly as the artist drew them. Clothing is found by colour: everything that is not
skin, not hair, not the blade, inside the body silhouette.

    frame_out.py <ark> <spr> <outdir> <frame,frame,...> [--scale 4]
"""
import os, sys, struct, zipfile, colorsys
from PIL import Image

KEY = 0x07ff
BG = (128, 128, 128)


def rgb(v):
    return ((v >> 11 & 31) * 255 // 31, (v >> 5 & 63) * 255 // 63, (v & 31) * 255 // 31)


def png_write(path, w, h, rgba):
    Image.frombytes('RGBA', (w, h), rgba).save(path)


def frames(b):
    n = struct.unpack_from("<I", b, 0x44)[0]
    out, o = [], 0x48
    for _ in range(n):
        w, h = struct.unpack_from("<ii", b, o)
        c = w * h
        out.append((w, h, struct.unpack_from("<%dH" % c, b, o + 8) if c > 0 else ()))
        o += 8 + max(0, c) * 2
    return out


def is_skin(h, l, s):
    return 10 <= h * 360 <= 45 and s > 0.15 and l > 0.45


def is_hair(h, l, s):
    return 140 <= h * 360 <= 200 and s > 0.20        # her teal hair


def is_cloth(v):
    """Her outfit is the dark navy/black body plus gold and white trim."""
    r, g, b = [c / 255 for c in rgb(v)]
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    if is_skin(h, l, s) or is_hair(h, l, s):
        return False
    if l < 0.40:                                      # dark dress, boots, gloves
        return True
    if 35 <= h * 360 <= 60 and s > 0.35:              # gold trim
        return True
    if s < 0.18 and l > 0.70:                         # white frills
        return True
    return False


def clothing_mask(fr, top, bot):
    """Cloth colours inside a torso band, eroded so the 1-2px outline she is drawn with and the
    thin sword sheath vanish, then every solid piece that is left: bodice, sleeves, skirt."""
    w, h, px = fr
    m = bytearray(w * h)
    for y in range(int(top * h), min(h, int(bot * h) + 1)):
        for x in range(w):
            v = px[y * w + x]
            if v != KEY and is_cloth(v):
                m[y * w + x] = 1
    # the outline she is drawn with is also "cloth" dark, and it runs from her hair to her boots,
    # so every part of her is one blob until the thin lines are eroded away. The sheath is thin
    # too and drops out with them; the dress is solid and survives.
    e = bytearray(w * h)
    for y in range(h):
        for x in range(w):
            if m[y * w + x] and all(0 <= x + dx < w and 0 <= y + dy < h and m[(y + dy) * w + x + dx]
                                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                e[y * w + x] = 1
    m = e
    best, seen = [], bytearray(w * h)
    for y in range(h):
        for x in range(w):
            if not m[y * w + x] or seen[y * w + x]:
                continue
            st, blob = [(x, y)], []
            seen[y * w + x] = 1
            while st:
                cx, cy = st.pop()
                blob.append(cy * w + cx)
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < w and 0 <= ny < h and m[ny * w + nx] and not seen[ny * w + nx]:
                        seen[ny * w + nx] = 1
                        st.append((nx, ny))
            if len(blob) >= 20:          # bodice, sleeves and skirt are separate solid pieces
                best += blob
    out = bytearray(w * h)
    for i in best:
        out[i] = 1
    for _ in range(3):                       # dilate back past the erosion, and a little further
                                             # so the AI can blend at the edges
        g = bytearray(out)
        for y in range(h):
            for x in range(w):
                if out[y * w + x]:
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < w and 0 <= ny < h:
                            g[ny * w + nx] = 1
        out = g
    return out


def export(fr, path, scale, mask_path=None, top=0.0, bot=1.0, bare=False):
    if bare:
        import legs
        fr = legs.bare(fr)                   # tights off before the AI ever sees the frame
    w, h, px = fr
    cm = clothing_mask(fr, top, bot)
    W, H = w * scale, h * scale
    img = bytearray(bytes(BG + (255,)) * (W * H))
    msk = bytearray(bytes((0, 0, 0, 255)) * (W * H))
    for y in range(h):
        for x in range(w):
            v = px[y * w + x]
            if v == KEY:
                continue
            c = bytes(rgb(v) + (255,))
            m = bytes((255, 255, 255, 255)) if cm[y * w + x] else bytes((0, 0, 0, 255))
            for yy in range(scale):
                i = ((y * scale + yy) * W + x * scale) * 4
                img[i:i + 4 * scale] = c * scale
                msk[i:i + 4 * scale] = m * scale
    png_write(path, W, H, bytes(img))
    if mask_path:
        png_write(mask_path, W, H, bytes(msk))


def main():
    a = sys.argv[1:]
    if len(a) < 4:
        print(__doc__)
        return 1
    ark, spr, out, picks = a[0], a[1], a[2], [int(x) for x in a[3].split(",")]
    o = dict(zip(a[4::2], a[5::2]))
    scale = int(o.get("--scale", 4))
    os.makedirs(out, exist_ok=True)
    fr = frames(zipfile.ZipFile(ark).read(spr))
    for i in picks:
        export(fr[i], os.path.join(out, "f%03d.png" % i), scale,
               os.path.join(out, "m%03d.png" % i),
               float(o.get("--top", 0.0)), float(o.get("--bot", 1.0)),
               o.get("--bare", "1") != "0")
        print("f%03d  %dx%d" % (i, fr[i][0], fr[i][1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
