"""inst_diff.py A.a3d B.a3d -- instance-list difference between two maps (name, variant, rounded pos/transform)."""
import sys
from collections import Counter
from pathlib import Path

sys.path[:0] = ["/Users/r/Projects/asciicker-Y9-2/docs/agent/cli-anything"]
import a3d_edit as A  # noqa: E402


def key(i):
    t = getattr(i, "pos", None) if getattr(i, "variant", "") == "sprite" else i.transform[12:15]
    return (getattr(i, "variant", ""), str(i.inst_name), str(i.mesh_name), tuple(round(float(v), 2) for v in (t or [])), int(i.flags))


secs = [list(A.read_a3d_sections(Path(p))) for p in sys.argv[1:3]]
ka, kb = Counter(map(key, secs[0][2])), Counter(map(key, secs[1][2]))
print("instances", sum(ka.values()), sum(kb.values()))
print("only in A:", len(ka - kb))
for k in list((ka - kb).elements())[:15]:
    print("  -", k)
print("only in B:", len(kb - ka))
for k in list((kb - ka).elements())[:15]:
    print("  +", k)
for n in range(len(secs[0])):
    if n != 2:
        same = secs[0][n] == secs[1][n] if not hasattr(secs[0][n], "__len__") or len(secs[0][n]) == len(secs[1][n]) else False
        print("section", n, type(secs[0][n]).__name__, "equal" if same else "DIFFERENT")
