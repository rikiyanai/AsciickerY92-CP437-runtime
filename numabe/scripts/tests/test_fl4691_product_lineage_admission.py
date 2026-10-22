"""The runner admits only the exact prepared Product reflection allocation."""
import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "lineage_transport_fixture", HERE / "test_fl4359_presentation_profile_manifest.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


@pytest.mark.parametrize("probe", [1, 64, 256, 384])
def test_admits_exact_prepared_table_and_maps(probe):
    runner = fixture._load_runner()
    receipt = fixture._complete_transport_receipt(runner)
    for row in receipt["native_source_content_sidecars"]:
        row["probe_size"] = probe
        row["join"]["probe_size"] = probe
        row["reflection_lineage_map_bytes"] = probe * probe * 18 * 4
    assert runner._presentation_field_capture_receipt_accepts(receipt)


@pytest.mark.parametrize("field,value", [
    ("reflection_lineage_epoch", True), ("reflection_lineage_epoch", 0),
    ("reflection_lineage_epoch", "3"),
    ("reflection_lineage_capacity", 262145),
    ("reflection_lineage_table_bytes", 16),
    ("reflection_lineage_table_bytes", 16777232.0),
    ("reflection_lineage_map_bytes", 4),
    ("reflection_lineage_map_bytes", "10616832"),
    ("probe_size", 385), ("layer_count", 19),
])
def test_refuses_malformed_lineage_envelope(field, value):
    runner = fixture._load_runner()
    receipt = fixture._complete_transport_receipt(runner)
    assert runner._presentation_field_capture_receipt_accepts(receipt)
    receipt["native_source_content_sidecars"][0][field] = value
    assert not runner._presentation_field_capture_receipt_accepts(receipt)


def test_lineage_resources_are_reserved_separately_from_native_outputs():
    runner = fixture._load_runner()
    assert runner.NATIVE_SOURCE_CONTENT_REQUIRED_PEAK_BYTES == (
        (16 + 1048576 * 144) * 7 + (16 + 262144 * 64) * 7
        + 384 * 384 * 18 * 2 * 4 + 64 + 80)
    assert runner.REFLECTION_LINEAGE_TABLE_BYTES == 16 + 262144 * 64
    assert runner.REFLECTION_LINEAGE_REQUIRED_PEAK_BYTES == 38010896
    receipt = fixture._complete_transport_receipt(runner)
    receipt.pop("presentation_field_reflection_lineage_required_peak_bytes")
    assert not runner._presentation_field_capture_receipt_accepts(receipt)
