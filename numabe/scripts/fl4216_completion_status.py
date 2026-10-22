#!/usr/bin/env python3
"""FL-4216 completion status report.

Authority: docs/FAILURE_LOG.md entries FL-4216, FL-4129, and FL-4208.
This script is a non-authoritative receipt inventory. It must not define
water-lane requirements, closure, acceptance, queue order, nor proof success.
The FL entries remain the only status authority.

Scans docs/research/ascii/verification/fl4216/water/ for step receipts,
classifies each step's status, runs the parent water validator + the
FL-4208 child gates, and emits a structured summary so the operator can
see what is landed, what is pending, and where the next action is.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RECEIPTS_ROOT = ROOT / "docs" / "research" / "ascii" / "verification" / "fl4216" / "water"
FINAL_RENDER_DUMP_ROOT = ROOT / ".run" / "final_render_cell_dump"
CLEAN_ARCHIVE_ROOT = ROOT / ".run" / "baseline_compositor_ab" / "current_source"
WATER_XDUMP_VALIDATOR = ROOT / "scripts" / "proofs" / "proof_fl4216_water_xdump.py"
WATER_XDUMP_POSE_MODULE = ROOT / "scripts" / "proofs" / "proof_fl4216_water_xdump.py"
STEP11_MANIFEST_ROOTS = [
    ROOT / ".run",
    RECEIPTS_ROOT,
]
REQUIRED_CLOSURE_POSES = [
    "actor_sink_deep",
    "open_reflection_source_backed",
    "shore_flat_line",
    "shore_grass_context",
    "wave_band_sweep",
]


STEP_DIRECTORIES = {
    "Step 0 plan freeze":                ("operator commit history", ["3fe5e78ea", "94d29a5f8"]),
    "Step 0+ source identity":           ("operator commit", ["a7ed6ca45"]),
    "Step 0+ drift dumpster":            ("operator commit", ["3f93aa22c"]),
    "Step 1 source health":              (["step01_source_health"], []),
    "Step 2 FL-4208 validator scaffold": (["step02_fl4208_validator", "step02_fl4208_validator_tests"], []),
    "Step 11 scaffold + parity":         (["step02_xdump_pair_writer", "step02_xdump_scaffold_parity"], []),
    "Step 2.5 perf instrumentation":     (["step02_5_perf_probe"], []),
    "Step 3 flat body coverage":         (["checkpoint_step03"], []),
    "Step 4 PixelWater profile":         (["checkpoint_step04"], []),
    "Step 5 refraction/depth/fog/detail": (["checkpoint_step05"], []),
    "Step 6 reflection + lighting":      (["checkpoint_step06"], []),
    "Step 7 X13 wave pass":              (["checkpoint_step07"], []),
    "Step 8 FL-4208 bucket table + resolvers": (["checkpoint_step08"], []),
    "Step 9 receipt admission":          (["assets/glyphs/generated/fl4208_water_role_scoring.jsonl"], []),
    "Step 10 actor sink row ownership":  (["checkpoint_step10"], []),
    "Step 11 water readback":            (["TBD"], []),
    "Step 12 perf budget gate":          (["checkpoint_step12"], []),
    "Step 13 scripted proof":            (["TBD"], []),
    "Step 14 headed manual proof":       (["TBD"], []),
    "Step 15 closure audit":             (["final_audit"], []),
    "Operator handoff brief":            (["operator_handoff"], []),
}


def _step_status(target: Any) -> dict[str, Any]:
    if isinstance(target, str) and target == "TBD":
        return {"status": "PENDING", "evidence": "no receipt directory yet"}
    if isinstance(target, list):
        landed: list[str] = []
        missing: list[str] = []
        for entry in target:
            if entry == "TBD":
                missing.append(entry)
                continue
            if entry.endswith(".jsonl"):
                path = ROOT / entry
                if path.exists():
                    landed.append(str(path.relative_to(ROOT)))
                else:
                    missing.append(entry)
                continue
            d = RECEIPTS_ROOT / entry
            if d.exists() and any(d.iterdir()):
                stamps = sorted([p.name for p in d.iterdir() if p.is_dir()])
                landed.append(f"{entry} ({len(stamps)} stamp{'s' if len(stamps) != 1 else ''})")
            else:
                missing.append(entry)
        if missing and not landed:
            return {"status": "PENDING", "evidence": missing}
        if missing:
            return {"status": "PARTIAL", "landed": landed, "missing": missing}
        if all(str(item).endswith(".jsonl") for item in landed):
            return {"status": "FILE_PRESENT", "evidence": landed}
        return {"status": "RECEIPT_ONLY", "evidence": landed}
    return {"status": "UNKNOWN", "evidence": str(target)}


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _relpath(path: Path) -> str:
    return str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path)


def _step11_launch_manifest_status(
    roots: list[Path] | None = None,
    required_pose_ids: list[str] | None = None,
) -> dict[str, Any]:
    scan_roots = roots if roots is not None else STEP11_MANIFEST_ROOTS
    required = required_pose_ids if required_pose_ids is not None else REQUIRED_CLOSURE_POSES
    manifests_by_pose: dict[str, dict[str, Any]] = {}
    invalid: list[str] = []
    for root in scan_roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("step11_launch_manifest.json")):
            payload = _read_json(path)
            if payload is None:
                invalid.append(_relpath(path))
                continue
            pose_id = payload.get("pose_id")
            command = payload.get("command")
            schema = payload.get("schema")
            if (
                schema != "fl4216_step11_launch_manifest.v1"
                or not isinstance(pose_id, str)
                or payload.get("will_launch") is not False
                or not isinstance(command, list)
                # FL-4216 repair 2026-08-19: the old marker was
                # "--fl4057-ab-x-dump", which Main.gd:486 now deletes and fails
                # closed on. Requiring it here made a manifest valid only when
                # its command could not run. The surviving pose-driving dump
                # route is the movement runner's --request-final-dump, which
                # reaches World._request_final_render_cell_dump.
                or "--request-final-dump" not in command
            ):
                invalid.append(_relpath(path))
                continue
            existing = manifests_by_pose.get(pose_id)
            if existing is None or _relpath(path) > str(existing.get("path", "")):
                manifests_by_pose[pose_id] = {
                    "path": _relpath(path),
                    "input_owner_kind": payload.get("input_owner_kind"),
                    "expected_dump_title_prefix": payload.get("expected_dump_title_prefix"),
                    "command": command,
                }
    missing = [pose_id for pose_id in required if pose_id not in manifests_by_pose]
    status = "FILE_PRESENT" if manifests_by_pose and not missing else "PARTIAL" if manifests_by_pose else "PENDING"
    evidence: dict[str, Any] = {
        "manifests": manifests_by_pose,
        "missing_required_pose_manifests": missing,
    }
    if invalid:
        evidence["invalid_manifests"] = invalid[:20]
    return {"status": status, "evidence": evidence}


def _git_commit_exists(commit: str) -> bool:
    res = subprocess.run(
        ["git", "rev-parse", "--verify", commit],
        cwd=ROOT,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return res.returncode == 0


def _operator_commits_status(commits: list[str]) -> dict[str, Any]:
    landed = [c for c in commits if _git_commit_exists(c)]
    missing = [c for c in commits if not _git_commit_exists(c)]
    if missing and not landed:
        return {"status": "PENDING", "evidence": missing}
    if missing:
        return {"status": "PARTIAL", "landed": landed, "missing": missing}
    return {"status": "COMMITTED", "evidence": landed}


def _run_validator(cmd: list[str], label: str) -> dict[str, Any]:
    res = subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return {
        "label": label,
        "command": " ".join(cmd),
        "exit_code": res.returncode,
        "stdout_tail": res.stdout.strip().splitlines()[-3:] if res.stdout else [],
        "stderr_tail": res.stderr.strip().splitlines()[-3:] if res.stderr else [],
    }


def _candidate_final_render_dumps(roots: list[Path] | None = None) -> list[Path]:
    # `roots` exists so the ambiguity contract can inject two fabricated dumps
    # without touching the real .run tree. Production callers pass nothing.
    if roots is not None:
        out: list[Path] = []
        for root in roots:
            if root.exists():
                out.extend(
                    p for p in root.iterdir()
                    if p.is_dir()
                    and (p / "metadata.json").exists()
                    and (p / "cells.jsonl").exists()
                )
        return sorted(out, key=lambda p: p.stat().st_mtime, reverse=True)
    candidates: list[Path] = []
    if FINAL_RENDER_DUMP_ROOT.exists():
        candidates.extend(
            p for p in FINAL_RENDER_DUMP_ROOT.iterdir()
            if p.is_dir() and (p / "metadata.json").exists() and (p / "cells.jsonl").exists()
        )
    if CLEAN_ARCHIVE_ROOT.exists():
        candidates.extend(
            p for p in CLEAN_ARCHIVE_ROOT.glob("*/.run/final_render_cell_dump/*")
            if p.is_dir() and (p / "metadata.json").exists() and (p / "cells.jsonl").exists()
        )
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)


def _latest_final_render_dump() -> Path | None:
    candidates = _candidate_final_render_dumps()
    return candidates[0] if candidates else None


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_water_pose_registry() -> dict[str, dict[str, Any]]:
    module = _load_water_proof_module()
    poses = getattr(module, "POSES", {}) if module is not None else {}
    return poses if isinstance(poses, dict) else {}


def _load_water_proof_module() -> Any:
    spec = importlib.util.spec_from_file_location("proof_fl4216_water_xdump", WATER_XDUMP_POSE_MODULE)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _current_water_source_state() -> dict[str, Any]:
    module = _load_water_proof_module()
    if module is not None and hasattr(module, "_repo_state"):
        state = module._repo_state()
        if isinstance(state, dict):
            return state
    return {
        "git_head": "",
        "repo_git_head": "",
        "dirty_files_relevant": ["water proof module source-state unavailable"],
        "dirty_files_runtime_relevant": ["water proof module source-state unavailable"],
        "dirty_files_proof_tool_relevant": [],
        "source_identity_scope": "unknown",
    }


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _metadata_matches_pose(
    metadata: dict[str, Any],
    pose: dict[str, Any],
    associated_dump: str | None = None,
    dump_dir_name: str | None = None,
) -> bool:
    """Match one dump against one required view.

    FL-4216 repair 2026-08-19. This used to key on a `dump_title` prefix, but
    NOTHING writes that field: the CLI flag that carried it has no engine
    consumer, and World's dump metadata records `proof_scope` instead. So the
    title condition could never be satisfied by any artifact current source can
    produce.

    The title cannot simply be dropped, because pose identity alone is NOT
    unique: `shore_flat_line` and `shore_grass_context` share player
    (155.5, 4.0, -0.5), yaw 0, viewport 1280x800 and declare no pitch, so one
    dump would silently satisfy both. The disambiguator is therefore the proof
    runner's own recorded pose_id -> dump association; the pose/viewport/yaw/
    pitch checks below remain as corroboration that the artifact really is that
    view.
    """
    if associated_dump is not None:
        if dump_dir_name is None or dump_dir_name != associated_dump:
            return False
    player_world = metadata.get("player_world")
    player = pose.get("player")
    if not isinstance(player_world, dict) or not isinstance(player, list) or len(player) != 3:
        return False
    actual = [player_world.get("x"), player_world.get("y"), player_world.get("z")]
    for expected, got in zip(player, actual):
        got_num = _number(got)
        if got_num is None or abs(got_num - float(expected)) > 0.05:
            return False
    viewport = pose.get("viewport")
    viewport_size = metadata.get("viewport_size")
    if isinstance(viewport, list) and isinstance(viewport_size, dict):
        if [viewport_size.get("w"), viewport_size.get("h")] != viewport:
            return False
    if "yaw" in pose:
        actual_yaw_rad = _number(metadata.get("camera_yaw"))
        if actual_yaw_rad is None:
            return False
        actual_yaw_deg = actual_yaw_rad * 180.0 / 3.141592653589793
        expected_yaw = float(pose["yaw"])
        delta = abs(((actual_yaw_deg - expected_yaw + 180.0) % 360.0) - 180.0)
        if delta > 1.0:
            return False
    if "camera_pitch_deg" in pose:
        actual_pitch_rad = _number(metadata.get("camera_pitch"))
        if actual_pitch_rad is None:
            return False
        actual_pitch_deg = actual_pitch_rad * 180.0 / 3.141592653589793
        if abs(actual_pitch_deg - float(pose["camera_pitch_deg"])) > 1.0:
            return False
    return True


def _pose_dump_associations(
    roots: list[Path] | None = None,
) -> dict[str, str]:
    """Read the proof runner's recorded pose_id -> dump directory association.

    FL-4216 repair follow-up, 2026-08-19. The previous change added an
    `associated_dump` parameter to `_metadata_matches_pose` but never supplied
    it from here, so the association it claimed did not exist and the two
    pose-identical shoreline views still resolved to the same newest dump.
    This is the missing half.

    The association is authored by the proof runner into its own launch
    manifest (`associated_dump`), because only the runner knows which pose it
    just launched. An absent association is an ABSENT observation, not a match:
    callers fall back to pose-only matching, which is sufficient for views
    whose pose is unique and deliberately insufficient for the two that share
    one.
    """
    scan_roots = roots if roots is not None else STEP11_MANIFEST_ROOTS
    out: dict[str, str] = {}
    for root in scan_roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("step11_launch_manifest.json")):
            payload = _read_json(path)
            if not isinstance(payload, dict):
                continue
            pose_id = payload.get("pose_id")
            associated = payload.get("associated_dump")
            if isinstance(pose_id, str) and isinstance(associated, str) and associated:
                out[pose_id] = associated
    return out


# Views whose frozen pose is NOT unique. Admitting either of these on pose
# alone would let ONE dump satisfy BOTH, which is a false green; they require
# an explicit association.
POSE_AMBIGUOUS_VIEWS = ("shore_flat_line", "shore_grass_context")


def _latest_dump_for_pose(
    pose_id: str,
    poses: dict[str, dict[str, Any]],
    associations: dict[str, str] | None = None,
    dump_roots: list[Path] | None = None,
) -> Path | None:
    pose = poses.get(pose_id)
    if not isinstance(pose, dict):
        return None
    assoc = (associations if associations is not None else _pose_dump_associations())
    associated = assoc.get(pose_id)
    if associated is None and pose_id in POSE_AMBIGUOUS_VIEWS:
        # Fail closed rather than admitting a dump that another view could
        # equally claim.
        return None
    for dump_dir in _candidate_final_render_dumps(dump_roots):
        metadata = _read_json_file(dump_dir / "metadata.json")
        if _metadata_matches_pose(metadata, pose, associated, dump_dir.name):
            return dump_dir
    return None


def _missing_required_pose_dump_failures(pose_id: str | None) -> list[str]:
    if pose_id is None:
        return ["no final_render_cell_dump metadata/cells artifact found"]
    pose = _load_water_pose_registry().get(pose_id, {})
    if not isinstance(pose, dict):
        return [f"no final_render_cell_dump metadata/cells artifact found for unknown pose {pose_id}"]
    title = pose.get("dump_title_prefix")
    player = pose.get("player")
    yaw = pose.get("yaw")
    viewport = pose.get("viewport")
    parts = [f"no required X-dump artifact found for {pose_id}"]
    if isinstance(title, str) and title:
        parts.append(f"dump_title prefix {title!r}")
    if isinstance(player, list):
        parts.append(f"player {player}")
    if yaw is not None:
        parts.append(f"yaw {yaw}")
    if isinstance(viewport, list):
        parts.append(f"viewport {viewport}")
    return ["; ".join(parts)]


def _run_water_xdump_validator(dump_dir: Path | None, pose_id: str | None = None) -> dict[str, Any]:
    current_source = _current_water_source_state()
    if dump_dir is None:
        payload = {
            "label": "FL-4216 water X-dump replacement validator",
            "exit_code": 1,
            "dump_dir": None,
            "verdict": "missing_dump",
            "failures": _missing_required_pose_dump_failures(pose_id),
            "warnings": [],
            "current_source_identity": current_source,
            "source_current_for_live_tree": False,
        }
        if pose_id is not None:
            payload["pose_id"] = pose_id
        return payload
    cmd = [sys.executable, str(WATER_XDUMP_VALIDATOR), "--validate-dump", str(dump_dir.relative_to(ROOT))]
    if pose_id is not None:
        cmd.extend(["--pose-id", pose_id])
    res = subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    payload: dict[str, Any]
    try:
        payload = json.loads(res.stdout)
    except json.JSONDecodeError:
        payload = {
            "verdict": "validator_output_parse_failed",
            "failures": [res.stdout.strip() or res.stderr.strip()],
            "warnings": [],
        }
    payload["label"] = "FL-4216 water X-dump replacement validator"
    payload["command"] = " ".join(cmd)
    payload["exit_code"] = res.returncode
    payload["dump_dir"] = str(dump_dir.relative_to(ROOT))
    metadata_head = str(payload.get("metadata_git_head", ""))
    repo_head = str(current_source.get("repo_git_head", current_source.get("git_head", "")))
    heads_match = bool(metadata_head and repo_head and (metadata_head.startswith(repo_head) or repo_head.startswith(metadata_head)))
    runtime_dirty = current_source.get("dirty_files_runtime_relevant", current_source.get("dirty_files_relevant", []))
    runtime_hash_mismatches = payload.get("runtime_source_hash_mismatches", [])
    runtime_hash_check_available = payload.get("runtime_source_hash_check_available") is True
    runtime_hash_current = runtime_hash_check_available and isinstance(runtime_hash_mismatches, list) and not runtime_hash_mismatches
    shader_drift = payload.get("water_shader_source_drift", {})
    water_shader_changed = (
        isinstance(shader_drift, dict)
        and isinstance(shader_drift.get("water_changed_lines"), int)
        and shader_drift.get("water_changed_lines", 0) > 0
    )
    source_current = (heads_match or runtime_hash_current) and not runtime_dirty and not water_shader_changed
    payload["current_source_identity"] = current_source
    payload["source_current_for_live_tree"] = source_current
    if not source_current:
        warnings = payload.get("warnings", [])
        if not isinstance(warnings, list):
            warnings = []
        if runtime_dirty:
            warnings.append("current runtime water source has relevant dirty files; dump is not closure-current")
        if not heads_match and not runtime_hash_current:
            warnings.append("dump git_head does not match current water source head")
        if not heads_match and runtime_hash_current:
            warnings.append("dump git_head differs, but runtime water source hashes match live source")
        if water_shader_changed:
            warnings.append("dump shader has water-owned source drift against live source")
        payload["warnings"] = warnings
    if pose_id is not None:
        payload["pose_id"] = pose_id
    return payload


def _apply_xdump_step_status(report: dict[str, Any], validator: dict[str, Any]) -> None:
    counts = validator.get("counts", {}) if isinstance(validator.get("counts"), dict) else {}
    failures = validator.get("failures", []) if isinstance(validator.get("failures"), list) else []
    warnings = validator.get("warnings", []) if isinstance(validator.get("warnings"), list) else []
    dump_dir = validator.get("dump_dir")
    proof_pass = (
        validator.get("exit_code") == 0
        and not failures
        and validator.get("verdict") == "proof_pass"
        and validator.get("source_current_for_live_tree") is True
    )

    if proof_pass:
        report["step_status"]["Step 11 water readback"] = {
            "status": "PROOF_PASS",
            "evidence": dump_dir,
            "counts": {
                "water_rows": counts.get("water_rows"),
                "shader_readback_rows": counts.get("shader_readback_rows"),
            },
        }
        report["step_status"]["Step 12 perf budget gate"] = {
            "status": "PROOF_PASS",
            "evidence": dump_dir,
        }
        report["step_status"]["Step 13 scripted proof"] = {
            "status": "PROOF_PASS",
            "evidence": dump_dir,
            "counts": counts,
        }
        report["step_status"]["Step 14 headed manual proof"] = {
            "status": "RECEIPT_ONLY",
            "evidence": dump_dir,
            "warnings": warnings,
        }
    else:
        report["step_status"]["Step 11 water readback"] = {
            "status": "PENDING",
            "evidence": dump_dir,
            "failures": failures,
        }


def _validator_closure_pass(validator: dict[str, Any]) -> bool:
    return _validator_evidence_pass(validator) and validator.get("artifact_unique") is not False


def _validator_evidence_pass(validator: dict[str, Any]) -> bool:
    failures = validator.get("failures", []) if isinstance(validator.get("failures"), list) else []
    return (
        validator.get("exit_code") == 0
        and not failures
        and validator.get("source_current_for_live_tree") is True
    )


def _validator_source_stale(validator: dict[str, Any]) -> bool:
    if validator.get("source_current_for_live_tree") is not False:
        return False
    if validator.get("verdict") == "missing_dump":
        return False
    if validator.get("dump_dir"):
        return True
    if validator.get("exit_code") == 0:
        return True
    if validator.get("runtime_source_hash_mismatches"):
        return True
    shader_drift = validator.get("water_shader_source_drift", {})
    return (
        isinstance(shader_drift, dict)
        and isinstance(shader_drift.get("water_changed_lines"), int)
        and shader_drift.get("water_changed_lines", 0) > 0
    )


def _validator_display_status(validator: dict[str, Any]) -> str:
	if _validator_evidence_pass(validator) and validator.get("artifact_unique") is False:
		return "DUPLICATE"
	if _validator_closure_pass(validator):
		return "PASS"
	if _validator_source_stale(validator):
		return "STALE"
	failures = validator.get("failures", []) if isinstance(validator.get("failures"), list) else []
	if validator.get("exit_code") == 0 and not failures:
		return "STALE"
	return "FAIL"


def _duplicate_required_pose_dump_groups(pose_validators: list[dict[str, Any]]) -> dict[str, list[str]]:
    by_dump: dict[str, list[str]] = {}
    for validator in pose_validators:
        if not _validator_evidence_pass(validator):
            continue
        dump_dir = validator.get("dump_dir")
        pose_id = validator.get("pose_id")
        if not isinstance(dump_dir, str) or not dump_dir:
            continue
        if not isinstance(pose_id, str) or not pose_id:
            continue
        by_dump.setdefault(dump_dir, []).append(pose_id)
    return {
        dump_dir: sorted(set(pose_ids))
        for dump_dir, pose_ids in by_dump.items()
        if len(set(pose_ids)) > 1
    }


def _annotate_required_pose_dump_uniqueness(pose_validators: list[dict[str, Any]]) -> None:
    duplicate_groups = _duplicate_required_pose_dump_groups(pose_validators)
    duplicate_pose_ids = {
        pose_id
        for pose_ids in duplicate_groups.values()
        for pose_id in pose_ids
    }
    for validator in pose_validators:
        pose_id = validator.get("pose_id")
        dump_dir = validator.get("dump_dir")
        if isinstance(pose_id, str) and pose_id in duplicate_pose_ids:
            validator["artifact_unique"] = False
            if isinstance(dump_dir, str):
                validator["shared_dump_pose_ids"] = duplicate_groups.get(dump_dir, [])
        elif _validator_evidence_pass(validator):
            validator["artifact_unique"] = True


def _required_pose_dump_duplicate_blockers(pose_validators: list[dict[str, Any]]) -> list[str]:
    by_dump = _duplicate_required_pose_dump_groups(pose_validators)
    blockers: list[str] = []
    for dump_dir, pose_ids in sorted(by_dump.items()):
        blockers.append(
            "required closure evidence views share one X-dump artifact: "
            f"{', '.join(pose_ids)} -> {dump_dir}"
        )
    return blockers


def _required_pose_dumps_unique(pose_validators: list[dict[str, Any]]) -> bool:
    return not _required_pose_dump_duplicate_blockers(pose_validators)


def _closure_blockers(pose_validators: list[dict[str, Any]], latest_validator: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    for validator in pose_validators:
        pose_id = str(validator.get("pose_id", "unknown_pose"))
        display_status = _validator_display_status(validator)
        if display_status != "PASS":
            blockers.append(f"required closure evidence view {pose_id} is {display_status}")
        failures = validator.get("failures", []) if isinstance(validator.get("failures"), list) else []
        if display_status != "STALE":
            for failure in failures[:3]:
                blockers.append(f"{pose_id}: {failure}")
    blockers.extend(_required_pose_dump_duplicate_blockers(pose_validators))
    if _validator_display_status(latest_validator) == "STALE":
        blockers.append("latest diagnostic X-dump is source-stale and cannot close FL-4216")
    runtime_hash_mismatches = latest_validator.get("runtime_source_hash_mismatches", [])
    if isinstance(runtime_hash_mismatches, list) and runtime_hash_mismatches:
        mismatch_preview = ", ".join(
            str(item.get("path", "unknown_path"))
            for item in runtime_hash_mismatches[:5]
            if isinstance(item, dict)
        )
        if mismatch_preview:
            blockers.append(f"latest diagnostic runtime source hash mismatch: {mismatch_preview}")
    proof_hash_mismatches = latest_validator.get("proof_tool_hash_mismatches", [])
    if isinstance(proof_hash_mismatches, list) and proof_hash_mismatches:
        proof_preview = ", ".join(
            str(item.get("path", "unknown_path"))
            for item in proof_hash_mismatches[:5]
            if isinstance(item, dict)
        )
        if proof_preview:
            blockers.append(f"latest diagnostic proof-tool hash mismatch: {proof_preview}")
    shader_drift = latest_validator.get("water_shader_source_drift", {})
    if isinstance(shader_drift, dict):
        water_changed = shader_drift.get("water_changed_lines", 0)
        if isinstance(water_changed, int) and water_changed > 0:
            water_tokens = shader_drift.get("water_tokens", [])
            token_preview = ", ".join(str(token) for token in water_tokens[:5]) if isinstance(water_tokens, list) else ""
            blockers.append(
                f"latest diagnostic shader water-owned drift: {water_changed} water-token changed lines ({token_preview})"
            )
    current_source = latest_validator.get("current_source_identity", {})
    if isinstance(current_source, dict):
        runtime_dirty = current_source.get("dirty_files_runtime_relevant", current_source.get("dirty_files_relevant", []))
        if isinstance(runtime_dirty, list) and runtime_dirty:
            dirty_preview = ", ".join(str(path) for path in runtime_dirty[:5])
            blockers.append(f"current runtime water source has relevant dirty files: {dirty_preview}")
        proof_dirty = current_source.get("dirty_files_proof_tool_relevant", [])
        if isinstance(proof_dirty, list) and proof_dirty:
            proof_dirty_preview = ", ".join(str(path) for path in proof_dirty[:5])
            blockers.append(f"current water proof tools have relevant dirty files: {proof_dirty_preview}")
    return list(dict.fromkeys(blockers))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FL-4216 completion status report")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON instead of human report")
    args = parser.parse_args(argv)

    report: dict[str, Any] = {
        "fl_id": "FL-4216",
        "schema": "fl4216_completion_status.v2",
        "step_status": {},
        "validators": [],
        "summary": {},
    }

    for step, (target, _commits) in STEP_DIRECTORIES.items():
        if isinstance(target, str) and target.startswith("operator commit"):
            report["step_status"][step] = _operator_commits_status(_commits)
        else:
            report["step_status"][step] = _step_status(target)
    report["step_status"]["Step 11 launch manifest"] = _step11_launch_manifest_status()

    pose_registry = _load_water_pose_registry()
    pose_validators: list[dict[str, Any]] = []
    for pose_id in REQUIRED_CLOSURE_POSES:
        dump_dir = _latest_dump_for_pose(pose_id, pose_registry)
        validator = _run_water_xdump_validator(dump_dir, pose_id)
        pose_validators.append(validator)
        report["validators"].append(validator)
    _annotate_required_pose_dump_uniqueness(pose_validators)
    latest_dump = _latest_final_render_dump()
    latest_validator = _run_water_xdump_validator(latest_dump)
    latest_validator["label"] = "FL-4216 latest X-dump diagnostic validator"
    report["validators"].append(latest_validator)
    required_pose_unique = _required_pose_dumps_unique(pose_validators)
    required_pose_pass = all(
        _validator_closure_pass(v) for v in pose_validators
    ) and required_pose_unique

    actor_sink_validator = next((v for v in pose_validators if v.get("pose_id") == "actor_sink_deep"), latest_validator)
    _apply_xdump_step_status(report, actor_sink_validator)
    report["step_status"]["Step 14 headed manual proof"] = {
        "status": "PROOF_PASS" if required_pose_pass else "PENDING",
        "evidence": {
            str(v.get("pose_id")): v.get("dump_dir")
            for v in pose_validators
        },
        "failures": {
            str(v.get("pose_id")): v.get("failures", [])
            for v in pose_validators
            if v.get("failures")
        },
        "warnings": {
            str(v.get("pose_id")): v.get("warnings", [])
            for v in pose_validators
            if v.get("warnings")
        },
    }

    report["summary"]["deleted_validator_gates"] = [
        "scripts/validate_godot_water_mechanics.py",
        "scripts/validate_fl4208_glyph_contract.py",
    ]

    counts: dict[str, int] = {
        "COMMITTED": 0,
        "FILE_PRESENT": 0,
        "PARTIAL": 0,
        "PENDING": 0,
        "PROOF_PASS": 0,
        "RECEIPT_ONLY": 0,
        "UNKNOWN": 0,
    }
    for step_state in report["step_status"].values():
        counts[step_state.get("status", "UNKNOWN")] = counts.get(step_state.get("status", "UNKNOWN"), 0) + 1
    report["summary"]["counts"] = counts
    report["summary"]["required_closure_poses"] = REQUIRED_CLOSURE_POSES
    report["summary"]["required_pose_validator_statuses"] = {
        str(v.get("pose_id")): _validator_display_status(v)
        for v in pose_validators
    }
    report["summary"]["required_pose_dump_unique"] = required_pose_unique
    report["summary"]["latest_diagnostic_validator_status"] = _validator_display_status(latest_validator)
    report["summary"]["latest_diagnostic_dump"] = latest_validator.get("dump_dir")
    report["summary"]["latest_diagnostic_verdict"] = latest_validator.get("verdict")
    report["summary"]["latest_diagnostic_counts"] = latest_validator.get("counts", {})
    report["summary"]["closure_blockers"] = _closure_blockers(pose_validators, latest_validator)
    report["summary"]["required_pose_validators_pass"] = required_pose_pass
    report["summary"]["all_validators_pass"] = required_pose_pass
    report["summary"]["completion_proof_available"] = required_pose_pass

    def _heads_match(validator: dict[str, Any]) -> bool:
        metadata_head = str(validator.get("metadata_git_head", ""))
        repo_head = str(validator.get("repo_git_head", ""))
        return bool(metadata_head and repo_head and (metadata_head.startswith(repo_head) or repo_head.startswith(metadata_head)))

    report["summary"]["closure_grade_identity"] = required_pose_pass and all(
        v.get("metadata_dirty") is False
        and _heads_match(v)
        for v in pose_validators
    )

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    print("=" * 70)
    print("FL-4216 COMPLETION STATUS")
    print("=" * 70)
    for step, state in report["step_status"].items():
        status = state.get("status", "UNKNOWN")
        sym = {"LANDED": "+", "PARTIAL": "~", "PENDING": "-", "UNKNOWN": "?"}.get(status, "?")
        print(f"  [{sym}] {step:55} {status}")
        if status in ("PARTIAL", "PENDING") and state.get("missing"):
            for m in state["missing"]:
                print(f"        missing: {m}")
    print()
    print("VALIDATORS")
    for validator in report["validators"]:
        status = _validator_display_status(validator)
        print(f"  [{status}] {validator.get('label')}")
        if validator.get("dump_dir"):
            print(f"      dump: {validator.get('dump_dir')}")
        if validator.get("verdict"):
            print(f"      verdict: {validator.get('verdict')}")
        for failure in validator.get("failures", [])[:5]:
            print(f"      failure: {failure}")
        for warning in validator.get("warnings", [])[:3]:
            print(f"      warning: {warning}")
    print("  [X] stale FL-4216/FL-4208 validators deleted")
    print("      replacement evidence gate is scripts/proofs/proof_fl4216_water_xdump.py")
    print()
    print("SUMMARY")
    for s, n in counts.items():
        print(f"  {s:10} {n}")
    print(f"  latest_diagnostic_validator_status: {report['summary']['latest_diagnostic_validator_status']}")
    for pose_id, status in report["summary"]["required_pose_validator_statuses"].items():
        print(f"  required_view {pose_id}: {status}")
    for blocker in report["summary"]["closure_blockers"][:8]:
        print(f"  blocker: {blocker}")
    print(f"  all_validators_pass: {report['summary']['all_validators_pass']}")
    print(f"  closure_grade_identity: {report['summary']['closure_grade_identity']}")
    return 0 if report["summary"]["all_validators_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
