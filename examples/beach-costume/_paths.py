"""Shared by every script in this example: find the pxf_pipeline package and the project.

WORK         the project's work directory (all/, anim.json, crops.json, runs/, bake_all/)
SOURCE_ARCHIVE   a pristine copy of the game archive the sheets come from (never written)
TARGET_ARCHIVE   the archive the bake writes into (the game's own file; backed up once)
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
_pkg = os.path.join(HERE, '..', '..', 'python')
if os.path.isdir(_pkg) and os.path.abspath(_pkg) not in sys.path:
    sys.path.insert(0, os.path.abspath(_pkg))

if not os.environ.get('PXF_PROJECT') and os.path.exists(os.path.join(HERE, 'project.json')):
    os.environ['PXF_PROJECT'] = os.path.join(HERE, 'project.json')

from pxf_pipeline.project import current  # noqa: E402

P = current()
WORK = P.work
SOURCE_ARCHIVE = P.path(P.get('source_archive', ''))
TARGET_ARCHIVE = P.path(P.get('target_archive', '')) if P.get('target_archive') else None
