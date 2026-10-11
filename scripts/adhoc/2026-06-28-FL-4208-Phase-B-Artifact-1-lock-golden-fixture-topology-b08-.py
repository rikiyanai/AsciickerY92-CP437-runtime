# Ad hoc script: FL-4208 Phase-B Artifact-1: lock golden-fixture topology (b0=8-conn FG comps, b1=4-conn bounded BG comps) at 48px unifont
# Created: 2026-06-28
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""Lock FL-4208 Phase-B golden-fixture topology invariants.

Measures, per fixture glyph rendered at 48px through the glyph_skeleton font chain:
  b0 = number of 8-connected FOREGROUND components (mask)
  b1 = number of 4-connected BOUNDED BACKGROUND components (mask) = actual hole count
This is the (8,4)-connectivity convention locked in the topology contract (Artifact 1).
NO graph construction here (that is Phase-B code, unbuilt); degmset is frozen later by the
constructor's own harness. This locks the *counts*, replacing the unrasterized {...} table.
"""
import sys
from pathlib import Path
import numpy as np
from collections import deque

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from glyph_skeleton import HiRenderer as Renderer

SIZE = 48
R = Renderer()

def label_fg_8(mask):
    """Count 8-connected foreground components."""
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    n = 0
    for sy in range(h):
        for sx in range(w):
            if mask[sy, sx] and not seen[sy, sx]:
                n += 1
                dq = deque([(sy, sx)]); seen[sy, sx] = True
                while dq:
                    y, x = dq.popleft()
                    for dy in (-1, 0, 1):
                        for dx in (-1, 0, 1):
                            if dy == 0 and dx == 0: continue
                            ny, nx = y+dy, x+dx
                            if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                                seen[ny, nx] = True; dq.append((ny, nx))
    return n

def holes_4(mask):
    """Count 4-connected bounded background components (holes)."""
    h, w = mask.shape
    bg = ~mask
    reach = np.zeros_like(mask, dtype=bool)
    dq = deque()
    for x in range(w):
        for y in (0, h-1):
            if bg[y, x] and not reach[y, x]:
                reach[y, x] = True; dq.append((y, x))
    for y in range(h):
        for x in (0, w-1):
            if bg[y, x] and not reach[y, x]:
                reach[y, x] = True; dq.append((y, x))
    while dq:
        y, x = dq.popleft()
        for dy, dx in ((1,0),(-1,0),(0,1),(0,-1)):
            ny, nx = y+dy, x+dx
            if 0 <= ny < h and 0 <= nx < w and bg[ny, nx] and not reach[ny, nx]:
                reach[ny, nx] = True; dq.append((ny, nx))
    # bounded bg = not reachable from border; count its 4-connected components
    bounded = bg & ~reach
    seen = np.zeros_like(mask, dtype=bool)
    n = 0
    for sy in range(h):
        for sx in range(w):
            if bounded[sy, sx] and not seen[sy, sx]:
                n += 1
                dq = deque([(sy, sx)]); seen[sy, sx] = True
                while dq:
                    y, x = dq.popleft()
                    for dy, dx in ((1,0),(-1,0),(0,1),(0,-1)):
                        ny, nx = y+dy, x+dx
                        if 0 <= ny < h and 0 <= nx < w and bounded[ny, nx] and not seen[ny, nx]:
                            seen[ny, nx] = True; dq.append((ny, nx))
    return n, int(bounded.sum())

FIXTURES = ['I','l','|','/','T','Y','X','+','O','0','o','P','A','Q','D','8','B','i','j']
print(f"{'glyph':>5} {'cp':>6} {'b0':>3} {'b1':>3} {'bounded_area':>12}")
print("-"*40)
for ch in FIXTURES:
    cp = ord(ch)
    g = R.grid(cp, SIZE)
    mask = g.astype(bool)
    b0 = label_fg_8(mask)
    b1, area = holes_4(mask)
    print(f"{ch:>5} U+{cp:04X} {b0:>3} {b1:>3} {area:>12}")


# ---------------------------------------------------------------------------
# Finding 7 (round 3): emit a DURABLE JSON receipt so the measured fixtures are
# evidence, not just stdout. Records font SHA-256 + path per codepoint, Pillow
# version, raster parameters (render_px rule, LANCZOS, INK_THRESHOLD, pad, size),
# per-glyph mask SHA-256, b0/b1raw, the command, and the git commit. Any of these
# changing (font file, Pillow resampling, threshold) can change topology, so they
# are part of the locked evidence.
# ---------------------------------------------------------------------------
import hashlib, json, subprocess, platform
import PIL
import glyph_skeleton as _gs

def _sha256_file(p):
    try:
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()
    except Exception:
        return None

def _git_head():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"],
                                       cwd=str(Path(__file__).resolve().parents[2])).decode().strip()
    except Exception:
        return None

font_sha_cache = {}
rows = []
for ch in FIXTURES:
    cp = ord(ch)
    fp = R._path_for(cp)
    fp_str = str(fp)
    if fp_str not in font_sha_cache:
        font_sha_cache[fp_str] = _sha256_file(fp)
    g = R.grid(cp, SIZE)
    mask = g.astype(bool)
    b0 = label_fg_8(mask)
    b1, area = holes_4(mask)
    rows.append({
        "glyph": ch, "codepoint": f"U+{cp:04X}", "cp": cp,
        "font_path": fp_str, "font_sha256": font_sha_cache[fp_str],
        "b0_fg8": b0, "b1raw_bg4_holes": b1, "bounded_bg_area_px": area,
        "mask_sha256": hashlib.sha256(mask.tobytes()).hexdigest(),
        "mask_shape": list(mask.shape),
    })

receipt = {
    "fl": "FL-4485", "artifact": "FL-4208 Phase-B Artifact-1 golden-fixture topology",
    "connectivity_convention": "(8,4): FG 8-connected, BG 4-connected (bounded components = holes)",
    "raster_params": {
        "size": SIZE, "render_px_rule": "max(size*3, 96)",
        "resampling": "PIL.Image.LANCZOS", "ink_threshold": _gs.INK_THRESHOLD,
        "pad": 8, "font_chain": [str(p) for p in R._chain_paths],
    },
    "env": {"pillow_version": PIL.__version__, "numpy": np.__version__,
            "python": platform.python_version()},
    "git_commit": _git_head(),
    "command": "python3 " + str(Path(__file__).relative_to(Path(__file__).resolve().parents[2])),
    "rows": rows,
}

OUT_DIR = Path(__file__).resolve().parents[2] / "docs/research/ascii/verification/fl4208"
OUT_DIR.mkdir(parents=True, exist_ok=True)
out_path = OUT_DIR / "2026-06-28-golden-fixture-topology-receipt.json"
out_path.write_text(json.dumps(receipt, indent=2, sort_keys=True))
print(f"\nReceipt written: {out_path}")
