"""Focused path-override contracts for the FL-4676 and FL-4683 compilers."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import jsonschema
import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SCHEMA = ROOT / "assets/glyphs/schema/fl4683.glyph_program_pin.v1.schema.json"
FACT_COMPILER = ROOT / "scripts/compile_fl4676_glyph_facts.py"
PROGRAM_COMPILER = ROOT / "scripts/compile_fl4683_glyph_program.py"
PRODUCTION_RECEIPT = ROOT / "assets/glyphs/generated/fl4683.glyph_program_pin.v1.json"


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def fact_compiler():
    return _module("fl4512_fact_preview_test", FACT_COMPILER)


@pytest.fixture(scope="module")
def program_compiler():
    return _module("fl4512_program_preview_test", PROGRAM_COMPILER)


def _production_receipt_fixture() -> dict:
    """Use the current committed receipt as the preview-schema fixture."""
    receipt = json.loads(PRODUCTION_RECEIPT.read_text(encoding="utf-8"))
    assert receipt["version"] == 7
    assert receipt["sidecar"]["format_version"] == 7
    assert "no_match_policy_contract" in receipt["harri_selector_contract"]
    receipt["runtime_rights"] = {
        "serialized_sidecar": {
            "runtime_consumption_allowed": False,
            "header_byte_offset": 160,
            "header_byte_value": 0,
        },
        "declared_future_rights": [
            "score_compiler_emitted_costs",
            "select_fact_id_from_scene_fact",
        ],
        "product_startup_promotion": {
            "enabled": False,
            "owner_source": "apps/fl4512-runtime/platform/fl4693_product_glyph_program.cpp",
            "owner_symbol": "Fl4693ProductGlyphProgramStartV1",
            "requires_loader_status": [
                "loaded",
                "loaded_without_local_relation_cache",
            ],
            "runtime_consumption_allowed_after_promotion": True,
            "runtime_rights_after_promotion": [
                "score_compiler_emitted_costs",
                "select_fact_id_from_scene_fact",
            ],
            "enabled_by": [],
            "scope_ceiling": "static_source_contract_only",
        },
        "loader_call_site_measurement": {
            "entry_symbol": "Fl4683GlyphProgramLoadV1",
            "measured_call_site_count": 0,
            "call_sites": [],
            "scope_ceiling": "static_gate_reachability_only",
        },
        "selector_call_path": {
            "enabled": False,
            "steps": [],
            "scope_ceiling": "static_source_path_only",
        },
        "compile_time_gate": "FL4683_GLYPH_PROGRAM_LOADER_ENABLED",
        "compile_time_gate_default": "off",
    }
    return receipt


def test_authoring_program_store_paths_are_persistent_and_contained(program_compiler, monkeypatch, tmp_path):
    monkeypatch.setattr(program_compiler, "ROOT", tmp_path)
    store = tmp_path / "assets/glyphs/generated/fl4512_authoring"
    store.mkdir(parents=True)
    good = Path("assets/glyphs/generated/fl4512_authoring/transaction-one/program.bin")
    assert program_compiler.authoring_path(good, "--authoring-output") == good
    for bad in ("assets/glyphs/generated/fl4683.glyph_program.v1.bin",
                "assets/glyphs/generated/fl4512_authoring/program.bin",
                "assets/glyphs/generated/fl4512_authoring/../canonical/program.bin",
                str(tmp_path / good)):
        with pytest.raises(program_compiler.CompileError):
            program_compiler.authoring_path(bad, "--authoring-output")
    outside = tmp_path / "outside"
    outside.mkdir()
    (store / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(program_compiler.CompileError):
        program_compiler.authoring_path(store.relative_to(tmp_path) / "escape/program.bin", "--authoring-output")


def test_preview_paths_are_relative_run_paths_and_default_identity_is_unchanged(
    fact_compiler, program_compiler, monkeypatch, tmp_path: Path
) -> None:
    assert fact_compiler.path_identity(fact_compiler.OUTPUT) == (
        "assets/glyphs/generated/fl4676.glyph_fact.v1.json"
    )
    assert program_compiler.path_identity(program_compiler.SIDECAR_OUTPUT) == (
        "assets/glyphs/generated/fl4683.glyph_program.v1.bin"
    )
    assert fact_compiler.preview_path(".run/facts.json", "--out").as_posix() == (
        ".run/facts.json"
    )
    assert program_compiler.preview_path(".run/program.bin", "--sidecar-out").as_posix() == (
        ".run/program.bin"
    )
    for compiler, option in (
        (fact_compiler, "--out"),
        (program_compiler, "--sidecar-out"),
    ):
        for invalid in ("/tmp/preview.json", "../preview.json", ".run/../preview.json", "assets/preview.json"):
            with pytest.raises(compiler.CompileError):
                compiler.preview_path(invalid, option)
    for compiler, option, suffix in (
        (fact_compiler, "--out", "facts"),
        (program_compiler, "--sidecar-out", "program"),
    ):
        root = tmp_path / suffix
        root.mkdir()
        monkeypatch.setattr(compiler, "ROOT", root)
        outside = tmp_path / f"{suffix}-outside"
        outside.mkdir()
        (root / ".run").symlink_to(outside, target_is_directory=True)
        with pytest.raises(compiler.CompileError):
            compiler.preview_path(".run/file.json", option)
        (root / ".run").unlink()
        (root / ".run").mkdir()
        guarded = root / "assets/glyphs/generated"
        guarded.mkdir(parents=True)
        (root / ".run/escape").symlink_to(outside, target_is_directory=True)
        (root / ".run/guarded").symlink_to(guarded, target_is_directory=True)
        for escaped in (".run/escape/file.json", ".run/guarded/file.json"):
            with pytest.raises(compiler.CompileError):
                compiler.preview_path(escaped, option)


def test_fl4676_source_pin_override_identity_is_resolved_without_full_compile(
    fact_compiler, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fact_compiler, "ROOT", tmp_path)
    sources = {
        name: Path(".run") / f"{name}.json"
        for name in fact_compiler.SOURCES
    }
    for path in sources.values():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(path.name, encoding="utf-8")
    pins = fact_compiler.source_pins({}, sources)
    by_name = {pin["name"]: pin for pin in pins}
    assert by_name["compiled_pool_v1"]["path"] == ".run/compiled_pool_v1.json"
    assert by_name["mesh_admission"]["path"] == ".run/mesh_admission.json"
    assert by_name["compiled_pool_v1"]["sha256"] == fact_compiler.sha_file(
        sources["compiled_pool_v1"]
    )


def test_staged_profile_is_the_hash_authority_for_both_compilers(
    fact_compiler, program_compiler, monkeypatch, tmp_path: Path
) -> None:
    for compiler in (fact_compiler, program_compiler):
        monkeypatch.setattr(compiler, "ROOT", tmp_path)
    profile_path = Path(".run/authoring/profiles.json")
    source = tmp_path / profile_path
    source.parent.mkdir(parents=True)
    source.write_text('{"profiles": []}', encoding="utf-8")
    pool = {"header": {"profile_hash": fact_compiler.sha_file(profile_path)}}
    assert fact_compiler.compile_profile_records(
        {"profiles": []}, pool, profile_path=profile_path
    ) == ([], {}, set())
    program_compiler.validate_pool_profile_pin(pool, profile_path)
    # An edit after pool compilation must fail; neither compiler may silently
    # validate against the unchanged global authored source instead.
    source.write_text('{"profiles": [], "edited": true}', encoding="utf-8")
    with pytest.raises(fact_compiler.CompileError, match="material_profiles"):
        fact_compiler.compile_profile_records({}, pool, profile_path=profile_path)
    with pytest.raises(program_compiler.CompileError, match="material_profiles"):
        program_compiler.validate_pool_profile_pin(pool, profile_path)


def test_staged_profile_requires_a_complete_isolated_compilation(
    fact_compiler, program_compiler
) -> None:
    with pytest.raises(fact_compiler.CompileError, match="profile override requires"):
        fact_compiler.compile_artifact(profile_path=Path(".run/profiles.json"))
    with pytest.raises(program_compiler.CompileError, match="complete preview path tuple"):
        program_compiler.compile_all(profile_path=Path(".run/profiles.json"))


def test_staged_profile_cannot_be_overwritten_by_program_output(
    program_compiler
) -> None:
    for output in ("sidecar_path", "receipt_path"):
        paths = dict(
            glyph_fact_path=Path(".run/facts.json"),
            compiled_pool_path=Path(".run/pool.json"),
            sidecar_path=Path(".run/program.bin"),
            receipt_path=Path(".run/receipt.json"),
            profile_path=Path(".run/profiles.json"),
        )
        paths[output] = paths["profile_path"]
        with pytest.raises(program_compiler.CompileError, match="output_input_collision"):
            program_compiler.compile_all(**paths)


def test_fl4683_identity_join_rejects_missing_or_mismatched_pool(
    program_compiler, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(program_compiler, "ROOT", tmp_path)
    pool_path = Path(".run/pool.json")
    pool_file = tmp_path / pool_path
    pool_file.parent.mkdir(parents=True, exist_ok=True)
    pool_file.write_text("{}", encoding="utf-8")
    pool_sha = program_compiler.sha256_file(pool_path)
    mesh_path = Path(".run/mesh.json")
    (tmp_path / mesh_path).write_text("mesh", encoding="utf-8")
    mesh_sha = program_compiler.sha256_file(mesh_path)
    artifact = {
        "source_pins": [
            {"name": "compiled_pool_v1", "path": ".run/pool.json", "sha256": pool_sha},
            {"name": "mesh_admission", "path": ".run/mesh.json", "sha256": mesh_sha},
        ]
    }
    pool = {"header": {"mesh_admission_artifact_sha256": mesh_sha}}
    program_compiler.validate_artifact_source_identity(artifact, pool, pool_path)
    artifact["source_pins"][0]["sha256"] = "b" * 64
    with pytest.raises(program_compiler.CompileError, match="compiled_pool_sha256"):
        program_compiler.validate_artifact_source_identity(artifact, pool, pool_path)
    artifact["source_pins"][0]["sha256"] = pool_sha
    artifact["source_pins"][0]["path"] = ".run/other-pool.json"
    with pytest.raises(program_compiler.CompileError, match="compiled_pool_path"):
        program_compiler.validate_artifact_source_identity(artifact, pool, pool_path)
    artifact["source_pins"][0]["path"] = ".run/pool.json"
    artifact["source_pins"].append(dict(artifact["source_pins"][0]))
    with pytest.raises(program_compiler.CompileError, match="duplicate_source_pin"):
        program_compiler.validate_artifact_source_identity(artifact, pool, pool_path)
    artifact["source_pins"].pop()
    artifact["source_pins"][1]["path"] = ".run/other-mesh.json"
    with pytest.raises(program_compiler.CompileError, match="mesh_admission:path"):
        program_compiler.validate_artifact_source_identity(artifact, pool, pool_path)
    with pytest.raises(program_compiler.CompileError, match="required input is missing"):
        program_compiler.compile_all(
            glyph_fact_path=Path(".run/missing-facts.json"),
            compiled_pool_path=pool_path,
            sidecar_path=Path(".run/program.bin"),
            receipt_path=Path(".run/program.pin.json"),
        )


def test_fl4683_identity_join_rejects_each_missing_or_wrong_pin_component(
    program_compiler, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(program_compiler, "ROOT", tmp_path)
    pool_path = Path(".run/pool.json")
    mesh_path = Path(".run/mesh.json")
    (tmp_path / ".run").mkdir()
    (tmp_path / pool_path).write_text("{}", encoding="utf-8")
    (tmp_path / mesh_path).write_text("mesh", encoding="utf-8")
    pool_sha = program_compiler.sha256_file(pool_path)
    mesh_sha = program_compiler.sha256_file(mesh_path)
    artifact = {
        "source_pins": [
            {"name": "compiled_pool_v1", "path": ".run/pool.json", "sha256": pool_sha},
            {"name": "mesh_admission", "path": ".run/mesh.json", "sha256": mesh_sha},
        ]
    }
    pool = {"header": {"mesh_admission_artifact_sha256": mesh_sha}}

    def mutate_pin(index: int, **changes):
        def mutate(value: dict) -> None:
            value["source_pins"][index].update(changes)

        return mutate

    cases = (
        (lambda value: value["source_pins"].pop(0), "compiled_pool_v1_pin_missing"),
        (lambda value: value["source_pins"].pop(1), "mesh_admission:pin_missing"),
        (mutate_pin(0, path=".run/other-pool.json"), "compiled_pool_path"),
        (mutate_pin(0, sha256="a" * 64), "compiled_pool_sha256"),
        (mutate_pin(1, path=".run/other-mesh.json"), "mesh_admission:path"),
        (mutate_pin(1, sha256="b" * 64), "mesh_admission_sha256"),
    )
    for mutate, message in cases:
        candidate = copy.deepcopy(artifact)
        mutate(candidate)
        with pytest.raises(program_compiler.CompileError, match=message):
            program_compiler.validate_artifact_source_identity(candidate, pool, pool_path)

    with pytest.raises(
        program_compiler.CompileError, match="mesh_admission:compiled_pool_sha256"
    ):
        program_compiler.validate_artifact_source_identity(
            artifact,
            {"header": {"mesh_admission_artifact_sha256": "c" * 64}},
            pool_path,
        )


def test_production_receipt_defaults_and_preview_receipt_are_schema_valid(
    program_compiler,
) -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    receipt = _production_receipt_fixture()
    validator.validate(receipt)
    assert "artifact_scope" not in receipt
    assert "deployment_eligible" not in receipt
    assert receipt["sidecar"]["path"] == "assets/glyphs/generated/fl4683.glyph_program.v1.bin"
    assert receipt["regeneration"]["command"] == (
        "python3 scripts/compile_fl4683_glyph_program.py --write"
    )

    receipt = copy.deepcopy(receipt)
    receipt["artifact_scope"] = "preview"
    receipt["deployment_eligible"] = False
    receipt["sidecar"]["path"] = ".run/program.bin"
    receipt["scene_address_index"]["address_space_source"] = ".run/pool.json"
    for pin in receipt["pins"]:
        if pin["role"] == 0:
            pin["path"] = ".run/facts.json"
        elif pin["role"] == 8:
            pin["path"] = ".run/pool.json"
    receipt["regeneration"]["command"] = (
        "python3 scripts/compile_fl4683_glyph_program.py --write "
        "--glyph-fact .run/facts.json --compiled-pool .run/pool.json "
        "--sidecar-out .run/program.bin --receipt-out .run/program.pin.json"
    )
    receipt["regeneration"]["recheck_command"] = receipt["regeneration"]["command"].replace(
        " --write ", " --check ", 1
    )
    validator.validate(receipt)
    staged_profile_receipt = copy.deepcopy(receipt)
    for key in ("command", "recheck_command"):
        staged_profile_receipt["regeneration"][key] = (
            staged_profile_receipt["regeneration"][key].replace(
                " --sidecar-out", " --profiles .run/profiles.json --sidecar-out"
            )
        )
    validator.validate(staged_profile_receipt)
    authoring_receipt = copy.deepcopy(staged_profile_receipt)
    authoring_receipt["artifact_scope"] = "authoring"
    authoring_receipt["deployment_eligible"] = True
    authoring_receipt["authoring_profile"] = {
        "path": ".run/profiles.json", "sha256": "a" * 64,
        "activation_owner": "Fl4693ProductProgramPrepareV1",
    }
    for key, mode in (("command", "--write"), ("recheck_command", "--check")):
        authoring_receipt["regeneration"][key] = authoring_receipt["regeneration"][key].replace(
            mode, mode + " --authoring", 1
        )
    validator.validate(authoring_receipt)
    durable_receipt = json.loads(json.dumps(authoring_receipt).replace(
        ".run/", "assets/glyphs/generated/fl4512_authoring/transaction-test/"))
    validator.validate(durable_receipt)
    preview_in_store = copy.deepcopy(durable_receipt)
    preview_in_store["artifact_scope"] = "preview"
    preview_in_store["deployment_eligible"] = False
    del preview_in_store["authoring_profile"]
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(preview_in_store)
    broken = copy.deepcopy(authoring_receipt)
    del broken["authoring_profile"]
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(broken)
    broken = copy.deepcopy(receipt)
    broken["deployment_eligible"] = True
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(broken)
    production = copy.deepcopy(receipt)
    del production["artifact_scope"]
    del production["deployment_eligible"]
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(production)


def test_preview_main_writes_only_override_outputs_and_never_guarded_files(
    fact_compiler, program_compiler, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fact_compiler, "ROOT", tmp_path)
    monkeypatch.setattr(
        fact_compiler,
        "OUTPUT",
        tmp_path / "assets/glyphs/generated/fl4676.glyph_fact.v1.json",
    )
    fact_writes: list[Path] = []
    monkeypatch.setattr(
        fact_compiler,
        "compile_artifact",
        lambda **_: {"glyph_record_count": 0, "fact_count": 0, "canonical_content_sha256": "x"},
    )
    monkeypatch.setattr(
        fact_compiler,
        "write_json",
        lambda path, _value: fact_writes.append(path) or "sha",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "compile_fl4676_glyph_facts.py",
            "--write",
            "--pool",
            ".run/pool.json",
            "--mesh-admission",
            ".run/mesh.json",
            "--out",
            ".run/facts.json",
        ],
    )
    assert fact_compiler.main() == 0
    assert fact_writes == [tmp_path / ".run/facts.json"]
    assert not fact_compiler.OUTPUT.exists()

    monkeypatch.setattr(program_compiler, "ROOT", tmp_path)
    program_writes: list[Path] = []
    monkeypatch.setattr(program_compiler, "compile_all", lambda **_: (b"sidecar", b"receipt"))
    original_write_bytes = Path.write_bytes

    def record_write(path: Path, payload: bytes) -> int:
        program_writes.append(path)
        return original_write_bytes(path, payload)

    monkeypatch.setattr(Path, "write_bytes", record_write)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "compile_fl4683_glyph_program.py",
            "--write",
            "--glyph-fact",
            ".run/facts.json",
            "--compiled-pool",
            ".run/pool.json",
            "--sidecar-out",
            ".run/program.bin",
            "--receipt-out",
            ".run/program.pin.json",
        ],
    )
    assert program_compiler.main() == 0
    assert program_writes == [
        tmp_path / ".run/program.bin",
        tmp_path / ".run/program.pin.json",
    ]
    assert not (tmp_path / "assets/glyphs/generated/fl4683.glyph_program.v1.bin").exists()


def test_preview_output_input_collisions_fail_before_loading_inputs(
    fact_compiler, program_compiler, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fact_compiler, "ROOT", tmp_path)
    monkeypatch.setattr(fact_compiler, "OUTPUT", tmp_path / "guarded.json")
    monkeypatch.setattr(
        fact_compiler,
        "compile_artifact",
        lambda **_: {"glyph_record_count": 0, "fact_count": 0, "canonical_content_sha256": "x"},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "compile_fl4676_glyph_facts.py",
            "--write",
            "--pool",
            ".run/same.json",
            "--mesh-admission",
            ".run/mesh.json",
            "--out",
            ".run/same.json",
        ],
    )
    assert fact_compiler.main() == 2
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "compile_fl4676_glyph_facts.py",
            "--write",
            "--pool",
            ".run/pool.json",
            "--mesh-admission",
            ".run/same.json",
            "--out",
            ".run/same.json",
        ],
    )
    assert fact_compiler.main() == 2
    for output in (".run/facts.json", ".run/pool.json"):
        with pytest.raises(program_compiler.CompileError, match="output_input_collision"):
            program_compiler.compile_all(
                glyph_fact_path=Path(".run/facts.json"),
                compiled_pool_path=Path(".run/pool.json"),
                sidecar_path=Path(output),
                receipt_path=Path(".run/receipt.json"),
            )
    for output in (".run/facts.json", ".run/pool.json"):
        with pytest.raises(program_compiler.CompileError, match="output_input_collision"):
            program_compiler.compile_all(
                glyph_fact_path=Path(".run/facts.json"),
                compiled_pool_path=Path(".run/pool.json"),
                sidecar_path=Path(".run/program.bin"),
                receipt_path=Path(output),
            )
    with pytest.raises(program_compiler.CompileError, match="output_input_collision"):
        program_compiler.compile_all(
            glyph_fact_path=Path(".run/facts.json"),
            compiled_pool_path=Path(".run/pool.json"),
            sidecar_path=Path(".run/program.bin"),
            receipt_path=Path(".run/program.bin"),
        )


def test_preview_cli_requires_every_override_argument(
    fact_compiler, program_compiler, monkeypatch
) -> None:
    cases = (
        (
            fact_compiler,
            ["compile_fl4676_glyph_facts.py", "--write", "--pool", ".run/pool.json"],
        ),
        (
            program_compiler,
            [
                "compile_fl4683_glyph_program.py",
                "--write",
                "--glyph-fact",
                ".run/facts.json",
            ],
        ),
        (
            program_compiler,
            [
                "compile_fl4683_glyph_program.py",
                "--write",
                "--relation-pointer",
                ".run/relation.json",
            ],
        ),
    )
    for compiler, argv in cases:
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit) as exit_info:
            compiler.main()
        assert exit_info.value.code == 2
