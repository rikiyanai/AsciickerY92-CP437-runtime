from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "web/asciiid-authoring-shell/src/ControlWidget.tsx"


def source() -> str:
    return CONTROL.read_text(encoding="utf-8")


def test_new_document_confirmation_is_html_owned_and_addressed() -> None:
    text = source()
    assert "window.confirm" not in text
    assert 'data-input-role="request"' in text
    assert 'data-input-role="confirm"' in text
    assert "newDocumentConfirming ?" in text


def test_multi_field_mutations_have_one_explicit_commit_surface() -> None:
    text = source()
    profile = text[text.index('leaf.leaf_id === "legacy.material.selectable.material.id"'):]
    profile = profile[:profile.index("CommitSelectTerrainTool")]
    assert 'data-input-role="profile_namespace"' in profile
    assert 'data-input-role="material_key"' in profile
    assert 'data-input-role="commit"' in profile
    assert "APPLY PROFILE" in profile

    height = text[text.rindex('leaf.leaf_id.includes("height.range")'):]
    height = height[:height.index("prop?.enum?.length")]
    assert 'data-input-role="minimum"' in height
    assert 'data-input-role="maximum"' in height
    assert 'data-input-role="commit"' in height
    assert "APPLY RANGE" in height
    assert "onBlur=" not in height


def test_exact_scalar_and_color_keyboard_surfaces_are_registry_addressable() -> None:
    text = source()
    assert 'data-input-role="value"' in text
    assert 'aria-label={`${label} exact value`}' in text
    assert 'data-input-role="rgb_hex"' in text
    assert 'aria-label={`${label} hexadecimal color`}' in text
    assert "enter a six-digit #rrggbb color" in text


def test_explicit_composite_identity_blocks_implicit_second_target() -> None:
    text = source()
    assert 'child.props as Record<string, unknown>' in text
    assert '["data-leaf-id"] === leaf.leaf_id' in text
    assert "return { children, changed: false };" in text
