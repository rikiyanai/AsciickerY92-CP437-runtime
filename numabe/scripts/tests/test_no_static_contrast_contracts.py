from __future__ import annotations

from scripts.validate_no_static_contrast_contracts import validate_test_source


PATH = "scripts/test_example.py"


def reasons(source: str) -> list[str]:
    return [finding.reason for finding in validate_test_source(source, path=PATH)]


def test_blocks_positive_shader_symbol_membership_contract() -> None:
    result = reasons(
        'def test_sky():\n'
        '    source = "shader"\n'
        '    assert "SKY_INK_MIN_CONTRAST" in source\n'
    )
    assert result == ["positive source-membership presentation contract is banned"]


def test_blocks_positive_contrast_helper_order_contract() -> None:
    result = reasons(
        'def test_sky():\n'
        '    source = "shader"\n'
        '    assert source.index("background") < source.index("bounded_ink_contrast")\n'
    )
    assert "source-text index presentation contract is banned" in result


def test_blocks_contrast_contract_helper_and_mutation_fixture() -> None:
    result = reasons(
        'def test_sky():\n'
        '    source = "shader"\n'
        '    require_ordered(source, ("paint", "product_bounded_ink_contrast"), "law", [])\n'
        '    replace_once(source, "SKY_INK_MAX_CONTRAST", "0.0")\n'
        '    _replace_in_function(source, "sky", "contrast_rail", "other")\n'
    )
    assert "require_ordered may not require a presentation implementation" in result
    assert "replace_once may not require a presentation implementation" in result
    assert "_replace_in_function may not require a presentation implementation" in result


def test_blocks_static_checks_through_contrast_named_source_slice() -> None:
    result = reasons(
        'def test_cloud():\n'
        '    contrast_guard = extract_function("shader")\n'
        '    assert contrast_guard.count("mix(0.03,") == 1\n'
    )
    assert result == ["source-text count presentation contract is banned"]


def test_blocks_parameterized_contrast_mutation_fixture() -> None:
    result = reasons(
        '@pytest.mark.parametrize("old", ("product_contrast_rail",))\n'
        'def test_mutant(old):\n'
        '    _replace_in_function(source, "paint", old, "replacement")\n'
    )
    assert result == [
        "parameterized mutation fixture may not require a presentation implementation"
    ]


def test_blocks_positive_global_colour_lattice_contract() -> None:
    result = reasons(
        'def test_colour_owner():\n'
        '    source = "shader"\n'
        '    assert "final_product_index_at_quarter_lattice" in source\n'
        '    assert source.count("normalized_weights") == 1\n'
    )
    assert result == [
        "positive source-membership presentation contract is banned",
        "source-text count presentation contract is banned",
    ]


def test_allows_negative_absence_assertion() -> None:
    result = reasons(
        'def test_sky():\n'
        '    source = "shader"\n'
        '    assert "SKY_INK_MIN_CONTRAST" not in source\n'
        '    assert "product_bounded_ink_contrast" not in source\n'
    )
    assert result == []


def test_blocks_runtime_contrast_measurement_without_run_identity() -> None:
    result = reasons(
        'def test_rendered_frame():\n'
        '    contrast = measured_luma(frame_a, frame_b)\n'
        '    assert contrast > baseline_contrast\n'
    )
    assert result == [
        "rendered-contrast test lacks exact run identity fields: "
        "scene, pose, time, source_commit, receipt"
    ]


def test_allows_runtime_contrast_measurement_with_run_identity() -> None:
    result = reasons(
        'def test_rendered_frame():\n'
        '    receipt = load_receipt()\n'
        '    scene = receipt["scene"]\n'
        '    pose = receipt["pose"]\n'
        '    time = receipt["time"]\n'
        '    source_commit = receipt["source_commit"]\n'
        '    contrast = measured_luma(frame_a, frame_b)\n'
        '    assert contrast > baseline_contrast\n'
    )
    assert result == []


def test_allows_named_abi_only_wiring_test() -> None:
    result = validate_test_source(
        'def test_wiring():\n'
        '    source = "schema"\n'
        '    assert "row_shade_contrast" in source\n',
        path="scripts/tests/test_fl4260_color_shade_slider_wiring.py",
    )
    assert result == []
