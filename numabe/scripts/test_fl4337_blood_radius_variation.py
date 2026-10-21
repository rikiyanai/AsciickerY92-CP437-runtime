#!/usr/bin/env python3
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORLD = ROOT / "godot_project" / "scripts" / "World.gd"
PRODUCT = ROOT / "godot_project" / "render" / "product" / "cell_owner_product.glsl"


def test_fl4337_blood_radius_envelope_is_smaller_and_varied() -> None:
    src = WORLD.read_text(encoding="utf-8")

    required = [
        "const BLOOD_MARK_RADIUS_MIN := 0.22",
        "const BLOOD_MARK_RADIUS_MAX := 0.52",
        "const BLOOD_MARK_RADIUS_VARIATION := 0.07",
        "var radius_variation := _blood_mark_radius_variation(kind, actor_id, seq)",
        "lerpf(BLOOD_MARK_RADIUS_MIN, BLOOD_MARK_RADIUS_MAX, amount) + radius_variation",
        '"radius_variation": radius_variation',
        '"radius_envelope_min": BLOOD_MARK_RADIUS_MIN',
        '"radius_envelope_max": BLOOD_MARK_RADIUS_MAX',
        "func _blood_mark_radius_variation(kind: String, actor_id: int, seq: int) -> float:",
        "for i in range(kind.length()):",
        "kind.unicode_at(i)",
        '"radius_variation": float(mark.get("radius_variation", 0.0))',
        '"radius_envelope_min": float(mark.get("radius_envelope_min", BLOOD_MARK_RADIUS_MIN))',
        '"radius_envelope_max": float(mark.get("radius_envelope_max", BLOOD_MARK_RADIUS_MAX))',
    ]
    for needle in required:
        assert needle in src, needle

    forbidden = [
        '"radius": lerpf(1.4, 2.6, amount)',
        'float(mark.get("radius", 1.6))',
    ]
    for needle in forbidden:
        assert needle not in src, needle


def test_fl4337_blood_marks_require_fresh_hit_sequence() -> None:
    src = WORLD.read_text(encoding="utf-8")

    required = [
        "var _blood_mark_last_seen_seq: Dictionary = {}",
        'var source_key := "%s:%d" % [kind, actor_id]',
        "var last_seen_seq := int(_blood_mark_last_seen_seq.get(source_key, -1))",
        "if last_seen_seq < 0:",
        "_blood_mark_last_seen_seq[source_key] = seq",
        "if seq <= last_seen_seq:",
        "_blood_mark_last_seen_seq[source_key] = seq",
    ]
    for needle in required:
        assert needle in src, needle


def test_fl4337_product_blood_rejects_broad_faint_circle_edges() -> None:
    src = _expand_includes(PRODUCT.read_text(encoding="utf-8"))

    required = [
        "float radius = clamp(slot0.z, 0.12, 0.58);",
        "float dist = length(receiver_world.xz - slot0.xy);",
        "float falloff = 1.0 - smoothstep(radius * 0.42, radius, dist);",
        "if (fact.intensity <= 0.12) {",
    ]
    for needle in required:
        assert needle in src, needle

    forbidden = [
        "float radius = clamp(slot0.z, 0.18, 1.05);",
        "float falloff = 1.0 - smoothstep(0.0, radius, length(receiver_world.xz - slot0.xy));",
        "if (fact.intensity <= 0.01) {",
    ]
    for needle in forbidden:
        assert needle not in src, needle
