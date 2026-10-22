#!/usr/bin/env python3
"""Executable guard for the FL-4431 orthographic restoration steps S1, S2, S3.

WHY THIS FILE EXISTS
--------------------
Steps S1, S2 and S3 changed how the ORTHOGRAPHIC picture is painted, and until
now nothing collected by pytest pinned any of them. The pre-existing FL-4431
contract files cannot fill the gap: they are written script-style (helpers plus
a `main()`, no `test_` prefixed module-level functions), so `pytest` collects
ZERO cases from them and a green sweep proves nothing about this region. See
FL-4660. This file is deliberately pytest-shaped so it actually runs in the
suite.

WHAT "REGISTER" MEANS HERE
--------------------------
The renderer draws the same world two ways. The ORTHOGRAPHIC register is the
fixed, map-like view; the PERSPECTIVE register is the moving in-game camera.
They are told apart by one number that the retained scene publishes once per
frame, `product_vsb_frame.view_identity.z`. Because it is decided per FRAME and
not per screen cell, every parallel invocation in a dispatch takes the same side
of the branch -- which is why the two registers may hold different paint laws
without reviving the deleted per-cell `stable_cutover_paint` selector.

WHAT EACH STEP DID, IN PLAIN TERMS
----------------------------------
  S1  Cloud shadows on the orthographic map went back to the pre-cutover
      five-sample filter at full strength, instead of a thirteen-sample filter
      whose result was scaled down and then capped.
  S2  Terrain glyph identity reads one authored row. Terrain foreground and
      background colour interpolate the two neighbouring rows in both
      projections, so projection never becomes a material-paint owner.
  S3  The orthographic ramp went back to snapping its value into seven steps,
      which is what makes a hillside read as stacked tone bands.

WHAT THIS FILE DOES NOT CLAIM
-----------------------------
Everything here is STATIC: it reads shader text. It cannot show the picture is
correct, cannot measure speed, and cannot detect shimmer during motion. Those
need a headed run and operator eyes.
"""

from __future__ import annotations
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / "godot_project" / "render" / "product" / "cell_owner_product.glsl"

# The one frame-uniform expression that is allowed to choose between registers.
PROJECTION_FACT = "product_vsb_frame.view_identity.z == VSB_PROJECTION_ORTHOGRAPHIC"


def _source() -> str:
    return _expand_includes(PRODUCT.read_text(encoding="utf-8"))


def _code_only(text: str) -> str:
    """Drop `//` comments so a check for an absent number reads CODE only.

    Without this, a comment that explains "there is no 0.62 here any more"
    would itself satisfy a search for "0.62" and the assertion would be
    meaningless.
    """
    return "\n".join(
        line.split("//", 1)[0] for line in text.split("\n"))


def _function_body(source: str, signature_start: str) -> str:
    """Return one whole `{ ... }` function body, brace-matched.

    `signature_start` is any unique text from the function's declaration. The
    scan begins at the first `{` after it and walks forward counting braces, so
    nested blocks inside the function do not end the slice early.
    """
    start = source.index(signature_start)
    open_brace = source.index("{", start)
    depth = 0
    index = open_brace
    while True:
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace : index + 1]
        index += 1


def _matlib_colour_law(source: str) -> str:
    """Return the bounded S2 foreground/background colour law."""
    start = source.index("precise vec3 matlib_fg;")
    end = source.index("// One glyph plus", start)
    return source[start:end]


# --------------------------------------------------------------------------
# S1 -- orthographic cloud shadow
# --------------------------------------------------------------------------

def test_s1_orthographic_cloud_kernel_is_the_five_tap_form() -> None:
    """Orthographic clouds use five samples at full strength.

    The restored filter weights the cell under the shadow at one half and its
    four immediate neighbours at one eighth each (0.50 + 4 x 0.125 = 1.0, so
    the filter neither brightens nor darkens on average). Critically the two
    cutover terms are gone: a `* 0.62` attenuation and a `min(..., 0.22)`
    ceiling, which together held maximum orthographic cloud darkening to about
    three percent where the restored path reaches about fourteen.
    """
    body = _function_body(
        _source(), "float q3_probe_payload_ortho_cloud_shadow_for_cell")
    code = _code_only(body)
    assert "center * 0.50 + cardinals * 0.125" in code
    assert "0.62" not in code, "orthographic cloud attenuation returned"
    assert "0.22" not in code, "orthographic cloud ceiling returned"
    # Five projections, and one shared receiver height rather than one per tap.
    assert code.count("q3_probe_payload_ortho_cloud_density_projected_at") == 5
    assert code.count("corner_height") == 4


def test_s1_left_the_perspective_cloud_kernel_alone() -> None:
    """The perspective cloud filter keeps the shared thirteen-tap response.

    S1 was scoped to orthographic. If this fails, a change intended for the map
    view has leaked into the in-game camera. The perspective register retains
    the full-density thirteen-tap filter and its edge-only smoothstep; the old
    attenuation and ceiling are deleted in both projection registers.
    """
    body = _function_body(
        _source(), "float q3_probe_payload_cutover_cloud_shadow_for_cell")
    code = _code_only(body)
    assert code.count("q3_probe_payload_live_cloud_shadow_for_cell") == 13
    assert "center * 0.30 + cardinals * 0.10 + diagonals * 0.05 + reach * 0.025" in code
    assert "smoothstep(0.20, 0.82, filtered)" in code
    assert "min(filtered * 0.62, 0.22)" not in code


# --------------------------------------------------------------------------
# S2 -- orthographic material-library colour rows
# --------------------------------------------------------------------------

def test_s2_both_projections_share_the_continuous_colour_law() -> None:
    """Colour interpolates; the separately fetched glyph stays discrete."""
    law = _code_only(_matlib_colour_law(_source()))
    assert "ortho_register" not in law
    assert law.count("texelFetch") == 4
    assert "shd_continuous" in law
    assert law.count("mix(") == 2


# --------------------------------------------------------------------------
# S3 -- orthographic ramp banding
# --------------------------------------------------------------------------

def test_s3_ramp_colour_is_continuous_in_both_projections() -> None:
    """Projection cannot select a second synthetic colour quantizer.

    The authored matlib row remains the discrete glyph owner. The later ramp is
    colour-only, so it consumes ``raw01`` directly in both registers and has no
    banded-versus-continuous selector argument.
    """
    source = _source()
    body = _function_body(source, "void product_apply_material_ramp_steps")
    assert "float stepped = raw01;" in body
    assert "stable_cutover_paint" not in body
    assert "band_idx" not in body
    assert "!ortho_register" not in body
    assert "ramp, family, boundary != 0u," in source
    assert "ramp, family, boundary != 0u, true," not in source, (
        "the ramp lane is hard-coded again; S3 has been reverted")


# --------------------------------------------------------------------------
# The selector itself
# --------------------------------------------------------------------------

def test_projection_selector_is_frame_uniform() -> None:
    """Every register split must read the per-frame projection word.

    `ortho_register` is the variable all three steps branch on. Each place it is
    declared must take its value from `product_vsb_frame.view_identity.z`, which
    the retained scene publishes ONCE PER FRAME. If any declaration were derived
    from something that varies per screen cell, these steps would have become a
    per-cell paint-mode selector -- the thing FL-4359 deleted.

    Note this does NOT assert that the identifier `stable_cutover_paint` is
    absent from the shader. It is still an ordinary parameter threaded through
    the water and material-library helpers; only a per-cell SELECTOR is
    forbidden, not the name.
    """
    source = _source()
    declarations = re.findall(r"bool ortho_register\s*=\s*([^;]+);", source)
    assert declarations, "no ortho_register declaration found at all"
    for declaration in declarations:
        assert PROJECTION_FACT in " ".join(declaration.split()), (
            f"ortho_register assigned from something else: {declaration!r}")


def main() -> int:
    """Allow running this file directly as well as under pytest."""
    for name, function in sorted(globals().items()):
        if name.startswith("test_") and callable(function):
            function()
            print(f"OK  {name}")
    print("FL4431_ORTHO_REGISTER_SPLIT_CONTRACT_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
