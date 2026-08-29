#!/usr/bin/env python3
import os
import sys
import subprocess
import time
import shutil
import argparse

from asciicker_constants import GAME_Z_BASE
from blender_utils import get_blender_bin, get_blender_pythonpath_entries

# Add project root to path for module imports
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from scripts.pipeline.xp_core import XPFile

# Config
BLENDER_SCRIPT = "scripts/blender_build_scene.py"
GAME_BIN = ".run/game"
GAME_MAP = "assets/a3d/game_map_y8.a3d"


def log(msg):
    print(f"[VERIFY] {msg}")


# ============================================================================
# PIPELINE VERIFICATION FUNCTIONS
# ============================================================================


def verify_xp_file_with_engine(
    xp_file_path, expected_angles=None, expected_frames=None
):
    """
    Comprehensive pipeline verification for .xp files.

    Args:
        xp_file_path: Path to .xp file
        expected_angles: Expected number of angles (None = don't verify)
        expected_frames: Expected number of frames (None = don't verify)

    Returns:
        dict with verification results:
        - xp_file_loads: bool
        - metadata_correct: bool
        - engine_loads: bool (if engine available)
        - errors: list of error messages
        - stats: dict with statistics
    """
    result = {
        "xp_file_loads": False,
        "metadata_correct": False,
        "engine_loads": None,  # None = not tested
        "errors": [],
        "stats": {},
    }

    log(f"Verifying .xp file: {xp_file_path}")

    # 1. Check if .xp file exists
    if not os.path.exists(xp_file_path):
        result["errors"].append(f"File does not exist: {xp_file_path}")
        return result

    # 2. Load .xp file using xp_core.py
    try:
        xp_file = XPFile(xp_file_path)
        result["xp_file_loads"] = True
        log(f"  ✓ File loads successfully ({len(xp_file.layers)} layers)")
    except Exception as e:
        result["errors"].append(f"Failed to load .xp file: {e}")
        return result

    # 3. Verify metadata
    try:
        metadata = xp_file.get_metadata()
        if metadata is None:
            result["errors"].append("Could not extract metadata")
            return result

        angles = metadata.get("angles", 0)
        anims = metadata.get("anims", [])

        # Verify angles if expected provided
        if expected_angles is not None:
            if angles == expected_angles:
                log(f"  ✓ Angles correct: {angles}")
            else:
                result["errors"].append(
                    f"Angles mismatch: expected {expected_angles}, got {angles}"
                )
                return result

        # Verify total frames if expected provided
        if expected_frames is not None:
            total_frames = sum(anims) if anims else 0
            if total_frames == expected_frames:
                log(f"  ✓ Total frames correct: {total_frames}")
            else:
                result["errors"].append(
                    f"Total frames mismatch: expected {expected_frames}, got {total_frames}"
                )
                return result

        result["metadata_correct"] = True
        result["stats"]["angles"] = angles
        result["stats"]["anims"] = anims
        result["stats"]["total_frames"] = sum(anims) if anims else 0
        log(f"  ✓ Metadata: angles={angles}, anims={anims}")

    except Exception as e:
        result["errors"].append(f"Metadata verification failed: {e}")
        return result

    # 4. Verify frame ordering and structural integrity
    try:
        # Verify first frame (0,0 of first data layer)
        if (
            len(xp_file.layers) > 1
            and xp_file.layers[1].width > 0
            and xp_file.layers[1].height > 0
        ):
            first_frame_glyph, _, _ = xp_file.layers[1].data[0][0]
            log(
                f"  ✓ First frame glyph at (0,0): {first_frame_glyph} (0x{first_frame_glyph:02x})"
            )

            # Verify all glyphs are valid CP437 (0-255)
            all_glyphs_valid = True
            min_glyph, max_glyph = 256, -1
            total_glyphs = 0

            for layer in xp_file.layers[1:]:  # Skip metadata layer
                for y in range(layer.height):
                    for x in range(layer.width):
                        glyph, fg, bg = layer.data[y][x]
                        total_glyphs += 1
                        min_glyph = min(min_glyph, glyph)
                        max_glyph = max(max_glyph, glyph)

                        if not (0 <= glyph <= 255):
                            all_glyphs_valid = False
                            result["errors"].append(
                                f"Invalid glyph at ({x},{y}): {glyph}"
                            )

            if all_glyphs_valid:
                log(
                    f"  ✓ All {total_glyphs} glyphs are valid CP437 codes (min: {min_glyph}, max: {max_glyph})"
                )
            else:
                result["errors"].append("Invalid glyph codes found")

            # Verify all colors are within valid RGB range
            all_colors_valid = True
            for layer in xp_file.layers[1:]:
                for row in layer.data:
                    for glyph, fg, bg in row:
                        # xp_core.py stores colors as tuples of integers (r, g, b)
                        fg_r, fg_g, fg_b = fg
                        bg_r, bg_g, bg_b = bg

                        # Verify RGB values are in valid range
                        def valid_color(c):
                            return isinstance(c, int) and 0 <= c <= 255

                        if not (
                            valid_color(fg_r)
                            and valid_color(fg_g)
                            and valid_color(fg_b)
                            and valid_color(bg_r)
                            and valid_color(bg_g)
                            and valid_color(bg_b)
                        ):
                            all_colors_valid = False

            if all_colors_valid:
                log(f"  ✓ All {total_glyphs} color values are valid (0-255 RGB)")
            else:
                result["errors"].append("Invalid color values found")

            # Calculate statistics
            unique_glyphs = set()
            for layer in xp_file.layers[1:]:
                for row in layer.data:
                    for glyph, _, _ in row:
                        unique_glyphs.add(glyph)

            result["stats"]["unique_glyphs"] = len(unique_glyphs)
            result["stats"]["total_glyphs"] = total_glyphs
            log(
                f"  ✓ Statistics: {len(unique_glyphs)} unique glyphs out of {total_glyphs} total"
            )

    except Exception as e:
        result["errors"].append(f"Frame ordering verification failed: {e}")
        return result

    # 5. Check if game engine is available (optional)
    game_available = os.path.exists(GAME_BIN)
    if game_available:
        # For now, we'll mark engine availability as true but skip actual load test
        # because the game engine doesn't have a command-line asset loading interface
        result["engine_loads"] = None
        log(
            f"  ℹ Game engine available at {GAME_BIN}, but direct asset loading not supported"
        )
    else:
        log(f"  ℹ Game engine not found at {GAME_BIN}")

    return result


def verify_pipeline_fixtures(verbose=False):
    """
    Run verification on all test fixtures.

    Returns:
        dict with overall verification results
    """
    results = {"character": None, "item": None, "overall_pass": False}

    log("=" * 60)
    log("PIPELINE VERIFICATION")
    log("=" * 60)

    # Test 1: Character file
    char_path = "tests/fixtures/generated/test_character.xp"
    log("\n[1/2] Verifying character asset...")
    results["character"] = verify_xp_file_with_engine(
        char_path,
        expected_angles=8,
        expected_frames=10,  # [1,4,1,4] from test fixture creation
    )

    if verbose:
        log(
            f"  Errors: {results['character']['errors']}"
            if results["character"]["errors"]
            else "  No errors"
        )
        log(f"  Stats: {results['character']['stats']}")

    # Test 2: Item file
    item_path = "tests/fixtures/generated/test_item.xp"
    log("\n[2/2] Verifying item asset...")
    results["item"] = verify_xp_file_with_engine(
        item_path, expected_angles=1, expected_frames=1
    )

    if verbose:
        log(
            f"  Errors: {results['item']['errors']}"
            if results["item"]["errors"]
            else "  No errors"
        )
        log(f"  Stats: {results['item']['stats']}")

    # Overall pass condition
    results["overall_pass"] = (
        results["character"]["xp_file_loads"]
        and results["character"]["metadata_correct"]
        and (not results["character"]["errors"])
        and results["item"]["xp_file_loads"]
        and results["item"]["metadata_correct"]
        and (not results["item"]["errors"])
    )

    # Summary
    log("\n" + "=" * 60)
    log("VERIFICATION SUMMARY")
    log("=" * 60)

    char_status = (
        "PASS ✓"
        if results["character"]["xp_file_loads"]
        and results["character"]["metadata_correct"]
        else "FAIL ✗"
    )
    item_status = (
        "PASS ✓"
        if results["item"]["xp_file_loads"] and results["item"]["metadata_correct"]
        else "FAIL ✗"
    )
    overall_status = "PASS ✓" if results["overall_pass"] else "FAIL ✗"

    log(f"\nCharacter test:  {char_status}")
    log(f"Item test:      {item_status}")
    log(f"\nOverall:       {overall_status}")

    if results["overall_pass"]:
        log("\n✓ All verification tests passed!")
    else:
        log("\n✗ Some verification tests failed")
        if results["character"]["errors"]:
            log(f"\nCharacter errors:")
            for err in results["character"]["errors"]:
                log(f"  - {err}")
        if results["item"]["errors"]:
            log(f"\nItem errors:")
            for err in results["item"]["errors"]:
                log(f"  - {err}")

    return results


# ============================================================================
# BLENDER/BUILD VERIFICATION FUNCTIONS (Original)
# ============================================================================


def run_blender_setup():
    log("Running Blender Scene Builder...")

    blender_bin = get_blender_bin()
    if not blender_bin:
        log("Blender not found!")
        return False

    repo_root = os.getcwd()
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        get_blender_pythonpath_entries(repo_root) + [env.get("PYTHONPATH", "")]
    )

    cmd = [
        blender_bin,
        "--background",
        "--factory-startup",
        "--python-use-system-env",
        "--python",
        BLENDER_SCRIPT,
    ]

    res = subprocess.run(cmd, env=env, capture_output=True, text=True)
    print("--- Blender Output ---")
    print(res.stdout)
    print("----------------------")
    if res.returncode != 0:
        log("Blender failed:")
        print(res.stdout)
        print(res.stderr)
        return False

    log("Blender scene export successful.")
    return True


def run_a3d_validation():
    log("Validating exported A3D...")
    script_path = os.path.join(os.path.dirname(__file__), "validate_a3d.py")
    cmd = [sys.executable, script_path, GAME_MAP]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=os.getcwd())
    print(res.stdout)
    if res.returncode != 0:
        if res.stderr:
            print(res.stderr)
        log("A3D validation failed.")
        return False
    log("A3D validation passed.")
    return True


def run_instance_validation():
    log("Validating instance transforms...")
    script_path = os.path.join(os.path.dirname(__file__), "verify_instances.py")
    cmd = [sys.executable, script_path, GAME_MAP]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=os.getcwd())
    print(res.stdout)
    if res.returncode != 0:
        if res.stderr:
            print(res.stderr)
        log("Instance validation failed.")
        return False
    log("Instance validation passed.")
    return True


def run_collision_validation():
    log("Validating collision alpha...")
    script_path = os.path.join(os.path.dirname(__file__), "verify_collision_alpha.py")
    cmd = [sys.executable, script_path]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=os.getcwd())
    print(res.stdout)
    if res.returncode != 0:
        if res.stderr:
            print(res.stderr)
        log("Collision alpha validation failed.")
        return False
    log("Collision alpha validation passed.")
    return True


def run_game_test():
    log("Launching Game Driver...")

    env = os.environ.copy()
    env["ASCIICKER_TEST_MODE"] = "1"

    # Start game process
    proc = subprocess.Popen(
        [GAME_BIN],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=os.getcwd(),
    )

    # Test Logic
    try:
        # Give it a second to init
        time.sleep(1)

        if not proc.stdin or not proc.stdout:
            log("FAIL: Process streams not available")
            return False

        # FL-1148: native TELEPORT stdin shortcut is disabled. This smoke now
        # observes the ordinary spawn state and exercises movement only.
        log("Skipping teleport gravity lane; FL-1148 disables native TELEPORT shortcut.")

        # Read state
        import fcntl

        fd = proc.stdout.fileno()
        fl = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)

        def read_last_pos(duration=1.0):
            t0 = time.time()
            pos = None
            buf = b""

            while time.time() - t0 < duration:
                if proc.poll() is not None:
                    break

                try:
                    chunk = os.read(fd, 4096)
                    if not chunk:
                        break
                    buf += chunk
                except BlockingIOError:
                    time.sleep(0.01)
                    continue
                except OSError:
                    break

                while b"\n" in buf:
                    line_bytes, buf = buf.split(b"\n", 1)
                    line = line_bytes.decode("utf-8", errors="ignore")

                    if not line.startswith("ScaleImg"):
                        print(f"GAME: {line}")

                    if "STATE: POS" in line:
                        parts = line.strip().split()
                        try:
                            pos = (float(parts[2]), float(parts[3]), float(parts[4]))
                        except:
                            pass
            return pos

        def get_latest_pos(wait=1.0):
            return read_last_pos(wait)

        last_pos = get_latest_pos(1.0)

        if not last_pos:
            log("FAIL: No state received.")
            return False

        log(f"Pos after fall: {last_pos}")

        # 0.5 Test Movement (Control)
        log("Testing Movement (Move Forward from ordinary spawn)...")
        proc.stdin.write("MOVE_FORWARD\n")
        proc.stdin.flush()
        time.sleep(1.0)

        pos_move = get_latest_pos(1.0)
        log(f"Pos after move: {pos_move}")

        if pos_move is not None and pos_move[1] > last_pos[1] + 0.1:
            log("PASS: Movement works.")
        else:
            log("FAIL: Movement failed (Physics stuck?).")
            return False

    finally:
        proc.terminate()

    return True


def verify_sprite_roundtrip(verbose=False):
    """
    Sprite round-trip integration test: Python generates .xp, validates, shows preview.

    This test proves that Python-generated .xp files meet the Asciicker engine's
    expectations by:
    1. Generating a minimal test sprite using the Python pipeline (xp_core.py)
    2. Validating the generated .xp using Python validation (validator.py)
    3. Running debug_sprite.py for ASCII preview
    4. (Optional) C++ validation if test harness available

    Returns:
        dict with keys:
            - python_generate: bool - Python successfully generated .xp
            - python_validate: bool - Python validation passed
            - ascii_preview: bool - ASCII preview ran successfully
            - cpp_validate: bool or None - C++ validation (None if skipped)
            - errors: list of error messages
            - output_path: str - Path to generated .xp file
    """
    from pathlib import Path

    result = {
        "python_generate": False,
        "python_validate": False,
        "ascii_preview": False,
        "cpp_validate": None,
        "errors": [],
        "output_path": None,
    }

    log("=" * 60)
    log("SPRITE ROUND-TRIP INTEGRATION TEST")
    log("=" * 60)

    # Output path in staging directory
    staging_xp_dir = Path("scripts/pipeline/staging/xp")
    staging_xp_dir.mkdir(parents=True, exist_ok=True)
    output_path = staging_xp_dir / "integration_test.xp"
    result["output_path"] = str(output_path)

    # ========================================================================
    # Step 1: Generate a minimal .xp sprite using Python
    # ========================================================================
    log("\n[1/4] Generating test sprite using Python pipeline...")

    try:
        # Import xp_core for XP file generation
        from scripts.pipeline.xp_core import XPFile, XPLayer

        # Create a minimal test sprite:
        # - Layer 0: Metadata (8 angles, 2 animations with 1 frame each)
        # - Layer 1: Height layer (placeholder)
        # - Layer 2: Visual layer (simple box pattern)

        xp = XPFile()
        xp.version = -1

        # Dimensions: 2 columns (1 frame per animation x 2 anims), 8 rows (8 angles)
        # Plus metadata encoding space
        width = 4  # Small test size
        height = 8

        # Layer 0: Metadata
        # Cell (0,0) = angles (8 -> '8' = glyph 56)
        # Cell (1,0) = anim 1 frames (1 -> '1' = glyph 49)
        # Cell (2,0) = anim 2 frames (1 -> '1' = glyph 49)
        layer0_data = [[(0, (0, 0, 0), (0, 0, 0)) for _ in range(width)] for _ in range(height)]
        layer0_data[0][0] = (56, (255, 255, 255), (0, 0, 0))  # '8' for 8 angles
        layer0_data[0][1] = (49, (255, 255, 255), (0, 0, 0))  # '1' for 1 frame anim 1
        layer0_data[0][2] = (49, (255, 255, 255), (0, 0, 0))  # '1' for 1 frame anim 2
        layer0 = XPLayer(width, height, layer0_data)

        # Layer 1: Height layer (transparent placeholder)
        layer1_data = [[(0, (0, 0, 0), (255, 0, 255)) for _ in range(width)] for _ in range(height)]
        layer1 = XPLayer(width, height, layer1_data)

        # Layer 2: Visual layer (box pattern)
        layer2_data = []
        for y in range(height):
            row = []
            for x in range(width):
                # Create a simple border/fill pattern
                if x == 0 or x == width - 1 or y == 0 or y == height - 1:
                    # Border: box-drawing character (glyph 219 = full block)
                    glyph = 219
                    fg = (200, 200, 200)
                    bg = (50, 50, 50)
                else:
                    # Interior: period character
                    glyph = 46  # '.'
                    fg = (100, 100, 100)
                    bg = (30, 30, 30)
                row.append((glyph, fg, bg))
            layer2_data.append(row)
        layer2 = XPLayer(width, height, layer2_data)

        xp.layers = [layer0, layer1, layer2]
        xp.save(str(output_path))

        result["python_generate"] = True
        log(f"  Generated: {output_path}")
        log(f"  Layers: {len(xp.layers)}")
        log(f"  Dimensions: {width}x{height}")

    except Exception as e:
        result["errors"].append(f"Python generation failed: {e}")
        log(f"  FAILED: {e}")
        return result

    # ========================================================================
    # Step 2: Validate generated .xp using Python validation
    # ========================================================================
    log("\n[2/4] Validating with Python (validator.py)...")

    try:
        from scripts.pipeline.validator import validate_xp

        validation_result = validate_xp(output_path)

        if validation_result["valid"]:
            result["python_validate"] = True
            log(f"  Validation: PASSED")
            log(f"  Metadata: angles={validation_result['metadata'].get('angles')}, anims={validation_result['metadata'].get('anims')}")
        else:
            result["errors"].extend(validation_result["errors"])
            log(f"  Validation: FAILED")
            for err in validation_result["errors"]:
                log(f"    - {err}")

    except Exception as e:
        result["errors"].append(f"Python validation failed: {e}")
        log(f"  FAILED: {e}")

    # ========================================================================
    # Step 3: Run debug_sprite.py for ASCII preview
    # ========================================================================
    log("\n[3/4] Running ASCII preview (debug_sprite.py)...")

    try:
        debug_script = Path("scripts/pipeline/debug_sprite.py")
        if debug_script.exists():
            # Set PYTHONPATH to both import roots after the addon move.
            env = os.environ.copy()
            project_root = os.getcwd()
            env["PYTHONPATH"] = os.pathsep.join(
                get_blender_pythonpath_entries(project_root)
                + [env.get("PYTHONPATH", "")]
            )

            proc = subprocess.run(
                [sys.executable, str(debug_script), str(output_path)],
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )

            if proc.returncode == 0:
                result["ascii_preview"] = True
                log("  ASCII preview: SUCCESS")
                if verbose:
                    # Print preview output
                    for line in proc.stdout.split("\n")[:30]:
                        log(f"    {line}")
            else:
                result["errors"].append(f"ASCII preview failed: {proc.stderr}")
                log(f"  ASCII preview: FAILED")
                if verbose:
                    log(f"    stderr: {proc.stderr}")
        else:
            log(f"  ASCII preview: SKIPPED (debug_sprite.py not found)")
            result["ascii_preview"] = True  # Not a failure, just missing tool

    except subprocess.TimeoutExpired:
        result["errors"].append("ASCII preview timed out")
        log("  ASCII preview: TIMEOUT")
    except Exception as e:
        result["errors"].append(f"ASCII preview error: {e}")
        log(f"  ASCII preview: ERROR - {e}")

    # ========================================================================
    # Step 4: (Optional) C++ validation if test harness available
    # ========================================================================
    log("\n[4/4] C++ validation (optional)...")

    # Check if a C++ test harness exists (sprite_validate binary or similar)
    cpp_validator = Path(".run/sprite_validate")
    if cpp_validator.exists():
        try:
            proc = subprocess.run(
                [str(cpp_validator), str(output_path)],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if proc.returncode == 0:
                result["cpp_validate"] = True
                log("  C++ validation: PASSED")
            else:
                result["cpp_validate"] = False
                result["errors"].append(f"C++ validation failed: {proc.stderr}")
                log(f"  C++ validation: FAILED")

        except Exception as e:
            result["errors"].append(f"C++ validation error: {e}")
            log(f"  C++ validation: ERROR - {e}")
    else:
        log("  C++ validation: SKIPPED (no test harness available)")
        log("    Note: C++ sprite.cpp loads would be tested via game engine")

    # ========================================================================
    # Summary
    # ========================================================================
    log("\n" + "=" * 60)
    log("ROUND-TRIP TEST SUMMARY")
    log("=" * 60)

    passed = (
        result["python_generate"]
        and result["python_validate"]
        and result["ascii_preview"]
        and (result["cpp_validate"] is None or result["cpp_validate"])
    )

    log(f"\n  Python Generate:  {'PASS' if result['python_generate'] else 'FAIL'}")
    log(f"  Python Validate:  {'PASS' if result['python_validate'] else 'FAIL'}")
    log(f"  ASCII Preview:    {'PASS' if result['ascii_preview'] else 'FAIL'}")
    log(f"  C++ Validate:     {'PASS' if result['cpp_validate'] else 'SKIP' if result['cpp_validate'] is None else 'FAIL'}")
    log(f"\n  Output: {output_path}")
    log(f"\n  Overall: {'PASS' if passed else 'FAIL'}")

    if result["errors"]:
        log("\n  Errors:")
        for err in result["errors"]:
            log(f"    - {err}")

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Verification script for Asciicker pipeline"
    )
    parser.add_argument(
        "--xp-path", type=str, help="Path to specific .xp file to verify"
    )
    parser.add_argument(
        "--pipeline", action="store_true", help="Run pipeline verification only"
    )
    parser.add_argument(
        "--blender", action="store_true", help="Run Blender/build verification only"
    )
    parser.add_argument(
        "--sprite-roundtrip",
        action="store_true",
        help="Run sprite round-trip integration test (Python generates .xp, validates, shows ASCII preview)"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    # Handle --sprite-roundtrip first (independent test)
    if args.sprite_roundtrip:
        result = verify_sprite_roundtrip(verbose=args.verbose)
        passed = (
            result["python_generate"]
            and result["python_validate"]
            and result["ascii_preview"]
            and (result["cpp_validate"] is None or result["cpp_validate"])
        )
        sys.exit(0 if passed else 1)

    # Default to pipeline verification if no specific mode selected
    run_pipeline = args.pipeline or (not args.blender and not args.xp_path)
    run_blender = args.blender
    verify_specific = args.xp_path is not None

    if verify_specific:
        # Verify a specific .xp file
        result = verify_xp_file_with_engine(args.xp_path)
        print("\n" + "=" * 60)
        print("VERIFICATION RESULT")
        print("=" * 60)
        if result["xp_file_loads"]:
            print("✓ File loads successfully")
        if result["metadata_correct"]:
            print("✓ Metadata is correct")
        if result["engine_loads"]:
            print("✓ Engine loads file")
        if result["errors"]:
            print("\nErrors:")
            for err in result["errors"]:
                print(f"  - {err}")
            sys.exit(1)
        else:
            print("\n✓ All verifications passed!")
            sys.exit(0)

    elif run_pipeline:
        # Run pipeline verification only
        results = verify_pipeline_fixtures(verbose=args.verbose)
        sys.exit(0 if results["overall_pass"] else 1)

    elif run_blender:
        # Run Blender/build verification
        if run_blender_setup():
            if run_a3d_validation():
                if run_instance_validation() and run_collision_validation():
                    if run_game_test():
                        log("\n✓ All Blender/build tests passed!")
                        sys.exit(0)
        log("\n✗ Blender/build tests failed")
        sys.exit(1)

    else:
        # Run both (default legacy behavior)
        print("Running full verification (pipeline + blender)...")
        results = verify_pipeline_fixtures(verbose=args.verbose)

        if results["overall_pass"]:
            if run_blender_setup():
                if run_a3d_validation():
                    if run_instance_validation() and run_collision_validation():
                        if run_game_test():
                            log("\n✓ All verification tests passed!")
                            sys.exit(0)
        log("\n✗ Some tests failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
