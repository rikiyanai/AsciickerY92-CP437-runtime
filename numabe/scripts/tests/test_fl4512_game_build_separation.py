"""FL-4512: Classic and current games are explicit app-owned build products.

The root conditional build owner (makefile_game{,_mac} with CLASSIC_CP437) is
deleted. The Makefile dispatcher selects one app-owned spec per product; no
live build spec decides product identity with a CLASSIC_CP437 conditional.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PRODUCT_MANIFEST = ROOT / "apps/fl4512-runtime/fl4512_product_sources.mk"


def _manifest_list(name: str) -> set[str]:
    """Expand one manifest partition into a file set."""
    lines = PRODUCT_MANIFEST.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(name + " :="))
    values: set[str] = set()
    for line in lines[start:]:
        chunk = line.split(":=", 1)[1] if ":=" in line else line
        continued = chunk.rstrip().endswith("\\")
        values.update(chunk.replace("\\", " ").split())
        if not continued:
            break
    return values


def _expand_manifest_refs(entries: set[str]) -> set[str]:
    """Resolve $(FL4512_*) spec references through the manifest."""
    text = PRODUCT_MANIFEST.read_text(encoding="utf-8")
    out: set[str] = set()
    for entry in entries:
        if entry == "$(FL4512_CORE_SRCS)":
            out |= _manifest_list("FL4512_CORE_SRCS")
        elif entry == "$(FL4512_PROGRAM_SRCS)":
            out |= _manifest_list("FL4512_PROGRAM_SRCS")
        elif entry == "$(FL4512_PORTS_ANCHOR)":
            out |= _manifest_list("FL4512_PORTS_ANCHOR")
        elif entry == "$(FL4512_TERMINAL_MAC_AUX_SRCS)":
            out |= _manifest_list("FL4512_TERMINAL_MAC_AUX_SRCS")
        elif entry.startswith("$("):
            raise AssertionError(f"unexpanded manifest var {entry}")
        else:
            out.add(entry)
    return out

CURRENT_SPECS = (
    ROOT / "apps" / "game-fl4512" / "makefile_game",
    ROOT / "apps" / "game-fl4512" / "makefile_game_mac",
)
CLASSIC_SPECS = (
    ROOT / "apps" / "game-classic" / "makefile_game_classic",
    ROOT / "apps" / "game-classic" / "makefile_game_classic_mac",
)

# The excluded unicode owners are exactly the manifest partitions a Current
# game selects; the Classic adapter (apps/classic-runtime) is allowed in
# Classic specs and is not a member. Derived, not reproduced.
EXCLUDED_UNICODE_OWNERS = (
    _manifest_list("FL4512_CORE_SRCS") | _manifest_list("FL4512_PROGRAM_SRCS")
)
PORTS_ANCHOR_SOURCES = _manifest_list("FL4512_PORTS_ANCHOR")
CLASSIC_ADAPTER_SOURCE = "apps/classic-runtime/classic_product_adapter.cpp"
# The make-level product flag is gone. The -DASCIICKER_RENDERER_CLASSIC_CP437=1
# compile define is not a product conditional; engine sources key off it.
PRODUCT_FLAG_PATTERNS = (
    "CLASSIC_CP437 ?=",
    "$(CLASSIC_CP437)",
    "ifeq ($(CLASSIC_CP437)",
    "ifneq ($(CLASSIC_CP437)",
)
# CLASSIC_CP437=1 only counts as a make-variable override when it is not part
# of the -DASCIICKER_RENDERER_CLASSIC_CP437=1 compile define.
PRODUCT_FLAG_OVERRIDE = re.compile(r"(?<![A-Z_])CLASSIC_CP437=1")


def _srcs_entries(makefile: Path) -> set[str]:
    """SRCS := block plus any SRCS += lines, with manifest vars expanded."""
    lines = makefile.read_text(encoding="utf-8").splitlines()
    start = next(
        i for i, line in enumerate(lines) if line.startswith("SRCS :=")
    )
    entries: set[str] = set()
    for line in lines[start:]:
        chunk = line.split(":=", 1)[1] if ":=" in line else line
        continued = chunk.rstrip().endswith("\\")
        chunk = chunk.replace("\\", " ")
        entries.update(chunk.split())
        if not continued:
            break
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("SRCS +="):
            entries.update(stripped.split("+=", 1)[1].split())
    return _expand_manifest_refs(entries)


def test_no_root_conditional_owner_remains() -> None:
    assert not (ROOT / "makefile_game").exists()
    assert not (ROOT / "makefile_game_mac").exists()


def test_dispatcher_selects_distinct_app_specs() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "make -f apps/game-fl4512/makefile_game$(MAC_SUFFIX)" in makefile
    assert (
        "make -f apps/game-classic/makefile_game_classic$(MAC_SUFFIX)"
        in makefile
    )


def test_no_product_flag_conditional_in_live_specs() -> None:
    paths = [ROOT / "Makefile", *CURRENT_SPECS, *CLASSIC_SPECS]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for pattern in PRODUCT_FLAG_PATTERNS:
            assert pattern not in text, f"{path.name} still owns {pattern}"
        assert PRODUCT_FLAG_OVERRIDE.search(text) is None, (
            f"{path.name} still passes CLASSIC_CP437=1"
        )


def test_current_specs_own_full_unicode_union() -> None:
    for spec in CURRENT_SPECS:
        text = spec.read_text(encoding="utf-8")
        entries = _srcs_entries(spec)
        assert EXCLUDED_UNICODE_OWNERS <= entries, spec.name
        assert PORTS_ANCHOR_SOURCES <= entries, spec.name
        assert CLASSIC_ADAPTER_SOURCE not in entries, spec.name
        assert "BIN := .run/game\n" in text
        assert "LINK_MAP :=\n" in text
        assert "OBJDIR := .o_game\n" in text
        assert "DEPDIR := .d_game\n" in text
        assert "-DFL4683_GLYPH_PROGRAM_LOADER_ENABLED" in text
        assert "-DFL4693_PRODUCT_GLYPH_PROGRAM_ENABLED" in text
        assert "-DASCIICKER_RENDERER_CLASSIC_CP437=1" not in text


def test_classic_specs_exclude_unicode_owners() -> None:
    for spec in CLASSIC_SPECS:
        text = spec.read_text(encoding="utf-8")
        entries = _srcs_entries(spec)
        assert not (EXCLUDED_UNICODE_OWNERS & entries), spec.name
        assert "BIN := .run/game_classic" in text
        assert "LINK_MAP := .run/game_classic.map" in text
        assert "OBJDIR := .o_game_classic" in text
        assert "DEPDIR := .d_game_classic" in text
        assert "-DASCIICKER_RENDERER_CLASSIC_CP437=1" in text
        assert "-DFL4683_GLYPH_PROGRAM_LOADER_ENABLED" not in text
        assert "-DFL4693_PRODUCT_GLYPH_PROGRAM_ENABLED" not in text


def test_classic_adapter_ownership_is_symmetric_per_platform() -> None:
    pairs = (
        (CURRENT_SPECS[0], CLASSIC_SPECS[0]),
        (CURRENT_SPECS[1], CLASSIC_SPECS[1]),
    )
    for current, classic in pairs:
        current_entries = _srcs_entries(current)
        classic_entries = _srcs_entries(classic)
        # Each product owns exactly its adapter: the only symmetric
        # difference outside shared sources is the two adapters plus the
        # manifest-selected unicode owners.
        assert classic_entries - current_entries == {CLASSIC_ADAPTER_SOURCE}
        assert current_entries - classic_entries == (
            EXCLUDED_UNICODE_OWNERS | PORTS_ANCHOR_SOURCES
        )


def test_audit_game_classic_still_routes_and_covers_exclusion() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "audit-game-classic: game-classic" in makefile
    assert ".run/game_classic.map" in makefile
    for owner in EXCLUDED_UNICODE_OWNERS:
        assert Path(owner).stem in makefile, owner


def test_dry_run_dispatch_selects_app_specs() -> None:
    # Classic is the default product: bare `game` resolves to game-classic.
    # Current is explicit via the game-fl4512 target.
    for target, spec_fragment in (
        ("game", "apps/game-classic/makefile_game_classic"),
        ("game-fl4512", "apps/game-fl4512/makefile_game"),
        ("game-classic", "apps/game-classic/makefile_game_classic"),
    ):
        result = subprocess.run(
            ["make", "--no-print-directory", "-n", target],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assert spec_fragment in result.stdout, result.stdout
    current = subprocess.run(
        ["make", "--no-print-directory", "-n", "game-fl4512"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    classic = subprocess.run(
        ["make", "--no-print-directory", "-n", "game-classic"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert current.stdout != classic.stdout
    default = subprocess.run(
        ["make", "--no-print-directory", "-n", "game"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert default.stdout == classic.stdout
