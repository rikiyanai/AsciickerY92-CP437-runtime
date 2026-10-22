#!/usr/bin/env python3
"""FL-4888 / FL-4873 round 4: every recording compile site's variant digest is
re-derived by the headed runner from snapshot bytes.

For each site the EXPECTED digest is computed here by a transcription of the
GDScript, step by step, written independently of the runner's mirror
(scripts/run_fl4359_headed_movement.py _variant_stage_candidates). The inputs
are the REAL shader files of this repository (and their transitive
#include files), committed into a temporary git repository whose preserved
snapshot the runner reads. A negative case flips one byte of the shader in
that repository: the recorded digest then must not verify.

GDScript facts the transcription relies on (read from source, not executed;
Godot is never launched here):
  RenderShaderSource.load_source(path): FileAccess.get_file_as_string(path);
    split("\\n"); every line whose strip_edges() begins with "#include" is
    replaced by load_source(target) where target = the line with "#include"
    trimmed and strip_edges()d, which must be '"res://..."'; join("\\n").
  FileAccess.get_file_as_string: UTF-8 decode; a leading BOM is dropped; CR is
    kept. PINNED: no .glsl under godot_project/ has a BOM, a CR, or invalid
    UTF-8 (test_repo_shader_encoding_is_pinned), so both readings agree.
  String.strip_edges(): removes chars with code <= 32 at both ends.
  String.find / substr(from, len) / replace: code-point indices; replace
    replaces every occurrence left to right (Python str semantics).
  String.sha256_text(): SHA-256 of the UTF-8 bytes (water_flow_field.glsl
    carries non-ASCII text, so this is exercised).
  record_compiled_variant: stage sha = sha256_text(stage text); stage names
    Array.sort()ed (code-point order: "fragment" < "vertex"); variant sha =
    sha256_text("".join("<stage>:<sha>\\n")).
  RenderPipelineFactory.inject_defines(source, defines): unchanged when
    defines is empty or the source does not begin with "#version" or has no
    "\\n"; else "#define D\\n" per define inserted after the first line.

Run: python3 -m pytest scripts/tests/test_fl4888_variant_rederivation.py -q
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "recording"))

import launch_fl4359_manual_play as launcher  # noqa: E402
import run_fl4359_headed_movement as runner  # noqa: E402

WS = "".join(chr(c) for c in range(33))


# ---------------------------------------------------------------- GDScript transcriptions

def gd_file(res: str, root: Path) -> str:
    return (root / "godot_project" / res[len("res://"):]).read_bytes().decode("utf-8")


def gd_load_source(res: str, root: Path, ancestors: tuple = ()) -> str:
    """RenderShaderSource.load_source, line by line."""
    assert res.startswith("res://") and res not in ancestors
    lines = gd_file(res, root).split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip(WS)
        if stripped.startswith("#include"):
            target = stripped[len("#include"):].strip(WS)
            assert target.startswith('"res://') and target.endswith('"')
            lines[i] = gd_load_source(target[1:-1], root, ancestors + (res,))
    return "\n".join(lines)


def gd_inject_defines(source: str, defines: list[str]) -> str:
    if not defines:
        return source
    nl = source.find("\n")
    if nl < 0 or not source.startswith("#version"):
        return source
    return source[:nl + 1] + "".join(f"#define {d}\n" for d in defines) + source[nl + 1:]


def gd_record(stages: dict[str, str]) -> str:
    shas = {k: hashlib.sha256(v.encode("utf-8")).hexdigest() for k, v in stages.items()}
    return hashlib.sha256("".join(f"{k}:{shas[k]}\n" for k in sorted(shas)).encode("utf-8")).hexdigest()


def site_render_pipeline_factory(res, root, defines):
    source = gd_load_source(res, root)
    source = source.replace("#[compute]\n", "").replace("#[compute]", "")
    return {"compute": gd_inject_defines(source.strip(WS), defines)}


def site_probe_compute(res, root):
    """PerspectiveProbeBackend / ProbeSplatStage._compile_compute_shader."""
    source = gd_load_source(res, root)
    at = source.find("#[compute]")
    return {"compute": source[at + len("#[compute]"):].strip(WS)}


def site_worklist(res, root):
    return {"compute": gd_load_source(res, root).replace("#[compute]", "")}


def site_native_source_capture(res, root):
    return {"compute": gd_file(res, root).replace("#[compute]", "")}


def site_water(res, root):
    text = gd_file(res, root)
    if text.startswith("#["):
        text = text[text.find("\n") + 1:]
    return {"compute": text}


def site_compositor_graphics(res, root, defines):
    source = gd_load_source(res, root)
    v, f = source.find("#[vertex]"), source.find("#[fragment]")
    vertex = source[v + len("#[vertex]"):f].strip(WS)
    fragment = source[f + len("#[fragment]"):].strip(WS)
    return {"vertex": gd_inject_defines(vertex, defines), "fragment": gd_inject_defines(fragment, defines)}


def site_probe_capture_graphics(res, root, specialization, raster, vertex_marks):
    """ProbeCaptureStage.compile_graphics_shader, in its statement order."""
    source = gd_load_source(res, root)
    vp, fp = source.find("#[vertex]"), source.find("#[fragment]")
    vertex = source[vp + len("#[vertex]"):vp + len("#[vertex]") + (fp - vp - len("#[vertex]"))].strip(WS)
    fragment = source[fp + len("#[fragment]"):].strip(WS)
    if specialization:
        fragment = fragment.replace("#version 450", "#version 450\n#define %s 1" % specialization)
    if raster:
        vertex = vertex.replace("#version 450", "#version 450\n#define FL4359_RASTER_WITNESS 1")
        fragment = fragment.replace("#version 450", "#version 450\n#define FL4359_RASTER_WITNESS 1")
    if vertex_marks:
        vertex = vertex.replace("#version 450", "#version 450\n#define FL4359_VERTEX_WITNESS 1")
        fragment = fragment.replace("#version 450", "#version 450\n#define FL4359_VERTEX_WITNESS 1")
    defines = ([specialization] if specialization else []) + (["FL4359_RASTER_WITNESS"] if raster else []) \
        + (["FL4359_VERTEX_WITNESS"] if vertex_marks else [])
    return {"vertex": vertex, "fragment": fragment}, defines


def site_true_ff_gbuffer(res, root):
    source = gd_load_source(res, root)
    split = source.find("#[fragment]")
    return {"vertex": source[:split].replace("#[vertex]", "").strip(WS),
            "fragment": source[split:].replace("#[fragment]", "").strip(WS)}


# ---------------------------------------------------------------- cases (real repo shaders)

CAPTURE = "res://render/backends/perspective/capture/perspective_probe_capture.glsl"
SPLAT = "res://render/backends/perspective/splat/perspective_probe_splat.glsl"


def _cases():
    cases = [
        ("RenderPipelineFactory.compile_compute", "res://render/post/blur/tilt_shift_blur.glsl",
         lambda r: (site_render_pipeline_factory("res://render/post/blur/tilt_shift_blur.glsl", r, []), [])),
        ("RenderPipelineFactory.compile_compute+defines", "res://render/post/blur/tilt_shift_blur.glsl",
         lambda r: (site_render_pipeline_factory("res://render/post/blur/tilt_shift_blur.glsl", r, ["FL_TEST_A"]),
                    ["FL_TEST_A"])),
        ("ProbeSplatStage._compile_compute_shader",
         "res://render/backends/perspective/underlay/perspective_probe_underlay_row_prefilter.glsl",
         lambda r: (site_probe_compute(
             "res://render/backends/perspective/underlay/perspective_probe_underlay_row_prefilter.glsl", r), [])),
        ("ProbeWorklistOrder.initialize", "res://render/backends/perspective/cull/probe_worklist_order.glsl",
         lambda r: (site_worklist("res://render/backends/perspective/cull/probe_worklist_order.glsl", r), [])),
        ("NativeSourceCapture", "res://render/diagnostics/capture/native_source_capture.glsl",
         lambda r: (site_native_source_capture("res://render/diagnostics/capture/native_source_capture.glsl", r), [])),
        ("WaterFlowFieldProducer", "res://scripts/world/water/simulation/water_flow_field.glsl",
         lambda r: (site_water("res://scripts/world/water/simulation/water_flow_field.glsl", r), [])),
        ("RenderPipelineFactory.compile_graphics", "res://render/ascii/structure/true_ff_gbuffer.glsl",
         lambda r: (site_true_ff_gbuffer("res://render/ascii/structure/true_ff_gbuffer.glsl", r), [])),
        ("ASCIICompositorEffect._compile_graphics_shader", CAPTURE,
         lambda r: (site_compositor_graphics(CAPTURE, r, ["FL_TEST_B"]), ["FL_TEST_B"])),
    ]
    for spec in ("", "PROBE_CAPTURE_PAINT_ONLY", "FL4216_REFLECTION_CAPTURE"):
        for raster, vertex_marks in ((False, False), (True, False), (True, True)):
            cases.append((f"ProbeCaptureStage.compile_graphics_shader capture spec={spec or '-'} "
                          f"raster={raster} vertex={vertex_marks}", CAPTURE,
                          lambda r, s=spec, a=raster, b=vertex_marks: site_probe_capture_graphics(CAPTURE, r, s, a, b)))
    cases.append(("ProbeCaptureStage.compile_graphics_shader splat underlay", SPLAT,
                  lambda r: site_probe_capture_graphics(SPLAT, r, "FL4359_UNDERLAY_ROW_GATE", True, False)))
    return cases


CASES = _cases()
EXPECTED_RULES = {
    # With no defines and "#[compute]" as the first line, the factory and the
    # after-marker constructions build the same text: either name is correct.
    "RenderPipelineFactory.compile_compute": {"compute_factory", "compute_after_marker"},
    "ProbeSplatStage._compile_compute_shader": {"compute_after_marker", "compute_factory"},
    "ProbeWorklistOrder.initialize": {"compute_replace", "compute_raw_replace"},
    "NativeSourceCapture": {"compute_raw_replace", "compute_replace"},
    "WaterFlowFieldProducer": {"compute_raw_first_line"},
    "RenderPipelineFactory.compile_graphics": {"graphics_true_ff_gbuffer", "graphics_compositor", "graphics_probe_capture"},
    "ASCIICompositorEffect._compile_graphics_shader": {"graphics_compositor"},
    "ProbeCaptureStage.compile_graphics_shader": {"graphics_probe_capture", "graphics_compositor",
                                                  "graphics_true_ff_gbuffer"},
}


def _includes(res: str, seen: set) -> None:
    if res in seen:
        return
    seen.add(res)
    for line in gd_file(res, ROOT).split("\n"):
        stripped = line.strip(WS)
        if stripped.startswith("#include"):
            _includes(stripped[len("#include"):].strip(WS)[1:-1], seen)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture(scope="module")
def shader_repo(tmp_path_factory):
    """A git repository holding the real shaders (and includes) at their paths."""
    repo = tmp_path_factory.mktemp("shaders") / "repo"
    needed: set = set()
    for _name, res, _build in CASES:
        _includes(res, needed)
    for res in needed:
        target = repo / "godot_project" / res[len("res://"):]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "godot_project" / res[len("res://"):], target)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "fixture")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "real shaders")
    return repo


@pytest.fixture()
def in_repo(shader_repo, monkeypatch):
    monkeypatch.setattr(launcher, "ROOT", shader_repo)
    monkeypatch.setattr(runner, "ROOT", shader_repo)
    monkeypatch.setattr(runner, "GODOT_PROJECT", shader_repo / "godot_project")
    return shader_repo


def _snapshot(repo: Path, root: Path) -> Path:
    state = launcher._runtime_source_state(root, ("godot_project",))
    return repo / state["snapshot"]


def _recorded_variant(stages: dict[str, str], defines: list[str], res: str) -> dict:
    shas = {k: hashlib.sha256(v.encode("utf-8")).hexdigest() for k, v in stages.items()}
    return {"path": res, "defines": defines, "stages": shas, "sha256": gd_record(stages), "compile_count": 1,
            "superseded_sha256": []}


def test_repo_shader_encoding_is_pinned() -> None:
    """The BOM/CR/UTF-8 behaviour of get_file_as_string cannot be exercised
    without Godot; every shader is free of those cases, so it does not matter."""
    for path in (ROOT / "godot_project").rglob("*.glsl"):
        if ".godot" in path.parts:
            continue
        data = path.read_bytes()
        assert not data.startswith(b"\xef\xbb\xbf") and b"\r" not in data, path
        data.decode("utf-8")


@pytest.mark.parametrize("name,res,build", CASES, ids=[c[0] for c in CASES])
def test_site_variant_is_rederived_from_snapshot_bytes(in_repo, tmp_path, name, res, build) -> None:
    stages, defines = build(ROOT)
    if "ProbeCaptureStage" in name and defines:
        # The marks must actually change the text, or the case proves nothing.
        assert "#define" in stages["fragment"]
    variants = {f"{res}|{','.join(defines)}": _recorded_variant(stages, defines, res)}
    result = runner._verify_compiled_variants(variants, _snapshot(in_repo, tmp_path / "snapshots"))
    row = result["variants"][0]
    assert row["reproduced"] is True, (name, row)
    assert result["status"] == "verified"
    # The digest must be reproduced by THIS site's rule (texts that two sites
    # build identically, e.g. an unmarked probe capture and an undefined
    # compositor shader, may name either).
    assert row["rule"] in EXPECTED_RULES[name.split(" ")[0].split("+")[0]], (name, row["rule"])


@pytest.mark.parametrize("name,res,build", CASES, ids=[c[0] for c in CASES])
def test_one_changed_byte_is_not_verified(in_repo, tmp_path, name, res, build) -> None:
    stages, defines = build(ROOT)
    variants = {f"{res}|{','.join(defines)}": _recorded_variant(stages, defines, res)}
    target = in_repo / "godot_project" / res[len("res://"):]
    original = target.read_bytes()
    try:
        # One byte inside the executed text: the last "0" digit (every shader has one) becomes "1".
        at = original.rfind(b"0")
        target.write_bytes(original[:at] + b"1" + original[at + 1:])
        snapshot = _snapshot(in_repo, tmp_path / "snapshots")
    finally:
        target.write_bytes(original)
    result = runner._verify_compiled_variants(variants, snapshot)
    assert result["variants"][0]["reproduced"] is not True, name
    assert result["status"] != "verified"
