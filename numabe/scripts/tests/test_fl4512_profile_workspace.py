from __future__ import annotations

import subprocess
from pathlib import Path
import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/fl4512-runtime/engine/fl4512_profile_workspace.cpp"
EDITOR = ROOT / "apps/asciicker-presenter/asciiid.cpp"
RESOLVER = ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp"
GAME_APP = ROOT / "engine/game_app.cpp"
TERMINAL_PRESENTER = ROOT / "platform/terminal_gl_present.cpp"
BUILD_FILES = (
    "build-web.sh",
    "apps/asciicker-presenter/makefile_asciiid",
    "apps/asciicker-presenter/makefile_asciiid_mac",
    "apps/game-fl4512/makefile_game",
    "apps/game-fl4512/makefile_game_mac",
)
TERMINAL_BUILD_FILES = (
    "makefile_game_term",
    "makefile_game_term_mac",
)
TERMINAL_RUNTIME_SOURCES_MK = "apps/terminal-runtime/terminal_runtime_sources.mk"


@pytest.fixture(scope="module")
def probe(tmp_path_factory: pytest.TempPathFactory) -> Path:
    build = tmp_path_factory.mktemp("fl4512_profile_workspace")
    source = build / "probe.cpp"
    binary = build / "probe"
    source.write_text(
        r'''
#include "apps/fl4512-runtime/engine/fl4512_profile_workspace.h"
#include <stdio.h>
#include <string.h>

static void PrintWorkspace(const Fl4512ProfileWorkspaceV1* workspace) {
    if (!workspace) {
        printf("workspace=null\n");
        return;
    }
    printf("root=%s\nprofile=%s\npool=%s\n", workspace->root_path,
        workspace->profile_path, workspace->compiled_pool_path);
    printf("profile_before_sha256=%s\npool_before_sha256=%s\nsource_fixture_sha256=%s\nsemantic_workspace_sha256=%s\n",
        workspace->profile_before_sha256, workspace->compiled_pool_before_sha256,
        workspace->source_fixture_sha256, workspace->semantic_workspace_sha256);
}

int main(int argc, char** argv) {
    char reason[256] = {};
    if (argc < 2) return 90;
    if (strcmp(argv[1], "lazy_default") == 0) {
        printf("configured_before=%d\n", Fl4512ProfileWorkspaceConfiguredV1() ? 1 : 0);
        PrintWorkspace(Fl4512GetProfileWorkspaceV1());
        printf("configured_after=%d\nprofile_getter=%s\npool_getter=%s\n",
            Fl4512ProfileWorkspaceConfiguredV1() ? 1 : 0,
            Fl4512ProfileWorkspaceProfilePathV1(),
            Fl4512ProfileWorkspaceCompiledPoolPathV1());
        return 0;
    }
    if (strcmp(argv[1], "configure") == 0) {
        const char* root = argc >= 3 && strcmp(argv[2], "DEFAULT") != 0 ? argv[2] : NULL;
        const bool ok = Fl4512ConfigureProfileWorkspaceV1(root, "assets/glyphs", reason, sizeof(reason));
        printf("ok=%d\nreason=%s\nconfigured=%d\n", ok ? 1 : 0, reason,
            Fl4512ProfileWorkspaceConfiguredV1() ? 1 : 0);
        PrintWorkspace(Fl4512GetProfileWorkspaceV1());
        const Fl4512GlobalAssetPathsV1* global = Fl4512GetGlobalAssetPathsV1();
        if (!global) {
            printf("global=null\n");
            return ok ? 91 : 2;
        }
        printf("catalog=%s\natlas=%s\npage16=%s\naspect=%s\nmetric=%s\naspect_golden=%s\nscale_golden=%s\n",
            global->shape_catalog_path, global->atlas_registry_path,
            global->page16_path, global->aspect_authoring_path,
            global->mesh_asset_metric_path,
            global->aspect_golden_path, global->projected_scale_golden_path);
        printf("catalog_getter=%s\natlas_getter=%s\npage16_getter=%s\naspect_getter=%s\naspect_golden_getter=%s\nscale_golden_getter=%s\n",
            Fl4512GlobalShapeCatalogPathV1(), Fl4512GlobalAtlasRegistryPathV1(),
            Fl4512GlobalPage16PathV1(), Fl4512GlobalAspectAuthoringPathV1(),
            Fl4512GlobalAspectGoldenPathV1(),
            Fl4512GlobalProjectedScaleGoldenPathV1());
        return ok ? 0 : 2;
    }
    if (strcmp(argv[1], "reconfigure") == 0 && argc == 4) {
        const bool first = Fl4512ConfigureProfileWorkspaceV1(argv[2], "assets/glyphs", reason, sizeof(reason));
        printf("first=%d\n", first ? 1 : 0);
        const bool second = Fl4512ConfigureProfileWorkspaceV1(argv[3], "assets/glyphs", reason, sizeof(reason));
        printf("second=%d\nreason=%s\n", second ? 1 : 0, reason);
        PrintWorkspace(Fl4512GetProfileWorkspaceV1());
        return first && !second ? 0 : 3;
    }
    return 92;
}
''',
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            "c++",
            "-std=c++11",
            "-pthread",
            "-I",
            str(ROOT),
            str(source),
            str(MODULE),
            str(ROOT / "engine/third_party/cjson/cJSON.c"),
            "-o",
            str(binary),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
    return binary


def run_probe(probe: Path, *args: str, expected: int = 0) -> dict[str, str]:
    completed = subprocess.run(
        [str(probe), *args], cwd=ROOT, text=True, capture_output=True
    )
    assert completed.returncode == expected, completed.stdout + completed.stderr
    return dict(line.split("=", 1) for line in completed.stdout.splitlines())


def test_getter_lazily_installs_default_workspace(probe: Path) -> None:
    observed = run_probe(probe, "lazy_default")
    root = (ROOT / "assets/glyphs").resolve()
    assert observed["configured_before"] == "0"
    assert observed["configured_after"] == "1"
    assert observed["root"] == str(root)
    assert observed["profile_getter"] == str(
        root / "profiles/material_rendering_profiles.v1.json"
    )
    assert observed["pool_getter"] == str(
        root / "generated/fl4512.compiled_pool.v1.json"
    )


def test_default_root_is_canonical_and_matches_historical_paths(probe: Path) -> None:
    observed = run_probe(probe, "configure", "DEFAULT")
    root = (ROOT / "assets/glyphs").resolve()
    assert observed["ok"] == "1"
    assert observed["root"] == str(root)
    assert observed["profile"] == str(
        root / "profiles/material_rendering_profiles.v1.json"
    )
    assert observed["pool"] == str(
        root / "generated/fl4512.compiled_pool.v1.json"
    )


def test_custom_root_changes_only_mutable_pair(probe: Path, tmp_path: Path) -> None:
    custom = tmp_path / "workspace"
    (custom / "profiles").mkdir(parents=True)
    (custom / "generated").mkdir()
    (custom / "profiles/material_rendering_profiles.v1.json").write_bytes(
        (ROOT / "assets/glyphs/profiles/material_rendering_profiles.v1.json").read_bytes()
    )
    (custom / "generated/fl4512.compiled_pool.v1.json").write_bytes(
        (ROOT / "assets/glyphs/generated/fl4512.compiled_pool.v1.json").read_bytes()
    )
    observed = run_probe(probe, "configure", str(custom))
    assert observed["root"] == str(custom.resolve())
    assert observed["profile"] == str(
        custom.resolve() / "profiles/material_rendering_profiles.v1.json"
    )
    assert observed["pool"] == str(
        custom.resolve() / "generated/fl4512.compiled_pool.v1.json"
    )
    assert observed["catalog"] == str(
        (ROOT / "assets/glyphs/generated/material.additive.v1.shape_catalog.json").resolve()
    )
    assert observed["atlas"] == str(
        (ROOT / "assets/glyphs/generated/fl4482.native_atlas_registry.v1.json").resolve()
    )
    assert observed["page16"] == str(
        (ROOT / "assets/glyphs/atlases/material.additive.v1.page16_rgba8.json").resolve()
    )
    assert observed["aspect"] == str(
        (ROOT / "assets/glyphs/profiles/fl4512.aspect_pool_authoring.v1.json").resolve()
    )
    assert observed["metric"] == str(
        (ROOT / "assets/glyphs/profiles/fl4512.mesh_asset_metric.v1.json").resolve()
    )
    assert observed["aspect_golden"] == str(
        (ROOT / "assets/glyphs/fixtures/fl4512_d6_aspect_golden_v1.json").resolve()
    )
    assert observed["scale_golden"] == str(
        (ROOT / "assets/glyphs/fixtures/fl4512_projected_scale_golden_v1.json").resolve()
    )
    assert observed["catalog_getter"] == observed["catalog"]
    assert observed["atlas_getter"] == observed["atlas"]
    assert observed["page16_getter"] == observed["page16"]
    assert observed["aspect_getter"] == observed["aspect"]
    assert observed["aspect_golden_getter"] == observed["aspect_golden"]
    assert observed["scale_golden_getter"] == observed["scale_golden"]
    for field in (
        "profile_before_sha256",
        "pool_before_sha256",
        "source_fixture_sha256",
        "semantic_workspace_sha256",
    ):
        assert len(observed[field]) == 64
    default_observed = run_probe(probe, "configure", "DEFAULT")
    assert observed["semantic_workspace_sha256"] == default_observed[
        "semantic_workspace_sha256"
    ]


def test_compiler_bytes_are_root_independent(tmp_path: Path) -> None:
    profile = ROOT / "assets/glyphs/profiles/material_rendering_profiles.v1.json"
    copied_root = tmp_path / "copied"
    copied_profile = copied_root / "profiles/material_rendering_profiles.v1.json"
    copied_profile.parent.mkdir(parents=True)
    copied_profile.write_bytes(profile.read_bytes())
    default_out = tmp_path / "default.json"
    copied_out = copied_root / "generated/fl4512.compiled_pool.v1.json"
    copied_out.parent.mkdir()
    common = [
        "python3",
        "scripts/compile_fl4512_pools.py",
        "--pool-revision",
        "2",
    ]
    first = subprocess.run(
        [*common, "--profiles", str(profile), "--out", str(default_out)],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    second = subprocess.run(
        [*common, "--profiles", str(copied_profile), "--out", str(copied_out)],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    assert default_out.read_bytes() == copied_out.read_bytes()


def test_missing_custom_files_fail_without_default_fallback(probe: Path, tmp_path: Path) -> None:
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    observed = run_probe(probe, "configure", str(empty_root), expected=2)
    assert observed["ok"] == "0"
    assert observed["reason"] == "workspace_hash_input_missing"
    assert observed["workspace"] == "null"


def test_missing_root_fails_without_claiming_workspace(probe: Path, tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    observed = run_probe(probe, "configure", str(missing), expected=2)
    assert observed["ok"] == "0"
    assert observed["configured"] == "0"
    assert observed["workspace"] == "null"
    assert observed["reason"].startswith("workspace_root_canonicalization_failed")


def test_root_is_canonicalized_before_paths_are_derived(probe: Path, tmp_path: Path) -> None:
    canonical = tmp_path / "a" / "workspace"
    (canonical / "profiles").mkdir(parents=True)
    (canonical / "generated").mkdir()
    (canonical / "profiles/material_rendering_profiles.v1.json").write_bytes(
        (ROOT / "assets/glyphs/profiles/material_rendering_profiles.v1.json").read_bytes()
    )
    (canonical / "generated/fl4512.compiled_pool.v1.json").write_bytes(
        (ROOT / "assets/glyphs/generated/fl4512.compiled_pool.v1.json").read_bytes()
    )
    requested = canonical / ".." / "workspace"
    observed = run_probe(probe, "configure", str(requested))
    assert observed["root"] == str(canonical.resolve())


def test_successful_configuration_is_immutable(probe: Path, tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    (first / "profiles").mkdir(parents=True)
    (first / "generated").mkdir()
    (first / "profiles/material_rendering_profiles.v1.json").write_bytes(
        (ROOT / "assets/glyphs/profiles/material_rendering_profiles.v1.json").read_bytes()
    )
    (first / "generated/fl4512.compiled_pool.v1.json").write_bytes(
        (ROOT / "assets/glyphs/generated/fl4512.compiled_pool.v1.json").read_bytes()
    )
    second.mkdir()
    observed = run_probe(probe, "reconfigure", str(first), str(second))
    assert observed["first"] == "1"
    assert observed["second"] == "0"
    assert observed["reason"] == "workspace_already_configured_immutable"
    assert observed["root"] == str(first.resolve())


def test_runtime_and_editor_deleted_fixed_mutable_path_owners() -> None:
    editor = EDITOR.read_text()
    resolver = RESOLVER.read_text()
    presenter = TERMINAL_PRESENTER.read_text()
    assert "kFl4260ProfilesV1Path" not in editor
    assert "kFl4512CompiledPoolV1Path" not in editor
    assert "kFl4512ProfileSourcePath" not in resolver
    assert "kFl4512CompiledPoolPath" not in resolver
    assert "assets/glyphs/profiles/material_rendering_profiles.v1.json" not in editor
    assert "assets/glyphs/generated/fl4512.compiled_pool.v1.json" not in editor
    assert "AsciickerAuthoring()->workspace_profile_path()" in editor
    assert "AsciickerAuthoring()->workspace_compiled_pool_path()" in editor
    adapter = (ROOT / "apps/fl4512-runtime/engine/fl4512_neutral_adapter.cpp").read_text()
    assert "return Fl4512ProfileWorkspaceProfilePathV1();" in adapter
    assert "return Fl4512ProfileWorkspaceCompiledPoolPathV1();" in adapter
    assert "Fl4512WorkspaceProfilePath()" in resolver
    assert "Fl4512WorkspaceCompiledPoolPath()" in resolver
    assert "kFl4131RuntimeShapeCatalogPath" not in resolver
    assert "AsciickerAuthoring()->resolve_global_glyph_path(" in editor
    assert "atlas_nrt->resolve_atlas_page_path(" in presenter


def test_startup_front_doors_configure_before_runtime_use() -> None:
    editor = EDITOR.read_text()
    game_app = GAME_APP.read_text()
    assert '"--profile-workspace"' not in editor
    # FL-4512: both front doors delegate argument interpretation to the same
    # startup owner; the editor must not configure a second hard-coded root.
    assert "AsciickerAuthoring()->configure_profile_workspace(" not in editor
    assert "startup->startup_configure_from_argv(argc, argv, base_path)" in editor
    assert "if (workspace_rc != 0) return workspace_rc;" in editor
    assert 'strcmp(argv[' in game_app
    assert '"--profile-workspace"' in game_app
    startup = (ROOT / "apps/fl4512-runtime/engine/fl4512_product_startup.cpp").read_text()
    assert "Fl4512ConfigureProfileWorkspaceV1(" in startup
    assert "profile workspace rejected" in startup
    assert editor.index("startup->startup_configure_from_argv(argc, argv, base_path)") < editor.index("LoadSprites();")
    assert game_app.index("startup_nrt->startup_configure_from_argv(argc, argv, base_path)") < game_app.index("InitMaterials();")


def test_editor_transaction_uses_sibling_temps_and_parent_fsync() -> None:
    editor = EDITOR.read_text()
    transaction = editor.split(
        "static bool Fl4512CompileAndCommitProfileArtifact(", 1
    )[1].split("static void Fl4260SetReason", 1)[0]
    assert "Fl4512SiblingTempTemplate(" in transaction
    assert "Fl4512DurableReplace(staged_profile_path, profile_path)" in transaction
    assert "Fl4512DurableReplace(staged_artifact_path, compiled_pool_path)" in transaction
    assert "Fl4512FsyncParentDirectory(target_path)" in editor
    for slot in ("global_shape_catalog_path", "global_atlas_registry_path",
                 "global_page16_path", "global_aspect_authoring_path",
                 "global_aspect_golden_path", "global_projected_scale_golden_path"):
        assert f"AsciickerAuthoring()->{slot}()" in transaction
    assert "--aspect-authoring %s --aspect-golden %s" in transaction
    assert "qv-null artifact behind the active selector" in transaction
    assert "profile workspace compiler command too long" in transaction
    # RQ-202 same-session authoring uses the sole program owner, not the retired
    # pool-only reload. Validate before durable writes; publish only after all
    # three durable members are installed, preserving rollback on rejection.
    assert "link(profile_path, profile_backup)" in transaction
    assert "link(compiled_pool_path, artifact_backup)" in transaction
    assert "Fl4512DurableReplace(profile_backup, profile_path)" in transaction
    assert "Fl4512DurableReplace(artifact_backup, compiled_pool_path)" in transaction
    assert "Fl4512RestoreCompiledPoolArtifactSameRevision" not in editor
    assert "U4-RESTORE" not in editor
    assert "same-revision" not in transaction
    assert "atomically committed" not in transaction
    assert "pair is fail-closed across crashes" not in transaction
    assert "fails closed on the next load" in transaction
    assert "rebuild the pinned GlyphProgram before restart" not in transaction
    assert transaction.index("prepare_compiled_program(program_receipt_path") < transaction.index(
        "Fl4512DurableReplace(staged_profile_path, profile_path)"
    )
    assert transaction.index("program_receipt_commit.Install()") < transaction.index(
        "publish_compiled_program(program_guard.update)"
    )
    assert "program_receipt_commit.Rollback()" in transaction
    assert "source_and_live_program_committed" in transaction


def test_every_resolver_target_links_single_workspace_owner() -> None:
    manifest_path = "apps/fl4512-runtime/fl4512_product_sources.mk"
    manifest = (ROOT / manifest_path).read_text()
    assert manifest.count("apps/fl4512-runtime/engine/fl4512_profile_workspace.cpp") == 1
    for build_file in BUILD_FILES:
        source = (ROOT / build_file).read_text()
        assert manifest_path in source, build_file
        assert "apps/fl4512-runtime/engine/fl4512_profile_workspace.cpp" not in source
        if build_file == "build-web.sh":
            assert 'FL4512_MANIFEST=' in source
        else:
            assert "$(FL4512_CORE_SRCS)" in source
    assert not (ROOT / "engine/fl4512_profile_workspace.cpp").exists()
    assert not (ROOT / "engine/fl4512_profile_workspace.h").exists()
    mk = (ROOT / TERMINAL_RUNTIME_SOURCES_MK).read_text()
    common = mk.split("TERMINAL_RUNTIME_COMMON_SOURCES :=", 1)[1].split(
        "TERMINAL_RUNTIME_MAC_AUX_SOURCES", 1
    )[0]
    assert manifest_path in mk
    assert "$(FL4512_CORE_SRCS)" in common
    assert "apps/fl4512-runtime/engine/fl4512_profile_workspace.cpp" not in common
    for build_file in TERMINAL_BUILD_FILES:
        source = (ROOT / build_file).read_text()
        assert TERMINAL_RUNTIME_SOURCES_MK in source
        assert "$(TERMINAL_RUNTIME_SOURCES)" in source
        assert "engine/fl4512_profile_workspace.cpp" not in source
    assert "engine/fl4512_profile_workspace.cpp" not in (
        ROOT / "makefile_server"
    ).read_text()
