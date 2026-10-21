"""Cresta native transport checks; none constitutes runtime visual acceptance."""
import json
import base64
import math
import subprocess
import struct

import pytest
import numpy as np
from PIL import Image

from scripts import compile_fl4831_cresta_native as native


def test_manual_owner_limits_scope_and_pins_approved_sources():
    owner = json.loads(native.OWNER.read_text())
    assert owner["subject"] == "cresta_gx71"
    assert owner["status"].endswith("NOT_RUNTIME_ACCEPTANCE")
    assert native.digest(native.ROOT/owner["construction"]) == owner["construction_sha256"]
    assert native.digest(native.ROOT/owner["font"]) == owner["font_sha256"]
    assert owner["inspected_native_samples"] == list(range(0, 96, 12))
    assert [native.part_for_feature(f"wheel.{side}.{axle}.valve", owner)
            for side, axle in [(-1, 0), (1, 0), (-1, 1), (1, 1)]] == [1, 2, 3, 4]
    assert native.part_for_feature("contact.-1.0", owner) == 0
    assert native.part_for_feature("unbound.random", owner) is None


@pytest.mark.parametrize("angle", [0, -45, -180, -356.25])
def test_source_demo_spin_is_inverted_before_real_part_attachment(angle):
    center = [-.935, .34, -1.42]
    neutral = [-1., .68, -1.42]
    animated = native.unspin(neutral, center, -math.radians(angle))
    assert native.unspin(animated, center, math.radians(angle)) == pytest.approx(neutral)
    assert native.runtime_point(neutral) == [1.42, .68, -1.]


def test_projector_matches_pinned_source_camera():
    eye = [-3.718625027438753, 2.1, -5.208822122640218]
    assert native.project([0, .77, 0], eye, [0, .77, 0])[:2] == pytest.approx([320, 238])
    assert native.project_many([[0, .77, 0]], eye, [0, .77, 0])[0].tolist() == pytest.approx(
        native.project([0, .77, 0], eye, [0, .77, 0]))


def test_native_publication_is_opt_in_and_has_no_second_image_writer():
    shader = (native.ROOT/"godot_project/render/ascii/features/native_car_publication.glsl").read_text()
    assert "imageStore" not in shader
    assert "atomicOr(admissions.values[previous-1u],2u)" in shader
    assert "distance_from_depth" in shader
    assert "distance_from_depth(glyph.depth.x)" not in shader
    assert "ink_depths.values[start+field_pixel]" in shader
    assert "depth_witnesses.values[index]" in shader
    assert "atomicMax(max_gap,floatBitsToUint(gap))" in shader
    # Witnesses explain an unchanged whole-ink predicate; they must not turn
    # rejected fragments into visible ink or relax the physical depth bound.
    assert "if(invalid || gap>0.0)" in shader
    assert "admissions.values[index]=refused==0u?1u:0u" in shader
    gd = (native.ROOT/"godot_project/render/ascii/features/NativeCarForeground.gd").read_text()
    assert '--fl4831-cresta-native-review' in gd
    assert 'FontScript.load_repertoire' in gd
    assert 'supports_subject(subject)' in gd
    assert 'AssetsScript.load_assets(subjects)' in gd
    assert 'DIAGNOSTIC_NOT_ACCEPTED' in gd
    assert 'CRESTA_NATIVE_DEPTH_REVIEW' in gd


def test_distinct_native_ink_contributors_are_not_flattened_to_the_glyph_mean():
    owner = json.loads(native.OWNER.read_text())
    pixels = np.array([[0, 0], [1, 0], [2, 0]])
    guides, inferred, refused = native.ink_depth_guides(pixels, 3, {"x": 100, "y": 100}, [
        {"feature": "body.front", "ink": [0], "world": [[0, 0, -4]]},
        {"feature": "body.rear", "ink": [2], "world": [[0, 0, -8]]},
    ], owner, {}, 0, [0, 0, 0], [0, 0, -1], [])
    decoded = list(struct.iter_unpack("<HBBfff", base64.b64decode(guides)))
    assert decoded == [(0, 0, 0, 4., 0., 0.), (1, 0, 0, 4., 0., 0.), (2, 0, 0, 8., 0., 0.)]
    assert inferred == 1 and refused == 0


def test_every_native_ink_pixel_has_one_explicit_depth_attachment():
    owner = json.loads(native.OWNER.read_text())
    bank = json.loads((native.ROOT/owner["output"]).read_text())
    assert bank["schema"].endswith(".v2")
    font_base = native.ROOT/"godot_project/assets/glyphs/true_ff/saitamaar"
    catalog = json.loads((font_base/"catalog.json").read_text())
    atlas = np.asarray(Image.open(font_base/"atlas.png").convert("L"))
    masks = {}
    for metric in catalog["glyphs"]:
        x0, y0, x1, y1 = metric["atlas_rect"]
        masks[metric["codepoint"]] = set(np.flatnonzero(atlas[y0:y1, x0:x1].reshape(-1) < 128).tolist())
    for view in bank["views"]:
        for glyph in view["glyphs"]:
            guides = list(struct.iter_unpack("<HBBfff", base64.b64decode(glyph["ink_guides_b64"], validate=True)))
            assert len(guides) == len(masks[glyph["cp"]])
            assert {g[0] for g in guides} == masks[glyph["cp"]]
            assert all(0 <= g[1] <= 4 and g[2] in (0, 1, 2, 255) and all(math.isfinite(x) for x in g[3:]) for g in guides)
            assert sum(g[2] == 255 for g in guides) == glyph["ink_physical_support_refused_count"]
    # Preserve the discovered disputed kana passage; never silently stretch
    # its physical plate or relabel unsupported ink as successfully bound.
    rejected = [(v["sample"], i, g["source_text"]) for v in bank["views"] for i, g in enumerate(v["glyphs"])
                if g["ink_physical_support_refused_count"]]
    assert rejected == [(20, 354, "ﾎ")]


def test_native_mask_domain_is_resolved_before_channels_and_raster_not_overridden_late():
    raster = (native.ROOT/"godot_project/render/ascii/raster/ascii_compositor.glsl").read_text()
    law = (native.ROOT/"godot_project/render/ascii/authority/native_feature_presentation.glslinc").read_text()
    assert "native_presentation_mask_reference(pos)" in raster
    assert raster.index("native_presentation_feature_channels(channels, published_mask)") < raster.index("glyph = int(channels.glyph)")
    assert "car_native_ink" not in raster and "intensity = 1.0;" not in raster
    assert "NATIVE_MASK_REFUSED" in law and "NATIVE_FEATURE_PUBLICATION_ABI" in law
    assert "NativeMaskPlan" in law
    # Existing pixel authority remains the color-channel contract, unchanged.
    old_law = (native.ROOT/"godot_project/render/ascii/authority/native_presentation_authority.glslinc").read_text()
    assert "pixel authority can select color channels, never glyph geometry" in old_law


def test_enabled_native_compositor_shader_compiles():
    from scripts import fl4895_glsl_check as compiler
    source = compiler.expand(native.ROOT/"godot_project/render/ascii/raster/ascii_compositor.glsl")
    source = "#version 450\n#define FL4831_CRESTA_NATIVE_REVIEW\n"+source
    result = subprocess.run(["glslangValidator", "-V", "--stdin", "-S", "comp", "-o", "/dev/null"],
                            input=source, text=True, capture_output=True)
    assert result.returncode == 0, (result.stdout+result.stderr)[-3000:]


def test_complete_bank_preserves_native_choices_and_source_keyview_positions():
    owner = json.loads(native.OWNER.read_text())
    bank = json.loads((native.ROOT/owner["output"]).read_text())
    assert bank["subject"] == "cresta_gx71" and len(bank["views"]) == 96
    index = json.loads(native.INDEX.read_text())
    assert native.digest(native.ROOT/owner["output"]) == index["cars"]["cresta_gx71"]["sha256"]
    assert bank["binding_sha256"] == native.digest(native.OWNER)
    construction_path = native.ROOT/owner["construction"]
    construction = json.loads(construction_path.read_text())
    wheels = {part["prefix"]: part["center"] for part in construction["wheel_motion"]["parts"]}
    for view in bank["views"]:
        sample = view["sample"]
        frame_path = construction_path.parent/"frames"/f"{sample:03}.json"
        frame = json.loads(frame_path.read_text())
        assert native.digest(frame_path) == bank["source_records"][sample]["sha256"]
        assert len(view["glyphs"]) == len(frame["glyphs"])
        summary = frame["summary"]
        phase = math.radians(summary["wheel_phase_degrees"])
        for record, original in zip(view["glyphs"], frame["glyphs"]):
            assert record["source_text"] == original[0]
            assert chr(record["cp"]) == original[0][0]
            screen = [0., 0.]
            weight = 0
            for part, x, y, z, count, motion in record["support"]:
                point = [z, y, -x]
                if motion == 2:
                    point = native.unspin(point, wheels[native.PART_PREFIXES[part]], -phase)
                at = native.project(point, summary["eye"], summary["target"])
                for j in range(2):
                    screen[j] += at[j]*count
                weight += count
            assert weight > 0
            assert [round(screen[j]/weight+record["offset"][j]) for j in range(2)] == original[1:3]
            assert record["source_depth"] > 0


def test_bank_is_not_a_flat_whole_car_billboard_and_valves_are_neutral():
    owner = json.loads(native.OWNER.read_text())
    bank = json.loads((native.ROOT/owner["output"]).read_text())
    construction_path = native.ROOT/owner["construction"]
    construction = json.loads(construction_path.read_text())
    paths = {path["id"]: path["points"] for path in construction["paths"]}
    frame = json.loads((construction_path.parent/"frames/000.json").read_text())
    summary = frame["summary"]
    axes = native.camera_arrays(summary["eye"], summary["target"])
    shift = axes[0]*.25
    translated_eye = [summary["eye"][j]+shift[j] for j in range(3)]
    translated_target = [summary["target"][j]+shift[j] for j in range(3)]
    displacements = []
    for record in bank["views"][0]["glyphs"]:
        if not all(s[0] == 0 for s in record["support"]):
            continue
        source = [0., 0.]
        for support in record["support"]:
            _, x, y, z, weight, _ = support
            point = [z, y, -x]
            a = native.project(point, summary["eye"], summary["target"])
            b = native.project(point, translated_eye, translated_target)
            source[0] += (b[0]-a[0])*weight
            source[1] += weight
        displacements.append(source[0]/source[1])
    assert max(displacements)-min(displacements) > 8, "single-plane text would have no depth parallax"
    checked = 0
    for view in bank["views"]:
        for record in view["glyphs"]:
            for feature, support in zip(record["features"], record["support"]):
                if not feature.endswith(".valve"):
                    continue
                part, x, y, z, _, motion = support
                assert motion == 2
                assert part == native.part_for_feature(feature, owner)
                point = [z, y, -x]
                for j in range(3):
                    assert min(p[j] for p in paths[feature])-1e-7 <= point[j] <= max(p[j] for p in paths[feature])+1e-7
                checked += 1
    assert checked > 30


def test_native_atlas_replay_matches_all_approved_keyview_pixels():
    owner = json.loads(native.OWNER.read_text())
    bank = json.loads((native.ROOT/owner["output"]).read_text())
    base = (native.ROOT/owner["construction"]).parent/"frames"
    font_base = native.ROOT/"godot_project/assets/glyphs/true_ff/saitamaar"
    catalog = json.loads((font_base/"catalog.json").read_text())
    glyphs = {g["codepoint"]: g for g in catalog["glyphs"]}
    atlas = np.asarray(Image.open(font_base/"atlas.png").convert("L"))
    for view in bank["views"]:
        frame = json.loads((base/f'{view["sample"]:03}.json').read_text())
        page = np.full((476, 640), 255, np.uint8)
        for glyph, original in zip(view["glyphs"], frame["glyphs"]):
            metric = glyphs[glyph["cp"]]
            ax, ay, ax1, ay1 = metric["atlas_rect"]
            ix, iy, _, _ = metric["ink_bounds"]
            crop = atlas[ay:ay1, ax:ax1]
            x, y = original[1]+ix, original[2]+iy
            target = page[y:y+crop.shape[0], x:x+crop.shape[1]]
            np.minimum(target, crop, out=target)
        approved = np.asarray(Image.open(base/f'{view["sample"]:03}.png').convert("L"))
        assert np.array_equal(page, approved), f'native atlas drift sample={view["sample"]}'
