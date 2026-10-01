//! The TEA variant that seals the first 64 bytes of `.spr` and `.ani` files.
//!
//! Those 64 bytes are **the file's own name**, NUL-terminated, followed by
//! whatever was on the packing tool's stack. The client decrypts them and
//! compares the name with the file it asked for; a mismatch is reported as
//! "This is not a sprite file". So a sheet cannot be copied to a new name by
//! copying bytes -- the header has to be re-sealed with the new name.
//!
//! Standard TEA, 32 rounds, little-endian words, key below. The Python side
//! (`python/pxf_pipeline/sheetio.py`) implements the same cipher.

const KEY: [u32; 4] = [0x78a3cbe3, 0x49d936a9, 0xb4c570ba, 0x186e7505];
const DELTA: u32 = 0x9E37_79B9;

fn words(b: &[u8], i: usize) -> (u32, u32) {
    (
        u32::from_le_bytes(b[i..i + 4].try_into().unwrap()),
        u32::from_le_bytes(b[i + 4..i + 8].try_into().unwrap()),
    )
}

pub fn encrypt(data: &[u8]) -> Vec<u8> {
    assert!(data.len() % 8 == 0, "TEA works on 8-byte blocks");
    let mut out = Vec::with_capacity(data.len());
    for i in (0..data.len()).step_by(8) {
        let (mut v0, mut v1) = words(data, i);
        let mut s = 0u32;
        for _ in 0..32 {
            s = s.wrapping_add(DELTA);
            v0 = v0.wrapping_add(
                (v1 << 4).wrapping_add(KEY[0]) ^ v1.wrapping_add(s) ^ (v1 >> 5).wrapping_add(KEY[1]),
            );
            v1 = v1.wrapping_add(
                (v0 << 4).wrapping_add(KEY[2]) ^ v0.wrapping_add(s) ^ (v0 >> 5).wrapping_add(KEY[3]),
            );
        }
        out.extend_from_slice(&v0.to_le_bytes());
        out.extend_from_slice(&v1.to_le_bytes());
    }
    out
}

pub fn decrypt(data: &[u8]) -> Vec<u8> {
    assert!(data.len() % 8 == 0, "TEA works on 8-byte blocks");
    let mut out = Vec::with_capacity(data.len());
    for i in (0..data.len()).step_by(8) {
        let (mut v0, mut v1) = words(data, i);
        let mut s = DELTA.wrapping_mul(32);
        for _ in 0..32 {
            v1 = v1.wrapping_sub(
                (v0 << 4).wrapping_add(KEY[2]) ^ v0.wrapping_add(s) ^ (v0 >> 5).wrapping_add(KEY[3]),
            );
            v0 = v0.wrapping_sub(
                (v1 << 4).wrapping_add(KEY[0]) ^ v1.wrapping_add(s) ^ (v1 >> 5).wrapping_add(KEY[1]),
            );
            s = s.wrapping_sub(DELTA);
        }
        out.extend_from_slice(&v0.to_le_bytes());
        out.extend_from_slice(&v1.to_le_bytes());
    }
    out
}

/// The name sealed in a 64-byte header, if it decrypts to one.
pub fn header_name(header: &[u8]) -> Option<String> {
    if header.len() != 64 {
        return None;
    }
    let plain = decrypt(header);
    let end = plain.iter().position(|&b| b == 0)?;
    let name = &plain[..end];
    if name.is_empty() || !name.iter().all(|&b| (0x20..0x7f).contains(&b)) {
        return None;
    }
    Some(String::from_utf8_lossy(name).into_owned())
}

/// `header` re-sealed to carry `name`. Keeps the bytes after the old name
/// exactly as they were, the way the Python `renamed` does, so renaming a file
/// back to its own name is byte-identical. A header that does not decrypt to
/// a name is replaced by a clean one.
pub fn rename_header(header: &[u8], name: &str) -> Vec<u8> {
    assert!(name.len() < 64, "a name must fit the 64-byte header with its NUL");
    let mut plain = if header.len() == 64 && header_name(header).is_some() {
        decrypt(header)
    } else {
        vec![0u8; 64]
    };
    let old_len = plain.iter().position(|&b| b == 0).unwrap_or(0);
    // Clear the old name and its NUL, then write the new one. Anything past
    // the longer of the two is left alone.
    for b in plain.iter_mut().take(old_len.max(name.len()) + 1) {
        *b = 0;
    }
    plain[..name.len()].copy_from_slice(name.as_bytes());
    encrypt(&plain)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trips() {
        let data: Vec<u8> = (0..64u8).collect();
        assert_eq!(decrypt(&encrypt(&data)), data);
    }

    #[test]
    fn key_matches_the_python_tools() {
        // bytes.fromhex("e3cba378a936d949ba70c5b405756e18") read as <4I.
        let raw = [0xe3, 0xcb, 0xa3, 0x78, 0xa9, 0x36, 0xd9, 0x49, 0xba, 0x70, 0xc5, 0xb4, 0x05,
                   0x75, 0x6e, 0x18];
        for i in 0..4 {
            assert_eq!(KEY[i], u32::from_le_bytes(raw[i * 4..i * 4 + 4].try_into().unwrap()));
        }
    }

    #[test]
    fn rename_is_reversible() {
        let orig = rename_header(&[0u8; 64], "LegendCard_Black.spr");
        assert_eq!(header_name(&orig).as_deref(), Some("LegendCard_Black.spr"));
        let z = rename_header(&orig, "ZetaCard_Black.spr");
        assert_eq!(header_name(&z).as_deref(), Some("ZetaCard_Black.spr"));
        assert_eq!(rename_header(&orig, "LegendCard_Black.spr"), orig);
    }
}
