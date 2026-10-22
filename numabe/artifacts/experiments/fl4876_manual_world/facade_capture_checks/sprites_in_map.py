"""sprites_in_map.py MAP.a3d -- sprite instances in a map (name, pos)."""
import sys
from collections import Counter
from pathlib import Path

sys.path[:0] = ["/Users/r/Projects/asciicker-Y9-2/docs/agent/cli-anything"]
import a3d_edit as A  # noqa: E402

inst = list(A.read_a3d_sections(Path(sys.argv[1])))[2]
print(Counter(i.variant for i in inst))
for i in inst:
    if i.variant == "sprite":
        print(i.inst_name, [round(v, 1) for v in i.pos], i.flags)
