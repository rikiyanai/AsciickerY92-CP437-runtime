#!/usr/bin/env python3
import argparse
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture canxkoz WebGPU output and fail unless the Godot frame meets the exact-pixel match threshold."
    )
    parser.add_argument("--url", default="http://127.0.0.1:3000/")
    parser.add_argument("--channel", default="chrome")
    parser.add_argument("--width", type=int, default=128)
    parser.add_argument("--height", type=int, default=80)
    parser.add_argument("--frames", type=int, default=30)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--godot-frame", type=Path, required=True)
    parser.add_argument("--min-match", type=float, default=0.95)
    args = parser.parse_args()

    out_dir = args.out_dir.resolve()
    reference_dir = out_dir / "reference"
    diff_path = out_dir / "diff_vs_godot.png"
    manifest_path = out_dir / "manifest.json"
    reference_dir.mkdir(parents=True, exist_ok=True)

    capture_cmd = [
        "node",
        "scripts/capture_canxkoz_reference_frame.mjs",
        "--channel",
        args.channel,
        "--url",
        args.url,
        "--out-dir",
        str(reference_dir),
        "--width",
        str(args.width),
        "--height",
        str(args.height),
        "--frames",
        str(args.frames),
    ]
    capture = run(capture_cmd, ROOT)
    reference_png = reference_dir / "canxkoz_reference.png"

    compare_cmd = [
        "python3",
        "scripts/compare_png_exact.py",
        str(reference_png),
        str(args.godot_frame),
        "--diff",
        str(diff_path),
        "--json",
        "--min-match",
        str(args.min_match),
    ]
    compare = run(compare_cmd, ROOT) if capture.returncode == 0 else None

    compare_json = None
    if compare and compare.stdout.strip():
        try:
            compare_json = json.loads(compare.stdout)
        except json.JSONDecodeError:
            compare_json = {"raw": compare.stdout}

    manifest = {
        "kind": "canxkoz_godot_pixel_parity",
        "status": "pass" if compare and compare.returncode == 0 else "fail",
        "reference_url": args.url,
        "browser_channel": args.channel,
        "width": args.width,
        "height": args.height,
        "frames": args.frames,
        "min_match": args.min_match,
        "godot_frame": str(args.godot_frame),
        "reference_png": str(reference_png),
        "diff_png": str(diff_path),
        "capture_returncode": capture.returncode,
        "capture_output": capture.stdout,
        "compare_returncode": compare.returncode if compare else None,
        "compare": compare_json,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0 if manifest["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
