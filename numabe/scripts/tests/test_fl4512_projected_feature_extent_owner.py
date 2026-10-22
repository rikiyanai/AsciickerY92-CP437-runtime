"""FL-4512: projected-feature-extent header single-owner cut.

The ABI-B17 admission header is current-only: its sole source consumer is
the current-runtime Harri resolver companion, Classic reaches it only
through guarded-out includes, and the server forbids the resolver chain.
The live owner is apps/fl4512-runtime/engine/; engine/render/ keeps no
duplicate. Boundary only; no visual acceptance claim.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
NEW = ROOT / "apps/fl4512-runtime/engine/fl4512_projected_feature_extent.h"
OLD = ROOT / "engine/render/fl4512_projected_feature_extent.h"
RESOLVER_H = ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.h"
CLASSIC_PIN = ROOT / "apps/asciiid-classic/pinned/engine/render/fl4512_projected_feature_extent.h"
SERVER_MAKEFILE = ROOT / "makefile_server"

LIVE_SOURCE_ROOTS = (
    ROOT / "engine",
    ROOT / "platform",
    ROOT / "apps" / "fl4512-runtime",
    ROOT / "editor",
    ROOT / "scripts" / "tests",
)


def test_projected_feature_extent_single_owner_cut_has_no_legacy_duplicate() -> None:
    """Regression: legacy engine/render header stays deleted; one live owner."""
    assert NEW.is_file(), str(NEW)
    assert not OLD.exists(), str(OLD)
    assert not CLASSIC_PIN.exists(), str(CLASSIC_PIN)


def test_projected_feature_extent_consumer_uses_local_include() -> None:
    """The sole source consumer includes the header locally, not by old path."""
    text = RESOLVER_H.read_text(encoding="utf-8")
    assert '#include "fl4512_projected_feature_extent.h"' in text
    assert "engine/render/fl4512_projected_feature_extent.h" not in text


def test_projected_feature_extent_old_path_has_no_live_references() -> None:
    """No live source root still points at the legacy header path."""
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
            if "engine/render/fl4512_projected_feature_extent.h" in text:
                hits.append(str(path.relative_to(ROOT)))
    assert hits == [], hits


def test_projected_feature_extent_stays_out_of_server_build() -> None:
    """The server makefile carries no registration of the header token."""
    text = SERVER_MAKEFILE.read_text(encoding="utf-8")
    assert "fl4512_projected_feature_extent" not in text
