from pathlib import Path

from scripts.maintainer.lib.run_budget_guard import check_run_budget, surface_for_path


def _sparse_file(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.seek(size - 1)
        handle.write(b"\0")


def test_manual_x_surface_is_independent_of_full_repo_run_budget(tmp_path: Path) -> None:
    _sparse_file(tmp_path / ".run" / "benchmark" / "old.bin", 13 * 1024**3)
    output = tmp_path / ".run" / "manual_x_dump" / "capture"

    assert surface_for_path(tmp_path, output).name == "manual_x_dump"
    decision = check_run_budget(tmp_path, output, estimated_bytes=32 * 1024**2)

    assert decision.allowed
    assert decision.surface == "manual_x_dump"
    assert decision.usage_bytes == 0


def test_manual_x_surface_still_has_a_hard_bound(tmp_path: Path) -> None:
    _sparse_file(
        tmp_path / ".run" / "manual_x_dump" / "old.bin",
        513 * 1024**2,
    )

    decision = check_run_budget(
        tmp_path,
        tmp_path / ".run" / "manual_x_dump" / "capture",
    )

    assert not decision.allowed
    assert decision.reason == "hard_budget_exceeded"
