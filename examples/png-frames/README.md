# Example: any PNG animation

The smallest project: a folder of PNG frames in, the same folder layout out, every frame
redrawn by the model and snapped back onto its own pixel grid and palette.

```
examples/png-frames/
  project.json
  prompt.txt            describe the change; keep the "same pose / same size" lines
  frames/               your sprite frames, PNG with transparency
    idle/00.png 01.png …      one subfolder = one animation (frames in name order)
    run/00.png  01.png …
  palette/              optional: PNG swatches of colours the new art may use
                        (skin, new cloth) that the original frames do not contain
```

```sh
cd examples/png-frames
cp ../../.env.example .env        # endpoint, key, model names
export PXF_PROJECT=$PWD/project.json
export PYTHONPATH=$PWD/../../python

python -m pxf_pipeline prepare                 # frames -> work/all, animations, crop boxes
python -m pxf_pipeline batch run first anim:0  # one animation; look at it first
python -m pxf_pipeline review 0                # work/review/anim_00.png: before / after
python -m pxf_pipeline batch run rest all
python -m pxf_pipeline finish                  # smooth each animation, gather, export
ls out/                                        # idle/00.png … with the redrawn frames
```

Notes:

- Frames of one animation are aligned on their bottom centre, so canvases may differ.
- The palette is what the original frames use, plus `palette/`. A colour that is in neither
  cannot appear in the result — add a swatch for every new colour you ask for.
- The edit is kept inside the original silhouette (plus `mask_grow` pixels). Taking things
  away (a cape, a skirt) works; making the figure bigger needs a custom mask hook.
- `finish` exports every frame: redrawn ones from the model, the rest unchanged.
