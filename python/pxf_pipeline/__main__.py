"""pixel-forge <command> [args] [--project project.json]      (or python -m pxf_pipeline)

  mcp                             run as an MCP server over stdio, for agents

single sprite (no project.json, see forge.py):
  create <outdir> --prompt TEXT   draw a new sprite from text
  animate <sprite> <outdir> ...   one sprite -> N frames on a fixed canvas
  edit <sprite> <out> --prompt    change one sprite, same canvas and grid
  pixelize <picture> <out>        an off-grid picture -> a sprite, no model call
  bundle <framedir>               frames -> sheet + json + gif/webp previews
  palettes                        the preset palettes

projects (a folder of animations, see docs/pipeline.md):
  prepare                         frames (PNG folder or game archive) + animations + crops into <work>
  plan [--scale N]                group frames into sheets (sheet-based engines)
  batch run|redo|requeue|status   send frames to the model and bring them back
  autorun <name>                  batch + finish across rate-limit windows
  smooth [anims]                  one drawing per animation
  transplant <run> <f>:<donor>    carry a finished frame onto a near-identical pose
  graft <f>:<donor> [--stamp]     fill a frame from a finished one of the same pose
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

COMMANDS = ['prepare', 'plan', 'batch', 'autorun', 'smooth', 'transplant', 'graft', 'hold', 'review',
            'leftover', 'crops', 'finish', 'export', 'backend', 'asebridge']


FORGE = ['create', 'animate', 'edit', 'pixelize', 'bundle', 'palettes']


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS + FORGE + ['mcp']:
        raise SystemExit(__doc__)
    name = sys.argv.pop(1)
    if name == 'mcp':
        from . import mcp_server
        return mcp_server.main()
    if name in FORGE:
        from . import forge
        return forge.main(name)
    sys.argv[0] = 'pxf_pipeline.' + name
    importlib.import_module('pxf_pipeline.' + name).main()


if __name__ == '__main__':
    main()
