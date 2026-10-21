"""FL-4359 screen-recording ownership and frame-exact lifecycle contracts.

The source checks protect the launcher/World ownership boundary. F9 records
rendered game frames in-engine (one PNG plus one TXT per
Engine.get_process_frames step); ffmpeg assembles the .mov at the measured
game fps after the writer thread joins. No screen capture, supervisor process,
or display-rate duplication exists in this path.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MANUAL_PATH = ROOT / "scripts" / "launch_fl4359_manual_play.py"
AIRPLANE_PATH = ROOT / "scripts" / "run_fl4265_airplane_proof.py"
DOGFIGHT_PATH = ROOT / "godot_project" / "tools" / "proofs" / "run_fl4265_airplane_dogfight_proof.gd"
EXTERNAL_MOVEMENT_DRIVER_PATH = ROOT / "scripts" / "adhoc" / "fl4359_external_movement_driver.gd"
MAIN_PATH = ROOT / "godot_project" / "scripts" / "Main.gd"
WORLD_PATH = ROOT / "godot_project" / "scripts" / "World.gd"
CONTROLLER_PATH = ROOT / "godot_project" / "scripts" / "recording" / "ScreenRecordingController.gd"
FRAME_MANIFEST_TEST_PATH = (
    ROOT / "godot_project" / "scripts" / "tests" / "test_fl4359_frame_manifest_contract.gd"
)
PROOF_SCRIPT_RECORDER_TOKENS = (
    "toggle_screen_recording",
    "ASCIICKER_SCREEN_RECORDING_ALLOWED",
    "--fl4359-record-f9-test",
    "record_flight_video",
    "fl4265_crash_recording_route",
)
EXPECTED_RECORDER_PROOF_SCRIPTS = {
    Path("godot_project/tools/proofs/run_fl4265_airplane_dogfight_proof.gd"),
    Path("scripts/adhoc/fl4359_external_movement_driver.gd"),
    Path("scripts/run_fl4265_airplane_proof.py"),
    Path("scripts/run_fl4359_headed_movement.py"),
}


def _proof_script_paths() -> list[Path]:
    candidates: set[Path] = set()
    for root in (
        ROOT / "scripts" / "proofs",
        ROOT / "scripts",
        ROOT / "scripts" / "adhoc",
        ROOT / "godot_project" / "scripts",
        ROOT / "godot_project" / "tools" / "proofs",
    ):
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".js", ".gd"}:
                continue
            rel = path.relative_to(ROOT)
            name = path.name
            if (
                rel.parts[:2] == ("scripts", "proofs")
                or rel.parts[:2] == ("scripts", "adhoc")
                or name.startswith("proof_")
                or re.match(r"run_fl\d+.*\.(py|js|gd)$", name)
            ):
                candidates.add(path)
    return sorted(candidates)


def test_auto_recording_opt_in_is_explicit_and_human_f9_stays_toggleable() -> None:
    manual = MANUAL_PATH.read_text(encoding="utf-8")
    airplane = AIRPLANE_PATH.read_text(encoding="utf-8")
    dogfight = DOGFIGHT_PATH.read_text(encoding="utf-8")
    main = MAIN_PATH.read_text(encoding="utf-8")
    world = WORLD_PATH.read_text(encoding="utf-8")

    assert 'SCREEN_RECORDING_ALLOWED_ENV = "ASCIICKER_SCREEN_RECORDING_ALLOWED"' in manual
    assert "Manual F9 screen" in manual
    assert "recording is togglable in-game" in manual
    assert "if args.allow_screen_recording:" in manual
    assert 'env[SCREEN_RECORDING_ALLOWED_ENV] = "1"' in manual
    assert 'env.pop(SCREEN_RECORDING_ALLOWED_ENV, None)' in manual
    assert manual.index('env[SCREEN_RECORDING_ALLOWED_ENV] = "1"') < manual.index(
        'env.pop(SCREEN_RECORDING_ALLOWED_ENV, None)'
    )

    assert "if args.fl4265_crash_recording_route:" in airplane
    assert 'env["ASCIICKER_SCREEN_RECORDING_ALLOWED"] = "1"' in airplane
    assert 'env.pop("ASCIICKER_SCREEN_RECORDING_ALLOWED", None)' in airplane
    assert airplane.count('env["ASCIICKER_SCREEN_RECORDING_ALLOWED"] = "1"') == 1
    assert 'world.call("toggle_screen_recording", false, true)' in dogfight
    assert 'world.call("toggle_screen_recording")' in main
    assert "func toggle_screen_recording(\n\topen_when_done: bool = true,\n\trequire_launcher_opt_in: bool = false\n)" in world


def test_every_recorder_touching_proof_script_is_enumerated_and_gated() -> None:
    proof_scripts = _proof_script_paths()
    recorder_scripts = {
        path.relative_to(ROOT)
        for path in proof_scripts
        if any(
            token in path.read_text(encoding="utf-8")
            for token in PROOF_SCRIPT_RECORDER_TOKENS
        )
    }
    assert recorder_scripts == EXPECTED_RECORDER_PROOF_SCRIPTS

    for rel in recorder_scripts:
        text = (ROOT / rel).read_text(encoding="utf-8")
        if rel.suffix == ".gd":
            assert 'world.call("toggle_screen_recording", false)' not in text
            assert 'screen_recording_world.call("toggle_screen_recording", false)' not in text
            assert 'toggle_screen_recording", false, true)' in text

    headed = (ROOT / "scripts/run_fl4359_headed_movement.py").read_text(
        encoding="utf-8"
    )
    external = EXTERNAL_MOVEMENT_DRIVER_PATH.read_text(encoding="utf-8")
    assert 'cmd.append("--fl4359-record-f9-test")' in headed
    assert 'env["ASCIICKER_SCREEN_RECORDING_ALLOWED"] = "1"' in headed
    assert 'screen_recording_world.call("toggle_screen_recording", false, true)' in external
    assert '\n\t\tscreen_recording_started = bool(' in external
    assert '\n\t\t\tscreen_recording_started = bool(' not in external


def test_recorder_lifecycle_owned_by_controller_with_async_readback() -> None:
    # Delete-first rework: capture ownership moved from World.gd to
    # ScreenRecordingController.gd with F9AsyncReadbackRing. World keeps only
    # the F9 input toggle adapter. This mirrors the one-owner source guard in
    # godot_project/scripts/tests/test_f9_async_readback_ring.gd.
    assert CONTROLLER_PATH.is_file()
    controller = CONTROLLER_PATH.read_text(encoding="utf-8")
    world = WORLD_PATH.read_text(encoding="utf-8")

    # Sync viewport readback is gone from both owners.
    assert "get_texture().get_image()" not in controller
    assert "get_texture().get_image()" not in world

    # The controller issues capture through the async readback ring.
    assert "F9AsyncReadbackRing" in controller
    assert "_ring.request(" in controller
    assert "rd_async_texture_get_data_async" in controller

    # Manifest helpers live on the controller, not on World.
    assert "static func frame_manifest_frame_line(" in controller
    assert "static func frame_compact_pair(" in controller

    # World delegates instead of owning capture.
    assert "func _start_screen_recording(" not in world
    assert "ScreenRecordingController" in world
    assert "func toggle_screen_recording(" in world


def test_frame_manifest_wrapper_contract_is_guarded() -> None:
    text = FRAME_MANIFEST_TEST_PATH.read_text(encoding="utf-8")
    assert "frame_manifest_frame_line" in text
    assert "frame: seq=" in text
    assert "frame_00007.png" in text


def test_recording_title_arg_reaches_game_through_recorder_env_door() -> None:
    headed = (ROOT / "scripts/run_fl4359_headed_movement.py").read_text(
        encoding="utf-8"
    )
    controller = CONTROLLER_PATH.read_text(encoding="utf-8")
    # The runner accepts the operator title next to --record-flight-video.
    assert '"--recording-title"' in headed
    # It forwards the title through the same environment door as the other
    # recorder options, and sets the git identity beside it.
    assert 'env["ASCIICKER_SCREEN_RECORDING_TITLE"]' in headed
    assert 'env["ASCIICKER_SCREEN_RECORDING_GIT_IDENTITY"]' in headed
    assert "def _recording_git_identity(" in headed
    # The controller consumes that door and composes the identity purely.
    assert "static func compose_recording_basename(" in controller
    assert "static func recording_movie_name(" in controller
    assert "static func sanitize_recording_title(" in controller
    assert "ASCIICKER_SCREEN_RECORDING_TITLE" in controller
    assert "ASCIICKER_SCREEN_RECORDING_GIT_IDENTITY" in controller
    # The old stamp-only literal is gone with no fallback.
    assert "FL4359_screen_recording_%s" not in controller
    # FL-4895: the single detached bundle finalizer burns the title band into
    # every recording.mov frame from manifest identity and refuses the movie
    # when a title field is missing.
    finalizer = (ROOT / "scripts" / "recording" / "f9_finalize_bundle.py").read_text(encoding="utf-8")
    assert "def missing_title_fields(identity: dict)" in finalizer
    assert "title_field_missing:" in finalizer
    assert "func _finalize_bundle(reason: String, open_when_done: bool)" in controller
    assert controller.count("_finalize_bundle(\"") == 3  # stop, restart, exit


def test_manual_recording_is_continuous_and_segments_mode_transitions() -> None:
    controller = CONTROLLER_PATH.read_text(encoding="utf-8")
    # Proof routes retain the bounded cap; human F9 has no implicit cap and
    # records changes as settings segments without rewriting its first-frame id.
    assert 'const _SCREEN_RECORD_MAX_FRAMES_ENV := "ASCIICKER_SCREEN_RECORDING_MAX_FRAMES"' in controller
    assert "func _recording_frame_limit(require_launcher_opt_in: bool) -> int:" in controller
    assert "if require_launcher_opt_in:\n\t\treturn _SCREEN_RECORD_MAX_FRAMES" in controller
    assert 'return 0\n\treturn maxi(0, int(raw))' in controller
    assert '"max_frames: %s"' in controller
    assert "static func settings_segments(rows: Array) -> Array" in controller
    assert "var mov_name := recording_movie_name({\"name\": identity})" in controller
    assert '"mov": String(_identity.get("mov_name", BUNDLE_MOV_NAME))' in controller
    # A later projection transition is metadata, while a first-frame mismatch
    # remains the only identity conflict that can make a bundle incomplete.
    assert "Only a first-frame mismatch remains a naming conflict." in controller
    assert '"seq": int(first.get("seq", -1))' in controller
    assert '_manual_session = not require_launcher_opt_in' in controller
    assert '_identity["manual_session"] = _manual_session' in controller
    assert 'if not bool(identity.get("manual_session", false)):' in controller


def test_recording_git_identity_uses_short_head_verbatim() -> None:
    # Lane FF-DPR-20260920-25: the F9 name carries `git rev-parse --short
    # HEAD` verbatim, never a fixed-width truncation of the full head.
    headed = (ROOT / "scripts/run_fl4359_headed_movement.py").read_text(
        encoding="utf-8"
    )
    assert '["git", "rev-parse", "--short", "HEAD"]' in headed
    assert '"git_head_short": git_head_short' in headed
    assert "head[:7]" not in headed
    assert 'short = str((repo_provenance or {}).get("git_head_short", "") or "")' in headed

    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    import run_fl4359_headed_movement as headed_module

    assert (
        headed_module._recording_git_identity(
            {"git_head_short": "0faa0cb90", "git_dirty": True}
        )
        == "0faa0cb90-dirty"
    )
    assert (
        headed_module._recording_git_identity(
            {"git_head_short": "0faa0cb90", "git_dirty": False}
        )
        == "0faa0cb90-clean"
    )
    assert headed_module._recording_git_identity({}) == "unknown"
    assert headed_module._recording_git_identity({"git_head_short": ""}) == "unknown"
