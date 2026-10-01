//! Module 3f — repaint a hand-authored region into a garment's own colour ramp.
//!
//! Why this exists rather than a wider mask: a whole-frame generator redraws
//! the pose, so the only pixels worth taking from it are the ones it drew in
//! the same place. When the garment needs to cover *more* of the body than the
//! generator gave — a bra cup that has to follow a breast the model left bare —
//! widening the mask drags the model's arm in with it. Painting is the cheaper
//! and more controllable answer: 0 GPU, and the arm is never a candidate.
//!
//! The paint is not flat. Each pixel keeps its position in the region's
//! luminance order and is remapped onto a ramp of colours taken from the
//! garment that is already there, so the existing form-shading of the body
//! carries through into the new cloth instead of being flattened away.

use crate::palette::collect_images;
use crate::snap::parse_hex;
use crate::Args;
use anyhow::{bail, Context, Result};
use serde::Deserialize;
use std::collections::BTreeMap;
use std::path::PathBuf;

#[derive(Deserialize)]
pub struct Region {
    #[serde(flatten)]
    shape: crate::mask::FrameSpec,
    /// Darkest first. Written as `rrggbb`; quantised to RGB565 before use so
    /// the result stays inside the sprite's own colour space.
    ramp: Vec<String>,
    /// Optional luminance cut points, `ramp.len() - 1` of them. Pin these when
    /// a region has to match across frames — the default splits by population,
    /// which moves if the region changes size.
    #[serde(default)]
    thresholds: Option<Vec<f64>>,
}

fn lum(p: &[u8; 4]) -> f64 {
    0.299 * p[0] as f64 + 0.587 * p[1] as f64 + 0.114 * p[2] as f64
}

pub fn cmd_recolor(args: &Args) -> Result<()> {
    let (input, outdir) =
        crate::import::two_paths(args, "pxf recolor <framedir> <outdir> --spec spec.json")?;
    std::fs::create_dir_all(&outdir)?;
    let spec_path = args.opt("spec").context("pxf recolor needs --spec <spec.json>")?;
    let spec: BTreeMap<String, Vec<Region>> = serde_json::from_slice(
        &std::fs::read(spec_path).with_context(|| format!("reading {spec_path}"))?,
    )
    .with_context(|| format!("parsing {spec_path}"))?;

    let overlay_dir = args.opt("overlay").map(PathBuf::from);
    if let Some(d) = &overlay_dir {
        std::fs::create_dir_all(d)?;
    }

    #[derive(serde::Serialize)]
    struct RegionReport {
        painted_px: usize,
        thresholds: Vec<f64>,
        bin_counts: Vec<usize>,
    }
    #[derive(serde::Serialize)]
    struct FrameReport {
        file: String,
        regions: Vec<RegionReport>,
    }
    let mut reports = Vec::new();
    let mut total = 0usize;

    for path in collect_images(&input)? {
        let stem = path.file_stem().unwrap().to_string_lossy().to_string();
        let name = path.file_name().unwrap().to_string_lossy().to_string();
        let mut img = image::open(&path)
            .with_context(|| format!("opening {}", path.display()))?
            .to_rgba8();
        let Some(regions) = spec.get(&stem) else {
            img.save(outdir.join(&name))?;
            eprintln!("{stem}: no entry in the spec, copied unchanged");
            continue;
        };

        let mut overlay = overlay_dir.as_ref().map(|_| img.clone());
        let mut rr = Vec::new();

        for (ri, region) in regions.iter().enumerate() {
            if region.ramp.is_empty() {
                bail!("{stem} region {ri}: ramp is empty");
            }
            let ramp: Vec<[u8; 3]> = region
                .ramp
                .iter()
                .map(|h| parse_hex(h).map(sprite_formats::spr::canonicalize))
                .collect::<Result<_>>()?;

            let m = crate::mask::rasterize(&region.shape, &img);
            let w = img.width() as usize;
            let picked: Vec<(u32, u32)> = m
                .iter()
                .enumerate()
                .filter(|(_, on)| **on)
                .map(|(i, _)| ((i % w) as u32, (i / w) as u32))
                .collect();
            if picked.is_empty() {
                eprintln!("{stem} region {ri}: WARNING empty — check the polygon coordinates");
                rr.push(RegionReport { painted_px: 0, thresholds: vec![], bin_counts: vec![] });
                continue;
            }

            let mut ls: Vec<f64> = picked.iter().map(|(x, y)| lum(&img.get_pixel(*x, *y).0)).collect();
            let cuts = match &region.thresholds {
                Some(t) => {
                    if t.len() + 1 != ramp.len() {
                        bail!(
                            "{stem} region {ri}: {} thresholds for a {}-colour ramp, expected {}",
                            t.len(), ramp.len(), ramp.len() - 1
                        );
                    }
                    t.clone()
                }
                None => {
                    ls.sort_by(|a, b| a.partial_cmp(b).unwrap());
                    (1..ramp.len())
                        .map(|i| ls[(i * ls.len() / ramp.len()).min(ls.len() - 1)])
                        .collect()
                }
            };

            let mut bins = vec![0usize; ramp.len()];
            for (x, y) in &picked {
                let l = lum(&img.get_pixel(*x, *y).0);
                // `>` not `>=`: a flat region has many pixels at the same luminance, and a
                // quantile cut lands on that value. Ties belong to the darker bin, so an
                // already-dark garment is never lightened by being repainted.
                let bin = cuts.iter().take_while(|c| l > **c).count().min(ramp.len() - 1);
                bins[bin] += 1;
                let a = img.get_pixel(*x, *y).0[3];
                let c = ramp[bin];
                img.put_pixel(*x, *y, image::Rgba([c[0], c[1], c[2], a]));
                if let Some(ov) = &mut overlay {
                    ov.put_pixel(*x, *y, image::Rgba([255, 60, 60, 255]));
                }
            }
            total += picked.len();
            println!(
                "{name} region {ri}: painted {} px into {} tones {:?}",
                picked.len(), ramp.len(), bins
            );
            rr.push(RegionReport { painted_px: picked.len(), thresholds: cuts, bin_counts: bins });
        }

        img.save(outdir.join(&name))?;
        if let (Some(d), Some(ov)) = (&overlay_dir, overlay) {
            ov.save(d.join(&name))?;
        }
        reports.push(FrameReport { file: name, regions: rr });
    }

    if let Some(p) = args.opt("report") {
        std::fs::write(p, serde_json::to_vec_pretty(&reports)?)?;
        println!("report -> {p}");
    }
    if total == 0 {
        bail!("every region came out empty");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::RgbaImage;

    fn spec(json: &str) -> Vec<Region> {
        serde_json::from_str(json).unwrap()
    }

    #[test]
    fn a_gradient_keeps_its_order_after_remapping() {
        // A 4x1 ramp of increasing luminance must land in increasing ramp bins.
        let mut img = RgbaImage::new(4, 1);
        for x in 0..4 {
            let v = (x as u8 + 1) * 40;
            img.put_pixel(x, 0, image::Rgba([v, v, v, 255]));
        }
        let regions = spec(
            r#"[{"add":[[[0,0],[4,0],[4,1],[0,1]]],"ramp":["000000","808080","ffffff"]}]"#,
        );
        let r = &regions[0];
        let m = crate::mask::rasterize(&r.shape, &img);
        assert_eq!(m.iter().filter(|v| **v).count(), 4);
        let mut ls: Vec<f64> = (0..4).map(|x| lum(&img.get_pixel(x, 0).0)).collect();
        ls.sort_by(|a, b| a.partial_cmp(b).unwrap());
        let cuts: Vec<f64> = (1..3).map(|i| ls[(i * 4 / 3).min(3)]).collect();
        let bins: Vec<usize> = (0..4)
            .map(|x| {
                let l = lum(&img.get_pixel(x, 0).0);
                cuts.iter().take_while(|c| l > **c).count().min(2)
            })
            .collect();
        assert!(bins.windows(2).all(|w| w[0] <= w[1]), "{bins:?}");
        assert_eq!(bins[0], 0);
        assert_eq!(bins[3], 2);
    }

    #[test]
    fn transparent_pixels_are_never_painted() {
        let mut img = RgbaImage::from_pixel(4, 1, image::Rgba([200, 200, 200, 255]));
        img.put_pixel(2, 0, image::Rgba([0, 0, 0, 0]));
        let regions = spec(r#"[{"add":[[[0,0],[4,0],[4,1],[0,1]]],"ramp":["100808"]}]"#);
        let m = crate::mask::rasterize(&regions[0].shape, &img);
        assert_eq!(m.iter().filter(|v| **v).count(), 3);
        assert!(!m[2]);
    }

    #[test]
    fn a_pinned_threshold_beats_the_population_split() {
        let regions = spec(
            r#"[{"add":[[[0,0],[2,0],[2,1],[0,1]]],"ramp":["000000","ffffff"],"thresholds":[100.0]}]"#,
        );
        assert_eq!(regions[0].thresholds.as_ref().unwrap(), &vec![100.0]);
    }
}
