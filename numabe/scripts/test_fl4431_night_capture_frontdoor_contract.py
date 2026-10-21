#!/usr/bin/env python3
"""Static and executable contract for the FL-4431 night capture front door.

The former package used an unowned ``--night 1`` argument, reset a diagnostic
jitter recorder from the still-image harness, and accepted receipts without
evidence that the captured frame was both night and freshly presented. Those
three failures made a daytime or stale image look like night evidence.

This contract checks the production sources and then damages each requirement
in memory. Every damaging mutation must make the checker fail; this prevents a
green test whose search accidentally matches unrelated prose elsewhere.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]
WRAPPER_PATH = ROOT / "scripts" / "run_fl4431_content_package.py"
STATIC_CAPTURE_PATH = ROOT / "godot_project" / "tools" / "proofs" / "run_fl4359_static_capture.gd"


def _function_body(source: str, signature: str, next_signature: str) -> str:
    """Return one named Python function so whole-file matches cannot fake it."""
    start = source.find(signature)
    end = source.find(next_signature, start + len(signature))
    if start < 0 or end < 0:
        return ""
    return source[start:end]


def _check_sources(wrapper: str, static_capture: str) -> list[str]:
    """Return human-readable failures for ownership, night, and freshness."""
    failures: list[str] = []
    night_spec_start = wrapper.find('"night": {')
    night_spec_end = wrapper.find('"airplane": {', night_spec_start)
    night_spec = wrapper[night_spec_start:night_spec_end]
    receipt_body = _function_body(wrapper, "def _night_main_receipt(", "def _port_open(")
    run_body = _function_body(wrapper, "def _run_arm(", "def main(")

    if not night_spec:
        failures.append("night surface definition missing")
    if '"--night", "1"' in night_spec:
        failures.append("retired unowned --night 1 spelling restored")
    required_night_args = (
        '"--manual-environment-night"',
        '"--force-weather=clear"',
        '"--force-baseline-feature=night,lighthouse_light,bonfire_light,firefly_light"',
        '"--fl4203-night-bonfire"',
        '"capture_owner": "main_one_shot"',
    )
    for required in required_night_args:
        if required not in night_spec:
            failures.append(f"night surface missing {required}")

    # The normal Main branch must carry the one-shot capture arguments itself.
    # The separate static harness remains confined to the non-night branch.
    required_main_args = (
        'if spec.get("capture_owner") == "main_one_shot":',
        '"--capture-viewport-exit"',
        'f"--capture-path={png_path}"',
        'f"--capture-frames={max(settle_frames + hold_frames, 1)}"',
    )
    for required in required_main_args:
        if required not in run_body:
            failures.append(f"Main one-shot branch missing {required}")
    main_branch_end = run_body.find("else:", run_body.find('if spec.get("capture_owner")'))
    main_branch = run_body[run_body.find('if spec.get("capture_owner")'):main_branch_end]
    if '"--script"' in main_branch or "HARNESS" in main_branch:
        failures.append("night Main branch still invokes the static script harness")

    required_receipt_laws = (
        'line.startswith("MANUAL_ENV_STATE ")',
        '"cycle=false" in line',
        '"phase=night" in line',
        '"weather=clear" in line',
        'metadata.get("owner") == "PixelCameraContext"',
        'metadata.get("metadata_owner") == "PixelCameraContext"',
        'metadata.get("saved_png") == str(png_path)',
        "and actual_night",
        "and fresh_presented",
    )
    for required in required_receipt_laws:
        if required not in receipt_body:
            failures.append(f"night receipt missing {required}")

    if "reset_fl4359_jitter_capture" in static_capture:
        failures.append("still-image harness restored the retired jitter reset")
    if "--fl4359-jitter-capture-path" in static_capture:
        failures.append("still-image harness restored the retired jitter path")
    if "--fl4359-jitter-hold-frames" in static_capture:
        failures.append("still-image harness restored the retired jitter hold flag")
    required_static_laws = (
        'String(runtime_state.get("status", "")) == "ON"',
        "and present_frame_count > 0",
        "and present_age_msec >= 0",
        "and present_age_msec <= 1000",
        'bool(cap["captured"]) and frames >= hold_frames and fresh_presented',
    )
    for required in required_static_laws:
        if required not in static_capture:
            failures.append(f"static receipt missing {required}")
    return failures


def _load_wrapper_module():
    """Load the wrapper without running its CLI so the receipt can be exercised."""
    spec = importlib.util.spec_from_file_location("fl4431_content_package", WRAPPER_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load content-package module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _exercise_night_receipt() -> None:
    """Prove the receipt admits a fresh real night and rejects missing evidence."""
    module = _load_wrapper_module()
    with tempfile.TemporaryDirectory(prefix="fl4431-night-contract-") as temp:
        temp_path = Path(temp)
        png_path = temp_path / "night.png"
        metadata_path = Path(f"{png_path}.pixel_camera.json")
        started_ns = 1_000_000_000
        png_path.write_bytes(b"not-a-real-png-needed-for-receipt-unit-test")
        metadata_path.write_text(json.dumps({
            "owner": "PixelCameraContext",
            "metadata_owner": "PixelCameraContext",
            "saved_png": str(png_path),
            "saved_image_size": [320, 180],
            "frame_index": 42,
        }))
        file_ns = started_ns + 1_000_000
        os.utime(png_path, ns=(file_ns, file_ns))
        os.utime(metadata_path, ns=(file_ns, file_ns))
        log_lines = [
            "GODOT_COLYSEUS_JOINED room=room session=session",
            "MANUAL_ENV_STATE manual test all cycle=false phase=night "
            "clouds=true cloud_shadow=false weather=clear state=0",
            "FORCED_BASELINE_FEATURE night=on",
            "FORCED_BASELINE_FEATURE lighthouse_light=on",
            "FORCED_BASELINE_FEATURE bonfire_light=on",
            "FORCED_BASELINE_FEATURE firefly_light=on",
            f"GODOT_VIEWPORT_PNG_SAVED {png_path} 320x180 owner=PixelCameraContext",
        ]
        accepted = module._night_main_receipt(
            godot_rc=0,
            log_text="\n".join(log_lines),
            png_path=png_path,
            metadata_path=metadata_path,
            capture_started_ns=started_ns,
        )
        if not accepted["pass"]:
            raise AssertionError(f"valid fresh night receipt rejected: {accepted}")

        without_night = module._night_main_receipt(
            godot_rc=0,
            log_text="\n".join(line for line in log_lines if "MANUAL_ENV_STATE" not in line),
            png_path=png_path,
            metadata_path=metadata_path,
            capture_started_ns=started_ns,
        )
        if without_night["pass"]:
            raise AssertionError("receipt accepted capture without actual-night evidence")

        stale = module._night_main_receipt(
            godot_rc=0,
            log_text="\n".join(log_lines),
            png_path=png_path,
            metadata_path=metadata_path,
            capture_started_ns=file_ns + 1,
        )
        if stale["pass"]:
            raise AssertionError("receipt accepted files older than this capture process")


def main() -> int:
    wrapper = WRAPPER_PATH.read_text()
    static_capture = STATIC_CAPTURE_PATH.read_text()
    failures = _check_sources(wrapper, static_capture)
    if failures:
        raise AssertionError("production source contract failed:\n- " + "\n- ".join(failures))

    # Each mutant reproduces one real regression shape. The checker must reject
    # it independently, proving that unrelated source text cannot keep the gate
    # green.
    mutants = {
        "stale_night_flag": (
            wrapper.replace('"--manual-environment-night"', '"--night", "1"', 1),
            static_capture,
        ),
        "jitter_reset_restored": (
            wrapper,
            static_capture.replace(
                "func _run() -> void:",
                'func _run() -> void:\n\treset_fl4359_jitter_capture("")',
                1,
            ),
        ),
        "night_assertion_removed": (
            wrapper.replace("        and actual_night\n", "", 1),
            static_capture,
        ),
        "night_freshness_removed": (
            wrapper.replace("        and fresh_presented\n", "", 1),
            static_capture,
        ),
        "static_freshness_removed": (
            wrapper,
            static_capture.replace(
                'bool(cap["captured"]) and frames >= hold_frames and fresh_presented',
                'bool(cap["captured"]) and frames >= hold_frames',
                1,
            ),
        ),
    }
    survivors = [
        name for name, (mutated_wrapper, mutated_static) in mutants.items()
        if not _check_sources(mutated_wrapper, mutated_static)
    ]
    if survivors:
        raise AssertionError("damaging source mutants survived: " + ", ".join(survivors))

    _exercise_night_receipt()
    print(
        "FL4431_NIGHT_CAPTURE_FRONTDOOR_CONTRACT_PASS "
        f"checks=3 mutants={len(mutants)} executable_receipts=3"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
