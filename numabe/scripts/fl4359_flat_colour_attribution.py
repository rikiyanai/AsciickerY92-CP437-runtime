#!/usr/bin/env python3
"""FL-4359: separate retained pixel colour from elected cell colour on saved frames.

This is a sampled attribution diagnostic, not a whole-image shimmer score.
The CC capture also contains b51 cell pairs, so its flat counterfactual uses
the exact same run and world-source comparisons as its observed pixel field.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import gzip
import hashlib
import json
from pathlib import Path
import shutil

import numpy as np

from fl4359_actual_field import ActualFieldReader, ActualFieldError
from fl4359_actual_field_stability import COLUMNS, THRESHOLD, quarter_pixel
from fl4359_raster_jitter_capture import stream_frames


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


class ArchivedCapture:
    def __init__(self, path):
        self.path = path

    def open(self, mode):
        return self.path.open(mode) if self.path.exists() else gzip.open(str(self.path) + ".gz", mode)


@contextmanager
def restored_field(capture, metadata):
    """Restore only this frame; the canonical reader verifies its original hash."""
    path = Path(metadata["raw_path"])
    if not path.is_absolute():
        path = capture.parent / path
    if not path.resolve().is_relative_to(capture.parent.resolve()):
        raise ActualFieldError("field restoration must stay within its capture directory")
    created = not path.exists()
    try:
        if created:
            with gzip.open(str(path) + ".gz", "rb") as source, path.open("xb") as target:
                shutil.copyfileobj(source, target, 4 * 1024 * 1024)
        yield
    finally:
        if created:
            path.unlink(missing_ok=True)


def sample_key(row):
    return (tuple(sorted(row["probe_prepack_identity"].items())),
            tuple(row["probe_prepack_world_position"][:3]),
            tuple(row["probe_prepack_revisions"][:3]))


def elected_key(presented):
    if presented.get("state", [])[:2] != [1, 1] or presented.get("world", [0] * 4)[3] <= 0:
        return None
    return (tuple(presented["identity"]), tuple(presented["world"][:3]),
            tuple(presented["revisions"][:3]))


def colour_changed(a, b):
    return bool(np.max(np.abs(np.asarray(a) - np.asarray(b))) > THRESHOLD)


def compare(before, after):
    counts = Counter()
    strata = {}
    for key in before.keys() & after.keys():
        if len(before[key]) != 1 or len(after[key]) != 1:
            counts["ambiguous_excluded"] += 1
            continue
        a, b = before[key][0], after[key][0]
        owner = ("unanchored_elected_source" if a["elected"] is None or b["elected"] is None
                 else "same_elected_source" if a["elected"] == b["elected"]
                 else "different_elected_source")
        probe = "eye_in_pair" if 0 in (a["probe"], b["probe"]) else "grid_probes_only"
        group = strata.setdefault(owner + "/" + probe, Counter())
        for target in (counts, group):
            target["matched"] += 1
            target["elected_glyph_changes"] += a["glyph"] != b["glyph"]
            if "painted_glyph" in a and "painted_glyph" in b:
                target["painted_glyph_changes"] += a["painted_glyph"] != b["painted_glyph"]
            for ch in ("fg", "bg"):
                flat_change = colour_changed(a["flat_" + ch], b["flat_" + ch])
                observed_change = colour_changed(a[ch], b[ch])
                target["flat_" + ch + "_changes"] += flat_change
                target["observed_" + ch + "_changes"] += observed_change
                target["flat_" + ch + "_changes_with_observed_stable"] += flat_change and not observed_change
    return {"counts": dict(counts), "strata": {k: dict(v) for k, v in strata.items()},
            "before_unique_sources": len(before), "after_unique_sources": len(after),
            "coverage": {
                "before_admitted_rows": sum(map(len, before.values())),
                "after_admitted_rows": sum(map(len, after.values())),
                "matched_rows_each_frame": counts["matched"],
                "before_unmeasured_rows": sum(map(len, before.values())) - counts["matched"],
                "after_unmeasured_rows": sum(map(len, after.values())) - counts["matched"],
            }}


def summarize_pairs(pairs):
    total, grouped = Counter(), {}
    for pair in pairs:
        total.update(pair["counts"])
        for key, value in pair["strata"].items():
            grouped.setdefault(key, Counter()).update(value)
    return {"totals": dict(total), "strata": {k: dict(v) for k, v in grouped.items()}}


def freeze(value):
    return tuple(freeze(v) for v in value) if isinstance(value, list) else value


def replay_samples(result_path):
    result = json.loads(result_path.read_text())
    path = result_path.parent / result["samples_file"]
    if digest(path) != result["samples_sha256"]:
        raise ActualFieldError("compact sample hash mismatch")
    previous, pairs = None, []
    with gzip.open(path, "rt") as source:
        for line in source:
            frame = json.loads(line)
            bank = {}
            for key, values in frame["bank"]:
                for value in values:
                    value["elected"] = freeze(value["elected"])
                bank[freeze(key)] = values
            if previous is not None:
                pairs.append(compare(previous, bank))
            previous = bank
    if len(pairs) != 4:
        raise ActualFieldError("compact sample replay requires five frames")
    observed = summarize_pairs(pairs)
    if any(observed[k] != result[k] for k in ("totals", "strata")):
        raise ActualFieldError("compact sample replay disagrees with reported attribution")
    return observed


def review_samples(result_path):
    """Report the denominator beside colour, election and glyph changes."""
    replay_samples(result_path)
    result = json.loads(result_path.read_text())
    previous, pairs = None, []
    with gzip.open(result_path.parent / result["samples_file"], "rt") as source:
        for line in source:
            frame = json.loads(line)
            bank = {}
            for key, values in frame["bank"]:
                for value in values:
                    value["elected"] = freeze(value["elected"])
                bank[freeze(key)] = values
            if previous is not None:
                pair = compare(previous, bank)
                n = pair["counts"].get("matched", 0)
                pair["source_switches"] = sum(
                    v["matched"] for k, v in pair["strata"].items()
                    if k.startswith("different_elected_source/"))
                pair["rates"] = {
                    name: value / n if n else None for name, value in {
                        "fg_change": pair["counts"].get("observed_fg_changes", 0),
                        "bg_change": pair["counts"].get("observed_bg_changes", 0),
                        "source_switch": pair["source_switches"],
                        "glyph_change": pair["counts"].get("elected_glyph_changes", 0),
                    }.items()}
                pairs.append(pair)
            previous = bank
    return {"schema": "fl4359.attribution_review.v1", "source": str(result_path),
            "source_sha256": digest(result_path), "samples_sha256": result["samples_sha256"],
            "mode": result["mode"], "acceptance_claimed": False,
            "scope": "Uniquely matched quarter samples only. Source/glyph changes are raw election churn, not automatically perceptual jitter. Unmatched rows are unmeasured, not stable.",
            "pairs": pairs, **summarize_pairs(pairs)}


def projected_extent(clip_corners, target_size, cell_size):
    """Projected bounding extents, not coverage, mip size or a stability gate."""
    clips = np.asarray(clip_corners, dtype=float)
    target, cell = np.asarray(target_size, dtype=float), np.asarray(cell_size, dtype=float)
    if (clips.shape != (4, 4) or target.shape != (2,) or cell.shape != (2,)
            or not np.all(np.isfinite(clips)) or np.any(clips[:, 3] <= 0)
            or not np.all(np.isfinite(target)) or not np.all(np.isfinite(cell))
            or np.any(target <= 0) or np.any(cell <= 0)):
        raise ActualFieldError("invalid projected footprint")
    pixels = (clips[:, :2] / clips[:, 3, None] * 0.5 + 0.5) * target
    extent = np.ptp(pixels, axis=0)
    return extent / cell


def capture_texel_clip_corners(face, texel, size, origin, depth, matrix):
    """Unexpanded cubemap texel at its recorded face depth, using the splat law.

    Cube directions have major component one, so normalization cancels against
    division by that component. Corners use half a texel, without overlap pads.
    """
    if not 0 <= face < 6 or size <= 0 or texel // (size * size) != face:
        raise ActualFieldError("invalid capture texel address")
    local = texel % (size * size)
    x, y = local % size, local // size
    q = (np.asarray([[x, y], [x + 1, y], [x, y + 1], [x + 1, y + 1]]) / size) * 2 - 1
    a, b, one = q[:, 0], q[:, 1], np.ones(4)
    directions = ((one, -b, -a), (-one, -b, a), (a, one, b),
                  (a, -one, -b), (a, -b, one), (-a, -b, -one))
    world = np.asarray(directions[face]).T * depth + np.asarray(origin)
    homogeneous = np.column_stack((world, one))
    return homogeneous @ np.asarray(matrix).reshape(4, 4)


def projected_scale(capture):
    """Read saved reconstruction witnesses; no colour readback or new flight."""
    columns = ("screen_cell", "subcell_slot", "probe_prepack_valid",
               "probe_prepack_identity", "probe_prepack_world_position",
               "probe_source_capture_probe", "probe_source_capture_texel",
               "probe_source_capture_generation", "probe_reconstructed_clip_valid",
               "probe_reconstructed_clip_corners", "probe_source_capture_face",
               "probe_source_capture_origin", "probe_source_capture_normal_depth")
    frames, all_extents, all_core_extents = [], [], []
    for frame_id, trace, rows, raw_count in stream_frames(ArchivedCapture(capture), 5, columns=columns):
        if raw_count != trace["oracle_expected_subcells_per_frame"]:
            raise ActualFieldError("incomplete quarter population")
        viewport = trace["viewport"]
        cell, target = viewport["cell_size"], viewport["target_size"]
        presentation = viewport["presentation_size"]
        extents, core_extents, counts, sources = [], [], Counter(), set()
        for row in rows:
            world = row["probe_prepack_world_position"]
            if (not row["probe_prepack_valid"] or len(world) != 4 or world[3] <= 0
                    or not np.all(np.isfinite(world))):
                counts["invalid_world_rows"] += 1
                continue
            x = int((row["screen_cell"]["x"] + (row["subcell_slot"] % 2 + 0.5) / 2) * cell[0])
            y = int((row["screen_cell"]["y"] + (row["subcell_slot"] // 2 + 0.5) / 2) * cell[1])
            if x >= presentation[0] or y >= presentation[1]:
                counts["outside_image_rows"] += 1
                continue
            counts["admitted_world_rows"] += 1
            if not row["probe_reconstructed_clip_valid"]:
                counts["invalid_projected_rows"] += 1
                continue
            try:
                extent = projected_extent(row["probe_reconstructed_clip_corners"], target, cell)
            except (ActualFieldError, ValueError, TypeError):
                counts["invalid_projected_rows"] += 1
                continue
            counts["measured_rows"] += 1
            extents.append(extent)
            core = capture_texel_clip_corners(
                row["probe_source_capture_face"], row["probe_source_capture_texel"],
                trace["perspective_probe_state"]["probe_size"], row["probe_source_capture_origin"],
                row["probe_source_capture_normal_depth"][3], trace["world_to_clip_matrix"])
            core_extents.append(projected_extent(core, target, cell))
            sources.add((row["probe_source_capture_probe"], row["probe_source_capture_generation"],
                         row["probe_source_capture_texel"]))
        all_extents.extend(extents)
        all_core_extents.extend(core_extents)
        frames.append({"frame_id": frame_id, "cell_size": cell, "target_size": target,
                       "presentation_size": presentation, "counts": dict(counts),
                       "distinct_visible_source_addresses": len(sources),
                       "extent_summary": extent_summary(extents),
                       "unexpanded_texel_extent_summary": extent_summary(core_extents)})
        print("projected scale frame", frame_id, dict(counts), flush=True)
    if len(frames) != 5:
        raise ActualFieldError("projected scale requires five frames")
    actual_path = capture if capture.exists() else Path(str(capture) + ".gz")
    return {"schema": "fl4359.projected_splat_scale.v1", "capture": str(actual_path),
            "capture_file_sha256": digest(actual_path), "acceptance_claimed": False,
            "scope": "Bounding extents at admitted quarter samples, weighted by visible rows. extent_summary uses recorded reconstructed splat corners including overlap/edge expansion. unexpanded_texel_extent_summary reconstructs half-texel corners from captured origin/face/address/depth and recorded camera matrix, without pads. Neither measures all retained texels, polygon containment or temporal stability. Extents below one cell on either axis prove the bounding box cannot contain a complete cell; extents above one do not prove containment. Tall profile is not measured by rescaling this square capture.",
            "frames": frames, "extent_summary": extent_summary(all_extents),
            "unexpanded_texel_extent_summary": extent_summary(all_core_extents)}


def extent_summary(extents):
    if not len(extents):
        return {"samples": 0}
    values = np.asarray(extents)
    return {"samples": len(values), "quantile_levels": [0, 0.1, 0.5, 0.9, 1],
            "width_in_cells": np.quantile(values[:, 0], [0, .1, .5, .9, 1]).tolist(),
            "height_in_cells": np.quantile(values[:, 1], [0, .1, .5, .9, 1]).tolist(),
            "bbox_smaller_than_cell_on_either_axis": int(np.sum(np.any(values < 1, axis=1))),
            "bbox_smaller_than_cell_on_both_axes": int(np.sum(np.all(values < 1, axis=1)))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", nargs="?", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--replay", type=Path, help="Verify an attribution JSON from its compact source samples.")
    parser.add_argument("--review", type=Path, help="Report coverage and colour/source/glyph churn together from saved samples.")
    parser.add_argument("--projected-scale", action="store_true", help="Measure recorded splat extents relative to actual cells; no native colour readback.")
    args = parser.parse_args()
    if args.review:
        reviewed = review_samples(args.review)
        if args.out:
            args.out.write_text(json.dumps(reviewed, indent=2) + "\n")
        else:
            print(json.dumps(reviewed, indent=2))
        return
    if args.replay:
        print(json.dumps(replay_samples(args.replay), indent=2))
        return
    if args.capture is None or args.out is None:
        parser.error("capture and --out are required unless --replay is used")
    if args.projected_scale:
        args.out.write_text(json.dumps(projected_scale(args.capture), indent=2) + "\n")
        return
    if args.capture.exists():
        capture_sha = digest(args.capture)
    else:
        archive = args.capture.parent / "raw_archive_manifest.json"
        manifest = json.loads(archive.read_text())
        entry = next(x for x in manifest["files"] if Path(x["original_path"]).name == args.capture.name)
        # Relocation does not change the archive's captured-byte identity.
        if digest(Path(str(args.capture) + ".gz")) != entry["compressed_sha256"]:
            raise ActualFieldError("compressed capture hash mismatch")
        capture_sha = entry["original_sha256"]
    reader = ActualFieldReader(args.capture)
    previous = None
    frames, pairs = [], []
    samples_path = args.out.with_suffix(".samples.jsonl.gz")
    samples_partial = samples_path.with_suffix(".partial")
    samples_out = gzip.open(samples_partial, "wt")
    for index, (frame_id, trace, rows, raw_count) in enumerate(stream_frames(
            ArchivedCapture(args.capture), 5, columns=(*COLUMNS, "probe_source_capture_probe", "final_paint_glyph"))):
        if raw_count != trace["oracle_expected_subcells_per_frame"]:
            raise ActualFieldError("incomplete quarter population")
        with restored_field(args.capture, trace["final_paint_field"]):
            field = reader.read(trace, frame_id=frame_id, sequence_index=index)
        if field.policy["mode"] not in ("FF", "FC", "CF", "CC"):
            raise ActualFieldError("unknown presentation mode")
        bank, counts, owner_samples = {}, Counter(), {}
        for row in rows:
            world = row["probe_prepack_world_position"]
            if not row["probe_prepack_valid"] or len(world) != 4 or world[3] <= 0 or not np.all(np.isfinite(world)):
                counts["invalid_world_rows"] += 1
                continue
            point = quarter_pixel(row, field.metadata).astype(int)
            x, y = int(point[0]), int(point[1])
            if x >= field.width or y >= field.height:
                counts["outside_image_rows"] += 1
                continue
            p = row["presented_surface"]
            if len(p.get("head", [])) != 4 or not p["head"][0] & 1:
                raise ActualFieldError("flat counterfactual lacks an admitted b51 record")
            item = {"fg": field.fields["fg_rgb"][y, x].tolist(),
                    "bg": field.fields["bg_rgb"][y, x].tolist(),
                    "flat_fg": p["fg"][:3], "flat_bg": p["bg"][:3],
                    "elected": elected_key(p), "glyph": p["head"][3],
                    "painted_glyph": row["final_paint_glyph"],
                    "probe": row["probe_source_capture_probe"]}
            bank.setdefault(sample_key(row), []).append(item)
            counts["admitted_world_rows"] += 1
            if field.policy["mode"] == "FF":
                for ch in ("fg", "bg"):
                    if not np.allclose(item[ch], item["flat_" + ch], rtol=0, atol=1e-7):
                        raise ActualFieldError("FF observed field does not equal recorded b51 " + ch)
                    counts["flat_" + ch + "_field_matches"] += 1
            cell = tuple(row["screen_cell"][k] for k in ("x", "y"))
            if item["elected"] is not None:
                owner_samples[cell] = (item["elected"], item["glyph"], item["flat_fg"], item["flat_bg"])
        frames.append({"frame_id": frame_id, "mode": field.policy["mode"],
                       "field_sha256": field.metadata["raw_sha256"], "counts": dict(counts),
                       "distinct_quarter_sources": len(bank), "anchored_elected_cells": len(owner_samples)})
        samples_out.write(json.dumps({"frame": frames[-1], "bank": list(bank.items())}, separators=(",", ":")) + "\n")
        if previous is not None:
            pairs.append({"before": frame_id - 1, "after": frame_id, **compare(previous, bank)})
        previous = bank
        del field
        print("attributed frame", frame_id, dict(counts), flush=True)
    samples_out.close()
    if len(frames) != 5:
        raise ActualFieldError("attribution requires five frames")
    samples_partial.replace(samples_path)
    result = {"schema": "fl4359.flat_colour_attribution.v1", "capture": str(args.capture),
              "capture_sha256": capture_sha, "mode": reader.identity["policy"]["mode"],
              "samples_file": samples_path.name, "samples_sha256": digest(samples_path),
              "threshold": THRESHOLD, "acceptance_claimed": False,
              "scope": "Matched quarter-world sources, partitioned by independently elected cell-colour source. CC b51 counterfactual is same-run recorded cell colour, not a rendered FF frame. Unmatched and ambiguous sources excluded; no all-texel or whole-image claim.",
              "frames": frames, "pairs": pairs, **summarize_pairs(pairs)}
    args.out.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
