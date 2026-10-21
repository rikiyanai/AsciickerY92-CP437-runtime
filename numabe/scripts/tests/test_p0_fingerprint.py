"""Focused behavior tests for the P0 scoped fingerprinter (stdlib only).

Run from the repo root with either:
    python3 scripts/tests/test_p0_fingerprint.py
    python3 -m pytest scripts/tests/test_p0_fingerprint.py -q
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.getcwd())
from scripts.maintainer.p0.fingerprint import (  # noqa: E402
    P0_CONTRACT_STATUS,
    P0_CONTRACT_VERSION,
    scoped_fingerprint,
    sha256_file,
)

GIT = shutil.which("git")
REPO_ROOT = os.getcwd()
SNAPSHOT = os.path.join(REPO_ROOT, "scripts", "maintainer", "p0",
                        "snapshot.py")


def _git(*args, cwd):
    subprocess.run(["git"] + list(args), cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _seed_repo(tmp, files):
    _git("init", cwd=tmp)
    _git("config", "user.email", "p0@test.local", cwd=tmp)
    _git("config", "user.name", "p0-test", cwd=tmp)
    for rel, data in files.items():
        dest = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(data)
    _git("add", ".", cwd=tmp)
    _git("commit", "-m", "seed", cwd=tmp)


class FingerprintTests(unittest.TestCase):
    def test_contract_markers_frozen(self):
        self.assertEqual(P0_CONTRACT_VERSION, 1)
        self.assertEqual(P0_CONTRACT_STATUS, "frozen")

    def test_hash_stable_and_matches_hashlib(self):
        with tempfile.NamedTemporaryFile(delete=False) as fh:
            fh.write(b"asciicker-p0-content\x00\xff")
            path = fh.name
        try:
            self.assertEqual(sha256_file(path), sha256_file(path))
            with open(path, "rb") as fh:
                self.assertEqual(sha256_file(path),
                                 hashlib.sha256(fh.read()).hexdigest())
        finally:
            os.unlink(path)

    # REASON for update: missing entries now participate as `M <rel>`
    # digest records with repo-relative keys (old test asserted absolute
    # keys and omission-from-digest semantics).
    def test_missing_files_participate_with_relative_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            present_abs = os.path.join(tmp, "present.txt")
            with open(present_abs, "wb") as fh:
                fh.write(b"present")
            ghost_abs = present_abs + "-does-not-exist"
            result = scoped_fingerprint(tmp, [present_abs, ghost_abs])
            self.assertIn("present.txt", result["scoped_hashes"])
            self.assertNotIn(ghost_abs, result["scoped_hashes"])
            self.assertEqual(result["missing"], ["present.txt-does-not-exist"])
            self.assertFalse(result["scope_complete"])
            # Same scope minus the missing entry MUST digest differently
            # (law: scope membership participates in the digest).
            without = scoped_fingerprint(tmp, [present_abs])
            self.assertNotEqual(result["fingerprint"], without["fingerprint"])
            # Deterministic: same missing scope, same digest.
            again = scoped_fingerprint(tmp, [present_abs, ghost_abs])
            self.assertEqual(result["fingerprint"], again["fingerprint"])
            for key in ("contract_status", "head_commit", "head_tree",
                        "worktree_clean_scoped", "scope_complete",
                        "scoped_hashes", "missing", "fingerprint"):
                self.assertIn(key, result)

    # REASON for update: new (repo_root, paths) signature with
    # repo-relative receipt keys.
    def test_fingerprint_moves_with_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "versioned.txt")
            with open(path, "wb") as fh:
                fh.write(b"version-one")
            before = scoped_fingerprint(tmp, [path])["fingerprint"]
            with open(path, "wb") as fh:
                fh.write(b"version-two")
            after = scoped_fingerprint(tmp, [path])["fingerprint"]
            self.assertNotEqual(before, after)

    # REASON for update: new (repo_root, paths) signature.
    def test_fingerprint_ignores_unrelated_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            scoped = os.path.join(tmp, "in_scope.txt")
            other = os.path.join(tmp, "out_of_scope.txt")
            with open(scoped, "wb") as fh:
                fh.write(b"scope-content")
            before = scoped_fingerprint(tmp, [scoped])["fingerprint"]
            with open(other, "wb") as fh:
                fh.write(b"unrelated-noise")
            after = scoped_fingerprint(tmp, [scoped])["fingerprint"]
            self.assertEqual(before, after)

    # REASON for update: cwd-independent (repo_root, paths) call -- no
    # chdir sleight-of-hand; deleted tracked file must flip BOTH
    # worktree_clean_scoped and scope_complete and move the digest.
    @unittest.skipUnless(GIT, "git binary required for dirty-detection test")
    def test_dirty_detection_in_tmp_git_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed_repo(tmp, {"tracked.txt": "clean\n"})
            result = scoped_fingerprint(tmp, ["tracked.txt"])
            self.assertTrue(result["worktree_clean_scoped"])
            self.assertTrue(result["scope_complete"])
            target = os.path.join(tmp, "tracked.txt")
            with open(target, "w", encoding="utf-8") as fh:
                fh.write("dirty\n")
            dirty = scoped_fingerprint(tmp, ["tracked.txt"])
            self.assertFalse(dirty["worktree_clean_scoped"])
            self.assertTrue(dirty["scope_complete"])

    @unittest.skipUnless(GIT, "git binary required for deletion test")
    def test_deleted_tracked_file_flips_clean_and_complete_and_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed_repo(tmp, {"keep.txt": "keep\n", "drop.txt": "drop\n"})
            scope = ["keep.txt", "drop.txt"]
            before = scoped_fingerprint(tmp, scope)
            self.assertTrue(before["worktree_clean_scoped"])
            self.assertTrue(before["scope_complete"])
            os.unlink(os.path.join(tmp, "drop.txt"))
            after = scoped_fingerprint(tmp, scope)
            self.assertFalse(after["worktree_clean_scoped"])
            self.assertFalse(after["scope_complete"])
            self.assertEqual(after["missing"], ["drop.txt"])
            self.assertNotEqual(before["fingerprint"], after["fingerprint"])

    @unittest.skipUnless(GIT, "git binary required for cwd test")
    def test_cwd_independent_call_from_tmp(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed_repo(tmp, {"tracked.txt": "clean\n"})
            expected = scoped_fingerprint(tmp, ["tracked.txt"])
            cwd = os.getcwd()
            try:
                os.chdir(tempfile.gettempdir())
                actual = scoped_fingerprint(tmp, [os.path.join(tmp, "tracked.txt")])
            finally:
                os.chdir(cwd)
            self.assertEqual(expected, actual)

    @unittest.skipUnless(GIT, "git binary required for prefix test")
    def test_absolute_prefix_identity_via_symlinked_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed_repo(tmp, {"a.txt": "same\n"})
            link = tmp + "-link"
            try:
                os.symlink(tmp, link)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            try:
                via_real = scoped_fingerprint(tmp, ["a.txt"])
                via_link = scoped_fingerprint(link, ["a.txt"])
                self.assertEqual(via_real["fingerprint"],
                                 via_link["fingerprint"])
                self.assertEqual(via_real["scoped_hashes"],
                                 via_link["scoped_hashes"])
            finally:
                os.unlink(link)

    def test_outside_root_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with tempfile.TemporaryDirectory() as elsewhere:
                outsider = os.path.join(elsewhere, "outsider.txt")
                with open(outsider, "w", encoding="utf-8") as fh:
                    fh.write("nope\n")
                with self.assertRaises(ValueError):
                    scoped_fingerprint(tmp, [outsider])
                with self.assertRaises(ValueError):
                    scoped_fingerprint(tmp, ["../escape.txt"])
                with self.assertRaises(ValueError):
                    scoped_fingerprint(tmp, [os.path.join(tmp, "..",
                                                           os.path.basename(tmp),
                                                           "..",
                                                           "escape2.txt")])

    def test_directory_in_scope_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            subdir = os.path.join(tmp, "subdir")
            os.makedirs(subdir)
            with self.assertRaises(ValueError):
                scoped_fingerprint(tmp, ["subdir"])
            with self.assertRaises(ValueError):
                scoped_fingerprint(tmp, ["."])

    # REASON for update: snapshot CLI gained --repo-root; receipt now
    # carries worktree_clean_scoped/scope_complete/contract_status.
    def test_snapshot_cli_prints_valid_json(self):
        rel = os.path.relpath(SNAPSHOT, REPO_ROOT)
        proc = subprocess.run(
            [sys.executable, SNAPSHOT, "--repo-root", REPO_ROOT,
             "--paths", rel],
            cwd=REPO_ROOT, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        receipt = json.loads(proc.stdout)
        for key in ("contract_status", "head_commit", "head_tree",
                    "worktree_clean_scoped", "scope_complete",
                    "scoped_hashes", "missing", "fingerprint"):
            self.assertIn(key, receipt)
        self.assertIn(rel, receipt["scoped_hashes"])

    def test_snapshot_cli_bare_repo_root_is_valid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, SNAPSHOT, "--repo-root", tmp],
                cwd=REPO_ROOT, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            receipt = json.loads(proc.stdout)  # must not raise
            self.assertIn("fingerprint", receipt)

    def test_snapshot_cli_outside_root_is_json_error_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            with tempfile.TemporaryDirectory() as elsewhere:
                outsider = os.path.join(elsewhere, "outsider.txt")
                with open(outsider, "w", encoding="utf-8") as fh:
                    fh.write("nope\n")
                proc = subprocess.run(
                    [sys.executable, SNAPSHOT, "--repo-root", tmp,
                     "--paths", outsider],
                    cwd=REPO_ROOT, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, timeout=60)
                self.assertEqual(proc.returncode, 2, msg=proc.stderr)
                receipt = json.loads(proc.stdout)  # must not raise
                self.assertIn("error", receipt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
