//! `.ani` — the animation script that says which `.spr` frame is drawn where.
//!
//! ```text
//! [64]      header: the file's own name, TEA-sealed (see `tea`)
//! i32       magic 1909
//! i32       A, number of animations
//! i32[A*F]  frame index per step (-1 = empty step)
//! i32[A]    last used step of each animation (steps played = last + 1)
//! i32[3*A*F] per step x, y, z  — x/y are the draw offset of the frame
//! i32[A*F]  per step flag (3 in ~97% of steps; meaning not pinned down)
//! ```
//!
//! `F`, the steps reserved per animation, is not stored: it is whatever makes
//! the sizes add up (21, 30, 40, 50, ... in the shipped data). All 1376 `.ani`
//! in the client parse under this layout and rebuild byte-identically.

use anyhow::{bail, Result};
use serde::{Deserialize, Serialize};

pub const MAGIC: i32 = 1909;

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Step {
    pub frame: i32,
    pub x: i32,
    pub y: i32,
    pub z: i32,
    pub flag: i32,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Anim {
    pub last: i32,
    /// Always `F` long, including the unused tail, so a rebuild is exact.
    pub steps: Vec<Step>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Ani {
    #[serde(with = "hexbytes")]
    pub header: Vec<u8>,
    pub steps_per_anim: usize,
    pub anims: Vec<Anim>,
}

impl Ani {
    pub fn parse(data: &[u8]) -> Result<Ani> {
        if data.len() < 72 || (data.len() - 64) % 4 != 0 {
            bail!("not an .ani: size {}", data.len());
        }
        let iv: Vec<i32> = data[64..].chunks_exact(4).map(|c| i32::from_le_bytes(c.try_into().unwrap())).collect();
        if iv[0] != MAGIC {
            bail!("not an .ani: magic {} (want {MAGIC})", iv[0]);
        }
        let a = iv[1];
        let rest = iv.len() as i64 - 2 - a as i64;
        if a <= 0 || rest <= 0 || rest % (5 * a as i64) != 0 {
            bail!("not an .ani: {} animations do not divide {} values", a, iv.len());
        }
        let (a, f) = (a as usize, (rest / (5 * a as i64)) as usize);
        let fr = &iv[2..2 + a * f];
        let last = &iv[2 + a * f..2 + a * f + a];
        let off = &iv[2 + a * f + a..2 + a * f + a + 3 * a * f];
        let flag = &iv[2 + a * f + a + 3 * a * f..];
        let anims = (0..a)
            .map(|i| Anim {
                last: last[i],
                steps: (0..f)
                    .map(|k| {
                        let s = i * f + k;
                        Step { frame: fr[s], x: off[3 * s], y: off[3 * s + 1], z: off[3 * s + 2], flag: flag[s] }
                    })
                    .collect(),
            })
            .collect();
        Ok(Ani { header: data[..64].to_vec(), steps_per_anim: f, anims })
    }

    pub fn to_bytes(&self) -> Result<Vec<u8>> {
        let f = self.steps_per_anim;
        if self.header.len() != 64 {
            bail!("header must be 64 bytes");
        }
        if let Some(i) = self.anims.iter().position(|a| a.steps.len() != f) {
            bail!("animation {i} has {} steps, the file reserves {f}", self.anims[i].steps.len());
        }
        let all = || self.anims.iter().flat_map(|a| a.steps.iter());
        let mut iv = vec![MAGIC, self.anims.len() as i32];
        iv.extend(all().map(|s| s.frame));
        iv.extend(self.anims.iter().map(|a| a.last));
        iv.extend(all().flat_map(|s| [s.x, s.y, s.z]));
        iv.extend(all().map(|s| s.flag));
        let mut out = self.header.clone();
        out.extend(iv.iter().flat_map(|v| v.to_le_bytes()));
        Ok(out)
    }

    pub fn name(&self) -> Option<String> {
        crate::tea::header_name(&self.header)
    }

    pub fn set_name(&mut self, name: &str) {
        self.header = crate::tea::rename_header(&self.header, name);
    }

    /// Steps that are actually played: `0..=last` of each animation, frame >= 0.
    pub fn played(&self) -> impl Iterator<Item = (usize, usize, &Step)> {
        self.anims.iter().enumerate().flat_map(|(ai, a)| {
            let n = (a.last + 1).clamp(0, a.steps.len() as i32) as usize;
            a.steps[..n].iter().enumerate().filter(|(_, s)| s.frame >= 0).map(move |(k, s)| (ai, k, s))
        })
    }
}

mod hexbytes {
    use serde::{Deserialize, Deserializer, Serializer};
    pub fn serialize<S: Serializer>(b: &[u8], s: S) -> Result<S::Ok, S::Error> {
        s.serialize_str(&b.iter().map(|x| format!("{x:02x}")).collect::<String>())
    }
    pub fn deserialize<'de, D: Deserializer<'de>>(d: D) -> Result<Vec<u8>, D::Error> {
        let s = String::deserialize(d)?;
        (0..s.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(s.get(i..i + 2).unwrap_or("x"), 16).map_err(serde::de::Error::custom))
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip_synthetic() {
        let mut data = crate::tea::encrypt(&{
            let mut h = b"test.ani".to_vec();
            h.resize(64, 0);
            h
        });
        let (a, f) = (2usize, 3usize);
        let mut iv = vec![MAGIC, a as i32];
        iv.extend((0..a * f).map(|i| i as i32 - 1));
        iv.extend([1, 2]);
        iv.extend((0..3 * a * f).map(|i| i as i32 * 7 - 20));
        iv.extend(std::iter::repeat(3).take(a * f));
        data.extend(iv.iter().flat_map(|v| v.to_le_bytes()));
        let ani = Ani::parse(&data).unwrap();
        assert_eq!(ani.steps_per_anim, 3);
        assert_eq!(ani.name().as_deref(), Some("test.ani"));
        assert_eq!(ani.anims[1].steps[0].frame, 2);
        assert_eq!(ani.to_bytes().unwrap(), data);
        assert_eq!(ani.played().count(), 4); // anim 0 skips frame -1
    }
}
