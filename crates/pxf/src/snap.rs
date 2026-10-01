//! Module 3a — snap an off-grid render back onto its implicit grid.
//!
//! Thin wrapper over spritefusion-pixel-snapper (MIT, Hugo Duprez). Its only
//! public native entry point is directory-to-directory, so a single file is
//! staged through a scratch directory.
//!
//! The size guard is the point of this wrapper. Measured on real input:
//!
//!   * a soft 8x bicubic upscale of a 68x76 sprite  -> detected 8.0px, out 69x77
//!     (correct, off by the usual one-pixel edge)
//!   * the 68x76 sprite itself                      -> detected 5.5px, out 14x17
//!     (destroyed: the detector read a grid in the art's own shading)
//!
//! So: snap is for what a diffusion model hands you, never for a sprite that is
//! already one pixel per pixel. Use `pxf conform` for those.
//!
//! Transparency also confuses the detector. Same 8x upscale, same art:
//!
//!   kept as RGBA                -> 81x94   (wrong; true answer is 68x76)
//!   flattened onto magenta      -> 69x76
//!   flattened onto cyan         -> 68x78
//!
//! Hence `--flatten`: composite onto a solid colour first, then hand the colour
//! back to `pxf conform --key` to turn it into transparency again.

use crate::palette::{collect_images, Palette};
use crate::Args;
use anyhow::{bail, Context, Result};
use spritefusion_pixel_snapper::{process_batch_with_reporter, BatchConfig, BatchEvent};
use std::path::PathBuf;

/// Below this, a "pixel art" image is almost certainly already on-grid.
const MIN_OFFGRID_DIMENSION: u32 = 128;

pub fn cmd_snap(args: &Args) -> Result<()> {
    let (input, outdir) = crate::import::two_paths(args, "pxf snap <in> <outdir> [--colors N]")?;
    std::fs::create_dir_all(&outdir)?;

    let images = collect_images(&input)?;
    let mut largest = 0u32;
    for p in &images {
        let (w, h) = image::image_dimensions(p).with_context(|| format!("reading {}", p.display()))?;
        largest = largest.max(w.max(h));
    }
    if largest < MIN_OFFGRID_DIMENSION && !args.has("force") {
        bail!(
            "largest input is {largest}px on its long side — that is already on-grid art, and \
             snapping it shrinks it to nonsense (measured: 68x76 in, 14x17 out). \
             Use `pxf conform` to fix its palette instead, or pass --force if you really mean it."
        );
    }

    let palette = match (args.opt("palette"), args.opts("ref")) {
        (Some(hex), _) => Some(Palette::from_hex(hex)?),
        (None, refs) if !refs.is_empty() => {
            Some(Palette::from_refs(&refs.iter().map(PathBuf::from).collect::<Vec<_>>(), 128)?)
        }
        _ => None,
    };
    // `--flatten auto` picks the colour furthest from the target palette. It
    // matters: flattening onto magenta left 37 fringe pixels that quantised
    // onto a real purple in the palette, because magenta was close to one.
    let flatten = match args.opt("flatten") {
        Some("auto") => {
            let pal = palette.as_ref().context(
                "--flatten auto needs the target palette: pass --ref <game frames> or --palette",
            )?;
            let c = furthest_from(pal);
            eprintln!("--flatten auto chose {:02x}{:02x}{:02x}", c[0], c[1], c[2]);
            Some(c)
        }
        Some(h) => Some(parse_hex(h)?),
        None => None,
    };

    let k_colors: usize = match args.opt("colors") {
        Some(v) => v.parse()?,
        None => palette.as_ref().map(|p| p.colors.len()).unwrap_or(16),
    };

    // The library takes a directory, and --flatten has to rewrite the pixels
    // first, so everything is staged through a scratch directory.
    let scratch = std::env::temp_dir().join(format!("pxf-snap-{}", std::process::id()));
    let input_dir = if flatten.is_none() && input.is_dir() {
        input.clone()
    } else {
        std::fs::create_dir_all(&scratch)?;
        let mut had_alpha = false;
        for p in &images {
            let name = p.file_name().context("input has no file name")?;
            match flatten {
                Some(bg) => {
                    let src = image::open(p)?.to_rgba8();
                    had_alpha |= src.pixels().any(|px| px.0[3] != 255);
                    flatten_onto(&src, bg).save(scratch.join(name))?;
                }
                None => {
                    let src = image::open(p)?.to_rgba8();
                    had_alpha |= src.pixels().any(|px| px.0[3] != 255);
                    std::fs::copy(p, scratch.join(name))?;
                }
            }
        }
        if had_alpha && flatten.is_none() {
            eprintln!(
                "warning: input has transparency and --flatten was not given. The grid detector \
                 reads alpha as image content and misjudges the pixel size (measured: 81x94 \
                 instead of 68x76). Try --flatten ff00ff, then `pxf conform --key ff00ff`."
            );
        }
        scratch.clone()
    };

    let config = BatchConfig {
        input_dir,
        output_dir: outdir.clone(),
        k_colors,
        pixel_size_override: match args.opt("pixel-size") {
            Some(v) => Some(v.parse()?),
            None => None,
        },
        // The flatten colour has to survive quantisation, or there is nothing
        // left for `pxf conform --key` to remove and the backdrop becomes part
        // of the sprite. Measured without this: 5168 opaque pixels out of a
        // 2407-pixel character.
        palette: palette.as_ref().map(|p| {
            let mut colors = p.colors.clone();
            if let Some(bg) = flatten {
                if !colors.contains(&bg) {
                    colors.push(bg);
                }
            }
            colors
        }),
    };

    let result = process_batch_with_reporter(&config, |event| match event {
        BatchEvent::Finished { input, output, index, total } => {
            let size = image::image_dimensions(&output)
                .map(|(w, h)| format!("{w}x{h}"))
                .unwrap_or_else(|_| "?".into());
            println!(
                "[{}/{}] {} -> {size}",
                index + 1,
                total,
                input.file_name().unwrap_or_default().to_string_lossy()
            );
        }
        BatchEvent::Failed { input, error, .. } => {
            eprintln!("failed {}: {error}", input.display());
        }
        _ => {}
    });

    let _ = std::fs::remove_dir_all(&scratch);
    result.map_err(|e| anyhow::anyhow!("{e}"))?;
    println!("snapped -> {}", outdir.display());
    println!("next: pxf conform {} <out> --ref <game frames> --canvas WxH", outdir.display());
    Ok(())
}

fn flatten_onto(src: &image::RgbaImage, bg: [u8; 3]) -> image::RgbaImage {
    let mut out = image::RgbaImage::new(src.width(), src.height());
    for (x, y, p) in src.enumerate_pixels() {
        let a = p.0[3] as u32;
        let mix = |c: u8, b: u8| ((c as u32 * a + b as u32 * (255 - a)) / 255) as u8;
        out.put_pixel(
            x,
            y,
            image::Rgba([mix(p.0[0], bg[0]), mix(p.0[1], bg[1]), mix(p.0[2], bg[2]), 255]),
        );
    }
    out
}

pub fn parse_hex(s: &str) -> Result<[u8; 3]> {
    let h = s.strip_prefix('#').unwrap_or(s);
    if h.len() != 6 {
        bail!("expected 6 hex digits, got {s:?}");
    }
    let v = u32::from_str_radix(h, 16)?;
    Ok([(v >> 16) as u8, (v >> 8) as u8, v as u8])
}

/// The colour a target palette is least able to imitate, so fringe pixels left
/// by flattening have nowhere plausible to quantise. Coarse grid; exact is not
/// needed, only "far from everything".
fn furthest_from(pal: &Palette) -> [u8; 3] {
    let mut best = [255u8, 0, 255];
    let mut best_d = -1i32;
    for r in (0..=255u32).step_by(17) {
        for g in (0..=255u32).step_by(17) {
            for b in (0..=255u32).step_by(17) {
                let c = [r as u8, g as u8, b as u8];
                let d = pal
                    .colors
                    .iter()
                    .map(|p| {
                        p.iter().zip(c.iter()).map(|(x, y)| (*x as i32 - *y as i32).pow(2)).sum::<i32>()
                    })
                    .min()
                    .unwrap_or(0);
                if d > best_d {
                    best_d = d;
                    best = c;
                }
            }
        }
    }
    best
}
