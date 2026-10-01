"""Write the finished frames back out as PNGs, under their original names.

    python -m pxf_pipeline.export [outdir]

For projects prepared from a folder of PNG frames ("frames"). Every frame listed in
<work>/frames.json is written to outdir (default <work>/out, or the project's "export_to")
at its original relative path: the finished frame from <work>/bake_all when there is one,
the original frame otherwise. finish runs this when the project has no finish.bake.
"""
import json, os, shutil, sys

from .project import current


def main():
    P = current()
    names = json.load(open(P.work + 'frames.json'))
    out = sys.argv[1] if len(sys.argv) > 1 else P.path(P.get('export_to', P.work + 'out'))
    done = 0
    for f, rel in names.items():
        fin = P.work + 'bake_all/frame_%03d.png' % int(f)
        dst = os.path.join(out, rel)
        os.makedirs(os.path.dirname(dst) or '.', exist_ok=True)
        if os.path.exists(fin):
            shutil.copy(fin, dst)
            done += 1
        else:
            shutil.copy(P.stock(int(f)), dst)
    print('%d frame(s) written to %s, %d redrawn' % (len(names), out, done))


if __name__ == '__main__':
    main()
