#!/usr/bin/env python3
# =============================================================================
# test_fl4588_camera_controls_reachable.py   (FL-4588 / FL-4665)
# =============================================================================
# WHAT THIS FILE GOT WRONG THE FIRST TIME
# ---------------------------------------
# The first version of this test demanded that every camera control named in
# the UI registry be implemented by the shell, and it failed on two of them.
# That was a false alarm produced by reading the registry carelessly.
#
# Registry rows carry a `status` field. Only `live` rows render; `tombstoned`
# and `merged_into` rows are deliberately dead entries kept for the record. All
# six camera rows are tombstoned. The registry was never advertising those
# controls -- it was recording that they are gone. Demanding implementations for
# them measured nothing real.
#
# WHAT ACTUALLY MATTERS
# ---------------------
# The camera genuinely cannot be driven right now. Arrow keys and right-drag
# leave the canvas hash unchanged, and the product says so in its own words:
# "VIEW LOCKED - CAMERA INPUT UNAVAILABLE". That is an honest fail-closed
# surface, not a bug to be silenced.
#
# The danger is the two halves drifting apart. Either half moving alone
# produces a lie:
#
#   * un-tombstone a camera row without wiring it, and the interface
#     advertises a control that does nothing;
#   * delete the VIEW LOCKED banner while the rows are still tombstoned, and a
#     dead camera starts looking like a working one;
#   * wire the camera for real but leave the banner up, and the operator is
#     told a working control is unavailable.
#
# So this file enforces AGREEMENT between the registry's declared camera state
# and the product's visible state, in both directions.
#
# WHAT THIS FILE STILL CANNOT DO
# ------------------------------
# Passing here does NOT mean the camera works. It means the product is telling
# the truth about the camera. Proving the camera responds requires a real run
# where a movement input and a camera turn change successive frame hashes; that
# is `rq153c_runtime_accepts_input` in the FL-4260 coverage gate, and it is red.
# Nothing in this file may be read as evidence for it.
# =============================================================================

from __future__ import annotations

import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_REGISTRY = REPO_ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.json"
SHELL_SRC = REPO_ROOT / "web/asciiid-authoring-shell/src"
FRAME_VIEW = SHELL_SRC / "FrameView.tsx"

# The banner the product shows while the camera cannot be driven.
FAIL_CLOSED_BANNER = "CAMERA INPUT UNAVAILABLE"

# Only rows with this status are presented to the operator. Everything else is
# a historical record, not an offer.
LIVE_STATUS = "live"


def camera_rows() -> list[dict]:
    """Every registry row whose leaf id names a camera concern."""
    registry = json.loads(UI_REGISTRY.read_text(encoding="utf-8"))
    rows: list[dict] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            leaf_id = node.get("leaf_id")
            if isinstance(leaf_id, str) and ".camera." in leaf_id:
                rows.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(registry)
    return rows


def shell_source_text() -> str:
    parts: list[str] = []
    for path in sorted(SHELL_SRC.rglob("*")):
        if path.suffix in {".ts", ".tsx"} and path.is_file():
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


class TheRegistryAndTheProductAgreeAboutTheCamera(unittest.TestCase):
    """A dead camera and a live-looking interface must never coexist."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = camera_rows()
        cls.live = [r for r in cls.rows if r.get("status") == LIVE_STATUS]
        cls.source = shell_source_text()
        cls.frame_view = FRAME_VIEW.read_text(encoding="utf-8")

    def test_the_registry_actually_carries_camera_rows(self):
        # Guards against the checks below passing vacuously if the rows are
        # ever removed outright rather than tombstoned.
        self.assertTrue(self.rows, "the UI registry carries no camera rows at all")

    def test_every_live_camera_row_is_implemented_by_the_shell(self):
        # Tombstoned rows are exempt by design. A LIVE row is a promise to the
        # operator and must be backed by real wiring.
        missing = [r["leaf_id"] for r in self.live if r["leaf_id"] not in self.source]
        self.assertEqual(
            missing, [],
            "these camera rows are marked live but the shell implements them "
            "nowhere, so the interface offers controls that cannot work:\n  "
            + "\n  ".join(missing))

    def test_a_camera_with_no_live_rows_must_say_so_on_screen(self):
        # This is the direction that protects the operator. If nothing is live,
        # the product must not look drivable.
        if self.live:
            self.skipTest("camera rows are live; the fail-closed case is not under test")
        self.assertIn(
            FAIL_CLOSED_BANNER, self.frame_view,
            "every camera row is tombstoned, so the camera cannot be driven, yet "
            "the viewport no longer displays its fail-closed banner. A dead "
            "camera that looks alive is worse than a dead camera that says so.")

    def test_a_live_camera_must_not_still_claim_to_be_unavailable(self):
        # The opposite direction. Wiring the camera without removing the banner
        # tells the operator a working control is unavailable.
        if not self.live:
            self.skipTest("no live camera rows; the wired case is not under test")
        self.assertNotIn(
            FAIL_CLOSED_BANNER, self.frame_view,
            "camera rows are live, so the camera is meant to be drivable, yet "
            "the viewport still shows the fail-closed banner.")


class TheShellCanDriveTheTransaction(unittest.TestCase):
    """The input path must exist and must agree with the banner.

    The shell wiring can land before the registry rows are activated: with every
    row tombstoned the handlers refuse and the banner stays locked, so nothing
    is activated early. What must never happen is the two disagreeing.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.frame_view = FRAME_VIEW.read_text(encoding="utf-8")
        cls.transport = (SHELL_SRC / "transport.ts").read_text(encoding="utf-8")

    def test_the_shell_mirrors_the_services_sealed_tuples(self):
        # The service compares leaf id, control uid and motion together. A shell
        # that sent a different control uid would simply be refused, so these
        # must match the native table exactly.
        for motion, leaf, control in (
            ("yaw", "scene.camera.pose.yaw", "fl4260_row_535__view_yaw"),
            ("pitch", "scene.camera.pose.pitch", "fl4260_row_534__view_pitch"),
            ("zoom", "scene.camera.pose.zoom", "fl4260_row_536__view_zoom"),
        ):
            with self.subTest(motion=motion):
                self.assertIn(leaf, self.transport)
                self.assertIn(control, self.transport)

    def test_physical_keyboard_and_pointer_are_bound(self):
        for handler in ("onKeyDown", "onPointerDown", "onPointerMove", "onPointerUp"):
            with self.subTest(handler=handler):
                self.assertIn(handler, self.frame_view)
        for key in ("ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"):
            with self.subTest(key=key):
                self.assertIn(key, self.frame_view)

    def test_the_shell_sends_deltas_not_absolute_poses(self):
        # Sending an absolute pose would make the browser a second owner of the
        # camera, which is the failure the previous surface was deleted for.
        self.assertIn("event.clientX - origin.x", self.frame_view)
        self.assertIn('sendCameraIntent("yaw"', self.frame_view)

    def test_the_banner_and_the_handlers_read_one_value(self):
        # Both must derive from cameraLive. If they were decided separately the
        # viewport could look drivable while nothing responded.
        self.assertIn("const cameraLive = useMemo(() => cameraIsLive(registry)",
                      self.frame_view)
        self.assertIn("if (!cameraLive) return;", self.frame_view)
        self.assertIn("cameraLive\n            ? \"CAMERA LIVE", self.frame_view)

    def test_the_shell_refuses_a_row_that_is_not_live(self):
        self.assertIn("camera_intent_row_not_live", self.transport)
        self.assertIn('leaf.status !== "live"', self.transport)

    def test_the_shell_rereads_the_authoritative_pose(self):
        # Displaying the requested pose rather than the resulting one would hide
        # clamping, which is exactly what the echoed pose exists to reveal.
        self.assertIn("fetchSnapshot(sessionId)", self.frame_view)


class NothingHereProvesTheCameraResponds(unittest.TestCase):
    """Keep this file's claim bounded, in the file itself."""

    def test_the_runtime_acceptance_gate_is_the_owner_of_that_claim(self):
        # The coverage gate owns the only check that can observe input actually
        # changing the picture. If that gate ever stops requiring it, the
        # agreement checks above would quietly become the strongest camera
        # evidence in the repository, which they must never be.
        gate = (REPO_ROOT / "scripts/fl4260_rq153c_coverage_gate.py").read_text(
            encoding="utf-8")
        self.assertIn("rq153c_runtime_accepts_input", gate)
        self.assertIn("camera_turn", gate)
        self.assertIn("frame_sha256_before", gate)


if __name__ == "__main__":
    unittest.main()
