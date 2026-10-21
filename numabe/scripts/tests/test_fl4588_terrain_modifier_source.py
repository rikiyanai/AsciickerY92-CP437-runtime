# =============================================================
# test_fl4588_terrain_modifier_source.py
# =============================================================
# FL4588-TERRAIN-MODIFIER-GRAPH-CUTOVER (FL-4588): SOURCE-STRUCTURE guards for the
# native (editor/asciiid.cpp) side of the four typed Sculpt modifier transactions.
#
# WHY SOURCE-STRUCTURE (not a compiled unit test) FOR THESE
# --------------------------------------------------------
# The native Stamp mode-2 Gaussian, the global probe writer, and the cell-inspection
# ray projection all live inside asciiid.cpp — a translation unit that only links
# against the full SDL/GL engine, so a focused unit harness of those functions is
# impractical. Following the established FL-4588 pattern (see
# test_fl4588_html_architecture.py, which asserts on asciiid.cpp text), these guards
# assert the STRUCTURE of the fix is present in source:
#   - the deleted native modifier writers are physically gone (delete-first),
#   - the blur/sharpen owner PREVALIDATES samples before any mutation (atomic reject),
#   - the mode-2 Gaussian populates the changed-patch set and gates undo/upload on a
#     real change (honest change accounting),
#   - the global Shift+RightClick probe_z writer is gone and only the typed
#     CommitAsciiidTerrainProbeHeight assigns probe_z from a live user action,
#   - the cell-inspection owner projects to the probe_z plane and emits the
#     terrain_probe_plane source kind that ONLY patch-topology accepts.
#
# The RUNTIME behaviour of the service dispatch + gate is proven separately in
# test_fl4588_terrain_modifier_service.py against the real service TU.
# =============================================================
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NATIVE = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text(encoding="utf-8")
SERVICE = (ROOT / "apps/asciicker-presenter/asciiid_application_service.cpp").read_text(
    encoding="utf-8"
)
TRANSPORT = (ROOT / "web/asciiid-authoring-shell/src/transport.ts").read_text(
    encoding="utf-8")
FRAME = (ROOT / "web/asciiid-authoring-shell/src/FrameView.tsx").read_text(
    encoding="utf-8")
EDITOR_STATE = (ROOT / "apps/asciicker-presenter/editor_state.h").read_text(encoding="utf-8")


def _slice(text: str, start_anchor: str, end_anchor: str) -> str:
    """Return the substring from start_anchor up to the next end_anchor.

    Used to scope an assertion to ONE function body so ordering checks
    (e.g. "prevalidation appears before URDO_Open") can't be satisfied by
    matching text in an unrelated function elsewhere in the file.
    """
    start = text.index(start_anchor)
    end = text.index(end_anchor, start + len(start_anchor))
    return text[start:end]


# ---- Delete-first: the native Sculpt modifier writers are physically gone ----

def test_implicit_shift_stamp_mode_selection_is_deleted():
    # The Stamp forced_mode default no longer reads the live Shift key.
    assert "stamp_mode = (forced_mode > 0) ? forced_mode : 1;" in NATIVE
    # The old implicit selection is not live code (only referenced in the
    # deletion comment, which spells it out inside parentheses).
    assert "KeysDown[LSHIFT] ? 2 : 1) is DELETED" in NATIVE
    # Every occurrence of the deleted selection expression must be inside a comment
    # line — never a live assignment reintroducing the global Shift read.
    for line in NATIVE.splitlines():
        if "stamp_mode = KeysDown[LSHIFT]" in line:
            assert line.strip().startswith("//"), \
                f"live reintroduction of deleted Shift stamp selection: {line}"


def test_global_shift_rightclick_probe_writer_is_deleted():
    # FL4588-GLOBAL-PROBE-OLD-OWNER: the pre-mode-block Shift+RightClick probe_z
    # mutation (probe_z = round(hit.z) + TerrainProbe) is gone entirely.
    assert "probe_z = (int)round(hit[2])" not in NATIVE
    assert "FL4588-GLOBAL-PROBE-OLD-OWNER" in NATIVE
    # The MouseDown[1] mutation branch is removed; only a read-only hover preview
    # (writing the ephemeral br_probe ring, not probe_z) remains.
    assert "br_probe[0] = (float)round(hit[2]);" in NATIVE


def test_deleted_owner_comments_present_for_each_modifier():
    # Each replaced native modifier leaves a delete-first breadcrumb.
    assert "native Alt patch-topology" in NATIVE          # Alt create/delete owner
    assert "native Ctrl+Shift" in NATIVE                  # Ctrl+Shift height sample
    assert "diagonal-flip owner (URDO_Diag" in NATIVE     # Ctrl diagonal flip
    assert "implicit native Shift selection" in NATIVE    # Shift-in-Stamp select


def test_probe_z_sole_user_action_owner_is_the_typed_committer():
    # The typed height-sample committer is the sole live user-action probe_z writer.
    assert "CommitAsciiidTerrainProbeHeight" in NATIVE
    # No native branch reconstructs the deleted round(hit.z) probe_z write.
    assert "probe_z = (int)round(hit" not in NATIVE


def test_creating_dead_owner_state_is_hard_deleted():
    # FL4588-CREATING-DEAD-OWNER: the Alt patch-topology `creating` state has no
    # remaining producer, so its field, macro, and consumer branch are all gone.
    assert "int creating = 0;" not in EDITOR_STATE
    assert "#define creating" not in NATIVE
    assert "if (creating)" not in NATIVE
    # The dead disjunct is removed from the paint/hover guard.
    assert "if (painting || creating)" not in NATIVE
    # Only the deletion breadcrumb naming may survive (never a live assignment).
    for line in NATIVE.splitlines():
        stripped = line.strip()
        if "creating =" in stripped and "painting" not in stripped:
            assert stripped.startswith("//"), f"live creating assignment: {line}"


def test_diag_flipped_dead_owner_state_is_hard_deleted():
    # The Ctrl diagonal one-shot guard `diag_flipped` is hard-deleted: no field,
    # no macro, and no per-frame reset assignment remain as live code.
    assert "bool diag_flipped = false;" not in EDITOR_STATE
    assert "#define diag_flipped" not in NATIVE
    for line in NATIVE.splitlines():
        if "diag_flipped = false" in line:
            assert line.strip().startswith("//"), \
                f"live diag_flipped reset survives: {line}"


def test_material_elevation_stale_height_probe_comment_is_corrected():
    # FL4588-STALE-OWNER-COMMENT: the Material Elevation cutover comment must NOT
    # still claim the Ctrl+Shift height-probe branch is kept / height probing stays
    # native — that writer is deleted and the typed Sculpt sampler is sole owner.
    assert "intentionally KEPT" not in NATIVE
    assert "height probing stays native" not in NATIVE
    assert "FL4588-STALE-OWNER-COMMENT correction" in NATIVE


# ---- Blur/sharpen atomic rejection (FL4588-BLUR-ATOMIC-REJECTION) ------------

def test_blur_prevalidates_samples_before_any_mutation():
    body = _slice(
        NATIVE,
        "static AsciiidTerrainSculptBlurSharpenCommitV1 CommitAsciiidTerrainSculptBlurSharpen",
        "static AsciiidTerrainDiagonalFlipCommitV1 CommitAsciiidTerrainDiagonalFlip",
    )
    assert "FL4588-BLUR-ATOMIC-REJECTION" in body
    # The finiteness rejection must appear BEFORE the first global mutation and
    # BEFORE the URDO group opens — otherwise a bad sample could leave a
    # half-applied stroke in history/revisions.
    reject_at = body.index("terrain_sculpt_blur_sharpen_sample_rejected")
    first_global_at = body.index("brush_shape = paint_brush_shape")
    urdo_open_at = body.index("URDO_Open();")
    assert reject_at < first_global_at, "sample reject must precede global mutation"
    assert reject_at < urdo_open_at, "sample reject must precede URDO_Open"
    # The mutation loop no longer restores globals / closes a group mid-stroke: the
    # only URDO_Close is the single group close after the loop.
    assert body.count("URDO_Open();") == 1
    assert body.count("URDO_Close();") == 1


# ---- Blur/sharpen change accounting (FL4588-BLUR-CHANGE-ACCOUNTING) ----------

def test_mode2_gaussian_populates_changed_patch_set_and_gates_upload():
    # Scope to the Stamp mode-2 (blur/sharpen) branch.
    body = _slice(NATIVE, "FL4588-BLUR-CHANGE-ACCOUNTING", "void Palettize(")
    # A real cell change lazily snapshots the patch for undo (deduped across the
    # stroke) AND inserts it into the changed-patch set the committer counts.
    assert "if (next != prev)" in body
    assert "g_painted_patches.insert(p).second" in body
    assert "URDO_Patch(p);" in body
    # The height texture is re-uploaded ONLY when the patch actually changed.
    assert "if (patch_changed)" in body
    assert "UpdateTerrainHeightMap(p);" in body
    # The patch is no longer snapshotted unconditionally before the cell loop.
    pre_loop = body[: body.index("for (int sy = 0; sy <= HEIGHT_CELLS; sy++)")]
    assert "URDO_Patch(p);" not in pre_loop, \
        "mode-2 must not snapshot the patch before any real change"


# ---- Cell-inspection probe-plane projection (FL4588-TOPOLOGY-CREATE-REACHABILITY)

def test_native_editor_scene_probe_projection_owner_is_deleted():
    assert "FL4588-TOPOLOGY-CREATE-REACHABILITY" not in NATIVE
    assert 'source_kind = "terrain_probe_plane";' not in NATIVE


def test_transport_type_declares_probe_plane_kind():
    # The TS inspection contract enumerates the projected kind.
    assert '"terrain_probe_plane"' in TRANSPORT


def test_patch_topology_effect_is_retained_but_has_no_canvas_dispatch():
    assert "AsciiidApplicationService::CommitTerrainPatchTopology" in SERVICE
    assert "terrain_probe_plane" not in SERVICE
    assert "submitRuntimePhysicalIntent(" not in FRAME


# ---- Physical-input join resolves the modifier chords (FL4588-MODIFIER-PHYSICAL-JOIN)

def test_runtime_intent_owner_maps_modifier_chords_without_browser_resolver():
    # Browser canvas gesture interpretation was deleted. FrameView submits raw
    # modifiers and RuntimeIntentOwner resolves them against the registry graph.
    assert not (
        ROOT / "web/asciiid-authoring-shell/src/canvasEventResolver.ts"
    ).exists()
    assert "submitRuntimePhysicalIntent(" not in FRAME
    registry = (
        ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.json"
    ).read_text(encoding="utf-8")
    assert '"chord": "Ctrl+Shift"' in registry
    assert '"status": "PROOF_OPEN"' in registry
