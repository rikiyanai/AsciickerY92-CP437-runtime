#!/usr/bin/env python3
# =============================================================================
# scripts/sheet_unifont_5x7.py  --  repo-root wrapper for the 5x7 sheet renderer
# =============================================================================
# The REAL renderer lives at .scratch/CLIPasso/sheet_unifont_5x7.py.  It cannot
# run from the repo root with plain `python3` because it:
#   * needs the gradscii virtualenv (svgpathtools / PIL / numpy / fontTools),
#   * imports its siblings unifont_match.py and gen_walk_unifont.py, and
#   * uses paths relative to .scratch/CLIPasso (output_sketches/, renders/).
#
# This thin wrapper re-invokes the real script with the right interpreter, from
# the .scratch/CLIPasso directory, forwarding any extra args (e.g. --svgroot ...).
# So `python3 scripts/sheet_unifont_5x7.py` from the repo root now succeeds.
# =============================================================================
import os
import sys
import subprocess

# Repo root = parent of this scripts/ directory.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIP = os.path.join(REPO, ".scratch", "CLIPasso")                    # real script's home
PY = os.path.join(REPO, ".scratch", "gradscii-art", ".venv", "bin", "python")  # its venv

# Forward all CLI args (e.g. --svgroot output_sketches/walk_p400) to the real one,
# running it from .scratch/CLIPasso so its relative paths resolve.
sys.exit(subprocess.run([PY, "sheet_unifont_5x7.py", *sys.argv[1:]], cwd=CLIP).returncode)
