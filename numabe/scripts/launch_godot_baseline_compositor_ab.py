#!/usr/bin/env python3
"""Baseline compositor A/B runner.

Authority: docs/FAILURE_LOG.md entries FL-4216, FL-4129, and FL-4208.
This launcher is diagnostic infrastructure. It must not define water-lane
requirements, closure, acceptance, queue order, nor proof success. Headed PNGs
from this runner are visual diagnostics only unless FL-4216 names the receipt.

Runs A from the June 14 pre-product-refactor clone and B from the current
checkout with the same baseline feature manifest plus one named improvement
lane.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from godot_proof_poses import DEFAULT_LOCATION_SET, DEFAULT_YAWS, TEST_LOCATIONS, resolve_yaws_for_location

try:
    from PIL import Image, ImageChops, ImageDraw, ImageEnhance
except Exception:  # pragma: no cover - visual comparison is best-effort.
    Image = None
    ImageChops = None
    ImageDraw = None
    ImageEnhance = None


ROOT = Path(__file__).resolve().parents[1]
GODOT_PROJECT = ROOT / "godot_project"
RUN_ROOT = ROOT / ".run" / "baseline_compositor_ab"
GODOT = os.environ.get("GODOT_BIN", "/opt/homebrew/bin/godot")

DEFAULT_CANDIDATE_FLAGS: list[str] = []
DEFAULT_CANDIDATE_LABEL = "BASELINE_FEATURE"
PROMOTED_BASELINE_ARCHIVE_DIR = Path("/private/tmp/y9-2-visual-baseline-20260614-1600")
PROMOTED_BASELINE_SOURCE_IDENTITY = "cad593850"
PROMOTED_BASELINE_FOLIAGE_SOURCE_IDENTITY = "june14_clone"
PROMOTED_BASELINE_WATER_SOURCE_IDENTITY = "june14_clone"
PROMOTED_BASELINE_SHADOW_SOURCE_IDENTITY = "june14_clone"
PROMOTED_BASELINE_LOCK_DATE = "2026-06-14"
PROMOTED_BASELINE_LOCK_FL = "FL-4377"
PROMOTED_BASELINE_A_TITLE = f"BASELINE A [{PROMOTED_BASELINE_LOCK_DATE}, {PROMOTED_BASELINE_LOCK_FL}]"
PROMOTED_BASELINE_SOURCE_ROOT = PROMOTED_BASELINE_ARCHIVE_DIR
PROMOTED_BASELINE_PROJECT = PROMOTED_BASELINE_SOURCE_ROOT / "godot_project"
PROMOTED_BASELINE_SERVER = PROMOTED_BASELINE_SOURCE_ROOT / "colyseus_server"
CURRENT_ARCHIVE_ROOT = RUN_ROOT / "current_source"
PROMOTED_BASELINE_FEATURES: list[str] = [
    "foliage_cards",
    "water_current_package",
    "material_ramp_steps",
    "surface_stable_dither",
    "glyph_distinctness",
]
CURRENT_BASELINE_FEATURES: list[str] = list(PROMOTED_BASELINE_FEATURES)
PROMOTED_BASELINE_CONTRACT: dict[str, Any] = {
    "source_identity": PROMOTED_BASELINE_SOURCE_IDENTITY,
    "foliage_source_identity": PROMOTED_BASELINE_FOLIAGE_SOURCE_IDENTITY,
    "water_current_package_source_identity": PROMOTED_BASELINE_WATER_SOURCE_IDENTITY,
    "shadow_source_identity": PROMOTED_BASELINE_SHADOW_SOURCE_IDENTITY,
    "source_identity_reason": "operator-directed FL-4377 Baseline A promotion to the June 14 pre-product-refactor clone",
    "baseline_a_title": PROMOTED_BASELINE_A_TITLE,
    "lock_fl": PROMOTED_BASELINE_LOCK_FL,
    "lock_date": PROMOTED_BASELINE_LOCK_DATE,
    "clone_dir": str(PROMOTED_BASELINE_ARCHIVE_DIR),
    "features": PROMOTED_BASELINE_FEATURES,
    "excluded_diagnostics": [
        "manual_environment_test_all",
        "clouds",
        "local_light_volume",
        "firefly_light",
        "ascii_birds",
    ],
    "weather": "clear/off unless explicitly selected",
    "freeze_policy": "fail_closed_when_june14_clone_identity_or_feature_manifest_drift",
}
BASELINE_FEATURE_DEBUG_LABELS = {
    "glyph_distinctness": [
        "material_family",
        "interior_glyph",
        "decal_stamp",
        "boundary_glyph",
        "legacy_hash_fallback",
        "glyph_bg_contrast_boost",
    ],
    "material_ramp_steps": [
        "material_family",
        "cell_scale_tone_band",
        "stepped_ramp",
    ],
    "surface_stable_dither": [
        "world_cell_dither",
        "surface_stable",
        "post_ramp_enrichment",
    ],
    "foliage_v2": [
        "foliage_v2_owner",
        "v1_grass_field_disabled",
        "upright_stamp_density",
    ],
    "ascii_birds": [
        "world_geometry_triangles",
        "oscillating_wing_mesh",
        "post_shadered_by_ascii_compositor",
    ],
}
# H-01 contract: every baseline A/B picks exactly one B differentiator on top
# of CURRENT_BASELINE_FEATURES. Names must match the runtime
# cmdline_baseline_features parser in ASCIICompositorEffect.gd.
KNOWN_BASELINE_FEATURES = {
    "contact_shadow",        # FL-4191 v3 accepted baseline at frozen source d48064157: cloud-drift-style ambient actor shadow with cliff elevation gate.
    "convex_edge",
    "grass_field",
    "night",
    "clouds",
    "water_current_package",  # FL-4129 current water package; single compositor water owner
    # B-24/B-25 weather: weather_kind enum replaces the old rain/snow flag
    # pair. Pick exactly one for an H-01 A/B. The harness also accepts the
    # `weather_kind=<name>` form via expand_baseline_features below.
    "weather_clear",       # FL-4155 enum value 0 (no precipitation)
    "weather_light_snow",  # FL-4155 enum value 1 (WeatherState::LIGHT_SNOW)
    "weather_heavy_snow",  # FL-4155 enum value 2 (WeatherState::HEAVY_SNOW)
    "weather_blizzard",    # FL-4155 enum value 3 (WeatherState::BLIZZARD)
    "weather_rain",        # FL-4155 enum value 4 (Godot-port-only owner; ADR B-24)
    # 2026-05-28 slice: 4 new compositor shader rows wired end-to-end.
    "grass_t3ssel8r",    # U-25, bit 8192 — toon-stepped grass blade color
    "wind_gameidea",     # U-26, bit 16384 — wind input to grass_field
    "wind_victorkarp",   # U-27, bit 32768 — wind input to grass_field
    "cloud_density",     # U-30, bit 65536 — 3D volume promoting clouds
    "material_ramp_steps",    # FL-4102 follow-up, bit 2048 — stepped material lighting/ramp bands
    "surface_stable_dither",  # U-05, bit 4096 — world-cell dither after ramp/glyph baseline
    # FL-4102 combined per operator decision 2026-05-28: U-08 material
    # glyph families + U-09 per-material decal/edge stamping ship under
    # one gate so the U-09 'no hash-primary terrain variety' acceptance
    # criterion can be met (U-08 alone leaves family routing disabled).
    "glyph_distinctness",     # U-08+U-09, bit 16777216 — material family palette + decal edges
    "derivative_aa",          # B-28, bit 33554432 — soften FL-4057 binary font threshold with neighbour gradient
    "rotation_crawl",         # U-07, bit 67108864 — yaw-modulated per-cell threshold dither
    "cloud_shadow_deep",      # FL-4080, bit 134217728 — lower cloud_atten floor 0.86 -> 0.58 for visible cloud darken
    "foliage_v2",             # FL-4103, bit TBD — v2 foliage query replaces v1 grass_field owner while on. Prior comment claimed bit29 (536870912); the runtime side was never wired (no var baseline_feature_foliage_v2 or bit-pack code in ASCIICompositorEffect.gd), so bit29 was reclaimed by water_player_wake (FL-4129 U-31) on 2026-06-02. When this row ships, allocate a fresh bit and update both the launcher comment and the GDScript bit-pack site.
    "foliage_cards",          # FL-4184, bit 8 — compositor-owned dense foliage coverage/glyph mode (single owner: foliage_blade_coverage_at_cell in ascii_compositor.glsl). Legacy WorldFoliageCards3D and foliage_blade_card.gdshader were deleted 2026-06-03 per Law 5; no parallel visible carrier exists.
    "ascii_birds",            # FL-4187 — render-only world triangle flocks consumed by ASCII compositor
    # FL-4203: local light volume features (no misc.w bit; passed via LocalLightControl UBO)
    "local_light_volume",
    "bonfire_light",
    "firefly_light",
}

def cmd_out(args: list[str]) -> str:
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def git_identity() -> tuple[str, str]:
    head = cmd_out(["git", "rev-parse", "--short=10", "HEAD"])
    dirty = "true" if cmd_out(["git", "status", "--porcelain"]) else "false"
    return head, dirty


def clone_git_identity() -> tuple[str, str]:
    head = subprocess.check_output(
        ["git", "rev-parse", "--short=9", "HEAD"],
        cwd=PROMOTED_BASELINE_ARCHIVE_DIR,
        text=True,
    ).strip()
    dirty = "true" if subprocess.check_output(
        ["git", "status", "--porcelain"],
        cwd=PROMOTED_BASELINE_ARCHIVE_DIR,
        text=True,
    ).strip() else "false"
    return head, dirty


def promoted_baseline_identity_matches(head: str) -> bool:
    return any([
        head.startswith(PROMOTED_BASELINE_SOURCE_IDENTITY),
        PROMOTED_BASELINE_SOURCE_IDENTITY.startswith(head),
    ])


def godot_runtime_support_ready(project_path: Path) -> bool:
    imported_dir = project_path / ".godot" / "imported"
    colyseus_extension = project_path / "addons" / "colyseus" / "colyseus.gdextension"
    return imported_dir.exists() and colyseus_extension.exists()


def run_source_matched_godot_import(project_path: Path, source_identity: str, error_prefix: str) -> None:
    import_cmd = [GODOT, "--headless", "--path", str(project_path), "--import"]
    result = subprocess.run(
        import_cmd,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=180,
    )
    imported_dir = project_path / ".godot" / "imported"
    if result.returncode != 0 or not imported_dir.exists():
        raise SystemExit(
            f"{error_prefix}_IMPORT_FAILED\n"
            + json.dumps({
                "source_identity": source_identity,
                "project_path": str(project_path),
                "cmd": import_cmd,
                "returncode": result.returncode,
                "imported_dir_exists": imported_dir.exists(),
                "output_tail": result.stdout[-4000:],
            }, indent=2)
        )


def ensure_live_candidate_source_imported(project_path: Path, candidate_head: str) -> None:
    """Reimport the live candidate project when shader source drifted past its
    `.godot/imported/*.md5` cache. Without this, Godot silently consumes a
    pre-FL-4101 compiled shader from the cache (see FL-4204) so every recent
    A/B rendered daytime even with `--baseline-feature=night` set."""
    shader_path = project_path / "render" / "ascii" / "raster" / "ascii_compositor.glsl"
    imported_dir = project_path / ".godot" / "imported"
    if not shader_path.exists():
        return
    needs_reimport = not imported_dir.exists()
    if not needs_reimport:
        source_md5_actual = hashlib.md5(shader_path.read_bytes()).hexdigest()
        md5_files = sorted(imported_dir.glob("ascii_compositor.glsl-*.md5"))
        if not md5_files:
            needs_reimport = True
        else:
            cached_md5 = ""
            for line in md5_files[0].read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("source_md5="):
                    cached_md5 = line.split("=", 1)[1].strip().strip('"')
                    break
            if cached_md5 != source_md5_actual:
                needs_reimport = True
    if not needs_reimport:
        return
    run_source_matched_godot_import(project_path, candidate_head, "LIVE_CANDIDATE")


def copy_godot_runtime_support(project_path: Path) -> None:
    """Copy extension support needed by archived source launches.

    Do not copy `.godot` from the current checkout. `.godot/imported` contains
    compiled/imported shaders, scenes, and textures, so it is visual runtime
    state, not neutral launch support.
    """
    support_paths = [
        (GODOT_PROJECT / "addons" / "colyseus", project_path / "addons" / "colyseus"),
    ]
    for source, target in support_paths:
        if not source.exists():
            continue
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True, symlinks=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def promoted_baseline_marker_payload() -> str:
    return json.dumps(
        {
            "source_identity": PROMOTED_BASELINE_SOURCE_IDENTITY,
            "clone_dir": str(PROMOTED_BASELINE_ARCHIVE_DIR),
            "runtime_support": {
                "godot_import_cache": (PROMOTED_BASELINE_PROJECT / ".godot" / "imported").exists(),
                "godot_import_cache_owner": "fl4377_june14_clone",
                "current_checkout_godot_cache_copied": False,
                "colyseus_addon": (PROMOTED_BASELINE_PROJECT / "addons" / "colyseus" / "colyseus.gdextension").exists(),
                "colyseus_server": PROMOTED_BASELINE_SERVER.exists(),
            },
        },
        indent=2,
        sort_keys=True,
    ) + "\n"


def materialize_promoted_baseline_source() -> Path:
    if not PROMOTED_BASELINE_ARCHIVE_DIR.exists():
        raise SystemExit(
            "PROMOTED_BASELINE_A_JUNE14_CLONE_MISSING\n"
            + json.dumps({
                "expected": PROMOTED_BASELINE_CONTRACT,
                "clone_dir": str(PROMOTED_BASELINE_ARCHIVE_DIR),
            }, indent=2)
        )
    clone_head = subprocess.check_output(
        ["git", "rev-parse", "--short=9", "HEAD"],
        cwd=PROMOTED_BASELINE_ARCHIVE_DIR,
        text=True,
    ).strip()
    if clone_head != PROMOTED_BASELINE_SOURCE_IDENTITY:
        raise SystemExit(
            "PROMOTED_BASELINE_A_JUNE14_CLONE_HEAD_DRIFT\n"
            + json.dumps({
                "expected": PROMOTED_BASELINE_CONTRACT,
                "actual_head": clone_head,
            }, indent=2)
        )
    if not godot_runtime_support_ready(PROMOTED_BASELINE_PROJECT) or not PROMOTED_BASELINE_SERVER.exists():
        raise SystemExit(
            "PROMOTED_BASELINE_A_JUNE14_CLONE_INCOMPLETE\n"
            + json.dumps({
                "expected": PROMOTED_BASELINE_CONTRACT,
                "project": str(PROMOTED_BASELINE_PROJECT),
                "server": str(PROMOTED_BASELINE_SERVER),
                "runtime_support_ready": godot_runtime_support_ready(PROMOTED_BASELINE_PROJECT),
            }, indent=2)
        )
    return PROMOTED_BASELINE_PROJECT


def current_archive_marker_payload(head: str) -> str:
    project_path = CURRENT_ARCHIVE_ROOT / head / "godot_project"
    return json.dumps(
        {
            "source_identity": head,
            "runtime_support": {
                "godot_import_cache": (project_path / ".godot" / "imported").exists(),
                "godot_import_cache_owner": "source_matched_godot_import",
                "current_checkout_godot_cache_copied": False,
                "colyseus_addon": (project_path / "addons" / "colyseus" / "colyseus.gdextension").exists(),
            },
        },
        indent=2,
        sort_keys=True,
    ) + "\n"


def materialize_current_archive_source(head: str) -> Path:
    source_root = CURRENT_ARCHIVE_ROOT / head
    project_path = source_root / "godot_project"
    marker = source_root / ".current_source_identity"
    if (
        project_path.exists()
        and marker.exists()
        and marker.read_text(encoding="utf-8") == current_archive_marker_payload(head)
        and godot_runtime_support_ready(project_path)
    ):
        return project_path
    if source_root.exists():
        shutil.rmtree(source_root)
    source_root.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(
        ["git", "archive", "--format=tar", "HEAD"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if archive.returncode != 0:
        raise SystemExit(
            "CURRENT_ARCHIVE_SOURCE_FAILED\n"
            + json.dumps({
                "source_identity": head,
                "failure": "git archive failed for current source",
                "stderr": archive.stderr.decode("utf-8", errors="replace").strip(),
            }, indent=2)
        )
    with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as tar:
        tar.extractall(source_root, filter="fully_trusted")
    copy_godot_runtime_support(project_path)
    run_source_matched_godot_import(project_path, head, "CURRENT_ARCHIVE_SOURCE")
    marker.write_text(current_archive_marker_payload(head), encoding="utf-8")
    return project_path


def validate_promoted_baseline_identity() -> Path:
    head, dirty = git_identity()
    failures: list[str] = []
    if CURRENT_BASELINE_FEATURES != PROMOTED_BASELINE_FEATURES:
        failures.append("CURRENT_BASELINE_FEATURES differs from PROMOTED_BASELINE_FEATURES")
    if failures:
        details = {
            "expected": PROMOTED_BASELINE_CONTRACT,
            "actual": {
                "head": head,
                "dirty": dirty,
                "current_baseline_features": CURRENT_BASELINE_FEATURES,
            },
            "failures": failures,
        }
        raise SystemExit(
            "PROMOTED_BASELINE_A_NOT_FROZEN\n"
            + json.dumps(details, indent=2)
        )
    return materialize_promoted_baseline_source()


def colyseus_is_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def find_free_port(start: int = 2600) -> int:
    for port in range(start, start + 200):
        if colyseus_is_listening(port):
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    raise RuntimeError("no free local Colyseus proof port found")


def start_colyseus(run_dir: Path, role: str, port: int, server_path: Path) -> subprocess.Popen[str]:
    log = (run_dir / f"colyseus_{role}.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        ["npm", "start"],
        cwd=server_path,
        env={**os.environ, "COLYSEUS_PORT": str(port)},
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    for _ in range(100):
        if colyseus_is_listening(port):
            return proc
        if proc.poll() is not None:
            raise RuntimeError(f"Colyseus exited before listening; see {run_dir / f'colyseus_{role}.log'}")
        time.sleep(0.1)
    proc.terminate()
    raise RuntimeError(f"Colyseus did not listen on port {port}; see {run_dir / f'colyseus_{role}.log'}")


def stop_colyseus(proc: subprocess.Popen[str] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def create_visual_comparisons(run_dir: Path, runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if Image is None or ImageChops is None or ImageDraw is None or ImageEnhance is None:
        return []
    comparison_dir = run_dir / "comparisons"
    comparison_dir.mkdir(parents=True, exist_ok=True)
    comparisons: list[dict[str, Any]] = []
    cell_size = 16
    for run in runs:
        baseline_pngs = [ROOT / path for path in run["baseline"].get("pngs", [])]
        candidate_pngs = [ROOT / path for path in run["candidate"].get("pngs", [])]
        for base_png, cand_png in zip(baseline_pngs, candidate_pngs):
            if not base_png.exists() or not cand_png.exists():
                continue
            base = Image.open(base_png).convert("RGB")
            cand = Image.open(cand_png).convert("RGB")
            if base.size != cand.size:
                continue
            diff = ImageChops.difference(base, cand)
            diff_gray = diff.convert("L")
            diff_heat = ImageEnhance.Contrast(diff_gray).enhance(8.0).convert("RGB")
            marked = cand.copy()
            draw = ImageDraw.Draw(marked)
            changed_pixels = 0
            total_pixels = base.size[0] * base.size[1]
            changed_cells = 0
            total_cells = 0
            px = diff_gray.load()
            for cy in range(0, base.size[1], cell_size):
                for cx in range(0, base.size[0], cell_size):
                    total_cells += 1
                    acc = 0
                    samples = 0
                    for y in range(cy, min(cy + cell_size, base.size[1]), 2):
                        for x in range(cx, min(cx + cell_size, base.size[0]), 2):
                            val = px[x, y]
                            acc += val
                            samples += 1
                            if val > 8:
                                changed_pixels += 4
                    mean = acc / max(samples, 1)
                    if mean > 9.0:
                        changed_cells += 1
                        draw.rectangle(
                            [cx, cy, min(cx + cell_size - 1, base.size[0] - 1), min(cy + cell_size - 1, base.size[1] - 1)],
                            outline=(255, 0, 255),
                            width=1,
                        )
            header_h = 28
            sheet = Image.new("RGB", (base.size[0] * 4, base.size[1] + header_h), (0, 0, 0))
            labels = ["A baseline", "B candidate", "pixel diff x8", "B changed cells"]
            for idx, (label, img) in enumerate(zip(labels, [base, cand, diff_heat, marked])):
                x0 = idx * base.size[0]
                sheet.paste(img, (x0, header_h))
                ImageDraw.Draw(sheet).text((x0 + 8, 7), label, fill=(220, 255, 80))
            out_name = f"{run['test_location']}_{cand_png.stem}_ab_diff.png"
            out_path = comparison_dir / out_name
            sheet.save(out_path)
            comparisons.append({
                "test_location": run["test_location"],
                "yaw_png": cand_png.name,
                "path": display_path(out_path),
                "changed_pixel_pct": round(min(changed_pixels, total_pixels) * 100.0 / max(total_pixels, 1), 3),
                "changed_cell_pct": round(changed_cells * 100.0 / max(total_cells, 1), 3),
                "changed_cells": changed_cells,
                "total_cells": total_cells,
            })
    return comparisons


def normalize_candidate_flags(values: list[str]) -> list[str]:
    flags: list[str] = []
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if part:
                flags.append(part)
    return flags or list(DEFAULT_CANDIDATE_FLAGS)


_WEATHER_KIND_VALUES = {"clear", "light_snow", "heavy_snow", "blizzard", "rain"}


def expand_baseline_features(features: list[str]) -> list[str]:
    expanded: list[str] = []
    seen: set[str] = set()
    for entry in features:
        for part in entry.split(","):
            name = part.strip()
            if not name:
                continue
            # weather_kind=<value> form: validate the enum value but pass
            # the full token through so the runtime parser sees it intact.
            if name.startswith("weather_kind="):
                value = name.split("=", 1)[1].strip().lower()
                if value not in _WEATHER_KIND_VALUES:
                    raise SystemExit(
                        f"unknown weather_kind={value!r}; choose from {sorted(_WEATHER_KIND_VALUES)}"
                    )
                if name in seen:
                    continue
                seen.add(name)
                expanded.append(f"--baseline-feature={name}")
                continue
            if name not in KNOWN_BASELINE_FEATURES:
                raise SystemExit(
                    f"unknown --baseline-feature {name!r}; choose from {sorted(KNOWN_BASELINE_FEATURES)}"
                )
            if name in seen:
                continue
            seen.add(name)
            expanded.append(f"--baseline-feature={name}")
    return expanded


def build_godot_cmd(
    *,
    role: str,
    label: str,
    project_path: Path,
    source_head: str,
    source_dirty: str,
    port: int,
    variant_dir: Path,
    yaws: str,
    warmup_frames: int,
    settle_frames: int,
    capture_frames: int,
    test_location: str,
    player_pos: str,
    window_x: int,
    window_y: int,
    window_w: int,
    window_h: int,
    extra_flags: list[str],
    splat_churn_probe: bool,
) -> list[str]:
    if role == "baseline":
        if source_head != PROMOTED_BASELINE_SOURCE_IDENTITY:
            raise SystemExit(
                "PROMOTED_BASELINE_A_SOURCE_DRIFT\n"
                + json.dumps({
                    "expected": PROMOTED_BASELINE_CONTRACT,
                    "actual_source_head": source_head,
                }, indent=2)
            )
        if label != PROMOTED_BASELINE_A_TITLE:
            raise SystemExit(
                "PROMOTED_BASELINE_A_TITLE_DRIFT\n"
                + json.dumps({
                    "expected_title": PROMOTED_BASELINE_A_TITLE,
                    "actual_title": label,
                }, indent=2)
            )
    cmd = [
        GODOT,
        "--path",
        str(project_path),
        "--",
        "--colyseus-client",
        f"--port={port}",
        "--fl4057-ab-yaw-capture",
        f"--fl4057-ab-output-dir={variant_dir}",
        f"--fl4057-ab-yaws={yaws}",
        f"--fl4057-ab-warmup-frames={warmup_frames}",
        f"--fl4057-ab-settle-frames={settle_frames}",
        f"--fl4057-ab-capture-frames={capture_frames}",
        f"--fl4057-ab-test-location={test_location}",
        f"--splat-compare-role={label}",
        f"--splat-compare-title={label}",
        f"--splat-compare-head={source_head}",
        f"--splat-compare-dirty={source_dirty}",
        f"--splat-compare-window-x={window_x}",
        f"--splat-compare-window-y={window_y}",
        f"--splat-compare-window-w={window_w}",
        f"--splat-compare-window-h={window_h}",
        f"--local-light-metrics-json={variant_dir / 'local_light_metrics.json'}",
    ]
    if splat_churn_probe:
        cmd.append(f"--probe-splat-churn-jsonl={variant_dir / 'baseline_splat_churn.jsonl'}")
    if player_pos:
        cmd.append(f"--fl4057-ab-player-pos={player_pos}")
    # FL-4208 step #25/#27 visual proof: trigger an X dump on the
    # candidate side AT the locked operator pose. The yaw_capture flow
    # in Main.gd consumes --fl4057-ab-x-dump AND requests the dump
    # after the player has been pinned at --fl4057-ab-player-pos AND
    # the camera at --fl4057-ab-yaws. This replaces the earlier
    # auto-final-render-dump-after-ms approach, which fired from the
    # World.gd state machine before the yaw_capture flow positioned
    # the player, capturing the default spawn instead of the locked
    # pose. Gated to `candidate` so the frozen baseline run stays
    # byte-identical to its prior reference output.
    if role == "candidate":
        cmd.append("--fl4057-ab-x-dump")
        cmd.append(f"--final-render-dump-tag={label}")
        cmd.append(f"--final-render-dump-intent={label}_x_dump")
    cmd.extend(extra_flags)
    return cmd


def run_variant(
    *,
    run_dir: Path,
    role: str,
    label: str,
    project_path: Path,
    source_head: str,
    source_dirty: str,
    port: int,
    yaws: str,
    warmup_frames: int,
    settle_frames: int,
    capture_frames: int,
    test_location: str,
    player_pos: str,
    window_x: int,
    window_y: int,
    window_w: int,
    window_h: int,
    extra_flags: list[str],
    splat_churn_probe: bool,
    timeout: int,
    dry_run: bool,
) -> dict[str, Any]:
    variant_dir = run_dir / role
    variant_dir.mkdir(parents=True, exist_ok=True)
    cmd = build_godot_cmd(
        role=role,
        label=label,
        project_path=project_path,
        source_head=source_head,
        source_dirty=source_dirty,
        port=port,
        variant_dir=variant_dir,
        yaws=yaws,
        warmup_frames=warmup_frames,
        settle_frames=settle_frames,
        capture_frames=capture_frames,
        test_location=test_location,
        player_pos=player_pos,
        window_x=window_x,
        window_y=window_y,
        window_w=window_w,
        window_h=window_h,
        extra_flags=extra_flags,
        splat_churn_probe=splat_churn_probe,
    )
    log_path = variant_dir / "godot.log"
    if dry_run:
        return {
            "ok": True,
            "role": role,
            "label": label,
            "project_path": display_path(project_path),
            "source_head": source_head,
            "source_dirty": source_dirty,
            "cmd": cmd,
            "capture_row_count": 0,
            "png_count": 0,
            "log": display_path(log_path),
        }
    started = time.time()
    with log_path.open("w", encoding="utf-8") as log:
        try:
            proc = subprocess.run(
                cmd,
                cwd=ROOT,
                env={**os.environ, "ASCIICKER_HUD": "1"},
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout,
            )
            returncode = proc.returncode
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            returncode = -1
            timed_out = True
            log.write(f"\nTIMEOUT after {timeout}s\n{exc}\n")
    yaw_rows = [
        row for row in load_jsonl(variant_dir / "yaw_capture.jsonl")
        if row.get("row_type") == "fl4057_ab_yaw_capture_frame"
    ]
    pngs = sorted(variant_dir.glob("yaw_*.png"))
    expected = len([part for part in yaws.split(",") if part]) * max(capture_frames, 1)
    return {
        "ok": returncode == 0 and len(yaw_rows) == expected,
        "role": role,
        "label": label,
        "project_path": display_path(project_path),
        "source_head": source_head,
        "source_dirty": source_dirty,
        "cmd": cmd,
        "returncode": returncode,
        "timed_out": timed_out,
        "elapsed_sec": time.time() - started,
        "capture_row_count": len(yaw_rows),
        "expected_capture_row_count": expected,
        "png_count": len(pngs),
        "yaw_jsonl": display_path(variant_dir / "yaw_capture.jsonl"),
        "pngs": [display_path(path) for path in pngs],
        "log": display_path(log_path),
    }


def write_summary(run_dir: Path, runs: list[dict[str, Any]], server: dict[str, Any]) -> dict[str, Any]:
    selected_features: list[str] = []
    for run in runs:
        for flag in run.get("shared_flags", []) + run.get("candidate_flags", []):
            if not isinstance(flag, str) or not flag.startswith("--baseline-feature="):
                continue
            feature = flag.removeprefix("--baseline-feature=")
            if feature not in selected_features:
                selected_features.append(feature)
    debug_labels = {
        feature: BASELINE_FEATURE_DEBUG_LABELS[feature]
        for feature in selected_features
        if feature in BASELINE_FEATURE_DEBUG_LABELS
    }
    visual_comparisons = create_visual_comparisons(run_dir, runs)
    summary = {
        "row_type": "baseline_compositor_ab_summary",
        "run_dir": display_path(run_dir),
        "runs": runs,
        "server": server,
        "ok": all(
            run.get("baseline", {}).get("ok", False)
            and run.get("candidate", {}).get("ok", False)
            for run in runs
        ),
        "same_yaws": runs[0].get("yaws", "").split(",") if runs and all(run.get("yaws") == runs[0].get("yaws") for run in runs) else [],
        "yaws_by_location": {run.get("test_location", ""): run.get("yaws", "") for run in runs},
        "terrain_positions": [run.get("test_location", "") for run in runs],
        "contract": {
            "a": "June 14 pre-product-refactor clone comparator",
            "b": "current checkout plus named baseline improvement lane",
            "promoted_baseline_contract": PROMOTED_BASELINE_CONTRACT,
            "current_baseline_features": CURRENT_BASELINE_FEATURES,
            "default_yaws": DEFAULT_YAWS,
            "default_terrain_positions": DEFAULT_LOCATION_SET,
            "feature_debug_labels": debug_labels,
        },
        "visual_comparisons": visual_comparisons,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-label", default=DEFAULT_CANDIDATE_LABEL)
    parser.add_argument("--candidate-flag", action="append", default=[], help="Raw flag(s) to add only to B. Repeat or comma-separate. Power-user escape hatch; prefer --baseline-feature.")
    parser.add_argument("--baseline-feature", action="append", default=[], help=f"Baseline-plus row(s) to enable on B only. One per A/B is the H-01 contract; repeating bundles multiple rows. Known names: {sorted(KNOWN_BASELINE_FEATURES)}.")
    parser.add_argument("--shared-baseline-feature", action="append", default=[], help=f"Baseline feature(s) to enable on both A and B before comparing the candidate. Repeat or comma-separate. Known names: {sorted(KNOWN_BASELINE_FEATURES)}.")
    parser.add_argument("--yaws", default=None)
    parser.add_argument("--allow-pose-yaw-override", action="store_true", help="Allow diagnostic captures to ignore pinned operator proof-pose yaw.")
    parser.add_argument("--capture-frames", type=int, default=1)
    parser.add_argument("--warmup-frames", type=int, default=12)
    parser.add_argument("--settle-frames", type=int, default=4)
    parser.add_argument("--test-location", choices=sorted(TEST_LOCATIONS), default=None)
    parser.add_argument("--test-locations", default=None, help="Comma-separated terrain positions; default runs both flat_default and elevated_grass.")
    parser.add_argument("--player-pos", default="")
    parser.add_argument("--window-x", type=int, default=80)
    parser.add_argument("--window-y", type=int, default=80)
    parser.add_argument("--window-w", type=int, default=1280)
    parser.add_argument("--window-h", type=int, default=800)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--splat-churn-probe", action="store_true", help="Enable per-frame splat churn GPU readback JSONL diagnostics.")
    parser.add_argument("--candidate-clean-archive", action="store_true", help="Run candidate B from a clean git archive of current HEAD and report source_dirty=false.")
    parser.add_argument("--keep-server", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    promoted_baseline_project = validate_promoted_baseline_identity()
    baseline_head, baseline_dirty = clone_git_identity()
    candidate_head, candidate_dirty = git_identity()
    candidate_project = GODOT_PROJECT
    if args.candidate_clean_archive:
        candidate_project = materialize_current_archive_source(candidate_head)
        candidate_dirty = "false"
    else:
        # FL-4204 root cause: the live godot_project/ candidate's
        # `.godot/imported/*.res` was 5 days stale because the harness
        # only reimports promoted/archived projects. A pre-FL-4101
        # cached compiled shader silently shipped to every run, so
        # `baseline_feature_night=true` rendered daytime and
        # `firefly.record_count=48` painted nothing. Force the same
        # source-identity-matched import the archived path uses so the
        # live candidate is never older than the working tree.
        ensure_live_candidate_source_imported(candidate_project, candidate_head)
    shared_features = CURRENT_BASELINE_FEATURES + args.shared_baseline_feature
    shared_flags = expand_baseline_features(shared_features)
    candidate_flags = expand_baseline_features(args.baseline_feature) + normalize_candidate_flags(args.candidate_flag)
    if not candidate_flags:
        raise SystemExit(
            "no candidate B differentiator: pass --baseline-feature <name> "
            f"(one of {sorted(KNOWN_BASELINE_FEATURES)}) "
            "or --candidate-flag <raw flag>"
        )
    if args.candidate_label == DEFAULT_CANDIDATE_LABEL and args.baseline_feature:
        feature_label = "_".join(
            flag.removeprefix("--baseline-feature=").upper()
            for flag in candidate_flags
            if flag.startswith("--baseline-feature=")
        )
        if feature_label:
            args.candidate_label = f"BASELINE_FEATURE_{feature_label}"
    if args.test_locations:
        location_names = [part.strip() for part in args.test_locations.split(",") if part.strip()]
    elif args.test_location:
        location_names = [args.test_location]
    else:
        location_names = [part.strip() for part in DEFAULT_LOCATION_SET.split(",") if part.strip()]
    for location_name in location_names:
        if location_name not in TEST_LOCATIONS:
            raise SystemExit(f"unknown --test-locations entry: {location_name}")

    run_dir = RUN_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    port = 2600 if args.dry_run else find_free_port()
    server_proc: subprocess.Popen[str] | None = None
    try:
        if not args.dry_run:
            server_proc = start_colyseus(run_dir, "baseline_compositor_ab", port, PROMOTED_BASELINE_SERVER)
        runs: list[dict[str, Any]] = []
        for location_name in location_names:
            player_pos = args.player_pos or TEST_LOCATIONS.get(location_name) or ""
            yaws = resolve_yaws_for_location(
                location_name,
                args.yaws,
                allow_pose_yaw_override=args.allow_pose_yaw_override,
            )
            location_dir = run_dir / location_name
            baseline = run_variant(
                run_dir=location_dir,
                role="baseline",
                label=PROMOTED_BASELINE_A_TITLE,
                project_path=promoted_baseline_project,
                source_head=baseline_head,
                source_dirty=baseline_dirty,
                port=port,
                yaws=yaws,
                warmup_frames=args.warmup_frames,
                settle_frames=args.settle_frames,
                capture_frames=args.capture_frames,
                test_location=location_name,
                player_pos=player_pos,
                window_x=args.window_x,
                window_y=args.window_y,
                window_w=args.window_w,
                window_h=args.window_h,
                extra_flags=shared_flags,
                splat_churn_probe=args.splat_churn_probe,
                timeout=args.timeout,
                dry_run=args.dry_run,
            )
            candidate = run_variant(
                run_dir=location_dir,
                role="candidate",
                label=args.candidate_label,
                project_path=candidate_project,
                source_head=candidate_head,
                source_dirty=candidate_dirty,
                port=port,
                yaws=yaws,
                warmup_frames=args.warmup_frames,
                settle_frames=args.settle_frames,
                capture_frames=args.capture_frames,
                test_location=location_name,
                player_pos=player_pos,
                window_x=args.window_x + args.window_w + 24,
                window_y=args.window_y,
                window_w=args.window_w,
                window_h=args.window_h,
                extra_flags=shared_flags + candidate_flags,
                splat_churn_probe=args.splat_churn_probe,
                timeout=args.timeout,
                dry_run=args.dry_run,
            )
            runs.append({
                "test_location": location_name,
                "player_pos": player_pos,
                "yaws": yaws,
                "shared_flags": shared_flags,
                "candidate_flags": candidate_flags,
                "baseline": baseline,
                "candidate": candidate,
            })
    finally:
        if not args.keep_server:
            stop_colyseus(server_proc)

    summary = write_summary(
        run_dir,
        runs,
        {
            "mode": "shared_server_for_same_session_baseline_feature_diff",
            "port": port,
            "pid": None if server_proc is None else server_proc.pid,
            "log": display_path(run_dir / "colyseus_baseline_compositor_ab.log"),
        },
    )
    print(json.dumps(summary, indent=2))
    print(f"summary={run_dir / 'summary.json'}")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
