# pixel-forge: the project pipeline

Everything beyond single sprites: redrawing whole animations of a game character frame by
frame, the measurements behind each step, and the `pxf` CLI. For drawing, animating and
editing one sprite, the [README](../README.md) is enough.

1. [Why not just ask the model?](#why-not-just-ask-the-model)
2. [Project configuration](#project-configuration-projectjson)
3. [Workflow](#workflow)
4. [The pipeline, step by step](#the-pipeline-step-by-step)
5. [`pxf` command reference](#pxf-command-reference)
6. [Game formats (optional)](#game-formats-optional)
7. [Case study: a beach costume](#case-study-a-beach-costume)
8. [Refusals and moderation](#refusals-and-moderation)
9. [LoRA / fine-tuning](#lora--fine-tuning)
10. [Limitations](#limitations)

---

## Why not just ask the model?

Because the model does not draw on your grid, does not keep your palette and does not
keep the pose. Measured on the same character frames:

| | grid fit ¹ | notes |
|---|---|---|
| original art | 100% | every edge on one grid phase |
| Qwen-Image-Edit (diffusion, whole sheet) | **32–37%** (chance) | edges spread evenly over all phases; lost shoulder armour, tassel, face detail; a different best offset per frame (+1,-1 / +3,-3 / +1,-1 / +4,0) — non-rigid drift, cannot be realigned |
| SDXL inpainting (masked) | — | seam better, the fill itself "smooth mush" with no pixel structure |
| GPT image-edit model, 4–6 frames per sheet | **76–87%** | keeps the grid but rescales it uniformly (always answered 1254×1254) |
| Gemini image model, one frame per request | **95–97%** | at scale 1.000, 120–350 weak pixels per frame |

¹ share of strong edges within 15% of a block boundary.

Other measured facts that shaped the design:

- Two generations of the same character that looked identical shared **zero** exact
  colours. A palette is a property of the whole set, not of a frame.
- Snapping an already on-grid sprite with a grid *detector* destroys it: a 68×76 sprite
  came back 14×17 (the detector read a 5.5 px grid in the shading).
- On a sheet built at exactly 3×, the detector still got the **scale** wrong
  (73×98 → 100×149). `pxf sheet` writes the grid, so `pxf downscale` *knows* it.
- Colour-derived clothing masks oscillate (the artist reuses the same darks for outline,
  stockings, skirt shadow and skin shadow); four attempts, four failure modes.
- Redrawing each frame independently made consecutive idle frames differ by 28–44% where
  the originals differ by 7–15% (breathing): the character shimmered. Consistency has to be
  enforced across the animation, not hoped for per frame.
- A frame's position on screen is set by its anchor, not by the image. A silhouette that
  moves one pixel makes the animation jitter.

So the model is used for what it is good at — drawing the change — and everything else is
done by tools whose output can be checked.


## Project configuration: `project.json`

Paths are relative to the project file. Minimal: [`examples/png-frames/project.json`](../examples/png-frames/project.json);
full: [`examples/beach-costume/project.json`](../examples/beach-costume/project.json).

| key | meaning |
|---|---|
| `work` | working directory (default `work`) |
| `frames` | **input**: a folder of PNG frames, one subfolder per animation (files in name order) |
| `export_to` | where `finish` / `export` writes the finished PNGs (default `work/out`) |
| `source_archive`, `sheet`, `ani`, `per` | alternative input: a game archive and the sheet / animation members in it (see [Game formats](#game-formats-optional)) |
| `target_archive` | the archive a custom bake step writes into |
| `scale`, `gap`, `bg`, `canvas` | how a frame is laid out for the model (5, 8, `808080`, 1024) |
| `refs` | extra palette references for `pxf downscale`, relative to `work` |
| `downscale_flags` | flags for `pxf downscale` (default `--tol 24 --resolve --grain --register --metric redmean`) |
| `prompt_file` | the edit prompt |
| `engine` | `chat` or `images` |
| `leftover_max`, `tries` | resend a frame whose original look survived above this % (default 28, 3 tries) |
| `mask_grow` | px the figure mask may grow over the original drawing |
| `hooks.crops` | `file.py:func(project) -> {frame: [x,y,w,h]}` — what to crop (default: opaque bbox) |
| `hooks.masks` | `file.py:func(outdir, frames, grow)` — the figure mask (default: every opaque pixel) |
| `hooks.after_remix` | list of `file.py:func(path, frame, run_dir)` run on each finished frame |
| `finish` | `after_smooth`, `keep_stock`, `always_ship`, `post`, `bake` — see [finish](#6-finish) |
| `hold`, `grow` | see [hold and grow](#7-hold-and-grow) |

The project is found from `--project FILE`, else `PXF_PROJECT`, else `./project.json`.


## Workflow

```sh
export PXF_PROJECT=examples/png-frames/project.json

python -m pxf_pipeline prepare                 # work/all, work/anim.json, work/crops.json
python -m pxf_pipeline batch run idle anim:0   # one animation first; look at it
python -m pxf_pipeline review 0                # work/review/anim_00.png: original vs result
python -m pxf_pipeline batch run rest all      # everything else (resumable; rerun to continue)
python -m pxf_pipeline batch status rest
python -m pxf_pipeline finish                  # smooth, gather, project fixes, export PNGs
```

Long runs across rate limits: `python -m pxf_pipeline autorun rest` (polls every 30 min,
resumes, finishes).

A frame that came back wrong: `batch requeue rest 41,42` and run again. After changing a
post-processing step: `batch redo rest` rebuilds every frame from the saved model output
without new requests.

Hand edits: `python -m pxf_pipeline asebridge to work/runs/x/bake x.aseprite --layers work/all`,
paint, then `python -m pxf_pipeline finish x` pulls `runs/x/aseprite/x.aseprite` back in.


## The pipeline, step by step

### 1. prepare
From a PNG folder (`frames`): every frame → `work/all/frame_NNN.png`, one animation per
subfolder → `work/anim.json` (frames anchored at their bottom centre), original names →
`work/frames.json`. From a game archive: `pxf import` + `pxf ani` instead. Either way the
crop boxes → `work/crops.json`.

### 2. batch (per frame)
1. **crop** the original frame to its box; **`pxf sheet`** lays it out at `scale` on a flat
   `bg` canvas (scale 5 measured best: at 3/4/5 a sheet gave 1286–1999 / 954–1126 / 305–653
   weak px per frame).
2. **model edit** through `backend.py` (`chat` or `images`).
3. **`pxf downscale`** back onto the grid: every source pixel is a weighted vote over its
   whole block; `--register` fits the model's 1–3% scale drift; `--resolve` re-decides weak
   pixels no neighbour agrees with (orphans 264–312 → 123–148, original art has 117–191);
   `--grain` folds two-shade grain (near-shade orphans 40% → 26%, original 8%);
   `--metric redmean` keeps skin from snapping to gold trim.
4. **place** the crop back into the full frame, build the **figure mask**, and
   **`pxf remix --alpha`**: inside the original figure the edit wins (transparency too — a
   skirt can go); outside, the original art stays. The silhouette can only shrink, never grow,
   so the animation offsets stay valid.
5. **`after_remix` hooks** (project-specific fixes).
6. **twins**: frames the game ships twice (same drawing, colours one RGB565 step off) get the
   representative's result with the colours mapped, so they cannot flicker.

`leftover.py` scores how much of the old outfit survived; above `leftover_max` the frame is
sent again (up to `tries`) and the best try is kept. State lives in
`work/runs/<name>/state.json`; a rerun continues.

### 3. smooth
Each animation becomes **one drawing**. Per-frame redraws made consecutive idle frames
differ by 28–44% where the original differs by 7–15% (breathing): the character shimmered.
Walking each animation from a key frame (the medoid), a pixel is carried over from the
previous finished frame wherever the **original** art did not change there (after the best
1–3 px shift) and the model's own pixel agrees; only where the artist really redrew is the
model's drawing used.

### 4. transplant / graft
Fill a frame from a finished frame of nearly the same pose — no request. `transplant`
takes the donor's pixel where the two original frames agree after alignment and reports the
rest. (The example's `graft.py` adds stamping a whole figure.)

### 5. review / leftover
`review <anims>` writes original-above-result sheets for QA; `leftover` prints the old-outfit
score per frame.

### 6. finish
Pulls Aseprite edits back, rebuilds `smooth`, runs `finish.after_smooth` steps, gathers
every run into `work/bake_all` (later runs win, `runs/graft` over them, `runs/smooth` over
all; frames still identical to the original are left out so the bake can rule-paint them), copies
`keep_stock` / `always_ship` frames, runs `finish.post` steps in order, `hold`, and finally
`finish.bake`. Each step is `[script.py, args...]` relative to the project, run with
`PXF_PROJECT` set; `{bake_all}` and `{work}` are substituted. Without a `bake` step, a PNG
project is exported: every frame back under its original name, redrawn or unchanged.

### 7. hold and grow
`hold` shows a finished neighbour in place of a frame no model would draw, placed by the
animation offsets so it stands where that frame stands. If the neighbour does not fit the
original canvas, `grow` pads that frame's canvas in **every** sheet sharing the animation file
and moves its steps by the padding (`x += left, z += top, y += right`) — the original art stays on the same
screen pixels (checked pixel by pixel in `tests/smoke_test.py`).


## `pxf` command reference

```
pxf info    <sheet|archive>
pxf import  <sheet|archive> <outdir> [--colorkey auto|cyan|black|none|0xHHHH] [--only member]
pxf export  <dir> <out-sheet> [--keep-header]    rebuild from dir/manifest.json;
            the header is re-sealed with the output file name
pxf pack    <dir> <out-archive> [--store]
pxf ani     <anim|archive> [--only member] [--json out.json]
pxf ani-build <anim.json> <out-anim> [--keep-header]
pxf palette <ref.png|refdir> ...                 print the union palette as hex
pxf conform <in.png|indir> <outdir> (--ref P... | --palette HEX,...)
            [--canvas WxH] [--colorkey cyan|black|none|0xHHHH] [--key RRGGBB]
            [--alpha-threshold N] [--report out.json]
pxf sheet   <indir> <out.png> [--scale N] [--gap N] [--rows N] [--bg RRGGBB] [--canvas WxH]
pxf unsheet <sheet.png> <outdir> [--manifest sheet.json]
pxf downscale <sheet.png> <outdir> --manifest sheet.json (--ref P... | --palette H)
            [--bg RRGGBB] [--colorkey ...] [--despeckle] [--report out.json]
            [--confidence DIR] [--weak F] [--tol N] [--resolve] [--grain] [--register]
            [--metric rgb|redmean]
pxf mask    <frame.png|dir> <outdir> --spec spec.json [--overlay dir]
pxf remix   <editeddir> <outdir> --orig <dir> --mask <dir> [--alpha] [--report out.json]
pxf recolor <framedir> <outdir> --spec spec.json [--overlay dir] [--report out.json]
pxf lift    <out.png> --over DIR --under DIR --control DIR --rect x,y,w,h
            [--clip x,y,w,h] [--min-alpha F] [--passes N]
pxf compose <outdir> --spec spec.json
pxf snap    <in.png|indir> <outdir> [--colors N] [--palette HEX,... | --ref P]
            [--pixel-size N] [--flatten RRGGBB] [--force]
pxf verify-roundtrip <dir>
```

| command | what it does, and why |
|---|---|
| `info` | frame count, sizes, sealed name, guessed colour key |
| `import` | native sheet → `frame_NNN.png` + `manifest.json`; from an archive, also decodes the animation file sharing the sheet's name into `anim.json` and records each frame's anchors. Other members are kept raw for `pack` |
| `export` | rebuild a native sheet from a directory; the header is re-sealed with the output name (the game rejects a sheet whose header names another file) |
| `pack` | directory → archive (zip, deflated unless `--store`) |
| `ani` / `ani-build` | animation file ↔ JSON (`{anims: [{last, steps: [{frame, x, y, z, flag}]}]}`) |
| `palette` | union palette of reference frames, as hex |
| `conform` | on-grid art → the game's palette, canvas and key; never quantises an opaque pixel onto the key (that would be a hole in game) |
| `sheet` / `unsheet` | frames ↔ one zoomed sheet with a recorded grid (`sheet.json`); `--rows` fits more frames on a square canvas |
| `downscale` | a model's sheet back to frames by **block vote on the known grid**. `--confidence` writes an overlay of weakly decided pixels; `--tol` merges near-identical shades in the vote; `--resolve` re-decides weak orphans; `--grain` folds two-shade grain; `--register` fits per-frame scale/offset (refused if it costs > 1 point of silhouette); `--metric redmean` perceptual colour match; `--despeckle` is opt-in (on a lossless round trip it cost 4–8 correct pixels per frame) |
| `mask` | polygon spec (`add`, `sub`, `sub_lines`) → binary masks |
| `remix` | edited frames composited into the originals through masks; `--alpha` lets the edit change transparency inside the mask; `--report` prints a seam error |
| `recolor` | remap a polygon region onto a colour ramp keeping each pixel's luminance rank (form shading survives); never touches alpha |
| `lift` | measure an overlay (badge, glow) as RGBA by regressing `over = k·under + m` across frames |
| `compose` | `fill` / `stamp` (+`repeat`) / `overlay` ops from JSON on every frame of a base sheet |
| `snap` | off-grid render with an **unknown** grid → pixels (wraps spritefusion-pixel-snapper); refuses inputs under 128 px unless `--force`; `--flatten auto` picks a backdrop far from the palette |
| `verify-roundtrip` | every sheet and animation file under a directory (and inside every archive) written back and compared byte for byte |

Measured: `verify-roundtrip` over a full game client — **1368 sheets + 1405 animation
files byte-identical**, 0 differing; import → PNG → export byte-identical; dir →
`.aseprite` → dir byte-identical on a 379-frame sheet.


## Game formats (optional)

PNG folders need none of this. For writing straight into a game, the readers and writers
live in `crates/sprite-formats` (Rust) and `python/pxf_pipeline/sheetio.py` (Python). In short: a sheet is a list of RGB565 frames with
a colour key instead of alpha and a sealed header; an animation file holds, per animation
step, a frame index and its offset from the character's anchor (and the mirrored offset);
an archive is a plain zip. To use the pipeline with another game, replace these two modules —
everything after `prepare` works on PNG frames and a JSON animation table.

Two rules carry over to any game: an opaque pixel must never land on the colour key (it
becomes a hole), and a frame's canvas and its animation offsets must change together (that
is what `grow` does).


## Case study: a beach costume

[`examples/beach-costume/`](../examples/beach-costume/) — a black bikini on one character,
379 frames, 50 animations (the image at the top). Everything that was specific to this
character lives there, wired in through `project.json`:

| step | why it exists |
|---|---|
| `bodymask.py` (+ `bkmask`, `legs`, `qretex_form`, …) | crop boxes and figure masks around **her**: face → body → stockings/boots joined to her → the skirt between; after-images and a summoned boss left out |
| `bossmask.py` | a finisher where the body finder grabs the boss |
| `hooks.py` | `masks`; `keep_effects` (slash arcs and dotted effects behind her come back); `boss_backfill`; `no_sash` |
| `cleanup.py`, `bounce.py` | specks and scraps where carried and drawn pixels meet; a chest bounce on the idle |
| `graft.py` | frames the model refused, filled from a redrawn frame of the same pose |
| `beachfx.py`, `foam.py` | effect colours in her sheet turned sea-blue, with foam |
| `swordfix.py` | models drew the sheathed sword as a glinting chain while she runs |
| `trailfix.py` | a stocking-coloured leg-swing trail in the get-up kept from the original art |
| `gauntlet.py` | one bracer through the dash instead of eight different drawings |
| `bkbake.py` + `bkpaint.py` | bake into a new sheet in the archive; frames no model drew get a rule-painted bikini |

Results: grid fit 95–97% per frame (chat engine, one frame per request); the baked sheet is
byte-identical between this repository's pipeline and the original scripts; the other sheets
sharing the animation file are pixel-identical on screen after `grow`. See the example's
README for the order of operations and the numbers behind each fix.


## Refusals and moderation

Hosted image models refuse some frames (in our runs: certain poses, at the input or the
output stage). The pipeline's policy is fixed:

- a refused frame is recorded as `refused` and is **never resent** — not with other wording,
  not with another crop, not through another engine to get around the refusal;
- it keeps the project's fallback (original art or a rule-painted version) until a person
  decides; `transplant`, `graft` and `hold` fill it **from frames that did come back**,
  without any request.

Do not use this tool to work around a provider's safety system.

---

## LoRA / fine-tuning

None is used. The models are hosted (OpenAI-compatible endpoints), which take no adapters;
style consistency comes from the deterministic steps above (grid vote, palette, masks,
smoothing). An open model (Qwen-Image-Edit, FLUX) with a LoRA trained on the game's own
sprites is the obvious next step for style fidelity, but in our tests the open model
scored 32–37% grid fit against 95–97% for the hosted one, and GPU time was mostly spent
downloading weights — so it is future work, not part of this pipeline.


## Limitations

- The edit stays inside the original silhouette (plus `mask_grow`): changing or removing
  things works, making the figure bigger needs a custom mask hook.
- New colours must be allowed explicitly (palette references); the snap never invents one.
- Built-in game-format support covers one game; for other engines use PNG folders or add
  a reader/writer.
- Masks and crops are the hard, subject-specific part. The defaults (every opaque pixel,
  opaque bounding box) are a starting point; a real costume change needs a figure finder
  like the example's.
- One request per frame costs time (≈20–60 s each) and quota; `autorun` waits out rate
  limits.
- The model still drifts between frames; `smooth` reduces it, review sheets catch the rest,
  and some details (bracers, a sword glint) needed a dedicated fix.

