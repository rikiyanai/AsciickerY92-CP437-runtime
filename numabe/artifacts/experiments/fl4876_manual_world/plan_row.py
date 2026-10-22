"""plan_row.py BUILD_DIR NEEDLE -- list-only: every JSON object (dict) in the build's plan files whose
placement_id / subject / instance_id contains NEEDLE, printed compactly with its file path."""
import json
import sys
from pathlib import Path

build, needle = Path(sys.argv[1]), sys.argv[2]
KEYS = ("placement_id", "subject", "instance_id", "fixture_id")


def walk(o, path, out):
    if isinstance(o, dict):
        if any(needle in str(o.get(k, "")) for k in KEYS):
            out.append((path, o))
            return
        for k, v in o.items():
            walk(v, path + "/" + str(k), out)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            walk(v, path + "[%d]" % i, out)


for p in sorted(list(build.glob("plan/**/*.json")) + [build / "spatial_coherence.json", build / "object_metric.json"]):
    if not p.exists():
        continue
    try:
        doc = json.loads(p.read_text())
    except Exception:
        continue
    out = []
    walk(doc, "", out)
    for path, o in out:
        print("==", p.relative_to(build), path)
        print(json.dumps(o, ensure_ascii=False)[:1500])
