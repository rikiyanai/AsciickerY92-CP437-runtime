#!/usr/bin/env python3
# =============================================================================
# test_fl4588_camera_intent_transaction.py   (FL-4588 / FL-4665)
# =============================================================================
# WHY THIS FILE EXISTS
# --------------------
# The camera-intent transaction was first reported as "proven at the transaction
# layer" on the strength of a one-off script run whose output survived only in a
# commit message. That is not evidence anyone can re-check, and it left the
# repository with no gate that would notice the contract regressing.
#
# These tests read the shipped source and assert the contract that source
# states. They are cheap, they run without a live backend, and they fail loudly
# if any of the specific defects found in review come back.
#
# WHAT THE TRANSACTION IS
# -----------------------
# The browser shell may ask the editor to move its camera. It sends an INTENT
# ("yaw by this much"), never a camera position, because a shell that held a
# position would be a second owner of the camera and the two could disagree --
# the exact failure the previous camera surface was deleted for.
#
# WHAT THIS FILE DOES NOT CLAIM
# -----------------------------
# Nothing here shows a camera turning on screen. Every camera row in the UI
# registry is currently tombstoned, so the service refuses camera mutation
# outright, and it should. Proving the camera responds needs a continuous headed
# run where input changes successive frame hashes; that is
# rq153c_runtime_accepts_input, and it is red.
# =============================================================================

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_CPP = REPO_ROOT / "apps/asciicker-presenter/asciiid_application_service.cpp"
SERVICE_H = REPO_ROOT / "apps/asciicker-presenter/asciiid_application_service.h"
EDITOR_CPP = REPO_ROOT / "apps/asciicker-presenter/asciiid.cpp"
HTTP_CPP = REPO_ROOT / "apps/asciicker-presenter/asciiid_authoring_http.cpp"
UI_REGISTRY = REPO_ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.json"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def camera_dispatch_block() -> str:
    """Just the camera branch of the typed-action dispatcher."""
    source = read(SERVICE_CPP)
    start = source.index('action_kind->valuestring, "camera_intent"')
    end = source.index("return CommitCameraIntent(", start)
    return source[start:end]


def code_only(block: str) -> str:
    """The block with // comments removed.

    Assertions about what the code PERMITS must read the code, not the prose
    around it. A comment explaining that a name was removed contains that name,
    so checking the raw text would report the explanation as the defect -- which
    is exactly what happened the first time this test was written.
    """
    return "\n".join(re.sub(r"//.*$", "", line) for line in block.splitlines())


def registry_camera_rows() -> list[dict]:
    registry = json.loads(read(UI_REGISTRY))
    rows: list[dict] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            leaf = node.get("leaf_id")
            if isinstance(leaf, str) and ".camera." in leaf:
                rows.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(registry)
    return rows


class TheDispatchIsASealedTuple(unittest.TestCase):
    """Leaf, control, and motion must match together or not at all."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.block = camera_dispatch_block()

    def test_each_permitted_row_binds_leaf_control_and_motion(self):
        # A table of leaf ids alone would let a forged control speak for a real
        # leaf, and would let the yaw row ask for a different motion entirely.
        for leaf, control, motion in (
            ("scene.camera.pose.yaw", "fl4260_row_535__view_yaw", "yaw"),
            ("scene.camera.pose.pitch", "fl4260_row_534__view_pitch", "pitch"),
            ("scene.camera.pose.zoom", "fl4260_row_536__view_zoom", "zoom"),
        ):
            with self.subTest(leaf=leaf):
                self.assertRegex(
                    self.block,
                    re.escape(f'{{"{leaf}",') + r"\s*" + re.escape(f'"{control}",')
                    + r"\s*" + re.escape(f'"{motion}"}}'),
                    f"{leaf} is not bound to its control and motion as one tuple")

    def test_all_three_tuple_components_are_compared(self):
        for field in ("leaf_id->valuestring", "control_uid->valuestring",
                      "motion->valuestring"):
            with self.subTest(field=field):
                self.assertIn(field, self.block,
                              f"{field} is never compared, so it is unchecked")

    def test_a_nonexistent_leaf_is_not_permitted(self):
        # scene.camera.move.planar was named by an earlier version and does not
        # exist in the registry at all. Read the code, not the comment that
        # records its removal.
        code = code_only(self.block)
        self.assertNotIn("scene.camera.move.planar", code)
        declared = {row["leaf_id"] for row in registry_camera_rows()}
        dispatchable = re.findall(r'\{"(scene\.camera\.[a-z_.]+)"', code)
        self.assertTrue(dispatchable, "no dispatchable camera leaf found at all")
        for leaf in dispatchable:
            with self.subTest(leaf=leaf):
                self.assertIn(leaf, declared,
                              f"{leaf} is dispatchable but the registry never declares it")

    def test_an_unmatched_tuple_is_rejected_with_its_own_reason(self):
        self.assertIn("camera_intent_tuple_rejected", self.block)

    def test_motions_without_a_registry_row_are_unreachable(self):
        # The native owner also understands forward and strafe, but no registry
        # row authorises them. They must not be dispatchable, because inventing
        # a leaf for them is the fabrication the tuple table prevents.
        code = code_only(self.block)
        for motion in ('"forward"', '"strafe"'):
            with self.subTest(motion=motion):
                self.assertNotIn(motion, code)


class ATombstonedRowCannotMutate(unittest.TestCase):
    """The endpoint must not be reachable while the control is retired."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.block = camera_dispatch_block()

    def test_the_service_consults_registry_row_status(self):
        self.assertIn("registry_row_is_live_", self.block,
                      "camera mutation never checks whether the row is live, so it "
                      "is a hidden transaction surface rather than a product action")
        self.assertIn("camera_intent_registry_row_not_live", self.block)

    def test_an_unbound_status_provider_refuses_rather_than_assumes_live(self):
        self.assertIn("camera_intent_registry_status_unavailable", self.block)

    def test_the_http_layer_records_which_rows_are_live(self):
        http = read(HTTP_CPP)
        self.assertIn("live_controls_", http)
        self.assertIn('std::strcmp(status, "live")', http)
        self.assertIn("set_registry_row_is_live", http)

    def test_every_camera_row_is_tombstoned_today_so_mutation_is_refused(self):
        # This records the CURRENT product state, and it is why the transaction
        # is unreachable. When the rows are activated this test should be
        # updated in the same change that wires the shell, never before.
        rows = registry_camera_rows()
        self.assertTrue(rows)
        live = [r["leaf_id"] for r in rows if r.get("status") == "live"]
        self.assertEqual(
            live, [],
            "camera rows are now live; the shell input wiring and the headed "
            "temporal proof must land in the same change, and VIEW LOCKED must "
            "come down only when the camera genuinely responds")


class TheZoomBoundHasOneOwner(unittest.TestCase):
    """Two owners of a range means the range is not actually known."""

    def test_the_transaction_does_not_restate_the_bound(self):
        source = read(EDITOR_CPP)
        start = source.index("CommitAsciiidCameraIntent(")
        end = source.index("CommitAsciiidSelectTerrainTool(", start)
        block = source[start:end]
        # The earlier version invented 1..64 while the editor clamped 0.25..32.
        self.assertNotIn("64.0f", block)
        self.assertIn("ClampInteractiveFontSize()", block,
                      "zoom must be clamped by the editor's own clamp, not a copy")

    def test_the_shared_maximum_replaced_the_scattered_literal(self):
        source = read(EDITOR_CPP)
        self.assertIn("kMaxInteractiveFontSize = 32.0f", source)
        self.assertIn("font_size > kMaxInteractiveFontSize", source)
        self.assertIn("kMinInteractiveFontSize, kMaxInteractiveFontSize", source)


class TheOwnerAndItsLimits(unittest.TestCase):
    """One camera owner, with the limits stated where the owner lives."""

    @classmethod
    def setUpClass(cls) -> None:
        source = read(EDITOR_CPP)
        start = source.index("CommitAsciiidCameraIntent(")
        cls.block = source[start:source.index("CommitAsciiidSelectTerrainTool(", start)]

    def test_it_writes_the_editors_own_camera_variables(self):
        # Any other target would be a second camera.
        for name in ("rot_yaw", "rot_pitch", "pos_x", "pos_y", "font_size"):
            with self.subTest(variable=name):
                self.assertIn(name, self.block)

    def test_pitch_cannot_pass_the_poles(self):
        self.assertIn("next < 1.0f", self.block)
        self.assertIn("next > 89.0f", self.block)

    def test_yaw_wraps_instead_of_growing_without_bound(self):
        self.assertIn("next += 360.0f", self.block)
        self.assertIn("next -= 360.0f", self.block)

    def test_an_unknown_motion_is_refused(self):
        self.assertIn("camera_intent_motion_unknown", self.block)

    def test_changed_is_measured_rather_than_assumed(self):
        # "changed" must come from comparing the pose before and after, so a
        # clamped motion reports unchanged instead of claiming it moved.
        self.assertIn("prior_yaw", self.block)
        self.assertIn("rot_yaw != prior_yaw", self.block)


class TheTransactionKeepsDocumentActionsUsable(unittest.TestCase):
    """Moving the view must not invalidate pending document edits."""

    def test_camera_moves_advance_the_display_revision_only(self):
        source = read(SERVICE_CPP)
        start = source.index("AsciiidApplicationService::CommitCameraIntent(")
        block = source[start:source.index("AsciiidApplicationService::CommitSelectTerrainTool(",
                                          start)]
        self.assertIn("++display_revision_", block)
        self.assertNotIn("++state_revision_", block,
                         "bumping the state revision would fail every pending "
                         "document action the moment the operator nudged the view")

    def test_a_missing_native_owner_fails_closed(self):
        source = read(SERVICE_CPP)
        self.assertIn("camera_intent_owner_unavailable", source)

    def test_the_amount_bound_is_written_as_an_accept_range(self):
        # Testing the acceptable range rejects NaN too, because NaN compares
        # false against every bound. Testing the unacceptable range would let
        # NaN through.
        block = camera_dispatch_block()
        self.assertIn("requested >= -1024.0 && requested <= 1024.0", block)
        self.assertIn("camera_intent_amount_out_of_range", block)


if __name__ == "__main__":
    unittest.main()
