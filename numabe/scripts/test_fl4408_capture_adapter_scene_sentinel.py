#!/usr/bin/env python3
import importlib.util
import json
import subprocess
from argparse import Namespace
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "fl4408_actual_godot_capture_adapter",
    ROOT / "scripts" / "fl4408_actual_godot_capture_adapter.py",
)
adapter = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(adapter)
_scene_content_sentinel = adapter._scene_content_sentinel
_write_output_frame = adapter._write_output_frame


def test_scene_content_sentinel_rejects_flat_panel() -> None:
    image = Image.new("RGB", (320, 180), (18, 42, 86))

    sentinel = _scene_content_sentinel(image)

    assert sentinel["passed"] is False
    assert sentinel["unique_sampled_colors"] == 1
    assert sentinel["luma_range"] == 0


def test_scene_content_sentinel_accepts_scene_like_panel() -> None:
    image = Image.new("RGB", (320, 180), (20, 34, 58))
    draw = ImageDraw.Draw(image)
    for x in range(0, 320, 8):
        shade = 40 + (x % 96)
        draw.rectangle((x, 90, x + 7, 179), fill=(shade, 80 + (x % 48), 36))
    for y in range(20, 170, 12):
        draw.line((0, y, 319, 179 - y // 2), fill=(180, 220 - (y % 70), 90), width=2)
    for i in range(30):
        x = (i * 37) % 320
        y = 35 + (i * 19) % 120
        draw.ellipse((x, y, x + 5, y + 5), fill=(230, 210, 80))

    sentinel = _scene_content_sentinel(image)

    assert sentinel["passed"] is True
    assert sentinel["unique_sampled_colors"] >= sentinel["thresholds"]["unique_sampled_colors_min"]
    assert sentinel["luma_range"] >= sentinel["thresholds"]["luma_range_min"]


def _scene_like_image(size: tuple[int, int]) -> Image.Image:
    image = Image.new("RGB", size, (22, 36, 62))
    draw = ImageDraw.Draw(image)
    width, height = size
    for x in range(0, width, max(1, width // 40)):
        shade = 45 + (x % 120)
        draw.rectangle((x, height // 2, x + max(1, width // 60), height - 1), fill=(shade, 92, 42))
    for y in range(max(1, height // 8), height - 10, max(1, height // 16)):
        draw.line((0, y, width - 1, height - y // 3), fill=(190, 210 - (y % 80), 100), width=2)
    return image


def _adapter_args(tmp_path: Path, side: str, resolution: str) -> Namespace:
    server_root = tmp_path / "checkout" / "colyseus_server"
    server_identity = {
        "server_commit": "a" * 40,
        "server_requested_root": str(server_root),
        "server_source_root": str(server_root),
        "server_dirty_paths": [],
        "server_runtime_scope_fingerprint": "b" * 64,
        "proof_tool_sha256": "c" * 64,
    }
    return Namespace(
        lane_id="foliage_grass_land",
        side=side,
        frame="panel",
        project=str(tmp_path / "checkout" / "godot_project"),
        capture_sequence="single_frame",
        camera_mode="ortho",
        view_mode="ortho",
        camera_position="[0,0,0]",
        camera_yaw="0",
        camera_pitch="0",
        camera_roll="0",
        camera_fov="60",
        projection_params="{}",
        player_position="[0,0,0]",
        player_yaw="0",
        npc_positions="[]",
        airplane_position="[0,0,0]",
        airplane_yaw="0",
        world_time="0",
        night_state=False,
        feature_flags="[]",
        beam_expected_in_png=False,
        # These unit rows exercise exact-size validation. Fullscreen captures
        # use the display's real size and therefore follow a distinct contract.
        fullscreen=False,
        resolution=resolution,
        move_frames=1,
        settle_frames=1,
        after_frames=1,
        plane_circle_frames=1,
        colyseus_dir=str(server_root),
        shared_current_server_identity=server_identity,
    )


def test_current_capture_size_mismatch_fails_closed(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _scene_like_image((80, 60)).save(run_dir / "panel.png")
    (run_dir / "receipt.json").write_text(json.dumps({"pass": True}), encoding="utf-8")

    args = _adapter_args(tmp_path, "current", "160x120")

    try:
        _write_output_frame(args, run_dir, "sig", "panel", tmp_path / "out.png", tmp_path / "out.json")
    except SystemExit as exc:
        assert "source size mismatch" in str(exc)
    else:
        raise AssertionError("current capture size mismatch should fail closed")


def test_baseline_larger_capture_is_accepted_for_crop_compatible_panels(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _scene_like_image((180, 140)).save(run_dir / "panel.png")
    (run_dir / "receipt.json").write_text(json.dumps({"pass": True}), encoding="utf-8")

    args = _adapter_args(tmp_path, "baseline", "160x120")
    out_png = tmp_path / "out.png"
    out_meta = tmp_path / "out.json"

    _write_output_frame(args, run_dir, "sig", "panel", out_png, out_meta)

    assert out_png.exists()
    metadata = json.loads(out_meta.read_text(encoding="utf-8"))
    assert metadata["scene_content_sentinel"]["passed"] is True


def test_adapter_does_not_reuse_old_complete_capture_marker() -> None:
    src = (ROOT / "scripts" / "fl4408_actual_godot_capture_adapter.py").read_text(encoding="utf-8")

    assert "complete_marker.exists()" not in src
    assert "capture_signature_hash\") == signature_hash" not in src
    assert "_colyseus_dir_for_project" not in src
    assert 'parser.add_argument("--colyseus-dir", default="")' not in src
    assert '"shared_current_server_identity": dict(server_identity)' in src


def _shared_server_checkout(tmp_path: Path) -> Path:
    """Create the smallest executable Git-owned Colyseus source fixture."""
    checkout = tmp_path / "current_checkout"
    server = checkout / "colyseus_server"
    (server / "src").mkdir(parents=True)
    (server / "package.json").write_text('{"scripts":{"start":"tsx src/index.ts"}}\n')
    (server / "src" / "index.ts").write_text("console.log('fixture');\n")
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "config", "user.name", "Adapter Fixture"],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(checkout),
            "config",
            "user.email",
            "adapter-fixture@example.invalid",
        ],
        check=True,
    )
    subprocess.run(["git", "-C", str(checkout), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "commit", "-q", "-m", "server fixture"],
        check=True,
    )
    return server


def test_adapter_rejects_missing_relative_nonexistent_and_ambiguous_server_roots(
    tmp_path: Path,
) -> None:
    """Every path that could revive renderer-derived server ownership fails."""
    invalid_values = (
        "",
        "colyseus_server",
        str(tmp_path / "missing" / "colyseus_server"),
    )
    for value in invalid_values:
        try:
            adapter._shared_current_server_identity(value)
        except SystemExit:
            pass
        else:
            raise AssertionError(f"server root should be rejected: {value!r}")

    checkout = tmp_path / "ambiguous_checkout"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    try:
        adapter._shared_current_server_identity(str(checkout))
    except SystemExit as exc:
        assert "top-level colyseus_server" in str(exc)
    else:
        raise AssertionError("a checkout root must not stand in for its server root")

    nested_server = checkout / "nested" / "colyseus_server"
    (nested_server / "src").mkdir(parents=True)
    (nested_server / "package.json").write_text('{"name":"nested"}\n')
    (nested_server / "src" / "index.ts").write_text("console.log('nested');\n")
    try:
        adapter._shared_current_server_identity(str(nested_server))
    except SystemExit as exc:
        assert "ambiguous" in str(exc)
    else:
        raise AssertionError("nested lookalike server root must fail closed")


def test_adapter_cache_signature_changes_with_shared_server_identity(
    tmp_path: Path,
) -> None:
    """Dirty server bytes must invalidate a previously computed capture key."""
    server = _shared_server_checkout(tmp_path)
    args = _adapter_args(tmp_path, "current", "160x120")
    args.colyseus_dir = str(server)
    args.shared_current_server_identity = adapter._shared_current_server_identity(
        str(server)
    )
    first_signature = adapter._capture_signature(args)
    first_hash = adapter._signature_hash(first_signature)

    (server / "src" / "index.ts").write_text("console.log('changed fixture');\n")
    args.shared_current_server_identity = adapter._shared_current_server_identity(
        str(server)
    )
    second_signature = adapter._capture_signature(args)
    second_hash = adapter._signature_hash(second_signature)

    assert first_hash != second_hash
    assert (
        first_signature["shared_current_server_identity"][
            "server_runtime_scope_fingerprint"
        ]
        != second_signature["shared_current_server_identity"][
            "server_runtime_scope_fingerprint"
        ]
    )
    assert second_signature["shared_current_server_identity"]["server_dirty_paths"]


def test_adapter_cache_signature_rejects_absent_server_identity(
    tmp_path: Path,
) -> None:
    """A renderer-only signature cannot select a canonical cache directory."""
    args = _adapter_args(tmp_path, "current", "160x120")
    del args.shared_current_server_identity
    try:
        adapter._capture_signature(args)
    except SystemExit as exc:
        assert "server identity is required" in str(exc)
    else:
        raise AssertionError("cache signature accepted no server identity")


def test_adapter_cache_signature_changes_with_proof_tool_hash(
    tmp_path: Path,
) -> None:
    """A stale capture tool cannot reuse a server-identical cache entry."""
    args = _adapter_args(tmp_path, "current", "160x120")
    first_hash = adapter._signature_hash(adapter._capture_signature(args))
    args.shared_current_server_identity = dict(
        args.shared_current_server_identity
    )
    args.shared_current_server_identity["proof_tool_sha256"] = "d" * 64
    second_hash = adapter._signature_hash(adapter._capture_signature(args))
    assert first_hash != second_hash


def test_night_temporal_mapping_cannot_forward_grounded_exact_pose_arguments() -> None:
    src = (ROOT / "scripts" / "fl4408_actual_godot_capture_adapter.py").read_text(encoding="utf-8")

    assert 'camera_mode == "night_lighthouse_beam"' in src
    assert 'camera_mode = "plane_air"' in src
    assert "driver_script = DOGFIGHT_DRIVER" in src
    assert "if not night_airborne_capture:" in src
    assert "--exact-player-position" in src
    assert "--exact-camera-position" in src
