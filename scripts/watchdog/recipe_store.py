#!/usr/bin/env python3
"""Declarative recipe store and post-run recipe capture for controller actions.

Watchdog corpus, plain English:

The watchdog corpus is the set of saved two-player test runs and input scripts
used to reproduce multiplayer behavior in a browser. A stored recipe is a punch
card: it says what a human-like controller should press, click, or wait for
after the game has already launched.

The recipe store keeps reusable input scripts captured from manual watchdog
runs. A stored recipe should describe how to recreate behavior, not how to prove
it. Do not store hidden pass criteria, analyzer gates, run-label changes, or
game-state mutation here. If a new case needs proof logic, add that to the
analyzer side.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent.parent  # watchdog/ -> scripts/
REPO_ROOT = SCRIPTS_DIR.parent                        # scripts/ -> repo root
RECIPE_DIR = SCRIPTS_DIR / "watchdog_recipes"
RUNS_DIR = REPO_ROOT / "artifacts" / "maintainer" / "watchdog_runs"
ARCHIVE_DIR = REPO_ROOT / "artifacts" / "maintainer" / "watchdog_archive"


class RecipeStoreError(RuntimeError):
    """Raised when a stored controller recipe is missing or malformed."""


def recipe_dir() -> Path:
    return RECIPE_DIR


def recipe_path(name: str) -> Path:
    safe = (name or "").strip()
    if not safe:
        raise RecipeStoreError("recipe name is required")
    return recipe_dir() / f"{safe}.json"


def list_recipe_names() -> list[str]:
    if not recipe_dir().exists():
        return []
    return sorted(path.stem for path in recipe_dir().glob("*.json") if path.is_file())


def latest_recipe_name() -> str | None:
    """Return the most recently modified recipe name, or None if store is empty."""
    rd = recipe_dir()
    if not rd.exists():
        return None
    files = [p for p in rd.glob("*.json") if p.is_file()]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime).stem


def load_recipe(name: str) -> dict[str, Any]:
    path = recipe_path(name)
    if not path.exists():
        raise RecipeStoreError(
            f"recipe '{name}' not found in {recipe_dir()}; available={', '.join(list_recipe_names()) or '(none)'}"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RecipeStoreError(f"recipe '{name}' is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RecipeStoreError(f"recipe '{name}' must be a JSON object")
    if payload.get("name") not in (None, "", name):
        raise RecipeStoreError(
            f"recipe '{name}' has mismatched name field '{payload.get('name')}'"
        )
    payload["name"] = name
    steps = payload.get("steps")
    if not isinstance(steps, list) or not steps:
        raise RecipeStoreError(f"recipe '{name}' must contain a non-empty steps[] array")
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise RecipeStoreError(f"recipe '{name}' step {index} must be an object")
        kind = str(step.get("kind") or "").strip()
        if not kind:
            raise RecipeStoreError(f"recipe '{name}' step {index} is missing kind")
    return payload


MIN_SLEEP_MS = 50
DEFAULT_HOLD_MS = 120


def _tab_number_from_sample(sample: dict[str, Any]) -> int | None:
    tab = sample.get("tab")
    if isinstance(tab, int) and tab in (1, 2):
        return tab
    tab_id = sample.get("tab_id")
    if isinstance(tab_id, str):
        normalized = tab_id.strip().lower()
        if normalized == "tab1":
            return 1
        if normalized == "tab2":
            return 2
    return None


def _relative_sample_seconds(
    sample: dict[str, Any],
    *,
    baseline_wall_ms: int | None,
    baseline_probe_ms: int | None,
) -> tuple[float, str] | tuple[None, None]:
    rel_s = sample.get("rel_s")
    try:
        return float(rel_s), "rel_s"
    except (TypeError, ValueError):
        pass

    wall_ms = sample.get("wall_ms")
    if baseline_wall_ms is not None:
        try:
            return max(0.0, (float(wall_ms) - float(baseline_wall_ms)) / 1000.0), "wall_ms"
        except (TypeError, ValueError):
            pass

    probe_ms = sample.get("probe_updated_at")
    if baseline_probe_ms is not None:
        try:
            return max(0.0, (float(probe_ms) - float(baseline_probe_ms)) / 1000.0), "probe_updated_at"
        except (TypeError, ValueError):
            pass

    return None, None


def _run_summary_path(run_id: str) -> Path:
    if not run_id:
        raise RecipeStoreError("run_id is required")
    summary = RUNS_DIR / run_id / "summary.json"
    if summary.exists():
        return summary
    raise RecipeStoreError(f"run '{run_id}' not found under {RUNS_DIR}")


def _load_run_summary(run_id: str) -> dict[str, Any]:
    summary_path = _run_summary_path(run_id)
    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RecipeStoreError(f"run '{run_id}' summary is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RecipeStoreError(f"run '{run_id}' summary is not a JSON object")
    return payload


def _recording_root_candidates(run: dict[str, Any]) -> list[Path]:
    candidates: list[Path] = []
    seen: set[Path] = set()

    def add(path_value: object) -> None:
        raw = str(path_value or "").strip()
        if not raw:
            return
        path = Path(raw)
        if path in seen:
            return
        seen.add(path)
        candidates.append(path)

    add(run.get("artifact_path"))
    add(run.get("archive_path"))
    add(run.get("launcher_output_dir"))

    for key in ("artifact_path", "launcher_output_dir"):
        raw = str(run.get(key) or "").strip()
        if not raw:
            continue
        add(ARCHIVE_DIR / Path(raw).name)

    return candidates


def _artifact_dir_for_run(run_id: str) -> Path:
    run = _load_run_summary(run_id)
    searched: list[str] = []
    for base in _recording_root_candidates(run):
        rec_path = base / "recorder" / "recording.jsonl"
        searched.append(str(rec_path))
        if rec_path.exists():
            return base

    artifact = run.get("artifact_path")
    if not artifact:
        raise RecipeStoreError(f"run '{run_id}' has no artifact_path in summary.json")
    if searched:
        raise RecipeStoreError(
            f"recording.jsonl not found for run '{run_id}'; searched: {', '.join(searched)}"
        )
    raise RecipeStoreError(f"artifact directory for run '{run_id}' not found locally: {artifact}")


def _load_recording_samples(run_id: str) -> list[dict[str, Any]]:
    rec_path = _artifact_dir_for_run(run_id) / "recorder" / "recording.jsonl"
    if not rec_path.exists():
        raise RecipeStoreError(f"recording.jsonl not found for run '{run_id}': {rec_path}")
    samples: list[dict[str, Any]] = []
    try:
        with open(rec_path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") not in ("meta", "event"):
                    samples.append(obj)
    except OSError as exc:
        raise RecipeStoreError(f"failed reading {rec_path}: {exc}") from exc
    return samples


def _sample_rel_ms(sample: dict[str, Any]) -> int:
    rel_s = sample.get("rel_s", 0.0)
    try:
        return int(round(float(rel_s) * 1000.0))
    except (TypeError, ValueError):
        return 0


def _playwright_key_for_event(key: object, kind: object) -> str | None:
    if not isinstance(key, int):
        return None
    if kind == 2:
        if key == 32:
            return "Space"
        if key in (10, 13):
            return "Enter"
        if key == 8:
            return "Backspace"
        return None
    specials = {
        1: "Backspace",
        2: "Tab",
        3: "Enter",
        5: "Escape",
        6: "Space",
        7: "PageUp",
        8: "PageDown",
        9: "End",
        10: "Home",
        11: "ArrowLeft",
        12: "ArrowUp",
        13: "ArrowRight",
        14: "ArrowDown",
    }
    if key in specials:
        return specials[key]
    if 18 <= key <= 27:
        return f"Digit{key - 18}"
    if 28 <= key <= 53:
        return f"Key{chr(ord('A') + (key - 28))}"
    return None


def recipe_from_run(
    run_id: str,
    *,
    recipe_name: str,
    tab_filter: int | None = None,
    description: str = "",
    related_fl: list[str] | None = None,
    min_sleep_ms: int = MIN_SLEEP_MS,
    default_hold_ms: int = DEFAULT_HOLD_MS,
    from_rel_s: float | None = None,
    to_rel_s: float | None = None,
) -> dict[str, Any]:
    samples = _load_recording_samples(run_id)
    tab_scope_baseline_wall_ms: int | None = None
    tab_scope_baseline_probe_ms: int | None = None
    for sample in samples:
        sample_tab = _tab_number_from_sample(sample)
        if tab_filter is not None and sample_tab != tab_filter:
            continue
        wall_ms = sample.get("wall_ms")
        if tab_scope_baseline_wall_ms is None:
            try:
                tab_scope_baseline_wall_ms = int(float(wall_ms))
            except (TypeError, ValueError):
                pass
        probe_ms = sample.get("probe_updated_at")
        if tab_scope_baseline_probe_ms is None:
            try:
                tab_scope_baseline_probe_ms = int(float(probe_ms))
            except (TypeError, ValueError):
                pass
        if tab_scope_baseline_wall_ms is not None and tab_scope_baseline_probe_ms is not None:
            break
    seen: set[tuple[int, int]] = set()
    raw_events: list[dict[str, Any]] = []
    skipped_non_press = 0
    skipped_unmapped = 0
    unmatched_ups = 0
    legacy_missing_tab = 0
    legacy_missing_rel_s = 0
    wall_clock_timing_samples = 0
    probe_clock_timing_samples = 0

    for sample in samples:
        tab = _tab_number_from_sample(sample)
        if tab is None:
            if tab_filter is None:
                legacy_missing_tab += 1
                continue
            tab = tab_filter
            legacy_missing_tab += 1
        if tab_filter is not None and tab != tab_filter:
            continue
        rel_s_f, timing_basis = _relative_sample_seconds(
            sample,
            baseline_wall_ms=tab_scope_baseline_wall_ms,
            baseline_probe_ms=tab_scope_baseline_probe_ms,
        )
        sample_ms: int | None = None
        if rel_s_f is not None:
            sample_ms = int(round(rel_s_f * 1000.0))
            if timing_basis == "wall_ms":
                wall_clock_timing_samples += 1
            elif timing_basis == "probe_updated_at":
                probe_clock_timing_samples += 1
        else:
            rel_s_f = 0.0
            legacy_missing_rel_s += 1
        if from_rel_s is not None and rel_s_f < from_rel_s:
            continue
        if to_rel_s is not None and rel_s_f > to_rel_s:
            continue
        for ev in sample.get("input_event_sample") or []:
            if not isinstance(ev, dict):
                continue
            seq = ev.get("seq")
            if not isinstance(seq, int) or seq <= 0:
                continue
            event_id = (tab, seq)
            if event_id in seen:
                continue
            seen.add(event_id)
            kind = ev.get("kind")
            key = ev.get("key")
            if kind not in (0, 1):
                skipped_non_press += 1
                continue
            mapped_key = _playwright_key_for_event(key, kind)
            if not mapped_key:
                skipped_unmapped += 1
                continue
            raw_events.append(
                {
                    "tab": tab,
                    "seq": seq,
                    "kind": int(kind),
                    "key": mapped_key,
                    "sample_ms": sample_ms,
                    "dt_ms": int(ev.get("dt_ms") or 0),
                    "auto_repeat": int(ev.get("auto_repeat") or 0),
                }
            )

    raw_events.sort(
        key=lambda ev: (
            ev["sample_ms"] if ev["sample_ms"] is not None else 10**12,
            ev["tab"],
            ev["seq"],
        )
    )
    pending_downs: dict[tuple[int, str], dict[str, Any]] = {}
    actions: list[dict[str, Any]] = []
    legacy_clock_ms = 0
    last_event_ms = 0

    for ev in raw_events:
        event_time_ms = ev["sample_ms"]
        if event_time_ms is None:
            legacy_clock_ms += ev["dt_ms"]
            event_time_ms = legacy_clock_ms
        last_event_ms = event_time_ms
        slot = (ev["tab"], ev["key"])
        if ev["kind"] == 0:
            if ev["auto_repeat"]:
                continue
            if slot in pending_downs:
                continue
            pending_downs[slot] = {**ev, "event_time_ms": event_time_ms}
            continue
        down_ev = pending_downs.pop(slot, None)
        if down_ev is None:
            unmatched_ups += 1
            continue
        try:
            up_dt_ms = max(0, int(ev["dt_ms"]))
        except (TypeError, ValueError):
            up_dt_ms = 0
        # The recorder timestamps input windows at sample time, so pairing
        # keydown/keyup by sample_ms can stretch holds across probe intervals.
        # Prefer the explicit keyup delta when it exists; fall back to sample
        # spacing only for legacy traces that lack per-event timing.
        duration_ms = up_dt_ms
        if duration_ms <= 0:
            duration_ms = max(0, event_time_ms - down_ev["event_time_ms"])
        if duration_ms <= 0:
            duration_ms = 16
        actions.append(
            {
                "at_ms": down_ev["event_time_ms"],
                "kind": "send-key",
                "tab": down_ev["tab"],
                "key": down_ev["key"],
                "duration_ms": duration_ms,
            }
        )

    unclosed_downs = len(pending_downs)
    for down_ev in pending_downs.values():
        duration_ms = max(default_hold_ms, last_event_ms - down_ev["event_time_ms"])
        actions.append(
            {
                "at_ms": down_ev["event_time_ms"],
                "kind": "send-key",
                "tab": down_ev["tab"],
                "key": down_ev["key"],
                "duration_ms": duration_ms,
            }
        )

    actions.sort(key=lambda action: (action["at_ms"], action["tab"], action["key"]))
    if not actions:
        raise RecipeStoreError(
            f"run '{run_id}' produced no mappable key down/up pairs in recording.jsonl"
        )

    recipe_steps: list[dict[str, Any]] = [{"kind": "validate-attach"}]
    last_end_ms = actions[0]["at_ms"]
    for action in actions:
        gap_ms = action["at_ms"] - last_end_ms
        if gap_ms > min_sleep_ms:
            recipe_steps.append({"kind": "sleep", "duration_ms": gap_ms})
        recipe_steps.append(
            {
                "kind": "send-key",
                "tab": action["tab"],
                "key": action["key"],
                "duration_ms": action["duration_ms"],
            }
        )
        last_end_ms = action["at_ms"] + action["duration_ms"]

    tab_label = "both-tabs" if tab_filter is None else f"tab{tab_filter}"
    recipe: dict[str, Any] = {
        "name": recipe_name,
        "description": description or f"Captured from run {run_id} ({tab_label})",
        "related_fl": related_fl or [],
        "source": "run-recording-input-events",
        "source_run_id": run_id,
        "source_tab_scope": tab_label,
        "source_window": {
            "from_rel_s": from_rel_s,
            "to_rel_s": to_rel_s,
        },
        "conversion_notes": {
            "min_sleep_ms": min_sleep_ms,
            "default_hold_ms": default_hold_ms,
            "mappable_key_pairs": len(actions),
            "skipped_non_press_events": skipped_non_press,
            "skipped_unmapped_keys": skipped_unmapped,
            "unmatched_key_ups": unmatched_ups,
            "unclosed_key_downs": unclosed_downs,
            "legacy_missing_tab_samples": legacy_missing_tab,
            "legacy_missing_rel_s_samples": legacy_missing_rel_s,
            "wall_clock_timing_samples": wall_clock_timing_samples,
            "probe_clock_timing_samples": probe_clock_timing_samples,
            "timing_basis": (
                "first sample rel_s where each input_event_sample seq appeared"
                if legacy_missing_rel_s == 0 and wall_clock_timing_samples == 0 and probe_clock_timing_samples == 0
                else (
                    "canonical fallback: sample wall_ms relative to first in-scope sample"
                    if wall_clock_timing_samples > 0
                    else (
                        "secondary fallback: sample probe_updated_at relative to first in-scope sample"
                        if probe_clock_timing_samples > 0
                        else "legacy fallback: cumulative input_event_sample dt_ms in unique event-seq order"
                    )
                )
            ),
        },
        "steps": recipe_steps,
    }
    return recipe


def save_recipe(recipe: dict[str, Any]) -> Path:
    """Write a recipe dict to the recipe store. Returns the written path."""
    name = (recipe.get("name") or "").strip()
    if not name:
        raise RecipeStoreError("recipe must have a name")
    path = recipe_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(recipe, indent=2) + "\n", encoding="utf-8")
    return path


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Store and derive declarative watchdog controller recipes"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List stored recipe names")

    show = sub.add_parser("show", help="Print one stored recipe JSON")
    show.add_argument("name")

    capture = sub.add_parser(
        "capture-from-run",
        help="Derive a declarative recipe from recorder input_event_sample data in a completed run",
    )
    capture.add_argument("run_id")
    capture.add_argument("--recipe-name", required=True)
    capture.add_argument(
        "--tab",
        choices=("both", "1", "2"),
        default="both",
        help="Which tab input stream to derive from (default: both).",
    )
    capture.add_argument("--description", default="")
    capture.add_argument("--related-fl", action="append", default=[])
    capture.add_argument("--from-rel-s", type=float, default=None)
    capture.add_argument("--to-rel-s", type=float, default=None)
    capture.add_argument("--min-sleep-ms", type=int, default=MIN_SLEEP_MS)
    capture.add_argument("--default-hold-ms", type=int, default=DEFAULT_HOLD_MS)
    capture.add_argument(
        "--stdout",
        action="store_true",
        help="Print the derived recipe JSON instead of saving it into scripts/watchdog_recipes/",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "list":
            for name in list_recipe_names():
                print(name)
            return 0
        if args.command == "show":
            _print_json(load_recipe(args.name))
            return 0
        if args.command == "capture-from-run":
            tab_filter = None if args.tab == "both" else int(args.tab)
            recipe = recipe_from_run(
                args.run_id,
                recipe_name=args.recipe_name,
                tab_filter=tab_filter,
                description=args.description,
                related_fl=args.related_fl,
                min_sleep_ms=args.min_sleep_ms,
                default_hold_ms=args.default_hold_ms,
                from_rel_s=args.from_rel_s,
                to_rel_s=args.to_rel_s,
            )
            if args.stdout:
                _print_json(recipe)
            else:
                path = save_recipe(recipe)
                print(path)
            return 0
    except RecipeStoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    raise AssertionError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
