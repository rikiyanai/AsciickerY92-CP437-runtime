#!/usr/bin/env python3
"""Render native train frames into a GIF with matched runtime-state receipts.

This consumes the real native CP437 capture owner; it never synthesises train
motion or substitutes an editor preview. A GIF is review media, not proof of
interactive gameplay. Timing/speed changes are disclosed in its receipt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WORKSPACE = REPO / "numabe"


def sha(path):
    """Hash actual source and derived bytes for the media receipt."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_capture(capture: Path) -> list[dict]:
    """Require paired native frames and state logs, rejecting missing samples."""
    rows = [json.loads(line) for line in (capture / "train-states.jsonl").read_text().splitlines()]
    if not rows or [row["shot_index"] for row in rows] != list(range(len(rows))):
        raise ValueError("Native shots must be present once each in contiguous order")
    for row in rows:
        k = row["shot_index"]
        metadata = capture / f"source-shot-{k}.json"
        frame = json.loads(metadata.read_text())
        if frame["shot_index"] != k or frame["trains"] != row["trains"]:
            raise ValueError(f"Shot {k}: metadata/state mismatch")
        for name in (f"source-shot-{k}.xp", f"source-resolved-cells-{k}.jsonl"):
            if not (capture / name).is_file():
                raise ValueError(f"Shot {k}: missing native frame {name}")
    return rows


def main():
    """Derive labelled-by-receipt media without overwriting existing GIFs."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("capture", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--fps", type=float, default=2.0)
    ap.add_argument("--source-map-sha", help="Required only for historical captures without launch_receipt.json")
    ap.add_argument("--historical", action="store_true", help="Mark as a pre-correction baseline, never current-map proof")
    args = ap.parse_args()
    if args.output.exists():
        raise SystemExit(f"Refusing to overwrite existing media: {args.output}")
    rows = read_capture(args.capture)
    launch_path = args.capture / "launch_receipt.json"
    launch = json.loads(launch_path.read_text()) if launch_path.is_file() else {}
    map_sha = launch.get("source_map_sha256") or args.source_map_sha
    if not map_sha:
        raise SystemExit("A source map hash is required; do not infer it from a pose-map filename")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame_rows = []
    for row in rows:
        k = row["shot_index"]
        png = args.capture / f"production_view-{k}.png"
        xp = args.capture / f"source-shot-{k}.xp"
        cells = args.capture / f"source-resolved-cells-{k}.jsonl"
        if not png.is_file():
            subprocess.run([sys.executable, str(WORKSPACE / "scripts/xp_to_png.py"), str(xp),
                            "--classic-resolved-cells", str(cells), "-o", str(png)], check=True,
                           stdout=subprocess.DEVNULL)
        frame_rows.append({"shot": k, "native_xp_sha256": sha(xp), "resolved_cells_sha256": sha(cells),
                           "png_sha256": sha(png), "player_pos": row["player_pos"], "trains": row["trains"]})
    fps = str(args.fps)
    scale = f"scale={args.width}:-1:flags=neighbor"
    # One filter graph avoids a loose palette companion; CP437 glyphs use
    # nearest-neighbour scaling rather than blurred text resampling.
    subprocess.run(["ffmpeg", "-n", "-loglevel", "error", "-framerate", fps,
                    "-i", str(args.capture / "production_view-%d.png"), "-filter_complex",
                    f"{scale},split[a][b];[a]palettegen[p];[b][p]paletteuse=dither=bayer:bayer_scale=3",
                    "-loop", "0", str(args.output)], check=True)
    clocks = [row["trains"][0]["clock_s"] for row in rows]
    deltas = [b - a for a, b in zip(clocks, clocks[1:]) if b > a]
    interval = statistics.median(deltas) if deltas else None
    phases = sorted({train["phase"] for row in rows for train in row["trains"]})
    receipt = {"schema": "cp437.numabe.native_train_media.v1", "capture": str(args.capture.resolve()),
               "gif": str(args.output.resolve()), "gif_sha256": sha(args.output),
               "source_map_sha256": map_sha, "native_binary_sha256": launch.get("binary_sha256"),
               "historical_pre_correction": args.historical, "frames": len(rows), "phases": phases,
               "median_native_sample_interval_s": interval, "gif_fps": args.fps,
               "playback_speed_multiplier_approx": interval * args.fps if interval else None,
               "interactive_playability": "not established by automatic capture",
               "visual_acceptance": "requires direct inspection of this GIF and selected native frames",
               "rows": frame_rows}
    args.output.with_suffix(".receipt.json").write_text(json.dumps(receipt, indent=1) + "\n")
    print(json.dumps({k: v for k, v in receipt.items() if k != "rows"}, indent=2))


if __name__ == "__main__":
    main()
