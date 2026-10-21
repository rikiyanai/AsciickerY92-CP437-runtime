"""Scratch copy of build_reg_73 with the sign stage re-run under the current compile_sign_sprites (no operator
captures), so sign_vis.mjs can measure the 19 signs on it via a server on another port."""
import json
import shutil
import sys
from pathlib import Path

SP = Path(__file__).resolve().parent
ROOT = Path("/Users/r/Projects/asciicker-Y9-2")
sys.path[:0] = [str(ROOT), str(ROOT / "docs/agent/cli-anything")]
from scripts.pipeline import compile_sign_sprites as C  # noqa: E402

b = SP / "clear_build"
if b.is_dir():
    shutil.rmtree(b)
shutil.copytree(ROOT / ".run/fl4876_builds/build_reg_73", b, ignore=shutil.ignore_patterns("image_observations", "*.bak"))
C.OPERATOR_CAPTURES = SP / "no_captures.json"
doc = C.build(b, b / "output.a3d")
(b / "sign_sprites_receipt.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
print("signs", len(doc["signs"]), "skipped", len(doc["skipped"]))
