# =============================================================
# test_fl4600_inst_provenance.py
# =============================================================
# FL-4600 ASan-only SpriteInst allocation/retirement provenance instrument.
#
# Plain-language summary: the FL-4600 crash is a WASM abort in
# DeleteInst -> free on an authoritative world-item's SpriteInst. This
# diagnostic (compiled ONLY under FL4600_DIAG=asan) stamps each item SpriteInst
# with a stable allocation-sequence number at creation, logs create/retire
# events, and aborts BEFORE the free if the same allocation is retired twice.
#
# These are static source-structure tests (the repo idiom for engine
# diagnostics that cannot be exercised without a headed WASM run). They prove
# the ownership/isolation properties in the acceptance criteria without building
# or launching a proof:
#   * production index flags are unchanged (no define, no sanitizer leak),
#   * the define is present ONLY in the asan branch,
#   * exactly one allocation identity is minted per created visual,
#   * every SpriteInst retirement is routed through the provenance check,
#   * a double retirement fails visibly (abort) before the free,
#   * the production macros are true no-ops (byte-equivalent),
#   * the prior unconditional fprintf diagnostic was deleted (delete-first).

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APPEARANCE_CPP = ROOT / "engine" / "authoritative_world_item_appearance.cpp"
BUILD_WEB = ROOT / "build-web.sh"


def _extract_function_body(source: str, signature_prefix: str) -> str:
    """Return one C/C++ function body by brace-matching from a unique prefix."""
    start = source.index(signature_prefix)
    open_brace = source.index("{", start)
    depth = 0
    i = open_brace
    while i < len(source):
        c = source[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace : i + 1]
        i += 1
    raise AssertionError(f"unterminated body for {signature_prefix!r}")


def _asan_case_block(build: str) -> str:
    """Return the text of the `asan)` case arm in build-web.sh."""
    start = build.index("\nasan)")
    end = build.index(";;", start)
    return build[start:end]


def _production_case_block(build: str) -> str:
    """Return the text of the unset (production, `"")`) case arm."""
    start = build.index('\n"")')
    end = build.index(";;", start)
    return build[start:end]


# ── Production isolation ─────────────────────────────────────────────────────


def test_production_index_flags_carry_no_diagnostic() -> None:
    build = BUILD_WEB.read_text(encoding="utf-8")
    prod = _production_case_block(build)
    # The production arm must not enable the sanitizer or the diagnostic define.
    assert "-DFL4600_DIAG_ASAN" not in prod
    assert "-fsanitize" not in prod
    assert 'INDEX_SAFETY="-s ASSERTIONS=1 -s STACK_SIZE=8388608"' in prod


def test_diag_define_present_only_under_asan() -> None:
    build = BUILD_WEB.read_text(encoding="utf-8")
    # The define must appear on exactly one actual compiler-flag line (an
    # INDEX_SAFETY= assignment), not counting explanatory comments.
    flag_lines = [
        ln
        for ln in build.splitlines()
        if "-DFL4600_DIAG_ASAN" in ln
        and "INDEX_SAFETY=" in ln
        and not ln.lstrip().startswith("#")
    ]
    assert len(flag_lines) == 1
    # ...and it is inside the asan case arm, alongside ASan; never in production.
    asan = _asan_case_block(build)
    assert "-DFL4600_DIAG_ASAN" in asan
    assert "-fsanitize=address" in asan
    assert "-DFL4600_DIAG_ASAN" not in _production_case_block(build)


def test_production_macros_are_noops() -> None:
    source = APPEARANCE_CPP.read_text(encoding="utf-8")
    # The #else (define-absent) branch must make both hooks true no-ops so the
    # production binary is byte-equivalent to no instrument at all.
    assert "#define FL4600_PROV_CREATE(ptr, item, slot, world) ((void)0)" in source
    assert "#define FL4600_PROV_RETIRE(ptr, stage)             ((void)0)" in source
    # The whole instrument (table, helpers, logging) lives under the guard.
    assert "#ifdef FL4600_DIAG_ASAN" in source
    assert "#endif // FL4600_DIAG_ASAN" in source


# ── Delete-first: the prior diagnostic owner is gone ─────────────────────────


def test_prior_unconditional_fprintf_diagnostic_removed() -> None:
    source = APPEARANCE_CPP.read_text(encoding="utf-8")
    # The old always-on retirement log is deleted; only the comment noting its
    # removal may reference the string. No live fprintf of it remains.
    assert 'fprintf(\n\t\t\t\t\t"[FL-4600] inactive item visual delete' not in source
    assert 'stderr,\n\t\t\t\t\t"[FL-4600] inactive item visual delete' not in source


# ── One allocation identity per created visual ───────────────────────────────


def test_one_allocation_identity_per_created_visual() -> None:
    source = APPEARANCE_CPP.read_text(encoding="utf-8")
    # Exactly one create hook, placed at the SpriteInst creation site.
    assert source.count("FL4600_PROV_CREATE(vis->inst, ai->item_id, i, world)") == 1
    create_idx = source.index("vis->inst = CreateInst(")
    hook_idx = source.index("FL4600_PROV_CREATE(vis->inst, ai->item_id, i, world)")
    assert create_idx < hook_idx  # registered immediately after creation
    # The registrar mints exactly one monotonic identity per call.
    reg = _extract_function_body(source, "void Fl4600ProvRegisterCreate(")
    assert reg.count("++g_fl4600_alloc_seq") == 1


# ── Every retirement routed through the provenance check ─────────────────────


def test_every_sprite_retirement_routes_through_check() -> None:
    source = APPEARANCE_CPP.read_text(encoding="utf-8")
    # Every free of a SpriteInst is immediately preceded by the check. Count
    # parity plus adjacency proves no delete bypasses the instrument.
    n_free = source.count("DeleteInst(vis->inst)")
    n_check = source.count("FL4600_PROV_RETIRE(vis->inst, stage_code)")
    assert n_free == 2  # the two static teardown helpers
    assert n_check == n_free
    for m in _find_all(source, "DeleteInst(vis->inst)"):
        window = source[max(0, m - 120) : m]
        assert "FL4600_PROV_RETIRE(vis->inst, stage_code)" in window


def test_mesh_inst_deletes_are_not_misclassified_as_sprite() -> None:
    # mesh_inst is a MeshInst, out of the SpriteInst provenance scope; it must
    # NOT be wrapped by the sprite retire hook.
    source = APPEARANCE_CPP.read_text(encoding="utf-8")
    for m in _find_all(source, "DeleteInst(vis->mesh_inst)"):
        window = source[max(0, m - 120) : m]
        assert "FL4600_PROV_RETIRE(vis->mesh_inst" not in window


# ── Double retirement fails visibly before free ──────────────────────────────


def test_double_retire_fails_visibly_before_free() -> None:
    source = APPEARANCE_CPP.read_text(encoding="utf-8")
    check = _extract_function_body(source, "void Fl4600ProvCheckRetire(")
    # The guard: not-live => log DOUBLE-RETIRE then abort(), all inside the
    # check, which is called BEFORE DeleteInst at both sites (see the retire
    # test above). So the abort happens before any free.
    assert "if (!rec->live)" in check
    assert "abort();" in check
    assert "DOUBLE-RETIRE" in check
    # The abort must precede the `rec->live = false` bookkeeping (a first, valid
    # retirement) — i.e. the double-retire is rejected, not recorded then freed.
    assert check.index("abort();") < check.index("rec->live = false;")
    # And the retire path itself must not call DeleteInst / free (fail BEFORE).
    assert "DeleteInst" not in check


def _find_all(haystack: str, needle: str):
    out = []
    start = 0
    while True:
        i = haystack.find(needle, start)
        if i < 0:
            return out
        out.append(i)
        start = i + len(needle)
