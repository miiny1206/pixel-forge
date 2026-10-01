//! pixel-forge — turn game sprite sheets into editable PNGs, and turn generated
//! art back into something the game will actually accept.
//!
//! Two jobs, kept apart on purpose:
//!
//!   snap     an off-grid render (a diffusion model's 512px "pixel art") back
//!            onto its implicit grid. Wraps spritefusion-pixel-snapper.
//!   conform  art that is already on-grid onto a *specific game's* palette,
//!            canvas and colour key. Our own code.
//!
//! Running `snap` on art that is already on-grid wrecks it — measured: a real
//! 68x76 sprite came back as 14x17 because the detector read a 5.5px grid in
//! the art's own shading. Hence the size guard in `cmd_snap`.

mod ani;
mod compose;
mod conform;
mod downscale;
mod import;
mod mask;
mod manifest;
mod palette;
mod recolor;
mod sheet;
mod snap;

use anyhow::{bail, Result};

fn usage() -> &'static str {
    "\
pxf — pixel-forge

USAGE
  pxf info    <file.spr|file.ark>
  pxf import  <file.spr|file.ark> <outdir> [--colorkey auto|cyan|black|none|0xHHHH]
  pxf export  <dir> <out.spr> [--keep-header]      rebuild from dir/manifest.json;
              the header is re-sealed with the output file name
  pxf pack    <dir> <out.ark> [--store]
  pxf ani     <file.ani|file.ark> [--only member] [--json out.json]
  pxf ani-build <anim.json> <out.ani> [--keep-header]
  pxf palette <ref.png|refdir> ...                 print the union palette as hex
  pxf conform <in.png|indir> <outdir> (--ref P... | --palette HEX,...)
              [--canvas WxH] [--colorkey cyan|black|none|0xHHHH] [--key RRGGBB]
              [--alpha-threshold N] [--report out.json]
  pxf sheet   <indir> <out.png> [--scale N] [--gap N] [--rows N] [--bg RRGGBB]
              [--canvas WxH]
  pxf unsheet <sheet.png> <outdir> [--manifest sheet.json]
  pxf downscale <sheet.png> <outdir> --manifest sheet.json (--ref P... | --palette H)
              [--bg RRGGBB] [--colorkey ...] [--despeckle] [--report out.json]
              [--confidence DIR] [--weak F] [--tol N] [--resolve] [--grain] [--register]
              [--metric rgb|redmean]
              --confidence: overlay of barely-decided pixels
              --resolve: re-decide weak pixels no neighbour agrees with
              --grain: move near-shade orphans onto a shade their block voted for
              --register: fit scale and offset per frame (generators drift 1-3%)
              --metric redmean: match colours perceptually (skin vs gold trim)
  pxf mask    <frame.png|dir> <outdir> --spec spec.json [--overlay dir]
  pxf remix   <editeddir> <outdir> --orig <dir> --mask <dir> [--alpha] [--report out.json]
  pxf recolor <framedir> <outdir> --spec spec.json [--overlay dir] [--report out.json]
  pxf lift    <out.png> --over DIR --under DIR --control DIR --rect x,y,w,h
              [--clip x,y,w,h] [--min-alpha F] [--passes N]
                                                   overlay (badge + glow) as RGBA
  pxf compose <outdir> --spec spec.json            fill / stamp / overlay on every
                                                   frame of a base sheet
  pxf snap    <in.png|indir> <outdir> [--colors N] [--palette HEX,... | --ref P]
              [--pixel-size N] [--flatten RRGGBB] [--force]
  pxf verify-roundtrip <dir>                       every .spr/.ani under dir, byte-identical?

NOTES
  --colorkey defaults to cyan (0x07FF), the key every character sprite uses.
  conform never quantises an opaque pixel onto the colour key; that would punch
  a hole in the sprite in game.
  sheet/unsheet exist because frames edited together on one sheet keep one
  outfit, while frames edited one at a time drift apart.
  recolor paints where the generator would not: it remaps a hand-authored
  region onto a ramp sampled from the garment, keeping each pixel's rank in
  the region's luminance order, so the body's own shading survives the change.
  downscale is the preferred way back from a generated sheet: the grid came
  from `pxf sheet`, so it is known, not detected, and each source pixel is a
  weighted vote over its whole block. Use `snap` only when the grid is unknown.
  --key is the input side: a background colour to read back as transparency,
  the counterpart to `snap --flatten`.
"
}

pub struct Args {
    pub positional: Vec<String>,
    pub flags: Vec<(String, Option<String>)>,
}

impl Args {
    fn parse(argv: &[String]) -> Args {
        let mut positional = Vec::new();
        let mut flags = Vec::new();
        let mut i = 0;
        while i < argv.len() {
            let a = &argv[i];
            if let Some(name) = a.strip_prefix("--") {
                let next = argv.get(i + 1);
                let takes_value = matches!(
                    name,
                    "colorkey" | "canvas" | "alpha-threshold" | "report" | "colors" | "palette"
                        | "pixel-size" | "ref" | "only" | "flatten" | "key" | "scale" | "gap"
                        | "bg" | "manifest" | "rows" | "spec" | "overlay" | "mask" | "orig"
                        | "over" | "under" | "control" | "rect" | "clip" | "min-alpha"
                        | "passes" | "json" | "confidence" | "weak" | "tol" | "metric"
                );
                if takes_value && next.map(|n| !n.starts_with("--")).unwrap_or(false) {
                    flags.push((name.to_string(), Some(next.unwrap().clone())));
                    i += 2;
                    continue;
                }
                flags.push((name.to_string(), None));
            } else {
                positional.push(a.clone());
            }
            i += 1;
        }
        Args { positional, flags }
    }

    pub fn opt(&self, name: &str) -> Option<&str> {
        self.flags.iter().rev().find(|(k, _)| k == name).and_then(|(_, v)| v.as_deref())
    }

    /// Every occurrence, so `--ref a --ref b` accumulates.
    pub fn opts(&self, name: &str) -> Vec<&str> {
        self.flags.iter().filter(|(k, _)| k == name).filter_map(|(_, v)| v.as_deref()).collect()
    }

    pub fn has(&self, name: &str) -> bool {
        self.flags.iter().any(|(k, _)| k == name)
    }
}

/// `auto` | `none` | `cyan` | `black` | `0x07FF`. Defaults to cyan: every
/// character and effect sprite in the shipped data keys against it, and a wrong
/// guess is worse than a wrong-but-stated default.
pub fn parse_colorkey(s: Option<&str>) -> Result<ColorKey> {
    Ok(match s.unwrap_or("cyan") {
        "auto" => ColorKey::Auto,
        "none" => ColorKey::None,
        "cyan" => ColorKey::Fixed(sprite_formats::KEY_CYAN),
        "black" => ColorKey::Fixed(sprite_formats::KEY_BLACK),
        v => {
            let n = if let Some(h) = v.strip_prefix("0x").or_else(|| v.strip_prefix("0X")) {
                u16::from_str_radix(h, 16)?
            } else {
                v.parse::<u16>()?
            };
            ColorKey::Fixed(n)
        }
    })
}

#[derive(Clone, Copy)]
pub enum ColorKey {
    Auto,
    None,
    Fixed(u16),
}

impl ColorKey {
    pub fn label(&self) -> String {
        match self {
            ColorKey::Auto => "auto".into(),
            ColorKey::None => "none".into(),
            ColorKey::Fixed(v) => format!("0x{v:04X}"),
        }
    }
    pub fn resolve(&self, frame: &sprite_formats::Frame) -> Option<u16> {
        match self {
            ColorKey::Auto => frame.guess_colorkey(),
            ColorKey::None => None,
            ColorKey::Fixed(v) => Some(*v),
        }
    }
    pub fn fixed(&self) -> Option<u16> {
        match self {
            ColorKey::Fixed(v) => Some(*v),
            _ => None,
        }
    }
}

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    if argv.is_empty() || argv[0] == "-h" || argv[0] == "--help" {
        print!("{}", usage());
        return;
    }
    let cmd = argv[0].clone();
    let args = Args::parse(&argv[1..]);
    let r = match cmd.as_str() {
        "info" => import::cmd_info(&args),
        "import" => import::cmd_import(&args),
        "export" => import::cmd_export(&args),
        "pack" => import::cmd_pack(&args),
        "verify-roundtrip" => import::cmd_verify_roundtrip(&args),
        "palette" => cmd_palette(&args),
        "conform" => conform::cmd_conform(&args),
        "downscale" => downscale::cmd_downscale(&args),
        "mask" => mask::cmd_mask(&args),
        "remix" => mask::cmd_remix(&args),
        "recolor" => recolor::cmd_recolor(&args),
        "sheet" => sheet::cmd_sheet(&args),
        "unsheet" => sheet::cmd_unsheet(&args),
        "snap" => snap::cmd_snap(&args),
        "lift" => compose::cmd_lift(&args),
        "ani" => ani::cmd_ani(&args),
        "ani-build" => ani::cmd_ani_build(&args),
        "compose" => compose::cmd_compose(&args),
        other => Err(anyhow::anyhow!("unknown command {other:?}\n\n{}", usage())),
    };
    if let Err(e) = r {
        eprintln!("error: {e:#}");
        std::process::exit(1);
    }
}

fn cmd_palette(args: &Args) -> Result<()> {
    if args.positional.is_empty() {
        bail!("usage: pxf palette <ref.png|refdir> ...");
    }
    let refs: Vec<_> = args.positional.iter().map(std::path::PathBuf::from).collect();
    let pal = palette::Palette::from_refs(&refs, 128)?;
    eprintln!("{} colours", pal.colors.len());
    println!("{}", pal.to_hex());
    Ok(())
}
