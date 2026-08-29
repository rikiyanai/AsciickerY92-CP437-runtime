#!/usr/bin/env python3
"""Run the SBU baked OSM pipeline and verify the SAC building contract.

This is the pasteable/launcher-callable wrapper for FL-1171 and FL-1146:
delete stale generated SBU OSM runs, rebuild from map_25.osm, prove the
plural Student Activities Center building is the mesh/marker source, and then
optionally open the resulting map in asciiid.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from scripts import asciiid_app


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNS_ROOT = PROJECT_ROOT / "assets" / "meshes" / "osm_runs"
DEFAULT_OSM_FILE = (PROJECT_ROOT.parent / "blender_maps" / "osm" / "map_25.osm").resolve()
ASCIIID_BIN = PROJECT_ROOT / ".run" / "asciiid"
LEGACY_BAD_RUN = RUNS_ROOT / "sbu_e2e_baked_20260422_fl1176_fix"

EXPECTED_INSTANCE = "Student_Activities_Center"
EXPECTED_MESH = "way_55446707.akm"
EXPECTED_LABEL = "Student Activities Center"
REJECTED_INSTANCE = "Student_Activity_Center"
REJECTED_MESH = "Student_Activity_Center.akm"
REJECTED_DUPLICATE_PREFIX = f"{EXPECTED_INSTANCE}_"
DEFAULT_CONTENT_SCALE = 2.25
DEFAULT_TOPOLOGY_Z_SCALE = 3.0
DEFAULT_ROAD_WIDTH_MULT = 1.5
DEFAULT_SAC_FRONT_SPAWN_X = 2616.8
DEFAULT_SAC_FRONT_SPAWN_Y = 1092.1


def _default_run_id() -> str:
    return time.strftime("sbu_e2e_sac_verify_%Y%m%d_%H%M%S")


def _shell_command(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    text = shlex.join([str(part) for part in cmd])
    if env:
        prefix = " ".join(f"{key}={shlex.quote(value)}" for key, value in sorted(env.items()))
        text = f"{prefix} {text}"
    if cwd:
        text = f"cd {shlex.quote(str(cwd))} && {text}"
    return text


def _run(cmd: list[str], *, label: str, cwd: Path = PROJECT_ROOT, env: dict[str, str] | None = None) -> None:
    print(f"\n-- {label} --")
    print(_shell_command(cmd, cwd=cwd, env=env), flush=True)
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    result = subprocess.run(cmd, cwd=str(cwd), env=run_env)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def _run_capture(cmd: list[str], *, label: str, cwd: Path = PROJECT_ROOT) -> str:
    print(f"\n-- {label} --")
    print(_shell_command(cmd, cwd=cwd), flush=True)
    result = subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        raise SystemExit(result.returncode)
    return result.stdout


def _load_building_specs(run_root: Path) -> list[dict]:
    specs_path = run_root / "building_instances.json"
    if not specs_path.is_file():
        raise SystemExit(f"missing building specs: {specs_path}")
    with specs_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise SystemExit(f"building specs are not a list: {specs_path}")
    return data


def _validate_sac_building_specs(run_root: Path) -> dict:
    specs = _load_building_specs(run_root)
    expected = [
        spec
        for spec in specs
        if spec.get("inst_name") == EXPECTED_INSTANCE and spec.get("mesh_name") == EXPECTED_MESH
    ]
    rejected = [
        spec
        for spec in specs
        if spec.get("inst_name") == REJECTED_INSTANCE or spec.get("mesh_name") == REJECTED_MESH
    ]
    duplicate_suffixes = [
        spec
        for spec in specs
        if str(spec.get("inst_name", "")).startswith(REJECTED_DUPLICATE_PREFIX)
        or str(spec.get("mesh_name", "")).startswith(REJECTED_DUPLICATE_PREFIX)
    ]
    if rejected:
        raise SystemExit(
            f"rejected shelter mapping still present as {REJECTED_INSTANCE}/{REJECTED_MESH} "
            f"in {run_root / 'building_instances.json'}"
        )
    if duplicate_suffixes:
        raise SystemExit(
            f"duplicate SAC mapping still present as {REJECTED_DUPLICATE_PREFIX}* "
            f"in {run_root / 'building_instances.json'}"
        )
    if not expected:
        names = sorted({str(spec.get("inst_name")) for spec in specs})
        raise SystemExit(
            f"missing {EXPECTED_INSTANCE}/{EXPECTED_MESH} in {run_root / 'building_instances.json'}; "
            f"available instances include: {', '.join(names[:12])}"
        )
    return expected[0]


def _validate_marker_output(markers_output: str) -> None:
    if EXPECTED_INSTANCE not in markers_output and EXPECTED_LABEL not in markers_output:
        raise SystemExit(f"embedded minimap marker for {EXPECTED_LABEL!r} was not found")
    if REJECTED_INSTANCE in markers_output:
        raise SystemExit(f"rejected minimap marker {REJECTED_INSTANCE!r} is still present")


def _validate_run(run_root: Path, *, old_run: Path) -> None:
    output_a3d = run_root / "output.a3d"
    if not output_a3d.is_file():
        raise SystemExit(f"missing final A3D: {output_a3d}")
    expected_spec = _validate_sac_building_specs(run_root)
    if old_run.exists():
        raise SystemExit(f"stale generated run still exists after cleanup: {old_run}")

    markers_output = _run_capture(
        [
            sys.executable,
            "docs/agent/cli-anything/minimap_render.py",
            "--map",
            str(output_a3d),
            "list-markers",
        ],
        label="List embedded minimap markers",
    )
    (run_root / "embedded_markers.txt").write_text(markers_output, encoding="utf-8")
    _validate_marker_output(markers_output)

    print("\n-- SAC verification --")
    print(f"run_root={run_root}")
    print(f"a3d={output_a3d}")
    print(f"building={expected_spec.get('inst_name')} mesh={expected_spec.get('mesh_name')}")
    print(f"markers={run_root / 'embedded_markers.txt'}")
    print(f"deleted_legacy_run={old_run}")


def _open_asciiid(run_root: Path, *, detach: bool) -> None:
    output_a3d = run_root / "output.a3d"
    env = {"ASCIICKER_ACTIVE_MESH_ROOT": str(run_root / "meshes")}
    cmd = [str(ASCIIID_BIN), "--map", str(output_a3d)]
    print("\n-- Open asciiid --")
    print(_shell_command(cmd, cwd=PROJECT_ROOT, env=env), flush=True)
    run_env = os.environ.copy()
    run_env.update(env)
    if detach:
        asciiid_app.launch_asciiid_gui_detached(
            ["--map", str(output_a3d)],
            cwd=PROJECT_ROOT,
            env=run_env,
            binary_path=ASCIIID_BIN,
        )
        print("asciiid launched detached")
        return
    result = asciiid_app.launch_asciiid_gui(
        ["--map", str(output_a3d)],
        cwd=PROJECT_ROOT,
        env=run_env,
        wait=True,
        binary_path=ASCIIID_BIN,
    )
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rebuild and verify the SBU SAC baked OSM run")
    parser.add_argument("--osm-file", default=str(DEFAULT_OSM_FILE), help="Local .osm input file")
    parser.add_argument("--run-id", default=None, help="Run id under assets/meshes/osm_runs")
    parser.add_argument("--runs-root", default=str(RUNS_ROOT), help="Generated OSM runs root")
    parser.add_argument(
        "--old-run",
        default=str(LEGACY_BAD_RUN),
        help="Generated run that must be deleted by the cleanup step",
    )
    parser.add_argument("--keep-previous-runs", type=int, default=0, help="Forwarded cleanup retention count")
    parser.add_argument(
        "--open-existing-run-root",
        default=None,
        help="Validate and open an existing generated run root without rerunning the OSM pipeline",
    )
    parser.add_argument("--no-open", action="store_true", help="Do not open asciiid after verification")
    parser.add_argument("--detach-open", action="store_true", help="Launch asciiid detached and exit")
    parser.add_argument("--content-scale", type=float, default=DEFAULT_CONTENT_SCALE)
    parser.add_argument("--topology-z-scale", type=float, default=DEFAULT_TOPOLOGY_Z_SCALE)
    parser.add_argument("--road-width-mult", type=float, default=DEFAULT_ROAD_WIDTH_MULT)
    parser.add_argument("--spawn-x", type=float, default=DEFAULT_SAC_FRONT_SPAWN_X)
    parser.add_argument("--spawn-y", type=float, default=DEFAULT_SAC_FRONT_SPAWN_Y)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    runs_root = Path(args.runs_root).expanduser().resolve()
    old_run = Path(args.old_run).expanduser().resolve()
    run_id = args.run_id or _default_run_id()
    run_root = runs_root / run_id

    if not ASCIIID_BIN.is_file():
        raise SystemExit(f"missing asciiid binary: {ASCIIID_BIN}")
    if args.keep_previous_runs < 0:
        raise SystemExit("--keep-previous-runs must be >= 0")

    if args.open_existing_run_root:
        existing_run_root = Path(args.open_existing_run_root).expanduser().resolve()
        _validate_run(existing_run_root, old_run=old_run)
        if not args.no_open:
            _open_asciiid(existing_run_root, detach=args.detach_open)
        return 0

    osm_file = Path(args.osm_file).expanduser().resolve()
    if not osm_file.is_file():
        raise SystemExit(f"missing OSM file: {osm_file}")

    _run(
        [
            sys.executable,
            "scripts/sbu_e2e_run.py",
            "--osm-file",
            str(osm_file),
            "--runs-root",
            str(runs_root),
            "--run-id",
            run_id,
            "--pipeline-mode",
            "baked",
            "--content-scale",
            str(args.content_scale),
            "--topology-bake",
            "--topology-z-scale",
            str(args.topology_z_scale),
            "--road-width-mult",
            str(args.road_width_mult),
            "--spawn-x",
            str(args.spawn_x),
            "--spawn-y",
            str(args.spawn_y),
            "--activate-run",
            "--keep-previous-runs",
            str(args.keep_previous_runs),
        ],
        label="Run baked SBU OSM pipeline",
    )
    _validate_run(run_root, old_run=old_run)
    if not args.no_open:
        _open_asciiid(run_root, detach=args.detach_open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
