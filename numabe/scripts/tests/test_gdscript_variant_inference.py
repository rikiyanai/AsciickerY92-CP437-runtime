"""FL-4512: `:=` must not infer its type from a Variant-returning global.

The project treats GDScript warnings as errors, so `var x := PI / ceil(y)`
(global ceil returns Variant) fails to compile and takes down every script
that depends on it. That happened in TrueFFPagePhase.gd (44ed3e28b): World.gd
failed to load and the True FF view was blank. gdparse checks syntax only,
so this offline test is the guard. It flags a Variant global called at the
top level of a `:=` initializer; a call nested inside a typed wrapper
(int(floor(x)), clampi(...)) is typed and allowed. Use ceilf/floorf/roundf/
absf/signf/clampf/minf/maxf/snappedf/lerpf/wrapf (or the i variants).
"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VARIANT = {"ceil", "floor", "round", "abs", "sign", "clamp", "min", "max", "snapped", "lerp", "wrap"}
DECL = re.compile(r"^\s*(?:static\s+)?var\s+\w+\s*:=\s*(.+)$")


def top_level_calls(expr: str) -> list[str]:
    out, depth, i, n = [], 0, 0, len(expr)
    while i < n:
        c = expr[i]
        if c in "\"'":
            quote, i = c, i + 1
            while i < n and expr[i] != quote:
                i += 2 if expr[i] == "\\" else 1
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and (c.isalpha() or c == "_") and (i == 0 or not (expr[i - 1].isalnum() or expr[i - 1] in "_.")):
            j = i
            while j < n and (expr[j].isalnum() or expr[j] == "_"):
                j += 1
            if j < n and expr[j] == "(":
                out.append(expr[i:j])
            i = j
            continue
        i += 1
    return out


def offending(line: str) -> bool:
    m = DECL.match(line)
    if not m:
        return False
    return bool(VARIANT & set(top_level_calls(m.group(1).split("#")[0].strip())))


def test_detector_catches_the_regression_and_allows_typed_wrappers():
    assert offending("    var yaw_step := PI / ceil(PI / atan(a / b))")
    assert offending("\tvar height_delta := abs(pos.y - sample_h)")
    assert not offending("    var yaw_step := PI / ceilf(PI / atan(a / b))")
    assert not offending("\tvar px := clampi(int(floor(uv.x * 4.0)), 0, 3)")
    assert not offending("\tvar out := Image.create(w, max(a, b), false, f)")
    assert not offending("\tvar x: float = floor(y)")


def test_no_tracked_gdscript_infers_from_a_variant_global():
    files = subprocess.run(["git", "ls-files", "godot_project/*.gd"], cwd=ROOT,
                           capture_output=True, text=True, check=True).stdout.split()
    hits = []
    for name in files:
        for number, line in enumerate((ROOT / name).read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if offending(line):
                hits.append(f"{name}:{number}: {line.strip()}")
    assert not hits, "\n".join(hits)
