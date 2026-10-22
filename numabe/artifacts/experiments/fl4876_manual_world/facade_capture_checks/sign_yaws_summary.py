"""sign_yaws_summary.py A.jsonl [B.jsonl] -- per sign: best-heading drawn fraction of its cells (real placement), and
the count of signs showing >= 40% from some heading; with two files, side by side. Read-only."""
import json
import sys


def load(p):
    return {r["label"]: r for r in (json.loads(l) for l in open(p) if l.startswith("{"))}


files = [load(p) for p in sys.argv[1:]]
labels = sorted(set().union(*files))
for lab in labels:
    cells = []
    for f in files:
        r = f.get(lab)
        cells.append("-" if r is None else "%3d/%-3d %.2f" % (max(r["real"]), r["total"], max(r["real"]) / r["total"]))
    print("%-40s %s" % (lab[:40], "   ".join(cells)))
for p, f in zip(sys.argv[1:], files):
    ok = sum(max(r["real"]) / r["total"] >= 0.4 for r in f.values())
    print(p.rsplit("/", 1)[-1], ">=40%% from some heading: %d of %d" % (ok, len(f)))
