"""python -m pxf_pipeline <command> [args] [--project project.json]

single sprite (no project.json, see forge.py):
  create <outdir> --prompt TEXT   draw a new sprite from text
  animate <sprite> <outdir> ...   one sprite -> N frames on a fixed canvas
  edit <sprite> <out> --prompt    change one sprite, same canvas and grid
  bundle <framedir>               frames -> sheet + json + gif/webp previews
  palettes                        the preset palettes

projects (a folder of animations, see README):
  prepare                         frames (PNG folder or game archive) + animations + crops into <work>
  plan [--scale N]                group frames into sheets (sheet-based engines)
  batch run|redo|requeue|status   send frames to the model and bring them back
  autorun <name>                  batch + finish across rate-limit windows
  smooth [anims]                  one drawing per animation
  transplant <run> <f>:<donor>    carry a finished frame onto a near-identical pose
  hold                            show finished neighbours in place of refused frames
  review <anims> [--zoom N]       stock-vs-result QA sheets
  leftover <dir> <frames>         how much of the stock outfit survived
  crops crops|place|masks ...     the crop / place / mask steps alone
  finish [aseprite runs]          gather, project post steps, bake or export
  export [outdir]                 finished frames back to PNGs under their original names
  backend chat|images ...         one image edit
  asebridge to|from ...           Aseprite round trip
"""
import importlib, sys

COMMANDS = ['prepare', 'plan', 'batch', 'autorun', 'smooth', 'transplant', 'hold', 'review',
            'leftover', 'crops', 'finish', 'export', 'backend', 'asebridge']


FORGE = ['create', 'animate', 'edit', 'bundle', 'palettes']


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS + FORGE:
        raise SystemExit(__doc__)
    name = sys.argv.pop(1)
    if name in FORGE:
        from . import forge
        return forge.main(name)
    sys.argv[0] = 'pxf_pipeline.' + name
    importlib.import_module('pxf_pipeline.' + name).main()


if __name__ == '__main__':
    main()
