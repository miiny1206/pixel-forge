//! Module 3e — say which pixels a generator is allowed to touch, and put the
//! rest back untouched.
//!
//! Measured, and the reason this module exists: a whole-frame text edit is not
//! spatially registered. Qwen-Image-Edit kept the pose but redrew the body, and
//! a translation sweep over +/-4 px found a *different* best offset for every
//! frame with a boundary error of ~92 per channel — the drift is non-rigid, so
//! no paste can repair it after the fact. The only fix is to never let the
//! model own those pixels: mask the garment, inpaint inside it, copy outside.
//!
//! Masks are hand-authored polygons rather than derived from colour. That is a
//! deliberate choice, also measured: this artist reuses the same dark tones for
//! the outline, the stockings, the skirt shadow and the skin shadow, so a
//! colour-distance classifier plus region growing oscillates — every colour
//! added to one class lets that class flood the others' shadows.

use crate::palette::collect_images;
use crate::Args;
use anyhow::{bail, Context, Result};
use image::RgbaImage;
use serde::Deserialize;
use std::collections::BTreeMap;
use std::path::PathBuf;

#[derive(Deserialize, Default)]
pub struct FrameSpec {
    #[serde(default)]
    pub add: Vec<Vec<[f64; 2]>>,
    #[serde(default)]
    pub sub: Vec<Vec<[f64; 2]>>,
    /// `[[x0,y0],[x1,y1],width]` — a thick segment cut out of the mask, for a
    /// weapon or a strap crossing the garment.
    #[serde(default)]
    pub sub_lines: Vec<([f64; 2], [f64; 2], f64)>,
}

pub fn cmd_mask(args: &Args) -> Result<()> {
    let (input, outdir) = crate::import::two_paths(args, "pxf mask <frame.png|dir> <outdir> --spec spec.json")?;
    std::fs::create_dir_all(&outdir)?;
    let spec_path = args.opt("spec").context("pxf mask needs --spec <spec.json>")?;
    let spec: BTreeMap<String, FrameSpec> = serde_json::from_slice(
        &std::fs::read(spec_path).with_context(|| format!("reading {spec_path}"))?,
    )
    .with_context(|| format!("parsing {spec_path}"))?;

    let overlay_dir = args.opt("overlay").map(PathBuf::from);
    if let Some(d) = &overlay_dir {
        std::fs::create_dir_all(d)?;
    }

    let mut total = 0usize;
    for path in collect_images(&input)? {
        let stem = path.file_stem().unwrap().to_string_lossy().to_string();
        let Some(fs) = spec.get(&stem) else {
            eprintln!("{stem}: no entry in the spec, skipped");
            continue;
        };
        let img = image::open(&path).with_context(|| format!("opening {}", path.display()))?.to_rgba8();
        let m = rasterize(fs, &img);
        let n = m.iter().filter(|v| **v).count();
        let opaque = img.pixels().filter(|p| p.0[3] >= 128).count();
        if n == 0 {
            eprintln!("{stem}: WARNING mask is empty — check the polygon coordinates");
        }

        let mut out = RgbaImage::new(img.width(), img.height());
        for (i, on) in m.iter().enumerate() {
            let p = if *on { [255, 255, 255, 255] } else { [0, 0, 0, 255] };
            out.put_pixel(i as u32 % img.width(), i as u32 / img.width(), image::Rgba(p));
        }
        let name = path.file_name().unwrap().to_string_lossy().to_string();
        out.save(outdir.join(&name))?;

        if let Some(d) = &overlay_dir {
            let mut ov = img.clone();
            for (i, on) in m.iter().enumerate() {
                if *on {
                    ov.put_pixel(i as u32 % img.width(), i as u32 / img.width(), image::Rgba([255, 60, 60, 255]));
                }
            }
            ov.save(d.join(&name))?;
        }
        println!("{name}: mask {n} px of {opaque} opaque ({:.0}%)", 100.0 * n as f64 / opaque.max(1) as f64);
        total += n;
    }
    if total == 0 {
        bail!("every mask came out empty");
    }
    Ok(())
}

/// Polygons in source-pixel coordinates, intersected with the frame's own
/// opaque area. The mask never leaves the silhouette: the per-frame offsets
/// live in the undecoded `.ani`, so a garment that grew past the frame's
/// occupied box would move the character in game.
pub(crate) fn rasterize(fs: &FrameSpec, img: &RgbaImage) -> Vec<bool> {
    let (w, h) = (img.width() as usize, img.height() as usize);
    let mut m = vec![false; w * h];
    for poly in &fs.add {
        fill(&mut m, w, h, poly, true);
    }
    for poly in &fs.sub {
        fill(&mut m, w, h, poly, false);
    }
    for (a, b, width) in &fs.sub_lines {
        cut_line(&mut m, w, h, *a, *b, *width);
    }
    for y in 0..h {
        for x in 0..w {
            if img.get_pixel(x as u32, y as u32).0[3] < 128 {
                m[y * w + x] = false;
            }
        }
    }
    m
}

/// Even-odd scanline fill, sampling at pixel centres.
fn fill(m: &mut [bool], w: usize, h: usize, poly: &[[f64; 2]], value: bool) {
    if poly.len() < 3 {
        return;
    }
    for y in 0..h {
        let cy = y as f64 + 0.5;
        let mut xs: Vec<f64> = Vec::new();
        for i in 0..poly.len() {
            let (p, q) = (poly[i], poly[(i + 1) % poly.len()]);
            let (y0, y1) = (p[1], q[1]);
            if (y0 <= cy) != (y1 <= cy) {
                xs.push(p[0] + (cy - y0) / (y1 - y0) * (q[0] - p[0]));
            }
        }
        xs.sort_by(|a, b| a.partial_cmp(b).unwrap());
        for pair in xs.chunks(2) {
            if pair.len() < 2 {
                break;
            }
            for x in 0..w {
                let cx = x as f64 + 0.5;
                if cx >= pair[0] && cx <= pair[1] {
                    m[y * w + x] = value;
                }
            }
        }
    }
}

fn cut_line(m: &mut [bool], w: usize, h: usize, a: [f64; 2], b: [f64; 2], width: f64) {
    let r = width / 2.0;
    let (dx, dy) = (b[0] - a[0], b[1] - a[1]);
    let len2 = dx * dx + dy * dy;
    for y in 0..h {
        for x in 0..w {
            let (px, py) = (x as f64 + 0.5, y as f64 + 0.5);
            let t = if len2 == 0.0 { 0.0 } else { (((px - a[0]) * dx + (py - a[1]) * dy) / len2).clamp(0.0, 1.0) };
            let (qx, qy) = (a[0] + t * dx, a[1] + t * dy);
            if (px - qx).powi(2) + (py - qy).powi(2) <= r * r {
                m[y * w + x] = false;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn solid(w: u32, h: u32) -> RgbaImage {
        RgbaImage::from_pixel(w, h, image::Rgba([1, 2, 3, 255]))
    }

    #[test]
    fn a_square_polygon_fills_its_interior() {
        let fs = FrameSpec { add: vec![vec![[2.0, 2.0], [8.0, 2.0], [8.0, 8.0], [2.0, 8.0]]], ..Default::default() };
        let m = rasterize(&fs, &solid(10, 10));
        assert_eq!(m.iter().filter(|v| **v).count(), 36);
        assert!(m[4 * 10 + 4]);
        assert!(!m[0]);
    }

    #[test]
    fn sub_cuts_out_of_add() {
        let fs = FrameSpec {
            add: vec![vec![[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]]],
            sub: vec![vec![[2.0, 2.0], [8.0, 2.0], [8.0, 8.0], [2.0, 8.0]]],
            ..Default::default()
        };
        let m = rasterize(&fs, &solid(10, 10));
        assert_eq!(m.iter().filter(|v| **v).count(), 100 - 36);
    }

    /// The mask must never extend past the frame's own opaque area, or the
    /// character's occupied box moves and the .ani offsets stop matching.
    #[test]
    fn the_mask_is_clipped_to_the_silhouette() {
        let mut img = solid(10, 10);
        for x in 0..10 {
            img.put_pixel(x, 0, image::Rgba([0, 0, 0, 0]));
        }
        let fs = FrameSpec { add: vec![vec![[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]]], ..Default::default() };
        let m = rasterize(&fs, &img);
        assert_eq!(m.iter().filter(|v| **v).count(), 90);
        assert!(!m[3]);
    }

    #[test]
    fn a_thick_line_cuts_a_band() {
        let fs = FrameSpec {
            add: vec![vec![[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]]],
            sub_lines: vec![([0.0, 5.0], [10.0, 5.0], 3.0)],
            ..Default::default()
        };
        let m = rasterize(&fs, &solid(10, 10));
        let cut = 100 - m.iter().filter(|v| **v).count();
        assert!(cut >= 10, "expected at least one full row cut, got {cut}");
        assert!(!m[4 * 10 + 5] || !m[5 * 10 + 5]);
    }
}

// ---------------------------------------------------------------------------

/// Put the generated garment back into the original frame. Outside the mask the
/// original is copied byte for byte, so everything the model was not asked to
/// touch — arms, weapon, face, feet — is exactly the shipped art, and the
/// silhouette (and therefore the `.ani` offsets) cannot move.
pub fn cmd_remix(args: &Args) -> Result<()> {
    let (edited, outdir) = crate::import::two_paths(args, "pxf remix <editeddir> <outdir> --orig <dir> --mask <dir>")?;
    std::fs::create_dir_all(&outdir)?;
    let orig_dir = PathBuf::from(args.opt("orig").context("pxf remix needs --orig <dir>")?);
    let mask_dir = PathBuf::from(args.opt("mask").context("pxf remix needs --mask <dir>")?);

    #[derive(serde::Serialize)]
    struct FrameReport {
        file: String,
        mask_px: usize,
        taken_from_edit: usize,
        kept_from_original: usize,
        /// Mask pixels the edit left transparent, so the original had to stand
        /// in. A large number means the model deleted the garment instead of
        /// replacing it.
        holes_backfilled: usize,
        /// With --alpha: mask pixels the edit made transparent and that are now
        /// transparent in the result. Removing a skirt is exactly this.
        cleared: usize,
        seam_error: f64,
    }
    let mut reports = Vec::new();
    // Backfilling is right when a garment is *replaced*: a hole there means the
    // model dropped it. It is wrong when a garment is *removed* - a skirt that
    // becomes bare thighs leaves background where the hem was, and backfilling
    // puts the old skirt right back. --alpha lets the edit's transparency win
    // inside the mask; the mask still bounds it, so nothing outside can vanish.
    let take_alpha = args.has("alpha");

    for path in collect_images(&edited)? {
        let name = path.file_name().unwrap().to_string_lossy().to_string();
        let e = image::open(&path)?.to_rgba8();
        let o = image::open(orig_dir.join(&name))
            .with_context(|| format!("opening original {}", orig_dir.join(&name).display()))?
            .to_rgba8();
        let m = image::open(mask_dir.join(&name))
            .with_context(|| format!("opening mask {}", mask_dir.join(&name).display()))?
            .to_rgba8();
        if o.dimensions() != e.dimensions() || o.dimensions() != m.dimensions() {
            bail!(
                "{name}: sizes disagree — original {:?}, edit {:?}, mask {:?}",
                o.dimensions(), e.dimensions(), m.dimensions()
            );
        }

        let mut out = o.clone();
        let (mut mask_px, mut took, mut kept, mut holes, mut cleared) = (0usize, 0usize, 0usize, 0usize, 0usize);
        for (x, y, p) in m.enumerate_pixels() {
            if p.0[0] < 128 {
                kept += 1;
                continue;
            }
            mask_px += 1;
            let ep = *e.get_pixel(x, y);
            if ep.0[3] >= 128 {
                out.put_pixel(x, y, ep);
                took += 1;
            } else if take_alpha {
                out.put_pixel(x, y, image::Rgba([0, 0, 0, 0]));
                cleared += 1;
            } else {
                holes += 1;
            }
        }

        // Seam error: **continuity across the boundary**, not similarity to the
        // garment being replaced. For every mask pixel that touches a preserved
        // pixel, compare the new colour against that preserved neighbour. A
        // garment change is supposed to look different from the old garment;
        // what it must not do is break the surface it joins onto. This is the
        // number that showed a whole-frame edit could not be composited.
        let (mut se, mut n) = (0f64, 0usize);
        for (x, y, p) in m.enumerate_pixels() {
            if p.0[0] < 128 {
                continue;
            }
            let here = *out.get_pixel(x, y);
            if here.0[3] < 128 {
                continue;
            }
            for (dx, dy) in [(-1i64, 0i64), (1, 0), (0, -1), (0, 1)] {
                let (a, b) = (x as i64 + dx, y as i64 + dy);
                if a < 0 || b < 0 || (a as u32) >= m.width() || (b as u32) >= m.height() {
                    continue;
                }
                let (a, b) = (a as u32, b as u32);
                if m.get_pixel(a, b).0[0] >= 128 {
                    continue; // still inside the mask
                }
                let nb = *o.get_pixel(a, b);
                if nb.0[3] < 128 {
                    continue; // the frame's own edge, not a seam
                }
                se += (0..3).map(|i| (here.0[i] as f64 - nb.0[i] as f64).powi(2)).sum::<f64>();
                n += 1;
            }
        }
        let seam = if n > 0 { se / n as f64 } else { 0.0 };

        out.save(outdir.join(&name))?;
        println!(
            "{name}: {took} px from the edit, {kept} kept from the original{}{}",
            if holes > 0 { format!(", {holes} mask px the edit left empty") } else { String::new() },
            if cleared > 0 { format!(", {cleared} cleared by the edit's transparency") } else { String::new() }
        );
        reports.push(FrameReport {
            file: name,
            mask_px,
            taken_from_edit: took,
            kept_from_original: kept,
            holes_backfilled: holes,
            cleared,
            seam_error: seam,
        });
    }

    if let Some(p) = args.opt("report") {
        std::fs::write(p, serde_json::to_vec_pretty(&reports)?)?;
        println!("report -> {p}");
    }
    Ok(())
}
