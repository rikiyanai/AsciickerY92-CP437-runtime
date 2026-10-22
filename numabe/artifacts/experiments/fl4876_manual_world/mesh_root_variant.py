"""mesh_root_variant.py SRC_MESH_ROOT OUT_ROOT EXCLUDE_PREFIX... -- render experiment only (decides nothing): copies a
build's mesh root to OUT_ROOT without the files whose names start with any EXCLUDE_PREFIX, to bisect Classic frame-time
regressions by mesh family (ASCIICKER_ACTIVE_MESH_ROOT points the editor at the copy)."""
import shutil, sys
from pathlib import Path
src, out, excl = Path(sys.argv[1]), Path(sys.argv[2]), tuple(sys.argv[3:])
if out.exists():
    shutil.rmtree(out)
shutil.copytree(src, out, ignore=lambda d, names: [n for n in names if n.startswith(excl)])
print(out, "files", sum(1 for _ in out.iterdir()))
