"""The project file: where one character's work lives and which steps are its own.

A project is a JSON file (see examples/beach-costume/project.json). Every path in it is
relative to the file's directory. It is found, in order, from:

    --project <file> on the command line (removed from sys.argv when read)
    the PXF_PROJECT environment variable
    ./project.json

Keys used by the core pipeline (all optional unless noted):

    work            working directory (default "work"); holds all/, anim.json, crops.json,
                    runs/, bake_all/ ...
    source_archive      the game archive the sheet is imported from (prepare)
    sheet, ani, per sheet and .ani member names, steps per animation in the .ani
    scale, gap, bg, canvas   how one frame is laid out for the model (5, 8, "808080", 1024)
    refs            extra palette references for `pxf downscale`, relative to work
    downscale_flags extra flags for `pxf downscale`
    prompt_file     the edit prompt (required for batch)
    engine          "chat" (chat-completions image model) or "images" (/v1/images/edits)
    leftover_max, tries   retry a frame whose stock outfit survived above leftover_max %
    mask_grow       px the figure mask may grow over the stock drawing
    hooks           {"masks": "file.py:func", "crops": ..., "after_remix": ["file.py:func", ...]}
    finish          see finish.py
    grow, hold      see grow.py / hold.py
"""
import importlib, importlib.util, json, os, sys

from . import env

_current = None


class Project:
    def __init__(self, path):
        self.file = os.path.abspath(path)
        self.root = os.path.dirname(self.file)
        self.cfg = json.load(open(self.file, encoding='utf-8'))
        self.work = os.path.join(self.root, self.cfg.get('work', 'work')).rstrip('/\\') + '/'
        env.load([self.root])

    def get(self, key, default=None):
        return self.cfg.get(key, default)

    def path(self, rel):
        """a path from the project file, relative to the project directory"""
        return rel if os.path.isabs(rel) else os.path.join(self.root, rel)

    def stock(self, f):
        """the stock (imported) frame f as PNG"""
        return self.work + 'all/frame_%03d.png' % f

    def crops(self):
        return json.load(open(self.work + 'crops.json'))

    def anims(self):
        return json.load(open(self.work + 'anim.json'))['anims']

    def hook(self, name, default=None):
        spec = self.cfg.get('hooks', {}).get(name)
        if not spec:
            return default
        return [load_callable(s, self.root) for s in spec] if isinstance(spec, list) else \
            load_callable(spec, self.root)

    def child_env(self):
        """environment for a step run as a subprocess: same project"""
        e = dict(os.environ)
        e['PXF_PROJECT'] = self.file
        return e


def load_callable(spec, root):
    """"path/to/file.py:func" (relative to the project) or "package.module:func" """
    mod, _, fn = spec.partition(':')
    if mod.endswith('.py'):
        p = mod if os.path.isabs(mod) else os.path.join(root, mod)
        d = os.path.dirname(os.path.abspath(p))
        if d not in sys.path:
            sys.path.insert(0, d)          # so an example step can import its siblings
        name = os.path.splitext(os.path.basename(p))[0]
        m = sys.modules.get(name)
        if m is None or os.path.abspath(getattr(m, '__file__', '')) != os.path.abspath(p):
            s = importlib.util.spec_from_file_location(name, p)
            m = importlib.util.module_from_spec(s)
            sys.modules[name] = m
            s.loader.exec_module(m)
    else:
        m = importlib.import_module(mod)
    return getattr(m, fn or 'main')


def current():
    global _current
    if _current is None:
        path = None
        if '--project' in sys.argv:
            i = sys.argv.index('--project')
            path = sys.argv[i + 1]
            del sys.argv[i:i + 2]
            os.environ['PXF_PROJECT'] = os.path.abspath(path)
        path = path or os.environ.get('PXF_PROJECT') or 'project.json'
        if not os.path.isfile(path):
            raise SystemExit('no project file: %s (pass --project or set PXF_PROJECT)' % path)
        _current = Project(path)
    return _current
