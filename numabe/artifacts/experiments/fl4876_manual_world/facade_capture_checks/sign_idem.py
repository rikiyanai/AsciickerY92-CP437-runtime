"""Re-run compile_sign_sprites.build twice on a scratch copy of build_reg_73 (no operator captures) and count
sprite instances after each run: an idempotent stage keeps 19."""
import shutil
import sys
from pathlib import Path

SP = Path(__file__).resolve().parent
ROOT = Path("/Users/r/Projects/asciicker-Y9-2")
sys.path[:0] = [str(ROOT), str(ROOT / "docs/agent/cli-anything")]
import a3d_edit as A  # noqa: E402
from scripts.pipeline import compile_sign_sprites as C  # noqa: E402

b = SP / "idem_build"
if b.is_dir():
    shutil.rmtree(b)
shutil.copytree(ROOT / ".run/fl4876_builds/build_reg_73", b, ignore=shutil.ignore_patterns("meshes", "image_observations", "*.bak"))
C.OPERATOR_CAPTURES = SP / "no_captures.json"
count = lambda: sum(1 for i in list(A.read_a3d_sections(b / "output.a3d"))[2] if i.variant == "sprite")
print("before", count())
for k in (1, 2):
    doc = C.build(b, b / "output.a3d")
    print("run", k, "receipt signs", len(doc["signs"]), "map sprites", count())
shutil.rmtree(b)
