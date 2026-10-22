from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.maintainer import install_hooks as INSTALL


def _project(tmp_path: Path, *, host_dir: str) -> Path:
    """Build a repo-shaped directory carrying one project-local door."""
    root = tmp_path / "repo"
    extensions = root / host_dir / "extensions"
    extensions.mkdir(parents=True)
    (extensions / "doc-placement-guard.ts").write_text("// door\n", encoding="utf-8")
    return root


def _store(tmp_path: Path, entries: dict[str, object]) -> Path:
    store = tmp_path / "trust.json"
    store.write_text(f"{json.dumps(entries, indent=2)}\n", encoding="utf-8")
    return store


def test_untrusted_repo_path_is_reported_as_a_non_firing_door(
    tmp_path: Path, capsys
) -> None:
    # The repository moved between absolute paths; the saved decision stayed
    # behind on the old one. The guard file is present and well-formed, so a
    # text grep still calls it current, but the host will not load it.
    root = _project(tmp_path, host_dir=".pi")
    store = _store(tmp_path, {str(tmp_path / "elsewhere"): True})

    assert not INSTALL.verify_project_trust(root=root, store=store)

    INSTALL.verify_project_trust(root=root, store=store, verbose=True)
    line = capsys.readouterr().out
    assert "[INVALID untrusted]" in line
    assert "doc-placement-guard.ts" in line
    assert "cannot fire" in line


def test_trusted_repo_path_reports_the_door_current(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path, host_dir=".pi")
    store = _store(tmp_path, {str(root.resolve()): True})

    assert INSTALL.verify_project_trust(root=root, store=store, verbose=True)
    assert "[current]" in capsys.readouterr().out


def test_trust_is_inherited_from_the_nearest_parent_decision(tmp_path: Path) -> None:
    # pi resolves the closest saved decision on the current-or-parent chain,
    # so a trusted parent folder covers the repository beneath it.
    root = _project(tmp_path, host_dir=".pi")
    assert INSTALL.verify_project_trust(
        root=root, store=_store(tmp_path, {str(tmp_path.resolve()): True}), verbose=False
    )
    assert INSTALL.nearest_project_trust(
        root, store=_store(tmp_path, {str(tmp_path.resolve()): True})
    ) == (True, str(tmp_path.resolve()))


def test_explicitly_declined_trust_fails_the_door(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path, host_dir=".pi")
    store = _store(tmp_path, {str(root.resolve()): False})

    assert not INSTALL.verify_project_trust(root=root, store=store, verbose=True)
    assert "trust declined at" in capsys.readouterr().out


def test_missing_or_unreadable_trust_store_is_treated_as_untrusted(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path, host_dir=".pi")
    assert INSTALL.nearest_project_trust(root, store=tmp_path / "absent.json") == (
        None,
        None,
    )
    broken = tmp_path / "broken.json"
    broken.write_text("{ not json", encoding="utf-8")
    assert INSTALL.nearest_project_trust(root, store=broken) == (None, None)
    assert not INSTALL.verify_project_trust(root=root, store=broken, verbose=False)


def test_an_ungated_host_is_not_failed_for_an_untrusted_path(
    tmp_path: Path, capsys
) -> None:
    # OMP 18.2.6 reads no trust store, so its door loads wherever the repo
    # sits. Failing it on a pi trust decision would be a second invented
    # dependency, not an honest check.
    root = _project(tmp_path, host_dir=".omp")
    store = _store(tmp_path, {})

    assert INSTALL.verify_project_trust(root=root, store=store, verbose=True)
    assert "no project-trust gating" in capsys.readouterr().out


def test_a_host_without_a_door_is_reported_absent_not_failed(
    tmp_path: Path, capsys
) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    assert INSTALL.verify_project_trust(root=root, store=_store(tmp_path, {}), verbose=True)
    out = capsys.readouterr().out
    assert "[absent]" in out
    assert "[INVALID untrusted]" not in out


def test_every_recorded_extension_dir_names_its_gating_measurement() -> None:
    # The gating flag is the one place the measured host behaviour lives, so a
    # new host cannot be added without deciding whether it is trust-gated.
    hosts = {host for host, _parts, _gated in INSTALL._TRUST_GATED_EXTENSION_DIRS}
    assert {"pi", "OMP"} <= hosts
    for _host, parts, gated in INSTALL._TRUST_GATED_EXTENSION_DIRS:
        assert isinstance(gated, bool)
        assert parts[-1] == "extensions"


def test_this_repository_path_is_trusted_where_a_gated_door_exists() -> None:
    # Guards the live failure this check was added for: the repo moved from
    # ~/Downloads to ~/Projects and the saved decision did not follow it.
    assert INSTALL.verify_project_trust(verbose=False)


def test_codex_trust_verifier_requires_each_live_project_hook(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    root = tmp_path / "repo"
    config_path = root / ".codex" / "hooks.json"
    config_path.parent.mkdir(parents=True)
    command = "python3 /repo/scripts/maintainer/hooks/md_file_ban_hook.py"
    second_command = "python3 /repo/scripts/maintainer/hooks/context_bloat_guard_hook.py"
    config_path.write_text(json.dumps({"hooks": {"PreToolUse": [{
        "matcher": "^apply_patch$",
        "hooks": [{"type": "command", "command": command}],
    }, {
        "matcher": ".*",
        "hooks": [{"type": "command", "command": second_command}],
    }]}}), encoding="utf-8")
    monkeypatch.setattr(INSTALL, "_PROJECT_ROOT", root)
    monkeypatch.setattr(INSTALL.shutil, "which", lambda _name: "codex")

    def check(hooks: list[dict]) -> bool:
        monkeypatch.setattr(INSTALL, "_codex_hooks_list", lambda _bin: {
            "result": {"data": [{"cwd": str(root), "hooks": hooks}]}
        })
        return INSTALL.verify_codex_repo_hook_trust(verbose=True)

    live = {
        "sourcePath": str(config_path), "eventName": "preToolUse",
        "matcher": "^apply_patch$", "command": command,
        "enabled": True, "trustStatus": "trusted",
    }
    second = {**live, "matcher": ".*", "command": second_command}
    assert check([live, second])
    assert not check([live])
    assert not check([])
    assert "[missing]" in capsys.readouterr().out
    assert not check([{**live, "enabled": False}, second])
    assert not check([{**live, "trustStatus": "untrusted"}, second])
    assert not check([{**live, "matcher": "^Bash$"}, second])
    assert not check([live, live, second])
