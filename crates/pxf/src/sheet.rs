//! Module 3c — many frames, one edit pass.
//!
//! The single most useful thing learned from the Qwen-Image-Edit runs: four
//! frames laid out on one sheet and edited together came back wearing the *same*
//! outfit, while frames edited one at a time drift. So the sheet layout is part
//! of the pipeline, not a debugging convenience, and it has to round-trip
//! exactly — otherwise model drift and layout bugs are indistinguishable.
//!
//! Layout matches the kernel that already worked: frames side by side at source
//! resolution with a gap, bottom-aligned, the whole strip then scaled
//! nearest-neighbour and centred on a flat background.

use crate::palette::collect_images;
use crate::Args;
use anyhow::{bail, Context, Result};
use image::RgbaImage;
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Serialize, Deserialize)]
pub struct SheetManifest {
    pub scale: u32,
    pub gap: u32,
    pub background: String,
    pub canvas: [u32; 2],
    pub frames: Vec<SheetFrame>,
}

#[derive(Serialize, Deserialize)]
pub struct SheetFrame {
    pub file: String,
    /// Position in **sheet** pixels, already multiplied by `scale`.
    pub x: u32,
    pub y: u32,
    /// Size in **source** pixels.
    pub w: u32,
    pub h: u32,
}

pub fn cmd_sheet(args: &Args) -> Result<()> {
    let (input, out) = crate::import::two_paths(args, "pxf sheet <indir> <out.png>")?;
    let scale: u32 = args.opt("scale").unwrap_or("3").parse()?;
    let gap: u32 = args.opt("gap").unwrap_or("6").parse()?;
    let bg = crate::snap::parse_hex(args.opt("bg").unwrap_or("808080"))?;

    let paths = collect_images(&input)?;
    let imgs: Vec<RgbaImage> = paths
        .iter()
        .map(|p| Ok(image::open(p).with_context(|| format!("opening {}", p.display()))?.to_rgba8()))
        .collect::<Result<_>>()?;

    // Rows exist for one reason: a diffusion model is trained at a particular
    // canvas shape, and laying four frames in one strip forces a long thin
    // canvas far from it. Splitting into rows keeps the sheet near-square, so
    // each frame gets more pixels at the same canvas size.
    let rows: usize = args.opt("rows").unwrap_or("1").parse()?;
    if rows == 0 || rows > imgs.len() {
        bail!("--rows must be between 1 and the frame count ({})", imgs.len());
    }
    let per_row = imgs.len().div_ceil(rows);
    let chunks: Vec<&[RgbaImage]> = imgs.chunks(per_row).collect();
    let strip_w: u32 = chunks
        .iter()
        .map(|c| c.iter().map(|i| i.width()).sum::<u32>() + gap * (c.len() as u32 - 1))
        .max()
        .unwrap_or(0);
    let row_h: Vec<u32> = chunks.iter().map(|c| c.iter().map(|i| i.height()).max().unwrap_or(0)).collect();
    let strip_h: u32 = row_h.iter().sum::<u32>() + gap * (rows as u32 - 1);

    let (cw, ch) = match args.opt("canvas") {
        Some(s) => {
            let (w, h) = s.split_once(['x', 'X']).context("--canvas wants WxH")?;
            (w.parse::<u32>()?, h.parse::<u32>()?)
        }
        None => (strip_w * scale, strip_h * scale),
    };
    if cw < strip_w * scale || ch < strip_h * scale {
        bail!(
            "canvas {cw}x{ch} is smaller than the scaled strip {}x{} — lower --scale or raise --canvas",
            strip_w * scale,
            strip_h * scale
        );
    }
    let ox = (cw - strip_w * scale) / 2;
    let oy = (ch - strip_h * scale) / 2;

    let mut sheet = RgbaImage::from_pixel(cw, ch, image::Rgba([bg[0], bg[1], bg[2], 255]));
    let mut frames = Vec::with_capacity(imgs.len());
    let mut cursor = 0u32;
    let mut row = 0usize;
    let mut row_top = 0u32;
    let mut in_row = 0usize;
    for (p, img) in paths.iter().zip(&imgs) {
        if in_row == per_row {
            row_top += row_h[row] + gap;
            row += 1;
            in_row = 0;
            cursor = 0;
        }
        // Bottom-aligned within its row: these are standing characters, so their
        // feet line up.
        let fx = ox + cursor * scale;
        let fy = oy + (row_top + row_h[row] - img.height()) * scale;
        for (x, y, px) in img.enumerate_pixels() {
            let src = if px.0[3] == 0 { image::Rgba([bg[0], bg[1], bg[2], 255]) } else { *px };
            for dy in 0..scale {
                for dx in 0..scale {
                    sheet.put_pixel(fx + x * scale + dx, fy + y * scale + dy, src);
                }
            }
        }
        frames.push(SheetFrame {
            file: p.file_name().unwrap().to_string_lossy().to_string(),
            x: fx,
            y: fy,
            w: img.width(),
            h: img.height(),
        });
        cursor += img.width() + gap;
        in_row += 1;
    }

    sheet.save(&out)?;
    let m = SheetManifest {
        scale,
        gap,
        background: format!("{:02x}{:02x}{:02x}", bg[0], bg[1], bg[2]),
        canvas: [cw, ch],
        frames,
    };
    let mpath = out.with_extension("json");
    std::fs::write(&mpath, serde_json::to_vec_pretty(&m)?)?;
    println!("{} frame(s) -> {} ({}x{}), manifest {}", m.frames.len(), out.display(), cw, ch, mpath.display());
    Ok(())
}

pub fn cmd_unsheet(args: &Args) -> Result<()> {
    if args.positional.len() < 2 {
        bail!("usage: pxf unsheet <sheet.png> <outdir> [--manifest sheet.json]");
    }
    let sheet_path = PathBuf::from(&args.positional[0]);
    let outdir = PathBuf::from(&args.positional[1]);
    let mpath = match args.opt("manifest") {
        Some(p) => PathBuf::from(p),
        None => sheet_path.with_extension("json"),
    };
    let m: SheetManifest = serde_json::from_slice(
        &std::fs::read(&mpath).with_context(|| format!("reading {}", mpath.display()))?,
    )?;
    let sheet = image::open(&sheet_path)?.to_rgba8();
    if sheet.dimensions() != (m.canvas[0], m.canvas[1]) {
        bail!(
            "{} is {}x{} but the manifest says {}x{} — an edit that resized the sheet cannot be sliced",
            sheet_path.display(),
            sheet.width(),
            sheet.height(),
            m.canvas[0],
            m.canvas[1]
        );
    }
    std::fs::create_dir_all(&outdir)?;
    for f in &m.frames {
        let crop = image::imageops::crop_imm(&sheet, f.x, f.y, f.w * m.scale, f.h * m.scale).to_image();
        crop.save(outdir.join(&f.file))?;
    }
    println!(
        "{} frame(s) -> {} (still at {}x scale; next: pxf snap --pixel-size {})",
        m.frames.len(),
        outdir.display(),
        m.scale,
        m.scale
    );
    Ok(())
}
