#!/usr/bin/env python3
"""Lower population-plan placements for point/facade domains (NUMABE-LOWER-FIXTURES-01).

Run-form facade_archetype / generic_storefront placements lower as exact
full-face panels from dispatcher-owned physical contracts, never as midpoint
points or lowerer-selected size buckets.

Domains owned by this lane: street_fixture, generic_storefront, facade_archetype.
The lane accepts only the dispatcher-owned fixture slice. It never discovers
or silently skips sibling-lane work.

Positions come only from the grounded dispatch and meshes come only from the
preassembly asset manifest. This module authors neither semantics nor assets.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline.compile_facade_shell import (
    HEIGHT_SCALE,
    _load_a3d_edit,
)
from scripts.pipeline.compile_population_ground_plan import require_grounded
from scripts.pipeline.population_asset_contract import load_asset_plan
from scripts.pipeline.world_ir import WorldIRStore
from scripts.pipeline.world_ir import schema as S

LANE = "lower_fixtures"
# FL-4876 FACE-FIT-01: furthest a wall panel slides along its face to stay inside it (m); under the 1.5 m
# spatial-coherence attachment tolerance with margin for the outward offset
FACE_FIT_MAX_SLIDE_M = 1.0
# FL-4876 WALL-SEAT-01: a wall fixture's back plane stands this far (m) in front of the wall the player sees, and
# the seat move (which, with the face slide, must stay inside the spatial-coherence attachment tolerance) is capped
# the way lower_plan_custom caps its SIGN-WALL-01 shift
WALL_SEAT_GAP_M = 0.02
WALL_SEAT_BUDGET_M = 1.45
# a freestanding mesh whose lowest vertex is within this height (m) of its origin already includes its post
GROUNDED_MESH_MAX_ZMIN_M = 0.1


def _mesh_zmin_m(path: Path, scale: float) -> float | None:
    """Lowest vertex of an ASCII-PLY AKM in metres (None if the file cannot be read)."""
    try:
        from scripts.decimate_akm import parse_akm
        verts = parse_akm(str(path))[0]
    except Exception:
        return None
    return min(p[2] for p in verts) / scale if verts else None


def _sanitize(value: str) -> str:
    """Instance-name sanitiser, identical to the sibling lowerer's.

    FL-4876 SANITIZE-01. This module called _sanitize at one place and
    defined it nowhere, so lower_population_plan raised NameError the first
    time it was ever reached. Like MARGIN_WORLD in the base compiler, it
    survived because the pre-assembly gate refuses long before any lowerer
    runs, so this half of the pipeline had never executed.

    The definition is taken verbatim from lower_plan_custom.py:37-38, the
    other lowerer, rather than invented here. compile_population_asset_plan
    carries the same function with a 180-character cap; the lowerers use 120
    and this is a lowerer. lower_plan_linear inlines the same character class
    at :161. Three copies of one rule already exist; this adds no fourth
    variant.
    """
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)[:120]


def _identity_token(value: str) -> str:
    """Encode an upstream identity for the generated-representation key."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)

def lower_population_plan(
    *, plan_path: Path, asset_plan_path: Path, world_ir_path: Path,
    terrain_metadata_path: Path,
    map_path: Path | None = None, meshes_out: Path, output_root: Path,
    scale: float,
) -> dict:
    plan = json.loads(Path(plan_path).read_text())
    dispatch = require_grounded(plan, "fixtures")
    population_assets, asset_by_placement, _ = load_asset_plan(
        asset_plan_path, meshes_out)
    source_dispatch_sha = (plan.get("grounding") or {}).get(
        "source_dispatch_sha256")
    if ((population_assets.get("inputs") or {}).get("dispatch_sha256") or {}).get(
            "fixtures") != source_dispatch_sha:
        raise ValueError(
            "population asset plan was authored from another fixture dispatch")
    # Spatial generation pin (REVIEW v4 re-anchor law): fail closed when
    # the plan was compiled against different bytes than these inputs.
    from scripts.pipeline.plan_spatial_provenance import verify as _verify_plan_world
    _verified_pin = _verify_plan_world(
        plan, world_ir_path,
        terrain_metadata_path=terrain_metadata_path,
        world_units_per_metre=scale)
    _verified_echo = {
        "canonical_world_ir_sha256": _verified_pin.get("canonical_world_ir_sha256"),
        "world_units_per_metre": _verified_pin.get("world_units_per_metre"),
    }
    if terrain_metadata_path is not None:
        _verified_echo["terrain_metadata_sha256"] = _verified_pin.get(
            "terrain_metadata_sha256")
    store = WorldIRStore.load(Path(world_ir_path))
    meta = json.loads(Path(terrain_metadata_path).read_text())
    origin_E = float(meta["origin_easting"])
    origin_N = float(meta["origin_northing"])
    offset_x = float(meta.get("offset_x", 0.0))
    offset_y = float(meta.get("offset_y", 0.0))
    def to_world(E, N):
        return (E - origin_E) * scale + offset_x, (N - origin_N) * scale + offset_y

    def ground_bake(placement, height_m):
        return (float(placement["ground_bake_z"])
                + max(0.0, height_m) * scale * HEIGHT_SCALE)

    meshes_out = Path(meshes_out)

    # FL-4876 WALL-SEAT-01: the wall the player sees behind a fixture, by the rule compile_sign_planes uses for logo
    # planes (called, not copied): a building drawn by a mass mesh, or carrying a facade panel / place front, shows
    # its wall on the canonical footprint face; any other (terrain-box) building shows the extruded terrain wall.
    from scripts.pipeline.compile_sign_planes import FACE_PANEL_MODULES, wall_distance_m
    wall_terrain = None
    if map_path is not None and Path(map_path).is_file():
        from scripts.pipeline.extruded_wall import Terrain
        wall_terrain = Terrain(Path(map_path))
    rings_world: dict[str, list] = {}
    for _feat in store.features:
        _g = _feat.get("geometry") or {}
        if _feat.get("semantic_class") == "building_footprint" and _g.get("kind") == "polygon":
            _ring = [tuple(q[:2]) for q in _g["coordinates"]]
            if len(_ring) > 1 and _ring[0] == _ring[-1]:
                _ring = _ring[:-1]
            rings_world[_feat["feature_id"]] = [to_world(e, n) for e, n in _ring]
    mass_ids: set[str] = set()
    for _bid in rings_world:
        _src = _bid.split(":", 2)[-1]
        if (meshes_out / ("mass_" + _src.replace("/", "_") + ".akm")).is_file():
            mass_ids.add(_bid)
    for _carrier in plan.get("carriers", []):
        for _pl in _carrier.get("placements", []):
            _att = _pl.get("attachment") or {}
            if (_pl.get("module_id") in FACE_PANEL_MODULES
                    and _att.get("anchor_kind") == "building_face"):
                mass_ids.add(_att.get("canonical_building_id"))
    _pf = Path(output_root) / "place_front_instances.json"
    if _pf.is_file():
        for _row in json.loads(_pf.read_text(encoding="utf-8")):
            _host = _row.get("parent_building_feature_id")
            if isinstance(_host, str):
                mass_ids.add(_host)
    wall_seats: list[dict] = []

    def wall_seat_m(px, py, nx, ny, slide_m):
        """Metres to move a wall fixture along +normal so its back plane (mesh y = 0) stands on the visible wall.

        px, py: the fixture pose (EPSG:32654). Returns (move_m, basis, capped). Without a baked map the pose stands.
        """
        if wall_terrain is None:
            return 0.0, "no baked map: pose kept", False
        wx0, wy0 = to_world(px, py)
        d_m, basis, _host = wall_distance_m(wall_terrain, rings_world, mass_ids, wx0, wy0, nx, ny, scale)
        if d_m is None:
            return 0.0, basis, False
        want = d_m + WALL_SEAT_GAP_M
        budget = math.sqrt(max(0.0, WALL_SEAT_BUDGET_M ** 2 - slide_m ** 2))
        move = max(-budget, min(0.5, want))
        return move, basis, abs(move - want) > 1e-9

    def _non_host_ring_at(w, host_id):
        """Feature id of a building outline (other than host_id) containing world point w, else None."""
        x, y = w
        for _bid, _r in rings_world.items():
            if _bid == host_id:
                continue
            _in = False
            for _i in range(len(_r)):
                (_x1, _y1), (_x2, _y2) = _r[_i], _r[(_i + 1) % len(_r)]
                if (_y1 > y) != (_y2 > y) and x < _x1 + (y - _y1) * (_x2 - _x1) / (_y2 - _y1):
                    _in = not _in
            if _in:
                return _bid
        return None

    _zmin_cache: dict[str, float | None] = {}
    freestanding_grounded = 0

    def _sited_pose(p):
        """Return the dispatcher-owned pose; do no spatial planning here."""
        _att = p.get("attachment")
        _aid = _att.get("attachment_id") if isinstance(_att, dict) else None
        pose = p.get("resolved_pose")
        if not isinstance(pose, dict):
            raise ValueError(
                f"fixture placement {p.get('placement_id')} has no "
                "pre-lowering resolved_pose")
        return (float(pose["E"]), float(pose["N"]), _aid,
                float(pose.get("yaw_deg", 0.0)))
    output_root = Path(output_root)
    meshes_out.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(parents=True, exist_ok=True)

    akm_cache: dict[str, str] = {}
    akm_metric: dict[str, dict] = {}
    instances: list[dict] = []
    consumed: dict[str, int] = {}
    attached: list = []
    seen: set[str] = set()
    facade_runs_consumed = 0
    run_panels: list[dict] = []
    def attachment_face_frame(placement):
        """Return the dispatcher-owned face frame without geometry lookup."""
        attachment = placement.get("attachment")
        if (not isinstance(attachment, dict)
                or attachment.get("record_type") != "attachment"
                or attachment.get("anchor_kind") != "building_face"):
            return None
        fid = attachment.get("canonical_building_id")
        pose = placement.get("resolved_pose") or {}
        frame = placement.get("resolved_frame") or {}
        tangent = frame.get("tangent")
        normal = frame.get("normal")
        if (not fid or not isinstance(tangent, list)
                or not isinstance(normal, list)):
            raise ValueError(
                f"fixture placement {placement.get('placement_id')} lacks "
                "its dispatcher-owned building-face frame")
        return (fid, float(tangent[0]), float(tangent[1]),
                float(normal[0]), float(normal[1]),
                float(pose["E"]), float(pose["N"]))

    for carrier in plan.get("carriers", []):
        for p in carrier.get("placements", []):
            pid = p["placement_id"]
            if pid in seen:
                raise ValueError(f"placement consumed twice: {pid}")
            seen.add(pid)
            module_id = p.get("module_id") or p.get("custom_identity_subject") or ""
            variant = p.get("variant")
            authored_asset = asset_by_placement.get(str(pid))
            if (authored_asset is None
                    or authored_asset.get("owner") != "fixtures"):
                raise ValueError(
                    f"fixture placement {pid} has no preauthored AKM")
            mesh = str(authored_asset["mesh_name"])
            akm_cache[str(pid)] = mesh
            akm_metric[mesh] = dict(authored_asset["metric_contract"])
            asset_contract = p["asset_contract"]
            mount_kind = asset_contract.get("mount_kind")
            if mount_kind not in (
                    "building_face_panel", "freestanding_fixture"):
                raise ValueError(
                    f"fixture placement {pid} has no upstream mount contract")
            is_facade_run = (isinstance(p.get("run"), dict)
                             and mount_kind == "building_face_panel")
            consumed[module_id] = consumed.get(module_id, 0) + 1
            _sited = _sited_pose(p)
            E, N, _attachment_id, _sited_yaw = _sited
            height_m = float(p.get("height_m") or 0.0)
            yaw_c = math.radians(_sited_yaw)
            wx, wy = to_world(E, N)
            evidence = p.get("evidence", {}) or {}
            kind = evidence.get("kind", "observation")
            provenance = {"observation": "measured", "osm": "derived", "rule": "synthesized"}.get(
                kind, "synthesized")
            # Per-placement run metadata for the shared emit tail; None on
            # every non-run path so point placements stay unchanged.
            run_extra = None
            inst_extra = None
            expected_metric_norms = (1.0, 1.0, 1.0)
            metric_site_variable = False
            if is_facade_run:
                # Full-face panel spanning the run. No midpoint-only fallback
                # remains on this path: every facade run emits exactly one panel.
                resolved = p["resolved_pose"]
                try:
                    E0, N0 = float(resolved["E0"]), float(resolved["N0"])
                    E1, N1 = float(resolved["E1"]), float(resolved["N1"])
                except KeyError as exc:
                    raise ValueError(
                        f"facade run {pid} lacks dispatcher-resolved endpoints") \
                        from exc
                envelope = p["asset_physical_envelope"]
                length_m = float(envelope["longitudinal_extent_m"])
                if length_m <= 0:
                    raise ValueError(f"facade run has no length: {pid}")
                seg = math.hypot(E1 - E0, N1 - N0) or 1.0
                ux, uy = (E1 - E0) / seg, (N1 - N0) / seg
                midE, midN = (E0 + E1) / 2.0, (N0 + N1) / 2.0
                _face_frame = attachment_face_frame(p)
                if _face_frame is not None:
                    (fid, ux, uy, nx, ny,
                     midE, midN) = _face_frame
                    parent, is_attached = fid, True
                    attached.append(pid)
                    px, py = midE, midN
                else:
                    raise ValueError(
                        f"facade run {pid} reached fixture lowering without "
                        "a planned building-face attachment")
                wx, wy = to_world(px, py)
                panel_w = length_m
                panel_h = float(envelope["vertical_extent_m"])
                width_tag = f"w{int(round(panel_w * 1000))}mm"
                height_tag = f"h{int(round(panel_h * 1000))}mm"
                storefront = asset_contract.get("catalog_domain") \
                    == "generic_storefront"
                yaw_deg = math.degrees(math.atan2(uy, ux))
                transform = [ux, uy, 0.0, 0.0,
                             nx, ny, 0.0, 0.0,
                             0.0, 0.0, HEIGHT_SCALE, 0.0,
                             wx, wy, ground_bake(p, 0.0), 1.0]
                facade_runs_consumed += 1
                run_panels.append({
                    "placement_id": pid, "module_id": module_id,
                    "run_length_m": round(length_m, 3),
                    "panel_width_m": panel_w, "panel_height_m": panel_h,
                    "asset_contract": envelope, "attached": is_attached})
                # The shared tail below emits the instance and the world-IR
                # representation exactly once per placement.
                run_extra = {"run_panel": True,
                             "run_length_m": round(length_m, 3),
                             "panel_width_m": panel_w,
                             "panel_height_m": panel_h,
                             "yaw_deg": round(yaw_deg, 3)}
                metric_site_variable = True
            elif mount_kind == "building_face_panel":
                _face_frame = attachment_face_frame(p)
                if _face_frame is not None:
                    (fid, tx, ty, nx, ny,
                     px, py) = _face_frame
                    # FL-4876 CONTRACT-WIDTH-01. Producer and consumer
                    # disagreed. population_representation_contract.py:376
                    # writes planned_width_m ONLY when the resolved dimensions
                    # carry a width_m; this read it unconditionally, so a
                    # building-face panel whose module declares no width
                    # killed the whole fixtures lowering with
                    # KeyError: 'planned_width_m'.
                    #
                    # The fallback is not invented. asset_physical_envelope is
                    # the placement's own declared bound, selected before
                    # dispatch with fit_policy "declared_asset_bounds", and
                    # longitudinal_extent_m is the same number the planner
                    # used to reconcile this footprint against building mass.
                    # The sibling run-panel branch above already takes its
                    # panel height from that envelope.
                    #
                    # If neither exists the placement is refused BY IDENTITY
                    # rather than given a guessed width, and rather than
                    # taking every other fixture down with it.
                    _planned_w = asset_contract.get("planned_width_m")
                    if _planned_w is None:
                        _planned_w = (p.get("asset_physical_envelope")
                                      or {}).get("longitudinal_extent_m")
                    if _planned_w is None:
                        raise ValueError(
                            f"fixture placement {pid} mounts on a building "
                            "face but declares no width: asset_contract has "
                            "no planned_width_m and asset_physical_envelope "
                            "has no longitudinal_extent_m")
                    length = float(_planned_w)
                    # FL-4876 CARDFIX-01: a real-size wall fixture keeps the width of its own mesh (its planned
                    # width is a catalogue default such as 0.4 m or 2.0 m, not the object); only the face-wide
                    # unit panels are stretched, by scaling X to `length`.
                    _basis = akm_metric[mesh].get("basis")
                    _real_size = _basis == "generated_wall_fixture"
                    _native_w = float(
                        (akm_metric[mesh].get("intended_native_extent_m") or [0.0])[0])
                    if _real_size:
                        if _native_w <= 0.0:
                            raise ValueError(
                                f"wall fixture {pid} mesh {mesh} has no native width")
                        length = _native_w
                    _slide_m = 0.0
                    # FL-4876 FACE-FIT-01: keep the panel inside its face. The panel never grows; when the
                    # catalog width is longer than the face it is cut to the face length, and when it would
                    # run past a face end it slides along the same wall until it fits. Without this, fixed
                    # 8 m roller shutters and rooftop railings hung up to 4 m past the corners of shorter
                    # faces (operator 2026-10-16: faces "floating / not connected to the extrusions").
                    _pose = p.get("resolved_pose") or {}
                    _fa, _fb = _pose.get("face_end_a"), _pose.get("face_end_b")
                    if _fa and _fb:
                        _fl = math.hypot(_fb[0] - _fa[0], _fb[1] - _fa[1])
                        if _fl > 1e-6:
                            # position of the panel centre along the face, measured from end a
                            _ux, _uy = (_fb[0] - _fa[0]) / _fl, (_fb[1] - _fa[1]) / _fl
                            _t = min(max((px - _fa[0]) * _ux + (py - _fa[1]) * _uy, 0.0), _fl)
                            length = min(length, _fl)
                            _tc = min(max(_t, length / 2.0), _fl - length / 2.0)
                            # the slide is capped at FACE_FIT_MAX_SLIDE_M so the mounted panel stays within the
                            # spatial-coherence attachment tolerance (1.5 m) of its observed point; whatever still
                            # overhangs after the capped slide is trimmed (build_reg_78: uncapped slides of
                            # 1.7-3.3 m failed 6 coherence rows)
                            _tc = _t + max(-FACE_FIT_MAX_SLIDE_M, min(FACE_FIT_MAX_SLIDE_M, _tc - _t))
                            length = min(length, 2.0 * _tc, 2.0 * (_fl - _tc))
                            _slide_m = _tc - _t
                            px, py = (px + (_tc - ((px - _fa[0]) * _ux + (py - _fa[1]) * _uy)) * _ux,
                                      py + (_tc - ((px - _fa[0]) * _ux + (py - _fa[1]) * _uy)) * _uy)
                    # FL-4876 WALL-SEAT-01: stand the back of the fixture on the wall the player sees (57 terrain-box
                    # fixtures floated 0.19-0.95 m off it, one 4.45 m)
                    _seat_m, _seat_basis, _seat_capped = wall_seat_m(px, py, nx, ny, _slide_m)
                    # the seat refines the dispatched pose; it may not carry the origin into another building's
                    # outline (build_reg_90 SF-4876-S409-015: a -0.48 m seat put a platform vending machine inside the
                    # PLATEAU station canopy whose outline overlaps the platform wall)
                    if _seat_m:
                        _entered = _non_host_ring_at(to_world(px + nx * _seat_m, py + ny * _seat_m), fid)
                        if _entered and not _non_host_ring_at(to_world(px, py), fid):
                            _seat_basis = "seat refused: it would enter non-host outline %s (%s)" % (
                                _entered, _seat_basis)
                            _seat_m, _seat_capped = 0.0, False
                    px += nx * _seat_m
                    py += ny * _seat_m
                    wx, wy = to_world(px, py)
                    _x_scale = (length / _native_w) if _real_size else length
                    transform = [tx * _x_scale, ty * _x_scale, 0.0, 0.0,
                                 nx, ny, 0.0, 0.0,
                                 0.0, 0.0, HEIGHT_SCALE, 0.0,
                                 wx, wy, ground_bake(p, 0.0), 1.0]
                    expected_metric_norms = (_x_scale, 1.0, 1.0)
                    metric_site_variable = True
                    wall_seats.append({
                        "placement_id": pid, "module_id": module_id,
                        "seat_move_m": round(_seat_m, 3), "wall_basis": _seat_basis,
                        "seat_capped": _seat_capped, "slide_m": round(_slide_m, 3),
                        "x_scale": round(_x_scale, 4)})
                    inst_extra = {"wall_seat_m": round(_seat_m, 3),
                                 "wall_seat_basis": _seat_basis,
                                 "wall_seat_capped": _seat_capped,
                                 "wall_fixture_width_m": round(length, 3) if _real_size else None}
                    parent, is_attached = fid, True
                    attached.append(pid)
                else:
                    raise ValueError(
                        f"face-panel placement {pid} lacks its dispatched "
                        "building-face frame")
            else:
                y = yaw_c
                c, s = math.cos(y), math.sin(y)
                # FL-4876 POST-GROUND-01. plan_height() gives every sign module its 1.8 m mounting height, and the
                # lowering added it. A freestanding sign mesh already carries its post from z = 0 (every archetype
                # and custom sign starts at the ground), so the whole post floated 1.8 m. A mesh that reaches the
                # ground is stood on the ground; one that starts above it keeps the planned height.
                if mesh not in _zmin_cache:
                    _zmin_cache[mesh] = _mesh_zmin_m(meshes_out / mesh, scale)
                _zmin = _zmin_cache[mesh]
                _grounded = (_zmin is not None
                             and _zmin <= GROUNDED_MESH_MAX_ZMIN_M)
                _lift_m = 0.0 if _grounded else height_m
                if _grounded and height_m > 0.0:
                    freestanding_grounded += 1
                transform = [c, s, 0.0, 0.0, -s, c, 0.0, 0.0,
                             0.0, 0.0, HEIGHT_SCALE, 0.0,
                             wx, wy, ground_bake(p, _lift_m), 1.0]
                if akm_metric[mesh].get("basis") == "generated_wall_fixture_freestanding":
                    # real-size shapes of many families (signs, racks, AC
                    # units); no one catalogue family band describes them, so
                    # the native and transform legs gate and the band is
                    # skipped, as for the wall-mounted form
                    metric_site_variable = True
                inst_extra = {"planned_height_m": height_m,
                              "grounded_mesh": _grounded,
                              "lift_m": _lift_m}
                _patt = p.get("attachment") or {}
                parent = p["planned_parent_feature_id"]
                is_attached = False
            inst_name = _sanitize(f"{LANE}_{pid}")
            inst = {
                "placement_id": pid, "module_id": module_id,
                "feature_id": p["planned_feature_id"],
                "attachment_id": (p.get("attachment") or {}).get("attachment_id"),
                "mesh_name": mesh, "inst_name": inst_name,
                "transform": transform, "flags": 3, "story_id": -1,
                "parent": parent, "facade_attached": is_attached,
                "mount_kind": mount_kind,
                "catalog_domain": asset_contract.get("catalog_domain"),
                # FACEALIGN-02: observed plan position (EPSG:32654). The
                # transform carries the mounted position (face outset / run
                # midpoint); orientation references are evaluated at the
                # observed point, where the mount wall is unambiguous.
                "E": E, "N": N,
            }
            inst["metric_contract"] = {
                **akm_metric[mesh],
                "expected_transform_norms": list(expected_metric_norms),
                "site_variable": metric_site_variable,
                "asset_contract": asset_contract,
            }
            product = {"mesh": mesh, "instance": inst_name,
                       "transform": transform, "placement_id": pid,
                       "module_id": module_id, "variant": variant,
                       "mount_kind": mount_kind,
                       "catalog_domain": asset_contract.get("catalog_domain"),
                       "E": E, "N": N}
            if run_extra is not None:
                inst.update(run_extra)
                product.update(run_extra)
            if inst_extra is not None:
                inst.update(inst_extra)
            instances.append(inst)
            store.add_representation(S.generated_representation(
                representation_id=f"rep:{LANE}:{_identity_token(pid)}",
                feature_id=p["planned_feature_id"],
                target="mesh_asset",
                product=product,
                lowering={"compiler": "scripts/pipeline/lower_plan_fixtures.py",
                          "provenance": provenance},
                authority_claim=S.AUTHORITY_PRESENTATION_ONLY))
    # Structural stamping: every emitted row carries its plan identity.
    # A placement missing from the plan is an error, never a default.
    _meta_by_pid = {}
    for _carrier in plan.get("carriers", []):
        for _placement in _carrier.get("placements", []):
            _meta_by_pid[_placement.get("placement_id")] = (
                _carrier.get("carrier_class", ""),
                ((_placement.get("evidence", {}) or {}).get("kind", "rule")))
    for row in instances:
        _pid = row.get("placement_id")
        if _pid not in _meta_by_pid:
            raise ValueError(
                f"structural stamping miss: {row.get('inst_name')} "
                f"placement {_pid!r} not in plan")
        _cc, _ev = _meta_by_pid[_pid]
        row["carrier_class"] = _cc
        row["evidence_kind"] = _ev

    store.assert_valid()
    store.save(output_root / f"{LANE}.world_ir.json")
    (output_root / f"{LANE}_instances.json").write_text(
        json.dumps(instances, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")

    if map_path is not None:
        editor = _load_a3d_edit()
        pre, version, existing, start, gens, markers = editor.read_a3d_sections(map_path)
        additions = [editor.a3d_format.A3DInstance(**{k: r[k] for k in
                    ("mesh_name", "inst_name", "transform", "flags", "story_id")})
                     for r in instances]
        if {r.inst_name for r in existing} & {r.inst_name for r in additions}:
            raise ValueError("duplicate fixture instances")
        editor.write_a3d_sections(map_path, pre, version, [*existing, *additions],
                                  start, gens, markers)

    for r in instances:
        if not (meshes_out / r["mesh_name"]).stat().st_size:
            raise ValueError(f"empty fixture asset: {r['mesh_name']}")
    expected_facade_runs = sum(
        1 for carrier in plan.get("carriers", [])
        for p in carrier.get("placements", [])
        if isinstance(p.get("run"), dict)
        and (p.get("asset_contract") or {}).get("mount_kind")
        == "building_face_panel")
    if facade_runs_consumed != expected_facade_runs:
        raise ValueError(
            f"facade run fallback remains: consumed {facade_runs_consumed} "
            f"of {expected_facade_runs} dispatched facade runs")
    max_width_err = 0.0
    for rp in run_panels:
        err = abs(rp["panel_width_m"] - rp["run_length_m"])
        max_width_err = max(max_width_err, err)
        if err > 0.5 + 1e-9:
            raise ValueError(
                f"run panel width error {err} m: {rp['placement_id']}")
    receipt = {
        "spatial_provenance": _verified_echo,
        "lane": LANE,
        "dispatch": dispatch,
        "mechanical_accounting": {
            "planned_placements": dispatch["placements_for_owner"],
            "consumed_placements": sum(consumed.values()),
            "emitted_instances": len(instances),
            "semantic_validation_owner": "compile_spatial_coherence_receipt.py",
        },
        "placements_consumed_per_module": dict(sorted(consumed.items())),
        "instances_emitted": len(instances),
        "akm_files_written": 0,
        "akm_files_referenced": len(set(akm_cache.values())),
        "facade_attached": len(attached),
        "facade_runs_consumed": facade_runs_consumed,
        "panels_emitted": len(run_panels),
        "run_panel_akm_files": len({
            row["mesh_name"] for row in instances if row.get("run_panel")}),
        "population_asset_plan": str(Path(asset_plan_path).resolve()),
        "max_panel_width_error_m": round(max_width_err, 3),
        "run_panels": run_panels,
        "wall_seats": wall_seats,
        "wall_seat_summary": {
            "wall_fixtures": len(wall_seats),
            "moved_to_wall": sum(1 for r in wall_seats if abs(r["seat_move_m"]) > 0.005),
            "capped": sum(1 for r in wall_seats if r["seat_capped"]),
            "max_move_m": max((abs(r["seat_move_m"]) for r in wall_seats), default=0.0),
        },
        "freestanding_grounded_from_planned_height": freestanding_grounded,
        "asset_contract_owner": "population_dispatch",
    }
    (output_root / f"{LANE}_lowering_receipt.json").write_text(
        json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(f"[{LANE}] instances={len(instances)}")
    return receipt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--asset-plan", type=Path, required=True)
    ap.add_argument("--world-ir", type=Path, required=True)
    ap.add_argument("--meshes-out", type=Path, required=True)
    ap.add_argument("--output-root", type=Path, required=True)
    ap.add_argument("--scale", type=float, required=True)
    ap.add_argument("--terrain-metadata", type=Path, required=True,
                    help="run terrain_metadata.json so the CLI emits the same "
                         "world frame as the build (origin_easting/northing + "
                         "offset)")
    args = ap.parse_args(argv)
    receipt = lower_population_plan(
        plan_path=args.plan, asset_plan_path=args.asset_plan,
        world_ir_path=args.world_ir,
        terrain_metadata_path=args.terrain_metadata,
        map_path=None,
        meshes_out=args.meshes_out, output_root=args.output_root,
        scale=args.scale)
    print(f"OK instances={receipt['instances_emitted']} "
          f"attached={receipt['facade_attached']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
