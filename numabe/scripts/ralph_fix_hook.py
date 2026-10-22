#!/usr/bin/env python3
"""Ralph fix hook — analyzes previous iteration failures and suggests/applies fixes.

Called by ralph_loop_multiplayer.sh between iterations.
Reads metrics.json from the previous run, identifies failed gates,
and prints diagnostics. Extensible: add auto-fix functions per gate.
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def analyze(metrics: dict, iteration: int, root: Path) -> dict:
    """Return dict with 'lines' (list[str]) and 'summary' (dict)."""
    gates = metrics.get("gates", {})
    diag = metrics.get("diagnostics", {})
    errors = metrics.get("errors", [])

    failed = [k for k, v in gates.items() if v is not True]
    passed = [k for k, v in gates.items() if v is True]

    lines = []

    lines.append(f"[fix-hook] iteration {iteration}, analyzing previous run")
    lines.append(f"[fix-hook] passed={len(passed)} failed={len(failed)}")
    if failed:
        lines.append(f"[fix-hook] failed gates: {failed}")
    if errors:
        lines.append(f"[fix-hook] errors: {errors[:3]}")

    # --- Gate-specific diagnostics ---

    if "load_both_world" in failed:
        lines.append("[fix-hook] CRITICAL: world never loaded — check WASM boot, fonts, WebSocket")
        lines.append("[fix-hook] action: none (requires manual investigation)")

    if "pickup_signal_observed" in failed:
        pickups = diag.get("pickup_signals", [])
        codes = [s.get("code") for s in pickups if isinstance(s, dict)]
        fail_class = diag.get("pickup_failure_class", "unknown")
        lines.append(f"[fix-hook] pickup: all codes={codes} class={fail_class}")
        if fail_class == "no_local_items_in_range":
            lines.append("[fix-hook] pickup: no local items near player (world content gap), not a code bug")
        elif fail_class == "still_authoritative_mode":
            lines.append("[fix-hook] pickup: mode enforcement failed (infra gap)")
        elif all(c == -3 for c in codes):
            lines.append("[fix-hook] pickup: code -3 = authoritative mode active, no items")
            lines.append("[fix-hook] pickup: players may not be near world items")
            lines.append("[fix-hook] suggestion: move players toward known item spawn area before pickup")
        elif fail_class == "items_found_but_pick_failed":
            lines.append("[fix-hook] pickup: items found but PickItem() failed — likely a code bug")

    if "combat_signal_observed" in failed:
        hp_delta = diag.get("hp_delta_tab2", 0)
        swing_delta = diag.get("combat_swing_delta", 0)
        dmg_delta = diag.get("combat_damage_delta", 0)
        conv = diag.get("convergence_teleport", {})
        lines.append(f"[fix-hook] combat: hp_delta={hp_delta} swing_delta={swing_delta} dmg_delta={dmg_delta}")
        if conv:
            lines.append(f"[fix-hook] combat: convergence={conv}")
        if hp_delta == 0 and swing_delta == 0 and dmg_delta == 0:
            lines.append("[fix-hook] combat: no damage registered — players may be too far apart")
            lines.append("[fix-hook] suggestion: increase movement duration before combat phase")

    jitter1 = diag.get("jitter_tab1", {})
    jitter2 = diag.get("jitter_tab2", {})
    if "tab1_jitter_ok" in failed or "tab2_jitter_ok" in failed:
        s1 = jitter1.get("score", "?")
        s2 = jitter2.get("score", "?")
        lines.append(f"[fix-hook] jitter: tab1_score={s1} tab2_score={s2}")
        lines.append("[fix-hook] jitter: score now reflects geometric wobble around the path, not raw speed variance")

    if "remote_visible_bidirectional" in failed:
        lines.append("[fix-hook] visibility: players can't see each other")
        lines.append("[fix-hook] visibility: may be timing — remote sprite loading is async")

    if not failed:
        lines.append("[fix-hook] all gates passed! no fixes needed")

    summary = {
        "passed_count": len(passed),
        "failed_count": len(failed),
        "passed_gates": passed,
        "failed_gates": failed,
    }
    return {"lines": lines, "summary": summary}


def main():
    parser = argparse.ArgumentParser(description="Ralph fix hook")
    parser.add_argument("--metrics", required=True, help="Path to previous metrics.json")
    parser.add_argument("--iter", type=int, required=True, help="Current iteration number")
    parser.add_argument("--root", required=True, help="Project root directory")
    parser.add_argument("--run-log", default=None, help="Path to JSONL run log for regression tracking")
    args = parser.parse_args()

    metrics_path = Path(args.metrics)
    if not metrics_path.exists():
        print(f"[fix-hook] ERROR: metrics not found: {metrics_path}", file=sys.stderr)
        sys.exit(1)

    metrics = json.loads(metrics_path.read_text())
    root = Path(args.root)

    result = analyze(metrics, args.iter, root)
    for line in result["lines"]:
        print(line)

    # --- JSONL run log with regression detection ---
    if args.run_log:
        summary = result["summary"]
        diag = metrics.get("diagnostics", {})
        try:
            commit = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"], cwd=str(root)
            ).decode().strip()
        except Exception:
            commit = "unknown"

        entry = {
            "iteration": args.iter,
            "timestamp": datetime.now().isoformat(),
            "commit": commit,
            "passed": summary["passed_count"],
            "failed": summary["failed_count"],
            "failed_gates": summary["failed_gates"],
            "diagnostics_summary": {
                "pickup_codes": [s.get("code") for s in diag.get("pickup_signals", []) if isinstance(s, dict)],
                "pickup_failure_class": diag.get("pickup_failure_class"),
                "hp_delta": diag.get("hp_delta_tab2", 0),
                "combat_swing_delta": diag.get("combat_swing_delta", 0),
                "combat_damage_delta": diag.get("combat_damage_delta", 0),
                "gate_tier_summary": diag.get("gate_tier_summary"),
            }
        }

        run_log_path = Path(args.run_log)
        run_log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(run_log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

        # Regression detection: compare with previous entry
        try:
            prev_entries = [json.loads(line) for line in run_log_path.read_text().splitlines() if line.strip()]
            if len(prev_entries) >= 2:
                prev = prev_entries[-2]
                prev_failed = set(prev.get("failed_gates", []))
                curr_failed = set(summary["failed_gates"])
                regressions = curr_failed - prev_failed
                for gate in sorted(regressions):
                    print(f"[fix-hook] REGRESSION: {gate} was passing, now failing")
        except Exception:
            pass


if __name__ == "__main__":
    main()
