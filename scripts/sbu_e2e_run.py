#!/usr/bin/env python3
"""SBU OSM → asciiid single-command E2E pipeline.

Runs the full pipeline from scratch: clean → blosm import → terrain → paint →
extrude → paint buildings → separate → AKM export → clean → A3D export.

Usage:
    python3 scripts/sbu_e2e_run.py                    # full run with default bbox
    python3 scripts/sbu_e2e_run.py --skip-import       # reuse existing .blend (skip blosm)
    python3 scripts/sbu_e2e_run.py --skip-pipeline     # only write manifest for existing artifacts

Bbox: SAC + Math/Physics area at Stony Brook University.
"""
# ==========================================================================
# WARNING: ~130 OSM FLs — RQ-013 MAP ASSETS LANE (2026-05-06)
# ==========================================================================
# CONFIRMED CURRENT STATE (2026-05-06 — verified .run/asciiid smoke tests):
#   .run/asciiid --batch survives LIST_INSTANCES, PROBE_TERRAIN, LOAD_MAP, QUIT
#   Exit 139 BATCH SEGFAULT IS RESOLVED. FL-2555 transport blocker is CLOSED.
#
# REMAINING OPEN LANES (all post-batch-transport):
#   FL-2595 P1: ROOT-11 AKM name collision → pipeline exports OSM geometry
#               to wrong/fake AKM because name matches game ROOT-11 fixtures
#               (gh#274)
#   FL-2594 P2: fixture AKMs are 0.02-0.38 BU (sub-cell, invisible in engine)
#               (gh#272)
#   FL-2533 FL-2534: buildings floating (Z=120 wrong baseline) or too big
#                    (stale root AKMs used as size oracle)
#   FL-906  P2: OSM pipeline recovered, terrain topology holes and scale still open
#   FL-1181 P1: terrain topology bake produces holes (quantization baseline fixed
#               to 128 on 2026-05-02, but fresh full-bake proof still required)
#
# SCALE CONSTANTS — CRITICAL (agents keep using wrong one):
#   addons/io_asciicker/scene/a3d_format.py:85-101:
#     BASE_TERRAIN_HEIGHT  = 0xA000 (40960) → LEGACY only, for old mesh instances
#     TERRAIN_EXPORT_BASELINE = 128 (8×HEIGHT_SCALE) → CORRECT for OSM exports
#   120 was wrong (off quantization grid); 128 = 8×16 is correct.
#   If buildings float/are invisible/are the wrong scale, you probably used
#   BASE_TERRAIN_HEIGHT instead of TERRAIN_EXPORT_BASELINE.
#
# RECURRING FAILURE PATTERNS — do NOT repeat:
#
# 1. WRONG CONSTANT (5+ false closures: FL-2533, FL-2549, FL-2553, FL-2554):
#    BASE_TERRAIN_HEIGHT=0xA000 is LEGACY. Use TERRAIN_EXPORT_BASELINE=128.
#    Both are in a3d_format.py:85-101. Do NOT use export_a3d.py constants.
#
# 2. WRONG CLI FLAGS (FL-2562–FL-2572):
#    sbu_e2e_run.py argparse is the canonical source of truth.
#    READ scripts/sbu_e2e_run.py --help before calling.
#    --skip-import does NOT mean workspace.blend is reusable (full_pipeline
#      requires live blosm objects).
#
# 3. WRONG-OWNER PATCHING (FL-2551, FL-2565, FL-2573):
#    TERRAIN_EXPORT_BASELINE lives in a3d_format.py.
#    Topology pits are owned by sbu_terrain.akm, not bake code.
#    Changing topology source requires deleting the old owner first.
#
# 4. STALE ARTIFACT REUSE (FL-2562, FL-2563, FL-2569):
#    run cleanup deletes workspace.blend and cleaned OSM files.
#    Do not assume any prior run artifact is still valid.
#
# 5. WRONG AKM IDENTITY (FL-2595 ROOT-11 collision):
#    OSM geometry exports to AKMs that share names with game ROOT fixtures.
#    The pipeline matches by NAME not by OSM geometry identity.
#    Fix requires OSM-geometry-based AKM naming, not name matching.
#   FL-1176: 2 attempts — 1/12-scale stale AKMs
#   FL-1181: 2 attempts — terrain topology bake produces holes
# ==========================================================================

import argparse
import contextlib
import json
import math
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Ensure scripts/ is importable for cli_style
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

sys.path.insert(0, str(SCRIPT_DIR / "pipeline"))
import cli_style  # noqa: E402
import osm_bake_contract  # noqa: E402
from cli_style import header, status, style, kv, ok_item, fail_item, progress  # noqa: E402
from osm_projection import osm_project as _osm_project_canonical  # noqa: E402


class _RunLog:
    """Structured JSONL event log written to <run_root>/run_trace.jsonl.

    Every significant pipeline event is appended as one JSON line.
    Agents and post-mortem tools can replay this to understand exactly
    what happened without parsing human-readable output.
    """
    def __init__(self):
        self._fh = None
        self._t0 = time.time()

    def open(self, run_root):
        os.makedirs(run_root, exist_ok=True)
        self._fh = open(os.path.join(run_root, "run_trace.jsonl"), "a", encoding="utf-8")
        self.emit("run_start", run_root=str(run_root))

    def emit(self, event, **data):
        if self._fh is None:
            return
        entry = {
            "t": round(time.time() - self._t0, 3),
            "ts": time.strftime("%H:%M:%S"),
            "event": event,
            **data,
        }
        self._fh.write(json.dumps(entry, default=str) + "\n")
        self._fh.flush()

    def close(self):
        if self._fh:
            self.emit("run_end")
            self._fh.close()
            self._fh = None

_run_log = _RunLog()


@contextlib.contextmanager
def _step_timer(label):
    """Emit structured step start/end with elapsed time."""
    print(f"\n  {style('▶ ' + label, 'subheader')}")
    _run_log.emit("step_start", step=label)
    t0 = time.time()
    try:
        yield
    finally:
        elapsed = time.time() - t0
        print(f"  {style('◀ ' + label, 'subheader')} {style(f'({elapsed:.1f}s)', 'dim')}")
        _run_log.emit("step_end", step=label, elapsed_s=round(elapsed, 1))

# Ensure cli-anything is importable
sys.path.insert(0, str(PROJECT_ROOT / "docs/agent/cli-anything"))

from cli_anything.blender.core.osm import full_pipeline
from cli_anything.asciiid.core.minimap import list_markers as _list_embedded_markers

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# SAC + Math/Physics bbox.
# min_lon extended west to -73.1290 (FL-1171): SAC is at ~-73.1274 which was
# outside the previous min_lon of -73.12688.
_DEFAULT_BBOX = {
    "min_lat": 40.91377,
    "max_lat": 40.91646,
    "min_lon": -73.1290,
    "max_lon": -73.12253,
}

INVALID_ENVELOPE_TOPOLOGY_MESH_NAME = "Terrain_envelope.akm"
DEFAULT_TOPOLOGY_MESH = "assets/meshes/sbu_terrain.akm"
DEFAULT_TOPOLOGY_Z_SCALE = 0.10
DEFAULT_ROAD_WIDTH_MULT = 1.0
MIN_TOPOLOGY_Z_LEVELS = 4

# Blender→asciiid handoff contracts (FL-2573, FL-1169, FL-1176)
# Topology bake must not change more than this % of terrain cells (destruction guard).
TOPOLOGY_BAKE_MAX_CHANGE_PCT = 90.0
# Building bake must change at least this % of terrain cells (stale-AKM guard, FL-1176).
BUILDING_BAKE_MIN_CHANGE_PCT = 1.0

# Output paths
DEFAULT_RUNS_ROOT = PROJECT_ROOT / "assets" / "meshes" / "osm_runs"
ACTIVE_MESH_ROOT_POINTER = DEFAULT_RUNS_ROOT / ".active_mesh_root"
BLANK_WORKSPACE_TEMPLATE = DEFAULT_RUNS_ROOT / "_templates" / "blank_workspace.blend"
GENERATED_RUN_DIR_PREFIX = "sbu_e2e_"
GENERATED_RUN_DIR_PATTERNS = (
    re.compile(r"^sbu_e2e_\d{8}_\d{6}$"),
    re.compile(r"^launcher_osm_\d{8}_\d{6}$"),
)
# Cleanup must key off actual run-artifact contents under assets/meshes/osm_runs,
# not just legacy directory names, or ad hoc FL/topology runs survive into later proofs.
GENERATED_RUN_ARTIFACT_MARKERS = frozenset((
    "active_mesh_root.env",
    "building_instances.json",
    "fixture_instances.json",
    "manifest.json",
    "osm_blosm_input.osm",
    "output.a3d",
    "output_buildings_only.a3d",
    "output_prebake.a3d",
    "output_prebake_staged.a3d",
    "output_staged.a3d",
    "output_terrain_only.a3d",
    "run_trace.jsonl",
    "terrain_metadata.json",
    "topology_instance.json",
    "workspace.blend",
    "workspace.blend1",
))
FIXTURES_DIR = str(PROJECT_ROOT / "assets" / "meshes" / "fixtures")
# WHY 128: 120 % HEIGHT_SCALE (16) = 8 — off-grid.  Edge samples at ~119 quantize to 112,
# fail the overwrite-height=0 gate (112 <= 120), and leave cells stuck at 120 in raised terrain
# (terrain holes, FL-1181).  128 = 8 × 16 is the nearest on-grid value.  Must equal
# TERRAIN_EXPORT_BASELINE in a3d_format.py and BAKE_COVERAGE_BASELINE in osm_bake_contract.py.
SBU_TERRAIN_BASELINE = 128
# Topology meshes describe the playable ground, not "no terrain".  The minimum
# sampled topology height must land one quantized height band above the sentinel
# baseline so sparse low regions do not remain indistinguishable from holes.
TOPOLOGY_MIN_HEIGHT_OFFSET = 16.0
# If OSM/Carto postprocessing has already produced varied terrain before the
# optional topology mesh bake, a low topology diff is not a stale-mesh failure.
# The bake still must pass stuck-at-baseline and pit checks.
TOPOLOGY_PREEXISTING_HEIGHT_RANGE_MIN = 128

ASCIIID_BIN = str(PROJECT_ROOT / ".run" / "asciiid")

# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------


def _resolve_path(path_str):
    path = Path(path_str).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return str(path.resolve())


def _default_run_id():
    return time.strftime("sbu_e2e_%Y%m%d_%H%M%S")


# ---------------------------------------------------------------------------
# Run configuration — frozen dataclasses built once in main(), threaded
# through every pipeline step. Replaces the 15 mutable module globals.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RunPaths:
    """Artifact paths this run produces — all must live inside run_root."""
    runs_root: Path
    run_id: str
    run_root: Path
    blend_file: str
    meshes_dir: str
    a3d_output: str
    manifest_output: str
    fixture_specs_output: str
    building_specs_output: str
    prebake_a3d_output: str
    buildings_only_a3d_output: str
    terrain_only_a3d_output: str
    terrain_metadata_output: str
    topology_instance_output: str
    building_bake_summary_output: str
    activation_env_output: str
    normalized_osm_output: str
    prebake_staged_output: str
    a3d_staged_output: str


@dataclass(frozen=True)
class RunBbox:
    """Geographic extent for the OSM import."""
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float

    def as_dict(self):
        return {
            "min_lat": self.min_lat,
            "max_lat": self.max_lat,
            "min_lon": self.min_lon,
            "max_lon": self.max_lon,
        }


@dataclass(frozen=True)
class RunParams:
    """Pipeline behavior knobs — tunable from CLI or launcher submenu."""
    pipeline_mode: str               # "traditional" | "baked"
    content_scale: float
    building_height_mult: float
    max_terrain_grid_segs: int
    topology_mesh: Optional[str]
    topology_z_scale: float
    road_width_mult: float
    no_topology_bake: bool
    stop_after_buildings_only: bool
    resume_fixtures_from: Optional[str]
    osm_file_override: Optional[str]
    skip_import: bool
    skip_pipeline: bool
    no_clean: bool
    keep_previous_runs: int
    activate_run: bool
    spawn_lat: Optional[float]
    spawn_lon: Optional[float]
    spawn_x: Optional[float]
    spawn_y: Optional[float]
    topology_bake_max_change_pct: float
    building_bake_min_change_pct: float
    osm_material_postprocess: bool = False
    osm_carto_stamp: bool = False
    osm_carto_osm_only: bool = False
    osm_carto_labels: bool = False
    satellite_paint: bool = False
    satellite_paint_zoom: int = 18
    satellite_paint_max_tiles: int = 100
    satellite_paint_force: bool = False


@dataclass(frozen=True)
class RunConfig:
    """Single source of truth for a pipeline run. Built once in main()."""
    paths: RunPaths
    bbox: RunBbox
    params: RunParams

    @staticmethod
    def from_args(args, parser) -> "RunConfig":
        # Validate bbox — all four or none.
        bbox_vals = [args.min_lat, args.max_lat, args.min_lon, args.max_lon]
        if any(v is not None for v in bbox_vals):
            if not all(v is not None for v in bbox_vals):
                parser.error(
                    "if any bbox override is provided, all of "
                    "--min-lat --max-lat --min-lon --max-lon are required"
                )
            bbox = RunBbox(
                min_lat=args.min_lat,
                max_lat=args.max_lat,
                min_lon=args.min_lon,
                max_lon=args.max_lon,
            )
        else:
            bbox = RunBbox(**_DEFAULT_BBOX)

        # Derive run root and run id.
        runs_root = Path(_resolve_path(args.runs_root)) if args.runs_root else DEFAULT_RUNS_ROOT
        if args.run_root:
            run_root = Path(_resolve_path(args.run_root))
            run_id = run_root.name
        else:
            run_id = args.run_id or _default_run_id()
            run_root = runs_root / run_id

        paths = RunPaths(
            runs_root=runs_root,
            run_id=run_id,
            run_root=run_root,
            blend_file=_resolve_path(args.blend_file) if args.blend_file else str(run_root / "workspace.blend"),
            meshes_dir=_resolve_path(args.meshes_dir) if args.meshes_dir else str(run_root / "meshes"),
            a3d_output=_resolve_path(args.a3d_output) if args.a3d_output else str(run_root / "output.a3d"),
            manifest_output=_resolve_path(args.manifest_output) if args.manifest_output else str(run_root / "manifest.json"),
            fixture_specs_output=str(run_root / "fixture_instances.json"),
            building_specs_output=str(run_root / "building_instances.json"),
            prebake_a3d_output=str(run_root / "output_prebake.a3d"),
            buildings_only_a3d_output=str(run_root / "output_buildings_only.a3d"),
            terrain_only_a3d_output=str(run_root / "output_terrain_only.a3d"),
            terrain_metadata_output=str(run_root / "terrain_metadata.json"),
            topology_instance_output=str(run_root / "topology_instance.json"),
            building_bake_summary_output=str(run_root / "building_bake_summary.json"),
            activation_env_output=str(run_root / "active_mesh_root.env"),
            normalized_osm_output=str(run_root / "osm_blosm_input.osm"),
            prebake_staged_output=str(run_root / "output_prebake_staged.a3d"),
            a3d_staged_output=str(run_root / "output_staged.a3d"),
        )

        params = RunParams(
            pipeline_mode=args.pipeline_mode,
            content_scale=args.content_scale,
            building_height_mult=args.building_height_mult,
            max_terrain_grid_segs=args.max_terrain_grid_segs,
            topology_mesh=_resolve_path(args.topology_mesh) if not args.no_topology_bake else None,
            topology_z_scale=args.topology_z_scale,
            road_width_mult=args.road_width_mult,
            no_topology_bake=args.no_topology_bake,
            stop_after_buildings_only=args.stop_after_buildings_only,
            resume_fixtures_from=_resolve_path(args.resume_fixtures_from) if args.resume_fixtures_from else None,
            osm_file_override=_resolve_path(args.osm_file) if args.osm_file else None,
            skip_import=args.skip_import,
            skip_pipeline=args.skip_pipeline,
            no_clean=args.no_clean,
            keep_previous_runs=args.keep_previous_runs,
            activate_run=args.activate_run and not args.no_activate_run,
            topology_bake_max_change_pct=TOPOLOGY_BAKE_MAX_CHANGE_PCT,
            building_bake_min_change_pct=BUILDING_BAKE_MIN_CHANGE_PCT,
            osm_material_postprocess=args.osm_material_postprocess,
            osm_carto_stamp=args.osm_carto_stamp,
            osm_carto_osm_only=args.osm_carto_osm_only,
            osm_carto_labels=args.osm_carto_labels,
            satellite_paint=args.satellite_paint,
            satellite_paint_zoom=args.satellite_paint_zoom,
            satellite_paint_max_tiles=args.satellite_paint_max_tiles,
            satellite_paint_force=args.satellite_paint_force,
            spawn_lat=getattr(args, "spawn_lat", None),
            spawn_lon=getattr(args, "spawn_lon", None),
            spawn_x=getattr(args, "spawn_x", None),
            spawn_y=getattr(args, "spawn_y", None),
        )

        return RunConfig(paths=paths, bbox=bbox, params=params)


def _elem_tags(elem):
    return {tag.get("k"): tag.get("v") for tag in elem.findall("tag")}


def _set_elem_tag(elem, key, value):
    tag = elem.find(f"tag[@k='{key}']")
    if tag is None:
        tag = ET.SubElement(elem, "tag")
        tag.set("k", key)
    tag.set("v", value)


def _normalized_osm_output_for_cfg(cfg):
    return Path(cfg.paths.normalized_osm_output) if cfg is not None else Path(NORMALIZED_OSM_OUTPUT)


def _prepare_blosm_osm_file(osm_file, cfg=None):
    """Materialize named building multipolygons for blosm file imports.

    Blosm file mode can ignore a named building relation when its outer way is
    untagged. Copying the relation's building/name tags onto that outer way
    keeps the generated run self-contained without mutating the source OSM.
    Always writes the effective OSM input to the run-local normalized artifact
    because later postprocess/proof stages are only allowed to read run-owned
    artifacts, not the original caller path.
    """
    normalized_output = _normalized_osm_output_for_cfg(cfg)
    os.makedirs(normalized_output.parent, exist_ok=True)
    try:
        tree = ET.parse(osm_file)
    except Exception:
        if Path(osm_file).resolve() != normalized_output.resolve():
            shutil.copy2(osm_file, normalized_output)
        return str(normalized_output)
    root = tree.getroot()
    ways = {way.get("id"): way for way in root.iter("way")}
    materialized = 0
    copy_keys = ("building", "name", "building:levels", "height", "roof:colour", "roof:levels")

    for rel in list(root.findall("relation")):
        rel_tags = _elem_tags(rel)
        if rel_tags.get("type") != "multipolygon" or "building" not in rel_tags or not rel_tags.get("name"):
            continue
        if rel_tags.get("public_transport") or rel_tags.get("amenity") == "shelter":
            continue
        outer_members = [
            member for member in rel.findall("member")
            if member.get("type") == "way" and (member.get("role") or "") == "outer"
        ]
        for member in outer_members:
            way = ways.get(member.get("ref"))
            if way is None:
                continue
            way_tags = _elem_tags(way)
            if way_tags.get("building") and way_tags.get("name"):
                continue
            for key in copy_keys:
                if key in rel_tags and not way_tags.get(key):
                    _set_elem_tag(way, key, rel_tags[key])
            _set_elem_tag(way, "asciicker:source_relation", rel.get("id") or "")
            materialized += 1

    if materialized > 0:
        tree.write(normalized_output, encoding="utf-8", xml_declaration=True)
        print(f"  materialized {materialized} building relation outer way(s): {normalized_output}")
    elif Path(osm_file).resolve() != normalized_output.resolve():
        shutil.copy2(osm_file, normalized_output)
    return str(normalized_output)


def _fetch_overpass_osm_file(cfg):
    """Download the current bbox into a per-run local OSM file.

    WARNING (FL-1175/FL-1179): Overpass-import-only runs are not enough for the
    rename → marker export contract. The baked label lane needs a real local
    .osm file on disk so `_get_osm_filepath()` can recover named building
    targets during the Blender phase.
    """
    bb = cfg.bbox
    query = f"""[out:xml][timeout:180];
(
  node({bb.min_lat},{bb.min_lon},{bb.max_lat},{bb.max_lon});
  way({bb.min_lat},{bb.min_lon},{bb.max_lat},{bb.max_lon});
  relation({bb.min_lat},{bb.min_lon},{bb.max_lat},{bb.max_lon});
);
out body;
>;
out skel qt;
"""
    os.makedirs(cfg.paths.run_root, exist_ok=True)
    proc = subprocess.run(
        [
            "curl",
            "-fsS",
            "-X",
            "POST",
            "https://overpass-api.de/api/interpreter",
            "--data-urlencode",
            f"data={query}",
            "-o",
            cfg.paths.normalized_osm_output,
        ],
        capture_output=True,
        text=True,
        timeout=240,
        cwd=PROJECT_ROOT,
    )
    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip() or f"curl exited {proc.returncode}"
        raise RuntimeError(f"failed to download Overpass OSM bbox: {err}")
    payload = Path(cfg.paths.normalized_osm_output).read_bytes() if os.path.isfile(cfg.paths.normalized_osm_output) else b""
    if not payload.strip():
        raise RuntimeError("failed to download Overpass OSM bbox: empty response")
    return _prepare_blosm_osm_file(cfg.paths.normalized_osm_output, cfg)


def _is_within(path_str, root):
    try:
        Path(path_str).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def _run_status_path(cfg):
    return Path(cfg.paths.run_root) / "run_status.json"


def _write_run_status(cfg, state, *, step=None, error=None):
    """Durable run lifecycle marker; partial artifacts must not look complete."""
    p = cfg.paths
    os.makedirs(p.run_root, exist_ok=True)
    payload = {
        "run_id": p.run_id,
        "run_root": str(p.run_root),
        "state": state,
        "step": step,
        "timestamp_local": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if error:
        payload["error"] = str(error)
    status_path = _run_status_path(cfg)
    tmp_path = status_path.with_suffix(status_path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp_path, status_path)
    return payload


def _write_run_status_best_effort(cfg, state, *, step=None, error=None):
    try:
        return _write_run_status(cfg, state, step=step, error=error)
    except Exception as status_error:
        print(
            f"WARNING: failed to write run_status state={state!r} step={step!r}: {status_error}",
            file=sys.stderr,
        )
        return None


def _read_run_status(cfg):
    try:
        with open(_run_status_path(cfg), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except OSError:
        return None
    except json.JSONDecodeError:
        return {"state": "invalid", "path": str(_run_status_path(cfg))}
    return None


def step_preflight(cfg, allow_custom_output=False):
    """Print run targets and abort if any path escapes the canonical run root."""
    p = cfg.paths
    print()
    header("Preflight: OUTPUT TARGETS", char="─", width=58)
    outputs = {
        "runs_root": str(p.runs_root),
        "run_id": p.run_id,
        "run_root": str(p.run_root),
        "blend_file": p.blend_file,
        "meshes_dir": p.meshes_dir,
        "a3d_output": p.a3d_output,
        "terrain_only_a3d_output": p.terrain_only_a3d_output,
        "prebake_a3d_output": p.prebake_a3d_output,
        "buildings_only_a3d_output": p.buildings_only_a3d_output,
        "manifest_output": p.manifest_output,
        "fixture_specs_output": p.fixture_specs_output,
        "building_specs_output": p.building_specs_output,
        "terrain_metadata_output": p.terrain_metadata_output,
        "topology_instance_output": p.topology_instance_output,
        "building_bake_summary_output": p.building_bake_summary_output,
        "activation_env_output": p.activation_env_output,
        "fixtures_dir": FIXTURES_DIR,
    }
    for label, value in outputs.items():
        print(f"  {label}: {value}")

    canonical_runs_root = PROJECT_ROOT / "assets" / "meshes"
    conflicts = []
    if not _is_within(str(p.runs_root), canonical_runs_root):
        conflicts.append(f"runs_root escapes assets/meshes/: {p.runs_root}")

    if not _is_within(str(p.run_root), p.runs_root):
        conflicts.append(f"run_root escapes runs_root: {p.run_root}")

    if Path(p.run_root).absolute() == Path(p.runs_root).absolute():
        conflicts.append(f"run_root must be a deletable child folder, not runs_root itself: {p.run_root}")

    if allow_custom_output:
        conflicts.append("--allow-custom-output is disabled; OSM outputs must stay inside run_root")

    for label in (
        "blend_file",
        "meshes_dir",
        "a3d_output",
        "terrain_only_a3d_output",
        "prebake_a3d_output",
        "buildings_only_a3d_output",
        "manifest_output",
        "fixture_specs_output",
        "building_specs_output",
        "terrain_metadata_output",
        "topology_instance_output",
        "building_bake_summary_output",
        "activation_env_output",
    ):
        value = outputs[label]
        if not _is_within(value, p.run_root):
            conflicts.append(f"{label} escapes run_root: {value}")

    if Path(p.meshes_dir).absolute() == Path(FIXTURES_DIR).absolute():
        conflicts.append(f"meshes_dir overlaps canonical fixtures dir: {p.meshes_dir}")

    if _is_within(p.meshes_dir, FIXTURES_DIR):
        conflicts.append(f"meshes_dir nests inside canonical fixtures dir: {p.meshes_dir}")

    if conflicts:
        print("  ERROR: refusing output targets outside the canonical OSM run root")
        for item in conflicts:
            print(f"    - {item}")
        sys.exit(2)


def _resolve_active_mesh_pointer_target():
    if not ACTIVE_MESH_ROOT_POINTER.exists():
        return None
    raw_pointer = ACTIVE_MESH_ROOT_POINTER.read_text(encoding="utf-8").strip()
    if not raw_pointer:
        return None
    pointer_target = Path(raw_pointer)
    if not pointer_target.is_absolute():
        pointer_target = (PROJECT_ROOT / pointer_target).absolute()
    return pointer_target


def _remove_active_mesh_pointer_for_roots(roots, removed):
    pointer_target = _resolve_active_mesh_pointer_target()
    if pointer_target is None:
        return
    for root in roots:
        if _is_within(str(pointer_target), root):
            ACTIVE_MESH_ROOT_POINTER.unlink()
            removed.append(str(ACTIVE_MESH_ROOT_POINTER))
            return


def _previous_generated_run_dirs(cfg):
    runs_root = Path(cfg.paths.runs_root)
    if not runs_root.is_dir():
        return []
    current_root = Path(cfg.paths.run_root).absolute()
    candidates = []
    for child in runs_root.iterdir():
        if child.is_symlink() or not child.is_dir():
            continue
        if child.absolute() == current_root:
            continue
        if not _looks_like_generated_osm_run_dir(child):
            continue
        candidates.append(child)
    candidates.sort(key=lambda q: (q.stat().st_mtime, q.name), reverse=True)
    return candidates


def _looks_like_generated_osm_run_dir(path):
    if any(pattern.match(path.name) for pattern in GENERATED_RUN_DIR_PATTERNS):
        return True
    try:
        child_names = {child.name for child in path.iterdir()}
    except OSError:
        return False
    return bool(child_names & GENERATED_RUN_ARTIFACT_MARKERS)


def _osm_file_would_be_deleted_by_clean(cfg, roots):
    osm_file = cfg.params.osm_file_override
    if not osm_file or cfg.params.no_clean:
        return False
    return any(_is_within(osm_file, root) for root in roots)


def _reject_self_deleting_osm_file(cfg, roots):
    """Fail closed when --osm-file is inside a run dir that clean would delete."""
    if not _osm_file_would_be_deleted_by_clean(cfg, roots):
        return
    raise RuntimeError(
        "--osm-file points inside an OSM run folder that step_clean would delete. "
        "Use an external source OSM path, run with --no-clean for explicit resume, "
        "or promote/copy only the required source artifact outside assets/meshes/osm_runs. "
        "The runner will not create hidden temp survivors."
    )


def _validate_clean_inputs(cfg, roots):
    _reject_self_deleting_osm_file(cfg, roots)


def step_clean(cfg):
    """GR-2 + GR-11: Delete current and prior OSM run folders before re-run."""
    print()
    header("Step 0: CLEAN", char="─", width=58)
    removed = []
    previous_runs = _previous_generated_run_dirs(cfg)
    prune_previous = previous_runs[max(0, cfg.params.keep_previous_runs):]
    _validate_clean_inputs(cfg, [cfg.paths.run_root, *prune_previous])

    # Build the deletion list and warn BEFORE any removal.
    stale_to_delete = [r for r in prune_previous if r.exists()]
    current_exists = cfg.paths.run_root.exists()

    if stale_to_delete:
        print(f"  {status('WARN', f'{len(stale_to_delete)} previous run(s) will be permanently deleted:')}")
        for r in stale_to_delete:
            print(f"    {r}")
        print()
        print("  To keep any run permanently, promote it to the official assets folder first:")
        print(f"    python3 scripts/sbu_e2e_run.py promote {stale_to_delete[0]}  --name <short-name>")
        print("  Or pass --no-clean to skip all deletion, or --keep-previous-runs N to keep the N newest.")
        print()
        for remaining in (3, 2, 1):
            print(f"\r  Continuing in {remaining}s...  (Ctrl-C to abort)", end="", flush=True)
            time.sleep(1)
        print("\r  Proceeding with cleanup.                       ")
        print()

    if current_exists:
        print(f"  Deleting current run folder: {cfg.paths.run_root}")

    _remove_active_mesh_pointer_for_roots([cfg.paths.run_root, *prune_previous], removed)

    if current_exists:
        shutil.rmtree(cfg.paths.run_root)
        removed.append(str(cfg.paths.run_root))

    for stale_run in stale_to_delete:
        shutil.rmtree(stale_run)
        removed.append(str(stale_run))

    if removed:
        print(f"  Removed {len(removed)} item(s)")
    else:
        print("  Nothing to clean.")


def _ensure_blend_file(cfg):
    """Create a blank .blend file if it doesn't exist.

    BlenderBridge passes blend_file to `blender --background <file>`, which
    fails if the file is missing. Blender 4.5 can crash when launched as a
    child of Python just to save an empty file, so the runner copies a tracked
    blank workspace template instead of bootstrapping Blender here.
    """
    blend_file = cfg.paths.blend_file
    if os.path.isfile(blend_file):
        return
    print(f"  Creating blank blend file: {os.path.basename(blend_file)}")
    os.makedirs(os.path.dirname(blend_file), exist_ok=True)
    if not BLANK_WORKSPACE_TEMPLATE.is_file():
        raise RuntimeError(
            f"blank workspace template missing: {BLANK_WORKSPACE_TEMPLATE}. "
            "Regenerate it with Blender from the shell, not from this Python runner."
        )
    shutil.copy2(BLANK_WORKSPACE_TEMPLATE, blend_file)


def step_import(cfg):
    """Import OSM data from blosm into a fresh blend file.

    Uses blosm file mode when cfg.params.osm_file_override is set, otherwise
    downloads from Overpass (avoids 504 timeouts on the API).
    """
    osm_file = cfg.params.osm_file_override
    bb = cfg.bbox
    blend_file = cfg.paths.blend_file
    print()
    header("Step 1: BLOSM IMPORT", char="─", width=58)
    _ensure_blend_file(cfg)
    print(f"  bbox: {bb.min_lat:.5f}–{bb.max_lat:.5f} / {bb.min_lon:.5f}–{bb.max_lon:.5f}")
    if osm_file:
        print(f"  source: file ({osm_file})")
    else:
        print(f"  source: Overpass server")
    t0 = time.time()

    if osm_file:
        blosm_osm_file = _prepare_blosm_osm_file(osm_file, cfg)
    else:
        blosm_osm_file = _fetch_overpass_osm_file(cfg)
        print(f"  overpass cache: {blosm_osm_file}")

    if blosm_osm_file:
        if blosm_osm_file != osm_file:
            print(f"  blosm input: {blosm_osm_file}")
        # Use blosm's file mode with the local OSM file
        from cli_anything.blender.core.bridge import BlenderBridge
        bridge = BlenderBridge(blend_file=blend_file)
        result = bridge.execute(f"""
import addon_utils
addon_utils.enable("blosm", default_set=True)
import bpy

props = bpy.context.scene.blosm
props.dataType = "osm"
props.osmSource = "file"
props.osmFilepath = {blosm_osm_file!r}
props.loadMissingMembers = False
props.mode = "3Dsimple"
props.minLat = {bb.min_lat}
props.maxLat = {bb.max_lat}
props.minLon = {bb.min_lon}
props.maxLon = {bb.max_lon}
props.buildings = True

before = len(bpy.context.scene.objects)
result = bpy.ops.blosm.import_data()
after = len(bpy.context.scene.objects)

if 'FINISHED' not in result:
    raise RuntimeError("blosm.import_data returned " + str(result))

bpy.ops.wm.save_as_mainfile(filepath={blend_file!r})
buildings_list = [o for o in bpy.context.scene.objects if o.type == 'MESH' and o.get('building')]
_data = {{
    "status": "imported",
    "operator_result": str(result),
    "objects_before": before,
    "objects_after": after,
    "objects_added": after - before,
    "buildings_found": len(buildings_list),
    "source": "file" if {bool(osm_file)!r} else "overpass-cache",
}}
""", timeout=300)

    elapsed = time.time() - t0
    ok = result.get("ok", False) if isinstance(result, dict) else False
    data = result.get("data", {}) if isinstance(result, dict) else {}

    if ok:
        print(f"  blosm import OK ({elapsed:.1f}s)")
        if isinstance(data, dict):
            for k, v in data.items():
                print(f"    {k}: {v}")
    else:
        err = result.get("error", "unknown") if isinstance(result, dict) else str(result)
        stderr = result.get("stderr", "") if isinstance(result, dict) else ""
        tb = result.get("traceback", "") if isinstance(result, dict) else ""
        print(f"  blosm import FAILED ({elapsed:.1f}s): {err}")
        if tb:
            print(f"  Traceback:\n{tb}")
        if stderr:
            # Show last 20 lines of Blender stderr
            lines = stderr.strip().splitlines()[-20:]
            print(f"  Blender stderr (last {len(lines)} lines):")
            for line in lines:
                print(f"    {line}")
        sys.exit(1)

    return result


def step_blender_phase(cfg):
    """Run the Blender-owned OSM phase and export either the final or prebake A3D."""
    p = cfg.paths
    par = cfg.params
    phase_output = _blender_phase_output_path(p, par.pipeline_mode)
    fixture_specs_output = p.fixture_specs_output if par.pipeline_mode == "baked" else None
    building_specs_output = p.building_specs_output if par.pipeline_mode == "baked" else None
    terrain_metadata_output = p.terrain_metadata_output if par.pipeline_mode == "baked" else None
    if par.pipeline_mode == "baked":
        print()
        header("Step 2: BLENDER PHASE (TERRAIN-ONLY EXPORT)", char="─", width=58)
    else:
        print()
        header("Step 2: BLENDER PHASE (TRADITIONAL EXPORT)", char="─", width=58)
    t0 = time.time()

    result = full_pipeline(
        blend_file=p.blend_file,
        meshes_dir=p.meshes_dir,
        a3d_output=phase_output,
        target_faces=300,
        subdivision_level=3,
        content_scale=par.content_scale,
        building_height_mult=par.building_height_mult,
        road_width_mult=par.road_width_mult,
        fixtures_dir=FIXTURES_DIR,
        fixture_specs_output=fixture_specs_output,
        building_specs_output=building_specs_output,
        terrain_metadata_output=terrain_metadata_output,
        save=True,
    )

    elapsed = time.time() - t0
    ok = result.get("ok", False) if isinstance(result, dict) else False
    data = result.get("data", {}) if isinstance(result, dict) else {}

    if ok:
        print(f"  Blender phase completed ({elapsed:.1f}s)")
        if isinstance(data, dict):
            status = data.get("status", "unknown")
            done = data.get("steps_done", [])
            failed = data.get("steps_failed", [])
            a3d_size = data.get("a3d_size_bytes", 0)
            akm_count = data.get("total_akms", 0)
            print(f"    status: {status}")
            print(f"    done: {', '.join(done)}")
            if failed:
                print(f"    FAILED: {', '.join(failed)}")
            terrain_sz = data.get("terrain_size", "?")
            print(f"    phase output: {phase_output}")
            print(f"    A3D size: {a3d_size:,} bytes")
            print(f"    AKMs exported: {akm_count}")
            print(f"    terrain_size: {terrain_sz}")
            if os.path.isfile(phase_output):
                markers = _report_embedded_markers(phase_output, "post-blender-export")
                _report_building_name_contract(building_specs_output, markers, "post-blender-export")
    else:
        err = result.get("error", "unknown") if isinstance(result, dict) else str(result)
        tb = result.get("traceback", "") if isinstance(result, dict) else ""
        print(f"  Blender phase FAILED ({elapsed:.1f}s): {err}")
        if tb:
            print(f"  Traceback:\n{tb}")
        sys.exit(1)

    return result


def _filter_and_probe_fixtures(map_path, fixture_json_path):
    """FL-1143 + FL-1144: filter sentinel fixtures, probe terrain Z, write back.

    Reads fixture_json_path, removes instances whose XY falls outside the
    terrain (sentinel coords like (-32,-32)), then runs PROBE_TERRAIN for
    each surviving fixture and updates transform[14] (Z) with the live
    terrain height.  Writes the cleaned result back to fixture_json_path
    and returns a summary dict.
    """
    _OFFMAP_THRESHOLD = -5.0  # terrain starts at 0; sentinel coords are ~-32

    with open(fixture_json_path, encoding="utf-8") as fh:
        fixtures = json.load(fh)

    # FL-1143: filter sentinels (off-map coords, e.g. -32,-32)
    valid = []
    sentinel_count = 0
    for fx in fixtures:
        t = fx.get("transform", [])
        bx = t[12] if len(t) > 12 else 0.0
        by = t[13] if len(t) > 13 else 0.0
        if bx < _OFFMAP_THRESHOLD or by < _OFFMAP_THRESHOLD:
            sentinel_count += 1
        else:
            valid.append(fx)

    if sentinel_count:
        print(f"  [FL-1143] filtered {sentinel_count} sentinel fixture(s) (off-map coords)")

    # FL-1144: probe terrain Z for each valid fixture
    if valid:
        probe_cmds = []
        for fx in valid:
            t = fx["transform"]
            probe_cmds.append(f"PROBE_TERRAIN {t[12]:.3f} {t[13]:.3f}")
        try:
            stdout = _run_asciiid_batch(map_path, probe_cmds)
            heights = []
            for line in stdout.splitlines():
                m = re.search(r"height=(\d+)", line)
                if m:
                    heights.append(float(m.group(1)))
            while len(heights) < len(valid):
                heights.append(None)
            probed = patched = 0
            for fx, h in zip(valid, heights):
                if h is not None:
                    fx["transform"][14] = h
                    probed += 1
                    if h != float(SBU_TERRAIN_BASELINE):
                        patched += 1
            print(f"  [FL-1144] probed Z for {probed}/{len(valid)} fixtures"
                  f" ({patched} non-baseline heights)")
        except Exception as exc:
            print(f"  [FL-1144] PROBE_TERRAIN failed ({exc}) — keeping Z={SBU_TERRAIN_BASELINE} fallback")

    with open(fixture_json_path, "w", encoding="utf-8") as fh:
        json.dump(valid, fh, indent=2, sort_keys=True)

    return {
        "total_input": len(fixtures),
        "sentinel_filtered": sentinel_count,
        "valid": len(valid),
    }


def _probe_terrain_heights(map_path, label=""):
    """Diagnostic: probe terrain at known SBU campus positions and print heights."""
    probes = [
        (800, 600, "main quad"),
        (944, 857, "topology anchor"),
        (1100, 1000, "campus building zone"),
    ]
    cmds = [f"PROBE_TERRAIN {x} {y}" for x, y, _ in probes]
    try:
        stdout = _run_asciiid_batch(map_path, cmds)
        heights = []
        for line in stdout.splitlines():
            m = re.search(r"height=(\d+)", line)
            if m:
                heights.append(int(m.group(1)))
        tag = f" [{label}]" if label else ""
        all_flat = all(h == SBU_TERRAIN_BASELINE for h in heights) if heights else False
        for (x, y, name), h in zip(probes, heights):
            if h == SBU_TERRAIN_BASELINE:
                print(f"    {status('WARN', f'PROBE{tag} ({x},{y}) {name}: height={h} FLAT')}")
            else:
                print(f"    {status('OK', f'PROBE{tag} ({x},{y}) {name}: height={h}')}")
        if all_flat:
            print(f"    {status('FAIL', f'PROBE{tag} ALL PROBES FLAT (height={SBU_TERRAIN_BASELINE}) — terrain may be uninitialized or destroyed')}")
        _run_log.emit("probe_terrain", label=label, probes={name: h for (_, _, name), h in zip(probes, heights)}, all_flat=all_flat)
        return heights
    except Exception as exc:
        tag = f" [{label}]" if label else ""
        print(f"    {status('FAIL', f'PROBE{tag} failed: {exc}')}")
        return []


def _a3d_file_stats(map_path, label=""):
    """Emit file size, patch count, and height range for an A3D file."""
    tag = f" [{label}]" if label else ""
    if not os.path.isfile(map_path):
        print(f"    {status('WARN', f'STATS{tag} file not found: {map_path}')}")
        _run_log.emit("a3d_stats", label=label, path=map_path, exists=False)
        return {}
    size = os.path.getsize(map_path)
    stats = {"path": map_path, "size_bytes": size, "exists": True}
    try:
        fmt = _load_a3d_format()
        with open(map_path, "rb") as f:
            hdr = fmt.A3DHeader.from_file(f)
            stats["num_patches"] = hdr.num_patches
            stats["terrain_size"] = int(hdr.num_patches ** 0.5) * 8 if hdr.num_patches > 0 else 0
            heights = []
            for _ in range(hdr.num_patches):
                patch = fmt.A3DPatch.from_file(f)
                for row in patch.height:
                    heights.extend(int(h) for h in row)
            if heights:
                heights_sorted = sorted(heights)
                stats["height_min"] = heights_sorted[0]
                stats["height_max"] = heights_sorted[-1]
                stats["height_median"] = heights_sorted[len(heights_sorted) // 2]
                stats["height_range"] = heights_sorted[-1] - heights_sorted[0]
    except Exception:
        pass
    num_p = stats.get("num_patches", "?")
    ter_s = stats.get("terrain_size", "?")
    height_msg = ""
    if "height_range" in stats:
        height_msg = (
            f" h=[{stats['height_min']},{stats['height_max']}]"
            f" median={stats['height_median']} range={stats['height_range']}"
        )
    print(f"    {status('INFO', f'STATS{tag} size={size:,} patches={num_p} terrain={ter_s}{height_msg}')}")
    _run_log.emit("a3d_stats", label=label, **stats)
    return stats


def _list_asciiid_meshes(map_path, mesh_root=None, label=""):
    """Run LIST_MESHES in asciiid to see which AKMs are actually loaded."""
    tag = f" [{label}]" if label else ""
    try:
        stdout = _run_asciiid_batch(map_path, ["LIST_MESHES"], mesh_root=mesh_root)
        lines = [l.strip() for l in stdout.splitlines() if ".akm" in l.lower()]
        mesh_names = []
        for line in lines:
            parts = line.split()
            for p in parts:
                if p.lower().endswith(".akm"):
                    mesh_names.append(p)
        print(f"    {status('INFO', f'MESHES{tag} {len(mesh_names)} loaded: {', '.join(mesh_names[:8])}')}")
        if len(mesh_names) > 8:
            print(f"      ... {len(mesh_names) - 8} more")
        _run_log.emit("list_meshes", label=label, count=len(mesh_names), meshes=mesh_names)
        return mesh_names
    except Exception as exc:
        print(f"    {status('WARN', f'MESHES{tag} LIST_MESHES failed: {exc}')}")
        _run_log.emit("list_meshes", label=label, error=str(exc))
        return []


def _report_embedded_markers(map_path, label=""):
    """Diagnostic: inspect embedded markers so bake bugs stay separate from runtime bugs."""
    tag = f" [{label}]" if label else ""
    try:
        markers = _list_embedded_markers(map_path=map_path)
    except Exception as exc:
        print(f"    {status('FAIL', f'MARKERS{tag} failed: {exc}')}")
        return []

    building_markers = [m for m in markers if str(m.get("type", "")).lower() == "building"]
    print(f"    {status('INFO', f'MARKERS{tag} total={len(markers)} building={len(building_markers)}')}")
    for marker in building_markers[:5]:
        print(
            f"      {marker.get('name', '')} @ "
            f"({float(marker.get('x', 0.0)):.1f},{float(marker.get('y', 0.0)):.1f}) "
            f"label={marker.get('label', '')}"
        )
    if len(building_markers) > 5:
        print(f"      ... {len(building_markers) - 5} more building marker(s)")
    if not building_markers:
        print(f"    {status('WARN', f'MARKERS{tag} no embedded building markers present')}")
    return markers


def _report_building_name_contract(building_specs_path, markers, label=""):
    """Explain zero-marker failures caused by generic deferred building names."""
    tag = f" [{label}]" if label else ""
    if not building_specs_path or not os.path.isfile(building_specs_path):
        return {}
    try:
        with open(building_specs_path, encoding="utf-8") as fh:
            rows = json.load(fh)
    except Exception as exc:
        print(f"    {status('WARN', f'BUILDING_NAMES{tag} failed: {exc}')}")
        _run_log.emit("building_name_contract", label=label, error=str(exc))
        return {}

    mesh_names = [str(row.get("mesh_name", "")) for row in rows if isinstance(row, dict)]
    generic = [name for name in mesh_names if re.match(r"^Building_\d+\.akm$", name)]
    named = [name for name in mesh_names if name and name not in generic]
    marker_count = len(markers or [])
    print(f"    {status('INFO', f'BUILDING_NAMES{tag} total={len(mesh_names)} generic={len(generic)} named={len(named)} markers={marker_count}')}")
    if mesh_names and len(generic) == len(mesh_names) and marker_count == 0:
        print(
            f"    {status('WARN', f'BUILDING_NAMES{tag} all deferred buildings are generic Building_### names; marker export filters them, so this run cannot produce minimap labels')}"
        )
    _run_log.emit(
        "building_name_contract",
        label=label,
        total=len(mesh_names),
        generic=len(generic),
        named=len(named),
        markers=marker_count,
    )
    return {
        "total": len(mesh_names),
        "generic": len(generic),
        "named": len(named),
        "markers": marker_count,
    }


def _classify_terrain_diff(changed, total, label="", low_change_ok_reason=None):
    pct = (changed / total * 100) if total > 0 else 0.0
    tag = f" [{label}]" if label else ""
    if changed == 0:
        verdict = "FAIL"
        msg = f"DIFF{tag} 0/{total:,} cells changed (0%) — bake had NO EFFECT"
    elif pct < 1.0:
        if low_change_ok_reason:
            verdict = "OK"
            msg = (
                f"DIFF{tag} {changed:,}/{total:,} cells changed ({pct:.1f}%) — "
                f"LOW CHANGE OK: {low_change_ok_reason}"
            )
        else:
            verdict = "WARN"
            msg = f"DIFF{tag} {changed:,}/{total:,} cells changed ({pct:.1f}%) — SUSPICIOUSLY LOW (FL-1176 symptom: stale 1/12-scale AKMs)"
    elif pct > 90.0:
        verdict = "WARN"
        msg = f"DIFF{tag} {changed:,}/{total:,} cells changed ({pct:.1f}%) — SUSPICIOUSLY HIGH (FL-1169 symptom: terrain destruction)"
    else:
        verdict = "OK"
        msg = f"DIFF{tag} {changed:,}/{total:,} cells changed ({pct:.1f}%)"
    return verdict, msg, pct


def _compare_terrain_heights(before_path, after_path, label="", low_change_ok_reason=None):
    """Compare two A3D files and report terrain height change coverage.

    This is THE critical diagnostic for FL-1176 (building bake covering only 0.7%
    of footprint) and FL-1169 (topology bake destroying terrain). The controlled
    diagnostic that proved FL-1176 showed 1,389,377 vs 25,655 changed vertices.

    Returns dict with changed/total/pct/verdict or empty dict on failure.
    """
    tag = f" [{label}]" if label else ""
    if not os.path.isfile(before_path) or not os.path.isfile(after_path):
        print(f"    {status('WARN', f'DIFF{tag} missing file(s) — cannot compare')}")
        return {}
    try:
        fmt = _load_a3d_format()

        def _read_heights_by_patch(path):
            heights_by_xy = {}
            order = []
            with open(path, "rb") as f:
                hdr = fmt.A3DHeader.from_file(f)
                for _ in range(hdr.num_patches):
                    patch = fmt.A3DPatch.from_file(f)
                    key = (patch.x, patch.y)
                    order.append(key)
                    if key in heights_by_xy:
                        raise ValueError(f"duplicate terrain patch coordinate {key} in {path}")
                    # Each patch has a 5x5 grid of height vertices
                    heights_by_xy[key] = tuple(h for row in patch.height for h in row)
            return heights_by_xy, order

        h_before, before_order = _read_heights_by_patch(before_path)
        h_after, after_order = _read_heights_by_patch(after_path)

        before_keys = set(h_before)
        after_keys = set(h_after)
        missing = before_keys - after_keys
        extra = after_keys - before_keys
        if missing or extra:
            print(
                f"    {status('WARN', f'DIFF{tag} patch coordinate mismatch: '
                          f'{len(missing)} missing, {len(extra)} extra')}"
            )
            _run_log.emit(
                "terrain_diff",
                label=label,
                missing_patches=len(missing),
                extra_patches=len(extra),
                verdict="WARN",
            )
            return {"mismatch": True, "missing_patches": len(missing), "extra_patches": len(extra)}

        common_keys = sorted(before_keys & after_keys)
        total = sum(len(h_before[key]) for key in common_keys)
        changed = sum(
            1
            for key in common_keys
            for a, b in zip(h_before[key], h_after[key])
            if a != b
        )
        reordered = before_order != after_order
        verdict, msg, pct = _classify_terrain_diff(
            changed,
            total,
            label=label,
            low_change_ok_reason=low_change_ok_reason,
        )

        if reordered:
            msg += " (patch file order changed; compared by patch coordinate)"
        print(f"    {status(verdict, msg)}")
        _run_log.emit(
            "terrain_diff",
            label=label,
            changed=changed,
            total=total,
            pct=round(pct, 2),
            verdict=verdict,
            reordered=reordered,
            low_change_ok_reason=low_change_ok_reason,
        )
        return {"changed": changed, "total": total, "pct": pct, "verdict": verdict, "reordered": reordered}
    except Exception as exc:
        print(f"    {status('FAIL', f'DIFF{tag} comparison failed: {exc}')}")
        _run_log.emit("terrain_diff", label=label, error=str(exc))
        return {}


TOPOLOGY_BAKE_MAX_STUCK_PCT = osm_bake_contract.TOPOLOGY_BAKE_MAX_STUCK_PCT
BUILDING_BAKE_MAX_STUCK_PCT = osm_bake_contract.BUILDING_BAKE_MAX_STUCK_PCT


def _parse_bake_coverage(stdout: str) -> list:
    return osm_bake_contract.parse_bake_coverage(stdout)


def _check_bake_per_instance_coverage(coverage: list, label: str, max_stuck_pct: float = BUILDING_BAKE_MAX_STUCK_PCT) -> dict:
    """Gate per-instance bake coverage for stuck-at-baseline terrain holes.

    Replaces _report_terrain_floor_regressions (FL-1181 Candidate 3).

    The old gate checked h < baseline and SKIPPED cells at exactly 120
    (``if h >= baseline: continue``).  Holes ARE cells stuck at 120 in terrain
    that should have been raised — the old gate was blind to the exact failure
    mode it was meant to catch.

    This gate checks: for each instance, what fraction of its AABB footprint
    cells are still at BAKE_COVERAGE_BASELINE after the bake?  A high fraction
    means the bake wrote nothing to that building's ground — terrain hole.

    Raises RuntimeError (hard fail) if any instance exceeds max_stuck_pct.
    Returns {"ok": bool, "stuck_instances": list, "total_instances": int}.
    """
    tag = f" [{label}]" if label else ""
    verdict = osm_bake_contract.evaluate_bake_coverage(coverage, max_stuck_pct)
    if not verdict["has_data"]:
        print(f"    {status('WARN', f'COVERAGE{tag} no per-instance data — binary predates FL-1181 fix or bake_height=0')}")
        _run_log.emit("bake_coverage", label=label, ok=True, total_instances=0)
        return {"ok": True, "stuck_instances": [], "total_instances": 0}

    stuck = verdict["stuck_instances"]
    total = verdict["total_instances"]
    if stuck:
        for s in stuck:
            msg = (
                f"COVERAGE{tag} {s['name']}: {s['at_baseline']}/{s['footprint_cells']} "
                f"cells stuck at baseline ({s['stuck_pct']}% > limit {max_stuck_pct:.0f}%) — terrain hole"
            )
            print(f"    {status('FAIL', msg)}")
        _run_log.emit(
            "bake_coverage", label=label, ok=False,
            stuck_count=len(stuck), total_instances=total,
            stuck_instances=[{"name": s["name"], "stuck_pct": s["stuck_pct"],
                              "at_baseline": s["at_baseline"], "footprint_cells": s["footprint_cells"]}
                             for s in stuck],
        )
        raise RuntimeError(
            f"Bake coverage FAIL [{label}]: {len(stuck)}/{total} instance(s) have "
            f">{max_stuck_pct:.0f}% of footprint stuck at baseline {osm_bake_contract.BAKE_COVERAGE_BASELINE} — "
            f"terrain holes in: {', '.join(s['name'] for s in stuck)}.  "
            "Staged output discarded."
        )

    print(f"    {status('OK', f'COVERAGE{tag} all {total} instance(s) passed per-footprint hole check')}")
    _run_log.emit(
        "bake_coverage", label=label, ok=True, total_instances=total,
        instances=verdict["instances"],
    )
    return {"ok": True, "stuck_instances": [], "total_instances": total}


def _blender_phase_output_path(paths, pipeline_mode: str) -> str:
    """Own the Blender-output map path for each pipeline mode.

    Traditional mode writes the final A3D directly. Baked mode writes the
    terrain-only handoff map first; asciiid then promotes that through the
    prebake/buildings-only/final lanes.
    """
    return paths.terrain_only_a3d_output if pipeline_mode == "baked" else paths.a3d_output


# ┌─────────────────────────────────────────────────────────────────────────┐
# │ FIX (FL-1176): mesh_root env parameter added 2026-04-22.              │
# │ Without ASCIICKER_ACTIVE_MESH_ROOT, asciiid loads stale 1/12-scale    │
# │ AKMs from assets/meshes/ instead of run-local full-scale meshes.      │
# │ The run-local SBU E2E terrain/building ratio is the authority.         │
# │ Do NOT compare root-vs-run mesh size and call the run-local export     │
# │ "wrong" from that alone; stale fallback root meshes have caused that    │
# │ false diagnosis multiple times already.                                 │
# │ FL-2553: even with the correct mesh_root, building-bases / ground-touch │
# │ proof is still a different gate from visible size/origin in asciiid.    │
# │ Do not claim runtime scale closure from base-Z agreement alone.         │
# │ Building bake covers 0.7% of footprint (25K vs 1.3M height vertices). │
# │ Code landed but NEVER proven in a full pipeline run as of 2026-04-30. │
# └─────────────────────────────────────────────────────────────────────────┘
def _run_asciiid_batch(map_path, commands, mesh_root=None):
    env = None
    if mesh_root:
        env = os.environ.copy()
        env["ASCIICKER_ACTIVE_MESH_ROOT"] = str(Path(mesh_root).resolve())
    print(f"    [asciiid] map={map_path}")
    if mesh_root:
        print(f"    [asciiid] ASCIICKER_ACTIVE_MESH_ROOT={Path(mesh_root).resolve()}")
    print(f"    [asciiid] commands={len(commands)}: {', '.join(c.split()[0] for c in commands)}")
    proc = subprocess.run(
        [ASCIIID_BIN, "--batch", "--map", map_path],
        input="".join(cmd if cmd.endswith("\n") else cmd + "\n" for cmd in commands),
        capture_output=True,
        text=True,
        timeout=300,
        cwd=PROJECT_ROOT,
        env=env,
    )
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    _run_log.emit("asciiid_batch",
                  map=map_path,
                  commands=[c.split()[0] for c in commands],
                  returncode=proc.returncode,
                  stdout_lines=len(stdout.splitlines()) if stdout else 0,
                  stderr_lines=len(stderr.splitlines()) if stderr else 0,
                  mesh_root=str(mesh_root) if mesh_root else None)
    if stderr:
        print(f"    [asciiid] stderr ({len(stderr.splitlines())} lines):")
        for line in stderr.splitlines()[-5:]:
            print(f"      {line}")
    if proc.returncode != 0:
        raise RuntimeError(f"asciiid batch failed ({proc.returncode}): {stderr or stdout or 'no output'}")
    errors = [line for line in stdout.splitlines() if "[MCP] Error:" in line]
    if errors:
        raise RuntimeError("asciiid batch reported errors:\n" + "\n".join(errors))
    return stdout


def _append_instances_from_json(map_path, json_path):
    proc = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "docs/agent/cli-anything" / "a3d_edit.py"),
         "append", map_path, "--json", json_path],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=PROJECT_ROOT,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"append failed for {json_path}")
    return proc.stdout.strip().splitlines()


def _run_direct_building_footprint_bake(input_map, building_specs, output_map, summary_path, material_id=5, footprint_inset=1.0):
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "pipeline" / "bake_osm_building_footprints.py"),
        "--map", input_map,
        "--buildings", building_specs,
        "--output", output_map,
        "--material-id", str(material_id),
        "--footprint-inset", str(footprint_inset),
        "--summary", summary_path,
    ]
    print(f"    [footprint-bake] map={input_map}")
    print(f"    [footprint-bake] buildings={building_specs}")
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,
        cwd=PROJECT_ROOT,
    )
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    _run_log.emit(
        "direct_building_footprint_bake",
        input_map=input_map,
        building_specs=building_specs,
        output_map=output_map,
        summary_path=summary_path,
        material_id=material_id,
        footprint_inset=footprint_inset,
        returncode=proc.returncode,
        stdout_lines=len(stdout.splitlines()) if stdout else 0,
        stderr_lines=len(stderr.splitlines()) if stderr else 0,
    )
    if stderr:
        print(f"    [footprint-bake] stderr ({len(stderr.splitlines())} lines):")
        for line in stderr.splitlines()[-5:]:
            print(f"      {line}")
    if proc.returncode != 0:
        raise RuntimeError(f"direct building footprint bake failed ({proc.returncode}): {stderr or stdout or 'no output'}")
    try:
        with open(summary_path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        raise RuntimeError(f"direct building footprint bake wrote unreadable summary {summary_path}: {exc}") from exc


def _check_direct_building_bake_summary(summary, label):
    failed = [row for row in summary.get("per_building", []) if not row.get("ok")]
    total = int(summary.get("buildings_total", 0) or 0)
    baked = int(summary.get("buildings_baked", 0) or 0)
    if total <= 0:
        raise RuntimeError(f"{label} contract FAIL: no building specs were provided")
    if failed or baked != total:
        preview = ", ".join(
            f"{row.get('name')}:{row.get('reason')}" for row in failed[:8]
        )
        raise RuntimeError(
            f"{label} contract FAIL: baked {baked}/{total} building footprint(s); "
            f"failed={len(failed)} {preview}"
        )
    height_writes = int(summary.get("height_vertices_written", 0) or 0)
    visual_writes = int(summary.get("visual_cells_written", 0) or 0)
    if height_writes <= 0 or visual_writes <= 0:
        raise RuntimeError(
            f"{label} contract FAIL: no effective terrain/material writes "
            f"(height_vertices={height_writes}, visual_cells={visual_writes})"
        )
    print(
        f"    {status('OK', f'{label} direct footprint bake {baked}/{total}; height_vertices={height_writes}; visual_cells={visual_writes}')}"
    )
    _run_log.emit(
        "direct_building_footprint_bake_summary",
        label=label,
        ok=True,
        buildings_total=total,
        buildings_baked=baked,
        height_vertices_written=height_writes,
        visual_cells_written=visual_writes,
    )
    return {"ok": True, "total": total, "baked": baked}


def _copy_markers_from_map(dst_map, src_map):
    proc = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "docs/agent/cli-anything" / "a3d_edit.py"),
         "copy-markers", dst_map, "--from", src_map],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=PROJECT_ROOT,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"marker copy failed from {src_map}")
    return proc.stdout.strip().splitlines()


def _sync_markers(dst_map, src_map, label):
    summary = _copy_markers_from_map(dst_map, src_map)
    if summary:
        print(f"  {label}: {summary[0]}")
        for line in summary[1:]:
            print(f"    {line}")


def _default_marker_source(cfg):
    for candidate in (cfg.paths.buildings_only_a3d_output, cfg.paths.terrain_only_a3d_output):
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def _read_akm_bounds(akm_path):
    with open(akm_path, "r", encoding="ascii", errors="strict") as fh:
        line = fh.readline().strip()
        if line != "ply":
            raise ValueError(f"not a PLY/AKM file: {akm_path}")

        vertex_count = None
        in_vertex = False
        vertex_props = []
        for raw in fh:
            line = raw.strip()
            if line == "end_header":
                break
            if line.startswith("element vertex "):
                vertex_count = int(line.split()[-1])
                in_vertex = True
                continue
            if line.startswith("element "):
                in_vertex = False
                continue
            if line.startswith("property ") and in_vertex:
                vertex_props.append(line.split()[-1])

        if vertex_count is None or vertex_count <= 0:
            raise ValueError(f"missing vertex header in {akm_path}")
        if not {"x", "y", "z"}.issubset(vertex_props):
            raise ValueError(f"missing xyz vertex props in {akm_path}")

        x_idx = vertex_props.index("x")
        y_idx = vertex_props.index("y")
        z_idx = vertex_props.index("z")
        xs, ys, zs = [], [], []
        for _ in range(vertex_count):
            parts = fh.readline().split()
            xs.append(float(parts[x_idx]))
            ys.append(float(parts[y_idx]))
            zs.append(float(parts[z_idx]))

    return {
        "min_x": min(xs),
        "max_x": max(xs),
        "min_y": min(ys),
        "max_y": max(ys),
        "min_z": min(zs),
        "max_z": max(zs),
    }


def _read_akm_unique_z_values(akm_path):
    with open(akm_path, "r", encoding="ascii", errors="strict") as fh:
        line = fh.readline().strip()
        if line != "ply":
            raise ValueError(f"not a PLY/AKM file: {akm_path}")

        vertex_count = None
        in_vertex = False
        vertex_props = []
        for raw in fh:
            line = raw.strip()
            if line == "end_header":
                break
            if line.startswith("element vertex "):
                vertex_count = int(line.split()[-1])
                in_vertex = True
                continue
            if line.startswith("element "):
                in_vertex = False
                continue
            if line.startswith("property ") and in_vertex:
                vertex_props.append(line.split()[-1])

        if vertex_count is None or vertex_count <= 0:
            raise ValueError(f"missing vertex header in {akm_path}")
        if "z" not in vertex_props:
            raise ValueError(f"missing z vertex prop in {akm_path}")

        z_idx = vertex_props.index("z")
        z_values = set()
        for _ in range(vertex_count):
            parts = fh.readline().split()
            if len(parts) > z_idx:
                z_values.add(round(float(parts[z_idx]), 3))
    return z_values


def _target_topology_bounds(blender_data):
    bounds = blender_data.get("content_bounds") or blender_data.get("terrain_bounds")
    if bounds:
        return bounds
    terrain_size = blender_data.get("terrain_size")
    if terrain_size:
        return {"min_x": 0.0, "min_y": 0.0, "max_x": float(terrain_size), "max_y": float(terrain_size)}
    return None


def _build_topology_instance(mesh_path, blender_data, z_scale):
    source_bounds = _read_akm_bounds(mesh_path)
    target_bounds = _target_topology_bounds(blender_data)
    if not target_bounds:
        raise RuntimeError("missing content/terrain bounds for topology alignment")

    src_w = source_bounds["max_x"] - source_bounds["min_x"]
    src_d = source_bounds["max_y"] - source_bounds["min_y"]
    dst_w = target_bounds["max_x"] - target_bounds["min_x"]
    dst_d = target_bounds["max_y"] - target_bounds["min_y"]
    if src_w <= 0 or src_d <= 0 or dst_w <= 0 or dst_d <= 0:
        raise RuntimeError("invalid topology/source bounds for alignment")

    sx = dst_w / src_w
    sy = dst_d / src_d
    src_cx = (source_bounds["min_x"] + source_bounds["max_x"]) / 2.0
    src_cy = (source_bounds["min_y"] + source_bounds["max_y"]) / 2.0
    dst_cx = (target_bounds["min_x"] + target_bounds["max_x"]) / 2.0
    dst_cy = (target_bounds["min_y"] + target_bounds["max_y"]) / 2.0
    tx = dst_cx - src_cx * sx
    ty = dst_cy - src_cy * sy
    tz = (
        float(SBU_TERRAIN_BASELINE)
        + TOPOLOGY_MIN_HEIGHT_OFFSET
        - source_bounds["min_z"] * z_scale * 16.0
    )

    return {
        "variant": "mesh",
        "mesh_name": Path(mesh_path).name,
        "inst_name": f"{Path(mesh_path).stem}_topology",
        "transform": [
            sx, 0.0, 0.0, 0.0,
            0.0, sy, 0.0, 0.0,
            0.0, 0.0, z_scale * 16.0, 0.0,
            tx, ty, tz, 1.0,
        ],
        "flags": 3,
        "story_id": -1,
    }


def _osm_project(lat, lon, scene_lat, scene_lon):
    """Delegate to canonical shared projection (osm_projection.py)."""
    return _osm_project_canonical(lat, lon, scene_lat, scene_lon)


def _spawn_xy_from_latlon(cfg, blender_data):
    par = cfg.params
    if par.spawn_x is not None and par.spawn_y is not None:
        return float(par.spawn_x), float(par.spawn_y)
    if par.spawn_lat is None or par.spawn_lon is None:
        return None
    shift = blender_data.get("terrain_shift") or {"x": 0.0, "y": 0.0}
    scene_lat = (cfg.bbox.min_lat + cfg.bbox.max_lat) / 2.0
    scene_lon = (cfg.bbox.min_lon + cfg.bbox.max_lon) / 2.0
    x, y = _osm_project(float(par.spawn_lat), float(par.spawn_lon), scene_lat, scene_lon)
    return (
        x * float(par.content_scale) + float(shift.get("x", 0.0)),
        y * float(par.content_scale) + float(shift.get("y", 0.0)),
    )


def step_baked_phase(cfg, blender_result):
    """Phase 2 for baked mode: topology bake, building bake, then restore fixtures."""
    p = cfg.paths
    par = cfg.params
    print()
    header("Step 3: ASCIIID BAKED PHASE", char="─", width=58)
    blender_data = _result_data(blender_result)
    if not os.path.isfile(p.terrain_only_a3d_output):
        raise RuntimeError(f"missing terrain-only A3D for baked mode: {p.terrain_only_a3d_output}")
    if not os.path.isfile(p.building_specs_output):
        raise RuntimeError(f"missing deferred building specs: {p.building_specs_output}")
    if not os.path.isfile(p.fixture_specs_output):
        raise RuntimeError(f"missing deferred fixture specs: {p.fixture_specs_output}")
    os.makedirs(os.path.dirname(p.a3d_output), exist_ok=True)
    shutil.copy2(p.terrain_only_a3d_output, p.prebake_a3d_output)
    pre_bake_probe_heights = _probe_terrain_heights(p.prebake_a3d_output, "pre-bake baseline")
    pre_bake_stats = _a3d_file_stats(p.prebake_a3d_output, "pre-bake baseline")

    steps_done = []
    topology_mesh = par.topology_mesh  # None when no_topology_bake; already resolved in from_args
    # Owner assertion: reject the envelope topology source regardless of how this
    # function was invoked. _validate_topology_contract catches CLI misuse at
    # argparse time; this catches programmatic callers that bypass main().
    if topology_mesh and Path(topology_mesh).name == INVALID_ENVELOPE_TOPOLOGY_MESH_NAME:
        raise RuntimeError(
            f"topology mesh {INVALID_ENVELOPE_TOPOLOGY_MESH_NAME!r} is not a terrain topology source "
            f"(FL-2573/FL-3695) — use {DEFAULT_TOPOLOGY_MESH!r} or another varied-Z AKM instead."
        )
    _run_log.emit("step", name="baked_phase_start", topology_bake=topology_mesh is not None)
    if topology_mesh:
        with _step_timer("TOPOLOGY BAKE"):
            if not os.path.isfile(topology_mesh):
                raise RuntimeError(f"missing topology mesh: {topology_mesh}")
            os.makedirs(p.meshes_dir, exist_ok=True)
            run_topology_mesh = os.path.join(p.meshes_dir, os.path.basename(topology_mesh))
            shutil.copy2(topology_mesh, run_topology_mesh)
            topology_payload = [_build_topology_instance(run_topology_mesh, blender_data, par.topology_z_scale)]
            with open(p.topology_instance_output, "w", encoding="utf-8") as fh:
                json.dump(topology_payload, fh, indent=2, sort_keys=True)
            summary = _append_instances_from_json(p.prebake_a3d_output, p.topology_instance_output)
            if summary:
                print(f"  {summary[0]}")
                for line in summary[1:]:
                    print(f"    {line}")
            # overwrite_height=0: only write terrain cells that are ABOVE baseline.
            # SBU_TERRAIN_BASELINE (128) is now on the 16-step quantization grid so
            # edge samples no longer round below baseline (FL-1181 Candidate 2 fix).
            # Keep overwrite_height=0 anyway — a topology mesh must never carve
            # terrain lower than existing raised cells from a prior bake step.
            stdout = _run_asciiid_batch(p.prebake_a3d_output, [
                "BAKE_MESH_TO_TERRAIN 1 0 0 0 0 0 70000 0",
                "DELETE_ALL_MESHES",
                f"SAVE {p.prebake_staged_output}",
            ], mesh_root=p.meshes_dir)
            print(f"  topology bake/delete/save completed -> {p.prebake_staged_output}")
            if stdout:
                for line in stdout.splitlines()[-6:]:
                    print(f"    {line}")
            _sync_markers(p.prebake_staged_output, p.terrain_only_a3d_output, "marker restore after topology bake")
            probe_heights = _probe_terrain_heights(p.prebake_staged_output, "post-topology-bake")
            _check_bake_per_instance_coverage(
                _parse_bake_coverage(stdout), "topology-bake",
                max_stuck_pct=TOPOLOGY_BAKE_MAX_STUCK_PCT,
            )
            topology_stats = _a3d_file_stats(p.prebake_staged_output, "post-topology-bake")
            low_change_ok_reason = None
            pre_height_range = int(pre_bake_stats.get("height_range", 0) or 0)
            pre_unique_heights = {h for h in pre_bake_probe_heights if h > SBU_TERRAIN_BASELINE}
            if (
                pre_height_range >= TOPOLOGY_PREEXISTING_HEIGHT_RANGE_MIN
                and len(pre_unique_heights) > 1
            ):
                low_change_ok_reason = (
                    f"pre-bake terrain already varied "
                    f"(height_range={pre_height_range}, probes={sorted(pre_unique_heights)})"
                )
            diff = _compare_terrain_heights(
                p.terrain_only_a3d_output,
                p.prebake_staged_output,
                "topology-bake",
                low_change_ok_reason=low_change_ok_reason,
            )
            if diff.get("verdict") == "FAIL":
                raise RuntimeError(
                    "Topology bake contract FAIL: 0 cells changed — bake had no effect. "
                    "Staged output discarded; prebake_a3d_output unchanged."
                )
            unique_probe_heights = {h for h in probe_heights if h > SBU_TERRAIN_BASELINE}
            global_height_range = int(topology_stats.get("height_range", 0) or 0)
            if (
                diff.get("pct", 0.0) > par.topology_bake_max_change_pct
                and len(unique_probe_heights) <= 1
                and global_height_range < 128
            ):
                raise RuntimeError(
                    f"Topology bake contract FAIL: {diff.get('pct', 0.0):.1f}% cells changed "
                    f"(limit={par.topology_bake_max_change_pct}%) with no varied probe heights and "
                    f"global height range={global_height_range} — terrain destruction detected "
                    "(FL-2573/FL-1169). Staged output discarded; prebake_a3d_output unchanged."
                )
            if diff.get("pct", 0.0) > par.topology_bake_max_change_pct and len(unique_probe_heights) <= 1:
                print(
                    f"    {status('WARN', f'TOPOLOGY high-diff probes are single-band, but global height range={global_height_range}; allowing varied terrain source')}"
                )
            # Position-level pit check (FL-1181/FL-2565): topology bake must raise all
            # known campus positions above baseline. A cell stuck at exactly SBU_TERRAIN_BASELINE
            # was NOT raised by the bake — that is a pit the player falls through, and
            # below120=0 will NOT catch it (the cell is AT 120, not below it).
            _probe_names = [
                "main quad (800,600)",
                "topology anchor (944,857)",
                "campus building zone (1100,1000)",
            ]
            flat = [(name, h) for name, h in zip(_probe_names, probe_heights) if h <= SBU_TERRAIN_BASELINE]
            if flat:
                flat_desc = ", ".join(f"{name}={h}" for name, h in flat)
                raise RuntimeError(
                    f"Topology bake position check FAIL: {len(flat)} probe(s) at or below "
                    f"baseline height={SBU_TERRAIN_BASELINE} after bake — {flat_desc} — "
                    "topology mesh did not raise these positions (FL-1181/FL-2565). "
                    "Staged output discarded; prebake_a3d_output unchanged."
                )
            shutil.copy2(p.prebake_staged_output, p.prebake_a3d_output)
            print(f"  topology bake contract OK — promoted staged -> {p.prebake_a3d_output}")
            steps_done.extend([
                "append_topology_mesh",
                "topology_bake",
                "delete_topology_mesh",
                "copy_markers_after_topology_bake",
            ])

    shutil.copy2(p.prebake_a3d_output, p.buildings_only_a3d_output)
    _sync_markers(p.buildings_only_a3d_output, p.prebake_a3d_output, "marker sync into building-footprint bake input")
    steps_done.extend(["prepare_building_footprint_bake_input", "copy_markers_into_building_footprint_input"])

    if par.stop_after_buildings_only:
        print(f"  buildings-only handoff ready -> {p.buildings_only_a3d_output}")
        return {
            "ok": True,
            "data": {
                "status": "handoff_ready",
                "steps_done": steps_done + ["stop_after_buildings_only"],
                "steps_failed": [],
                "input_map": p.prebake_a3d_output,
                "buildings_only_map": p.buildings_only_a3d_output,
                "output_map": p.buildings_only_a3d_output,
                "terrain_only_map": p.terrain_only_a3d_output,
                "topology_mesh": topology_mesh,
            },
        }

    # Building bake: after topology establishes the varied terrain floor, bake
    # OSM footprints directly into A3D terrain/material cells.  Do not append
    # building AKMs or call BAKE_MESH_TO_TERRAIN here: that path rasterizes
    # triangulated mesh roofs on CPU, so concave/noisy OSM buildings can become
    # jagged before terrain ever sees the source footprint (FL-2534/FL-1176).
    with _step_timer("BUILDING BAKE"):
        bake_summary = _run_direct_building_footprint_bake(
            p.buildings_only_a3d_output,
            p.building_specs_output,
            p.a3d_staged_output,
            p.building_bake_summary_output,
            material_id=5,
            footprint_inset=1.0,
        )
        print(f"  direct building footprint bake completed -> {p.a3d_staged_output}")
        _sync_markers(p.a3d_staged_output, p.buildings_only_a3d_output, "marker restore after direct building footprint bake")
        _probe_terrain_heights(p.a3d_staged_output, "post-building-bake")
        _check_direct_building_bake_summary(bake_summary, "building-bake")
        _a3d_file_stats(p.a3d_staged_output, "post-building-bake")
        diff = _compare_terrain_heights(p.prebake_a3d_output, p.a3d_staged_output, "building-bake")
        if diff.get("verdict") == "FAIL":
            raise RuntimeError(
                "Building bake contract FAIL: 0 cells changed — bake had no effect. "
                "Staged output discarded; a3d_output unchanged."
            )
        if 0.0 <= diff.get("pct", 100.0) < par.building_bake_min_change_pct:
            raise RuntimeError(
                f"Building bake contract FAIL: {diff.get('pct', 0.0):.1f}% cells changed "
                f"(minimum={par.building_bake_min_change_pct}%) — stale or wrong-scale AKMs detected "
                "(FL-1176). Staged output discarded; a3d_output unchanged."
            )
        shutil.copy2(p.a3d_staged_output, p.a3d_output)
        print(f"  building bake contract OK — promoted staged -> {p.a3d_output}")
        _list_asciiid_meshes(p.a3d_output, mesh_root=p.meshes_dir, label="post-building-bake")
        _report_embedded_markers(p.a3d_output, "post-building-bake")
        steps_done.extend(["direct_footprint_bake", "copy_markers_after_building_bake"])

    # FL-3690: derive and embed player-start from terrain height at the run's
    # spawn coordinates. derive_player_start() runs in Blender's export_a3d.py
    # save_a3d() but is skipped for terrain-only prebake output (baked mode now
    # promotes the direct footprint bake output instead of a Blender export).
    # Probe terrain height at spawn XY and add a small runtime clearance above it.
    with _step_timer("EMBED PLAYER-START"):
        spawn_x = blender_data.get("spawn_x")
        spawn_y = blender_data.get("spawn_y")
        if par.spawn_x is not None and par.spawn_y is not None:
            spawn_x, spawn_y = float(par.spawn_x), float(par.spawn_y)
        if spawn_x is None or spawn_y is None:
            projected_spawn = _spawn_xy_from_latlon(cfg, blender_data)
            if projected_spawn is not None:
                spawn_x, spawn_y = projected_spawn
        if spawn_x is not None and spawn_y is not None:
            _embed_player_start(p.a3d_output, float(spawn_x), float(spawn_y))
            steps_done.append("embed_player_start")
        else:
            print(f"  [FL-3690] no spawn coords in blender_data — skipping player-start embed")
            steps_done.append("skip_embed_player_start")

    # FL-1143 + FL-1144: filter sentinel fixtures and probe terrain Z before append
    with _step_timer("FIXTURE FILTER + APPEND"):
        if os.path.isfile(p.fixture_specs_output):
            probe_summary = _filter_and_probe_fixtures(p.a3d_output, p.fixture_specs_output)
            steps_done.append(f"filter_probe_fixtures({probe_summary['valid']}/{probe_summary['total_input']})")

        summary = _append_instances_from_json(p.a3d_output, p.fixture_specs_output)
        if summary:
            print(f"  {summary[0]}")
            for line in summary[1:]:
                print(f"    {line}")
        _sync_markers(p.a3d_output, p.buildings_only_a3d_output, "marker restore after fixture append")
        _report_embedded_markers(p.a3d_output, "post-fixture-append")
        steps_done.extend(["append_deferred_fixtures", "copy_markers_after_fixture_append"])

    print()
    header("BAKED PHASE SUMMARY", char="─", width=58)
    print(kv([
        ("steps completed", ", ".join(steps_done)),
        ("terrain-only", style(p.terrain_only_a3d_output, "path")),
        ("prebake", style(p.prebake_a3d_output, "path")),
        ("buildings-only", style(p.buildings_only_a3d_output, "path")),
        ("final output", style(p.a3d_output, "path")),
    ], indent=1))
    return {
        "ok": True,
        "data": {
            "status": "completed",
            "steps_done": steps_done,
            "steps_failed": [],
            "input_map": p.prebake_a3d_output,
            "buildings_only_map": p.buildings_only_a3d_output,
            "output_map": p.a3d_output,
            "terrain_only_map": p.terrain_only_a3d_output,
            "topology_mesh": topology_mesh,
        },
    }


def step_append_fixtures_resume(cfg, resume_map):
    """Append deferred fixtures onto an already baked terrain-only map."""
    p = cfg.paths
    print()
    header("Step 3: RESUME FIXTURES", char="─", width=58)
    resume_path = _resolve_path(resume_map)
    if not os.path.isfile(resume_path):
        raise RuntimeError(f"missing baked resume map: {resume_path}")
    if not os.path.isfile(p.fixture_specs_output):
        raise RuntimeError(f"missing deferred fixture specs: {p.fixture_specs_output}")

    os.makedirs(os.path.dirname(p.a3d_output), exist_ok=True)
    if Path(resume_path).absolute() != Path(p.a3d_output).absolute():
        shutil.copy2(resume_path, p.a3d_output)

    marker_source = _default_marker_source(cfg)
    steps_done = ["resume_from_external_baked_map"]
    if marker_source:
        _sync_markers(p.a3d_output, marker_source, "marker restore before fixture append resume")
        steps_done.append("copy_markers_before_fixture_resume")

    # FL-1143 + FL-1144: filter sentinel fixtures and probe terrain Z before append
    probe_summary = _filter_and_probe_fixtures(p.a3d_output, p.fixture_specs_output)
    steps_done.append(f"filter_probe_fixtures({probe_summary['valid']}/{probe_summary['total_input']})")

    summary = _append_instances_from_json(p.a3d_output, p.fixture_specs_output)
    if summary:
        print(f"  {summary[0]}")
        for line in summary[1:]:
            print(f"    {line}")
    steps_done.append("append_deferred_fixtures")

    if marker_source:
        _sync_markers(p.a3d_output, marker_source, "marker restore after fixture append resume")
        steps_done.append("copy_markers_after_fixture_resume")
    _report_embedded_markers(p.a3d_output, "resume-output")

    return {
        "ok": True,
        "data": {
            "status": "fixtures_resumed",
            "steps_done": steps_done,
            "steps_failed": [],
            "input_map": resume_path,
            "buildings_only_map": p.buildings_only_a3d_output if os.path.isfile(p.buildings_only_a3d_output) else None,
            "output_map": p.a3d_output,
            "terrain_only_map": p.terrain_only_a3d_output if os.path.isfile(p.terrain_only_a3d_output) else None,
            "topology_mesh": None,
        },
    }


# ---------------------------------------------------------------------------
# Post-processing fixes (applied after pipeline, before render)
# ---------------------------------------------------------------------------

GAME_MAP_A3D = str(PROJECT_ROOT / "assets" / "a3d" / "game_map_y8.a3d")


def _postprocess_stage_commands(cfg, map_path):
    """Return formal postprocess stage commands for the terrain-only A3D.

    These stages must run before the baked-building phase. Running them after
    buildings are baked can overwrite terrain heights under building imprints.
    """
    if cfg is None:
        return []

    p = cfg.paths
    par = cfg.params
    commands = []
    if par.osm_material_postprocess:
        commands.append((
            "osm-material-postprocess",
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "pipeline" / "sbu_satellite_style_postprocess.py"),
                "--map", str(map_path),
                "--osm", str(p.normalized_osm_output),
                "--manifest", str(p.manifest_output),
                "--metadata", str(p.terrain_metadata_output),
            ],
        ))
    if par.osm_carto_stamp:
        cmd = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "pipeline" / "osm_carto_stamper.py"),
            "--run-root", str(p.run_root),
            "--map", str(map_path),
            "--osm", str(p.normalized_osm_output),
            "--metadata", str(p.terrain_metadata_output),
            "--out", str(map_path),
        ]
        if par.osm_carto_osm_only:
            cmd.append("--osm-only")
        if par.osm_carto_labels:
            cmd.append("--labels")
        commands.append(("osm-carto-stamp", cmd))
    if par.satellite_paint:
        cmd = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "pipeline" / "satellite_terrain_painter.py"),
            "--map", str(map_path),
            "--metadata", str(p.terrain_metadata_output),
            "--zoom", str(par.satellite_paint_zoom),
            "--max-tiles", str(par.satellite_paint_max_tiles),
        ]
        if par.satellite_paint_force:
            cmd.append("--force")
        commands.append(("satellite-paint", cmd))
    return commands


def _run_postprocess_stage(label, cmd):
    print(f"  {label}: {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), text=True, capture_output=True)
    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.stderr:
        print(proc.stderr.rstrip(), file=sys.stderr)
    if proc.returncode != 0:
        raise RuntimeError(f"{label} failed with exit code {proc.returncode}")


def step_postprocess(map_path, cfg=None):
    """Fix known pipeline output issues before rendering.

    1. Palette transplant: The Blender A3D exporter generates near-black
       material palette entries. Copy the working palette from the original
       game map so terrain/materials render with correct colors.
    2. Optional formal terrain/material stages:
       - OSM style postprocess paints roads/parking/plazas and varies ground.
       - Satellite painter refines remaining pavement/grass/water cells.
    """
    print()
    header("Step 2b: POST-PROCESS", char="─", width=58)

    # --- Palette transplant ---
    if not os.path.isfile(GAME_MAP_A3D):
        print(f"  WARNING: Game map not found for palette: {GAME_MAP_A3D}")
    elif not os.path.isfile(map_path):
        print(f"  WARNING: A3D output not found: {map_path}")
    else:
        fmt = _load_a3d_format()

        def _palette_offset(path):
            with open(path, "rb") as f:
                hdr = fmt.A3DHeader.from_file(f)
                for _ in range(hdr.num_patches):
                    fmt.A3DPatch.from_file(f)
                return f.tell()

        game_off = _palette_offset(GAME_MAP_A3D)
        osm_off = _palette_offset(map_path)
        palette_size = 131072  # 256 materials × 512 bytes

        with open(GAME_MAP_A3D, "rb") as f:
            f.seek(game_off)
            palette = f.read(palette_size)

        with open(map_path, "r+b") as f:
            f.seek(osm_off)
            f.write(palette)
        print(f"  Palette transplanted into {map_path} from game_map_y8.a3d ({palette_size:,} bytes)")

    for label, cmd in _postprocess_stage_commands(cfg, map_path):
        _run_postprocess_stage(label, cmd)


def _load_a3d_format():
    """Lazy import of the A3D format module."""
    from cli_anything.asciiid.core.minimap import _load_a3d_format
    return _load_a3d_format()


def _embed_player_start(map_path, spawn_x, spawn_y, spawn_z_elev=16.0):
    """Derive and embed player-start from terrain height at (spawn_x, spawn_y).

    [FL-3690] Mirrors export_a3d.py:derive_player_start() logic in the baked pipeline.
    Reads terrain patches from the A3D binary, samples height at spawn XY,
    then embeds a v4 player-start record just above terrain.

    Args:
        map_path:     Path to the final baked A3D output.
        spawn_x:      World X for player spawn.
        spawn_y:      World Y for player spawn.
        spawn_z_elev: Height above terrain for spawn Z. Default 16.0 avoids
                      starting the runtime in a high camera/fall band.
    """
    fmt = _load_a3d_format()
    with open(map_path, "rb") as f:
        sig = f.read(4)
        if sig != b"AS3D":
            print(f"  [FL-3690] WARN: not an A3D file: {map_path}")
            return
        header_size = struct.unpack("<I", f.read(4))[0]
        num_patches = struct.unpack("<I", f.read(4))[0]
        # Read terrain patches to find the one covering (spawn_x, spawn_y)
        f.seek(header_size)
        patches = []
        for _ in range(num_patches):
            patch = fmt.A3DPatch.from_file(f)
            patches.append(patch)

    if not patches:
        print(f"  [FL-3690] WARN: no terrain patches in {map_path}")
        return

    # Find the patch containing (spawn_x, spawn_y)
    patch_world = float(fmt.VISUAL_CELLS)
    target_patch = None
    for patch in patches:
        px0 = float(patch.x) * patch_world
        py0 = float(patch.y) * patch_world
        if px0 <= spawn_x <= px0 + patch_world and py0 <= spawn_y <= py0 + patch_world:
            target_patch = patch
            break

    if target_patch is None:
        target_patch = min(
            patches,
            key=lambda p: (
                (float(p.x) * patch_world + patch_world * 0.5 - spawn_x) ** 2 +
                (float(p.y) * patch_world + patch_world * 0.5 - spawn_y) ** 2
            ),
        )

    # Bilinear sample of the target patch
    local_x = max(0.0, min(patch_world, spawn_x - float(target_patch.x) * patch_world))
    local_y = max(0.0, min(patch_world, spawn_y - float(target_patch.y) * patch_world))
    vertex_step = patch_world / float(fmt.HEIGHT_CELLS)
    fx = local_x / vertex_step
    fy = local_y / vertex_step
    x0 = min(fmt.HEIGHT_CELLS - 1, max(0, int(fx)))
    y0 = min(fmt.HEIGHT_CELLS - 1, max(0, int(fy)))
    x1 = min(fmt.HEIGHT_CELLS, x0 + 1)
    y1 = min(fmt.HEIGHT_CELLS, y0 + 1)
    tx = max(0.0, min(1.0, fx - x0))
    ty = max(0.0, min(1.0, fy - y0))
    h00 = float(target_patch.height[y0][x0])
    h10 = float(target_patch.height[y0][x1])
    h01 = float(target_patch.height[y1][x0])
    h11 = float(target_patch.height[y1][x1])
    hx0 = h00 + (h10 - h00) * tx
    hx1 = h01 + (h11 - h01) * tx
    terrain_h = hx0 + (hx1 - hx0) * ty

    ps = fmt.A3DPlayerStart(
        pos=[float(spawn_x), float(spawn_y), terrain_h + float(spawn_z_elev)],
        yaw=0.0,
        dir=0.0,
    )

    # Append player-start using the a3d_edit module directly
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "docs" / "agent" / "cli-anything"))
    from a3d_edit import read_a3d_sections, write_a3d_sections
    pre, fv, instances, _old_ps, enemy_gens, markers = read_a3d_sections(map_path)
    out_fv = fv if fv <= -4 else -4
    write_a3d_sections(map_path, pre, out_fv, instances, ps, enemy_gens, markers)
    print(f"  [FL-3690] embedded player-start at ({ps.pos[0]:.1f}, {ps.pos[1]:.1f}, {ps.pos[2]:.1f})")


def step_report(cfg):
    """Print final summary."""
    p = cfg.paths
    print()
    header("Final Summary")
    a3d_ok = os.path.isfile(p.a3d_output)
    prebake_ok = os.path.isfile(p.prebake_a3d_output)
    buildings_only_ok = os.path.isfile(p.buildings_only_a3d_output)
    terrain_only_ok = os.path.isfile(p.terrain_only_a3d_output)
    akm_count = 0
    if os.path.isdir(p.meshes_dir):
        akm_count = len([f for f in os.listdir(p.meshes_dir) if f.endswith(".akm")])

    print(f"  {status('INFO', 'Run root:')}  {style(p.run_root, 'path')}")
    if terrain_only_ok:
        print(f"  {status('OK', 'Terrain:')}  {style(p.terrain_only_a3d_output, 'path')}")
    if prebake_ok:
        print(f"  {status('OK', 'Prebake:')}  {style(p.prebake_a3d_output, 'path')}")
    if buildings_only_ok:
        print(f"  {status('OK', 'Handoff:')}  {style(p.buildings_only_a3d_output, 'path')}")
    a3d_word = "OK" if a3d_ok else "MISSING"
    print(f"  {status(a3d_word, 'A3D:')}     {style(p.a3d_output, 'path')}")
    print(f"  {status('INFO', 'AKMs:')}     {style(akm_count, 'count')} in {os.path.basename(p.meshes_dir)}/")
    print(f"  {status('INFO', 'Manifest:')} {style(p.manifest_output, 'path')}")


def _result_data(result):
    return result.get("data", {}) if isinstance(result, dict) else {}


def _validate_topology_contract(args, parser):
    """Fail closed on removed/unverified baked topology owners."""
    if args.no_topology_bake:
        return

    mesh_path = _resolve_path(args.topology_mesh)
    mesh_name = Path(mesh_path).name
    if mesh_name == INVALID_ENVELOPE_TOPOLOGY_MESH_NAME:
        parser.error(
            f"--topology-mesh {args.topology_mesh} is not a terrain topology source; "
            "it is a two-level envelope that stamps a broad plateau. "
            f"Use {DEFAULT_TOPOLOGY_MESH!r} or another AKM with varied Z levels."
        )

    z_values = _read_akm_unique_z_values(mesh_path)
    if len(z_values) < MIN_TOPOLOGY_Z_LEVELS:
        parser.error(
            f"--topology-mesh {args.topology_mesh} has only {len(z_values)} unique Z level(s); "
            f"need at least {MIN_TOPOLOGY_Z_LEVELS} for terrain topology."
        )


def _extract_step_metric(steps_done, prefix):
    for step in steps_done or []:
        if not step.startswith(prefix + "("):
            continue
        match = re.match(rf"^{re.escape(prefix)}\(([^)]+)\)$", step)
        if not match:
            continue
        raw = match.group(1)
        if "/" in raw:
            left, right = raw.split("/", 1)
            if left.isdigit() and right.isdigit():
                return {"placed": int(left), "total": int(right)}
        if raw.isdigit():
            return int(raw)
        return raw
    return None


def _blender_probe_snapshot():
    try:
        import importlib.util

        probe_path = PROJECT_ROOT / "scripts" / "launcher_lib" / "blender_paths.py"
        spec = importlib.util.spec_from_file_location("blender_paths_probe", probe_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"failed to load blender_paths probe from {probe_path}")
        _blender_paths = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = _blender_paths
        spec.loader.exec_module(_blender_paths)

        status = _blender_paths.probe()
        return {
            "path": status.blender_path,
            "version": status.version,
            "addon_profile": status.addon_profile,
            "required_addons": list(status.required_addons),
            "addons": status.addons,
            "legacy_addons": status.legacy_addons,
            "blosm_available": status.blosm_available,
        }
    except Exception as exc:
        return {"probe_error": str(exc)}


def write_manifest(cfg, import_result=None, blender_result=None, baked_result=None):
    """Persist a per-run manifest alongside generated artifacts."""
    p = cfg.paths
    par = cfg.params
    os.makedirs(p.run_root, exist_ok=True)
    run_status = _read_run_status(cfg)
    import_data = _result_data(import_result)
    blender_data = _result_data(blender_result)
    baked_data = _result_data(baked_result)
    steps_done = blender_data.get("steps_done", [])
    steps_failed = blender_data.get("steps_failed", [])
    fixture_metric = _extract_step_metric(steps_done, "fixtures")
    if fixture_metric is None:
        fixture_metric = _extract_step_metric(steps_done, "fixtures_deferred")
    separate_metric = _extract_step_metric(steps_done, "separate")
    extrude_metric = _extract_step_metric(steps_done, "extrude")
    mesh_count = len([f for f in os.listdir(p.meshes_dir) if f.endswith(".akm")]) if os.path.isdir(p.meshes_dir) else 0
    bbox_dict = cfg.bbox.as_dict()
    phase_a3d = p.terrain_only_a3d_output if par.pipeline_mode == "baked" else p.a3d_output
    manifest = {
        "run_id": p.run_id,
        "run_root": str(p.run_root),
        "run_status": run_status,
        "source": {"type": "file", "path": par.osm_file_override} if par.osm_file_override else {"type": "overpass", "bbox": bbox_dict},
        "bbox": bbox_dict,
        "pipeline_mode": par.pipeline_mode,
        "pipeline_config": {
            "content_scale": par.content_scale,
            "building_height_mult": par.building_height_mult,
            "max_terrain_grid_segs": par.max_terrain_grid_segs,
            "topology_mesh": par.topology_mesh,
            "topology_z_scale": par.topology_z_scale,
            "road_width_mult": par.road_width_mult,
            "stop_after_buildings_only": par.stop_after_buildings_only,
            "resume_fixtures_from": par.resume_fixtures_from,
            "skip_import": par.skip_import,
            "skip_pipeline": par.skip_pipeline,
            "osm_material_postprocess": par.osm_material_postprocess,
            "osm_carto_stamp": par.osm_carto_stamp,
            "osm_carto_osm_only": par.osm_carto_osm_only,
            "osm_carto_labels": par.osm_carto_labels,
            "satellite_paint": par.satellite_paint,
            "satellite_paint_zoom": par.satellite_paint_zoom,
            "satellite_paint_max_tiles": par.satellite_paint_max_tiles,
            "satellite_paint_force": par.satellite_paint_force,
        },
        "paths": {
            "blend_file": p.blend_file,
            "meshes_dir": p.meshes_dir,
            "terrain_only_a3d_output": p.terrain_only_a3d_output if par.pipeline_mode == "baked" else None,
            "prebake_a3d_output": p.prebake_a3d_output if par.pipeline_mode == "baked" else None,
            "buildings_only_a3d_output": p.buildings_only_a3d_output if par.pipeline_mode == "baked" else None,
            "a3d_output": p.a3d_output,
            "manifest_output": p.manifest_output,
            "normalized_osm_output": p.normalized_osm_output if os.path.isfile(p.normalized_osm_output) else None,
            "building_specs_output": p.building_specs_output if par.pipeline_mode == "baked" else None,
            "fixture_specs_output": p.fixture_specs_output,
            "terrain_metadata_output": p.terrain_metadata_output if par.pipeline_mode == "baked" else None,
            "topology_instance_output": p.topology_instance_output if par.pipeline_mode == "baked" else None,
            "building_bake_summary_output": p.building_bake_summary_output if par.pipeline_mode == "baked" else None,
            "activation_env_output": p.activation_env_output,
            "run_status_output": str(_run_status_path(cfg)),
            "fixtures_dir": FIXTURES_DIR,
        },
        "artifacts": {
            "terrain_only_a3d_exists": os.path.isfile(p.terrain_only_a3d_output),
            "terrain_only_a3d_size_bytes": os.path.getsize(p.terrain_only_a3d_output) if os.path.isfile(p.terrain_only_a3d_output) else 0,
            "prebake_a3d_exists": os.path.isfile(p.prebake_a3d_output),
            "prebake_a3d_size_bytes": os.path.getsize(p.prebake_a3d_output) if os.path.isfile(p.prebake_a3d_output) else 0,
            "buildings_only_a3d_exists": os.path.isfile(p.buildings_only_a3d_output),
            "buildings_only_a3d_size_bytes": os.path.getsize(p.buildings_only_a3d_output) if os.path.isfile(p.buildings_only_a3d_output) else 0,
            "a3d_exists": os.path.isfile(p.a3d_output),
            "a3d_size_bytes": os.path.getsize(p.a3d_output) if os.path.isfile(p.a3d_output) else 0,
            "blend_exists": os.path.isfile(p.blend_file),
            "activation_env_exists": os.path.isfile(p.activation_env_output),
            "mesh_count": mesh_count,
        },
        "import": import_data,
        "pipeline": {
            "status": baked_data.get("status") or blender_data.get("status"),
            "mode": par.pipeline_mode,
            "blender_phase": {
                "status": blender_data.get("status"),
                "steps_done": steps_done,
                "steps_failed": steps_failed,
                "terrain_size": blender_data.get("terrain_size"),
                "content_bounds": blender_data.get("content_bounds"),
                "terrain_bounds": blender_data.get("terrain_bounds"),
                "terrain_shift": blender_data.get("terrain_shift"),
                "phase_a3d_output": phase_a3d,
                "exported_mesh_count": blender_data.get("total_akms", mesh_count),
            },
            "asciiid_phase": {
                "status": baked_data.get("status"),
                "steps_done": baked_data.get("steps_done", []),
                "steps_failed": baked_data.get("steps_failed", []),
            },
            "building_mesh_groups": separate_metric,
            "extrude": extrude_metric,
            "fixture_placements": fixture_metric,
        },
        "blender_probe": _blender_probe_snapshot(),
        "timestamp_local": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(p.manifest_output, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
    print(f"  Manifest: {p.manifest_output}")


def activate_run(cfg):
    """Write contained runtime activation instructions inside the run folder.

    Writes both:
      1. active_mesh_root.env — env var source for launcher/asciiid sessions
      2. .active_mesh_root pointer file — read by engine C++ ReadActiveMeshRoot()
         as fallback when ASCIICKER_ACTIVE_MESH_ROOT env var is not set.
         Located at assets/meshes/osm_runs/.active_mesh_root.
    """
    p = cfg.paths
    os.makedirs(p.run_root, exist_ok=True)
    rel_mesh_root = os.path.relpath(p.meshes_dir, PROJECT_ROOT)

    # 1. env file for launcher sessions
    with open(p.activation_env_output, "w", encoding="utf-8") as fh:
        fh.write(f"ASCIICKER_ACTIVE_MESH_ROOT={rel_mesh_root}\n")
    print(f"  Activation env: {p.activation_env_output} -> {rel_mesh_root}")

    # 2. C++ engine pointer file at assets/meshes/osm_runs/.active_mesh_root
    #    Engine prepends base_path for relative paths, so rel_mesh_root works.
    os.makedirs(os.path.dirname(ACTIVE_MESH_ROOT_POINTER), exist_ok=True)
    with open(ACTIVE_MESH_ROOT_POINTER, "w", encoding="utf-8") as fh:
        fh.write(rel_mesh_root + "\n")
    print(f"  Engine pointer: {ACTIVE_MESH_ROOT_POINTER} -> {rel_mesh_root}")


def _write_terrain_metadata_sidecar(cfg, blender_result):
    """Persist terrain bounds metadata for runtime front doors.

    Traditional OSM exports keep buildings embedded in `output.a3d`, so they
    cannot use the baked deferred-building writer that normally emits
    `terrain_metadata.json`. The launcher still needs a map-local center to
    synthesize a sane spawn instead of the legacy off-map fallback.
    """
    data = _result_data(blender_result)
    if not data:
        return

    terrain_size = data.get("terrain_size")
    terrain_bounds = data.get("terrain_bounds")
    if not terrain_bounds and terrain_size:
        terrain_bounds = {
            "min_x": 0.0,
            "min_y": 0.0,
            "max_x": float(terrain_size),
            "max_y": float(terrain_size),
        }

    content_bounds = data.get("content_bounds")
    terrain_shift = data.get("terrain_shift") or {"x": 0.0, "y": 0.0}

    if not isinstance(content_bounds, dict) and not isinstance(terrain_bounds, dict):
        return

    p = cfg.paths
    os.makedirs(os.path.dirname(p.terrain_metadata_output), exist_ok=True)
    # Embed projection origin so downstream tools (osm_to_cell.py, postprocessor)
    # can convert lat/lon ↔ world coords without needing workspace.blend.
    # Try exact blosm scene center from workspace.blend first (can differ from
    # bbox midpoint by ~90m), fall back to bbox midpoint.
    scene_lat = None
    scene_lon = None
    blend_path = cfg.paths.blend_file
    if os.path.isfile(blend_path):
        try:
            blender_bin = shutil.which("blender") or "/Applications/Blender.app/Contents/MacOS/Blender"
            result = subprocess.run(
                [blender_bin, "--background", blend_path, "--python-expr",
                 'import bpy,json;s=bpy.context.scene;print("BLOSM_LATLON="+json.dumps({"lat":s.get("lat"),"lon":s.get("lon")}))'],
                capture_output=True, text=True, timeout=30)
            for line in result.stdout.splitlines():
                if line.startswith("BLOSM_LATLON="):
                    d = json.loads(line[len("BLOSM_LATLON="):])
                    scene_lat = d.get("lat")
                    scene_lon = d.get("lon")
                    if scene_lat is not None:
                        print(f"  Scene center from blend: ({scene_lat}, {scene_lon})")
        except Exception:
            pass
    if scene_lat is None or scene_lon is None:
        scene_lat = (cfg.bbox.min_lat + cfg.bbox.max_lat) / 2.0
        scene_lon = (cfg.bbox.min_lon + cfg.bbox.max_lon) / 2.0
        print(f"  Scene center from bbox midpoint: ({scene_lat}, {scene_lon})")

    payload = {
        "content_bounds": content_bounds if isinstance(content_bounds, dict) else None,
        "terrain_bounds": terrain_bounds if isinstance(terrain_bounds, dict) else None,
        "terrain_shift": {
            "x": float(terrain_shift.get("x", 0.0)),
            "y": float(terrain_shift.get("y", 0.0)),
        },
        "terrain_size": float(terrain_size or 0.0),
        "content_scale": float(cfg.params.content_scale),
        "scene_lat": scene_lat,
        "scene_lon": scene_lon,
    }
    with open(p.terrain_metadata_output, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
    print(f"  Terrain metadata: {p.terrain_metadata_output}")


def _write_runtime_sidecars(cfg, blender_result=None):
    """Keep each OSM run self-contained for later GUI/game launches."""
    activate_run(cfg)
    _write_terrain_metadata_sidecar(cfg, blender_result)


# ---------------------------------------------------------------------------
# Promote — copy a run to the permanent named-asset folder
# ---------------------------------------------------------------------------

def cmd_promote(argv=None):
    """Copy a generated run's output to assets/meshes/osm_runs/<name>/ and print git commands.

    Usage:
        python3 scripts/sbu_e2e_run.py promote <run-root> --name <short-name>

    The target folder gets a non-generated-prefix name so it is never swept by
    the automatic cleanup that removes sbu_e2e_*/launcher_osm_* directories.
    """
    import argparse as _ap
    p = _ap.ArgumentParser(prog="sbu_e2e_run.py promote",
                           description="Promote an OSM run to the permanent named-asset folder")
    p.add_argument("run_root", help="Path to the run folder to promote")
    p.add_argument("--name", required=True,
                   help="Short name for the promoted folder (must not start with sbu_e2e_ or launcher_osm_)")
    args = p.parse_args(argv)

    src = Path(_resolve_path(args.run_root))
    name = args.name.strip()
    if not src.is_dir():
        print(f"ERROR: run_root does not exist: {src}")
        sys.exit(1)
    for pat in GENERATED_RUN_DIR_PATTERNS:
        if pat.match(name):
            print(f"ERROR: promoted name '{name}' matches auto-cleanup pattern — choose a stable name")
            sys.exit(1)

    dst = DEFAULT_RUNS_ROOT / name
    if dst.exists():
        print(f"ERROR: destination already exists: {dst}")
        print("Delete or rename it first.")
        sys.exit(1)

    print(f"  Promoting: {src}")
    print(f"       → {dst}")
    shutil.copytree(str(src), str(dst))
    print("  Copy transferred. To commit:")
    print(f"    git add assets/meshes/osm_runs/{name}/")
    print(f"    git commit -m 'chore: promote osm run {name}'")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="SBU OSM → asciiid E2E pipeline")
    parser.add_argument("--skip-import", action="store_true",
                        help="Skip blosm import (reuse existing .blend)")
    parser.add_argument("--skip-pipeline", action="store_true",
                        help="Skip pipeline phases and only write a manifest for existing run artifacts")
    parser.add_argument("--no-clean", action="store_true",
                        help="Skip cleanup step (keep old output)")
    parser.add_argument("--keep-previous-runs", type=int, default=0,
                        help="Keep N newest previous generated run folders during cleanup (default: 0)")
    parser.add_argument("--osm-file", type=str, default=None,
                        help="Use local .osm file instead of downloading from Overpass")
    parser.add_argument("--min-lat", type=float, default=None,
                        help="Override bbox minimum latitude")
    parser.add_argument("--max-lat", type=float, default=None,
                        help="Override bbox maximum latitude")
    parser.add_argument("--min-lon", type=float, default=None,
                        help="Override bbox minimum longitude")
    parser.add_argument("--max-lon", type=float, default=None,
                        help="Override bbox maximum longitude")
    parser.add_argument("--spawn-lat", type=float, default=None,
                        help="Spawn point latitude override (auto-derived from terrain centroid when omitted)")
    parser.add_argument("--spawn-lon", type=float, default=None,
                        help="Spawn point longitude override (auto-derived from terrain centroid when omitted)")
    parser.add_argument("--runs-root", type=str, default=str(DEFAULT_RUNS_ROOT),
                        help="Canonical root for per-run OSM output folders")
    parser.add_argument("--run-id", type=str, default=None,
                        help="Run identifier under --runs-root (default: timestamp)")
    parser.add_argument("--run-root", type=str, default=None,
                        help="Explicit per-run output folder (overrides --runs-root/--run-id)")
    parser.add_argument("--blend-file", type=str, default=None,
                        help="Workspace .blend path (defaults under the run folder)")
    parser.add_argument("--meshes-dir", type=str, default=None,
                        help="Building mesh output dir (defaults under the run folder)")
    parser.add_argument("--a3d-output", type=str, default=None,
                        help="A3D output path (defaults under the run folder)")
    parser.add_argument("--manifest-output", type=str, default=None,
                        help="Manifest path (defaults under the run folder)")
    parser.add_argument("--activate-run", action="store_true",
                        help="Write contained active mesh-root env file after a successful run")
    parser.add_argument("--no-activate-run", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--allow-custom-output", "--allow-repo-output", action="store_true",
                        dest="allow_custom_output",
                        help="Deprecated; OSM outputs are always required to stay inside the run folder")
    parser.add_argument("--pipeline-mode", choices=("traditional", "baked"), default="traditional",
                        help="Traditional export or baked-terrain export with mesh deletion before fixtures")
    parser.add_argument("--content-scale", type=float, default=2.25,
                        help="Scale applied to the imported OSM scene before terrain/building export. "
                             "Recommended: 2.0-2.5 for walkable gameplay maps, 4.5+ for inspection/detail. "
                             "Higher values produce larger maps (patches grow quadratically).")
    parser.add_argument("--building-height-mult", type=float, default=5.0,
                        help="Multiplier for building extrude height when source meshes are flat footprints")
    parser.add_argument("--max-terrain-grid-segs", type=int, default=2048,
                        help="Cap terrain grid segments per axis for high-scale OSM runs (0 = no cap)")
    # FL-2573/FL-3695: Terrain_envelope.akm cleared one old pit oracle by
    # stamping a broad two-level plateau, but that is not proper topology.
    # The CLI now rejects low-Z-variety meshes before the destructive bake.
    parser.add_argument("--topology-mesh", type=str, default=DEFAULT_TOPOLOGY_MESH,
                        help="Topology AKM baked into terrain before deferred buildings are appended")
    parser.add_argument("--topology-z-scale", type=float, default=DEFAULT_TOPOLOGY_Z_SCALE,
                        help="Vertical scale applied to the topology mesh before runtime terrain bake")
    parser.add_argument("--road-width-mult", type=float, default=DEFAULT_ROAD_WIDTH_MULT,
                        help="Multiply OSM road half-widths during terrain paint")
    parser.add_argument("--spawn-x", type=float, default=None,
                        help="Map-local player-start X override for baked output")
    parser.add_argument("--spawn-y", type=float, default=None,
                        help="Map-local player-start Y override for baked output")
    parser.add_argument("--osm-material-postprocess", action="store_true",
                        help="Run committed OSM terrain/material postprocessor before baked-building terrain bake")
    parser.add_argument("--osm-carto-stamp", action="store_true",
                        help="Run OSM-Carto-like vector raster stamper before baked-building terrain bake")
    parser.add_argument("--osm-carto-osm-only", action="store_true",
                        help="Let OSM-Carto stamper paint all OSM-covered ground cells, not only grass cells")
    parser.add_argument("--osm-carto-labels", action="store_true",
                        help="Generate OSM-Carto label proof PNG during the Carto stamp stage")
    parser.add_argument("--satellite-paint", action="store_true",
                        help="Run committed satellite terrain painter before baked-building terrain bake")
    parser.add_argument("--satellite-paint-zoom", type=int, default=18,
                        help="Satellite painter tile zoom (default: 18)")
    parser.add_argument("--satellite-paint-max-tiles", type=int, default=100,
                        help="Satellite painter fetch cap (default: 100)")
    parser.add_argument("--satellite-paint-force", action="store_true",
                        help="Pass --force to satellite terrain painter snow/cloud gate")
    topology_group = parser.add_mutually_exclusive_group()
    # WARNING (FL-1169/FL-1181): topology bake used to be ON by default for
    # baked mode. That left the destructive lane opt-out and kept reopening
    # hole/terrain-destruction regressions. Baked runs now default to topology
    # bake OFF until the remap formula is proven; --topology-bake is explicit.
    topology_group.add_argument("--topology-bake", dest="topology_bake", action="store_true",
                                help="Enable the topology bake lane in baked mode (explicit opt-in)")
    topology_group.add_argument("--no-topology-bake", dest="topology_bake", action="store_false",
                                help="Skip the topology bake lane in baked mode")
    parser.set_defaults(topology_bake=None)
    parser.add_argument("--stop-after-buildings-only", action="store_true",
                        help="In baked mode, stop after producing output_buildings_only.a3d without baking or appending fixtures")
    parser.add_argument("--resume-fixtures-from", type=str, default=None,
                        help="Append deferred fixtures onto an already baked terrain-only map and write the final output")
    bake_alias_group = parser.add_mutually_exclusive_group()
    bake_alias_group.add_argument("--bake-buildings", dest="bake_buildings_alias", action="store_true",
                                  default=False,
                                  help="Alias for --pipeline-mode baked (bake terrain before fixtures)")
    bake_alias_group.add_argument("--no-bake-buildings", dest="no_bake_buildings_alias", action="store_true",
                                  default=False,
                                  help="Alias for --pipeline-mode traditional (no terrain bake)")
    args = parser.parse_args()
    # Warn if content_scale default was used (changed from 12.0 → 4.5 → 2.25).
    if "--content-scale" not in sys.argv and "--content_scale" not in sys.argv:
        print("NOTE: --content-scale defaults to 2.25 (was 4.5/12.0). Pass --content-scale explicitly for old behavior.",
              file=sys.stderr)
    # Resolve --bake-buildings / --no-bake-buildings aliases.
    # These are convenience flags; --pipeline-mode always wins if explicitly set.
    _pipeline_mode_default = "traditional"
    if args.bake_buildings_alias and args.no_bake_buildings_alias:
        parser.error("--bake-buildings and --no-bake-buildings are mutually exclusive")
    if args.bake_buildings_alias:
        if args.pipeline_mode != _pipeline_mode_default:
            parser.error("--bake-buildings conflicts with explicit --pipeline-mode; use one or the other")
        args.pipeline_mode = "baked"
    elif args.no_bake_buildings_alias:
        if args.pipeline_mode != _pipeline_mode_default:
            parser.error("--no-bake-buildings conflicts with explicit --pipeline-mode; use one or the other")
        args.pipeline_mode = "traditional"
    if args.stop_after_buildings_only and args.pipeline_mode != "baked":
        parser.error("--stop-after-buildings-only requires --pipeline-mode baked (or --bake-buildings)")
    if args.resume_fixtures_from and args.pipeline_mode != "baked":
        parser.error("--resume-fixtures-from requires --pipeline-mode baked (or --bake-buildings)")
    if args.stop_after_buildings_only and args.resume_fixtures_from:
        parser.error("--stop-after-buildings-only and --resume-fixtures-from are mutually exclusive")
    if args.keep_previous_runs < 0:
        parser.error("--keep-previous-runs must be >= 0")
    if args.topology_bake is None:
        args.no_topology_bake = args.pipeline_mode == "baked"
    else:
        args.no_topology_bake = not args.topology_bake
    _validate_topology_contract(args, parser)
    cfg = RunConfig.from_args(args, parser)
    os.environ["ASCIICKER_OSM_MAX_GRID_SEGS"] = str(cfg.params.max_terrain_grid_segs)
    import_result = None
    blender_result = None
    baked_result = None
    old_excepthook = sys.excepthook

    def mark_uncaught_run_failure(exc_type, exc, tb):
        if not issubclass(exc_type, SystemExit):
            state = "interrupted" if issubclass(exc_type, KeyboardInterrupt) else "failed"
            _write_run_status_best_effort(cfg, state, step="uncaught-exception", error=exc)
        old_excepthook(exc_type, exc, tb)

    def _handle_sigterm(signum, frame):
        # FL-3899.1/FL-3899.5: Python signal handlers must not perform
        # filesystem I/O. Raise SystemExit directly so SIGTERM terminates.
        raise SystemExit(128 + int(signum))

    sys.excepthook = mark_uncaught_run_failure
    signal.signal(signal.SIGTERM, _handle_sigterm)

    header("SBU OSM → asciiid E2E Pipeline")
    p = cfg.paths
    q = cfg.params
    source = (
        "resume fixtures" if q.resume_fixtures_from
        else ("local OSM" if q.osm_file_override
              else ("existing blend" if q.skip_import
                    else "Overpass bbox"))
    )
    spawn_label = (
        f"x={q.spawn_x}  y={q.spawn_y}"
        if q.spawn_x is not None and q.spawn_y is not None
        else
        f"lat={q.spawn_lat}  lon={q.spawn_lon}"
        if q.spawn_lat is not None and q.spawn_lon is not None
        else "auto (terrain centroid)"
    )
    print(kv([
        ("mode", q.pipeline_mode),
        ("source", source),
        ("run root", style(str(p.run_root), "path")),
        ("blend", style(p.blend_file, "path")),
        ("meshes", style(p.meshes_dir, "path")),
        ("output", style(p.a3d_output, "path")),
        ("topology bake", "DISABLED" if q.no_topology_bake else f"ON ({q.topology_mesh})"),
        ("content scale", q.content_scale),
        ("road width mult", q.road_width_mult),
        ("spawn", spawn_label),
        ("mutates", "writes only inside the configured OSM run folder"),
    ], indent=1))
    def open_run_log():
        _run_log.open(str(p.run_root))
        _run_log.emit("config", mode=q.pipeline_mode,
                      content_scale=q.content_scale,
                      no_topology_bake=q.no_topology_bake,
                      stop_after_buildings_only=q.stop_after_buildings_only,
                      bbox=cfg.bbox.as_dict(),
                      run_root=str(p.run_root),
                      mesh_root=p.meshes_dir)

    step_preflight(cfg, allow_custom_output=args.allow_custom_output)

    if q.resume_fixtures_from:
        open_run_log()
        _write_run_status(cfg, "running", step="resume-fixtures")
        baked_result = step_append_fixtures_resume(cfg, q.resume_fixtures_from)
    elif not q.skip_pipeline:
        if not q.no_clean:
            step_clean(cfg)

        _write_run_status(cfg, "running", step="import")
        open_run_log()
        if not q.skip_import:
            import_result = step_import(cfg)
        else:
            if not os.path.isfile(p.blend_file):
                print(f"ERROR: --skip-import but no blend file: {p.blend_file}")
                sys.exit(1)
            print(f"\n  Reusing existing blend: {p.blend_file}")

        _write_run_status(cfg, "running", step="blender")
        blender_result = step_blender_phase(cfg)
        blender_phase_a3d = _blender_phase_output_path(p, q.pipeline_mode)
        if os.path.isfile(blender_phase_a3d):
            if q.osm_material_postprocess or q.osm_carto_stamp or q.satellite_paint:
                _write_terrain_metadata_sidecar(cfg, blender_result)
                write_manifest(cfg, import_result=import_result, blender_result=blender_result)
            _write_run_status(cfg, "running", step="postprocess")
            step_postprocess(blender_phase_a3d, cfg=cfg)
            if q.pipeline_mode == "baked":
                _write_run_status(cfg, "running", step="baked-phase")
                baked_result = step_baked_phase(cfg, blender_result)
    else:
        open_run_log()
        _write_run_status(cfg, "running", step="skip-pipeline")

    _write_run_status(cfg, "running", step="report")
    step_report(cfg)
    _write_runtime_sidecars(cfg, blender_result)
    _write_run_status(cfg, "complete", step="done")
    write_manifest(
        cfg,
        import_result=import_result,
        blender_result=blender_result,
        baked_result=baked_result,
    )

    # Post-run guidance
    print()
    if os.path.isfile(p.a3d_output):
        print(f"  {ok_item('Output ready')}: {style(p.a3d_output, 'path')}")
        print(f"  {style('next action:', 'warn')} Open in ASCIIID, swap into single-player, or verify a building:")
        print(f"    python3 scripts/launcher.py --action map-open-asciiid --map {p.a3d_output}")
        print(f"    python3 scripts/launcher.py --action map-swap-single-player --map {p.a3d_output}")
        print(f"    python3 scripts/launcher.py --action game-single-player")
        print(f"    python3 scripts/sbu_verify_building.py --run-id {p.run_id} --building <name>")
    else:
        print(f"  {fail_item('No output produced')}")
        print(f"  {style('next action:', 'fail')} Check step errors above and rerun.")

    _run_log.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "promote":
        cmd_promote(sys.argv[2:])
        sys.exit(0)
    main()
