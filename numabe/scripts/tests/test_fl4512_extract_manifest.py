"""FL-4512 extraction manifest front door.

Covers deterministic ordering, stale detection, required inclusions,
exclusions, missing-file rejection, per-path category/reason presence, and
safe materialization boundaries for scripts/fl4512_extract_manifest.py.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "scripts" / "fl4512_extract_manifest.py"
MANIFEST = (
    ROOT
    / "docs"
    / "research"
    / "ascii"
    / "verification"
    / "fl4512"
    / "2026-09-18-fl4512-extraction-manifest.json"
)


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "fl4512_extract_manifest", GENERATOR
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(*args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GENERATOR), *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_fl4512_extract_manifest_names_runtime_scripts_and_classic() -> None:
    result = _run()
    assert result.returncode == 0, result.stderr
    manifest = json.loads(result.stdout)
    files = set(manifest["files"])

    assert manifest["schema"] == "fl4512.extraction_manifest.v2"
    assert "apps/fl4512-runtime/engine/fl4512_active_stroke_owner.cpp" in files
    assert "scripts/compile_fl4512_pools.py" in files
    assert "scripts/presenter_app.py" in files
    assert "scripts/asciid.py" in files
    assert "scripts/a3d/runtime_data_contract.py" in files
    assert "scripts/a3d/compatibility.py" in files
    assert "scripts/classic/app.py" in files
    assert "apps/asciicker-presenter/makefile_asciiid" in files
    assert "apps/asciiid-classic/SKILL.md" in files
    assert "apps/asciiid-classic/makefile_asciiid_classic" in files
    assert "apps/asciiid-classic/CLASSIC_PIN.md" in files
    assert "apps/asciiid-classic/pinned/editor/asciiid.cpp" in files
    assert "apps/fl4512-runtime/platform/fl4693_product_glyph_program.cpp" in files
    assert "apps/fl4512-runtime/platform/fl4683_glyph_program_loader.cpp" in files
    assert "apps/fl4512-runtime/platform/fl4683_glyph_program_loader.h" in files
    assert "scripts/package_fl4512_prism_archive.py" in files
    assert ".run/asciiid" not in files
    assert ".run/asciicker-presenter" not in files


def test_fl4512_extract_manifest_deterministic_ordering() -> None:
    first = _run()
    second = _run()
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    a, b = json.loads(first.stdout), json.loads(second.stdout)
    assert a["files"] == sorted(a["files"])
    assert [e["path"] for e in a["entries"]] == sorted(
        e["path"] for e in a["entries"]
    )
    # Semantic content is identical across runs at one HEAD; only the
    # wall-clock generated_utc stamp may differ.
    for key in (
        "schema",
        "generator_version",
        "source_head",
        "entries",
        "files",
        "counts",
        "categories",
        "excluded_counts",
    ):
        assert a[key] == b[key], key


def test_fl4512_extract_manifest_check_passes_and_detects_stale(
    tmp_path: Path,
) -> None:
    assert _run("--check").returncode == 0
    stale = tmp_path / "stale.json"
    stale.write_text(
        json.dumps(
            {
                "schema": "fl4512.extraction_manifest.v0",
                "generator_version": "0.0.0",
                "source_head": "deadbeef",
                "content_scan": False,
                "entries": [],
            }
        ),
        encoding="utf-8",
    )
    result = _run("--check", "--manifest", str(stale))
    assert result.returncode == 1
    assert "stale" in result.stderr


def test_fl4512_extract_manifest_check_passes_on_head_lag(tmp_path: Path) -> None:
    module = _load_generator()
    fresh = module.build_manifest()
    lagged = dict(fresh)
    lagged["source_head"] = "0" * 40
    candidate = tmp_path / "lagged.json"
    candidate.write_text(json.dumps(lagged), encoding="utf-8")
    assert module.check_manifest(candidate) == 0


def test_fl4512_extract_manifest_required_inclusions() -> None:
    files = set(_manifest()["files"])
    required = [
        # Presenter and Classic wrappers.
        "scripts/presenter_app.py",
        "scripts/asciid.py",
        "scripts/a3d/compatibility.py",
        "scripts/a3d/runtime_data_contract.py",
        "scripts/classic/app.py",
        # Classic makefile and source pin.
        "apps/asciiid-classic/makefile_asciiid_classic",
        "apps/asciiid-classic/CLASSIC_PIN.md",
        "apps/asciiid-classic/PIN_MANIFEST.sha256",
        "apps/asciiid-classic/pinned/editor/asciiid.cpp",
        # FL-4512 C++ owners.
        "apps/fl4512-runtime/engine/fl4512_active_stroke_owner.cpp",
        "apps/fl4512-runtime/engine/fl4512_glyph_transform_relation_v2.cpp",
        # Adjacent glyph-graph/runtime owners without FL-4512 in path.
        "apps/asciicker-presenter/asciiid.cpp",
        # AOT compilers.
        "scripts/compile_fl4512_pools.py",
        "scripts/compile_fl4512_glyph_transform_relation_v2.py",
        # Representative tests.
        "scripts/tests/test_fl4512_extract_manifest.py",
        "tests/engine_tests/fl4512_primitive_field_test.cpp",
        # Harness.
        "scripts/tests/fl4512_active_stroke_owner_harness.cpp",
        # Compiled pool assets.
        "assets/glyphs/generated/cp437.native_atlas_source.v1.json",
        # Fixture maps.
        "assets/glyphs/fixtures/extended_glyph_material_additive_v1.json",
        "assets/a3d/frog_pieta_4x4.a3d",
        # Schemas.
        "assets/glyphs/schema/fl4512.glyph_transform_relation.v2.schema.json",
        "docs/research/ascii/verification/fl4512/paper/2026-07-13-paper-hygiene/receipt.json",
        # Verification evidence.
        "docs/research/ascii/verification/fl4512/2026-09-18-asciiid-runtime-separation-index.md",
        # CI.
        ".github/workflows/fl4512-paper.yml",
        # Build/launcher anchors.
        "Makefile",
        "apps/game-fl4512/makefile_game",
        "apps/game-fl4512/makefile_game_mac",
        "apps/game-classic/makefile_game_classic",
        "apps/game-classic/makefile_game_classic_mac",
        "makefile_game_term",
        "scripts/launcher.py",
    ]
    missing = [p for p in required if p not in files]
    assert missing == []


def test_fl4512_extract_manifest_exclusions() -> None:
    files = _manifest()["files"]
    assert files, "manifest must not be empty"
    for path in files:
        assert not path.startswith(".run/"), path
        assert re.search(r"(^|/)\.[od]_", path) is None, path
        assert ".cache/" not in path, path
        assert "__pycache__" not in path, path
        assert Path(path).name != ".DS_Store", path
        assert not path.startswith(".scratch/"), path
        assert not path.startswith("artifacts/"), path
        assert not path.startswith("graft/"), path
    assert "scripts/asciiid_app.py" not in set(files)


def test_owned_runtime_roots_exclude_finder_metadata(tmp_path: Path, monkeypatch) -> None:
    module = _load_generator()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    for root_name, discover in (
        (module.CURRENT_RUNTIME_ROOT, module.current_runtime_files),
        (module.TERMINAL_RUNTIME_ROOT, module.terminal_runtime_files),
    ):
        root = tmp_path / root_name
        (root / "nested").mkdir(parents=True)
        (root / ".DS_Store").write_bytes(b"Finder metadata")
        (root / "nested/.DS_Store").write_bytes(b"Finder metadata")
        (root / "nested/owner.cpp").write_text("// owned source\n")
        assert discover() == {root_name + "/nested/owner.cpp"}


def test_fl4512_extract_manifest_paths_exist_and_allow_owned_untracked_sources() -> None:
    manifest = _manifest()
    module = _load_generator()
    tracked = set(
        subprocess.check_output(
            ["git", "ls-files"], cwd=ROOT, text=True
        ).splitlines()
    )
    missing_disk = [p for p in manifest["files"] if not (ROOT / p).is_file()]
    assert missing_disk == []
    untracked = [p for p in manifest["files"] if p not in tracked]
    # Three owned working-tree mechanisms: the current-runtime ownership root,
    # the terminal-runtime ownership root, and the terminal source-closure
    # contract anchor, which participates tracked or pre-commit.
    anchors = (
        {module.TERMINAL_CONTRACT_MK}
        if (ROOT / module.TERMINAL_CONTRACT_MK).is_file()
        else set()
    )
    assert set(untracked) == (
        module.current_runtime_files() | module.terminal_runtime_files() | anchors
    ) - tracked
    assert all(
        p == module.CURRENT_RUNTIME_ROOT
        or p.startswith(module.CURRENT_RUNTIME_ROOT + "/")
        or p == module.TERMINAL_RUNTIME_ROOT
        or p.startswith(module.TERMINAL_RUNTIME_ROOT + "/")
        or p == module.TERMINAL_CONTRACT_MK
        for p in untracked
    )


def test_fl4512_extract_manifest_discovers_untracked_current_runtime_source(tmp_path: Path, monkeypatch) -> None:
    module = _load_generator()
    current_sources = {
        "apps/fl4512-runtime/platform/fl4693_product_glyph_program.cpp",
        "apps/fl4512-runtime/platform/fl4693_product_glyph_program.h",
        "apps/fl4512-runtime/platform/fl4683_glyph_program_loader.cpp",
        "apps/fl4512-runtime/platform/fl4683_glyph_program_loader.h",
    }
    tracked = set(subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines())
    assert current_sources <= module.current_runtime_files()
    # Tracked or untracked, extraction must own them: no disjointness claim.

    manifest = module.build_manifest()
    by_path = {item["path"]: item for item in manifest["entries"]}
    for current_source in sorted(current_sources):
        entry = by_path[current_source]
        assert entry["category"] == "current_runtime_source"
        assert "working tree" in entry["reason"]
        assert current_source in manifest["files"]
    assert "platform/fl4693_product_glyph_program.cpp" not in manifest["files"]
    assert "platform/fl4693_product_glyph_program.h" not in manifest["files"]
    assert "platform/fl4683_glyph_program_loader.cpp" not in manifest["files"]
    assert "platform/fl4683_glyph_program_loader.h" not in manifest["files"]
    untracked = set(manifest["files"]) - tracked
    assert current_sources <= set(manifest["files"])
    anchors = (
        {module.TERMINAL_CONTRACT_MK}
        if (ROOT / module.TERMINAL_CONTRACT_MK).is_file()
        else set()
    )
    assert untracked == (
        module.current_runtime_files() | module.terminal_runtime_files() | anchors
    ) - tracked
    assert manifest["untracked_source_total"] == len(untracked)
    assert manifest["source_total"] == len(module.source_files())
    # Safe untracked discovery without touching the repository: the probe
    # content lives in tmp_path and the ownership/index views are simulated
    # through monkeypatched module functions. No fixed repository filename
    # is created, overwritten, staged, or unlinked here.
    probe_rel = "apps/fl4512-runtime/platform/fl4693_tmp_discovery_probe.cpp"
    assert probe_rel not in tracked
    assert not (ROOT / probe_rel).exists()
    staged_copy = tmp_path / "fl4693_tmp_discovery_probe.cpp"
    staged_copy.write_text("// temporary FL-4512 discovery probe; repository untouched.\n", encoding="utf-8")
    real_current = set(module.current_runtime_files())
    assert probe_rel not in real_current
    real_git_tracked = module.git_tracked_files
    monkeypatch.setattr(module, "current_runtime_files", lambda: real_current | {probe_rel})
    monkeypatch.setattr(module, "git_tracked_files", lambda: set(real_git_tracked()) - {probe_rel})
    probe_manifest = module.build_manifest()
    probe_by_path = {item["path"]: item for item in probe_manifest["entries"]}
    assert probe_rel in set(probe_manifest["files"])
    assert probe_by_path[probe_rel]["category"] == "current_runtime_source"
    assert "working tree" in probe_by_path[probe_rel]["reason"]
    assert probe_manifest["tracked_total"] == manifest["tracked_total"]
    assert probe_manifest["untracked_source_total"] == manifest["untracked_source_total"] + 1
    assert probe_manifest["source_total"] == manifest["source_total"] + 1
    assert staged_copy.is_file()
    assert not (ROOT / probe_rel).exists()


def test_fl4512_extract_manifest_tracked_total_excludes_untracked_sources() -> None:
    module = _load_generator()
    pure = set(module.git_tracked_files())
    assert set(module.tracked_files()) == pure
    if (ROOT / module.TERMINAL_CONTRACT_MK).is_file():
        assert module.TERMINAL_CONTRACT_MK in module.source_union_anchors()
        assert (module.TERMINAL_CONTRACT_MK in pure) == (
            module.TERMINAL_CONTRACT_MK in set(module.tracked_files())
        )
    manifest = module.build_manifest()
    candidates = set(module.source_files())
    files = set(manifest["files"])
    assert manifest["tracked_total"] == len(pure)
    assert manifest["untracked_source_total"] == len(candidates - pure)
    assert manifest["source_total"] == len(candidates)
    assert manifest["tracked_total"] + manifest["untracked_source_total"] == manifest["source_total"]
    assert not (set(module.tracked_files()) & (candidates - pure))
    assert set(files) - pure <= candidates - pure


def test_fl4512_extract_manifest_entries_carry_category_and_reason() -> None:
    manifest = _manifest()
    assert manifest["entries"], "manifest must carry per-path entries"
    for entry in manifest["entries"]:
        assert set(entry) >= {"path", "category", "reason"}, entry
        assert entry["category"], entry
        assert entry["reason"], entry
    counted = sum(len(v) for v in manifest["categories"].values())
    assert counted == manifest["counts"]["extraction_files"] == len(manifest["files"])


def test_fl4512_extract_manifest_materialize(tmp_path: Path) -> None:
    module = _load_generator()
    manifest = module.build_manifest()
    # Broad and in-repository destinations are refused.
    for bad in (Path("/"), Path.home(), ROOT, ROOT / "out"):
        try:
            module.materialize(bad, manifest)
        except ValueError:
            pass
        else:
            raise AssertionError(f"destination not refused: {bad}")
    dest = tmp_path / "fl4512-extract-out"
    receipt = module.materialize(dest, manifest)
    assert receipt["file_count"] == len(manifest["files"])
    assert receipt["copied"] == len(manifest["files"])
    assert receipt["skipped_identical"] == 0
    assert (dest / "scripts" / "presenter_app.py").is_file()
    assert (dest / "fl4512-extract-receipt.json").is_file()
    assert not (dest / ".run").exists()
    second = module.materialize(dest, manifest)
    assert second["skipped_identical"] == len(manifest["files"])


def test_fl4512_extract_manifest_covers_build_spec_closure() -> None:
    module = _load_generator()
    manifest = _manifest()
    files = set(manifest["files"])
    by_path = {entry["path"]: entry for entry in manifest["entries"]}
    # Every build spec (presenter, current, Classic, terminal, server) contributes
    # SRCS entries through one syntactic rule; the union must be covered.
    assert len(module.BUILD_SPEC_MAKEFILES) == 9
    assert "makefile_server" in module.BUILD_SPEC_MAKEFILES
    parsed: set[str] = set()
    for name in module.BUILD_SPEC_MAKEFILES:
        parsed.update(module.parse_makefile_srcs(ROOT / name))
    assert len(parsed) > 130, "all product specs and the local authority must contribute SRCS entries"
    for server_source in module.parse_makefile_srcs(ROOT / "makefile_server"):
        assert server_source in parsed
    assert sorted(parsed - files) == []
    source_files = set(module.source_files())
    direct, closure = module.build_dependency_closure(source_files)
    assert direct <= files
    assert sorted(closure - files) == []
    assert "apps/fl4512-runtime/platform/fl4693_product_glyph_program.cpp" in direct
    assert (
        by_path["apps/fl4512-runtime/platform/fl4693_product_glyph_program.cpp"]["category"]
        == "current_runtime_source"
    )
    assert manifest["dependency_closure"]["direct_sources"] == len(direct)
    assert manifest["dependency_closure"]["include_closure"] == len(closure)
    assert manifest["dependency_closure"]["makefiles"] == list(module.BUILD_SPEC_MAKEFILES)
    # The presenter pair stays a subset of the nine-spec union, and no
    # presenter-only source-closure alias may reappear: those names falsely
    # promised presenter-only semantics for the all-product union.
    assert set(module.PRESENTER_MAKEFILES) <= set(module.BUILD_SPEC_MAKEFILES)
    assert not hasattr(module, "presenter_direct_sources")
    assert not hasattr(module, "presenter_dependency_closure")
    # Spot checks at authoritative repo paths with truthful categories.
    assert by_path["engine/game.cpp"]["category"] == "build_source"
    assert by_path["platform/gl.c"]["category"] == "build_source"
    assert by_path["server/network.cpp"]["category"] == "build_source"
    # Previously omitted game/terminal sources and their headers.
    for path in (
        "engine/game_app.cpp",
        "engine/game_editor_hook_stubs.cpp",
        "engine/input_state.cpp",
        "engine/mainmenu.cpp",
        "apps/terminal-runtime/platform/backend/host_terminal_ansi.cpp",
        "platform/backend/terminal_input_parser.cpp",
        "server/connection/server_connection.cpp",
        "server/authority/authority_appearance.cpp",
        "server/authority/authority_combat.cpp",
        "server/authority/authority_items.cpp",
        "server/authority/authority_npcs.cpp",
        "server/authority/authority_players.cpp",
        "server/authority/authority_snapshots.cpp",
    ):
        assert by_path[path]["category"] == "build_source", path
    for path in (
        "platform/backend/host_terminal_ansi.h",
        "platform/backend/terminal_input_parser.h",
    ):
        assert by_path[path]["category"] == "build_include", path
    for path, entry in by_path.items():
        if path in closure:
            # The current-runtime header is both include-reachable from the
            # product source and explicitly owned by the current-runtime root;
            # root ownership wins the category while closure still covers it.
            if path.startswith(module.CURRENT_RUNTIME_ROOT + "/"):
                assert entry["category"] == "current_runtime_source", path
            else:
                assert entry["category"] == "build_include", path
    for root in module.PRESENTER_RUNTIME_ROOTS:
        assert any(path == root or path.startswith(root + "/") for path in files), root


def test_native_build_check_runs_from_its_extracted_inputs(tmp_path: Path) -> None:
    module = _load_generator()
    inputs = module.build_tool_inputs()
    assert inputs <= set(_manifest()["files"])
    assert inputs == set(_manifest()["dependency_closure"]["build_tool_inputs"])
    # Copy only the declared tool/input set: no checkout, Git metadata, cached
    # outputs, PYTHONPATH, or neighboring repository files can satisfy the check.
    for rel in inputs:
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, target)
    result = subprocess.run(
        [sys.executable, "-I", "scripts/check_actor_visual_table_coverage.py"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "actor visual coverage ok: 192 rows" in result.stdout


def test_native_build_input_declaration_fails_closed(tmp_path: Path, monkeypatch) -> None:
    module = _load_generator()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "BUILD_SPEC_MAKEFILES", ("native.mk",))
    monkeypatch.setattr(module, "BUILD_INPUT_DECLARERS", ())
    (tmp_path / "native.mk").write_text("all:\n\t@python3 scripts/missing_check.py\n")
    try:
        module.build_tool_inputs()
    except ValueError as exc:
        assert "scripts/missing_check.py" in str(exc)
    else:
        raise AssertionError("missing build check was silently excluded")


def test_fl4512_extract_manifest_models_terminal_contract_closure() -> None:
    module = _load_generator()
    manifest = module.build_manifest()
    files = set(manifest["files"])
    by_path = {entry["path"]: entry for entry in manifest["entries"]}
    # The included terminal source contract is itself extracted, tracked or pre-commit.
    assert module.TERMINAL_CONTRACT_MK in files
    assert by_path[module.TERMINAL_CONTRACT_MK]["category"] == "source_union"
    # The SRCS += tail expands through the contract include edge per
    # platform: Linux binds the common set, macOS adds its aux source.
    expanded = module.terminal_expanded_sources()
    assert set(expanded) == {"makefile_game_term", "makefile_game_term_mac"}
    linux = expanded["makefile_game_term"]
    mac = expanded["makefile_game_term_mac"]
    assert "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp" in linux
    assert "apps/fl4512-runtime/engine/fl4512_glyph_transform_relation_v2.cpp" not in linux
    assert "apps/fl4512-runtime/engine/fl4512_glyph_transform_relation_v2.cpp" in mac
    assert set(mac) - set(linux) == {
        "apps/fl4512-runtime/engine/fl4512_glyph_transform_relation_v2.cpp"
    }
    # The terminal product-program list stays empty: the terminal closure
    # must never acquire the current-runtime product program.
    contract_vars = module.parse_make_vars(ROOT / module.TERMINAL_CONTRACT_MK)
    assert contract_vars.get("TERMINAL_RUNTIME_PRODUCT_PROGRAM_SOURCES") == []
    for entries in expanded.values():
        assert (
            "apps/fl4512-runtime/platform/fl4693_product_glyph_program.cpp"
            not in entries
        )
    # Every expanded entry is covered with terminal provenance. Root
    # ownership wins the category: entries under apps/fl4512-runtime are
    # truthfully current_runtime_source, everything else is build_source.
    provenance = module.build_source_provenance()
    for spec, entries in expanded.items():
        for path in entries:
            assert path in files, (spec, path)
            if path == module.CURRENT_RUNTIME_ROOT or path.startswith(
                module.CURRENT_RUNTIME_ROOT + "/"
            ):
                assert by_path[path]["category"] == "current_runtime_source", (
                    spec,
                    path,
                )
            else:
                assert by_path[path]["category"] == "build_source", (spec, path)
            assert "terminal" in provenance[path], (spec, path)
