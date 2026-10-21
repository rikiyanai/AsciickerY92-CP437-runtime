"""Pytest path setup for the pipeline suites.

``scripts/pipeline`` modules import each other as siblings -- for example
``plan_spatial_provenance`` does ``from canonical_attachment import resolve``
and falls back to ``None`` when that import fails. Because that fallback is
evaluated once at import time, whether span resolution works depended on
which test module happened to be imported first: a suite that pulled
``plan_spatial_provenance`` in before anything had put ``scripts/pipeline``
on ``sys.path`` baked ``None`` in for the whole session, and a later suite
that needed it failed with "span resolve needs
scripts/pipeline/canonical_attachment.py" -- while passing when run alone.

The production runner and the pipeline CLIs already run with
``PYTHONPATH=.:scripts/pipeline``. This gives the test session the same two
roots before any test module is imported, so import order stops mattering.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# APPENDED, not inserted. ``scripts/pipeline`` holds 199 modules, and putting
# 199 names at the FRONT of sys.path for every suite in the session is a much
# wider blast radius than the import-order problem this file exists to fix.
# Appending resolves the sibling imports while leaving any name something
# else already provides where it was.
#
# One name collides at all: ``schemas``. Appending does not actually change
# that one -- site-packages ships it as a namespace package (chromadb's
# ``schemas.embedding_functions``) and this repo ships a regular package, and
# a regular package wins at any sys.path position. It is harmless here
# because nothing in this repository imports ``schemas``, and the production
# runner already runs with ``PYTHONPATH=.:scripts/pipeline``, so this session
# is not the first place that resolution happens. Recorded rather than
# papered over, since the next person to add a pipeline module named after
# something installed will not get a warning.
_PIPELINE = str(ROOT / "scripts" / "pipeline")
if _PIPELINE not in sys.path:
    sys.path.append(_PIPELINE)
