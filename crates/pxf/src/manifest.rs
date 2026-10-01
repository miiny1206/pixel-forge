//! The sidecar that survives the round trip.
//!
//! Deliberately conservative: it records only what the formats actually state.
//! `anchors` comes straight from the sheet's `.ani` (same archive, same stem):
//! the draw offsets the animation script places that frame at. A frame the
//! script never plays has none — that is a fact about the data, not a gap.

use serde::{Deserialize, Serialize};

#[derive(Serialize, Deserialize)]
pub struct Manifest {
    /// Where the frames came from, for the human reading the directory later.
    pub source: String,
    /// The 64 opaque header bytes, hex. Needed for a byte-identical rebuild.
    pub header_hex: String,
    /// Colour key used when decoding, as written on the command line.
    pub colorkey: String,
    pub frames: Vec<FrameEntry>,
}

#[derive(Serialize, Deserialize)]
pub struct FrameEntry {
    pub index: usize,
    /// Absent for placeholder frames, which have no image.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub file: Option<String>,
    pub width: i32,
    pub height: i32,
    /// A -1 x -1 "nothing here" frame the engine skips.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub placeholder: bool,
    /// The key this frame's border voted for, when it voted at all.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub guessed_key: Option<String>,
    /// Every distinct (x, y) the `.ani` draws this frame at, in order of first
    /// use. Art may change size only if it keeps its pixels where these say.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub anchors: Vec<[i32; 2]>,
}

pub fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

pub fn unhex(s: &str) -> anyhow::Result<Vec<u8>> {
    if s.len() % 2 != 0 {
        anyhow::bail!("hex string has odd length");
    }
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).map_err(Into::into))
        .collect()
}
