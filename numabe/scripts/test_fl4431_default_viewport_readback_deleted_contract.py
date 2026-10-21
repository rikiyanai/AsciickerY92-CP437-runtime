#!/usr/bin/env python3
# =============================================================================
# test_fl4431_default_viewport_readback_deleted_contract.py
# =============================================================================
# Ordinary Main startup used to schedule a full viewport GPU readback after 120
# frames and write /tmp/godot_screen.png. The branch ran without an explicit
# capture request, contaminating performance and terminating manual sessions
# when ASCIICKER_CAPTURE_VIEWPORT_EXIT leaked through a launcher environment.
#
# The retained owner is explicit capture-and-exit only:
# --capture-viewport-exit plus its environment mirror schedule the readback.
# The dogfight proof and manual-play launcher both scrub that mirror before
# creating children. Main's stale moving-cell dump caller is deleted because
# the compositor method it targeted no longer exists.
# =============================================================================

"""Guard deletion of FL-4431's implicit viewport readback owner."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = ROOT / "godot_project" / "scripts" / "Main.gd"
DOGFIGHT_PATH = ROOT / "scripts" / "run_fl4265_airplane_proof.py"
MANUAL_PATH = ROOT / "scripts" / "launch_fl4359_manual_play.py"
CAPTURE_EXIT_ENV = "ASCIICKER_CAPTURE_VIEWPORT_EXIT"


def _extract_gd_function(source: str, function_name: str) -> str | None:
    """Return one top-level GDScript function through the next declaration."""

    start = re.search(
        r"^func\s+" + re.escape(function_name) + r"\s*\(",
        source,
        re.MULTILINE,
    )
    if start is None:
        return None
    following = re.search(r"^func\s+\w+\s*\(", source[start.end() :], re.MULTILINE)
    if following is None:
        return source[start.start() :]
    return source[start.start() : start.end() + following.start()]


def _extract_py_function(source: str, function_name: str) -> str | None:
    """Return one top-level Python function through the next declaration."""

    start = re.search(
        r"^def\s+" + re.escape(function_name) + r"\s*\(",
        source,
        re.MULTILINE,
    )
    if start is None:
        return None
    following = re.search(r"^def\s+\w+\s*\(", source[start.end() :], re.MULTILINE)
    if following is None:
        return source[start.start() :]
    return source[start.start() : start.end() + following.start()]


def _compact(source: str) -> str:
    """Collapse whitespace so formatting cannot weaken an executable pin."""

    return re.sub(r"\s+", "", source)


def _load_module(path: Path, module_name: str) -> ModuleType:
    """Import one launcher without entering its command-line main."""

    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def contract_violations(
    main_source: str,
    dogfight_source: str,
    manual_source: str,
) -> tuple[str, ...]:
    """Return every broken deletion, explicit-owner, and environment law."""

    violations: list[str] = []
    ready = _extract_gd_function(main_source, "_ready")
    process_exit = _extract_gd_function(
        main_source, "_process_capture_viewport_exit"
    )
    capture = _extract_gd_function(main_source, "_capture_viewport_png")
    operator_ab = _extract_gd_function(main_source, "_run_fl4359_operator_ab")
    manual_env = _extract_py_function(
        manual_source, "_manual_child_environment"
    )
    manual_main = _extract_py_function(manual_source, "main")
    dogfight_env = _extract_py_function(
        dogfight_source, "_capture_child_environment"
    )
    if ready is None:
        violations.append("Main._ready is missing")
    if process_exit is None:
        violations.append("explicit capture-exit processor is missing")
    if capture is None:
        violations.append("explicit viewport PNG writer is missing")
    if operator_ab is None:
        violations.append("operator A/B function is missing")
    if manual_env is None:
        violations.append("manual child-environment scrub is missing")
    if manual_main is None:
        violations.append("manual launcher main is missing")
    if dogfight_env is None:
        violations.append("dogfight child-environment scrub is missing")
    if violations:
        return tuple(violations)

    assert ready is not None
    assert process_exit is not None
    assert capture is not None
    assert operator_ab is not None
    assert manual_env is not None
    assert manual_main is not None
    assert dogfight_env is not None
    ready_compact = _compact(ready)
    process_compact = _compact(process_exit)
    manual_env_compact = _compact(manual_env)
    manual_main_compact = _compact(manual_main)

    if "_capture_viewport_png_after_frames" in ready:
        violations.append(
            "Main._ready must not schedule an implicit delayed viewport readback"
        )
    if "_capture_viewport_png_after_frames" in main_source:
        violations.append(
            "the dead delayed viewport-capture helper must remain deleted"
        )
    explicit_request = (
        'varcapture_exit_requested:=args.has("--capture-viewport-exit")'
        'orOS.get_environment("ASCIICKER_CAPTURE_VIEWPORT_EXIT")=="1"'
    )
    if explicit_request not in ready_compact:
        violations.append(
            "Main._ready must retain the explicit CLI and environment owner"
        )
    if (
        "ifcapture_exit_requested:"
        "_capture_exit_path=_cmdline_capture_path(" not in ready_compact
        or "_capture_exit_frames=_cmdline_capture_frames(30)"
        not in ready_compact
    ):
        violations.append(
            "an explicit request must still schedule path plus frame countdown"
        )
    if (
        'if_capture_exit_path=="":return' not in process_compact
        or "varerr:=await_capture_viewport_png(path)" not in process_compact
        or "get_tree().quit(0)" not in process_compact
    ):
        violations.append(
            "the explicit capture-and-exit processor must remain live"
        )
    if "img.save_png(path)" not in capture:
        violations.append("the explicit viewport writer must still encode PNG")

    if "fl4359_arm_moving_cell_dump" in operator_ab:
        violations.append(
            "Main must not call the deleted compositor moving-cell dump method"
        )
    if "--fl4359-moving-cell-dump" in operator_ab:
        violations.append(
            "Main must not retain the dead moving-cell dump trigger"
        )

    exact_key = f'"{CAPTURE_EXIT_ENV}"'
    denied_set = dogfight_source[
        dogfight_source.find("CAPTURE_CHILD_DENIED_ENV_EXACT") :
        dogfight_source.find("CAPTURE_CHILD_DENIED_ENV_PREFIXES")
    ]
    if exact_key not in denied_set:
        violations.append(
            "dogfight exact-deny set must include viewport capture-and-exit"
        )
    if "keyinCAPTURE_CHILD_DENIED_ENV_EXACT" not in _compact(dogfight_env):
        violations.append(
            "dogfight child builder must execute its exact-key deny set"
        )
    if (
        "child_environment.pop(VIEWPORT_CAPTURE_EXIT_ENV,None)"
        not in manual_env_compact
    ):
        violations.append(
            "manual child builder must remove inherited capture-and-exit"
        )
    if (
        "env=_manual_child_environment(dict(os.environ))"
        not in manual_main_compact
    ):
        violations.append(
            "manual launcher main must use the scrubbed child environment"
        )
    server_start = manual_main.find("server = subprocess.Popen(")
    server_end = manual_main.find("\n    def listening", server_start)
    godot_start = manual_main.find("godot_rc = subprocess.run(")
    godot_end = manual_main.find(").returncode", godot_start)
    if min(server_start, server_end) < 0:
        violations.append("manual Colyseus subprocess seam is missing")
    elif "env=env" not in _compact(manual_main[server_start:server_end]):
        violations.append(
            "manual Colyseus subprocess must consume the scrubbed environment"
        )
    if min(godot_start, godot_end) < 0:
        violations.append("manual Godot subprocess seam is missing")
    elif "env=env" not in _compact(
        manual_main[godot_start : godot_end + len(").returncode")]
    ):
        violations.append(
            "manual Godot subprocess must consume the scrubbed environment"
        )
    return tuple(violations)


def _sources() -> tuple[str, str, str]:
    """Read the three ownership surfaces in stable contract order."""

    return (
        MAIN_PATH.read_text(encoding="utf-8"),
        DOGFIGHT_PATH.read_text(encoding="utf-8"),
        MANUAL_PATH.read_text(encoding="utf-8"),
    )


def test_current_source_contract() -> None:
    """The checked-in working sources must expose only the explicit owner."""

    assert contract_violations(*_sources()) == ()


def test_environment_scrubs_execute() -> None:
    """Both launchers must delete a polluted inherited capture switch."""

    dogfight = _load_module(DOGFIGHT_PATH, "fl4431_viewport_dogfight")
    manual = _load_module(MANUAL_PATH, "fl4431_viewport_manual")
    parent = {
        "PATH": "/usr/bin:/bin",
        CAPTURE_EXIT_ENV: "1",
        "SAFE_PLAYER_ENV": "preserved",
    }
    dogfight_child, dogfight_receipt = dogfight._capture_child_environment(
        parent
    )
    manual_child = manual._manual_child_environment(parent)
    assert CAPTURE_EXIT_ENV not in dogfight_child
    assert CAPTURE_EXIT_ENV not in manual_child
    assert dogfight_child["SAFE_PLAYER_ENV"] == "preserved"
    assert manual_child["SAFE_PLAYER_ENV"] == "preserved"
    assert CAPTURE_EXIT_ENV in dogfight_receipt["denied_parent_keys"]
    assert dogfight_receipt["effective_forbidden_keys_absent"] is True


def test_realistic_destructive_mutants() -> None:
    """Restoring each deleted contamination path must make the contract red."""

    main_source, dogfight_source, manual_source = _sources()
    explicit_line = (
        '\tvar capture_exit_requested := args.has("--capture-viewport-exit") '
        'or OS.get_environment("ASCIICKER_CAPTURE_VIEWPORT_EXIT") == "1"\n'
    )
    restored_default = explicit_line + (
        '\tif not capture_exit_requested:\n'
        '\t\t_capture_viewport_png_after_frames(120, '
        '"/tmp/godot_screen.png")\n'
    )
    dead_caller_anchor = (
        '\t\t\tvar mode_name := "baseline" if cm == 0 else "heighthue"\n'
    )
    restored_dead_caller = (
        '\t\t\tif OS.get_cmdline_user_args().has('
        '"--fl4359-moving-cell-dump") \\\n'
        '\t\t\t\t\tand eff != null and '
        'eff.has_method("fl4359_arm_moving_cell_dump"):\n'
        '\t\t\t\teff.call("fl4359_arm_moving_cell_dump", ab_frames, 0)\n'
        + dead_caller_anchor
    )
    mutants = {
        "default_auto_capture_restored": (
            main_source.replace(explicit_line, restored_default, 1),
            dogfight_source,
            manual_source,
        ),
        "dogfight_inherits_capture_exit": (
            main_source,
            dogfight_source.replace(
                f'        "{CAPTURE_EXIT_ENV}",\n',
                "",
                1,
            ),
            manual_source,
        ),
        "manual_inherits_capture_exit": (
            main_source,
            dogfight_source,
            manual_source.replace(
                "    child_environment.pop(VIEWPORT_CAPTURE_EXIT_ENV, None)\n",
                "",
                1,
            ),
        ),
        "dead_moving_cell_caller_restored": (
            main_source.replace(
                dead_caller_anchor,
                restored_dead_caller,
                1,
            ),
            dogfight_source,
            manual_source,
        ),
        "client_env_not_forwarded": (
            main_source,
            dogfight_source,
            manual_source.replace(
                "                cwd=str(ROOT),\n"
                "                env=env,\n"
                "                stdout=glog,\n",
                "                cwd=str(ROOT),\n"
                "                stdout=glog,\n",
                1,
            ),
        ),
    }
    baseline = (main_source, dogfight_source, manual_source)
    for name, mutant_sources in mutants.items():
        assert mutant_sources != baseline, f"{name} failed to alter its source"
        assert contract_violations(*mutant_sources), f"{name} survived"


def main() -> int:
    """Run the direct contract and print one stable diagnostic receipt."""

    test_current_source_contract()
    test_environment_scrubs_execute()
    test_realistic_destructive_mutants()
    print(
        "FL4431_DEFAULT_VIEWPORT_READBACK_DELETED_CONTRACT_PASS "
        "env_scrubs=2 manual_subprocess_seams=2 mutants=5"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
