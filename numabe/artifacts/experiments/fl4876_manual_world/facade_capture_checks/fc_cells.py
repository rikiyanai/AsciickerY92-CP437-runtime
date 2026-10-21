"""Test cells for the facade-capture pipeline check (NOT an operator capture): the operator's queued Lawson
screenshot, roughly cropped (it is near-frontal), converted with the build's half-block converter at band size."""
import json
import sys
from pathlib import Path

ROOT = Path("/Users/r/Projects/asciicker-Y9-2")
sys.path[:0] = [str(ROOT), str(ROOT / "docs/agent/cli-anything")]
from PIL import Image  # noqa: E402
from scripts.pipeline import compile_sign_sprites as C  # noqa: E402

img = Image.open(ROOT / "artifacts/experiments/fl4876_manual_world/operator_map_review/shots/a3caa18e6d617dd1.png").convert("RGB")
W, H = img.size
sc = 5.1320023927966725
face_w = 8.41
# (band, image crop as fractions of the screenshot, metres on the face)
bands = [("sign", (0.0, 0.07, 0.90, 0.19), (0.0, face_w, 2.6, 3.3)),
         ("storefront", (0.0, 0.19, 0.90, 0.80), (0.0, face_w, 0.0, 2.6))]
out = []
for kind, (fx0, fy0, fx1, fy1), (x0, x1, y0, y1) in bands:
    crop = img.crop((int(fx0 * W), int(fy0 * H), int(fx1 * W), int(fy1 * H)))
    cols = round((x1 - x0) * sc * C.COLS_PER_WORLD_UNIT)
    rows = round((y1 - y0) * sc * C.HEIGHT_SCALE / C.Z_PER_ROW)
    small = crop.resize((cols, rows * 2), Image.LANCZOS).load()
    cells = [[[C.HALF_UPPER, list(C._snap(small[x, 2 * y])), list(C._snap(small[x, 2 * y + 1]))] for x in range(cols)]
             for y in range(rows)]
    out.append({"kind": kind, "cells": cells, "x0_m": x0, "x1_m": x1, "y0_m": y0, "y1_m": y1})
    print(kind, cols, "x", rows)
Path(sys.argv[1]).write_text(json.dumps(out))
