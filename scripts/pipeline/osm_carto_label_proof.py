#!/usr/bin/env python3
"""Generate a transparent label proof overlay from OSM Carto sidecars."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


LABELABLE_FEATURES = {
    "road_primary", "road_secondary", "road_tertiary", "road_residential",
    "footway", "path", "cycleway", "steps", "pedestrian_area", "parking",
    "building", "sport_court", "water", "wood", "grass", "garden", "hedge",
    "tree", "monument_artwork", "memorial", "bench", "bus_stop",
    "bicycle_parking",
}

LABEL_COLORS = {
    "water": (40, 110, 180, 230),
    "wood": (30, 120, 55, 230),
    "grass": (50, 130, 45, 220),
    "garden": (70, 120, 45, 220),
    "hedge": (30, 100, 45, 220),
    "tree": (20, 100, 40, 230),
    "building": (95, 80, 65, 235),
    "road_primary": (45, 65, 85, 235),
    "road_secondary": (55, 75, 95, 235),
    "road_tertiary": (65, 85, 105, 235),
    "road_residential": (75, 90, 110, 230),
    "footway": (130, 80, 60, 230),
    "path": (145, 75, 60, 230),
    "cycleway": (55, 95, 145, 230),
    "steps": (130, 80, 60, 230),
    "pedestrian_area": (90, 85, 120, 230),
    "parking": (100, 95, 90, 230),
    "sport_court": (105, 100, 85, 230),
    "monument_artwork": (130, 70, 55, 235),
    "memorial": (120, 70, 60, 235),
    "bench": (95, 95, 95, 230),
    "bus_stop": (65, 100, 150, 235),
    "bicycle_parking": (70, 105, 160, 235),
}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _label_for(record: dict[str, Any]) -> str:
    tags = record.get("source_tags") or {}
    name = tags.get("name") or tags.get("ref")
    kind = record.get("feature_kind", "feature")
    return str(name or kind).replace("_", " ")


def _record_point(record: dict[str, Any]) -> tuple[float, float] | None:
    centroid = record.get("world_centroid")
    if isinstance(centroid, list) and len(centroid) >= 2:
        return float(centroid[0]), float(centroid[1])
    point = record.get("world_point")
    if isinstance(point, list) and len(point) >= 2:
        return float(point[0]), float(point[1])
    bounds = record.get("world_bounds")
    if isinstance(bounds, dict):
        return (
            (float(bounds["min_x"]) + float(bounds["max_x"])) * 0.5,
            (float(bounds["min_y"]) + float(bounds["max_y"])) * 0.5,
        )
    return None


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("Arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def generate_label_proof(
    features_path: Path,
    topology_path: Path,
    *,
    min_cells: int = 1,
    font_size: int = 12,
) -> Image.Image:
    topology = json.loads(topology_path.read_text(encoding="utf-8"))
    min_x, min_y, max_x, max_y = [int(v) for v in topology["cell_bounds"]]
    image = Image.new("RGBA", (max(1, max_x - min_x), max(1, max_y - min_y)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = _font(font_size)

    records = _load_jsonl(features_path)
    for record in records:
        kind = str(record.get("feature_kind", ""))
        if kind not in LABELABLE_FEATURES:
            continue
        if int(record.get("painted_cell_count", 0)) < min_cells:
            continue
        point = _record_point(record)
        if point is None:
            continue
        px = int(round(point[0] - min_x))
        py = int(round(point[1] - min_y))
        if px < 0 or py < 0 or px >= image.width or py >= image.height:
            continue
        text = _label_for(record)
        fill = LABEL_COLORS.get(kind, (60, 60, 60, 230))
        bbox = draw.textbbox((px, py), text, font=font)
        pad = 2
        draw.rectangle(
            [bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad],
            fill=(255, 255, 245, 185),
        )
        draw.text((px, py), text, fill=fill, font=font)
    return image


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", required=True, type=Path)
    parser.add_argument("--topology", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--min-cells", type=int, default=1)
    parser.add_argument("--font-size", type=int, default=12)
    args = parser.parse_args()

    image = generate_label_proof(
        args.features,
        args.topology,
        min_cells=args.min_cells,
        font_size=args.font_size,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.out)
    print(json.dumps({"out": str(args.out), "width": image.width, "height": image.height}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
