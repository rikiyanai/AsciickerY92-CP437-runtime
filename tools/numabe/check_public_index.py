#!/usr/bin/env python3
"""Check the exact staged Numabe publication, including GitHub file limits.

This inspects Git blobs, not untracked local datasets or optimistic disk state.
It never changes the index, branches, configuration or worktree.
"""
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def git(*args, data=None):
    """Run a read-only Git plumbing operation against this publication checkout."""
    return subprocess.check_output(["git", "-C", str(ROOT), *args], input=data)


def main():
    """Fail when a runtime asset is absent, a raw survey leaked, or a blob is too large."""
    changed = {p.decode() for p in git("diff", "--cached", "--name-only", "--diff-filter=ACM", "-z").split(b"\0") if p}
    entries = {}
    for entry in git("ls-files", "--stage", "-z").split(b"\0"):
        if not entry:
            continue
        header, path = entry.split(b"\t", 1)
        mode, oid, stage = header.decode().split()
        if stage != "0":
            raise SystemExit("Unresolved index stages are not publishable")
        entries[path.decode()] = oid
    oids = sorted({entries[path] for path in changed})
    output = git("cat-file", "--batch-check=%(objectname) %(objectsize)",
                 data=("\n".join(oids) + "\n").encode()).decode().splitlines()
    sizes = {line.split()[0]: int(line.split()[1]) for line in output}
    too_big = [(path, sizes[entries[path]]) for path in changed if sizes[entries[path]] >= 100_000_000]
    raw = [path for path in changed if path.startswith("numabe/assets/source_bundles/")
           and ("/plateau/" in path or "/tmg_pointcloud/" in path)]
    if too_big or raw:
        raise SystemExit(json.dumps({"required_sharding": too_big, "unneeded_raw_surveys": raw}))
    prefix = "numabe/.run/fl4876_builds/live_numabe/"
    required = {prefix + "output.a3d", prefix + "output.trains.json",
                "numabe/.web-classic/index.js", "numabe/.web-classic/index.wasm",
                "numabe/.web-classic/index.data", "docs/recordings/numabe-train.gif",
                "numabe/artifacts/experiments/fl4876_manual_world/map_editor.py"}
    import sys
    sys.path.insert(0, str(ROOT / "numabe/artifacts/experiments/fl4876_manual_world"))
    import a3d_edit
    map_bytes = git("show", ":" + prefix + "output.a3d")
    records = a3d_edit.parse(map_bytes)[2]
    for row in records:
        if row["kind"] != "mesh":
            continue
        candidates = (prefix + "meshes/" + row["mesh"], "numabe/assets/meshes/" + row["mesh"])
        if not any(path in entries for path in candidates):
            raise SystemExit("Map references an unstaged mesh: " + row["mesh"])
    absent = sorted(required - entries.keys())
    if absent:
        raise SystemExit("Missing staged deliverables: " + json.dumps(absent))
    assert b"docs/recordings/numabe-train.gif" in git("show", ":README.md")
    print(json.dumps({"staged_changed_files": len(changed),
                      "largest_changed_blob_bytes": max(sizes.values()),
                      "saved_map_sha256": hashlib.sha256(map_bytes).hexdigest(),
                      "mesh_instances": sum(row["kind"] == "mesh" for row in records),
                      "all_map_meshes_in_index": True, "raw_surveys_in_index": 0,
                      "readme_gif_in_index": True, "required_shards": 0}, indent=2))


if __name__ == "__main__":
    main()
