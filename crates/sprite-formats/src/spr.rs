//! `.spr` — the sprite container.
//!
//! ```text
//! 0x00..0x3F  64-byte header: the file's own name, TEA-sealed (see tea.rs).
//!             The client refuses a file whose header names another file.
//! 0x40        uint32 magic, always 1958
//! 0x44        uint32 frame count
//! then per frame, packed back to back:
//!     int32 width   (SIGNED)
//!     int32 height  (SIGNED)
//!     width * height * uint16, RGB565 little-endian, row-major
//! ```
//!
//! Two details bite anyone who assumes otherwise:
//!
//! * Dimensions are **signed**. Placeholder frames are stored as -1 x -1, whose
//!   product is still 1, so exactly one pixel follows. Reading them unsigned
//!   turns them into 0xFFFFFFFF and derails the rest of the parse; 52 of the
//!   shipped character sprites contain them.
//! * There is no alpha channel. The engine colour-keys instead, and which key
//!   a frame uses is not recorded anywhere — see [`Frame::guess_colorkey`].

use anyhow::{bail, Context, Result};
use std::path::Path;

pub const HEADER_SIZE: usize = 64;
pub const MAGIC_OFFSET: usize = 64;
pub const MAGIC: u32 = 1958;

/// Black — UI panels and cards key against this.
pub const KEY_BLACK: u16 = 0x0000;
/// Cyan — character and effect sprites key against this.
pub const KEY_CYAN: u16 = 0x07FF;

pub fn rgb565_to_rgb(v: u16) -> [u8; 3] {
    let r = ((v >> 11) & 0x1F) as u8;
    let g = ((v >> 5) & 0x3F) as u8;
    let b = (v & 0x1F) as u8;
    // Replicate the high bits into the low ones so 0x1F maps to 255 exactly.
    [(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)]
}

pub fn rgb_to_rgb565(r: u8, g: u8, b: u8) -> u16 {
    (((r & 0xF8) as u16) << 8) | (((g & 0xFC) as u16) << 3) | ((b >> 3) as u16)
}

/// Round an RGB888 colour through RGB565 and back, giving the exact colour the
/// engine will display. Palette matching has to happen on these values, not on
/// the originals, or a match that looked perfect drifts on write.
pub fn canonicalize(rgb: [u8; 3]) -> [u8; 3] {
    rgb565_to_rgb(rgb_to_rgb565(rgb[0], rgb[1], rgb[2]))
}

#[derive(Clone)]
pub struct Frame {
    pub width: i32,
    pub height: i32,
    /// `width * height` RGB565 pixels, row-major. For a placeholder frame this
    /// is the single pixel the format still stores.
    pub pixels: Vec<u16>,
}

impl Frame {
    /// A -1 x -1 "nothing here" frame.
    pub fn is_placeholder(&self) -> bool {
        self.width < 0 || self.height < 0
    }

    /// Pick the colour key from the frame border, where the background lives.
    /// Returns `None` when the border holds neither known key, which happens on
    /// full-bleed art. The guess is genuinely fallible — `a result-screen sheet`
    /// has a cyan background but a border that votes black — so callers that
    /// care should take the key from the user instead.
    pub fn guess_colorkey(&self) -> Option<u16> {
        if self.is_placeholder() {
            return None;
        }
        let (w, h) = (self.width as usize, self.height as usize);
        let mut black = 0usize;
        let mut cyan = 0usize;
        let mut vote = |v: u16| match v {
            KEY_BLACK => black += 1,
            KEY_CYAN => cyan += 1,
            _ => {}
        };
        for x in 0..w {
            vote(self.pixels[x]);
            vote(self.pixels[(h - 1) * w + x]);
        }
        for y in 0..h {
            vote(self.pixels[y * w]);
            vote(self.pixels[y * w + w - 1]);
        }
        if cyan > black {
            Some(KEY_CYAN)
        } else if black > 0 {
            Some(KEY_BLACK)
        } else {
            None
        }
    }

    /// Decode to RGBA. Pixels equal to `key` become fully transparent; every
    /// other pixel is fully opaque. The format has no partial alpha and neither
    /// does the output.
    pub fn to_rgba(&self, key: Option<u16>) -> Result<image::RgbaImage> {
        if self.is_placeholder() {
            bail!("placeholder frame ({}x{}) has no image", self.width, self.height);
        }
        let (w, h) = (self.width as u32, self.height as u32);
        let mut img = image::RgbaImage::new(w, h);
        for (i, px) in self.pixels.iter().enumerate() {
            let [r, g, b] = rgb565_to_rgb(*px);
            let a = if Some(*px) == key { 0 } else { 255 };
            img.put_pixel(i as u32 % w, i as u32 / w, image::Rgba([r, g, b, a]));
        }
        Ok(img)
    }

    /// Encode from RGBA. Transparent pixels are written back as `key`.
    pub fn from_rgba(img: &image::RgbaImage, key: Option<u16>) -> Frame {
        let pixels = img
            .pixels()
            .map(|p| {
                let [r, g, b, a] = p.0;
                match (a, key) {
                    (0, Some(k)) => k,
                    _ => rgb_to_rgb565(r, g, b),
                }
            })
            .collect();
        Frame { width: img.width() as i32, height: img.height() as i32, pixels }
    }
}

pub struct Spr {
    /// The 64 header bytes: the file's own name, TEA-sealed. Kept verbatim so
    /// a rebuild is byte-identical; use [`Spr::set_name`] to write a sheet
    /// under a different name, or the client will refuse it.
    pub header: Vec<u8>,
    pub frames: Vec<Frame>,
}

impl Spr {
    pub fn parse(data: &[u8]) -> Result<Spr> {
        for (sig, kind) in [
            (&b"\x89PNG\r\n\x1a\n"[..], "PNG"),
            (&b"GIF8"[..], "GIF"),
            (&b"\xff\xd8\xff"[..], "JPEG"),
            (&b"BM"[..], "BMP"),
        ] {
            if data.starts_with(sig) {
                bail!("this is a {kind} image with a .spr extension, not a sprite container");
            }
        }
        if data.len() < MAGIC_OFFSET + 8 {
            bail!("file is too small to be a .spr");
        }
        let magic = u32::from_le_bytes(data[MAGIC_OFFSET..MAGIC_OFFSET + 4].try_into()?);
        if magic != MAGIC {
            bail!("bad magic {magic} (expected {MAGIC}) — not a sprite sheet of this format");
        }
        let count = u32::from_le_bytes(data[MAGIC_OFFSET + 4..MAGIC_OFFSET + 8].try_into()?);
        let mut frames = Vec::with_capacity(count as usize);
        let mut off = MAGIC_OFFSET + 8;
        for i in 0..count {
            if off + 8 > data.len() {
                bail!("truncated at frame {i}/{count}");
            }
            let width = i32::from_le_bytes(data[off..off + 4].try_into()?);
            let height = i32::from_le_bytes(data[off + 4..off + 8].try_into()?);
            off += 8;
            let n = (width as i64) * (height as i64);
            if n < 0 || n > 4096 * 4096 || off + (n as usize) * 2 > data.len() {
                bail!("frame {i} has implausible size {width}x{height}");
            }
            let mut pixels = Vec::with_capacity(n as usize);
            for k in 0..n as usize {
                pixels.push(u16::from_le_bytes(data[off + k * 2..off + k * 2 + 2].try_into()?));
            }
            off += (n as usize) * 2;
            frames.push(Frame { width, height, pixels });
        }
        let trailing = data.len() - off;
        if trailing != 0 {
            eprintln!("warning: {trailing} trailing byte(s) after the last frame");
        }
        Ok(Spr { header: data[..HEADER_SIZE].to_vec(), frames })
    }

    /// The file name sealed in the header, if it decrypts to one.
    pub fn name(&self) -> Option<String> {
        crate::tea::header_name(&self.header)
    }

    /// Re-seal the header to carry `name` (just the file name, no directory).
    pub fn set_name(&mut self, name: &str) {
        self.header = crate::tea::rename_header(&self.header, name);
    }

    pub fn load(path: impl AsRef<Path>) -> Result<Spr> {
        let path = path.as_ref();
        let data = std::fs::read(path).with_context(|| format!("reading {}", path.display()))?;
        Spr::parse(&data).with_context(|| format!("parsing {}", path.display()))
    }

    pub fn to_bytes(&self) -> Vec<u8> {
        let mut out = Vec::with_capacity(HEADER_SIZE + 8 + self.frames.len() * 64);
        if self.header.len() == HEADER_SIZE {
            out.extend_from_slice(&self.header);
        } else {
            out.extend_from_slice(&[0u8; HEADER_SIZE]);
        }
        out.extend_from_slice(&MAGIC.to_le_bytes());
        out.extend_from_slice(&(self.frames.len() as u32).to_le_bytes());
        for f in &self.frames {
            out.extend_from_slice(&f.width.to_le_bytes());
            out.extend_from_slice(&f.height.to_le_bytes());
            for px in &f.pixels {
                out.extend_from_slice(&px.to_le_bytes());
            }
        }
        out
    }

    pub fn save(&self, path: impl AsRef<Path>) -> Result<()> {
        let path = path.as_ref();
        std::fs::write(path, self.to_bytes()).with_context(|| format!("writing {}", path.display()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rgb565_is_exact_at_the_ends() {
        assert_eq!(rgb565_to_rgb(0xFFFF), [255, 255, 255]);
        assert_eq!(rgb565_to_rgb(KEY_BLACK), [0, 0, 0]);
        assert_eq!(rgb565_to_rgb(KEY_CYAN), [0, 255, 255]);
        assert_eq!(rgb_to_rgb565(0, 255, 255), KEY_CYAN);
    }

    #[test]
    fn canonical_colors_are_fixed_points() {
        for v in 0..=u16::MAX {
            let rgb = rgb565_to_rgb(v);
            assert_eq!(canonicalize(rgb), rgb, "0x{v:04X} drifted");
        }
    }

    #[test]
    fn placeholder_frames_survive_a_round_trip() {
        // -1 x -1 still stores one pixel; reading the dimensions unsigned
        // turns them into 0xFFFFFFFF and derails everything after them.
        let spr = Spr {
            header: vec![0u8; HEADER_SIZE],
            frames: vec![
                Frame { width: -1, height: -1, pixels: vec![KEY_CYAN] },
                Frame { width: 2, height: 1, pixels: vec![0x1234, 0x5678] },
            ],
        };
        let bytes = spr.to_bytes();
        let back = Spr::parse(&bytes).unwrap();
        assert!(back.frames[0].is_placeholder());
        assert_eq!(back.frames[1].pixels, vec![0x1234, 0x5678]);
        assert_eq!(back.to_bytes(), bytes);
    }

    #[test]
    fn colorkey_vote_prefers_the_border() {
        let f = Frame { width: 3, height: 3, pixels: vec![KEY_CYAN; 9] };
        assert_eq!(f.guess_colorkey(), Some(KEY_CYAN));
        let f = Frame { width: 3, height: 3, pixels: vec![0x1234; 9] };
        assert_eq!(f.guess_colorkey(), None);
    }
}
