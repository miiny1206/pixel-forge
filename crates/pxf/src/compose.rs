//! Module 4 — build new sheets out of pieces the game already ships.
//!
//! Most "new art" a live game needs is not new: a higher grade of a card is the
//! lower grade's portrait, a longer star strip and a badge lifted from another
//! sheet. Doing that by hand across 66 frames is where drift creeps in; doing
//! it from a spec keeps every frame on the same recipe.
//!
//!   lift     pull an overlay (badge, glow, stamp) out of a sheet as RGBA, by
//!            comparing it frame by frame against the sheet it was drawn over
//!   compose  apply a list of ops (fill, stamp, overlay) to every frame of a
//!            base sheet and write a directory `pxf export` accepts as is
//!
//! Neither command invents a colour: `fill` and `stamp` copy exact pixels,
//! `overlay` blends a layer that `lift` measured from shipped art.

use crate::manifest::Manifest;
use crate::snap::parse_hex;
use crate::Args;
use anyhow::{bail, Context, Result};
use image::RgbaImage;
use serde::Deserialize;
use std::path::{Path, PathBuf};

fn load_manifest(dir: &Path) -> Result<Manifest> {
    let p = dir.join("manifest.json");
    serde_json::from_slice(&std::fs::read(&p).with_context(|| format!("reading {}", p.display()))?)
        .with_context(|| format!("parsing {}", p.display()))
}

fn frame_png(dir: &Path, m: &Manifest, index: usize) -> Result<Option<RgbaImage>> {
    let Some(fe) = m.frames.iter().find(|f| f.index == index) else {
        bail!("{} has no frame {index}", dir.display())
    };
    match &fe.file {
        None => Ok(None),
        Some(f) => Ok(Some(
            image::open(dir.join(f)).with_context(|| format!("opening {}", dir.join(f).display()))?.to_rgba8(),
        )),
    }
}

fn parse_rect(s: &str) -> Result<[u32; 4]> {
    let v: Vec<u32> = s.split(',').map(|t| t.trim().parse()).collect::<Result<_, _>>()?;
    if v.len() != 4 {
        bail!("a rect is x,y,w,h — got {s:?}");
    }
    Ok([v[0], v[1], v[2], v[3]])
}

// ---------------------------------------------------------------- lift

/// `pxf lift --over Z --under H --control C --rect x,y,w,h <out.png>`
///
/// Per pixel, over the frames where `under` and `control` agree (the pixel is
/// the shared portrait there, not a grade-coloured background), fit
/// `over = k * under + m` with one `k` for all three channels. `k = 0` is an
/// opaque overlay pixel, `0 < k < 1` a translucent one (alpha `1 - k`, colour
/// `m / alpha`), `k = 1, m = 0` nothing at all. A pixel that never varies
/// across frames cannot be measured and is left transparent.
pub fn cmd_lift(args: &Args) -> Result<()> {
    let out = PathBuf::from(args.positional.first().context(
        "usage: pxf lift <out.png> --over DIR --under DIR --control DIR --rect x,y,w,h \
         [--clip x,y,w,h] [--min-alpha F] [--passes N]",
    )?);
    let over = PathBuf::from(args.opt("over").context("--over <dir> is required")?);
    let under = PathBuf::from(args.opt("under").context("--under <dir> is required")?);
    let control = PathBuf::from(args.opt("control").context("--control <dir> is required")?);
    let [rx, ry, rw, rh] = parse_rect(args.opt("rect").context("--rect x,y,w,h is required")?)?;
    let clip = args.opt("clip").map(parse_rect).transpose()?;
    let min_alpha: f64 = args.opt("min-alpha").map(str::parse).transpose()?.unwrap_or(0.08);
    let passes: usize = args.opt("passes").map(str::parse).transpose()?.unwrap_or(2);

    let (mo, mu, mc) = (load_manifest(&over)?, load_manifest(&under)?, load_manifest(&control)?);
    let mut stack = Vec::new();
    for fe in &mo.frames {
        let (Some(o), Some(u), Some(c)) = (
            frame_png(&over, &mo, fe.index)?,
            frame_png(&under, &mu, fe.index)?,
            frame_png(&control, &mc, fe.index)?,
        ) else {
            continue;
        };
        // Frames that differ in size are not the same layout; skip them.
        if o.dimensions() != u.dimensions() || o.dimensions() != c.dimensions()
            || rx + rw > o.width() || ry + rh > o.height()
        {
            continue;
        }
        stack.push((o, u, c));
    }
    if stack.len() < 2 {
        bail!("need at least two frames of the same size in all three sheets, found {}", stack.len());
    }

    let mut img = RgbaImage::new(rw, rh);
    let mut solved = 0usize;
    for y in 0..rh {
        for x in 0..rw {
            if let Some([cx, cy, cw, ch]) = clip {
                if x < cx || y < cy || x >= cx + cw || y >= cy + ch {
                    continue;
                }
            }
            let (px, py) = (rx + x, ry + y);
            let pts: Vec<([f64; 3], [f64; 3])> = stack
                .iter()
                .filter(|(_, u, c)| u.get_pixel(px, py).0[..3] == c.get_pixel(px, py).0[..3])
                .map(|(o, u, _)| {
                    let (o, u) = (o.get_pixel(px, py).0, u.get_pixel(px, py).0);
                    ([u[0] as f64, u[1] as f64, u[2] as f64], [o[0] as f64, o[1] as f64, o[2] as f64])
                })
                .collect();
            if pts.len() < 2 {
                continue;
            }
            let n = pts.len() as f64;
            let (mut num, mut den) = (0.0, 0.0);
            let mut mean_u = [0.0; 3];
            let mut mean_o = [0.0; 3];
            for ch in 0..3 {
                mean_u[ch] = pts.iter().map(|p| p.0[ch]).sum::<f64>() / n;
                mean_o[ch] = pts.iter().map(|p| p.1[ch]).sum::<f64>() / n;
                for (u, o) in &pts {
                    num += (u[ch] - mean_u[ch]) * (o[ch] - mean_o[ch]);
                    den += (u[ch] - mean_u[ch]).powi(2);
                }
            }
            if den == 0.0 {
                continue; // the pixel under it never changed: nothing to measure against
            }
            let k = (num / den).clamp(0.0, 1.0);
            let a = 1.0 - k;
            if a < min_alpha {
                continue;
            }
            let mut rgb = [0u8; 3];
            for ch in 0..3 {
                rgb[ch] = ((mean_o[ch] - k * mean_u[ch]) / a).round().clamp(0.0, 255.0) as u8;
            }
            img.put_pixel(x, y, image::Rgba([rgb[0], rgb[1], rgb[2], (a * 255.0).round() as u8]));
            solved += 1;
        }
    }

    // Measurement noise shows up as lone translucent pixels; a real overlay is
    // a connected blob. Opaque pixels are always kept.
    for _ in 0..passes {
        let alive: Vec<bool> = img.pixels().map(|p| p.0[3] > 0).collect();
        let at = |x: i64, y: i64| x >= 0 && y >= 0 && x < rw as i64 && y < rh as i64 && alive[(y * rw as i64 + x) as usize];
        for y in 0..rh as i64 {
            for x in 0..rw as i64 {
                if !at(x, y) || img.get_pixel(x as u32, y as u32).0[3] >= 200 {
                    continue;
                }
                let n = (-1..=1).flat_map(|dy| (-1..=1).map(move |dx| (dx, dy)))
                    .filter(|&(dx, dy)| (dx, dy) != (0, 0) && at(x + dx, y + dy))
                    .count();
                if n < 4 {
                    img.put_pixel(x as u32, y as u32, image::Rgba([0, 0, 0, 0]));
                }
            }
        }
    }
    let kept = img.pixels().filter(|p| p.0[3] > 0).count();
    img.save(&out)?;
    println!(
        "{}: {kept} px kept ({solved} measured) from {} frame(s)",
        out.display(),
        stack.len()
    );
    Ok(())
}

// ---------------------------------------------------------------- compose

#[derive(Deserialize)]
struct Spec {
    /// Sheet directory (an import) whose frames are the starting point.
    base: String,
    ops: Vec<Op>,
}

#[derive(Deserialize)]
#[serde(tag = "op", rename_all = "lowercase")]
enum Op {
    /// Paint a rectangle one colour.
    Fill { rect: [u32; 4], rgb: String },
    /// Copy a rectangle of one frame of a sheet (`frame` absent = the frame
    /// being built) to one or more places. `key` pixels are not copied.
    Stamp {
        from: String,
        #[serde(default)]
        frame: Option<usize>,
        rect: [u32; 4],
        #[serde(default)]
        key: Option<String>,
        #[serde(default)]
        at: Vec<[i64; 2]>,
        /// `count` copies spread evenly from `x0` to `x1` (left edges) at `y`.
        #[serde(default)]
        repeat: Option<Repeat>,
    },
    /// Alpha-blend a PNG layer (e.g. one written by `pxf lift`).
    Overlay { png: String, at: [i64; 2] },
}

#[derive(Deserialize)]
struct Repeat {
    count: u32,
    x0: i64,
    x1: i64,
    y: i64,
}

/// `pxf compose <outdir> --spec spec.json` — paths in the spec are relative to
/// the spec file. The output keeps the base's manifest, so `pxf export` works
/// on it unchanged (and re-seals the header with whatever name it is given).
pub fn cmd_compose(args: &Args) -> Result<()> {
    let outdir = PathBuf::from(args.positional.first().context("usage: pxf compose <outdir> --spec spec.json")?);
    let spec_path = PathBuf::from(args.opt("spec").context("pxf compose needs --spec <spec.json>")?);
    let spec: Spec = serde_json::from_slice(
        &std::fs::read(&spec_path).with_context(|| format!("reading {}", spec_path.display()))?,
    )
    .with_context(|| format!("parsing {}", spec_path.display()))?;
    let root = spec_path.parent().map(Path::to_path_buf).unwrap_or_default();
    let rel = |p: &str| root.join(p);

    let base = rel(&spec.base);
    let m = load_manifest(&base)?;
    std::fs::create_dir_all(&outdir)?;

    // Load every sheet and layer once.
    let mut sheets: std::collections::HashMap<String, (PathBuf, Manifest)> = Default::default();
    let mut layers: std::collections::HashMap<String, RgbaImage> = Default::default();
    for op in &spec.ops {
        match op {
            Op::Stamp { from, .. } if !sheets.contains_key(from) => {
                let d = rel(from);
                let mm = load_manifest(&d)?;
                sheets.insert(from.clone(), (d, mm));
            }
            Op::Overlay { png, .. } if !layers.contains_key(png) => {
                layers.insert(png.clone(), image::open(rel(png))
                    .with_context(|| format!("opening {}", rel(png).display()))?.to_rgba8());
            }
            _ => {}
        }
    }

    let mut written = 0;
    for fe in &m.frames {
        let Some(mut img) = frame_png(&base, &m, fe.index)? else { continue };
        let (w, h) = (img.width() as i64, img.height() as i64);
        let put = |img: &mut RgbaImage, x: i64, y: i64, p: image::Rgba<u8>| {
            if x >= 0 && y >= 0 && x < w && y < h {
                img.put_pixel(x as u32, y as u32, p);
            }
        };
        for op in &spec.ops {
            match op {
                Op::Fill { rect: [x, y, rw, rh], rgb } => {
                    let c = parse_hex(rgb)?;
                    for yy in *y..y + rh {
                        for xx in *x..x + rw {
                            put(&mut img, xx as i64, yy as i64, image::Rgba([c[0], c[1], c[2], 255]));
                        }
                    }
                }
                Op::Stamp { from, frame, rect: [sx, sy, sw, sh], key, at, repeat } => {
                    let (d, mm) = &sheets[from];
                    let src = frame_png(d, mm, frame.unwrap_or(fe.index))?
                        .with_context(|| format!("{from} frame {} is a placeholder", frame.unwrap_or(fe.index)))?;
                    let key = key.as_deref().map(parse_hex).transpose()?;
                    let mut places = at.clone();
                    if let Some(r) = repeat {
                        for k in 0..r.count {
                            let x = if r.count > 1 {
                                r.x0 + ((r.x1 - r.x0) as f64 * k as f64 / (r.count - 1) as f64).round() as i64
                            } else {
                                r.x0
                            };
                            places.push([x, r.y]);
                        }
                    }
                    for [ax, ay] in places {
                        for yy in 0..*sh {
                            for xx in 0..*sw {
                                let p = *src.get_pixel(sx + xx, sy + yy);
                                if p.0[3] == 0 || key.map(|k| p.0[..3] == k[..]).unwrap_or(false) {
                                    continue;
                                }
                                put(&mut img, ax + xx as i64, ay + yy as i64, p);
                            }
                        }
                    }
                }
                Op::Overlay { png, at: [ax, ay] } => {
                    let l = &layers[png];
                    for (lx, ly, p) in l.enumerate_pixels() {
                        let (x, y) = (ax + lx as i64, ay + ly as i64);
                        if p.0[3] == 0 || x < 0 || y < 0 || x >= w || y >= h {
                            continue;
                        }
                        let q = img.get_pixel_mut(x as u32, y as u32);
                        let a = p.0[3] as u32;
                        for c in 0..3 {
                            q.0[c] = ((p.0[c] as u32 * a + q.0[c] as u32 * (255 - a) + 127) / 255) as u8;
                        }
                    }
                }
            }
        }
        img.save(outdir.join(fe.file.as_ref().unwrap()))?;
        written += 1;
    }
    let mut out_m = m;
    out_m.source = format!("pxf compose {}", spec_path.display());
    std::fs::write(outdir.join("manifest.json"), serde_json::to_string_pretty(&out_m)?)?;
    println!("{written} frame(s) -> {}", outdir.display());
    Ok(())
}
