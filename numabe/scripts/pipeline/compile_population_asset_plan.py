#!/usr/bin/env python3
"""Author every population AKM before the preassembly completeness gate.

This is the sole writer for assets selected by the dispatched population plan.
The output is an immutable placement/logical-instance-to-AKM manifest.  A3D
lowerers consume the manifest and transforms only; they do not choose or author
geometry.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path

from scripts.pipeline.classic_procedural_mesh import (
    ARCHETYPE_PHYSICAL,
    _BLOCK_FONT_5X7,
    _add_block_text,
    _add_box,
    _build_archetype,
)
from scripts.pipeline.compile_facade_shell import write_akm
from scripts.pipeline.dispatch_population_plan import require_owner, OWNERS
from scripts.pipeline.population_asset_geometry import (
    ARCHETYPE_BY_MODULE,
    CUSTOM_METRIC_FAMILY,
    CUSTOM_MODULES,
    CUSTOM_VARIANTS,
    build_custom_geometry,
    build_run_panel,
    build_unit_panel,
    build_wall_fixture_freestanding,
    unit_panel_basis,
    build_point_mesh,
    build_unit_mesh,
    catenary_boxes,
    variant_tint,
)
from scripts.pipeline.population_asset_contract import SCHEMA, sha256
from scripts.pipeline.population_representation_contract import (
    LINEAR_REALIZATION_KINDS,
    LINEAR_RUN_LENGTH_KINDS,
)

# FL-4876.  The kinds _linear_geometry() below actually branches on.  The
# representation contract owns the vocabulary; this is the lowerer's claim to
# cover it, checked at import so a new producer kind stops the pipeline here
# instead of seventy minutes into a build.
_COVERED_LINEAR_KINDS = frozenset({"point", "unit", "segment",
                                   "hardware", "span"})
if _COVERED_LINEAR_KINDS != LINEAR_REALIZATION_KINDS:
    raise ImportError(
        "population asset authoring does not cover the representation "
        "contract's realization kinds; uncovered="
        + repr(sorted(LINEAR_REALIZATION_KINDS - _COVERED_LINEAR_KINDS))
        + " unknown-to-contract="
        + repr(sorted(_COVERED_LINEAR_KINDS - LINEAR_REALIZATION_KINDS)))


def _load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sanitize(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)[:180]


def _extent(vertices: list[tuple], scale: float) -> list[float]:
    if not vertices:
        raise ValueError("asset builder returned no vertices")
    axes = [[float(vertex[index]) for vertex in vertices] for index in range(3)]
    return [(max(values) - min(values)) / scale for values in axes]


def _contract_hash(contract: dict) -> str:
    return hashlib.sha256(json.dumps(
        contract, sort_keys=True).encode("utf-8")).hexdigest()[:12]


class AssetWriter:
    def __init__(self, root: Path, scale: float):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.scale = float(scale)
        self.cache: dict[str, dict] = {}

    def write(self, key: str, mesh_name: str, vertices: list[tuple],
              faces: list[list[int]], *, family: str, basis: str) -> dict:
        if key in self.cache:
            return self.cache[key]
        if not vertices or not faces:
            raise ValueError(f"asset {key} has empty geometry")
        mesh_name = _sanitize(Path(mesh_name).stem) + ".akm"
        mesh_path = self.root / mesh_name
        write_akm(
            mesh_path, vertices, faces,
            comment=(f"population asset plan {key}; authored before A3D "
                     "assembly; alpha=255 => no collision"))
        row = {
            "mesh_name": mesh_name,
            "mesh_sha256": sha256(mesh_path),
            "mesh_bytes": mesh_path.stat().st_size,
            "metric_contract": {
                "basis": basis,
                "semantic_family": family,
                "intended_native_extent_m": _extent(vertices, self.scale),
            },
        }
        self.cache[key] = row
        return row


FACADE_DOMAINS = ("facade_archetype", "generic_storefront")


def _unmappable_error(unmappable) -> str:
    """Say which of two different failures happened, for every offender.

    A facade module here is not a missing archetype. A facade is realized as a
    panel on a building face; it only reaches this branch because
    compile_population_plan demoted it to freestanding when no canonical face
    was within tolerance. That is an unfillable facade slot, and giving it a
    fixture archetype would stand a facade in the street detached from the
    building it describes.

    A street fixture here is a genuine vocabulary gap: the module was planned,
    observed and dispatched, and no archetype names its physical form. Mapping
    it to a near neighbour would assert a shape nobody saw, so the run stops
    and asks rather than guessing.
    """
    facades, fixtures = [], []
    for placement_id, module_id, domain in unmappable:
        (facades if domain in FACADE_DOMAINS else fixtures).append(
            f"{module_id} ({placement_id})")
    parts = []
    if facades:
        parts.append(
            f"{len(facades)} facade placement(s) demoted to freestanding with "
            "no attachable building face, which is an unfillable facade slot "
            "rather than a missing archetype: " + ", ".join(sorted(facades)))
    if fixtures:
        parts.append(
            f"{len(fixtures)} fixture module(s) with no entry in "
            "FIXTURE_ARCHETYPE_BY_MODULE: " + ", ".join(sorted(fixtures)))
    return "; ".join(parts)


def _fixture_rows(document: dict, writer: AssetWriter,
                  source_palette: dict, unmappable: list) -> list[dict]:
    require_owner(document, "fixtures")
    rows = []
    for carrier in document.get("carriers", []):
        for placement in carrier.get("placements", []):
            placement_id = str(placement["placement_id"])
            module_id = str(placement.get("module_id") or
                            placement.get("custom_identity_subject") or "")
            variant = placement.get("variant")
            contract = dict(placement["asset_contract"])
            mount_kind = contract.get("mount_kind")
            run = placement.get("run")
            appearance = None
            if mount_kind == "building_face_panel":
                attachment = placement.get("attachment") or {}
                building_id = str(
                    attachment.get("canonical_building_id")
                    or placement.get("planned_feature_id") or "")
                palette_entry = (source_palette.get(
                    "building_material_by_feature") or {}).get(building_id)
                if palette_entry:
                    appearance = palette_entry.get("facade_profile")
                if appearance:
                    contract["facade_profile"] = appearance
            if isinstance(run, dict) and mount_kind == "building_face_panel":
                envelope = placement["asset_physical_envelope"]
                width = float(envelope["longitudinal_extent_m"])
                height = float(envelope["vertical_extent_m"])
                storefront = contract.get("catalog_domain") == "generic_storefront"
                vertices, faces = build_run_panel(
                    module_id, variant, width, height, writer.scale,
                    storefront, contract)
                contract_tag = _contract_hash(contract)
                key = (f"fixture:run:{module_id}:{variant}:{width:.6f}:"
                       f"{height:.6f}:{contract_tag}")
                asset = writer.write(
                    key,
                    f"{module_id}__{variant or 'novariant'}__run_"
                    f"w{round(width * 1000)}mm_h{round(height * 1000)}mm_"
                    f"{contract_tag}.akm",
                    vertices, faces, family="storefront_front",
                    basis="generated_full_face_run_panel")
            elif mount_kind == "building_face_panel":
                vertices, faces = build_unit_panel(
                    module_id, variant, writer.scale, contract)
                contract_tag = _contract_hash(contract)
                key = (f"fixture:facade:{module_id}:{variant}:"
                       f"{contract_tag}")
                asset = writer.write(
                    key, f"{module_id}__{variant or 'novariant'}__"
                    f"{contract_tag}.akm",
                    vertices, faces, family="storefront_front",
                    basis=unit_panel_basis(module_id, variant))
            elif mount_kind == "freestanding_fixture":
                resolved = placement.get("resolved_archetype")
                if isinstance(resolved, dict) and resolved.get(
                        "archetype_subtype") in ARCHETYPE_PHYSICAL:
                    # FL-4876. The representation plan already chose this
                    # asset, as a world-template template_id whose physical
                    # block names the archetype. Honour that choice instead
                    # of re-deriving one from a module id: these placements
                    # carry no module because the plan never picked one, and
                    # inventing eighteen module ids to launder an existing
                    # decision would be a second asset vocabulary.
                    subtype = str(resolved["archetype_subtype"])
                    vertices, faces = _build_archetype(subtype, writer.scale)
                    family = str(ARCHETYPE_PHYSICAL[subtype][0])
                    basis = ("representation_plan:"
                             + str(resolved.get("basis") or subtype))
                    key = f"fixture:archetype:{subtype}"
                    asset = writer.write(
                        key, f"{subtype}.akm", vertices, faces,
                        family=family, basis=basis)
                    rows.append({
                        "owner": "fixtures", "placement_id": placement_id,
                        "module_id": module_id,
                        "variant": variant, **asset,
                    })
                    continue
                if (module_id in CUSTOM_MODULES
                        or (module_id, variant) in CUSTOM_VARIANTS):
                    vertices, faces = build_custom_geometry(
                        module_id, variant, writer.scale)
                    family = CUSTOM_METRIC_FAMILY[module_id]
                    basis = "generated_custom_fixture"
                elif (module_id not in ARCHETYPE_BY_MODULE
                        and unit_panel_basis(module_id, variant)
                        == "generated_wall_fixture"):
                    # FL-4876 SNAP-CAP-01: a wall fixture whose face snap was
                    # refused for distance stands where it was observed, as
                    # the same real-size shape with a post under any part
                    # that does not reach the ground.
                    vertices, faces = build_wall_fixture_freestanding(
                        module_id, variant, writer.scale, contract)
                    family = "storefront_front"
                    basis = "generated_wall_fixture_freestanding"
                elif module_id not in ARCHETYPE_BY_MODULE:
                    # FL-4876. This index used to be unguarded, so an
                    # unmappable module raised a bare KeyError naming one
                    # module and nothing else: the run had to be repeated once
                    # per offender to learn the set. Collect instead, and let
                    # build() report every one at the end.
                    #
                    # The table is FIXTURE_ARCHETYPE_BY_MODULE, re-exported
                    # here under a name that drops the word carrying its
                    # precondition. The sibling consumer in
                    # population_representation_contract tests membership
                    # before reading; this one did not.
                    unmappable.append((placement_id, module_id,
                                       str(contract.get("catalog_domain")
                                           or "")))
                    continue
                else:
                    subtype = ARCHETYPE_BY_MODULE[module_id]
                    vertices, faces = _build_archetype(subtype, writer.scale)
                    family = str(ARCHETYPE_PHYSICAL[subtype][0])
                    basis = f"ARCHETYPE_PHYSICAL:{subtype}"
                key = f"fixture:point:{module_id}:{variant}"
                asset = writer.write(
                    key, f"{module_id}__{variant or 'novariant'}.akm",
                    vertices, faces, family=family, basis=basis)
            else:
                raise ValueError(
                    f"fixture placement {placement_id} has invalid mount "
                    f"contract {mount_kind!r}")
            row = {
                "owner": "fixtures", "placement_id": placement_id,
                "module_id": module_id, "variant": variant, **asset,
            }
            if appearance:
                row["appearance_contract"] = {
                    "colour_method": appearance.get("colour_method"),
                    "provenance_class": appearance.get("provenance_class"),
                    "source_observations": appearance.get(
                        "source_observations") or [],
                    "wall_rgb": appearance.get("wall_rgb"),
                    "glass_rgb": appearance.get("glass_rgb"),
                    "trim_rgb": appearance.get("trim_rgb"),
                    "roof_rgb": appearance.get("roof_rgb"),
                }
            rows.append(row)
    return rows


def _custom_rows(document: dict, specifications: dict,
                 writer: AssetWriter) -> list[dict]:
    require_owner(document, "custom")
    specs = {str(row["placement_id"]): row
             for row in specifications.get("rows", [])}
    rows = []
    seen = set()
    for carrier in document.get("carriers", []):
        for placement in carrier.get("placements", []):
            placement_id = str(placement["placement_id"])
            spec = specs.get(placement_id)
            if spec is None:
                raise ValueError(
                    f"custom placement {placement_id} has no specification")
            seen.add(placement_id)
            width = float(spec["width_m"])
            height = float(spec["height_m"])
            depth = float(spec["depth_m"])
            vertices: list[tuple] = []
            faces: list[list[int]] = []
            _add_box(
                vertices, faces,
                (-width / 2.0, width / 2.0, -depth / 2.0, depth / 2.0,
                 0.0, height),
                tuple(int(value) for value in spec["background_rgb"]),
                writer.scale)
            text = spec.get("block_text")
            if text:
                if (text != text.upper() or any(
                        char != " " and char not in _BLOCK_FONT_5X7
                        for char in text)):
                    raise ValueError(
                        f"custom asset contains non-CP437 text: {text!r}")
                rendered = _add_block_text(
                    vertices, faces, text, max_width_m=width * 0.9,
                    z_min_m=height * 0.15, z_max_m=height * 0.85,
                    rgb=tuple(int(value) for value in spec["text_rgb"]),
                    scale=writer.scale)
                if not rendered:
                    raise ValueError(
                        f"custom text does not fit asset: {placement_id}")
            key = f"custom:{placement_id}:{_contract_hash(spec)}"
            asset = writer.write(
                key, f"{spec['label']}__{placement_id}.akm",
                vertices, faces, family=str(spec["semantic_family"]),
                basis="custom_asset_plan_exact_dimensions")
            # FL-4876 FACADE-C1.  Report the appearance provenance this row
            # already has.
            #
            # The box above is coloured from spec["background_rgb"] and the
            # text from spec["text_rgb"], and compile_custom_asset_plan
            # records in colour_basis where those came from -- but none of it
            # was carried onto the asset row, so a custom storefront standing
            # on a building face reached the gate with no appearance contract
            # at all.  Nothing here invents a colour; it states the one the
            # asset is built from.
            #
            # provenance_class is deliberately separate from colour_method so
            # a fallback cannot be read as a measurement.  On this lane every
            # one of the 61 custom assets reports crop_unreadable, which is a
            # neutral grey default, not source colour -- see
            # artifacts/experiments/fl4876_custom_appearance/.
            colour_basis = str(spec.get("colour_basis") or "")
            measured = colour_basis == "source_crop_dominant"
            rows.append({
                "owner": "custom", "placement_id": placement_id,
                "module_id": placement.get("module_id"),
                "rendered_text": text, **asset,
                "appearance_contract": {
                    "colour_method": colour_basis or "unstated",
                    "provenance_class": ("source_measured" if measured
                                         else "synthesized_fallback"),
                    "source_observations": list(spec.get("capture_ids") or []),
                    "wall_rgb": list(spec.get("background_rgb") or []),
                    "trim_rgb": list(spec.get("text_rgb") or []),
                    "glass_rgb": None,
                    "roof_rgb": None,
                },
            })
    if seen != set(specs):
        raise ValueError(
            "custom specifications and dispatch differ: extras="
            f"{sorted(set(specs) - seen)}")
    return rows


def _rail_catenary_track_offset(placement: dict, ref_yaw_deg: float):
    """Signed local-Y distance from a rail-attached catenary mast to its track centreline.

    Lowering maps local +X to the pose yaw (maths angle) and local +Y to its left.
    A mast attached on side R of its railway carrier at lateral d has the track
    centreline to the LEFT of the carrier direction (+Y), on side L to the right.
    For a span whose yaw runs against the carrier direction the sign flips.
    Returns None when the placement is not attached to a railway carrier."""
    att = placement.get("attachment") or {}
    if "railway" not in str(att.get("canonical_carrier_id") or ""):
        return None
    lat = float(att.get("lateral_offset_m") or 0.0)
    if lat <= 0.0:
        return None
    carrier_yaw = next((float((lg.get("pose") or {}).get("yaw_deg", ref_yaw_deg))
                        for lg in placement.get("logical_realizations") or []
                        if lg.get("kind") == "hardware"), ref_yaw_deg)
    sign = 1.0 if str(att.get("side") or "R") == "R" else -1.0
    if math.cos(math.radians(ref_yaw_deg - carrier_yaw)) < 0.0:
        sign = -sign
    return sign * lat


def _linear_geometry(placement: dict, logical: dict, scale: float):
    module_id = str(placement["module_id"])
    variant = placement.get("variant")
    contract = placement["asset_contract"]
    kind = str(logical["kind"])
    if kind == "point":
        return (*build_point_mesh(module_id, variant, scale, contract),
                module_id, "planned_point_module")
    if kind in LINEAR_RUN_LENGTH_KINDS:
        # "unit" and "segment" are the same cross-section at a different
        # length authority: a unit takes the contract's fixed isolated-module
        # extent, a segment takes its measured run length.  build_unit_mesh
        # is already run geometry parameterized by that length -- every branch
        # spans -L/2..L/2 and subdivides from L -- so the run case needs no
        # second geometry vocabulary, only its own length and its own basis.
        length = float(logical.get("length_m") or
                       (logical.get("physical_envelope") or {}).get(
                           "longitudinal_extent_m") or 0.0)
        if length <= 0:
            raise ValueError(
                f"linear logical {logical.get('logical_instance_id')} has no length")
        basis = ("planned_linear_unit" if kind == "unit"
                 else "planned_linear_run_segment")
        return (*build_unit_mesh(module_id, variant, length, scale, contract),
                module_id, basis)
    vertices: list[tuple] = []
    faces: list[list[int]] = []
    if kind == "hardware":
        height = float(logical["height_m"])
        tint = variant_tint((115, 113, 108), variant or "")
        hardware = logical.get("hardware")
        if hardware == "utility_pole":
            _add_box(vertices, faces,
                     (-0.15, 0.15, -0.15, 0.15, 0, height), tint, scale)
            _add_box(vertices, faces,
                     (-0.9, 0.9, -0.06, 0.06, height - 1.4,
                      height - 1.25),
                     variant_tint((80, 82, 80), variant or ""), scale)
        elif (hardware == "gantry_portal" and module_id == "overhead_gantry_wire"
              and _rail_catenary_track_offset(
                  placement, float((logical.get("pose") or {}).get("yaw_deg", 0.0))) is not None):
            # Railway catenary mast (photographed masts stand beside the track,
            # 1.6-4 m off its centreline): one post plus a cantilever arm that
            # reaches 0.6 m past the track centreline, so the contact wire hangs
            # over the rails -- not a 5 m portal centred beside the track.
            off = _rail_catenary_track_offset(
                placement, float((logical.get("pose") or {}).get("yaw_deg", 0.0)))
            reach = off + math.copysign(0.6, off)
            lo, hi = (0.0, reach) if reach > 0 else (reach, 0.0)
            _add_box(vertices, faces,
                     (-0.12, 0.12, -0.12, 0.12, 0, height), tint, scale)
            _add_box(vertices, faces,
                     (-0.08, 0.08, lo, hi, height - 0.35, height - 0.2), tint, scale)
        elif hardware == "gantry_portal":
            for side in (-2.5, 2.5):
                _add_box(vertices, faces,
                         (-0.12, 0.12, side - 0.12, side + 0.12,
                          0, height), tint, scale)
            _add_box(vertices, faces,
                     (-0.12, 0.12, -2.7, 2.7, height - 0.2,
                      height + 0.1), tint, scale)
        else:
            raise ValueError(f"unknown planned hardware {hardware!r}")
        return vertices, faces, module_id, f"planned_hardware:{hardware}"
    if kind == "span":
        length = float(logical["length_m"])
        nominal = float(logical["nominal_height_m"])
        strands = (-0.14, 0.14)
        if module_id == "overhead_gantry_wire":
            # Railway contact wire hangs over the track centreline, carried by
            # the masts' arms -- not mast-to-mast beside the rails.
            off = _rail_catenary_track_offset(placement, float(logical.get("yaw_deg", 0.0)))
            if off is not None:
                strands = (off - 0.14, off + 0.14)
        catenary_boxes(
            vertices, faces, -length / 2.0, length / 2.0,
            nominal, nominal,
            variant_tint((48, 52, 52), variant or ""), scale, strands=strands)
        return vertices, faces, module_id, "planned_topology_span"
    raise ValueError(f"unknown linear logical kind {kind!r}")


def _linear_rows(document: dict, writer: AssetWriter) -> list[dict]:
    require_owner(document, "linear")
    rows = []
    seen = set()
    for carrier in document.get("carriers", []):
        for placement in carrier.get("placements", []):
            placement_id = str(placement["placement_id"])
            for logical in placement.get("logical_realizations", []):
                logical_id = str(logical["logical_instance_id"])
                if logical_id in seen:
                    raise ValueError(f"duplicate logical instance: {logical_id}")
                seen.add(logical_id)
                vertices, faces, family, basis = _linear_geometry(
                    placement, logical, writer.scale)
                key = (f"linear:{placement.get('module_id')}:"
                       f"{placement.get('variant')}:{logical.get('kind')}:"
                       f"{_contract_hash({'logical': logical, 'asset': placement['asset_contract']})}")
                asset = writer.write(
                    key, f"linear_{placement.get('module_id')}__"
                    f"{placement.get('variant') or 'default'}__"
                    f"{logical.get('kind')}__{hashlib.sha256(key.encode()).hexdigest()[:12]}.akm",
                    vertices, faces, family=family, basis=basis)
                rows.append({
                    "owner": "linear", "placement_id": placement_id,
                    "logical_instance_id": logical_id,
                    "module_id": placement.get("module_id"),
                    "variant": placement.get("variant"),
                    "logical_kind": logical.get("kind"), **asset,
                })
    return rows


def build(*, dispatch_dir: Path, custom_asset_plan_path: Path,
          source_palette_path: Path,
          meshes_out: Path, scale: float) -> dict:
    dispatch_dir = Path(dispatch_dir)
    # FL-4876. The preassembly gate verifies dispatch_sha256 for all four
    # OWNERS, so writing only three guaranteed a
    # population_asset_provenance blocker on "terminal" for every real run.
    # The test masked it by hand-writing four owners into its synthetic plan.
    # terminal placements are omissions and author no asset, so it is hashed
    # here but never read for rows.
    paths = {
        owner: dispatch_dir / f"population_plan.{owner}.json"
        for owner in OWNERS}
    documents = {owner: _load(paths[owner])
                 for owner in ("linear", "fixtures", "custom")}
    custom_specs = _load(custom_asset_plan_path)
    source_palette = _load(source_palette_path)
    writer = AssetWriter(meshes_out, scale)
    unmappable: list[tuple[str, str, str]] = []
    rows = [
        *_fixture_rows(documents["fixtures"], writer, source_palette,
                       unmappable),
        *_custom_rows(documents["custom"], custom_specs, writer),
        *_linear_rows(documents["linear"], writer),
    ]
    if unmappable:
        raise ValueError(
            "population asset plan cannot author "
            f"{len(unmappable)} placement(s): " + _unmappable_error(unmappable))
    return {
        "schema": SCHEMA,
        "inputs": {
            "dispatch_sha256": {
                owner: sha256(path) for owner, path in paths.items()},
            "custom_asset_plan_sha256": sha256(custom_asset_plan_path),
            "source_palette_sha256": sha256(source_palette_path),
            "world_units_per_metre": float(scale),
        },
        "asset_root": str(Path(meshes_out).resolve()),
        "counts": {
            "rows": len(rows),
            "unique_meshes": len(writer.cache),
            "fixtures": sum(row["owner"] == "fixtures" for row in rows),
            "custom": sum(row["owner"] == "custom" for row in rows),
            "linear": sum(row["owner"] == "linear" for row in rows),
        },
        "rows": rows,
    }


def write(*, out_path: Path, **kwargs) -> dict:
    document = build(**kwargs)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n", encoding="utf-8")
    return document


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dispatch-dir", type=Path, required=True)
    parser.add_argument("--custom-asset-plan", type=Path, required=True)
    parser.add_argument("--source-palette", type=Path, required=True)
    parser.add_argument("--meshes-out", type=Path, required=True)
    parser.add_argument("--scale", type=float, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    write(
        dispatch_dir=args.dispatch_dir,
        custom_asset_plan_path=args.custom_asset_plan,
        source_palette_path=args.source_palette,
        meshes_out=args.meshes_out,
        scale=args.scale,
        out_path=args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
