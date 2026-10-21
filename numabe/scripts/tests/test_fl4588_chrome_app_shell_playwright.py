# =============================================================================
# test_fl4588_chrome_app_shell_playwright.py
# =============================================================================
# Focused fixture tests for sole-Chrome physical dispatch
# (FL4588-SOLE-CHROME-PHYSICAL-DISPATCH-20260717-24 / FL-4588).
#
# No Chromium launch. Fake page + mouse/keyboard only. Quiet-hold safe.
# =============================================================================

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
HELPER_PATH = ROOT / "scripts/fl4588/chrome_app_shell_playwright.py"
LEAF_ID = "settings.display.termpp_performance_hud"


def load_helper():
    name = "chrome_app_shell_playwright"
    spec = importlib.util.spec_from_file_location(name, HELPER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class FakeMouse:
    def __init__(self) -> None:
        self.ops: list[tuple] = []

    def move(self, x, y):
        self.ops.append(("move", x, y))

    def down(self, button="left"):
        self.ops.append(("down", button))

    def up(self, button="left"):
        self.ops.append(("up", button))

    def wheel(self, dx, dy):
        self.ops.append(("wheel", dx, dy))


class FakeKeyboard:
    def __init__(self) -> None:
        self.ops: list[tuple] = []

    def down(self, key):
        self.ops.append(("down", key))

    def up(self, key):
        self.ops.append(("up", key))

    def press(self, key):
        self.ops.append(("press", key))

    def type(self, text):
        self.ops.append(("type", text))


class FakePage:
    """Injectable page for FL-4588 dispatch fixtures — never launches Chrome."""

    def __init__(self, *, targets: dict[str, dict] | None = None) -> None:
        self.url = "http://127.0.0.1:43125/"
        self.mouse = FakeMouse()
        self.keyboard = FakeKeyboard()
        self._targets = targets or {
            f'[data-leaf-id="{LEAF_ID}"]': {
                "found": True,
                "attached": True,
                "visible": True,
                "enabled": True,
                "actionable": True,
                "tag_name": "button",
                "matched_leaf_id": LEAF_ID,
                # Omit matched_control_uid unless a test asserts UID matching.
                "box": {"x": 10.0, "y": 20.0, "width": 40.0, "height": 20.0},
            }
        }
        self._listeners: dict[str, list] = {}
        self.viewport_size = {"width": 800, "height": 600}
        self.form_ops: list[tuple] = []

    def on(self, event: str, handler) -> None:
        self._listeners.setdefault(event, []).append(handler)

    def evaluate_target(self, selector: str) -> dict:
        raw = dict(self._targets.get(selector) or {"found": False})
        # Fixture default: found targets are actionable unless the test says
        # otherwise (reproduces the Undo wrapper-center miss when false).
        if raw.get("found") and "actionable" not in raw:
            raw["actionable"] = True
        if raw.get("found") and "tag_name" not in raw:
            raw["tag_name"] = "button"
        return raw

    def evaluate_viewport(self) -> dict:
        return dict(self.viewport_size)

    def select_option(self, selector: str, value: str) -> None:
        self.form_ops.append(("select_option", selector, value))

    def select_file(self, selector: str, value: str) -> None:
        self.form_ops.append(("select_file", selector, value))

    def emit_console(self, level: str, message: str) -> None:
        class Msg:
            pass

        msg = Msg()
        msg.type = level
        msg.text = message
        for handler in self._listeners.get("console", []):
            handler(msg)

    def emit_event_record(self, body: dict) -> None:
        class Req:
            url = "http://127.0.0.1:43124/api/v1/event.record"
            method = "POST"
            post_data_json = body

        for handler in self._listeners.get("request", []):
            handler(Req())

    def emit_action_result(self, body: dict) -> None:
        class Resp:
            url = "http://127.0.0.1:43124/api/v1/runtime-intent"

            def json(self):
                return body

        for handler in self._listeners.get("response", []):
            handler(Resp())

    def emit_request(
        self,
        path: str,
        body: dict,
        *,
        raw: str | None = None,
        include_post_data: bool = True,
    ) -> None:
        """Emit a POST request to `path` (FL4588 task 60 / 63).

        Provides post_data_json (the parsed diagnostic body) and, when
        `include_post_data` is True, post_data (the exact raw string used for the
        SHA-256 sidecar). If `raw` is omitted, a compact JSON of `body` is used as
        the exact bytes. When `include_post_data` is False the request exposes NO
        post_data attribute — this reproduces a transport that only surfaces
        parsed JSON, so task 63 can prove the raw-byte sidecars stay absent (no
        reserialized bytes are minted as observed evidence).
        """
        raw_bytes = raw if raw is not None else json.dumps(body, separators=(",", ":"))

        if include_post_data:

            class Req:
                url = f"http://127.0.0.1:43124{path}"
                method = "POST"
                post_data = raw_bytes
                post_data_json = body

        else:

            class Req:  # type: ignore[no-redef]
                url = f"http://127.0.0.1:43124{path}"
                method = "POST"
                post_data_json = body

        for handler in self._listeners.get("request", []):
            handler(Req())


def test_deleted_grouped_coordinator_is_absent_from_live_adapter() -> None:
    assert not (ROOT / "scripts/fl4588/live_grouped_capture_coordinator.py").exists()
    helper = HELPER_PATH.read_text(encoding="utf-8")
    assert "chromium.launch" not in helper
    assert "connect_over_cdp" in helper
    assert "subprocess" not in helper


def test_bounding_box_translation_center() -> None:
    h = load_helper()
    x, y = h.translate_point_in_box({"x": 10, "y": 20, "width": 40, "height": 20})
    assert (x, y) == (30.0, 30.0)
    x2, y2 = h.translate_point_in_box(
        {"x": 10, "y": 20, "width": 40, "height": 20}, local_x=5, local_y=5
    )
    assert (x2, y2) == (15.0, 25.0)
    try:
        h.translate_point_in_box(
            {"x": 10, "y": 20, "width": 40, "height": 20}, local_x=99, local_y=0
        )
        raise AssertionError("must reject out-of-bounds")
    except h.ChromeShellDispatchError as exc:
        assert "target_local_point_out_of_bounds" in str(exc)


def test_pointer_click_down_move_up_ordering() -> None:
    h = load_helper()
    assert h.expand_pointer_click_order({"kind": "pointer_click"}) == [
        "pointer_down",
        "pointer_up",
    ]
    page = FakePage()
    session = h.PlaywrightChromeSession(
        expected_origin="http://127.0.0.1:43125",
        connect_over_cdp=lambda _base: None,
        injected_page=page,
    )
    session.connect(cdp_http_base="http://127.0.0.1:9222")
    entry = session.dispatch_physical(
        {"kind": "pointer_click", "target_leaf_id": LEAF_ID, "button": "left"}
    )
    assert entry.order == ["pointer_down", "pointer_up"]
    assert page.mouse.ops == [
        ("move", 30.0, 30.0),
        ("down", "left"),
        ("up", "left"),
    ]


def test_registry_role_targets_drive_select_number_checkbox_and_file() -> None:
    h = load_helper()
    role = '[data-leaf-id="settings.display.termpp_performance_hud"][data-input-role="value"]'
    checkbox = role + '[data-input-value="enabled"]'
    page = FakePage(
        targets={
            role: {
                "found": True,
                "attached": True,
                "visible": True,
                "enabled": True,
                "actionable": True,
                "tag_name": "select",
                "matched_leaf_id": LEAF_ID,
                "box": {"x": 10.0, "y": 20.0, "width": 40.0, "height": 20.0},
            },
            checkbox: {
                "found": True,
                "attached": True,
                "visible": True,
                "enabled": True,
                "actionable": True,
                "tag_name": "input",
                "matched_leaf_id": LEAF_ID,
                "checked": False,
                "box": {"x": 10.0, "y": 20.0, "width": 20.0, "height": 20.0},
            },
        }
    )
    session = h.PlaywrightChromeSession(
        expected_origin="http://127.0.0.1:43125",
        connect_over_cdp=lambda _base: None,
        injected_page=page,
    )
    session.connect(cdp_http_base="http://127.0.0.1:9222")

    session.dispatch_physical(
        {
            "kind": "select_option",
            "target_leaf_id": LEAF_ID,
            "target_role": "value",
            "value": "nord",
        }
    )
    session.dispatch_physical(
        {
            "kind": "fill_number",
            "target_leaf_id": LEAF_ID,
            "target_role": "value",
            "value": 1876,
        }
    )
    session.dispatch_physical(
        {
            "kind": "checkbox_set",
            "target_leaf_id": LEAF_ID,
            "target_role": "value",
            "target_value": "enabled",
            "value": True,
        }
    )
    session.dispatch_physical(
        {
            "kind": "file_select",
            "target_leaf_id": LEAF_ID,
            "target_role": "value",
            "value": "/evidence/map.a3d",
        }
    )
    assert page.form_ops == [
        ("select_option", role, "nord"),
        ("select_file", role, "/evidence/map.a3d"),
    ]
    assert ("press", "Meta+A") in page.keyboard.ops
    assert ("type", "1876") in page.keyboard.ops
    assert page.mouse.ops.count(("down", "left")) == 3


def test_keyboard_text_focuses_exact_target_and_replaces_visible_value() -> None:
    h = load_helper()
    role = (
        '[data-leaf-id="settings.display.termpp_performance_hud"]'
        '[data-input-role="rgb_hex"]'
    )
    page = FakePage(
        targets={
            role: {
                "found": True,
                "attached": True,
                "visible": True,
                "enabled": True,
                "actionable": True,
                "tag_name": "input",
                "matched_leaf_id": LEAF_ID,
                "box": {"x": 10.0, "y": 20.0, "width": 80.0, "height": 20.0},
            }
        }
    )
    session = h.PlaywrightChromeSession(
        expected_origin="http://127.0.0.1:43125",
        connect_over_cdp=lambda _base: None,
        injected_page=page,
    )
    session.connect(cdp_http_base="http://127.0.0.1:9222")
    session.dispatch_physical(
        {
            "kind": "keyboard_text",
            "target_leaf_id": LEAF_ID,
            "target_role": "rgb_hex",
            "value": "#123456",
        }
    )
    assert page.mouse.ops == [
        ("move", 50.0, 30.0),
        ("down", "left"),
        ("up", "left"),
    ]
    assert page.keyboard.ops == [
        ("press", "Meta+A"),
        ("type", "#123456"),
    ]


def test_pointer_click_value_requires_exact_registry_value_selector() -> None:
    h = load_helper()
    selector = (
        '[data-leaf-id="settings.display.termpp_performance_hud"]'
        '[data-input-role="glyph_id"][data-input-value="1876"]'
    )
    page = FakePage(
        targets={
            selector: {
                "found": True,
                "attached": True,
                "visible": True,
                "enabled": True,
                "actionable": True,
                "tag_name": "button",
                "matched_leaf_id": LEAF_ID,
                "box": {"x": 1.0, "y": 2.0, "width": 20.0, "height": 20.0},
            }
        }
    )
    session = h.PlaywrightChromeSession(
        expected_origin="http://127.0.0.1:43125",
        connect_over_cdp=lambda _base: None,
        injected_page=page,
    )
    session.connect(cdp_http_base="http://127.0.0.1:9222")
    entry = session.dispatch_physical(
        {
            "kind": "pointer_click_value",
            "target_leaf_id": LEAF_ID,
            "target_role": "glyph_id",
            "target_value": 1876,
            "button": "left",
        }
    )
    assert selector in (entry.notes or "")


def test_structured_browser_action_result_is_observed_from_console() -> None:
    h = load_helper()
    page = FakePage()
    session = h.PlaywrightChromeSession(
        expected_origin="http://127.0.0.1:43125",
        connect_over_cdp=lambda _base: None,
        injected_page=page,
    )
    session.connect(cdp_http_base="http://127.0.0.1:9222")
    result = {
        "schema": "asciiid.browser_action_result.v1",
        "leaf_id": "glyph.catalog.select",
        "control_uid": "asciiid_interaction_glyph_catalog_select",
        "action_kind": "browser.select_glyph_catalog_entry",
        "interaction_id": "browser-1",
        "action_scope": "browser_draft",
        "status": "applied",
        "payload": {"glyph_id": 1876, "unicode_scalar": 64},
        "write_set": ["/browser/glyph_catalog/selected_glyph_id"],
        "prior_browser_revision": 0,
        "new_browser_revision": 1,
        "product_mutation": False,
        "native_transaction_expected": False,
    }
    page.emit_console("info", h.BROWSER_ACTION_RESULT_PREFIX + json.dumps(result))
    assert session.observed_browser_action_results() == [result]

    invalid = dict(result, product_mutation=True, interaction_id="browser-2")
    page.emit_console("info", h.BROWSER_ACTION_RESULT_PREFIX + json.dumps(invalid))
    assert session.observed_browser_action_results() == [result]


def test_modifier_handling_control_arrow() -> None:
    h = load_helper()
    mods, primary = h.parse_key_with_modifiers("Control+ArrowLeft")
    assert mods == ["Control"]
    assert primary == "ArrowLeft"
    page = FakePage()
    session = h.PlaywrightChromeSession(
        expected_origin="http://127.0.0.1:43125",
        connect_over_cdp=lambda _base: None,
        injected_page=page,
    )
    session.connect(cdp_http_base="http://127.0.0.1:9222")
    session.dispatch_physical(
        {
            "kind": "keyboard_key",
            "target_leaf_id": LEAF_ID,
            "key": "Control+ArrowLeft",
        }
    )
    assert page.keyboard.ops == [
        ("down", "Control"),
        ("press", "ArrowLeft"),
        ("up", "Control"),
    ]


def test_visible_enabled_target_enforcement() -> None:
    h = load_helper()
    page = FakePage(
        targets={
            f'[data-leaf-id="{LEAF_ID}"]': {
                "found": True,
                "attached": True,
                "visible": False,
                "enabled": True,
                "actionable": True,
                "tag_name": "button",
                "matched_leaf_id": LEAF_ID,
                "box": {"x": 1, "y": 1, "width": 10, "height": 10},
            }
        }
    )
    session = h.PlaywrightChromeSession(
        expected_origin="http://127.0.0.1:43125",
        connect_over_cdp=lambda _base: None,
        injected_page=page,
    )
    session.connect(cdp_http_base="http://127.0.0.1:9222")
    try:
        session.dispatch_physical(
            {"kind": "pointer_click", "target_leaf_id": LEAF_ID, "button": "left"}
        )
        raise AssertionError("hidden target must fail")
    except h.ChromeShellDispatchError as exc:
        assert "target_not_visible" in str(exc)


def test_disabled_and_detached_and_mismatch() -> None:
    h = load_helper()

    def run(targets, expect_substr):
        page = FakePage(targets=targets)
        session = h.PlaywrightChromeSession(
            expected_origin="http://127.0.0.1:43125",
            connect_over_cdp=lambda _base: None,
            injected_page=page,
        )
        session.connect(cdp_http_base="http://127.0.0.1:9222")
        try:
            session.dispatch_physical(
                {"kind": "pointer_click", "target_leaf_id": LEAF_ID, "button": "left"}
            )
            raise AssertionError(f"expected {expect_substr}")
        except h.ChromeShellDispatchError as exc:
            assert expect_substr in str(exc)

    sel = f'[data-leaf-id="{LEAF_ID}"]'
    run(
        {
            sel: {
                "found": True,
                "attached": True,
                "visible": True,
                "enabled": False,
                "matched_leaf_id": LEAF_ID,
                "box": {"x": 1, "y": 1, "width": 10, "height": 10},
            }
        },
        "target_disabled",
    )
    run(
        {
            sel: {
                "found": True,
                "attached": False,
                "visible": True,
                "enabled": True,
                "matched_leaf_id": LEAF_ID,
                "box": {"x": 1, "y": 1, "width": 10, "height": 10},
            }
        },
        "target_detached",
    )
    run(
        {
            sel: {
                "found": True,
                "attached": True,
                "visible": True,
                "enabled": True,
                "matched_leaf_id": "other.leaf",
                "box": {"x": 1, "y": 1, "width": 10, "height": 10},
            }
        },
        "target_leaf_mismatch",
    )


def test_undo_wrapper_center_is_not_actionable_inner_button_is() -> None:
    """Undo row-center miss vs corrected inner-button target (FL-4588).

    Old owner: wide control-row div carried data-leaf-id; center click missed
    button.widget-button. New owner: only the inner button is actionable.
    """
    h = load_helper()
    undo_leaf = "workspace.history.undo"
    wrapper_sel = f'[data-leaf-id="{undo_leaf}"]'
    page_wrapper = FakePage(
        targets={
            wrapper_sel: {
                "found": True,
                "attached": True,
                "visible": True,
                "enabled": True,
                "actionable": False,
                "tag_name": "div",
                "matched_leaf_id": undo_leaf,
                "box": {"x": 180.0, "y": 493.0, "width": 738.0, "height": 38.5},
            }
        }
    )
    session_bad = h.PlaywrightChromeSession(
        expected_origin="http://127.0.0.1:43125",
        connect_over_cdp=lambda _base: None,
        injected_page=page_wrapper,
    )
    session_bad.connect(cdp_http_base="http://127.0.0.1:9222")
    try:
        session_bad.dispatch_physical(
            {"kind": "pointer_click", "target_leaf_id": undo_leaf, "button": "left"}
        )
        raise AssertionError("wrapper-center must fail closed")
    except h.ChromeShellDispatchError as exc:
        assert "target_not_actionable" in str(exc)
        assert "wrapper_center_click_deleted" in str(exc)

    page_btn = FakePage(
        targets={
            wrapper_sel: {
                "found": True,
                "attached": True,
                "visible": True,
                "enabled": True,
                "actionable": True,
                "tag_name": "button",
                "matched_leaf_id": undo_leaf,
                # Button box must sit inside FakePage 800x600 viewport.
                "box": {"x": 700.0, "y": 501.0, "width": 51.86, "height": 22.5},
            }
        }
    )
    session_ok = h.PlaywrightChromeSession(
        expected_origin="http://127.0.0.1:43125",
        connect_over_cdp=lambda _base: None,
        injected_page=page_btn,
    )
    session_ok.connect(cdp_http_base="http://127.0.0.1:9222")
    entry = session_ok.dispatch_physical(
        {"kind": "pointer_click", "target_leaf_id": undo_leaf, "button": "left"}
    )
    assert entry.page_x == 700.0 + 51.86 / 2.0
    assert entry.page_y == 501.0 + 22.5 / 2.0
    assert page_btn.mouse.ops[0] == ("move", entry.page_x, entry.page_y)


def test_missing_undo_does_not_fall_through_to_termpp_canvas() -> None:
    """Missing Undo leaf must not resolve to the inspect-only TERM++ canvas."""
    h = load_helper()
    undo_leaf = "workspace.history.undo"
    page = FakePage(
        targets={
            h.TERMPP_FINAL_CANVAS_SELECTOR: {
                "found": True,
                "attached": True,
                "visible": True,
                "enabled": True,
                "actionable": True,
                "tag_name": "canvas",
                "matched_leaf_id": None,
                "box": {"x": 174.0, "y": 116.0, "width": 368.5, "height": 276.875},
            }
        }
    )
    session = h.PlaywrightChromeSession(
        expected_origin="http://127.0.0.1:43125",
        connect_over_cdp=lambda _base: None,
        injected_page=page,
    )
    session.connect(cdp_http_base="http://127.0.0.1:9222")
    try:
        session.dispatch_physical(
            {"kind": "pointer_click", "target_leaf_id": undo_leaf, "button": "left"}
        )
        raise AssertionError("undo must not resolve to canvas")
    except h.ChromeShellDispatchError as exc:
        assert "target_not_found" in str(exc)


def test_hard_coded_canvas_gesture_allowlist_deleted() -> None:
    """DELETED owner: CANVAS_GESTURE_LEAF_IDS frozenset (stale dotted camera ids)."""
    h = load_helper()
    assert not hasattr(h, "CANVAS_GESTURE_LEAF_IDS")
    registry = h.load_ui_registry()
    derived = h.derive_canvas_target_leaf_ids(registry)
    assert derived == frozenset({"inspect.cell.select"})
    # Stale dotted ids from the deleted allowlist must not be derived.
    assert "scene.camera.pointer.rotate" not in derived
    assert "scene.camera.pointer.pan" not in derived
    assert "scene.camera.keyboard.move" not in derived
    assert "scene.camera.wheel.zoom" not in derived


def test_sculpt_resolves_via_canvas_data_leaf_id_binding() -> None:
    h = load_helper()
    sculpt = "scene.terrain.paint.sculpt"
    uid = h.registered_control_uid(h.load_ui_registry(), sculpt)
    assert uid == "asciiid_canvas_terrain_sculpt_brush"
    page = FakePage(
        targets={
            f'[data-leaf-id="{sculpt}"]': {
                "found": True,
                "attached": True,
                "visible": True,
                "enabled": True,
                "actionable": True,
                "tag_name": "canvas",
                "matched_leaf_id": sculpt,
                "matched_control_uid": uid,
                "box": {"x": 100.0, "y": 100.0, "width": 200.0, "height": 100.0},
            }
        }
    )
    session = h.PlaywrightChromeSession(
        expected_origin="http://127.0.0.1:43125",
        connect_over_cdp=lambda _base: None,
        injected_page=page,
    )
    session.connect(cdp_http_base="http://127.0.0.1:9222")
    entry = session.dispatch_physical(
        {"kind": "pointer_click", "target_leaf_id": sculpt, "button": "left"}
    )
    assert entry.page_x == 200.0
    assert entry.page_y == 150.0


def test_material_and_eyedropper_canvas_gestures_are_deleted() -> None:
    h = load_helper()
    registry = h.load_ui_registry()
    eyedropper = "scene.terrain.eyedropper.material"
    material = "scene.terrain.paint.material"
    assert h.tool_modifier_canvas_owner_leaf_ids(registry) == {}
    assert material not in h.derive_canvas_target_leaf_ids(registry)
    assert eyedropper not in h.derive_canvas_target_leaf_ids(registry)


def test_pointer_drag_holds_modifier_until_pointer_up() -> None:
    h = load_helper()
    sculpt = "scene.terrain.paint.sculpt"
    uid = h.registered_control_uid(h.load_ui_registry(), sculpt)
    page = FakePage(
        targets={
            f'[data-leaf-id="{sculpt}"]': {
                "found": True,
                "attached": True,
                "visible": True,
                "enabled": True,
                "actionable": True,
                "tag_name": "canvas",
                "matched_leaf_id": sculpt,
                "matched_control_uid": uid,
                "box": {"x": 100.0, "y": 100.0, "width": 200.0, "height": 100.0},
            }
        }
    )
    session = h.PlaywrightChromeSession(
        expected_origin="http://127.0.0.1:43125",
        connect_over_cdp=lambda _base: None,
        injected_page=page,
    )
    session.connect(cdp_http_base="http://127.0.0.1:9222")
    session.dispatch_physical({
        "kind": "pointer_down", "target_leaf_id": sculpt,
        "button": "left", "modifiers": ["Shift"],
    })
    session.dispatch_physical({
        "kind": "pointer_move", "target_leaf_id": sculpt,
        "button": "left",
    })
    entry = session.dispatch_physical({
        "kind": "pointer_up", "target_leaf_id": sculpt, "button": "left",
    })
    assert entry.modifiers == ["Shift"]
    assert page.keyboard.ops == [("down", "Shift"), ("up", "Shift")]


def test_visible_termpp_frame_identity_is_read_from_dom_without_input() -> None:
    h = load_helper()
    mesh = "scene.objects.mesh.place"
    page = FakePage(
        targets={
            h.TERMPP_FINAL_CANVAS_SELECTOR: {
                "found": True,
                "attached": True,
                "visible": True,
                "enabled": True,
                "actionable": True,
                "tag_name": "canvas",
                "matched_leaf_id": mesh,
                "box": {"x": 2.0, "y": 3.0, "width": 200.0, "height": 100.0},
                "frame_id": 917,
                "frame_width": 640,
                "frame_height": 360,
                "view_state_sha256": "a" * 64,
            }
        }
    )
    session = h.PlaywrightChromeSession(
        expected_origin="http://127.0.0.1:43125",
        connect_over_cdp=lambda _base: None,
        injected_page=page,
    )
    session.connect(cdp_http_base="http://127.0.0.1:9222")
    assert session.read_visible_canvas_frame_identity() == {
        "frame_id": 917,
        "width": 640,
        "height": 360,
        "view_state_sha256": "a" * 64,
    }
    assert page.mouse.ops == []
    assert page.keyboard.ops == []


def test_sculpt_cannot_fall_through_to_termpp_canvas() -> None:
    h = load_helper()
    sculpt = "scene.terrain.paint.sculpt"
    page = FakePage(
        targets={
            h.TERMPP_FINAL_CANVAS_SELECTOR: {
                "found": True,
                "attached": True,
                "visible": True,
                "enabled": True,
                "actionable": True,
                "tag_name": "canvas",
                "matched_leaf_id": None,
                "box": {"x": 100.0, "y": 100.0, "width": 200.0, "height": 100.0},
            }
        }
    )
    session = h.PlaywrightChromeSession(
        expected_origin="http://127.0.0.1:43125",
        connect_over_cdp=lambda _base: None,
        injected_page=page,
    )
    session.connect(cdp_http_base="http://127.0.0.1:9222")
    try:
        session.dispatch_physical(
            {"kind": "pointer_click", "target_leaf_id": sculpt, "button": "left"}
        )
        raise AssertionError("sculpt must require data-leaf-id canvas binding")
    except h.ChromeShellDispatchError as exc:
        assert "canvas_target_missing_graph_membership" in str(exc)


def test_stale_dotted_camera_ids_fail_closed() -> None:
    h = load_helper()
    stale = "scene.camera.pointer.rotate"
    page = FakePage(
        targets={
            h.TERMPP_FINAL_CANVAS_SELECTOR: {
                "found": True,
                "attached": True,
                "visible": True,
                "enabled": True,
                "actionable": True,
                "tag_name": "canvas",
                "matched_leaf_id": None,
                "box": {"x": 100.0, "y": 100.0, "width": 200.0, "height": 100.0},
            }
        }
    )
    session = h.PlaywrightChromeSession(
        expected_origin="http://127.0.0.1:43125",
        connect_over_cdp=lambda _base: None,
        injected_page=page,
    )
    session.connect(cdp_http_base="http://127.0.0.1:9222")
    try:
        session.dispatch_physical(
            {"kind": "pointer_click", "target_leaf_id": stale, "button": "right"}
        )
        raise AssertionError("stale dotted camera id must fail closed")
    except h.ChromeShellDispatchError as exc:
        assert "target_not_found" in str(exc)


def test_toolbar_selection_resolves_to_operable_button() -> None:
    h = load_helper()
    tab = "legacy.edit.sculpt.tab"
    uid = h.registered_control_uid(h.load_ui_registry(), tab)
    page = FakePage(
        targets={
            f'[data-leaf-id="{tab}"]': {
                "found": True,
                "attached": True,
                "visible": True,
                "enabled": True,
                "actionable": True,
                "tag_name": "button",
                "matched_leaf_id": tab,
                "matched_control_uid": uid,
                "box": {"x": 20.0, "y": 30.0, "width": 80.0, "height": 24.0},
            },
        }
    )
    session = h.PlaywrightChromeSession(
        expected_origin="http://127.0.0.1:43125",
        connect_over_cdp=lambda _base: None,
        injected_page=page,
    )
    session.connect(cdp_http_base="http://127.0.0.1:9222")
    entry = session.dispatch_physical(
        {"kind": "pointer_click", "target_leaf_id": tab, "button": "left"}
    )
    assert entry.page_x == 60.0
    assert entry.page_y == 42.0


def test_ambiguous_canvas_leaf_ownership_fails_closed() -> None:
    h = load_helper()
    sculpt = "scene.terrain.paint.sculpt"
    page = FakePage(
        targets={
            f'[data-leaf-id="{sculpt}"]': {
                "found": True,
                "multi": True,
                "count": 2,
            }
        }
    )
    session = h.PlaywrightChromeSession(
        expected_origin="http://127.0.0.1:43125",
        connect_over_cdp=lambda _base: None,
        injected_page=page,
    )
    session.connect(cdp_http_base="http://127.0.0.1:9222")
    try:
        session.dispatch_physical(
            {"kind": "pointer_click", "target_leaf_id": sculpt, "button": "left"}
        )
        raise AssertionError("ambiguous canvas ownership must fail closed")
    except h.ChromeShellDispatchError as exc:
        assert "target_ambiguous" in str(exc)


def test_control_uid_mismatch_fails_closed() -> None:
    h = load_helper()
    sculpt = "scene.terrain.paint.sculpt"
    page = FakePage(
        targets={
            f'[data-leaf-id="{sculpt}"]': {
                "found": True,
                "attached": True,
                "visible": True,
                "enabled": True,
                "actionable": True,
                "tag_name": "canvas",
                "matched_leaf_id": sculpt,
                "matched_control_uid": "wrong_control_uid",
                "box": {"x": 100.0, "y": 100.0, "width": 200.0, "height": 100.0},
            }
        }
    )
    session = h.PlaywrightChromeSession(
        expected_origin="http://127.0.0.1:43125",
        connect_over_cdp=lambda _base: None,
        injected_page=page,
    )
    session.connect(cdp_http_base="http://127.0.0.1:9222")
    try:
        session.dispatch_physical(
            {"kind": "pointer_click", "target_leaf_id": sculpt, "button": "left"}
        )
        raise AssertionError("control uid mismatch must fail closed")
    except h.ChromeShellDispatchError as exc:
        assert "target_control_uid_mismatch" in str(exc)


def test_exactly_one_page_enforcement() -> None:
    h = load_helper()

    class P:
        def __init__(self, url):
            self.url = url

    class Browser:
        contexts = [type("C", (), {"pages": [P("http://127.0.0.1:43125/a"), P("http://127.0.0.1:43125/b")]})()]

    session = h.PlaywrightChromeSession(
        expected_origin="http://127.0.0.1:43125",
        connect_over_cdp=lambda _base: Browser(),
    )
    try:
        session.connect(cdp_http_base="http://127.0.0.1:9222")
        raise AssertionError("multi page must fail")
    except h.ChromeShellDispatchError as exc:
        assert "multiple_chrome_app_shell_pages" in str(exc)

    class BrowserNone:
        contexts = [type("C", (), {"pages": [P("http://127.0.0.1:9999/")]})()]

    session2 = h.PlaywrightChromeSession(
        expected_origin="http://127.0.0.1:43125",
        connect_over_cdp=lambda _base: BrowserNone(),
    )
    try:
        session2.connect(cdp_http_base="http://127.0.0.1:9222")
        raise AssertionError("missing page must fail")
    except h.ChromeShellDispatchError as exc:
        assert "PROOF_OPEN:no_existing_chrome_app_shell_page" in str(exc)


def test_console_network_and_trusted_ui_event_capture() -> None:
    h = load_helper()
    page = FakePage()
    session = h.PlaywrightChromeSession(
        expected_origin="http://127.0.0.1:43125",
        connect_over_cdp=lambda _base: None,
        injected_page=page,
    )
    session.connect(cdp_http_base="http://127.0.0.1:9222")
    page.emit_console("log", "shell ready")
    trusted = {
        "schema": "asciiid.ui_event.v1",
        "leaf_id": LEAF_ID,
        "control_uid": "settings_display_termpp_performance_hud",
        "event_type": "pointer",
        "is_trusted": True,
        "interaction_id": "join-1",
    }
    page.emit_event_record(trusted)
    assert any(row["text"] == "shell ready" for row in session.observed_console())
    observed = session.observed_ui_events()
    assert len(observed) == 1
    assert {key: observed[0][key] for key in trusted} == trusted
    assert len(observed[0]["observed_post_data_sha256"]) == 64
    assert any("/api/v1/event.record" in row["url"] for row in session.observed_network())


def test_real_action_response_is_observed_from_html_request() -> None:
    h = load_helper()
    page = FakePage()
    session = h.PlaywrightChromeSession(
        expected_origin="http://127.0.0.1:43125",
        connect_over_cdp=lambda _base: None,
        injected_page=page,
    )
    session.connect(cdp_http_base="http://127.0.0.1:9222")
    result = {
        "schema": "asciiid.runtime_intent_result.v1",
        "ok": True,
        "interaction_id": "join-1",
        "action_kind": "paint_terrain_sculpt",
        "status": "applied",
    }
    page.emit_action_result(result)
    observed = session.observed_runtime_intent_results()
    assert len(observed) == 1
    assert observed[0]["interaction_id"] == "join-1"
    assert observed[0]["observed_response_url"].endswith("/api/v1/runtime-intent")


def test_no_backend_setters_and_no_launch_api_in_source() -> None:
    text = HELPER_PATH.read_text(encoding="utf-8")
    # Executable launch call must be absent (docstrings must not spell it either).
    assert "chromium.launch" not in text
    assert "subprocess.Popen" not in text
    assert "playwright.chromium.launch" not in text
    assert "connect_over_cdp" in text
