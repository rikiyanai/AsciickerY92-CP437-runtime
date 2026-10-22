"""FL-4359 style-profile data contract.

The default profile names code points, never atlas indices. These checks pin
the document shape the GDScript owner consumes: schema, sorted unique code
points, resolution entries only for scalars the CP437 page cannot carry, and
a documented reason on every unmapped scalar.
"""

from __future__ import annotations

import json
from pathlib import Path


PROFILE_PATH = (
    Path(__file__).resolve().parents[2]
    / "godot_project"
    / "render"
    / "ascii"
    / "glyphs"
    / "style_profile_default.json"
)


def _profile() -> dict:
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def test_schema_and_counts() -> None:
    profile = _profile()
    assert profile["schema"] == "fl4359.style_profile.v2"
    assert profile["atlas_glyph_count"] == 1881
    points = profile["code_points"]
    assert len(points) == len(set(points)) == 41
    assert points == sorted(points)


def test_resolution_only_above_cp437() -> None:
    profile = _profile()
    resolution = profile["resolution"]
    assert set(resolution) == {"180", "183"}
    assert resolution["180"] == [552]
    assert resolution["183"] == [250, 1686]


def test_unmapped_documented_with_reasons() -> None:
    profile = _profile()
    documented = {
        entry["codepoint"]: entry["reason"]
        for entry in profile["unmapped_documented"]
    }
    assert set(documented) == {8226, 12462}
    assert all(reason.strip() for reason in documented.values())

def test_appearance_pair() -> None:
    profile = _profile()
    assert profile["ink"] == [255, 255, 255, 255]
    assert profile["paper"] == [0, 0, 0, 255]
