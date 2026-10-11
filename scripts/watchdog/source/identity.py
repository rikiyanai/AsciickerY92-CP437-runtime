"""SourceIdentity — immutable git-state snapshot for the watchdog proof run.

Phase 2: Extracted from _resolve_worktree_state() and WorktreeState.
Once constructed, no child or phase may recompute source_ref, branch, or
worktree_clean — SourceIdentity is the single authoritative owner.

This makes FL-3860 (mixed source_ref/gameplay_ref) and FL-3054 (stale local
HEAD comparison) class bugs impossible.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SourceIdentity:
    """Immutable source snapshot captured once before deploy begins.

    All deploy, preflight, and launch phases consume this one object.
    No child recomputes identity.

    ``submodule_state`` is a dict of path → commit for tracked submodules.
    Extraction is deferred to ``source/submodules.py`` (Phase 4 follow-up).
    """

    source_ref: str  # 40-char full SHA
    source_ref_short: str  # 7-char short SHA
    branch: str
    worktree_clean: bool
    dirty_paths: tuple[str, ...]  # tracked + untracked paths from git status
    restoreability_contract: dict[str, Any] | None = None  # Phase 2: plain dict; Phase 4: RestoreabilityContract
    submodule_state: dict[str, str] = None  # type: ignore[assignment]  # path → commit (deferred)

    def __post_init__(self):
        if self.submodule_state is None:
            object.__setattr__(self, "submodule_state", {})
        else:
            object.__setattr__(self, "submodule_state", dict(self.submodule_state))
        if self.restoreability_contract is not None:
            object.__setattr__(
                self,
                "restoreability_contract",
                MappingProxyType(dict(self.restoreability_contract)),
            )

    @classmethod
    def from_worktree_state(
        cls,
        *,
        branch: str,
        head_short: str,
        head_full: str,
        worktree_clean: bool,
        status_lines: list[str],
        front_door_restoreability_contract: dict[str, Any] | None,
        submodule_state: dict[str, str] | None = None,
    ) -> "SourceIdentity":
        """Construct from the values currently returned by _resolve_worktree_state().

        This is a transition constructor — after Phase 5, WorktreeState is
        replaced entirely by SourceIdentity.
        """
        from watchdog.source.dirty_tree import tracked_status_paths, untracked_status_paths

        dirty_paths: tuple[str, ...] = tuple(
            sorted(tracked_status_paths(status_lines)) +
            sorted(untracked_status_paths(status_lines))
        )
        return cls(
            source_ref=head_full,
            source_ref_short=head_short,
            branch=branch,
            worktree_clean=worktree_clean,
            dirty_paths=dirty_paths,
            restoreability_contract=front_door_restoreability_contract,
            submodule_state=submodule_state or {},
        )

    def restoreable_by_commit(self) -> bool:
        """True when the source tree is clean and deployable from this commit."""
        return self.worktree_clean

    def has_dirty_paths(self) -> bool:
        return len(self.dirty_paths) > 0
