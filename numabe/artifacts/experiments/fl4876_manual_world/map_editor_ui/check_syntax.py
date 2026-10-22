"""check_syntax.py -- syntax check of the editor's JavaScript: facade_capture.js, editor_views.js, street_view_overlay.js and every inline <script> of
a3d.html, via `node --check` on temporary copies (checks only; writes nothing in the repo)."""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
bad = 0
with tempfile.TemporaryDirectory() as td:
    jobs = [(n, (HERE / n).read_text()) for n in ("facade_capture.js", "editor_views.js", "street_view_overlay.js")]
    for i, s in enumerate(re.findall(r"<script>(.*?)</script>", (HERE / "a3d.html").read_text(), re.S)):
        jobs.append(("a3d.html#script%d" % i, s))
    for name, src in jobs:
        p = Path(td) / "x.js"
        p.write_text(src)
        r = subprocess.run(["node", "--check", str(p)], capture_output=True, text=True)
        print(name, "ok" if r.returncode == 0 else "SYNTAX ERROR\n" + r.stderr[-1500:])
        bad += r.returncode != 0
sys.exit(1 if bad else 0)
