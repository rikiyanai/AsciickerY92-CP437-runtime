#!/usr/bin/env python3
"""FL-4260 RQ-147 — Material Rendering Profile schema/unit validator (fail-closed).

This is the "passes schema/unit/compile" gate for the profile data model. It runs
without external deps (manual structural checks; uses jsonschema if installed for
the formal pass too). Exit 0 = valid; non-zero = fail closed with reasons.

Invariants beyond the JSON Schema:
  - profile_state is direct live profile state; review receipts are evidence.
  - terrain key must be terrain:<int>; raw RGB / unkeyed material cannot bind.
  - mesh key is allowed structurally but is DEFERRED (RQ-159): a mesh profile may
    stay blocked in v1.
  - [G6] no live color fg/bg pair may equal a reserved marker pair
    (231/196 MISSING_POLICY, 226/16 MISSING_GLYPH, 201/16 DIAGNOSTIC).
  - role_buckets has all six lanes; Edge buckets with empty candidates must carry
    a blocked reason (Law 6 fail-closed: absence is visible).
  - assignment sidecar: every key is terrain:<int> (or mesh: deferred); each
    assignment references an existing profile idempotency_marker; map_hash present.
"""
import glob
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILES = os.path.join(REPO, "assets/glyphs/profiles/material_rendering_profiles.v1.json")
SCHEMA = os.path.join(REPO, "assets/glyphs/schema/material_rendering_profiles.v1.schema.json")
SIDECAR_GLOB = os.path.join(REPO, "assets/a3d/*.material_profile_assignments.v1.json")

RESERVED_MARKER_PAIRS = {(231, 196), (226, 16), (201, 16)}
REQUIRED_PROFILE_FIELDS = [
    "material_id", "source_class", "display_name", "preset_seed_id",
    "seed_provenance", "idempotency_marker", "profile_state",
    "source_table_hash", "review_receipt_id", "colors", "glyph_pools",
    "role_buckets", "scoring",
]
ROLE_LANES = ["ramp", "density", "direction", "edge", "flow", "accent"]


def fail(errs, msg):
    errs.append(msg)


def validate_profile(p, errs):
    pid = p.get("material_id", "<no-id>")
    for f in REQUIRED_PROFILE_FIELDS:
        if f not in p:
            fail(errs, f"{pid}: missing required field '{f}'")

    mid = p.get("material_id", "")
    if not (mid.startswith("terrain:") and mid[len("terrain:"):].isdigit()) and not mid.startswith("mesh:"):
        fail(errs, f"{pid}: material_id must be terrain:<int> or mesh:<...> (raw RGB cannot bind)")

    state = p.get("profile_state")
    if state not in ("live", "blocked"):
        fail(errs, f"{pid}: profile_state '{state}' invalid")

    # mesh PROFILE deferred to RQ-159: mesh may not be live in v1
    if mid.startswith("mesh:") and state == "live":
        fail(errs, f"{pid}: mesh PROFILE binding is DEFERRED (RQ-159); cannot be live in v1")

    if not p.get("idempotency_marker"):
        fail(errs, f"{pid}: idempotency_marker must be set (guards operator edits)")

    rb = p.get("role_buckets", {})
    for lane in ROLE_LANES:
        if lane not in rb:
            fail(errs, f"{pid}: role_buckets missing lane '{lane}'")
    # Edge fail-closed: empty-candidate edge bucket must carry a blocked reason
    for eb in rb.get("edge", []):
        if not eb.get("candidates") and not eb.get("blocked"):
            fail(errs, f"{pid}: edge bucket '{eb.get('fact')}' empty but no blocked reason (Law 6)")

    # [G6] live color pair must not equal a reserved marker pair
    if state == "live":
        colors = p.get("colors", {})
        for fg in colors.get("fg_palette", []):
            for bg in colors.get("bg_palette", []):
                if (fg, bg) in RESERVED_MARKER_PAIRS:
                    fail(errs, f"{pid}: live palette pair fg={fg} bg={bg} collides with reserved marker [G6]")


def validate_sidecars(profiles_by_marker, errs):
    n = 0
    for path in sorted(glob.glob(SIDECAR_GLOB)):
        n += 1
        d = json.load(open(path))
        base = os.path.basename(path)
        if d.get("schema") != "material_profile_assignments.v1":
            fail(errs, f"{base}: wrong schema '{d.get('schema')}'")
        if not d.get("map_hash"):
            fail(errs, f"{base}: missing map_hash")
        for a in d.get("assignments", []):
            key = a.get("key", "")
            if not (key.startswith("terrain:") and key[len("terrain:"):].isdigit()) and not key.startswith("mesh:"):
                fail(errs, f"{base}: assignment key '{key}' invalid (raw RGB cannot bind)")
            if a.get("profile_id") not in profiles_by_marker:
                fail(errs, f"{base}: assignment '{key}' references unknown profile_id '{a.get('profile_id')}'")
    return n


def try_jsonschema(doc, errs):
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return "skipped (jsonschema not installed; manual checks ran)"
    schema = json.load(open(SCHEMA))
    try:
        jsonschema.validate(doc, schema)
        return "passed"
    except jsonschema.ValidationError as e:
        fail(errs, f"jsonschema: {e.message} at {list(e.path)}")
        return "failed"


def main():
    errs = []
    if not os.path.exists(PROFILES):
        print(f"[RQ-147] FAIL: missing {PROFILES}")
        return 1
    doc = json.load(open(PROFILES))
    if doc.get("schema") != "material_rendering_profiles.v1":
        fail(errs, f"top-level schema '{doc.get('schema')}' != material_rendering_profiles.v1")

    profiles = doc.get("profiles", [])
    for p in profiles:
        validate_profile(p, errs)
    by_marker = {p.get("idempotency_marker"): p for p in profiles}
    nsidecars = validate_sidecars(by_marker, errs)
    js = try_jsonschema(doc, errs)

    if errs:
        print(f"[RQ-147] VALIDATION FAILED ({len(errs)} error(s)); jsonschema={js}")
        for e in errs:
            print(f"  - {e}")
        return 1
    print(f"[RQ-147] VALID: {len(profiles)} profile(s), {nsidecars} sidecar(s); jsonschema={js}")
    print(f"[RQ-147] live profile state, terrain-key bound, mesh deferred, edge fail-closed, [G6] clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
