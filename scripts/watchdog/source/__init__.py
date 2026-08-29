"""Source identity module — single owner for git state, source_ref, and restoreability.

Phase 2: SourceIdentity frozen dataclass extracted from _resolve_worktree_state().
Phase 3: tmp_clone.py will extend the source/ subtree with tmp-clone helpers.
"""

from watchdog.source.identity import SourceIdentity
from watchdog.source.restoreability_contract import RestoreabilityContract
from watchdog.source.dirty_tree import source_dirty_paths_set, scope_labels_for_path
from watchdog.source.tmp_clone import (
    copy_path_into_tmp_clone,
    git_output_raw,
    git_status_porcelain_raw,
    rewrite_repo_root_strings,
    runs_root_for_repo,
)

__all__ = [
    "SourceIdentity",
    "RestoreabilityContract",
    "source_dirty_paths_set",
    "scope_labels_for_path",
    "copy_path_into_tmp_clone",
    "git_output_raw",
    "git_status_porcelain_raw",
    "rewrite_repo_root_strings",
    "runs_root_for_repo",
]
