"""stage_sign_sprites.py remove BUILD_DIR -- cleanup only.

'add' is RETIRED (FL-4876 SIGN-SHADOW-01, traced by FACADE-CAPTURE-REVIEW-20261014): it copied a build's
sign_*.xp into assets/sprites on the old premise that ASCIIID Classic loads sprites only from there. The
runtime now registers <map>.sprites/ itself (RegisterMapSprites), and the registry keeps the FIRST name
registered, with assets/sprites loaded first, so a staged copy silently replaces every later build's sign of
the same name. 'add build_reg_64' without a matching 'remove' left 19 such copies (2026-10-13 08:03:20),
which hid every rebuilt sign until they were quarantined. compile_sign_sprites.py now refuses a build whose
sign names exist in assets/sprites. 'remove' still deletes exactly the files a past 'add' recorded."""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
op, build = sys.argv[1], Path(sys.argv[2])
dst = ROOT / "assets/sprites"
rec = build / "staged_sign_sprites.json"
if op == "add":
    sys.exit("stage_sign_sprites.py add is retired (SIGN-SHADOW-01): the runtime loads <map>.sprites/ itself, and "
             "a copy in assets/sprites shadows every later build's sign of the same name. Open the build directly.")
if op != "remove":
    sys.exit("usage: stage_sign_sprites.py remove BUILD_DIR")
staged = json.loads(rec.read_text())
for n in staged:
    (dst / n).unlink(missing_ok=True)
rec.unlink()
print("removed", len(staged))
