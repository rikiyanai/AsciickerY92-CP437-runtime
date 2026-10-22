"""Fault-inject the presenter's actual receipt transaction, using real files."""
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def receipt_probe(tmp_path_factory):
    source = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text()
    helpers = source[source.index("static bool Fl4512SiblingTempTemplate("):
                     source.index("static bool Fl4512ShellQuote(")]
    transaction = source[source.index("struct Fl4512ProgramReceiptCommit\n"):
                         source.index("static bool Fl4512CompileAndCommitProfileArtifact(")]
    output = tmp_path_factory.mktemp("fl4512_receipt_commit")
    probe_source = output / "probe.cpp"
    probe_source.write_text(r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <limits.h>
#include <sys/stat.h>
#include <errno.h>
static int fail_fsync_at = 0;
static int fsync_calls = 0;
static int fail_rename_at = 0;
static int rename_calls = 0;
static int ProbeFsync(int fd) {
    if (++fsync_calls == fail_fsync_at) { errno = EIO; return -1; }
    return fsync(fd);
}
static int ProbeRename(const char* from, const char* to) {
    if (++rename_calls == fail_rename_at) { errno = EIO; return -1; }
    return rename(from, to);
}
#define fsync ProbeFsync
#define rename ProbeRename
''' + helpers + transaction + r'''
int main(int argc, char** argv) {
    if (argc != 4) return 90;
    const char* mode = argv[3];
    {
        Fl4512ProgramReceiptCommit receipt;
        if (!receipt.Stage(argv[1], argv[2])) return 91;
        if (strcmp(mode, "rename_failure") == 0) fail_rename_at = 1;
        if (strcmp(mode, "sync_failure") == 0) fail_fsync_at = fsync_calls + 1;
        const bool installed = receipt.Install();
        if (strcmp(mode, "rename_failure") == 0 || strcmp(mode, "sync_failure") == 0) {
            if (installed) return 92;
        } else if (!installed) return 93;
        if (strcmp(mode, "accept") == 0) receipt.accepted = true;
        if (strcmp(mode, "explicit_rollback") == 0 && !receipt.Rollback()) return 94;
    }
    return 0;
}
''')
    binary = output / "probe"
    result = subprocess.run([
        "c++", "-std=c++11", "-fsanitize=address,undefined",
        str(probe_source), "-o", str(binary),
    ], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return binary


@pytest.mark.parametrize("had_original", [False, True])
@pytest.mark.parametrize("mode", [
    "accept", "implicit_rollback", "explicit_rollback", "rename_failure", "sync_failure",
])
def test_receipt_commit_retains_exact_prior_bytes(receipt_probe, tmp_path, had_original, mode):
    source = tmp_path / "new.json"
    target = tmp_path / "active.json"
    source.write_bytes(b'{"program":"new"}\n')
    previous = b'{"program":"prior"}\n'
    if had_original:
        target.write_bytes(previous)
    result = subprocess.run([str(receipt_probe), str(source), str(target), mode],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    if mode == "accept":
        assert target.read_bytes() == source.read_bytes()
    elif had_original:
        assert target.read_bytes() == previous
    else:
        assert not target.exists()
    assert not list(tmp_path.glob(".active.json.*"))
