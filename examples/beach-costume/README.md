# Example: a beach costume

A black bikini on one character's sheet: 379 frames, 50 animations, baked into a new sheet
in the same archive (plus a smaller town sheet). This is the project the pipeline was built
on; every script here is something a real character needed. No game files are included —
point `project.json` at your own copy. The member names in `project.json` are placeholders.

![stock (top) and result (bottom)](../../docs/compare.png)

## Setup

```
examples/beach-costume/
  project.json            the job (paths are relative to this directory)
  prompt.txt              the edit prompt, one frame per request
  game/pristine/<archive> ← your untouched copy of the character's archive (read only)
  game/<archive>          ← the archive to bake into (backed up once before the first bake)
  game/<town archive>     ← optional town archive ("bake.town")
```

```sh
cd examples/beach-costume
cp ../../.env.example .env            # endpoint + key + model names
export PXF_PROJECT=$PWD/project.json
export PYTHONPATH=$PWD/../../python

python -m pxf_pipeline prepare        # work/all, work/anim.json, work/crops.json (bodymask.crops)
python make_refs.py                   # work/src (palette frames) + work/skin_ref.png
python bossmask.py apply              # her own masks/boxes in the boss finisher
python -m pxf_pipeline batch run idle anim:0
python -m pxf_pipeline review 0
python -m pxf_pipeline batch run rest all
python -m pxf_pipeline finish
```

## What runs, in order

**Per frame** (`batch`): crop around her figure (`bodymask.crops`) → sheet at scale 5 on
`#808080` → model (`engine: chat`) → `pxf downscale --tol 24 --resolve --grain --register
--metric redmean` against the palette frames, the skin shades and the crop → place → mask
(`hooks.masks`) → `pxf remix --alpha` → `hooks.keep_effects` → `hooks.boss_backfill` →
`hooks.no_sash` → twins.

**Finish**: Aseprite edits → `smooth` → `bounce.py` → `cleanup.py` → gather → frames kept
stock → `beachfx.py` → `swordfix.py` → `trailfix.py` → `gauntlet.py` → `hold` (refused
frames shown as a finished neighbour, canvases grown) → `bkbake.py`.

## The scripts

| file | role | measured reason |
|---|---|---|
| `bodymask.py` | her whole figure: the `bkmask` body (face → skin/hair/cloth, capped) + stocking/boot pieces from `legs.region` that are near her face, her body or a kept piece + the skirt between | each single rule lost poses (skirt/cape between torso and legs; kicks whose stockings start at the knee); after-images stay out |
| `bkmask.py`, `legs.py`, `qmask.py`, `qmask01.py`, `qretex.py`, `qretex_form.py`, `bikini.py`, `frame_out.py` | colour classes, body axis, leg region, rule-painted bikini | the building blocks of the figure finder and of the fallback paint |
| `hooks.py` | `masks`: figure grown 4 px over the shoulder-armour spikes and without limit over dark stocking pixels in the lower 55% of her box | spikes stayed stock after the model removed the armour; boots stayed on in some kicks |
| | `keep_effects`: stock pixel back where the edit left a hole in a slash arc, a light cone or a dotted effect | arcs behind her head are inside her mask; the model drew background there |
| | `boss_backfill`: the scene back behind her in the boss finisher | a halo around her in game |
| | `no_sash`: strip the blue sash below 30% of her box | the hair band is the same blue family and sits above |
| `bossmask.py` | her own mask in the boss finisher, by difference with a frame without her | the body finder grabbed the boss |
| `cleanup.py` | specks, scraps under 25 px, leftover sash pieces not touching her hair | dark dots on her thighs, a stray blob beside her legs in the walk |
| `bounce.py` | chest bounce on the idle | — |
| `graft.py` | refused frames filled from a redrawn frame of the same pose; `--stamp` puts a whole donor figure in place | no request, so nothing is resent |
| `beachfx.py`, `foam.py` | effect colours drawn in her sheet turned sea-blue, foam rims; never inside her figure | — |
| `swordfix.py` | stock sword pixels back around the glint the models drew in the dash | she "glowed" while running; the sword is also excluded from beachfx |
| `trailfix.py` | the leg-swing trail in one get-up frame (stocking-dark, tinted blue by beachfx) cut; small detached streaks cut from the last get-up frame | it swept over her head as she stood up |
| `gauntlet.py` | the stock bracer pasted on her back forearm in every dash frame; stock shoe scraps turned to skin | each frame was redrawn on its own, so the bracer flickered |
| `bkbake.py`, `bkpaint.py` | bake from the pristine archive every time; finished frames from `--frames`, every other drawn frame rule-painted; `grow` applied to every sheet sharing the animation file; town sheet updated where sizes match | rerunning gives the same bytes; a frame no model drew still wears the bikini |
| `make_refs.py` | palette references | the model's shin shadow snapped to gold trim without the extra skin shades |
| `preview.py` | preview sheets for the scripts' `preview` commands | — |

## Hold and grow

The model refused two get-up frames and there was no donor pose, so they show the finished
next frame instead (`"hold"`). That frame is taller than their stock canvases, so placed at
its own spot it lost the top of her head. `"grow"` pads those two canvases in every sheet
sharing the animation file and moves their steps by the padding: the other sheets are
pixel-identical on screen before and after.

## Engines used

The final sheet is mostly the `chat` engine (one frame per request). Earlier pilots used the
`images` engine (an image-edit model) on 4–6-frame sheets with an approved idle sheet as
reference; it was dropped because its moderation refused most poses (17 of 23 in the
pilots) and its grid fit was lower. Always record which engine drew which frame —
`state.json` keeps it per frame.
