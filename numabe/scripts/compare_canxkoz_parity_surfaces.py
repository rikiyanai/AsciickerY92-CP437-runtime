#!/usr/bin/env python3
"""Compare the frozen canxkoz parity surfaces for FL-4537."""

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REFERENCE = ROOT / ".run/canxkoz_reference_frame/20260704-timing-audit-frame30-128x80-hideui/canxkoz_reference.png"
DEFAULT_RD = Path("/tmp/canxkoz_frame30_no_readback_flip.png")
CPU_FLOOR_EXACT = 19
CPU_FLOOR_TOTAL = 10240
CPU_FLOOR_TOL48_RATIO = 0.948
TOLERANCES = [0, 1, 2, 4, 8, 16, 32, 48, 64]


def _sha256(path: Path) -> str:
	digest = hashlib.sha256()
	with path.open("rb") as handle:
		for chunk in iter(lambda: handle.read(1024 * 1024), b""):
			digest.update(chunk)
	return digest.hexdigest()


def _load(path: Path) -> Image.Image:
	return Image.open(path).convert("RGBA")


def _safe_corr(xs: List[float], ys: List[float]) -> Optional[float]:
	n = len(xs)
	if n == 0:
		return None
	mean_x = sum(xs) / n
	mean_y = sum(ys) / n
	var_x = 0.0
	var_y = 0.0
	cov = 0.0
	for x, y in zip(xs, ys):
		dx = x - mean_x
		dy = y - mean_y
		var_x += dx * dx
		var_y += dy * dy
		cov += dx * dy
	denom = math.sqrt(var_x * var_y)
	if denom <= 0.0:
		return None
	return cov / denom


def _compare(expected_path: Path, actual_path: Path) -> Dict[str, object]:
	expected = _load(expected_path)
	actual = _load(actual_path)
	result: Dict[str, object] = {
		"expected": str(expected_path),
		"actual": str(actual_path),
		"expected_sha256": _sha256(expected_path),
		"actual_sha256": _sha256(actual_path),
		"expected_size": list(expected.size),
		"actual_size": list(actual.size),
	}
	if expected.size != actual.size:
		result.update({"status": "size_mismatch"})
		return result

	width, height = expected.size
	total = width * height
	tolerance_counts = {tol: 0 for tol in TOLERANCES}
	exact = 0
	differing = 0
	max_delta = 0
	sum_delta = [0, 0, 0, 0]
	sum_abs_delta = [0, 0, 0, 0]
	exp_luma: List[float] = []
	act_luma: List[float] = []
	expected_bytes = expected.tobytes()
	actual_bytes = actual.tobytes()
	for idx in range(total):
		base = idx * 4
		exp_px = expected_bytes[base : base + 4]
		act_px = actual_bytes[base : base + 4]
		if exp_px == act_px:
			exact += 1
		else:
			differing += 1
		pixel_max = 0
		for channel in range(4):
			delta = int(act_px[channel]) - int(exp_px[channel])
			abs_delta = abs(delta)
			sum_delta[channel] += delta
			sum_abs_delta[channel] += abs_delta
			pixel_max = max(pixel_max, abs_delta)
			max_delta = max(max_delta, abs_delta)
		for tol in TOLERANCES:
			if pixel_max <= tol:
				tolerance_counts[tol] += 1
		exp_luma.append(0.2126 * exp_px[0] + 0.7152 * exp_px[1] + 0.0722 * exp_px[2])
		act_luma.append(0.2126 * act_px[0] + 0.7152 * act_px[1] + 0.0722 * act_px[2])

	result.update(
		{
			"status": "compared",
			"pixel_count": total,
			"exact": exact,
			"exact_ratio": exact / total,
			"differing": differing,
			"max_abs_channel_delta": max_delta,
			"tolerances": {
				str(tol): {"match": count, "ratio": count / total}
				for tol, count in tolerance_counts.items()
			},
			"mean_delta_rgba": [value / total for value in sum_delta],
			"mean_abs_delta_rgba": [value / total for value in sum_abs_delta],
			"luma_corr": _safe_corr(exp_luma, act_luma),
		}
	)
	return result


def _surface_report(name: str, expected: Path, actual: Optional[Path]) -> Dict[str, object]:
	if actual is None:
		return {"name": name, "status": "not_supplied"}
	if not actual.exists():
		return {"name": name, "status": "missing", "actual": str(actual)}
	report = _compare(expected, actual)
	report["name"] = name
	return report


def _print_human(report: Dict[str, object]) -> None:
	print(f"Reference: {report['reference']}")
	print(f"CPU floor: {CPU_FLOOR_EXACT}/{CPU_FLOOR_TOTAL} exact, tolerance-48 {CPU_FLOOR_TOL48_RATIO:.3f}")
	for surface in report["surfaces"]:
		name = surface["name"]
		status = surface["status"]
		if status != "compared":
			print(f"{name}: {status}")
			continue
		tol48 = surface["tolerances"]["48"]
		corr = surface["luma_corr"]
		corr_text = "n/a" if corr is None else f"{corr:.6f}"
		verdict = surface.get("floor_verdict", "")
		print(
			f"{name}: exact {surface['exact']}/{surface['pixel_count']} "
			f"({surface['exact_ratio']:.6%}); tol48 {tol48['match']}/{surface['pixel_count']} "
			f"({tol48['ratio']:.6%}); corr {corr_text}; "
			f"max_delta {surface['max_abs_channel_delta']}; {verdict}"
		)


def main() -> int:
	parser = argparse.ArgumentParser(description="Freeze and compare FL-4537 canxkoz parity surfaces.")
	parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
	parser.add_argument("--rd", type=Path, default=DEFAULT_RD)
	parser.add_argument("--cpu", type=Path, default=None)
	parser.add_argument("--json", action="store_true")
	args = parser.parse_args()

	if not args.reference.exists():
		raise SystemExit(f"reference PNG missing: {args.reference}")
	report: Dict[str, object] = {
		"fl": "FL-4537",
		"reference": str(args.reference),
		"reference_sha256": _sha256(args.reference),
		"cpu_floor": {
			"exact": CPU_FLOOR_EXACT,
			"pixel_count": CPU_FLOOR_TOTAL,
			"tolerance_48_ratio": CPU_FLOOR_TOL48_RATIO,
			"source": "docs/FAILURE_LOG.md FL-4537 CPU no-hack baseline",
		},
		"surfaces": [
			_surface_report("cpu_no_hack_image", args.reference, args.cpu),
			_surface_report("current_rd", args.reference, args.rd),
		],
	}
	for surface in report["surfaces"]:
		if surface.get("status") == "compared" and surface.get("name") == "current_rd":
			exact = int(surface["exact"])
			tol48 = float(surface["tolerances"]["48"]["ratio"])
			if exact < CPU_FLOOR_EXACT:
				surface["floor_verdict"] = "RD BELOW CPU FLOOR"
			elif exact == CPU_FLOOR_EXACT:
				surface["floor_verdict"] = "RD EQUAL CPU FLOOR"
			else:
				surface["floor_verdict"] = "RD ABOVE CPU FLOOR"
			surface["tol48_floor_delta"] = tol48 - CPU_FLOOR_TOL48_RATIO

	if args.json:
		print(json.dumps(report, indent=2))
	else:
		_print_human(report)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
