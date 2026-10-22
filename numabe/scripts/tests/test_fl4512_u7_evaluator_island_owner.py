"""FL-4512: U7 evaluator-island header single-owner cut.

The U7 topology evaluator and integer scorer are current-only: every live
source consumer is a current-runtime translation unit, a guarded-nonclassic
shared section, or the presenter editor. Classic and the server reach them
only through guarded-out includes. The live owners are
apps/fl4512-runtime/engine/; engine/render/ keeps no duplicate.
Boundary only; no visual acceptance claim.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
NEW_TOPO = ROOT / "apps/fl4512-runtime/engine/fl4512_u7_topology.h"
NEW_SCORE = ROOT / "apps/fl4512-runtime/engine/fl4512_u7_score.h"
OLD_TOPO = ROOT / "engine/render/fl4512_u7_topology.h"
OLD_SCORE = ROOT / "engine/render/fl4512_u7_score.h"
SERVER_MAKEFILE = ROOT / "makefile_server"

LIVE_SOURCE_ROOTS = (
    ROOT / "engine",
    ROOT / "platform",
    ROOT / "apps" / "fl4512-runtime",
    ROOT / "editor",
    ROOT / "tests",
    ROOT / "scripts" / "tests",
)

OLD_TOKENS = (
    "engine/render/fl4512_u7_topology.h",
    "engine/render/fl4512_u7_score.h",
)


def test_u7_island_single_owner_cut_has_no_legacy_duplicate() -> None:
    """Regression: legacy engine/render headers stay deleted; one live owner each."""
    assert NEW_TOPO.is_file(), str(NEW_TOPO)
    assert NEW_SCORE.is_file(), str(NEW_SCORE)
    assert not OLD_TOPO.exists(), str(OLD_TOPO)
    assert not OLD_SCORE.exists(), str(OLD_SCORE)


def test_u7_island_definitions_live_once_in_current_runtime() -> None:
    """The evaluator entry points are defined exactly once, in the new owners."""
    topo = NEW_TOPO.read_text(encoding="utf-8")
    score = NEW_SCORE.read_text(encoding="utf-8")
    assert "static inline void Fl4512U7Topology(" in topo
    assert "static inline uint32_t Fl4512ScoreMaskV1(" in score
    assert "static inline void Fl4512U7Topology(" not in score
    assert "static inline uint32_t Fl4512ScoreMaskV1(" not in topo


def test_u7_island_consumers_use_new_include_paths() -> None:
    """Every live source consumer points at the current-runtime owner."""
    expectations = (
        (
            "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp",
            ('#include "fl4512_u7_topology.h"', '#include "fl4512_u7_score.h"'),
        ),
        (
            "apps/fl4512-runtime/engine/fl4693_final_tuple_transport.cpp",
            ('#include "fl4512_u7_topology.h"', '#include "fl4512_u7_score.h"'),
        ),
        (
            "apps/fl4512-runtime/platform/fl4683_exact_cell_selector.cpp",
            ('#include "../../engine/fl4512_u7_score.h"',),
        ),
        (
            "engine/render/render_resolve.cpp",
            ('#include "../../apps/fl4512-runtime/engine/fl4512_u7_topology.h"',),
        ),
        (
            "platform/terminal_gl_present.cpp",
            (
                '#include "../apps/fl4512-runtime/engine/fl4512_u7_topology.h"',
                '#include "../apps/fl4512-runtime/engine/fl4512_u7_score.h"',
            ),
        ),
        (
            "apps/asciicker-presenter/asciiid.cpp",
            ('#include "../apps/fl4512-runtime/engine/fl4512_u7_score.h"',),
        ),
        (
            "tests/engine_tests/fl4512_u7_topology_test.cpp",
            ('#include "../../apps/fl4512-runtime/engine/fl4512_u7_topology.h"',),
        ),
    )
    for rel, includes in expectations:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for include in includes:
            assert include in text, f"{rel} misses {include}"
        for token in OLD_TOKENS:
            assert token not in text, f"{rel} still references {token}"


def test_u7_island_shared_consumers_stay_guarded_nonclassic() -> None:
    """Shared-TU includes remain inside the nonclassic guard."""
    guard = "#if !defined(ASCIICKER_RENDERER_CLASSIC_CP437)"
    for rel, include in (
        (
            "engine/render/render_resolve.cpp",
            '#include "../../apps/fl4512-runtime/engine/fl4512_u7_topology.h"',
        ),
        (
            "platform/terminal_gl_present.cpp",
            '#include "../apps/fl4512-runtime/engine/fl4512_u7_topology.h"',
        ),
        (
            "platform/terminal_gl_present.cpp",
            '#include "../apps/fl4512-runtime/engine/fl4512_u7_score.h"',
        ),
    ):
        source = (ROOT / rel).read_text(encoding="utf-8")
        pos = source.index(include)
        assert source.rfind(guard, 0, pos) > source.rfind("#endif", 0, pos), rel


def test_u7_island_old_path_has_no_live_references() -> None:
    """No live source root still points at either legacy header path."""
    hits = []
    self_path = Path(__file__).resolve()
    for root in LIVE_SOURCE_ROOTS:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in {".h", ".cpp", ".py"}:
                continue
            if path.resolve() == self_path:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for token in OLD_TOKENS:
                if token in text:
                    hits.append(f"{path.relative_to(ROOT)}:{token}")
    assert hits == [], hits


def test_u7_island_stays_out_of_server_build() -> None:
    """The server makefile carries no registration of either header token."""
    text = SERVER_MAKEFILE.read_text(encoding="utf-8")
    assert "fl4512_u7_topology" not in text
    assert "fl4512_u7_score" not in text
