#!/usr/bin/env python3
"""FL-4359: compare FF density experiments at the same cells and camera poses.

Different cubemap resolutions need not share exact world samples. This reports
raw temporal changes with camera motion held equal, plus structural ownership
disagreements. Neither metric is itself a perceptual shimmer acceptance gate.
"""
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path

from fl4359_flat_colour_attribution import ArchivedCapture, colour_changed, digest, elected_key
from fl4359_raster_jitter_capture import stream_frames


def extract(capture, output):
    columns = ("screen_cell", "subcell_slot", "presented_surface", "final_paint_glyph")
    frames = []
    for frame_id, trace, rows, raw_count in stream_frames(ArchivedCapture(capture), 5, columns=columns):
        if raw_count != trace["oracle_expected_subcells_per_frame"]:
            raise ValueError("incomplete quarter population")
        meta = trace["final_paint_field"]
        if meta["applied_policy"]["mode"] != "FF":
            raise ValueError("recorded cell-pair comparison requires observed FF mode")
        grid = meta["semantic_ansi_size"]
        cells = [None] * (grid[0] * grid[1])
        for row in rows:
            if row["subcell_slot"] != 0:
                continue
            cell, p = row["screen_cell"], row["presented_surface"]
            index = cell["y"] * grid[0] + cell["x"]
            if cells[index] is not None:
                raise ValueError("duplicate cell")
            elected = elected_key(p)
            identity = p["identity"]
            cells[index] = {"class": [identity[0], identity[1], identity[3]] if elected else None,
                            "elected": elected, "glyph": row["final_paint_glyph"],
                            "fg": p["fg"][:3], "bg": p["bg"][:3]}
        if any(c is None for c in cells):
            raise ValueError("missing cell")
        state = trace["perspective_probe_state"]
        frames.append({"frame": frame_id, "grid": grid, "cell_size": meta["cell_size"],
                       "matrix": trace["world_to_clip_matrix"], "probe_size": state["probe_size"],
                       "transition": state["stable_identity_transition_progress"], "cells": cells})
        print("extracted lattice frame", frame_id, flush=True)
    if len(frames) != 5:
        raise ValueError("five frames required")
    with gzip.open(output, "wt") as handle:
        for frame in frames:
            handle.write(json.dumps(frame, separators=(",", ":")) + "\n")
    path = capture if capture.exists() else Path(str(capture) + ".gz")
    output.with_suffix(output.suffix + ".json").write_text(json.dumps({
        "schema": "fl4359.lattice_samples.v1", "capture": str(path), "capture_sha256": digest(path),
        "samples_sha256": digest(output), "extractor_sha256": digest(Path(__file__)),
        "scope": "One visible top-left quarter per screen cell; FF b51 colour and final native glyph. Not world-source matched."}, indent=2) + "\n")


def read_samples(path):
    meta = json.loads(path.with_suffix(path.suffix + ".json").read_text())
    if digest(path) != meta["samples_sha256"]:
        raise ValueError("sample hash mismatch")
    with gzip.open(path, "rt") as handle:
        return [json.loads(line) for line in handle]


def compare_frames(before, after):
    if len(before) != 5 or len(after) != 5:
        raise ValueError("five frames required on each side")
    fidelity, temporal = [], []
    for index, (a, b) in enumerate(zip(before, after, strict=True)):
        if any(a[k] != b[k] for k in ("grid", "cell_size", "matrix")):
            raise ValueError("camera or cell lattice differs")
        counts, changes = Counter(), Counter()
        for ca, cb in zip(a["cells"], b["cells"], strict=True):
            counts["cells"] += 1
            counts["source_disagreements"] += ca["elected"] != cb["elected"]
            counts["glyph_disagreements"] += ca["glyph"] != cb["glyph"]
            for channel in ("fg", "bg"):
                counts[channel + "_disagreements"] += colour_changed(ca[channel], cb[channel])
            if ca["elected"] is not None and ca["elected"] == cb["elected"]:
                counts["exact_source_cells"] += 1
                for channel in ("fg", "bg"):
                    counts["exact_source_" + channel + "_inequalities"] += ca[channel] != cb[channel]
            if ca["class"] != cb["class"]:
                counts["structural_disagreements"] += 1
                counts["world_to_unanchored"] += ca["class"] is not None and cb["class"] is None
                counts["unanchored_to_world"] += ca["class"] is None and cb["class"] is not None
                changes[str(ca["class"]) + " -> " + str(cb["class"])] += 1
        fidelity.append({"counts": dict(counts), "changes_by_owner_object_material": dict(changes)})
        if index == 0:
            continue
        old, new, excluded = Counter(), Counter(), 0
        by_class = {}
        exact_by_class, exact_eligible = {}, 0
        for a0, a1, b0, b1 in zip(before[index-1]["cells"], a["cells"], after[index-1]["cells"], b["cells"], strict=True):
            if a0["class"] is None or not a0["class"] == a1["class"] == b0["class"] == b1["class"]:
                excluded += 1
                continue
            exact_sources = (a0["elected"] is not None and a1["elected"] is not None
                             and a0["elected"] == b0["elected"]
                             and a1["elected"] == b1["elected"])
            exact_eligible += exact_sources
            for counts, first, last in ((old, a0, a1), (new, b0, b1)):
                changes = {"eligible": 1,
                           "source_changes": int(first["elected"] != last["elected"]),
                           "painted_glyph_changes": int(first["glyph"] != last["glyph"]),
                           "fg_changes": int(colour_changed(first["fg"], last["fg"])),
                           "bg_changes": int(colour_changed(first["bg"], last["bg"]))}
                counts.update(changes)
                group = by_class.setdefault(str(first["class"]), {"before": Counter(), "after": Counter()})
                side = "before" if counts is old else "after"
                group[side].update(changes)
                if exact_sources:
                    exact_group = exact_by_class.setdefault(str(first["class"]), {"before": Counter(), "after": Counter()})
                    exact_group[side].update(changes)
        temporal.append({"before": dict(old), "after": dict(new), "excluded_cells": excluded,
                         "by_owner_object_material": by_class,
                         "same_elected_sources": {
                             "eligible": exact_eligible,
                             "excluded_cells": len(a["cells"]) - exact_eligible,
                             "by_owner_object_material": exact_by_class}})
    return {"schema": "fl4359.lattice_comparison.v1", "acceptance_claimed": False,
            "scope": "Aligned screen cells at identical recorded camera matrices. Temporal subset retains the same anchored owner/object/material across both poses and both runs. Camera motion still causes changes; no perceptual threshold or geometry acceptance is inferred. Structural disagreements expose sample/visibility differences and must be inspected before any default change.",
            "same_elected_sources_scope": "Additional subset requires identical non-null elected identity/world/revision at each pose across runs. Sources may still change between poses. Exclusions include every other screen-cell pair; this is not motion compensation or a shimmer verdict.",
            "probe_sizes": [before[0]["probe_size"], after[0]["probe_size"]],
            "transition_sequences": [[f["transition"] for f in frames] for frames in (before, after)],
            "transition_sequences_match": [f["transition"] for f in before] == [f["transition"] for f in after],
            "fidelity": fidelity, "temporal": temporal}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("extract", "compare"))
    parser.add_argument("source", type=Path)
    parser.add_argument("--after", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.action == "extract":
        extract(args.source, args.out)
    else:
        if args.after is None:
            parser.error("compare requires --after")
        result = compare_frames(read_samples(args.source), read_samples(args.after))
        result["comparator_sha256"] = digest(Path(__file__))
        result["sources"] = [{"path": str(p), "sha256": digest(p)} for p in (args.source, args.after)]
        args.out.write_text(json.dumps(result, indent=2) + "\n")
