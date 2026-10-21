#!/usr/bin/env python3
"""Numabe evidence-to-reconstruction orchestration.

Acquisition produces evidence. Canonicalization, reconstruction decisions,
representation, population, attachment, and asset contracts finish before
mechanical lowering and A3D serialization. Classic is a final consumer, never
an acquisition or planning authority. The historical Blender/ASCIIID lane is
retained only behind ``--map-build-backend asciiid_legacy``.
"""
# ==========================================================================
# WARNING: ~130 OSM FLs — RQ-013 MAP ASSETS LANE (2026-05-06)
# ==========================================================================
# CONFIRMED CURRENT STATE (2026-05-06 — historical batch smoke tests, now
# routed through the separately pinned Classic product):
#   .run/asciiid-classic --batch supports LIST_INSTANCES, PROBE_TERRAIN,
#   LOAD_MAP, and QUIT. The presenter is not a batch map-authoring backend.
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
sys.path.insert(0, str(PROJECT_ROOT))

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

# Explicit historical SBU/ASCIIID profile defaults.  The direct World IR
# backend never reads these values.
# SAC + Math/Physics bbox.
# min_lon extended west to -73.1290 (FL-1171): SAC is at ~-73.1274 which was
# outside the previous min_lon of -73.12688.
SBU_LEGACY_DEFAULT_BBOX = {
    "min_lat": 40.91377,
    "max_lat": 40.91646,
    "min_lon": -73.1290,
    "max_lon": -73.12253,
}

INVALID_ENVELOPE_TOPOLOGY_MESH_NAME = "Terrain_envelope.akm"
SBU_LEGACY_TOPOLOGY_MESH = "assets/meshes/sbu_terrain.akm"
SBU_LEGACY_TOPOLOGY_Z_SCALE = 0.10
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

ASCIIID_CLASSIC_BIN = str(PROJECT_ROOT / ".run" / "asciiid-classic")

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
    map_build_backend: str = "direct"
    runtime_product: str = "classic_cp437"
    profile: str = "world_ir"


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
            bbox = RunBbox(**SBU_LEGACY_DEFAULT_BBOX)

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
            map_build_backend=args.map_build_backend,
            runtime_product=args.runtime_product,
            profile=args.profile,
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
            stdout = _run_asciiid_classic_batch(map_path, probe_cmds)
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
        stdout = _run_asciiid_classic_batch(map_path, cmds)
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
        stdout = _run_asciiid_classic_batch(
            map_path, ["LIST_MESHES"], mesh_root=mesh_root
        )
        lines = [l.strip() for l in stdout.splitlines() if ".akm" in l.lower()]
        mesh_names = []
        for line in lines:
            parts = line.split()
            for p in parts:
                if p.lower().endswith(".akm"):
                    mesh_names.append(p)
        mesh_summary = ', '.join(mesh_names[:8])
        print(f"    {status('INFO', f'MESHES{tag} {len(mesh_names)} loaded: {mesh_summary}')}")
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
            # FL-4812: the CUDA image uses Python 3.10 (before PEP 701).
            mismatch_message = (f'DIFF{tag} patch coordinate mismatch: '
                                f'{len(missing)} missing, {len(extra)} extra')
            print(f"    {status('WARN', mismatch_message)}")
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
def _run_asciiid_classic_batch(map_path, commands, mesh_root=None):
    env = None
    if mesh_root:
        env = os.environ.copy()
        env["ASCIICKER_ACTIVE_MESH_ROOT"] = str(Path(mesh_root).resolve())
    print(f"    [asciiid-classic] map={map_path}")
    if mesh_root:
        print(
            "    [asciiid-classic] ASCIICKER_ACTIVE_MESH_ROOT="
            f"{Path(mesh_root).resolve()}"
        )
    print(
        f"    [asciiid-classic] commands={len(commands)}: "
        f"{', '.join(c.split()[0] for c in commands)}"
    )
    proc = subprocess.run(
        [ASCIIID_CLASSIC_BIN, "--batch", "--map", map_path],
        input="".join(cmd if cmd.endswith("\n") else cmd + "\n" for cmd in commands),
        capture_output=True,
        text=True,
        timeout=300,
        cwd=PROJECT_ROOT,
        env=env,
    )
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    _run_log.emit("asciiid_classic_batch",
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
            f"(FL-2573/FL-3695) — use {SBU_LEGACY_TOPOLOGY_MESH!r} or another varied-Z AKM instead."
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
            stdout = _run_asciiid_classic_batch(p.prebake_a3d_output, [
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


def _embed_player_start(map_path, spawn_x, spawn_y, spawn_z_elev=16.0, *, yaw=0.0):
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
        yaw=float(yaw),
        dir=float(yaw),
    )

    # Append player-start using the a3d_edit module directly
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "docs" / "agent" / "cli-anything"))
    from a3d_edit import read_a3d_sections, write_a3d_sections
    pre, fv, instances, _old_ps, enemy_gens, markers = read_a3d_sections(map_path)
    out_fv = fv if fv <= -4 else -4
    write_a3d_sections(map_path, pre, out_fv, instances, ps, enemy_gens, markers)
    print(f"  [FL-3690] embedded player-start at ({ps.pos[0]:.1f}, {ps.pos[1]:.1f}, {ps.pos[2]:.1f})")
    return {'position':ps.pos,'ground_raw':terrain_h,'clearance_raw':float(spawn_z_elev)}


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
            f"Use {SBU_LEGACY_TOPOLOGY_MESH!r} or another AKM with varied Z levels."
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
        "map_build_backend": par.map_build_backend,
        "runtime_product": par.runtime_product,
        "runtime_binary": ".run/game_classic" if par.runtime_product == "classic_cp437" else ".run/game",
        "profile": par.profile,
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

def _direct_aoi_path_for_base(aoi: Path, requirements: dict, run_root: Path,
                              parser: argparse.ArgumentParser) -> Path:
    """Use the AOI as given when it carries home_start and selected_station_entrance.

    Otherwise write a run-local copy adding both roles: the station from the
    AOI's own mapped station landmark, and home from requirements
    home_start_point. The source file is never modified.
    """
    doc = json.loads(aoi.read_text(encoding="utf-8"))
    features = doc.get("features", [])
    roles = {f.get("properties", {}).get("role") for f in features}
    if "home_start" in roles and "selected_station_entrance" in roles:
        return aoi
    station = next(
        (f for f in features
         if f.get("properties", {}).get("role") == "selected_station_entrance"),
        None,
    )
    if station is None:
        station = next(
            (f for f in features
             if f.get("properties", {}).get("landmark_id")
             == "numabe_station_selected_entrance"),
            None,
        )
    if station is None:
        parser.error(f"AOI has no station entrance feature: {aoi}")
    station_lonlat = station["geometry"]["coordinates"][:2]
    home_point = requirements.get("home_start_point")
    if home_point is None:
        parser.error(
            "direct requirements must declare home_start_point "
            "when the AOI lacks a home_start role"
        )
    augmented = {
        "type": "FeatureCollection",
        "features": [
            *features,
            {
                "type": "Feature",
                "properties": {
                    "role": "home_start",
                    "label": "Home 17-16",
                    "basis": home_point.get("basis", ""),
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(home_point["lon"]), float(home_point["lat"])],
                },
            },
            {
                "type": "Feature",
                "properties": {
                    "role": "selected_station_entrance",
                    "label": "Numabe Station selected entrance",
                    "basis": "AOI mapped_landmark numabe_station_selected_entrance",
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(station_lonlat[0]), float(station_lonlat[1])],
                },
            },
        ],
    }
    out = run_root / "aoi.direct.geojson"
    out.write_text(
        json.dumps(augmented, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return out


def _run_direct_world_ir_backend(args, parser):
    """Compile the direct Classic-target base and DEM through shared owners."""
    required = {
        "--osm-world-ir": args.osm_world_ir,
        "--dem-world-ir": args.dem_world_ir,
        "--aoi-geojson": args.aoi_geojson,
        "--requirements": args.requirements,
    }
    missing = [flag for flag, value in required.items() if not value]
    if missing:
        parser.error(
            "direct map backend requires " + " ".join(missing)
            + "; use --map-build-backend asciiid_legacy only for an explicit historical workflow"
        )
    if args.launch and args.launch_intermediate_review:
        parser.error('choose final acceptance launch or intermediate review')
    if (args.launch or args.launch_intermediate_review) and args.runtime_product != "classic_cp437":
        parser.error("--launch currently accepts only --runtime-product classic_cp437")

    from scripts.pipeline.apply_world_ir_dem_to_a3d import lower as lower_dem
    from scripts.pipeline.bake_osm_building_footprints import bake as bake_buildings
    from scripts.pipeline.compile_world_ir_building_specs import compile_specs as compile_building_specs
    from scripts.pipeline.compile_world_ir_classic_base import compile_base
    from scripts.pipeline.compile_facade_shell import (
        lower_anchored_world_ir_facade,
    )
    from scripts.pipeline.compile_source_palette import compile_palette
    from scripts.pipeline.compile_route_walkability import compile_route_walkability
    from scripts.pipeline.compile_scale_receipt import compile_scale_receipt
    from scripts.pipeline.compile_world_frame import write_metadata as write_world_frame
    from scripts.pipeline.world_ir import WorldIRStore

    runs_root = Path(_resolve_path(args.runs_root))
    run_root = Path(_resolve_path(args.run_root)) if args.run_root else runs_root / (
        args.run_id or time.strftime("world_e2e_%Y%m%d_%H%M%S")
    )
    run_root.mkdir(parents=True, exist_ok=True)
    osm_ir = Path(_resolve_path(args.osm_world_ir))
    dem_ir = Path(_resolve_path(args.dem_world_ir))
    aoi = Path(_resolve_path(args.aoi_geojson))
    requirements_path = Path(_resolve_path(args.requirements))
    capture_manifest_path = Path(_resolve_path(args.capture_manifest)) if args.capture_manifest else (
        requirements_path.parent / "route_capture_manifest.json"
    )
    if not capture_manifest_path.is_file():
        parser.error(
            "direct map backend requires a route capture manifest via --capture-manifest "
            f"or beside the requirements file: {capture_manifest_path}"
        )
    capture_manifest = json.loads(capture_manifest_path.read_text(encoding="utf-8"))
    if capture_manifest.get("schema") != "y92.route_capture_manifest.v1":
        parser.error(f"capture manifest has unsupported schema: {capture_manifest_path}")
    palette = Path(_resolve_path(args.palette_map))
    requirements = json.loads(requirements_path.read_text(encoding="utf-8"))
    # WIRE-01: regenerate the triage-bearing OSM World IR into the run root
    # so the direct lane never consumes a stale promoted copy. The ingest
    # carries the TRIAGE-01 triage table (groups, debt_owed, rule gaps).
    from scripts.pipeline.world_ir_ingest_osm_extract import main as run_osm_ingest
    osm_regen_path = run_root / "osm.world_ir.json"
    osm_regen_receipt_path = run_root / "osm_ingest_receipt.json"
    osm_regen_anchors_path = run_root / "osm.anchor_set.json"
    if run_osm_ingest([
        "--bundle", str(requirements_path.parent),
        "--out", str(osm_regen_path),
        "--anchors-out", str(osm_regen_anchors_path),
        "--summary", str(osm_regen_receipt_path),
        "--ir-id", "numabe_connected_osm",
        "--generated-at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "--validate",
    ]) != 0:
        raise RuntimeError("direct OSM ingest failed")
    osm_ir = osm_regen_path
    osm_ingest_receipt = json.loads(osm_regen_receipt_path.read_text(encoding="utf-8"))
    compiler_inputs = requirements.get("compiler_inputs", {})
    osm_source_value = compiler_inputs.get("osm_source")
    if not osm_source_value:
        parser.error("direct requirements must declare compiler_inputs.osm_source")
    osm_source_path = requirements_path.parent / osm_source_value
    if not osm_source_path.is_file():
        parser.error(f"direct OSM source is absent: {osm_source_path}")
    # Observation registration is part of this generation.  A population
    # candidate must not inherit carrier IDs or ray intersections from the
    # historical obsrun01 working directory: those coordinates were solved
    # against an older OSM snapshot and were the source of otherwise
    # inexplicable mass intrusions in later builds.
    observation_mapping_path = (
        PROJECT_ROOT / "artifacts/experiments/"
        "fl4813_capture_review_20260915/preserved/"
        "numabe_full_727_mapping_20260915/"
        "observation_module_mapping.jsonl")
    if not observation_mapping_path.is_file():
        parser.error(
            f"reviewed observation mapping is absent: {observation_mapping_path}")
    registration_dir = run_root / "observation_registration"
    from scripts.pipeline.register_observations import main as register_observations
    _registration_argv = sys.argv
    try:
        sys.argv = [
            "register_observations.py",
            "--ledger", str(observation_mapping_path),
            "--osm", str(osm_source_path),
            "--out-dir", str(registration_dir),
        ]
        registration_rc = register_observations()
    finally:
        sys.argv = _registration_argv
    if registration_rc != 0:
        raise RuntimeError("in-generation observation registration failed")
    registered_instances_path = registration_dir / "registered_instances.json"
    # FL-4876 ENTITY-01 outputs (written by resolve_identity_entities).
    reconciled_instances_path = registration_dir / "entity_instances.json"
    identity_entities_path = registration_dir / "identity_entities.json"
    placement_zones_path = registration_dir / "placement_zones.json"
    route_source_value = compiler_inputs.get("route_source")
    if not route_source_value:
        parser.error("direct requirements must declare compiler_inputs.route_source")
    route_source_path = requirements_path.parent / route_source_value
    if not route_source_path.is_file():
        parser.error(f"direct pedestrian route source is absent: {route_source_path}")

    # FL-4814: official road refresh and law selection are offline plan inputs
    # (rules_resolved.json feeds compile_population_plan.py); the build does not
    # re-run or gate on them.
    hero_facades = requirements.get("hero_facades", [])
    required_signs = requirements.get("required_signs", [])
    if not hero_facades or not required_signs:
        parser.error("direct requirements must declare hero_facades and required_signs")
    observation_artifact_specs = {
        "station_capture_index": ("schema", "y92.capture_source_index.v1"),
        "camera_registration": ("receipt_id", "fl4812_camera_solve"),
        "camera_sparse_reconstruction": (
            "schema", "y92.sparse_reconstruction.v1"
        ),
        "object_detections": ("schema", "y92.bulk_object_observations.v1"),
        "object_masks": ("schema", "y92.bulk_mask_observations.v1"),
        "object_tracks": ("schema", "y92.bulk_object_tracks.v1"),
        "track_triangulation": ("schema", "y92.track_triangulation_hypotheses.v1"),
        "sign_ocr": ("schema", "y92.bulk_sign_ocr.v1"),
        "template_candidates": ("schema", "y92.bulk_template_candidates.v1"),
        "station_identity_plane": ("schema", "y92.planar_track_reconstruction.v1"),
        "station_facade_reconstruction": (
            "schema", "y92.anchored_facade_reconstruction.v1"
        ),
        "station_facade_world_ir": ("schema_version", "y92.world_ir/1.0.0"),
    }
    observation_paths = requirements.get("fl4813_observation_artifacts", {})
    missing_observations = sorted(set(observation_artifact_specs) - set(observation_paths))
    if missing_observations:
        parser.error(
            "direct requirements lack FL-4813 observation artifacts: "
            + ", ".join(missing_observations)
        )
    observation_documents = {}
    resolved_observation_paths = {}
    for key, (identity_key, expected_identity) in observation_artifact_specs.items():
        path = requirements_path.parent / observation_paths[key]
        if not path.is_file():
            parser.error(f"FL-4813 observation artifact is absent: {path}")
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get(identity_key) != expected_identity:
            parser.error(
                f"FL-4813 artifact {path} has {identity_key}="
                f"{document.get(identity_key)!r}; expected {expected_identity!r}"
            )
        observation_documents[key] = document
        resolved_observation_paths[key] = path.resolve()


    # FL-4813: retain every configured image corpus in World IR as evidence.
    # These counts never satisfy canonical facade, sign or physical-content gates.
    from scripts.pipeline.world_ir_ingest_image_observations import ingest_configured_corpora
    image_corpus_receipts = ingest_configured_corpora(
        requirements_path, run_root / "image_observations",
    )
    # Operator boxes enter the direct build through the same evidence-only
    # boundary. Reviewed generic/custom routing is retained for downstream
    # localization and authoring, but cannot fabricate a World IR feature.
    capture_review_ingest_receipt = None
    capture_review_routing_ref = (
        args.capture_review_routing or compiler_inputs.get("capture_review_routing")
    )
    if capture_review_routing_ref:
        capture_review_routing_path = (
            Path(_resolve_path(capture_review_routing_ref))
            if args.capture_review_routing
            else requirements_path.parent / capture_review_routing_ref
        )
        if not capture_review_routing_path.is_file():
            parser.error(
                f"configured capture review routing is absent: {capture_review_routing_path}"
            )
        capture_review_routing = json.loads(
            capture_review_routing_path.read_text(encoding="utf-8")
        )
        if capture_review_routing.get("schema") != "y92.capture_authoring_routing.v1":
            parser.error(
                "configured capture review routing has unsupported schema: "
                f"{capture_review_routing_path}"
            )
        from scripts.pipeline.world_ir_ingest_capture_review import ingest as ingest_capture_review
        capture_review_ingest_receipt = ingest_capture_review(
            capture_review_routing_path,
            run_root / "image_observations" / "capture_review.world_ir.json",
        )

    header("Evidence → canonical reconstruction → A3D candidate")
    print(kv([
        ("map build backend", "direct"),
        ("runtime product", args.runtime_product),
        ("profile", args.profile),
        ("launch", bool(args.launch)),
        ("run root", style(str(run_root), "path")),
    ], indent=1))
    base_aoi = _direct_aoi_path_for_base(aoi, requirements, run_root, parser)
    # FL-4809 ARCH-01: compile the coordinate frame without serializing A3D.
    # Canonical reconstruction, representation, population, asset, placement,
    # and content-readiness gates must close before compile_base writes the
    # first Classic world byte.
    terrain_metadata_path = run_root / "terrain_metadata.json"
    world_frame = write_world_frame(base_aoi, terrain_metadata_path)
    output_map = Path(_resolve_path(args.a3d_output)) if args.a3d_output else run_root / "output.a3d"
    terrain_map = run_root / "terrain.a3d"
    dem_receipt_path = run_root / "dem_lowering_receipt.json"

    # WIRE-01 (a): PLATEAU CityGML ingest from the bundle tiles. Fails closed
    # naming the manifest record to re-fetch when the tiles are absent.
    from scripts.pipeline.world_ir_ingest_plateau_citygml import main as run_plateau_ingest
    plateau_tiles = sorted((requirements_path.parent / "plateau").glob("udx/bldg/*.gml"))
    if not plateau_tiles:
        parser.error(
            "PLATEAU bldg tiles absent under bundle plateau/udx/bldg/; re-fetch manifest "
            "records plateau_ota_2025_bldg_* (source plateau-13111-ota-ku-2025, "
            "https://www.geospatial.jp/ckan/dataset/plateau-13111-ota-ku-2025) "
            "into assets/source_bundles/numabe_connected_neighborhood_scope_20260915/plateau/udx/bldg/"
        )
    plateau_ir_path = run_root / "plateau.world_ir.json"
    plateau_receipt_path = run_root / "plateau_ingest_receipt.json"
    if run_plateau_ingest([
        "--plateau-dir", str(requirements_path.parent / "plateau"),
        "--aoi", str(aoi),
        "--terrain-metadata", str(run_root / "terrain_metadata.json"),
        "--osm-world-ir", str(osm_ir),
        "--out", str(plateau_ir_path),
        "--summary", str(plateau_receipt_path),
        "--validate",
    ]) != 0:
        raise RuntimeError("direct PLATEAU ingest failed")
    plateau_ingest_receipt = json.loads(plateau_receipt_path.read_text(encoding="utf-8"))
    # WIRE-01 (b): canonical entities conflate OSM + PLATEAU buildings. The
    # receipt is derived in-lane from the stage document (the stage writes
    # only --out); stage code itself is untouched.
    from scripts.pipeline.compile_canonical_entities import main as run_canonical_entities
    canonical_entities_path = run_root / "canonical_entities.json"
    canonical_entities_receipt_path = run_root / "canonical_entities_receipt.json"
    # BUILD-06: Mapillary objects fuse as third canonical evidence (MERGE-01).
    mapillary_objects_path = (
        Path(_resolve_path(args.mapillary_objects_ir))
        if args.mapillary_objects_ir
        else PROJECT_ROOT / ".run/numabe_connected/observe03/mapillary_objects.world_ir.json"
    )
    if not mapillary_objects_path.is_file():
        parser.error(f"mapillary objects world IR absent: {mapillary_objects_path}")
    # FL-4932 TMG-HEIGHT-01: an optional roof-field summary adds the TMG
    # height rung and dated contradictions inside the one canonical owner.
    _lidar_roof_argv = []
    if args.lidar_roof_summary:
        _lidar_roof_path = Path(_resolve_path(args.lidar_roof_summary))
        if not _lidar_roof_path.is_file():
            parser.error(f"lidar roof summary absent: {_lidar_roof_path}")
        _lidar_roof_argv = ["--lidar-roof-summary", str(_lidar_roof_path)]
    if run_canonical_entities([
        "--osm-world-ir", str(osm_ir),
        "--plateau-world-ir", str(plateau_ir_path),
        "--match-receipt", str(plateau_receipt_path),
        "--mapillary-objects", str(mapillary_objects_path),
        "--out", str(canonical_entities_path),
        *_lidar_roof_argv,
    ]) != 0:
        raise RuntimeError("direct canonical entities build failed")
    canonical_doc = json.loads(canonical_entities_path.read_text(encoding="utf-8"))
    canonical_entities_receipt = {
        "schema": "y92.canonical_entities_receipt.v1",
        "inputs": canonical_doc["inputs"],
        "counts": canonical_doc["counts"],
    }
    canonical_entities_receipt_path.write_text(
        json.dumps(canonical_entities_receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    # WIRE-02 (a): canonical footprints rewrite triage rings from the
    # conflate claims (556 PLATEAU rings in, 83 plateau-only buildings
    # added). Every World IR consumer below reads canonical_ir_path; the
    # ingest output osm_ir remains the canonical stage input only.
    from scripts.pipeline.compile_canonical_footprints import main as run_canonical_footprints
    canonical_ir_path = run_root / "canonical.world_ir.json"
    canonical_footprints_receipt_path = run_root / "canonical_footprints_receipt.json"
    if run_canonical_footprints([
        "--osm-world-ir", str(osm_ir),
        "--plateau-world-ir", str(plateau_ir_path),
        "--canonical-entities", str(canonical_entities_path),
        "--out", str(canonical_ir_path),
        "--receipt", str(canonical_footprints_receipt_path),
        "--mapillary-objects", str(mapillary_objects_path),
        "--diff", str(run_root / "canonical_footprints_diff.geojson"),
        "--generated-at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "--validate",
    ]) != 0:
        raise RuntimeError("direct canonical footprints build failed")
    canonical_footprints_receipt = json.loads(canonical_footprints_receipt_path.read_text(encoding="utf-8"))

    # FL-4809 R1: measure importance against the route, here, because this is
    # the first line at which the canonical world IR exists on disk and the
    # last stage before anything reads it. Importance is a
    # level-of-information-need statement about each feature; without it every
    # building falls to the background default, which is how 733 buildings came
    # to be planned as terrain lattices with no vertical face to carry a
    # facade.
    #
    # route_source_path, not the literal filename the observation-debt stage
    # uses: the planner pins THAT file as its route input, and two receipts
    # describing two different routes with nothing to catch the difference is
    # the failure this pinning exists to prevent.
    from scripts.pipeline.compile_route_importance import (
        main as run_route_importance, preserve_receipt as preserve_route_importance)
    # The same primitive the producer and the plan receipt use. A second
    # hashing law in the orchestrator is the two-owners defect again, in
    # miniature.
    from scripts.pipeline.representation_plan_provenance import (
        file_sha256 as _file_sha256)
    route_importance_path = run_root / "route_importance.json"
    route_landmarks_path = requirements_path.parent / "required_landmarks.json"
    if run_route_importance([
        "--world-ir", str(canonical_ir_path),
        "--route", str(route_source_path),
        "--landmarks", str(route_landmarks_path),
        "--output", str(route_importance_path),
    ]) != 0:
        raise RuntimeError("direct route importance build failed")
    # main() already preserved it; this call is idempotent and refuses to
    # overwrite preserved bytes with different ones, so it returns the same
    # path rather than re-deriving the content address from a second copy of
    # the naming law.
    route_importance_preserved = preserve_route_importance(route_importance_path)
    route_importance_receipt = json.loads(
        route_importance_path.read_text(encoding="utf-8"))

    # An authored waiver is an approved omission. The committed document may
    # hold proposals, which discharge nothing by design, so passing it cannot
    # change a plan outcome; it can only put the approval on the record and
    # into the receipt hash.
    authored_waivers_path = (
        requirements_path.parent / "authored_representation_waivers.json")
    if not authored_waivers_path.is_file():
        authored_waivers_path = None
    # WIRE-01 (d): observation debt assignment (informational; its counts go
    # into the build receipt). Mirrors the OBSERVE-01 invocation against the
    # freshly regenerated triage inputs.
    from scripts.pipeline.compile_observation_debt import main as run_observation_debt
    debt_plan_path = Path(_resolve_path(args.population_plan)) if args.population_plan else None
    debt_instances_path = (
        debt_plan_path.parent / "registered_instances.json"
        if debt_plan_path is not None and (debt_plan_path.parent / "registered_instances.json").is_file()
        else registered_instances_path
    )
    if not debt_instances_path.is_file():
        parser.error(f"observation debt registered instances absent: {debt_instances_path}")
    debt_path = run_root / "observation_debt.json"
    debt_receipt_path = run_root / "observation_debt_receipt.json"
    if run_observation_debt([
        "--triage-receipt", str(osm_regen_receipt_path),
        "--world-ir", str(canonical_ir_path),
        "--survey", str(requirements_path.parent / "added_route_imagery_survey.json"),
        "--manifest", str(PROJECT_ROOT / "assets/source_bundles/numabe_home_to_station_20260912/route_capture_manifest.json"),
        "--route", str(requirements_path.parent / "valhalla_pedestrian_route.json"),
        "--landmarks", str(requirements_path.parent / "required_landmarks.json"),
        "--instances", str(debt_instances_path),
        "--facade-views", str(PROJECT_ROOT / "assets/source_bundles/numabe_home_to_station_20260912/mapillary_facade_views"),
        "--out", str(debt_path),
        "--receipt-out", str(debt_receipt_path),
    ]) != 0:
        raise RuntimeError("direct observation debt build failed")
    debt_receipt = json.loads(debt_receipt_path.read_text(encoding="utf-8"))
    # BUILD-06: representation plan is compiled in-lane from this run's
    # canonical + entities + debt (the lattice closes BEFORE the emitters
    # consume it). --representation-plan is an evidence-only override: it
    # runs only when its receipt proves it was compiled from this run's
    # complete spatial-generation tuple, else PlanWorldMismatch fails the
    # build naming the mismatched field (F4).
    lane_plan_dir = run_root / "plan"
    lane_plan_dir.mkdir(parents=True, exist_ok=True)
    lane_plan_path = lane_plan_dir / "representation_plan.json"
    lane_plan_receipt_path = lane_plan_dir / "representation_plan_receipt.json"
    lane_plan_by_owner = lane_plan_dir / "plan_by_owner"
    # BUILD-07: plan rules regenerate in-lane on every path (representation
    # plan, population plan, and override verify() all read the same file).
    from scripts.pipeline.compile_rules_resolved import main as run_rules_resolved
    import sys as _build06_sys
    import sys as _build07_sys
    plan_rules = lane_plan_dir / "rules_resolved.json"
    _lane_argv = _build06_sys.argv
    _build06_sys.argv = ["compile_rules_resolved.py",
        "--receipt", str(PROJECT_ROOT / "artifacts/experiments/fl4813_phase1_final_reconcile_20260916/applicable_law_selection_receipt.json"),
        "--corpus", str(PROJECT_ROOT / "assets/public_realm_corpora/jp/law_standards_corpus_index.json"),
        "--out", str(plan_rules)]
    try:
        rules_rc = run_rules_resolved()
    finally:
        _build06_sys.argv = _lane_argv
    if rules_rc != 0:
        raise RuntimeError("direct plan rules build failed")
    import os as _build06_os
    if not plan_rules.is_file():
        raise RuntimeError(
            f"plan rules missing right after regen (dir: "
            f"{sorted(_build06_os.listdir(lane_plan_dir))})")
    # JEV is an in-generation decision boundary, not a borrowed observe06
    # artifact. Deterministic routine laws resolve catalogued generic objects
    # and road markings before the backend; TypeSafe sees only the unmatched
    # remainder. Fixture-backed decisions are never accepted here.
    _current_canonical = json.loads(
        canonical_ir_path.read_text(encoding="utf-8"))
    from scripts.pipeline.jev_decisions import select_current_candidates
    _jev_candidate_rows = select_current_candidates(_current_canonical)
    # Materialize the decision denominator entirely from this generation's
    # canonical fusion.  No older observation file owns membership or ids.
    lane_jev_candidates_path = lane_plan_dir / "jev_candidates.world_ir.json"
    lane_jev_candidates_path.write_text(json.dumps({
        "schema_version": _current_canonical.get("schema_version"),
        "canonical_features": _jev_candidate_rows,
    }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not os.environ.get("TYPESAFE_API_KEY"):
        _typesafe_key_path = Path.home() / ".config" / "typesafe" / "key"
        if not _typesafe_key_path.is_file():
            parser.error(
                "live Jev key file is absent: ~/.config/typesafe/key")
        if (_typesafe_key_path.stat().st_mode & 0o077) != 0:
            parser.error(
                "live Jev key file permissions must be 600: "
                "~/.config/typesafe/key")
        _typesafe_key = _typesafe_key_path.read_text(
            encoding="utf-8").strip()
        if not _typesafe_key:
            parser.error("live Jev key file is empty: ~/.config/typesafe/key")
        os.environ["TYPESAFE_API_KEY"] = _typesafe_key
    from scripts.pipeline.jev_decisions import TypesafeBackend, run as run_jev
    rep_jev_path = lane_plan_dir / "jev_decisions.json"
    run_jev(
        lane_jev_candidates_path,
        TypesafeBackend(),
        plan_rules,
        rep_jev_path,
        backend_kind="typesafe",
    )
    _jev_document = json.loads(rep_jev_path.read_text(encoding="utf-8"))
    if (_jev_document.get("execution") or {}).get("backend") != "typesafe":
        raise RuntimeError("canonical representation planning refuses fixture Jev")
    plan_pack = PROJECT_ROOT / "assets/world_templates/japan_urban/pack.json"
    # PLANORDER-04: authoritative representation-tuple members. Requirements
    # carry no fusion-review input; the lane observation output is the
    # authority (observe05/fusion_reviews.json). Route is the resolved
    # pedestrian route source; terrain is this run's lane metadata.
    # FL-4932 FUSION-OWNER-01: that output lived only in git-ignored .run/,
    # was deleted with it, and survived solely in an external backup. The
    # frozen bundle now owns it beside authored_population_decisions.json
    # (same bytes, sha256 666f475a...), so a fresh clone can rebuild.
    fusion_reviews_path = requirements_path.parent / "fusion_reviews.json"
    if not fusion_reviews_path.is_file():
        parser.error(
            f"authoritative fusion reviews absent: {fusion_reviews_path}")
    rep_catalog_path = (PROJECT_ROOT / "artifacts/experiments/fl4813_capture_review_20260915/preserved/numabe_full_727_mapping_20260915/generic_module_catalog.json")
    rep_terrain_path = run_root / "terrain_metadata.json"
    if not rep_terrain_path.is_file():
        parser.error(f"lane terrain metadata absent: {rep_terrain_path}")
    if args.representation_plan:
        representation_plan_path = Path(_resolve_path(args.representation_plan))
        if not representation_plan_path.is_file():
            parser.error(f"representation plan is absent: {representation_plan_path}")
        override_receipt_path = representation_plan_path.parent / "representation_plan_receipt.json"
        # Assigned in the generate branch only until FL-4809 R1. Every override
        # run raised NameError at the owner-rows check below, so this path
        # could not execute at all and its provenance verification had never
        # run against a real plan.
        plan_by_owner_dir = representation_plan_path.parent / "plan_by_owner"
        if not override_receipt_path.is_file():
            raise RuntimeError(
                "PlanWorldMismatch: override representation plan has no sibling "
                f"receipt proving its provenance: {override_receipt_path}")
        from scripts.pipeline.compile_representation_plan import (
            PLANNER_VERSION as REPRESENTATION_PLANNER_VERSION,
            verify as verify_representation_plan)
        from scripts.pipeline.representation_plan_provenance import (
            PlanWorldMismatch as _PlanWorldMismatch)
        _terrain_doc = json.loads(rep_terrain_path.read_text(encoding="utf-8"))
        _canon_doc = json.loads(canonical_ir_path.read_text(encoding="utf-8"))
        _frame = _canon_doc["coordinate_frames"]["epsg32654"]
        try:
            verify_representation_plan(
                representation_plan_path, override_receipt_path,
                canonical_path=canonical_ir_path,
                entities_path=canonical_entities_path,
                terrain_metadata_path=rep_terrain_path, rules_path=plan_rules,
                catalog_path=rep_catalog_path, pack_path=plan_pack,
                route_path=route_source_path,
                fusion_reviews_path=fusion_reviews_path, jev_path=rep_jev_path,
                route_importance_path=route_importance_path,
                authored_waivers_path=authored_waivers_path,
                aoi_path=run_root / "aoi.direct.geojson",
                debt_path=debt_path, triage_receipt_path=osm_regen_receipt_path,
                osm_ir_path=osm_ir,
                world_units_per_metre=_terrain_doc.get("content_scale"),
                planner_version=REPRESENTATION_PLANNER_VERSION,
                planner_context={
                    "coordinate_frame": {"name": _frame["crs"],
                                         "units": _frame["units"]},
                    "content_scale_source": "terrain_metadata.content_scale"})
        except _PlanWorldMismatch as exc:
            raise RuntimeError(str(exc)) from exc
    else:
        from scripts.pipeline.compile_representation_plan import main as run_representation_plan
        if run_representation_plan([
            "--canonical", str(canonical_ir_path),
            "--entities", str(canonical_entities_path),
            "--debt", str(debt_path),
            "--triage-receipt", str(osm_regen_receipt_path),
            "--rules", str(plan_rules),
            "--osm-ir", str(osm_ir),
            "--module-catalog", str(rep_catalog_path),
            "--pack", str(plan_pack),
            "--route", str(route_source_path),
            "--terrain-metadata", str(rep_terrain_path),
            "--fusion-reviews", str(fusion_reviews_path),
            "--out", str(lane_plan_path),
            "--jev-decisions", str(rep_jev_path),
            "--route-importance", str(route_importance_path),
            "--receipt-out", str(lane_plan_receipt_path),
            "--owner-dir", str(lane_plan_by_owner),
            # FL-4876. The declared product scope. Without it the planner
            # promises street features the population stage will not build,
            # because population applies the ring and the planner did not:
            # 21 poles and 109 other street features on this lane.
            "--aoi", str(run_root / "aoi.direct.geojson"),
        ] + (["--authored-waivers", str(authored_waivers_path)]
             if authored_waivers_path else [])
          + (["--authored-tenancy", str(requirements_path.parent / "authored_place_tenancy.json")]
             if (requirements_path.parent / "authored_place_tenancy.json").is_file() else [])
          # FL-4876 STATION-DUP-01: reviewed per-object Mapillary decisions,
          # and the authored crossing equipment that stands in for the
          # infrastructure stage's own crossing proposal.
          + (["--authored-mapillary-decisions",
              str(requirements_path.parent / "authored_mapillary_object_decisions.json")]
             if (requirements_path.parent / "authored_mapillary_object_decisions.json").is_file() else [])
          + (["--authored-fixtures",
              str(requirements_path.parent / "authored_scene_fixtures.json")]
             if (requirements_path.parent / "authored_scene_fixtures.json").is_file() else [])) != 0:
            raise RuntimeError("direct representation plan build failed")
        representation_plan_path = lane_plan_path
        plan_by_owner_dir = lane_plan_by_owner
    print(f"  representation plan: {representation_plan_path}", flush=True)
    # BUILD-06: emitters obey the resolved plan. Owner rows load once from
    # the plan's plan_by_owner directory (in-lane plan or verified
    # override); every emitter below resolves siting through
    # canonical_attachment against this run's canonical document.
    if not plan_by_owner_dir.is_dir():
        raise RuntimeError(f"plan owner rows absent: {plan_by_owner_dir}")
    # BUILD-07: population plan is compiled in-lane from this run's
    # canonical + entities + terrain + rules (same generation rule as the
    # representation plan). --population-plan is an evidence-run override:
    # it must fail verify() when its provenance differs from the lane inputs.
    # FL-4876 ROAD-XSECTION-01. The surface stamp plan is the single owner
    # of the painted road cross-section. It needs only canonical, the
    # representation plan, rules and the optional measured corridors, so it
    # is written before population and population places road-edge laterals
    # against it (previously it was written after population, and curb
    # hardware followed a different, narrower width).
    from scripts.pipeline.compile_surface_stamp_plan import (
        write as write_surface_stamp_plan,
    )
    surface_stamp_plan_path = run_root / "plan" / "surface_stamp_plan.json"
    # FL-4934 ROAD-CORRIDOR-01: an optional measured-corridor document widens
    # eligible side roads inside the surface-plan owner.
    _road_corridors_path = None
    if args.lidar_road_corridors:
        _road_corridors_path = Path(_resolve_path(args.lidar_road_corridors))
        if not _road_corridors_path.is_file():
            parser.error(f"lidar road corridors absent: {_road_corridors_path}")
    surface_stamp_plan_path.parent.mkdir(parents=True, exist_ok=True)
    write_surface_stamp_plan(
        canonical_ir_path, representation_plan_path, plan_rules,
        surface_stamp_plan_path, _road_corridors_path)
    from scripts.pipeline.compile_population_plan import main as run_population_plan
    from scripts.pipeline.plan_spatial_provenance import verify as _verify_pop_plan
    lane_pop_dir = lane_plan_dir / "population"
    lane_pop_dir.mkdir(parents=True, exist_ok=True)
    lane_pop_path = lane_pop_dir / "population_plan.json"
    population_scale = float(world_frame["content_scale"])
    _authored_spatial_decisions_path = (
        Path(args.aoi_geojson).resolve().parent
        / "authored_spatial_coherence_decisions.json")
    if args.population_plan:
        plan_path = Path(_resolve_path(args.population_plan))
        if not plan_path.is_file():
            parser.error(f"population plan is absent: {plan_path}")
        population_plan_doc = json.loads(plan_path.read_text(encoding="utf-8"))
        try:
            _verify_pop_plan(
                population_plan_doc, canonical_ir_path,
                entities_path=canonical_entities_path,
                terrain_metadata_path=run_root / "terrain_metadata.json",
                rules_path=plan_rules,
                world_units_per_metre=population_scale)
        except Exception as exc:
            raise RuntimeError(
                "PlanWorldMismatch: override population plan failed provenance "
                f"verify against this run's inputs: {exc}") from exc
        print(f"  population plan override verified: {plan_path}", flush=True)
    else:
        # FL-4876 ENTITY-01: observations -> canonical semantic entities
        # before any representation planning. Observations never create
        # geometry; population, the station assembly and lower_plan_custom
        # read entity records only.
        from scripts.pipeline.resolve_identity_entities import write as resolve_entities
        _ents = resolve_entities(
            registered_instances_path, canonical_ir_path, registration_dir,
            representation_plan_path=representation_plan_path,
            mapping_path=observation_mapping_path,
            explicit_links_path=Path(args.aoi_geojson).resolve().parent
            / "place_front_logo_sources.v1.json",
            # FL-4876 ID-ADJ-01: per-annotation visual identity adjudication
            # (registration-stage authored owner, same file as MANUAL-PLACE-01)
            adjudication_path=Path(args.aoi_geojson).resolve().parent
            / "authored_observation_positions.json")
        print(f"  identity entities: {_ents['counts']} conservation "
              f"{_ents['conservation']}", flush=True)
        _pop_argv = _build06_sys.argv
        _build06_sys.argv = ["compile_population_plan.py",
            "--world-ir", str(canonical_ir_path),
            "--zones", str(placement_zones_path),
            "--instances", str(reconciled_instances_path),
            "--rules", str(plan_rules),
            "--catalog", str(PROJECT_ROOT / "artifacts/experiments/fl4813_capture_review_20260915/preserved/numabe_full_727_mapping_20260915/generic_module_catalog.json"),
            "--entities-path", str(canonical_entities_path),
            "--terrain-metadata-path", str(run_root / "terrain_metadata.json"),
            "--surface-stamp-plan", str(surface_stamp_plan_path),
            "--route-path", str(route_source_path),
            "--out-dir", str(lane_pop_dir)]
        # FL-4876 DECIDED-OMISSION-01. Operator decisions that settle an owed
        # placement into a decided omission, from the same frozen bundle that
        # carries the authored representation waivers.
        _authored_decisions_path = (
            Path(args.aoi_geojson).resolve().parent
            / "authored_population_decisions.json")
        if _authored_decisions_path.is_file():
            _build06_sys.argv += ["--authored-decisions",
                                  str(_authored_decisions_path)]
        # FL-4876 MANUAL-SPATIAL-01.  Exact operator decisions are bundle
        # inputs.  JEV proposals are written separately and never enter this
        # argument until a person accepts them into the authored document.
        if _authored_spatial_decisions_path.is_file():
            _build06_sys.argv += [
                "--authored-spatial-decisions",
                str(_authored_spatial_decisions_path)]
        try:
            pop_rc = run_population_plan()
        finally:
            _build06_sys.argv = _pop_argv
        if pop_rc != 0:
            raise RuntimeError("direct population plan build failed")
        plan_path = lane_pop_path
        population_plan_doc = json.loads(plan_path.read_text(encoding="utf-8"))
    import shutil as _build07_shutil
    _build07_shutil.copy2(plan_rules, lane_pop_dir / "rules_resolved.json")
    # Lower_plan_custom joins registered_instances.json as a sibling output;
    # stage the same owned source the planner consumed (identical bytes to
    # every plan_finalN copy).
    _build07_shutil.copy2(
        reconciled_instances_path if reconciled_instances_path.is_file()
        else debt_instances_path,
        lane_pop_dir / "registered_instances.json")
    # FL-4876 REPFIX-01: the representation-driven fixture placements,
    # connected.
    #
    # compile_representation_fixture_placements generates a population
    # placement for every mesh_fixture representation row whose template and
    # attachment the plan already resolved. Population builds its own
    # placements from registered_instances.json alone, which carries no
    # feature ids, so without this stage 425 primary mesh rows reach the gate
    # promised and unplaceable -- the largest single gap between the plan and
    # the world.
    #
    # It was written, measured through a hand-assembled chain, and never
    # called here. That is the same defect this file just repaired for
    # place_fronts, committed by me one stage earlier: an emitter that exists,
    # is tested, and that the production runner does not invoke. The first
    # orchestrated run after those repairs is what surfaced it -- the chain
    # measured 49 blockers and the runner measured 387, and the entire
    # difference was this stage plus what depends on it.
    from scripts.pipeline.compile_representation_fixture_placements import (
        build as build_representation_placements,
        load_templates as load_placement_templates,
        merge_into_population_plan as merge_representation_placements,
    )
    _repfix_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    _repfix_canonical = json.loads(canonical_ir_path.read_text(encoding="utf-8"))
    _repfix_placements, _repfix_receipt = build_representation_placements(
        representation_plan=json.loads(
            representation_plan_path.read_text(encoding="utf-8")),
        canonical=_repfix_canonical,
        templates=load_placement_templates(
            PROJECT_ROOT / "assets" / "world_templates"),
    )
    merge_representation_placements(_repfix_plan, _repfix_placements)
    # FL-4876 ROADSIDE-CLEAR-01: no standing object on the painted
    # carriageway. Every road-attached fixture or custom placement is
    # resolved as dispatch will resolve it and tested against the painted
    # surface union; one off the asphalt is left alone, one on it is moved to
    # the nearest legal kerbside site or kept as an explicit terminal debt.
    # Replaces ROADSIDE-SNAP-01's per-carrier median edge, which left 67
    # fixtures on the carriageway in build_reg_22.
    from scripts.pipeline.roadside_clearance import clear as clear_roadside
    from scripts.pipeline.world_surfaces import load_surfaces as _load_surfaces
    _clear_surf = _load_surfaces(
        placement_zones_path, canonical_ir_path, plan_rules, None,
        painted_plan_path=surface_stamp_plan_path)
    # FL-4876 RAIL-CLEAR-02: the train asset's width and the bundle's LiDAR
    # rail-corridor edges set the rail envelope.
    _train_manifest = PROJECT_ROOT / "assets/meshes/tokyu/tokyu_7000_3car.json"
    _vehicle_w = (json.loads(_train_manifest.read_text(encoding="utf-8"))
                  .get("dimensions_m", {}).get("width") if _train_manifest.is_file() else None)
    _rail_edges = Path(args.aoi_geojson).resolve().parent / "rail_corridor_edges.v1.json"
    _clear_receipt = clear_roadside(
        _repfix_plan, _repfix_canonical, _clear_surf,
        json.loads(rep_catalog_path.read_text(encoding="utf-8")),
        vehicle_width_m=_vehicle_w,
        corridor_edges=(json.loads(_rail_edges.read_text(encoding="utf-8"))
                        if _rail_edges.is_file() else None))
    (run_root / "plan" / "roadside_clearance_receipt.json").write_text(
        json.dumps(_clear_receipt, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")
    print("  roadside clearance: {}".format(_clear_receipt["counts"]),
          flush=True)
    plan_path = lane_pop_dir / "population_plan.representation_merged.json"
    plan_path.write_text(json.dumps(_repfix_plan, sort_keys=True) + "\n",
                         encoding="utf-8")
    (run_root / "plan" / "representation_fixture_placements_receipt.json"
     ).write_text(json.dumps(_repfix_receipt, indent=1, sort_keys=True) + "\n",
                  encoding="utf-8")
    population_plan_doc = _repfix_plan
    print("  representation fixtures: generated {}; skipped {}".format(
        _repfix_receipt["counts"]["placements"],
        {k: v for k, v in _repfix_receipt["counts"].items()
         if k != "placements" and v}), flush=True)
    # One dispatch owner partitions the heterogeneous logical plan before any
    # lowerer sees it. Lowerers reject the full plan and reject another
    # owner's slice; "foreign" and "sibling" are no longer terminal states.
    from scripts.pipeline.dispatch_population_plan import write_dispatch
    population_dispatch_paths = write_dispatch(
        plan_path, rep_catalog_path, representation_plan_path, plan_rules,
        canonical_ir_path, lane_pop_dir)
    # Every semantic placement plan exists before any lowerer runs. The
    # specialized station facade consumes the individual station sightings as
    # evidence for one accepted assembly; population dispatch records those
    # sightings as explicit non-emitting terminals instead of duplicate panels.
    from scripts.pipeline.compile_station_facade_plan import (
        rail_routes_from_canonical,
        write as write_station_facade_plan,
    )
    station_facade_plan_path = run_root / "plan" / "station_facade_plan.json"
    write_station_facade_plan(
        resolved_observation_paths["station_facade_world_ir"],
        resolved_observation_paths["station_facade_reconstruction"],
        requirements_path,
        PROJECT_ROOT / "assets" / "world_templates",
        station_facade_plan_path,
        registered_instances_path=identity_entities_path,
        object_observations_path=resolved_observation_paths["object_detections"],
        capture_index_path=resolved_observation_paths["station_capture_index"],
        meshes_out=run_root / "meshes",
        content_scale=float(world_frame["content_scale"]),
        authored_spatial_decisions_path=(
            _authored_spatial_decisions_path
            if _authored_spatial_decisions_path.is_file()
            else None),
        # FL-4876 STATION-RAIL-01: the plane may not cross a rail route.
        rail_routes_en=rail_routes_from_canonical(canonical_ir_path),
    )
    # Custom evidence enrichment and physical authoring terminate before any
    # lowerer runs. The custom lowerer receives exact dimensions, colours,
    # text, pose, and frame; it does not read OCR, annotations, or crops.
    from scripts.pipeline.compile_custom_asset_plan import (
        apply_exact_contracts as apply_custom_asset_contracts,
        write as write_custom_asset_plan,
    )
    custom_asset_plan_path = run_root / "plan" / "custom_asset_plan.json"
    write_custom_asset_plan(
        population_dispatch_paths["custom"],
        lane_pop_dir / "registered_instances.json",
        PROJECT_ROOT / "artifacts/experiments/"
        "fl4813_capture_review_20260915/preserved/"
        "numabe_full_727_mapping_20260915/observation_module_mapping.jsonl",
        [
            PROJECT_ROOT / "artifacts/experiments/"
            "fl4810_aoi_street_network_boxing_20260915/annotations.json",
            PROJECT_ROOT / "artifacts/experiments/"
            "fl4813_capture_review_20260915/annotations.json",
        ],
        [
            PROJECT_ROOT / "assets/source_bundles/"
            "numabe_home_to_station_sakurazaka_20260913/"
            "mapillary_route_sign_ocr.json",
            PROJECT_ROOT / "assets/source_bundles/"
            "numabe_home_to_station_sakurazaka_20260913/"
            "mapillary_facade_sign_ocr.json",
            PROJECT_ROOT / "assets/source_bundles/"
            "numabe_home_to_station_sakurazaka_20260913/"
            "station_sign_ocr_observations.json",
        ],
        custom_asset_plan_path,
        # FL-4876 FACE-SPAN-01: the host face bounds a facade plane's width.
        canonical_path=canonical_ir_path,
        # FL-4876 LOGO-CROP-01: business sign boards take the entity's sign size
        entities_path=identity_entities_path,
    )
    apply_custom_asset_contracts(
        population_dispatch_paths["custom"], custom_asset_plan_path)
    # Associate image-derived facade colours with the canonical building hit
    # by the capture ray.  This runs after canonical footprint fusion and
    # before any facade AKM is authored; unassociated imagery is never spread
    # across buildings by a hash lottery.
    from scripts.pipeline.compile_facade_evidence_associations import (
        write as write_facade_evidence_associations,
    )
    facade_associations_path = (
        run_root / "plan" / "facade_evidence_associations.json")
    write_facade_evidence_associations(
        canonical_ir_path,
        [Path(item["world_ir_path"]) for item in image_corpus_receipts],
        facade_associations_path,
    )
    # Building specifications and appearance profiles are upstream inputs to
    # facade asset authoring.  Authoring a generic mesh and compiling the
    # source palette afterward made source-derived appearance unreachable.
    building_specs_path = run_root / "building_instances.json"
    building_specs_receipt_path = run_root / "building_specs_receipt.json"
    building_specs_receipt = compile_building_specs(
        canonical_ir_path,
        run_root / "terrain_metadata.json",
        building_specs_path,
        building_specs_receipt_path,
        locale_context=requirements.get('locale_contexts', {}).get(
            'route_default'),
        canonical_entities=canonical_entities_path,
        rules_path=plan_rules,
    )
    source_palette_path = run_root / "source_palette.json"
    source_palette = compile_palette(
        str(building_specs_path), str(osm_source_path), roles=None,
        locale_context=requirements.get('locale_contexts', {}).get(
            'route_default'),
        image_evidence_paths=[
            item['world_ir_path'] for item in image_corpus_receipts],
        facade_associations_path=str(facade_associations_path),
        authored_appearance_path=str(
            requirements_path.parent / "authored_building_appearance.json"),
    )
    source_palette_path.write_text(
        json.dumps(source_palette, ensure_ascii=False, indent=2,
                   sort_keys=True) + "\n",
        encoding="utf-8",
    )
    # Author every population mesh before the completeness gate.  Lowerers
    # receive this immutable manifest and never create AKM bytes while
    # serializing A3D instances.
    from scripts.pipeline.compile_population_asset_plan import (
        write as write_population_asset_plan,
    )
    population_asset_plan_path = (
        run_root / "plan" / "population_asset_plan.json")
    write_population_asset_plan(
        dispatch_dir=lane_pop_dir,
        custom_asset_plan_path=custom_asset_plan_path,
        source_palette_path=source_palette_path,
        meshes_out=run_root / "meshes",
        scale=float(world_frame["content_scale"]),
        out_path=population_asset_plan_path,
    )
    print(f"  population plan: {plan_path}", flush=True)
    lane_canonical_doc = json.loads(canonical_ir_path.read_text(encoding="utf-8"))
    def _load_plan_rows(name):
        doc = json.loads((plan_by_owner_dir / f"{name}.json").read_text(encoding="utf-8"))
        return doc["rows"]
    plan_fixture_rows = _load_plan_rows("fixtures")
    plan_front_rows = _load_plan_rows("place_fronts")
    plan_infrastructure_rows = _load_plan_rows("infrastructure")
    plan_stamp_rows = _load_plan_rows("stamp")
    plan_reason_rows = _load_plan_rows("bake") + _load_plan_rows("minimap")
    print(f"  plan rows: fixtures {len(plan_fixture_rows)}, fronts {len(plan_front_rows)}, "
          f"infrastructure {len(plan_infrastructure_rows)}, "
          f"stamp {len(plan_stamp_rows)}, reason {len(plan_reason_rows)}", flush=True)
    # Surface representation is fully compiled before serialization.  The
    # stamper receives exact roles, metre widths, geometry, and terminal
    # states; it does not resolve attachments or invent presentation priors.
    # FL-4876 ROAD-XSECTION-01: surface_stamp_plan_path was written before
    # population (above); the stamper and every later consumer read it.
    # ARCH-01 terminal planning seam: this gate sees no A3D and cannot infer
    # success from terrain/building bytes. It closes every primary
    # representation through a named building, surface, population, asset, or
    # terminal owner and refuses required facade/place debt before assembly.
    from scripts.pipeline.compile_preassembly_candidate import (
        write as write_preassembly_candidate,
    )
    preassembly_candidate_path = (
        run_root / "plan" / "preassembly_candidate.json")
    preassembly_candidate = write_preassembly_candidate(
        canonical_path=canonical_ir_path,
        representation_plan_path=representation_plan_path,
        dispatch_dir=lane_pop_dir,
        custom_asset_plan_path=custom_asset_plan_path,
        population_asset_plan_path=population_asset_plan_path,
        source_palette_path=source_palette_path,
        surface_stamp_plan_path=surface_stamp_plan_path,
        station_facade_plan_path=station_facade_plan_path,
        building_specs_path=building_specs_path,
        requirements_path=requirements_path,
        route_path=route_source_path,
        terrain_metadata_path=terrain_metadata_path,
        # FL-4809 R2. The gate resolves every authored_waiver claim in the
        # plan against this document instead of trusting the planner's own
        # word for it. Without this argument a run whose plan claims a waiver
        # would trip WAIVER_DOCUMENT_ABSENT: it still fails closed, but for
        # the wrong reason, and the real cross-check would never execute.
        authored_waivers_path=authored_waivers_path,
        # FL-4876 PLACE-FRONT-01. The plan-by-owner slices are what the
        # runner actually hands each non-population emitter, so the gate
        # checks the handoff against the same bytes rather than assuming it.
        plan_by_owner_dir=plan_by_owner_dir,
        out_path=preassembly_candidate_path,
    )
    # The candidate is preserved by its own writer, on both outcomes, before
    # this reports either. .run/ is gitignored, and a refusal is the case
    # where the receipt matters most, so the durable path is named here
    # rather than left for a reader to find.
    _preassembly_preserved = preassembly_candidate.get("preserved_path")
    if not preassembly_candidate["ready_for_a3d_assembly"]:
        if args.stop_after_preassembly:
            print(
                "PREASSEMBLY REFUSED (no A3D generated): "
                f"{preassembly_candidate_path}; blockers="
                f"{preassembly_candidate['counts']['blockers']}"
                f"; preserved={_preassembly_preserved}"
            )
            return 2
        raise RuntimeError(
            "pre-assembly candidate refuses A3D generation: "
            f"{preassembly_candidate_path}; blockers="
            f"{preassembly_candidate['counts']['blockers']}"
            f"; preserved={_preassembly_preserved}")

    if args.stop_after_preassembly:
        stop_receipt = {
            "schema": "asciicker.pipeline.preassembly_stop.v1",
            "stage": "preassembly_ready",
            "a3d_generated": False,
            "preassembly_candidate": str(preassembly_candidate_path.resolve()),
            "preassembly_candidate_preserved": _preassembly_preserved,
            "counts": preassembly_candidate["counts"],
        }
        stop_path = run_root / "preassembly_stop.json"
        stop_path.write_text(
            json.dumps(stop_receipt, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"PREASSEMBLY READY (no A3D generated): {stop_path}")
        return 0

    # Serialization begins only after the complete plan is ready. compile_base
    # rewrites terrain_metadata.json from the same compile_world_frame owner;
    # byte drift is a generation error, not an accepted re-projection.
    world_frame_bytes = terrain_metadata_path.read_bytes()
    base = compile_base(osm_ir, dem_ir, base_aoi, palette, run_root,
                        locale_context=requirements.get(
                            'locale_contexts', {}).get('route_default'),
                        home_label=requirements.get('home_start_label'))
    if terrain_metadata_path.read_bytes() != world_frame_bytes:
        raise RuntimeError(
            "Classic base projection differs from the accepted world frame")
    dem_receipt = lower_dem(
        run_root / "classic_base.a3d", dem_ir, run_root,
        run_root / "dem_terrain.a3d", dem_receipt_path)
    shutil.copy2(run_root / "dem_terrain.a3d", terrain_map)
    # FL-4814: the flat-base Z=144 is invalid after metric DEM lowering.
    spawn_receipt = _embed_player_start(
        terrain_map, base["spawn"]["x"], base["spawn"]["y"],
        yaw=base["spawn"]["yaw"])
    if spawn_receipt is None:
        raise RuntimeError("DEM-grounded player start could not be embedded")
    from scripts.pipeline.osm_carto_stamper import stamp_surface_plan
    surface_receipt = stamp_surface_plan(
        surface_stamp_plan_path, run_root / "terrain_metadata.json",
        terrain_map, source_palette, run_root)
    building_bake_receipt_path = run_root / "building_mass_receipt.json"
    building_bake_receipt = bake_buildings(
        terrain_map,
        building_specs_path,
        output_map,
        material_id=source_palette['role_material']['building_outline'],
        palette=source_palette,
        footprint_inset=0.0,
        material_inset=0.0,
        contain_terrain_support=True,
        collision_mesh_root=run_root / "meshes",
        # FL-4809 R8: which buildings are real geometry is the plan's
        # decision. This stage reads it; it does not re-derive it.
        representation_plan=representation_plan_path,
        # FL-4876: per-building LiDAR roof fields (roof_form_fit.py output) shape terrain roofs per
        # vertex and give mesh masses their fitted gable / hip / shed roofs; absent -> flat lids.
        roof_field_dir=(Path(_resolve_path(args.lidar_roof_field_dir))
                        if getattr(args, "lidar_roof_field_dir", None) else None),
        # FL-4876 AUTHORED-SCENE-MESH-01: carrier-less authored scenery (the temple graveyard) is emitted here.
        authored_scene_fixtures=Path(args.aoi_geojson).resolve().parent / "authored_scene_fixtures.json",
        terrain_metadata=run_root / "terrain_metadata.json",
    )
    building_bake_receipt_path.write_text(
        json.dumps(building_bake_receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if not building_bake_receipt["ok"]:
        raise RuntimeError(
            "direct building mass lowering failed for "
            f"{building_bake_receipt['buildings_failed']} building(s)"
        )
    if args.stop_after_buildings_only:
        stop_receipt = {
            "stage": "buildings_only",
            "map_build_backend": "direct",
            "runtime_product": args.runtime_product,
            "profile": args.profile,
            "buildings_baked": building_bake_receipt["buildings_baked"],
            "buildings_failed": building_bake_receipt["buildings_failed"],
            "dem_elevation_m": [
                dem_receipt["elevation"]["source_min_m"],
                dem_receipt["elevation"]["source_max_m"],
            ],
            "outputs": {
                "terrain": str(terrain_map.resolve()),
                "classic_base": str((run_root / "classic_base.a3d").resolve()),
                "building_specs": str(building_specs_receipt_path.resolve()),
                "building_mass": str(building_bake_receipt_path.resolve()),
                "dem_lowering": str(dem_receipt_path.resolve()),
            },
        }
        (run_root / "buildings_only_stop.json").write_text(
            json.dumps(stop_receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print("BUILDINGS ONLY STOP")
        return 0

    # Finalize the A3D terrain height lattice before any placed object samples
    # ground Z.  No world-assembly stage may rewrite terrain after lowering.
    route_walkability_receipt_path = run_root / "route_walkability_receipt.json"
    route_walkability_receipt = compile_route_walkability(
        terrain_map=terrain_map,
        building_map=output_map,
        route_path=route_source_path,
        terrain_metadata_path=run_root / "terrain_metadata.json",
        building_specs_path=building_specs_path,
        output_map=output_map,
        receipt_path=route_walkability_receipt_path,
    )

    # The terrain lattice is now terminal. Resolve ground support once into a
    # mechanical placement plan; lowerers are forbidden to resample A3D and
    # silently choose their own Z.
    from scripts.pipeline.compile_population_ground_plan import (
        compile_grounded_dispatch,
    )
    grounded_population_dir = run_root / "plan" / "population_grounded"
    grounded_population_paths = compile_grounded_dispatch(
        dispatch_dir=lane_pop_dir,
        terrain_metadata_path=terrain_metadata_path,
        terrain_map_path=output_map,
        out_dir=grounded_population_dir,
    )

    # Population-plan lowering is the only public-realm/facade emission path.
    population_plan_counts = {}
    population_linear_receipt = None
    population_fixtures_receipt = None
    population_custom_receipt = None
    population_plan_error = None
    # BUILD-07: plan_path resolved above (in-lane compile or verified override).
    if plan_path is not None:
        from scripts.pipeline.lower_plan_linear import (
            lower_population_plan as lower_linear,
        )
        from scripts.pipeline.lower_plan_fixtures import (
            lower_population_plan as lower_fixtures,
        )
        population_scale = float(world_frame["content_scale"])
        # FL-4809 NUMABE-BUILD-11 (LOWER-01 integration): the linear
        # lowerer consumes the representation plan as the disposition
        # owner. Pass this run's plan (in-lane, or the verified
        # --representation-plan override) and this run's rules explicitly
        # so the lowerer verifies every input it consumes (canonical +
        # rules sha against the plan receipt). An absent plan or receipt
        # is a named refusal here, never a silent default.
        _linear_plan_receipt = (
            representation_plan_path.parent / "representation_plan_receipt.json")
        if not representation_plan_path.is_file() or not _linear_plan_receipt.is_file():
            raise RuntimeError(
                "PlanWorldMismatch: representation plan for the linear lowerer "
                f"is absent: {representation_plan_path} (receipt "
                f"{_linear_plan_receipt}); stage the in-lane plan or pass a "
                "verified --representation-plan")
        population_linear_receipt = lower_linear(
            plan_path=grounded_population_paths["linear"],
            asset_plan_path=population_asset_plan_path,
            world_ir_path=canonical_ir_path,
            terrain_metadata_path=run_root / "terrain_metadata.json",
            map_path=output_map,
            meshes_out=run_root / "meshes", output_root=run_root,
            scale=population_scale,
            rules_path=plan_rules,
        )
        population_fixtures_receipt = lower_fixtures(
            plan_path=grounded_population_paths["fixtures"],
            asset_plan_path=population_asset_plan_path,
            world_ir_path=canonical_ir_path,
            terrain_metadata_path=run_root / "terrain_metadata.json",
            map_path=output_map,
            meshes_out=run_root / "meshes", output_root=run_root,
            scale=population_scale,
        )
        from scripts.pipeline.lower_plan_custom import (
            lower_population_plan as lower_custom,
        )
        # FL-4814: route-visible custom identities (stores, signs, temple and
        # individual station observations). The accepted station facade has
        # its own typed plan and specialized mechanical lowerer below.
        population_custom_receipt = lower_custom(
            plan_path=grounded_population_paths["custom"],
            asset_plan_path=custom_asset_plan_path,
            population_asset_plan_path=population_asset_plan_path,
            world_ir_path=canonical_ir_path,
            terrain_metadata_path=run_root / "terrain_metadata.json",
            map_path=output_map,
            meshes_out=run_root / "meshes", output_root=run_root,
            scale=population_scale,
        )
        population_plan_counts = {
            # Lowerer receipts are either {"counts": {...}} or a flat count dict.
            "population_linear": population_linear_receipt.get("counts", population_linear_receipt),
            "population_fixtures": population_fixtures_receipt.get("counts", population_fixtures_receipt),
            "population_custom": population_custom_receipt.get("counts", population_custom_receipt),
        }
    # PLANORDER-04: station-facade lowering is an emitter, so it runs BEFORE
    # the terminal object-metric and spatial-coherence gates; the gates scan
    # --build-dir and therefore verify lowered facade instances too. No
    # plan-owner emitter (linear, fixtures, custom, facade) may run after
    # the terminal gates. Post-gate stages below (route walkability, scale,
    # ledger, inventory, acceptance) are verifiers whose own receipts feed
    # the acceptance gate; that is their explicit equivalent verifier.
    facade_lowering_receipt_path = run_root / "station_facade_lowering_receipt.json"
    lowered_station_world_ir_path = run_root / "station_facade.lowered.world_ir.json"
    facade_lowering_receipt = lower_anchored_world_ir_facade(
        world_ir_path=resolved_observation_paths["station_facade_world_ir"],
        reconstruction_path=resolved_observation_paths["station_facade_reconstruction"],
        camera_receipt_path=resolved_observation_paths["camera_registration"],
        terrain_metadata_path=run_root / "terrain_metadata.json",
        map_path=output_map,
        meshes_out=run_root / "meshes",
        lowered_world_ir_out=lowered_station_world_ir_path,
        receipt_out=facade_lowering_receipt_path,
        station_plan_path=station_facade_plan_path,
    )
    # PLANORDER-04 handoff: the verifier inputs sibling spatial/metric
    # integrations consume from station-facade outputs.
    (run_root / "station_facade_verifier_inputs.json").write_text(
        json.dumps({
            "build_dir": str(run_root),
            "facade_lowering_receipt": str(facade_lowering_receipt_path),
            "station_facade_instances": str(
                run_root / "station_facade_instances.json"),
            "facades_emitted": facade_lowering_receipt.get(
                "counts", {}).get("facades_emitted"),
            "lowered_world_ir": str(lowered_station_world_ir_path),
            "map_path": str(output_map),
            "meshes_dir": str(run_root / "meshes"),
            "terrain_metadata": str(run_root / "terrain_metadata.json"),
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # FL-4876 PLACE-FRONT-01: the place_fronts owner stage, connected.
    # compile_representation_plan routes named commercial places to
    # owner_stage "place_fronts", whose designated emitter is
    # compile_world_ir_place_fronts.  That emitter existed, refused foreign
    # owners, and had tests -- but this runner loaded its plan slice, printed
    # the row count, and never called it, so every planned place front was a
    # promise nothing kept.  It runs here for the same reason the station
    # facade does (PLANORDER-04): it is a plan-owner emitter, so it must
    # precede the emission manifest and the terminal gates that scan the
    # build directory.
    place_front_receipt_path = run_root / "place_front_receipt.json"
    place_front_instances_path = run_root / "place_front_instances.json"
    from scripts.pipeline.compile_world_ir_fixtures import (
        compile_world_ir_place_fronts,
    )
    if plan_front_rows:
        place_front_receipt = compile_world_ir_place_fronts(
            world_ir_path=canonical_ir_path,
            terrain_metadata_path=run_root / "terrain_metadata.json",
            ground_map_path=run_root / "terrain.a3d",
            map_path=output_map,
            meshes_out=run_root / "meshes",
            receipt_out=place_front_receipt_path,
            template_root=PROJECT_ROOT / "assets" / "world_templates",
            locale_context=requirements.get("locale_contexts", {}).get(
                "route_default"),
            route_path=route_source_path,
            requirements_path=requirements_path,
            building_instances_path=building_specs_path,
            plan_rows=plan_front_rows,
            canonical_doc=lane_canonical_doc,
            instances_out=place_front_instances_path,
        )
        print("  place fronts: planned {}, emitted {}, skipped {}".format(
            len(plan_front_rows),
            place_front_receipt["counts"]["place_fronts_emitted"],
            len(place_front_receipt["plan"]["skipped_by_state"] or [])),
            flush=True)
    else:
        place_front_receipt = None
        place_front_instances_path.write_text("[]\n", encoding="utf-8")
    # FL-4876 INFRA-01: the infrastructure owner stage, connected.
    # compile_world_ir_infrastructure lowers rail infrastructure, railway
    # grade crossings, rail corridor fencing, bridge decks, watercourse
    # edges and the station platform/entrance proposals. It is complete and
    # it has a passing test (test_fl4814_station_streetscape_grammar), and
    # before this line its name appeared exactly once in the repository --
    # on its own def. Nothing had ever called it, so four resolved railway
    # crossings, the station platform and both station entrance assemblies
    # were blocked on a stage that was never going to run.
    #
    # Fourth instance of this defect in this lane after place_fronts,
    # compile_representation_fixture_placements and this one; the wiring
    # test covers each by owner stage.
    infrastructure_receipt_path = run_root / "infrastructure_receipt.json"
    from scripts.pipeline.compile_world_ir_fixtures import (
        compile_world_ir_infrastructure,
    )
    infrastructure_receipt = compile_world_ir_infrastructure(
        world_ir_path=canonical_ir_path,
        terrain_metadata_path=run_root / "terrain_metadata.json",
        ground_map_path=run_root / "terrain.a3d",
        map_path=output_map,
        meshes_out=run_root / "meshes",
        receipt_out=infrastructure_receipt_path,
        template_root=PROJECT_ROOT / "assets" / "world_templates",
        locale_context=requirements.get("locale_contexts", {}).get(
            "station_default")
        or requirements.get("locale_contexts", {}).get("route_default"),
        plan_crossing_rows=plan_infrastructure_rows,
        canonical_doc=lane_canonical_doc,
        rail_profiles_path=(Path(args.aoi_geojson).resolve().parent
                            / "tmg_pointcloud" / "rail_vertical_profiles.v1.json"),
    )
    print("  infrastructure: platforms {}, entrances {}, crossings {}, "
          "fences {}, bridges {}".format(
              infrastructure_receipt["platform_proposals"],
              infrastructure_receipt["station_entrance_proposals"],
              infrastructure_receipt["crossing_equipment_proposals"],
              infrastructure_receipt["rail_fence_proposals"],
              infrastructure_receipt["bridge_track_proposals"]), flush=True)
    # One terminal/emission denominator is compiled before any verifier.
    # Every dispatched logical placement must be emitted, intentionally
    # omitted, or carry a named lowering debt. No verifier reconstructs this
    # join from lane-specific skip counters.
    from scripts.pipeline.compile_emission_manifest import (
        main as run_emission_manifest,
    )
    emission_manifest_path = run_root / "emission_manifest.json"
    if run_emission_manifest([
        "--build-dir", str(run_root),
        "--out", str(emission_manifest_path),
    ]) != 0:
        raise RuntimeError(
            "population dispatch has missing or contradictory terminal states")
    emission_manifest = json.loads(
        emission_manifest_path.read_text(encoding="utf-8"))
    # OBJECT-METRIC (NUMABE-METRIC-01, wired BUILD-05): every emitted
    # instance is verified against its asset's physical metre contract;
    # PS1 simplification never grants distortion. Runs after the
    # lowerers so lowered instances are verified too.
    from scripts.pipeline.compile_object_metric_receipt import (
        main as run_object_metric,
    )
    object_metric_path = run_root / "object_metric.json"
    object_metric_receipt_path = run_root / "object_metric_receipt.json"
    if run_object_metric([
        "--build-dir", str(run_root),
        "--terrain-metadata", str(run_root / "terrain_metadata.json"),
        "--specs", str(building_specs_path),
        "--mass-receipt", str(building_bake_receipt_path),
        "--out", str(object_metric_path),
        "--receipt-out", str(object_metric_receipt_path),
        "--generated-at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    ]) != 0:
        raise RuntimeError("object metric receipt has FAIL rows")
    # NUMABE-SPATIAL-01: spatial coherence gate. Runs after every lowering
    # stage (population lowerers plus the station facade owner).
    # Fails the build when any route-visible object is off its declared
    # host. Never soften this gate to make a build pass: a FAIL is
    # Executed-with-named-rows, reported from the receipt.
    if plan_path is not None:
        from scripts.pipeline.compile_spatial_coherence_receipt import (
            main as run_spatial_coherence,
        )
        coherence_path = run_root / "spatial_coherence.json"
        coherence_receipt_path = run_root / "spatial_coherence_receipt.json"
        coherence_argv = [
            "--route", str(route_source_path),
            "--world-ir", str(canonical_ir_path),
            "--terrain-metadata", str(run_root / "terrain_metadata.json"),
            "--plan", str(representation_plan_path),
            "--build-dir", str(run_root),
            "--out", str(coherence_path),
            "--receipt-out", str(coherence_receipt_path),
            "--jev-input-out", str(
                run_root / "spatial_coherence_jev_input.json"),
            "--generated-at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        ]
        if _authored_spatial_decisions_path.is_file():
            coherence_argv.extend([
                "--authored-spatial-decisions",
                str(_authored_spatial_decisions_path)])
        if (plan_path.parent / "rules_resolved.json").is_file():
            coherence_argv.extend(
                ["--rules", str(plan_path.parent / "rules_resolved.json")])
        if run_spatial_coherence(coherence_argv) != 0:
            raise RuntimeError("spatial coherence has failing rows")
    else:
        print("spatial coherence skipped: no population plan")

    # FL-4815: real player-scale calibration.  The receipt cross-checks the
    # compiled corridor, DEM range, OSM anchor chords and gravity alignment
    # against independent sources; it never compares WORLD_UNITS_PER_METRE
    # to itself.  The gate below reads pass/fail from this receipt.
    scale_receipt_path = run_root / "scale_receipt.json"
    anchor_set_path = canonical_ir_path.parent / "osm.anchor_set.json"
    if not anchor_set_path.is_file():
        parser.error(f"OSM anchor set is absent: {anchor_set_path}")
    scale_receipt = compile_scale_receipt(
        route_walkability_receipt_path=route_walkability_receipt_path,
        dem_receipt_path=dem_receipt_path,
        anchor_set_path=anchor_set_path,
        terrain_metadata_path=run_root / "terrain_metadata.json",
        camera_registration_path=resolved_observation_paths["camera_registration"],
        out_path=scale_receipt_path,
    )

    osm_doc = WorldIRStore.load(canonical_ir_path)
    from scripts.pipeline.world_ir import schema as world_schema
    lowered = WorldIRStore.load(canonical_ir_path)
    # Merge only the outputs of the typed population owners. The runner does
    # not reconstruct representations from receipts and there is no parallel
    # legacy fixture/front/infrastructure world.
    _representation_ids = {
        row["representation_id"] for row in lowered.representations
    }
    _observation_ids = {
        row["observation_id"] for row in lowered.observations
    }
    for _lowered_path in (
            run_root / "lower_linear.world_ir.json",
            run_root / "lower_fixtures.world_ir.json",
            run_root / "lower_custom.world_ir.json",
            lowered_station_world_ir_path):
        _lane_world = WorldIRStore.load(_lowered_path)
        # FL-4876 MERGE-CLOSURE-01: a lane world may be self-contained. The
        # station facade lane carries its own control_enu frame and its own
        # facade_plane / sign features; copying only its observations and
        # representations left them pointing at a frame and features the
        # merged world never received, and assert_valid refused the build
        # (E_UNKNOWN_FRAME, E_DANGLING_FEATURE_REF). Carry the referential
        # closure: the frames merged records use, and the features merged
        # representations target. A frame id that already exists with a
        # different definition is a conflict, not a merge.
        _lane_feature_by_id = {
            row["feature_id"]: row for row in _lane_world.features}
        _merged_feature_ids = {row["feature_id"] for row in lowered.features}
        _new_records = []
        for _observation in _lane_world.observations:
            _oid = _observation["observation_id"]
            if _oid not in _observation_ids:
                lowered.add_observation(_observation)
                _observation_ids.add(_oid)
                _new_records.append(_observation)
        for _representation in _lane_world.representations:
            _rid = _representation["representation_id"]
            if _rid not in _representation_ids:
                _target = _representation.get("feature_id")
                if (_target not in _merged_feature_ids
                        and _target in _lane_feature_by_id):
                    lowered.add_feature(_lane_feature_by_id[_target])
                    _merged_feature_ids.add(_target)
                    _new_records.append(_lane_feature_by_id[_target])
                lowered.add_representation(_representation)
                _representation_ids.add(_rid)
        for _record in _new_records:
            _fid = (_record.get("geometry") or {}).get("frame_id")
            if not _fid or _fid not in _lane_world.frames:
                continue
            if _fid in lowered.frames:
                if lowered.frames[_fid] != _lane_world.frames[_fid]:
                    raise RuntimeError(
                        f"lane world {_lowered_path} redefines coordinate "
                        f"frame {_fid!r} differently from the merged world")
                continue
            lowered.add_frame(_lane_world.frames[_fid])
    for surface in surface_receipt['features']:
        lowered.add_representation(world_schema.generated_representation(
            representation_id='rep:surface:'+surface['feature_id'],
            feature_id=surface['feature_id'], target='a3d_terrain_material',
            product={'material_id':source_palette['role_material'][surface['role']],
                     'cell_owner_index':str((run_root/'world_ir.carto_cells.bin').resolve())},
            lowering={'compiler':'scripts/pipeline/osm_carto_stamper.py',
                      'role':surface['role'],'width_m':surface['width_m'],
                      'width_provenance':surface['width_provenance'],
                      'generated_components':surface.get('generated_components',[])},
            authority_claim='presentation_only'))
    for mass in building_bake_receipt['per_building']:
        row=mass.get('collision_instance')
        if row:
            lowered.add_representation(world_schema.generated_representation(
                representation_id='rep:'+row['inst_name'],feature_id=row['feature_id'],target='mesh_asset',
                product={'mesh':row['mesh_name'],'instance':row['inst_name'],'transform':row['transform']},
                lowering={'compiler':'scripts/pipeline/bake_osm_building_footprints.py',
                          'reason':'footprint too small for contained terrain support'},
                authority_claim='collision'))
    lowered.assert_valid()
    lowered.save(run_root / 'macro.lowered.world_ir.json')
    # WIRE-02 (b): feature ledger joins every stage truth per feature. Last
    # stage before the build receipt; reads this build's own receipts.
    from scripts.pipeline.compile_feature_ledger import main as run_feature_ledger
    ledger_path = run_root / "feature_ledger.json"
    ledger_receipt_path = run_root / "feature_ledger_receipt.json"
    if run_feature_ledger([
        "--triage-receipt", str(osm_regen_receipt_path),
        "--world-ir", str(canonical_ir_path),
        "--entities", str(canonical_entities_path),
        "--debt", str(debt_path),
        "--route", str(route_source_path),
        "--build-dir", str(run_root),
        "--out", str(ledger_path),
        "--receipt-out", str(ledger_receipt_path),
        "--generated-at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    ]) != 0:
        raise RuntimeError("direct feature ledger build failed")
    ledger_doc = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger_receipt = json.loads(ledger_receipt_path.read_text(encoding="utf-8"))
    # NUMABE-INVENTORY-01 (POPPLAN-01 wiring): route streetscape inventory
    # is the last stage. It fails the build when a route-visible observed
    # feature vanishes without a decision. The tool writes its outputs
    # before its exit code is read, so the receipt exists in the build
    # dir regardless of the exit; a non-zero exit raises here, after
    # every other output has been written. Never soften this gate to
    # make a build pass: a vanished failure is Executed-with-named-
    # vanished, reported from the receipt.
    from scripts.pipeline.compile_route_streetscape_inventory import (
        main as run_streetscape_inventory,
    )
    inventory_path = run_root / "route_streetscape_inventory.json"
    inventory_receipt_path = run_root / "route_streetscape_inventory_receipt.json"
    inventory_rules = (plan_path.parent / "rules_resolved.json"
                       if plan_path is not None else None)
    inventory_argv = [
        "--route", str(route_source_path),
        "--world-ir", str(canonical_ir_path),
        "--plan", str(representation_plan_path),
        "--build-dir", str(run_root),
        "--debt", str(debt_path),
        "--entities", str(canonical_entities_path),
        "--out", str(inventory_path),
        "--receipt-out", str(inventory_receipt_path),
        "--png-out", str(run_root / "route_represented_ratio.png"),
        "--generated-at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    ]
    if inventory_rules is not None and inventory_rules.is_file():
        inventory_argv.extend(["--rules", str(inventory_rules)])
    inventory_receipt = None
    if run_streetscape_inventory(inventory_argv) != 0:
        if inventory_receipt_path.is_file():
            inventory_receipt = json.loads(
                inventory_receipt_path.read_text(encoding="utf-8"))
        raise RuntimeError("route streetscape inventory has vanished features")
    inventory_receipt = json.loads(
        inventory_receipt_path.read_text(encoding="utf-8"))
    # ACCEPTANCE CHAIN (NUMABE-COVERAGE-01, wired BUILD-08): per route-visible
    # feature the section-15 chain to emitted, joined to the lane's
    # inventory/spatial/metric receipts. Fail rows fail the build; omitted
    # and not-emitted-in-build are named states, not fails.
    from scripts.pipeline.compile_acceptance_chain import main as run_acceptance_chain
    chain_path = run_root / "acceptance_chain.json"
    chain_text_path = run_root / "acceptance_chain.txt"
    if run_acceptance_chain([
        "--build-dir", str(run_root),
        "--route", str(route_source_path),
        "--out", str(chain_path),
        "--text-out", str(chain_text_path),
        "--generated-at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    ]) != 0:
        chain_summary = json.loads(chain_path.read_text(encoding="utf-8"))
        raise RuntimeError(
            "acceptance chain has FAIL rows: %s" % json.dumps(
                {k: v for k, v in chain_summary.get("by_group", {}).items()}, ensure_ascii=False)[:800])
    # FL-4876 SIGN-XP-02.  Shop logos ship with the map as XP sprites made
    # from the capture-review crops (brand place fronts take one bound crop
    # on their fascia band); the sprites live in <map>.sprites/, which the
    # Classic client and server register before the map loads.  Compiled
    # before the requirements gate, which reads sign_sprites_receipt.json
    # (GATE-OWNERS-01).
    _sign_sprites_receipt = run_root / "sign_sprites_receipt.json"
    if args.runtime_product == "classic_cp437":
        from scripts.pipeline.compile_sign_sprites import main as compile_sign_sprites
        if compile_sign_sprites([
                "--run-root", str(run_root),
                "--map", str(output_map),
                "--logo-bindings", str(Path(args.aoi_geojson).resolve().parent
                                       / "place_front_logo_sources.v1.json")]) != 0:
            raise RuntimeError("Classic sign sprite compilation failed")
    # FL-4876 ENTITY-GATE-01: every identity-bearing output belongs to one
    # resolved canonical entity on its host; refuse the build otherwise.
    from scripts.pipeline.check_identity_invariants import main as check_identity_invariants
    if check_identity_invariants([str(run_root)]) != 0:
        raise RuntimeError("identity invariant gate refused the build: "
                           + str(run_root / "identity_invariant_gate.json"))
    # FL-4809 FRONT-01: the requirements owner supplies the final verdict.
    # Keep failed artifacts inspectable, but never publish BUILD COMPLETE
    # while a required row is unmet or not evaluable.
    from scripts.pipeline import compile_requirements_gate
    gate_document = compile_requirements_gate.run(
        requirements=requirements_path, build_dir=run_root,
        plan=representation_plan_path)
    gate_path = run_root / f"{run_root.name}_gate.json"
    gate_receipt_path = run_root / f"{run_root.name}_gate_receipt.json"
    gate_path.write_text(
        json.dumps(gate_document, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")
    gate_receipt_path.write_text(json.dumps({
        "schema": "y92.requirements_gate_receipt.v1",
        "inputs": {"requirements": str(requirements_path),
                   "build_dir": str(run_root),
                   "plan": str(representation_plan_path)},
        "counts": gate_document["counts"],
    }, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    if (gate_document["counts"]["suspicious_empty"]
            or any(row["state"].split(":")[0] != "met"
                   for row in gate_document["rows"])):
        raise RuntimeError(
            f"requirements gate refuses build: {gate_path}; "
            f"counts={gate_document['counts']}")
    building_count = sum(
        feature.get("semantic_class") == "building_footprint" for feature in osm_doc.features
    )
    _physical_emissions = [
        row for row in emission_manifest["emissions"]
        if row.get("record_kind") == "emitted_instance"
    ]
    _population_emissions = [
        row for row in _physical_emissions
        if row.get("owner") in {"linear", "fixtures", "custom", "station_facade"}
    ]
    _fixture_emissions = [
        row for row in _population_emissions if row.get("owner") == "fixtures"
    ]
    _front_emissions = [
        row for row in _fixture_emissions
        if row.get("mount_kind") == "building_face_panel"
    ]
    # FL-4876. These are two different things and were reported as one.
    # _front_emissions are fixture-owner facade panels; the place_fronts
    # owner stage emits its own instances, which never appeared in any count
    # because the runner never invoked its lowerer.
    _place_front_emissions = [
        row for row in _physical_emissions if row.get("owner") == "place_front"
    ]
    # FL-4876 TRAIN-ROUTE-01. The Classic runtime reads <map>.trains.json at
    # map load and runs one train per route. Absent the configured line in
    # canonical, nothing is written and the runtime is unchanged.
    _train_routes_path = output_map.with_suffix(".trains.json")
    if args.runtime_product == "classic_cp437":
        from scripts.pipeline.compile_classic_train_routes import (
            main as compile_train_routes,
        )
        if compile_train_routes([
                "--canonical", str(canonical_ir_path),
                "--terrain-metadata", str(run_root / "terrain_metadata.json"),
                "--dem-a3d", str(run_root / "dem_terrain.a3d"),
                "--out", str(_train_routes_path), "--allow-absent"]) != 0:
            raise RuntimeError("train route compilation failed")
    build_receipt = {
        "schema": "y92.world_e2e_build.v1",
        "map_build_backend": "direct",
        "runtime_product": args.runtime_product,
        "runtime_binary": ".run/game_classic" if args.runtime_product == "classic_cp437" else ".run/game",
        "profile": args.profile,
        "requirements": str(requirements_path.resolve()),
        "map_path": str(output_map.resolve()),
        "mesh_root": str((run_root / "meshes").resolve()),
        "train_routes": (str(_train_routes_path.resolve())
                         if _train_routes_path.is_file() else None),
        "sign_sprites": (str(_sign_sprites_receipt.resolve())
                         if _sign_sprites_receipt.is_file() else None),
        "counts": {
            "world_ir_buildings": building_count,
            "emitted_building_mass": building_bake_receipt["buildings_baked"],
            "emitted_facades": len(_front_emissions) + int(
                facade_lowering_receipt["counts"]["facades_emitted"]),
            "facades_street_derived": facade_lowering_receipt["counts"]["facades_emitted"],
            "facades_procedural_fallback": 0,
            "accepted_signs": 0,
            "emitted_sign_representations": facade_lowering_receipt["counts"]["signs_emitted"],
            "emitted_fixtures": len(_fixture_emissions),
            "emitted_facade_panels_fixture_owner": len(_front_emissions),
            "emitted_place_fronts": len(_place_front_emissions),
            "planned_place_fronts": len(plan_front_rows),
            "emitted_population_instances": len(_population_emissions),
            "emitted_linear_instances": sum(
                row.get("owner") == "linear" for row in _population_emissions),
            "emitted_custom_instances": sum(
                row.get("owner") == "custom" for row in _population_emissions),
            "population_terminal_states": emission_manifest["counts"][
                "population_by_terminal_state"],
            "surface_plan": str(surface_stamp_plan_path),
            "embedded_minimap_markers": base["counts"]["minimap_markers"],
        },
    }
    # Keep the accounting explicit while FL-4813/4814 products are connected.
    build_receipt["image_observation_corpora"] = image_corpus_receipts
    build_receipt["capture_review_ingest"] = capture_review_ingest_receipt
    build_receipt["counts"].update(population_plan_counts)
    build_receipt['macro_appearance'] = {
        'profiled_buildings': len(_front_emissions),
        'building_specific_source_association_claimed': False,
        'world_ir': str((run_root / 'macro.lowered.world_ir.json').resolve()),
    }
    build_receipt["counts"].update({
        "embedded_mesh_instances": (
            len(_population_emissions)
            + building_bake_receipt["buildings_mesh_collision"]
        ),
        "elevation_samples": dem_receipt["counts"]["dem_samples"],
        "hero_facades_required": len(hero_facades),
        "hero_facades_street_derived": sum(
            feature.get("semantic_class") == "facade_plane"
            for feature in observation_documents["station_facade_world_ir"].get(
                "canonical_features", []
            )
        ),
        "street_imagery_covered_facades": sum(
            bool(item.get("source_coverage")) for item in hero_facades
        ),
        "street_imagery_facades_compiled": facade_lowering_receipt["counts"]["facades_emitted"],
        "route_capture_frames": capture_manifest.get("counts", {}).get("accepted_captures", 0),
        "route_keyframes": capture_manifest.get("counts", {}).get("selected_keyframes", 0),
        "image_object_detections": observation_documents["object_detections"]["counts"]["observations"],
        "capture_review_annotations": (
            capture_review_routing["counts"]["annotations"]
            if capture_review_ingest_receipt is not None else 0
        ),
        "capture_review_generic_instances": (
            capture_review_routing["counts"]["generic_template_instances"]
            if capture_review_ingest_receipt is not None else 0
        ),
        "capture_review_generic_population_profiles": (
            capture_review_routing["counts"].get("generic_population_profiles", 0)
            if capture_review_ingest_receipt is not None else 0
        ),
        "capture_review_custom_authoring": (
            capture_review_routing["counts"]["custom_authoring"]
            if capture_review_ingest_receipt is not None else 0
        ),
        "capture_review_unresolved": (
            capture_review_routing["counts"]["unresolved_review"]
            if capture_review_ingest_receipt is not None else 0
        ),
        "image_object_masks": observation_documents["object_masks"]["counts"]["masks"],
        "machine_track_hypotheses": observation_documents["object_tracks"]["counts"]["track_hypotheses"],
        "machine_track_matches": observation_documents["object_tracks"]["counts"]["matched_detections"],
        "triangulated_track_candidates": observation_documents["track_triangulation"]["counts"]["triangulated_candidates"],
        "track_geometry_gate_passed": observation_documents["track_triangulation"]["counts"]["geometry_gate_passed"],
        "track_world_ir_promotion_ready": observation_documents["track_triangulation"]["counts"]["world_ir_promotion_ready"],
        "unreviewed_sign_ocr_candidates": observation_documents["sign_ocr"]["counts"]["sign_candidates"],
        "locale_template_candidate_tracks": observation_documents["template_candidates"]["counts"]["tracks_with_candidates"],
        "selected_locale_templates": facade_lowering_receipt["counts"]["selected_locale_templates"],
        "station_camera_frames": observation_documents["camera_registration"]["corridor"]["frames_solved"],
        "station_gravity_inliers": observation_documents["camera_registration"]["stages"]["gravity_alignment"]["inliers"],
        "station_planar_surface_points": (
            observation_documents["station_identity_plane"].get("plane") or {}
        ).get("surface_points", 0),
        "station_sparse_reconstruction_points": observation_documents[
            "camera_sparse_reconstruction"
        ]["counts"]["points"],
        "station_facade_portal_views": len(
            observation_documents["station_facade_reconstruction"]["portal"][
                "observations"
            ]
        ),
        "station_facade_sign_views": len(
            observation_documents["station_facade_reconstruction"][
                "station_identity_sign"
            ]["observations"]
        ),
        "route_corridor_samples": route_walkability_receipt["counts"][
            "corridor_samples"
        ],
        "route_pre_blocked_samples": route_walkability_receipt["counts"][
            "pre_blocked_samples"
        ],
        "route_post_blocked_samples": route_walkability_receipt["counts"][
            "post_blocked_samples"
        ],
        "route_height_vertices_restored": route_walkability_receipt["counts"][
            "height_vertices_restored"
        ],
    })
    build_receipt["counts"]["accepted_signs"] = sum(
        feature.get("semantic_class") == "sign"
        for feature in observation_documents["station_facade_world_ir"].get(
            "canonical_features", []
        )
    )
    build_receipt["elevation"] = {
        "world_min_m": dem_receipt["elevation"]["source_min_m"],
        "world_max_m": dem_receipt["elevation"]["source_max_m"],
    }
    build_receipt["minimap_labels"] = requirements.get("minimap_labels", [])
    build_receipt["home_start_marker"] = requirements.get("home_start_marker")
    build_receipt["required_unresolved_conflicts"] = emission_manifest[
        "counts"]["blocking_terminal_states"]
    # WIRE-01: source, canonical-entity and observation-debt provenance.
    # The OSM ingest receipt carries no release/version keys, so the OSM
    # block records the source fields the receipt does carry.
    _triage_groups: dict = {}
    for _row in (osm_ingest_receipt.get("triage") or {}).get("by_group", []):
        _triage_groups[_row.get("group")] = _triage_groups.get(_row.get("group"), 0) + 1
    _plateau_tiles = plateau_ingest_receipt.get("tiles", [])
    _canonical_dispositions: dict = {}
    for _row in canonical_doc.get("dispositions", []):
        _canonical_dispositions[_row.get("disposition")] = (
            _canonical_dispositions.get(_row.get("disposition"), 0) + 1
        )
    _debt_by_state: dict = {}
    for _key, _val in (debt_receipt.get("counts") or {}).get("by_state", {}).items():
        if _key.endswith("unboxed"):
            _debt_by_state["unboxed"] = _val
        elif _key.endswith("boxed"):
            _debt_by_state["boxed"] = _val
        elif _key.endswith("owed"):
            _debt_by_state["owed"] = _val
        else:
            _debt_by_state[_key] = _val
    _survey_doc = json.loads((requirements_path.parent / "added_route_imagery_survey.json").read_text(encoding="utf-8"))
    _frame_dir = PROJECT_ROOT / "assets/source_bundles/numabe_home_to_station_20260912/captures"
    _frames_on_disk = sum(
        1 for _p in _frame_dir.rglob("*")
        if _p.is_file() and _p.suffix.lower() in (".jpg", ".jpeg", ".png")
    ) if _frame_dir.is_dir() else 0
    build_receipt["sources"] = {
        "osm": {
            "bundle": str(osm_ingest_receipt.get("bundle")),
            "artifact": (osm_ingest_receipt.get("source") or {}).get("artifact_path"),
            "capture_date": (osm_ingest_receipt.get("source") or {}).get("capture_date"),
            "licence": (osm_ingest_receipt.get("source") or {}).get("licence"),
            "features_emitted": osm_ingest_receipt.get("features_emitted"),
            "observations_emitted": osm_ingest_receipt.get("observations_emitted"),
            "triage_by_group": _triage_groups,
        },
        "plateau": {
            "release": plateau_ingest_receipt.get("release"),
            "licence": plateau_ingest_receipt.get("licence"),
            "tile_sha256s": [_t.get("sha256") for _t in _plateau_tiles],
            "buildings_in_aoi": (plateau_ingest_receipt.get("counts") or {}).get("buildings_in_aoi"),
            "matched": (plateau_ingest_receipt.get("match") or {}).get("matched"),
            "footprints": {
                "rewritten_to_plateau": (canonical_footprints_receipt.get("counts") or {}).get("rewritten_to_plateau"),
                "added_plateau_only": (canonical_footprints_receipt.get("counts") or {}).get("added_plateau_only"),
                "skipped_overlap": (canonical_footprints_receipt.get("counts") or {}).get("skipped_overlap"),
            },
        },
        "imagery": {
            "survey_id": _survey_doc.get("survey_id"),
            "frames_on_disk": _frames_on_disk,
        },
    }
    build_receipt["canonical"] = {
        "entities": canonical_doc["counts"]["entities"],
        "dispositions_by_state": _canonical_dispositions,
        "height_evidence_state": canonical_doc["counts"]["height_by_evidence_state"],
        "conflicting": canonical_doc.get("conflicting_heights", []),
    }
    # FL-4809 R1: the measurement that decided importance, pinned by content
    # rather than by path. A run directory is gitignored, so a path alone does
    # not let a later reader confirm which numbers this build was planned from.
    # The producer's own input hashes travel with it, so the chain reads
    # world IR + route + landmarks -> receipt -> plan -> build, end to end.
    build_receipt["route_importance"] = {
        "run_path": str(route_importance_path.resolve()),
        "preserved_path": str(route_importance_preserved.resolve()),
        "sha256": _file_sha256(route_importance_path),
        "producer_inputs": route_importance_receipt.get("inputs", {}),
        "counts": route_importance_receipt.get("counts", {}),
        "buildings_by_importance": (
            (route_importance_receipt.get("counts") or {})
            .get("by_semantic_class", {}).get("building_footprint", {})),
    }
    build_receipt["authored_waivers"] = (
        {"path": str(Path(authored_waivers_path).resolve()),
         "sha256": _file_sha256(Path(authored_waivers_path))}
        if authored_waivers_path else None)
    build_receipt["observation_debt"] = {
        "by_state": _debt_by_state,
        "by_importance": (debt_receipt.get("counts") or {}).get("by_importance", {}),
    }
    build_receipt["ledger"] = {
        "rows": (ledger_doc.get("counts") or {}).get("rows", 0),
        "histograms": ledger_receipt.get("histograms", {}),
        "cross_tab": ledger_receipt.get("cross_tab", {}),
        "no_visual_owner": ledger_receipt.get("no_visual_owner", []),
        "unresolved": ledger_receipt.get("unresolved", []),
    }
    build_receipt["counts"]["observation_debt_features"] = (debt_receipt.get("counts") or {}).get("features", 0)
    build_receipt["pipeline_ownership"] = {
        "preassembly_candidate": str(preassembly_candidate_path.resolve()),
        "population_asset_plan": str(population_asset_plan_path.resolve()),
        "population_and_attachment": "population dispatch plans",
        "asset_specification": {
            "custom": str(custom_asset_plan_path.resolve()),
            "station_facade": str(station_facade_plan_path.resolve()),
        },
        "lowering": "mechanical receipts only",
        "spatial_validation": str(
            (run_root / "spatial_coherence.json").resolve()),
        "metric_validation": str((run_root / "object_metric.json").resolve()),
    }
    build_receipt['human_scale_calibration'] = scale_receipt
    build_receipt["spawn_grounding"] = spawn_receipt
    build_receipt["surface_appearance"] = surface_receipt
    build_receipt["component_receipts"] = {
        "surface": str((run_root / "surface_receipt.json").resolve()),
        "classic_base": str((run_root / "classic_base_receipt.json").resolve()),
        "dem_lowering": str(dem_receipt_path.resolve()),
        "building_specs": str(building_specs_receipt_path.resolve()),
        "building_mass": str(building_bake_receipt_path.resolve()),
        "route_walkability": str(route_walkability_receipt_path.resolve()),
        "scale_receipt": str(scale_receipt_path.resolve()),
        "source_palette": str(source_palette_path.resolve()),
        "population_dispatch": str(
            (lane_pop_dir / "population_dispatch.json").resolve()),
        "preassembly_candidate": str(preassembly_candidate_path.resolve()),
        "population_asset_plan": str(population_asset_plan_path.resolve()),
        "emission_manifest": str(emission_manifest_path.resolve()),
        "linear_lowering": str(
            (run_root / "lower_linear_lowering_receipt.json").resolve()),
        "fixture_lowering": str(
            (run_root / "lower_fixtures_lowering_receipt.json").resolve()),
        "custom_lowering": str(
            (run_root / "lower_custom_lowering_receipt.json").resolve()),
        "station_facade_lowering": str(facade_lowering_receipt_path.resolve()),
        "station_facade_lowered_world_ir": str(lowered_station_world_ir_path.resolve()),
        "requirements": str(requirements_path.resolve()),
        "route_capture_manifest": str(capture_manifest_path.resolve()),
        "osm_world_ir": str(osm_regen_path.resolve()),
        "osm_ingest_receipt": str(osm_regen_receipt_path.resolve()),
        "plateau_world_ir": str(plateau_ir_path.resolve()),
        "plateau_ingest_receipt": str(plateau_receipt_path.resolve()),
        "canonical_entities": str(canonical_entities_path.resolve()),
        "canonical_entities_receipt": str(canonical_entities_receipt_path.resolve()),
        "observation_debt": str(debt_path.resolve()),
        "observation_debt_receipt": str(debt_receipt_path.resolve()),
        "route_importance": str(route_importance_path.resolve()),
        "canonical_world_ir": str(canonical_ir_path.resolve()),
        "canonical_footprints_receipt": str(canonical_footprints_receipt_path.resolve()),
        "feature_ledger": str(ledger_path.resolve()),
        "feature_ledger_receipt": str(ledger_receipt_path.resolve()),
        "acceptance_chain": str(chain_path.resolve()),
        "acceptance_chain_text": str(chain_text_path.resolve()),
        "requirements_gate": str(gate_path.resolve()),
        "requirements_gate_receipt": str(gate_receipt_path.resolve()),
        **({
            "capture_review_routing": str(capture_review_routing_path.resolve()),
            "capture_review_world_ir": capture_review_ingest_receipt["world_ir"]["path"],
        } if capture_review_ingest_receipt is not None else {}),
        **{
            f"fl4813_{key}": str(path)
            for key, path in resolved_observation_paths.items()
        },
    }
    receipt_path = run_root / "build_receipt.json"
    receipt_path.write_text(json.dumps(build_receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print("BUILD COMPLETE")
    if args.launch_intermediate_review:
        from launch_world_ir_classic import main as launch_classic
        return launch_classic(['--receipt', str(receipt_path), '--intermediate-review'])
    if args.launch:
        subprocess.run(["make", "audit-game-classic"], cwd=PROJECT_ROOT, check=True)
        from launch_world_ir_classic import main as launch_classic
        return launch_classic(["--receipt", str(receipt_path)])
    return 0

def main():
    parser = argparse.ArgumentParser(
        description="Evidence-to-canonical-reconstruction pipeline")
    parser.add_argument('--geometry-proposal-config', type=str,
                        help='Run a pinned unaccepted geometry experiment only; requires --run-root')
    parser.add_argument("--profile", choices=("world_ir", "sbu_legacy"), default="world_ir",
                        help="Configuration family (default: world_ir; sbu_legacy is historical only)")
    parser.add_argument("--map-build-backend", choices=("direct", "asciiid_legacy"), default="direct",
                        help="Map compilation backend (default: direct; ASCIIID is explicit legacy use)")
    parser.add_argument("--runtime-product", choices=("classic_cp437", "fl4512_unicode"),
                        default="classic_cp437", help="Runtime consumer identity (default: classic_cp437)")
    parser.add_argument("--launch", action="store_true",
                        help="After a passing completeness and product audit, launch the Classic runtime "
                             "(Classic-only: requires default --runtime-product classic_cp437; explicit "
                             "FL4512 remains via make run-game-fl4512)")
    parser.add_argument('--launch-intermediate-review', action='store_true',
                        help='Inspect macro content and open Classic for explicitly intermediate visual review')
    parser.add_argument("--osm-world-ir", type=str, default=None,
                        help="Canonical OSM World IR document for the direct backend")
    parser.add_argument("--dem-world-ir", type=str, default=None,
                        help="Canonical DEM World IR document for the direct backend")
    parser.add_argument("--aoi-geojson", type=str, default=None,
                        help="Source-bundle AOI with home/station roles for the direct backend")
    parser.add_argument("--requirements", type=str, default=None,
                        help="Source-bundle required-content contract for the direct backend")
    parser.add_argument("--capture-manifest", type=str, default=None,
                        help="Route capture manifest (default: beside --requirements)")
    parser.add_argument(
        "--capture-review-routing", type=str, default=None,
        help=("Reviewed generic/custom crop-routing receipt; the direct backend "
              "ingests it as evidence-only World IR observations"),
    )
    parser.add_argument("--palette-map", type=str, default="assets/a3d/game_map_y8.a3d",
                        help="Existing Classic A3D palette source for the direct backend")
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
                        help="Legacy SBU import scale only. Direct World IR builds use the "
                             "global native calibration in scripts/pipeline/geo_frames.py.")
    parser.add_argument("--building-height-mult", type=float, default=5.0,
                        help="Multiplier for building extrude height when source meshes are flat footprints")
    parser.add_argument("--max-terrain-grid-segs", type=int, default=2048,
                        help="Cap terrain grid segments per axis for high-scale OSM runs (0 = no cap)")
    # FL-2573/FL-3695: Terrain_envelope.akm cleared one old pit oracle by
    # stamping a broad two-level plateau, but that is not proper topology.
    # The CLI now rejects low-Z-variety meshes before the destructive bake.
    parser.add_argument("--topology-mesh", type=str, default=SBU_LEGACY_TOPOLOGY_MESH,
                        help="Legacy SBU topology AKM baked before deferred buildings are appended")
    parser.add_argument("--topology-z-scale", type=float, default=SBU_LEGACY_TOPOLOGY_Z_SCALE,
                        help="Legacy SBU vertical scale applied before the ASCIIID terrain bake")
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
    parser.add_argument(
        "--stop-after-preassembly", action="store_true",
        help=("Compile the complete evidence, reconstruction, representation, "
              "population, attachment, surface, station, and asset plans; "
              "write the preassembly verdict and stop before any A3D bytes"),
    )
    parser.add_argument("--population-plan", type=str, default=None,
                        help="Evidence-only population plan override; must pass provenance verify() against lane inputs (default: compile in-lane)")
    parser.add_argument("--representation-plan", type=str, default=None,
                        help="Evidence-only representation plan override; runs only with a sibling receipt proving same-generation provenance (default: compile in-lane)")
    parser.add_argument("--mapillary-objects-ir", type=str, default=None,
                        help="Mapillary objects world IR fused as third canonical evidence (default: observe03 mapillary_objects.world_ir.json)")
    parser.add_argument("--lidar-road-corridors", type=str, default=None,
                        help="FL-4934: compile_lidar_road_corridor.py output; paints eligible side roads at their measured corridor (default: none)")
    parser.add_argument("--lidar-roof-field-dir", type=str, default=None,
                        help="FL-4876: directory of per-building <source>.roof_field.json (roof_form_fit.py output); "
                             "the building bake lowers their per-vertex roof heights and fitted roof forms (default: none)")
    parser.add_argument("--lidar-roof-summary", type=str, default=None,
                        help="FL-4932: compile_lidar_roof_field.py --all roof_field_summary.json; adds the TMG height rung to canonical entities (default: none)")
    parser.add_argument("--resume-fixtures-from", type=str, default=None,
                        help="Append deferred fixtures onto an already baked terrain-only map and write the final output")
    bake_alias_group = parser.add_mutually_exclusive_group()
    bake_alias_group.add_argument("--bake-buildings", dest="bake_buildings_alias", action="store_true",
                                  default=False,
                                  help="Alias for --pipeline-mode baked (bake terrain before fixtures)")
    bake_alias_group.add_argument("--no-bake-buildings", dest="no_bake_buildings_alias", action="store_true",
                                  default=False,
                        help="Alias for --pipeline-mode traditional (append fixtures without baking)")
    args = parser.parse_args()
    if args.geometry_proposal_config:
        # FL-4812: model proposals never imply a build or runtime acceptance.
        if (args.profile != 'world_ir' or args.map_build_backend != 'direct'
                or args.launch or args.launch_intermediate_review or args.activate_run
                or not args.run_root):
            parser.error('geometry proposal requires world_ir/direct, --run-root, and no launch/activation')
        from scripts.pipeline.camera_solve.geometry_proposal import run_proposal
        result = run_proposal(args.geometry_proposal_config, args.run_root)
        print(json.dumps({'stage': result['stage'], 'counts': result['counts'],
                          'world_ir_promotion_ready': False}, sort_keys=True))
        return 0
    if args.map_build_backend == "direct":
        if any(arg == '--content-scale' or arg.startswith('--content-scale=') for arg in sys.argv[1:]):
            parser.error('--content-scale is legacy-only; direct builds use the canonical geo_frames calibration')
        if args.profile != "world_ir":
            parser.error("the direct backend requires --profile world_ir")
        return _run_direct_world_ir_backend(args, parser)
    if args.profile != "sbu_legacy":
        parser.error("asciiid_legacy requires explicit --profile sbu_legacy")
    if args.launch:
        parser.error(
            "--launch is unavailable with asciiid_legacy; compile through the direct backend "
            "and pass the World IR completeness gate"
        )
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

    header("World E2E Pipeline — explicit ASCIIID legacy backend")
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
        ("map build backend", q.map_build_backend),
        ("runtime product", q.runtime_product),
        ("profile", q.profile),
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
                      map_build_backend=q.map_build_backend,
                      runtime_product=q.runtime_product,
                      profile=q.profile,
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
        print(f"  {style('next action:', 'warn')} Open in the HTML ASCIIID authoring alternate:")
        print("    python3 scripts/launcher.py --action map-asciicker-presenter-html")
        print(f"    then open through the visible HTML file control: {p.a3d_output}")
    else:
        print(f"  {fail_item('No output produced')}")
        print(f"  {style('next action:', 'fail')} Check step errors above and rerun.")

    _run_log.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "promote":
        cmd_promote(sys.argv[2:])
        raise SystemExit(0)
    raise SystemExit(main())
