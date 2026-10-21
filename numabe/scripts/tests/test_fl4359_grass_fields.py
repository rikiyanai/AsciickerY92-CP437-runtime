"""Behavioral compiler coverage for the FL-4359 meadow field cards.

These tests exercise the compiler's source-shaped output. Runtime shader and
GPU ownership tests live in their own lanes; this file verifies that terrain
area admission, residency-stable clump metadata, face ownership, and bounded
deformation inputs are authored correctly before any payload is emitted.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPILER = ROOT / "scripts" / "compile_fl4789_chunk_catalog.py"


def _compiler():
    spec = importlib.util.spec_from_file_location("fl4789_grass_compiler", COMPILER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _decoded_grid(compiler, *, material=0, height=64, water_patch=None,
                  step_patch=None):
    """Build a four-by-four patch (one 32x32 chunk) test terrain."""
    patches = []
    for patch_z in range(4):
        for patch_x in range(4):
            patch_material = material
            patch_height = height
            if water_patch == (patch_x, patch_z):
                patch_material = 2
            if step_patch == (patch_x, patch_z):
                patch_height = height + 32
            patches.append({
                "source_index": len(patches),
                "patch_coord": [patch_x, patch_z],
                "visual_words_u16": [patch_material] * 64,
                "corner_heights_u16": [patch_height] * 25,
                "diagonal_bits_u16": 0,
                "record_sha256": "fixture",
                "_raw": b"fixture",
            })
    return {
        "center_offset": [0.0, 0.0],
        "bounds": [0, 0, 3, 3],
        "patches": patches,
        "patch_keys": {(x, z) for x in range(4) for z in range(4)},
    }


def _build(compiler, decoded, trees=None):
    records, rows = compiler.build_meadow_records(decoded, trees or [])
    assert len(records) == len(rows)
    return records, rows


def _face_rows(compiler, rows):
    return [rows[offset:offset + compiler.FLOATS_PER_FACE]
            for offset in range(0, len(rows), compiler.FLOATS_PER_FACE)]


def test_flat_grass_coverage_is_clump_correlated_not_a_full_lattice() -> None:
    compiler = _compiler()
    records, _rows = _build(compiler, _decoded_grid(compiler))
    assert len(records) == 1
    record = records[0]
    clumps = record["vegetation_clumps"]
    assert len(clumps) >= 30

    # The coverage lattice remains a bounded candidate scaffold, but low-field
    # candidates are absent. A flat terrain must therefore have occupied and
    # unoccupied interior cells, rather than a root at every grid key.
    occupied = {
        (int(math.floor(float(clump["root"][0]) / compiler.MEADOW_COVERAGE_SPACING)),
         int(math.floor(float(clump["root"][2]) / compiler.MEADOW_COVERAGE_SPACING)))
        for clump in clumps
    }
    interior = {(x, z) for x in range(1, 11) for z in range(1, 11)}
    assert occupied & interior
    assert interior - occupied
    assert record["face_count"] == len(record["vegetation_face_clumps"])
    assert record["face_count"] <= compiler.MEADOW_MAX_FACES_PER_CHUNK


def test_one_field_sample_drives_admission_and_traits() -> None:
    compiler = _compiler()
    records, _rows = _build(compiler, _decoded_grid(compiler))
    clumps = records[0]["vegetation_clumps"]
    assert clumps
    for clump in clumps:
        x, _y, z = clump["root"]
        field = compiler._meadow_clump_field(float(x), float(z))
        traits = compiler.meadow_clump_traits_from_field(field)
        assert abs(float(clump["height"]) - traits["height"]) < 2e-5
        assert abs(float(clump["shade"]) - traits["shade"]) < 2e-5
        assert abs(float(clump["phase_lag"]) - traits["phase_lag"]) < 2e-5

    # The same conversion supplies the authored colour/value and height lanes.
    assert compiler.meadow_clump_traits_from_field(0.0)["height"] \
        < compiler.meadow_clump_traits_from_field(1.0)["height"]


def test_terrain_water_tree_and_step_gates_leave_only_supported_faces() -> None:
    compiler = _compiler()
    decoded = _decoded_grid(compiler, water_patch=(1, 1), step_patch=(2, 2))
    tree = {
        "root_base_world": [6.0, 4.0, 6.0],
        "root_base_half_size": [2.0, 2.0],
    }
    records, rows_by_source = _build(compiler, decoded, [tree])
    assert records
    for record in records:
        rows = rows_by_source[record["instance_or_source_id"]]
        for face in _face_rows(compiler, rows):
            assert int(face[3]) == compiler.MEADOW_BLADE_MAT
            endpoints = [(face[i], face[i + 1], face[i + 2]) for i in (0, 4)]
            for x, y, z in endpoints:
                ground = compiler._sample_capture_ground(decoded, x + record["world_xz"][0],
                                                          z + record["world_xz"][1])
                assert ground is not None
                assert ground[1] in (0, 1, 3)
                assert ground[0] > 49.0 / 16.0
                assert abs(y + record["authored_godot_y"] - ground[0] - 0.02) < 1e-4
            root_ground = sum(
                compiler._sample_capture_ground(
                    decoded,
                    endpoint[0] + record["world_xz"][0],
                    endpoint[2] + record["world_xz"][1],
                )[0]
                for endpoint in endpoints
            ) / 2.0
            assert all(
                abs(compiler._sample_capture_ground(
                    decoded,
                    endpoint[0] + record["world_xz"][0],
                    endpoint[2] + record["world_xz"][1],
                )[0] - root_ground) <= compiler.MEADOW_BLADE_MAX_STEP + 1e-6
                for endpoint in endpoints
            )

    # A tree root is a source gate, not a post-publish shader convention.
    roots = record["vegetation_clumps"]
    assert all(
        not (abs(float(clump["root"][0]) - 6.0) < 2.18
             and abs(float(clump["root"][2]) - 6.0) < 2.18)
        for clump in roots
    )


def test_area_batching_is_residency_stable_and_order_independent() -> None:
    compiler = _compiler()
    decoded = _decoded_grid(compiler)
    first_records, first_rows = _build(compiler, decoded)
    decoded["patches"] = list(reversed(decoded["patches"]))
    second_records, second_rows = _build(compiler, decoded)
    assert first_records == second_records
    assert first_rows == second_rows

    record = first_records[0]
    roots = [tuple(clump["root"]) for clump in record["vegetation_clumps"]]
    assert len(roots) == len(set(roots))
    assert all(0 <= int(clump["seed"]) <= 0xFFFFFFFF
               for clump in record["vegetation_clumps"])


def test_meadow_traits_and_face_map_are_exact_per_record() -> None:
    compiler = _compiler()
    decoded = _decoded_grid(compiler)
    records, rows_by_source = _build(compiler, decoded)
    required = {"root", "height", "heading", "shade", "phase_lag",
                "species", "seed", "motif", "flags"}
    for record in records:
        clumps = record["vegetation_clumps"]
        face_map = record["vegetation_face_clumps"]
        rows = rows_by_source[record["instance_or_source_id"]]
        assert len(face_map) == record["face_count"] == len(rows) // compiler.FLOATS_PER_FACE
        assert all(index == -1 or 0 <= index < len(clumps) for index in face_map)
        assert all(required == set(clump) for clump in clumps)
        assert all(1.1 <= float(clump["height"]) <= 2.0 for clump in clumps)
        assert all(0.0 <= float(clump["shade"]) <= 1.0 for clump in clumps)
        assert all(0.0 <= float(clump["phase_lag"]) <= 0.60 for clump in clumps)
        assert all(
            (compiler.MEADOW_FLOWER_SPECIES_BASE
             <= int(clump["species"]) < compiler.MEADOW_SPECIES_COUNT)
            if int(clump["flags"]) & compiler.MEADOW_FLOWER_FLAG
            else 0 <= int(clump["species"]) < compiler.MEADOW_GRASS_SPECIES_COUNT
            for clump in clumps
        )
        assert all(0 <= int(clump["motif"]) < compiler.MEADOW_MOTIF_COUNT
                   for clump in clumps)

        origin_x, origin_z = record["world_xz"]
        origin_y = record["authored_godot_y"]
        for face_index, clump_index in enumerate(face_map):
            assert clump_index >= 0
            face = rows[face_index * compiler.FLOATS_PER_FACE:(face_index + 1) * compiler.FLOATS_PER_FACE]
            clump = clumps[clump_index]
            lane_root = (face[7] + origin_x, face[11] + origin_y, face[15] + origin_z)
            # Each blade stores its own planted root. The clump root remains
            # the shared trait owner and is therefore nearby, rather than being
            # copied into every face.
            ground = compiler._sample_capture_ground(decoded, lane_root[0], lane_root[2])
            assert ground is not None and ground[1] in (0, 1, 3)
            assert abs(lane_root[1] - ground[0] - 0.02) < 1e-4
            assert math.dist((lane_root[0], lane_root[2]),
                             (clump["root"][0], clump["root"][2])) \
                <= compiler.MEADOW_CLUMP_RADIUS + compiler.MEADOW_BLADE_WIDTH
            left_rise = face[13] - face[1]
            right_rise = face[9] - face[5]
            assert 0.0 < left_rise <= float(clump["height"]) + 1e-5
            assert 0.0 < right_rise <= float(clump["height"]) + 1e-5


def test_clumps_emit_separated_tapered_blades() -> None:
    compiler = _compiler()
    records, rows_by_source = _build(compiler, _decoded_grid(compiler))
    faces_by_clump = {}
    roots_by_clump = {}
    for record in records:
        rows = rows_by_source[record["instance_or_source_id"]]
        for face_index, clump_index in enumerate(record["vegetation_face_clumps"]):
            face = rows[face_index * compiler.FLOATS_PER_FACE:(face_index + 1) * compiler.FLOATS_PER_FACE]
            faces_by_clump[clump_index] = faces_by_clump.get(clump_index, 0) + 1
            roots_by_clump.setdefault(clump_index, []).append((face[7], face[15]))
            base_width = math.dist((face[0], face[2]), (face[4], face[6]))
            tip_width = math.dist((face[8], face[10]), (face[12], face[14]))
            assert base_width > 0.0
            assert 0.0 < tip_width < base_width * 0.2
            assert abs(tip_width / base_width - 0.1) < 1e-5
    assert faces_by_clump
    assert all(1 <= count <= compiler.MEADOW_BLADES_PER_CLUMP
               for count in faces_by_clump.values())
    assert sum(count == compiler.MEADOW_BLADES_PER_CLUMP
               for count in faces_by_clump.values()) > len(faces_by_clump) * 0.8
    # Surviving blades have distinct roots, leaving narrow transparent gaps
    # between their tapered tips instead of forming a solid crossed ribbon.
    for roots in roots_by_clump.values():
        assert len(set((round(x, 5), round(z, 5)) for x, z in roots)) == len(roots)
        assert all(math.dist(a, b) > compiler.MEADOW_BLADE_WIDTH
                   for i, a in enumerate(roots) for b in roots[:i])


def test_ordinary_blades_use_only_one_cell_wide_rooted_grass_stamps() -> None:
    compiler = _compiler()
    records, _rows = _build(compiler, _decoded_grid(compiler))
    ordinary_species = {
        int(clump["species"])
        for record in records
        for clump in record["vegetation_clumps"]
        if not int(clump["flags"]) & compiler.MEADOW_FLOWER_FLAG
    }
    assert ordinary_species == {0, 1, 2}
    assert compiler.MEADOW_GRASS_SPECIES_COUNT == 3
    vocabulary = (ROOT / "godot_project/render/ascii/glyphs/authored_glyph_vocabulary.glslinc").read_text()
    for species in range(3):
        assert f"AUTHORED_PLANT_ROOTED_GRASS_{species}_SIZE = ivec2(1, 4)" in vocabulary


def test_blade_paper_is_unowned_and_motion_consumes_wind_contact_and_weather() -> None:
    compositor = (ROOT / "godot_project/render/ascii/raster/ascii_compositor.glsl").read_text()
    payload = (ROOT / "godot_project/render/backends/perspective/publish/perspective_probe_payload_publish.glsl").read_text()
    deformation = (ROOT / "godot_project/render/effects/vegetation/vegetation_deformation.glslinc").read_text()
    host = (ROOT / "godot_project/render/api/ASCIICompositorEffect.gd").read_text()
    source_contract = (ROOT / "godot_project/render/core/contracts/source/render_source_contract.glslinc").read_text()
    for material in ("205u", "255u"):
        assert f"material != {material}" in source_contract
    for consumer in (payload, compositor):
        assert 'render_source_contract.glslinc' in consumer
        assert 'source_material_owns_paper(' in consumer
    assert "FL4359_RESOLVE_STATUS_INK_PAPER_ELECTED" in compositor
    assert "vegetation_displacement_tex" in deformation
    assert "vegetation_clump_motion.values[index]" in deformation
    assert "vegetation_frame.origin_cell_time.w * 5.0" in deformation
    assert 'vec4 weather_kind_wind' in deformation
    assert 'footprints.append({"position": particle["landing"]' in host
    assert 'footprints.append({"position": fact["ground_effect_surface_position"]' in host


def test_blas_pad_uses_contact_lever_wind_and_vertical_press() -> None:
    compiler = _compiler()
    horizontal = (compiler.MEADOW_MAX_CONTACT_THROW * compiler.MEADOW_MAX_CONTACT_LEVER
                  + compiler.MEADOW_WIND_AMPLITUDE * compiler.MEADOW_CLUMP_HEIGHT_MAX)
    assert compiler.MEADOW_MAX_CONTACT_LEVER == 1.5
    assert compiler.MEADOW_MAX_VERTICAL_PRESS == 0.75 * compiler.MEADOW_CLUMP_HEIGHT_MAX
    # AABB axes bound independently; simultaneous horizontal bend and vertical
    # press do not require adding their magnitudes to every axis.
    for displacement in [(horizontal, -compiler.MEADOW_MAX_VERTICAL_PRESS, 0),
                         (0, -compiler.MEADOW_MAX_VERTICAL_PRESS, -horizontal)]:
        assert all(abs(axis) <= compiler.MEADOW_BLAS_PAD for axis in displacement)
    assert "1.5 + 0.90" not in COMPILER.read_text(encoding="utf-8")


def test_flower_is_a_stable_card_flag_without_separate_meadow_geometry() -> None:
    compiler = _compiler()
    records, rows_by_source = _build(compiler, _decoded_grid(compiler))
    flagged = [clump for record in records for clump in record["vegetation_clumps"]
               if int(clump["flags"]) & compiler.MEADOW_FLOWER_FLAG]
    assert flagged
    assert all(int(value[3]) == compiler.MEADOW_BLADE_MAT
               for record in records
               for value in _face_rows(compiler, rows_by_source[record["instance_or_source_id"]]))
