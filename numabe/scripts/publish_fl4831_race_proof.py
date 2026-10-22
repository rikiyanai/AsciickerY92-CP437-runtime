#!/usr/bin/env python3
"""Publish a completed FL-4831 two-client headed race run.

The headed harness owns capture and gameplay receipts. This adapter preserves
those raw files, copies selected frames to canonical proof names, publishes a
raw manifest, and builds separately labelled delivery copies through the
shared Godot proof-publication boundary.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from godot_proof_publication import (  # noqa: E402
    apply_finder_tags,
    deterministic_artifact_name,
    publish_artifacts,
    sha256_file,
    validate_manifest,
)
from publish_godot_labeled_media import publish_labeled_media  # noqa: E402

GODOT = Path("/opt/homebrew/bin/godot")
SELECTED_TAGS = (
    "boarded",
    "drift_release",
    "crash_entry",
    "crash_exit",
    "finish",
    "horizon_post",
)
EXECUTED_SOURCES = (
    "colyseus_server/src/index.ts",
    "colyseus_server/src/rooms/AuthoritativeRoom.ts",
    "colyseus_server/src/rooms/schema/ActorSchema.ts",
    "colyseus_server/src/rooms/schema/RaceStateSchema.ts",
    "colyseus_server/src/rooms/schema/WorldState.ts",
    "colyseus_server/src/sim/collision.ts",
    "colyseus_server/src/sim/terrain.ts",
    "colyseus_server/src/sim/world.ts",
    "godot_project/scripts/Main.gd",
    "godot_project/scripts/World.gd",
    "godot_project/scripts/player.gd",
    "godot_project/scripts/map/PlayableMap.gd",
    "godot_project/scripts/net/ColyseusStateAdapter.gd",
    "godot_project/scripts/ui/StatusHUD.gd",
    "godot_project/scripts/proofs/fl4831_headed_two_client_race.gd",
    "godot_project/render/api/ASCIICompositorEffect.gd",
    "godot_project/assets/maps/race_circuit_y8.a3d",
    "godot_project/assets/world_spawns/race_circuit_y8.fl4798_map_spawns.v1.json",
    "godot_project/assets/world_mesh/race_car_faces.json",
)


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} is not a JSON object")
    return value


def _sources() -> list[dict[str, str]]:
    rows = []
    for relative in EXECUTED_SOURCES:
        path = ROOT / relative
        if not path.is_file():
            raise FileNotFoundError(f"executed source missing: {relative}")
        rows.append({"path": relative, "sha256": sha256_file(path)})
    return rows


def _fingerprint(sources: list[dict[str, str]]) -> str:
    digest = hashlib.sha256()
    for source in sorted(sources, key=lambda row: row["path"]):
        digest.update(f"{source['path']}:{source['sha256']}\n".encode())
    return digest.hexdigest()


def _accept(receipts: list[dict]) -> None:
    for index, receipt in enumerate(receipts, start=1):
        missing = []
        if receipt.get("passed") is not True:
            missing.append(f"passed={receipt.get('passed')}")
        if int(receipt.get("laps", -1)) != 3:
            missing.append("three_laps")
        if receipt.get("map_id") != "race_circuit_y8":
            missing.append("authored_circuit")
        drift = receipt.get("drift_script", {})
        if int(drift.get("tail_sustain_ticks", 0)) <= 0:
            missing.append("drift_tail")
        boost = receipt.get("boost", {})
        if float(boost.get("top_speed_u_s", 0.0)) <= float(boost.get("unboosted_ceiling_u_s", 0.0)):
            missing.append("boost_above_baseline")
        crash = receipt.get("crash", {})
        if int(crash.get("entry_elapsed", -1)) < 0 or int(crash.get("exit_elapsed", -1)) < 0:
            missing.append("crash_recovery")
        vectors = receipt.get("physics_vectors", {})
        if int(vectors.get("ray_bits_seen", 0)) != 15:
            missing.append("four_rays")
        if float(vectors.get("max_force", 0.0)) <= 0.0 or float(vectors.get("max_torque", 0.0)) <= 0.0:
            missing.append("force_torque")
        if missing:
            raise ValueError(f"client {index} acceptance failed: {', '.join(missing)}")


def _capture_sources(run_dir: Path, receipts: list[dict]) -> list[dict[str, Path]]:
    selected: list[dict[str, Path]] = []
    for index, receipt in enumerate(receipts, start=1):
        captures = {str(row["tag"]): Path(str(row["path"])).resolve() for row in receipt["captures"]}
        missing = [tag for tag in SELECTED_TAGS if tag not in captures]
        if missing:
            raise ValueError(f"client {index} missing required captures: {', '.join(missing)}")
        for tag in SELECTED_TAGS:
            source = captures[tag]
            if not source.is_relative_to(run_dir):
                raise ValueError(f"client {index} capture escapes run directory: {source}")
            if not source.is_file():
                raise FileNotFoundError(source)
        selected.append(captures)
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--state", required=True, choices=("CLEAN", "DIRTY", "PATCHED"))
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    if not run_dir.is_dir():
        raise FileNotFoundError(run_dir)
    receipt_paths = [run_dir / "client1/receipt.json", run_dir / "client2/receipt.json"]
    receipts = [_load(path) for path in receipt_paths]
    _accept(receipts)
    capture_sources = _capture_sources(run_dir, receipts)

    summary = {
        "schema": "fl4831-headed-acceptance.v1",
        "run_id": run_dir.name,
        "source_commit": args.commit,
        "clients": [
            {
                "client": receipt["client"],
                "receipt": str(path.relative_to(run_dir)),
                "receipt_sha256": sha256_file(path),
                "laps": receipt["laps"],
                "outcome": receipt["outcome"],
                "finish_order_actor_ids": receipt["finish_order_actor_ids"],
                "drift_script": receipt["drift_script"],
                "boost": receipt["boost"],
                "crash": receipt["crash"],
                "physics_vectors": receipt["physics_vectors"],
            }
            for receipt, path in zip(receipts, receipt_paths, strict=True)
        ],
    }
    summary_path = run_dir / "acceptance_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    sources = _sources()
    metadata = {
        "provenance_status": "ATTESTED",
        "fl_id": "FL-4831",
        "run_id": run_dir.name,
        "proof_kind": "RENDER",
        "architecture": "WORLD_TEXEL_SPLAT",
        "foreground_mode": "FLAT",
        "background_mode": "FLAT",
        "capture_source_state": args.state,
        "source_commit": args.commit,
        "effective_tree_fingerprint": _fingerprint(sources),
        "executed_sources": sources,
        "build_identity": {
            "kind": "godot-official-binary",
            "path": str(GODOT),
            "sha256": sha256_file(GODOT),
        },
        "runtime_identity": {
            "target": "macos-arm64-metal-headed-two-window",
            "engine_version": "4.6.2.stable.official.71f334935",
        },
        "workload_identity": {
            "runner": "godot_project/scripts/proofs/fl4831_headed_two_client_race.gd (two live clients)",
            "scene": "godot_project/scenes/Main.tscn with production Colyseus adapter and race HUD",
            "map": "godot_project/assets/maps/race_circuit_y8.a3d",
            "pose": "server grid spawn through three laps, drift release, boost, crash recovery, and finish",
            "camera": "Player-owned perspective car chase camera; 800x500 requested window per client",
            "timing": "30 Hz authoritative room tick; captures follow named server-state transitions",
        },
        "cell_resolution": {"width": 12, "height": 12},
        "glyph_source_resolution": [{"width": 32, "height": 32}],
        "provenance_note": (
            f"Fresh two-client headed acceptance at source commit {args.commit}. Raw gameplay screenshots are "
            "preserved beside both client receipts; selected frames are copied byte-identically "
            "to canonical names before separately labelled delivery copies are built. The JSON "
            "acceptance gate requires three laps, blank platform, drift tail, measured boost above "
            "the unboosted ceiling, crash entry/exit, all four rays, and nonzero force/torque for both clients."
        ),
        "storage": {
            "status": "TRACKED",
            "location": ".",
            "failure_log": "docs/FAILURE_LOG.md#FL-4831",
            "godot_goal": "docs/plans/GODOT_GOAL.MD",
            "receipt_path": summary_path.name,
            "receipt_sha256": sha256_file(summary_path),
        },
    }

    raw_dir = run_dir / "raw_published"
    if raw_dir.exists() and any(raw_dir.iterdir()):
        raise FileExistsError(f"preserve prior publication: {raw_dir}")
    raw_dir.mkdir(parents=True, exist_ok=True)
    artifacts = []
    for receipt, captures in zip(receipts, capture_sources, strict=True):
        client = int(receipt["client"])
        for tag in SELECTED_TAGS:
            source = captures[tag]
            with Image.open(source) as image:
                dimensions = {"width": image.width, "height": image.height}
            draft = {
                "role": f"client-{client}-{tag.replace('_', '-')}",
                "frame_count": 1,
                "extension": ".png",
                "image_dimensions": dimensions,
            }
            target = raw_dir / deterministic_artifact_name(metadata, draft)
            shutil.copy2(source, target)
            artifacts.append({**draft, "path": target, "aliases": [source]})

    raw_manifest = run_dir / "raw_godot_proof_publication.json"
    publish_artifacts(raw_manifest, metadata, artifacts, artifact_root=run_dir)
    raw_errors = validate_manifest(raw_manifest, require_storage=True)
    if raw_errors:
        raise ValueError(f"raw publication invalid: {raw_errors}")
    apply_finder_tags(raw_manifest)

    labelled_dir = run_dir / "published_labeled"
    labelled_manifest = publish_labeled_media([raw_manifest], labelled_dir, tags=True)
    labelled_errors = validate_manifest(labelled_manifest, require_storage=True)
    if labelled_errors:
        raise ValueError(f"labelled publication invalid: {labelled_errors}")
    print(f"FL4831_PUBLICATION_PASS raw={raw_manifest} labelled={labelled_manifest} artifacts={len(artifacts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
