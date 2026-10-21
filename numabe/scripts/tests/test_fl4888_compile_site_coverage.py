"""FL-4888 / FL-4873: every first-party RD shader compile records its variant.

Static checks only (no Godot launch):

* every first-party RenderingDevice compile site calls
  RenderShaderSource.record_compiled_variant() right after
  shader_create_from_spirv(), and is named in RECORDED_COMPILE_SITES;
* the only unrecorded sites are the vendored ones listed in
  UNRECORDED_COMPILE_SITES, and their line numbers still point at the compile;
* the registry, the F9 manifest and the external movement receipt carry the
  late-compile evidence fields (first/last compile frame, registry revision,
  final registry, per-frame revision).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GODOT = ROOT / "godot_project"
REGISTRY = GODOT / "render" / "core" / "shaders" / "RenderShaderSource.gd"
RECORDER = GODOT / "scripts" / "recording" / "ScreenRecordingController.gd"
DRIVER = ROOT / "scripts" / "adhoc" / "fl4359_external_movement_driver.gd"

COMPILE_API = re.compile(
    r"\b(shader_compile_spirv_from_source|shader_create_from_spirv|shader_create_from_bytecode|RDShaderFile)\b"
)
CREATE_API = re.compile(r"\bshader_create_from_spirv\s*\(")
FUNC = re.compile(r"^\s*(?:static\s+)?func\s+(\w+)")
# Sites that never run in the recorded game process: GPU test harnesses and
# offline compile oracles. They are not render or recording code.
NON_RUNTIME_PREFIXES = ("godot_project/scripts/tests/", "godot_project/tools/")
VENDORED_PREFIXES = ("godot_project/canxkoz_gpu_port/",)
# How far after shader_create_from_spirv() the record call may sit.
RECORD_WINDOW_LINES = 16


def _code(line: str) -> str:
    """Line text without a trailing GDScript comment (strings here hold no '#')."""
    return line.split("#", 1)[0]


def _res(path: Path) -> str:
    return "res://" + path.relative_to(GODOT).as_posix()


def _function_at(lines: list[str], index: int) -> str:
    for back in range(index, -1, -1):
        match = FUNC.match(lines[back])
        if match:
            return match.group(1)
    return "<top-level>"


def _scan() -> list[dict]:
    sites = []
    files = sorted(GODOT.rglob("*.gd")) + sorted((ROOT / "scripts").rglob("*.gd"))
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        if "/addons/" in rel or "/.godot/" in rel:
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for index, line in enumerate(lines):
            match = COMPILE_API.search(_code(line))
            if not match:
                continue
            sites.append({
                "rel": rel,
                "path": path,
                "line": index + 1,
                "api": match.group(1),
                "function": _function_at(lines, index),
                "lines": lines,
            })
    return sites


def _classify(rel: str) -> str:
    if rel.startswith(NON_RUNTIME_PREFIXES):
        return "non_runtime"
    if rel.startswith(VENDORED_PREFIXES):
        return "vendored"
    return "first_party"


def _registry_text() -> str:
    return REGISTRY.read_text(encoding="utf-8")


def _recorded_site_list() -> set[str]:
    block = _registry_text().split("const RECORDED_COMPILE_SITES := [", 1)[1].split("]", 1)[0]
    return set(re.findall(r'"([^"]+)"', block))


def _unrecorded_site_list() -> list[dict]:
    block = _registry_text().split("const UNRECORDED_COMPILE_SITES := [", 1)[1].split("\n]", 1)[0]
    rows = []
    for match in re.finditer(
        r'\{"path":\s*"([^"]+)",\s*"line":\s*(\d+),\s*"reason":\s*"([^"]+)"', block
    ):
        rows.append({"path": match.group(1), "line": int(match.group(2)), "reason": match.group(3)})
    return rows


def test_scan_finds_the_known_first_party_sites() -> None:
    first_party = {s["rel"] for s in _scan() if _classify(s["rel"]) == "first_party"}
    # Guard the scanner itself: these files are known compile owners.
    for rel in (
        "godot_project/render/core/device/RenderPipelineFactory.gd",
        "godot_project/render/api/ASCIICompositorEffect.gd",
        "godot_project/render/backends/perspective/capture/ProbeCaptureStage.gd",
        "godot_project/scripts/world/water/simulation/WaterFlowFieldProducer.gd",
    ):
        assert rel in first_party


def test_no_first_party_site_uses_an_unrecordable_compile_api() -> None:
    bad = [
        f"{s['rel']}:{s['line']} {s['api']}"
        for s in _scan()
        if _classify(s["rel"]) == "first_party"
        and s["api"] in ("shader_create_from_bytecode", "RDShaderFile")
        and not (
            s["rel"] == "godot_project/render/core/device/RenderPipelineFactory.gd"
            and s["function"] in ("_load_shader_binary", "compile_compute", "compile_graphics")
        )
    ]
    assert bad == [], "compile path that bypasses the text registry: " + ", ".join(bad)


def test_pipeline_factory_bytecode_cache_preserves_source_identity() -> None:
    source = (GODOT / "render/core/device/RenderPipelineFactory.gd").read_text()
    key = source.split("static func _shader_binary_cache_key", 1)[1].split(
        "\nstatic func", 1)[0]
    for identity in ("SHADER_BINARY_CACHE_SCHEMA", 'version.get("string"',
                     'version.get("hash"', "get_device_vendor_name",
                     "get_device_name", "get_device_pipeline_cache_uuid",
                     "source.sha256_text"):
        assert identity in key
    assert 'if pipeline_cache_uuid.is_empty():\n\t\treturn ""' in key
    assert 'if cache_path.is_empty() or not FileAccess.file_exists(cache_path):' in source
    assert 'if cache_path.is_empty() or bytecode.is_empty():' in source
    compile_body = source.split("static func compile_compute(", 1)[1].split(
        "\nstatic func", 1)[0]
    assert compile_body.index("_load_shader_binary") < compile_body.index(
        "shader_compile_spirv_from_source")
    assert compile_body.index("shader_create_from_bytecode") < compile_body.index(
        "shader_create_from_spirv")
    assert compile_body.index("shader_create_from_spirv") < compile_body.index(
        "record_compiled_variant")
    assert compile_body.index("compute_pipeline_create") < compile_body.index(
        "_store_shader_binary")
    assert "_discard_shader_binary(cache_path)" in compile_body
    assert compile_body.index("if cache_hit:") < compile_body.index(
        "_discard_shader_binary(cache_path)")
    graphics = source.split("static func compile_graphics(", 1)[1].split(
        "\nstatic func", 1)[0]
    assert graphics.index("_load_shader_binary") < graphics.index(
        "shader_compile_spirv_from_source")
    assert graphics.index("shader_create_from_bytecode") < graphics.index(
        "shader_create_from_spirv") < graphics.index("record_compiled_variant")


def test_every_first_party_create_records_its_variant_after_creation() -> None:
    missing = []
    for site in _scan():
        if _classify(site["rel"]) != "first_party" or not CREATE_API.search(_code(site["lines"][site["line"] - 1])):
            continue
        lines = site["lines"]
        window = lines[site["line"] - 1 : site["line"] - 1 + RECORD_WINDOW_LINES]
        following_func = next((i for i, text in enumerate(window[1:], 1) if FUNC.match(text)), len(window))
        body = "\n".join(_code(text) for text in window[:following_func])
        if "record_compiled_variant(" not in body:
            missing.append(f"{site['rel']}:{site['line']} ({site['function']})")
    assert missing == [], "first-party compile sites without record_compiled_variant: " + ", ".join(missing)


def test_recorded_site_list_matches_the_source_tree() -> None:
    found = {
        f"{_res(s['path'])}:{s['function']}"
        for s in _scan()
        if _classify(s["rel"]) == "first_party" and s["api"] == "shader_create_from_spirv"
    }
    listed = _recorded_site_list()
    assert found - listed == set(), f"compile sites missing from RECORDED_COMPILE_SITES: {sorted(found - listed)}"
    assert listed - found == set(), f"RECORDED_COMPILE_SITES names absent sites: {sorted(listed - found)}"


def test_unrecorded_sites_are_exactly_the_vendored_compiles() -> None:
    listed = _unrecorded_site_list()
    assert listed, "vendored compile sites must stay visible in UNRECORDED_COMPILE_SITES"
    assert all(row["reason"] == "vendored" for row in listed)
    vendored_compiles = {
        (_res(s["path"]), s["line"])
        for s in _scan()
        if _classify(s["rel"]) == "vendored" and s["api"] == "shader_compile_spirv_from_source"
    }
    listed_pairs = {(row["path"], row["line"]) for row in listed}
    assert listed_pairs == vendored_compiles, (
        f"UNRECORDED_COMPILE_SITES lines drifted: listed={sorted(listed_pairs)} "
        f"actual={sorted(vendored_compiles)}"
    )
    # A vendored site that started recording would no longer be an exception.
    for site in _scan():
        if _classify(site["rel"]) == "vendored":
            assert "record_compiled_variant" not in site["path"].read_text(encoding="utf-8")


def test_registry_carries_compile_frames_and_revision() -> None:
    source = _registry_text()
    assert "static var registry_revision: int" in source
    assert "static func compiled_variant_registry_revision() -> int:" in source
    assert "static func unrecorded_compile_sites() -> Array:" in source
    record = source.split("static func record_compiled_variant(", 1)[1].split("\nstatic func", 1)[0]
    for field in ('"first_compile_frame"', '"last_compile_frame"', '"compile_count"', '"superseded_sha256"'):
        assert field in record
    assert "Engine.get_process_frames()" in record
    # The revision advances under the same lock that publishes the registry.
    locked = record.split("_variants_lock.lock()", 1)[1].split("_variants_lock.unlock()", 1)[0]
    assert "registry_revision += 1" in locked
    assert locked.index("registry_revision += 1") < locked.index("_published_variants =")
    # The first frame of a key is kept on recompiles; the last one moves.
    assert 'previous.get("first_compile_frame", compile_frame)' in record
    reader = source.split("static func compiled_variant_registry_revision()", 1)[1].split("\nstatic func", 1)[0]
    assert "sha256" not in reader and "duplicate" not in reader


def test_recorder_manifest_carries_final_registry_and_per_frame_revision() -> None:
    source = RECORDER.read_text(encoding="utf-8")
    manifest = source.split("func _write_manifest_json(", 1)[1].split("\nfunc ", 1)[0]
    assert '"compiled_variants_final": RenderShaderSourceScript.compiled_variant_hashes()' in manifest
    assert (
        '"compiled_variant_registry_revision_final": '
        "RenderShaderSourceScript.compiled_variant_registry_revision()" in manifest
    )
    # First-frame copy stays where readers already find it.
    assert '"first_frame_view_state": _identity.get("settings", {})' in manifest
    capture = source.split("func _capture_frame_async(", 1)[1].split("\nfunc ", 1)[0]
    assert '"variant_revision": RenderShaderSourceScript.compiled_variant_registry_revision()' in capture
    # One int per frame: the per-frame path never copies or hashes the registry.
    assert "compiled_variant_hashes" not in capture


def test_every_driver_receipt_carries_final_registry() -> None:
    # Live run fl4873_live1_strafe_f10 (ghost tower_strafe) wrote a receipt with
    # no registry because only the movement path set it; the write function
    # itself must attach it so every route (ghost replay, menu, early
    # failure) carries the registry as it stands at write time.
    source = DRIVER.read_text(encoding="utf-8")
    assert 'const RenderShaderSourceScript = preload("res://render/core/shaders/RenderShaderSource.gd")' in source
    body = source.split("func _write_receipt(receipt: Dictionary) -> void:", 1)[1].split("\nfunc ", 1)[0]
    assert 'receipt["compiled_variants"] = RenderShaderSourceScript.compiled_variant_hashes()' in body
    assert ('receipt["compiled_variant_registry_revision"] = '
            "RenderShaderSourceScript.compiled_variant_registry_revision()") in body
    assert 'receipt["unrecorded_compile_sites"] = RenderShaderSourceScript.unrecorded_compile_sites()' in body
    assert body.index("compiled_variants") < body.index("store_string")
