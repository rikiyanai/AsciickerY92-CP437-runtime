# =============================================================
# test_fl4588_material_profile_urdo_source.py
# =============================================================
# FL4588-MATERIAL-PROFILE-URDO-CUTOVER (FL-4588): SOURCE-STRUCTURE guards for the
# canonical Material Look (FL-4260 qualified profile) history cutover.
#
# WHY SOURCE-STRUCTURE (not a compiled unit test)
# -----------------------------------------------
# The two owners of this cutover live in translation units that only link against
# the full SDL/GL editor engine:
#   - editor/urdo.cpp    (the canonical URDO history owner + the new batch command)
#   - editor/asciiid.cpp (the profiles serializer, the atomic batch commit, the
#                         typed committers, and the native ImGui panel)
# A focused harness of those functions would have to stub the entire terrain/world/
# render engine, so — following the established FL-4588 pattern (see
# test_fl4588_terrain_modifier_source.py, which asserts on asciiid.cpp text) — these
# guards assert the STRUCTURE of the fix is present in source. They encode, as
# executable checks, every invariant the review corrections 99A/99B/99C/99D/99E
# demanded, so a regression that reintroduces a forbidden pattern fails CI.
#
# The RUNTIME behaviour (a rejected Undo/Redo leaving cursor/revision/state/bytes
# unchanged, a palette spread persisting all-or-nothing) remains PROOF_OPEN: it is
# only closed by a sole-Chrome headed HTML receipt, not by these diagnostics.
# =============================================================
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NATIVE = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text(encoding="utf-8")
URDO_CPP = (ROOT / "apps/asciicker-presenter/urdo.cpp").read_text(encoding="utf-8")
URDO_H = (ROOT / "apps/asciicker-presenter/urdo.h").read_text(encoding="utf-8")
SERVICE = (ROOT / "apps/asciicker-presenter/asciiid_application_service.cpp").read_text(encoding="utf-8")


def _slice(text: str, start_anchor: str, end_anchor: str) -> str:
    """Return the substring from start_anchor up to the next end_anchor.

    Scopes an assertion to ONE function body so an ordering/structure check can't
    be satisfied by matching text in an unrelated function elsewhere in the file.
    """
    start = text.index(start_anchor)
    end = text.index(end_anchor, start + len(start_anchor))
    return text[start:end]


# ---- Delete-first: the private one-step recovery owner is physically gone -------

def test_private_profile_undo_arrays_are_deleted():
    # The two private per-profile snapshot arrays no longer exist as declarations.
    assert "static Fl4260ProfileEditState g_fl4260_profile_undo_state_by_profile" not in NATIVE
    assert "static bool g_fl4260_profile_undo_valid_by_profile" not in NATIVE
    # And no code writes them (the migrated committers replaced those two lines).
    assert "g_fl4260_profile_undo_state_by_profile[profile_index] = before;" not in NATIVE
    assert "g_fl4260_profile_undo_valid_by_profile[profile_index] = true;" not in NATIVE


def test_private_snapshot_and_undo_helpers_are_deleted():
    # The private snapshot/undo helper FUNCTIONS are gone (only prose mentioning the
    # deletion may remain, and only inside // comment lines).
    assert "static bool Fl4260SnapshotProfileEdit(" not in NATIVE
    assert "static bool Fl4260UndoProfileEdit(" not in NATIVE
    for name in ("Fl4260SnapshotProfileEdit", "Fl4260UndoProfileEdit"):
        for line in NATIVE.splitlines():
            if name in line:
                assert line.strip().startswith("//"), \
                    f"live reference to deleted {name}: {line}"


def test_native_undo_last_change_widget_is_deleted():
    # The visible face of the private owner (the rq147undo one-step widget) is gone.
    # (A deletion breadcrumb may still name it inside a // comment.)
    assert 'ImGui::Button("Undo last change##rq147undo")' not in NATIVE


# ---- The one canonical URDO command + fallible-Do threading (F1) ----------------

def test_urdo_command_and_public_api_exist():
    assert "CMD_FL4260_PROFILE" in URDO_CPP
    assert "struct URDO_Fl4260ProfileBatch : URDO" in URDO_CPP
    # Public API is the prepare/link/discard trio owning a WHOLE addressed batch.
    assert "URDO_Fl4260ProfileBatch* URDO_Fl4260ProfilePrepare(int count" in URDO_H
    assert "URDO_Fl4260ProfileBatch* URDO_Fl4260ProfilePrepare(int count" in URDO_CPP


def test_do_and_cursor_functions_return_bool():
    # URDO::Do, URDO_Group::Do, and public URDO_Undo/URDO_Redo now propagate success.
    assert "bool URDO::Do(bool un)" in URDO_CPP
    assert "bool URDO_Group::Do(bool un)" in URDO_CPP
    assert "bool URDO_Undo(int max_depth)" in URDO_CPP
    assert "bool URDO_Redo(int max_depth)" in URDO_CPP
    assert "bool URDO_Undo(int max_depth);" in URDO_H
    assert "bool URDO_Redo(int max_depth);" in URDO_H


def test_dispatcher_rolls_back_revision_on_rejected_step():
    # URDO::Do snapshots revision, and restores it when the subtype reports failure,
    # so a rejected fallible step leaves the monotonic revision unchanged (F1).
    body = _slice(URDO_CPP, "bool URDO::Do(bool un)", "void URDO::Free()")
    assert "const uint64_t revision_before = revision;" in body
    assert "if (!ok)" in body
    assert "revision = revision_before;" in body
    assert "case CMD_FL4260_PROFILE: ok = ((URDO_Fl4260ProfileBatch*)this)->Do(un);" in body


def test_undo_leaf_branch_returns_false_without_moving_cursor():
    # The single-leaf apply branch of URDO_Undo returns false on a rejected Do()
    # WITHOUT advancing redo/undo — cursor + revision stay put (F1).
    body = _slice(URDO_CPP, "bool URDO_Undo(int max_depth)", "bool URDO_Redo(int max_depth)")
    assert "if (!undo->Do(true))" in body
    assert "return false;" in body
    # The empty path (nothing to undo) must report false, not true (review 99C item 2).
    assert "bool advanced = false;" in body
    assert "return advanced;" in body
    assert "return true;" not in body.split("bool advanced = false;")[1]


def test_redo_unwind_uses_do_false_direction():
    # Review 99C item 3: the grouped redo unwind must redo in the FORWARD direction
    # (Do(false)), not Do(true). Scope to the redo function's unwind loop.
    body = _slice(URDO_CPP, "bool URDO_Redo(int max_depth)", "void URDO_Open()")
    assert "bool advanced = false;" in body
    unwind = _slice(body, "while (stack_depth > max_depth)", "return advanced;")
    assert "redo->Do(false);" in unwind
    assert "redo->Do(true);" not in unwind


# ---- Standalone-only + fail-safe validation (review 99C item 1 + 5) --------------

def test_profile_command_is_standalone_and_validated():
    # Prepare validates + allocates BEFORE any durable commit (prepare-before-persist).
    body = _slice(URDO_CPP, "URDO_Fl4260ProfileBatch* URDO_Fl4260ProfilePrepare(",
                  "void URDO_Fl4260ProfileLink(")
    # (1) never nested in an open group — the sole fallible command is top-level.
    assert "assert(!group_open);" in body
    assert "if (group_open)" in body
    # (5) count cap 8, pointers, stride, duplicate keys AND indices, allocations checked.
    assert "count <= 0 || count > 8 || !profile_index || !profile_namespace" in body
    assert "if (stride == 0)" in body
    assert "material_key[i] == material_key[j]" in body   # duplicate qualified key reject
    assert "profile_index[i] == profile_index[j]" in body  # duplicate flattened index reject
    assert "if (!node || !idx || !ns || !mat || !blob)" in body  # allocation-success check


def test_group_do_has_no_reapply_compensation():
    # Review 99A: the group applies children and reports success — it must NOT
    # reapply/compensate (the only fallible command is never placed in a group).
    body = _slice(URDO_CPP, "bool URDO_Group::Do(bool un)", "void URDO_PatchUpdateHeight::Open")
    assert "bool ok = true;" in body
    assert "return ok;" in body
    # No compensation loop that re-runs Do in the opposite direction on failure.
    assert "// re-appl" not in body.lower()


# ---- Composite batch owner: one durable commit, no compensation (99A/99B) --------

def test_single_document_batch_serializer_and_commit_exist():
    # The per-profile node mutation is factored out so the batch fills N nodes into
    # ONE parsed root before emitting a single staged document.
    assert "static bool Fl4260ApplyProfileStateToRoot(cJSON* root" in NATIVE
    assert "static bool Fl4260BuildProfileBatchBytes(const ProfileKeyV1* keys, int count" in NATIVE
    assert "static bool Fl4260CommitProfileBatch(const ProfileKeyV1* keys, int count" in NATIVE
    # The retired single-profile wrapper is gone; every typed edit reaches the batch
    # owner through prepare-before-install history.
    assert "Fl4260SaveProfileEdit(" not in NATIVE


def test_batch_commit_writes_the_document_exactly_once():
    # Anchor on the definition's own comment (a forward declaration of the same name
    # exists near the top of the file, so a bare-signature anchor would mis-slice).
    body = _slice(NATIVE,
                  "// FL4588-MATERIAL-PROFILE-URDO-CUTOVER: the ONE persistence owner for a whole",
                  "// FL4588-MATERIAL-PROFILE-URDO-CUTOVER: URDO bridge hooks")
    # Exactly one staged temp + one durable commit for the whole batch.
    assert body.count("mkstemp(") == 1
    assert body.count("Fl4512CompileAndCommitProfileArtifact(") == 1
    # Serializes the whole batch (not a per-profile save loop).
    assert "Fl4260BuildProfileBatchBytes(keys, count" in body
    assert "Fl4260SaveProfileEdit(" not in body   # no recursion / no per-profile save


def test_swap_publish_restores_all_on_rejected_commit():
    # The URDO bridge swaps the WHOLE batch, commits once, and on rejection restores
    # every live state (no durable/publication change) — the F1 runtime contract.
    body = _slice(NATIVE, "bool Fl4260ProfileUndoSwapPublishBatch(int count, const int* profile_index,",
                  "static bool Fl4260CommitProfileBatchWithHistory(")
    assert "Fl4260CommitProfileBatch(keys, count, reason, sizeof(reason))" in body
    assert body.count("Fl4260CommitProfileBatch(") == 1   # single commit for the batch
    # Save prev, install targets, restore prev on failure, symmetric-swap on success.
    assert "prev[i] = g_fl4260_profile_edit_state_by_profile[profile_index[i]];" in body
    assert "g_fl4260_profile_edit_state_by_profile[profile_index[i]] = prev[i];" in body
    assert "restore_targets[i] = prev[i];" in body


# ---- Every typed committer records canonical URDO history -----------------------

def test_single_profile_committers_record_urdo_history():
    # Nine single-profile committers persist AND record canonical history through the
    # count-1 prepare-before-persist owner (no separate post-commit recording).
    assert NATIVE.count(
        "Fl4260CommitProfileBatchWithHistory(&profile_key, 1, &before,") == 9


def _multi_profile_committer_is_atomic(fn_anchor: str, end_anchor: str, fail_reason: str):
    body = _slice(NATIVE, fn_anchor, end_anchor)
    # ONE prepare-before-persist commit+link owner call; restore ALL in-memory states on
    # rejection. History is prepared before the durable commit and linked on success.
    assert body.count(
        "Fl4260CommitProfileBatchWithHistory(batch_keys, n, batch_before,") == 1
    assert "batch_candidate" in body
    # No per-profile save loop and no direct raw commit (both go through the owner).
    assert "Fl4260SaveProfileEdit(" not in body
    assert "Fl4260CommitProfileBatch(batch_keys" not in body
    assert fail_reason in body


def test_palette_spread_is_one_atomic_composite():
    _multi_profile_committer_is_atomic(
        "static AsciiidPaletteSpreadCommitV1 CommitAsciiidPaletteSpread(",
        "static AsciiidMaterialScoreCommitV1 CommitAsciiidMaterialScoreReset(",
        "palette_spread_commit_failed")


def test_palette_assign_is_one_atomic_composite():
    # Review 99B: palette assign, like spread, is one composite transaction.
    _multi_profile_committer_is_atomic(
        "static AsciiidPaletteAssignCommitV1 CommitAsciiidPaletteAssignNextUnused(",
        "static AsciiidPaletteColorApplyCommitV1 CommitAsciiidPaletteColorApply(",
        "palette_assign_commit_failed")


def test_glyph_catalog_uses_addressed_candidate_lane_history_owner():
    # FL-4588: catalog add/remove must honor lane_id through CommitCandidateLane.
    # The retired Env11 flat-pool path ignored lane_id and is physically absent.
    assert 'else if (op == "glyph_pool_add" || op == "glyph_pool_remove")' not in NATIVE
    assert '"glyph_pool_add", "glyph"' not in SERVICE
    assert '"glyph_pool_remove", "glyph"' not in SERVICE
    routes = _slice(SERVICE, "static const CandRoute kCandidateRoutes[] = {",
                    "for (const CandRoute& route : kCandidateRoutes)")
    assert '"glyph.catalog.add_to_active_pool"' in routes
    assert '"add_candidate_to_lane", "add", true' in routes
    assert '"glyph.catalog.remove_from_active_pool"' in routes
    assert '"remove_candidate_from_lane", "remove", true' in routes
    assert "return CommitCandidateLane(" in SERVICE


# ---- History undo/redo surface a rejected re-commit as fail-closed --------------

def test_history_committers_surface_publish_failure():
    undo = _slice(NATIVE, "static AsciiidHistoryCommitV1 CommitAsciiidHistoryUndo(",
                  "static AsciiidHistoryCommitV1 CommitAsciiidHistoryRedo(")
    assert "undo_step_ok = URDO_Undo(0);" in undo
    assert '"history_undo_publish_failed"' in undo
    redo = _slice(NATIVE, "static AsciiidHistoryCommitV1 CommitAsciiidHistoryRedo(",
                  "// FL-4588: HTML material-profile select committer.")
    assert "redo_step_ok = URDO_Redo(0);" in redo
    assert '"history_redo_publish_failed"' in redo


# ---- Native-writer ZERO CENSUS (delete-first, reviews 99D + 99E) ----------------

def test_dead_shared_helpers_are_hard_deleted():
    # Role-lane helpers (99D) and preset/theme appliers (99D) are gone as definitions.
    for name in ("Fl4260ReplaceRoleLaneRange", "Fl4260AddRoleLaneGlyph",
                 "Fl4260RemoveRoleLaneGlyph", "Fl4260ClearRoleLane",
                 "Fl4260ClearTonalRoleLanes", "Fl4260ApplyProfileEditFromPreset",
                 "AsciiidApplyExtendedPresetToActiveMaterial",
                 "ApplyAsciiidExtendedPresetToMaterial", "Fl4260ApplyThemeToProfile"):
        assert f"static bool {name}(" not in NATIVE and f"static int {name}(" not in NATIVE \
            and f"static void {name}(" not in NATIVE, f"{name} definition survives"
        # Any remaining mention must be inside a // comment (deletion breadcrumb).
        for line in NATIVE.splitlines():
            if name in line:
                assert line.strip().startswith("//"), f"live reference to deleted {name}: {line}"


def test_no_native_material_look_mutation_writers_remain():
    # The zero-census guard: no native ImGui path may still snapshot the deleted
    # private owner, apply a preset, or (review 99E) load/hydrate profile state on
    # panel visibility or a Reload button. Only // comment mentions may survive.
    forbidden = ("Fl4260SnapshotProfileEdit", "Fl4260UndoProfileEdit",
                 "Fl4260ApplyProfileEditFromPreset")
    for token in forbidden:
        for line in NATIVE.splitlines():
            if token in line:
                assert line.strip().startswith("//"), f"live native writer survives: {line}"
    # Review 99E: the render-time panel autoload owner is gone.
    assert "static int s_autoloaded_profile" not in NATIVE
    # Review 99E: the Reload button no longer calls the loader (display-only text).
    # (A deletion breadcrumb may still name it inside a // comment.)
    assert 'ImGui::Button("Reload saved look##rq147load")' not in NATIVE


def test_deferred_autosave_owner_is_hard_deleted():
    for retired in (
        "Fl4260ApplyProfileDirectEdit",
        "g_fl4260_autosave_pending_profile_key",
        "Fl4512AutosaveStateV1",
        "g_fl4512_autosave_state",
        "Fl4512ServiceQueuedProfileAutosave",
        "FL4512_DUMP_AUTOSAVE_STATE",
        "profile_transaction_pending",
        "persist.autosave_status",
    ):
        assert retired not in NATIVE, retired


# ---- Prepare-before-persist / link-after-success (review 99F) -------------------

def test_urdo_exposes_prepare_link_discard():
    # The command lifecycle is split so allocation happens BEFORE the durable commit.
    for sig in ("URDO_Fl4260ProfilePrepare(int count, const int* profile_index,",
                "void URDO_Fl4260ProfileLink(URDO_Fl4260ProfileBatch* handle)",
                "void URDO_Fl4260ProfileDiscard(URDO_Fl4260ProfileBatch* handle)"):
        assert sig in URDO_CPP, sig
        assert sig.split("(")[0].split()[-1] in URDO_H
    # The old single void entry that allocated AFTER the commit is gone.
    assert "void URDO_Fl4260Profile(int count" not in URDO_CPP
    assert "void URDO_Fl4260Profile(int count" not in URDO_H


def test_prepare_does_not_link_or_move_revision():
    prep = _slice(URDO_CPP, "URDO_Fl4260ProfileBatch* URDO_Fl4260ProfilePrepare(",
                  "void URDO_Fl4260ProfileLink(")
    # Prepare validates + allocates only; it must NOT purge redo, bump revision, or
    # append to the undo chain (that is Link's job, after the durable commit).
    assert "PurgeRedo()" not in prep
    assert "++revision" not in prep
    assert "undo = " not in prep
    # count is capped at the product max of 8 BEFORE any size multiplication.
    assert "count > 8" in prep
    # Link performs exactly those bookkeeping steps.
    link = _slice(URDO_CPP, "void URDO_Fl4260ProfileLink(", "void URDO_Fl4260ProfileDiscard(")
    assert "PurgeRedo();" in link and "++revision;" in link and "undo = handle;" in link


def test_commit_with_history_prepares_before_persist():
    body = _slice(NATIVE, "static bool Fl4260CommitProfileBatchWithHistory(",
                  "// FL-4260 RQ-147 LOAD")
    # Order: prepare -> candidate install -> commit -> discard/link.
    prepare_at = body.index("URDO_Fl4260ProfilePrepare(")
    install_at = body.index("g_fl4260_profile_edit_state_by_profile[idx[i]] = candidate_states[i]")
    commit_at = body.index("Fl4260CommitProfileBatch(keys, count")
    discard_at = body.index("URDO_Fl4260ProfileDiscard(hist)")
    link_at = body.index("URDO_Fl4260ProfileLink(hist)")
    assert prepare_at < install_at < commit_at < discard_at
    assert commit_at < link_at, "link only after a successful commit"
    # count-1 and multi-profile committers alike route through this one owner.
    assert NATIVE.count("Fl4260CommitProfileBatchWithHistory(") >= 11


def test_no_post_durable_history_recording_helper():
    # The old post-commit recorder that allocated after the durable write is gone.
    assert "Fl4260RecordProfileHistory" not in NATIVE


# ---- Batch validation (review 99F) ----------------------------------------------

def test_swap_publish_validates_index_and_uniqueness_before_live_state():
    body = _slice(NATIVE, "bool Fl4260ProfileUndoSwapPublishBatch(int count, const int* profile_index,",
                  "// FL4588-MATERIAL-PROFILE-URDO-CUTOVER (review 99F): commit an addressed batch")
    assert "count > 8" in body
    # Each flattened index must equal Fl4512FlattenProfileKey(namespace, material_key).
    assert "Fl4512FlattenProfileKey(kbuf[i]) != idx" in body
    # Duplicate indices AND duplicate qualified keys are rejected.
    assert "profile_index[j] == idx" in body
    # The validation loop runs BEFORE any live-slot install.
    validate_at = body.index("Fl4512FlattenProfileKey(kbuf[i]) != idx")
    install_at = body.index("g_fl4260_profile_edit_state_by_profile[profile_index[i]] = restore_targets[i];")
    assert validate_at < install_at


def test_batch_serializer_rejects_duplicate_keys_and_caps_count():
    body = _slice(NATIVE, "static bool Fl4260BuildProfileBatchBytes(const ProfileKeyV1* keys, int count,",
                  "static bool Fl4260BuildProfileEditBytes(")
    assert "count > 8" in body
    assert '"duplicate profile key in batch"' in body


# ---- Atomicity wording is precise (review 99F) ----------------------------------

def test_commit_wording_does_not_overclaim_atomic_rename():
    # The precise guarantee is stated; the overclaims are gone.
    header = _slice(NATIVE, "// FL4588-MATERIAL-PROFILE-URDO-CUTOVER: the ONE persistence owner for a whole",
                    "static bool Fl4260CommitProfileBatch(const ProfileKeyV1* keys, int count,")
    # FL-4588 review 99F: the precise guarantee names two durable replacements with
    # hard-link rollback and a fail-shut crash mismatch. Normalize wrapped comment
    # lines ("\n// ") to single spaces so multi-word phrases survive line wrapping.
    flat = header.replace("\n// ", " ")
    assert "TWO durable replacements" in flat
    assert "hard-link rollback" in flat
    assert ("fails cl" + "osed") in flat   # fail-shut crash-mismatch wording
    # 'one atomic rename' / 'whole-pair crash-atomic' may appear ONLY as an explicit
    # negation ("NOT ..."), never as a bare claim.
    assert 'NOT "one atomic rename"' in flat
    assert "whole-pair crash-atomic" in flat and "NOT a whole-pair" in flat
    # The retired bare-overclaim phrasings are gone from the committed comments.
    assert "the only durable write on this path" not in NATIVE
    assert "this atomic rename is the only point" not in NATIVE


# ---- FL-3955 enforcement comments preserved -------------------------------------

def test_fl3955_comment_count_preserved():
    # The cutover must not drop any FL-3955 enforcement comment. HEAD carried 20.
    assert NATIVE.count("FL-3955") == 20
