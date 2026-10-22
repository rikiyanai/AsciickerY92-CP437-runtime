#!/usr/bin/env python3
# =============================================================================
# generate_fl4131_profile_marker_contract.py
# =============================================================================
# WHAT THIS PRODUCES
# ------------------
# One machine-generated receipt describing the "fail marker" the renderer draws
# when it cannot honestly draw a real glyph:
#
#     assets/glyphs/generated/fl4131.profile_marker_contract.v1.json
#
# WHAT A "FAIL MARKER" IS
# -----------------------
# The renderer picks a text character (a "glyph") for every cell of the screen.
# Sometimes it genuinely cannot: the cell asked for a material that has no
# authored policy, or the policy exists but no glyph reached the cell, or the
# cell belongs to a sandbox area deliberately excluded from closure.
#
# In all three cases the renderer must NOT silently invent a plausible-looking
# character, because that would hide the failure inside a picture that looks
# fine. Instead it draws an exclamation mark in a reserved colour pair -- a
# deliberately conspicuous marker meaning "this cell failed, and here is why".
# The three cases use three DIFFERENT reserved colour pairs so the failure kind
# is readable straight off the screen.
#
# WHY THIS GENERATOR EXISTS
# -------------------------
# The coverage gate used to read a JSON file that a person wrote by hand in
# June, at
#     docs/research/ascii/verification/fl4260/2026-06-15-rq153c-diagnostic-glyph-receipt.json
# That file was a claim ABOUT the runtime, not evidence FROM it. It could drift
# from the engine without anything noticing, and it was in fact deleted, which
# left the gate permanently failing on a missing document instead of reporting
# anything about the renderer.
#
# This generator replaces that hand-written claim with a measurement. It
# compiles the engine translation unit that defines the marker producer, calls
# the real C++ function `Fl4131ProfileMarkerProducer` once per marker class, and
# writes down exactly what the engine returned. Nothing in the output is typed
# in by hand; if the engine changes, re-running this changes the receipt.
#
# HOW IT WORKS
# ------------
#   1. Compile engine/fl4131_runtime_harri_resolver.cpp to an object file.
#      This is the translation unit that DEFINES the marker producer.
#   2. Compile and link a tiny probe program that calls that producer for every
#      marker class, and also asks the engine which colour pairs it considers
#      reserved.
#   3. Run the probe and parse the values it prints.
#   4. Write those values, plus a content hash of the two engine source files
#      they came from, into the generated receipt.
#
# A NOTE ON LINKING
# -----------------
# The marker producer is a small self-contained function, but it lives in a
# large translation unit next to code that reaches into the whole running game
# (the material table, the installed render hook, the JSON loaders). A linker
# normally takes a whole object file or none of it, so a naive link fails on
# dozens of symbols the producer itself never touches.
#
# The fix is to compile each function into its own section and let the linker
# discard everything unreachable from the probe's `main`. What remains is the
# real compiled producer and nothing else -- no stand-ins, no stubbed globals,
# nothing hand-written standing in for engine behaviour. If the producer ever
# did depend on game state, the link would fail loudly rather than quietly
# producing a receipt that describes something other than the engine.
#
# Run with:
#     python3 scripts/generate_fl4131_profile_marker_contract.py
#     python3 scripts/generate_fl4131_profile_marker_contract.py --check
#
# `--check` regenerates into memory and compares against the committed receipt
# without writing, so a gate can detect drift.
# =============================================================================

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

# The engine files this receipt is derived from. Their content hashes go into
# the receipt so a reader can tell whether it still matches the source.
PRODUCER_SRC = REPO_ROOT / "engine/fl4131_runtime_harri_resolver.cpp"
PRODUCER_HDR = REPO_ROOT / "engine/fl4131_runtime_harri_resolver.h"
GLYPH_ID_HDR = REPO_ROOT / "engine/glyph_id.h"

OUTPUT = REPO_ROOT / "assets/glyphs/generated/fl4131.profile_marker_contract.v1.json"

SCHEMA = "fl4131.profile_marker_contract.v1"

# Include directories needed to compile the resolver translation unit. These
# mirror what the normal build passes; the resolver pulls headers from the
# engine root, the render subdirectory, the platform layer, and thirdparty.
INCLUDE_DIRS = [".", "engine", "engine/render", "platform", "thirdparty"]

# Split every function and variable into its own section so the linker is able
# to drop the ones the probe never reaches.
SECTION_FLAGS = ["-ffunction-sections", "-fdata-sections"]


def dead_strip_flag() -> str:
    """Linker flag that discards unreachable sections.

    Apple's linker spells this '-dead_strip'; the GNU linker spells it
    '--gc-sections'. Selecting by platform keeps both toolchains supported.
    """
    return "-Wl,-dead_strip" if sys.platform == "darwin" else "-Wl,--gc-sections"

# The marker classes to exercise. The names come from the engine enum
# Fl4131ProfileMarkerClass; the numeric values are NOT written here -- the probe
# prints whatever the engine's own enum resolves to.
MARKER_CLASS_NAMES = [
    "FL4131_MARKER_MISSING_POLICY",
    "FL4131_MARKER_MISSING_GLYPH",
    "FL4131_MARKER_DIAGNOSTIC",
]

# The probe program. It includes the engine header (so it sees the real enum
# and the real struct) and calls the real producer. Every number it prints comes
# out of the engine, not out of this file.
#
# It prints three kinds of line:
#   CLASS|<name>|<enum value>|<glyph>|<fg>|<bg>|<marker_class echoed>|<reason>
#   SENTINEL|<the non-renderable glyph id used for marker cells>
#   RESERVED|<fg>|<bg>|<1 if the engine calls this pair reserved, else 0>
PROBE_SRC = r"""
// Probe: asks the engine what its fail markers are. Generated, do not edit.
#include "fl4131_runtime_harri_resolver.h"
#include "glyph_id.h"
#include <cstdio>

// Print one marker class exactly as the engine's producer returns it.
static void emit(const char* name, int cls)
{
    struct Fl4131ProfileMarker m = Fl4131ProfileMarkerProducer(cls);
    printf("CLASS|%s|%d|%u|%u|%u|%d|%s\n",
           name, cls,
           (unsigned)m.glyph, (unsigned)m.fg, (unsigned)m.bg,
           m.marker_class, m.reason ? m.reason : "");
}

// Ask the engine whether a given foreground/background pair is one of the
// reserved marker pairs. A real authored glyph must never be allowed to use
// one, so the gate checks both that reserved pairs ARE rejected and that an
// ordinary pair is NOT.
static void probe_reserved(int fg, int bg)
{
    printf("RESERVED|%d|%d|%d\n", fg, bg,
           Fl4131ProfileColorPairIsReservedMarker(fg, bg) ? 1 : 0);
}

int main(void)
{
    emit("FL4131_MARKER_MISSING_POLICY", FL4131_MARKER_MISSING_POLICY);
    emit("FL4131_MARKER_MISSING_GLYPH",  FL4131_MARKER_MISSING_GLYPH);
    emit("FL4131_MARKER_DIAGNOSTIC",     FL4131_MARKER_DIAGNOSTIC);

    // The glyph id written into a marker cell. It is deliberately outside the
    // renderable range so it can never be mistaken for real content.
    printf("SENTINEL|%u\n", (unsigned)GLYPH_ID_DIAGNOSTIC_MISSING);

    // The three reserved pairs, then one ordinary pair as a negative control.
    probe_reserved(FL4131_MARKER_FG_MISSING_POLICY, FL4131_MARKER_BG_MISSING_POLICY);
    probe_reserved(FL4131_MARKER_FG_MISSING_GLYPH,  FL4131_MARKER_BG_MISSING_GLYPH);
    probe_reserved(FL4131_MARKER_FG_DIAGNOSTIC,     FL4131_MARKER_BG_DIAGNOSTIC);
    probe_reserved(7, 0);
    return 0;
}
"""


def sha256_of(path: Path) -> str:
    """Content hash of one file, so the receipt can be tied to exact sources."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find_compiler() -> str | None:
    """First available C++ compiler, preferring clang++ as the build does."""
    for candidate in ("clang++", "g++"):
        if shutil.which(candidate):
            return candidate
    return None


def run_probe() -> list[str]:
    """Compile and run the probe; return the lines it printed.

    Raises RuntimeError with the compiler output when anything fails, because a
    silent fallback here would let the receipt be produced without the engine
    ever being consulted -- exactly the failure this generator exists to end.
    """
    compiler = find_compiler()
    if compiler is None:
        raise RuntimeError("no C++ compiler found (looked for clang++ and g++)")

    includes: list[str] = []
    for directory in INCLUDE_DIRS:
        includes += [f"-I{REPO_ROOT / directory}"]

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        probe_cpp = tmpdir / "fl4131_marker_probe.cpp"
        probe_cpp.write_text(PROBE_SRC, encoding="utf-8")
        producer_obj = tmpdir / "fl4131_runtime_harri_resolver.o"
        binary = tmpdir / "fl4131_marker_probe"

        # Step 1: compile the translation unit that DEFINES the producer, with
        # each function in its own section so the linker can discard the rest.
        compile_producer = subprocess.run(
            [compiler, "-std=c++17", *SECTION_FLAGS,
             "-c", str(PRODUCER_SRC), "-o", str(producer_obj)] + includes,
            capture_output=True, text=True)
        if compile_producer.returncode != 0:
            raise RuntimeError(f"compiling the producer failed:\n{compile_producer.stderr[-3000:]}")

        # Step 2: compile the probe, link it against that object, and drop every
        # section the probe cannot reach. Only the producer survives.
        build_probe = subprocess.run(
            [compiler, "-std=c++17", *SECTION_FLAGS,
             str(probe_cpp), str(producer_obj), "-o", str(binary),
             dead_strip_flag()] + includes,
            capture_output=True, text=True)
        if build_probe.returncode != 0:
            raise RuntimeError(f"linking the probe failed:\n{build_probe.stderr[-3000:]}")

        # Step 3: run it.
        result = subprocess.run([str(binary)], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"probe exited {result.returncode}:\n{result.stderr[-2000:]}")

    return [line for line in result.stdout.splitlines() if line.strip()]


def build_contract() -> dict[str, Any]:
    """Turn the probe's printed lines into the receipt structure."""
    lines = run_probe()

    markers: dict[str, Any] = {}
    sentinel: int | None = None
    reserved: list[dict[str, Any]] = []

    for line in lines:
        parts = line.split("|")
        kind = parts[0]

        if kind == "CLASS":
            # name, enum value, glyph, fg, bg, echoed class, reason
            _, name, value, glyph, fg, bg, echoed, reason = parts
            markers[name] = {
                "marker_class_value": int(value),
                # The producer echoes the class back; if it ever disagreed with
                # what was asked for, that is a real defect and must be visible.
                "marker_class_echoed": int(echoed),
                "glyph_byte": int(glyph),
                "glyph_char": chr(int(glyph)),
                "fg": int(fg),
                "bg": int(bg),
                "reason": reason,
            }

        elif kind == "SENTINEL":
            sentinel = int(parts[1])

        elif kind == "RESERVED":
            reserved.append({
                "fg": int(parts[1]),
                "bg": int(parts[2]),
                "engine_calls_reserved": bool(int(parts[3])),
            })

    if sentinel is None:
        raise RuntimeError("probe did not report the marker sentinel glyph id")
    missing = [name for name in MARKER_CLASS_NAMES if name not in markers]
    if missing:
        raise RuntimeError(f"probe did not report marker classes: {missing}")

    return {
        "schema": SCHEMA,
        "generator": "scripts/generate_fl4131_profile_marker_contract.py",
        "derived_from": {
            "producer_function": "Fl4131ProfileMarkerProducer",
            "reserved_pair_predicate": "Fl4131ProfileColorPairIsReservedMarker",
            "sources": {
                "engine/fl4131_runtime_harri_resolver.cpp": sha256_of(PRODUCER_SRC),
                "engine/fl4131_runtime_harri_resolver.h": sha256_of(PRODUCER_HDR),
                "engine/glyph_id.h": sha256_of(GLYPH_ID_HDR),
            },
        },
        "method": (
            "The engine translation unit defining the marker producer is compiled "
            "and linked into a probe that calls the real function once per marker "
            "class. Every value below is what the engine returned. Nothing is "
            "transcribed by hand."
        ),
        # The glyph id written into a marker cell. Deliberately outside the
        # renderable range, so a marker can never be confused with content.
        "marker_sentinel_glyph_id": sentinel,
        "marker_sentinel_hex": f"0x{sentinel:08X}",
        "markers": markers,
        "reserved_pair_probe": reserved,
        "presenter_contract": (
            "A cell that fails to resolve is presented as the marker glyph in its "
            "class colour pair, carrying the sentinel glyph id rather than any "
            "renderable identity. The CPU resolver and the GPU presenter both read "
            "marker identity from this single producer so the two agree byte for "
            "byte. No live profile palette pair may equal a reserved marker pair, "
            "so a real glyph can never masquerade as a failure marker."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="regenerate in memory and compare with the committed receipt; write nothing")
    parser.add_argument("--json", action="store_true", help="print the receipt")
    args = parser.parse_args()

    try:
        contract = build_contract()
    except RuntimeError as exc:
        print(f"[FL-4131] marker contract generation FAILED: {exc}", file=sys.stderr)
        return 3

    rendered = json.dumps(contract, indent=2, sort_keys=True) + "\n"

    if args.check:
        if not OUTPUT.exists():
            print(f"[FL-4131] receipt missing: {OUTPUT.relative_to(REPO_ROOT)}", file=sys.stderr)
            return 2
        committed = OUTPUT.read_text(encoding="utf-8")
        if committed != rendered:
            print("[FL-4131] committed marker receipt DIFFERS from the engine", file=sys.stderr)
            print("  regenerate with: python3 scripts/generate_fl4131_profile_marker_contract.py",
                  file=sys.stderr)
            return 2
        print("[FL-4131] marker receipt matches the engine")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)}")
    if args.json:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
