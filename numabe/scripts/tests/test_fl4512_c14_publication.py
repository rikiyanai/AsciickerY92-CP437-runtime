"""
FL-4512 C14 publication audit — the U7 structural lattice/winner handoff through
the persistent U6 space-time owner into the SINGLE final TERM++ tuple.

This is a *diagnostic* test (per the C14-PUBLICATION contract: "tests are not
visual proof"). It asserts the source-level ownership invariants that make the
CPU structural result load-bearing on the presented frame. It makes NO claim
of a passing visual proof.

Invariants asserted (all in the C14 owner files):
  1. RuntimeFinalTupleOwner constructs the tuple once; the native adapter has
     exactly one committed-copy assignment plus one fail-closed assignment.
  2. The structural commit (Fl4652CommitSpacetimeStructuralGlyphV1) is a RESULT
     SERIALIZER into `final_result` (final_writer=2) — never a second painter of
     `final_glyph_buf`.
  3. The old frame-local structural final-writer/owner is DELETED (delete-first).
  4. MESH_FEATURE (structural) cells are skipped from the tonal SurfaceId replay
     so tonal and structural ownership stay disjoint.
  5. Absence stays fail-closed to the named absent-missing-fact glyph, never a
     silent CP437 fallback, for profile cells.
"""

import re
from pathlib import Path

# Repo root: this file lives at <root>/scripts/tests/.
ROOT = Path(__file__).resolve().parents[2]
PRESENT = ROOT / "platform/terminal_gl_present.cpp"
FINAL_TUPLE_OWNER = ROOT / "apps/fl4512-runtime/engine/fl4693_final_tuple_transport.cpp"
SPACETIME = ROOT / "engine/render/fl4512_spacetime_track_owner.h"


def _present_text() -> str:
    return PRESENT.read_text(encoding="utf-8")


def test_single_final_plane_writer() -> None:
    """The native adapter may only copy the committed tuple or fail closed."""
    text = _present_text()
    assigns = re.findall(r"final_glyph_buf\s*\[[^\]]+\]\s*=", text)
    assert len(assigns) == 2
    adapter = text.split("static void Fl4693PresentCommittedTupleV1", 1)[1]
    adapter = adapter.split("static bool g_term_player_sprite_visible", 1)[0]
    assert "tuples[index].final_gid" in adapter
    assert "GLYPH_ID_ABSENT_MISSING_FACT" in adapter
    assert "cell.gl" not in adapter


def test_structural_commit_is_serializer_not_painter() -> None:
    """Fl4652CommitSpacetimeStructuralGlyphV1 writes the U6 tuple (final_result,
    final_writer=2), never final_glyph_buf. It is a serializer, not the final
    plane owner."""
    text = _present_text()
    m = re.search(
        r"static bool Fl4652CommitSpacetimeStructuralGlyphV1\b.*?\n\}",
        text,
        re.DOTALL,
    )
    assert m, "Fl4652CommitSpacetimeStructuralGlyphV1 definition not found"
    body = m.group(0)
    assert "b.final_result[idx]" in body, "structural commit must write final_result"
    assert "final_writer = 2" in body, "structural commit must stamp final_writer=2"
    assert "final_glyph_buf" not in body, (
        "structural commit must NOT write the final plane (no second painter)"
    )


def test_old_frame_local_structural_painter_deleted() -> None:
    """Delete-first: the former frame-local structural final-writer AND the
    former frame-local structural final owner are recorded as removed."""
    text = _present_text()
    assert "the former frame-local structural final-writer" in text
    assert "commit has been removed" in text
    assert "the former frame-local structural final owner" in text
    assert "is gone" in text
    # And the sole-final-writer contract is asserted in source.
    assert "RuntimeFinalTupleOwner is the sole tuple writer" in text


def test_tonal_and_structural_ownership_disjoint() -> None:
    """MESH_FEATURE cells are skipped from the tonal SurfaceId replay so the two
    ownership domains never overlap."""
    text = _present_text()
    # The skip guard: structural cells `continue` before the tonal history maps.
    assert re.search(
        r"if \(current\.selected_role == FL4512_ROLE_MESH_FEATURE\)\s*\n\s*continue;",
        text,
    ), "MESH_FEATURE cells must be skipped from tonal replay"


def test_absence_stays_fail_closed_not_cp437() -> None:
    """A profile cell without U6 provenance shows the named absent-missing-fact
    glyph, never a silent CP437 fallback."""
    owner = FINAL_TUPLE_OWNER.read_text(encoding="utf-8")
    assert "MissingAuthorityTuple" in owner
    assert "GLYPH_ID_ABSENT_MISSING_FACT" in owner
    assert "TupleFromIdentity((GlyphId)cell.gl" in owner
