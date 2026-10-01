//! `pxf ani` / `pxf ani-build` — read and write the `.ani` animation scripts.
//!
//! The script is what places each `.spr` frame on screen, so it is the only
//! authority on where a redrawn frame has to keep its pixels. `import` already
//! folds it into `manifest.json` (`anchors`) and `anim.json`; these commands
//! are for looking at one directly and for writing an edited one back.

use crate::Args;
use anyhow::{Context, Result};
use sprite_formats::{ark, Ani};
use std::path::{Path, PathBuf};

fn summary(label: &str, ani: &Ani) {
    let played: Vec<_> = ani.played().collect();
    let frames: std::collections::BTreeSet<i32> = played.iter().map(|(_, _, s)| s.frame).collect();
    let (mut x0, mut y0, mut x1, mut y1) = (i32::MAX, i32::MAX, i32::MIN, i32::MIN);
    for (_, _, s) in &played {
        x0 = x0.min(s.x);
        y0 = y0.min(s.y);
        x1 = x1.max(s.x);
        y1 = y1.max(s.y);
    }
    println!(
        "{label}: name {:?}, {} animation(s) x {} steps, {} played step(s), frames {}..{} ({} distinct), offsets x {x0}..{x1} y {y0}..{y1}",
        ani.name().unwrap_or_default(),
        ani.anims.len(),
        ani.steps_per_anim,
        played.len(),
        frames.first().copied().unwrap_or(-1),
        frames.last().copied().unwrap_or(-1),
        frames.len(),
    );
}

/// `pxf ani <file.ani|file.ark> [--only member] [--json out.json]`
pub fn cmd_ani(args: &Args) -> Result<()> {
    let src = PathBuf::from(args.positional.first().context(
        "usage: pxf ani <file.ani|file.ark> [--only member] [--json out.json]",
    )?);
    let mut found: Vec<(String, Ani)> = Vec::new();
    if src.extension().map(|e| e.eq_ignore_ascii_case("ark")).unwrap_or(false) {
        for e in ark::list(&src)? {
            if !e.name.to_ascii_lowercase().ends_with(".ani") || args.opt("only").map(|o| o != e.name).unwrap_or(false) {
                continue;
            }
            found.push((e.name.clone(), Ani::parse(&ark::read(&src, &e.name)?).with_context(|| e.name.clone())?));
        }
    } else {
        found.push((src.display().to_string(), Ani::parse(&std::fs::read(&src)?)?));
    }
    for (label, ani) in &found {
        summary(label, ani);
    }
    if let Some(out) = args.opt("json") {
        anyhow::ensure!(found.len() == 1, "--json needs exactly one .ani (use --only), found {}", found.len());
        std::fs::write(out, serde_json::to_vec_pretty(&found[0].1)?)?;
        println!("wrote {out}");
    }
    Ok(())
}

/// `pxf ani-build <anim.json> <out.ani> [--keep-header]` — the header is
/// re-sealed with the output file name, as `export` does for sheets.
pub fn cmd_ani_build(args: &Args) -> Result<()> {
    let (json, out) = crate::import::two_paths(args, "pxf ani-build <anim.json> <out.ani>")?;
    let mut ani: Ani = serde_json::from_slice(&std::fs::read(&json).with_context(|| json.display().to_string())?)?;
    let name = Path::new(&out).file_name().and_then(|n| n.to_str()).context("output has no file name")?;
    if !args.has("keep-header") && ani.name().as_deref() != Some(name) {
        println!("header renamed: {} -> {name}", ani.name().unwrap_or_default());
        ani.set_name(name);
    }
    std::fs::write(&out, ani.to_bytes()?)?;
    summary(&out.display().to_string(), &ani);
    Ok(())
}
