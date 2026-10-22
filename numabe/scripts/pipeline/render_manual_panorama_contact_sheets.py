#!/usr/bin/env python3
"""Render low-resolution panorama contact sheets for manual Numabe authoring.

This is deliberately an inspection-only tool.  It reads capture pixels and
metadata, writes thumbnails plus an index, and makes no classification,
identity, building, facade, or placement decision.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


def _metadata(capture_index: Path, survey: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    doc = json.loads(capture_index.read_text(encoding="utf-8"))
    for row in doc.get("captures", []):
        out[str(row["capture_id"]).removeprefix("mapillary_")] = row
    doc = json.loads(survey.read_text(encoding="utf-8"))
    for row in ((doc.get("providers") or {}).get("mapillary") or {}).get("pictures", []):
        out.setdefault(str(row["picture_id"]), row)
    return out


def _label(path: Path, row: dict) -> list[str]:
    picture_id = path.stem.removeprefix("mapillary_")
    when = row.get("timestamp") or row.get("capture_date") or "date unknown"
    seq = row.get("sequence_id") or "sequence unknown"
    lat, lon = row.get("lat"), row.get("lon")
    heading = row.get("heading_deg")
    return [
        f"{picture_id}  {when[:19]}",
        f"lat/lon {lat!s}, {lon!s}  heading {heading!s}",
        f"sequence {seq}",
    ]


def render(captures: Path, capture_index: Path, survey: Path, output: Path,
           per_sheet: int = 4) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    meta = _metadata(capture_index, survey)
    paths = sorted(captures.glob("mapillary_*.jpg"), key=lambda p: (
        str(meta.get(p.stem.removeprefix("mapillary_"), {}).get("sequence_id") or ""),
        str(meta.get(p.stem.removeprefix("mapillary_"), {}).get("timestamp") or
            meta.get(p.stem.removeprefix("mapillary_"), {}).get("capture_date") or ""),
        p.name,
    ))
    if per_sheet != 4:
        raise ValueError("manual review layout is fixed at four captures per sheet")

    font = ImageFont.load_default()
    rows = []
    for start in range(0, len(paths), 4):
        page = Image.new("RGB", (1280, 900), "#171717")
        draw = ImageDraw.Draw(page)
        items = []
        for slot, path in enumerate(paths[start:start + 4]):
            x, y = (slot % 2) * 640, (slot // 2) * 450
            with Image.open(path) as source:
                thumb = ImageOps.contain(source.convert("RGB"), (620, 360), Image.Resampling.LANCZOS)
            px = x + 10 + (620 - thumb.width) // 2
            py = y + 8 + (360 - thumb.height) // 2
            page.paste(thumb, (px, py))
            picture_id = path.stem.removeprefix("mapillary_")
            labels = _label(path, meta.get(picture_id, {}))
            for line, text in enumerate(labels):
                draw.text((x + 10, y + 374 + line * 18), text, fill="white", font=font)
            items.append({"slot": slot, "capture_id": path.stem,
                          "source": str(path), "labels": labels})
        sheet_name = f"sheet_{start // 4 + 1:03d}.jpg"
        page.save(output / sheet_name, quality=88, optimize=True)
        rows.append({"sheet": sheet_name, "items": items})

    manifest = {
        "schema": "y92.manual_panorama_contact_sheets.v1",
        "purpose": "inspection_only_no_authored_decisions",
        "capture_count": len(paths),
        "sheet_count": len(rows),
        "sheets": rows,
    }
    (output / "index.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--captures", required=True, type=Path)
    ap.add_argument("--capture-index", required=True, type=Path)
    ap.add_argument("--survey", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()
    doc = render(args.captures, args.capture_index, args.survey, args.output)
    print(f"rendered {doc['capture_count']} captures on {doc['sheet_count']} inspection sheets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
