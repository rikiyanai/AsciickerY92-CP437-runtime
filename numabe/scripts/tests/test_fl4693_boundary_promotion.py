"""FL-4693 P0 source contract for the ownership-boundary promotion.

These checks establish architecture and instrumentation only. They do not
claim runtime execution, pixel equivalence, visual acceptance, or signoff.
"""

import importlib.util
import json
from functools import lru_cache
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RESOLVER_H = (ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.h").read_text()
RESOLVER_CPP = (ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp").read_text()
PRESENTER = (ROOT / "platform/terminal_gl_present.cpp").read_text()
FINAL_TUPLE_OWNER = (
    ROOT / "apps/fl4512-runtime/engine/fl4693_final_tuple_transport.cpp"
).read_text()
CENSUS = (ROOT / "apps/fl4512-runtime/platform/fl4684_scene_fact_emission_census.h").read_text()
WEB_DIAGNOSTICS = (ROOT / "web/web_diagnostics.cpp").read_text()
WEB_PRELOAD = (ROOT / "scripts/web_preload_assets.py").read_text()
NATIVE_RECEIPT_DRIVER = (
    ROOT / "scripts/proofs/proof_fl4684_native_live_producer_motion.py"
).read_text()
WEB_RECEIPT_DRIVER = (
    ROOT / "scripts/proofs/proof_fl4684_web_live_producer_motion.js"
).read_text()
HANDSHAKE = (ROOT / "apps/fl4512-runtime/platform/fl4683_scene_fact_handshake.h").read_text()
PIN_RECEIPT = json.loads(
    (ROOT / "assets/glyphs/generated/fl4683.glyph_program_pin.v1.json").read_text()
)
COMPILER = ROOT / "scripts/compile_fl4683_glyph_program.py"
PRODUCT_MANIFEST = ROOT / "apps/fl4512-runtime/fl4512_product_sources.mk"


def _expand_product_manifest_selections(text: str) -> str:
    """Expand $(FL4512_*) selections through the product-owned manifest.

    Consumer specs name manifest partitions, not product sources; the
    web driver references the manifest and selects CORE|PROGRAM plus the
    ports anchor. Counts below run against the expanded closure.
    """
    manifest = PRODUCT_MANIFEST.read_text()
    entries: dict[str, list[str]] = {}
    current: str | None = None
    for line in manifest.splitlines():
        if line.startswith("FL4512_") and ":=" in line:
            current = line.split(":=")[0].strip()
            entries[current] = (
                line.split(":=", 1)[1].replace("\\", " ").split()
            )
            continue
        if current is None:
            continue
        stripped = line.strip().rstrip("\\").strip()
        if stripped:
            entries[current].extend(stripped.split())
        if not line.rstrip().endswith("\\"):
            current = None
    for var, paths in entries.items():
        text = text.replace("$(%s)" % var, " ".join(paths))
    if "fl4512_product_sources.mk" in text:
        text += "\n" + " ".join(
            entries.get("FL4512_CORE_SRCS", [])
            + entries.get("FL4512_PROGRAM_SRCS", [])
            + entries.get("FL4512_PORTS_ANCHOR", [])
        )
    return text


BUILD_FILES = _expand_product_manifest_selections(
    "\n".join(
        (ROOT / path).read_text()
        for path in (
            "apps/game-fl4512/makefile_game",
            "apps/game-fl4512/makefile_game_mac",
            "apps/asciicker-presenter/makefile_asciiid",
            "apps/asciicker-presenter/makefile_asciiid_mac",
            "build-web.sh",
        )
    )
)


@lru_cache(maxsize=1)
def _compiler_module():
    """Load scripts/compile_fl4683_glyph_program.py once for version pinning."""
    spec = importlib.util.spec_from_file_location(
        "fl4683_compiler_boundary_test", COMPILER
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def _fresh_pin_receipt():
    """Recompile the pin receipt in memory rather than reading the committed file.

    HISTORY, so the version numbers below are not mistaken for arbitrary
    constants. When this helper was written (da7489e74) the committed receipt
    was v4 and the compiler emitted v5, so the version difference WAS the
    signal that the committed file was stale against its compiler. f8fdf7703
    moved the compiler constant to 6 with a v6 committed receipt (61a11750f);
    FL-4783 then moved both to 7 (immutable stroke-port graph tables), so both
    sides now read 7 and the version field no longer reports staleness at all.

    The receipt version tracks the SIDECAR FORMAT, nothing else. Whether the
    committed artifact is actually current is now owned by the FL-4768 profile
    pin inside the compiler, which compares the compiled pool against its
    authored profile source and fails closed. Do not reintroduce a version
    skew here as a staleness proxy.
    """
    _, payload = _compiler_module().compile_all()
    return json.loads(payload)


def test_one_public_partition_owner_replaces_presenter_membership_predicates():
    assert "Fl4693ClassifyProfilePartitionV1(" in RESOLVER_H
    assert RESOLVER_CPP.count("Fl4693ClassifyProfilePartitionV1(") == 3
    assert PRESENTER.count("Fl4693ClassifyProfilePartitionV1(") == 0
    assert FINAL_TUPLE_OWNER.count("Fl4693ClassifyProfilePartitionV1(") == 1
    assert "Fl4260MaterialProfileLive" not in PRESENTER


def test_partition_has_three_results_plus_named_fail_closed_instrument_state():
    for token in (
        "FL4693_PROFILE_PARTITION_POPULATED_ADDRESS",
        "FL4693_PROFILE_PARTITION_DECLARED_ABSENCE",
        "FL4693_PROFILE_PARTITION_OUT_OF_PROFILE_SCOPE",
        "FL4693_PROFILE_PARTITION_UNCLASSIFIED",
        "FL4693_PROFILE_PARTITION_FAILURE_MISSING_SCENE_FACT",
        "FL4693_PROFILE_PARTITION_FAILURE_MIXED_DISPOSITION",
    ):
        assert token in RESOLVER_H
    assert "out.profile_scoped = dispatch_is_profile" in RESOLVER_CPP
    assert "dispatch_is_profile && namespace_is_profile" in RESOLVER_CPP
    assert "out.populated_family_count == out.route_family_count" in RESOLVER_CPP
    assert "out.declared_absent_family_count == out.route_family_count" in RESOLVER_CPP


def test_missing_program_retains_compiler_declared_profile_scope():
    profile_live = RESOLVER_CPP.split("bool Fl4260MaterialProfileLive", 1)[1]
    profile_live = profile_live.split("Fl4693ProfilePartitionResultV1", 1)[0]
    assert "if (Fl4693ProductGlyphProgramReadyV1())" in profile_live
    assert "g_fl4260_v1_profiles" in profile_live
    assert "Fl4512CompiledPoolFailureDomainApplies(profile_key)" in profile_live


def test_missing_program_keeps_fail_closed_publication_hook_installed():
    installer = RESOLVER_CPP.split("bool Fl4131RuntimeHarriInstall", 1)[1]
    installer = installer.split("bool Fl4131RuntimeHarriInstalled", 1)[0]
    assert "const bool product_ready = Fl4131RuntimeEnsureLoaded();" in installer
    assert "!product_ready && g_fl4131_runtime_glyph_count <= 0" in installer
    assert "HarriSlotWrite(Fl4693RuntimeGlyphResolveHook)" in installer


def test_scene_fact_wrapper_names_a_missing_fact_instead_of_returning_failure_none():
    wrapper = RESOLVER_CPP.split(
        "Fl4693ProfilePartitionResultV1 Fl4693ClassifySceneFactPartitionV1(", 1
    )[1]
    wrapper = wrapper.split("bool Fl4512CompiledPoolLoadFailed", 1)[0]
    assert "if (!scene_fact)" in wrapper
    assert (
        "out.failure = FL4693_PROFILE_PARTITION_FAILURE_MISSING_SCENE_FACT"
        in wrapper
    )


def test_every_compiled_route_family_maps_inside_the_single_owner():
    helper = RESOLVER_CPP.split("Fl4693ClassifyProfilePartitionV1", 1)[1]
    helper = helper.split("Fl4693ClassifySceneFactPartitionV1", 1)[0]
    for family in (
        "FL4683_ROUTE_FAMILY_RAMP_V1",
        "FL4683_ROUTE_FAMILY_DENSITY_V1",
        "FL4683_ROUTE_FAMILY_EDGE_V1",
        "FL4683_ROUTE_FAMILY_DIRECTION_V1",
        "FL4683_ROUTE_FAMILY_FLOW_V1",
        "FL4683_ROUTE_FAMILY_FILLED_FIELD_V1",
        "FL4683_ROUTE_FAMILY_MESH_FEATURE_BUNDLE_V1",
    ):
        assert family in helper


def test_p0_behavior_uses_only_the_preserved_two_field_scope_bit():
    final_plane = FINAL_TUPLE_OWNER.split(
        "bool Fl4693FinalTupleCommitFrameV1", 1
    )[1].split('extern "C" const void* GetFinalGlyphTuplesV1', 1)[0]
    assert "Fl4512FinalizePresentationTupleV2" not in PRESENTER
    assert "partition.profile_scoped" in final_plane
    assert "partition.outcome" not in final_plane
    assert "partition.profile_scoped" not in PRESENTER


def test_retired_gpu_partition_receipt_does_not_survive_the_owner_cutover():
    assert "[FL-4512-OWN] gpu winner pass" not in PRESENTER
    assert "[FL-4693-P0] partition:" not in PRESENTER
    commit = FINAL_TUPLE_OWNER.split("bool Fl4693FinalTupleCommitFrameV1", 1)[1]
    assert "Fl4693ClassifyProfilePartitionV1(" in commit
    assert "partition.profile_scoped" in commit


def test_shared_census_and_browser_adapter_report_the_same_partition():
    assert "fl4684.live_producer_emission_census.v4" in CENSUS
    assert "Fl4693ClassifySceneFactPartitionV1(fact)" in CENSUS
    for token in (
        "fl4693_partition_populated_cells",
        "fl4693_partition_declared_absent_cells",
        "fl4693_partition_out_of_profile_cells",
        "fl4693_partition_unclassified_cells",
        "fl4693_final_tuple_frames_observed",
        "fl4693_final_tuple_selected_cells",
        "fl4693_final_tuple_declared_absence_cells",
        "fl4693_final_tuple_missing_authority_cells",
        "fl4693_final_tuple_out_of_profile_cells",
    ):
        assert token in CENSUS
    # Web reads rows plus partition outcome through the neutral inspection
    # port; classification runs adapter-side, never in web globals.
    assert "diag_nrt->read_scene_fact_rows" in WEB_DIAGNOSTICS
    assert "sample_fl4693_partition_outcome" in WEB_DIAGNOSTICS
    assert "sample_fl4693_partition_failure" in WEB_DIAGNOSTICS
    assert "sample_fl4693_profile_scoped" in WEB_DIAGNOSTICS


def test_both_product_receipts_preserve_the_partition_totals():
    for token in (
        "fl4693_partition_populated_cells",
        "fl4693_partition_declared_absent_cells",
        "fl4693_partition_out_of_profile_cells",
        "fl4693_partition_unclassified_cells",
    ):
        assert token in NATIVE_RECEIPT_DRIVER
        assert token in WEB_RECEIPT_DRIVER


def test_native_negative_fixture_separates_process_root_from_compiler_workspace():
    assert '"--game-cwd"' in NATIVE_RECEIPT_DRIVER
    assert '"--profile-workspace"' in NATIVE_RECEIPT_DRIVER
    assert 'game_command.extend(["--profile-workspace"' in NATIVE_RECEIPT_DRIVER
    assert 'cwd=str(game_cwd)' in NATIVE_RECEIPT_DRIVER


def test_web_target_packages_the_operator_selected_pieta_corpus():
    assert 'Path("assets/a3d/fl4260_fixture_all_materials.a3d")' in WEB_PRELOAD
    assert 'Path("assets/a3d/frog_pieta_4x4.a3d")' in WEB_PRELOAD
    assert 'Path("assets/a3d/frog_pieta_1x1.a3d")' not in WEB_PRELOAD
    assert "for scene_path in PRELOAD_SCENE_FILES:" in WEB_PRELOAD


def test_web_negative_fixture_omits_only_the_glyph_program_sidecar():
    assert 'choices=("missing-glyph-program",)' in WEB_PRELOAD
    assert "assets.remove(GLYPH_PROGRAM_SIDECAR)" in WEB_PRELOAD
    assert "FL4693_WEB_NEGATIVE_FIXTURE" in BUILD_FILES
    assert "--negative-fixture missing-glyph-program" in BUILD_FILES
    assert "FL4693_PRELOAD_SCRIPT_ARGS=(" in BUILD_FILES
    assert "FL4693_PRELOAD_MODE_ARGS=()" not in BUILD_FILES


def test_p6_activates_only_the_validated_product_view():
    assert "if (!input->glyph_program.runtime_consumption_allowed)" in HANDSHAKE
    # Both sides carry the FL-4783 receipt revision 7 (immutable stroke-port
    # graph tables): the committed artifact and the compiler constant
    # SIDECAR_FORMAT_VERSION. Asserting explicit 7 plus equality to the live
    # compiler constant is the point -- a divergence here means the receipt
    # revision and the sidecar format have come apart, and the explicit 7
    # keeps a future format bump from passing silently.
    compiler_version = _compiler_module().SIDECAR_FORMAT_VERSION
    assert compiler_version == 7
    assert PIN_RECEIPT["version"] == 7
    assert PIN_RECEIPT["version"] == compiler_version
    assert PIN_RECEIPT["sidecar"]["format_version"] == compiler_version
    receipt = _fresh_pin_receipt()
    rights = receipt["runtime_rights"]
    assert receipt["version"] == 7
    assert receipt["version"] == PIN_RECEIPT["version"]
    assert receipt["sidecar"]["format_version"] == compiler_version
    assert receipt["sidecar"]["format_version"] == PIN_RECEIPT["sidecar"]["format_version"]
    assert rights["serialized_sidecar"]["runtime_consumption_allowed"] is False
    assert rights["serialized_sidecar"]["header_byte_value"] == 0
    assert rights["product_startup_promotion"]["enabled"] is True
    assert (
        rights["product_startup_promotion"][
            "runtime_consumption_allowed_after_promotion"
        ]
        is True
    )
    assert rights["loader_call_site_measurement"]["measured_call_site_count"] == 1
    assert len(rights["loader_call_site_measurement"]["call_sites"]) == 1
    assert rights["selector_call_path"]["enabled"] is True
    assert len(rights["selector_call_path"]["steps"]) == 2
    assert BUILD_FILES.count("fl4683_glyph_program_loader.cpp") == 5
    assert BUILD_FILES.count("fl4683_exact_cell_selector.cpp") == 5
    assert BUILD_FILES.count("fl4693_product_glyph_program.cpp") == 5
    product = (ROOT / "apps/fl4512-runtime/platform/fl4693_product_glyph_program.cpp").read_text()
    assert "g_image->program.runtime_consumption_allowed = 1u" in product
    assert "g_image->declared_future_rights" in product
