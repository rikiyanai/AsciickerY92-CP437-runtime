"""Offline descriptor-set ownership for the True FF shader family.

Set 0 owns pass storage and images. Set 1 is the explicit live-source fact
table (water, sky, effects, and wakes); no other include may grow that set and
no True FF shader may declare a third descriptor set.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import fl4895_glsl_check as glsl  # noqa: E402

ASCII = ROOT / "godot_project" / "render" / "ascii"


def test_true_ff_set_one_is_owned_only_by_live_source_facts():
    allowed = {
        "features/true_ff_live_water_facts.glslinc": set(range(6)),
        "features/true_ff_live_sky_source.glslinc": {6},
        "features/true_ff_effect_field_source.glslinc": {7, 8},
        "features/true_ff_live_wake_source.glslinc": {9, 10},
    }
    offenders = []
    seen = set()
    for shader in sorted(ASCII.rglob("*.glsl*")):
        source = shader.read_text(encoding="utf-8")
        for match in re.finditer(r"layout\s*\(([^)]*)\)", source):
            set_match = re.search(r"\bset\s*=\s*(\d+)", match.group(1))
            if not set_match:
                continue
            descriptor_set = int(set_match.group(1))
            if descriptor_set == 0:
                continue
            relative = str(shader.relative_to(ASCII))
            binding_match = re.search(r"\bbinding\s*=\s*(\d+)", match.group(1))
            binding = int(binding_match.group(1)) if binding_match else -1
            if descriptor_set != 1 or binding not in allowed.get(relative, set()):
                offenders.append(f"{shader.relative_to(ROOT)}: layout({match.group(1).strip()})")
            else:
                seen.add((relative, binding))
    expected = {(path, binding) for path, bindings in allowed.items() for binding in bindings}
    assert not offenders, "unowned True FF descriptor-set declaration:\n" + "\n".join(offenders)
    assert seen == expected


def test_true_ff_compute_shaders_never_query_runtime_array_length():
    # FL-4512 METAL_BUFFER_SIZE_ALIAS_20260929: a runtime-array .length() makes
    # SPIRV-Cross add a buffer-size table to the descriptor set; Godot's Metal
    # driver gave it the id of the set's first buffer (budget pipeline failed
    # to compile) and such lengths also read 0 on Metal.
    # Counts come from the host (push constants or the budget ABI).
    offenders = []
    for shader in sorted(p for p in ASCII.rglob("*.glsl") if "#[compute]" in p.read_text(encoding="utf-8")):
        source = re.sub(r"//[^\n]*", "", glsl.expand(shader))
        if re.search(r"\]\s*\.\s*length\s*\(\s*\)|\bitems\s*\.\s*length\s*\(", source):
            offenders.append(str(shader.relative_to(ROOT)))
    assert not offenders, "runtime-array .length() in: " + ", ".join(offenders)
