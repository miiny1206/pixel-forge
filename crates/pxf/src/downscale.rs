//! Module 3d — take a generated sheet back down to source resolution using the
//! grid we *already know*, instead of detecting one.
//!
//! spritefusion-pixel-snapper has to infer the block size and phase from the
//! image. When it guesses the phase one pixel off, it point-samples the wrong
//! pixel of each block, and the damage shows up exactly where a human notices
//! it: single stray pixels off a silhouette edge, and thin features (fingers,
//! legs, weapon shafts) that break apart. That is the failure mode in the
//! snapper's own comparison thread.
//!
//! `pxf sheet` wrote the sheet, so here the block size and phase are given:
//! frame `i` occupies `(x, y)` at `scale` pixels per source pixel. Nothing is
//! inferred. Each source pixel is then decided by a *vote* over its whole
//! block rather than by one sample, so a single resampled pixel cannot carry
//! the decision, and the background is decided by the same vote — which is
//! what keeps the edge clean without an alpha channel.

use crate::palette::Palette;
use crate::sheet::SheetManifest;
use crate::{parse_colorkey, Args};
use anyhow::{bail, Context, Result};
use image::RgbaImage;
use serde::Serialize;
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

#[derive(Serialize)]
struct Report {
    scale: u32,
    palette_size: usize,
    colorkey: String,
    background: String,
    frames: Vec<FrameReport>,
}

#[derive(Serialize)]
struct FrameReport {
    file: String,
    size: String,
    opaque: usize,
    colors: usize,
    /// Blocks where the vote was not unanimous — how much resampling noise the
    /// vote actually had to absorb. Point sampling gambles on every one of these.
    contested_blocks: usize,
    /// Pixels whose winning colour (or background) took less than `--weak` of
    /// the block's weight. These are the ones worth a human look.
    weak_pixels: usize,
    /// Winner's share of the block, in ten buckets: [0-10%) .. [90-100%].
    /// A clean round trip puts everything in the last bucket.
    share_histogram: [usize; 10],
    /// Weak, orphaned pixels `--resolve` re-decided from their neighbours.
    resolved: usize,
    /// Near-shade orphans `--grain` moved onto a neighbour's shade.
    grain_smoothed: usize,
    /// With --register: the grid this frame was read on.
    #[serde(skip_serializing_if = "Option::is_none")]
    register: Option<RegReport>,
    /// Share of the frame's strong edges (RGB step > 90) that fall within 15%
    /// of a block boundary. 1.0 on our own sheet; ~0.35 is chance, i.e. a
    /// generator that ignored the grid and whose weak pixels are unavoidable.
    grid_fit: f64,
    specks_removed: usize,
    holes_filled: usize,
    /// Against the matching source frame, when one is given.
    ref_opaque: Option<usize>,
    alpha_agreement: Option<String>,
}

#[derive(Serialize)]
struct RegReport {
    /// output pixels per sheet pixel, as fitted
    k: f64,
    /// frame origin minus where the manifest and the canvas ratio put it, px
    offset: [f64; 2],
    /// edge phase concentration at the fitted scale, 0..1 (x, y)
    concentration: [f64; 2],
    /// whole blocks the origin was moved to line the silhouette up with --ref
    block_shift: [i32; 2],
    /// silhouette agreement with --ref, nominal grid then fitted grid, %
    alpha: [Option<f64>; 2],
    /// false when the fitted grid would have cost > 1 point of silhouette
    used: bool,
}

struct Reg {
    x: f64,
    y: f64,
    k: f64,
    conc: [f64; 2],
    sx: i32,
    sy: i32,
    /// silhouette agreement with --ref on the nominal and the fitted grid
    alpha: [Option<f64>; 2],
    /// whether the fitted grid was used
    kept: bool,
}

/// Fit this frame's grid on the generated sheet.
///
/// 1. Scale: try k0 * (1 + d/1000) for d in -40..=40 and keep the one whose
///    block period lines the strong edges (RGB step > 90) up best - the
///    length of their mean phase vector, averaged over x and y. That length
///    does not depend on where the grid starts, so scale is fitted alone.
/// 2. Phase: the direction of the same vector gives where block boundaries
///    fall, modulo one block.
/// 3. Whole blocks: the phase cannot tell one block from the next, so the
///    origin nearest the nominal one is taken and then moved by -2..=2 blocks
///    per axis to the placement whose silhouette (block centres that are not
///    background) agrees best with the reference frame, when there is one.
fn register_frame(
    sheet: &RgbaImage,
    f: &crate::sheet::SheetFrame,
    scale: u32,
    k0: f64,
    reference: Option<&RgbaImage>,
    pal: &Palette,
    bg: [u8; 3],
) -> Reg {
    let s = scale as f64;
    let pad = 3.0 * s * k0;
    let x0 = ((f.x as f64 * k0 - pad).floor().max(1.0)) as u32;
    let y0 = ((f.y as f64 * k0 - pad).floor().max(1.0)) as u32;
    let x1 = (((f.x + f.w * scale) as f64 * k0 + pad).ceil() as u32).min(sheet.width());
    let y1 = (((f.y + f.h * scale) as f64 * k0 + pad).ceil() as u32).min(sheet.height());
    let step = |a: [u8; 4], b: [u8; 4]| -> i32 { (0..3).map(|i| (a[i] as i32 - b[i] as i32).abs()).sum() };
    let (mut ex, mut ey) = (Vec::new(), Vec::new());
    for y in y0..y1 {
        for x in x0..x1 {
            let p = sheet.get_pixel(x, y).0;
            if step(sheet.get_pixel(x - 1, y).0, p) > 90 {
                ex.push(x as f64);
            }
            if step(sheet.get_pixel(x, y - 1).0, p) > 90 {
                ey.push(y as f64);
            }
        }
    }
    let phase = |pos: &[f64], period: f64| -> (f64, f64) {
        if pos.is_empty() {
            return (0.0, 0.0);
        }
        let (mut sn, mut cs) = (0f64, 0f64);
        for &p in pos {
            let a = std::f64::consts::TAU * p / period;
            sn += a.sin();
            cs += a.cos();
        }
        ((sn * sn + cs * cs).sqrt() / pos.len() as f64, sn.atan2(cs) / std::f64::consts::TAU * period)
    };
    let (mut bk, mut best) = (k0, -1.0);
    for d in -40..=40 {
        let kk = k0 * (1.0 + d as f64 / 1000.0);
        let (cx, _) = phase(&ex, s * kk);
        let (cy, _) = phase(&ey, s * kk);
        if (cx + cy) / 2.0 > best + 1e-12 {
            best = (cx + cy) / 2.0;
            bk = kk;
        }
    }
    let period = s * bk;
    let (cx, phx) = phase(&ex, period);
    let (cy, phy) = phase(&ey, period);
    let snap = |nominal: f64, ph: f64| ph + ((nominal - ph) / period).round() * period;
    let (bx, by) = (snap(f.x as f64 * bk, phx), snap(f.y as f64 * bk, phy));

    let (mut sx, mut sy) = (0i32, 0i32);
    if let Some(r) = reference.filter(|r| r.dimensions() == (f.w, f.h)) {
        // Solid-or-background for every sheet pixel the candidates can reach, once:
        // the same rule the vote uses. Deciding a block by its centre sample alone
        // picked the wrong whole-block shift on frame 311 (alpha 93.2% -> 88.5%).
        let reach = 2.0 * period + 1.0;
        let rx0 = ((bx - reach).floor().max(0.0)) as u32;
        let ry0 = ((by - reach).floor().max(0.0)) as u32;
        let rx1 = ((bx + f.w as f64 * period + reach).ceil() as u32).min(sheet.width());
        let ry1 = ((by + f.h as f64 * period + reach).ceil() as u32).min(sheet.height());
        let rw = (rx1 - rx0) as usize;
        let mut solid = vec![false; rw * (ry1 - ry0) as usize];
        for y in ry0..ry1 {
            for x in rx0..rx1 {
                let p = sheet.get_pixel(x, y).0;
                let rgb = [p[0], p[1], p[2]];
                let d_bg = dist2(bg, rgb);
                solid[(y - ry0) as usize * rw + (x - rx0) as usize] =
                    p[3] >= 128 && !(d_bg <= 12 * 12 || d_bg < dist2(pal.nearest(rgb), rgb));
            }
        }
        let block_solid = |ox: f64, oy: f64| -> bool {
            let (a, b) = (ox.max(rx0 as f64), oy.max(ry0 as f64));
            let (c, d) = ((ox + period).min(rx1 as f64), (oy + period).min(ry1 as f64));
            let (mut n, mut on) = (0usize, 0usize);
            let mut y = (b - 0.5).ceil() as u32;
            while (y as f64) + 0.5 < d {
                let mut x = (a - 0.5).ceil() as u32;
                while (x as f64) + 0.5 < c {
                    n += 1;
                    on += solid[(y - ry0) as usize * rw + (x - rx0) as usize] as usize;
                    x += 1;
                }
                y += 1;
            }
            on * 2 > n
        };
        let mut bestv = -1i64;
        for ty in -2..=2i32 {
            for tx in -2..=2i32 {
                let (ox, oy) = (bx + tx as f64 * period, by + ty as f64 * period);
                let mut agree = 0i64;
                for j in 0..f.h {
                    for i in 0..f.w {
                        let a = block_solid(ox + i as f64 * period, oy + j as f64 * period);
                        let b = r.get_pixel(i, j).0[3] >= 128;
                        agree += (a == b) as i64;
                    }
                }
                // ties keep the placement nearest the nominal one
                let better = agree > bestv || (agree == bestv && tx.abs() + ty.abs() < sx.abs() + sy.abs());
                if better {
                    bestv = agree;
                    sx = tx;
                    sy = ty;
                }
            }
        }
    }
    Reg { x: bx + sx as f64 * period, y: by + sy as f64 * period, k: bk, conc: [cx, cy], sx, sy, alpha: [None, None], kept: true }
}

/// Per-axis sample weights for one block. Mild centre bias: the centre of a
/// block is the most likely to be uncontaminated by the neighbouring source
/// pixel, but never heavy enough to outvote a clear majority — a heavy centre
/// weight is just point sampling again, with the same failure mode.
fn axis_weights(scale: u32) -> Vec<u32> {
    if scale == 1 {
        return vec![1];
    }
    (0..scale)
        .map(|i| {
            // triangle: 1 at the edges, peak in the middle
            let d = (2 * i as i64 - (scale as i64 - 1)).abs();
            let peak = scale as i64 - 1;
            (peak - d / 2).max(1) as u32
        })
        .collect()
}

pub fn cmd_downscale(args: &Args) -> Result<()> {
    let (input, outdir) = crate::import::two_paths(args, "pxf downscale <sheet.png> <outdir> --manifest sheet.json")?;
    std::fs::create_dir_all(&outdir)?;

    let mpath = match args.opt("manifest") {
        Some(p) => PathBuf::from(p),
        None => input.with_extension("json"),
    };
    let man: SheetManifest = serde_json::from_slice(
        &std::fs::read(&mpath).with_context(|| format!("reading {}", mpath.display()))?,
    )
    .with_context(|| format!("parsing {}", mpath.display()))?;

    let sheet = image::open(&input).with_context(|| format!("opening {}", input.display()))?.to_rgba8();
    // GPT Image returns 1254x1254 for a 1024x1024 request. Measured on that
    // output: the drawing keeps our grid, only scaled by 1254/1024 - 78-84% of
    // its strong edges sit within 15% of a scaled block boundary (Qwen: 34-36%,
    // i.e. chance) and the circular-mean offset is under 0.1 px. So a uniform
    // resize is not a broken grid; it is the same grid with a known factor.
    // Anything non-uniform still is, and still refuses.
    let k = if [sheet.width(), sheet.height()] == man.canvas {
        1.0
    } else {
        let kx = sheet.width() as f64 / man.canvas[0] as f64;
        let ky = sheet.height() as f64 / man.canvas[1] as f64;
        if (kx - ky).abs() > 0.01 * kx {
            bail!(
                "sheet is {}x{} but {} describes {}x{} — the generator resized it unevenly ({kx:.4} x {ky:.4}), so the grid in the manifest no longer applies",
                sheet.width(), sheet.height(), mpath.display(), man.canvas[0], man.canvas[1]
            );
        }
        eprintln!("sheet is {}x{} = canvas x{kx:.4}; blocks are {:.3} px", sheet.width(), sheet.height(), kx * man.scale as f64);
        kx
    };

    let bg = match args.opt("bg") {
        Some(h) => crate::snap::parse_hex(h)?,
        None => crate::snap::parse_hex(&man.background)?,
    };
    let key = parse_colorkey(args.opt("colorkey"))?;
    let threshold: u8 = args.opt("alpha-threshold").unwrap_or("128").parse()?;
    // Measured on a lossless round trip of real game frames: despeckling cost
    // 4-8 correct pixels per frame, because the artist *did* draw single-pixel
    // holes and single-pixel highlights. It is a repair for noisy generator
    // output, never a default.
    let despeckle = args.has("despeckle");
    // "contested" only says a block was not unanimous, and on generated art ~28%
    // of blocks are. What a human needs is *which* pixels the vote barely
    // decided, so they can be checked in Aseprite instead of all 7000.
    let confdir = args.opt("confidence").map(PathBuf::from);
    if let Some(d) = &confdir {
        std::fs::create_dir_all(d)?;
    }
    let weak: f64 = args.opt("weak").unwrap_or("0.6").parse()?;
    // A block split between two near-identical shades is not noise: either
    // winner looks right. Votes within --tol (RGB distance) of the winner count
    // with it when measuring confidence. The colour decision itself is unchanged.
    let tol: i32 = args.opt("tol").unwrap_or("0").parse()?;
    // Measured on sheet_s2 (Qwen, scale 3): the vote leaves 264-312 pixels per
    // frame with no 8-neighbour of a similar colour, the stock art has 117-191.
    // Re-deciding only the pixels that are both weak AND orphaned brings that to
    // 123-148 while touching 133-160 pixels; letting every weak pixel follow its
    // neighbours touched twice as many for a worse result.
    let resolve = args.has("resolve");
    // Measured on the GPT sheet after --resolve: 40% of the orphan pixels have a
    // neighbour within 2x--tol - two adjacent palette shades alternating, read
    // as grain - against 8% in the stock art. --grain moves such a pixel to the
    // near shade its neighbours share, but only a shade its own block voted for
    // (>= 5%), so nothing the model did not draw there is introduced. 991 -> 789
    // orphans, near-shade 40% -> 26%, 198 of 12,300 opaque pixels changed.
    // Dropping the block rule reached 19% for 211 pixels by overriding shades
    // the model drew on purpose; not worth it.
    let grain = args.has("grain");
    // GPT does not always keep its own resize exact. Measured per frame by the
    // scale that best lines the output's edges up on block boundaries: the idle
    // sheet and pilot5 sat at 1.221-1.224 with offsets up to 1.6 px, a later
    // two-frame sheet at 1.196-1.201 with offsets of 2-2.5 px - there the fixed
    // 1254/1024 grid dropped to 21-23% grid fit and ~1460 weak pixels a frame.
    // --register fits scale and offset per frame instead (see register_frame).
    let register = args.has("register");
    match args.opt("metric") {
        None | Some("rgb") => {}
        Some("redmean") => crate::palette::REDMEAN.store(true, std::sync::atomic::Ordering::Relaxed),
        Some(m) => anyhow::bail!("--metric {m}: expected rgb or redmean"),
    }
    if grain && tol == 0 {
        bail!("--grain needs --tol (24 was measured): it tells a near shade from a different colour");
    }

    let refs: Vec<PathBuf> = args.opts("ref").iter().map(PathBuf::from).collect();
    let base = if let Some(hex) = args.opt("palette") {
        Palette::from_hex(hex)?
    } else if !refs.is_empty() {
        Palette::from_refs(&refs, threshold)?
    } else {
        bail!("downscale needs the game palette: pass --ref <source frames> or --palette <hex,...>");
    };
    // The backdrop is not art. Reference frames flattened onto it carry it as
    // just another colour, and left in the palette it wins the vote for every
    // edge pixel that should have become a hole. Naming it with --bg is a
    // statement that it is background, so drop it.
    let bg_canon = sprite_formats::spr::canonicalize(bg);
    let dropped = base.colors.iter().filter(|c| **c == bg_canon).count();
    let base = Palette { colors: base.colors.iter().copied().filter(|c| *c != bg_canon).collect() };
    if dropped > 0 {
        eprintln!("background {:02X}{:02X}{:02X} removed from the palette ({} colours left)",
            bg[0], bg[1], bg[2], base.colors.len());
    }
    let pal = base.without_key(key.fixed());
    if pal.colors.is_empty() {
        bail!("palette is empty once the background and colour key are removed");
    }
    let keyed_rgb = key.fixed().map(sprite_formats::spr::rgb565_to_rgb);

    let wx = axis_weights(man.scale);
    let mut report = Report {
        scale: man.scale,
        palette_size: pal.colors.len(),
        colorkey: key.label(),
        background: format!("{:02X}{:02X}{:02X}", bg[0], bg[1], bg[2]),
        frames: Vec::new(),
    };

    for f in &man.frames {
        let mut out = RgbaImage::new(f.w, f.h);
        let mut conf = RgbaImage::new(f.w, f.h);
        let mut contested = 0usize;
        let mut weak_n = 0usize;
        let mut hist = [0usize; 10];
        let mut decided: Vec<Option<[u8; 3]>> = Vec::with_capacity((f.w * f.h) as usize);
        let mut shares: Vec<f64> = Vec::with_capacity((f.w * f.h) as usize);
        let mut candlist: Vec<Vec<(Option<[u8; 3]>, f64)>> = Vec::with_capacity((f.w * f.h) as usize);
        let refimg = find_ref(&refs, &f.file).and_then(|p| image::open(p).ok()).map(|i| i.to_rgba8());
        let read = |gx: f64, gy: f64, block: f64, int_path: bool| -> Vec<Vote> {
            let mut v = Vec::with_capacity((f.w * f.h) as usize);
            for j in 0..f.h {
                for i in 0..f.w {
                    v.push(if int_path {
                        vote_block(&sheet, f.x + i * man.scale, f.y + j * man.scale, man.scale, &wx, &pal, bg, keyed_rgb, tol)
                    } else {
                        let (x0, y0) = (gx + i as f64 * block, gy + j as f64 * block);
                        vote_rect(&sheet, x0, y0, x0 + block, y0 + block, man.scale, &pal, bg, keyed_rgb, tol)
                    });
                }
            }
            v
        };
        let (nx, ny, nb) = (f.x as f64 * k, f.y as f64 * k, man.scale as f64 * k);
        let nominal = read(nx, ny, nb, k == 1.0);
        let (mut gx, mut gy, mut block, mut votes, mut reg) = (nx, ny, nb, nominal, None);
        if register {
            let mut r = register_frame(&sheet, f, man.scale, k, refimg.as_ref(), &pal, bg);
            let rb = man.scale as f64 * r.k;
            let fitted = read(r.x, r.y, rb, false);
            // The fitted grid is taken unless it costs the silhouette: on frame 311
            // of a drifted sheet GPT's pitch was 6.0 px while the figure kept the
            // 6.12 px size, and the grid that caught the pitch lost 4.7 points of
            // silhouette - feet off the .ani anchor line. More than 1 point: keep
            // the nominal grid.
            let agree = |v: &[Vote]| -> Option<f64> {
                let r = refimg.as_ref().filter(|r| r.dimensions() == (f.w, f.h))?;
                let same = v.iter().enumerate().filter(|(i, vo)| {
                    (vo.0.is_some()) == (r.get_pixel(*i as u32 % f.w, *i as u32 / f.w).0[3] >= 128)
                }).count();
                Some(100.0 * same as f64 / v.len() as f64)
            };
            let (an, af) = (agree(&votes), agree(&fitted));
            r.alpha = [an, af];
            r.kept = match (an, af) {
                (Some(a), Some(b)) => b >= a - 1.0,
                _ => true,
            };
            if r.kept {
                gx = r.x;
                gy = r.y;
                block = rb;
                votes = fitted;
            }
            reg = Some(r);
        }
        for j in 0..f.h {
            for i in 0..f.w {
                let (c, was_contested, share, cands) = votes[(j * f.w + i) as usize].clone();
                decided.push(c);
                shares.push(share);
                candlist.push(cands);
                if was_contested {
                    contested += 1;
                }
                // blocks that are background on every sample say nothing; only
                // pixels the vote actually had to decide go in the histogram
                if was_contested || c.is_some() {
                    hist[((share * 10.0) as usize).min(9)] += 1;
                }
                if share < weak {
                    weak_n += 1;
                    // red, more opaque the closer the vote was: an overlay layer
                    let a = (255.0 * (1.0 - share) * 1.6).min(255.0) as u8;
                    conf.put_pixel(i, j, image::Rgba([255, 0, 64, a]));
                }
                out.put_pixel(i, j, match c {
                    Some(rgb) => image::Rgba([rgb[0], rgb[1], rgb[2], 255]),
                    None => image::Rgba([0, 0, 0, 0]),
                });
            }
        }

        let after_resolve = if resolve {
            resolve_orphans(f.w, f.h, &decided, &shares, &candlist, weak, tol)
        } else {
            decided.clone()
        };
        let resolved = decided.iter().zip(after_resolve.iter()).filter(|(a, b)| a != b).count();
        let (fin, grained) = if grain {
            smooth_grain(f.w, f.h, &after_resolve, &candlist, tol)
        } else {
            (after_resolve, 0)
        };
        for (idx, (a, b)) in decided.iter().zip(fin.iter()).enumerate() {
            if a != b {
                let (x, y) = (idx as u32 % f.w, idx as u32 / f.w);
                out.put_pixel(x, y, match b {
                    Some(rgb) => image::Rgba([rgb[0], rgb[1], rgb[2], 255]),
                    None => image::Rgba([0, 0, 0, 0]),
                });
            }
        }

        let fit = grid_fit(&sheet, gx, gy, f.w, f.h, block);

        let (specks, holes) = if despeckle { clean(&mut out) } else { (0, 0) };

        let opaque = out.pixels().filter(|p| p.0[3] >= threshold).count();
        let colors: BTreeSet<[u8; 3]> =
            out.pixels().filter(|p| p.0[3] >= threshold).map(|p| [p.0[0], p.0[1], p.0[2]]).collect();

        let (ref_opaque, agreement) = match find_ref(&refs, &f.file) {
            Some(p) => match image::open(&p) {
                Ok(r) => {
                    let r = r.to_rgba8();
                    if r.dimensions() == out.dimensions() {
                        let n = (f.w * f.h) as usize;
                        let same = out
                            .pixels()
                            .zip(r.pixels())
                            .filter(|(a, b)| (a.0[3] >= threshold) == (b.0[3] >= threshold))
                            .count();
                        let ro = r.pixels().filter(|p| p.0[3] >= threshold).count();
                        (Some(ro), Some(format!("{:.1}%", 100.0 * same as f64 / n as f64)))
                    } else {
                        (None, None)
                    }
                }
                Err(_) => (None, None),
            },
            None => (None, None),
        };

        out.save(outdir.join(&f.file))?;
        if let Some(d) = &confdir {
            conf.save(d.join(&f.file))?;
        }
        println!(
            "{}: {}x{} {} opaque, {} colours,{} grid fit {:.0}%, {} contested blocks, {} weak{}{}{}",
            f.file, f.w, f.h, opaque, colors.len(),
            match &reg {
                Some(r) if r.kept => format!(" registered k {:.4} offset {:+.1},{:+.1} px,", r.k, r.x - f.x as f64 * k, r.y - f.y as f64 * k),
                Some(r) => format!(" register rejected (k {:.4} would cost silhouette {:.1}% -> {:.1}%),", r.k,
                    r.alpha[0].unwrap_or(0.0), r.alpha[1].unwrap_or(0.0)),
                None => String::new(),
            },
            fit * 100.0, contested, weak_n,
            match (resolve, grain) {
                (true, true) => format!(", {resolved} resolved, {grained} grain"),
                (true, false) => format!(", {resolved} resolved"),
                (false, true) => format!(", {grained} grain"),
                _ => String::new(),
            },
            if specks + holes > 0 { format!(", cleaned {specks} specks / {holes} holes") } else { String::new() },
            match &agreement { Some(a) => format!(", alpha {a} vs source"), None => String::new() },
        );
        report.frames.push(FrameReport {
            file: f.file.clone(),
            size: format!("{}x{}", f.w, f.h),
            opaque,
            colors: colors.len(),
            contested_blocks: contested,
            weak_pixels: weak_n,
            resolved,
            grain_smoothed: grained,
            register: reg.as_ref().map(|r| RegReport {
                k: r.k,
                offset: [r.x - f.x as f64 * k, r.y - f.y as f64 * k],
                concentration: r.conc,
                block_shift: [r.sx, r.sy],
                alpha: r.alpha,
                used: r.kept,
            }),
            grid_fit: fit,
            share_histogram: hist,
            specks_removed: specks,
            holes_filled: holes,
            ref_opaque,
            alpha_agreement: agreement,
        });
    }

    if let Some(p) = args.opt("report") {
        std::fs::write(p, serde_json::to_vec_pretty(&report)?)?;
        println!("report -> {p}");
    }
    Ok(())
}

type Vote = (Option<[u8; 3]>, bool, f64, Vec<(Option<[u8; 3]>, f64)>);

/// Decide one source pixel from its whole block. Returns the palette colour, or
/// `None` for background, whether the vote was contested, the winner's share
/// of the block's weight (background counts as a candidate), 0..=1, and every
/// candidate with its share in the order it first appeared.
#[allow(clippy::too_many_arguments)]
fn vote_block(
    sheet: &RgbaImage,
    ox: u32,
    oy: u32,
    scale: u32,
    wx: &[u32],
    pal: &Palette,
    bg: [u8; 3],
    keyed_rgb: Option<[u8; 3]>,
    tol: i32,
) -> Vote {
    let mut samples = Vec::with_capacity((scale * scale) as usize);
    for dj in 0..scale {
        for di in 0..scale {
            let (x, y) = (ox + di, oy + dj);
            if x >= sheet.width() || y >= sheet.height() {
                continue;
            }
            samples.push((sheet.get_pixel(x, y).0, (wx[di as usize] * wx[dj as usize]) as f64));
        }
    }
    tally(&samples, pal, bg, keyed_rgb, tol)
}

/// The same vote over a block that no longer sits on whole pixels: the sheet
/// came back scaled by a non-integer factor. Every pixel whose centre falls in
/// [x0,x1) x [y0,y1) votes, weighted by the same triangle `axis_weights` uses,
/// evaluated at where the pixel sits inside the block.
#[allow(clippy::too_many_arguments)]
fn vote_rect(
    sheet: &RgbaImage,
    x0: f64,
    y0: f64,
    x1: f64,
    y1: f64,
    scale: u32,
    pal: &Palette,
    bg: [u8; 3],
    keyed_rgb: Option<[u8; 3]>,
    tol: i32,
) -> Vote {
    let axis = |a: f64, b: f64, n: u32| -> Vec<(u32, f64)> {
        let first = (a - 0.5).ceil().max(0.0) as i64;
        let last = ((b - 0.5).ceil() as i64 - 1).min(n as i64 - 1);
        let peak = scale as f64 - 1.0;
        (first..=last)
            .map(|p| {
                let t = (p as f64 + 0.5 - a) / (b - a);
                (p as u32, (peak - (2.0 * t - 1.0).abs() * scale as f64 / 2.0).max(1.0))
            })
            .collect()
    };
    let xs = axis(x0, x1, sheet.width());
    let ys = axis(y0, y1, sheet.height());
    let mut samples = Vec::with_capacity(xs.len() * ys.len());
    for &(y, wy) in &ys {
        for &(x, wxx) in &xs {
            samples.push((sheet.get_pixel(x, y).0, wxx * wy));
        }
    }
    tally(&samples, pal, bg, keyed_rgb, tol)
}

/// Count one block's weighted samples. Weights are f64 so a rescaled block can
/// use fractional ones; on the integer path every weight is a small whole
/// number and every sum is exact, so the decisions are the ones the u32 code
/// made - checked byte-for-byte against the previous binary.
fn tally(
    samples: &[([u8; 4], f64)],
    pal: &Palette,
    bg: [u8; 3],
    keyed_rgb: Option<[u8; 3]>,
    tol: i32,
) -> Vote {
    let mut bg_weight = 0f64;
    let mut total = 0f64;
    // first-appearance order of every candidate, background included as None;
    // `resolve` breaks its ties on this order, so it has to be the scan order
    let mut order: Vec<Option<[u8; 3]>> = Vec::with_capacity(8);
    // Small palettes; a vec of (colour, weight) beats a map here and keeps the
    // tie-break order deterministic.
    let mut votes: Vec<([u8; 3], f64)> = Vec::with_capacity(8);
    let mut distinct = 0usize;

    for &(p, w) in samples {
        total += w;
        if p[3] < 128 {
            bg_weight += w;
            if !order.contains(&None) {
                order.push(None);
            }
            continue;
        }
        let rgb = [p[0], p[1], p[2]];
        let near = pal.nearest(rgb);
        // Same comparative rule conform uses: a resampled edge pixel sits
        // between subject and backdrop, so "closer to the backdrop than to
        // anything in the palette" is background even without an exact match.
        let d_bg = dist2(bg, rgb);
        if d_bg <= 12 * 12 || d_bg < dist2(near, rgb) {
            bg_weight += w;
            if !order.contains(&None) {
                order.push(None);
            }
            continue;
        }
        let c = match keyed_rgb {
            Some(k) if near == k => second_nearest(pal, rgb, k),
            _ => near,
        };
        match votes.iter_mut().find(|(v, _)| *v == c) {
            Some(e) => e.1 += w,
            None => {
                votes.push((c, w));
                order.push(Some(c));
                distinct += 1;
            }
        }
    }

    let contested = distinct > 1 || (bg_weight > 0.0 && distinct > 0);
    if total == 0.0 {
        return (None, false, 1.0, Vec::new());
    }
    let cands: Vec<(Option<[u8; 3]>, f64)> = order
        .iter()
        .map(|k| {
            let w = match k {
                None => bg_weight,
                Some(c) => votes.iter().find(|(v, _)| v == c).map(|e| e.1).unwrap_or(0.0),
            };
            (*k, w / total)
        })
        .collect();
    if bg_weight * 2.0 > total || votes.is_empty() {
        return (None, contested, bg_weight / total, cands);
    }
    // the LAST of equal maxima, as Iterator::max_by_key did on the u32 weights
    let mut best = votes[0];
    for v in &votes[1..] {
        if v.1 >= best.1 {
            best = *v;
        }
    }
    let agree: f64 = votes.iter().filter(|(c, _)| dist2(*c, best.0) <= tol * tol).map(|(_, w)| *w).sum();
    (Some(best.0), contested, agree / total, cands)
}

/// How well the drawing inside one frame's box follows the block grid: of the
/// strong edges (RGB step > 90) between horizontally or vertically adjacent
/// pixels, the share that falls within 15% of a block boundary. The boundary
/// between pixel p-1 and p sits at coordinate p.
fn grid_fit(sheet: &RgbaImage, fx: f64, fy: f64, w: u32, h: u32, block: f64) -> f64 {
    let (x0, y0) = (fx.floor().max(0.0) as u32, fy.floor().max(0.0) as u32);
    let x1 = ((fx + w as f64 * block).ceil() as u32).min(sheet.width());
    let y1 = ((fy + h as f64 * block).ceil() as u32).min(sheet.height());
    let step = |a: [u8; 4], b: [u8; 4]| -> i32 { (0..3).map(|i| (a[i] as i32 - b[i] as i32).abs()).sum() };
    let near = |c: f64, origin: f64| -> bool {
        let ph = ((c - origin) / block).rem_euclid(1.0);
        ph <= 0.15 || ph >= 0.85
    };
    let (mut edges, mut on) = (0usize, 0usize);
    for y in y0..y1 {
        for x in x0..x1 {
            let p = sheet.get_pixel(x, y).0;
            if x > x0 && step(sheet.get_pixel(x - 1, y).0, p) > 90 {
                edges += 1;
                on += near(x as f64, fx) as usize;
            }
            if y > y0 && step(sheet.get_pixel(x, y - 1).0, p) > 90 {
                edges += 1;
                on += near(y as f64, fy) as usize;
            }
        }
    }
    if edges == 0 { 1.0 } else { on as f64 / edges as f64 }
}

/// Re-decide the pixels the vote barely decided AND that ended up with no
/// 8-neighbour of a similar colour — the speckle a viewer sees first. Such a
/// pixel takes whichever of its block's candidates (share >= 0.2, background
/// included) the most neighbours agree with. Everything is read from the vote's
/// own result, never from pixels already changed, so the pass is
/// order-independent. A pixel a neighbour agrees with, or one the vote decided
/// clearly, is never touched: those are the artist's single-pixel highlights.
fn resolve_orphans(
    w: u32,
    h: u32,
    decided: &[Option<[u8; 3]>],
    shares: &[f64],
    cands: &[Vec<(Option<[u8; 3]>, f64)>],
    weak: f64,
    tol: i32,
) -> Vec<Option<[u8; 3]>> {
    const MIN_SHARE: f64 = 0.2;
    let same = |a: Option<[u8; 3]>, b: Option<[u8; 3]>| match (a, b) {
        (None, None) => true,
        (Some(x), Some(y)) => dist2(x, y) <= tol * tol,
        _ => false,
    };
    let nb8 = |i: usize| {
        let (x, y) = ((i as u32 % w) as i64, (i as u32 / w) as i64);
        let mut v = Vec::with_capacity(8);
        for dy in -1..=1i64 {
            for dx in -1..=1i64 {
                if dx == 0 && dy == 0 {
                    continue;
                }
                let (nx, ny) = (x + dx, y + dy);
                if nx >= 0 && ny >= 0 && (nx as u32) < w && (ny as u32) < h {
                    v.push((ny as u32 * w + nx as u32) as usize);
                }
            }
        }
        v
    };
    let mut out = decided.to_vec();
    for i in 0..decided.len() {
        if shares[i] >= weak {
            continue;
        }
        let n = nb8(i);
        if n.iter().any(|&j| same(decided[i], decided[j])) {
            continue;
        }
        let (mut best, mut bs) = (decided[i], 0usize);
        for &(k, s) in &cands[i] {
            if s < MIN_SHARE {
                continue;
            }
            let sup = n.iter().filter(|&&j| same(k, decided[j])).count();
            if sup > bs || (sup == bs && sup > 0) {
                best = k;
                bs = sup;
            }
        }
        out[i] = best;
    }
    out
}

/// Smooth shade grain: a pixel no 8-neighbour matches within `tol`, but one
/// does within 2*tol, takes the near shade that the most neighbours match -
/// provided its own block gave that shade >= 5% of its weight. Near shades are
/// tried in sorted order and the first with the most support wins, so the
/// result does not depend on hashing. Reads `cur` as it was: order-independent.
fn smooth_grain(
    w: u32,
    h: u32,
    cur: &[Option<[u8; 3]>],
    cands: &[Vec<(Option<[u8; 3]>, f64)>],
    tol: i32,
) -> (Vec<Option<[u8; 3]>>, usize) {
    const MIN_SHARE: f64 = 0.05;
    let near2 = (2 * tol) * (2 * tol);
    let tol2 = tol * tol;
    let mut out = cur.to_vec();
    let mut changed = 0usize;
    for i in 0..cur.len() {
        let Some(c) = cur[i] else { continue };
        let (x, y) = ((i as u32 % w) as i64, (i as u32 / w) as i64);
        let mut n: Vec<[u8; 3]> = Vec::with_capacity(8);
        for dy in -1..=1i64 {
            for dx in -1..=1i64 {
                if dx == 0 && dy == 0 {
                    continue;
                }
                let (nx, ny) = (x + dx, y + dy);
                if nx >= 0 && ny >= 0 && (nx as u32) < w && (ny as u32) < h {
                    if let Some(q) = cur[(ny as u32 * w + nx as u32) as usize] {
                        n.push(q);
                    }
                }
            }
        }
        if n.iter().any(|q| dist2(c, *q) <= tol2) {
            continue;
        }
        let mut near: Vec<[u8; 3]> = n.iter().copied().filter(|q| dist2(c, *q) <= near2).collect();
        if near.is_empty() {
            continue;
        }
        near.sort();
        near.dedup();
        let (mut best, mut bs) = (None, 0usize);
        for k in near {
            let share = cands[i].iter().find(|(v, _)| *v == Some(k)).map(|e| e.1).unwrap_or(0.0);
            if share < MIN_SHARE {
                continue;
            }
            let sup = n.iter().filter(|q| dist2(k, **q) <= tol2).count();
            if sup > bs {
                best = Some(k);
                bs = sup;
            }
        }
        if let Some(k) = best {
            out[i] = Some(k);
            changed += 1;
        }
    }
    (out, changed)
}

/// Kill the two artefacts a human eye catches first: a lone opaque pixel with
/// no orthogonal neighbour (the stray dots circled in the snapper comparison),
/// and a lone hole fully surrounded by opaque pixels. Both are decided on the
/// *input* image so the pass is order-independent.
fn clean(img: &mut RgbaImage) -> (usize, usize) {
    let (w, h) = img.dimensions();
    let snapshot = img.clone();
    let opaque = |x: i64, y: i64| -> bool {
        x >= 0 && y >= 0 && (x as u32) < w && (y as u32) < h && snapshot.get_pixel(x as u32, y as u32).0[3] >= 128
    };
    let (mut specks, mut holes) = (0usize, 0usize);
    for y in 0..h as i64 {
        for x in 0..w as i64 {
            let n = [(x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)];
            let live = n.iter().filter(|(a, b)| opaque(*a, *b)).count();
            if opaque(x, y) {
                if live == 0 {
                    img.put_pixel(x as u32, y as u32, image::Rgba([0, 0, 0, 0]));
                    specks += 1;
                }
            } else if live == 4 {
                // Fill with the majority of the four; ties take the first in
                // scan order, which is stable.
                let mut v: Vec<([u8; 3], u32)> = Vec::new();
                for (a, b) in n {
                    let p = snapshot.get_pixel(a as u32, b as u32).0;
                    let c = [p[0], p[1], p[2]];
                    match v.iter_mut().find(|(k, _)| *k == c) {
                        Some(e) => e.1 += 1,
                        None => v.push((c, 1)),
                    }
                }
                let c = v.iter().max_by_key(|(_, n)| *n).unwrap().0;
                img.put_pixel(x as u32, y as u32, image::Rgba([c[0], c[1], c[2], 255]));
                holes += 1;
            }
        }
    }
    (specks, holes)
}

fn find_ref(refs: &[PathBuf], name: &str) -> Option<PathBuf> {
    for r in refs {
        let cand = if r.is_dir() { r.join(name) } else { r.clone() };
        if cand.is_file() && cand.file_name().map(|n| n == Path::new(name)).unwrap_or(false) {
            return Some(cand);
        }
    }
    None
}

fn second_nearest(pal: &Palette, rgb: [u8; 3], banned: [u8; 3]) -> [u8; 3] {
    let filtered = Palette { colors: pal.colors.iter().copied().filter(|c| *c != banned).collect() };
    if filtered.colors.is_empty() {
        return rgb;
    }
    filtered.nearest(rgb)
}

fn dist2(a: [u8; 3], b: [u8; 3]) -> i32 {
    a.iter().zip(b.iter()).map(|(x, y)| (*x as i32 - *y as i32).pow(2)).sum()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn weights_are_symmetric_and_peak_in_the_middle() {
        let w = axis_weights(3);
        assert_eq!(w, vec![1, 2, 1]);
        let w5 = axis_weights(5);
        assert_eq!(w5.first(), w5.last());
        assert!(w5[2] > w5[0]);
    }

    /// The point of the vote: one contaminated pixel in a block must not decide
    /// the block, which is exactly what point sampling lets it do.
    #[test]
    fn a_single_off_colour_sample_loses_the_vote() {
        let pal = Palette { colors: vec![[200, 30, 40], [10, 10, 200]] };
        let mut sheet = RgbaImage::new(3, 3);
        for p in sheet.pixels_mut() {
            *p = image::Rgba([200, 30, 40, 255]);
        }
        // contaminate the centre — the sample point sampling would have taken
        sheet.put_pixel(1, 1, image::Rgba([10, 10, 200, 255]));
        let (c, contested, share, _) = vote_block(&sheet, 0, 0, 3, &axis_weights(3), &pal, [128, 128, 128], None, 0);
        assert_eq!(c, Some([200, 30, 40]));
        assert!(contested);
        // centre weight is 2*2=4 of 16: the winner holds 12/16
        assert!((share - 0.75).abs() < 1e-9);
    }

    /// A clean block is fully confident; a near-even split is not, and that
    /// is the pixel the confidence map has to point a human at.
    #[test]
    fn share_separates_clean_blocks_from_coin_flips() {
        let pal = Palette { colors: vec![[200, 30, 40], [10, 10, 200]] };
        let mut sheet = RgbaImage::new(4, 4);
        for p in sheet.pixels_mut() {
            *p = image::Rgba([200, 30, 40, 255]);
        }
        let (_, contested, share, _) = vote_block(&sheet, 0, 0, 4, &axis_weights(4), &pal, [128, 128, 128], None, 0);
        assert!(!contested);
        assert_eq!(share, 1.0);
        for y in 0..4 {
            for x in 2..4 {
                sheet.put_pixel(x, y, image::Rgba([10, 10, 200, 255]));
            }
        }
        let (_, contested, share, _) = vote_block(&sheet, 0, 0, 4, &axis_weights(4), &pal, [128, 128, 128], None, 0);
        assert!(contested);
        assert!(share <= 0.5 + 1e-9, "an even split must not look confident: {share}");
    }

    #[test]
    fn a_majority_background_block_is_a_hole() {
        let pal = Palette { colors: vec![[200, 30, 40]] };
        let mut sheet = RgbaImage::new(3, 3);
        for p in sheet.pixels_mut() {
            *p = image::Rgba([128, 128, 128, 255]);
        }
        sheet.put_pixel(1, 1, image::Rgba([200, 30, 40, 255]));
        let (c, _, _, _) = vote_block(&sheet, 0, 0, 3, &axis_weights(3), &pal, [128, 128, 128], None, 0);
        assert_eq!(c, None);
    }

    /// A weak pixel that no neighbour agrees with takes the candidate its
    /// neighbours back; a weak pixel that is part of a line is left alone.
    #[test]
    fn resolve_fixes_orphans_and_spares_lines() {
        let (a, b) = (Some([200u8, 30, 40]), Some([10u8, 10, 200]));
        // 3x3, all `a`, centre voted `b` weakly with `a` as runner-up
        let mut decided = vec![a; 9];
        decided[4] = b;
        let mut shares = vec![1.0; 9];
        shares[4] = 0.5;
        let mut cands = vec![Vec::new(); 9];
        cands[4] = vec![(b, 0.5), (a, 0.4)];
        let r = resolve_orphans(3, 3, &decided, &shares, &cands, 0.6, 0);
        assert_eq!(r[4], a, "an orphan must follow its neighbours");

        // same, but the centre sits on a vertical line of `b`: it has support
        decided[1] = b;
        decided[7] = b;
        let r = resolve_orphans(3, 3, &decided, &shares, &cands, 0.6, 0);
        assert_eq!(r[4], b, "a pixel on a line must not be resolved away");

        // a confident orphan is the artist's highlight: never touched
        let mut decided = vec![a; 9];
        decided[4] = b;
        let shares = vec![1.0; 9];
        let r = resolve_orphans(3, 3, &decided, &shares, &cands, 0.6, 0);
        assert_eq!(r[4], b);
    }

    /// A block at scale factor 1 read through vote_rect must decide exactly as
    /// vote_block does, share and all - the fractional path is the same vote.
    #[test]
    fn vote_rect_at_factor_one_matches_vote_block() {
        let pal = Palette { colors: vec![[200, 30, 40], [10, 10, 200]] };
        let mut sheet = RgbaImage::new(3, 3);
        for (i, p) in sheet.pixels_mut().enumerate() {
            *p = if i % 4 == 0 { image::Rgba([10, 10, 200, 255]) } else { image::Rgba([200, 30, 40, 255]) };
        }
        let a = vote_block(&sheet, 0, 0, 3, &axis_weights(3), &pal, [128, 128, 128], None, 0);
        let b = vote_rect(&sheet, 0.0, 0.0, 3.0, 3.0, 3, &pal, [128, 128, 128], None, 0);
        assert_eq!(a.0, b.0);
        assert_eq!(a.1, b.1);
        assert!((a.2 - b.2).abs() < 1e-9, "{} vs {}", a.2, b.2);
    }

    /// A 5-pixel block scaled by 1.2246 covers 6-7 real pixels; a solid block
    /// must still come out solid and unanimous, wherever its edges fall.
    #[test]
    fn a_rescaled_solid_block_is_unanimous() {
        let pal = Palette { colors: vec![[200, 30, 40]] };
        let mut sheet = RgbaImage::new(16, 16);
        for p in sheet.pixels_mut() {
            *p = image::Rgba([200, 30, 40, 255]);
        }
        let k = 1254.0 / 1024.0;
        let (c, contested, share, _) = vote_rect(&sheet, 1.0 * k, 2.0 * k, 6.0 * k, 7.0 * k, 5, &pal, [128, 128, 128], None, 0);
        assert_eq!(c, Some([200, 30, 40]));
        assert!(!contested);
        assert_eq!(share, 1.0);
    }

    #[test]
    fn grid_fit_is_one_on_blocky_art_and_low_on_offgrid_art() {
        // 4x4 blocks of alternating colour: every edge on a boundary
        let mut on = RgbaImage::new(16, 16);
        for (x, y, p) in on.enumerate_pixels_mut() {
            *p = if ((x / 4) + (y / 4)) % 2 == 0 { image::Rgba([0, 0, 0, 255]) } else { image::Rgba([255, 255, 255, 255]) };
        }
        assert_eq!(grid_fit(&on, 0.0, 0.0, 4, 4, 4.0), 1.0);
        // the same art shifted by half a block: every edge mid-block
        let mut off = RgbaImage::new(16, 16);
        for (x, y, p) in off.enumerate_pixels_mut() {
            *p = if (((x + 2) / 4) + ((y + 2) / 4)) % 2 == 0 { image::Rgba([0, 0, 0, 255]) } else { image::Rgba([255, 255, 255, 255]) };
        }
        assert!(grid_fit(&off, 0.0, 0.0, 4, 4, 4.0) < 0.1);
    }

    /// Grain moves a pixel only onto a near shade its block voted for.
    #[test]
    fn grain_takes_a_voted_near_shade_and_nothing_else() {
        let (a, b, far) = ([100u8, 100, 100], [130u8, 120, 110], [250u8, 20, 20]);
        // centre b among a's: b-a distance ~36, inside 2*24 but outside 24
        let mut cur = vec![Some(a); 9];
        cur[4] = Some(b);
        let mut cands = vec![Vec::new(); 9];
        cands[4] = vec![(Some(b), 0.8), (Some(a), 0.2)];
        let (r, n) = smooth_grain(3, 3, &cur, &cands, 24);
        assert_eq!((r[4], n), (Some(a), 1));
        // same, but the block never voted for `a`: left as the model drew it
        cands[4] = vec![(Some(b), 1.0)];
        let (r, n) = smooth_grain(3, 3, &cur, &cands, 24);
        assert_eq!((r[4], n), (Some(b), 0));
        // a truly different colour is not grain, whatever the block says
        cur[4] = Some(far);
        cands[4] = vec![(Some(far), 0.5), (Some(a), 0.5)];
        let (r, n) = smooth_grain(3, 3, &cur, &cands, 24);
        assert_eq!((r[4], n), (Some(far), 0));
    }

    #[test]
    fn clean_drops_orphans_and_fills_pinholes() {
        let mut img = RgbaImage::new(5, 5);
        let solid = image::Rgba([10, 20, 30, 255]);
        for y in 1..4 {
            for x in 1..4 {
                img.put_pixel(x, y, solid);
            }
        }
        img.put_pixel(2, 2, image::Rgba([0, 0, 0, 0])); // pinhole
        img.put_pixel(0, 0, solid); // orphan
        let (specks, holes) = clean(&mut img);
        assert_eq!((specks, holes), (1, 1));
        assert_eq!(img.get_pixel(0, 0).0[3], 0);
        assert_eq!(img.get_pixel(2, 2).0, solid.0);
    }
}
