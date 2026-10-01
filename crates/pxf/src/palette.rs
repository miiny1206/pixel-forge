//! Palette extraction and matching.
//!
//! Everything here works on RGB565-canonical colours. The game stores RGB565,
//! so a palette entry lifted from a PNG that was never round-tripped through
//! 565 will drift the moment it is written back. Matching on the canonical
//! value keeps import -> edit -> export lossless.

use anyhow::{bail, Context, Result};
use sprite_formats::spr::canonicalize;
use std::collections::BTreeSet;
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};

/// `downscale --metric redmean`: match by the "redmean" weighted distance instead of plain
/// RGB. Plain RGB weighs green like red and blue, which the eye does not; on skin that is
/// the difference between a lit shin and gold trim. Measured on a Gemini frame: its shin
/// (224,131,114) is 1054 from trim (206,158,115) and 1262 from skin (239,150,140) in RGB,
/// so her lower legs came out tan (4219 of the samples there went to trim); with redmean
/// 4128 of them go to skin. A process-wide
/// switch because a palette is built in a dozen places and only one command sets it.
pub static REDMEAN: AtomicBool = AtomicBool::new(false);

/// Weighted squared distance ("redmean" low-cost approximation of perceived difference),
/// scaled by 256 to stay in integers.
pub fn redmean2(a: [u8; 3], b: [u8; 3]) -> u32 {
    let rm = (a[0] as i32 + b[0] as i32) / 2;
    let (dr, dg, db) = (a[0] as i32 - b[0] as i32, a[1] as i32 - b[1] as i32, a[2] as i32 - b[2] as i32);
    (((512 + rm) * dr * dr) + 1024 * dg * dg + ((767 - rm) * db * db)) as u32
}

#[derive(Clone)]
pub struct Palette {
    pub colors: Vec<[u8; 3]>,
}

impl Palette {
    /// Union of the opaque colours of every reference image, canonicalized.
    pub fn from_refs(paths: &[std::path::PathBuf], alpha_threshold: u8) -> Result<Palette> {
        let mut set: BTreeSet<[u8; 3]> = BTreeSet::new();
        for p in paths {
            for img in collect_images(p)? {
                let rgba = image::open(&img)
                    .with_context(|| format!("opening {}", img.display()))?
                    .to_rgba8();
                for px in rgba.pixels() {
                    if px.0[3] >= alpha_threshold {
                        set.insert(canonicalize([px.0[0], px.0[1], px.0[2]]));
                    }
                }
            }
        }
        if set.is_empty() {
            bail!("no opaque pixels found in the reference image(s)");
        }
        Ok(Palette { colors: set.into_iter().collect() })
    }

    pub fn from_hex(s: &str) -> Result<Palette> {
        let mut colors = Vec::new();
        for part in s.split(',').map(str::trim).filter(|s| !s.is_empty()) {
            let h = part.strip_prefix('#').unwrap_or(part);
            if h.len() != 6 {
                bail!("palette entry {part:?} is not 6 hex digits");
            }
            let v = u32::from_str_radix(h, 16).with_context(|| format!("bad hex {part:?}"))?;
            colors.push(canonicalize([(v >> 16) as u8, (v >> 8) as u8, v as u8]));
        }
        if colors.is_empty() {
            bail!("empty palette");
        }
        Ok(Palette { colors })
    }

    /// Drop the colour key so no opaque pixel can ever be quantised onto it.
    /// An opaque pixel that lands on the key is a hole in the sprite in game,
    /// which is the single most expensive mistake this pipeline can make.
    pub fn without_key(&self, key: Option<u16>) -> Palette {
        let Some(k) = key else { return self.clone() };
        let keyed = sprite_formats::spr::rgb565_to_rgb(k);
        Palette { colors: self.colors.iter().copied().filter(|c| *c != keyed).collect() }
    }

    pub fn to_hex(&self) -> String {
        self.colors
            .iter()
            .map(|c| format!("{:02x}{:02x}{:02x}", c[0], c[1], c[2]))
            .collect::<Vec<_>>()
            .join(",")
    }

    /// Nearest entry by squared distance in RGB (or redmean, see REDMEAN). Linear scan: palettes here run
    /// to a few dozen colours and sprites to a few thousand pixels.
    pub fn nearest(&self, rgb: [u8; 3]) -> [u8; 3] {
        let mut best = self.colors[0];
        let mut best_d = u32::MAX;
        let redmean = REDMEAN.load(Ordering::Relaxed);
        for c in &self.colors {
            if redmean {
                let d = redmean2(*c, rgb);
                if d < best_d {
                    best_d = d;
                    best = *c;
                }
                continue;
            }
            let d = c
                .iter()
                .zip(rgb.iter())
                .map(|(a, b)| {
                    let d = *a as i32 - *b as i32;
                    (d * d) as u32
                })
                .sum();
            if d < best_d {
                best_d = d;
                best = *c;
            }
        }
        best
    }
}

/// A path that is a PNG, or a directory of them.
pub fn collect_images(p: &Path) -> Result<Vec<std::path::PathBuf>> {
    if p.is_file() {
        return Ok(vec![p.to_path_buf()]);
    }
    if !p.is_dir() {
        bail!("{} is neither a file nor a directory", p.display());
    }
    let mut out: Vec<_> = std::fs::read_dir(p)?
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| {
            p.is_file()
                && matches!(
                    p.extension().and_then(|e| e.to_str()).map(str::to_ascii_lowercase).as_deref(),
                    Some("png")
                )
        })
        .collect();
    out.sort();
    if out.is_empty() {
        bail!("no PNGs in {}", p.display());
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn redmean_picks_skin_over_trim() {
        // the Gemini shin that came out tan under plain RGB
        let shin = [224, 131, 114];
        let (trim, skin) = ([206, 158, 115], [239, 150, 140]);
        let rgb = |a: [u8; 3], b: [u8; 3]| -> i32 { (0..3).map(|i| (a[i] as i32 - b[i] as i32).pow(2)).sum() };
        assert!(rgb(shin, trim) < rgb(shin, skin), "plain RGB prefers the trim - the bug");
        assert!(redmean2(shin, skin) < redmean2(shin, trim));
    }
}
