#!/usr/bin/env python3
"""Audit held-out cube vectors against the StructuralSequenceV1 topology contract.

This is an input/control receipt, not a renderer screenshot and not a Xu result.
It consumes the existing authored 12-edge cube vectors, clips each edge to the
requested terminal grid, then applies the same topology rule as the C++ adapter:
one semantic stroke is a chain node; several strokes sharing one object-owned
cube vertex are a topology anchor; every other multi-stroke cell is a projection
collision anchor. Both anchors retain each authored stroke identity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_ROOT = (
    ROOT
    / ".scratch/macleek-ascii-art-ref/port_output/fl4512_cube_aspect_xu"
)
GRID_SIZES = (1, 2, 3, 4, 5, 6, 8, 9, 12)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def segment_intersects_rect(
    a: list[float], b: list[float], xmin: float, ymin: float, xmax: float, ymax: float
) -> bool:
    """Liang-Barsky segment/closed-rectangle intersection."""
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    p = (-dx, dx, -dy, dy)
    q = (a[0] - xmin, xmax - a[0], a[1] - ymin, ymax - a[1])
    t0 = 0.0
    t1 = 1.0
    for pi, qi in zip(p, q, strict=True):
        if abs(pi) < 1.0e-12:
            if qi < 0.0:
                return False
            continue
        t = qi / pi
        if pi < 0.0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return False
    return True


def point_in_half_open_cell(
    p: tuple[float, float], xmin: float, ymin: float, xmax: float, ymax: float
) -> bool:
    return xmin <= p[0] < xmax and ymin <= p[1] < ymax


def endpoint_key(point: list[float]) -> tuple[float, float]:
    return (round(float(point[0]), 9), round(float(point[1]), 9))


def classify_view(path: Path, grid_size: int) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    polylines = payload["polylines"]
    stroke_ids = payload["stroke_ids"]
    if len(polylines) != 12 or stroke_ids != list(range(12)):
        raise ValueError(f"{path}: expected stable authored stroke_ids 0..11")
    width = float(payload["canvas"]["width"])
    height = float(payload["canvas"]["height"])
    cell_w = width / grid_size
    cell_h = height / grid_size

    event_strokes: dict[tuple[float, float], set[int]] = defaultdict(set)
    for stroke_id, polyline in zip(stroke_ids, polylines, strict=True):
        if len(polyline) != 2:
            raise ValueError(f"{path}: cube edge {stroke_id} is not one segment")
        event_strokes[endpoint_key(polyline[0])].add(stroke_id)
        event_strokes[endpoint_key(polyline[1])].add(stroke_id)
    authored_events = {
        point: strokes for point, strokes in event_strokes.items() if len(strokes) >= 2
    }
    if len(authored_events) != 8:
        raise ValueError(f"{path}: expected 8 cube vertices, got {len(authored_events)}")
    if sorted(len(strokes) for strokes in authored_events.values()) != [3] * 8:
        raise ValueError(f"{path}: each cube vertex must own exactly 3 incident edges")

    kinds: Counter[str] = Counter()
    projection_anchor_cells: list[list[int]] = []
    for cy in range(grid_size):
        for cx in range(grid_size):
            xmin = cx * cell_w
            ymin = cy * cell_h
            xmax = (cx + 1) * cell_w
            ymax = (cy + 1) * cell_h
            present: set[int] = set()
            for stroke_id, polyline in zip(stroke_ids, polylines, strict=True):
                if segment_intersects_rect(polyline[0], polyline[1], xmin, ymin, xmax, ymax):
                    present.add(stroke_id)
            if not present:
                kinds["empty"] += 1
                continue
            if len(present) == 1:
                kinds["chain"] += 1
                continue
            qualifying = []
            for point, incident in authored_events.items():
                if point_in_half_open_cell(point, xmin, ymin, xmax, ymax) and present == incident:
                    qualifying.append(point)
            if len(qualifying) == 1:
                kinds["topology_anchor"] += 1
            else:
                kinds["projection_anchor"] += 1
                projection_anchor_cells.append([cx, cy])
    return {
        "input": str(path.relative_to(ROOT)),
        "input_sha256": sha256(path),
        "grid_size": grid_size,
        "cell_size": [cell_w, cell_h],
        "counts": dict(sorted(kinds.items())),
        "projection_anchor_cells": projection_anchor_cells,
        "representable": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, default=DEFAULT_INPUT_ROOT)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    results = []
    for grid_size in GRID_SIZES:
        directory = args.input_root / f"cells_{grid_size}x{grid_size}_ascii95_12x22_f20"
        paths = sorted(directory.glob("r??_c??.polyline-input.json"))
        if len(paths) != 49:
            raise SystemExit(f"expected 49 held-out views in {directory}, found {len(paths)}")
        views = [classify_view(path, grid_size) for path in paths]
        totals: Counter[str] = Counter()
        for view in views:
            totals.update(view["counts"])
        results.append(
            {
                "grid_size": grid_size,
                "view_count": len(views),
                "representable_views": sum(bool(view["representable"]) for view in views),
                "projection_anchor_views": sum(
                    int(view["counts"].get("projection_anchor", 0)) > 0 for view in views
                ),
                "cell_counts": dict(sorted(totals.items())),
                "views": views,
            }
        )

    git_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, text=True, capture_output=True
    ).stdout.strip()
    receipt = {
        "schema": "fl4512.cube_semantic_sequence_control.v2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_head": git_head,
        "dirty": bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=ROOT,
                check=True,
                text=True,
                capture_output=True,
            ).stdout
        ),
        "classification": "diagnostic_control_not_visual_proof",
        "contract": {
            "stroke_identity": "authored cube edge stroke_ids 0..11",
            "topology_anchor": "all co-occupying strokes share one projected authored cube vertex",
            "projection_anchor": "projected crossing, mixed event, or several events in one cell",
            "projection_anchor_runtime_disposition": "read-only merged-mask glyph shared by distinct chains",
        },
        "results": results,
    }
    encoded = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(encoded, encoding="utf-8")
        print(args.out)
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
