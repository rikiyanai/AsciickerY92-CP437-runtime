#!/usr/bin/env python3
# =============================================================================
# test_fl4431_activation_contract_mutants.py
# =============================================================================
# This script mutation-tests the FL-4431 activation contract. Mutation testing
# deliberately damages a disposable copy of the source, then asks the contract
# test whether it noticed. A green contract is trustworthy only when the
# property it claims to guard can be broken and the matching check turns red.
#
# The live repository sources are never edited. The harness copies every file
# inspected by the contract into one newly created temporary repository,
# redirects the imported contract module to that private copy, restores the
# copy before every mutation, and deletes the temporary repository on exit.
#
# Run with:
#   python3 scripts/tests/test_fl4431_activation_contract_mutants.py
#
# Exit code 0 means every required damaging mutation was caught and the
# comment-only control remained ignored. Exit code 1 means at least one real
# regression survived, which is a useful finding about a weak contract check.
# Exit code 2 means the pristine copied sources already fail a tested section,
# so the harness cannot attribute later failures to its mutations.
# =============================================================================

from __future__ import annotations

import shutil
import sys
import tempfile
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


# The harness lives under scripts/tests, so two parent steps reach the project
# root and one parent step reaches the scripts directory containing the subject
# contract module.
REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# Import the real contract implementation once. Its path variables are rebound
# below before any section runs, so all reads land in the temporary repository.
import test_fl4431_vsb_activation_contract as contract  # noqa: E402


# These relative paths name every source file exposed through the contract's
# module-level path constants. Both retained-scene host files are copied because
# section 6 compares the b82 producer, uploader, and shader as one agreement.
PRODUCT_REL = Path("godot_project/render/product/cell_owner_product.glsl")
COMPOSITOR_REL = Path("godot_project/render/ascii/raster/ascii_compositor.glsl")
VSB_REL = Path("godot_project/render/backends/ortho/visibility/visible_surface_builder.glsl")
ACE_REL = Path("godot_project/render/api/ASCIICompositorEffect.gd")
BUILDER_REL = Path("godot_project/render/core/contracts/visibility/VisibleSurfaceABI.gd")
SCENE_DB_REL = Path("godot_project/render/scene/SceneGeometryDB.gd")
WORLD_REL = Path("godot_project/scripts/World.gd")
MAIN_REL = Path("godot_project/scripts/Main.gd")
MOVING_ANALYZER_REL = Path("scripts/test_fl4359_moving_camera_analyzer.py")
HEADED_MOVEMENT_REL = Path("scripts/run_fl4359_headed_movement.py")
AIRPLANE_PROOF_REL = Path("scripts/run_fl4265_airplane_proof.py")
MANUAL_PLAY_REL = Path("scripts/launch_fl4359_manual_play.py")
COPIED_RELATIVE_PATHS = (
    PRODUCT_REL,
    COMPOSITOR_REL,
    VSB_REL,
    ACE_REL,
    BUILDER_REL,
    SCENE_DB_REL,
    WORLD_REL,
    MAIN_REL,
    MOVING_ANALYZER_REL,
    HEADED_MOVEMENT_REL,
    AIRPLANE_PROOF_REL,
    MANUAL_PLAY_REL,
)

# Section functions take no arguments and report through the imported module's
# FAILURES and CHECKS globals.
SectionFunction = Callable[[], None]


@dataclass(frozen=True)
class TextEdit:
    """Describe one deterministic edit applied only to a temporary copy.

    kind selects the mechanical operation. target is relative to the temporary
    repository. old and new hold the text moved, replaced, appended, swapped,
    or created. occurrence is one-based when a repeated token must be selected.
    """

    kind: str
    target: Path
    old: str = ""
    new: str = ""
    occurrence: int = 1


@dataclass(frozen=True)
class Mutation:
    """Describe one regression and the contract section that should detect it.

    name is printed in the result table. section_name identifies the contract
    family. section is the imported check function. edits are applied in order.
    must_be_caught is false only for the harmless comment-only control.
    """

    name: str
    section_name: str
    section: SectionFunction
    edits: tuple[TextEdit, ...]
    must_be_caught: bool = True


class Sandbox:
    """Own and restore the disposable repository used by every mutation.

    The constructor copies live inputs once. reset writes only the private copy,
    ensuring one mutation cannot contaminate the next. redirect_contract moves
    every subject path constant, including the precomputed SHIPPING_SHADERS
    tuple, onto this sandbox.
    """

    def __init__(self, root: Path) -> None:
        """Copy all contract inputs into root and retain pristine byte snapshots.

        root is the unique directory returned by tempfile.mkdtemp. The snapshots
        are read back from the copies, not from the live sources, so concurrent
        source-lane edits cannot change this harness midway through its run.
        """

        self.root = root
        self.pristine: dict[Path, bytes] = {}
        for relative_path in COPIED_RELATIVE_PATHS:
            source_path = REPO / relative_path
            copied_path = self.path(relative_path)
            copied_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, copied_path)
            self.pristine[relative_path] = copied_path.read_bytes()
        self.redirect_contract()

    def path(self, relative_path: Path) -> Path:
        """Return one path inside the private repository.

        relative_path must remain relative so a mutation cannot escape the
        sandbox and touch a live source file.
        """

        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError(f"unsafe sandbox-relative path: {relative_path}")
        return self.root / relative_path

    def redirect_contract(self) -> None:
        """Point every module-level contract path at the private repository.

        SHIPPING_SHADERS is also rebound because the subject computes that tuple
        at import time. Rebinding it keeps future compile-section use isolated,
        even though this harness exercises only the requested structural parts.
        """

        contract.REPO = self.root
        contract.SHADERS = self.root / "godot_project"
        contract.RENDER = self.root / "godot_project"
        contract.PRODUCT_PATH = self.path(PRODUCT_REL)
        contract.COMPOSITOR_PATH = self.path(COMPOSITOR_REL)
        contract.VSB_PATH = self.path(VSB_REL)
        contract.ACE_PATH = self.path(ACE_REL)
        contract.BUILDER_PATH = self.path(BUILDER_REL)
        contract.SCENE_DB_PATH = self.path(SCENE_DB_REL)
        contract.WORLD_PATH = self.path(WORLD_REL)
        contract.MAIN_PATH = self.path(MAIN_REL)
        contract.MOVING_ANALYZER_PATH = self.path(MOVING_ANALYZER_REL)
        contract.HEADED_MOVEMENT_PATH = self.path(HEADED_MOVEMENT_REL)
        contract.AIRPLANE_PROOF_PATH = self.path(AIRPLANE_PROOF_REL)
        contract.MANUAL_PLAY_PATH = self.path(MANUAL_PLAY_REL)
        contract.SHIPPING_SHADERS = (
            contract.PRODUCT_PATH,
            contract.COMPOSITOR_PATH,
            contract.VSB_PATH,
        )

    def reset(self) -> None:
        """Restore pristine copied files and remove mutation-created old files.

        The deleted-file section covers paths that are intentionally absent.
        A prior S1 mutation may have recreated one, so every reset removes such
        files from the sandbox before the next independent experiment.
        """

        for relative_path, contents in self.pristine.items():
            copied_path = self.path(relative_path)
            copied_path.parent.mkdir(parents=True, exist_ok=True)
            copied_path.write_bytes(contents)
        for deleted_name in contract.DELETED_FILES:
            deleted_path = self.path(Path(deleted_name))
            if deleted_path.exists() or deleted_path.is_symlink():
                deleted_path.unlink()
        self.redirect_contract()

    def apply(self, edit: TextEdit) -> None:
        """Apply one validated edit to a copied file.

        Each operation proves its anchor exists exactly where expected. A stale
        mutation definition therefore fails loudly instead of producing a
        vacuous green result against an unchanged copy.
        """

        target = self.path(edit.target)
        if edit.kind == "create":
            if target.exists():
                raise AssertionError(f"create target already exists: {edit.target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(edit.new, encoding="utf-8")
            return

        source = target.read_text(encoding="utf-8")
        if edit.kind == "append":
            target.write_text(source + edit.new, encoding="utf-8")
            return

        if edit.kind == "replace":
            matches = source.count(edit.old)
            if matches < edit.occurrence:
                raise AssertionError(
                    f"replace anchor {edit.occurrence} missing in {edit.target}: "
                    f"found {matches}"
                )
            start = -1
            search_from = 0
            for _ in range(edit.occurrence):
                start = source.find(edit.old, search_from)
                search_from = start + len(edit.old)
            mutated = source[:start] + edit.new + source[start + len(edit.old) :]
            target.write_text(mutated, encoding="utf-8")
            return

        if edit.kind == "move_after":
            if source.count(edit.old) != 1 or source.count(edit.new) != 1:
                raise AssertionError(
                    f"move anchors must each be unique in {edit.target}"
                )
            without_block = source.replace(edit.old, "", 1)
            moved = without_block.replace(edit.new, edit.new + edit.old, 1)
            target.write_text(moved, encoding="utf-8")
            return

        if edit.kind == "swap":
            first_start = source.find(edit.old)
            second_start = source.find(edit.new)
            if (
                first_start < 0
                or second_start < 0
                or first_start >= second_start
                or source.count(edit.old) != 1
                or source.count(edit.new) != 1
            ):
                raise AssertionError(f"swap anchors are invalid in {edit.target}")
            between = source[first_start + len(edit.old) : second_start]
            swapped = (
                source[:first_start]
                + edit.new
                + between
                + edit.old
                + source[second_start + len(edit.new) :]
            )
            target.write_text(swapped, encoding="utf-8")
            return

        raise ValueError(f"unknown mutation edit kind: {edit.kind}")


def reset_contract_result() -> None:
    """Clear the imported contract's result collectors before one section run."""

    contract.FAILURES.clear()
    contract.CHECKS = 0
    # Metal verdict rows belong to one section invocation. Clearing them keeps
    # a pristine SKIP from being mistaken for the later mutated shader's result.
    contract.METAL_BACKEND_RESULTS.clear()


def run_section(section: SectionFunction) -> tuple[int, tuple[str, ...]]:
    """Run one contract section and return its check count plus failure labels."""

    reset_contract_result()
    section()
    return contract.CHECKS, tuple(contract.FAILURES)


def build_mutations() -> tuple[Mutation, ...]:
    """Return every required mutation plus focused extra regressions.

    Each nearby comment explains the real defect being simulated. The edits are
    data rather than ad-hoc functions so they remain easy to audit and always
    flow through Sandbox.apply's anchor validation.
    """

    # The host lane may legitimately re-indent the guarded callback while this
    # test file is under review. Derive the exact copied blocks by syntax shape
    # instead of pinning two tabs. This keeps mutations about ordering and
    # reachability, not formatting, and still fails loudly unless each anchor
    # is unique in the real callback snapshot.
    ace_source = contract.ACE_PATH.read_text(encoding="utf-8", errors="replace")
    render_callback = contract.extract_gd_function(ace_source, "_render_callback")
    if render_callback is None:
        raise AssertionError("_render_callback missing while building mutations")
    product_source = contract.PRODUCT_PATH.read_text(
        encoding="utf-8",
        errors="replace",
    )
    foliage_downwash_mutants = (
        contract.foliage_downwash_invariant_destructive_mutants(
            product_source
        )
    )
    foliage_downwash_mutation_entries = tuple(
        Mutation(
            f"foliage downwash {name.replace('_', ' ')}",
            "S9 foliage downwash",
            contract.section_foliage_downwash_invariant,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=product_source,
                    new=mutated_source,
                ),
            ),
        )
        for name, mutated_source in foliage_downwash_mutants.items()
    )
    falling_leaf_quad_mutants = (
        contract.falling_leaf_quad_replay_destructive_mutants(
            product_source
        )
    )
    falling_leaf_quad_mutation_entries = tuple(
        Mutation(
            f"leaf fused replay {name.replace('_', ' ')}",
            "S9 leaf compact",
            contract.section_falling_leaf_compaction,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=product_source,
                    new=mutated_source,
                ),
            ),
        )
        for name, mutated_source in falling_leaf_quad_mutants.items()
    )
    # The focused wake contract owns Product-consumer and host-publication
    # damage shapes. Reusing both full-source mutation sets here makes the
    # activation battery prove the same ownership boundary as the focused
    # runner: Product cannot rebuild topology, and the host cannot publish a
    # stale, ambiguous, inexact, or cross-epoch relationship.
    wake_topology_mutants = contract.wake_topology_destructive_mutants(
        product_source
    )
    wake_topology_product_mutation_entries = tuple(
        Mutation(
            f"wake Product topology {name.replace('_', ' ')}",
            "S9 wake topology",
            contract.section_wake_topology_materialization,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=product_source,
                    new=mutated_source,
                ),
            ),
        )
        for name, mutated_source in wake_topology_mutants.items()
    )
    wake_host_mutants = contract.wake_topology_host_destructive_mutants(
        ace_source
    )
    wake_topology_host_mutation_entries = tuple(
        Mutation(
            f"wake host topology {name.replace('_', ' ')}",
            "S9 wake topology",
            contract.section_wake_topology_materialization,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old=ace_source,
                    new=mutated_source,
                ),
            ),
        )
        for name, mutated_source in wake_host_mutants.items()
    )
    # Binding 81's fourth aabb_min lane is an exact count of kind-1 rows.
    # These are the focused contract's executable damages, reused verbatim so
    # the activation battery proves the same zero-load, malformed-count,
    # sparse-row, mode-isolation, and early-exhaustion laws.
    dynamic_shadow_count_mutants = (
        contract.dynamic_shadow_active_count_destructive_mutants(product_source)
    )
    dynamic_shadow_count_mutation_entries = tuple(
        Mutation(
            f"dynamic shadow count {name.replace('_', ' ')}",
            "S9 b81 dynamic shadow count",
            contract.section_dynamic_shadow_active_count,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=product_source,
                    new=mutated_source,
                ),
            ),
        )
        for name, mutated_source in dynamic_shadow_count_mutants.items()
    )
    # The focused deletion contract owns five realistic ways an ordinary
    # session can regain an implicit viewport readback. Recreate those exact
    # damages in the disposable repository so the global activation battery
    # proves the same boundary: Main cannot schedule a hidden capture, both
    # launchers must scrub inherited capture-and-exit, the manual Godot child
    # must consume that scrubbed environment, and the retired moving-cell
    # diagnostic caller must stay gone.
    main_source = contract.MAIN_PATH.read_text(
        encoding="utf-8",
        errors="replace",
    )
    dogfight_source = contract.AIRPLANE_PROOF_PATH.read_text(
        encoding="utf-8",
        errors="replace",
    )
    manual_source = contract.MANUAL_PLAY_PATH.read_text(
        encoding="utf-8",
        errors="replace",
    )
    explicit_capture_line = (
        '\tvar capture_exit_requested := args.has("--capture-viewport-exit") '
        'or OS.get_environment("ASCIICKER_CAPTURE_VIEWPORT_EXIT") == "1"\n'
    )
    moving_cell_anchor = (
        '\t\t\tvar mode_name := "baseline" if cm == 0 else "heighthue"\n'
    )
    default_viewport_readback_mutation_entries = (
        Mutation(
            "default auto viewport capture restored",
            "S11D default viewport readback",
            contract.section_default_viewport_readback_deleted,
            (
                TextEdit(
                    "replace",
                    MAIN_REL,
                    old=explicit_capture_line,
                    new=(
                        explicit_capture_line
                        + "\tif not capture_exit_requested:\n"
                        + "\t\t_capture_viewport_png_after_frames("
                        '120, "/tmp/godot_screen.png")\n'
                    ),
                ),
            ),
        ),
        Mutation(
            "dogfight inherits viewport capture exit",
            "S11D default viewport readback",
            contract.section_default_viewport_readback_deleted,
            (
                TextEdit(
                    "replace",
                    AIRPLANE_PROOF_REL,
                    old='        "ASCIICKER_CAPTURE_VIEWPORT_EXIT",\n',
                    new="",
                ),
            ),
        ),
        Mutation(
            "manual inherits viewport capture exit",
            "S11D default viewport readback",
            contract.section_default_viewport_readback_deleted,
            (
                TextEdit(
                    "replace",
                    MANUAL_PLAY_REL,
                    old=(
                        "    child_environment.pop("
                        "VIEWPORT_CAPTURE_EXIT_ENV, None)\n"
                    ),
                    new="",
                ),
            ),
        ),
        Mutation(
            "dead moving cell caller restored",
            "S11D default viewport readback",
            contract.section_default_viewport_readback_deleted,
            (
                TextEdit(
                    "replace",
                    MAIN_REL,
                    old=moving_cell_anchor,
                    new=(
                        '\t\t\tif OS.get_cmdline_user_args().has('
                        '"--fl4359-moving-cell-dump") \\\n'
                        "\t\t\t\t\tand eff != null and "
                        'eff.has_method("fl4359_arm_moving_cell_dump"):\n'
                        "\t\t\t\teff.call("
                        '"fl4359_arm_moving_cell_dump", ab_frames, 0)\n'
                        + moving_cell_anchor
                    ),
                ),
            ),
        ),
        Mutation(
            "manual Godot child ignores scrubbed environment",
            "S11D default viewport readback",
            contract.section_default_viewport_readback_deleted,
            (
                TextEdit(
                    "replace",
                    MANUAL_PLAY_REL,
                    old=(
                        "                cwd=str(ROOT),\n"
                        "                env=env,\n"
                        "                stdout=glog,\n"
                    ),
                    new=(
                        "                cwd=str(ROOT),\n"
                        "                stdout=glog,\n"
                    ),
                ),
            ),
        ),
    )
    retired_probe_tool_paths = (
        "scripts/fl4359_mesh_ghost_blob_meter.py",
        "scripts/fl4359_wrong_cell_attribution.py",
        "scripts/fl4359_mesh_duel_gate.py",
        "scripts/fl4359_mesh_funnel_gate.py",
        "scripts/fl4359_truth_map_gate.py",
        "scripts/run_fl4359_ghost_pulse_gate.py",
        "scripts/fl4359_symptom_battery.py",
        "scripts/test_fl4359_wrong_cell_attribution.py",
    )
    # Each retired path gets its own executable mutation. A single representative
    # recreation would prove only that one filename is guarded; eight independent
    # cases prove the entire old probe-dump dependency closure fails red.
    retired_probe_tool_recreation_entries = tuple(
        Mutation(
            f"restore deleted {Path(relative_path).name}",
            "S1 deleted files",
            contract.section_deleted_files,
            (
                TextEdit(
                    "create",
                    Path(relative_path),
                    new=(
                        "#!/usr/bin/env python3\n"
                        "raise SystemExit('retired probe-dump tool')\n"
                    ),
                ),
            ),
        )
        for relative_path in retired_probe_tool_paths
    )

    def call_blocks(function_source: str, call_token: str) -> list[str]:
        """Return complete, indentation-delimited call blocks containing token."""
        lines = function_source.splitlines(keepends=True)
        blocks: list[str] = []
        for start_index, line in enumerate(lines):
            if call_token not in line:
                continue
            indentation = len(line) - len(line.lstrip(" \t"))
            block_lines = [line]
            for following in lines[start_index + 1 :]:
                block_lines.append(following)
                following_indent = len(following) - len(following.lstrip(" \t"))
                if following.strip() == ")" and following_indent == indentation:
                    blocks.append("".join(block_lines))
                    break
        return blocks

    shared_list_lines = [
        line
        for line in render_callback.splitlines(keepends=True)
        if "var product_compute_list: int = rd.compute_list_begin()" in line
    ]
    if len(shared_list_lines) != 1:
        raise AssertionError(
            f"expected one product compute-list declaration, found {len(shared_list_lines)}"
        )
    shared_barrier_lines = [
        line
        for line in render_callback.splitlines(keepends=True)
        if "rd.compute_list_add_barrier(product_compute_list)" in line
    ]
    if len(shared_barrier_lines) != 3:
        raise AssertionError(
            f"expected three shared-list barriers, found {len(shared_barrier_lines)}"
        )
    first_barrier_line = shared_barrier_lines[0]
    first_barrier_indent = first_barrier_line[
        : len(first_barrier_line) - len(first_barrier_line.lstrip(" \t"))
    ]

    vsb_dispatch_candidates = [
        block
        for block in call_blocks(render_callback, "rd.compute_list_dispatch(")
        if "vsb_dispatch_groups.x" in block
    ]
    if len(vsb_dispatch_candidates) != 1:
        raise AssertionError(
            f"expected one VSB dispatch block, found {len(vsb_dispatch_candidates)}"
        )
    vsb_dispatch_block = vsb_dispatch_candidates[0]

    product_dispatch_candidates = call_blocks(
        render_callback, "_dispatch_cell_owner_product("
    )
    if len(product_dispatch_candidates) != 1:
        raise AssertionError(
            "expected one Product dispatch block, found "
            f"{len(product_dispatch_candidates)}"
        )
    product_dispatch_block = product_dispatch_candidates[0]
    product_dispatch_indent = product_dispatch_block[
        : len(product_dispatch_block)
        - len(product_dispatch_block.lstrip(" \t"))
    ]
    indented_product_dispatch_block = "".join(
        "\t" + line if line.strip() else line
        for line in product_dispatch_block.splitlines(keepends=True)
    )

    return (
        *foliage_downwash_mutation_entries,
        *falling_leaf_quad_mutation_entries,
        *wake_topology_product_mutation_entries,
        *wake_topology_host_mutation_entries,
        *dynamic_shadow_count_mutation_entries,
        *default_viewport_readback_mutation_entries,
        *retired_probe_tool_recreation_entries,
        # The stale animation clock was executable in the ordinary callback
        # even while default false. Recreating the runtime symbol must fail the
        # dead-debug-owner section independently from every deleted tool path.
        Mutation(
            "restore retired FL-4359 animation freeze owner",
            "S2 dead performance and debug owners",
            contract.section_dead_perf_and_debug_owners,
            (
                TextEdit(
                    "append",
                    ACE_REL,
                    new=(
                        "\nvar fl4359_freeze_animation: bool = false\n"
                        "func _mutant_freeze_animation_clock() -> void:\n"
                        "\tif fl4359_freeze_animation:\n"
                        "\t\tpass\n"
                    ),
                ),
            ),
        ),
        # These five measured regressions are deleted owners. Each mutation
        # recreates executable shader work plus its host activation surface,
        # proving that default-false preservation cannot satisfy the contract.
        Mutation(
            "resurrect Product leaf live-mask owner",
            "S9 Product perf",
            contract.section_product_perf_instrumentation,
            (
                TextEdit(
                    "append",
                    PRODUCT_REL,
                    new=(
                        "\nlayout(constant_id = 2) const bool "
                        "PRODUCT_LEAF_LIVE_MASK_ENABLED = false;\n"
                        "const uint LEAF_PARTICLE_MASK_WORDS = "
                        "LEAF_PARTICLE_LANE_CAPACITY / 32u;\n"
                        "const uint LEAF_PARTICLE_MASK_ROW_BASE = "
                        "LEAF_PARTICLE_LANE_CAPACITY;\n"
                        "void mutant_restore_product_leaf_live_mask(uint lane) {\n"
                        "\tif (PRODUCT_LEAF_LIVE_MASK_ENABLED) {\n"
                        "\t\tuint mask_word = lane / 32u;\n"
                        "\t\tatomicOr(leaf_particle_bank.rows["
                        "LEAF_PARTICLE_MASK_ROW_BASE + mask_word].x, "
                        "1u << (lane % 32u));\n"
                        "\t}\n"
                        "}\n"
                    ),
                ),
                TextEdit(
                    "append",
                    ACE_REL,
                    new=(
                        "\nvar _fl4431_perf_enable_product_leaf_live_mask := "
                        "OS.get_environment("
                        '"FL4431_PERF_ENABLE_PRODUCT_LEAF_LIVE_MASK") == "1"\n'
                        "const LEAF_PARTICLE_LIVE_MASK_ROWS := "
                        "LEAF_PARTICLE_LANES / 32\n"
                        "const LEAF_PARTICLE_LIVE_MASK_BYTES := "
                        "LEAF_PARTICLE_LIVE_MASK_ROWS * 16\n"
                        "const MUTANT_FL4431_ABAB_PRODUCT_CONSTANT_IDS := {\n"
                        '\t"enable_product_leaf_live_mask": 2,\n'
                        "}\n"
                        "var _leaf_particle_live_mask_zero_bytes := "
                        "PackedByteArray()\n"
                        "func _mutant_restore_product_leaf_live_mask() -> void:\n"
                        "\tvar product_leaf_live_mask_specialization := "
                        "RDPipelineSpecializationConstant.new()\n"
                        "\tproduct_leaf_live_mask_specialization.constant_id = 2\n"
                        "\trd.buffer_update(\n"
                        "\t\t_leaf_particle_bank_rd,\n"
                        "\t\tLEAF_PARTICLE_RECORD_BYTES,\n"
                        "\t\tLEAF_PARTICLE_LIVE_MASK_BYTES,\n"
                        "\t\t_leaf_particle_live_mask_zero_bytes\n"
                        "\t)\n"
                    ),
                ),
            ),
        ),
        Mutation(
            "resurrect Product dynamic-shadow bound owner",
            "S9 Product perf",
            contract.section_product_perf_instrumentation,
            (
                TextEdit(
                    "append",
                    PRODUCT_REL,
                    new=(
                        "\nlayout(constant_id = 3) const bool "
                        "PRODUCT_DYNAMIC_SHADOW_BOUND_ENABLED = false;\n"
                        "uint mutant_restore_dynamic_shadow_bound(\n"
                        "\tuint first_face, uint face_count, int selection_mode\n"
                        ") {\n"
                        "\tuint shadow_faces_remaining = first_face < 512u "
                        "? 512u - first_face : 0u;\n"
                        "\treturn PRODUCT_DYNAMIC_SHADOW_BOUND_ENABLED "
                        "&& selection_mode == 0\n"
                        "\t\t? min(face_count, shadow_faces_remaining) "
                        ": face_count;\n"
                        "}\n"
                    ),
                ),
                TextEdit(
                    "append",
                    ACE_REL,
                    new=(
                        "\nvar _fl4431_perf_enable_product_dynamic_shadow_bound := "
                        "OS.get_environment("
                        '"FL4431_PERF_ENABLE_PRODUCT_DYNAMIC_SHADOW_BOUND") == "1"\n'
                        "const MUTANT_DYNAMIC_SHADOW_ABAB_IDS := {\n"
                        '\t"enable_product_dynamic_shadow_bound": 3,\n'
                        "}\n"
                        "func _mutant_restore_product_dynamic_shadow_bound() -> void:\n"
                        "\tvar product_dynamic_shadow_bound_specialization := "
                        "RDPipelineSpecializationConstant.new()\n"
                        "\tproduct_dynamic_shadow_bound_specialization.constant_id = 3\n"
                    ),
                ),
            ),
        ),
        Mutation(
            "resurrect Product wake-lane bound owner",
            "S9 Product perf",
            contract.section_product_perf_instrumentation,
            (
                TextEdit(
                    "append",
                    PRODUCT_REL,
                    new=(
                        "\nlayout(constant_id = 4) const bool "
                        "PRODUCT_WAKE_LANE_BOUND_ENABLED = false;\n"
                        "ivec2 mutant_restore_product_wake_lane(int lane) {\n"
                        "\tint source_lane_start = "
                        "PRODUCT_WAKE_LANE_BOUND_ENABLED ? (lane / 8) * 8 : 0;\n"
                        "\tint source_lane_end = PRODUCT_WAKE_LANE_BOUND_ENABLED "
                        "? source_lane_start + 8 : WAKE_TRAIL_CAPACITY;\n"
                        "\treturn ivec2(source_lane_start, source_lane_end);\n"
                        "}\n"
                    ),
                ),
                TextEdit(
                    "append",
                    ACE_REL,
                    new=(
                        "\nvar _fl4431_perf_enable_product_wake_lane_bound := "
                        "OS.get_environment("
                        '"FL4431_PERF_ENABLE_PRODUCT_WAKE_LANE_BOUND") == "1"\n'
                        "const MUTANT_WAKE_LANE_ABAB_IDS := {\n"
                        '\t"enable_product_wake_lane_bound": 4,\n'
                        "}\n"
                        "func _mutant_restore_product_wake_lane_bound() -> void:\n"
                        "\tvar product_wake_lane_bound_specialization := "
                        "RDPipelineSpecializationConstant.new()\n"
                        "\tproduct_wake_lane_bound_specialization.constant_id = 4\n"
                    ),
                ),
            ),
        ),
        Mutation(
            "resurrect VSB ordered-static owner",
            "S6 VSB slots",
            contract.section_vsb_binding_coverage,
            (
                TextEdit(
                    "append",
                    VSB_REL,
                    new=(
                        "\nlayout(constant_id = 4) const bool "
                        "VSB_STATIC_TLAS_ORDERED_ENABLED = false;\n"
                        "void mutant_restore_vsb_ordered_static(\n"
                        "\tSceneRay ray, inout SceneHit best\n"
                        ") {\n"
                        "\tif (VSB_STATIC_TLAS_ORDERED_ENABLED) {\n"
                        "\t\tscene_query_static_tlas(ray, best);\n"
                        "\t}\n"
                        "}\n"
                    ),
                ),
                TextEdit(
                    "append",
                    ACE_REL,
                    new=(
                        "\nvar _fl4431_perf_enable_static_ordered := "
                        "OS.get_environment("
                        '"FL4431_PERF_ENABLE_STATIC_ORDERED") == "1"\n'
                        "const MUTANT_STATIC_ORDERED_ABAB_IDS := {\n"
                        '\t"enable_static_ordered": 4,\n'
                        "}\n"
                        "func _mutant_restore_vsb_ordered_static() -> void:\n"
                        "\tvar static_ordered_specialization := "
                        "RDPipelineSpecializationConstant.new()\n"
                        "\tstatic_ordered_specialization.constant_id = 4\n"
                    ),
                ),
            ),
        ),
        Mutation(
            "resurrect VSB b83-minimum owner",
            "S6 VSB slots",
            contract.section_vsb_binding_coverage,
            (
                TextEdit(
                    "append",
                    VSB_REL,
                    new=(
                        "\nlayout(constant_id = 5) const bool "
                        "VSB_B83_MIN_SKIP_ENABLED = false;\n"
                        "bool mutant_restore_vsb_b83_minimum(\n"
                        "\tfloat lower_guard_enter_y, float exit_y, "
                        "float super_min, float epsilon\n"
                        ") {\n"
                        "\tbool super_proven_below = VSB_B83_MIN_SKIP_ENABLED "
                        "&& max(lower_guard_enter_y, exit_y) "
                        "< super_min - epsilon;\n"
                        "\treturn super_proven_below;\n"
                        "}\n"
                    ),
                ),
                TextEdit(
                    "append",
                    ACE_REL,
                    new=(
                        "\nvar _fl4431_perf_enable_b83_min := "
                        "OS.get_environment("
                        '"FL4431_PERF_ENABLE_B83_MIN") == "1"\n'
                        "const MUTANT_B83_MIN_ABAB_IDS := {\n"
                        '\t"enable_b83_min": 5,\n'
                        "}\n"
                        "func _mutant_restore_vsb_b83_minimum() -> void:\n"
                        "\tvar b83_min_specialization := "
                        "RDPipelineSpecializationConstant.new()\n"
                        "\tb83_min_specialization.constant_id = 5\n"
                    ),
                ),
            ),
        ),
        # Restoring a synchronous bank dump in executable code would put the
        # removed diagnostic stall back on the render thread. The camera receipt
        # remains allowed, so this mutation names one retired marker precisely.
        Mutation(
            "Product retired VSB bank diagnostic restored",
            "S9A ABAB",
            contract.section_abab_adjudicator,
            (
                TextEdit(
                    "append",
                    ACE_REL,
                    new=(
                        "\nfunc _mutant_restore_bank_diag() -> void:\n"
                        '\tprint("FL4431_VSB_DIAG_SAMPLE")\n'
                    ),
                ),
            ),
        ),
        # S1: Recreating one retired shader simulates an old visibility owner
        # becoming compilable and dispatchable again.
        Mutation(
            "restore deleted ascii_probe_capture.glsl",
            "S1 deleted files",
            contract.section_deleted_files,
            (
                TextEdit(
                    "create",
                    Path("godot_project/shaders/ascii_probe_capture.glsl"),
                    new="#[compute]\nvoid main() {}\n",
                ),
            ),
        ),
        # S1: The clear shader could recreate the first writable probe image in
        # the deleted route. Recreating its exact path proves that the deletion
        # contract guards this quiet infrastructure file, not just main passes.
        Mutation(
            "restore deleted ascii_probe_clear.glsl",
            "S1 deleted files",
            contract.section_deleted_files,
            (
                TextEdit(
                    "create",
                    Path("godot_project/shaders/ascii_probe_clear.glsl"),
                    new="#[compute]\nvoid main() {}\n",
                ),
            ),
        ),
        # S1: The static-face cull shader was a compilable prepass feeding the
        # retired resolver. Restoring its exact path must fail even when no live
        # host call has yet been added, since dead owners stay dead by absence.
        Mutation(
            "restore deleted ascii_probe_static_face_cull.glsl",
            "S1 deleted files",
            contract.section_deleted_files,
            (
                TextEdit(
                    "create",
                    Path(
                        "godot_project/shaders/"
                        "ascii_probe_static_face_cull.glsl"
                    ),
                    new="#[compute]\nvoid main() {}\n",
                ),
            ),
        ),
        # S1: The resource descriptor encoded the retired probe bank's identity
        # and packing laws. Recreating it would give future host code a ready
        # blueprint for reviving the deleted route, so absence is guarded too.
        Mutation(
            "restore deleted ascii_probe_resources.gd",
            "S1 deleted files",
            contract.section_deleted_files,
            (
                TextEdit(
                    "create",
                    Path(
                        "godot_project/scripts/render/"
                        "ascii_probe_resources.gd"
                    ),
                    new="class_name AsciiProbeResources\nextends RefCounted\n",
                ),
            ),
        ),
        # S1: The exact-key wrapper depended entirely on the deleted in-game
        # jitter JSONL producer. Recreating its path must fail even when the
        # replacement file contains no launcher yet, because the old command
        # name itself presents a false current evidence surface.
        Mutation(
            "restore deleted run_fl4359_exact_key_capture.py",
            "S1 deleted files",
            contract.section_deleted_files,
            (
                TextEdit(
                    "create",
                    Path("scripts/run_fl4359_exact_key_capture.py"),
                    new=(
                        "#!/usr/bin/env python3\n"
                        "raise SystemExit('retired exact-key capture')\n"
                    ),
                ),
            ),
        ),
        # S1: FL-4431 now has one current-source package owner. Recreating the
        # removed historical comparison wrapper would restore a second launch
        # surface with the stale baseline/current semantics that were
        # explicitly discarded, even before another caller referenced it.
        Mutation(
            "restore deleted run_fl4431_content_package.py",
            "S1 deleted files",
            contract.section_deleted_files,
            (
                TextEdit(
                    "create",
                    Path("scripts/run_fl4431_content_package.py"),
                    new=(
                        "#!/usr/bin/env python3\n"
                        "raise SystemExit('retired comparison package')\n"
                    ),
                ),
            ),
        ),
        # S2: A live GDScript reference to a retired switch simulates the old
        # runtime owner being wired back into executable host code.
        Mutation(
            "reintroduce retired host symbol in executable code",
            "S2 dead symbols",
            contract.section_dead_symbols,
            (
                TextEdit(
                    "append",
                    ACE_REL,
                    new=(
                        "\nfunc _mutation_retired_owner() -> void:\n"
                        "\tvar probe_enabled: bool = true\n"
                        "\tif probe_enabled:\n"
                        "\t\tpass\n"
                    ),
                ),
            ),
        ),
        # W1: A blacklist of old names is not enough. This mutation adds a
        # second executable writer under entirely new names, then wires it into
        # the live callback after Product. The byte payload is deliberately
        # ordinary; the defect is the second authority over b44, not its name.
        Mutation(
            "W1 restore renamed final-product writer",
            "S2 payload-owner topology",
            contract.section_dead_symbols,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old=product_dispatch_block,
                    new=(
                        product_dispatch_block
                        + product_dispatch_indent
                        + "_renamed_visibility_publish(final_failure_bytes)\n"
                    ),
                ),
                TextEdit(
                    "append",
                    ACE_REL,
                    new=(
                        "\nfunc _renamed_visibility_publish(bytes: PackedByteArray) -> void:\n"
                        "\t# A renamed legacy route can still overwrite the one final payload.\n"
                        "\trd.buffer_update(_vsb_final_product_rd, 0, bytes.size(), bytes)\n"
                    ),
                ),
            ),
        ),
        # Deleted performance experiments must stay physically absent. Each
        # mutation recreates one executable owner on one side of its former
        # ABI, proving that a partial resurrection cannot hide behind a clean
        # counterpart.
        Mutation(
            "resurrect Product receiver-cache owner",
            "S2D dead performance owners",
            contract.section_dead_perf_and_debug_owners,
            (
                TextEdit(
                    "append",
                    PRODUCT_REL,
                    new=(
                        "\nconst bool PRODUCT_SHADOW_RECEIVER_CACHE_ENABLED = false;\n"
                        "bool product_shadow_receiver_cache_lookup() { return false; }\n"
                    ),
                ),
            ),
        ),
        Mutation(
            "resurrect host receiver-cache owner",
            "S2D dead performance owners",
            contract.section_dead_perf_and_debug_owners,
            (
                TextEdit(
                    "append",
                    ACE_REL,
                    new=(
                        "\nvar _fl4431_perf_enable_shadow_receiver_cache: bool = false\n"
                        "var product_shadow_receiver_cache_specialization := "
                        "RDPipelineSpecializationConstant.new()\n"
                    ),
                ),
            ),
        ),
        Mutation(
            "resurrect Product b85 shadow prepass",
            "S2D dead performance owners",
            contract.section_dead_perf_and_debug_owners,
            (
                TextEdit(
                    "append",
                    PRODUCT_REL,
                    new=(
                        "\nlayout(set = 0, binding = 85, std430) buffer "
                        "ProductShadowPrepass { vec2 rows[]; } "
                        "product_shadow_receiver_prepass;\n"
                        "void product_emit_shadow_receiver_prepass() {}\n"
                    ),
                ),
            ),
        ),
        Mutation(
            "resurrect host b85 shadow prepass",
            "S2D dead performance owners",
            contract.section_dead_perf_and_debug_owners,
            (
                TextEdit(
                    "append",
                    ACE_REL,
                    new=(
                        "\nvar _product_shadow_prepass_rd: RID\n"
                        "var product_shadow_prepass_specialization := "
                        "RDPipelineSpecializationConstant.new()\n"
                    ),
                ),
            ),
        ),
        Mutation(
            "resurrect Product workgroup hash cache",
            "S2D dead performance owners",
            contract.section_dead_perf_and_debug_owners,
            (
                TextEdit(
                    "append",
                    PRODUCT_REL,
                    new=(
                        "\nconst bool PRODUCT_SHADOW_WG_CACHE_ENABLED = false;\n"
                        "shared uint product_shadow_wg_hash_key[512];\n"
                    ),
                ),
            ),
        ),
        Mutation(
            "resurrect host workgroup hash cache",
            "S2D dead performance owners",
            contract.section_dead_perf_and_debug_owners,
            (
                TextEdit(
                    "append",
                    ACE_REL,
                    new=(
                        "\nvar _fl4431_perf_enable_shadow_wg_cache: bool = false\n"
                        "var product_shadow_wg_cache_specialization := "
                        "RDPipelineSpecializationConstant.new()\n"
                    ),
                ),
            ),
        ),
        Mutation(
            "resurrect Product b83 phase counters",
            "S2D dead performance owners",
            contract.section_dead_perf_and_debug_owners,
            (
                TextEdit(
                    "append",
                    PRODUCT_REL,
                    new=(
                        "\nlayout(set = 0, binding = 83, std430) buffer "
                        "ProductPhaseCounters { uint values[1]; } "
                        "product_phase_counters;\n"
                        "void product_phase_count(uint lane, uint amount) {}\n"
                    ),
                ),
            ),
        ),
        Mutation(
            "resurrect host Product phase counters",
            "S2D dead performance owners",
            contract.section_dead_perf_and_debug_owners,
            (
                TextEdit(
                    "append",
                    ACE_REL,
                    new=(
                        "\nvar _fl4431_product_phase_counters_enabled: bool = false\n"
                        "var _product_phase_counters_rd: RID\n"
                        "func _read_product_phase_counters() -> void:\n"
                        "\tpass\n"
                    ),
                ),
            ),
        ),
        Mutation(
            "resurrect VSB b84 phase counters",
            "S2D dead performance owners",
            contract.section_dead_perf_and_debug_owners,
            (
                TextEdit(
                    "append",
                    VSB_REL,
                    new=(
                        "\nlayout(set = 0, binding = 84, std430) buffer "
                        "VsbPhaseCounters { uint values[1]; } "
                        "vsb_phase_counters;\n"
                        "void vsb_phase_count(uint lane, uint amount) {}\n"
                    ),
                ),
            ),
        ),
        Mutation(
            "resurrect VSB b84 builder constant",
            "S2D dead performance owners",
            contract.section_dead_perf_and_debug_owners,
            (
                TextEdit(
                    "append",
                    BUILDER_REL,
                    new="\nconst BINDING_VSB_PHASE_COUNTERS := 84\n",
                ),
            ),
        ),
        Mutation(
            "resurrect host VSB phase counters",
            "S2D dead performance owners",
            contract.section_dead_perf_and_debug_owners,
            (
                TextEdit(
                    "append",
                    ACE_REL,
                    new=(
                        "\nvar _fl4431_vsb_phase_counters_enabled: bool = false\n"
                        "var _vsb_phase_counters_rd: RID\n"
                        "func _read_vsb_phase_counters() -> void:\n"
                        "\tpass\n"
                    ),
                ),
            ),
        ),
        Mutation(
            "resurrect World final-render pixel debug",
            "S2D dead debug owners",
            contract.section_dead_perf_and_debug_owners,
            (
                TextEdit(
                    "append",
                    WORLD_REL,
                    new=(
                        "\nvar _final_render_dump_pixel_debug := "
                        '"--final-render-dump-pixel-debug"\n'
                        "func _mutant_pixel_debug() -> void:\n"
                        "\twrite_latest_pixel_debug_binary()\n"
                        "\tvar pixel_debug_manifest := {}\n"
                    ),
                ),
            ),
        ),
        Mutation(
            "resurrect ACE final-render pixel debug",
            "S2D dead debug owners",
            contract.section_dead_perf_and_debug_owners,
            (
                TextEdit(
                    "append",
                    ACE_REL,
                    new=(
                        "\nfunc write_latest_pixel_debug_binary() -> void:\n"
                        "\tvar pixel_debug := PackedByteArray()\n"
                    ),
                ),
            ),
        ),
        Mutation(
            "resurrect ACE FL4231 light diagnostic",
            "S2D dead debug owners",
            contract.section_dead_perf_and_debug_owners,
            (
                TextEdit(
                    "append",
                    ACE_REL,
                    new=(
                        "\nfunc _fl4231_light_diag_tick() -> void:\n"
                        '\tif OS.get_environment("ASCIICKER_FL4231_LIGHT_DIAG") == "1":\n'
                        '\t\tprint("FL4231_LIGHT_DIAG")\n'
                    ),
                ),
            ),
        ),
        Mutation(
            "resurrect ACE FL4270 projection diagnostics",
            "S2D dead debug owners",
            contract.section_dead_perf_and_debug_owners,
            (
                TextEdit(
                    "append",
                    ACE_REL,
                    new=(
                        "\nvar _fl4270_projection_diag_printed := false\n"
                        "var _fl4270_persp_diag_printed := false\n"
                    ),
                ),
            ),
        ),
        # S2 control: Documentation may name a deleted owner. Comment stripping
        # must prevent that harmless explanation from becoming a false alarm.
        Mutation(
            "mention retired host symbol in comment only",
            "S2 dead symbols",
            contract.section_dead_symbols,
            (
                TextEdit(
                    "append",
                    ACE_REL,
                    new="\n# Historical note: probe_enabled retired with the old route.\n",
                ),
            ),
            must_be_caught=False,
        ),
        # S3: A second readonly payload buffer gives the final painter two
        # possible input owners again.
        Mutation(
            "add second compositor readonly product buffer",
            "S3 compositor",
            contract.section_compositor_single_authority,
            (
                TextEdit(
                    "append",
                    COMPOSITOR_REL,
                    new=(
                        "\nlayout(set = 0, binding = 45, std430) readonly buffer "
                        "MutantSecondProduct {\n"
                        "\tFinalSubcellProductRecord products[];\n"
                        "} mutant_second_product;\n"
                    ),
                ),
            ),
        ),
        # W2: The old count only recognised readonly storage buffers. A sampled
        # unsigned image can carry a competing finished product just as easily,
        # so this realistic mutant declares and reads one under a new binding.
        Mutation(
            "W2 add sampled-image payload authority",
            "S3 compositor",
            contract.section_compositor_single_authority,
            (
                TextEdit(
                    "replace",
                    COMPOSITOR_REL,
                    old="layout(set = 0, binding = 6) uniform sampler2D morphology_glyph_tex;\n",
                    new=(
                        "layout(set = 0, binding = 6) uniform sampler2D morphology_glyph_tex;\n"
                        "layout(set = 0, binding = 45) uniform usampler2D alternate_product;\n"
                    ),
                ),
                TextEdit(
                    "replace",
                    COMPOSITOR_REL,
                    old=(
                        "\tFinalSubcellProductRecord product = "
                        "finalized_subcell_products.products[cell_index * 4u + slot];"
                    ),
                    new=(
                        "\tFinalSubcellProductRecord product = "
                        "finalized_subcell_products.products[cell_index * 4u + slot];\n"
                        "\tproduct.r0 ^= texelFetch(alternate_product, pos, 0);"
                    ),
                ),
            ),
        ),
        # S3: Changing the four-record stride makes a pixel read a neighboring
        # cell's product while still producing a plausible image.
        Mutation(
            "change compositor product index law",
            "S3 compositor",
            contract.section_compositor_single_authority,
            (
                TextEdit(
                    "replace",
                    COMPOSITOR_REL,
                    old="cell_index * 4u + slot",
                    new="cell_index * 3u + slot",
                ),
            ),
        ),
        # W3: The consumer can remain correct while the producer writes a
        # three-record stride. This is the highest-value two-sided mutation
        # because the result still resembles a picture while every cell drifts.
        Mutation(
            "W3 change producer product index law",
            "S3 compositor",
            contract.section_compositor_single_authority,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old="uint final_index = cell_index * 4u + slot;",
                    new="uint final_index = cell_index * 3u + slot;",
                ),
            ),
        ),
        # W4: The live Product publication and compositor read must agree on
        # the subcell axes. This mutation damages the producer copy while the
        # consumer stays correct.
        Mutation(
            "W4 transpose producer subcell slot axes",
            "S3 compositor",
            contract.section_compositor_single_authority,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old="+ ivec2(int(slot & 1u), int(slot >> 1u));",
                    new="+ ivec2(int(slot >> 1u), int(slot & 1u));",
                ),
            ),
        ),
        # S3: Deriving the live projection selector from the matrix revives the
        # compositor as a camera/ray owner instead of consuming frozen b68 truth.
        Mutation(
            "derive live compositor projection from inverse matrix",
            "S3 compositor",
            contract.section_compositor_single_authority,
            (
                TextEdit(
                    "replace",
                    COMPOSITOR_REL,
                    old=(
                        "\treturn product_vsb_frame.view_identity.z "
                        "== VSB_PROJECTION_PERSPECTIVE;"
                    ),
                    new=(
                        "\tvec4 near_probe = params.inv_view_proj "
                        "* vec4(0.0, 0.0, 0.0, 1.0);\n"
                        "\tvec4 far_probe = params.inv_view_proj "
                        "* vec4(0.0, 0.0, 1.0, 1.0);\n"
                        "\treturn abs(near_probe.w - far_probe.w) > 1e-4;"
                    ),
                ),
            ),
        ),
        # S3: Letting perspective enter derivative AA changes its reviewed
        # nearest-sampling grammar while leaving the binary color branch intact.
        Mutation(
            "enable derivative AA in perspective paint",
            "S3.5 perspective paint",
            contract.section_compositor_single_authority,
            (
                TextEdit(
                    "replace",
                    COMPOSITOR_REL,
                    old="if (!perspective_paint && derivative_aa_enabled()) {",
                    new="if (derivative_aa_enabled()) {",
                ),
            ),
        ),
        # S3: A second output store simulates a later paint path overwriting the
        # single authoritative color.
        Mutation(
            "add second compositor imageStore",
            "S3 compositor",
            contract.section_compositor_single_authority,
            (
                TextEdit(
                    "append",
                    COMPOSITOR_REL,
                    new=(
                        "\nvoid mutant_second_paint(ivec2 pos) {\n"
                        "\timageStore(color_image, pos, vec4(0.0));\n"
                        "}\n"
                    ),
                ),
            ),
        ),
        # W6: Aliasing the image name through the preprocessor defeats a check
        # that counts only the literal text imageStore(color_image, ...).
        Mutation(
            "W6 add second output store through macro alias",
            "S3 compositor",
            contract.section_compositor_single_authority,
            (
                TextEdit(
                    "replace",
                    COMPOSITOR_REL,
                    old="#version 450\n",
                    new="#version 450\n#define MUTANT_TARGET color_image\n",
                ),
                TextEdit(
                    "replace",
                    COMPOSITOR_REL,
                    old="\timageStore(color_image, pos, vec4(color, 1.0));",
                    new=(
                        "\timageStore(color_image, pos, vec4(color, 1.0));\n"
                        "\timageStore(MUTANT_TARGET, pos, vec4(color, 1.0));"
                    ),
                ),
            ),
        ),
        # S3.5: Applying binary paint globally destroys the orthographic
        # continuous blend that fixed FL-4359 shimmer.
        Mutation(
            "apply binary fg/bg paint globally",
            "S3.5 projection paint",
            contract.section_compositor_single_authority,
            (
                TextEdit(
                    "replace",
                    COMPOSITOR_REL,
                    old=(
                        "\t} else {\n"
                        "\t\tcolor = mix(resolved.bg, resolved.fg, intensity);\n"
                        "\t}"
                    ),
                    new=(
                        "\t} else {\n"
                        "\t\tcolor = intensity > 0.5 "
                        "? resolved.fg : resolved.bg;\n"
                        "\t}"
                    ),
                ),
            ),
        ),
        # S3.5: Applying continuous paint globally repeats the cutover
        # regression by erasing Baseline A's perspective binary grammar.
        Mutation(
            "apply continuous glyph paint globally",
            "S3.5 projection paint",
            contract.section_compositor_single_authority,
            (
                TextEdit(
                    "replace",
                    COMPOSITOR_REL,
                    old="\t\tcolor = chose_fg ? resolved.fg : resolved.bg;",
                    new="\t\tcolor = mix(resolved.bg, resolved.fg, intensity);",
                ),
            ),
        ),
        # S3.5: A small threshold drift changes terrain pixel identity across
        # the entire perspective frame despite every payload byte staying equal.
        Mutation(
            "drift perspective mountain threshold",
            "S3.5 projection paint",
            contract.section_compositor_single_authority,
            (
                TextEdit(
                    "replace",
                    COMPOSITOR_REL,
                    old=(
                        "return mountain_present ? 0.28 : "
                        "(terrain_present ? 0.34 : 0.5);"
                    ),
                    new=(
                        "return mountain_present ? 0.29 : "
                        "(terrain_present ? 0.34 : 0.5);"
                    ),
                ),
            ),
        ),
        # S3.5: Product family metadata cannot become a downstream mountain
        # morphology owner in the compositor.
        Mutation(
            "reintroduce cliff family terrain centre pin",
            "S3.5 projection paint",
            contract.section_compositor_single_authority,
            (
                TextEdit(
                    "replace",
                    COMPOSITOR_REL,
                    old="if (foliage_center_pin) {",
                    new=(
                        "if (foliage_center_pin "
                        "|| material_family == TERRAIN_FAMILY_CLIFF_ROCK) {"
                    ),
                ),
            ),
        ),
        # S3.5: The explicit foliage visibility fact remains the only bounded
        # center-pin exception before continuous Product paint.
        Mutation(
            "delete foliage centre-pin branch",
            "S3.5 projection paint",
            contract.section_compositor_single_authority,
            (
                TextEdit(
                    "replace",
                    COMPOSITOR_REL,
                    old="if (foliage_center_pin) {",
                    new="if (false && foliage_center_pin) {",
                ),
            ),
        ),
        Mutation(
            "final status ABI version drifts in compositor",
            "S4 status ABI",
            contract.section_final_product_status_abi,
            (
                TextEdit(
                    "replace",
                    COMPOSITOR_REL,
                    old="const uint FINAL_SUBCELL_PRODUCT_ABI_VERSION = 2u;",
                    new="const uint FINAL_SUBCELL_PRODUCT_ABI_VERSION = 3u;",
                ),
            ),
        ),
        Mutation(
            "final status accepts an unknown semantic bit",
            "S4 status ABI",
            contract.section_final_product_status_abi,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old="(status & ~FINAL_PRODUCT_STATUS_KNOWN_MASK) == 0u",
                    new=(
                        "(status & ~(FINAL_PRODUCT_STATUS_KNOWN_MASK "
                        "| (1u << 2u))) == 0u"
                    ),
                ),
                TextEdit(
                    "replace",
                    COMPOSITOR_REL,
                    old="(status & ~FINAL_PRODUCT_STATUS_KNOWN_MASK) == 0u",
                    new=(
                        "(status & ~(FINAL_PRODUCT_STATUS_KNOWN_MASK "
                        "| (1u << 2u))) == 0u"
                    ),
                ),
            ),
        ),
        Mutation(
            "foliage bit is accepted without valid status",
            "S4 status ABI",
            contract.section_final_product_status_abi,
            (
                TextEdit(
                    "replace",
                    COMPOSITOR_REL,
                    old="return final_product_status_is_valid(status)",
                    new="return true",
                ),
            ),
        ),
        Mutation(
            "one Product reader restores whole-word validity",
            "S4 status ABI",
            contract.section_final_product_status_abi,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old="!final_product_status_is_valid(record.r0.x)",
                    new="record.r0.x == 0u",
                    occurrence=1,
                ),
            ),
        ),
        Mutation(
            "accepted base foliage loses final status publication",
            "S4 status ABI",
            contract.section_final_product_status_abi,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old="foliage_product_present = true;",
                    new="foliage_product_present = false;",
                ),
            ),
        ),
        Mutation(
            "actor takeover leaks underlying foliage status",
            "S4 status ABI",
            contract.section_final_product_status_abi,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=(
                        "\t\trecord.r0.x &= "
                        "~FINAL_PRODUCT_STATUS_FOLIAGE_PRESENT;\n"
                    ),
                    new="",
                ),
            ),
        ),
        Mutation(
            "falling leaf is admitted over actor paint",
            "S4 status ABI",
            contract.section_final_product_status_abi,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old="final_record.r0.y != OWNER_ACTOR_SPRITE",
                    new="final_record.r0.y != OWNER_NONE",
                ),
            ),
        ),
        Mutation(
            "admitted falling leaf omits foliage status",
            "S4 status ABI",
            contract.section_final_product_status_abi,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=(
                        "\t\tstaged_final_records[slot].r0.x |=\n"
                        "\t\t\tFINAL_PRODUCT_STATUS_FOLIAGE_PRESENT;\n"
                    ),
                    new="",
                ),
            ),
        ),
        Mutation(
            "compositor infers foliage from paint fields",
            "S4 status ABI",
            contract.section_final_product_status_abi,
            (
                TextEdit(
                    "replace",
                    COMPOSITOR_REL,
                    old="final_product_status_has_foliage(product_status)",
                    new="(product.fg.g > product.bg.g)",
                    occurrence=1,
                ),
            ),
        ),
        Mutation(
            "compositor restores binding-62 foliage inference",
            "S4 status ABI",
            contract.section_final_product_status_abi,
            (
                TextEdit(
                    "replace",
                    COMPOSITOR_REL,
                    old="struct FinalSubcellProductRecord {",
                    new=(
                        "const uint Q3_DIAG_MARKER_FOLIAGE = 4u;\n"
                        "layout(set = 0, binding = 62, std430) readonly buffer "
                        "MutantProductDiagnostics {\n"
                        "\tuvec4 rows[];\n"
                        "} mutant_product_diagnostics;\n\n"
                        "struct FinalSubcellProductRecord {"
                    ),
                ),
                TextEdit(
                    "replace",
                    COMPOSITOR_REL,
                    old="final_product_status_has_foliage(product_status)",
                    new=(
                        "(mutant_product_diagnostics.rows[cell_index].w "
                        "& Q3_DIAG_MARKER_FOLIAGE) != 0u"
                    ),
                    occurrence=1,
                ),
            ),
        ),
        Mutation(
            "orthographic foliage pin loses 12-percent bound",
            "S3.5 projection paint",
            contract.section_compositor_single_authority,
            (
                TextEdit(
                    "replace",
                    COMPOSITOR_REL,
                    old="float(cell_span_px.x) * 0.12",
                    new="float(cell_span_px.x) * 0.10",
                ),
                TextEdit(
                    "replace",
                    COMPOSITOR_REL,
                    old="float(cell_span_px.y) * 0.12",
                    new="float(cell_span_px.y) * 0.10",
                ),
            ),
        ),
        Mutation(
            "water-skip family leaks into foliage centre pin",
            "S3.5 projection paint",
            contract.section_compositor_single_authority,
            (
                TextEdit(
                    "replace",
                    COMPOSITOR_REL,
                    old="if (foliage_center_pin) {",
                    new=(
                        "if (foliage_center_pin "
                        "|| material_family == TERRAIN_FAMILY_WATER_SKIP) {"
                    ),
                ),
            ),
        ),
        Mutation(
            "dead flower threshold is revived",
            "S3.5 projection paint",
            contract.section_compositor_single_authority,
            (
                TextEdit(
                    "replace",
                    COMPOSITOR_REL,
                    old=(
                        "return mountain_present ? 0.28 : "
                        "(terrain_present ? 0.34 : 0.5);"
                    ),
                    new=(
                        "return material_family == 0u ? 0.22 : "
                        "(mountain_present ? 0.28 : "
                        "(terrain_present ? 0.34 : 0.5));"
                    ),
                ),
            ),
        ),
        Mutation(
            "dead foliage threshold is revived",
            "S3.5 projection paint",
            contract.section_compositor_single_authority,
            (
                TextEdit(
                    "replace",
                    COMPOSITOR_REL,
                    old=(
                        "return mountain_present ? 0.28 : "
                        "(terrain_present ? 0.34 : 0.5);"
                    ),
                    new=(
                        "return owner_kind == OWNER_STATIC_MESH ? 0.30 : "
                        "(mountain_present ? 0.28 : "
                        "(terrain_present ? 0.34 : 0.5));"
                    ),
                ),
            ),
        ),
        # W9: Product absence must stay exact. Painting a plausible colour when
        # validity is zero hides the upstream failure the cutover must expose.
        Mutation(
            "W9 substitute sky blue for absent product",
            "S3 compositor",
            contract.section_compositor_single_authority,
            (
                TextEdit(
                    "replace",
                    COMPOSITOR_REL,
                    old=(
                        "\t\tcolor = mix(resolved.bg, resolved.fg, intensity);\n"
                        "\t}"
                    ),
                    new=(
                        "\t\tcolor = mix(resolved.bg, resolved.fg, intensity);\n"
                        "\t}\n"
                        "\tif (product.r0.x == 0u) "
                        "color = vec3(0.18, 0.42, 0.70);"
                    ),
                ),
            ),
        ),
        # S4: Swapping the two unsigned rows keeps the same type sequence while
        # exchanging validity/color identity with provenance in raw memory.
        Mutation(
            "swap producer r0 and r1 fields",
            "S4 record ABI",
            contract.section_record_abi_mirror,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=(
                        "\tuvec4 r0; // ABI-v2 status, owner_kind, glyph, "
                        "material_family\n"
                        "\tuvec4 r1; // material_id, probe_texel_id, entity_id, "
                        "packed material cell\n"
                    ),
                    new=(
                        "\tuvec4 r1; // material_id, probe_texel_id, entity_id, "
                        "packed material cell\n"
                        "\tuvec4 r0; // ABI-v2 status, owner_kind, glyph, "
                        "material_family\n"
                    ),
                ),
            ),
        ),
        # S4: Changing one row from unsigned to signed changes the declared raw
        # ABI type on only the producer side.
        Mutation(
            "change producer record field type",
            "S4 record ABI",
            contract.section_record_abi_mirror,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=(
                        "\tuvec4 r0; // ABI-v2 status, owner_kind, glyph, "
                        "material_family"
                    ),
                    new=(
                        "\tivec4 r0; // ABI-v2 status, owner_kind, glyph, "
                        "material_family"
                    ),
                ),
            ),
        ),
        # S4: Adding one row only to the producer shifts its record stride past
        # the compositor's fixed five-row interpretation.
        Mutation(
            "add producer-only record field",
            "S4 record ABI",
            contract.section_record_abi_mirror,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old="\tvec4 anchor;\n};",
                    new="\tvec4 anchor;\n\tvec4 mutant_extra;\n};",
                ),
            ),
        ),
        # W10: Leaving the reviewed structs intact does not help when the live
        # b44 array and local variable switch to a larger replacement record.
        Mutation(
            "W10 route live b44 through a dead-mirror replacement struct",
            "S4 record ABI",
            contract.section_record_abi_mirror,
            (
                TextEdit(
                    "replace",
                    COMPOSITOR_REL,
                    old=(
                        "layout(set = 0, binding = 44, std430) readonly buffer "
                        "FinalizedSubcellProducts {\n"
                    ),
                    new=(
                        "struct MutantFinalRecord {\n"
                        "\tuvec4 r0;\n"
                        "\tuvec4 r1;\n"
                        "\tvec4 fg;\n"
                        "\tvec4 bg;\n"
                        "\tvec4 anchor;\n"
                        "\tvec4 extra;\n"
                        "};\n\n"
                        "layout(set = 0, binding = 44, std430) readonly buffer "
                        "FinalizedSubcellProducts {\n"
                    ),
                ),
                TextEdit(
                    "replace",
                    COMPOSITOR_REL,
                    old="\tFinalSubcellProductRecord products[];",
                    new="\tMutantFinalRecord products[];",
                ),
                TextEdit(
                    "replace",
                    COMPOSITOR_REL,
                    old="\tFinalSubcellProductRecord product = finalized_subcell_products.products",
                    new="\tMutantFinalRecord product = finalized_subcell_products.products",
                ),
            ),
        ),
        # S6: Removing one named binding constant leaves the builder unable to
        # address one shader slot.
        Mutation(
            "delete one VSB binding constant",
            "S6 VSB slots",
            contract.section_vsb_binding_coverage,
            (
                TextEdit(
                    "replace",
                    BUILDER_REL,
                    old="const BINDING_SCENE_TLAS_INSTANCE_IDS := 66\n",
                    new="",
                ),
            ),
        ),
        # S6: Giving two names the same slot silently leaves one real shader slot
        # unowned and makes another slot ambiguous.
        Mutation(
            "duplicate one VSB slot across constants",
            "S6 VSB slots",
            contract.section_vsb_binding_coverage,
            (
                TextEdit(
                    "replace",
                    BUILDER_REL,
                    old="const BINDING_SCENE_TLAS_INSTANCE_IDS := 66",
                    new="const BINDING_SCENE_TLAS_INSTANCE_IDS := 65",
                ),
            ),
        ),
        # S6: Moving one constant outside the declared 64-83 ABI simulates a
        # host-builder numbering drift.
        Mutation(
            "change one VSB binding constant value",
            "S6 VSB slots",
            contract.section_vsb_binding_coverage,
            (
                TextEdit(
                    "replace",
                    BUILDER_REL,
                    old="const BINDING_SCENE_INSTANCES := 67",
                    new="const BINDING_SCENE_INSTANCES := 99",
                ),
            ),
        ),
        # S7: Removing the first fence lets VSB race the two host-side absence
        # buffer fills.
        Mutation(
            "delete one shared-list barrier",
            "S7 dispatch order",
            contract.section_dispatch_ordering,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old=first_barrier_line,
                    new="",
                    occurrence=1,
                ),
            ),
        ),
        # S7: Reintroducing the old ordinary-frame b64 upload gives the host a
        # second live visibility writer beside VSB. The copy is inserted at the
        # former seam before Product preparation, outside the skip diagnostic.
        Mutation(
            "restore ordinary-frame b64 host upload",
            "S7 dispatch order",
            contract.section_dispatch_ordering,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old=(
                        "\t\t# Product preparation owns every remaining "
                        "buffer upload"
                    ),
                    new=(
                        "\t\trd.buffer_update(\n"
                        "\t\t\t_vsb_product_rd,\n"
                        "\t\t\t0,\n"
                        "\t\t\tint(vsb_publication[\"buffer_size_bytes\"]),\n"
                        "\t\t\tvsb_publication[\"record_bytes\"]\n"
                        "\t\t)\n"
                        "\t\t# Product preparation owns every remaining "
                        "buffer upload"
                    ),
                ),
            ),
        ),
        # S7: Reintroducing the old ordinary-frame b55 helper call revives the
        # host as a full Product-output writer even when Product will dispatch.
        Mutation(
            "restore ordinary-frame b55 host upload",
            "S7 dispatch order",
            contract.section_dispatch_ordering,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old=(
                        "\t\t# Product preparation owns every remaining "
                        "buffer upload"
                    ),
                    new=(
                        "\t\t_upload_vsb_final_failure_prefill(\n"
                        "\t\t\tint(vsb_publication[\"record_count\"])\n"
                        "\t\t)\n"
                        "\t\t# Product preparation owns every remaining "
                        "buffer upload"
                    ),
                ),
            ),
        ),
        # S7: A rejected view has no GPU writer, so deleting this recovery call
        # exposes stale Product rows from the previous accepted view.
        Mutation(
            "delete rejected-view b55 recovery upload",
            "S7 dispatch order",
            contract.section_dispatch_ordering,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old=(
                        "\t\t\t_upload_vsb_final_failure_prefill("
                        "rejected_record_count)\n"
                    ),
                    new="",
                ),
            ),
        ),
        # S7: Running Product before VSB makes Product consume stale b64 absence
        # rows from the previous initialization state.
        Mutation(
            "swap VSB and Product dispatch blocks",
            "S7 dispatch order",
            contract.section_dispatch_ordering,
            (
                TextEdit(
                    "swap",
                    ACE_REL,
                    old=vsb_dispatch_block,
                    new=product_dispatch_block,
                ),
            ),
        ),
        # S8: A literal extra Product binding reproduces the uniform-set creation
        # failure that originally motivated this section.
        Mutation(
            "bind undeclared Product slot from host",
            "S8 binding agreement",
            contract.section_host_binding_agreement,
            (
                # CORRECTED 2026-07-23 (root). The first version of this mutation
                # appended a NEW, NEVER-CALLED function that created a uniform at
                # slot 999 and stopped there. That does not reproduce the defect
                # it names: a uniform object that is never added to a uniform set
                # is simply unused code, and cannot make set creation fail. It
                # "survived" because there was nothing to catch -- the contract
                # was right and the mutation was wrong.
                #
                # The real defect, seen twice in FL-4431 (Product bindings 5/6/8
                # and compositor binding 42), is a uniform that IS a member of the
                # set handed to the shader. So the mutation now inserts the
                # uniform into the actual Product cache list, which is what makes
                # the graphics API reject the whole set and leaves the stage dead.
                TextEdit(
                    "replace",
                    ACE_REL,
                    old="\tvar uset: RID = UniformSetCacheRD.get_cache(cell_owner_product_shader, 0, [\n",
                    new=(
                        "\tvar u_mutant := _probe_u_storage(999, _static_world_mesh_empty_rd)\n"
                        "\tvar uset: RID = UniformSetCacheRD.get_cache(cell_owner_product_shader, 0, [\n"
                        "\t\tu_mutant,\n"
                    ),
                ),
            ),
        ),
        # S8: Changing a compositor uniform member to an undeclared number
        # reproduces the second blank-screen set-construction defect.
        Mutation(
            "bind undeclared compositor slot from host",
            "S8 binding agreement",
            contract.section_host_binding_agreement,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old="u_final_subcell_product.binding = 44",
                    new="u_final_subcell_product.binding = 999",
                ),
            ),
        ),
        # S8: The shader can declare b68 correctly while the real compositor
        # set omits it. This reproduces a missing projection-authority binding,
        # which makes uniform-set creation fail before any pixel is painted.
        Mutation(
            "remove compositor b68 binding from host set",
            "S8 b68 binding agreement",
            contract.section_host_binding_agreement,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old="\t\t\tu_compositor_vsb_product_frame,\n",
                    new="",
                ),
            ),
        ),
        # S8 inverse: A newly declared Product slot with no host member is the
        # opposite mismatch and can still invalidate uniform-set construction.
        Mutation(
            "declare Product shader slot with no host binding",
            "S8 binding agreement",
            contract.section_host_binding_agreement,
            (
                TextEdit(
                    "append",
                    PRODUCT_REL,
                    new=(
                        "\nlayout(set = 0, binding = 998, std430) "
                        "readonly buffer MutantUnboundProductInput {\n"
                        "\tuint values[];\n"
                        "} mutant_unbound_product_input;\n"
                    ),
                ),
            ),
        ),
        # Extra S6: Skipping one middle slot inside the host's contiguous loop
        # leaves both endpoint names present while b75 is never appended.
        Mutation(
            "skip one middle VSB input in host binding loop",
            "S6 VSB slots",
            contract.section_vsb_binding_coverage,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old=(
                        "\t):\n"
                        "\t\tvar binding_rid: RID = "
                        "binding_rids.get(binding, RID())"
                    ),
                    new=(
                        "\t):\n"
                        "\t\tif binding == "
                        "VisibleSurfaceBuilderScript.BINDING_WATER_SPANS:\n"
                        "\t\t\tcontinue\n"
                        "\t\tvar binding_rid: RID = "
                        "binding_rids.get(binding, RID())"
                    ),
                ),
            ),
        ),
        # W11: All constants and loop endpoints can remain while the constructed
        # RDUniform is never appended. That produces an empty input set at run
        # time, so the exact append is part of the structural contract.
        Mutation(
            "W11 drop VSB input append from host loop",
            "S6 VSB slots",
            contract.section_vsb_binding_coverage,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old="\t\tuniforms.append(uniform)\n",
                    new="",
                ),
            ),
        ),
        # W12: A barrier textually present beneath a literal-false guard never
        # executes. Indentation makes this a real GDScript control-flow change,
        # not a comment mutation.
        Mutation(
            "W12 hide first shared-list barrier under if false",
            "S7 dispatch order",
            contract.section_dispatch_ordering,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old=first_barrier_line,
                    new=(
                        f"{first_barrier_indent}if false:\n"
                        f"{first_barrier_indent}\t"
                        "rd.compute_list_add_barrier(product_compute_list)\n"
                    ),
                    occurrence=1,
                ),
            ),
        ),
        # W13: The native Product call can remain in the callback text while a
        # literal-false guard makes it unreachable. This reproduces the liveness
        # hole in both the activation and native-raster checks.
        Mutation(
            "W13 hide Product dispatch under if false",
            "S7 dispatch order",
            contract.section_dispatch_ordering,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old=product_dispatch_block,
                    new=(
                        f"{product_dispatch_indent}if false:\n"
                        + indented_product_dispatch_block
                    ),
                ),
            ),
        ),
        # W14 verification: the blank-screen defect used the storage-image
        # helper, not the older storage-buffer helper. The resolver must remain
        # helper-agnostic and catch the actual member inserted into the set.
        Mutation(
            "W14 bind undeclared Product slot through storage-image helper",
            "S8 binding agreement",
            contract.section_host_binding_agreement,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old="\tvar uset: RID = UniformSetCacheRD.get_cache(cell_owner_product_shader, 0, [\n",
                    new=(
                        "\tvar u_mutant := _vsb_product_storage_image(999, _vsb_product_rd)\n"
                        "\tvar uset: RID = UniformSetCacheRD.get_cache(cell_owner_product_shader, 0, [\n"
                        "\t\tu_mutant,\n"
                    ),
                ),
            ),
        ),
        # Extra S8 inverse: The compositor has the same declared-but-unbound
        # failure direction as Product, so exercise both shader families.
        Mutation(
            "declare compositor shader slot with no host binding",
            "S8 binding agreement",
            contract.section_host_binding_agreement,
            (
                TextEdit(
                    "append",
                    COMPOSITOR_REL,
                    new=(
                        "\nlayout(set = 0, binding = 998, std430) "
                        "readonly buffer MutantUnboundCompositorInput {\n"
                        "\tuint values[];\n"
                        "} mutant_unbound_compositor_input;\n"
                    ),
                ),
            ),
        ),
        # F1: A correct slot number with the wrong descriptor class is just as
        # fatal as a missing slot. This changes live Product b67 from a storage
        # buffer to a uniform block while keeping it in the real cache list.
        Mutation(
            "F1 change live Product b67 descriptor class",
            "S8 descriptor classes",
            contract.section_host_binding_agreement,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old=(
                        "var u_vsb_visible_products: RDUniform = _probe_u_storage(\n"
                        "\t\t67, _vsb_product_rd\n"
                    ),
                    new=(
                        "var u_vsb_visible_products: RDUniform = _probe_u_ubo(\n"
                        "\t\t67, _vsb_product_rd\n"
                    ),
                ),
            ),
        ),
        # F1: The compositor set uses hand-built RDUniform objects. Mutating the
        # live b44 member proves the checker covers that construction spelling.
        Mutation(
            "F1 change live compositor b44 descriptor class",
            "S8 descriptor classes",
            contract.section_host_binding_agreement,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old=(
                        "u_final_subcell_product.uniform_type = "
                        "RenderingDevice.UNIFORM_TYPE_STORAGE_BUFFER"
                    ),
                    new=(
                        "u_final_subcell_product.uniform_type = "
                        "RenderingDevice.UNIFORM_TYPE_UNIFORM_BUFFER"
                    ),
                ),
            ),
        ),
        # F1: Binding 68 is a std140 uniform block. Supplying the same RID under
        # STORAGE_BUFFER still keeps the slot number correct, yet Vulkan rejects
        # the set because descriptor class is part of the ABI.
        Mutation(
            "F1 change live compositor b68 descriptor class",
            "S8 b68 descriptor class",
            contract.section_host_binding_agreement,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old=(
                        "u_compositor_vsb_product_frame.uniform_type = "
                        "RenderingDevice.UNIFORM_TYPE_UNIFORM_BUFFER"
                    ),
                    new=(
                        "u_compositor_vsb_product_frame.uniform_type = "
                        "RenderingDevice.UNIFORM_TYPE_STORAGE_BUFFER"
                    ),
                ),
            ),
        ),
        # F1: VSB's range loop chooses descriptor classes by named constants.
        # Moving b72 out of the uniform-block branch reproduces a real same-slot
        # class mismatch without changing any slot number.
        Mutation(
            "F1 remove VSB b72 from uniform-block class branch",
            "S8 descriptor classes",
            contract.section_host_binding_agreement,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old=(
                        "elif binding == "
                        "VisibleSurfaceBuilderScript.BINDING_HEIGHTFIELD_DESCRIPTOR \\"
                    ),
                    new=(
                        "elif binding == "
                        "VisibleSurfaceBuilderScript.BINDING_SCENE_TLAS_NODES \\"
                    ),
                ),
            ),
        ),
        # Metal ABI: Product b1 is a sampler-less texture object. Restoring the
        # combined-sampler declaration recreates the descriptor mismatch that
        # hid behind the old parser's blanket uniform-buffer classification.
        Mutation(
            "Metal ABI restore combined sampler on Product b1",
            "S8 descriptor classes",
            contract.section_host_binding_agreement,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=(
                        "layout(set = 0, binding = 1) uniform texture2D "
                        "terrain_corner_height_tex;"
                    ),
                    new=(
                        "layout(set = 0, binding = 1) uniform sampler2D "
                        "terrain_corner_height_tex;"
                    ),
                ),
            ),
        ),
        # Metal ABI: Reordering one b78 row leaves all field names present while
        # changing every host-to-shader meaning from byte 16 onward. The new
        # cardinality section must reject that plausible-looking layout drift.
        Mutation(
            "Metal ABI swap b78 dynamic and static count rows",
            "S6B Metal cardinality ABI",
            contract.section_metal_cardinality_abi,
            (
                TextEdit(
                    "swap",
                    VSB_REL,
                    old=(
                        "    uvec4 dynamic_binding_counts;    // offset 16: "
                        "b76 rows, b79 ids, b80 records, b81 rows\n"
                    ),
                    new=(
                        "    uvec4 static_binding_counts_0;   // offset 32: "
                        "b65 rows, b66 ids, b69 rows, b70 ids\n"
                    ),
                ),
            ),
        ),
        # Metal ABI: Restoring a single runtime-array length query recreates
        # SPIRV-Cross's buffer-size-constant path even when b64 stays unqualified.
        Mutation(
            "Metal ABI restore one VSB runtime-array length query",
            "S6B Metal cardinality ABI",
            contract.section_metal_cardinality_abi,
            (
                TextEdit(
                    "append",
                    VSB_REL,
                    new=(
                        "\nuint mutant_runtime_length() {\n"
                        "    return scene_tlas_nodes.rows.length();\n"
                        "}\n"
                    ),
                ),
            ),
        ),
        # Metal traversal cost: Reintroducing the reciprocal inside the slab
        # helper recreates three floating-point divisions for every visited
        # TLAS and BLAS node. The ray still carries a reciprocal in this mutant,
        # which proves the contract checks the live consumer rather than merely
        # finding the new field and setup assignments somewhere in the shader.
        Mutation(
            "VSB restore per-node slab reciprocal division",
            "S6 VSB slots",
            contract.section_vsb_binding_coverage,
            (
                TextEdit(
                    "replace",
                    VSB_REL,
                    old=(
                        "float inverse_direction = "
                        "ray.inverse_direction[axis];"
                    ),
                    new=(
                        "float inverse_direction = "
                        "1.0 / direction_component;"
                    ),
                ),
            ),
        ),
        # The b83 level may reject a super-tile only when the ray interval is
        # wholly above its exact maximum. Reversing that comparison skips the
        # intervals most likely to contain terrain and can erase the scene while
        # every binding remains valid. This mutation proves the contract reads
        # the live conservative predicate, not just the new b83 declaration.
        Mutation(
            "VSB reverse b83 conservative height skip",
            "S6 VSB slots",
            contract.section_vsb_binding_coverage,
            (
                TextEdit(
                    "replace",
                    VSB_REL,
                    old=(
                        "min(enter_y, exit_y) > super_max + epsilon;"
                    ),
                    new=(
                        "min(enter_y, exit_y) < super_max + epsilon;"
                    ),
                ),
            ),
        ),
        # Adding epsilon widens the rejected interval and can skip a triangle
        # whose endpoint lies within the exact intersection tolerance. This is
        # the dangerous mutation shape: it still looks conservative in prose.
        Mutation(
            "VSB b82 minimum skip widens across epsilon",
            "S6 VSB slots",
            contract.section_vsb_binding_coverage,
            (
                TextEdit(
                    "replace",
                    VSB_REL,
                    old=(
                        "max(lower_guard_enter_y, exit_y) "
                        "< tile_min - epsilon"
                    ),
                    new=(
                        "max(lower_guard_enter_y, exit_y) "
                        "< tile_min + epsilon"
                    ),
                ),
            ),
        ),
        # Swapping the two producer channels keeps a valid RG32F texture and a
        # compiling shader while inverting the interval. The agreement check
        # must compare actual channel meanings on both sides.
        Mutation(
            "VSB b82 producer swaps minimum and maximum channels",
            "S6 VSB slots",
            contract.section_vsb_binding_coverage,
            (
                TextEdit(
                    "replace",
                    SCENE_DB_REL,
                    old="Color(tile_max, tile_min, 0.0, 1.0)",
                    new="Color(tile_min, tile_max, 0.0, 1.0)",
                ),
            ),
        ),
        # An eastward step must copy the previous east edge. Copying the old
        # west corner still compiles and saves a fetch, yet changes terrain
        # geometry, so the contract pins the actual shared-corner mapping.
        Mutation(
            "VSB height-cache east edge mapped to wrong corner",
            "S6 VSB slots",
            contract.section_vsb_binding_coverage,
            (
                TextEdit(
                    "replace",
                    VSB_REL,
                    old="next_heights.x = cached_heights.y;",
                    new="next_heights.x = cached_heights.x;",
                ),
            ),
        ),
        # Explicit absence must reject before Product allocates the large
        # compiler input. This compile-valid mutation restores the constructor
        # work ahead of the selected decode-first boundary.
        Mutation(
            "Product compiler state materialized before decode gate",
            "S9A ABAB",
            contract.section_abab_adjudicator,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=(
                        "\t\tvisible_records[int(slot)] =\n"
                        "\t\t\tvsb_visible_surface_products.products[visible_index];"
                    ),
                    new=(
                        "\t\tVsbProductCompileInput wasted_input =\n"
                        "\t\t\tvsb_product_empty_compile_input(subcell, ansi_dims);\n"
                        "\t\tvisible_records[int(slot)] =\n"
                        "\t\t\tvsb_visible_surface_products.products[visible_index];"
                    ),
                ),
            ),
        ),
        # Metal ABI: Reintroducing writeonly on b64 revives the qualifier shape
        # implicated in the rejected Apple translation without changing slots.
        Mutation(
            "Metal ABI restore writeonly on VSB b64",
            "S6B Metal cardinality ABI",
            contract.section_metal_cardinality_abi,
            (
                TextEdit(
                    "replace",
                    VSB_REL,
                    old=(
                        "layout(set = 0, binding = 64, std430) buffer "
                        "VisibleSurfaceProducts {"
                    ),
                    new=(
                        "layout(set = 0, binding = 64, std430) writeonly buffer "
                        "VisibleSurfaceProducts {"
                    ),
                ),
            ),
        ),
        # Metal ABI: A 32-byte host allocation truncates the two static-count
        # rows while the shader declaration still looks complete.
        Mutation(
            "Metal ABI truncate b78 host allocation to 32 bytes",
            "S6B Metal cardinality ABI",
            contract.section_metal_cardinality_abi,
            (
                TextEdit(
                    "replace",
                    BUILDER_REL,
                    old="const DYNAMIC_GEOMETRY_DESCRIPTOR_BYTES := 64",
                    new="const DYNAMIC_GEOMETRY_DESCRIPTOR_BYTES := 32",
                ),
            ),
        ),
        # F2: A shorter host allocation truncates the last two std140 rows even
        # when every shader field name remains unchanged.
        Mutation(
            "F2 truncate Product frame receipt allocation",
            "S9 Product receipt",
            contract.section_product_frame_receipt_abi,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old="const VSB_PRODUCT_FRAME_RECEIPT_BYTES := 240",
                    new="const VSB_PRODUCT_FRAME_RECEIPT_BYTES := 208",
                ),
            ),
        ),
        # F2: Reusing byte 192 for row 13 shifts cloud/night over the sun row
        # while preserving the total allocation size. The row-order check must
        # detect this interior corruption.
        Mutation(
            "F2 shift Product receipt cloud row onto sun row",
            "S9 Product receipt",
            contract.section_product_frame_receipt_abi,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old=(
                        "\t# Row 13, bytes 208..223: shader cloud_night receives frozen sky facts\n"
                        "\t# plus exact complete-record counts for Product's binding-10 blood bank and\n"
                        "\t# binding-13 local-light glyph bank. Bytes 216 and 220 are cloud_night.zw;\n"
                        "\t# using these reserved float lanes avoids a new ABI row while representing\n"
                        "\t# every allowed count from zero through sixty-four exactly.\n"
                        "\t_write_vec4(\n"
                        "\t\tbytes,\n"
                        "\t\t208,"
                    ),
                    new=(
                        "\t# Row 13, bytes 208..223: shader cloud_night receives frozen sky facts\n"
                        "\t# plus exact complete-record counts for Product's binding-10 blood bank and\n"
                        "\t# binding-13 local-light glyph bank. Bytes 216 and 220 are cloud_night.zw;\n"
                        "\t# using these reserved float lanes avoids a new ABI row while representing\n"
                        "\t# every allowed count from zero through sixty-four exactly.\n"
                        "\t_write_vec4(\n"
                        "\t\tbytes,\n"
                        "\t\t192,"
                    ),
                ),
            ),
        ),
        # Blood active-prefix authority: leaving the CPU count at zero makes
        # every real blood event invisible even though binding 10 is populated.
        Mutation(
            "blood count setter leaves stale zero",
            "S9B blood count",
            contract.section_blood_mark_count_authority,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old="_blood_mark_count = copy_count",
                    new="_blood_mark_count = 0",
                ),
            ),
        ),
        # A requested count can exceed the number of complete twelve-float rows.
        # Dropping the complete-row bound publishes a partially owned record.
        Mutation(
            "blood count ignores complete-row boundary",
            "S9B blood count",
            contract.section_blood_mark_count_authority,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old="mini(BLOOD_MARK_MAX_COUNT, complete_row_count)",
                    new="BLOOD_MARK_MAX_COUNT",
                ),
            ),
        ),
        # Keeping the formerly reserved lane at zero defeats the entire
        # handoff while the buffer and shader declarations still look valid.
        Mutation(
            "blood count omitted from b68",
            "S9B blood count",
            contract.section_blood_mark_count_authority,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old=(
                        "\t\tfloat(_blood_mark_count),\n"
                        "\t\tfloat(local_light_glyph_count)\n"
                    ),
                    new=(
                        "\t\t0.0,\n"
                        "\t\tfloat(local_light_glyph_count)\n"
                    ),
                ),
            ),
        ),
        # Swapping the two active counts preserves all fifteen rows but makes
        # each Product consumer read the other binding's prefix.
        Mutation(
            "blood and glyph counts swapped across b68 lanes",
            "S9B blood count",
            contract.section_blood_mark_count_authority,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old=(
                        "\t\tfloat(_blood_mark_count),\n"
                        "\t\tfloat(local_light_glyph_count)\n"
                    ),
                    new=(
                        "\t\tfloat(local_light_glyph_count),\n"
                        "\t\tfloat(_blood_mark_count)\n"
                    ),
                ),
            ),
        ),
        # A hard-coded capacity revives the deleted sixty-four-row runtime
        # owner and restores the normal-play work this slice removes.
        Mutation(
            "blood count reader hardcodes physical capacity",
            "S9B blood count",
            contract.section_blood_mark_count_authority,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old="return encoded_count == float(count) ? count : 0u;",
                    new="return uint(BLOOD_MARK_MAX_COUNT);",
                ),
            ),
        ),
        # Each consumer needs its own bound. Removing the first guard restores
        # a full ground scan while actor blood still appears structurally sound.
        Mutation(
            "ground blood scan ignores active count",
            "S9B blood count",
            contract.section_blood_mark_count_authority,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old="\t\tif (uint(i) >= mark_count) break;\n",
                    new="",
                    occurrence=1,
                ),
            ),
        ),
        # Removing the second guard restores the same regression for actor
        # coverage without disturbing the independently checked ground path.
        Mutation(
            "actor blood scan ignores active count",
            "S9B blood count",
            contract.section_blood_mark_count_authority,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old="\t\t\tif (uint(i) >= mark_count) break;\n",
                    new="",
                    occurrence=1,
                ),
            ),
        ),
        # The zero fast exit is the normal-play guarantee: no active marks means
        # no binding-10 access before the bounded loop.
        Mutation(
            "ground blood zero-count fast exit removed",
            "S9B blood count",
            contract.section_blood_mark_count_authority,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=(
                        "\tif (mark_count == 0u) {\n"
                        "\t\treturn fact;\n"
                        "\t}\n"
                    ),
                    new="",
                ),
            ),
        ),
        # World owns the physical row count. Substituting the nonzero-intensity
        # metric can truncate later valid rows when an earlier slot has faded.
        Mutation(
            "World publishes nonzero metric as blood slot count",
            "S9B blood count",
            contract.section_blood_mark_count_authority,
            (
                TextEdit(
                    "replace",
                    WORLD_REL,
                    old="_ascii_effect.set_blood_events(rows, count)",
                    new="_ascii_effect.set_blood_events(rows, nonzero_count)",
                ),
            ),
        ),
        # A zero active prefix is the common daylight case. Removing the early
        # return restores the fixed-capacity b13 scan before Product can prove
        # that no glyph record belongs to this view.
        Mutation(
            "local-light glyph zero-count fast exit removed",
            "S9C local-light glyph count",
            contract.section_local_light_glyph_count_authority,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old="\tif (glyph_count == 0u) return product;\n",
                    new="",
                ),
            ),
        ),
        # Checking the prefix after the first accessor still performs one
        # unowned b13 load on every zero-count frame.
        Mutation(
            "local-light glyph prefix guard moved after first load",
            "S9C local-light glyph count",
            contract.section_local_light_glyph_count_authority,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=(
                        "\t\tif (uint(i) >= glyph_count) break;\n"
                        "\t\tfloat alpha = local_light_glyph_alpha(i);\n"
                    ),
                    new=(
                        "\t\tfloat alpha = local_light_glyph_alpha(i);\n"
                        "\t\tif (uint(i) >= glyph_count) break;\n"
                    ),
                ),
            ),
        ),
        # Analyzer metrics describe the completed pack but do not own it.
        # Replacing the direct return value with that mirror revives a stale,
        # independently mutable authority path.
        Mutation(
            "local-light glyph receipt sourced from analyzer metrics",
            "S9C local-light glyph count",
            contract.section_local_light_glyph_count_authority,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old=(
                        "_build_vsb_view_dispatch(\n"
                        "\t\t\tpixel_ctx,\n"
                        "\t\t\tview,\n"
                        "\t\t\tlocal_light_glyph_count\n"
                        "\t\t)"
                    ),
                    new=(
                        "_build_vsb_view_dispatch(\n"
                        "\t\t\tpixel_ctx,\n"
                        "\t\t\tview,\n"
                        "\t\t\tint(_local_light_last_metrics.get("
                        "\"glyph_record_count\", 0))\n"
                        "\t\t)"
                    ),
                ),
            ),
        ),
        # Clamping turns builder failure (-1) into a plausible empty bank.
        # The view must preserve the negative sentinel and fail closed.
        Mutation(
            "local-light glyph builder failure clamped to zero",
            "S9C local-light glyph count",
            contract.section_local_light_glyph_count_authority,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old=(
                        "\t\tvar local_light_glyph_count: int = "
                        "_build_local_light_records("
                    ),
                    new=(
                        "\t\tvar local_light_glyph_count: int = maxi("
                        "_build_local_light_records("
                    ),
                ),
                TextEdit(
                    "replace",
                    ACE_REL,
                    old=(
                        "\t\t\tbonfire_anchor_valid_for_frame\n"
                        "\t\t)\n"
                        "\t\t_ensure_local_light_control_synced()"
                    ),
                    new=(
                        "\t\t\tbonfire_anchor_valid_for_frame\n"
                        "\t\t), 0)\n"
                        "\t\t_ensure_local_light_control_synced()"
                    ),
                ),
            ),
        ),
        # The shader-side integer conversion truncates fractions. Removing the
        # equality check would accept 3.5 as a three-record authority.
        Mutation(
            "local-light glyph fractional count accepted",
            "S9C local-light glyph count",
            contract.section_local_light_glyph_count_authority,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old="return encoded_count == float(count) ? count : 0u;",
                    new="return count;",
                    occurrence=2,
                ),
            ),
        ),
        # A NaN bypasses ordinary range comparisons. Its explicit rejection is
        # required before the float is converted to an integer.
        Mutation(
            "local-light glyph NaN count accepted",
            "S9C local-light glyph count",
            contract.section_local_light_glyph_count_authority,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old="if (isnan(encoded_count) || isinf(encoded_count)",
                    new="if (isinf(encoded_count)",
                    occurrence=2,
                ),
            ),
        ),
        # Keeping byte 220 at zero preserves the struct shape while silently
        # disabling every firefly glyph in Product.
        Mutation(
            "local-light glyph count omitted from b68 byte 220",
            "S9C local-light glyph count",
            contract.section_local_light_glyph_count_authority,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old=(
                        "\t\tfloat(_blood_mark_count),\n"
                        "\t\tfloat(local_light_glyph_count)\n"
                    ),
                    new=(
                        "\t\tfloat(_blood_mark_count),\n"
                        "\t\t0.0\n"
                    ),
                ),
            ),
        ),
        # The unchanged-record fast path skips byte repacking but must return
        # the same active prefix. Zeroing its count makes an identical second
        # view lose every firefly glyph despite valid cached bytes.
        Mutation(
            "local-light glyph cache hit loses active prefix",
            "S9C local-light glyph count",
            contract.section_local_light_glyph_count_authority,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old=(
                        "\telse:\n"
                        "\t\tglyph_matched = mini("
                        "glyph_candidates.size(), "
                        "LOCAL_LIGHT_MAX_GLYPH_RECORDS)\n"
                    ),
                    new=(
                        "\telse:\n"
                        "\t\tglyph_matched = 0\n"
                    ),
                ),
            ),
        ),
        # F3: Product's cell dispatch must divide by the shader's 8-wide local
        # axis. Sixteen leaves half the ANSI columns undispatched.
        Mutation(
            "F3 change Product cell dispatch X divisor",
            "S10 dispatch extent",
            contract.section_dispatch_extent_agreement,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old="(ansi.x + 7) / 8",
                    new="(ansi.x + 15) / 16",
                ),
            ),
        ),
        # F3: The compositor has an independent 8-wide dispatch and needs its
        # own mutation, since Product parity cannot prove final paint coverage.
        Mutation(
            "F3 change compositor dispatch X divisor",
            "S10 dispatch extent",
            contract.section_dispatch_extent_agreement,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old="(pixel_target_size.x + 7) / 8",
                    new="(pixel_target_size.x + 15) / 16",
                ),
            ),
        ),
        # F3: VSB derives group counts through a shared host constant. Drifting
        # that constant leaves the shader at 8x8 and misses half the subcells.
        Mutation(
            "F3 change VSB host LOCAL_SIZE",
            "S10 dispatch extent",
            contract.section_dispatch_extent_agreement,
            (
                TextEdit(
                    "replace",
                    BUILDER_REL,
                    old="const LOCAL_SIZE := Vector2i(8, 8)",
                    new="const LOCAL_SIZE := Vector2i(16, 8)",
                ),
            ),
        ),
        # F3: Product's leaf emitter flattens all 64 local invocations into X;
        # a divisor of 128 strands half its particle lanes.
        Mutation(
            "F3 change Product leaf dispatch divisor",
            "S10 dispatch extent",
            contract.section_dispatch_extent_agreement,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old="(leaf_lanes + 63) / 64",
                    new="(leaf_lanes + 127) / 128",
                ),
            ),
        ),
        # F4: A mask test admits corrupt status combinations as scene sky. The
        # real law requires exact equality with ABSENT|NO_HIT.
        Mutation(
            "F4 weaken b64 absence status equality",
            "S11 Product adapter",
            contract.section_product_adapter_semantics,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=(
                        "record.status_flags == "
                        "(VSB_STATUS_ABSENT | VSB_STATUS_NO_HIT)"
                    ),
                    new=(
                        "(record.status_flags & "
                        "(VSB_STATUS_ABSENT | VSB_STATUS_NO_HIT)) != 0u"
                    ),
                ),
            ),
        ),
        # F4: Averaging an adjacent height instead of the diagonal h11 corner
        # changes the semantic anchor despite valid-looking terrain paint.
        Mutation(
            "F4 change terrain anchor diagonal corner",
            "S11 Product adapter",
            contract.section_product_adapter_semantics,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old="float h11 = input_value.selected_corner_heights.w;",
                    new="float h11 = input_value.selected_corner_heights.y;",
                ),
            ),
        ),
        # F4: Hit-to-plane distance is zero at a water hit. Substituting it for
        # four-corner bed depth erases the entire water-column law.
        Mutation(
            "F4 replace water column depth with hit-plane distance",
            "S11 Product adapter",
            contract.section_product_adapter_semantics,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=(
                        "+ input_value.selected_corner_heights.w);"
                    ),
                    new=(
                        "+ input_value.selected_corner_heights.z);"
                    ),
                ),
            ),
        ),
        # F4: Receiver geometry must come from the exact b64 hit position.
        # Replacing it with the semantic anchor changes depth/lighting inputs
        # while leaving identity fields plausible.
        Mutation(
            "F4 map input receiver from semantic anchor",
            "S11 Product adapter",
            contract.section_product_adapter_semantics,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=(
                        "input_value.context.trace_receiver = "
                        "record.world_position.xyz;"
                    ),
                    new=(
                        "input_value.context.trace_receiver = "
                        "record.stable_world_anchor.xyz;"
                    ),
                ),
            ),
        ),
        # F4: These shifts were transposed in an earlier reviewed plan. Keeping
        # both mutations in one defect reproduces the complete bit-lane swap.
        Mutation(
            "F4 transpose entity type and shade shifts",
            "S11 Product adapter",
            contract.section_product_adapter_semantics,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old="const uint PROBE_ENTITY_MESH_TYPE_SHIFT = 22u;",
                    new="const uint PROBE_ENTITY_MESH_TYPE_SHIFT = 26u;",
                ),
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old="const uint PROBE_ENTITY_MESH_SHADE_SHIFT = 26u;",
                    new="const uint PROBE_ENTITY_MESH_SHADE_SHIFT = 22u;",
                ),
            ),
        ),
        # F4: Static source keys live after terrain. Dropping that base makes
        # unrelated owners collide in diagnostic provenance.
        Mutation(
            "F4 remove terrain base from static source key",
            "S11 Product adapter",
            contract.section_product_adapter_semantics,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=(
                        "(uint(product_vsb_frame.heightfield_dims_identity.x\n"
                        "\t\t\t* product_vsb_frame.heightfield_dims_identity.y)\n"
                        "\t\t+ legacy_face) & SPLAT_SOURCE_INDEX_MASK"
                    ),
                    new="legacy_face & SPLAT_SOURCE_INDEX_MASK",
                ),
            ),
        ),
        # F4: Dynamic source keys must start after both terrain and all retained
        # static faces, not merely after terrain.
        Mutation(
            "F4 remove static base from dynamic source key",
            "S11 Product adapter",
            contract.section_product_adapter_semantics,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=(
                        "(terrain_count + product_vsb_frame.view_identity.w "
                        "+ legacy_face)"
                    ),
                    new="(terrain_count + legacy_face)",
                ),
            ),
        ),
        # F4: The far-water backstop uses a reserved sentinel outside the
        # ordinary masked source namespace. Masking it destroys that class.
        Mutation(
            "F4 mask reserved water backstop source key",
            "S11 Product adapter",
            contract.section_product_adapter_semantics,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=(
                        "input_value.facts.source_key = "
                        "VSB_PRODUCT_BACKSTOP_SOURCE_KEY;"
                    ),
                    new=(
                        "input_value.facts.source_key = "
                        "VSB_PRODUCT_BACKSTOP_SOURCE_KEY & "
                        "SPLAT_SOURCE_INDEX_MASK;"
                    ),
                ),
            ),
        ),
        # F4: Marker values are part of Product-authored diagnostics; changing
        # one bit silently reclassifies flower receipts.
        Mutation(
            "F4 drift Product flower diagnostic marker",
            "S11 Product adapter",
            contract.section_product_adapter_semantics,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old="const uint Q3_DIAG_MARKER_FLOWER = 8u;",
                    new="const uint Q3_DIAG_MARKER_FLOWER = 16u;",
                ),
            ),
        ),
        # F4: W-LIT's 0.35 matlib blend is a moved visual law. A plausible
        # nearby blend still changes every retained mesh's authored colour.
        Mutation(
            "F4 drift W-LIT matlib blend",
            "S11 Product adapter",
            contract.section_product_adapter_semantics,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old="albedo = mix(background, foreground, 0.35);",
                    new="albedo = mix(background, foreground, 0.50);",
                ),
            ),
        ),
        # F4: Projection fences were deleted so ortho and perspective share the
        # same owner compiler. Reintroducing a main-level branch violates that
        # boundary even if both branches currently appear harmless.
        Mutation(
            "F4 reintroduce projection branch in Product main",
            "S11 Product adapter",
            contract.section_product_adapter_semantics,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old="void main() {\n",
                    new=(
                        "void main() {\n"
                        "\tif (product_vsb_frame.view_identity.z == 99u) return;\n"
                    ),
                    occurrence=1,
                ),
            ),
        ),
        # FL-4431 TSHADOW3: replacing the orthographic clone jitter with the
        # perspective cell hash recreates the bright, flat grass register while
        # leaving the shader syntactically valid.
        Mutation(
            "F4 replace ortho smooth shade jitter",
            "S11 Product adapter",
            contract.section_product_adapter_semantics,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old="product_smooth_terrain_noise(cell, 5.0, 37u)",
                    new="stable_cell_noise(cell, 37u)",
                    occurrence=2,
                ),
            ),
        ),
        # The Bayer key is the stable authored terrain cell in the baseline
        # orthographic register. Height-band noise changes its shade choices.
        Mutation(
            "F4 replace ortho ANSI Bayer shade key",
            "S11 Product adapter",
            contract.section_product_adapter_semantics,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old="product_ansi_dither_threshold(cell)",
                    new="stable_cell_noise(band_cell, 4359u)",
                    occurrence=1,
                ),
            ),
        ),
        # TSHADOW3 deliberately reconnects ortho to the live world cloud field.
        # Pointing the helper back at the frozen Slice-2 field reproduces the
        # temporal-register mismatch without breaking GLSL compilation.
        Mutation(
            "F4 freeze ortho cloud field",
            "S11 Product adapter",
            contract.section_product_adapter_semantics,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=(
                        "return product_cloud_density_at_sample_xz("
                        "(receiver + sun * t_cloud).xz);"
                    ),
                    new=(
                        "return q3_probe_payload_soft_cloud_density_at_sample_xz("
                        "(receiver + sun * t_cloud).xz);"
                    ),
                ),
            ),
        ),
        # A plausible perspective amplitude is exactly the kind of accidental
        # constant unification that erased the baseline orthographic register.
        Mutation(
            "F4 overwrite ortho dirt dither amplitude",
            "S11 Product adapter",
            contract.section_product_adapter_semantics,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old="amp = ortho_register ? 0.055 : 0.090;",
                    new="amp = ortho_register ? 0.090 : 0.090;",
                ),
            ),
        ),
        # The remapped-glyph step is intentionally smaller than the general
        # mountain contrast. Substituting 0.24 loses the baseline 0.20 witness.
        Mutation(
            "F4 overwrite ortho remapped-cliff contrast",
            "S11 Product adapter",
            contract.section_product_adapter_semantics,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=(
                        "family == TERRAIN_FAMILY_CLIFF_ROCK ? 0.20 : 0.17"
                    ),
                    new=(
                        "family == TERRAIN_FAMILY_CLIFF_ROCK ? 0.24 : 0.17"
                    ),
                ),
            ),
        ),
        # FL-4431 Product-stage contribution receipts occupy the six high bits
        # of b62's existing marker word. Colliding two masks would make one
        # contribution indistinguishable from another even though both shader
        # and host still compile.
        Mutation(
            "F4A collide contrail with bonfire-light contribution bit",
            "S11B Product contribution receipt",
            contract.section_product_contribution_receipt,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=(
                        "const uint PRODUCT_CONTRIBUTED_AIRPLANE_CONTRAIL_BIT "
                        "= 1u << 31u;"
                    ),
                    new=(
                        "const uint PRODUCT_CONTRIBUTED_AIRPLANE_CONTRAIL_BIT "
                        "= 1u << 30u;"
                    ),
                ),
            ),
        ),
        # A reachable beam call is insufficient evidence: the contribution
        # marker must stay false when the stage did not change glyph and ink.
        Mutation(
            "F4A remove lighthouse paint-change admission",
            "S11B Product contribution receipt",
            contract.section_product_contribution_receipt,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=(
                        "if (beam.present && vsb_product_paint_changed("
                        "before_beam, record)) {"
                    ),
                    new="if (beam.present) {",
                ),
            ),
        ),
        # Flame facts are dynamic faces tagged with the two authored flame
        # kinds. Treating every retained face as flame would produce confident
        # positive receipts across ordinary meshes.
        Mutation(
            "F4A remove bonfire-flame fact-kind admission",
            "S11B Product contribution receipt",
            contract.section_product_contribution_receipt,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=(
                        "(input_value.retained_face.actor_effect_kind == 8u\n"
                        "\t\t\t\t|| input_value.retained_face.actor_effect_kind "
                        "== 9u)"
                    ),
                    new=(
                        "(input_value.retained_face.actor_effect_kind >= 0u)"
                    ),
                ),
            ),
        ),
        # Bonfire light must have contributed kind-1 illumination. A generic
        # local-light paint change cannot truthfully stand in for that fact.
        Mutation(
            "F4A remove bonfire-light contribution admission",
            "S11B Product contribution receipt",
            contract.section_product_contribution_receipt,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=(
                        "if (local_light_facts.bonfire_contributed\n"
                        "\t\t\t\t&& vsb_product_paint_changed(\n"
                        "\t\t\t\t\tbefore_local_light, record)) {"
                    ),
                    new=(
                        "if (vsb_product_paint_changed(\n"
                        "\t\t\t\t\tbefore_local_light, record)) {"
                    ),
                ),
            ),
        ),
        # The composite walk must keep bonfire smoke in its own winner lane.
        # Reusing the airplane winner would let whichever material happens to
        # be nearer erase the other independently layered effect.
        Mutation(
            "F4A collapse bonfire smoke into airplane vapor winner",
            "S11B Product contribution receipt",
            contract.section_product_contribution_receipt,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=(
                        "facts, retained_anchor, "
                        "selected.airplane_vapor);"
                    ),
                    new=(
                        "facts, retained_anchor, "
                        "selected.bonfire_smoke);"
                    ),
                ),
            ),
        ),
        # The whole optimization is one retained-tree walk shared by both
        # material lanes. Restoring a second call recreates the duplicated
        # traversal cost even though the selected pixels remain identical.
        Mutation(
            "F4A resurrect second composite vapor traversal",
            "S11B Product contribution receipt",
            contract.section_product_contribution_receipt,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=(
                        "vsb_retained_select_vapor_effect_candidates(\n"
                        "\t\t\tinput_value.ray, effect_depth);"
                    ),
                    new=(
                        "vsb_retained_select_vapor_effect_candidates(\n"
                        "\t\t\tinput_value.ray, effect_depth);\n"
                        "\tVsbRetainedVaporSelections duplicate_vapor_layers =\n"
                        "\t\tvsb_retained_select_vapor_effect_candidates(\n"
                        "\t\t\tinput_value.ray, effect_depth);"
                    ),
                ),
            ),
        ),
        # Material 207 is the ordinary contrail lane. A nearby numeric typo
        # would silently turn a different effect into the positive receipt.
        Mutation(
            "F4A drift contrail contribution material",
            "S11B Product contribution receipt",
            contract.section_product_contribution_receipt,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=(
                        "if (vapor_layers.airplane_vapor.facts.material_id "
                        "== 207u) {"
                    ),
                    new=(
                        "if (vapor_layers.airplane_vapor.facts.material_id "
                        "== 208u) {"
                    ),
                ),
            ),
        ),
        # Material 211 is wounded-airplane damage vapor and must remain
        # distinct from explosion material 212.
        Mutation(
            "F4A drift damage-vapor contribution material",
            "S11B Product contribution receipt",
            contract.section_product_contribution_receipt,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=(
                        "vapor_layers.airplane_vapor.facts.material_id "
                        "== 211u) {"
                    ),
                    new=(
                        "vapor_layers.airplane_vapor.facts.material_id "
                        "== 212u) {"
                    ),
                ),
            ),
        ),
        # Product now authors the live failure marker itself. Changing one ink
        # component would make dispatch rejection look different from the
        # byte-identical allocation and host-recovery marker.
        Mutation(
            "F4A drift Product-local failure marker colour",
            "S11B Product contribution receipt",
            contract.section_product_contribution_receipt,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old="record.fg = vec4(1.0, 0.0, 1.0, 1.0);",
                    new="record.fg = vec4(0.0, 0.0, 0.0, 1.0);",
                ),
            ),
        ),
        # Returning to an empty local initializer would make failed subcells
        # black now that ordinary frames no longer receive a host b55 upload.
        Mutation(
            "F4A replace Product-local failure initializer",
            "S11B Product contribution receipt",
            contract.section_product_contribution_receipt,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=(
                        "\t\tProbeSubcellProductRecord final_record =\n"
                        "\t\t\tvsb_product_failure_final_record();"
                    ),
                    new=(
                        "\t\tProbeSubcellProductRecord final_record =\n"
                        "\t\t\tvsb_product_record_from_payload(empty_record());"
                    ),
                ),
            ),
        ),
        # Guarding the sole b55 store revives stale prior-frame rows whenever
        # decode, owner preparation, frozen-ray work, and compilation reject.
        Mutation(
            "F4A condition the sole Product store on success",
            "S11B Product contribution receipt",
            contract.section_product_contribution_receipt,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=(
                        "\t\tfinalized_probe_subcell_products"
                        ".products[final_index] =\n"
                        "\t\t\tstaged_final_records[slot];"
                    ),
                    new=(
                        "\t\tif (staged_final_record_ready[slot]) {\n"
                        "\t\t\tfinalized_probe_subcell_products"
                        ".products[final_index] =\n"
                        "\t\t\t\tstaged_final_records[slot];\n"
                        "\t\t}"
                    ),
                ),
            ),
        ),
        # Removing the explicit final-index bound leaves the unconditional
        # store dependent on an inferred equality instead of its own capacity.
        Mutation(
            "F4A remove explicit b55 final-index capacity guard",
            "S11B Product contribution receipt",
            contract.section_product_contribution_receipt,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=(
                        "if (visible_index >= final_record_capacity\n"
                        "\t\t\t\t|| final_index >= final_record_capacity) "
                        "continue;"
                    ),
                    new=(
                        "if (visible_index >= final_record_capacity) continue;"
                    ),
                ),
            ),
        ),
        # Moving the contribution write before b55 publication recreates the
        # false positive where a stage changed a record Product later refused.
        Mutation(
            "F4A publish b62 contribution before admitted b55 store",
            "S11B Product contribution receipt",
            contract.section_product_contribution_receipt,
            (
                TextEdit(
                    "swap",
                    PRODUCT_REL,
                    old=(
                        "\t\tfinalized_probe_subcell_products"
                        ".products[final_index] =\n"
                        "\t\t\tstaged_final_records[slot];\n"
                    ),
                    new=(
                        "\t\t\tif (staged_diagnostic_index_valid[slot]\n"
                        "\t\t\t\t\t&& staged_secondary_effect_bits[slot] != 0u) {\n"
                        "\t\t\t\tq3_probe_subcell_diagnostics"
                        ".records[final_index].r0.w |=\n"
                        "\t\t\t\t\tstaged_secondary_effect_bits[slot];\n"
                        "\t\t\t}\n"
                    ),
                ),
            ),
        ),
        # Evidence readback is intentionally synchronous. Bypassing the
        # command-line flag would contaminate ordinary performance every frame.
        Mutation(
            "F4A bypass secondary-effect evidence callback gate",
            "S11B Product contribution receipt",
            contract.section_product_contribution_receipt,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old=(
                        "\t\t\tif _fl4431_secondary_effect_evidence_enabled:\n"
                        "\t\t\t\t_read_vsb_secondary_effect_contribution_receipt("
                    ),
                    new=(
                        "\t\t\tif true:\n"
                        "\t\t\t\t_read_vsb_secondary_effect_contribution_receipt("
                    ),
                ),
            ),
        ),
        # A true declaration would enable the readback before command-line
        # parsing and would make a missing argument no longer fail closed.
        Mutation(
            "F4A enable secondary-effect evidence by default",
            "S11B Product contribution receipt",
            contract.section_product_contribution_receipt,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old=(
                        "var _fl4431_secondary_effect_evidence_enabled: "
                        "bool = false"
                    ),
                    new=(
                        "var _fl4431_secondary_effect_evidence_enabled: "
                        "bool = true"
                    ),
                ),
            ),
        ),
        # b62 is three uvec4 rows. A 44-byte host stride walks into the previous
        # row after the first record while still returning plausible integers.
        Mutation(
            "F4A drift b62 host record stride",
            "S11B Product contribution receipt",
            contract.section_product_contribution_receipt,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old="const VSB_PRODUCT_Q3_DIAGNOSTIC_RECORD_BYTES := 48",
                    new="const VSB_PRODUCT_Q3_DIAGNOSTIC_RECORD_BYTES := 44",
                ),
            ),
        ),
        # The marker is r0.w at byte 12. Reading r0.z at byte 8 turns primitive
        # identity into convincing effect flags.
        Mutation(
            "F4A read b62 contribution from r0.z",
            "S11B Product contribution receipt",
            contract.section_product_contribution_receipt,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old=(
                        "record_index * "
                        "VSB_PRODUCT_Q3_DIAGNOSTIC_RECORD_BYTES + 12"
                    ),
                    new=(
                        "record_index * "
                        "VSB_PRODUCT_Q3_DIAGNOSTIC_RECORD_BYTES + 8"
                    ),
                ),
            ),
        ),
        # Host and shader both name each bit. Drifting only the reader recreates
        # a cross-language ABI disagreement that neither compiler can detect.
        Mutation(
            "F4A drift host lighthouse contribution bit",
            "S11B Product contribution receipt",
            contract.section_product_contribution_receipt,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old=(
                        "const PRODUCT_CONTRIBUTED_LIGHTHOUSE_BEAM_BIT := 1 << 28"
                    ),
                    new=(
                        "const PRODUCT_CONTRIBUTED_LIGHTHOUSE_BEAM_BIT := 1 << 25"
                    ),
                ),
            ),
        ),
        # b62 stops at Product. Calling its availability "visible" would turn
        # a stage-local diagnostic into a false claim about compositor output.
        Mutation(
            "F4A relabel Product contribution availability as visible",
            "S11B Product contribution receipt",
            contract.section_product_contribution_receipt,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old="product_mat207_contribution_receipt_available",
                    new="product_mat207_visible_receipt_available",
                ),
            ),
        ),
        # The versioned schema is the consumer's first semantic boundary.
        # Reverting it to the old ambiguous name must fail even when every
        # numeric count still parses.
        Mutation(
            "F4A revert Product contribution snapshot schema",
            "S11B Product contribution receipt",
            contract.section_product_contribution_receipt,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old=(
                        "fl4431.secondary_effect_product_contribution_snapshot.v1"
                    ),
                    new="fl4431.secondary_effect_product_snapshot.v1",
                ),
            ),
        ),
        # S11C: Restoring a print in the render callback recreates the moving-
        # camera log stream that polluted every normal frame.
        Mutation(
            "S11C restore moving-camera render-loop print",
            "S11C measurement contamination",
            contract.section_measurement_contamination,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old=(
                        "\t\t_write_ivec4(push, 80, frame_cell_size.x, "
                        "frame_cell_size.y, glyph_w, glyph_h)"
                    ),
                    new=(
                        '\t\tprint("FL4431_CAM_DIAG mutant")\n'
                        "\t\t_write_ivec4(push, 80, frame_cell_size.x, "
                        "frame_cell_size.y, glyph_w, glyph_h)"
                    ),
                ),
            ),
        ),
        # S11C: Calling the full-cell audit from HUD state recreates the
        # synchronous GPU drain hidden behind ordinary status polling.
        Mutation(
            "S11C restore HUD-triggered final-cell readback",
            "S11C measurement contamination",
            contract.section_measurement_contamination,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old=(
                        "\tvar final_paint_audit: Dictionary = (\n"
                        "\t\t_last_explicit_final_cell_paint_audit.duplicate(true)\n"
                        "\t)"
                    ),
                    new=(
                        "\tvar final_paint_audit: Dictionary = (\n"
                        "\t\t_runtime_final_cell_paint_audit(\n"
                        "\t\t\trd.buffer_get_data(final_cell_paint_rd)\n"
                        "\t\t)\n"
                        "\t)"
                    ),
                ),
            ),
        ),
        # S11C: Keeping the explicit capture buffer alive after its sole dump
        # lets later code revive a full-bank observation cost.
        Mutation(
            "S11C strand explicit final-cell capture allocation",
            "S11C measurement contamination",
            contract.section_measurement_contamination,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old="\t_release_final_cell_paint_capture_buffer()",
                    new="\t# Mutant leaves the full capture allocation alive.",
                ),
            ),
        ),
        # S11C: Removing the host evidence gate restores the complete b62 clear
        # and upload on every frame even when nobody requested a receipt.
        Mutation(
            "S11C bypass host b62 evidence gate",
            "S11C measurement contamination",
            contract.section_measurement_contamination,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old=(
                        "\tif _fl4431_secondary_effect_evidence_enabled:\n"
                        "\t\tvar required_diagnostic_bytes: int = ("
                    ),
                    new=(
                        "\tif true:\n"
                        "\t\tvar required_diagnostic_bytes: int = ("
                    ),
                ),
            ),
        ),
        # S11C: A true shader default makes b62 part of normal Product work even
        # when the host forgets to supply the specialization.
        Mutation(
            "S11C enable Product b62 diagnostics by default",
            "S11C measurement contamination",
            contract.section_measurement_contamination,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=(
                        "layout(constant_id = 17) const bool "
                        "PRODUCT_B62_DIAGNOSTICS_ENABLED = false;"
                    ),
                    new=(
                        "layout(constant_id = 17) const bool "
                        "PRODUCT_B62_DIAGNOSTICS_ENABLED = true;"
                    ),
                ),
            ),
        ),
        # S11C: Forcing the host specialization true recreates the same default
        # cost while leaving the shader's source default deceptively false.
        Mutation(
            "S11C force host b62 specialization true",
            "S11C measurement contamination",
            contract.section_measurement_contamination,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old=(
                        "\tproduct_b62_diagnostics_specialization.value = (\n"
                        "\t\t_fl4431_secondary_effect_evidence_enabled\n"
                        "\t)"
                    ),
                    new=(
                        "\tproduct_b62_diagnostics_specialization.value = true"
                    ),
                ),
            ),
        ),
        # S11C: Dropping the specialization from the per-slot admission makes
        # every b62 store execute whenever the output index is in range.
        Mutation(
            "S11C remove Product b62 specialization admission",
            "S11C measurement contamination",
            contract.section_measurement_contamination,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=(
                        "\t\tbool diagnostic_index_valid =\n"
                        "\t\t\tPRODUCT_B62_DIAGNOSTICS_ENABLED\n"
                        "\t\t\t&& final_index < final_record_capacity;"
                    ),
                    new=(
                        "\t\tbool diagnostic_index_valid =\n"
                        "\t\t\tfinal_index < final_record_capacity;"
                    ),
                ),
            ),
        ),
        # S11C: Re-coupling the compiler to diagnostic capacity turns an
        # observation lane back into visible-output authority.
        Mutation(
            "S11C recouple Product compiler to b62 capacity",
            "S11C measurement contamination",
            contract.section_measurement_contamination,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old="\t\t\tif (input_value.valid) {",
                    new=(
                        "\t\t\tif (input_value.valid "
                        "&& diagnostic_index_valid) {"
                    ),
                ),
            ),
        ),
        # S11C: Removing one local fence proves the contract checks each access
        # scope instead of merely counting b62 tokens.
        Mutation(
            "S11C expose one Product b62 store outside its fence",
            "S11C measurement contamination",
            contract.section_measurement_contamination,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old="\t\t\tif (diagnostic_index_valid) {",
                    new="\t\t\tif (true) {",
                    occurrence=1,
                ),
            ),
        ),
        # F5: Moving the real beam call into an uncalled helper preserves both
        # declaration and call tokens while removing the visible behavior.
        Mutation(
            "F5 strand lighthouse call in dead helper",
            "S12 lighthouse reachability",
            contract.section_lighthouse_reachability,
            (
                TextEdit(
                    "replace",
                    PRODUCT_REL,
                    old=(
                        "\t\t\t\tbest_beam_glyph = "
                        "lighthouse_beam_screen_stroke_glyph(\n"
                        "\t\t\t\t\tcone, beam_axis_t, fb_size);"
                    ),
                    new=(
                        "\t\t\t\tbest_beam_glyph = uint(35);"
                    ),
                ),
                TextEdit(
                    "append",
                    PRODUCT_REL,
                    new=(
                        "\nuint mutant_dead_beam_call(\n"
                        "\tLighthouseCone cone, float axis_t, vec2 fb_size\n"
                        ") {\n"
                        "\treturn lighthouse_beam_screen_stroke_glyph(\n"
                        "\t\tcone, axis_t, fb_size);\n"
                        "}\n"
                    ),
                ),
            ),
        ),
        # F5: A zero angular speed leaves the call graph intact but turns the
        # named rotating beam into a static stripe.
        Mutation(
            "F5 stop lighthouse rotation",
            "S12 lighthouse reachability",
            contract.section_lighthouse_reachability,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old="const LIGHTHOUSE_BEAM_ROT_RAD_PER_S := 0.5235987756",
                    new="const LIGHTHOUSE_BEAM_ROT_RAD_PER_S := 0.0",
                ),
            ),
        ),
        # F7: The retired analyzer defect was an unconditional return followed
        # by hundreds of assertions. This creates that exact shape in the
        # disposable Python copy.
        Mutation(
            "F7 add unreachable assertions after top-level return",
            "S12 Python tool reachability",
            contract.section_python_tool_reachability,
            (
                TextEdit(
                    "append",
                    MOVING_ANALYZER_REL,
                    new=(
                        "\ndef mutant_dead_test_tail():\n"
                        "    return\n"
                        "    assert False\n"
                    ),
                ),
            ),
        ),
        # F9: Reintroducing the deleted compositor JSONL switch in the live
        # headed command reproduces the stale request that used to launch a run
        # with no producer behind it.
        Mutation(
            "F9 restore headed in-game jitter request",
            "S13 retired jitter CLI",
            contract.section_retired_jitter_cli_ownership,
            (
                TextEdit(
                    "replace",
                    HEADED_MOVEMENT_REL,
                    old=(
                        "    # The former compositor JSONL switches are "
                        "intentionally absent."
                    ),
                    new=(
                        '    cmd.append("--fl4359-jitter-capture")\n'
                        "    # The former compositor JSONL switches are "
                        "intentionally absent."
                    ),
                ),
            ),
        ),
        # F9: The airplane wrapper has a separate Godot command, so it needs an
        # independent mutation proving the shared ownership rule is not inferred
        # from the headed runner alone.
        Mutation(
            "F9 restore airplane in-game jitter request",
            "S13 retired jitter CLI",
            contract.section_retired_jitter_cli_ownership,
            (
                TextEdit(
                    "replace",
                    AIRPLANE_PROOF_REL,
                    old=(
                        "        if fl4359_moving_capture:\n"
                        "            cmd.extend([\n"
                    ),
                    new=(
                        '        cmd.append("--fl4359-jitter-capture")\n'
                        "        if fl4359_moving_capture:\n"
                        "            cmd.extend([\n"
                    ),
                ),
            ),
        ),
        # F9: A false guard silently revives the headed runner's default path.
        # This mutation proves the contract inspects the main entry point instead
        # of merely finding the ownership reason somewhere in the file.
        Mutation(
            "F9 disable headed external-recorder failure",
            "S13 retired jitter CLI",
            contract.section_retired_jitter_cli_ownership,
            (
                TextEdit(
                    "replace",
                    HEADED_MOVEMENT_REL,
                    old="    if not args.skip_jitter_analysis:\n",
                    new="    if False and not args.skip_jitter_analysis:\n",
                ),
            ),
        ),
        # F9: The optional airplane switch must also fail before launch. Its
        # guard is mutated separately because the headed runner cannot prove
        # this independent command surface.
        Mutation(
            "F9 disable airplane external-recorder failure",
            "S13 retired jitter CLI",
            contract.section_retired_jitter_cli_ownership,
            (
                TextEdit(
                    "replace",
                    AIRPLANE_PROOF_REL,
                    old="    if args.fl4359_jitter_capture:\n",
                    new="    if False and args.fl4359_jitter_capture:\n",
                    occurrence=1,
                ),
            ),
        ),
        # F10: Keeping an exact Product call in a dead GDScript helper must not
        # satisfy the live callback check after its real call edge is removed.
        Mutation(
            "F10 move Product dispatch text to dead host helper",
            "S7 dispatch order",
            contract.section_dispatch_ordering,
            (
                TextEdit(
                    "replace",
                    ACE_REL,
                    old=product_dispatch_block,
                    new="",
                ),
                TextEdit(
                    "append",
                    ACE_REL,
                    new=(
                        "\nfunc _mutant_dead_product_dispatch(\n"
                        "\tpush, pixel_ctx, product_compute_list, "
                        "prepared_product_dispatch\n"
                        ") -> void:\n"
                        + "".join(
                            "\t" + line if line.strip() else line
                            for line in product_dispatch_block.splitlines(
                                keepends=True
                            )
                        )
                    ),
                ),
            ),
        ),
        # F11: The first headed activation failed in Metal after Vulkan had
        # accepted the shader because a deep helper referenced this generated
        # combined-sampler name without a declaration. Reintroducing the exact
        # identifier in an uncalled helper proves the backend gate compiles all
        # helper bodies instead of checking only the reachable main closure.
        Mutation(
            "F11 reintroduce deep combined-sampler identifier",
            "S5 Metal backend",
            contract.section_shaders_compile_metal,
            (
                TextEdit(
                    "append",
                    PRODUCT_REL,
                    new=(
                        "\nfloat mutant_deep_combined_sampler_use("
                        "ivec2 vertex) {\n"
                        "\treturn texelFetch("
                        "terrain_corner_height_texSmplr, vertex, 0).r;\n"
                        "}\n"
                    ),
                ),
            ),
        ),
    )


def verify_pristine_sections(
    sandbox: Sandbox,
    mutations: tuple[Mutation, ...],
) -> list[str]:
    """Require every exercised section to be green before mutation.

    A pre-existing section failure would make every mutation in that section
    look caught. Running one pristine baseline per unique section prevents that
    false attribution.
    """

    baseline_failures: list[str] = []
    seen_sections: set[str] = set()
    for mutation in mutations:
        if mutation.section_name in seen_sections:
            continue
        seen_sections.add(mutation.section_name)
        sandbox.reset()
        checks, failures = run_section(mutation.section)
        if failures:
            baseline_failures.append(
                f"{mutation.section_name}: {len(failures)} of {checks} "
                f"baseline checks failed: {'; '.join(failures)}"
            )
    return baseline_failures


def main() -> int:
    """Run every isolated mutation and print the caught/survived ledger.

    The returned process code follows the file-header contract. The temporary
    directory is deleted in finally even when an edit anchor or section raises.
    Passing --weak-ledger limits the run to W1-W13. Passing
    --red-first-resurrection-smoke applies only the five physical-deletion
    resurrection mutants and requires each resulting section to stay red. The
    smoke mode exists for the intentional red-first interval before the source
    deletion lands; the default complete suite still requires a green pristine
    baseline so mutation attribution remains sound.
    """

    allowed_arguments = {
        "--weak-ledger",
        "--red-first-resurrection-smoke",
    }
    unknown_arguments = set(sys.argv[1:]) - allowed_arguments
    if unknown_arguments:
        print(
            "FL-4431 mutation harness: unknown arguments: "
            + ", ".join(sorted(unknown_arguments))
        )
        return 2

    temporary_name = tempfile.mkdtemp(prefix="fl4431-activation-mutants-")
    temporary_root = Path(temporary_name)
    try:
        sandbox = Sandbox(temporary_root)
        mutations = build_mutations()
        if "--weak-ledger" in sys.argv[1:]:
            # Match W1 through W13 as complete numeric labels. A prefix check
            # would accidentally include W14 when selecting W1.
            mutations = tuple(
                mutation
                for mutation in mutations
                if re.match(r"^W(?:[1-9]|1[0-3])\b", mutation.name)
            )
        resurrection_smoke = (
            "--red-first-resurrection-smoke" in sys.argv[1:]
        )
        if resurrection_smoke:
            mutations = tuple(
                mutation
                for mutation in mutations
                if mutation.name.startswith("resurrect ")
                and mutation.name
                in {
                    "resurrect Product leaf live-mask owner",
                    "resurrect Product dynamic-shadow bound owner",
                    "resurrect Product wake-lane bound owner",
                    "resurrect VSB ordered-static owner",
                    "resurrect VSB b83-minimum owner",
                }
            )
        baseline_failures = (
            [] if resurrection_smoke
            else verify_pristine_sections(sandbox, mutations)
        )
        if baseline_failures:
            print("FL-4431 mutation harness: pristine copied sections are not green.")
            for failure in baseline_failures:
                print(f"  BASELINE-FAILED {failure}")
            return 2

        caught_count = 0
        survived_count = 0
        required_survivors: list[str] = []
        over_strict_controls: list[str] = []

        for mutation in mutations:
            sandbox.reset()
            for edit in mutation.edits:
                sandbox.apply(edit)
            checks, failures = run_section(mutation.section)
            detected = bool(failures)
            if detected:
                caught_count += 1
                status = "CAUGHT"
                detail = "; ".join(failures)
            else:
                survived_count += 1
                status = "SURVIVED"
                detail = "no failure recorded"
            expectation = (
                "required damaging mutation"
                if mutation.must_be_caught
                else "expected comment-only control"
            )
            print(
                f"{status} [{mutation.section_name}] {mutation.name} "
                f"({checks} checks; {expectation}): {detail}"
            )
            if mutation.must_be_caught and not detected:
                required_survivors.append(
                    f"[{mutation.section_name}] {mutation.name}"
                )
            if not mutation.must_be_caught and detected:
                over_strict_controls.append(
                    f"[{mutation.section_name}] {mutation.name}"
                )

        print(
            "FL-4431 mutation harness summary: "
            f"{len(mutations)} mutations, {caught_count} caught, "
            f"{survived_count} survived."
        )
        if required_survivors:
            print("WEAK CHECKS - required damaging mutations that survived:")
            for survivor in required_survivors:
                print(f"  - {survivor}")
        if over_strict_controls:
            print("OVER-STRICT CHECKS - harmless controls that were caught:")
            for control in over_strict_controls:
                print(f"  - {control}")
        return 1 if required_survivors or over_strict_controls else 0
    finally:
        # The path was created by this process with a unique prefix and validated
        # as an absolute temporary directory before recursive cleanup.
        if temporary_root.is_absolute() and temporary_root.name.startswith(
            "fl4431-activation-mutants-"
        ):
            shutil.rmtree(temporary_root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
