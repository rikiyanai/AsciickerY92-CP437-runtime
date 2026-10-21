"""FL-4689: Linux exposes the same isolated Classic product build as macOS.

The current and Classic products are explicit app-owned specs. No
CLASSIC_CP437 make flag selects product identity; the dispatcher chooses
the spec file and each spec carries its own BIN/OBJDIR/DEPDIR/LINK_MAP.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CURRENT = ROOT / "apps" / "game-fl4512" / "makefile_game"
CLASSIC = ROOT / "apps" / "game-classic" / "makefile_game_classic"

# Manifest-derived: the Current-minus-Classic closure is exactly the
# partitions a Current game selects. Derived, not reproduced.
def _manifest_set(name: str) -> set[str]:
    lines = (ROOT / "apps/fl4512-runtime/fl4512_product_sources.mk").read_text(
        encoding="utf-8"
    ).splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(name + " :="))
    values: set[str] = set()
    for line in lines[start:]:
        chunk = line.split(":=", 1)[1] if ":=" in line else line
        continued = chunk.rstrip().endswith("\\")
        values.update(chunk.replace("\\", " ").split())
        if not continued:
            break
    return values


UNICODE_OWNER_SOURCES = (
    _manifest_set("FL4512_CORE_SRCS") | _manifest_set("FL4512_PROGRAM_SRCS")
)
PORTS_ANCHOR_SOURCES = _manifest_set("FL4512_PORTS_ANCHOR")
CLASSIC_ADAPTER_SOURCE = "apps/classic-runtime/classic_product_adapter.cpp"

_VARIABLES = ("BIN", "LINK_MAP", "OBJDIR", "DEPDIR", "CPPFLAGS", "LDFLAGS", "LDLIBS", "SRCS")


def _make_database(makefile: Path, *args: str) -> str:
    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "-f",
            str(makefile),
            "SHELL=/usr/bin/true",
            *args,
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _database_variables(database: str) -> dict[str, str]:
    variables: dict[str, str] = {}
    for name in _VARIABLES:
        match = re.search(rf"^{name} :=(.*)$", database, flags=re.MULTILINE)
        assert match is not None, f"GNU Make database omitted {name}"
        variables[name] = match.group(1).strip()
    return variables


def _static_variables(makefile: Path) -> dict[str, str]:
    """Read := / += assignments straight from the spec text.

    Platform-safe fallback for hosts rejected by the spec's platform guard
    (Linux specs error out on macOS). Whitespace runs collapse like the make
    database; only $(LINK_MAP) is expanded. Other $(VAR) references
    (V8_PREFIX, BREW_PREFIX) stay literal: no assertion depends on them.
    """
    logical: list[str] = []
    pending = ""
    for line in makefile.read_text(encoding="utf-8").splitlines():
        stripped = line.rstrip()
        if stripped.endswith("\\"):
            pending += stripped[:-1] + " "
            continue
        logical.append(pending + line)
        pending = ""
    if pending:
        logical.append(pending)
    variables: dict[str, str] = {}
    for line in logical:
        match = re.match(r"^([A-Z_]+) (:?=|\+=)(.*)$", line)
        if match is None or match.group(1) not in _VARIABLES:
            continue
        name, operator, value = match.group(1), match.group(2), match.group(3).strip()
        value = re.sub(r"\s+", " ", value)
        if operator == "+=":
            variables[name] = re.sub(r"\s+", " ", (variables.get(name, "") + " " + value)).strip()
        else:
            variables[name] = value
    missing = [name for name in _VARIABLES if name not in variables]
    assert not missing, f"{makefile} omitted {missing}"
    variables["LDFLAGS"] = variables["LDFLAGS"].replace("$(LINK_MAP)", variables["LINK_MAP"])
    # Expand manifest partition selections so SRCS reflects linked files.
    parts = {
        name: _manifest_set(name)
        for name in (
            "FL4512_CORE_SRCS",
            "FL4512_PROGRAM_SRCS",
            "FL4512_PORTS_ANCHOR",
            "FL4512_TERMINAL_MAC_AUX_SRCS",
        )
    }
    words: list[str] = []
    for word in variables["SRCS"].split():
        matched = False
        for var, files in parts.items():
            if word == f"$({var})":
                words.extend(sorted(files))
                matched = True
        if not matched:
            words.append(word)
    variables["SRCS"] = " ".join(words)
    return variables


def _spec_srcs(makefile: Path) -> set[str]:
    """SRCS entries of one app spec, excluding the exclusion comment."""
    lines = makefile.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("SRCS :="))
    entries: set[str] = set()
    for line in lines[start:]:
        chunk = line.split(":=", 1)[1] if ":=" in line else line
        continued = chunk.rstrip().endswith("\\")
        chunk = chunk.replace("\\", " ")
        entries.update(chunk.split())
        if not continued:
            break
    return entries


def _make_variables(makefile: Path) -> dict[str, str]:
    if sys.platform.startswith("linux"):
        return _database_variables(_make_database(makefile, "-pn", "help"))
    return _static_variables(makefile)


def test_linux_classic_make_database_has_isolated_product_ownership() -> None:
    normal = _make_variables(CURRENT)
    classic = _make_variables(CLASSIC)

    assert normal["BIN"] == ".run/game"
    assert normal["OBJDIR"] == ".o_game"
    assert normal["DEPDIR"] == ".d_game"
    assert normal["LINK_MAP"] == ""
    assert "-DFL4683_GLYPH_PROGRAM_LOADER_ENABLED" in normal["CPPFLAGS"]
    assert "-DFL4693_PRODUCT_GLYPH_PROGRAM_ENABLED" in normal["CPPFLAGS"]

    assert classic["BIN"] == ".run/game_classic"
    assert classic["OBJDIR"] == ".o_game_classic"
    assert classic["DEPDIR"] == ".d_game_classic"
    assert classic["LINK_MAP"] == ".run/game_classic.map"
    assert "-DASCIICKER_RENDERER_CLASSIC_CP437=1" in classic["CPPFLAGS"]
    assert "-DFL4683_GLYPH_PROGRAM_LOADER_ENABLED" not in classic["CPPFLAGS"]
    assert "-DFL4693_PRODUCT_GLYPH_PROGRAM_ENABLED" not in classic["CPPFLAGS"]
    assert "-Wl,-Map,.run/game_classic.map" in classic["LDFLAGS"]

    assert normal["LDLIBS"] == classic["LDLIBS"]
    assert set(normal["SRCS"].split()) - set(classic["SRCS"].split()) == (
        UNICODE_OWNER_SOURCES | PORTS_ANCHOR_SOURCES
    )


def test_linux_classic_dry_run_targets_classic_roots_and_binary() -> None:
    if sys.platform.startswith("linux"):
        dry_run = _make_database(CLASSIC, "-Bn", "all")

        assert "-DASCIICKER_RENDERER_CLASSIC_CP437=1" in dry_run
        assert "-DFL4683_GLYPH_PROGRAM_LOADER_ENABLED" not in dry_run
        assert "-DFL4693_PRODUCT_GLYPH_PROGRAM_ENABLED" not in dry_run
        assert ".o_game_classic/platform/gl.o" in dry_run
        assert ".d_game_classic/platform/gl.Td" in dry_run
        assert "-Wl,-Map,.run/game_classic.map -o .run/game_classic" in dry_run
        assert not any(source in dry_run for source in UNICODE_OWNER_SOURCES)
        return

    text = CLASSIC.read_text(encoding="utf-8")
    assert "-DASCIICKER_RENDERER_CLASSIC_CP437=1" in text
    assert "-DFL4683_GLYPH_PROGRAM_LOADER_ENABLED" not in text
    assert "-DFL4693_PRODUCT_GLYPH_PROGRAM_ENABLED" not in text
    assert "OBJDIR := .o_game_classic" in text
    assert "DEPDIR := .d_game_classic" in text
    assert "BIN := .run/game_classic" in text
    assert "LINK_MAP := .run/game_classic.map" in text
    assert "-Wl,-Map,$(LINK_MAP)" in text
    assert not (UNICODE_OWNER_SOURCES & _spec_srcs(CLASSIC))
