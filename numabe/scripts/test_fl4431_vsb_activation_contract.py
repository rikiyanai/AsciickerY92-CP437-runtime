#!/usr/bin/env python3
# =============================================================================
# test_fl4431_vsb_activation_contract.py
# =============================================================================
# WHAT THIS FILE IS FOR
#
# The FL-4431 "activation" change replaced how this project decides what every
# character on screen is showing. The old way used a chain of GPU passes that
# rendered the world into pictures ("probe capture", "splat", "resolve") and
# then guessed, per screen cell, which of several competing buffers had the
# right answer. The new way asks one component -- the Visible Surface Builder,
# abbreviated VSB -- to trace the scene directly and publish one finished
# answer per sub-cell. Everything downstream reads that single answer.
#
# A change that big only stays correct if the OLD way is physically gone. If
# any part of it survives, the two systems can disagree, and past incidents in
# this project came from exactly that: two owners writing the same pixels.
#
# So this file is a CONTRACT test. It reads the source code as text and proves
# structural facts about it. It does NOT run the game, does not use a GPU, and
# does not look at any image. Passing here means "the code is shaped correctly".
# It does NOT mean the picture looks right -- that requires the separate visual
# acceptance run with operator signoff, which is deliberately not automated.
#
# Vocabulary used below, in plain terms:
#   glyph      - one character shape drawn into a cell (a letter, block, etc).
#   ANSI cell  - one character-sized square of the screen.
#   subcell    - one quarter of an ANSI cell; each cell holds four.
#   binding    - a numbered slot through which a shader reads or writes a buffer.
#   shader     - a small program that runs on the graphics card.
#   host       - the ordinary (GDScript) code that sets shaders up and runs them.
#
# Run with:  python3 scripts/test_fl4431_vsb_activation_contract.py
# Exit code 0 means every structural check passed.
# =============================================================================

"""Guard the FL-4431 retained-scene activation with backend-specific evidence.

The Vulkan 1.1 compiler and Apple's Metal compiler validate different generated
programs. A Vulkan pass therefore never implies Metal acceptance. This explicit
warning exists because the first headed activation run reached Metal with an
undeclared ``terrain_corner_height_texSmplr`` identifier after every Vulkan
check had passed. The Metal section below reports PASS, FAIL, plus a named SKIP
when the local machine lacks one required compiler tool.
"""

from __future__ import annotations
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import ast
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from test_fl4431_abab_perf_ablation_deleted_contract import (
    contract as abab_perf_ablation_deleted_contract,
)
from test_fl4431_dynamic_shadow_active_count_contract import (
    contract_violations as dynamic_shadow_active_count_contract_violations,
    destructive_mutants as dynamic_shadow_active_count_destructive_mutants,
)
from test_fl4431_default_viewport_readback_deleted_contract import (
    contract_violations as default_viewport_readback_contract_violations,
)
from test_fl4431_foliage_downwash_invariant_contract import (
    contract_violations as foliage_downwash_invariant_contract_violations,
)
from test_fl4216_wake_endpoint_anchor import (
    destructive_mutants as wake_topology_destructive_mutants,
    host_destructive_mutants as wake_topology_host_destructive_mutants,
    source_contract_errors as wake_topology_contract_errors,
)

# The repository root, found by walking up from this script's own location, so
# the test works no matter which directory it is invoked from.
REPO = Path(__file__).resolve().parent.parent

SHADERS = REPO / "godot_project"
RENDER = REPO / "godot_project"

PRODUCT_PATH = SHADERS / "render/product/cell_owner_product.glsl"
COMPOSITOR_PATH = SHADERS / "render/ascii/raster/ascii_compositor.glsl"
VSB_PATH = SHADERS / "render/backends/ortho/visibility/visible_surface_builder.glsl"
ACE_PATH = RENDER / "render/api/ASCIICompositorEffect.gd"
BUILDER_PATH = RENDER / "render/core/contracts/visibility/VisibleSurfaceABI.gd"
SCENE_DB_PATH = RENDER / "render/scene/SceneGeometryDB.gd"
WORLD_PATH = REPO / "godot_project" / "scripts" / "World.gd"
MAIN_PATH = REPO / "godot_project" / "scripts" / "Main.gd"
MOVING_ANALYZER_PATH = REPO / "scripts" / "test_fl4359_moving_camera_analyzer.py"
HEADED_MOVEMENT_PATH = REPO / "scripts" / "run_fl4359_headed_movement.py"
AIRPLANE_PROOF_PATH = REPO / "scripts" / "run_fl4265_airplane_proof.py"
MANUAL_PLAY_PATH = REPO / "scripts" / "launch_fl4359_manual_play.py"
WAKE_HOST_BEHAVIOR_PATH = (
    REPO
    / "godot_project"
    / "scripts"
    / "tests"
    / "test_fl4216_wake_host_topology.gd"
)


def foliage_downwash_invariant_destructive_mutants(
    product_source: str,
) -> dict[str, str]:
    """Return the focused contract's six concrete Product regressions.

    The focused runner currently keeps these mutations beside its pytest. The
    activation harness needs full mutated source texts so its sandbox can
    execute the same damages against the global section without editing the
    focused owner.
    """

    fact_before_loop = (
        "\t\t\t\tFoliageAnchorWindFact anchor_wind =\n"
        "\t\t\t\t\tfoliage_anchor_wind_fact("
        "anchor_facts.world_origin.xz);\n\n"
        "\t\t\t\tfor (int slot = 0; "
        "slot < FOLIAGE_SLOTS_PER_GRASS_CELL; slot++) {\n"
    )
    fact_inside_loop = (
        "\t\t\t\tfor (int slot = 0; "
        "slot < FOLIAGE_SLOTS_PER_GRASS_CELL; slot++) {\n"
        "\t\t\t\t\tFoliageAnchorWindFact anchor_wind =\n"
        "\t\t\t\t\t\tfoliage_anchor_wind_fact("
        "anchor_facts.world_origin.xz);\n"
    )
    return {
        "scan_resurrection": product_source.replace(
            "vec2 dw_dir = downwash_direction;\n\tfloat dw = downwash_boost;",
            "vec2 dw_dir = vec2(0.0);"
            "\n\tfloat dw = foliage_downwash_boost(cell_xz, t, dw_dir);",
            1,
        ),
        "slot_dependent_time": product_source.replace(
            "float t = anchor_wind.quantized_time;",
            "float t = anchor_wind.quantized_time + float(slot) * 0.2;",
            1,
        ),
        "missing_anchor_admission": product_source.replace(
            "\t\t\t\tif (!anchor_admitted) continue;\n",
            "",
            1,
        ),
        "cross_anchor_reuse": product_source.replace(
            "foliage_anchor_wind_fact(anchor_facts.world_origin.xz);",
            "foliage_anchor_wind_fact(target_facts.world_origin.xz);",
            1,
        ),
        "fact_moved_into_slot_loop": product_source.replace(
            fact_before_loop,
            fact_inside_loop,
            1,
        ),
        "wrong_admission_argument": product_source.replace(
            "anchor, slot, anchor_facts, anchor_scatter_density);",
            "anchor, slot, target_facts, anchor_scatter_density);",
            1,
        ),
    }


def falling_leaf_quad_replay_destructive_mutants(
    product_source: str,
) -> dict[str, str]:
    """Recreate one retired b59 owner to prove the deletion guard."""
    return {
        "retired_b59_bank_resurrected": product_source
        + "\nlayout(set = 0, binding = 59, std430) buffer LeafParticleBank "
        + "{ uvec4 rows[]; } leaf_particle_bank;\n"
    }

# These switches asked the game to write jitter JSONL directly. The VSB
# activation deleted that in-game producer. Python may still accept the old
# option long enough to print a named ownership error, but no command sent to
# Godot may contain one of these prefixes.
RETIRED_JITTER_GODOT_PREFIXES = (
    "--fl4359-jitter-capture",
    "--fl4359-jitter-capture-path=",
    "--fl4359-jitter-capture-frame-start=",
    "--fl4359-jitter-capture-frame-limit=",
    "--fl4359-jitter-capture-world-sample-stride=",
)
EXTERNAL_RECORDER_JITTER_REASON = "external_recorder_owns_fl4359_jitter_capture"

# Every failure is collected rather than raised immediately, so one run reports
# the complete picture instead of stopping at the first problem.
FAILURES: list[str] = []
CHECKS = 0


def check(condition: bool, label: str) -> bool:
    """Record one contract check.

    condition - the thing that must be true for the contract to hold.
    label     - human-readable description printed when it fails.
    Returns the condition so callers can skip dependent work when it failed.
    """
    global CHECKS
    CHECKS += 1
    if not condition:
        FAILURES.append(label)
    return condition


def strip_comments(source: str) -> str:
    """Remove comments so symbol searches only see EXECUTABLE code.

    This matters more than it sounds. After a deletion this large, the surviving
    files legitimately mention old names in explanatory comments ("the probe
    route used to live here"). A naive text search would count those and report
    a false failure. Only code that can actually run is relevant, so both
    block comments and line comments are blanked out first.
    """
    # Remove /* ... */ block comments, including ones spanning several lines.
    source = re.sub(r"/\*.*?\*/", " ", source, flags=re.DOTALL)
    # Remove // ... line comments up to the end of their line.
    source = re.sub(r"//[^\n]*", " ", source)
    return source


def strip_gd_comments(source: str) -> str:
    """Same idea as strip_comments, for GDScript, which comments with '#'.

    GDScript has no block comments, so a single line rule is enough. Strings
    are not stripped; a deleted symbol appearing inside a live string would
    still be a real reference worth catching.
    """
    return re.sub(r"#[^\n]*", " ", source)


def extract_glsl_function(source: str, function_name: str) -> str | None:
    """Return one complete GLSL function, including nested blocks.

    source        - comment-free shader text.
    function_name - exact function name to find.
    Returns the function from its declaration through its closing brace.

    A regular expression cannot safely match a function that contains nested
    if-statements and loops because each nested block has its own braces. This
    small brace counter lets later checks inspect the live main function rather
    than accepting a matching expression in dead helper code.
    """
    signature = re.search(
        r"\b\w+(?:\s+\w+)*\s+"
        + re.escape(function_name)
        + r"\s*\([^;]*?\)\s*\{",
        source,
        re.DOTALL,
    )
    if signature is None:
        return None
    opening_brace = source.find("{", signature.start())
    depth = 0
    for index in range(opening_brace, len(source)):
        character = source[index]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return source[signature.start() : index + 1]
    return None


def extract_gd_function(source: str, function_name: str) -> str | None:
    """Return one top-level GDScript function through the next declaration.

    GDScript uses indentation instead of braces. Top-level function declarations
    therefore provide an unambiguous boundary, and preserving indentation lets
    the dispatch checks distinguish a live call from one nested under
    `if false`.
    """
    start = re.search(
        r"^(?:static\s+)?func\s+" + re.escape(function_name) + r"\b",
        source,
        re.MULTILINE,
    )
    if start is None:
        return None
    # Find the colon that ends the full signature. Several host functions place
    # parameters on following lines and put the closing `) -> Type:` at column
    # zero, so indentation alone cannot delimit the declaration.
    end = start.start()
    parenthesis_depth = 0
    declaration_finished = False
    for line in source[start.start() :].splitlines(keepends=True):
        parenthesis_depth += line.count("(") - line.count(")")
        end += len(line)
        if parenthesis_depth == 0 and line.rstrip().endswith(":"):
            declaration_finished = True
            break
    if not declaration_finished:
        return None
    # A function owns every following blank or indented line. The next
    # unindented declaration (`var`, `const`, another `func`, and comparable
    # top-level syntax) ends it. Stopping only at the next function previously
    # misattributed top-level RID declarations to the function above them.
    for line in source[end:].splitlines(keepends=True):
        if line.strip() and not line[0].isspace():
            break
        end += len(line)
    return source[start.start() : end]


def python_command_strings(source: str) -> list[str]:
    """Return string fragments placed into a local variable named cmd.

    The retired jitter options still appear in argparse declarations so an old
    caller can receive the explicit ownership error. A whole-file text search
    would mistake those parser strings for a Godot request. This AST walk looks
    only at list assignments and append/extend calls whose destination is cmd,
    which is the boundary that actually launches another process.
    """

    tree = ast.parse(source)
    values: list[str] = []

    def collect_strings(node: ast.AST) -> None:
        """Append every literal string fragment nested under one command node."""

        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                values.append(child.value)

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(
                isinstance(target, ast.Name) and target.id == "cmd"
                for target in targets
            ):
                value = node.value
                if value is not None:
                    collect_strings(value)
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if not (
            isinstance(function, ast.Attribute)
            and function.attr in ("append", "extend")
            and isinstance(function.value, ast.Name)
            and function.value.id == "cmd"
        ):
            continue
        for argument in node.args:
            collect_strings(argument)
    return values


def extract_python_function(source: str, function_name: str) -> str | None:
    """Return one Python function exactly as parsed from source.

    Function-scoped checks prevent a named failure in a comment or dead helper
    from satisfying the contract for the live command-line entry point.
    """

    tree = ast.parse(source)
    matches = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    ]
    if len(matches) != 1:
        return None
    return ast.get_source_segment(source, matches[0])


def gd_functions_referencing(source: str, token: str) -> set[str]:
    """Return the names of top-level GDScript functions that contain token.

    This is used for an ownership boundary, not ordinary style checking. The
    final visible-product RID is allowed only in its lifecycle, publication,
    Product-binding, and compositor-binding owners. A newly named legacy route
    still has to touch that RID to overwrite the visible product, so it cannot
    evade the check merely by avoiding an old symbol name.
    """
    owners: set[str] = set()
    for match in re.finditer(r"^func\s+(\w+)\b", source, re.MULTILINE):
        function_name = match.group(1)
        body = extract_gd_function(source, function_name)
        if body is not None and token in body:
            owners.add(function_name)
    return owners


def gd_target_guard_states(function_source: str, token: str) -> list[bool]:
    """Report whether each token line is nested under a literal-false guard.

    True means the line is inside `if false:` or `if 0:` and can never execute.
    False means this narrow, source-provable reachability defect is absent.
    Dynamic conditions are deliberately outside this helper's claim because
    static source cannot prove their runtime value.
    """
    false_guard_indents: list[int] = []
    states: list[bool] = []
    for raw_line in function_source.splitlines():
        expanded = raw_line.expandtabs(4)
        stripped = expanded.lstrip()
        if not stripped:
            continue
        indentation = len(expanded) - len(stripped)
        false_guard_indents = [
            guard_indent
            for guard_indent in false_guard_indents
            if indentation > guard_indent
        ]
        if token in stripped:
            states.append(bool(false_guard_indents))
        if re.match(r"if\s+(?:false|0)\s*:", stripped):
            false_guard_indents.append(indentation)
    return states


def normalize_expression(expression: str) -> str:
    """Remove whitespace so two extracted index formulas compare by value."""
    return re.sub(r"\s+", "", expression)


def compact_source(source: str) -> str:
    """Remove comments and whitespace for function-scoped expression checks."""
    return normalize_expression(strip_comments(source))


def glsl_token_guarded_by_if(
    function_source: str, token: str, guard: str
) -> bool:
    """Prove every token reference sits inside an if block naming guard.

    Counting the access tokens cannot prove that specialization disabled them:
    deleting one nearby ``if`` leaves every token and binding intact. This
    small brace walk records each guarded block, then requires every reference
    to fall inside one of those ranges. It deliberately handles only ordinary
    GLSL ``if (...) { ... }`` blocks because that is the source shape under
    contract here.
    """

    guarded_ranges: list[tuple[int, int]] = []
    guarded_if = re.compile(
        rf"\bif\s*\([^)]*\b{re.escape(guard)}\b[^)]*\)\s*\{{"
    )
    for match in guarded_if.finditer(function_source):
        open_brace = function_source.find("{", match.start(), match.end())
        if open_brace < 0:
            continue
        depth = 0
        for index in range(open_brace, len(function_source)):
            character = function_source[index]
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    guarded_ranges.append((open_brace, index))
                    break
    positions = [
        match.start() for match in re.finditer(re.escape(token), function_source)
    ]
    return bool(positions) and all(
        any(start < position < end for start, end in guarded_ranges)
        for position in positions
    )


def glsl_call_graph(source: str) -> dict[str, set[str]]:
    """Return direct call edges between named GLSL functions.

    The graph deliberately starts from parsed function bodies. Counting a
    function name across a whole shader cannot distinguish a live call from its
    declaration, which previously let the lighthouse gate pass with no beam
    reaching Product main.
    """
    code = strip_comments(source)
    names = set(
        re.findall(
            r"^\s*(?:\w+\s+)+(\w+)\s*\([^;{}]*\)\s*\{",
            code,
            re.MULTILINE,
        )
    )
    graph: dict[str, set[str]] = {}
    for name in names:
        body = extract_glsl_function(code, name)
        graph[name] = (
            set(re.findall(r"\b(\w+)\s*\(", body)) & names
            if body is not None
            else set()
        )
    return graph


def glsl_reachable_functions(source: str, root: str = "main") -> set[str]:
    """Return every function reachable from one parsed GLSL entry point."""
    graph = glsl_call_graph(source)
    reached: set[str] = set()
    pending = [root]
    while pending:
        function_name = pending.pop()
        if function_name in reached:
            continue
        reached.add(function_name)
        pending.extend(sorted(graph.get(function_name, set()) - reached))
    return reached


# =============================================================================
# SECTION 1 - The old renderer's files must be physically deleted.
# =============================================================================
# These shaders implemented the retired probe-and-raster visibility chain.
# Two GDScript objects described and tracked its per-frame state, while one
# GDScript runner exercised that state. The Python entries at the end consumed
# probe-era dumps, epochs, and in-game jitter JSONL that the retained-scene
# cutover no longer produces. Emptying any of them and commenting them out are
# insufficient: while a file exists, a future change can revive a second
# evidence owner. The contract therefore requires physical absence for every
# retired runtime and diagnostic artifact.
DELETED_FILES = (
    "godot_project/shaders/ascii_probe_capture.glsl",
    "godot_project/shaders/ascii_probe_capture_downsample.glsl",
    "godot_project/shaders/ascii_probe_clear.glsl",
    "godot_project/shaders/ascii_probe_light.glsl",
    "godot_project/shaders/ascii_probe_cull.glsl",
    "godot_project/shaders/ascii_probe_edge_mask.glsl",
    "godot_project/shaders/ascii_probe_static_face_cull.glsl",
    "godot_project/shaders/ascii_probe_splat_args.glsl",
    "godot_project/shaders/ascii_probe_splat.glsl",
    "godot_project/shaders/ascii_probe_resolve.glsl",
    "godot_project/shaders/terrain_cell_splat.glsl",
    "godot_project/shaders/terrain_cell_splat_clear.glsl",
    "godot_project/shaders/terrain_cell_resolve.glsl",
    "godot_project/scripts/render/ascii_probe_resources.gd",
    "godot_project/scripts/render/ascii_probe_state.gd",
    # The runner that exercised that state object. It called
    # load("res://scripts/render/ascii_probe_state.gd") on its first line,
    # so once the state object was deleted this file could only fail before
    # reaching a single assertion. A test that cannot run is not coverage;
    # it is a broken file that makes a suite look larger than it is.
    "godot_project/scripts/render/test_ascii_probe_state_runner.gd",
    # The old Sunday harness shared a current server across comparison arms,
    # so it could not establish matched source-owned flight.
    "scripts/fl4359_sunday_parity_jitter_harness.py",
    # This compatibility stub retained the name of a capture front door whose
    # in-game JSONL producer was physically deleted. Absence is clearer than a
    # command that can only fail.
    "scripts/run_fl4359_exact_key_capture.py",
    # These readers interpret probe G-buffer, EYE/GRID/PREV epoch, and
    # probe-splat artifacts that no surviving runtime publishes.
    "scripts/fl4359_coherence_probe.py",
    "scripts/fl4359_epoch_alternation_gate.py",
    "scripts/fl4359_epoch_flip_gate.py",
    # These tools consumed the same retired probe G-buffer, EYE/GRID/PREV
    # partition, probe-splat winner, and moving-cell dump. Their launchers
    # could still print authoritative-looking scores even though current VSB
    # publishes none of those rows, so the whole dependency closure is absent.
    "scripts/fl4359_mesh_ghost_blob_meter.py",
    "scripts/fl4359_wrong_cell_attribution.py",
    "scripts/fl4359_mesh_duel_gate.py",
    "scripts/fl4359_mesh_funnel_gate.py",
    "scripts/fl4359_truth_map_gate.py",
    "scripts/run_fl4359_ghost_pulse_gate.py",
    "scripts/fl4359_symptom_battery.py",
    "scripts/test_fl4359_wrong_cell_attribution.py",
    # These one-off probe decoders target the same deleted artifact family.
    # Keeping them executable would let historical dump formats masquerade as
    # current retained-scene evidence.
    "scripts/adhoc/2026-06-29-Compile-Godot-probe-lane-GLSL-with-glslangValidator-splittin.py",
    "scripts/adhoc/2026-07-07-FL-4359-card-island-classifier-moving-cell-dump.py",
    "scripts/adhoc/2026-07-08-FL-4359-phasec1-f6-byte-identity-audit.py",
    "scripts/adhoc/2026-07-10-Cross-tab-FL-4359-probe-owner-kind-by-rekey-diagnostic-reaso.py",
    "scripts/adhoc/2026-07-10-Decode-FL-4359-moving-probe-subcell-payload-dump-owner-count.py",
    "scripts/adhoc/2026-07-10-Decode-FL-4359-probe-payload-color-averages-by-owner-kind.py",
    # FL-4431 current-only ownership physically removed the historical
    # comparison front door and its dedicated contract. Recreating either file
    # would revive the discarded baseline/current package beside the one
    # current-source package that now owns capture and performance evidence.
    "scripts/run_fl4431_content_package.py",
    "scripts/test_fl4431_night_capture_frontdoor_contract.py",
)


def section_deleted_files() -> None:
    for rel in DELETED_FILES:
        check(not (REPO / rel).exists(), f"deleted-file survives on disk: {rel}")


# =============================================================================
# SECTION 2 - No surviving code may still name the old owners.
# =============================================================================
# Each name below was an entry point, buffer, or state flag of the retired
# chain. A single live reference means some path still expects the old system.
# The names are grouped by which retired subsystem they belonged to, so a
# failure points straight at what came back.

# Host-side (GDScript) owners: pipelines, textures, dispatch functions, and the
# runtime switches that used to let the old and new routes trade places.
DEAD_HOST_SYMBOLS = (
    "AsciiProbeState",
    "ascii_probe_state",
    "gpu_cell_splat",
    "probe_enabled",
    "probe_geometry_cutover_enabled",
    "_dispatch_probe_passes",
    "_probe_dispatch_fail_closed",
    "_ensure_probe_textures",
    "_reset_probe_rids",
    "splat_depth_rd",
    "splat_subcell_rd",
    "splat_sidecar_rd",
    "splat_clear_shader",
    "splat_shader",
    "terrain_resolve_shader",
    "_ensure_splat_textures",
    "_dispatch_splat_clear",
    "_dispatch_splat",
    "_dispatch_terrain_resolve",
    "_clear_terrain_sidecar",
    "probe_capture_shader",
    "probe_capture_downsample_shader",
    "probe_light_shader",
    "probe_cull_shader",
    "probe_edge_mask_shader",
    "probe_splat_shader",
    "probe_resolve_shader",
    "probe_subcell_diag_rd",
    "probe_origins_ubo_rd",
    "_recreate_dynamic_mover_mesh_faces",
    "_recreate_static_world_mesh",
    "_rebuild_static_mesh_bvh",
    "set_static_world_mesh",
    "_sync_shared_caster_counts",
    "_refresh_capture_cull_snapshot",
    # The retired perspective-route switch. This symbol WAS already checked --
    # but only inside the compositor shader, where it is indeed gone. The host
    # kept its own copy of the constant and kept writing it into a flags word
    # that no shader reads any more, and a check that looks at one file cannot
    # see residue in another. Listing it here, where the host is the subject,
    # is what closes that gap. The lesson generalises: a retired symbol has to
    # be checked on EVERY surface that could still name it, not on the one
    # surface where the deletion happened to be noticed.
    "TRUE_PERSPECTIVE_DISPLAY_ROUTE_FLAG",
)

# Shader-side owners inside the surviving product shader: the images the probe
# chain wrote, the record it published, the flat geometry banks that the
# retained scene database replaced, and the temporary dual-compute comparator.
DEAD_PRODUCT_SYMBOLS = (
    "probe_receiver_image",
    "probe_normal_image",
    "probe_lit_image",
    "probe_matcell_image",
    "probe_entity_image",
    "probe_meta_image",
    "probe_texel_image",
    "probe_anchor_image",
    "probe_water_depth_image",
    "probe_eye_position_tex",
    "probe_eye_matcell_image",
    "probe_eye_entity_image",
    "ProbeControl",
    "probe_control",
    "probe_face_control",
    "ProbeSelectedRecord",
    "product_probe_geometry_cutover_enabled",
    "product_hit_from_probe_record",
    "splat_depth_image",
    "splat_sidecar_image",
    "TerrainSidecar",
    "resolve_raster_splat_owner",
    "resolve_dynamic_mover_splat_owner",
    "StaticWorldMeshFaces",
    "static_world_mesh_faces",
    "DynamicMoverMeshFaces",
    "dynamic_mover_mesh_faces",
    "q3_probe_payload_compile_cell",
    "product_step8_identity_probe",
    "product_step8_actor_record_complete",
    "product_step8_dynamic_record_complete",
    "product_step8_beam_record_complete",
    "Q3_PAYLOAD_CALL_REQUIRES_PRODUCT_IS_PERSPECTIVE",
)

# Compositor-side owners: the per-cell buffer, the projection test, and the
# substitute paint that used to disguise a missing record as blue sky.
DEAD_COMPOSITOR_SYMBOLS = (
    "TRUE_PERSPECTIVE_DISPLAY_ROUTE_FLAG",
    "CellOwnerRecord",
    "ProbeSubcellResolve",
    "load_probe_subcell_product",
    "compositor_is_perspective",
    "completed_actor_cell_owner_product",
    "completed_dynamic_mesh_cell_owner_product",
    "completed_beam_cell_owner_product",
    "fail_closed_probe_resolve",
    "COMPOSITOR_SKY_FALLBACK_BG",
)


def section_dead_symbols() -> None:
    host = strip_gd_comments(ACE_PATH.read_text(encoding="utf-8", errors="replace"))
    product = strip_comments(_expand_includes(PRODUCT_PATH.read_text(encoding="utf-8", errors="replace")))
    compositor = strip_comments(COMPOSITOR_PATH.read_text(encoding="utf-8", errors="replace"))

    for sym in DEAD_HOST_SYMBOLS:
        check(sym not in host, f"retired host symbol still live in ASCIICompositorEffect.gd: {sym}")
    for sym in DEAD_PRODUCT_SYMBOLS:
        check(sym not in product, f"retired shader symbol still live in cell_owner_product.glsl: {sym}")
    for sym in DEAD_COMPOSITOR_SYMBOLS:
        check(sym not in compositor, f"retired symbol still live in ascii_compositor.glsl: {sym}")

    # A name blacklist catches an old function pasted back verbatim, but it
    # cannot catch the same second writer after every identifier is renamed.
    # The visible 80-byte product RID is the harder ownership boundary: any
    # shipping path that overwrites what the compositor reads must eventually
    # touch this RID. Pinning the small reviewed owner set catches a renamed
    # legacy publication path without banning harmless historical words.
    #
    # This is intentionally an owner allow-list. A legitimate future lifecycle
    # refactor can update the list in the same reviewed patch; silently adding a
    # new function that can touch final visible bytes is exactly the event this
    # activation contract is supposed to stop.
    expected_final_product_rid_owners = {
        "_notification",
        "_render_callback",
        "_ensure_vsb_view_buffers",
        "_build_vsb_view_dispatch",
        "_prepare_cell_owner_product_dispatch",
        "_upload_vsb_final_failure_prefill",
    }
    actual_final_product_rid_owners = gd_functions_referencing(
        host, "_vsb_final_product_rd"
    )
    check(
        actual_final_product_rid_owners == expected_final_product_rid_owners,
        "the final visible-product RID must remain confined to its six reviewed "
        "lifecycle/publication/binding owners; found "
        f"{sorted(actual_final_product_rid_owners)}",
    )


def section_dead_perf_and_debug_owners() -> None:
    """Prove rejected experiments and retired debug owners are physically gone.

    Default-off code still occupies bindings, compilation surface, host state,
    and future maintenance attention. These implementations were measured
    slower, completed their diagnostic purpose, and were deleted. Each check
    spans both sides of its former ABI so a shader-only deletion cannot leave a
    host binding that kills uniform-set creation, and a host-only deletion
    cannot leave dead GPU resources behind.
    """

    product = strip_comments(
        _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8", errors="replace"))
    )
    vsb = strip_comments(VSB_PATH.read_text(encoding="utf-8", errors="replace"))
    host = strip_gd_comments(
        ACE_PATH.read_text(encoding="utf-8", errors="replace")
    )
    builder = strip_gd_comments(
        BUILDER_PATH.read_text(encoding="utf-8", errors="replace")
    )
    world = strip_gd_comments(
        WORLD_PATH.read_text(encoding="utf-8", errors="replace")
    )
    compositor = strip_comments(
        COMPOSITOR_PATH.read_text(encoding="utf-8", errors="replace")
    )

    product_dead_tokens = (
        "PRODUCT_SHADOW_RECEIVER_CACHE_ENABLED",
        "product_shadow_receiver_cache_lookup",
        "product_shadow_receiver_cache_store",
        "product_shadow_receiver_cache_reset",
        "product_shadow_receiver_cache_a_",
        "product_shadow_receiver_cache_b_",
        "PRODUCT_SHADOW_PREPASS_ENABLED",
        "product_shadow_prepass_",
        "product_emit_shadow_receiver_prepass",
        "product_shadow_receiver_prepass",
        "PRODUCT_SHADOW_WG_CACHE_ENABLED",
        "product_shadow_wg_cache_",
        "product_shadow_wg_hash_",
        "PRODUCT_SHADOW_WG_HASH_",
        "PRODUCT_PHASE_COUNTERS_ENABLED",
        "PRODUCT_COUNTER_",
        "product_phase_count",
        "ProductPhaseCounters",
        "product_phase_counters",
    )
    for token in product_dead_tokens:
        check(
            token not in product,
            f"retired Product performance owner still executable: {token}",
        )
    check(
        re.search(
            r"layout\s*\([^)]*\bbinding\s*=\s*85\b[^)]*\)\s*"
            r"(?:readonly\s+|writeonly\s+)?buffer\b",
            product,
        )
        is None,
        "retired Product b85 shadow-prepass buffer must be physically absent",
    )
    check(
        re.search(
            r"layout\s*\([^)]*\bbinding\s*=\s*83\b[^)]*\)\s*"
            r"(?:readonly\s+|writeonly\s+)?buffer\s+ProductPhaseCounters\b",
            product,
        )
        is None,
        "retired Product b83 phase-counter block must be physically absent",
    )

    host_dead_tokens = (
        "_fl4431_perf_enable_shadow_receiver_cache",
        "FL4431_PERF_ENABLE_SHADOW_RECEIVER_CACHE",
        "product_shadow_receiver_cache_specialization",
        "enable_shadow_receiver_cache",
        "_fl4431_perf_enable_shadow_prepass",
        "FL4431_PERF_ENABLE_SHADOW_PREPASS",
        "product_shadow_prepass_specialization",
        "_product_shadow_prepass_rd",
        "u_product_shadow_prepass",
        "shadow_prepass_active",
        "enable_shadow_prepass",
        "_fl4431_perf_enable_shadow_wg_cache",
        "FL4431_PERF_ENABLE_SHADOW_WG_CACHE",
        "product_shadow_wg_cache_specialization",
        "enable_shadow_wg_cache",
        "_fl4431_product_phase_counters_enabled",
        "FL4431_PRODUCT_PHASE_COUNTERS",
        "PRODUCT_PHASE_COUNTER_",
        "_read_product_phase_counters",
        "_product_phase_counter_",
        "_product_phase_counters_rd",
        "_fl4431_vsb_phase_counters_enabled",
        "FL4431_VSB_PHASE_COUNTERS",
        "VSB_PHASE_COUNTER_",
        "_read_vsb_phase_counters",
        "_vsb_phase_counter_",
        "_vsb_phase_counters_rd",
        "_fl4231_light_diag_tick",
        "ASCIICKER_FL4231_LIGHT_DIAG",
        "_fl4270_projection_diag_printed",
        "_fl4270_persp_diag_printed",
        # The retired ghost-pulse route was the sole positive caller of this
        # animation-clock override. Keeping the default-false branch in the
        # frame callback would leave dead proof behavior in ordinary render
        # code after its dump consumer was deleted.
        "fl4359_freeze_animation",
        "--fl4359-freeze-animation",
    )
    for token in host_dead_tokens:
        check(token not in host, f"retired host owner still executable: {token}")

    vsb_dead_tokens = (
        "VSB_PHASE_COUNTERS_ENABLED",
        "VSB_COUNTER_",
        "vsb_phase_count",
        "VsbPhaseCounters",
        "vsb_phase_counters",
    )
    for token in vsb_dead_tokens:
        check(token not in vsb, f"retired VSB counter owner still executable: {token}")
    check(
        re.search(r"layout\s*\([^)]*\bbinding\s*=\s*84\b", vsb) is None,
        "retired VSB b84 phase-counter resource must be physically absent",
    )
    check(
        "BINDING_VSB_PHASE_COUNTERS" not in builder,
        "retired VSB b84 builder constant must be physically absent",
    )

    pixel_debug_tokens = (
        "_final_render_dump_pixel_debug",
        "--final-render-dump-pixel-debug",
        "pixel_debug_manifest",
        "write_latest_pixel_debug_binary",
    )
    for token in pixel_debug_tokens:
        check(token not in world, f"retired World pixel-debug owner survives: {token}")
    for token in ("write_latest_pixel_debug_binary", "pixel_debug"):
        check(token not in host, f"retired ACE pixel-debug owner survives: {token}")
        check(
            token not in compositor,
            f"retired compositor pixel-debug resource survives: {token}",
        )


# =============================================================================
# SECTION 3 - One visible payload plus receipt-owned projection paint.
# =============================================================================
# This is the heart of the cutover. The compositor is the last stage before
# pixels reach the screen, so if it can read more than one source of truth,
# the whole single-owner argument collapses. Binding 44 remains that one source.
# Binding 68 is non-payload metadata: it reuses Product's frozen frame receipt
# solely to select Baseline A perspective paint without rebuilding projection.


def parse_glsl_binding_kinds(source: str) -> dict[int, tuple[str, str]]:
    """Return each declared binding as slot -> (resource kind, declared name).

    The compositor ABI is deliberately tiny. Treating sampled images, storage
    images, uniform blocks, and storage buffers as distinct resource kinds
    catches a second payload smuggled through a type the old buffer-only regex
    never counted. Duplicate slots are encoded with a `duplicate:` prefix so
    they fail the exact-map comparison instead of silently overwriting.
    """
    result: dict[int, tuple[str, str]] = {}
    declaration_pattern = re.compile(
        r"layout\s*\(([^)]*\bbinding\s*=\s*\d+[^)]*)\)\s*"
        r"(?:(readonly|writeonly)\s+)?"
        r"(uniform|buffer)\s+(?:restrict\s+)?(\w+)",
        re.MULTILINE,
    )
    for declaration in declaration_pattern.finditer(source):
        layout_text, access, declaration_kind, declared_name = declaration.groups()
        slot_match = re.search(r"\bbinding\s*=\s*(\d+)", layout_text)
        if slot_match is None:
            continue
        slot = int(slot_match.group(1))
        if declaration_kind == "uniform":
            resource_kind = declared_name
        else:
            resource_kind = (
                f"{access} buffer" if access is not None else "buffer"
            )
        signature = (resource_kind, declared_name)
        if slot in result:
            result[slot] = (f"duplicate:{resource_kind}", declared_name)
        else:
            result[slot] = signature
    return result


def parse_live_product_array_type(source: str, binding: int) -> str | None:
    """Return the record type used by the live `products[]` buffer at binding.

    Reviewing a struct declaration is useless when the live buffer switched to
    a different struct and left the reviewed declaration behind as dead text.
    This helper follows the binding to its actual unsized array element type.
    """
    block = re.search(
        r"layout\s*\([^)]*\bbinding\s*=\s*"
        + str(binding)
        + r"\b[^)]*\)\s*(?:readonly|writeonly)?\s*buffer\s+\w+\s*"
        r"\{(.*?)\}\s*\w+\s*;",
        source,
        re.DOTALL,
    )
    if block is None:
        return None
    array_member = re.search(r"\b(\w+)\s+products\s*\[\s*\]\s*;", block.group(1))
    return None if array_member is None else array_member.group(1)


def producer_slot_axis_bits(function_body: str) -> tuple[int, int] | None:
    """Decode which slot bit one Product subcell loop assigns to X and Y.

    `slot & 1u` means bit zero and `slot >> 1u` means bit one. Returning the
    numeric pair makes the comparison semantic: whitespace and line wrapping
    can change freely, while swapping X and Y still fails. The expression is
    anchored to the named ``subcell = ansi_xy * 2`` assignment so an unrelated
    ``ivec2`` earlier in the same function cannot satisfy this ABI check.
    """
    pair = re.search(
        r"\bivec2\s+subcell\s*=\s*ansi_xy\s*\*\s*2\s*\+\s*"
        r"ivec2\s*\(\s*int\s*\((.*?)\)\s*,\s*int\s*\((.*?)\)\s*\)\s*;",
        function_body,
        re.DOTALL,
    )
    if pair is None:
        return None

    def bit_number(expression: str) -> int | None:
        normalized = normalize_expression(expression)
        if normalized == "slot&1u":
            return 0
        if normalized == "slot>>1u":
            return 1
        return None

    x_bit = bit_number(pair.group(1))
    y_bit = bit_number(pair.group(2))
    if x_bit is None or y_bit is None:
        return None
    return (x_bit, y_bit)


def consumer_slot_axis_bits(slot_function: str) -> tuple[int, int] | None:
    """Decode the bit positions used for compositor X and Y half selection."""
    normalized = normalize_expression(slot_function)
    if "returnsx|(sy<<1u);" in normalized:
        return (0, 1)
    if "returnsy|(sx<<1u);" in normalized:
        return (1, 0)
    return None


def section_compositor_single_authority() -> None:
    raw = COMPOSITOR_PATH.read_text(encoding="utf-8", errors="replace")
    code = strip_comments(raw)
    product_code = strip_comments(
        _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8", errors="replace"))
    )
    compositor_main = extract_glsl_function(code, "main")
    product_main = extract_glsl_function(product_code, "main")

    # 3.1 Exactly one storage buffer supplies finished visible payload. The
    # paint-receipt buffer at binding 43 is a diagnostic output, not an input,
    # so it is identified separately and excluded from this count.
    product_bindings = re.findall(
        r"layout\s*\(\s*set\s*=\s*0\s*,\s*binding\s*=\s*(\d+)\s*,\s*std430\s*\)\s*readonly\s+buffer",
        code,
    )
    check(
        product_bindings == ["44"],
        "compositor must declare exactly one readonly product buffer at binding 44, "
        f"found bindings {product_bindings}",
    )

    # Counting one class of declaration is not enough: a second visible product
    # can be carried through a sampled image while the readonly-buffer count
    # stays one. The exact resource-kind map guards the whole compositor set.
    # Adding a legitimate new atlas or receipt is allowed, but it must update
    # this reviewed ABI map in the same patch instead of silently becoming a
    # second source of pixels.
    expected_compositor_resources = {
        0: ("image2D", "image2D"),
        4: ("sampler2D", "sampler2D"),
        5: ("sampler2D", "sampler2D"),
        6: ("sampler2D", "sampler2D"),
        43: ("buffer", "FinalCellPaintBuffer"),
        44: ("readonly buffer", "FinalizedSubcellProducts"),
        68: ("VsbProductFrameReceipt", "VsbProductFrameReceipt"),
    }
    actual_resources = parse_glsl_binding_kinds(code)
    # Uniform declarations expose the resource type where buffer declarations
    # expose the block name. Normalize only the uniform name half so both cases
    # remain easy to read in a failure.
    normalized_resources = {
        slot: (
            resource_kind,
            resource_kind if resource_kind in {"image2D", "sampler2D"} else declared_name,
        )
        for slot, (resource_kind, declared_name) in actual_resources.items()
    }
    check(
        normalized_resources == expected_compositor_resources,
        "compositor resource ABI must contain only the output image, three glyph "
        "atlases, paint receipt, sole b44 product, and frozen b68 frame receipt; found "
        f"{normalized_resources}",
    )

    # 3.2 The index law must match the producer exactly: four consecutive
    # records per cell. If the two sides disagree by even one multiplier, every
    # pixel reads a neighbour's answer -- a failure that still "looks like a
    # picture", which is precisely why it needs a mechanical check.
    check(
        re.search(r"cell_index\s*\*\s*4u\s*\+\s*slot", code) is not None,
        "compositor must index the product buffer as cell_index * 4u + slot",
    )
    if check(compositor_main is not None, "compositor main function not found") \
            and check(product_main is not None, "Product main function not found"):
        consumer_index = re.search(
            r"finalized_subcell_products\.products\s*\[(.*?)\]",
            compositor_main,
            re.DOTALL,
        )
        producer_index = re.search(
            r"\bfinal_index\s*=\s*(.*?);",
            product_main,
            re.DOTALL,
        )
        if check(consumer_index is not None, "live compositor product index not found") \
                and check(producer_index is not None, "live Product final index not found"):
            consumer_value = normalize_expression(consumer_index.group(1))
            producer_value = normalize_expression(producer_index.group(1))
            check(
                producer_value == consumer_value == "cell_index*4u+slot",
                "producer and compositor must use the same four-record index "
                f"law; producer={producer_value}, consumer={consumer_value}",
            )

        # The record stride can agree while the four records are assigned to
        # the wrong quarters. Decode both live formulas to numeric bit
        # positions rather than pinning formatting; this catches a transpose
        # that keeps every index in range.
        slot_function = extract_glsl_function(code, "final_subcell_slot")
        if (
            check(
                slot_function is not None,
                "compositor slot-packing function not found",
            )
        ):
            producer_bits = producer_slot_axis_bits(product_main)
            consumer_bits = consumer_slot_axis_bits(slot_function)
            check(
                producer_bits == consumer_bits == (0, 1),
                "Product publication and compositor "
                "slot packing must agree that X uses bit 0 and Y uses bit 1; "
                f"producer={producer_bits}, consumer={consumer_bits}",
            )

    # 3.3 Projection may choose only the paint grammar. The retired design chose
    # a different payload buffer by projection; binding 44 now stays invariant.
    # Baseline A perspective paint is selected exclusively from the already
    # frozen Product-frame receipt at b68.
    #
    # The camera matrix FIELD stays declared in the push-constant block, because
    # that block's memory layout is a shared agreement with the host code and
    # removing a field would shift every field after it. What must not exist is
    # any USE of it: reading params.inv_view_proj is how the compositor used to
    # rebuild rays and decide the projection family for itself.
    check(
        "params.inv_view_proj" not in code,
        "compositor must not read params.inv_view_proj (ray reconstruction / projection test)",
    )
    projection_helper = extract_glsl_function(
        code, "compositor_uses_perspective_paint"
    )
    if check(
        projection_helper is not None,
        "compositor must expose the receipt-owned perspective paint selector",
    ):
        projection_expression = normalize_expression(projection_helper)
        check(
            "returnproduct_vsb_frame.view_identity.z"
            "==VSB_PROJECTION_PERSPECTIVE;" in projection_expression,
            "perspective paint must consume only b68 view_identity.z",
        )
        check(
            "params." not in projection_helper,
            "perspective paint selector must not derive projection from push data",
        )
    # A projection-local paint branch can hide behind another push field. The
    # live main function needs only grid dimensions and glyph dimensions. Its
    # one approved misc.w feature read lives in the derivative-AA helper. The
    # final-cell evidence flag was physically deleted from this shipping
    # variant; the capture variant is selected through a compile-time define.
    # Pinning the remaining owner catches a new push-flag branch.
    misc_w_owners = {
        function_name
        for function_name in (
            "derivative_aa_enabled",
            "main",
        )
        if (
            (function_body := extract_glsl_function(code, function_name))
            is not None
            and "params.misc.w" in function_body
        )
    }
    check(
        misc_w_owners == {"derivative_aa_enabled"},
        "params.misc.w may be read only by the projection-independent "
        f"derivative-AA helper; found owners {sorted(misc_w_owners)}",
    )

    # 3.4 Exactly one write to the output image. Two stores would mean two paint
    # paths, which is the same two-owner defect in a different costume.
    stores = re.findall(r"\bimageStore\s*\(", code)
    direct_color_stores = re.findall(r"\bimageStore\s*\(\s*color_image", code)
    check(
        len(stores) == 1 and len(direct_color_stores) == 1,
        "compositor must contain one total imageStore and it must target "
        f"color_image directly; total={len(stores)}, direct={len(direct_color_stores)}",
    )

    # 3.5 Final paint remains Product-only. The compositor samples the elected
    # Product mask and continuous FG/BG. Foliage retains its explicit sparse-
    # content visibility fact; terrain morphology has no downstream family-
    # inferred center pin.
    check(
        re.search(r"mix\s*\(\s*resolved\.bg\s*,\s*resolved\.fg\s*,\s*intensity\s*\)", code) is not None,
        "orthographic paint must retain mix(resolved.bg, resolved.fg, intensity)",
    )
    check(
        re.search(r"color\s*=\s*chose_fg\s*\?\s*resolved\.fg\s*:\s*resolved\.bg", code)
        is not None,
        "perspective paint must retain Baseline A binary fg/bg selection",
    )
    check(
        "return mountain_present ? 0.28 : (terrain_present ? 0.34 : 0.5);"
        in raw,
        "perspective thresholds must stay 0.28 mountain, 0.34 terrain, 0.5 default",
    )
    check(
        "terrain_center_pin" not in raw
        and "mountain_product_present" not in raw
        and "material_family == TERRAIN_FAMILY_WATER_SKIP" not in compositor_main
        and "material_family == TERRAIN_FAMILY_CLIFF_ROCK" not in compositor_main,
        "final paint must not infer terrain or mountain morphology from Product family",
    )
    check(
        "bool foliage_center_pin = cell_mask_foliage_present" in raw
        and "float(cell_span_px.x) * 0.12" in raw
        and "float(cell_span_px.y) * 0.12" in raw
        and "if (foliage_center_pin) {" in raw,
        "Product's foliage fact must retain the bounded 12-percent pin",
    )
    check(
        "0.22" not in code and "0.30" not in code,
        "historically unreachable flower and foliage thresholds must stay absent",
    )

    if compositor_main is not None:
        # Perspective must take the nearest branch even when derivative AA is
        # enabled. Orthographic alone may enter the existing four-tap AA branch.
        check(
            "if (!perspective_paint && derivative_aa_enabled()) {" in compositor_main,
            "derivative AA must remain orthographic-only; perspective uses nearest sampling",
        )
        check(
            "if (perspective_paint) {" in compositor_main,
            "binary threshold paint must remain scoped to perspective",
        )
        check(
            re.search(
                r"if\s*\(\s*foliage_center_pin\s*\)\s*\{.*?"
                r"color\s*=\s*resolved\.fg\s*;.*?"
                r"else\s*\{.*?color\s*=\s*mix\s*\(",
                compositor_main,
                re.DOTALL,
            )
            is not None,
            "final paint must keep the foliage pin before continuous Product mix",
        )
        # There are exactly two legitimate assignments to intensity: one in
        # orthographic AA and one in the shared nearest-sample branch.
        intensity_assignments = re.findall(r"\bintensity\s*=", compositor_main)
        check(
            len(intensity_assignments) == 2,
            "compositor main must assign intensity only in its two sampler "
            f"branches; found {len(intensity_assignments)} assignments",
        )
        # One assignment belongs to perspective, the shared orthographic pin,
        # and ordinary orthographic continuous paint.
        color_assignments = re.findall(r"\bcolor\s*=", compositor_main)
        check(
            len(color_assignments) == 3,
            "compositor main must author color exactly once per reviewed branch "
            f"mix; found {len(color_assignments)} assignments",
        )
        check(
            compositor_main.count("product.r0.x") == 1
            and "uint product_status = product.r0.x;" in compositor_main
            and "final_product_status_has_foliage(product_status)"
            in compositor_main
            and not re.search(
                r"if\s*\([^)]*product_status[^)]*\)\s*"
                r"(?:\{[^}]*\bcolor\s*=|\bcolor\s*=)",
                compositor_main,
                re.DOTALL,
            ),
            "compositor may decode foliage from status exactly once but must "
            "never substitute color when status is invalid",
        )


# =============================================================================
# SECTION 4 - Producer and consumer must agree on the record layout.
# =============================================================================
# The product record is raw memory: 80 bytes, five rows of 16. Nothing in the
# graphics API checks that the writing shader and the reading shader describe
# those bytes the same way. If one side inserts, removes, or reorders a field,
# every later field is read from the wrong offset and the image degrades in
# confusing, non-obvious ways. This check compares the two declarations
# field by field.

# Field kinds in declaration order. Names may differ between the two files
# (the producer calls it ProbeSubcellProductRecord for historical reasons);
# what must match is the sequence of types, because that is what fixes offsets.
EXPECTED_RECORD_FIELDS = (
    ("uvec4", "r0"),      # ABI-v2 status, owner kind, glyph, material family
    ("uvec4", "r1"),      # material id, stable id, entity id, packed material cell
    ("vec4", "fg"),       # foreground colour (the glyph's ink)
    ("vec4", "bg"),       # background colour (the paper behind it)
    ("vec4", "anchor"),   # world-space anchor of the sample
)


def section_final_product_status_abi() -> None:
    """Pin the semantic ABI layered into r0.x without growing the 80-byte row."""
    product_raw = _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8", errors="replace"))
    compositor_raw = COMPOSITOR_PATH.read_text(encoding="utf-8", errors="replace")
    product = strip_comments(product_raw)
    compositor = strip_comments(compositor_raw)

    shared_constants = (
        "const uint FINAL_SUBCELL_PRODUCT_ABI_VERSION = 2u;",
        "const uint FINAL_PRODUCT_STATUS_VALID = 1u << 0u;",
        "const uint FINAL_PRODUCT_STATUS_FOLIAGE_PRESENT = 1u << 1u;",
        "const uint FINAL_PRODUCT_STATUS_ABI_SHIFT = 24u;",
        "const uint FINAL_PRODUCT_STATUS_ABI_MASK = 0xFF000000u;",
    )
    for token in shared_constants:
        check(
            token in product_raw and token in compositor_raw,
            f"producer and compositor must share final status ABI token: {token}",
        )

    producer_valid_helper = extract_glsl_function(
        product, "final_product_status_is_valid"
    )
    consumer_valid_helper = extract_glsl_function(
        compositor, "final_product_status_is_valid"
    )
    check(
        producer_valid_helper is not None
        and consumer_valid_helper is not None
        and normalize_expression(producer_valid_helper)
        == normalize_expression(consumer_valid_helper),
        "producer and compositor disagree on final_product_status_is_valid",
    )
    expected_valid_helper = normalize_expression(
        """
        bool final_product_status_is_valid(uint status) {
            return (status & FINAL_PRODUCT_STATUS_VALID) != 0u
                && (status & FINAL_PRODUCT_STATUS_ABI_MASK)
                    == FINAL_PRODUCT_STATUS_ABI_V2
                && (status & ~FINAL_PRODUCT_STATUS_KNOWN_MASK) == 0u;
        }
        """
    )
    expected_foliage_helper = normalize_expression(
        """
        bool final_product_status_has_foliage(uint status) {
            return final_product_status_is_valid(status)
                && (status & FINAL_PRODUCT_STATUS_FOLIAGE_PRESENT) != 0u;
        }
        """
    )
    check(
        normalize_expression(
            extract_glsl_function(product, "final_product_status_is_valid") or ""
        )
        == expected_valid_helper,
        "status validity must reject version drift, unknown bits, and missing valid",
    )
    check(
        normalize_expression(
            extract_glsl_function(
                compositor, "final_product_status_has_foliage"
            )
            or ""
        )
        == expected_foliage_helper,
        "compositor foliage consumption must require recognized ABI-v2 validity",
    )
    check(
        "final_product_status_has_foliage" not in product,
        "Product must not retain an unused final foliage query helper",
    )
    compositor_main = extract_glsl_function(compositor, "main")
    check(
        compositor_main is not None
        and compositor_main.count(
            "final_product_status_has_foliage(product_status)"
        )
        == 1,
        "compositor must consume foliage only through the recognized status helper",
    )

    version = 2
    valid_bit = 1 << 0
    foliage_bit = 1 << 1
    version_shift = 24
    version_mask = 0xFF000000
    known_mask = version_mask | valid_bit | foliage_bit
    abi_v2 = version << version_shift

    def status_is_valid(status: int) -> bool:
        return (
            status & valid_bit != 0
            and status & version_mask == abi_v2
            and status & ~known_mask == 0
        )

    def status_has_foliage(status: int) -> bool:
        return status_is_valid(status) and status & foliage_bit != 0

    vectors = (
        ("empty", 0x00000000, False, False),
        ("ordinary", 0x02000001, True, False),
        ("foliage", 0x02000003, True, True),
        ("version_drift", 0x01000001, False, False),
        ("unknown_bit", 0x02000005, False, False),
        ("foliage_without_valid", 0x02000002, False, False),
    )
    for name, status, expected_valid, expected_foliage in vectors:
        check(
            status_is_valid(status) == expected_valid,
            f"status vector {name} validity mismatch",
        )
        check(
            status_has_foliage(status) == expected_foliage,
            f"status vector {name} foliage mismatch",
        )

    foliage_apply = extract_glsl_function(
        product, "q3_probe_payload_apply_foliage"
    )
    visible_record = extract_glsl_function(
        product, "q3_probe_payload_visible_record"
    )
    finalizer = extract_glsl_function(product, "vsb_product_finalize_record")
    product_main = extract_glsl_function(product, "main")
    check(
        foliage_apply is not None
        and "out bool foliage_product_present" in foliage_apply
        and "foliage_product_present = false;" in foliage_apply
        and "foliage_product_present = true;" in foliage_apply,
        "accepted Product foliage must publish an explicit semantic out-fact",
    )
    check(
        visible_record is not None
        and "bool foliage_present" in visible_record
        and "final_product_status(foliage_present)" in visible_record,
        "the final record constructor must pack the explicit foliage fact",
    )
    check(
        finalizer is not None
        and "record.r0.x &= ~FINAL_PRODUCT_STATUS_FOLIAGE_PRESENT;"
        in finalizer
        and finalizer.index(
            "record.r0.x &= ~FINAL_PRODUCT_STATUS_FOLIAGE_PRESENT;"
        )
        < finalizer.index("record.r0.y = OWNER_ACTOR_SPRITE;"),
        "actor takeover must clear foliage before publishing actor identity",
    )
    check(
        product_main is not None
        and "vsb_product_leaf_for_subcell" not in product
        and "vsb_product_apply_leaf" not in product
        and "vsb_product_consider_leaf_lane" not in product
        and "vsb_product_consider_leaf_record" not in product
        and "staged_leaf_admitted" not in product_main,
        "retired falling-leaf replay must remain physically absent",
    )
    check(
        "record.r0.x == 0u" not in product
        and "record.r0.x != 0u" not in product
        and "final_record.r0.x == 0u" not in product
        and "final_record.r0.x != 0u" not in product
        and product.count("final_product_status_is_valid(record.r0.x)") == 2
        and product.count("final_product_status_is_valid(final_record.r0.x)")
        == 2
        and product.count(
            "final_product_status_is_valid(\n"
            "\t\t\t\tstaged_final_records[slot].r0.x)"
        ) == 1,
        "all five live final-record validity readers must use strict ABI-v2 "
        "validation instead of whole-word truthiness",
    )
    check(
        "q3_probe_subcell_diagnostics" not in compositor
        and "Q3_DIAG_MARKER_FOLIAGE" not in compositor,
        "binding 62 diagnostic markers must stay excluded from presentation",
    )


def parse_struct_fields(source: str, struct_name: str) -> list[tuple[str, str]] | None:
    """Return the declared fields of one GLSL struct, in order, as (type, name).

    source      - shader text with comments already removed.
    struct_name - the struct to look for.
    Returns [("uvec4", "r0"), ("uvec4", "r1"), ...], or None if not found.

    The NAME matters as much as the type, and leaving it out was a real hole.
    This record's first two rows are both uvec4: row 0 carries validity, owner
    and glyph; row 1 carries provenance ids. Swap the two declarations and the
    byte count is unchanged, the type sequence is unchanged, and a types-only
    comparison sees nothing wrong -- while the reader now takes provenance ids
    for validity and draws the wrong thing everywhere. Mutation testing caught
    exactly that swap surviving, which is why names are compared now.
    """
    match = re.search(r"struct\s+" + re.escape(struct_name) + r"\s*\{(.*?)\}\s*;", source, re.DOTALL)
    if match is None:
        return None
    body = match.group(1)
    # Each member is "<type> <name>;" -- capture both tokens.
    return re.findall(r"\b(uvec4|ivec4|vec4|uint|int|float)\b\s+(\w+)\s*;", body)


def section_record_abi_mirror() -> None:
    product = strip_comments(_expand_includes(PRODUCT_PATH.read_text(encoding="utf-8", errors="replace")))
    compositor = strip_comments(COMPOSITOR_PATH.read_text(encoding="utf-8", errors="replace"))

    producer_fields = parse_struct_fields(product, "ProbeSubcellProductRecord")
    consumer_fields = parse_struct_fields(compositor, "FinalSubcellProductRecord")
    producer_live_type = parse_live_product_array_type(product, 55)
    consumer_live_type = parse_live_product_array_type(compositor, 44)

    if check(producer_fields is not None, "producer struct ProbeSubcellProductRecord not found in cell_owner_product.glsl") \
            and check(consumer_fields is not None, "consumer struct FinalSubcellProductRecord not found in ascii_compositor.glsl"):
        check(
            tuple(producer_fields) == EXPECTED_RECORD_FIELDS,
            f"producer record layout drifted: expected {EXPECTED_RECORD_FIELDS}, found {tuple(producer_fields)}",
        )
        check(
            tuple(consumer_fields) == EXPECTED_RECORD_FIELDS,
            f"consumer record layout drifted: expected {EXPECTED_RECORD_FIELDS}, found {tuple(consumer_fields)}",
        )
        check(
            producer_fields == consumer_fields,
            "producer and compositor disagree on the 80-byte product record layout: "
            f"{producer_fields} vs {consumer_fields}",
        )

        # The declarations above are useful only if the live producer and live
        # b44 consumer arrays actually use them. A stale 80-byte mirror can stay
        # perfect while a newly introduced 96-byte type becomes the real array
        # element, shifting every record after the first. Follow each binding to
        # its products[] member and compare the actual types on both sides.
        check(
            producer_live_type == "ProbeSubcellProductRecord",
            "Product binding 55 must write ProbeSubcellProductRecord, found "
            f"{producer_live_type}",
        )
        check(
            consumer_live_type == "FinalSubcellProductRecord",
            "compositor binding 44 must read FinalSubcellProductRecord, found "
            f"{consumer_live_type}",
        )
        live_producer_fields = (
            parse_struct_fields(product, producer_live_type)
            if producer_live_type is not None
            else None
        )
        live_consumer_fields = (
            parse_struct_fields(compositor, consumer_live_type)
            if consumer_live_type is not None
            else None
        )
        check(
            live_producer_fields == live_consumer_fields == list(EXPECTED_RECORD_FIELDS),
            "the live b55 writer and b44 reader must both use the reviewed "
            f"80-byte fields; producer={live_producer_fields}, "
            f"consumer={live_consumer_fields}",
        )


# =============================================================================
# SECTION 5A - Every shipping shader must still compile for Vulkan 1.1.
# =============================================================================
# Text checks cannot catch a syntax error or a type mismatch. glslangValidator
# is the reference compiler for this shader language, so it is the honest
# arbiter of "this file is valid". Godot prefixes its compute shaders with a
# "#[compute]" marker line that the standalone compiler does not understand,
# so that first line is removed before validation.
SHIPPING_SHADERS = (PRODUCT_PATH, COMPOSITOR_PATH, VSB_PATH)


def section_shaders_compile() -> None:
    for path in SHIPPING_SHADERS:
        if not check(path.exists(), f"shipping shader missing: {path.name}"):
            continue
        # Reuse the exact normalization consumed by the Metal chain so both
        # backends inspect identical shader text after removing Godot's marker.
        body = _shader_body_for_standalone_compiler(path)
        with tempfile.NamedTemporaryFile("w", suffix=".comp", delete=False) as handle:
            handle.write(body)
            temp_path = handle.name
        try:
            result = subprocess.run(
                ["glslangValidator", "--target-env", "vulkan1.1", "-S", "comp", temp_path],
                capture_output=True,
                text=True,
                timeout=900,
            )
        except FileNotFoundError:
            check(False, "glslangValidator not installed; shader validity cannot be proven")
            return
        except subprocess.TimeoutExpired:
            # A timeout is itself a known failure mode here: a past change made
            # this compiler take over ten minutes and several gigabytes on this
            # exact shader family. Treat slowness as a defect, not an accident.
            check(False, f"glslangValidator timed out on {path.name} (compile pathology)")
            continue
        finally:
            Path(temp_path).unlink(missing_ok=True)
        check(
            result.returncode == 0,
            f"glslangValidator rejected {path.name}:\n{result.stdout}\n{result.stderr}",
        )


# =============================================================================
# SECTION 5B - Every shipping shader must be checked against Apple's Metal path.
# =============================================================================
# Godot runs through Metal on this machine. The Vulkan compiler above proves
# only the source-to-SPIR-V step. This second gate performs the complete static
# chain available outside the game:
#
#   GLSL -> SPIR-V -> Metal Shading Language -> Apple Metal object
#
# Missing tools produce named SKIP rows instead of a plausible green claim.
# Even without Apple's compiler, a successful SPIR-V-to-MSL translation remains
# useful evidence and is reported separately from the skipped final compile.
METAL_BACKEND_RESULTS: list[tuple[str, str, str]] = []


def _shader_body_for_standalone_compiler(path: Path) -> str:
    """Return shader text without Godot's non-GLSL stage marker.

    path identifies one shipping compute shader. The returned text is passed to
    ordinary command-line compilers, which understand ``#version`` but not
    Godot's leading ``#[compute]`` marker. Keeping this normalization in one
    helper guarantees the Vulkan and Metal chains inspect identical source.
    """

    lines = path.read_text(
        encoding="utf-8", errors="replace"
    ).splitlines(keepends=True)
    if lines and lines[0].startswith("#["):
        return "".join(lines[1:])
    return "".join(lines)


def _record_metal_backend_result(
    shader_name: str,
    status: str,
    detail: str,
) -> None:
    """Record one reader-facing Metal result without changing pass/fail state.

    shader_name identifies the file being checked. status is exactly PASS,
    FAIL, plus SKIP. detail explains the completed stage and any missing tool.
    Failures also flow through ``check`` at their source so the process exits
    nonzero; this ledger exists to keep skipped work visible in normal output.
    """

    METAL_BACKEND_RESULTS.append((shader_name, status, detail))


def _metal_compiler_command() -> tuple[list[str] | None, str]:
    """Return the Apple Metal command prefix plus a precise missing-tool reason.

    ``xcrun`` selects the macOS SDK's compiler. Command Line Tools installations
    commonly provide xcrun and an SDK without the ``metal`` executable, so both
    layers must be checked. The empty detail means the compiler is available.
    """

    xcrun_path = shutil.which("xcrun")
    if xcrun_path is None:
        return None, "missing tool: xcrun"
    try:
        lookup = subprocess.run(
            [xcrun_path, "-sdk", "macosx", "--find", "metal"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return None, "tool lookup timed out: xcrun -sdk macosx --find metal"
    if lookup.returncode != 0:
        diagnostic = (lookup.stderr or lookup.stdout).strip()
        return (
            None,
            "missing tool: xcrun -sdk macosx metal"
            + (f" ({diagnostic})" if diagnostic else ""),
        )
    return [xcrun_path, "-sdk", "macosx", "metal"], ""


def section_shaders_compile_metal() -> None:
    """Transpile and compile every shipping shader for the Metal backend.

    Each source first becomes SPIR-V through glslangValidator, then Metal
    Shading Language through spirv-cross. When Apple's compiler exists, the MSL
    becomes an AIR object through ``metal -c``. A missing tool records one SKIP
    per shader; a present tool that rejects input records FAIL and fails the
    contract. Temporary compiler products are isolated and deleted on return.
    """

    glslang_path = shutil.which("glslangValidator")
    spirv_cross_path = shutil.which("spirv-cross")
    metal_command, metal_missing_reason = _metal_compiler_command()

    for path in SHIPPING_SHADERS:
        if not check(path.exists(), f"shipping shader missing: {path.name}"):
            _record_metal_backend_result(
                path.name, "FAIL", "shipping shader is absent"
            )
            continue

        # A missing prerequisite means this backend was not checked. Name the
        # exact missing executable instead of silently treating absence as pass.
        missing_tools: list[str] = []
        if glslang_path is None:
            missing_tools.append("glslangValidator")
        if spirv_cross_path is None:
            missing_tools.append("spirv-cross")
        if missing_tools:
            _record_metal_backend_result(
                path.name,
                "SKIP",
                "missing tool: " + ", ".join(missing_tools),
            )
            continue

        with tempfile.TemporaryDirectory(
            prefix=f"fl4431-metal-{path.stem}-"
        ) as temp_name:
            temp_root = Path(temp_name)
            glsl_path = temp_root / f"{path.stem}.comp"
            spirv_path = temp_root / f"{path.stem}.spv"
            metal_path = temp_root / f"{path.stem}.metal"
            air_path = temp_root / f"{path.stem}.air"
            glsl_path.write_text(
                _shader_body_for_standalone_compiler(path),
                encoding="utf-8",
            )

            try:
                spirv_result = subprocess.run(
                    [
                        glslang_path,
                        "-V",
                        "--target-env",
                        "vulkan1.1",
                        "-S",
                        "comp",
                        "-o",
                        str(spirv_path),
                        str(glsl_path),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=900,
                )
            except subprocess.TimeoutExpired:
                check(
                    False,
                    f"Metal gate SPIR-V compile timed out on {path.name}",
                )
                _record_metal_backend_result(
                    path.name, "FAIL", "SPIR-V compile timed out"
                )
                continue
            if not check(
                spirv_result.returncode == 0,
                "Metal gate could not produce SPIR-V for "
                f"{path.name}:\n{spirv_result.stdout}\n{spirv_result.stderr}",
            ):
                _record_metal_backend_result(
                    path.name, "FAIL", "GLSL-to-SPIR-V compilation failed"
                )
                continue

            try:
                msl_result = subprocess.run(
                    [
                        spirv_cross_path,
                        str(spirv_path),
                        "--msl",
                        "--output",
                        str(metal_path),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=900,
                )
            except subprocess.TimeoutExpired:
                check(
                    False,
                    f"spirv-cross MSL translation timed out on {path.name}",
                )
                _record_metal_backend_result(
                    path.name, "FAIL", "SPIR-V-to-MSL translation timed out"
                )
                continue
            if not check(
                msl_result.returncode == 0 and metal_path.exists(),
                "spirv-cross rejected "
                f"{path.name}:\n{msl_result.stdout}\n{msl_result.stderr}",
            ):
                _record_metal_backend_result(
                    path.name, "FAIL", "SPIR-V-to-MSL translation failed"
                )
                continue

            if metal_command is None:
                _record_metal_backend_result(
                    path.name,
                    "SKIP",
                    "MSL transpile passed; Metal compile skipped: "
                    + metal_missing_reason,
                )
                continue

            try:
                metal_result = subprocess.run(
                    metal_command
                    + ["-c", str(metal_path), "-o", str(air_path)],
                    capture_output=True,
                    text=True,
                    timeout=900,
                )
            except subprocess.TimeoutExpired:
                check(
                    False,
                    f"Apple Metal compiler timed out on {path.name}",
                )
                _record_metal_backend_result(
                    path.name, "FAIL", "Apple Metal compilation timed out"
                )
                continue
            if check(
                metal_result.returncode == 0 and air_path.exists(),
                "Apple Metal compiler rejected "
                f"{path.name}:\n{metal_result.stdout}\n{metal_result.stderr}",
            ):
                _record_metal_backend_result(
                    path.name,
                    "PASS",
                    "GLSL-to-SPIR-V, MSL transpile, and Metal compile passed",
                )
            else:
                _record_metal_backend_result(
                    path.name, "FAIL", "Apple Metal compilation failed"
                )


def _print_metal_backend_results() -> None:
    """Print every per-shader Metal verdict so skipped work stays visible."""

    for shader_name, status, detail in METAL_BACKEND_RESULTS:
        print(f"FL-4431 Metal backend {status}: {shader_name}: {detail}")


# =============================================================================
# SECTION 6 - The host must supply every input the VSB shader declares.
# =============================================================================
# The Visible Surface Builder occupies twenty numbered slots (64 through 83):
# one output at 64 plus nineteen inputs at 65 through 83. Those numbers are a
# contract between the shader and the ordinary GDScript code that sets it up. If
# the host forgets one slot, or
# fills a different number, the graphics driver does not necessarily complain --
# the shader simply reads whatever memory happens to be bound there, and the
# image degrades in ways that are extremely hard to trace back to the cause.
#
# This section reads the slot numbers straight out of the shader (which is the
# authority and is frozen for this change) and requires the host file to mention
# every one of them. It is a coarse check on purpose: proving the buffers hold
# CORRECT data needs a run, which the standing moratorium forbids. Proving the
# host at least addresses every declared slot is possible from source alone, and
# catches the most common and most confusing wiring mistake.
VSB_EXPECTED_SLOTS = tuple(range(64, 84))


def section_vsb_binding_coverage() -> None:
    if not check(VSB_PATH.exists(), "visible_surface_builder.glsl missing"):
        return
    vsb = strip_comments(VSB_PATH.read_text(encoding="utf-8", errors="replace"))
    host = strip_gd_comments(
        ACE_PATH.read_text(encoding="utf-8", errors="replace")
    )
    declared = sorted({int(n) for n in re.findall(r"binding\s*=\s*(\d+)", vsb)})
    declared_in_range = tuple(n for n in declared if 64 <= n <= 83)
    check(
        declared_in_range == VSB_EXPECTED_SLOTS,
        f"VSB shader slot set drifted: expected {VSB_EXPECTED_SLOTS}, found {declared_in_range}",
    )
    # The two-level height hierarchy is allowed to reject work, never to invent
    # a hit. b83 owns a range over eight b82 tiles, b82 owns the maximum and
    # minimum over eight authored cells, and every surviving interval must
    # still reach b73's exact triangle owner. These checks compare all three
    # levels instead of merely proving that the new binding number appears.
    check(
        re.search(
            r"const\s+int\s+HEIGHTFIELD_COARSE_TILE_CELLS\s*=\s*8\s*;",
            vsb,
        )
        is not None,
        "VSB coarse terrain ABI must keep eight cells per tile",
    )
    check(
        re.search(
            r"binding\s*=\s*82\)\s*uniform\s+sampler2D\s+"
            r"heightfield_coarse_bounds_tex",
            vsb,
        )
        is not None,
        "VSB b82 must remain the sampled coarse height-bounds texture",
    )
    check(
        re.search(
            r"const\s+int\s+HEIGHTFIELD_SUPER_TILE_TILES\s*=\s*8\s*;",
            vsb,
        )
        is not None,
        "VSB super terrain ABI must keep eight b82 tiles per b83 texel",
    )
    check(
        re.search(
            r"binding\s*=\s*83\)\s*uniform\s+sampler2D\s+"
            r"heightfield_super_bounds_tex",
            vsb,
        )
        is not None,
        "VSB b83 must remain the sampled super height-bounds texture",
    )
    # The selected acceleration program is shipping code, not a measurement
    # specialization. Reintroducing a compile-time fork revives a second owner.
    check(
        all(
            token not in vsb
            for token in (
                "VSB_HEIGHT_CORNER_CACHE_ENABLED",
                "VSB_B82_MIN_SKIP_ENABLED",
                "VSB_DYNAMIC_LEAF_AABB_ENABLED",
            )
        ),
        "VSB measurement specializations must stay physically absent",
    )
    check(
        all(
            token not in vsb
            for token in (
                "VSB_STATIC_TLAS_ORDERED_ENABLED",
                "scene_query_static_tlas_unordered",
                "static_tlas_child_entry",
                "static_query_depth_limit",
            )
        )
        and all(
            token not in host
            for token in (
                "_fl4431_perf_enable_static_ordered",
                "FL4431_PERF_ENABLE_STATIC_ORDERED",
                '"enable_static_ordered"',
                "static_ordered_specialization",
            )
        ),
        "measured-regressive VSB ordered-static path must be physically absent",
    )
    check(
        all(
            token not in vsb
            for token in (
                "VSB_B83_MIN_SKIP_ENABLED",
                "super_proven_below",
            )
        )
        and all(
            token not in host
            for token in (
                "_fl4431_perf_enable_b83_min",
                "FL4431_PERF_ENABLE_B83_MIN",
                '"enable_b83_min"',
                "b83_min_specialization",
            )
        ),
        "measured-regressive VSB b83-minimum path must be physically absent",
    )
    # Dynamic TLAS leaf words identify movers but do not encode each leaf box.
    # The optimized leaf owner must therefore reject against the retained
    # mover's containing AABB before opening its face range. This catches the
    # real regression shape where every ray touching the parent union executes
    # thousands of unrelated airplane triangles.
    dynamic_leaf = extract_glsl_function(vsb, "scene_query_dynamic_leaf")
    check(
        dynamic_leaf is not None
        and "instance.aabb_min.xyz - vec3(leaf_epsilon)" in dynamic_leaf
        and "instance.aabb_max.xyz + vec3(leaf_epsilon)" in dynamic_leaf
        and dynamic_leaf.find("ray_box_interval(") >= 0
        and dynamic_leaf.find("for (uint face_index") >= 0
        and dynamic_leaf.find("ray_box_interval(")
        < dynamic_leaf.find("for (uint face_index"),
        "dynamic mover AABB rejection must precede every b81 face-range read",
    )
    fine_tile = extract_glsl_function(vsb, "scene_query_heightfield_tile")
    check(
        fine_tile is not None
        and "scene_query_heightfield_cell(" in fine_tile,
        "every candidate coarse tile must retain the exact fine-cell intersection owner",
    )
    height_cache = extract_glsl_function(
        vsb, "heightfield_cell_corner_heights"
    )
    check(
        height_cache is not None
        and "if (!cache_valid)" in height_cache
        and "next_heights.x = cached_heights.y;" in height_cache
        and "next_heights.z = cached_heights.w;" in height_cache
        and "next_heights.y = cached_heights.x;" in height_cache
        and "next_heights.w = cached_heights.z;" in height_cache
        and "next_heights.x = cached_heights.z;" in height_cache
        and "next_heights.y = cached_heights.w;" in height_cache
        and "next_heights.z = cached_heights.x;" in height_cache
        and "next_heights.w = cached_heights.y;" in height_cache
        and "next_heights.x = cached_heights.w;" in height_cache
        and "next_heights.z = cached_heights.y;" in height_cache
        and "next_heights.y = cached_heights.z;" in height_cache
        and "next_heights.w = cached_heights.x;" in height_cache,
        "adjacent fine cells must reuse the exact shared b73 edge bits",
    )
    check(
        fine_tile is not None
        and "bool height_cache_valid = false;" in fine_tile
        and "cached_cell," in fine_tile
        and "cached_heights," in fine_tile
        and "height_cache_valid," in fine_tile,
        "each b82 tile must own one bounded corner cache passed to the exact cell owner",
    )
    coarse_range = extract_glsl_function(
        vsb, "scene_query_heightfield_coarse_range"
    )
    check(
        coarse_range is not None
        and "heightfield_coarse_bounds_tex" in coarse_range
        and ").rg" in coarse_range
        and "tile_min > tile_max" in coarse_range
        and "min(enter_y, exit_y) > tile_max + epsilon" in coarse_range
        and "fine_depth_epsilon = max(epsilon, 1.0e-5)" in coarse_range
        and "tile_enter - fine_depth_epsilon" in coarse_range
        and "max(lower_guard_enter_y, exit_y) < tile_min - epsilon"
        in coarse_range
        and "VSB_B82_MIN_SKIP_ENABLED" not in coarse_range
        and "if (!tile_proven_above && !tile_proven_below)" in coarse_range
        and "scene_query_heightfield_tile(" in coarse_range,
        "b82 disjoint-range proofs must conservatively descend to exact b73 cells",
    )
    check(
        coarse_range is not None
        and "float(next_boundary_cell_x) * cell_size" in coarse_range
        and "float(next_boundary_cell_z) * cell_size" in coarse_range,
        "b83-to-b82 handoffs must use direct authored-grid endpoints",
    )
    heightfield_query = extract_glsl_function(vsb, "scene_query_heightfield")
    check(
        heightfield_query is not None
        and "heightfield_super_bounds_tex" in heightfield_query
        and ").rg" in heightfield_query
        and "super_min > super_max" in heightfield_query
        and "min(enter_y, exit_y) > super_max + epsilon" in heightfield_query
        and re.search(
            r"if\s*\(\s*!super_proven_above"
            r"(?:\s*&&\s*!super_proven_below)?\s*\)",
            heightfield_query,
        )
        is not None
        and "scene_query_heightfield_coarse_range(" in heightfield_query,
        "b83 upper-range proofs must conservatively descend to exact b82 tiles",
    )
    check(
        heightfield_query is not None
        and "float(next_boundary_cell_x) * cell_size" in heightfield_query
        and "float(next_boundary_cell_z) * cell_size" in heightfield_query,
        "top-level b83 DDA must use the same direct authored-grid endpoints as b82/b73",
    )
    # Byte 88 is retained as an ABI witness fixed to the shipping b83 hierarchy.
    check(
        heightfield_query is not None
        and "!= floatBitsToUint(1.0)" in heightfield_query
        and "b83_hierarchy_enabled" not in heightfield_query,
        "VSB must require the shipping b83 identity without a second arm",
    )
    check(
        heightfield_query is not None
        and heightfield_query.count("scene_query_heightfield_coarse_range(") == 1,
        "b83 must descend through one selected b82 range owner",
    )

    # A ray can visit many static and dynamic acceleration nodes, and every
    # node performs the same three slab-axis calculations. The reciprocal of a
    # ray direction belongs to the ray's coordinate-space setup, not to each
    # node: repeating the division in ray_box_interval multiplied that fixed
    # work by the number of visited nodes on Metal. Pin both materialization
    # sites because the world ray and each transformed instance-local ray have
    # different directions. Pin the consumer too, so retaining an unused field
    # cannot make this performance contract pass on a technicality.
    scene_ray = re.search(
        r"struct\s+SceneRay\s*\{(?P<body>.*?)\};",
        vsb,
        re.DOTALL,
    )
    world_ray_builder = extract_glsl_function(vsb, "build_subcell_ray")
    local_ray_builder = extract_glsl_function(vsb, "instance_local_ray")
    slab_query = extract_glsl_function(vsb, "ray_box_interval")
    check(
        scene_ray is not None
        and "vec3 inverse_direction;" in scene_ray.group("body"),
        "SceneRay must retain one reciprocal direction for repeated slab tests",
    )
    check(
        world_ray_builder is not None
        and "ray.inverse_direction = vec3(1.0) / ray.direction;" in world_ray_builder,
        "world-ray setup must materialize its reciprocal exactly once",
    )
    check(
        local_ray_builder is not None
        and (
            "local_ray.inverse_direction = vec3(1.0) / "
            "local_ray.direction;"
        )
        in local_ray_builder,
        "instance-local setup must materialize its reciprocal exactly once",
    )
    check(
        slab_query is not None
        and "ray.inverse_direction[axis]" in slab_query
        and "1.0 / direction_component" not in slab_query,
        "every slab node must reuse the ray reciprocal instead of dividing per node",
    )

    # The host does not write raw slot numbers; it uses named constants declared
    # in VisibleSurfaceBuilder.gd. Those constants are therefore the real bridge
    # between the shader's numbering and the setup code, so they are what must
    # be checked. Searching the host for a bare "64" would pass on any unrelated
    # appearance of that number -- a byte size, an array length -- and prove
    # nothing at all.
    if not check(BUILDER_PATH.exists(), "VisibleSurfaceBuilder.gd missing"):
        return
    builder = strip_gd_comments(BUILDER_PATH.read_text(encoding="utf-8", errors="replace"))
    check(
        re.search(
            r"const\s+PUSH_B83_HIERARCHY_ENABLED_OFFSET\s*:?=\s*88\b",
            builder,
        )
        is not None
        and (
            "bytes.encode_float("
            "PUSH_B83_HIERARCHY_ENABLED_OFFSET, b83_hierarchy_enabled)"
        )
        in builder,
        "the host serializer must publish the exact b83 flag at push byte 88",
    )
    check(
        (
            "b83_hierarchy_enabled != 0.0 "
            "and b83_hierarchy_enabled != 1.0"
        )
        in builder,
        "the host serializer must reject every non-binary b83 flag",
    )
    # Collect "const BINDING_SOMETHING := 71" declarations as name -> number.
    constants = {
        name: int(value)
        for name, value in re.findall(r"const\s+(BINDING_\w+)\s*:?=\s*(\d+)", builder)
    }
    declared_values = tuple(sorted(constants.values()))
    check(
        declared_values == VSB_EXPECTED_SLOTS,
        "VisibleSurfaceBuilder binding constants must cover exactly slots 64-83, "
        f"found {declared_values}",
    )
    # Every slot the shader declares must have exactly one named constant, so a
    # slot cannot be silently addressed by two different names.
    for slot in declared_in_range:
        owners = [name for name, value in constants.items() if value == slot]
        check(
            len(owners) == 1,
            f"VSB slot {slot} must have exactly one binding constant, found {owners}",
        )

    # Finally, the host must actually use those constants to build its set. It
    # binds the output explicitly and sweeps the inputs as a contiguous range,
    # so both the first and last input constants must appear by name.
    host = strip_gd_comments(ACE_PATH.read_text(encoding="utf-8", errors="replace"))
    # The ordinary process fixes b83 on and exposes no runtime measurement keys.
    check(
        "FL4431_PERF_" not in host
        and "0.0 if _fl4431_perf_skip_b83 else 1.0" not in host
        and re.search(r"\n\s*1\.0,\n\s*0\.000001,", host) is not None,
        "host must fix b83 on and expose no VSB ablation owner",
    )
    pipeline_builder = extract_gd_function(host, "_ensure_vsb_pipeline")
    check(
        pipeline_builder is not None
        and "RDPipelineSpecializationConstant" not in pipeline_builder
        and '"res://render/backends/ortho/visibility/visible_surface_builder.glsl"' in pipeline_builder,
        "VSB pipeline creation must expose no measurement specialization",
    )
    # The b82 producer and uploader form one cross-language record contract.
    # Merely checking the shader's `.rg` would miss an RF host image whose
    # absent G channel reads as a plausible zero on the GPU.
    scene_db = strip_gd_comments(
        SCENE_DB_PATH.read_text(encoding="utf-8", errors="replace")
    )
    check(
        '"coarse_height_bounds_image"' in scene_db
        and "Image.FORMAT_RGF" in scene_db
        and "Color(tile_max, tile_min, 0.0, 1.0)" in scene_db
        and '"coarse_max_height_image"' not in scene_db,
        "SceneGeometryDB must publish exactly one R=max G=min b82 image owner",
    )
    check(
        '"coarse_height_bounds_image"' in host
        and "coarse_image.get_format() != Image.FORMAT_RGF" in host
        and "RenderingDevice.DATA_FORMAT_R32G32_SFLOAT" in host
        and "BINDING_HEIGHTFIELD_COARSE_BOUNDS_TEXTURE" in host
        and "BINDING_HEIGHTFIELD_COARSE_MAX_TEXTURE" not in host,
        "ACE must upload retained RGF bounds as RG32F at the renamed b82 slot",
    )
    check(
        '"super_height_bounds_image"' in scene_db
        and "func _build_super_height_bounds_image(" in scene_db
        and "Image.FORMAT_RGF" in scene_db
        and "super_min = minf(super_min, tile_min)" in scene_db
        and "Color(super_max, super_min, 0.0, 1.0)" in scene_db
        and '"super_max_height_image"' not in scene_db,
        "SceneGeometryDB must publish exactly one R=max G=min b83 image owner",
    )
    check(
        '"super_height_bounds_image"' in host
        and "super_image.get_format() != Image.FORMAT_RGF" in host
        and "BINDING_HEIGHTFIELD_SUPER_BOUNDS_TEXTURE" in host
        and "BINDING_HEIGHTFIELD_SUPER_MAX_TEXTURE" not in host,
        "ACE must upload retained RGF bounds as RG32F at the renamed b83 slot",
    )
    for required in (
        "BINDING_VISIBLE_SURFACE_PRODUCTS",
        "BINDING_SCENE_TLAS_NODES",
        "BINDING_HEIGHTFIELD_SUPER_BOUNDS_TEXTURE",
    ):
        check(
            required in host,
            f"host never references {required}; the FL-4431 VSB uniform set would be missing inputs",
        )

    # Naming the endpoints is not enough. The inputs are supplied by a loop over
    # the whole range, so the loop must visit EVERY slot and skip none. Mutation
    # testing showed that adding a skip for one middle slot -- say b75 -- left
    # this section reporting no failures while the shader was handed an
    # incomplete set, which fails the same way as the binding defects above.
    #
    # The loop is allowed exactly one early exit: a `return` that abandons the
    # whole set when a buffer is missing. That is fail-closed and correct. What
    # must NOT exist is a `continue`, which would silently omit one slot and
    # build a set that looks complete.
    builder_fn = re.search(
        r"func _build_vsb_uniform_set\b.*?(?=\nfunc\s)", host, re.DOTALL
    )
    if check(builder_fn is not None, "_build_vsb_uniform_set not found in host"):
        body = builder_fn.group(0)
        check(
            re.search(
                r"for\s+\w+\s+in\s+range\(\s*VisibleSurfaceBuilderScript\.BINDING_SCENE_TLAS_NODES\s*,"
                r"\s*VisibleSurfaceBuilderScript\.BINDING_HEIGHTFIELD_SUPER_BOUNDS_TEXTURE\s*\+\s*1",
                body,
            )
            is not None,
            "the VSB uniform set must be built by a loop spanning the full "
            "BINDING_SCENE_TLAS_NODES..BINDING_HEIGHTFIELD_SUPER_BOUNDS_TEXTURE range",
        )
        check(
            "continue" not in body,
            "_build_vsb_uniform_set must not skip any slot with `continue`; "
            "a skipped slot yields an incomplete uniform set",
        )
        # Constructing a uniform object is not the same as supplying it. The
        # driver sees only members appended to the array passed to the cache.
        # Require one append of the per-slot object, after its binding number is
        # assigned. This catches the real dropped-append defect while allowing
        # the loop's resource-type branches to be reorganized freely.
        binding_assignment = body.find("uniform.binding = binding")
        input_append = body.find("uniforms.append(uniform)")
        check(
            body.count("uniforms.append(uniform)") == 1
            and binding_assignment >= 0
            and input_append > binding_assignment,
            "_build_vsb_uniform_set must append each configured input uniform "
            "exactly once after assigning its binding",
        )


# =============================================================================
# SECTION 6B - The Metal-safe cardinality ABI must remain byte-exact.
# =============================================================================
# Godot's Apple backend rejected SPIRV-Cross output when this shader asked for a
# runtime storage array's length. Binding 78 now publishes every needed element
# count as four std140 uvec4 rows. This section checks both sides of that repair:
# the shader must consume the exact four-row table without any `.length()` call,
# and the GDScript serializer must allocate and populate the matching 64 bytes.
#
# Binding 64 also deliberately lacks GLSL's `writeonly` qualifier. Keeping that
# access spelling pinned prevents a later cleanup from reviving the same Metal
# buffer-size-constant translation path.


def section_product_perf_instrumentation() -> None:
    """Pin the selected Product program after deleting perf instrumentation."""

    product = strip_comments(
        _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8", errors="replace"))
    )
    host = strip_gd_comments(
        ACE_PATH.read_text(encoding="utf-8", errors="replace")
    )
    product_main = extract_glsl_function(product, "main")
    check(
        product_main is not None
        and product_main.count("vsb_product_select_actor_paint_quad(") == 1
        and product_main.count("compile_probe_subcell_payload(") == 1
        and product_main.count("vsb_product_finalize_record(") == 2
        and "if (decode == VSB_PRODUCT_DECODE_ABSENT)" in product_main
        and "PRODUCT_DECODE_FIRST" not in product,
        "Product must keep decode-first refusal and the complete actor/compiler/finalizer path",
    )
    full_phase_calls = (
        "q3_probe_payload_apply_foliage(",
        "q3_probe_payload_apply_material(",
        "q3_probe_payload_apply_water(",
        "q3_probe_payload_apply_shadow_and_lit(",
        "q3_probe_payload_apply_debug(",
        "vsb_product_consider_leaf_record(",
    )
    check(
        all(call in product for call in full_phase_calls)
        and "PRODUCT_SKIP_" not in product
        and "PRODUCT_SPINE_STAGE" not in product,
        "Product must keep every selected phase with no skip and no spine checkpoint",
    )
    check(
        all(
            token not in product
            for token in (
                "PRODUCT_LEAF_LIVE_MASK_ENABLED",
                "PRODUCT_DYNAMIC_SHADOW_BOUND_ENABLED",
                "PRODUCT_WAKE_LANE_BOUND_ENABLED",
            )
        ),
        "measured-regressive Product live-mask, shadow-bound, and wake-bound "
        "specializations must stay physically absent",
    )
    check(
        product.count(
            "finalized_probe_subcell_products.products[final_index] =\n"
            "\t\t\tstaged_final_records[slot];"
        ) == 1,
        "Product must retain exactly one final b55 store",
    )
    initializer = extract_gd_function(host, "_initialize_compute")
    check(
        initializer is not None
        and "product_b62_diagnostics_specialization.constant_id = 17" in initializer
        and "product_specializations.append(product_b62_diagnostics_specialization)"
        in initializer
        and "PRODUCT_B62_DIAGNOSTICS_ENABLED" in product,
        "b62 must remain an explicit evidence specialization",
    )


def section_falling_leaf_compaction() -> None:
    """Prove the retired tree/falling-leaf runtime owner stays absent."""
    product = _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8", errors="replace"))
    host = ACE_PATH.read_text(encoding="utf-8", errors="replace")
    scene_db = SCENE_DB_PATH.read_text(encoding="utf-8", errors="replace")
    retired_tokens = (
        "LeafParticleBank",
        "leaf_particle_bank",
        "vsb_emit_retained_leaf_bank",
        "vsb_retained_enumerate_canopy_face",
        "STATIC_WORLD_TREE_LEAF_MAT",
        "STATIC_WORLD_TREE_BARK_MAT",
        "canopy_identity_bytes",
    )
    combined = "\n".join((product, host, scene_db))
    check(
        not any(token in combined for token in retired_tokens),
        "retired tree/falling-leaf runtime owner was resurrected",
    )
    check(
        "layout(set = 0, binding = 59" not in product
        and "_probe_u_storage(59" not in host,
        "retired falling-leaf binding 59 was resurrected",
    )


def section_foliage_downwash_invariant() -> None:
    """Pin one downwash lookup and one quantized time per foliage anchor.

    Slot admission remains blade-specific. Once at least one blade is admitted,
    the shared world anchor owns one immutable wind fact that every admitted
    slot consumes without another local-light scan.
    """

    product = _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8", errors="replace"))
    violations = foliage_downwash_invariant_contract_violations(product)
    check(
        not violations,
        "foliage downwash invariant contract failed: "
        + "; ".join(violations),
    )


def section_dynamic_shadow_active_count() -> None:
    """Pin b81's exact kind-1 count from retained rows through Product.

    A zero count must stop shadow selection before binding 74 is touched.
    Positive counts may stop only after that many kind-1 rows were inspected;
    sparse non-shadow rows do not consume the count, and airplane selection
    still traverses the complete mover range.
    """

    product = _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8", errors="replace"))
    host = ACE_PATH.read_text(encoding="utf-8", errors="replace")
    scene_db = SCENE_DB_PATH.read_text(encoding="utf-8", errors="replace")
    violations = dynamic_shadow_active_count_contract_violations(
        product, host, scene_db
    )
    check(
        not violations,
        "b81 dynamic-shadow active-count contract failed: "
        + "; ".join(violations),
    )


def section_wake_topology_materialization() -> None:
    """Pin host-owned wake topology and Product's direct validated consumer.

    The host already owns each fixed lane's insertion order, so it publishes
    predecessor/latest metadata while Product validates and consumes those
    facts without rebuilding a private 32-by-32 relationship table.
    """

    product = _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8", errors="replace"))
    host = ACE_PATH.read_text(encoding="utf-8", errors="replace")
    violations = wake_topology_contract_errors(product, host)
    check(
        not violations,
        "wake topology single-materialization contract failed: "
        + "; ".join(violations),
    )


def section_wake_host_behavior() -> None:
    """Execute the real GDScript ring owner without launching a game frame.

    Text checks prove source shape. This engine test proves the actual Packed
    Float32Array and Dictionary mutations for wrap, equal time, overflow,
    stale/NaN refusal, full capacity, and mounted-player endpoint isolation.
    """

    if not check(
        WAKE_HOST_BEHAVIOR_PATH.exists(),
        "wake host behavior test script is missing",
    ):
        return
    godot = shutil.which("godot")
    if not check(godot is not None, "godot is required for wake host behavior"):
        return
    with tempfile.NamedTemporaryFile(
        prefix="fl4216-wake-host-", suffix=".log", delete=False
    ) as log_handle:
        log_path = Path(log_handle.name)
    try:
        result = subprocess.run(
            [
                godot,
                "--headless",
                "--log-file",
                str(log_path),
                "--path",
                str(REPO / "godot_project"),
                "--script",
                "res://scripts/tests/test_fl4216_wake_host_topology.gd",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        check(False, "wake host behavior test timed out")
        return
    finally:
        log_path.unlink(missing_ok=True)
    combined = result.stdout + result.stderr
    check(
        result.returncode == 0
        and "FL4216_WAKE_HOST_TOPOLOGY PASS cases=5" in combined
        and "SCRIPT ERROR" not in combined,
        "wake host behavior test failed:\n" + combined,
    )


def section_abab_adjudicator() -> None:
    """Prove the retired ABAB and runtime-ablation owner is physically gone."""

    host_raw = ACE_PATH.read_text(encoding="utf-8", errors="replace")
    product_raw = _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8", errors="replace"))
    vsb_raw = VSB_PATH.read_text(encoding="utf-8", errors="replace")
    check(
        abab_perf_ablation_deleted_contract(host_raw, product_raw, vsb_raw),
        "the focused physical-deletion contract must reject every retired "
        "ABAB, runtime-ablation, and Product-spine owner",
    )
    host = strip_gd_comments(host_raw)
    product = strip_comments(product_raw)
    vsb = strip_comments(vsb_raw)
    check(
        "product_b62_diagnostics_specialization.constant_id = 17" in host
        and "rd.compute_list_bind_compute_pipeline(product_compute_list, vsb_dispatch_pipeline)"
        in host
        and "_dispatch_cell_owner_product(" in host
        and (
            "rd.compute_list_bind_compute_pipeline("
            "\n\t\t\tcompute_list,"
            "\n\t\t\tcompositor_pipeline_for_frame"
            "\n\t\t)"
        )
        in host,
        "host must keep b62 evidence while always dispatching VSB, Product, and paint",
    )
    check(
        "if (decode == VSB_PRODUCT_DECODE_ABSENT)" in product
        and "vsb_product_select_actor_paint_quad(" in product
        and "compile_probe_subcell_payload(" in product
        and "vsb_product_finalize_record(" in product
        and product.count(
            "finalized_probe_subcell_products.products[final_index] =\n"
            "\t\t\tstaged_final_records[slot];"
        ) == 1,
        "Product must keep decode-first, full compilation/finalization, and one store",
    )
    check(
        "if (!cache_valid)" in vsb
        and "max(lower_guard_enter_y, exit_y) < tile_min - epsilon" in vsb
        and "instance.aabb_min.xyz - vec3(leaf_epsilon)" in vsb
        and "!= floatBitsToUint(1.0)" in vsb,
        "VSB must keep the selected cache, b82 minimum, dynamic-leaf, and b83 arms",
    )


def section_metal_cardinality_abi() -> None:
    """Pin the sampler-free VSB array-count contract on both ABI owners."""

    vsb_source = VSB_PATH.read_text(encoding="utf-8", errors="replace")
    vsb = strip_comments(vsb_source)
    builder_source = BUILDER_PATH.read_text(encoding="utf-8", errors="replace")
    builder = strip_gd_comments(builder_source)
    host = strip_gd_comments(ACE_PATH.read_text(encoding="utf-8", errors="replace"))

    # No executable runtime-array length query may survive anywhere in VSB.
    # The host-owned b78 counts replace every former query, so even a query on
    # an input buffer would revive the backend machinery that broke b64.
    check(
        re.search(r"\.\s*length\s*\(", vsb) is None,
        "VSB must contain zero executable runtime-array .length() calls",
    )

    output_block = re.search(
        r"layout\s*\(\s*set\s*=\s*0\s*,\s*binding\s*=\s*64\s*,\s*std430\s*\)"
        r"\s*(?P<qualifiers>(?:(?:readonly|writeonly|restrict)\s+)*)"
        r"buffer\s+VisibleSurfaceProducts\s*\{(?P<body>.*?)\}"
        r"\s*visible_surface_products\s*;",
        vsb,
        re.DOTALL,
    )
    if check(output_block is not None, "VSB b64 VisibleSurfaceProducts block is missing"):
        check(
            "writeonly" not in output_block.group("qualifiers").split(),
            "VSB b64 must omit writeonly on the Metal-safe output declaration",
        )
        check(
            re.sub(r"\s+", "", output_block.group("body"))
            == "VisibleSurfaceProductrecords[];",
            "VSB b64 must remain one runtime array of 96-byte VisibleSurfaceProduct records",
        )

    descriptor_block = re.search(
        r"layout\s*\(\s*set\s*=\s*0\s*,\s*binding\s*=\s*78\s*,\s*std140\s*\)"
        r"\s*uniform\s+DynamicGeometryDescriptor\s*\{(?P<body>.*?)\}"
        r"\s*dynamic_geometry_descriptor\s*;",
        vsb,
        re.DOTALL,
    )
    expected_shader_fields = [
        ("uvec4", "counts_revision"),
        ("uvec4", "dynamic_binding_counts"),
        ("uvec4", "static_binding_counts_0"),
        ("uvec4", "static_binding_counts_1"),
    ]
    if check(
        descriptor_block is not None,
        "VSB b78 DynamicGeometryDescriptor std140 block is missing",
    ):
        actual_shader_fields = re.findall(
            r"\b(uvec4)\s+(\w+)\s*;",
            descriptor_block.group("body"),
        )
        check(
            actual_shader_fields == expected_shader_fields,
            "VSB b78 must contain exactly four ordered uvec4 rows: "
            f"expected {expected_shader_fields}, found {actual_shader_fields}",
        )

    size_match = re.search(
        r"const\s+DYNAMIC_GEOMETRY_DESCRIPTOR_BYTES\s*:?=\s*(\d+)",
        builder,
    )
    check(
        size_match is not None and int(size_match.group(1)) == 64,
        "VisibleSurfaceBuilder b78 allocation must be exactly 64 bytes",
    )

    # Each name below owns one uint lane in the four shader rows. Matching all
    # sixteen offsets proves the host's byte table uses the same field order as
    # std140 instead of merely allocating the same total size.
    expected_offsets = {
        "DYNAMIC_DESCRIPTOR_LEAF_COUNT_OFFSET": 0,
        "DYNAMIC_DESCRIPTOR_GLOBAL_REVISION_OFFSET": 4,
        "DYNAMIC_DESCRIPTOR_FACE_TOTAL_OFFSET": 8,
        "DYNAMIC_DESCRIPTOR_ENABLED_OFFSET": 12,
        "DYNAMIC_DESCRIPTOR_DYNAMIC_TLAS_ROW_COUNT_OFFSET": 16,
        "DYNAMIC_DESCRIPTOR_DYNAMIC_LEAF_ID_COUNT_OFFSET": 20,
        "DYNAMIC_DESCRIPTOR_DYNAMIC_INSTANCE_COUNT_OFFSET": 24,
        "DYNAMIC_DESCRIPTOR_DYNAMIC_FACE_ROW_COUNT_OFFSET": 28,
        "DYNAMIC_DESCRIPTOR_STATIC_TLAS_ROW_COUNT_OFFSET": 32,
        "DYNAMIC_DESCRIPTOR_STATIC_INSTANCE_ID_COUNT_OFFSET": 36,
        "DYNAMIC_DESCRIPTOR_STATIC_BLAS_ROW_COUNT_OFFSET": 40,
        "DYNAMIC_DESCRIPTOR_STATIC_PRIMITIVE_ID_COUNT_OFFSET": 44,
        "DYNAMIC_DESCRIPTOR_STATIC_FACE_ROW_COUNT_OFFSET": 48,
        "DYNAMIC_DESCRIPTOR_WATER_SPAN_WORD_COUNT_OFFSET": 52,
        "DYNAMIC_DESCRIPTOR_RESERVED_0_OFFSET": 56,
        "DYNAMIC_DESCRIPTOR_RESERVED_1_OFFSET": 60,
    }
    actual_offsets = {
        name: int(value)
        for name, value in re.findall(
            r"const\s+(DYNAMIC_DESCRIPTOR_\w+_OFFSET)\s*:?=\s*(\d+)",
            builder,
        )
    }
    check(
        actual_offsets == expected_offsets,
        "VisibleSurfaceBuilder b78 uint-lane offsets drifted: "
        f"expected {expected_offsets}, found {actual_offsets}",
    )

    serializer = extract_gd_function(builder, "build_dynamic_geometry_descriptor")
    if check(serializer is not None, "b78 serializer function is missing"):
        for offset_name in expected_offsets:
            check(
                len(
                    re.findall(
                        r"bytes\.encode_u32\(\s*" + re.escape(offset_name) + r"\b",
                        serializer,
                    )
                )
                == 1,
                f"b78 serializer must write {offset_name} exactly once",
            )
        check(
            "bytes.resize(DYNAMIC_GEOMETRY_DESCRIPTOR_BYTES)" in serializer,
            "b78 serializer must resize through the 64-byte ABI constant",
        )

    frame_builder = extract_gd_function(host, "_build_vsb_frame_receipt")
    if check(frame_builder is not None, "VSB frame receipt builder is missing"):
        frame_compact = compact_source(frame_builder)
        check(
            "VisibleSurfaceBuilderScript.build_dynamic_geometry_descriptor("
            "dynamic_leaf_count,dynamic_revision,dynamic_face_total,"
            "dynamic_enabled,source_bytes)" in frame_compact,
            "the frame owner must serialize b78 from the complete bound byte-bank family",
        )


# =============================================================================
# SECTION 7 - The GPU work must happen in one ordered, fenced sequence.
# =============================================================================
# Two pieces of GPU work run per view, and each one depends on the previous:
#
#   1. the Visible Surface Builder traces the scene and writes every b64 row
#      with what each subcell actually sees;
#   2. the Product stage turns each surface into a finished b55 record. Rejected
#      subcells receive Product's local failure record at the same sole store.
#
# Graphics hardware runs work in parallel and does NOT automatically wait for
# VSB to finish before starting Product. A "barrier" is the instruction that
# forces the wait. Miss one and Product reads half-written memory. The
# symptom is not a crash: it is an intermittently wrong or blank frame, often
# only on one machine, which is among the most expensive kinds of bug to chase.
#
# So the order is checked literally. The interesting property is not "a barrier
# exists somewhere" but "the barriers sit between the right pairs of stages",
# which means the SEQUENCE of operations is what has to be verified.

# The operations that matter, in the order the reviewed design requires. Each
# entry is (regular expression, short name used in failure messages).
DISPATCH_SEQUENCE = (
    (r"rd\.compute_list_begin\(\)", "open the shared compute list"),
    (r"rd\.compute_list_add_barrier\(product_compute_list\)", "barrier: host facts -> VSB"),
    (
        r"rd\.compute_list_bind_compute_pipeline\(\s*"
        r"product_compute_list\s*,\s*vsb_dispatch_pipeline\s*\)",
        "bind VSB pipeline",
    ),
    (r"rd\.compute_list_dispatch\(", "dispatch VSB"),
    (r"rd\.compute_list_add_barrier\(product_compute_list\)", "barrier: VSB -> Product"),
    (r"_dispatch_cell_owner_product\(", "dispatch Product onto the shared list"),
    (r"rd\.compute_list_add_barrier\(product_compute_list\)", "barrier: Product -> paint"),
    (r"rd\.compute_list_end\(\)", "close the shared compute list"),
)


def section_dispatch_ordering() -> None:
    host = strip_gd_comments(ACE_PATH.read_text(encoding="utf-8", errors="replace"))
    render_callback = extract_gd_function(host, "_render_callback")
    if not check(render_callback is not None, "_render_callback not found in host"):
        return
    allocation = extract_gd_function(host, "_ensure_vsb_view_buffers")
    recovery = extract_gd_function(host, "_upload_vsb_final_failure_prefill")
    callback_compact = compact_source(render_callback)
    b64_updates = list(
        re.finditer(r"rd\.buffer_update\(_vsb_product_rd,", callback_compact)
    )
    check(
        len(b64_updates) == 0
        and "rd.compute_list_bind_compute_pipeline(product_compute_list,vsb_dispatch_pipeline)"
        in callback_compact
        and "_dispatch_cell_owner_product(" in callback_compact
        and "_fl4431_perf_skip_vsb" not in callback_compact
        and "_fl4431_perf_skip_product" not in callback_compact,
        "ordinary frames must always dispatch VSB then Product, with no host "
        "b64 replacement upload and no retired skip owner",
    )
    check(
        re.search(r"rd\.buffer_update\(\s*_vsb_final_product_rd", render_callback)
        is None,
        "ordinary valid frames must not upload a full b55 failure bank",
    )
    check(
        allocation is not None
        and compact_source(allocation).count(
            "rd.storage_buffer_create(required_b64_bytes,b64_initial_bytes)"
        )
        == 1
        and compact_source(allocation).count(
            "rd.storage_buffer_create(required_final_bytes,final_initial_bytes)"
        )
        == 1,
        "b64 and b55 must retain their one-time allocation initializers",
    )
    check(
        recovery is not None
        and compact_source(recovery).count(
            "rd.buffer_update(_vsb_final_product_rd,0,"
            "failure_bytes.size(),failure_bytes)"
        )
        == 1,
        "the host must keep exactly one b55 rejection-upload helper",
    )
    check(
        callback_compact.count("_upload_vsb_final_failure_prefill(") == 2
        and (
            "ifnotbool(_vsb_view_receipt.get(\"valid\",false)):"
            in callback_compact
        )
        and (
            "_upload_vsb_final_failure_prefill(rejected_record_count)"
            in callback_compact
        )
        and (
            "ifprepared_product_dispatch.is_empty():"
            "_upload_vsb_final_failure_prefill("
            "int(vsb_publication[\"record_count\"]))"
            in callback_compact
        )
        and "_fl4431_perf_skip_product" not in callback_compact,
        "b55 host recovery must run only for rejected view publication, "
        "then rejected Product preparation",
    )

    # Walk forward through the file, requiring each operation to appear after
    # the previous one. Searching from the running cursor is what makes this an
    # ORDER check rather than a mere presence check.
    cursor = 0
    # Remember where each step was found, so later checks can reason about the
    # positions of THIS chain specifically. Searching the whole file for a name
    # like "compute_list_begin" would find an unrelated earlier list belonging
    # to a different pass and compare against the wrong thing entirely.
    positions: dict[str, int] = {}
    for pattern, label in DISPATCH_SEQUENCE:
        found = re.search(pattern, render_callback[cursor:])
        if not check(found is not None, f"VSB dispatch chain: missing or out of order -> {label}"):
            return
        # Record the first time each label is seen, measured from file start.
        positions.setdefault(label, cursor + found.start())
        cursor += found.end()

    # Exactly three barriers belong to this chain: one per stage boundary. A
    # fourth would be harmless but signals someone was unsure; a missing one is
    # a real defect, so the count is pinned.
    barriers = re.findall(
        r"rd\.compute_list_add_barrier\(product_compute_list\)", render_callback
    )
    check(
        len(barriers) == 3,
        f"the shared VSB/Product list must carry exactly three barriers, found {len(barriers)}",
    )

    # Presence and order are still insufficient when a call is textually nested
    # under `if false:`. Such a line can never execute, yet an ordinary regex
    # sees it. Count guard states for the three barriers and the Product call
    # inside the real callback. This deliberately proves literal reachability
    # only; runtime-dependent conditions require execution evidence.
    barrier_guard_states = gd_target_guard_states(
        render_callback,
        "rd.compute_list_add_barrier(product_compute_list)",
    )
    product_guard_states = gd_target_guard_states(
        render_callback,
        "_dispatch_cell_owner_product(",
    )
    check(
        barrier_guard_states == [False, False, False],
        "all three shared-list barriers must be executable, not nested under a "
        f"literal-false guard; states={barrier_guard_states}",
    )
    check(
        product_guard_states == [False],
        "the one Product dispatch in _render_callback must be executable, not "
        f"nested under a literal-false guard; states={product_guard_states}",
    )


# =============================================================================
# SECTION 8 - The host may not bind a slot the shader does not declare.
# =============================================================================
# Before a shader runs, the host hands it a "uniform set": a numbered list of
# buffers. The graphics API checks that list against what the shader actually
# declares. Hand it a slot the shader no longer has, and building the set FAILS
# -- which means the stage never runs, which (because this code correctly
# refuses to present stale bytes) means the screen goes blank.
#
# This is easy to create during a large deletion: someone removes a buffer from
# the shader, and the host code that supplies it survives because nothing links
# the two. It happened here under FL-4431. The shader used to declare binding 60
# for identity probe counters; the cut removed it; the host kept binding 60
# unconditionally, with a comment still asserting that the shader declares it.
#
# A source check catches this in a second. Discovering it at runtime costs a
# launch, a blank frame, and an investigation into the wrong subsystem.


def resolve_uniform_slots(host: str) -> dict[str, int]:
    """Map every host uniform variable name to the slot number it was given.

    The host creates uniforms two ways, and both must be understood:
      * through a helper, e.g.  var u_thing := _probe_u_storage(59, ...)
      * by hand, e.g.           u_thing.binding = 42
    Returns {"u_thing": 59, ...}.
    """
    slots: dict[str, int] = {}
    # Every declaration spelling must be understood, and missing ANY of them
    # produces confident nonsense. Two things vary independently here:
    #
    #   the declaration form -- GDScript allows an inferred declaration
    #     ("var u_thing := ...") and an explicitly typed one
    #     ("var u_thing: RDUniform = ..."), possibly with the arguments on the
    #     following line;
    #   the helper name -- the host has SEVERAL uniform-building helpers, not
    #     one. Storage buffers, uniform blocks and storage images each have
    #     their own (_probe_u_storage, _probe_u_ubo, _vsb_product_storage_image,
    #     and others may be added later).
    #
    # Both variations have already caused real damage. Matching only the
    # inferred form fabricated eleven failures that did not exist. Matching only
    # helpers named "_probe_u_*" hid a genuine blank-screen defect: three
    # uniforms built by _vsb_product_storage_image were handed to the shader at
    # slots it no longer declared, and this file reported no failures because the
    # resolver simply did not see them.
    #
    # So the pattern below deliberately does NOT name the helper. It matches any
    # call whose first argument is a plain integer -- which is what a binding
    # slot looks like -- and lets the set-membership check decide relevance. A
    # new helper added tomorrow is covered without anyone remembering to update
    # this list.
    for name, slot in re.findall(
        r"var\s+(u_\w+)\s*(?::\s*[\w.]+\s*)?:?=\s*\w+\(\s*(\d+)\s*[,)]", host
    ):
        slots[name] = int(slot)
    for name, slot in re.findall(r"(\bu_\w+)\.binding\s*=\s*(\d+)", host):
        slots[name] = int(slot)
    return slots


def uniform_set_members(host: str, shader_var: str) -> set[str] | None:
    """Return the uniform variable names passed to one uniform-set build.

    shader_var - the shader argument the set is built against, which is what
    distinguishes the compositor's set from the Product's set from the VSB's.
    Returns None when that set construction cannot be found.
    """
    call = re.search(
        r"UniformSetCacheRD\.get_cache\(\s*" + re.escape(shader_var) + r"\s*,\s*0\s*,\s*\[(.*?)\]",
        host,
        re.DOTALL,
    )
    if call is not None:
        return set(re.findall(r"\bu_\w+", call.group(1)))

    # The compositor selects its ordinary and explicit-capture shader RIDs at
    # runtime, then passes a named uniform array to get_cache(). Resolve the
    # array's literal initializer so the ordinary six-binding ABI remains
    # mechanically comparable without pretending the capture-only b43 append
    # belongs to every frame.
    named_call = re.search(
        r"UniformSetCacheRD\.get_cache\(\s*"
        + re.escape(shader_var)
        + r"\s*,\s*0\s*,\s*(\w+)\s*\)",
        host,
        re.DOTALL,
    )
    if named_call is None:
        return None
    member_array = named_call.group(1)
    initializer = re.search(
        r"var\s+"
        + re.escape(member_array)
        + r"\s*(?::\s*[^=]+)?=\s*\[(.*?)\]",
        host,
        re.DOTALL,
    )
    if initializer is None:
        return None
    return set(re.findall(r"\bu_\w+", initializer.group(1)))


def compositor_shipping_source(source: str) -> str:
    """Return the ordinary compositor variant's source-visible ABI.

    Binding 43 and its atomic receipt writer compile only for the explicitly
    requested FL4431_FINAL_CELL_CAPTURE variant. The shipping host compiles the
    same source without that define, so two-sided ABI validation must compare
    the host's ordinary set against the preprocessed ordinary declaration set.
    """

    return re.sub(
        r"(?ms)^\s*#ifdef\s+FL4431_FINAL_CELL_CAPTURE\s*$"
        r".*?^\s*#endif\s*$",
        "",
        source,
    )


def shader_resource_classes(source: str) -> dict[int, str]:
    """Map shader binding numbers to RenderingDevice-compatible classes.

    A slot number alone is not an ABI. Vulkan also requires the descriptor
    class to match: a storage buffer supplied for a uniform block makes uniform
    set construction fail even though both sides say, for example, "binding
    68". This parser classifies the declaration attached to each slot.
    """
    classes: dict[int, str] = {}
    declaration = re.compile(
        r"layout\s*\(([^)]*\bbinding\s*=\s*(\d+)[^)]*)\)\s*"
        r"(?:(?:readonly|writeonly|restrict)\s+)*"
        r"(uniform|buffer)\s+(?:(?:readonly|writeonly|restrict)\s+)*(\w+)",
        re.MULTILINE,
    )
    for match in declaration.finditer(strip_comments(source)):
        slot = int(match.group(2))
        declaration_kind = match.group(3)
        declared_type = match.group(4)
        if declaration_kind == "buffer":
            resource_class = "storage_buffer"
        elif "sampler" in declared_type:
            resource_class = "sampler_with_texture"
        elif "texture" in declared_type:
            # Separate texture objects are the sampler-less Metal-safe Product
            # ABI. They map to RD's texture descriptor class, not a UBO.
            resource_class = "texture"
        elif "image" in declared_type:
            resource_class = "image"
        else:
            resource_class = "uniform_buffer"
        if slot in classes:
            classes[slot] = "duplicate"
        else:
            classes[slot] = resource_class
    return classes


def host_uniform_specs(host: str) -> dict[str, tuple[int, str]]:
    """Map each named host uniform to its slot and descriptor class.

    Helpers and hand-built RDUniform objects are both live construction forms
    in this host. Reading only one form produced a false-green agreement check
    earlier in FL-4431, so this routine resolves both from executable source.
    """
    class_names = {
        "STORAGE_BUFFER": "storage_buffer",
        "UNIFORM_BUFFER": "uniform_buffer",
        "SAMPLER_WITH_TEXTURE": "sampler_with_texture",
        "TEXTURE": "texture",
        "IMAGE": "image",
    }
    helper_classes: dict[str, str] = {}
    for helper_match in re.finditer(r"^func\s+(\w+)\b", host, re.MULTILINE):
        helper_name = helper_match.group(1)
        body = extract_gd_function(host, helper_name)
        if body is None:
            continue
        types = re.findall(
            r"\.uniform_type\s*=\s*RenderingDevice\.UNIFORM_TYPE_(\w+)",
            body,
        )
        if len(set(types)) == 1 and types[0] in class_names:
            helper_classes[helper_name] = class_names[types[0]]

    slots = resolve_uniform_slots(host)
    classes: dict[str, str] = {}
    for name, helper_name, _slot in re.findall(
        r"var\s+(u_\w+)\s*(?::\s*[\w.]+\s*)?:?=\s*(\w+)\(\s*(\d+)\s*[,)]",
        host,
    ):
        if helper_name in helper_classes:
            classes[name] = helper_classes[helper_name]
    for name, type_name in re.findall(
        r"\b(u_\w+)\.uniform_type\s*=\s*"
        r"RenderingDevice\.UNIFORM_TYPE_(\w+)",
        host,
    ):
        if type_name in class_names:
            classes[name] = class_names[type_name]
    return {
        name: (slot, classes[name])
        for name, slot in slots.items()
        if name in classes
    }


def vsb_host_resource_classes(host: str, builder: str) -> dict[int, str]:
    """Derive the b64-b83 host classes from the actual branch conditions."""
    constants = {
        name: int(value)
        for name, value in re.findall(
            r"const\s+(BINDING_\w+)\s*:?=\s*(\d+)",
            builder,
        )
    }
    body = extract_gd_function(host, "_build_vsb_uniform_set")
    if body is None:
        return {}
    classes = {
        constants["BINDING_VISIBLE_SURFACE_PRODUCTS"]: "storage_buffer"
    }
    sampler_branch = re.search(
        r"if\s+(.*?)\s*:\s*\n\s*uniform\.uniform_type\s*=\s*"
        r"RenderingDevice\.UNIFORM_TYPE_SAMPLER_WITH_TEXTURE",
        body,
        re.DOTALL,
    )
    uniform_branch = re.search(
        r"elif\s+(.*?)\s*:\s*\n\s*uniform\.uniform_type\s*=\s*"
        r"RenderingDevice\.UNIFORM_TYPE_UNIFORM_BUFFER",
        body,
        re.DOTALL,
    )
    sampler_names = (
        set(re.findall(r"VisibleSurfaceBuilderScript\.(BINDING_\w+)", sampler_branch.group(1)))
        if sampler_branch is not None
        else set()
    )
    uniform_names = (
        set(re.findall(r"VisibleSurfaceBuilderScript\.(BINDING_\w+)", uniform_branch.group(1)))
        if uniform_branch is not None
        else set()
    )
    for name, slot in constants.items():
        if slot == constants["BINDING_VISIBLE_SURFACE_PRODUCTS"]:
            continue
        if name in sampler_names:
            classes[slot] = "sampler_with_texture"
        elif name in uniform_names:
            classes[slot] = "uniform_buffer"
        else:
            classes[slot] = "storage_buffer"
    return classes


def section_host_binding_agreement() -> None:
    host = strip_gd_comments(ACE_PATH.read_text(encoding="utf-8", errors="replace"))
    product_raw = _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8", errors="replace"))
    compositor_raw = compositor_shipping_source(
        COMPOSITOR_PATH.read_text(encoding="utf-8", errors="replace")
    )
    product = strip_comments(product_raw)
    compositor = strip_comments(compositor_raw)
    specs = host_uniform_specs(host)

    # Each entry: which host set, which shader it targets, and that shader's text.
    # BOTH directions are checked for each, because the two failure modes are
    # equally fatal and both occurred in FL-4431:
    #   host binds a slot the shader dropped  -> set creation fails, stage dead;
    #   shader declares a slot the host never supplies -> same failure, opposite
    #   cause. Only the first direction was guarded at first; mutation testing
    #   showed the second could be broken while the battery reported no failures.
    targets = (
        ("Product", "cell_owner_product_shader", product, "the stage never runs"),
        (
            "compositor",
            "compositor_shader_for_frame",
            compositor,
            "nothing is painted",
        ),
    )

    for label, shader_var, shader_src, consequence in targets:
        declared = shader_resource_classes(shader_src)
        members = uniform_set_members(host, shader_var)
        if not check(members is not None, f"{label} uniform-set construction not found in host"):
            continue
        missing_specs = sorted(member for member in members if member not in specs)
        check(
            not missing_specs,
            f"{label} set members must expose both slot and descriptor class; "
            f"unresolved members: {missing_specs}",
        )
        bound = {
            specs[member][0]: specs[member][1]
            for member in members
            if member in specs
        }

        extra = sorted(set(bound) - set(declared))
        for slot in extra:
            check(
                False,
                f"host binds {label} slot {slot} but the shader does not declare it; "
                f"uniform-set creation fails and {consequence}",
            )
        check(
            not extra,
            f"every host-bound {label} slot must be declared by the shader (extra: {extra})",
        )

        missing = sorted(set(declared) - set(bound))
        for slot in missing:
            check(
                False,
                f"{label} shader declares slot {slot} but the host set never supplies it; "
                f"uniform-set creation fails and {consequence}",
            )
        check(
            not missing,
            f"every {label} shader-declared slot must be supplied by the host set (missing: {missing})",
        )
        for slot in sorted(set(bound) & set(declared)):
            check(
                bound[slot] == declared[slot],
                f"{label} binding {slot} descriptor class mismatch: host "
                f"{bound[slot]}, shader {declared[slot]}",
            )

    # VSB builds its uniform array in a range loop instead of naming every
    # member in a cache literal. Derive each loop branch's class, then compare
    # all twenty slots against the shader declarations.
    builder = strip_gd_comments(
        BUILDER_PATH.read_text(encoding="utf-8", errors="replace")
    )
    vsb_declared = shader_resource_classes(
        VSB_PATH.read_text(encoding="utf-8", errors="replace")
    )
    vsb_bound = vsb_host_resource_classes(host, builder)
    check(
        set(vsb_bound) == set(vsb_declared),
        "VSB host and shader must cover the same complete b64-b83 slot set; "
        f"host={sorted(vsb_bound)}, shader={sorted(vsb_declared)}",
    )
    for slot in sorted(set(vsb_bound) & set(vsb_declared)):
        check(
            vsb_bound[slot] == vsb_declared[slot],
            f"VSB binding {slot} descriptor class mismatch: host "
            f"{vsb_bound[slot]}, shader {vsb_declared[slot]}",
        )


# =============================================================================
# SECTION 9 - Product's 272-byte frame receipt must match its std140 block.
# =============================================================================

PRODUCT_FRAME_RECEIPT_FIELDS = (
    ("vec4", "camera_origin_near"),
    ("vec4", "camera_forward_far"),
    ("vec4", "camera_right_ortho_half_width"),
    ("vec4", "camera_up_ortho_half_height"),
    ("vec4", "ray_parameters"),
    ("vec4", "heightfield_origin_cell"),
    ("vec4", "heightfield_geometry_contract"),
    ("uvec4", "heightfield_dims_identity"),
    ("uvec4", "heightfield_state"),
    ("vec4", "water_surface_contract"),
    ("uvec4", "water_identity_flags"),
    ("uvec4", "water_counts_revision"),
    ("vec4", "sun_dir_ambient"),
    ("vec4", "cloud_night"),
    ("uvec4", "view_identity"),
    ("vec4", "cohort_colour_transition"),
    ("uvec4", "cohort_colour_contract"),
)


def section_product_frame_receipt_abi() -> None:
    """Compare both b68 consumers against the host's actual 16-byte writes.

    Product and the compositor share one immutable UBO RID. Mirroring the full
    reviewed block on both shader sides prevents the small compositor consumer
    from inventing a shortened projection-only ABI that can drift independently.
    """
    product = strip_comments(_expand_includes(PRODUCT_PATH.read_text(encoding="utf-8", errors="replace")))
    compositor = strip_comments(
        COMPOSITOR_PATH.read_text(encoding="utf-8", errors="replace")
    )
    host = strip_gd_comments(ACE_PATH.read_text(encoding="utf-8", errors="replace"))
    for label, shader_source in (
        ("Product", product),
        ("compositor", compositor),
    ):
        block = re.search(
            r"binding\s*=\s*68[^)]*\)\s*uniform\s+VsbProductFrameReceipt\s*\{"
            r"(.*?)\}\s*product_vsb_frame\s*;",
            shader_source,
            re.DOTALL,
        )
        if not check(
            block is not None,
            f"{label} b68 std140 receipt block is missing",
        ):
            continue
        fields = tuple(
            re.findall(r"\b(vec4|uvec4)\s+(\w+)\s*;", block.group(1))
        )
        check(
            fields == PRODUCT_FRAME_RECEIPT_FIELDS,
            f"{label} b68 row order must remain the reviewed seventeen-row ABI; "
            f"found {fields}",
        )
    size_match = re.search(
        r"const\s+VSB_PRODUCT_FRAME_RECEIPT_BYTES\s*:?=\s*(\d+)",
        host,
    )
    check(
        size_match is not None and int(size_match.group(1)) == 272,
        "host b68 allocation must be exactly seventeen std140 rows (272 bytes)",
    )
    serializer = extract_gd_function(host, "_build_vsb_product_frame_receipt_bytes")
    if not check(serializer is not None, "host b68 serializer is missing"):
        return
    compact = normalize_expression(serializer)
    copied_rows = (
        ("bytes[byte_index]=vsb_push_bytes[16+byte_index]", 0),
        ("bytes[16+byte_index]=vsb_push_bytes[32+byte_index]", 1),
        ("bytes[32+byte_index]=vsb_push_bytes[48+byte_index]", 2),
        ("bytes[48+byte_index]=vsb_push_bytes[64+byte_index]", 3),
        ("bytes[64+byte_index]=vsb_push_bytes[80+byte_index]", 4),
        ("bytes[80+byte_index]=heightfield_bytes[byte_index]", 5),
        ("bytes[96+byte_index]=heightfield_bytes[16+byte_index]", 6),
        ("bytes[112+byte_index]=heightfield_bytes[32+byte_index]", 7),
        ("bytes[128+byte_index]=heightfield_bytes[48+byte_index]", 8),
        ("bytes[144+byte_index]=water_bytes[byte_index]", 9),
        ("bytes[160+byte_index]=water_bytes[16+byte_index]", 10),
        ("bytes[176+byte_index]=water_bytes[32+byte_index]", 11),
    )
    for expression, row in copied_rows:
        check(
            expression in compact,
            f"host b68 row {row} does not copy the reviewed source row",
        )
    check(
        "_write_vec4(bytes,192,sun_direction.x,sun_direction.y,"
        "sun_direction.z,float(_vsb_frame_receipt.get(\"ambient_floor\",0.25)))"
        in compact,
        "host b68 row 12 must write sun direction plus ambient at byte 192",
    )
    check(
        "_write_vec4(bytes,208,float(_vsb_frame_receipt.get("
        "\"cloud_opacity\",0.0)),float(_vsb_frame_receipt.get("
        "\"celestial_night_factor\",0.0)),float(_blood_mark_count),"
        "float(local_light_glyph_count))"
        in compact,
        "host b68 row 13 must write cloud/night plus exact blood and glyph "
        "counts at bytes 216 and 220",
    )
    check(
        "_write_ivec4(bytes,224,int(_vsb_frame_receipt[\"scene_revision\"]),"
        "view_revision,projection_kind,int(_vsb_frame_receipt["
        "\"retained_static_face_total\"]))" in compact,
        "host b68 row 14 must write view identity at byte 224",
    )
    check(
        "_write_vec4(bytes,240,clampf(transition_progress,0.0,1.0),0.0,0.0,0.0)"
        in compact,
        "host b68 row 15 must write transition progress at byte 240",
    )
    check(
        "_write_ivec4(bytes,256,cohort_colour_count,"
        "cohort_colour_abi_version,cohort_colour_record_bytes,"
        "1ifcohort_colour_enabledelse0)" in compact,
        "host b68 row 16 must write the cohort-colour storage contract",
    )


# =============================================================================
# SECTION 9B - Blood scans consume one host-owned active-record count.
# =============================================================================

def section_blood_mark_count_authority() -> None:
    """Pin the b10 active prefix to b68 instead of scanning sixty-four zeros.

    Binding 10 retains a fixed physical capacity because uniform-buffer layouts
    cannot change per frame. The number of meaningful rows is different: World
    publishes it with the records, the host copies only complete rows, and b68
    carries that exact count to both Product blood consumers. A zero count must
    return before either function reads a single b10 row.
    """
    product = strip_comments(
        _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8", errors="replace"))
    )
    compositor_shader = strip_comments(
        COMPOSITOR_PATH.read_text(encoding="utf-8", errors="replace")
    )
    host = strip_gd_comments(
        ACE_PATH.read_text(encoding="utf-8", errors="replace")
    )
    world = strip_gd_comments(
        WORLD_PATH.read_text(encoding="utf-8", errors="replace")
    )

    setter = extract_gd_function(host, "set_blood_events")
    serializer = extract_gd_function(
        host, "_build_vsb_product_frame_receipt_bytes"
    )
    prepare = extract_gd_function(host, "_prepare_cell_owner_product_dispatch")
    callback = extract_gd_function(host, "_render_callback")
    world_sync = extract_gd_function(world, "_sync_blood_marks_to_compositor")
    count_reader = extract_glsl_function(
        product, "blood_mark_count_from_receipt"
    )
    ground_scan = extract_glsl_function(product, "blood_fact_at_world")
    actor_scan = extract_glsl_function(product, "actor_body_blood_fact")

    required_functions = {
        "host blood setter": setter,
        "host b68 serializer": serializer,
        "host Product preparation": prepare,
        "host render callback": callback,
        "World blood publication": world_sync,
        "Product blood-count reader": count_reader,
        "Product ground-blood scan": ground_scan,
        "Product actor-blood scan": actor_scan,
    }
    for label, body in required_functions.items():
        check(body is not None, f"{label} is missing")
    if any(body is None for body in required_functions.values()):
        return

    setter_compact = normalize_expression(setter)
    check(
        "var_blood_mark_count:int=0" in normalize_expression(host),
        "host must keep one frame-visible blood-mark count initialized to zero",
    )
    check(
        "varcomplete_row_count:int=int(rows.size()/BLOOD_MARK_STRIDE)"
        in setter_compact
        and "varcopy_count:int=clampi(mark_count,0,"
        "mini(BLOOD_MARK_MAX_COUNT,complete_row_count))" in setter_compact,
        "blood setter must bound the copied prefix by request, physical "
        "capacity, and complete twelve-float rows",
    )
    check(
        "_blood_mark_count=copy_count" in setter_compact
        and setter_compact.find("_blood_mark_count=copy_count")
        < setter_compact.find("_blood_mark_dirty=true"),
        "blood setter must publish the exact copied count before marking b10 dirty",
    )
    check(
        "ifsrc_offset+j<rows.size()" not in setter_compact,
        "blood setter must not count a partially copied final record",
    )

    serializer_compact = normalize_expression(serializer)
    check(
        "_write_vec4(bytes,208,float(_vsb_frame_receipt.get("
        "\"cloud_opacity\",0.0)),float(_vsb_frame_receipt.get("
        "\"celestial_night_factor\",0.0)),float(_blood_mark_count),"
        "float(local_light_glyph_count))"
        in serializer_compact,
        "b68 byte 216 must serialize the exact host blood count in cloud_night.z",
    )
    product_block = re.search(
        r"binding\s*=\s*68[^)]*\)\s*uniform\s+VsbProductFrameReceipt\s*\{"
        r"(.*?)\}\s*product_vsb_frame\s*;",
        product,
        re.DOTALL,
    )
    compositor_block = re.search(
        r"binding\s*=\s*68[^)]*\)\s*uniform\s+VsbProductFrameReceipt\s*\{"
        r"(.*?)\}\s*product_vsb_frame\s*;",
        compositor_shader,
        re.DOTALL,
    )
    check(
        product_block is not None
        and compositor_block is not None
        and "vec4 cloud_night;" in product_block.group(1)
        and "vec4 cloud_night;" in compositor_block.group(1),
        "both b68 shader mirrors must retain cloud_night as row 13",
    )

    count_compact = compact_source(count_reader)
    for token in (
        "product_vsb_frame.cloud_night.z",
        "isnan(encoded_count)",
        "isinf(encoded_count)",
        "encoded_count<0.0",
        "encoded_count>float(BLOOD_MARK_MAX_COUNT)",
        "encoded_count==float(count)?count:0u",
    ):
        check(
            token in count_compact,
            f"blood-count reader lost fail-closed condition: {token}",
        )

    for label, scan in (
        ("ground", ground_scan),
        ("actor", actor_scan),
    ):
        scan_compact = compact_source(scan)
        check(
            "mark_count=blood_mark_count_from_receipt()" in scan_compact,
            f"{label} blood scan must consume the shared b68 count",
        )
        check(
            "if(mark_count==0u)" in scan_compact,
            f"{label} blood scan must return before b10 loads when count is zero",
        )
        check(
            "for(inti=0;i<BLOOD_MARK_MAX_COUNT;i++)" in scan_compact
            and "if(uint(i)>=mark_count)break;" in scan_compact,
            f"{label} blood scan must keep a compile-time capacity ceiling "
            "and stop at the authoritative prefix",
        )
        first_load = scan_compact.find("blood_marks.marks[i*3+0]")
        break_guard = scan_compact.find("if(uint(i)>=mark_count)break;")
        check(
            first_load >= 0 and 0 <= break_guard < first_load,
            f"{label} blood scan must test the count before its first b10 load",
        )

    world_compact = normalize_expression(world_sync)
    check(
        "_ascii_effect.set_blood_events(rows,count)" in world_compact,
        "World must publish record bytes and their physical slot count together",
    )
    callback_compact = normalize_expression(callback)
    prepare_compact = normalize_expression(prepare)
    check(
        callback_compact.find("_build_vsb_view_dispatch(pixel_ctx,view)")
        < callback_compact.find(
            "_prepare_cell_owner_product_dispatch(push,pixel_ctx)"
        )
        and "_ensure_blood_mark_buffer_synced()" in prepare_compact,
        "the b68 count and b10 bytes must both upload before Product dispatch",
    )


# =============================================================================
# SECTION 9C - Local-light glyphs consume one host-owned active-record count.
# =============================================================================

def section_local_light_glyph_count_authority() -> None:
    """Pin the b13 live prefix to the successful per-view host pack.

    Binding 13 keeps sixty-four physical records for a stable descriptor ABI.
    The host builder knows how many complete glyph records it actually packed,
    threads that value directly through the view receipt, and stores it in b68
    byte 220. Product rejects malformed counts and returns before its first b13
    load when the current view has no glyphs.
    """
    product = strip_comments(
        _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8", errors="replace"))
    )
    compositor_shader = strip_comments(
        COMPOSITOR_PATH.read_text(encoding="utf-8", errors="replace")
    )
    host = strip_gd_comments(
        ACE_PATH.read_text(encoding="utf-8", errors="replace")
    )

    builder = extract_gd_function(host, "_build_local_light_records")
    serializer = extract_gd_function(
        host, "_build_vsb_product_frame_receipt_bytes"
    )
    view_builder = extract_gd_function(host, "_build_vsb_view_dispatch")
    callback = extract_gd_function(host, "_render_callback")
    count_reader = extract_glsl_function(
        product, "local_light_glyph_count_from_receipt"
    )
    glyph_scan = extract_glsl_function(
        product, "compute_local_light_glyph_cell_product"
    )
    required_functions = {
        "host local-light builder": builder,
        "host b68 serializer": serializer,
        "host VSB view builder": view_builder,
        "host render callback": callback,
        "Product glyph-count reader": count_reader,
        "Product local-light glyph scan": glyph_scan,
    }
    for label, body in required_functions.items():
        check(body is not None, f"{label} is missing")
    if any(body is None for body in required_functions.values()):
        return

    builder_compact = normalize_expression(builder)
    check(
        "func_build_local_light_records(" in builder_compact
        and ")->int:" in builder_compact,
        "local-light builder must return its packed glyph count",
    )
    for token in (
        "ifpixel_ctx==null:",
        "not_local_light_control_rd.is_valid()",
        "not_local_light_record_rd.is_valid()",
        "not_local_light_glyph_record_rd.is_valid()",
        "_local_light_control_data.size()!=LOCAL_LIGHT_CONTROL_BYTES",
        "_local_light_record_data.size()"
        "!=LOCAL_LIGHT_MAX_RECORDS*LOCAL_LIGHT_RECORD_BYTES",
        "_local_light_glyph_record_data.size()"
        "!=LOCAL_LIGHT_MAX_GLYPH_RECORDS*LOCAL_LIGHT_GLYPH_RECORD_BYTES",
    ):
        check(
            token in builder_compact,
            f"local-light builder lost fail-closed input: {token}",
        )
    check(
        builder_compact.count("return-1") >= 3,
        "missing PixelCamera, RID, and byte capacity must each fail with -1",
    )
    check(
        "returnglyph_matched" in builder_compact,
        "local-light builder must return the exact successfully packed prefix",
    )
    check(
        "glyph_matched=mini(glyph_candidates.size(),"
        "LOCAL_LIGHT_MAX_GLYPH_RECORDS)" in builder_compact,
        "local-light glyph cache hit must preserve the same active prefix",
    )

    callback_compact = normalize_expression(callback)
    check(
        "varlocal_light_glyph_count:int=_build_local_light_records("
        in callback_compact,
        "render callback must retain the builder's exact per-view count",
    )
    check(
        "_build_vsb_view_dispatch(pixel_ctx,view,local_light_glyph_count)"
        in callback_compact,
        "render callback must thread the builder count directly into the view",
    )
    check(
        "_local_light_last_metrics" not in callback_compact,
        "observational local-light metrics must not source receipt authority",
    )
    check(
        "maxi(local_light_glyph_count,0)" not in callback_compact
        and "clampi(local_light_glyph_count" not in callback_compact,
        "builder failure must remain -1 so the view fails closed",
    )

    view_compact = normalize_expression(view_builder)
    for token in (
        "local_light_glyph_count:int",
        "local_light_glyph_count<0",
        "local_light_glyph_count>LOCAL_LIGHT_MAX_GLYPH_RECORDS",
        "\"absence_reason\":\"local-lightglyphpublicationrejected\"",
        "_build_vsb_product_frame_receipt_bytes("
        "push_bytes,view_revision,projection_kind,local_light_glyph_count,"
        "transition_progress,cohort_colour_count,",
    ):
        check(
            token in view_compact,
            f"view receipt lost glyph-count handoff: {token}",
        )
    check(
        "_local_light_last_metrics" not in view_compact,
        "view receipt must not rebuild authority from analyzer metrics",
    )

    serializer_compact = normalize_expression(serializer)
    check(
        "local_light_glyph_count:int" in serializer_compact
        and "local_light_glyph_count<0" in serializer_compact
        and "local_light_glyph_count>LOCAL_LIGHT_MAX_GLYPH_RECORDS"
        in serializer_compact,
        "b68 serializer must reject missing and oversized glyph counts",
    )
    check(
        "_write_vec4(bytes,208,float(_vsb_frame_receipt.get("
        "\"cloud_opacity\",0.0)),float(_vsb_frame_receipt.get("
        "\"celestial_night_factor\",0.0)),float(_blood_mark_count),"
        "float(local_light_glyph_count))" in serializer_compact,
        "b68 byte 220 must serialize glyph count in cloud_night.w while "
        "blood remains in cloud_night.z",
    )
    check(
        "_local_light_last_metrics" not in serializer_compact,
        "b68 serializer must consume the threaded count, not metrics",
    )

    product_block = re.search(
        r"binding\s*=\s*68[^)]*\)\s*uniform\s+VsbProductFrameReceipt\s*\{"
        r"(.*?)\}\s*product_vsb_frame\s*;",
        product,
        re.DOTALL,
    )
    compositor_block = re.search(
        r"binding\s*=\s*68[^)]*\)\s*uniform\s+VsbProductFrameReceipt\s*\{"
        r"(.*?)\}\s*product_vsb_frame\s*;",
        compositor_shader,
        re.DOTALL,
    )
    check(
        product_block is not None
        and compositor_block is not None
        and "vec4 cloud_night;" in product_block.group(1)
        and "vec4 cloud_night;" in compositor_block.group(1),
        "both b68 mirrors must retain the shared cloud_night count row",
    )

    count_compact = compact_source(count_reader)
    for token in (
        "product_vsb_frame.cloud_night.w",
        "isnan(encoded_count)",
        "isinf(encoded_count)",
        "encoded_count<0.0",
        "encoded_count>float(LOCAL_LIGHT_MAX_GLYPH_RECORDS)",
        "encoded_count==float(count)?count:0u",
    ):
        check(
            token in count_compact,
            f"glyph-count reader lost fail-closed condition: {token}",
        )

    scan_compact = compact_source(glyph_scan)
    check(
        "glyph_count=local_light_glyph_count_from_receipt()" in scan_compact,
        "local-light glyph scan must consume the shared b68 count",
    )
    check(
        "if(glyph_count==0u)returnproduct;" in scan_compact,
        "zero glyph count must return before any b13 access",
    )
    check(
        "for(inti=0;i<LOCAL_LIGHT_MAX_GLYPH_RECORDS;++i)" in scan_compact
        and "if(uint(i)>=glyph_count)break;" in scan_compact,
        "glyph scan must retain a compile-time capacity and stop at the prefix",
    )
    first_load = scan_compact.find("local_light_glyph_alpha(i)")
    prefix_break = scan_compact.find("if(uint(i)>=glyph_count)break;")
    zero_exit = scan_compact.find("if(glyph_count==0u)returnproduct;")
    check(
        first_load >= 0
        and 0 <= zero_exit < first_load
        and 0 <= prefix_break < first_load,
        "zero exit and prefix break must both precede the first b13 load",
    )


# =============================================================================
# SECTION 10 - Host dispatch divisors must match shader workgroup declarations.
# =============================================================================

def glsl_local_size(source: str) -> tuple[int, int, int] | None:
    """Read the three local-size dimensions from a compute shader."""
    match = re.search(
        r"layout\s*\(\s*local_size_x\s*=\s*(\d+)\s*,\s*"
        r"local_size_y\s*=\s*(\d+)\s*,\s*local_size_z\s*=\s*(\d+)\s*\)\s*in",
        strip_comments(source),
    )
    return (
        tuple(int(value) for value in match.groups())
        if match is not None
        else None
    )


def section_dispatch_extent_agreement() -> None:
    """Pin every activated dispatch's host coverage to its shader group size."""
    product_size = glsl_local_size(_expand_includes(PRODUCT_PATH.read_text(encoding="utf-8", errors="replace")))
    compositor_size = glsl_local_size(
        COMPOSITOR_PATH.read_text(encoding="utf-8", errors="replace")
    )
    vsb_size = glsl_local_size(VSB_PATH.read_text(encoding="utf-8", errors="replace"))
    check(product_size == (8, 8, 1), f"Product local size drifted: {product_size}")
    check(compositor_size == (8, 8, 1), f"compositor local size drifted: {compositor_size}")
    check(vsb_size == (8, 8, 1), f"VSB local size drifted: {vsb_size}")

    host = strip_gd_comments(ACE_PATH.read_text(encoding="utf-8", errors="replace"))
    dispatch = extract_gd_function(host, "_dispatch_cell_owner_product")
    callback = extract_gd_function(host, "_render_callback")
    builder = strip_gd_comments(
        BUILDER_PATH.read_text(encoding="utf-8", errors="replace")
    )
    groups = extract_gd_function(builder, "dispatch_groups_for_ansi")
    check(
        dispatch is not None
        and "(ansi.x + 7) / 8" in dispatch
        and "(ansi.y + 7) / 8" in dispatch,
        "Product cell dispatch must round ANSI X/Y by its 8x8 local size",
    )
    check(
        dispatch is not None
        and "leaf_lanes" not in dispatch
        and "compute_list_add_barrier" not in dispatch,
        "retired Product leaf dispatch and its barrier must remain absent",
    )
    check(
        callback is not None
        and "(pixel_target_size.x + 7) / 8" in callback
        and "(pixel_target_size.y + 7) / 8" in callback,
        "compositor dispatch must round pixel X/Y by its 8x8 local size",
    )
    local_constant = re.search(
        r"const\s+LOCAL_SIZE\s*:?=\s*Vector2i\(\s*(\d+)\s*,\s*(\d+)\s*\)",
        builder,
    )
    builder_size = (
        (int(local_constant.group(1)), int(local_constant.group(2)), 1)
        if local_constant is not None
        else None
    )
    check(
        builder_size == vsb_size,
        f"VSB host LOCAL_SIZE must match shader local size; host={builder_size}, shader={vsb_size}",
    )
    check(
        groups is not None
        and "LOCAL_SIZE.x" in groups
        and "LOCAL_SIZE.y" in groups,
        "VSB dispatch extent must divide each subcell axis by the shared LOCAL_SIZE",
    )


# =============================================================================
# SECTION 11 - The b64 decoder and input adapter must preserve reviewed laws.
# =============================================================================

def section_product_adapter_semantics() -> None:
    """Pin the cutover's live decoder and owner-specific input reconstruction."""
    product = _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8", errors="replace"))
    decoder = extract_glsl_function(product, "vsb_product_decode_record")
    entity = extract_glsl_function(product, "vsb_product_entity_word")
    mesh_lit = extract_glsl_function(product, "vsb_product_mesh_lit_rgb")
    static = extract_glsl_function(product, "vsb_product_prepare_static")
    dynamic = extract_glsl_function(product, "vsb_product_prepare_dynamic")
    terrain = extract_glsl_function(product, "vsb_product_prepare_terrain")
    water = extract_glsl_function(product, "vsb_product_prepare_water")
    compile_input = extract_glsl_function(product, "vsb_product_compile_input")
    terrain_shade = extract_glsl_function(
        product, "q3_probe_payload_compile_terrain_shade"
    )
    surface_dither = extract_glsl_function(
        product, "q3_probe_payload_apply_surface_stable_dither"
    )
    terrain_material = extract_glsl_function(
        product, "q3_probe_payload_apply_material"
    )
    ortho_cloud = extract_glsl_function(
        product, "q3_probe_payload_ortho_cloud_density_projected_at"
    )
    # FL-4381/FL-4737 R1 (2026-08-13): the up-sun walk to the cloud plane was
    # extracted out of both cloud wrappers into one shared world-space helper.
    # The wrapper still owns the world frame it hands that helper; the field
    # consumption itself now lives in the helper.
    shared_cloud_projection = extract_glsl_function(
        product, "product_cloud_shadow_projected_world"
    )
    payload_compile = extract_glsl_function(product, "compile_probe_subcell_payload")
    required = {
        "decoder": decoder,
        "entity": entity,
        "mesh light": mesh_lit,
        "static adapter": static,
        "dynamic adapter": dynamic,
        "terrain adapter": terrain,
        "water adapter": water,
        "input-frame adapter": compile_input,
        "terrain shade compiler": terrain_shade,
        "surface dither": surface_dither,
        "terrain material compiler": terrain_material,
        "ortho cloud field": ortho_cloud,
        "payload compiler": payload_compile,
    }
    for label, body in required.items():
        check(body is not None, f"Product {label} function is missing")
    if any(body is None for body in required.values()):
        return
    decoder_compact = compact_source(decoder)
    check(
        "record.status_flags==(VSB_STATUS_ABSENT|VSB_STATUS_NO_HIT)"
        in decoder_compact,
        "b64 decoder sky admission must use exact status equality",
    )
    check(
        "record.status_flags!=VSB_STATUS_VALID" in decoder_compact
        and "returnVSB_PRODUCT_DECODE_HIT;" in decoder_compact,
        "b64 decoder must admit only VALID-alone hits and reject every other status",
    )
    for required_token in (
        "record.abi_version!=VSB_VISIBLE_SURFACE_ABI_VERSION",
        "record.scene_revision!=product_vsb_frame.view_identity.x",
        "record.view_revision!=product_vsb_frame.view_identity.y",
        "record.projection_kind!=product_vsb_frame.view_identity.z",
        "record.owner_kind<VSB_OWNER_STATIC",
        "record.owner_kind>VSB_OWNER_DYNAMIC",
        "record.object_id==VSB_UINT_SENTINEL",
        "record.primitive_id==VSB_UINT_SENTINEL",
        "record.material==VSB_UINT_SENTINEL",
        "floatBitsToUint(record.coverage)!=floatBitsToUint(1.0)",
        "!vsb_product_finite_vec4(record.world_position)",
    ):
        check(
            required_token in decoder_compact,
            f"b64 decoder lost fail-closed validation: {required_token}",
        )

    terrain_compact = compact_source(terrain)
    check(
        "record.primitive_id-product_vsb_frame.heightfield_state.z"
        in terrain_compact
        and "uvec2(local_primitive%dims.x,local_primitive/dims.x)"
        in terrain_compact,
        "terrain primitive identity must decode by base subtraction then modulo/division",
    )
    check(
        "unsigned_cell.x>0xffffu||unsigned_cell.y>0xffffu" in terrain_compact,
        "terrain material-cell overflow must fail closed before 16-bit packing",
    )
    check(
        "cell_xz=product_vsb_frame.heightfield_origin_cell.xy"
        "+vec2(cell)*product_vsb_frame.heightfield_origin_cell.z;"
        "vec2anchor_xz=cell_xz+0.5*product_vsb_frame.heightfield_origin_cell.z;"
        "floath00=input_value.selected_corner_heights.x;"
        "floath11=input_value.selected_corner_heights.w;"
        "floatanchor_y=0.5*(h00+h11);" in terrain_compact,
        "terrain anchor must preserve the adapter's diagonal h00/h11 corner order",
    )

    water_compact = compact_source(water)
    check(
        "boolbackstop=local_primitive==cell_count;" in water_compact,
        "water backstop must use the reserved primitive class after base subtraction",
    )
    check(
        "semantic_cell=ivec2(floor(record.world_position.xz"
        "-product_vsb_frame.heightfield_origin_cell.xy));" in water_compact,
        "water backstop semantic cell must remain signed world-position minus origin",
    )
    check(
        "unsigned_cell.x>0xffffu||unsigned_cell.y>0xffffu" in water_compact,
        "finite-water material-cell overflow must fail closed",
    )
    check(
        "floatselected_average_height=0.25*("
        "input_value.selected_corner_heights.x"
        "+input_value.selected_corner_heights.y"
        "+input_value.selected_corner_heights.z"
        "+input_value.selected_corner_heights.w);"
        "input_value.facts.water_depth="
        "max(WATER_SURFACE_Y-selected_average_height,0.0)"
        in water_compact,
        "finite-water depth must use the adapter's ordered four-corner bed average",
    )
    check(
        water_compact.count("VSB_PRODUCT_BACKSTOP_SOURCE_KEY") == 2
        and "VSB_PRODUCT_BACKSTOP_SOURCE_KEY&" not in water_compact,
        "water backstop sentinel must stay unmasked in texel and source-key facts",
    )

    input_compact = compact_source(compile_input)
    check(
        "input_value.selected_corner_heights=vec4("
        "texelFetch(terrain_corner_height_tex,corner00,0).r,"
        "texelFetch(terrain_corner_height_tex,corner10,0).r,"
        "texelFetch(terrain_corner_height_tex,corner01,0).r,"
        "texelFetch(terrain_corner_height_tex,corner11,0).r);"
        "input_value.selected_corner_heights_valid=true;" in input_compact,
        "the shallow Product adapter must fetch h00/h10/h01/h11 in canonical order",
    )
    for required_token in (
        "input_value.ray=frozen_ray",
        "if(!frozen_ray_valid){"
        "vsb_product_prepare_diagnostic=0x0201u;"
        "returninput_value;}",
        "input_value.identity.object_id=record.object_id",
        "input_value.identity.primitive_id=record.primitive_id",
        "input_value.identity.material=record.material",
        "input_value.identity.stable_world_anchor=record.stable_world_anchor",
        "input_value.context.payload_normal=record.world_normal.xyz",
        "input_value.context.anchor_world=record.stable_world_anchor.xyz",
        "input_value.context.terrain_ground_world=record.world_position.xyz",
        "input_value.context.trace_receiver=record.world_position.xyz",
        "input_value.context.trace_t_hit=record.depth",
        "input_value.facts.world_depth=record.depth",
    ):
        check(
            required_token in input_compact,
            f"b64 input-frame mapping drifted: {required_token}",
        )

    entity_compact = compact_source(entity)
    check(
        "0x80000000u" in entity_compact
        and "((shade_q5&0x1fu)<<PROBE_ENTITY_MESH_SHADE_SHIFT)"
        in entity_compact
        and "((mesh_type&0x0fu)<<PROBE_ENTITY_MESH_TYPE_SHIFT)"
        in entity_compact
        and "((surface_kind&0x0fu)<<18u)" in entity_compact
        and "(stable_key&0xffffu)" in entity_compact,
        "entity word must preserve validity, shade, type, surface, zero probe, and stable-key lanes",
    )
    product_compact = compact_source(product)
    check(
        "constuintPROBE_ENTITY_MESH_TYPE_SHIFT=22u;" in product_compact
        and "constuintPROBE_ENTITY_MESH_SHADE_SHIFT=26u;" in product_compact,
        "entity type and shade shifts must remain 22 and 26",
    )

    static_compact = compact_source(static)
    dynamic_compact = compact_source(dynamic)
    check(
        "(uint(product_vsb_frame.heightfield_dims_identity.x"
        "*product_vsb_frame.heightfield_dims_identity.y)+legacy_face)"
        "&SPLAT_SOURCE_INDEX_MASK" in static_compact,
        "static source key must start after the complete terrain domain",
    )
    check(
        "(terrain_count+product_vsb_frame.view_identity.w+legacy_face)"
        "&SPLAT_SOURCE_INDEX_MASK" in dynamic_compact,
        "dynamic source key must start after terrain plus retained static faces",
    )
    check(
        "legacy_face&0xffffu" in dynamic_compact,
        "dynamic material-cell identity must retain explicit 16-bit masking",
    )

    mesh_compact = compact_source(mesh_lit)
    check(
        "surface_kind!=OWNER_STATIC_MESH&&surface_kind!=OWNER_DYNAMIC_MESH"
        in mesh_compact,
        "W-LIT must reject non-mesh owners",
    )
    for law in (
        "mix(background,foreground,0.35)",
        "clamp(ambient+lambert,ambient,1.0)",
        "mix(light,light*0.42,night)",
    ):
        check(law in mesh_compact, f"W-LIT expression drifted: {law}")
    check(
        "cloud_night.x" not in mesh_lit
        and "terrain_mat_tex" not in mesh_lit
        and "local_light" not in mesh_lit,
        "W-LIT must not double-read receiver-local cloud, terrain material, "
        "or local-light records",
    )

    # FL-4431 TSHADOW3: projection-specific tone registers share one Product
    # owner, yet their historical constants and temporal keys are intentionally
    # different. These checks compare each selected pair so a future cleanup
    # cannot silently overwrite ortho parity or perturb perspective bytes.
    shade_compact = compact_source(terrain_shade)
    check(
        "?product_smooth_terrain_noise(cell,5.0,37u)"
        ":stable_cell_noise(band_cell,37u)" in shade_compact
        and "jitter_amplitude=ortho_register?0.055:0.10" in shade_compact,
        "ortho shade must select baseline smooth +/-0.055 jitter while perspective "
        "retains height-band +/-0.10 jitter",
    )
    check(
        "?product_ansi_dither_threshold(cell)"
        ":stable_cell_noise(band_cell,4359u)" in shade_compact,
        "ortho shade must use the stable-cell ANSI Bayer key while perspective keeps "
        "the resolve height-band key",
    )

    material_compact = compact_source(terrain_material)
    check(
        "?q3_probe_payload_ortho_cloud_shadow_for_cell("
        "context.material_cell,context.flags)"
        ":q3_probe_payload_cutover_cloud_shadow_for_cell("
        "context.material_cell,context.flags)" in material_compact,
        "ortho terrain must select the live baseline cloud register while "
        "perspective retains the frozen thirteen-tap register",
    )
    ortho_cloud_compact = compact_source(ortho_cloud)
    shared_cloud_projection_compact = compact_source(shared_cloud_projection)
    check(
        "product_cloud_density_at_sample_xz((receiver+sun*t_cloud).xz)"
        in shared_cloud_projection_compact,
        "the shared cloud projection must consume the live world-owned cloud "
        "field",
    )
    check(
        "product_cloud_shadow_projected_world("
        "params.terrain_params.xy+vec2(cell)+vec2(0.5),receiver_y)"
        in ortho_cloud_compact,
        "ortho cloud projection must delegate to the shared world-space "
        "projection with the terrain origin applied",
    )

    dither_compact = compact_source(surface_dither)
    for selected_pair in (
        "amp=ortho_register?0.055:0.090",
        "amp=ortho_register?0.045:0.085",
        "fg_tint_weight=ortho_register?0.11:0.16",
        "bg_tint_weight=ortho_register?0.08:0.14",
        "amp*=ortho_register?0.40:0.65",
    ):
        check(
            selected_pair in dither_compact,
            f"ortho/perspective surface-dither register drifted: {selected_pair}",
        )

    payload_compile_compact = compact_source(payload_compile)
    check(
        "ortho_mountain_glyph_remapped="
        "product_vsb_frame.view_identity.z==VSB_PROJECTION_ORTHOGRAPHIC"
        "&&glyph!=glyph_before_mountain_remap" in payload_compile_compact
        and "family==TERRAIN_FAMILY_CLIFF_ROCK?0.20:0.17"
        in payload_compile_compact,
        "only an ortho mountain glyph remap may select the baseline 0.20/0.17 "
        "contrast step",
    )
    expected_markers = {
        "Q3_DIAG_MARKER_BACKSTOP": "2u",
        "Q3_DIAG_MARKER_FOLIAGE": "4u",
        "Q3_DIAG_MARKER_FLOWER": "8u",
    }
    marker_map = dict(
        re.findall(
            r"const\s+uint\s+(Q3_DIAG_MARKER_\w+)\s*=\s*(\d+u)",
            strip_comments(product),
        )
    )
    check(
        marker_map == expected_markers,
        f"Product diagnostic marker set must be exactly {expected_markers}; found {marker_map}",
    )
    main_body = extract_glsl_function(product, "main")
    check(
        main_body is not None
        and "product_is_perspective" not in main_body
        and "projection_kind" not in main_body
        and "product_vsb_frame.view_identity.z" not in main_body,
        "Product main must run one decoder/compiler path for both projection families",
    )

    # These small arithmetic witnesses make the source checks falsifiable in
    # terms of the real defect class, without claiming GPU execution.
    clean_absence = 2 | 4
    decode_model = lambda status: (
        "sky" if status == clean_absence else "hit" if status == 1 else "absent"
    )
    check(
        decode_model(clean_absence) == "sky"
        and decode_model(1) == "hit"
        and decode_model(clean_absence | 1) == "absent"
        and decode_model(2) == "absent",
        "decoder truth-table witness must keep exactly three outcomes",
    )
    h00, h10, h11 = 2.0, 19.0, 8.0
    check(
        0.5 * (h00 + h11) != 0.5 * (h00 + h10),
        "terrain anchor fixture must distinguish diagonal from adjacent-corner averaging",
    )
    bed_corners = (1.0, 3.0, 5.0, 7.0)
    water_surface = 9.0
    check(
        max(water_surface - sum(bed_corners) / 4.0, 0.0) == 5.0,
        "water-depth fixture must exercise four-corner column depth",
    )


# =============================================================================
# SECTION 11B - Product contribution receipts must remain truthful.
# =============================================================================

def section_product_contribution_receipt() -> None:
    """Pin the Product-stage b62 contribution seam from producer to host reader.

    A bit means one named Product stage changed the in-flight glyph or ink and
    Product admitted the paired b55 store. It does not mean the contribution
    survived a later Product overlay, compositor sampling, or presentation.
    The host may synchronously read the matching b62 word only under the
    explicit evidence argument.
    """

    product = _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8", errors="replace"))
    host = ACE_PATH.read_text(encoding="utf-8", errors="replace")
    product_code = strip_comments(product)
    host_code = strip_gd_comments(host)
    finalizer = extract_glsl_function(
        product_code, "vsb_product_finalize_record"
    )
    paint_changed = extract_glsl_function(
        product_code, "vsb_product_paint_changed"
    )
    vapor_mover = extract_glsl_function(
        product_code, "vsb_retained_consider_vapor_mover"
    )
    vapor_candidate = extract_glsl_function(
        product_code, "vsb_retained_consider_vapor_candidate"
    )
    vapor_selector = extract_glsl_function(
        product_code, "vsb_retained_select_vapor_effect_candidates"
    )
    product_main = extract_glsl_function(product_code, "main")
    host_init = extract_gd_function(host_code, "_init")
    render_callback = extract_gd_function(host_code, "_render_callback")
    readback = extract_gd_function(
        host_code, "_read_vsb_secondary_effect_contribution_receipt"
    )
    snapshot = extract_gd_function(
        host_code, "snapshot_fl4431_vapor_product_receipt"
    )
    required = {
        "Product finalizer": finalizer,
        "Product paint-change predicate": paint_changed,
        "retained vapor mover": vapor_mover,
        "retained vapor candidate": vapor_candidate,
        "retained vapor selector": vapor_selector,
        "Product main": product_main,
        "host initializer": host_init,
        "host render callback": render_callback,
        "host b62 reader": readback,
        "host receipt snapshot": snapshot,
    }
    for label, body in required.items():
        check(body is not None, f"{label} is missing")
    if any(body is None for body in required.values()):
        return

    expected_shifts = {
        "PRODUCT_CONTRIBUTED_AIRPLANE_DAMAGE_VAPOR_BIT": 26,
        "PRODUCT_CONTRIBUTED_BONFIRE_SMOKE_BIT": 27,
        "PRODUCT_CONTRIBUTED_LIGHTHOUSE_BEAM_BIT": 28,
        "PRODUCT_CONTRIBUTED_BONFIRE_FLAME_BIT": 29,
        "PRODUCT_CONTRIBUTED_BONFIRE_LIGHT_BIT": 30,
        "PRODUCT_CONTRIBUTED_AIRPLANE_CONTRAIL_BIT": 31,
    }
    shader_shifts = {
        name: int(shift)
        for name, shift in re.findall(
            r"const\s+uint\s+(PRODUCT_CONTRIBUTED_\w+_BIT)\s*=\s*"
            r"1u\s*<<\s*(\d+)u",
            product_code,
        )
    }
    host_shifts = {
        name: int(shift)
        for name, shift in re.findall(
            r"const\s+(PRODUCT_CONTRIBUTED_\w+_BIT)\s*:=\s*"
            r"1\s*<<\s*(\d+)",
            host_code,
        )
    }
    check(
        shader_shifts == expected_shifts,
        "Product contribution marker bits must occupy exactly b62 bits 26..31: "
        f"found {shader_shifts}",
    )
    check(
        host_shifts == expected_shifts,
        "host contribution marker bits must exactly mirror the shader: "
        f"found {host_shifts}",
    )
    masks = [1 << shift for shift in shader_shifts.values()]
    check(
        len(masks) == 6
        and len(set(masks)) == 6
        and all(mask & 0x03FFFFFF == 0 for mask in masks),
        "six contribution marker masks must be disjoint from each other and "
        "from b62's protected low 26 bits",
    )

    finalizer_compact = compact_source(finalizer)
    paint_changed_compact = compact_source(paint_changed)
    vapor_mover_compact = compact_source(vapor_mover)
    vapor_candidate_compact = compact_source(vapor_candidate)
    vapor_selector_compact = compact_source(vapor_selector)
    main_compact = compact_source(product_main)
    check(
        "before_record.r0.z!=after_record.r0.z" in paint_changed_compact
        and "floatBitsToUint(before_record.fg)" in paint_changed_compact
        and "floatBitsToUint(after_record.fg)" in paint_changed_compact
        and "floatBitsToUint(before_record.bg)" in paint_changed_compact
        and "floatBitsToUint(after_record.bg)" in paint_changed_compact,
        "Product paint-change predicate must compare final glyph, foreground, "
        "and background bits",
    )
    check(
        "if((input_value.retained_face.actor_effect_kind==8u"
        "||input_value.retained_face.actor_effect_kind==9u)"
        "&&vsb_product_paint_changed(before_bonfire_face,record))"
        "{secondary_effect_contribution_bits|="
        "PRODUCT_CONTRIBUTED_BONFIRE_FLAME_BIT;}" in finalizer_compact,
        "bonfire-flame contribution must require kind 8/9 plus a paint change",
    )
    check(
        "if(beam.present&&vsb_product_paint_changed(before_beam,record))"
        "{secondary_effect_contribution_bits|="
        "PRODUCT_CONTRIBUTED_LIGHTHOUSE_BEAM_BIT;}" in finalizer_compact,
        "lighthouse contribution must require an admitted beam plus a paint change",
    )
    check(
        "if(local_light_facts.bonfire_contributed"
        "&&vsb_product_paint_changed(before_local_light,record))"
        "{secondary_effect_contribution_bits|="
        "PRODUCT_CONTRIBUTED_BONFIRE_LIGHT_BIT;}" in finalizer_compact,
        "bonfire-light marker must require kind-1 contribution plus a paint change",
    )
    check(
        finalizer_compact.count(
            "vsb_retained_select_vapor_effect_candidates("
            "input_value.ray,effect_depth)"
        ) == 1
        # The view ray joined this call on 2026-07-27 so the smoke paint can
        # shape each cell by where it lands inside the puff, instead of filling
        # every covered cell identically and reading as a square block.
        and "vsb_product_apply_vapor_style("
        "vapor_layers.bonfire_smoke,input_value.ray,record)" in finalizer_compact
        and "if(vapor_layers.bonfire_smoke.valid"
        "&&vsb_product_paint_changed("
        "before_bonfire_smoke,record)){secondary_effect_contribution_bits|="
        "PRODUCT_CONTRIBUTED_BONFIRE_SMOKE_BIT;}" in finalizer_compact,
        "bonfire-smoke contribution must use the single composite traversal's "
        "independent smoke winner and require a paint change",
    )
    check(
        "facts.actor_effect_kind!=4u||facts.source_id==0u"
        in vapor_mover_compact
        and "if(facts.material_id==234u)" in vapor_mover_compact
        and "elseif(facts.material_id==207u"
        "||facts.material_id==211u)" in vapor_mover_compact
        and "selected.bonfire_smoke" in vapor_mover_compact
        and "selected.airplane_vapor" in vapor_mover_compact
        and vapor_mover_compact.count(
            "vsb_retained_consider_vapor_candidate("
        ) == 2
        and "depth>world_depth+0.25" in vapor_candidate_compact
        and "floatBitsToUint(depth)==floatBitsToUint(selected.depth)"
        in vapor_candidate_compact
        and "stack_size>0&&pops<node_count" in vapor_selector_compact,
        "retained vapor selection must admit only kind-4 materials 207/211/234 "
        "and preserve independent bonfire/airplane winners in one bounded walk",
    )
    check(
        # Same added ray argument as the bonfire-smoke call above. The per-cell
        # shaping itself is gated to material 234, so contrail (207) and damage
        # vapor (211) keep their previous appearance byte for byte.
        "vsb_product_apply_vapor_style("
        "vapor_layers.airplane_vapor,input_value.ray,record)" in finalizer_compact
        and "if(vapor_layers.airplane_vapor.valid"
        "&&vsb_product_paint_changed("
        "before_airplane_vapor,record)){"
        "if(vapor_layers.airplane_vapor.facts.material_id==207u)"
        "{secondary_effect_contribution_bits|="
        "PRODUCT_CONTRIBUTED_AIRPLANE_CONTRAIL_BIT;}"
        "elseif(vapor_layers.airplane_vapor.facts.material_id==211u)"
        "{secondary_effect_contribution_bits|="
        "PRODUCT_CONTRIBUTED_AIRPLANE_DAMAGE_VAPOR_BIT;}}" in finalizer_compact,
        "airplane vapor contributions must require selected paint changes and keep "
        "material 207 contrail separate from material 211 damage vapor",
    )
    for bit_name in expected_shifts:
        check(
            finalizer_compact.count(bit_name) == 1
            and product_code.count(bit_name) == 2,
            f"{bit_name} must have one declaration and one guarded finalizer use",
        )

    final_store = (
        "finalized_probe_subcell_products.products[final_index]="
        "staged_final_records[slot];"
    )
    marker_store = (
        "q3_probe_subcell_diagnostics.records[final_index].r0.w"
        "|=staged_secondary_effect_bits[slot];"
    )
    check(
        main_compact.count(final_store) == 1,
        "Product must retain exactly one b55 final-record store",
    )
    failure_builder = extract_glsl_function(
        product_code, "vsb_product_failure_final_record"
    )
    failure_compact = compact_source(failure_builder or "")
    check(
        (
            "record.r0=uvec4(0u,0u,0xDBu,0u);"
            "record.r1=uvec4(0u);"
            "record.fg=vec4(1.0,0.0,1.0,1.0);"
            "record.bg=vec4(1.0,1.0,0.0,1.0);"
            "record.anchor=vec4(0.0);"
        )
        in failure_compact
        and (
            "ProbeSubcellProductRecordfinal_record="
            "vsb_product_failure_final_record();"
        )
        in main_compact,
        "Product's local live-dispatch failure record must byte-match the host "
        "magenta/yellow allocation marker",
    )
    final_initializer_at = main_compact.find(
        "ProbeSubcellProductRecordfinal_record="
        "vsb_product_failure_final_record();"
    )
    publication_loop_at = main_compact.find(
        "for(intslot=0;slot<4;slot+=1){"
        "if(!staged_slot_in_range[slot])continue;"
        "uintfinal_index=staged_final_indices[slot];"
    )
    final_store_at = main_compact.find(final_store)
    final_capacity_guard = (
        "if(visible_index>=final_record_capacity"
        "||final_index>=final_record_capacity)continue;"
    )
    final_capacity_guard_at = main_compact.find(final_capacity_guard)
    staged_in_range_at = main_compact.find(
        "staged_slot_in_range[int(slot)]=true;"
    )
    final_store_depth = (
        main_compact[:final_store_at].count("{")
        - main_compact[:final_store_at].count("}")
        if final_store_at >= 0
        else -1
    )
    check(
        main_compact.count(final_capacity_guard) == 1
        and 0 <= final_capacity_guard_at < staged_in_range_at < final_store_at,
        "Product main must reject both an out-of-range b64 source index and an "
        "out-of-range b55 destination index before marking a staged slot "
        "publishable; inferred equal extents are not a destination-capacity "
        "guard",
    )
    check(
        min(final_initializer_at, final_store_at) >= 0
        and final_initializer_at < final_store_at
        and 0 <= publication_loop_at < final_store_at
        and final_store_depth == 2
        and "continue;" not in main_compact[
            publication_loop_at
            + len(
                "for(intslot=0;slot<4;slot+=1){"
                "if(!staged_slot_in_range[slot])continue;"
            ):
            final_store_at
        ],
        "every explicitly in-range Product subcell must reach the sole b55 "
        "store at the main-loop depth even when semantic work rejects",
    )
    check(
        main_compact.count(marker_store) == 1
        and (
            final_store
            + "if(staged_final_record_ready[slot]){"
            + "if(staged_diagnostic_index_valid[slot]"
            + "&&staged_secondary_effect_bits[slot]!=0u){"
            + marker_store
            + "}"
        ) in main_compact,
        "b62 contribution bits must require an authored valid record and explicit "
        "diagnostic capacity after the sole b55 store",
    )

    diagnostic_fields = re.search(
        r"struct\s+Q3ProbeSubcellDiagnosticRecord\s*\{([^}]*)\}",
        product_code,
        re.DOTALL,
    )
    fields = (
        re.findall(r"\buvec4\s+(\w+)\s*;", diagnostic_fields.group(1))
        if diagnostic_fields is not None
        else []
    )
    check(
        fields == ["r0", "r1", "r2", "r3", "r4", "r5", "r6"],
        "shader b62 records must remain seven uvec4 rows (112 bytes) with marker "
        "word r0.w at byte offset 12",
    )
    readback_compact = normalize_expression(readback)
    check(
        re.search(
            r"const\s+VSB_PRODUCT_Q3_DIAGNOSTIC_RECORD_BYTES\s*:=\s*112\b",
            host_code,
        )
        is not None
        and "record_index*VSB_PRODUCT_Q3_DIAGNOSTIC_RECORD_BYTES+12"
        in readback_compact,
        "host b62 reader must use the shader's 112-byte stride and r0.w byte "
        "offset 12",
    )

    init_compact = normalize_expression(host_init)
    callback_compact = normalize_expression(render_callback)
    snapshot_compact = normalize_expression(snapshot)
    check(
        re.search(
            r"var\s+_fl4431_secondary_effect_evidence_enabled\s*:\s*bool"
            r"\s*=\s*false",
            host_code,
        )
        is not None
        and 'OS.get_cmdline_user_args().has("--fl4431-secondary-effect-evidence")'
        in init_compact,
        "secondary-effect evidence readback must default off and arm only from "
        "its explicit command-line argument",
    )
    check(
        render_callback.count(
            "_read_vsb_secondary_effect_contribution_receipt("
        ) == 1
        and re.search(
            r"(?m)^(?P<indent>[ \t]+)"
            r"if _fl4431_secondary_effect_evidence_enabled:\s*\n"
            r"(?P=indent)[ \t]+"
            r"_read_vsb_secondary_effect_contribution_receipt\(",
            render_callback,
        )
        is not None,
        "the render callback must keep its sole b62 readback behind the "
        "explicit evidence gate",
    )
    check(
        gd_functions_referencing(
            host_code, "_read_vsb_secondary_effect_contribution_receipt("
        ) == {
            "_render_callback",
            "_read_vsb_secondary_effect_contribution_receipt",
        },
        "the render callback must be the sole b62 readback caller",
    )
    check(
        "ifnot_fl4431_secondary_effect_evidence_enabled" in snapshot_compact
        and '"--fl4431-secondary-effect-evidencewasnotsupplied"'
        in snapshot_compact,
        "receipt snapshot must report named absence while evidence is disabled",
    )
    expected_availability_fields = {
        "product_mat207_contribution_receipt_available",
        "product_mat211_contribution_receipt_available",
        "product_lighthouse_beam_contribution_receipt_available",
        "product_bonfire_flame_contribution_receipt_available",
        "product_bonfire_light_contribution_receipt_available",
        "product_bonfire_smoke_contribution_receipt_available",
    }
    expected_count_fields = {
        "product_mat207_contributed_subcell_count",
        "product_mat211_contributed_subcell_count",
        "product_lighthouse_beam_contributed_subcell_count",
        "product_bonfire_flame_contributed_subcell_count",
        "product_bonfire_light_contributed_subcell_count",
        "product_bonfire_smoke_contributed_subcell_count",
    }
    check(
        '"fl4431.secondary_effect_product_contribution_snapshot.v1"'
        in snapshot_compact
        and all(
            f'"{field_name}"' in snapshot_compact
            for field_name in expected_availability_fields | expected_count_fields
        ),
        "the capture-facing schema must name Product contribution availability "
        "and contributed-subcell counts for all six effect lanes",
    )
    check(
        "_visible_receipt_available" not in snapshot
        and "_applied_subcell_count" not in snapshot,
        "the capture-facing API must not claim visible pixels or applied output "
        "from a Product-stage contribution receipt",
    )
    # Read the entire contiguous comment block immediately above the public
    # snapshot. Starting at the last `#` line would inspect only the final
    # sentence and would make a truthful multi-line ownership contract fail.
    snapshot_comment_match = re.search(
        r"(?P<comment>(?:^#[^\n]*\n)+)"
        r"^func\s+snapshot_fl4431_vapor_product_receipt\b",
        host,
        re.MULTILINE,
    )
    snapshot_leading_comment = (
        snapshot_comment_match.group("comment")
        if snapshot_comment_match is not None
        else ""
    )
    check(
        "does not claim" in snapshot_leading_comment
        and "later Product overlay" in snapshot_leading_comment
        and "compositor sampling" in snapshot_leading_comment
        and "presentation" in snapshot_leading_comment,
        "the public snapshot comment must delimit the contribution receipt from "
        "later Product overlays, compositor sampling, and presentation",
    )


# =============================================================================
# SECTION 11C - Observation tooling must not contaminate normal rendering.
# =============================================================================

def section_measurement_contamination() -> None:
    """Keep GPU readbacks, diagnostic uploads, and moving-camera logs explicit.

    Normal HUD polling must read cached metadata only. The final-cell dump owns
    its one GPU copy and releases that RID after decoding. Product b62 exists as
    a full bank only in the named evidence specialization; the shipping
    specialization must compile every access away.
    """

    host = ACE_PATH.read_text(encoding="utf-8", errors="replace")
    product = _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8", errors="replace"))
    host_code = strip_gd_comments(host)
    product_code = strip_comments(product)
    present_state = extract_gd_function(host_code, "get_runtime_present_state")
    paint_audit = extract_gd_function(
        host_code, "_runtime_final_cell_paint_audit"
    )
    row_builder = extract_gd_function(host_code, "build_final_render_cell_rows")
    release_capture = extract_gd_function(
        host_code, "_release_final_cell_paint_capture_buffer"
    )
    ensure_q3 = extract_gd_function(
        host_code, "_ensure_vsb_product_q3_read_facts"
    )
    prepare_product = extract_gd_function(
        host_code, "_prepare_cell_owner_product_dispatch"
    )
    initialize_compute = extract_gd_function(host_code, "_initialize_compute")
    diagnostic_builder = extract_glsl_function(
        product_code, "q3_probe_payload_diagnostic"
    )
    product_main = extract_glsl_function(product_code, "main")
    required = {
        "runtime present state": present_state,
        "final-cell audit": paint_audit,
        "final-cell row builder": row_builder,
        "final-cell release": release_capture,
        "Product read-fact preparation": ensure_q3,
        "Product dispatch preparation": prepare_product,
        "compute initializer": initialize_compute,
        "Product diagnostic builder": diagnostic_builder,
        "Product main": product_main,
    }
    for label, body in required.items():
        check(body is not None, f"{label} is missing")
    if any(body is None for body in required.values()):
        return

    check(
        "_emit_fl4431_cam_diag_on_change" not in host_code
        and "_fl4431_cam_diag_signature_by_view" not in host_code
        and "FL4431_CAM_DIAG" not in host_code,
        "normal rendering must contain no moving-camera diagnostic logger",
    )

    present_compact = compact_source(present_state)
    check(
        "_runtime_final_cell_paint_audit(" not in present_state
        and "buffer_get_data(" not in present_state
        and "_last_explicit_final_cell_paint_audit.duplicate(true)"
        in present_compact,
        "runtime present state must return cached audit metadata without GPU readback",
    )
    check(
        gd_functions_referencing(
            host_code, "_runtime_final_cell_paint_audit("
        )
        == {
            "_runtime_final_cell_paint_audit",
            "build_final_render_cell_rows",
        }
        and "data: PackedByteArray" in paint_audit
        and "buffer_get_data(" not in paint_audit,
        "the full-cell audit must consume bytes owned by the explicit row builder",
    )

    builder_compact = compact_source(row_builder)
    readback_position = builder_compact.find(
        "rd.buffer_get_data(final_cell_paint_rd)"
    )
    audit_position = builder_compact.find(
        "_runtime_final_cell_paint_audit(final_cell_data)"
    )
    release_position = builder_compact.find(
        "_release_final_cell_paint_capture_buffer()"
    )
    check(
        row_builder.count("rd.buffer_get_data(final_cell_paint_rd)") == 1
        and readback_position >= 0
        and readback_position < audit_position < release_position,
        "explicit final-cell capture must read once, audit copied bytes, then release",
    )
    check(
        gd_functions_referencing(
            host_code, "_release_final_cell_paint_capture_buffer("
        )
        == {
            "_release_final_cell_paint_capture_buffer",
            "build_final_render_cell_rows",
            "_cancel_final_cell_paint_capture",
        }
        and "rd.free_rid(final_cell_paint_rd)" in release_capture
        and "final_cell_paint_rd = RID()" in release_capture
        and "_final_cell_paint_alloc_cells = 0" in release_capture
        and "_final_cell_paint_capture_frames = 0" in release_capture,
        "only explicit capture success and named capture failure may release "
        "the evidence RID",
    )

    ensure_compact = compact_source(ensure_q3)
    evidence_guard = "if_fl4431_secondary_effect_evidence_enabled:"
    clear_call = "_vsb_product_q3_clear_bytes(record_count)"
    check(
        evidence_guard in ensure_compact
        and ensure_compact.count(clear_call) == 1
        and ensure_compact.index(evidence_guard)
        < ensure_compact.index(clear_call)
        and "required_rids.append(_vsb_product_q3_diagnostics_rd)"
        in ensure_compact,
        "full b62 allocation and clear must exist only under explicit evidence",
    )
    prepare_compact = compact_source(prepare_product)
    check(
        "_fl4431_secondary_effect_evidence_enabled"
        in prepare_compact
        and "_vsb_product_q3_diagnostics_rd.is_valid()" in prepare_compact
        and "else_static_world_mesh_empty_rd" in prepare_compact
        and "_probe_u_storage(62,q3_diagnostics_bind)" in prepare_compact,
        "ordinary Product must bind the shared empty-storage row at b62",
    )

    initialize_compact = compact_source(initialize_compute)
    check(
        (
            "layout(constant_id = 17) const bool "
            "PRODUCT_B62_DIAGNOSTICS_ENABLED = false;"
        )
        in product
        and "product_b62_diagnostics_specialization.constant_id=17"
        in initialize_compact
        and (
            "product_b62_diagnostics_specialization.value="
            "(_fl4431_secondary_effect_evidence_enabled)"
        )
        in initialize_compact
        and (
            "product_specializations.append("
            "product_b62_diagnostics_specialization)"
        )
        in initialize_compact,
        "Product b62 accesses must share one default-off host/shader specialization",
    )

    diagnostic_compact = compact_source(diagnostic_builder)
    main_compact = compact_source(product_main)
    check(
        "uvec4source_r1=uvec4(0u);"
        "if(PRODUCT_B62_DIAGNOSTICS_ENABLED){"
        "source_r1=q3_probe_subcell_diagnostics.records[idx].r1;}"
        in diagnostic_compact,
        "compiler diagnostic input must not read b62 in the shipping specialization",
    )
    check(
        (
            "booldiagnostic_index_valid=PRODUCT_B62_DIAGNOSTICS_ENABLED"
            "&&final_index<final_record_capacity;"
        )
        in main_compact
        and main_compact.count(
            "booldiagnostic_index_valid=PRODUCT_B62_DIAGNOSTICS_ENABLED"
        )
        == 1
        and main_compact.count(
            "q3_probe_subcell_diagnostics.records[final_index]"
        )
        >= 1,
        "Product main must derive one default-off diagnostic admission",
    )
    compile_end = product_main.find("LeafCellProduct staged_leaf_winners[4];")
    compile_region = (
        product_main[:compile_end]
        if compile_end >= 0
        else product_main
    )
    publication_region = product_main[compile_end:] if compile_end >= 0 else ""
    compile_b62_guarded = glsl_token_guarded_by_if(
        compile_region,
        "q3_probe_subcell_diagnostics.records[final_index]",
        "diagnostic_index_valid",
    )
    publication_compact = compact_source(publication_region)
    check(
        compile_b62_guarded
        and (
            "if(staged_final_record_ready[slot]){"
            "if(staged_diagnostic_index_valid[slot]"
            "&&staged_secondary_effect_bits[slot]!=0u){"
            "q3_probe_subcell_diagnostics.records[final_index].r0.w"
            "|=staged_secondary_effect_bits[slot];"
        ) in publication_compact,
        "compile-loop b62 writes must use diagnostic_index_valid, while the "
        "post-store contribution write must use staged diagnostic and ready gates",
    )
    check(
        "if(input_value.valid){" in main_compact
        and "if(input_value.valid&&diagnostic_index_valid)" not in main_compact
        and "PRODUCT_SPINE_STAGE" not in main_compact
        and main_compact.count(
            "finalized_probe_subcell_products.products[final_index]="
            "staged_final_records[slot];"
        )
        == 1,
        "diagnostic capacity must never gate the protected compiler or sole b55 store",
    )


# =============================================================================
# SECTION 11D - Ordinary play must not schedule an implicit viewport readback.
# =============================================================================

def section_default_viewport_readback_deleted() -> None:
    """Keep full-frame GPU readback behind one explicit capture request.

    Main once scheduled a viewport download after 120 ordinary frames. The
    manual and dogfight launchers must also remove an inherited capture-and-exit
    switch before starting children, then pass that scrubbed environment to
    every child. This section imports the focused owner so the global battery
    cannot drift into a weaker duplicate interpretation of those laws.
    """

    violations = default_viewport_readback_contract_violations(
        MAIN_PATH.read_text(encoding="utf-8", errors="replace"),
        AIRPLANE_PROOF_PATH.read_text(encoding="utf-8", errors="replace"),
        MANUAL_PLAY_PATH.read_text(encoding="utf-8", errors="replace"),
    )
    check(
        not violations,
        "ordinary play must retain only explicit viewport capture ownership: "
        + "; ".join(violations),
    )


# =============================================================================
# SECTION 12 - Named behavior and test tooling must remain reachable.
# =============================================================================

def section_lighthouse_reachability() -> None:
    """Require the rotating beam renderer on the activated Product call graph."""
    product = _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8", errors="replace"))
    host = strip_gd_comments(ACE_PATH.read_text(encoding="utf-8", errors="replace"))
    reached = glsl_reachable_functions(product)
    check(
        "vsb_product_collect_local_light_slot_facts" in reached
        and "lighthouse_beam_screen_stroke_glyph" in reached,
        "single-pass lighthouse renderer must be reachable from Product main",
    )
    check(
        "apply_effect_cell_product_to_paint" in reached,
        "lighthouse effect apply edge must be reachable from Product main",
    )
    check(
        re.search(
            r"const\s+LIGHTHOUSE_BEAM_ROT_RAD_PER_S\s*:?=\s*"
            r"0\.5235987756",
            host,
        )
        is not None,
        "host lighthouse angular speed must remain positive and nonzero",
    )
    azimuth = extract_gd_function(host, "get_lighthouse_beam_azimuth")
    check(
        azimuth is not None
        and "time_seconds * LIGHTHOUSE_BEAM_ROT_RAD_PER_S" in azimuth,
        "host lighthouse azimuth must consume frame time through the rotation speed",
    )


def section_python_tool_reachability() -> None:
    """Reject statements stranded after a top-level unconditional return.

    This is intentionally narrow. Conditional returns are normal. A Return node
    directly in a test function's body followed by more statements means those
    later assertions can never execute, which is how hundreds of retired-probe
    lines remained falsely documented as live coverage.
    """
    source = MOVING_ANALYZER_PATH.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(source, filename=str(MOVING_ANALYZER_PATH))
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        dead_returns = [
            statement.lineno
            for statement in node.body[:-1]
            if isinstance(statement, ast.Return)
        ]
        check(
            not dead_returns,
            f"{MOVING_ANALYZER_PATH.name}:{node.name} leaves executable statements "
            f"after top-level return lines {dead_returns}",
        )


def section_retired_jitter_cli_ownership() -> None:
    """Reject live requests to the deleted in-game jitter JSONL producer.

    The active architecture records temporal evidence outside the game. The
    headed runner can still perform visual capture when analysis is explicitly
    skipped, and the airplane runner can still run its non-jitter proof. The
    retired exact-key front door is guarded by physical absence in section 1.
    Each surviving stale request must therefore fail before process launch
    with the same machine-readable reason.
    """

    runner_sources = {
        HEADED_MOVEMENT_PATH: HEADED_MOVEMENT_PATH.read_text(
            encoding="utf-8", errors="replace"
        ),
        AIRPLANE_PROOF_PATH: AIRPLANE_PROOF_PATH.read_text(
            encoding="utf-8", errors="replace"
        ),
    }
    for path, source in runner_sources.items():
        command_strings = python_command_strings(source)
        stale_requests = sorted(
            {
                value
                for value in command_strings
                if any(
                    value.startswith(prefix)
                    for prefix in RETIRED_JITTER_GODOT_PREFIXES
                )
            }
        )
        check(
            not stale_requests,
            f"{path.name} still sends retired in-game jitter options: "
            f"{stale_requests}",
        )
        check(
            re.search(
                r'^EXTERNAL_RECORDER_JITTER_REASON\s*=\s*"'
                + re.escape(EXTERNAL_RECORDER_JITTER_REASON)
                + r'"$',
                source,
                re.MULTILINE,
            )
            is not None,
            f"{path.name} must expose the stable external-recorder ownership reason",
        )

    headed_main = extract_python_function(
        runner_sources[HEADED_MOVEMENT_PATH], "main"
    )
    check(headed_main is not None, "headed movement runner must define main")
    if headed_main is not None:
        guard = "if not args.skip_jitter_analysis:"
        check(
            guard in headed_main
            and "EXTERNAL_RECORDER_JITTER_REASON" in headed_main
            and "return 2" in headed_main,
            "headed movement must reject its default in-game jitter request "
            "with the external-recorder reason",
        )
        check(
            headed_main.find(guard) < headed_main.find("out_dir ="),
            "headed movement jitter rejection must run before output and process setup",
        )

    airplane_main = extract_python_function(
        runner_sources[AIRPLANE_PROOF_PATH], "main"
    )
    check(airplane_main is not None, "airplane proof runner must define main")
    if airplane_main is not None:
        guard = "if args.fl4359_jitter_capture:"
        check(
            guard in airplane_main
            and "EXTERNAL_RECORDER_JITTER_REASON" in airplane_main
            and "return 2" in airplane_main,
            "airplane proof must reject the legacy jitter option with the "
            "external-recorder reason",
        )
        check(
            airplane_main.find(guard) < airplane_main.find("godot_project ="),
            "airplane jitter rejection must run before output and process setup",
        )

def main() -> int:
    METAL_BACKEND_RESULTS.clear()
    section_deleted_files()
    section_dead_symbols()
    section_dead_perf_and_debug_owners()
    section_compositor_single_authority()
    section_final_product_status_abi()
    section_record_abi_mirror()
    section_vsb_binding_coverage()
    section_product_perf_instrumentation()
    section_falling_leaf_compaction()
    section_foliage_downwash_invariant()
    section_dynamic_shadow_active_count()
    section_wake_topology_materialization()
    section_wake_host_behavior()
    section_abab_adjudicator()
    section_metal_cardinality_abi()
    section_dispatch_ordering()
    section_host_binding_agreement()
    section_product_frame_receipt_abi()
    section_blood_mark_count_authority()
    section_local_light_glyph_count_authority()
    section_dispatch_extent_agreement()
    section_product_adapter_semantics()
    section_product_contribution_receipt()
    section_measurement_contamination()
    section_default_viewport_readback_deleted()
    section_lighthouse_reachability()
    section_python_tool_reachability()
    section_retired_jitter_cli_ownership()
    section_shaders_compile()
    section_shaders_compile_metal()
    _print_metal_backend_results()

    if FAILURES:
        print(f"FL-4431 activation contract: {len(FAILURES)} of {CHECKS} checks FAILED\n")
        for item in FAILURES:
            print(f"  - {item}")
        return 1
    metal_skip_count = sum(
        1 for _, status, _ in METAL_BACKEND_RESULTS if status == "SKIP"
    )
    if metal_skip_count:
        print(
            "FL-4431 activation contract: "
            f"all {CHECKS} enforced checks passed; "
            f"{metal_skip_count} Metal compiler checks SKIPPED."
        )
    else:
        print(f"FL-4431 activation contract: all {CHECKS} structural checks passed.")
    print("Structural only. No runtime, no GPU, no visual acceptance is implied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
