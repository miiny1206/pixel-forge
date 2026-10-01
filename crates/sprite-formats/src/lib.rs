//! Readers and writers for the the target game's asset formats.
//!
//! Ported from the Python tools in `mini-fighter-dev/mf/tools`, which were
//! themselves reverse engineered from the shipped client data. The port is
//! considered correct only when `pxf verify-roundtrip` reports every `.spr`
//! in the client tree rebuilding byte-identically.

pub mod ani;
pub mod ark;
pub mod spr;
pub mod tea;

pub use ani::Ani;
pub use spr::{Frame, Spr, KEY_BLACK, KEY_CYAN};
