#!/usr/bin/env python3
"""FL-4131 W2 — legacy CP437 no-diff proof harness.

Purpose:
  Prove that the existing CP437-only XP corpus (assets/sprites/**/*.xp) is
  byte-equivalent AND structurally equivalent before/after FL-4131 changes.

  This is the executable proof surface for two FL-4131 gates:
    - legacy_cp437_unchanged
    - legacy_assets_no_diff_after_font_capacity

CARDINAL RULES (FL-4131):
  - This harness ONLY operates on CP437-only assets. Any .xp with a paired
    .sidecar.json is skipped (it is extended-glyph territory and not part
    of the legacy contract).
  - Any cell with glyph > 255 disqualifies the .xp from baseline membership
    (defense-in-depth: even without a sidecar, an out-of-range glyph means
    the source is not legacy CP437).
  - This harness MUST NOT modify any input file.
  - This harness MUST NOT admit GlyphId > 255 into runtime/material/sprite
    cells. It only reads files.

Front door:
  python3 scripts/fl4131_legacy_cp437_no_diff.py --baseline
      Write the baseline manifest to assets/glyphs/fixtures/legacy_cp437_baseline.json
      after confirming the operator wants to overwrite. Used once to freeze
      the legacy corpus state.

  python3 scripts/fl4131_legacy_cp437_no_diff.py --verify
      Read the baseline manifest and compare against the current corpus.
      Exit 0 if every legacy .xp still matches both its byte hash and
      structural hash. Exit non-zero (and emit a JSON diff report) if not.

  python3 scripts/fl4131_legacy_cp437_no_diff.py --snapshot
      Emit the current snapshot to stdout as JSON without writing or
      comparing. Useful for debugging.

Output fields per .xp:
  path                : repo-relative path
  bytes_sha256        : SHA-256 of file bytes (proves the file on disk is unchanged)
  structural_sha256   : SHA-256 of canonical loader output (proves XPFile parses
                        to the same cell grid)
  layer_count         : number of layers
  layer_geometry      : list of [width, height]
  cp437_only          : True iff every cell has glyph in [0, 255]
  worst_glyph         : maximum glyph value seen (sanity check)
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
SPRITES_DIR = REPO_ROOT / "assets" / "sprites"
BASELINE_PATH = REPO_ROOT / "assets" / "glyphs" / "fixtures" / "legacy_cp437_baseline.json"

# scripts/pipeline lives outside the import path by default; scripts/ is added
# so we can pull SIDECAR_SUFFIX from the canonical glyph_sidecar module.
sys.path.insert(0, str(REPO_ROOT / "scripts" / "pipeline"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

try:
    from xp_core import XPFile  # type: ignore
except Exception as exc:  # pragma: no cover - import fail is fatal
    print(f"[FATAL] cannot import xp_core: {exc}", file=sys.stderr)
    raise

try:
    from glyph_sidecar import SIDECAR_SUFFIX  # type: ignore
except Exception as exc:  # pragma: no cover - import fail is fatal
    print(f"[FATAL] cannot import SIDECAR_SUFFIX from glyph_sidecar: {exc}", file=sys.stderr)
    raise


@dataclass
class XpRecord:
    path: str
    bytes_sha256: str
    structural_sha256: str
    layer_count: int
    layer_geometry: list[list[int]]
    cp437_only: bool
    worst_glyph: int


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _structural_sha256(xp: XPFile) -> tuple[str, list[list[int]], int, bool]:
    """Compute a canonical structural hash of an XPFile.

    Encoding: for each layer, emit width/height, then iterate row-major over
    cells emitting (glyph, fg_r, fg_g, fg_b, bg_r, bg_g, bg_b) as little-endian
    bytes (glyph is uint32 to allow >255 without crashing; the cp437_only
    flag separately reports whether any cell actually exceeds 255).
    """
    h = hashlib.sha256()
    geometry: list[list[int]] = []
    worst_glyph = 0
    cp437_only = True
    for layer in xp.layers:
        w = int(layer.width)
        ht = int(layer.height)
        geometry.append([w, ht])
        h.update(w.to_bytes(4, "little", signed=False))
        h.update(ht.to_bytes(4, "little", signed=False))
        for row in layer.data:
            for cell in row:
                glyph, fg, bg = cell
                gi = int(glyph) & 0xFFFFFFFF
                if gi > worst_glyph:
                    worst_glyph = gi
                if gi > 255:
                    cp437_only = False
                h.update(gi.to_bytes(4, "little", signed=False))
                fr, fg_, fb = (int(fg[0]) & 0xFF, int(fg[1]) & 0xFF, int(fg[2]) & 0xFF)
                br, bg_, bb = (int(bg[0]) & 0xFF, int(bg[1]) & 0xFF, int(bg[2]) & 0xFF)
                h.update(bytes((fr, fg_, fb, br, bg_, bb)))
    return h.hexdigest(), geometry, worst_glyph, cp437_only


def _discover_xp(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.xp") if p.is_file())


def _has_sidecar(xp_path: Path) -> bool:
    # Canonical sidecar suffix lives in scripts/glyph_sidecar.py (currently
    # ".glyph_profile.json"). Earlier versions of this harness used the wrong
    # literal ".sidecar.json" and would never detect a real sidecar.
    return xp_path.with_name(xp_path.name + SIDECAR_SUFFIX).exists()


def _record_for(xp_path: Path) -> tuple[XpRecord | None, str | None]:
    if _has_sidecar(xp_path):
        return None, "sidecar_present"
    try:
        # xp_core's XPFile() prints "Loading ..." to stdout; suppress it so
        # the harness's own JSON output stays clean.
        with contextlib.redirect_stdout(io.StringIO()):
            xp = XPFile(str(xp_path))
    except Exception as exc:
        return None, f"load_error: {exc}"
    try:
        struct_hash, geometry, worst, cp437_only = _structural_sha256(xp)
    except Exception as exc:
        return None, f"struct_error: {exc}"
    rel = str(xp_path.relative_to(REPO_ROOT))
    rec = XpRecord(
        path=rel,
        bytes_sha256=_file_sha256(xp_path),
        structural_sha256=struct_hash,
        layer_count=len(xp.layers),
        layer_geometry=geometry,
        cp437_only=cp437_only,
        worst_glyph=worst,
    )
    if not cp437_only:
        return None, f"glyph_above_255 (worst={worst}, sample disqualified)"
    return rec, None


def snapshot_corpus() -> dict[str, Any]:
    """Return the in-memory snapshot of all legacy CP437 XPs."""
    xp_paths = _discover_xp(SPRITES_DIR)
    records: list[XpRecord] = []
    skipped: list[dict[str, str]] = []
    for xp in xp_paths:
        rec, why = _record_for(xp)
        if rec is None:
            skipped.append({"path": str(xp.relative_to(REPO_ROOT)), "reason": why or ""})
        else:
            records.append(rec)
    records.sort(key=lambda r: r.path)
    skipped.sort(key=lambda s: s["path"])
    return {
        "_comment": (
            "FL-4131 W2 legacy CP437 no-diff baseline. NOT a glyph manifest. "
            "The leading underscore key is the convention compile_glyph_manifest.py "
            "uses to skip non-manifest JSON files in assets/glyphs/fixtures/."
        ),
        "version": 1,
        "fl": "FL-4131",
        "gate": "legacy_cp437_unchanged",
        "corpus_root": str(SPRITES_DIR.relative_to(REPO_ROOT)),
        "record_count": len(records),
        "skip_count": len(skipped),
        "records": [asdict(r) for r in records],
        "skipped": skipped,
    }


def write_baseline(snapshot: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(snapshot, indent=2, sort_keys=True) + "\n"
    path.write_text(payload, encoding="utf-8")


def _git_tracked_sprite_paths() -> set[str]:
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--", "assets/sprites/*.xp"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return set()
    if proc.returncode != 0:
        return set()
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def _filter_records_to_paths(records: Iterable[dict[str, Any]], tracked_paths: set[str] | None) -> dict[str, dict[str, Any]]:
    if tracked_paths is None:
        return {r["path"]: r for r in records}
    return {r["path"]: r for r in records if r.get("path") in tracked_paths}


def diff_against_baseline(
    snapshot: dict[str, Any],
    baseline: dict[str, Any],
    tracked_paths: set[str] | None = None,
) -> dict[str, Any]:
    cur_index = _filter_records_to_paths(snapshot.get("records", []), tracked_paths)
    base_index = _filter_records_to_paths(baseline.get("records", []), tracked_paths)
    missing_in_current = sorted(set(base_index) - set(cur_index))
    added_in_current = sorted(set(cur_index) - set(base_index))
    byte_mismatches: list[dict[str, str]] = []
    structural_mismatches: list[dict[str, str]] = []
    for path in sorted(set(cur_index) & set(base_index)):
        cur = cur_index[path]
        base = base_index[path]
        if cur["bytes_sha256"] != base["bytes_sha256"]:
            byte_mismatches.append({
                "path": path,
                "baseline_bytes_sha256": base["bytes_sha256"],
                "current_bytes_sha256": cur["bytes_sha256"],
            })
        if cur["structural_sha256"] != base["structural_sha256"]:
            structural_mismatches.append({
                "path": path,
                "baseline_structural_sha256": base["structural_sha256"],
                "current_structural_sha256": cur["structural_sha256"],
            })
    ok = (
        not missing_in_current
        and not added_in_current
        and not byte_mismatches
        and not structural_mismatches
    )
    return {
        "ok": ok,
        "missing_in_current": missing_in_current,
        "added_in_current": added_in_current,
        "byte_mismatches": byte_mismatches,
        "structural_mismatches": structural_mismatches,
        "baseline_record_count": baseline.get("record_count"),
        "current_record_count": snapshot.get("record_count"),
        "baseline_compared_record_count": len(base_index),
        "current_compared_record_count": len(cur_index),
        "tracked_filter_applied": tracked_paths is not None,
    }


def cmd_baseline(args: argparse.Namespace) -> int:
    if BASELINE_PATH.exists() and not args.force:
        print(
            f"[ERROR] baseline already exists at {BASELINE_PATH.relative_to(REPO_ROOT)}; "
            "pass --force to overwrite.",
            file=sys.stderr,
        )
        return 2
    snapshot = snapshot_corpus()
    write_baseline(snapshot, BASELINE_PATH)
    print(
        f"[BASELINE] wrote {snapshot['record_count']} records "
        f"(skipped {snapshot['skip_count']}) to "
        f"{BASELINE_PATH.relative_to(REPO_ROOT)}"
    )
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    if not BASELINE_PATH.exists():
        print(
            f"[ERROR] baseline not found at {BASELINE_PATH.relative_to(REPO_ROOT)}; "
            "run --baseline once first.",
            file=sys.stderr,
        )
        return 3
    try:
        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        # Distinct exit code (4) so an orchestrator can tell baseline-corruption
        # apart from diff-FAIL (exit 1).
        print(
            f"[ERROR] baseline at {BASELINE_PATH.relative_to(REPO_ROOT)} could not be read: {exc}",
            file=sys.stderr,
        )
        return 4
    snapshot = snapshot_corpus()
    tracked_paths = _git_tracked_sprite_paths()
    diff = diff_against_baseline(snapshot, baseline, tracked_paths=tracked_paths or None)
    if args.json:
        print(json.dumps(diff, indent=2, sort_keys=True))
    else:
        print(
            f"baseline records: {diff['baseline_record_count']}  "
            f"current records: {diff['current_record_count']}"
        )
        if diff["tracked_filter_applied"]:
            print(
                f"tracked records compared: baseline={diff['baseline_compared_record_count']}  "
                f"current={diff['current_compared_record_count']}"
            )
        for label, items in (
            ("missing_in_current", diff["missing_in_current"]),
            ("added_in_current", diff["added_in_current"]),
            ("byte_mismatches", diff["byte_mismatches"]),
            ("structural_mismatches", diff["structural_mismatches"]),
        ):
            if items:
                print(f"  {label} ({len(items)}):")
                for item in items[:20]:
                    print(f"    - {item}")
                if len(items) > 20:
                    print(f"    ... +{len(items) - 20} more")
        print()
        print("VERDICT:", "PASS" if diff["ok"] else "FAIL")
    return 0 if diff["ok"] else 1


def cmd_snapshot(args: argparse.Namespace) -> int:
    snapshot = snapshot_corpus()
    if args.summary:
        # Compact summary
        print(json.dumps({
            "record_count": snapshot["record_count"],
            "skip_count": snapshot["skip_count"],
            "first_5_records": [r["path"] for r in snapshot["records"][:5]],
            "first_5_skips": snapshot["skipped"][:5],
        }, indent=2, sort_keys=True))
    else:
        print(json.dumps(snapshot, indent=2, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--baseline", action="store_true", help="write the baseline manifest")
    mode.add_argument("--verify", action="store_true", help="compare current corpus against baseline")
    mode.add_argument("--snapshot", action="store_true", help="emit current corpus as JSON")
    parser.add_argument("--force", action="store_true", help="overwrite existing baseline")
    parser.add_argument("--json", action="store_true", help="machine-readable verify output")
    parser.add_argument("--summary", action="store_true", help="emit compact summary (snapshot mode)")
    args = parser.parse_args()
    if args.baseline:
        return cmd_baseline(args)
    if args.verify:
        return cmd_verify(args)
    if args.snapshot:
        return cmd_snapshot(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
