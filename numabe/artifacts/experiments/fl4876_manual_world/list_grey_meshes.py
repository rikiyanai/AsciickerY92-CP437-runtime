#!/usr/bin/env python3
"""list_grey_meshes.py BUILD_DIR -- FL-4876 listing aid (measures, decides nothing).

Lists mesh files used by instances of BUILD_DIR/output.a3d whose ASCII-PLY vertex colours are all one neutral grey
(r == g == b), i.e. a module drawn as an uncoloured placeholder, with how many instances draw each one and the
first few instance names. Binary or non-PLY meshes are skipped and counted.
"""
import collections
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import a3d_edit as A  # noqa: E402


def colours(path: Path):
    try:
        lines = path.read_text(errors="strict").splitlines()
    except (UnicodeDecodeError, OSError):
        return None
    if not lines or lines[0].strip() != "ply" or "ascii" not in lines[1]:
        return None
    props, nv, i = [], 0, 2
    while lines[i].strip() != "end_header":
        t = lines[i].split()
        if t[:2] == ["element", "vertex"]:
            nv = int(t[2])
        elif t[0] == "property" and t[1] != "list" and nv and not props_done(lines, i):
            props.append(t[-1])
        i += 1
    if "red" not in props:
        return None
    ri, gi, bi = props.index("red"), props.index("green"), props.index("blue")
    out = set()
    for ln in lines[i + 1:i + 1 + nv]:
        v = ln.split()
        out.add((int(v[ri]), int(v[gi]), int(v[bi])))
    return out


def props_done(lines, i):
    # vertex properties stop at the next 'element' line; face properties belong to the face element
    for j in range(i - 1, 1, -1):
        t = lines[j].split()
        if t and t[0] == "element":
            return t[1] != "vertex"
    return False


def main():
    build = Path(sys.argv[1])
    data = (build / "output.a3d").read_bytes()
    _, _, recs, _ = A.parse(data)
    uses = collections.defaultdict(list)
    for r in recs:
        if r["kind"] == "mesh":
            uses[r["mesh"]].append(r["inst"])
    grey, skipped = [], 0
    for mesh, names in uses.items():
        c = colours(build / "meshes" / mesh)
        if c is None:
            skipped += 1
            continue
        if len(c) == 1:
            (rgb,) = c
            if rgb[0] == rgb[1] == rgb[2]:
                grey.append((len(names), mesh, rgb, names[:4]))
    grey.sort(reverse=True)
    print(f"meshes in use {len(uses)}, skipped (not ascii ply with colour) {skipped}, single-grey {len(grey)}, "
          f"instances drawing them {sum(g[0] for g in grey)}")
    for n, mesh, rgb, names in grey:
        print(f"{n:4d} {rgb} {mesh}  e.g. {', '.join(names)}")


if __name__ == "__main__":
    main()
