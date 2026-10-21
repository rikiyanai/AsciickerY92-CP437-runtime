#!/usr/bin/env python3
"""FL-4512: commit owned content on top of HEAD without touching the shared index.

Other agents keep uncommitted hunks in the same files. This builds the commit
in a private index from HEAD plus the given blobs, then resets only shared-index
entries that still hold the pre-commit blob (task-observer 0077). It never
pushes unless the caller supplies ``--push``.

usage: commit_owned.py (message on stdin) [--fl FL-N KEY ...] [--file PATH ...] [--blob PATH=SHA ...] [--delete PATH ...]
  --fl FL KEY   copy the worktree fix-attempt block containing KEY into FL's
                section of HEAD's ledger (front door already wrote it)
  --fl-add FL KEY copy one native new entry plus its exact-ID overlay rows;
                refuse missing overlays and existing/overlay-only identifiers
  --file PATH   take the worktree file whole (only for files with no foreign hunks)
  --blob P=SHA@BASE  prepared blob plus the HEAD blob it was built on
  --delete PATH remove a tracked path
  --interface PATH  landed sibling interface to recheck before the ref update
  --push             explicitly authorize pushing origin/main after the commit
"""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from fl4512_ledger_projection import append_attempt, append_entry
except ModuleNotFoundError:
    from scripts.fl4512_ledger_projection import append_attempt, append_entry

try:
    from fl4512_commit_preflight import (
        snapshot_interfaces,
        stable_file_blob,
        verify_interfaces,
    )
    from fl4512_commit_transport import push_origin
    from fl4512_index_resync import resync_committed_paths
except ModuleNotFoundError:
    from scripts.fl4512_commit_preflight import (
        snapshot_interfaces,
        stable_file_blob,
        verify_interfaces,
    )
    from scripts.fl4512_commit_transport import push_origin
    from scripts.fl4512_index_resync import resync_committed_paths

R = os.environ.get("FL4512_REPO_ROOT", "/Users/r/Projects/asciicker-Y9-2")


def run(*a, inp=None, env=None, check=True):
    r = subprocess.run(list(a), cwd=R, input=inp, capture_output=True, env=env)
    if check and r.returncode:
        sys.exit(f"{a}: {r.stderr.decode()}")
    return r.stdout.decode()


def _index_entry(path: str) -> str:
    return run("git", "ls-files", "-s", "--", path, check=False).strip()


def _entry_has_blob(entry: str, blob: str) -> bool:
    fields = entry.split()
    return len(fields) >= 2 and fields[1] == blob


def _run_commit_guards(env: dict[str, str], message: str) -> None:
    """Run the hook-equivalent guards against the private index.

    ``commit-tree`` bypasses Git's commit-msg and pre-commit hooks. Keep the
    same guard functions on this route by passing the private index through the
    environment, then refuse the ref update when either guard fails.
    """
    message_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix="fl4512_commit_message_", delete=False
        ) as message_file:
            message_file.write(message)
            message_path = message_file.name
        for command in ("pre-commit", "commit-msg"):
            args = [sys.executable, "scripts/git_commit_policy_guard.py", command]
            if command == "commit-msg":
                args.append(message_path)
            result = subprocess.run(
                args,
                cwd=R,
                env=env,
                capture_output=True,
                text=True,
            )
            if result.returncode:
                output = "\n".join(
                    part.strip() for part in (result.stdout, result.stderr) if part.strip()
                )
                sys.exit(f"temporary-index {command} guard failed:\n{output}")
            print(f"temporary-index {command} guard: pass")
    finally:
        if message_path:
            os.unlink(message_path)


ap = argparse.ArgumentParser()
ap.add_argument("--fl", nargs=2, action="append", default=[])
ap.add_argument("--fl-add", nargs=2, action="append", default=[],
                metavar=("FL", "KEY"), help="copy one native fl add entry and its overlay rows")
ap.add_argument("--file", action="append", default=[])
ap.add_argument("--blob", action="append", default=[])
ap.add_argument("--delete", action="append", default=[])
ap.add_argument("--interface", action="append", default=[])
ap.add_argument("--push", action="store_true", help="authorize push to origin/main")
a = ap.parse_args()
msg = sys.stdin.read()
assert "Godot-Feature-Impact:" in msg and " or " not in msg, "commit message rules"
assert "Claude-Session" not in msg, "repo commit guard rejects AI attribution trailers"
head = run("git", "rev-parse", "HEAD").strip()
if run("git", "symbolic-ref", "--quiet", "HEAD", check=False).strip() != "refs/heads/main":
    sys.exit("owned commit route requires main; refusing to update a different branch")
interface_snapshot = snapshot_interfaces(R, a.interface)
blobs = {}
if a.fl or a.fl_add:
    new = run("git", "show", "HEAD:docs/FAILURE_LOG.md")
    wt = open(R + "/docs/FAILURE_LOG.md", encoding="utf-8").read()
    for fl, key in a.fl:
        try:
            new = append_attempt(new, wt, fl, key)
        except ValueError as exc:
            sys.exit(str(exc))
    head_parts = {}
    for path in run("git", "ls-tree", "-r", "--name-only", head, "--", "docs/FAILURE_LOG.overlay").splitlines():
        if path.endswith(".jsonl"):
            head_parts[path] = run("git", "show", f"{head}:{path}")
    worktree_parts = {str(path.relative_to(R)): path.read_text(encoding="utf-8")
                      for path in Path(R, "docs/FAILURE_LOG.overlay").glob("part-*.jsonl")}
    for fl, key in a.fl_add:
        try:
            projection = append_entry(new, wt, fl, key, head_parts, worktree_parts)
        except (ValueError, TypeError) as exc:
            sys.exit(str(exc))
        new = projection.pop("docs/FAILURE_LOG.md")
        head_parts.update(projection)
        for path, text in projection.items():
            blobs[path] = run("git", "hash-object", "-w", "--stdin", inp=text.encode()).strip()
    blobs["docs/FAILURE_LOG.md"] = run("git", "hash-object", "-w", "--stdin", inp=new.encode()).strip()
for p in a.file:
    try:
        blobs[p] = stable_file_blob(R, p)
    except RuntimeError as exc:
        sys.exit(str(exc))
for pb in a.blob:
    # PATH=BLOB@BASE: BASE is the HEAD blob the step was built on (third
    # column of fl4895_delete_step.py). A blob built on an older HEAD would
    # silently revert whatever landed since (it dropped two lanes' tests on
    # 2026-09-28), so a missing or stale base is refused: rerun the step.
    p, spec = pb.split("=", 1)
    if "@" not in spec:
        sys.exit(f"--blob {p}: pass BLOB@BASE (base = third column of fl4895_delete_step.py)")
    sha, base = spec.split("@", 1)
    now = run("git", "rev-parse", f"{head}:{p}", check=False).strip()
    if now != base:
        sys.exit(f"--blob {p}: HEAD changed since the step ran (base {base[:9]}, HEAD {now[:9]}); rerun the step on the current HEAD")
    blobs[p] = sha
idx = tempfile.mktemp(prefix="fl4512_commit_index_")
paths = list(dict.fromkeys([*blobs, *a.delete]))
before_entries = {p: _index_entry(p) for p in paths}
env = dict(os.environ, GIT_INDEX_FILE=idx)
try:
    run("git", "read-tree", "HEAD", env=env)
    for p, sha in blobs.items():
        run("git", "update-index", "--add", "--cacheinfo", f"100644,{sha},{p}", env=env)
    for p in a.delete:
        run("git", "rm", "-q", "--cached", p, env=env)
    print(run("git", "diff", "--cached", "--stat", "HEAD", env=env))
    changed = subprocess.run(
        ["git", "diff", "--cached", "--quiet", "HEAD"],
        cwd=R,
        env=env,
        capture_output=True,
    )
    if changed.returncode == 0:
        sys.exit("temporary-index commit has no changes; refusing empty commit")
    if changed.returncode != 1:
        sys.exit(f"temporary-index diff failed: {changed.stderr.decode().strip()}")
    if "docs/FAILURE_LOG.md" in blobs:
        d = run("git", "diff", "--cached", "-U0", "HEAD", "--", "docs/FAILURE_LOG.md", env=env).splitlines()
        minus = sorted(l[1:] for l in d if l.startswith("-") and not l.startswith("---"))
        plus = set(l[1:] for l in d if l.startswith("+") and not l.startswith("+++"))
        foreign = [l for l in minus if l not in plus]
        assert not foreign, foreign[:3]
    try:
        verify_interfaces(R, interface_snapshot)
    except RuntimeError as exc:
        sys.exit(str(exc))
    _run_commit_guards(env, msg)
    tree = run("git", "write-tree", env=env).strip()
    commit = run("git", "commit-tree", tree, "-p", head, "-F", "-", inp=msg.encode(), env=env).strip()
    if run("git", "rev-parse", "HEAD").strip() != head:
        sys.exit("HEAD moved; rerun")
    try:
        verify_interfaces(R, interface_snapshot)
    except RuntimeError as exc:
        sys.exit(str(exc))
    run("git", "update-ref", "refs/heads/main", commit, head)
finally:
    if os.path.exists(idx):
        os.unlink(idx)

# Repair only paths whose shared-index entry still has its pre-commit identity.
# A sibling's changed entry is left alone. The helper verifies each reset
# against the new HEAD and raises on an index-lock race that remains stale.
try:
    resync_committed_paths(before_entries)
except RuntimeError as exc:
    sys.exit(f"post-commit shared-index resync failed: {exc}")

# A best-effort reset is not enough: fail if one of our unchanged pre-commit
# entries still stages the parent blob, or if a new/deleted path still has the
# pre-commit entry after the targeted resync.
for p, before in before_entries.items():
    current = _index_entry(p)
    if current != before:
        continue
    expected = run("git", "rev-parse", "--verify", "-q", f"HEAD:{p}", check=False).strip()
    if expected and not _entry_has_blob(current, expected):
        sys.exit(f"post-commit shared-index resync left stale entry for {p}")
    if not expected and current:
        sys.exit(f"post-commit shared-index resync left deleted path staged: {p}")
log_line = run("git", "log", "--oneline", "-1").strip()
push_code = push_origin(R, a.push)
if push_code is None:
    print(log_line, "push skipped (--push not supplied)")
else:
    print(log_line, "push", push_code)
