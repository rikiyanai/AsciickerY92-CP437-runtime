#!/usr/bin/env python3
"""Deterministically group reviewed crops before semantic/agent review.

This stage performs local, model-free visual similarity grouping.  A group is a
review convenience, never a semantic classification, physical-object merge, or
World IR promotion.  Every source annotation remains a distinct member.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter, ImageOps


INPUT_SCHEMA = "y92.capture_review.v1"
SCHEMA = "y92.capture_similarity_groups.v1"
ALGORITHM_ID = "y92.classical_crop_similarity.v1"
VISION_SCHEMA = "y92.capture_semantic_neighbors.v1"
LABEL_TRIAGE_SCHEMA = "y92.manual_label_population_triage.v1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
VISION_CLASS_MAP = {
    "crosswalk": "crosswalk",
    "traffic_light": "traffic_signal",
    "storefront": "shopfront",
    "window": "window",
    "door": "door",
    "fence": "fence",
    "tree": "tree",
    "shrub": "hedge",
    "bush": "hedge",
    "bicycle": "bicycle",
    "bench": "bench",
    "vending_machine": "vending_machine",
}
VISION_CLASS_MIN_CONFIDENCE = {
    "crosswalk": 0.50,
    "traffic_signal": 0.40,
    "shopfront": 0.12,
    "window": 0.40,
    "door": 0.35,
    "fence": 0.25,
    "tree": 0.35,
    "hedge": 0.25,
    "bicycle": 0.35,
    "bench": 0.30,
    "vending_machine": 0.30,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved)


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def _normalized_histogram(values: list[int], bins: int, value_range: int = 256) -> list[float]:
    counts = [0] * bins
    for value in values:
        counts[min(bins - 1, value * bins // value_range)] += 1
    total = float(max(1, len(values)))
    return [count / total for count in counts]


def _dhash(gray: Image.Image) -> int:
    pixels = list(gray.resize((9, 8), Image.Resampling.BILINEAR).get_flattened_data())
    result = 0
    for y in range(8):
        for x in range(8):
            result = (result << 1) | int(pixels[y * 9 + x] > pixels[y * 9 + x + 1])
    return result


def _descriptor(path: Path) -> dict[str, Any]:
    with Image.open(path) as source:
        rgb = source.convert("RGB")
        width, height = rgb.size
        normalized = ImageOps.fit(rgb, (32, 32), method=Image.Resampling.LANCZOS)
    channels = normalized.split()
    color_hist = []
    for channel in channels:
        color_hist.extend(_normalized_histogram(list(channel.get_flattened_data()), 8))
    gray = normalized.convert("L")
    gray_pixels = list(gray.get_flattened_data())
    gray_hist = _normalized_histogram(gray_pixels, 16)
    thumb = [value / 255.0 for value in gray.resize((8, 8), Image.Resampling.BILINEAR).get_flattened_data()]
    edges = gray.filter(ImageFilter.FIND_EDGES)
    edge_thumb = [value / 255.0 for value in edges.resize((8, 8), Image.Resampling.BILINEAR).get_flattened_data()]
    return {
        "color_hist": color_hist,
        "gray_hist": gray_hist,
        "thumb": thumb,
        "edge_thumb": edge_thumb,
        "dhash": _dhash(gray),
        "log_aspect": math.log(max(width, 1) / max(height, 1)),
    }


def _mean_abs(left: list[float], right: list[float]) -> float:
    return sum(abs(a - b) for a, b in zip(left, right)) / max(1, len(left))


def descriptor_distance(left: dict[str, Any], right: dict[str, Any]) -> float:
    """Return a stable 0..1-ish appearance distance for conservative grouping."""
    color = sum(abs(a - b) for a, b in zip(left["color_hist"], right["color_hist"])) / 6.0
    gray = sum(abs(a - b) for a, b in zip(left["gray_hist"], right["gray_hist"])) / 2.0
    thumb = _mean_abs(left["thumb"], right["thumb"])
    edge = _mean_abs(left["edge_thumb"], right["edge_thumb"])
    hamming = (left["dhash"] ^ right["dhash"]).bit_count() / 64.0
    aspect = min(1.0, abs(left["log_aspect"] - right["log_aspect"]) / 1.5)
    return round(
        0.24 * color + 0.12 * gray + 0.22 * thumb
        + 0.17 * edge + 0.16 * hamming + 0.09 * aspect,
        8,
    )


def _resolve_crop(annotations_path: Path, row: dict[str, Any]) -> Path:
    crop = row.get("crop")
    if not isinstance(crop, dict):
        raise ValueError(f"annotation {row.get('annotation_id')} has no crop record")
    raw = crop.get("path")
    expected = crop.get("sha256")
    if not isinstance(raw, str) or not raw or not isinstance(expected, str):
        raise ValueError(f"annotation {row.get('annotation_id')} has an invalid crop record")
    candidate = Path(raw)
    path = candidate.resolve() if candidate.is_absolute() else (annotations_path.parent / candidate).resolve()
    if not path.is_file():
        raise ValueError(f"annotation {row.get('annotation_id')} crop is absent: {path}")
    actual = sha256(path)
    if actual != expected:
        raise ValueError(f"annotation {row.get('annotation_id')} crop digest mismatch")
    return path


def resolve_crop_paths(annotations_path: Path) -> dict[str, Path]:
    annotations_path = annotations_path.resolve()
    source = json.loads(annotations_path.read_text(encoding="utf-8"))
    if source.get("schema") != INPUT_SCHEMA:
        raise ValueError(f"annotations are not {INPUT_SCHEMA}")
    result = {}
    for row in source.get("annotations") or []:
        annotation_id = row.get("annotation_id")
        if not isinstance(annotation_id, str) or not annotation_id or annotation_id in result:
            raise ValueError("annotation IDs must be non-empty and unique")
        result[annotation_id] = _resolve_crop(annotations_path, row)
    if not result:
        raise ValueError("capture review has no annotations")
    return result


def _complete_link_groups(
    ids: list[str], distances: dict[tuple[str, str], float], threshold: float,
) -> list[list[str]]:
    """Greedy complete-link clustering with deterministic seed/member ordering."""
    remaining = set(ids)
    groups: list[list[str]] = []
    for seed in ids:
        if seed not in remaining:
            continue
        group = [seed]
        remaining.remove(seed)
        candidates = sorted(
            remaining,
            key=lambda item: (distances[tuple(sorted((seed, item)))], item),
        )
        for candidate in candidates:
            if candidate not in remaining:
                continue
            if all(
                distances[tuple(sorted((candidate, member)))] <= threshold
                for member in group
            ):
                group.append(candidate)
                remaining.remove(candidate)
        groups.append(sorted(group))
    return groups


def build_apple_vision_neighbors(
    crop_paths: dict[str, Path], out_path: Path, *, neighbor_count: int = 16,
) -> dict[str, Any]:
    """Run Apple's on-device feature print. No network request or cloud model is used."""
    if platform.system() != "Darwin":
        raise RuntimeError("Apple Vision grouping is available only on macOS")
    helper = Path(__file__).with_name("capture_review_vision_features.swift")
    if not helper.is_file():
        raise RuntimeError(f"Apple Vision helper is absent: {helper}")
    input_rows = [
        {"annotation_id": annotation_id, "path": str(path)}
        for annotation_id, path in sorted(crop_paths.items())
    ]
    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as stream:
        json.dump(input_rows, stream, ensure_ascii=False, sort_keys=True)
        stream.flush()
        subprocess.run(
            ["swift", str(helper), stream.name, str(out_path), str(neighbor_count)],
            check=True,
        )
    return json.loads(out_path.read_text(encoding="utf-8"))


def _semantic_distances(
    document: dict[str, Any], annotation_ids: set[str],
) -> dict[tuple[str, str], float]:
    if document.get("schema") != VISION_SCHEMA:
        raise ValueError(f"semantic neighbors are not {VISION_SCHEMA}")
    observations = document.get("observations") or []
    if {row.get("annotation_id") for row in observations} != annotation_ids:
        raise ValueError("semantic neighbors do not cover the annotation set")
    distances: dict[tuple[str, str], float] = {}
    for row in observations:
        left = row["annotation_id"]
        for neighbor in row.get("neighbors") or []:
            right = neighbor.get("annotation_id")
            distance = neighbor.get("distance")
            if right not in annotation_ids or right == left or not isinstance(distance, (int, float)):
                raise ValueError(f"invalid semantic neighbor for {left}")
            key = tuple(sorted((left, right)))
            if key in distances and abs(distances[key] - float(distance)) > 1e-5:
                raise ValueError(f"asymmetric semantic distance for {key}")
            distances[key] = float(distance)
    return distances


def _complete_link_sparse_groups(
    ids: list[str], distances: dict[tuple[str, str], float], threshold: float,
) -> list[list[str]]:
    remaining = set(ids)
    groups = []
    for seed in ids:
        if seed not in remaining:
            continue
        group = [seed]
        remaining.remove(seed)
        candidates = sorted(
            (
                (distance, other)
                for pair, distance in distances.items()
                if seed in pair
                for other in pair
                if other != seed and other in remaining and distance <= threshold
            ),
            key=lambda item: (item[0], item[1]),
        )
        for _, candidate in candidates:
            if candidate not in remaining:
                continue
            pair_distances = [
                distances.get(tuple(sorted((candidate, member)))) for member in group
            ]
            if all(distance is not None and distance <= threshold for distance in pair_distances):
                group.append(candidate)
                remaining.remove(candidate)
        groups.append(sorted(group))
    return groups


def build_document(
    annotations_path: Path,
    *,
    threshold: float = 0.16,
    semantic_neighbors_path: Path | None = None,
    semantic_threshold: float = 0.62,
    label_triage_path: Path | None = None,
) -> dict[str, Any]:
    annotations_path = annotations_path.resolve()
    source = json.loads(annotations_path.read_text(encoding="utf-8"))
    if source.get("schema") != INPUT_SCHEMA:
        raise ValueError(f"annotations are not {INPUT_SCHEMA}")
    rows = source.get("annotations")
    if not isinstance(rows, list) or not rows:
        raise ValueError("capture review has no annotations")
    if not 0.0 < threshold < 1.0:
        raise ValueError("threshold must lie between zero and one")
    by_id: dict[str, dict[str, Any]] = {}
    descriptors: dict[str, dict[str, Any]] = {}
    crop_records: dict[str, dict[str, Any]] = {}
    for row in rows:
        annotation_id = row.get("annotation_id")
        if not isinstance(annotation_id, str) or not annotation_id or annotation_id in by_id:
            raise ValueError("annotation IDs must be non-empty and unique")
        path = _resolve_crop(annotations_path, row)
        by_id[annotation_id] = row
        descriptors[annotation_id] = _descriptor(path)
        crop_records[annotation_id] = {
            "path": portable_path(path),
            "sha256": row["crop"]["sha256"],
            "width": row["crop"].get("width"),
            "height": row["crop"].get("height"),
        }
    ids = sorted(by_id)
    triage_by_id: dict[str, dict[str, Any]] = {}
    if label_triage_path is not None:
        label_triage_path = label_triage_path.resolve()
        label_triage = json.loads(label_triage_path.read_text(encoding="utf-8"))
        if label_triage.get("schema") != LABEL_TRIAGE_SCHEMA:
            raise ValueError(f"label triage is not {LABEL_TRIAGE_SCHEMA}")
        triage_by_id = {
            row["annotation_id"]: row for row in label_triage.get("proposals", [])
        }
        unknown = sorted(set(triage_by_id) - set(ids))
        if unknown:
            raise ValueError(f"label triage refers to unknown annotations: {unknown[:3]}")
    distances: dict[tuple[str, str], float] = {}
    for index, left in enumerate(ids):
        for right in ids[index + 1:]:
            distances[(left, right)] = descriptor_distance(descriptors[left], descriptors[right])
    semantic_document = None
    semantic_distances: dict[tuple[str, str], float] = {}
    classifier_by_id: dict[str, list[dict[str, Any]]] = {}
    if semantic_neighbors_path is not None:
        semantic_neighbors_path = semantic_neighbors_path.resolve()
        semantic_document = json.loads(semantic_neighbors_path.read_text(encoding="utf-8"))
        semantic_distances = _semantic_distances(semantic_document, set(ids))
        classifier_by_id = {
            row["annotation_id"]: [
                {
                    "source_label": item["identifier"],
                    "semantic_candidate": (
                        VISION_CLASS_MAP.get(item["identifier"])
                        if VISION_CLASS_MAP.get(item["identifier"]) is not None
                        and float(item["confidence"]) >= VISION_CLASS_MIN_CONFIDENCE[
                            VISION_CLASS_MAP[item["identifier"]]
                        ]
                        else None
                    ),
                    "confidence": round(float(item["confidence"]), 8),
                }
                for item in row.get("classifications") or []
                if float(item.get("confidence", 0.0)) >= 0.05
            ]
            for row in semantic_document["observations"]
        }
        classified_by_id = {}
        for annotation_id, proposals in classifier_by_id.items():
            accepted = [row for row in proposals if row["semantic_candidate"] is not None]
            if accepted:
                classified_by_id[annotation_id] = max(
                    accepted, key=lambda row: (row["confidence"], row["semantic_candidate"])
                )["semantic_candidate"]
        semantic_batches: dict[str, list[str]] = {}
        for annotation_id, label in classified_by_id.items():
            semantic_batches.setdefault(label, []).append(annotation_id)
        remaining_ids = [annotation_id for annotation_id in ids if annotation_id not in classified_by_id]
        similarity_groups = _complete_link_sparse_groups(
            remaining_ids, semantic_distances, semantic_threshold
        )
        group_specs = [
            (sorted(members), label)
            for label, members in sorted(semantic_batches.items())
        ] + [(members, None) for members in similarity_groups]
    else:
        group_specs = [
            (members, None) for members in _complete_link_groups(ids, distances, threshold)
        ]
    output_groups = []
    member_rows = []
    for members, proposed_label in group_specs:
        triage_rows = [triage_by_id[member] for member in members if member in triage_by_id]
        triage_labels = sorted({
            row["proposed_semantic_label"] for row in triage_rows
            if row.get("proposed_semantic_label")
        })
        triage_roles = sorted({row["population_role"] for row in triage_rows})
        triage_consensus = (
            triage_labels[0]
            if len(triage_rows) == len(members) and len(triage_labels) == 1
            and "discard" not in triage_roles
            else None
        )
        effective_proposal = proposed_label or triage_consensus
        active_distances = (
            distances if proposed_label is not None
            else semantic_distances if semantic_document is not None
            else distances
        )
        if len(members) == 1:
            representative = members[0]
        else:
            representative = min(
                members,
                key=lambda candidate: (
                    sum(
                        active_distances[tuple(sorted((candidate, other)))]
                        for other in members if other != candidate
                    ),
                    candidate,
                ),
            )
        prefix = "class" if effective_proposal is not None else "sim"
        group_id = prefix + "-" + hashlib.sha256("\n".join(members).encode()).hexdigest()[:16]
        distances_to_rep = {
            member: (0.0 if member == representative else
                     active_distances[tuple(sorted((member, representative)))])
            for member in members
        }
        candidate_labels = sorted({
            str(by_id[member].get("label") or "").strip()
            for member in members
            if str(by_id[member].get("label") or "").strip()
            and str(by_id[member].get("label")).strip() != "unclassified_fixture"
        })
        classifier_labels = sorted({
            item["semantic_candidate"]
            for member in members
            for item in classifier_by_id.get(member, [])
            if item["semantic_candidate"] is not None
        })
        output_groups.append({
            "group_id": group_id,
            "state": (
                "candidate_local_classification"
                if proposed_label is not None
                else "candidate_agent_triage"
                if triage_consensus is not None
                else "candidate_similarity_only"
            ),
            "representative_annotation_id": representative,
            "member_count": len(members),
            "members": [
                {
                    "annotation_id": member,
                    "crop": crop_records[member],
                    "distance_to_representative": distances_to_rep[member],
                    "classical_distance_to_representative": (
                        0.0 if member == representative else
                        distances[tuple(sorted((member, representative)))]
                    ),
                    "first_pass_label": by_id[member].get("label"),
                    "local_classifier_proposals": classifier_by_id.get(member, []),
                    "agent_label_triage": triage_by_id.get(member),
                }
                for member in members
            ],
            "first_pass_label_candidates": candidate_labels,
            "local_classifier_label_candidates": classifier_labels,
            "agent_label_candidates": triage_labels,
            "agent_population_role_candidates": triage_roles,
            "proposed_semantic_label": effective_proposal,
            "semantic_label": None,
        })
        member_rows.extend({
            "annotation_id": member,
            "group_id": group_id,
            "representative_annotation_id": representative,
        } for member in members)
    output_groups.sort(key=lambda row: (-row["member_count"], row["group_id"]))
    member_rows.sort(key=lambda row: row["annotation_id"])
    return {
        "schema": SCHEMA,
        "stage": "pre_agent_candidate_grouping",
        "input": {"path": portable_path(annotations_path), "sha256": sha256(annotations_path)},
        "algorithm": {
            "id": (
                "y92.apple_vision_crop_similarity.v1"
                if semantic_document is not None else ALGORITHM_ID
            ),
            "semantic_model": (
                {
                    "backend": semantic_document["backend"],
                    "operating_system": semantic_document["operating_system"],
                    "neighbors": {
                        "path": portable_path(semantic_neighbors_path),
                        "sha256": sha256(semantic_neighbors_path),
                    },
                    "threshold": semantic_threshold,
                }
                if semantic_document is not None else None
            ),
            "manual_label_triage": (
                {"path": portable_path(label_triage_path), "sha256": sha256(label_triage_path)}
                if label_triage_path is not None else None
            ),
            "network_used": False,
            "threshold": threshold,
            "linkage": (
                "deterministic_sparse_complete_link"
                if semantic_document is not None else "deterministic_complete_link"
            ),
            "warning": "Groups are review batches, not labels, deduplication, or physical identity.",
        },
        "counts": {
            "annotations": len(ids),
            "groups": len(output_groups),
            "multi_member_groups": sum(row["member_count"] > 1 for row in output_groups),
            "singletons": sum(row["member_count"] == 1 for row in output_groups),
            "local_classification_batches": sum(
                row["state"] == "candidate_local_classification" for row in output_groups
            ),
            "locally_classified_annotations": sum(
                row["member_count"] for row in output_groups
                if row["state"] == "candidate_local_classification"
            ),
            "agent_triage_batches": sum(
                row["state"] == "candidate_agent_triage" for row in output_groups
            ),
            "agent_triaged_annotations": len(triage_by_id),
        },
        "groups": output_groups,
        "membership": member_rows,
        "promotion": {"semantic_labels_accepted": 0, "world_ir_promotion_ready": False},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=0.16)
    parser.add_argument("--semantic-neighbors", type=Path)
    parser.add_argument("--semantic-threshold", type=float, default=0.62)
    parser.add_argument("--label-triage", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    document = build_document(
        args.annotations,
        threshold=args.threshold,
        semantic_neighbors_path=args.semantic_neighbors,
        semantic_threshold=args.semantic_threshold,
        label_triage_path=args.label_triage,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(canonical_bytes(document))
    print(
        f"[capture-groups] annotations={document['counts']['annotations']} "
        f"groups={document['counts']['groups']} "
        f"multi={document['counts']['multi_member_groups']} "
        f"singletons={document['counts']['singletons']}"
    )
    print(f"[capture-groups] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
