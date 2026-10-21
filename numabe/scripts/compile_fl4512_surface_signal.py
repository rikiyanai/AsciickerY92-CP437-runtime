#!/usr/bin/env python3
"""Compile the FL-4512 Phase 1 pre-glyph surface-signal artifact.

Ownership
---------
This compiler is the OFFLINE producer of
``assets/glyphs/generated/fl4512.pre_glyph_surface_signal.v1.json`` — the
hash-pinned artifact that replaces the bg-only pre-glyph surface-signal owner
in the native renderer.  The durable color owner remains the Law-1 profile
document ``assets/glyphs/profiles/material_rendering_profiles.v1.json``; this
script only derives a continuous per-shade RGB signal from it.  The native
loader consumes the artifact read-only and never recomputes the ramp.

Signal formula (authored tonal ramp)
------------------------------------
For every material profile, every elevation row, and every shade s in 0..15:

    surface_rgb[elv][s][c] = round(bg[c] + (fg[c] - bg[c]) * s / 15)

i.e. a 16-step linear ramp from the row's authored bg color (darkest shade,
s=0 reproduces bg exactly) to its fg color (brightest shade, s=15 reproduces
fg exactly).  Rounding is exact-integer HALF-UP (no floats anywhere), so the
emitted artifact is bit-reproducible across platforms and Python versions.

Elevation row order (MUST match the native loader)
--------------------------------------------------
elv index 0..3 maps to the row keys in this exact order:

    0 = fall_lower, 1 = high, 2 = rise, 3 = flat_low

This mirrors kRowKeys in Fl4512LoadColorsInto
(engine/fl4131_runtime_harri_resolver.cpp).  Reordering either side silently
swaps elevation semantics, so the order is pinned here as a module constant
and documented at both owners.

Canonical hash contract (why sort_keys must match cJSON document order)
-----------------------------------------------------------------------
The native verifier Fl4512CanonicalHashMatches validates ``artifact_sha256``
by REPRINTING the parsed document with cJSON_PrintUnformatted (which emits
keys in DOCUMENT order with compact separators) after deleting the hash key,
then hashing those bytes.  Python computes the same hash with
``json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)``.
The two byte streams agree only when the file's document order IS plain
alphabetical order — therefore the artifact is written with
``sort_keys=True`` so cJSON's document-order reprint equals Python's
sorted-order canonical dump.  All leaf values are ints or strings, so the
cJSON float-reprint hazard fixed for FL-4589 (integral floats printing as
"0" vs "0.0") cannot occur; this compiler asserts no float survives anyway.

Fail-closed behavior
--------------------
Any missing, malformed, out-of-range, or duplicate source field rejects the
WHOLE artifact with a non-zero exit and a stable reason; no partial output is
ever written (the artifact is staged to a temp file and renamed only after
full validation and hashing succeed).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

# Schema identity of the emitted artifact.  Version bumps require a paired
# native-loader change; the loader pins both fields.
SCHEMA_ID = "fl4512.pre_glyph_surface_signal.v1"
SCHEMA_VERSION = 1

# Repo-relative identities recorded verbatim inside the artifact so a consumer
# can trace provenance without guessing.
SOURCE_PROFILE_PATH = "assets/glyphs/profiles/material_rendering_profiles.v1.json"
OUTPUT_ARTIFACT_PATH = "assets/glyphs/generated/fl4512.pre_glyph_surface_signal.v1.json"
GENERATED_BY = "scripts/compile_fl4512_surface_signal.py"

# INVARIANT: this tuple order IS the elv index meaning (0..3) and MUST equal
# kRowKeys in Fl4512LoadColorsInto (engine/fl4131_runtime_harri_resolver.cpp).
ELEVATION_ROW_KEYS = ("fall_lower", "high", "rise", "flat_low")

# INVARIANT: shade axis is exactly 16 steps (s = 0..15); the divisor below is
# SHADE_STEPS - 1 so s=0 hits bg exactly and s=15 hits fg exactly.
SHADE_STEPS = 16

# The only namespaces the native material table addresses.
VALID_NAMESPACES = ("terrain", "mesh")


class CompileFailure(ValueError):
    """Whole-artifact rejection carrying a stable receipt reason."""

    def __init__(self, reason: str, detail: str):
        super().__init__(f"{reason}: {detail}")
        self.reason = reason


def assert_no_floats(value: Any, path: str) -> None:
    # Defense-in-depth for the canonical-hash contract: every leaf must be an
    # int or a string so cJSON's reprint and Python's dump cannot diverge on
    # number formatting (the FL-4589 failure class).  bool is a subclass of
    # int in Python but prints as true/false, so it is rejected explicitly.
    if isinstance(value, bool) or isinstance(value, float):
        raise CompileFailure("float_leaf_forbidden", f"{path} = {value!r}")
    if isinstance(value, dict):
        for key, item in value.items():
            assert_no_floats(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            assert_no_floats(item, f"{path}[{index}]")


def canonical_bytes(value: Any) -> bytes:
    # EXACT mirror of the native verifier's hash input: compact separators,
    # sorted keys, raw UTF-8.  No float normalization is needed here because
    # assert_no_floats guarantees int/str leaves before hashing.
    assert_no_floats(value, "$")
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_channel(value: Any, path: str) -> int:
    # A color channel must be a genuine int (bools rejected) in 0..255.
    if isinstance(value, bool) or not isinstance(value, int):
        raise CompileFailure("channel_not_int", f"{path} = {value!r}")
    if not (0 <= value <= 255):
        raise CompileFailure("channel_out_of_range", f"{path} = {value!r}")
    return value


def parse_rgb(value: Any, path: str) -> list[int]:
    if not isinstance(value, list) or len(value) != 3:
        raise CompileFailure("rgb_shape_invalid", f"{path} must be a 3-int list")
    return [parse_channel(item, f"{path}[{index}]") for index, item in enumerate(value)]


def ramp_value(bg: int, fg: int, shade: int) -> int:
    # round(bg + (fg - bg) * shade / 15) in EXACT integer arithmetic.
    # numerator = bg*(15-s) + fg*s is always >= 0 (channels are 0..255), so
    # HALF-UP rounding of numerator/15 is (2*numerator + 15) // 30.  This
    # avoids float rounding entirely (Python round() is banker's rounding and
    # float division is platform-honest but the artifact must be
    # bit-reproducible, so ties are pinned half-up explicitly).
    numerator = bg * (SHADE_STEPS - 1 - shade) + fg * shade
    value = (2 * numerator + (SHADE_STEPS - 1)) // (2 * (SHADE_STEPS - 1))
    # Postcondition: the ramp interpolates between two in-range endpoints, so
    # the result must stay in range; violation means the formula regressed.
    if not (0 <= value <= 255):
        raise CompileFailure("ramp_out_of_range", f"bg={bg} fg={fg} s={shade} -> {value}")
    return value


def parse_material_id(value: Any, path: str) -> tuple[str, int]:
    # material_id format is "namespace:key" (e.g. "terrain:1", "mesh:200").
    if not isinstance(value, str) or value.count(":") != 1:
        raise CompileFailure("material_id_invalid", f"{path} = {value!r}")
    namespace, _, key_text = value.partition(":")
    if namespace not in VALID_NAMESPACES:
        raise CompileFailure("material_namespace_invalid", f"{path} = {value!r}")
    if not key_text.isdigit():
        raise CompileFailure("material_key_invalid", f"{path} = {value!r}")
    return namespace, int(key_text)


def compile_material(profile: Any, path: str) -> dict[str, Any]:
    if not isinstance(profile, dict):
        raise CompileFailure("profile_not_object", path)
    namespace, material_key = parse_material_id(profile.get("material_id"), f"{path}.material_id")
    colors = profile.get("colors")
    if not isinstance(colors, dict):
        raise CompileFailure("colors_missing", f"{path}.colors")
    elevation_rows = colors.get("elevation_rows")
    if not isinstance(elevation_rows, dict):
        raise CompileFailure("elevation_rows_missing", f"{path}.colors.elevation_rows")
    # The source owner authors EXACTLY the four canonical rows; extra or
    # missing keys mean the durable owner and this compiler disagree about
    # elevation semantics, which must fail closed rather than guess.
    if set(elevation_rows.keys()) != set(ELEVATION_ROW_KEYS):
        raise CompileFailure(
            "elevation_row_keys_invalid",
            f"{path}.colors.elevation_rows keys = {sorted(elevation_rows.keys())}",
        )

    surface_rgb: list[list[list[int]]] = []
    # elv index 0..3 follows ELEVATION_ROW_KEYS order (native kRowKeys order).
    for row_key in ELEVATION_ROW_KEYS:
        row = elevation_rows[row_key]
        row_path = f"{path}.colors.elevation_rows.{row_key}"
        if not isinstance(row, dict):
            raise CompileFailure("elevation_row_not_object", row_path)
        fg = parse_rgb(row.get("fg"), f"{row_path}.fg")
        bg = parse_rgb(row.get("bg"), f"{row_path}.bg")
        # 16-step authored tonal ramp: shade 0 = bg (darkest), shade 15 = fg
        # (brightest), linear in between per channel.
        shades = [
            [ramp_value(bg[c], fg[c], shade) for c in range(3)]
            for shade in range(SHADE_STEPS)
        ]
        surface_rgb.append(shades)

    return {
        "material_key": material_key,
        "profile_namespace": namespace,
        "surface_rgb": surface_rgb,
    }


def compile_artifact(repo_root: Path) -> tuple[dict[str, Any], Path]:
    source_path = repo_root / SOURCE_PROFILE_PATH
    try:
        source_raw = source_path.read_bytes()
    except OSError as error:
        raise CompileFailure("source_unreadable", f"{source_path}: {error}")
    try:
        source_doc = json.loads(source_raw)
    except json.JSONDecodeError as error:
        raise CompileFailure("source_not_json", f"{source_path}: {error}")
    if not isinstance(source_doc, dict):
        raise CompileFailure("source_root_not_object", str(source_path))
    profiles = source_doc.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        raise CompileFailure("profiles_missing", f"{source_path} profiles list")

    materials: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for index, profile in enumerate(profiles):
        entry = compile_material(profile, f"profiles[{index}]")
        identity = (entry["profile_namespace"], entry["material_key"])
        # Duplicate identities would make the native table load
        # last-writer-wins nondeterministically; reject the whole artifact.
        if identity in seen:
            raise CompileFailure("material_duplicate", f"{identity[0]}:{identity[1]}")
        seen.add(identity)
        materials.append(entry)
    # Deterministic artifact order: (profile_namespace, material_key).
    materials.sort(key=lambda entry: (entry["profile_namespace"], entry["material_key"]))

    doc: dict[str, Any] = {
        "generated_by": GENERATED_BY,
        "materials": materials,
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "source_profile_path": SOURCE_PROFILE_PATH,
        # Pin of the raw source-file bytes so any edit to the durable color
        # owner is detectable as artifact staleness.
        "source_profile_sha256": sha256_bytes(source_raw),
    }
    # Self-hash computed EXACTLY like Fl4512CanonicalHashMatches: complete
    # document WITHOUT the artifact_sha256 key, canonical compact bytes.
    doc["artifact_sha256"] = sha256_bytes(canonical_bytes(doc))
    return doc, source_path


def write_artifact(repo_root: Path, doc: dict[str, Any]) -> Path:
    output_path = repo_root / OUTPUT_ARTIFACT_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # sort_keys=True is LOAD-BEARING: the file's document order must be plain
    # alphabetical so cJSON's document-order reprint (the native hash input)
    # equals Python's sorted-order canonical dump.  See module docstring.
    text = json.dumps(doc, sort_keys=True, indent=1, ensure_ascii=False) + "\n"
    # Stage-then-rename so a validation or serialization failure never leaves
    # a partial artifact at the canonical path (fail closed, atomic publish).
    fd, staged = tempfile.mkstemp(
        dir=str(output_path.parent), prefix=".fl4512.surface_signal.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(staged, output_path)
    except BaseException:
        try:
            os.unlink(staged)
        except OSError:
            pass
        raise
    return output_path


def self_check(repo_root: Path, expected_materials: int) -> None:
    """Reload the published artifact and re-prove the canonical contract."""
    output_path = repo_root / OUTPUT_ARTIFACT_PATH
    reloaded = json.loads(output_path.read_text(encoding="utf-8"))
    unhashed = {key: value for key, value in reloaded.items() if key != "artifact_sha256"}
    recomputed = sha256_bytes(canonical_bytes(unhashed))
    if recomputed != reloaded["artifact_sha256"]:
        raise CompileFailure(
            "self_hash_mismatch", f"{reloaded['artifact_sha256']} != {recomputed}"
        )
    materials = reloaded["materials"]
    if len(materials) != expected_materials:
        raise CompileFailure(
            "material_count_mismatch", f"{len(materials)} != {expected_materials}"
        )
    for entry in materials:
        grid = entry["surface_rgb"]
        if len(grid) != 4:
            raise CompileFailure("surface_rgb_rows_invalid", entry["profile_namespace"])
        for shades in grid:
            if len(shades) != SHADE_STEPS:
                raise CompileFailure("surface_rgb_shades_invalid", entry["profile_namespace"])
            for rgb in shades:
                if len(rgb) != 3 or any(
                    isinstance(c, bool) or not isinstance(c, int) or not (0 <= c <= 255)
                    for c in rgb
                ):
                    raise CompileFailure("surface_rgb_value_invalid", repr(rgb))
    # Spot-proof: terrain:1 elv=1 ("high" row) must ramp monotonically per
    # channel from bg (shade 0) toward fg (shade 15) — direction per channel
    # follows sign(fg - bg).
    terrain1 = next(
        entry
        for entry in materials
        if entry["profile_namespace"] == "terrain" and entry["material_key"] == 1
    )
    shades = terrain1["surface_rgb"][1]
    for channel in range(3):
        series = [shades[s][channel] for s in range(SHADE_STEPS)]
        ascending = all(a <= b for a, b in zip(series, series[1:]))
        descending = all(a >= b for a, b in zip(series, series[1:]))
        if not (ascending or descending):
            raise CompileFailure("ramp_not_monotonic", f"channel {channel}: {series}")
    print(
        "self-check terrain:1 elv=1 shade samples: "
        f"s0={shades[0]} s9={shades[9]} s15={shades[15]}"
    )
    print(f"self-check OK: {expected_materials} materials, hash {recomputed}")


def serialize_artifact(doc: dict[str, Any]) -> str:
    """Return the exact canonical text write_artifact would publish.

    FL-4230: --check must be able to compare against the on-disk artifact
    WITHOUT writing, so the serialization has to live in one place that both
    modes call. Any divergence here would make --check report a false result.
    """
    # sort_keys=True is LOAD-BEARING: see write_artifact.
    return json.dumps(doc, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    # FL-4230 / FL-4512: this compiler publishes a CANONICAL artifact under
    # assets/glyphs/generated/, which the water-lane marker guards. Until this
    # parser existed the script took no arguments at all and main() wrote
    # unconditionally, so ANY invocation mutated the guarded artifact --
    # including `--help`, `--check`, and typos, which is exactly how it was
    # written by accident twice (recorded on FL-4512 and FL-4230). A mode is
    # now REQUIRED and there is deliberately no implicit default: the safe
    # reading of a bare invocation is "tell me the state", not "republish".
    parser = argparse.ArgumentParser(
        description=(
            "Compile the FL-4512 pre-glyph surface signal. Writing is guarded: "
            "pass --write explicitly."
        )
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--write", action="store_true", help="publish the canonical artifact"
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="recompute in memory and exit non-zero if the on-disk copy differs; writes nothing",
    )
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parent.parent
    try:
        doc, _ = compile_artifact(repo_root)
    except CompileFailure as failure:
        print(f"compile_fl4512_surface_signal FAILED {failure}", file=sys.stderr)
        return 1

    output_path = repo_root / OUTPUT_ARTIFACT_PATH
    if args.check:
        expected = serialize_artifact(doc)
        try:
            on_disk = output_path.read_text(encoding="utf-8")
        except OSError:
            print(f"{OUTPUT_ARTIFACT_PATH} is missing", file=sys.stderr)
            return 2
        if on_disk != expected:
            print(f"{OUTPUT_ARTIFACT_PATH} is stale", file=sys.stderr)
            return 2
        print(f"ok {OUTPUT_ARTIFACT_PATH} artifact_sha256 {doc['artifact_sha256']}")
        return 0

    try:
        output_path = write_artifact(repo_root, doc)
        self_check(repo_root, expected_materials=len(doc["materials"]))
    except CompileFailure as failure:
        print(f"compile_fl4512_surface_signal FAILED {failure}", file=sys.stderr)
        return 1
    print(f"wrote {output_path}")
    print(f"artifact_sha256 {doc['artifact_sha256']}")
    print(f"source_profile_sha256 {doc['source_profile_sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
