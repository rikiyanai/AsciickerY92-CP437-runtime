#!/usr/bin/env python3
import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_rgba(path: Path) -> Image.Image:
    return Image.open(path).convert("RGBA")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail unless two PNG files are exact pixel matches."
    )
    parser.add_argument("expected", type=Path)
    parser.add_argument("actual", type=Path)
    parser.add_argument("--diff", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--min-match",
        type=float,
        default=1.0,
        help="Required exact pixel match ratio. Use 0.95 for a 95 percent exact-pixel parity gate.",
    )
    args = parser.parse_args()
    if args.min_match < 0.0 or args.min_match > 1.0:
        raise SystemExit("--min-match must be between 0.0 and 1.0")

    expected = load_rgba(args.expected)
    actual = load_rgba(args.actual)

    result = {
        "pass": False,
        "expected": str(args.expected),
        "actual": str(args.actual),
        "expected_sha256": sha256(args.expected),
        "actual_sha256": sha256(args.actual),
        "expected_size": list(expected.size),
        "actual_size": list(actual.size),
        "pixel_count": expected.size[0] * expected.size[1],
        "differing_pixels": None,
        "differing_percent": None,
        "matching_pixels": None,
        "matching_percent": None,
        "min_match": args.min_match,
        "max_abs_channel_delta": None,
        "diff": str(args.diff) if args.diff else None,
    }

    if expected.size != actual.size:
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"size mismatch: expected {expected.size}, actual {actual.size}")
        return 1

    expected_bytes = expected.tobytes()
    actual_bytes = actual.tobytes()
    differing_pixels = 0
    max_abs_delta = 0

    diff_image = None
    diff_pixels = None
    if args.diff:
        diff_image = Image.new("RGBA", expected.size, (0, 0, 0, 255))
        diff_pixels = diff_image.load()

    width, height = expected.size
    for pixel_idx in range(width * height):
        base = pixel_idx * 4
        exp = expected_bytes[base : base + 4]
        act = actual_bytes[base : base + 4]
        if exp != act:
            differing_pixels += 1
            for channel in range(4):
                delta = abs(exp[channel] - act[channel])
                if delta > max_abs_delta:
                    max_abs_delta = delta
            if diff_pixels is not None:
                x = pixel_idx % width
                y = pixel_idx // width
                diff_pixels[x, y] = (255, 0, 255, 255)

    if args.diff and diff_image is not None:
        args.diff.parent.mkdir(parents=True, exist_ok=True)
        diff_image.save(args.diff)

    result["differing_pixels"] = differing_pixels
    result["differing_percent"] = differing_pixels / result["pixel_count"]
    result["matching_pixels"] = result["pixel_count"] - differing_pixels
    result["matching_percent"] = result["matching_pixels"] / result["pixel_count"]
    result["max_abs_channel_delta"] = max_abs_delta
    result["pass"] = result["matching_percent"] >= args.min_match

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(
            f"PASS exact pixel parity >= {args.min_match:.2%}"
            if result["pass"]
            else (
                "FAIL pixel parity: "
                f"{result['matching_pixels']}/{result['pixel_count']} pixels match "
                f"({result['matching_percent']:.6%}), required {args.min_match:.2%}; "
                f"{differing_pixels} pixels differ "
                f"({result['differing_percent']:.6%}); "
                f"max channel delta {max_abs_delta}"
            )
        )
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
