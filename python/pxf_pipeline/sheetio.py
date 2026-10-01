"""The sprite formats in pure Python: .spr, .ani, .ark and the TEA-sealed
header. The Rust crate `sprite-formats` is the reference implementation; this module is what the
Python steps use so they need no subprocess for a quick read or write.

.spr   64-byte header (the file's own name, TEA-encrypted), int32 magic 1958 at 0x40,
       int32 frame count at 0x44, then per frame: int32 w, int32 h (both SIGNED), w*h
       little-endian RGB565 words. Transparency is a colour key, cyan 0x07FF for every
       character sheet. A placeholder frame is -1 x -1, whose product is still 1, so exactly
       one word follows.
.ani   64-byte sealed header, then int32s: 1909 (magic), A (animation count),
       frame[A*F] (-1 none), last[A] (index of each animation's last step),
       (x, y, z)[A*F] interleaved per step, flag[A*F]. F, the steps per animation, is not
       stored (50 for the character sheets used here). A frame's top-left on screen is
       anchor - (x, z); y = w - x is the same offset for the mirrored side.
.ark   a plain zip of .spr/.ani/.col members; every sheet the game ships is deflated.
"""
import os, struct, zipfile

KEY = 0x07FF                     # cyan colour key
ANI_MAGIC = 1909
TEA_KEY = struct.unpack('<4I', bytes.fromhex('e3cba378a936d949ba70c5b405756e18'))
SPR_MAGIC = 1958


# --- colours ------------------------------------------------------------------------------

def rgb(v):
    """RGB565 -> (r, g, b) 0..255"""
    return ((v >> 11 & 31) * 255 // 31, (v >> 5 & 63) * 255 // 63, (v & 31) * 255 // 31)


def to565(r, g, b):
    return (r >> 3) << 11 | (g >> 2) << 5 | b >> 3


# --- TEA header -----------------------------------------------------------------------------

def tea_encrypt(data):
    k, m, out = TEA_KEY, 0xFFFFFFFF, b''
    for i in range(0, len(data), 8):
        v0, v1 = struct.unpack_from('<2I', data, i)
        s = 0
        for _ in range(32):
            s = (s + 0x9E3779B9) & m
            v0 = (v0 + ((((v1 << 4) & m) + k[0]) ^ (v1 + s) ^ ((v1 >> 5) + k[1]))) & m
            v1 = (v1 + ((((v0 << 4) & m) + k[2]) ^ (v0 + s) ^ ((v0 >> 5) + k[3]))) & m
        out += struct.pack('<2I', v0, v1)
    return out


def tea_decrypt(data):
    k, m, out = TEA_KEY, 0xFFFFFFFF, b''
    for i in range(0, len(data), 8):
        v0, v1 = struct.unpack_from('<2I', data, i)
        s = (0x9E3779B9 * 32) & m
        for _ in range(32):
            v1 = (v1 - ((((v0 << 4) & m) + k[2]) ^ (v0 + s) ^ ((v0 >> 5) + k[3]))) & m
            v0 = (v0 - ((((v1 << 4) & m) + k[0]) ^ (v1 + s) ^ ((v1 >> 5) + k[1]))) & m
            s = (s - 0x9E3779B9) & m
        out += struct.pack('<2I', v0, v1)
    return out


def header_name(blob):
    return tea_decrypt(blob[:64]).split(b'\0')[0].decode('latin-1')


def renamed(blob, new):
    """a .spr/.ani under a new name: the client refuses a file whose sealed header names
    another file ("This is not a sprite file")"""
    old = header_name(blob)
    if old == new:
        return blob
    plain = tea_decrypt(blob[:64])
    plain = (new.encode() + b'\0').ljust(len(old) + 1, b'\0') + plain[len(old) + 1:]
    return tea_encrypt(plain[:64].ljust(64, b'\0')) + blob[64:]


# --- .spr -----------------------------------------------------------------------------------

def frames(blob):
    """[(w, h, px)] with px a tuple of w*h RGB565 words (one word for a -1 x -1 placeholder)"""
    if struct.unpack_from('<I', blob, 0x40)[0] != SPR_MAGIC:
        raise ValueError('not a .spr (bad magic)')
    n = struct.unpack_from('<I', blob, 0x44)[0]
    out, o = [], 0x48
    for i in range(n):
        w, h = struct.unpack_from('<ii', blob, o)
        c = w * h
        if c < 0 or o + 8 + 2 * c > len(blob):
            raise ValueError('frame %d has implausible size %dx%d' % (i, w, h))
        out.append((w, h, struct.unpack_from('<%dH' % c, blob, o + 8)))
        o += 8 + 2 * c
    return out


def build(header_from, frs):
    """a .spr from (w, h, px) frames, keeping the first 0x44 bytes of `header_from`"""
    out = [header_from[:0x40], struct.pack('<II', SPR_MAGIC, len(frs))]
    for w, h, px in frs:
        out.append(struct.pack('<ii', w, h) + struct.pack('<%dH' % len(px), *px))
    return b''.join(out)


def to_image(fr):
    """(w, h, px) -> RGBA PIL image, the key transparent"""
    from PIL import Image
    w, h, px = fr
    im = Image.new('RGBA', (max(w, 1), max(h, 1)), (0, 0, 0, 0))
    if w > 0 and h > 0:
        im.putdata([(0, 0, 0, 0) if v == KEY else rgb(v) + (255,) for v in px])
    return im


def from_image(im):
    """RGBA image -> (w, h, px); an opaque pixel that would land on the key is moved one
    blue step off it, so it cannot become a hole in game"""
    im = im.convert('RGBA')
    out = []
    for r, g, b, a in im.getdata():
        v = KEY if a < 128 else to565(r, g, b)
        out.append(v ^ 1 if a >= 128 and v == KEY else v)
    return im.size[0], im.size[1], tuple(out)


# --- .ani -----------------------------------------------------------------------------------

class Ani:
    """an .ani as editable lists; per = steps per animation"""

    def __init__(self, blob, per):
        self.head, self.per = blob[:64], per
        iv = list(struct.unpack_from('<%di' % ((len(blob) - 64) // 4), blob, 64))
        if iv[0] != ANI_MAGIC:
            raise ValueError('not an .ani (magic %d)' % iv[0])
        A = self.count = iv[1]
        n = A * per
        self.frame = iv[2:2 + n]
        self.last = iv[2 + n:2 + n + A]
        xyz = iv[2 + n + A:2 + n + A + 3 * n]
        self.x, self.y, self.z = xyz[0::3], xyz[1::3], xyz[2::3]
        self.flag = iv[2 + n + A + 3 * n:2 + n + A + 4 * n]
        self.tail = blob[64 + 4 * (2 + A + 5 * n):]      # some editors left trailing junk

    def steps(self, a):
        k = a * self.per
        return [dict(frame=self.frame[k + s], x=self.x[k + s], y=self.y[k + s], z=self.z[k + s],
                     flag=self.flag[k + s]) for s in range(self.per)]

    def to_json(self):
        return {'per': self.per, 'anims': [{'last': self.last[a], 'steps': self.steps(a)}
                                           for a in range(self.count)]}

    def blob(self):
        xyz = [v for t in zip(self.x, self.y, self.z) for v in t]
        iv = [ANI_MAGIC, self.count] + self.frame + self.last + xyz + self.flag
        return self.head + struct.pack('<%di' % len(iv), *iv) + self.tail


# --- .ark -----------------------------------------------------------------------------------

def read_ark(path):
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}, z.namelist()


def write_ark(path, data, order, deflate=()):
    """rewrite an archive with the members in `order`; names in `deflate` are always stored
    compressed. Keeping an entry's old compress_type would make a mistake stick: one sheet
    once went in stored and every rebuild kept it so, blowing the archive up 4x."""
    tmp = path + '.tmp'
    infos = {}
    if os.path.exists(path):
        with zipfile.ZipFile(path) as z:
            infos = {i.filename: i for i in z.infolist()}
    with zipfile.ZipFile(tmp, 'w') as z:
        for name in order:
            old = infos.get(name)
            zi = zipfile.ZipInfo(name, old.date_time if old else (2020, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED if name in deflate else (
                old.compress_type if old else zipfile.ZIP_DEFLATED)
            zi.external_attr = old.external_attr if old else 0
            z.writestr(zi, data[name])
    os.replace(tmp, path)
    with zipfile.ZipFile(path) as z:
        for name in order:
            if z.read(name) != data[name]:
                raise SystemExit('read-back mismatch: %s in %s' % (name, path))
