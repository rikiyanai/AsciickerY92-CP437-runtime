"""FL-4907 MANUAL_LAUNCH_METAL_FAILURES_FIXED_20260930.

Offline guards for the four failure classes of the operator's manual launch
(Godot 4.6.2, Metal, Apple M3 Pro):

1. every RD shader stage under render/ascii compiles GLSL -> SPIR-V -> MSL ->
   Metal AIR (scripts/fl4895_glsl_check.py --metal); skipped only when
   glslangValidator, spirv-cross or the xcrun metal toolchain is absent;
2. each host push-constant payload equals the shader's reflected block size;
3. a pipeline the backend rejects fails closed (no null pipeline is bound),
   and uniform sets are freed only while still live;
4. every glyph the HUD producers, the run table and the roles can emit is in
   the private Saitamaar catalog's admitted vocabulary.

Compile and source checks only; no Godot launch.
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import fl4895_glsl_check as glsl  # noqa: E402

GODOT = ROOT / "godot_project"
FEATURES = GODOT / "render" / "ascii" / "features"
CATALOG = GODOT / "assets" / "glyphs" / "true_ff" / "saitamaar" / "catalog.json"
LANGUAGE = GODOT / "assets" / "glyphs" / "true_ff" / "outline_language.v1.json"
HUD_PRODUCERS = [
    GODOT / "scripts" / "ui" / "StatusHUD.gd",
    GODOT / "scripts" / "ui" / "ChatLog.gd",
    GODOT / "scripts" / "ui" / "CombatReticle.gd",
    GODOT / "scripts" / "ui" / "InventoryPanel.gd",
    GODOT / "scripts" / "PauseMenu.gd",
    GODOT / "scripts" / "ControlsRegistry.gd",
]
# Strings in HUD producers that only reach engine Controls or the window
# title, never TrueFFHudText: StatusHUD debug window title (U+1F4F7, U+2705),
# PauseMenu Control title (U+23F8) and Control key/badge labels (U+2014; the
# FF menu publishes badge text only from a fixed ASCII set).
CONTROL_ONLY = {0x1F4F7, 0x2705, 0x23F8, 0x2014}

TOOLS = glsl.metal_tools()
needs_metal = pytest.mark.skipif(not TOOLS, reason="glslangValidator, spirv-cross or xcrun metal absent")


def _shaders():
    return glsl.all_shaders(metal=True)


@needs_metal
@pytest.mark.parametrize("res", _shaders())
def test_every_render_ascii_shader_stage_compiles_to_metal(res):
    results = glsl.metal_check(res, TOOLS)
    assert results, res
    failures = [(stage, message) for stage, ok, message, _ in results if not ok]
    assert not failures, failures


def test_metal_gate_covers_the_feature_and_raster_stages():
    shaders = _shaders()
    # traverse ships as true_ff_feature_traverse.glslinc, included by the
    # tested demand/dispatch stages; no standalone traverse stage exists.
    for name in ["extract", "sites", "visibility", "scene", "native", "owed", "layout", "glyphs"]:
        assert f"render/ascii/features/true_ff_feature_{name}.glsl" in shaders
    assert "render/ascii/structure/true_ff_gbuffer.glsl" in shaders
    assert set(glsl.stages(GODOT / "render/ascii/structure/true_ff_gbuffer.glsl")) == {"vertex", "fragment"}


def test_no_glsl_function_shadows_a_metal_standard_name():
    # metal::level (the manual-launch failure) and siblings that SPIR-V-Cross
    # emits under `using namespace metal`.
    reserved = {"level", "bias", "gradient2d", "min_lod_clamp", "access", "sample", "coord",
                "address", "filter", "compare_func", "mip_filter", "texture", "sampler"}
    offenders = []
    for path in sorted((GODOT / "render" / "ascii").rglob("*.glsl*")):
        for match in re.finditer(r"^\s*\w+\s+(\w+)\s*\([^;{]*\)\s*\{", path.read_text(encoding="utf-8"), re.M):
            if match.group(1) in reserved:
                offenders.append(f"{path.relative_to(GODOT)}:{match.group(1)}")
    assert not offenders


def _top_level_items(text, start):
    """Number of top-level comma-separated items of the [...] at text[start]."""
    depth, items = 0, 1
    for ch in text[start:]:
        if ch in "[(":
            depth += 1
        elif ch in "])":
            depth -= 1
            if depth == 0:
                return items
        elif ch == "," and depth == 1:
            items += 1
    raise AssertionError("unbalanced push array")


@needs_metal
def test_host_push_constants_match_reflected_sizes():
    reflected = {}
    for name in ["extract", "sites", "visibility", "native", "owed", "layout", "glyphs"]:
        res = f"render/ascii/features/true_ff_feature_{name}.glsl"
        variants = glsl.metal_check(res, TOOLS)
        assert variants, res
        failures = [(stage, message) for stage, ok, message, _ in variants if not ok]
        assert not failures, (res, failures)
        push_sizes = {push for _, _, _, push in variants}
        assert len(push_sizes) == 1, (res, variants)
        reflected[name] = push_sizes.pop()
    passes = (FEATURES / "TrueFFFeaturePasses.gd").read_text(encoding="utf-8")
    # Every PASSES push is a 4-element int array converted by _bind.
    pushes = [_top_level_items(passes, m.end()) for m in
              re.finditer(r"_dispatch_(?:indirect|host_count)\(\s*list,\s*\w+_PATH,\s*(?=\[)", passes)]
    assert len(pushes) >= 10 and all(n == 4 for n in pushes), pushes
    host_passes = 4 * 4
    for name in ["extract", "sites", "visibility"]:
        assert reflected[name] == host_passes, name
    renderer = (FEATURES / "TrueFFFeatureRenderer.gd").read_text(encoding="utf-8")
    layout_body = renderer.split("func _layout_push", 1)[1].split("\nfunc ", 1)[0]
    words = sum(len(arr.split(",")) for arr in re.findall(r"Packed(?:Int|Float)32Array\(\[([^\]]*)\]",
                                                            layout_body, re.S))
    layout_submit = renderer.split("func _layout(", 1)[1].split("\nfunc ", 1)[0]
    assert 'push.append_array(PackedInt32Array([overflow_word, _crest_idiom, 0, 0])' in layout_submit
    assert reflected["layout"] == (words + 4) * 4 == 96
    for name, function, expected in [("native", "_dispatch_native_certificate", 80),
                                     ("owed", "_dispatch_owed", 64)]:
        body = passes.split(f"func {function}(", 1)[1].split("\nfunc ", 1)[0]
        arrays = re.findall(r"Packed(?:Int|Float)32Array\((?=\[)", body)
        sizes = [_top_level_items(body, match.end()) for match in
                 re.finditer(r"Packed(?:Int|Float)32Array\((?=\[)", body)]
        assert len(arrays) == len(sizes) and sizes
        assert reflected[name] == sum(sizes) * 4 == expected
    paint_body = renderer.split("func _paint", 1)[1].split("\nfunc ", 1)[0]
    start = re.search(r"var push: PackedByteArray = PackedInt32Array\((?=\[)", paint_body).end()
    # sizes + phase (FL-4907 SILENT_ZERO_OUTPUT_TRACE_20260930: HUD paper, then ink).
    assert reflected["glyphs"] == _top_level_items(paint_body, start) * 4 == 32


def test_pipeline_factory_fails_closed_on_backend_rejection():
    factory = (GODOT / "render/core/device/RenderPipelineFactory.gd").read_text(encoding="utf-8")
    body = factory.split("static func compile_compute", 1)[1]
    assert "if not pipeline_rid.is_valid():" in body
    assert "rd.free_rid(shader_rid)" in body
    assert '"pipeline": pipeline_rid' in body


def test_passes_configure_latches_a_failure_and_never_binds_a_null_pipeline():
    passes = (FEATURES / "TrueFFFeaturePasses.gd").read_text(encoding="utf-8")
    configure = passes.split("func configure", 1)[1].split("\nfunc ", 1)[0]
    assert "if not _configure_failure.is_empty():" in configure
    assert 'program.is_empty() or not program["pipeline"].is_valid()' in configure
    assert "func failure() -> String:" in passes
    execute = passes.split("func execute(", 1)[1].split("\nfunc ", 1)[0]
    assert 'not _prepared or _rd == null or not _failure.is_empty()' in execute
    reconcile = passes.split("func reconcile_publication(", 1)[1].split("\nfunc ", 1)[0]
    assert "if _prepared_sets_valid():" in reconcile
    assert reconcile.index("if _prepared_sets_valid():") < reconcile.index("_ensure_sets(")
    renderer = (FEATURES / "TrueFFFeatureRenderer.gd").read_text(encoding="utf-8")
    render = renderer.split("func render(", 1)[1].split("\nfunc ", 1)[0]
    # No session latch: prepare_language retries setup every call, so a stale
    # _latched value must never fail steady-state frames (FL-4877).
    assert "if not _latched.is_empty():" not in render
    fail = renderer.split("func _fail(", 1)[1]
    assert "if not _warned.has(reason):" in fail


def test_uniform_sets_are_freed_only_while_live():
    renderer = (FEATURES / "TrueFFFeatureRenderer.gd").read_text(encoding="utf-8")
    for name in ["_layout_set", "_glyph_set"]:
        assert f"if {name}.is_valid():" not in renderer
        assert f"not {name}.is_valid()" not in renderer
    release = renderer.split("func _release_page", 1)[1].split("\nfunc ", 1)[0]
    assert "_rd.uniform_set_is_valid(rid)" in release
    assert release.index("uniform_set_is_valid") < release.index("_page_owned")
    passes = (FEATURES / "TrueFFFeaturePasses.gd").read_text(encoding="utf-8")
    assert "if _rd.uniform_set_is_valid(s):" in passes


def _catalog():
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def _hud_vocabulary(catalog):
    admitted = set(catalog["candidate_admission"]["codepoints"])
    return {g["codepoint"] for g in catalog["glyphs"] if g["has_ink"]
            and (0x21 <= g["codepoint"] <= 0x7E or g["codepoint"] in admitted)}


def test_catalog_admits_the_middle_dot_and_keeps_the_crest_glyph():
    catalog = _catalog()
    codepoints = {g["codepoint"] for g in catalog["glyphs"]}
    assert 0x00B7 in codepoints and 0x2248 in codepoints
    vocabulary = _hud_vocabulary(catalog)
    assert 0x00B7 in vocabulary and 0x2248 in vocabulary


def _string_literals(path):
    text = path.read_text(encoding="utf-8")
    code = "\n".join(line.split("#", 1)[0] if not line.lstrip().startswith("#") else ""
                     for line in text.splitlines())
    return re.findall(r'"((?:[^"\\\n]|\\.)*)"', code)


def test_every_hud_producer_glyph_is_in_the_hud_vocabulary():
    vocabulary = _hud_vocabulary(_catalog())
    missing = {}
    for path in HUD_PRODUCERS:
        for literal in _string_literals(path):
            for ch in literal:
                cp = ord(ch)
                if cp in (0x20, 0x3000) or cp < 0x20 or cp in CONTROL_ONLY:
                    continue
                if cp not in vocabulary:
                    missing.setdefault(f"U+{cp:04X}", path.name)
    assert not missing, missing


def test_every_run_table_and_role_glyph_is_in_the_catalog():
    codepoints = {g["codepoint"] for g in _catalog()["glyphs"]}
    language = json.loads(LANGUAGE.read_text(encoding="utf-8"))
    missing = set()
    for row in language["run_table"]["rows"]:
        for text in (row["sequence"], row["substitute"]):
            missing |= {f"U+{ord(ch):04X}" for ch in text if ord(ch) not in codepoints}
    assert not missing, sorted(missing)
