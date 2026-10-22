#!/usr/bin/env python3
"""Line-style recording arm checker (FF-DPR-56A, FL-4359).

Judges one recorded arm (a .run out dir plus its Desktop frames dir)
against the Line-style acceptance surface: log hygiene, artifact
completeness, sidecar join, keyframe statistics, and the FF-DPR-35 churn
meter against the Line targets and the Filled baseline.

Line gates (FF-ACQ-12 doc, spec:2207/P3:2224, FF-DPR-35 numbers):
  density_median  <= 0.12 (upper anti-carpet bound around the 225x71
                   reference 0.07205; zero is allowed)
  changed_median  < 0.2953  (below the Filled median; motion drives changed,
                             so this gates only the carpet extreme)
  flips_median    < 0.05    ("well under 0.07" with margin; scene-(d) 0.0563
                             is already suspect per F-12-4)
Density is intentionally an upper-bound-only gate. Empty cells are a legitimate
Line authoring choice for uncertain cells, anti-aliasing, seam breaks, contact
shadows, and erasure; visual/readability and symbolic provenance remain
separate acceptance gates. The upper bound rejects the known F10 FGONLY
density 0.5502 while allowing frame-level occupancy variation around the
canonical reference. Filled baseline for the report: 0.8595 / 0.2953 / 0.0708.

Exit code 0 only when every gated row passes. Writes
<run-dir>/line_check_report.json plus a contact sheet and a 24-frame
cropped GIF preview for the operator. Keyframe-versus-tween statistics come
from the sidecar wire (key/delta/drop counts, keyframe_every, f10 tags);
contract-7 tuple bits are reported present only when a future sidecar
carries them (v1 does not).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "recording"))
import f9_symbolic_codec as sym  # noqa: E402

from PIL import Image  # noqa: E402

FILLED_BASELINE = {"density_median": 0.8595, "changed_median": 0.2953,
                   "flips_median": 0.0708}
FGONLY_BASELINE = {"density_median": 0.5502, "changed_median": 0.4513,
                   "flips_median": 0.0773}
LINE_DENSITY_REFERENCE = 0.07205
LINE_DENSITY_MAX = 0.12
LINE_GATES = {"changed_median": ("<", 0.2953),
              "flips_median": ("<", 0.05)}
FENCE_TOKEN = "timeout waiting for fence"
SCRIPT_ERROR_TOKEN = "SCRIPT ERROR"
GIF_FRAMES = 24
CONTACT_WIDTH = 600
GIF_WIDTH = 1280
GIF_HEIGHT = 800


def parse_manifest(path: Path) -> dict:
    """Parse manifest.txt into meta plus per-frame rows. Raises on defect."""
    meta: dict = {}
    frames: list[dict] = []
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        if line.startswith("frame:"):
            row: dict = {}
            for token in line[len("frame:"):].strip().split():
                if "=" in token:
                    key, value = token.split("=", 1)
                    row[key] = value
            frames.append(row)
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            if value.strip():
                meta[key.strip()] = value.strip()
    matrix = [0, 0]
    for row in frames:
        for key, value in row.items():
            if key == "matrix":
                match = re.fullmatch(r"\[(\d+),(\d+)\]", value)
                if match:
                    matrix = [int(match.group(1)), int(match.group(2))]
    return {"meta": meta, "frames": frames, "matrix": matrix}
def count_token_lines(path: Path, token: str) -> int:
    """Count lines containing token. Missing file is a defect (-1)."""
    if not path.is_file():
        return -1
    count = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if token in line:
                count += 1
    return count


def frame_pngs(frames_dir: Path) -> list[Path]:
    """Sorted frame_*.png paths in the frames dir."""
    return sorted(frames_dir.glob("frame_*.png"))


def sidecar_stats(sym_path: Path, manifest_seqs: list[int]) -> dict:
    """Decode the sidecar fail-closed; join to manifest seqs; key stats."""
    doc = sym.load(str(sym_path))
    covered, absent = sym.join_manifest(doc, manifest_seqs)
    kinds: dict[str, int] = {}
    f10: dict[str, int] = {}
    for entry in doc["frames"]:
        kinds[entry["kind"]] = kinds.get(entry["kind"], 0) + 1
        tag = str(entry.get("f10", 0))
        f10[tag] = f10.get(tag, 0) + 1
    header = {key: doc.get(key) for key in
              ("cols", "rows", "keyframe_every", "count", "crc")}
    n_tween = kinds.get("delta", 0)
    n_key = kinds.get("key", 0)
    return {"header": header, "kinds": kinds, "f10_tags": f10,
            "covered": len(covered), "absent": sorted(absent),
            "tween_share": (n_tween / max(1, n_key + n_tween)),
            "contract7_tuple": "absent-on-v1"}




def write_contact_sheet(pngs: list[Path], out_path: Path) -> None:
    """First, middle, and last frames side by side, scaled for overview."""
    picks = [pngs[0], pngs[len(pngs) // 2], pngs[-1]]
    thumbs = []
    for pick in picks:
        with Image.open(pick) as image:
            frame = image.convert("RGB")
            scale = CONTACT_WIDTH / frame.width
            thumbs.append(frame.resize(
                (CONTACT_WIDTH, round(frame.height * scale))))
    height = max(thumb.height for thumb in thumbs)
    sheet = Image.new("RGB", (CONTACT_WIDTH * 3, height), (0, 0, 0))
    for index, thumb in enumerate(thumbs):
        sheet.paste(thumb, (index * CONTACT_WIDTH, 0))
    sheet.save(out_path)


def run_meter(frames_dir: Path, cols: int, rows: int) -> dict:
    """Run the FF-DPR-35 churn meter; return flattened medians."""
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "measure_f10_churn.py"),
         str(frames_dir), "--cols", str(cols), "--rows", str(rows)],
        capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(f"meter failed rc={proc.returncode}: "
                           f"{proc.stderr[-500:]}")
    raw = json.loads(proc.stdout)
    return {"density_median": float(raw["density"]["median"]),
            "changed_median": float(raw["changed"]["median"]),
            "flips_median": float(raw["flip"]["median"]),
            "raw": raw}


def write_preview_gif(pngs: list[Path], out_path: Path, fps: float) -> None:
    """24 consecutive middle frames, center-cropped 1:1 for legibility."""
    start = max(0, len(pngs) // 2 - GIF_FRAMES // 2)
    picks = pngs[start:start + GIF_FRAMES]
    frames = []
    for pick in picks:
        with Image.open(pick) as image:
            frame = image.convert("RGB")
            width = min(GIF_WIDTH, frame.width)
            height = min(GIF_HEIGHT, frame.height)
            left = (frame.width - width) // 2
            top = (frame.height - height) // 2
            frames.append(frame.crop((left, top, left + width, top + height)))
    duration_ms = round(1000.0 / fps) if fps and fps > 0 else 120
    frames[0].save(out_path, save_all=True, append_images=frames[1:],
                   duration=duration_ms, loop=0)


def check_arm(run_dir: Path, frames_dir: Path, name: str,
              make_previews: bool = True) -> dict:
    """Run every check row for one arm. Never raises on arm defects."""
    rows: dict[str, dict] = {}

    def row(key: str, passed: bool, detail: str = "",
            report_only: bool = False) -> None:
        rows[key] = {"pass": bool(passed), "detail": detail,
                     "report_only": report_only}

    godot_log = run_dir / "godot_client.log"
    fence = count_token_lines(godot_log, FENCE_TOKEN)
    row("fence_lines_zero", fence == 0, f"fence_lines={fence}")
    if fence != 0:
        return {"name": name, "rows": rows, "pass": False,
                "reason": "fence hard stop"}
    script_errors = count_token_lines(godot_log, SCRIPT_ERROR_TOKEN)
    row("script_errors_zero", script_errors == 0,
        f"script_errors={script_errors}")

    manifest_path = frames_dir / "manifest.txt"
    try:
        manifest = parse_manifest(manifest_path)
    except (OSError, ValueError) as exc:
        row("manifest_parse", False, f"{type(exc).__name__}: {exc}")
        return {"name": name, "rows": rows, "pass": False,
                "reason": "manifest unreadable"}
    row("manifest_parse", True,
        f"frames={manifest['meta'].get('frames')} "
        f"dropped={manifest['meta'].get('dropped')}")
    meta = manifest["meta"]
    expected_frames = int(meta.get("frames", -1))
    expected_dropped = int(meta.get("dropped", -1))
    pngs = frame_pngs(frames_dir)
    row("frames_match_manifest",
        len(pngs) == expected_frames - expected_dropped,
        f"pngs={len(pngs)} manifest_frames={expected_frames} "
        f"dropped={expected_dropped}")

    mov_raw = meta.get("mov", "")
    mov_path = Path(mov_raw) if mov_raw else frames_dir.with_suffix(".mov")
    row("mov_present", mov_path.is_file() and mov_path.stat().st_size > 0,
        f"mov={mov_path} "
        f"bytes={mov_path.stat().st_size if mov_path.is_file() else -1}")

    sym_path = frames_dir / sym.SIDECAR_FILENAME
    try:
        seqs = [int(row.get("seq", -1)) for row in manifest["frames"]]
        stats = sidecar_stats(sym_path, seqs)
        row("sidecar_decodes", True,
            f"kinds={stats['kinds']} keyframe_every="
            f"{stats['header'].get('keyframe_every')}")
        row("sidecar_join", not stats["absent"],
            f"covered={stats['covered']} absent={stats['absent']}")
    except (OSError, ValueError, LookupError) as exc:
        row("sidecar_decodes", False, f"{type(exc).__name__}: {exc}")
        row("sidecar_join", False, "no join without decode")
        stats = {"header": {}, "kinds": {}, "f10_tags": {},
                 "covered": 0, "absent": seqs, "tween_share": 0.0,
                 "contract7_tuple": "unknown"}
    rows["keyframe_stats"] = {"pass": True, "report_only": True,
                              "detail": json.dumps(stats, sort_keys=True)}

    matrix = manifest["matrix"]
    meter: dict = {}
    try:
        if matrix[0] <= 0 or matrix[1] <= 0:
            raise RuntimeError("matrix missing from manifest")
        meter = run_meter(frames_dir, matrix[0], matrix[1])
        row("meter_runs", True,
            f"density={meter.get('density_median')} "
            f"changed={meter.get('changed_median')} "
            f"flips={meter.get('flips_median')}")
        density = float(meter.get("density_median", float("nan")))
        # Density must not impose a universal nonzero-ink requirement.  The
        # operator's anti-carpet requirement is an upper bound only; the
        # visual/readability and symbolic-provenance gates own empty output.
        density_pass = density <= LINE_DENSITY_MAX
        row("line_density_median", density_pass,
            f"density_median={density} upper_gate<={LINE_DENSITY_MAX} "
            f"reference={LINE_DENSITY_REFERENCE} "
            f"fgonly={FGONLY_BASELINE['density_median']}")
        for metric, (op, bound) in LINE_GATES.items():
            value = float(meter.get(metric, float("nan")))
            passed = value < bound if op == "<" else value > bound
            row(f"line_{metric}", passed,
                f"{metric}={value} gate={op}{bound} "
                f"filled={FILLED_BASELINE[metric]}")
    except (RuntimeError, ValueError, KeyError) as exc:
        row("meter_runs", False, f"{type(exc).__name__}: {exc}")

    if make_previews and pngs:
        try:
            contact_path = run_dir / f"line_contact_{name}.png"
            write_contact_sheet(pngs, contact_path)
            fps = float(meta.get("measured_fps", 0) or 0)
            gif_path = run_dir / f"line_preview_{name}.gif"
            write_preview_gif(pngs, gif_path, fps)
            row("previews_written", True,
                f"contact={contact_path.name} gif={gif_path.name}")
        except (OSError, ValueError) as exc:
            row("previews_written", False, f"{type(exc).__name__}: {exc}")

    gated = [key for key, value in rows.items()
             if not value.get("report_only", False)]
    passed = all(rows[key]["pass"] for key in gated)
    return {"name": name, "rows": rows, "meter": meter,
            "matrix": matrix, "pass": passed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Judge Line-style recording arms against acceptance.")
    parser.add_argument("--run-dir", type=Path, action="append",
                        default=[], help="Runner out dir (godot_client.log). "
                        "Repeat per arm; pairs with --frames-dir in order.")
    parser.add_argument("--frames-dir", type=Path, action="append",
                        default=[], help="Desktop frames dir "
                        "(manifest.txt, PNGs, sidecar).")
    parser.add_argument("--name", action="append", default=[],
                        help="Arm name per pair (default: run dir stem).")
    parser.add_argument("--no-previews", action="store_true",
                        help="Skip contact sheet and GIF.")
    parser.add_argument("--report", type=Path, default=None,
                        help="Report JSON path (default: first run dir / "
                        "line_check_report.json).")
    args = parser.parse_args(argv)
    if len(args.run_dir) != len(args.frames_dir) or not args.run_dir:
        print("need equal non-empty --run-dir / --frames-dir pairs",
              file=sys.stderr)
        return 2
    report: dict = {"arms": []}
    for index, (run_dir, frames_dir) in enumerate(
            zip(args.run_dir, args.frames_dir)):
        name = (args.name[index] if index < len(args.name)
                else run_dir.stem)
        result = check_arm(run_dir, frames_dir, name,
                           make_previews=not args.no_previews)
        report["arms"].append(result)
    report["pass"] = all(arm["pass"] for arm in report["arms"])
    report_path = args.report or (args.run_dir[0] / "line_check_report.json")
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(
        {"pass": report["pass"],
         "arms": [{"name": arm["name"], "pass": arm["pass"],
                   "rows": {key: value["pass"]
                            for key, value in arm["rows"].items()}}
                  for arm in report["arms"]]}, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
