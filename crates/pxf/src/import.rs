//! Module 1 — get the game's own art out, and put it back unchanged.

use crate::manifest::{hex, unhex, FrameEntry, Manifest};
use crate::{parse_colorkey, Args};
use anyhow::{bail, Context, Result};
use sprite_formats::{ark, Spr};
use std::path::{Path, PathBuf};

pub fn cmd_info(args: &Args) -> Result<()> {
    let path = PathBuf::from(args.positional.first().context("usage: pxf info <file>")?);
    if is_ark(&path) {
        let entries = ark::list(&path)?;
        let total: u64 = entries.iter().map(|e| e.size).sum();
        for e in &entries {
            println!("  {:<48} {:>10}  {}", e.name, e.size, if e.compressed { "deflate" } else { "stored" });
        }
        println!("\n{} entries, {} bytes uncompressed", entries.len(), total);
        return Ok(());
    }
    let spr = Spr::load(&path)?;
    println!("header  : {}", hex(&spr.header));
    println!("name    : {}", spr.name().as_deref().unwrap_or("(header does not decrypt to a name)"));
    println!("frames  : {}", spr.frames.len());
    let mut sizes: Vec<(i32, i32, usize)> = Vec::new();
    for f in &spr.frames {
        match sizes.iter_mut().find(|(w, h, _)| *w == f.width && *h == f.height) {
            Some((_, _, n)) => *n += 1,
            None => sizes.push((f.width, f.height, 1)),
        }
    }
    for (w, h, n) in sizes {
        println!("  {w:>6} x {h:<6} x{n}");
    }
    for (i, f) in spr.frames.iter().enumerate().take(4) {
        match f.guess_colorkey() {
            Some(k) => println!("  frame {i} colourkey guess: 0x{k:04X}"),
            None => println!("  frame {i} colourkey guess: none"),
        }
    }
    Ok(())
}

pub fn cmd_import(args: &Args) -> Result<()> {
    let (src, outdir) = two_paths(args, "pxf import <file.spr|file.ark> <outdir>")?;
    let key = parse_colorkey(args.opt("colorkey"))?;
    std::fs::create_dir_all(&outdir)?;

    if is_ark(&src) {
        let only = args.opt("only");
        let raw = outdir.join("_raw");
        std::fs::create_dir_all(&raw)?;
        let mut done = 0;
        let mut sheets = Vec::new();
        let mut anis = Vec::new();
        for entry in ark::list(&src)? {
            if only.map(|o| o != entry.name).unwrap_or(false) {
                continue;
            }
            let bytes = ark::read(&src, &entry.name)?;
            let stem = Path::new(&entry.name).file_stem().unwrap_or_default().to_string_lossy().to_string();
            if entry.name.to_ascii_lowercase().ends_with(".spr") {
                match Spr::parse(&bytes) {
                    Ok(spr) => {
                        let dir = outdir.join(&stem);
                        let label = format!("{}/{}", src.display(), entry.name);
                        import_spr(&spr, &dir, &label, key)?;
                        sheets.push((stem.to_ascii_lowercase(), dir));
                        done += 1;
                        continue;
                    }
                    Err(e) => eprintln!("note: {} is not a parseable .spr ({e}), kept raw", entry.name),
                }
            }
            // Everything else rides along untouched. An .ani is also decoded
            // into its sheet's directory below, but the raw copy stays the
            // one that gets packed back.
            if entry.name.to_ascii_lowercase().ends_with(".ani") {
                anis.push((stem.to_ascii_lowercase(), entry.name.clone(), bytes.clone()));
            }
            std::fs::write(raw.join(Path::new(&entry.name).file_name().unwrap()), &bytes)?;
        }
        println!("imported {done} sprite(s) from {}", src.display());
        for (stem, name, bytes) in anis {
            let Some((_, dir)) = sheets.iter().find(|(s, _)| *s == stem) else { continue };
            match sprite_formats::Ani::parse(&bytes) {
                Ok(ani) => attach_ani(&ani, dir, &name)?,
                Err(e) => eprintln!("note: {name} did not parse as .ani ({e})"),
            }
        }
        return Ok(());
    }

    let spr = Spr::load(&src)?;
    import_spr(&spr, &outdir, &src.display().to_string(), key)?;
    Ok(())
}

/// Write `anim.json` next to the frames and record each frame's anchors in
/// the manifest.
fn attach_ani(ani: &sprite_formats::Ani, dir: &Path, name: &str) -> Result<()> {
    let mp = dir.join("manifest.json");
    let mut m: Manifest = serde_json::from_slice(&std::fs::read(&mp)?)?;
    let mut used = 0;
    for (_, _, s) in ani.played() {
        if let Some(fe) = m.frames.iter_mut().find(|f| f.index as i32 == s.frame) {
            if !fe.anchors.contains(&[s.x, s.y]) {
                fe.anchors.push([s.x, s.y]);
            }
            used += 1;
        }
    }
    std::fs::write(&mp, serde_json::to_vec_pretty(&m)?)?;
    std::fs::write(dir.join("anim.json"), serde_json::to_vec_pretty(ani)?)?;
    let anchored = m.frames.iter().filter(|f| !f.anchors.is_empty()).count();
    println!(
        "  {name}: {} animation(s) x {} steps, {used} played step(s), {anchored}/{} frames anchored",
        ani.anims.len(), ani.steps_per_anim, m.frames.len()
    );
    Ok(())
}

fn import_spr(spr: &Spr, outdir: &Path, source: &str, key: crate::ColorKey) -> Result<()> {
    std::fs::create_dir_all(outdir)?;
    let mut frames = Vec::with_capacity(spr.frames.len());
    let mut written = 0;
    for (i, f) in spr.frames.iter().enumerate() {
        let guessed = f.guess_colorkey().map(|k| format!("0x{k:04X}"));
        if f.is_placeholder() {
            frames.push(FrameEntry {
                index: i,
                file: None,
                width: f.width,
                height: f.height,
                placeholder: true,
                guessed_key: guessed,
                anchors: Vec::new(),
            });
            continue;
        }
        let name = format!("frame_{i:03}.png");
        f.to_rgba(key.resolve(f))?.save(outdir.join(&name))?;
        written += 1;
        frames.push(FrameEntry {
            index: i,
            file: Some(name),
            width: f.width,
            height: f.height,
            placeholder: false,
            guessed_key: guessed,
            anchors: Vec::new(),
        });
    }
    let m = Manifest {
        source: source.to_string(),
        header_hex: hex(&spr.header),
        colorkey: key.label(),
        frames,
    };
    std::fs::write(outdir.join("manifest.json"), serde_json::to_vec_pretty(&m)?)?;
    println!("{written} frame(s) -> {}", outdir.display());
    Ok(())
}

pub fn cmd_export(args: &Args) -> Result<()> {
    let (dir, out) = two_paths(args, "pxf export <dir> <out.spr>")?;
    let m: Manifest = serde_json::from_slice(&std::fs::read(dir.join("manifest.json")).context(
        "no manifest.json in that directory — export rebuilds from the manifest an import wrote",
    )?)?;
    let key = parse_colorkey(Some(&m.colorkey))?;
    let mut frames = Vec::with_capacity(m.frames.len());
    for fe in &m.frames {
        if fe.placeholder {
            // The format still stores one pixel for these. Reproduce it as the
            // key so the rebuild is faithful when nothing edited it.
            frames.push(sprite_formats::Frame {
                width: fe.width,
                height: fe.height,
                pixels: vec![key.fixed().unwrap_or(0)],
            });
            continue;
        }
        let file = fe.file.as_ref().context("non-placeholder frame without a file")?;
        let img = image::open(dir.join(file))?.to_rgba8();
        if img.width() as i32 != fe.width || img.height() as i32 != fe.height {
            bail!(
                "{file} is {}x{} but the manifest says {}x{} — run `pxf conform --canvas {}x{}` first",
                img.width(), img.height(), fe.width, fe.height, fe.width, fe.height
            );
        }
        frames.push(sprite_formats::Frame::from_rgba(&img, key.fixed()));
    }
    let mut spr = Spr { header: unhex(&m.header_hex)?, frames };
    // The header seals the file's own name and the client checks it, so a
    // sheet written under a new name has to carry that name. Byte-identical
    // when the name is unchanged, so round trips are unaffected.
    let file_name = out
        .file_name()
        .and_then(|n| n.to_str())
        .context("output path has no file name")?
        .to_string();
    let before = spr.name();
    if !args.has("keep-header") && before.as_deref() != Some(file_name.as_str()) {
        spr.set_name(&file_name);
        println!(
            "header renamed: {} -> {file_name}",
            before.as_deref().unwrap_or("(no readable name)")
        );
    }
    spr.save(&out)?;
    println!("wrote {} ({} frames)", out.display(), m.frames.len());
    Ok(())
}

pub fn cmd_pack(args: &Args) -> Result<()> {
    let (dir, out) = two_paths(args, "pxf pack <dir> <out.ark>")?;
    let n = ark::pack(&dir, &out, args.has("store"))?;
    println!("packed {n} file(s) -> {}", out.display());
    Ok(())
}

/// The only proof the Rust port of the format is right: load every shipped
/// `.spr` and write it back, byte for byte.
pub fn cmd_verify_roundtrip(args: &Args) -> Result<()> {
    let root = PathBuf::from(args.positional.first().context("usage: pxf verify-roundtrip <dir>")?);
    let mut ok = 0;
    let mut skipped = 0;
    let mut bad = Vec::new();
    let mut ani_ok = 0;
    for path in walk(&root)? {
        let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("").to_ascii_lowercase();
        if ext == "ani" {
            check_ani(&std::fs::read(&path)?, &path, &mut ani_ok, &mut bad);
            continue;
        }
        if ext != "spr" {
            continue;
        }
        let data = std::fs::read(&path)?;
        check(&data, &path, &mut ok, &mut skipped, &mut bad);
    }
    // The loose .spr on disk are the minority; most of the art lives inside the
    // archives, so verify those members too.
    for path in walk(&root)? {
        if !is_ark(&path) {
            continue;
        }
        let Ok(entries) = ark::list(&path) else { continue };
        for e in entries {
            if e.name.to_ascii_lowercase().ends_with(".ani") {
                let data = ark::read(&path, &e.name)?;
                check_ani(&data, &path.join(&e.name), &mut ani_ok, &mut bad);
                continue;
            }
            if !e.name.to_ascii_lowercase().ends_with(".spr") {
                continue;
            }
            let data = ark::read(&path, &e.name)?;
            let label = path.join(&e.name);
            check(&data, &label, &mut ok, &mut skipped, &mut bad);
        }
    }
    println!("{ok} .spr + {ani_ok} .ani byte-identical, {} differing, {skipped} not parseable as .spr", bad.len());
    for p in bad.iter().take(20) {
        println!("  DIFF {}", p.display());
    }
    if !bad.is_empty() {
        bail!("{} file(s) did not round-trip", bad.len());
    }
    Ok(())
}


fn check(data: &[u8], label: &Path, ok: &mut usize, skipped: &mut usize, bad: &mut Vec<PathBuf>) {
    match Spr::parse(data) {
        Ok(spr) if spr.to_bytes() == data => {
            *ok += 1;
            // The client compares the sealed name with the file it opened, so
            // a sheet whose header names another file is broken in game even
            // though it round-trips perfectly. Say so.
            let want = label.file_name().and_then(|n| n.to_str()).unwrap_or("");
            match spr.name() {
                Some(n) if n.eq_ignore_ascii_case(want) => {}
                Some(n) => println!("  NAME {} carries \"{n}\"", label.display()),
                None => println!("  NAME {} has no readable name in its header", label.display()),
            }
        }
        Ok(_) => bad.push(label.to_path_buf()),
        Err(_) => *skipped += 1,
    }
}

fn check_ani(data: &[u8], label: &Path, ok: &mut usize, bad: &mut Vec<PathBuf>) {
    match sprite_formats::Ani::parse(data) {
        Ok(a) if a.to_bytes().ok().as_deref() == Some(data) => *ok += 1,
        _ => bad.push(label.to_path_buf()),
    }
}

fn walk(root: &Path) -> Result<Vec<PathBuf>> {
    let mut out = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        for e in std::fs::read_dir(&dir).with_context(|| format!("reading {}", dir.display()))? {
            let p = e?.path();
            if p.is_dir() {
                stack.push(p);
            } else {
                out.push(p);
            }
        }
    }
    out.sort();
    Ok(out)
}

fn is_ark(p: &Path) -> bool {
    p.extension().map(|e| e.eq_ignore_ascii_case("ark")).unwrap_or(false)
}

pub fn two_paths(args: &Args, usage: &str) -> Result<(PathBuf, PathBuf)> {
    if args.positional.len() < 2 {
        bail!("usage: {usage}");
    }
    Ok((PathBuf::from(&args.positional[0]), PathBuf::from(&args.positional[1])))
}
