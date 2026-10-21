#!/usr/bin/env python3
"""FL-4359: condition election changes on measured motion and probe origins.

This is attribution, not a perceptual shimmer verdict. It uses unique quarter
world-source matches, including their exclusions, and never treats an unmatched
sample or an ordinary projected boundary crossing as stable or defective.
"""
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path

import numpy as np

from fl4359_flat_colour_attribution import (
    ArchivedCapture, colour_changed, digest, elected_key, sample_key,
)
from fl4359_raster_jitter_capture import stream_frames


def project_cells(world, matrix, grid):
    """Godot exports matrices column-major; project into logical cell units."""
    clip = np.asarray(matrix).reshape(4, 4, order="F") @ np.r_[world, 1.0]
    if not np.isfinite(clip).all() or clip[3] <= 0:
        raise ValueError("world source is not projectable")
    return (clip[:2] / clip[3] * 0.5 + 0.5) * grid


def motion_band(displacement):
    return ("lt_0.25" if displacement < .25 else "0.25_to_0.5"
            if displacement < .5 else "0.5_to_1" if displacement < 1 else "ge_1")


def elected_origin(presented, state):
    if elected_key(presented) is None:
        return None
    role = presented["source"][0]
    if role not in (0, 1, 2) or not state["captured_origin_valid_mask"] & (1 << role):
        raise ValueError("elected source has no captured probe origin")
    return tuple(state["captured_origins_after_capture"][role])


def bank_frame(trace, rows):
    bank, counts = {}, Counter()
    viewport = trace["viewport"]
    for row in rows:
        world = row["probe_prepack_world_position"]
        if (not row["probe_prepack_valid"] or len(world) != 4 or world[3] <= 0
                or not np.isfinite(world).all()):
            counts["invalid_world_rows"] += 1
            continue
        cell, slot = row["screen_cell"], row["subcell_slot"]
        quarter = np.array([cell["x"] + (slot % 2 + .5) / 2,
                            cell["y"] + (slot // 2 + .5) / 2])
        if np.any(np.floor(quarter * viewport["cell_size"]) >= viewport["presentation_size"]):
            counts["outside_image_rows"] += 1
            continue
        p = row["presented_surface"]
        item = {"elected": elected_key(p), "glyph": p["head"][3],
                "origin": elected_origin(p, trace["perspective_probe_state"]),
                "source": p["source"], "cell": (cell["x"], cell["y"]),
                "fg": p["fg"][:3], "bg": p["bg"][:3]}
        bank.setdefault(sample_key(row), []).append(item)
        counts["admitted_world_rows"] += 1
    return bank, dict(counts)


def address_relation(a, b, size):
    """Face-local adjacency only; cross-face neighbours require a seam mapping."""
    if not all(0 <= address < 6 * size * size for address in (a, b)):
        raise ValueError("invalid cubemap address")
    fa, la = divmod(a, size * size)
    fb, lb = divmod(b, size * size)
    if fa != fb:
        return "different_face"
    ay, ax = divmod(la, size)
    by, bx = divmod(lb, size)
    distance = max(abs(ax - bx), abs(ay - by))
    return "same_address" if distance == 0 else "adjacent_same_face" if distance == 1 else "nonadjacent_same_face"


def election_detail(a, b, size, motion, quarter_world):
    ea, eb = a["elected"], b["elected"]
    ia, ib = ea[0], eb[0]
    surface = ("same_primitive" if ia == ib else "same_owner_object_material"
               if ia[:2] + ia[3:] == ib[:2] + ib[3:] else "different_owner_object_material")
    return {"motion_cells": motion, "quarter_world": quarter_world,
            "address_relation": address_relation(a["source"][1], b["source"][1], size),
            "surface_relation": surface, "source_changed": ea != eb,
            "glyph_changed": a["glyph"] != b["glyph"],
            "fg_changed": colour_changed(a["fg"], b["fg"]),
            "bg_changed": colour_changed(a["bg"], b["bg"]),
            "recorded_cell_changed": a["cell"] != b["cell"],
            "before": a, "after": b}


def compare_motion(before, after, trace_a, trace_b, *, include_details=False):
    counts, strata, motions, details = Counter(), {}, [], []
    for key in before.keys() & after.keys():
        if len(before[key]) != 1 or len(after[key]) != 1:
            counts["ambiguous_shared_keys_excluded"] += 1
            continue
        a, b = before[key][0], after[key][0]
        pa = project_cells(key[1], trace_a["world_to_clip_matrix"], trace_a["viewport"]["semantic_ansi_size"])
        pb = project_cells(key[1], trace_b["world_to_clip_matrix"], trace_b["viewport"]["semantic_ansi_size"])
        motion = float(np.max(np.abs(pb - pa)))
        motions.append(motion)
        anchored = a["elected"] is not None and b["elected"] is not None
        changed = anchored and a["elected"] != b["elected"]
        origin = "unanchored" if not anchored else "same_origin" if a["origin"] == b["origin"] else "changed_origin"
        values = {"matched": 1, "elected_changes": int(changed),
                  "glyph_changes": int(a["glyph"] != b["glyph"]),
                  "quarter_cell_changes": int(a["cell"] != b["cell"]),
                  "projected_centre_cell_changes": int(np.any(np.floor(pa) != np.floor(pb)))}
        counts.update(values)
        strata.setdefault(motion_band(motion) + "/" + origin, Counter()).update(values)
        if include_details and motion < .25 and origin == "same_origin":
            size = trace_a["perspective_probe_state"]["probe_size"]
            if size != trace_b["perspective_probe_state"]["probe_size"]:
                raise ValueError("capture density changed within run")
            details.append(election_detail(a, b, size, motion, key[1]))
    for name, bank in (("before", before), ("after", after)):
        counts[name + "_admitted_rows"] = sum(map(len, bank.values()))
        counts[name + "_unmeasured_rows"] = counts[name + "_admitted_rows"] - counts["matched"]
    result = {"counts": dict(counts), "strata": {k: dict(v) for k, v in sorted(strata.items())},
              "motion_max_axis_cell_quantiles_10_50_90_99": np.quantile(motions, [.1, .5, .9, .99]).tolist() if motions else []}
    if include_details:
        result["small_motion_same_origin_details"] = details
    return result


def diagnose(capture, details_path=None):
    columns = ("probe_prepack_valid", "probe_prepack_world_position", "probe_prepack_identity",
               "probe_prepack_revisions", "screen_cell", "subcell_slot", "presented_surface")
    previous, frames, pairs, total, strata = None, [], [], Counter(), {}
    for frame_id, trace, rows, raw_count in stream_frames(ArchivedCapture(capture), 5, columns=columns):
        if raw_count != trace["oracle_expected_subcells_per_frame"]:
            raise ValueError("incomplete frame")
        bank, counts = bank_frame(trace, rows)
        state = trace["perspective_probe_state"]
        frames.append({"frame_id": frame_id, "counts": counts,
                       "probe": {k: state[k] for k in ("active_mask", "captured_origins_after_capture", "capture_activity_by_probe")},
                       "transition": {k: v for k, v in state.items() if "transition" in k or "recapture_reason" in k}})
        if previous is not None:
            pair = compare_motion(previous[0], bank, previous[1], trace,
                                  include_details=details_path is not None)
            pairs.append(pair)
            total.update(pair["counts"])
            for key, values in pair["strata"].items():
                strata.setdefault(key, Counter()).update(values)
        previous = bank, trace
        print("motion attributed frame", frame_id, flush=True)
    if len(frames) != 5:
        raise ValueError("canonical comparison requires five frames")
    path = capture if capture.exists() else Path(str(capture) + ".gz")
    result = {"schema": "fl4359.election_motion_diagnostic.v1", "capture": str(path),
            "capture_file_sha256": digest(path), "acceptance_claimed": False,
            "scope": "Unique quarter world-position/identity/revision matches. Motion is max absolute projected cell displacement along either axis. Projection is of the source centre, not a prediction of finite-splat coverage or expected glyph. Elected origins come from presented_surface.source, not the quarter witness. Changes remain raw churn, not classified shimmer. Unmatched and ambiguous rows are unmeasured.",
            "frames": frames, "pairs": pairs, "totals": dict(total),
            "strata": {k: dict(v) for k, v in sorted(strata.items())}}
    if details_path is not None:
        groups = {}
        with gzip.open(details_path, "wt") as target:
            for pair_id, pair in enumerate(pairs):
                for row in pair.pop("small_motion_same_origin_details"):
                    row["pair"] = pair_id
                    target.write(json.dumps(row, separators=(",", ":")) + "\n")
                    group = (row["address_relation"] + "/" + row["surface_relation"])
                    values = {"matched": 1, **{k: int(row[k]) for k in (
                        "source_changed", "glyph_changed", "fg_changed", "bg_changed", "recorded_cell_changed")}}
                    groups.setdefault(group, Counter()).update(values)
        result["small_motion_election_details"] = {
            "path": str(details_path), "sha256": digest(details_path),
            "analyzer_sha256": digest(Path(__file__)),
            "scope": "Motion-limited, not motion-compensated. Same captured origin does not prove identical capture content. Adjacency alone does not establish overlap or a depth-test defect.",
            "groups": groups}
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--election-details", type=Path, help="Save actual FF small-motion source pairs as gzip JSONL.")
    args = parser.parse_args()
    args.out.write_text(json.dumps(diagnose(args.capture, args.election_details), indent=2) + "\n")
