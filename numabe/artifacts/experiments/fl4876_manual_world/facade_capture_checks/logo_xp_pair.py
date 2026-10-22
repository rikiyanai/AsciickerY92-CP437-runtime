"""logo_xp_pair.py CELLS.json OUT_DIR -- encode a logo cell grid twice through the editor's own path
(map_editor.clean_cells -> compile_sign_sprites.xp_bytes): as authored (null cells / null backgrounds -> XP
magenta key) and with every transparent cell/side filled black. sign_frame-style renders of the two show whether
the engine keeps the key cells see-through. Writes OUT_DIR/logo_keyed.xp and OUT_DIR/logo_filled.xp."""
import json
import sys
from pathlib import Path

ROOT = Path("/Users/r/Projects/asciicker-Y9-2")
sys.path[:0] = [str(ROOT), str(ROOT / "artifacts/experiments/fl4876_manual_world")]
from scripts.pipeline import compile_sign_sprites as C  # noqa: E402
import map_editor as M  # noqa: E402

cells = json.loads(Path(sys.argv[1]).read_text())
out = Path(sys.argv[2])
filled = [[[219, [0, 0, 0], [0, 0, 0]] if c is None else [c[0], c[1], c[2] if c[2] is not None else [0, 0, 0]] for c in row]
          for row in cells]
for name, grid in (("logo_keyed.xp", cells), ("logo_filled.xp", filled)):
    (out / name).write_bytes(C.xp_bytes(M.clean_cells(grid, C)))
    print(out / name)
