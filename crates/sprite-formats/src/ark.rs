//! `.ark` — the asset archive.
//!
//! Ordinary ZIP archives (deflate, no password) holding `.spr` sprites, `.ani`
//! animation scripts and friends. Any ZIP tool opens them; this module exists
//! so callers do not have to rename files, and so a repack reproduces the
//! layout the client expects.

use anyhow::{Context, Result};
use std::io::{Read, Write};
use std::path::Path;

pub struct Entry {
    pub name: String,
    pub size: u64,
    pub compressed: bool,
}

pub fn list(path: impl AsRef<Path>) -> Result<Vec<Entry>> {
    let path = path.as_ref();
    let file = std::fs::File::open(path).with_context(|| format!("opening {}", path.display()))?;
    let mut zip = zip::ZipArchive::new(file).with_context(|| format!("reading {}", path.display()))?;
    let mut out = Vec::with_capacity(zip.len());
    for i in 0..zip.len() {
        let e = zip.by_index(i)?;
        out.push(Entry {
            name: e.name().to_string(),
            size: e.size(),
            compressed: e.compression() != zip::CompressionMethod::Stored,
        });
    }
    Ok(out)
}

/// Read one member by name.
pub fn read(path: impl AsRef<Path>, name: &str) -> Result<Vec<u8>> {
    let path = path.as_ref();
    let file = std::fs::File::open(path).with_context(|| format!("opening {}", path.display()))?;
    let mut zip = zip::ZipArchive::new(file)?;
    let mut e = zip
        .by_name(name)
        .with_context(|| format!("{} has no member {name}", path.display()))?;
    let mut buf = Vec::with_capacity(e.size() as usize);
    e.read_to_end(&mut buf)?;
    Ok(buf)
}

pub fn unpack(path: impl AsRef<Path>, outdir: impl AsRef<Path>) -> Result<Vec<String>> {
    let path = path.as_ref();
    let outdir = outdir.as_ref();
    std::fs::create_dir_all(outdir)?;
    let file = std::fs::File::open(path).with_context(|| format!("opening {}", path.display()))?;
    let mut zip = zip::ZipArchive::new(file)?;
    let mut names = Vec::with_capacity(zip.len());
    for i in 0..zip.len() {
        let mut e = zip.by_index(i)?;
        let name = e.name().to_string();
        // Members are flat in every shipped archive; refuse anything that tries
        // to escape the output directory rather than trusting that.
        let safe = Path::new(&name)
            .file_name()
            .with_context(|| format!("member {name} has no file name"))?;
        let mut buf = Vec::with_capacity(e.size() as usize);
        e.read_to_end(&mut buf)?;
        std::fs::write(outdir.join(safe), buf)?;
        names.push(name);
    }
    Ok(names)
}

pub fn pack(dir: impl AsRef<Path>, out: impl AsRef<Path>, store: bool) -> Result<usize> {
    let dir = dir.as_ref();
    let out = out.as_ref();
    let file = std::fs::File::create(out).with_context(|| format!("creating {}", out.display()))?;
    let mut zip = zip::ZipWriter::new(file);
    let method = if store {
        zip::CompressionMethod::Stored
    } else {
        zip::CompressionMethod::Deflated
    };
    let opts: zip::write::FileOptions<()> = zip::write::FileOptions::default().compression_method(method);
    let mut entries: Vec<_> = std::fs::read_dir(dir)?
        .filter_map(|e| e.ok())
        .filter(|e| e.path().is_file())
        .map(|e| e.path())
        .collect();
    entries.sort();
    for p in &entries {
        let name = p.file_name().unwrap().to_string_lossy().to_string();
        zip.start_file(name, opts)?;
        zip.write_all(&std::fs::read(p)?)?;
    }
    zip.finish()?;
    Ok(entries.len())
}
