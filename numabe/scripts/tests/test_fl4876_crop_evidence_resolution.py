#!/usr/bin/env python3
"""FL-4876 APPEARANCE-01. Every shopfront in the world was painted one grey.

``compile_custom_asset_plan`` colours each custom identity asset from the
dominant colour of its reviewed crop, and records where that colour came from
in ``colour_basis``. Measured on the lane:

    colour_basis: Counter({'crop_unreadable': 61})   # 61 of 61

All sixty-one -- Cocokara Fine, FamilyMart, every route-visible storefront --
were the same neutral grey ``[150, 150, 144]``, and none used source colour.

The cause was not missing data. The crop ledger stores **absolute** paths
written on whichever checkout reviewed the captures, and all 727 rows of
``observation_module_mapping.jsonl`` begin ``/Users/r/Downloads/asciicker-Y9-2/``,
a root that does not exist here. ``Image.open`` raised ``FileNotFoundError``,
which is an ``OSError``, which the except clause caught and turned into grey.
The crops are present and git-tracked in this repository the whole time.

Two things are pinned here: a stored absolute path is re-anchored on this
repository, and a crop that is *named but absent* raises instead of quietly
repainting a building.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline.compile_custom_asset_plan import (  # noqa: E402
    _dominant_colours,
    resolve_crop_path,
)

LEDGER = (ROOT / "artifacts/experiments/fl4813_capture_review_20260915"
          / "preserved/numabe_full_727_mapping_20260915"
          / "observation_module_mapping.jsonl")

FALLBACK_GREY = [150, 150, 144]
DEAD_ROOT = "/Users/r/Downloads/asciicker-Y9-2/"

requires_ledger = pytest.mark.skipif(
    not LEDGER.is_file(), reason="the preserved crop ledger is unavailable")


def _png(path: Path, rgb=(12, 200, 90)) -> Path:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), rgb).save(path)
    return path


# --------------------------------------------------------------------------
# Re-anchoring a path from a checkout that no longer exists.
# --------------------------------------------------------------------------

@requires_ledger
def test_every_stale_ledger_path_resolves_in_this_checkout():
    """The whole defect, measured against the real ledger."""
    import json
    rows = [json.loads(line) for line in
            LEDGER.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 727
    named = [row.get("crop_path") for row in rows]
    assert all(str(path).startswith(DEAD_ROOT) for path in named), (
        "this test is about absolute paths from a dead checkout root")
    assert not any(Path(path).is_file() for path in named), (
        "the stored paths are expected to be unresolvable as written")
    resolved = [resolve_crop_path(path) for path in named]
    assert all(path is not None and path.is_file() for path in resolved), (
        "a stored absolute path must be re-anchored on this repository")


def test_an_existing_path_is_used_unchanged(tmp_path):
    crop = _png(tmp_path / "crops" / "ann-1.png")
    assert resolve_crop_path(str(crop)) == crop


def test_a_path_outside_any_repo_anchor_is_not_invented(tmp_path):
    """Re-anchoring keys on a repo-owned component, not on a basename.

    Searching the tree for a matching filename would happily bind a crop to
    an unrelated image; this must simply fail to find it.
    """
    missing = tmp_path / "somewhere" / "else" / "ann-nope.png"
    assert not resolve_crop_path(str(missing)).is_file()


# --------------------------------------------------------------------------
# A named-but-absent crop is a broken run, not a grey building.
# --------------------------------------------------------------------------

def test_a_named_but_absent_crop_raises(tmp_path):
    with pytest.raises(ValueError) as excinfo:
        _dominant_colours(str(tmp_path / "artifacts" / "crops" / "gone.png"))
    assert "named but absent" in str(excinfo.value)


def test_the_dead_root_no_longer_silently_returns_grey():
    """The exact shape of the production failure."""
    with pytest.raises(ValueError):
        _dominant_colours(DEAD_ROOT + "artifacts/experiments/"
                          "fl4813_capture_review_20260915/crops/"
                          "definitely-not-here.png")


def test_no_crop_named_at_all_is_still_an_honest_absence():
    """Absent evidence and broken evidence are different facts.

    Nothing was claimed here, so the neutral default is correct and says so.
    """
    background, text, basis = _dominant_colours(None)
    assert basis == "no_crop_evidence"
    assert background == FALLBACK_GREY
    assert text == [48, 48, 48]


# --------------------------------------------------------------------------
# A real crop yields real colour.
# --------------------------------------------------------------------------

def test_a_readable_crop_reports_source_colour(tmp_path):
    crop = _png(tmp_path / "artifacts" / "crops" / "ann-2.png",
                rgb=(200, 40, 40))
    background, text, basis = _dominant_colours(str(crop))
    assert basis == "source_crop_dominant"
    assert background != FALLBACK_GREY
    assert background[0] > background[1] and background[0] > background[2]
    # Dark background gets light text and vice versa.
    assert text == [232, 232, 226]


@requires_ledger
def test_the_lane_crops_are_not_one_colour():
    """145 distinct backgrounds, not 1. The point of the whole repair.

    A run reporting source_crop_dominant while every asset shared one colour
    would mean the resolution worked and the sampling did not.
    """
    import json
    rows = [json.loads(line) for line in
            LEDGER.read_text(encoding="utf-8").splitlines() if line.strip()]
    results = [_dominant_colours(row.get("crop_path")) for row in rows]
    assert {basis for _bg, _tx, basis in results} == {"source_crop_dominant"}
    backgrounds = {tuple(bg) for bg, _tx, _basis in results}
    assert len(backgrounds) > 100, (
        f"only {len(backgrounds)} distinct crop colours across {len(rows)} "
        "crops; sampling is probably not reading the images")
    assert tuple(FALLBACK_GREY) not in backgrounds
