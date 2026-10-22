#!/usr/bin/env python3
"""FL-4359: inspect recorded splat footprints behind small-motion FF swaps.

This reuses captured production geometry. It does not change a visibility gate
or infer that an ordinary boundary crossing is perceptual shimmer.
"""
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path

import numpy as np

from fl4359_actual_field_stability import quad_residual_px
from fl4359_flat_colour_attribution import ArchivedCapture, digest
from fl4359_raster_jitter_capture import stream_frames


def key(item):
    return tuple(item["origin"]), item["source"][1]


def triangle_depth(clips, point, extent):
    clips = np.asarray(clips)
    ndc = clips[:, :3] / clips[:, 3, None]
    xy = (ndc[:, :2] * .5 + .5) * extent
    for ids in ((0, 1, 3), (0, 3, 2)):
        a, b, c = xy[list(ids)]
        try:
            uv = np.linalg.solve(np.column_stack((b-a, c-a)), point-a)
        except np.linalg.LinAlgError:
            continue
        if min(uv) >= 0 and sum(uv) <= 1:
            weights = np.r_[1-sum(uv), uv]
            return float(weights @ ndc[list(ids), 2])
    return None


def classify(row, ga, gb, viewport):
    a, b = row["before"], row["after"]
    if 0 in (a["source"][0], b["source"][0]):
        return {"kind": "eye_excluded"}
    span, extent = np.asarray(viewport["cell_size"]), np.asarray(viewport["target_size"])
    # The finite projectile/weather exception can choose an off-centre pixel.
    if any(i["elected"][0][0] == 3 and i["elected"][0][3] == 205 for i in (a, b)):
        return {"kind": "offcentre_effect_excluded"}
    pa = np.asarray(a["cell"]) * span + span // 2 + .5
    pb = np.asarray(b["cell"]) * span + span // 2 + .5
    aa, ab, ba, bb = ga.get(key(a)), gb.get(key(a)), ga.get(key(b)), gb.get(key(b))
    if any(g is None for g in (aa, ab, ba, bb)):
        return {"kind": "missing_or_ambiguous_quad", "available": [g is not None for g in (aa, ab, ba, bb)]}
    if any(g["identity"] != item["elected"][0] for g, item in ((aa, a), (ab, a), (ba, b), (bb, b))):
        return {"kind": "capture_identity_changed"}
    own = [quad_residual_px(pa, aa["clip"], extent), quad_residual_px(pb, bb["clip"], extent)]
    if any(r is None or r > 0 for r in own):
        return {"kind": "own_quad_precision_or_join_gap", "residual_px": own}
    old_at_new = quad_residual_px(pb, ab["clip"], extent)
    new_at_old = quad_residual_px(pa, ba["clip"], extent)
    if old_at_new is None or new_at_old is None:
        return {"kind": "unprojectable_quad"}
    result = {"old_at_new_residual_px": old_at_new, "new_at_old_residual_px": new_at_old,
              "world_quad_changed": aa["world"] != ab["world"] or ba["world"] != bb["world"],
              "world_centres_changed": aa["centre"] != ab["centre"] or ba["centre"] != bb["centre"]}
    if old_at_new > 0 or new_at_old > 0:
        result["kind"] = "boundary_entry_or_exit"
    else:
        result["kind"] = "overlap_at_both_poses"
        # Forward depth: negative A-B predicts A nearer. Preserve raw deltas;
        # float64 replay is not a hardware depth-order acceptance test.
        za = [triangle_depth(g["clip"], pa, extent) for g in (aa, ba)]
        zb = [triangle_depth(g["clip"], pb, extent) for g in (ab, bb)]
        result["depth_a_minus_b"] = [None if None in zs else zs[0]-zs[1] for zs in (za, zb)]
    return result


def diagnose(capture, details):
    with gzip.open(details, "rt") as source:
        cases = [json.loads(line) for line in source if line.strip()]
    cases = [r for r in cases if r["source_changed"]]
    targets = [set() for _ in range(5)]
    for row in cases:
        for frame in (row["pair"], row["pair"]+1):
            targets[frame].update((key(row["before"]), key(row["after"])))
    columns = ("probe_source_capture_valid", "probe_source_capture_probe", "probe_source_capture_origin",
               "probe_source_capture_texel", "probe_source_capture_identity", "probe_reconstructed_clip_valid",
               "probe_reconstructed_clip_corners", "probe_reconstructed_world_corners", "probe_reconstructed_center")
    banks, traces = [], []
    for frame, trace, rows, raw_count in stream_frames(ArchivedCapture(capture), 5, columns=columns):
        if raw_count != trace["oracle_expected_subcells_per_frame"]:
            raise ValueError("incomplete frame")
        if trace["final_paint_field"]["applied_policy"]["mode"] != "FF":
            raise ValueError("this diagnostic requires captured FF presentation")
        bank = {}
        for row in rows:
            if not row["probe_source_capture_valid"] or row["probe_source_capture_probe"] == 0 or not row["probe_reconstructed_clip_valid"]:
                continue
            k = tuple(row["probe_source_capture_origin"][:3]), row["probe_source_capture_texel"]
            if k not in targets[frame]:
                continue
            identity = row["probe_source_capture_identity"]
            g = {"identity": [identity[n] for n in ("owner_kind", "object_id", "primitive_id", "material")],
                 "clip": row["probe_reconstructed_clip_corners"], "world": row["probe_reconstructed_world_corners"],
                 "centre": row["probe_reconstructed_center"]}
            if k in bank and bank[k] != g:
                bank[k] = None
            else:
                bank[k] = g
        banks.append(bank)
        traces.append(trace)
        print("indexed production quad frame", frame, flush=True)
    if len(banks) != 5:
        raise ValueError("five recorded poses required")
    counts, groups, results = Counter(), {}, []
    for row in cases:
        pair = row["pair"]
        result = classify(row, banks[pair], banks[pair+1], traces[pair]["viewport"])
        counts[result["kind"]] += 1
        group = row["address_relation"] + "/" + row["surface_relation"]
        groups.setdefault(group, Counter())[result["kind"]] += 1
        results.append({"pair": pair, "before": row["before"], "after": row["after"], **result})
    return {"schema": "fl4359.election_footprints.v1", "acceptance_claimed": False,
            "capture_sha256": digest(capture), "details_sha256": digest(details), "analyzer_sha256": digest(Path(__file__)),
            "scope": "Changed elected sources in the motion-limited same-origin FF cohort. Both candidate quads must have same-frame recorded grid-probe geometry at both poses; missing/ambiguous and own-footprint residuals remain excluded. This is boundary/depth attribution, not perceptual acceptance.",
            "counts": counts, "groups": groups, "cases": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--details", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.write_text(json.dumps(diagnose(args.capture, args.details), indent=2) + "\n")
