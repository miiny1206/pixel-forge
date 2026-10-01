//! Module 3b — conform on-grid art to a specific game's palette, canvas and key.
//!
//! Measured on PixelLab output against the game's art: two generations of the
//! same character that looked identical shared **zero** exact colours, because
//! each was quantised independently. So the palette is a property of the *set*,
//! taken once from reference frames and applied to every frame — never
//! re-derived per image.

use crate::palette::{collect_images, Palette};
use crate::{parse_colorkey, Args};
use anyhow::{bail, Context, Result};
use image::RgbaImage;
use serde::Serialize;
use std::collections::BTreeSet;
use std::path::PathBuf;

#[derive(Serialize)]
struct Report {
    palette_size: usize,
    colorkey: String,
    files: Vec<FileReport>,
}

#[derive(Serialize)]
struct FileReport {
    file: String,
    input: String,
    output: String,
    colors_in: usize,
    colors_out: usize,
    pixels_recolored: usize,
    semi_alpha_flattened: usize,
}

pub fn cmd_conform(args: &Args) -> Result<()> {
    let (input, outdir) = crate::import::two_paths(args, "pxf conform <in> <outdir> --ref P | --palette HEX")?;
    std::fs::create_dir_all(&outdir)?;

    let threshold: u8 = args.opt("alpha-threshold").unwrap_or("128").parse()?;
    let key = parse_colorkey(args.opt("colorkey"))?;
    // A generator that cannot emit alpha puts the subject on a flat background;
    // so does `pxf snap --flatten`. This turns that colour back into holes.
    let input_key = match args.opt("key") {
        Some(h) => Some(crate::snap::parse_hex(h)?),
        None => None,
    };
    let canvas = match args.opt("canvas") {
        Some(s) => Some(parse_canvas(s)?),
        None => None,
    };

    let base = if let Some(hex) = args.opt("palette") {
        Palette::from_hex(hex)?
    } else {
        let refs: Vec<PathBuf> = args.opts("ref").iter().map(PathBuf::from).collect();
        if refs.is_empty() {
            bail!("conform needs a target palette: pass --ref <frames from the game> or --palette <hex,...>");
        }
        Palette::from_refs(&refs, threshold)?
    };
    let pal = base.without_key(key.fixed());
    if pal.colors.len() < base.colors.len() {
        eprintln!("colour key removed from the palette ({} -> {} colours)", base.colors.len(), pal.colors.len());
    }
    if pal.colors.is_empty() {
        bail!("palette is empty once the colour key is removed");
    }

    let keyed_rgb = key.fixed().map(sprite_formats::spr::rgb565_to_rgb);
    let mut report = Report { palette_size: pal.colors.len(), colorkey: key.label(), files: Vec::new() };

    for path in collect_images(&input)? {
        let img = image::open(&path).with_context(|| format!("opening {}", path.display()))?.to_rgba8();
        let (out, fr) = conform_image(&img, &pal, threshold, canvas, keyed_rgb, input_key, &path);
        let name = path.file_name().unwrap().to_string_lossy().to_string();
        out.save(outdir.join(&name))?;
        println!(
            "{name}: {} -> {} colours, {} px recoloured{}",
            fr.colors_in,
            fr.colors_out,
            fr.pixels_recolored,
            if fr.input == fr.output { String::new() } else { format!(", canvas {} -> {}", fr.input, fr.output) }
        );
        report.files.push(fr);
    }

    if let Some(p) = args.opt("report") {
        std::fs::write(p, serde_json::to_vec_pretty(&report)?)?;
        println!("report -> {p}");
    }
    Ok(())
}

fn conform_image(
    img: &RgbaImage,
    pal: &Palette,
    threshold: u8,
    canvas: Option<(u32, u32)>,
    keyed_rgb: Option<[u8; 3]>,
    input_key: Option<[u8; 3]>,
    path: &std::path::Path,
) -> (RgbaImage, FileReport) {
    let mut colors_in: BTreeSet<[u8; 3]> = BTreeSet::new();
    let mut colors_out: BTreeSet<[u8; 3]> = BTreeSet::new();
    let mut recolored = 0usize;
    let mut flattened = 0usize;

    let mut work = RgbaImage::new(img.width(), img.height());
    for (x, y, p) in img.enumerate_pixels() {
        let [r, g, b, a] = p.0;
        // A resampled background does not leave a clean edge: it leaves pixels
        // part-way between the subject and the backdrop. Exact-match keying
        // leaves those as a coloured fringe, so a pixel also counts as
        // background when it sits nearer the key than anything in the palette.
        let on_input_key = input_key
            .map(|k| {
                let d_key = dist2(k, [r, g, b]);
                d_key <= 12 * 12 || d_key < dist2(pal.nearest([r, g, b]), [r, g, b])
            })
            .unwrap_or(false);
        if a < threshold || on_input_key {
            // Binary alpha, always. The engine colour-keys; a half-transparent
            // pixel has nowhere to go but the key, which reads as a hole.
            if a != 0 {
                flattened += 1;
            }
            work.put_pixel(x, y, image::Rgba([0, 0, 0, 0]));
            continue;
        }
        if a != 255 {
            flattened += 1;
        }
        colors_in.insert([r, g, b]);
        let mut c = pal.nearest([r, g, b]);
        if Some(c) == keyed_rgb {
            // Cannot happen while the key is excluded from the palette, but an
            // opaque pixel on the key is a hole in game, so refuse to emit one.
            c = second_nearest(pal, [r, g, b], keyed_rgb.unwrap());
        }
        if c != [r, g, b] {
            recolored += 1;
        }
        colors_out.insert(c);
        work.put_pixel(x, y, image::Rgba([c[0], c[1], c[2], 255]));
    }

    let input_size = format!("{}x{}", img.width(), img.height());
    let out = match canvas {
        Some((w, h)) if (w, h) != (work.width(), work.height()) => refit(&work, w, h),
        _ => work,
    };
    let output_size = format!("{}x{}", out.width(), out.height());

    (
        out,
        FileReport {
            file: path.file_name().unwrap().to_string_lossy().to_string(),
            input: input_size,
            output: output_size,
            colors_in: colors_in.len(),
            colors_out: colors_out.len(),
            pixels_recolored: recolored,
            semi_alpha_flattened: flattened,
        },
    )
}

fn second_nearest(pal: &Palette, rgb: [u8; 3], banned: [u8; 3]) -> [u8; 3] {
    let filtered = Palette { colors: pal.colors.iter().copied().filter(|c| *c != banned).collect() };
    if filtered.colors.is_empty() {
        return rgb;
    }
    filtered.nearest(rgb)
}

/// Centre the art on a new canvas, padding with transparency or cropping.
/// Centring is a stated default, not a guess at the real anchor: the per-frame
/// offsets live in the `.ani` scripts, which are not decoded yet.
fn refit(src: &RgbaImage, w: u32, h: u32) -> RgbaImage {
    let mut out = RgbaImage::new(w, h);
    let dx = (w as i64 - src.width() as i64) / 2;
    let dy = (h as i64 - src.height() as i64) / 2;
    for (x, y, p) in src.enumerate_pixels() {
        let (nx, ny) = (x as i64 + dx, y as i64 + dy);
        if nx >= 0 && ny >= 0 && (nx as u32) < w && (ny as u32) < h {
            out.put_pixel(nx as u32, ny as u32, *p);
        }
    }
    out
}

fn parse_canvas(s: &str) -> Result<(u32, u32)> {
    let (w, h) = s
        .split_once(['x', 'X'])
        .with_context(|| format!("--canvas wants WxH, got {s:?}"))?;
    Ok((w.trim().parse()?, h.trim().parse()?))
}

fn dist2(a: [u8; 3], b: [u8; 3]) -> i32 {
    a.iter().zip(b.iter()).map(|(x, y)| (*x as i32 - *y as i32).pow(2)).sum()
}
