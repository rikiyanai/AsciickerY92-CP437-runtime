#!/usr/bin/env python3
"""Generate the source profile for future FL-4512 repository extraction.

The manifest is intentionally conservative. It includes tracked FL-4512-named
files, the explicitly owned current-runtime working-tree root (including files
that are not staged yet), adjacent runtime/source-union dependencies, required
build and launcher files, AOT compilers, tests, harnesses, schemas, compiled
glyph assets, fixture maps, paper and bibliography sources, verification
receipts, relevant CI, and skill-routing reference information. It excludes
build outputs (.run/, .o_*, .d_*), caches,
missing or deleted tracked paths, and unrelated scratch/artifact trees. It is
not the all-evidence Prism export; use scripts/package_fl4512_prism_archive.py
for that corpus.

Every included path carries a machine-readable category and reason. Two runs
at one HEAD produce identical semantic content (entries, source head,
generator version). Use --check to fail when the committed manifest is stale.
Use --materialize to export the set to an explicit destination outside the
repository. The standalone Git repository is NOT created here.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
GENERATOR_VERSION = "2.7.4"
GENERATOR_PATH = "scripts/fl4512_extract_manifest.py"
SCHEMA = "fl4512.extraction_manifest.v2"
DEFAULT_MANIFEST = Path(
    "docs/research/ascii/verification/fl4512/2026-09-18-fl4512-extraction-manifest.json"
)
DURABLE_INDEX = (
    "docs/research/ascii/verification/fl4512/"
    "2026-09-18-asciiid-runtime-separation-index.md"
)
PRIVATE_SKILL = (
    "/Users/r/.claude/skills/fl4512-rendering-npr-rendering-techniques/SKILL.md"
)
# Canonical build-spec union. The presenter, current-game, Classic-game, and
# terminal and local-authority specs each own an SRCS list; the source profile is the
# union of those lists, never one spec alone. The Classic pinned tree owns no
# SRCS list (variable-driven makefiles) and stays out of this union.
BUILD_SPEC_MAKEFILES = (
    "apps/asciicker-presenter/makefile_asciiid",
    "apps/asciicker-presenter/makefile_asciiid_mac",
    "apps/game-fl4512/makefile_game",
    "apps/game-fl4512/makefile_game_mac",
    "apps/game-classic/makefile_game_classic",
    "apps/game-classic/makefile_game_classic_mac",
    "makefile_game_term",
    "makefile_game_term_mac",
    "makefile_server",
)
# Per-file spec labels aligned with BUILD_SPEC_MAKEFILES order.
BUILD_SPEC_NAMES = (
    "presenter",
    "presenter",
    "current",
    "current",
    "classic",
    "classic",
    "terminal",
    "terminal",
    "server",
)
# Back-compat alias: the presenter-owned pair of the build-spec union.
PRESENTER_MAKEFILES = (
    "apps/asciicker-presenter/makefile_asciiid",
    "apps/asciicker-presenter/makefile_asciiid_mac",
)
PRESENTER_INCLUDE_DIRS = (
    ".",
    "vendor",
    "engine",
    "engine/render",
    "editor",
    "platform",
    "server",
)
RUNTIME_DATA_CONTRACT_PATH = "shared/runtime_data/runtime_data_contract.v1.json"
RUNTIME_DATA_CONTRACT = json.loads(
    (ROOT / RUNTIME_DATA_CONTRACT_PATH).read_text(encoding="utf-8")
)
PRESENTER_DATA_PACKAGES = tuple(
    name
    for name, package in RUNTIME_DATA_CONTRACT["data_packages"].items()
    if "asciicker_presenter" in package.get("consumers", ())
)
PRESENTER_RUNTIME_ROOTS = tuple(sorted({
    prefix
    for name in PRESENTER_DATA_PACKAGES
    for prefix in RUNTIME_DATA_CONTRACT["data_packages"][name].get("path_prefixes", ())
}))
PRESENTER_BUILD_ROOTS = ("vendor",)
CURRENT_RUNTIME_ROOT = "apps/fl4512-runtime"
TERMINAL_RUNTIME_ROOT = "apps/terminal-runtime"
# Terminal source-closure contract. The terminal makefiles include this .mk
# and compile its expanded source variables; extraction follows that include
# edge instead of copying terminal paths into a local list.
TERMINAL_CONTRACT_MK = "apps/terminal-runtime/terminal_runtime_sources.mk"
# Product-owned source closure. Consumer specs select partitions from this
# manifest; extraction expands the selections instead of copying file lists.
PRODUCT_MANIFEST_MK = "apps/fl4512-runtime/fl4512_product_sources.mk"
QUOTED_INCLUDE_RE = re.compile(r'^\s*#\s*include\s*"([^"]+)"', re.MULTILINE)
_CLOSURE_EXTENSIONS = (".c", ".cc", ".cpp", ".cxx", ".h", ".hpp")
# Build checks own their data defaults; extraction asks for the declared inputs.
BUILD_INPUT_DECLARERS = ("scripts/check_actor_visual_table_coverage.py",)
MAKE_PYTHON_RECIPE_RE = re.compile(r"^\t\s*@?python3\s+(scripts/[\w/.-]+\.py)\b", re.MULTILINE)

PATH_RE = re.compile(
    r"(fl[-_]?4512|4512|glyph-rendering-paper|asciicker-presenter|"
    r"presenter_app|classic_asciiid|asciiid-classic|asciiid)",
    re.IGNORECASE,
)
CONTENT_PATTERN = (
    r"FL-4512|fl4512|4512|glyph-rendering-paper|asciicker-presenter|"
    r"ASCIIID Classic|asciiid-classic"
)
ADJACENT_PATH_RE = re.compile(
    r"^(assets/glyphs/(generated|schema|profiles|fixtures)|"
    r"assets/a3d/|engine/(render/)?(glyph_|fl4131_|fl4512_|curvature)|"
    r"engine/render/(projected_surface_feature|glyph_graph_runtime_adapter)|"
    r"platform/fl4512_|editor/(asciiid|classic_asciiid|editor_|terrain_preview|urdo)|"
    r"scripts/(compile_fl4512|fl4512_|analyze_fl4512|build_fl4512|validate_fl4512)|"
    r"scripts/tests/test_fl4512|tests/engine_tests/fl4512|"
    r"godot_project/(scripts|shaders).*fl4512|"
    r"docs/research/ascii/(verification/fl4512|articles)|"
    r"articles/.*fl4512|prototype/)",
    re.IGNORECASE,
)
SOURCE_UNION_FILES = (
    ".gitignore",
    "AGENTS.md",
    "docs/FAILURE_LOG.md",
    "Makefile",
    "pyproject.toml",
    "apps/asciicker-presenter/makefile_asciiid",
    "apps/asciicker-presenter/makefile_asciiid_mac",
    "apps/asciicker-presenter/README.md",
    "apps/game-fl4512/makefile_game",
    "apps/game-fl4512/makefile_game_mac",
    "apps/game-classic/makefile_game_classic",
    "apps/game-classic/makefile_game_classic_mac",
    "apps/asciiid-classic/makefile_asciiid_classic",
    "apps/asciiid-classic/makefile_asciiid_classic_mac",
    "apps/asciiid-classic/README.md",
    "apps/asciiid-classic/SKILL.md",
    "apps/asciiid-classic/Info.plist",
    "apps/asciiid-classic/CLASSIC_PIN.md",
    "apps/asciiid-classic/PIN_MANIFEST.sha256",
    "scripts/presenter_app.py",
    "scripts/package_asciicker_presenter_html_mac.py",
    "scripts/tests/test_asciicker_presenter_html_mac.py",
    "scripts/tests/test_fl4588_evidence_viewer_handoff.py",
    "scripts/tests/test_fl4588_no_read_only_product_ui.py",
    "scripts/tests/test_fl4588_document_merge_runtime_transaction.py",
    "scripts/tests/test_fl4588_launcher_html_only.py",
    "scripts/tests/test_fl4588_presenter_wrapper.py",
    "scripts/tests/test_fl4689_launcher_product_modes.py",
    "tests/scripts_tests/test_fl4588_html_user_action_guard.py",
    "tests/scripts_tests/test_asciiid_computer_use_guard_hook.py",
    "scripts/fl4588_html_user_action_guard.py",
    "scripts/fl4588_ui_registry_identity.py",
    "scripts/launcher_agent_bridge.py",
    "scripts/sbu_e2e_run.py",
    "scripts/attach_sbu_assets.py",
    "scripts/apply_map_edits.py",
    "scripts/asciid.py",
    "scripts/README.md",
    "scripts/a3d/__init__.py",
    "scripts/classic/__init__.py",
    "scripts/classic/app.py",
    "scripts/fl4512_extract_manifest.py",
    "scripts/package_fl4512_prism_archive.py",
    "scripts/a3d/compatibility.py",
    "scripts/a3d/runtime_data_contract.py",
    "scripts/tests/test_asciid_cli.py",
    "scripts/tests/test_asciiid_classic_skill_route.py",
    "scripts/maintainer/hooks/md_file_ban_hook.py",
    "docs/agent/agents.md",
    "shared/runtime_data/a3d_abi.h",
    RUNTIME_DATA_CONTRACT_PATH,
    TERMINAL_CONTRACT_MK,
)
BUILD_LAUNCHER_FILES = frozenset(
    {
        "apps/game-fl4512/makefile_game",
        "apps/game-fl4512/makefile_game_mac",
        "apps/game-classic/makefile_game_classic",
        "apps/game-classic/makefile_game_classic_mac",
        "makefile_game_term",
        "makefile_game_term_mac",
        "makefile_server",
        "scripts/launcher.py",
        "scripts/write_native_build_identity.py",
        "build-web.sh",
        "clean.sh",
        "run_console.sh",
        "run_xterm.sh",
    }
)
LAUNCHER_LIB_RE = re.compile(r"^(scripts/launcher_lib/|apps/asciiid-classic/)")
AOT_COMPILER_RE = re.compile(r"^scripts/compile_fl4512_.*\.py$")
TEST_RE = re.compile(r"^(scripts/tests/test_fl4512|tests/engine_tests/fl4512)")
SCHEMA_RE = re.compile(r"^assets/glyphs/schema/|\.schema\.json$")
COMPILED_ASSET_RE = re.compile(r"^assets/glyphs/(generated|profiles)/")
FIXTURE_MAP_RE = re.compile(r"^(assets/glyphs/fixtures/|assets/a3d/)")
PAPER_RE = re.compile(
    r"^docs/research/ascii/verification/fl4512/paper/|references\.bib$"
)
REFERENCE_CORPUS_RE = re.compile(
    r"^docs/research/ascii/articles/|^articles/.*fl4512"
)
VERIFICATION_RECEIPT_RE = re.compile(
    r"^docs/research/ascii/verification/fl4512/"
)
CI_FILES = frozenset({".github/workflows/fl4512-paper.yml"})

EXCLUDE_RES: tuple[tuple[re.Pattern[str], str], ...] = (
    # FL-4512: Finder metadata is not a product source, even in an owned root.
    (re.compile(r"(^|/)\.DS_Store$"), "cache-finder-metadata"),
    (re.compile(r"^\.run(/|$)"), "build-output-run-dir"),
    (re.compile(r"(^|/)\.[od]_[^/]*"), "build-output-objdep-dir"),
    (re.compile(r"(^|/)\.cache(/|$)"), "cache-dir"),
    (re.compile(r"(^|/)__pycache__(/|$)"), "cache-pycache"),
    (re.compile(r"(^|/)\.venv(/|$)"), "cache-venv"),
    (re.compile(r"(^|/)node_modules(/|$)"), "vendored-deps"),
    (re.compile(r"^\.scratch(/|$)"), "unrelated-scratch-tree"),
    (re.compile(r"^artifacts(/|$)"), "unrelated-artifact-tree"),
    (re.compile(r"^graft(/|$)"), "graft-projection-copy"),
)

EXCLUDED_BUILD_OUTPUT_PATTERNS = [
    ".run/",
    ".o_asciiid/",
    ".d_asciiid/",
    ".o_presenter/",
    ".d_presenter/",
    ".o_asciiid_classic/",
    ".d_asciiid_classic/",
    ".o_game/",
    ".d_game/",
]

def git_lines(args: list[str]) -> list[str]:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).splitlines()


def git_tracked_files() -> set[str]:
    """Return pure ``git ls-files`` paths present in the working tree.

    Gitlinks (mode 160000 submodule pointers such as
    tmp/unicasso-fl4512-audit-20260821) resolve to directories, not files,
    and are excluded here as non-exportable tracked paths.
    """
    return {rel for rel in git_lines(["ls-files"]) if (ROOT / rel).is_file()}


def tracked_files() -> list[str]:
    """Return pure git tracked regular files present in the working tree."""
    return sorted(git_tracked_files())


def source_union_anchors() -> set[str]:
    """Return present SOURCE_UNION_FILES paths, tracked or pre-commit."""
    return {rel for rel in SOURCE_UNION_FILES if (ROOT / rel).is_file()}


def current_runtime_files() -> set[str]:
    """Return files under the explicit current-runtime ownership root.

    This root is a source owner before Git staging or commit. Discovering it
    from the working tree prevents a new current-only source from becoming
    visible to extraction only as a side effect of staging it first.
    """
    root = ROOT / CURRENT_RUNTIME_ROOT
    if not root.is_dir():
        return set()
    paths: set[str] = set()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        if exclusion_reason(rel) is None:
            paths.add(rel)

    return paths


def terminal_runtime_files() -> set[str]:
    """Return files under the explicit terminal-runtime ownership root.

    This root owns terminal-only sources (such as the ANSI polling driver)
    before Git staging or commit. Discovering it from the working tree
    prevents a new terminal-only source from becoming visible to extraction
    only as a side effect of staging it first.
    """
    root = ROOT / TERMINAL_RUNTIME_ROOT
    if not root.is_dir():
        return set()
    paths: set[str] = set()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        if exclusion_reason(rel) is None:
            paths.add(rel)
    return paths


def source_files() -> list[str]:
    """Return tracked plus source-union anchors plus owned runtime roots."""
    return sorted(
        set(tracked_files())
        | source_union_anchors()
        | current_runtime_files()
        | terminal_runtime_files()
    )


def runtime_data_packages(rel: str) -> tuple[str, ...]:
    matches: list[str] = []
    for name in PRESENTER_DATA_PACKAGES:
        package = RUNTIME_DATA_CONTRACT["data_packages"][name]
        if any(rel == prefix or rel.startswith(prefix + "/")
               for prefix in package.get("path_prefixes", ())):
            matches.append(name)
            continue
        if any(fnmatch.fnmatchcase(rel, pattern)
               for pattern in package.get("path_globs", ())):
            matches.append(name)
    return tuple(matches)


def content_matches(paths: Iterable[str]) -> set[str]:
    tracked = set(paths)
    if not tracked:
        return set()
    try:
        matches = set(git_lines(["grep", "-Il", "-E", CONTENT_PATTERN]))
    except subprocess.CalledProcessError as exc:
        if exc.returncode == 1:
            return set()
        raise


def product_manifest_vars() -> dict[str, list[str]]:
    """Parse the product-owned source manifest into make variables."""
    return parse_make_vars(ROOT / PRODUCT_MANIFEST_MK)


def parse_makefile_srcs(makefile: Path) -> list[str]:
    """Return the SRCS entries of a build-spec makefile in order.

    The rule is syntactic: collect the ``SRCS :=`` assignment across
    backslash continuations and split on whitespace, then collect any
    ``SRCS +=`` tails. ``$(FL4512_*)`` selections expand through the
    product-owned manifest. No shell, no .d files.
    """
    entries: list[str] = []
    in_srcs = False
    tails: list[str] = []
    for line in makefile.read_text(encoding="utf-8").splitlines():
        if not in_srcs:
            if re.match(r"^SRCS\s*:=", line):
                in_srcs = True
                line = re.sub(r"^SRCS\s*:=\s*", "", line)
            elif re.match(r"^SRCS\s*\+=", line):
                tails.append(re.sub(r"^SRCS\s*\+=\s*", "", line))
                continue
            else:
                continue
        continued = line.rstrip().endswith("\\")
        chunk = line.replace("\\", " ")
        entries.extend(chunk.split())
        if not continued:
            in_srcs = False
    variables = product_manifest_vars()
    expanded = [t for t in expand_make_tokens(entries + tails, variables) if "$(" not in t and "${" not in t]
    if makefile.name in ("makefile_game_term", "makefile_game_term_mac"):
        expanded.extend(terminal_expanded_sources()[makefile.name])
    return expanded

_MAKE_VAR_ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*[:?+]?=\s*(.*)$")
_MAKE_TOKEN_RE = re.compile(r"\$\(([^)]+)\)|\$\{([^}]+)\}")
_MAKE_INCLUDE_RE = re.compile(r"^\s*-?include\s+(.+)$")


def parse_make_vars(makefile: Path) -> dict[str, list[str]]:
    """Collect simple make variable assignments across continuations.

    Syntactic only: ``:=``/``=`` replace, ``+=`` appends, backslash
    continuations join. No shell, no conditionals, no function calls.
    """
    variables: dict[str, list[str]] = {}
    pending_name: str | None = None
    pending_text = ""
    pending_append = False

    def commit(name: str, text: str, append: bool) -> None:
        tokens = text.split()
        if append:
            variables.setdefault(name, []).extend(tokens)
        else:
            variables[name] = tokens

    for line in makefile.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if pending_name is not None:
            chunk = stripped
            continued = chunk.endswith("\\")
            pending_text += " " + (chunk[:-1] if continued else chunk)
            if not continued:
                commit(pending_name, pending_text, pending_append)
                pending_name = None
            continue
        if not stripped or stripped.startswith("#"):
            continue
        match = _MAKE_VAR_ASSIGN_RE.match(stripped)
        if match is None:
            continue
        name, text = match.group(1), match.group(2).strip()
        append = stripped.split("=", 1)[0].rstrip().endswith("+")
        continued = text.endswith("\\")
        body = text[:-1] if continued else text
        if continued:
            pending_name, pending_text, pending_append = name, body, append
        else:
            commit(name, body, append)
    if pending_name is not None:
        commit(pending_name, pending_text, pending_append)
    return variables


def expand_make_tokens(
    tokens: list[str], variables: dict[str, list[str]], depth: int = 10
) -> list[str]:
    """Expand ``$(VAR)``/``${VAR}`` tokens against parsed variables."""
    expanded = list(tokens)
    for _ in range(depth):
        changed = False
        out: list[str] = []
        for token in expanded:
            def replace(match: re.Match[str]) -> str:
                name = match.group(1) if match.group(1) is not None else match.group(2)
                return " ".join(variables.get(name, [match.group(0)]))
            new_token = _MAKE_TOKEN_RE.sub(replace, token)
            changed = changed or new_token != token
            out.extend(new_token.split())
        expanded = out
        if not changed:
            break
    return expanded


def terminal_expanded_sources() -> dict[str, list[str]]:
    """Map each terminal makefile to its include-expanded SRCS entries.

    Follows the ``include apps/terminal-runtime/terminal_runtime_sources.mk``
    edge and expands the ``SRCS += $(TERMINAL_RUNTIME_SOURCES)`` tail through
    the makefile's own platform binding overlaid on the contract variables.
    Linux binds ``TERMINAL_RUNTIME_LINUX_SOURCES``; macOS binds the ``_MAC``
    set. No shell, no staging side effect.
    """
    expanded: dict[str, list[str]] = {}
    for makefile_rel in ("makefile_game_term", "makefile_game_term_mac"):
        makefile = ROOT / makefile_rel
        variables: dict[str, list[str]] = {}
        for name, tokens in product_manifest_vars().items():
            variables.setdefault(name, tokens)
        for line in makefile.read_text(encoding="utf-8").splitlines():
            match = _MAKE_INCLUDE_RE.match(line)
            if match is None:
                continue
            for chunk in match.group(1).split():
                included = ROOT / chunk
                if included.is_file():
                    for name, tokens in parse_make_vars(included).items():
                        variables.setdefault(name, tokens)
        for name, tokens in parse_make_vars(makefile).items():
            variables[name] = tokens
        tail: list[str] = []
        in_srcs = False
        for line in makefile.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not in_srcs:
                if re.match(r"^SRCS\s*\+=", stripped):
                    in_srcs = True
                    stripped = re.sub(r"^SRCS\s*\+=\s*", "", stripped)
                else:
                    continue
            continued = stripped.endswith("\\")
            tail.extend(stripped.replace("\\", " ").split())
            if not continued:
                in_srcs = False
        expanded[makefile_rel] = expand_make_tokens(tail, variables)
    return expanded


def build_source_provenance() -> dict[str, tuple[str, ...]]:
    """Map each SRCS entry to the sorted spec names compiling it.

    One syntactic rule covers every spec: collect the ``SRCS :=``
    assignment across backslash continuations and split on whitespace.
    Terminal specs additionally follow their ``include`` edge into the
    terminal source-closure contract and expand the ``SRCS +=`` tail
    through the platform-bound source variables. No shell, no .d files,
    no broad path regex.
    """
    provenance: dict[str, set[str]] = {}
    for spec, makefile in zip(BUILD_SPEC_NAMES, BUILD_SPEC_MAKEFILES):
        for entry in parse_makefile_srcs(ROOT / makefile):
            provenance.setdefault(entry, set()).add(spec)
    for makefile_rel, entries in terminal_expanded_sources().items():
        for entry in entries:
            provenance.setdefault(entry, set()).add("terminal")
    return {entry: tuple(sorted(specs)) for entry, specs in provenance.items()}


def build_direct_sources() -> set[str]:
    """Return the union of SRCS entries from all build-spec makefiles."""
    return set(build_source_provenance())


def _resolve_repo_include(name: str, includer: str) -> str | None:
    """Resolve a quoted include to a repo-relative path or None.

    Search order mirrors the makefile ``-I`` flags: the includer's own
    directory first, then each ``PRESENTER_INCLUDE_DIRS`` entry.
    """
    search_roots = [posixpath.dirname(includer), *PRESENTER_INCLUDE_DIRS]
    for root in search_roots:
        candidate = posixpath.normpath(posixpath.join(root, name))
        if (ROOT / candidate).is_file():
            return candidate
    return None


def build_include_closure(seeds: set[str], tracked: set[str]) -> set[str]:
    """Return repo-local quoted-#include files reachable from seeds.

    Breadth-first walk over tracked C/C++ files only. System and missing
    includes resolve to None and stay out of the closure.
    """
    seen = set(seeds)
    queue = sorted(seeds)
    while queue:
        rel = queue.pop()
        if not rel.endswith(_CLOSURE_EXTENSIONS):
            continue
        path = ROOT / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name in QUOTED_INCLUDE_RE.findall(text):
            resolved = _resolve_repo_include(name, rel)
            if resolved is None or resolved not in tracked or resolved in seen:
                continue
            seen.add(resolved)
            queue.append(resolved)
    return seen - set(seeds)


def build_dependency_closure(
    available_files: set[str],
) -> tuple[set[str], set[str]]:
    """Return (direct SRCS, include closure) for the build-spec union.

    Direct entries are filtered to tracked files; a parsed SRCS entry that
    is absent from the manifest is a fail-closed signal caught by tests.
    """
    direct = {rel for rel in build_direct_sources() if rel in available_files}
    return direct, build_include_closure(direct, available_files)


def build_tool_inputs() -> set[str]:
    """Include Python recipe entrypoints and native checks' declared inputs.

    This supplements the C/C++ closure: a clean build must also execute its
    prerequisite checks. The check itself remains the owner of its input paths.
    """
    paths = {
        script
        for makefile in BUILD_SPEC_MAKEFILES
        for script in MAKE_PYTHON_RECIPE_RE.findall((ROOT / makefile).read_text())
    }
    for script in BUILD_INPUT_DECLARERS:
        if script not in paths:
            raise ValueError(f"native input declarer is not invoked by a build spec: {script}")
        declared = json.loads(subprocess.check_output(
            [sys.executable, str(ROOT / script), "--list-inputs"], cwd=ROOT, text=True,
        ))
        paths.update(declared)
    for rel in paths:
        path = Path(rel)
        if path.is_absolute() or ".." in path.parts or not (ROOT / path).is_file():
            raise ValueError(f"missing or invalid native build tool/input: {rel}")
    return paths


def exclusion_reason(rel: str) -> str | None:
    for rx, reason in EXCLUDE_RES:
        if rx.search(rel):
            return reason
    return None


def categorize(
    rel: str,
    content_hit: bool,
    direct_sources: set[str] | frozenset[str] = frozenset(),
    include_closure: set[str] | frozenset[str] = frozenset(),
    tool_inputs: set[str] | frozenset[str] = frozenset(),
) -> tuple[str, str] | None:
    """Return (category, reason) for an included path, else None."""
    if rel == CURRENT_RUNTIME_ROOT or rel.startswith(CURRENT_RUNTIME_ROOT + "/"):
        return (
            "current_runtime_source",
            "explicit current-runtime ownership root discovered from the working tree",
        )
    if rel in direct_sources:
        return ("build_source", "direct SRCS entry in the presenter/current/Classic/terminal build specs")
    if rel in include_closure:
        return ("build_include", "repo-local quoted-#include closure of build-spec SRCS")
    if rel in tool_inputs:
        return ("build_tool_input", "build-spec Python recipe entrypoint or declared native-check input")
    if rel in SOURCE_UNION_FILES:
        return ("source_union", "explicit source-union anchor for extraction")
    packages = runtime_data_packages(rel)
    if packages:
        category = (
            "fl4512_runtime_data"
            if "fl4512_compiled_selection" in packages
            else "shared_runtime_data"
        )
        return (category, "runtime-data contract packages: " + ",".join(packages))
    for root in PRESENTER_BUILD_ROOTS:
        if rel == root or rel.startswith(root + "/"):
            return ("presenter_build_dependency", f"declared presenter build root {root}")
    if rel in BUILD_LAUNCHER_FILES or LAUNCHER_LIB_RE.search(rel):
        return ("build_launcher", "required build or launcher file")
    if AOT_COMPILER_RE.search(rel):
        return ("aot_compiler", "offline FL-4512 AOT pool/feature compiler")
    if TEST_RE.search(rel):
        return ("test", "focused FL-4512 test owner")
    if "harness" in rel and "fl4512" in rel.lower():
        return ("harness", "FL-4512 compiler/verification harness")
    if SCHEMA_RE.search(rel) and "glyph" in rel.lower():
        return ("schema", "glyph/pool/fixture schema owner")
    if COMPILED_ASSET_RE.search(rel):
        return ("compiled_asset", "compiled glyph pool asset")
    if FIXTURE_MAP_RE.search(rel):
        return ("fixture_map", "glyph fixture or a3d fixture map")
    if PAPER_RE.search(rel):
        return ("paper_bibliography", "paper or bibliography source")
    if REFERENCE_CORPUS_RE.search(rel):
        return ("reference_corpus", "reference corpus or intake media source")
    if rel == DURABLE_INDEX or rel == str(DEFAULT_MANIFEST):
        return ("index_doc", "durable separation index or extraction manifest")
    if rel in CI_FILES:
        return ("ci", "FL-4512 paper CI owner")
    if VERIFICATION_RECEIPT_RE.search(rel):
        return ("verification_receipt", "FL-4512 verification receipt")
    if ADJACENT_PATH_RE.search(rel):
        return (
            "adjacent_runtime",
            "adjacent glyph-graph/runtime owner without FL-4512 in path",
        )
    if PATH_RE.search(rel):
        return ("fl4512_path", "tracked FL-4512/asciiid path-name match")
    if content_hit:
        return ("fl4512_content", "tracked FL-4512 content match")
    return None


def build_manifest(*, include_content_scan: bool = False) -> dict:
    tracked = set(tracked_files())
    files = source_files()
    tracked_set = set(files)
    direct_sources, include_closure = build_dependency_closure(tracked_set)
    tool_inputs = build_tool_inputs()
    missing_tool_inputs = tool_inputs - tracked_set
    if missing_tool_inputs:
        raise ValueError(f"native build inputs absent from source inventory: {sorted(missing_tool_inputs)}")
    content_hits = content_matches(files) if include_content_scan else set()
    entries: list[dict] = []
    excluded_counts: dict[str, int] = {}
    excluded_samples: dict[str, list[str]] = {}
    for rel in files:
        reason = exclusion_reason(rel)
        if reason is not None:
            excluded_counts[reason] = excluded_counts.get(reason, 0) + 1
            if len(excluded_samples.setdefault(reason, [])) < 10:
                excluded_samples[reason].append(rel)
            continue
        hit = rel in content_hits
        cat = categorize(rel, hit, direct_sources, include_closure, tool_inputs)
        if cat is None:
            continue
        category, why = cat
        entry = {"path": rel, "category": category, "reason": why}
        packages = runtime_data_packages(rel)
        if packages:
            entry["data_packages"] = list(packages)
        entries.append(entry)
    entries.sort(key=lambda e: e["path"])
    paths = [e["path"] for e in entries]
    categories: dict[str, list[str]] = {}
    for e in entries:
        categories.setdefault(e["category"], []).append(e["path"])
    counts = {name: len(v) for name, v in categories.items()}
    counts["extraction_files"] = len(paths)
    return {
        "schema": SCHEMA,
        "generator_version": GENERATOR_VERSION,
        "generator_path": GENERATOR_PATH,
        "source_head": git_lines(["rev-parse", "HEAD"])[0],
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tracked_total": len(tracked),
        "untracked_source_total": len(set(files) - tracked),
        "source_total": len(files),
        "content_scan": include_content_scan,
        "counts": counts,
        "categories": categories,
        "entries": entries,
        "files": paths,
        "excluded_counts": excluded_counts,
        "excluded_samples": excluded_samples,
        "dependency_closure": {
            "rule": (
                "union of SRCS entries parsed from the presenter pair "
                "(apps/asciicker-presenter/makefile_asciiid{,_mac}), the current-game pair "
                "(apps/game-fl4512/makefile_game{,_mac}), the Classic-game pair "
                "(apps/game-classic/makefile_game_classic{,_mac}), and the terminal pair "
                "(makefile_game_term{,_mac}) with the terminal SRCS += tail expanded "
                "through the included terminal source-closure contract "
                "(apps/terminal-runtime/terminal_runtime_sources.mk), plus the breadth-first repo-local "
                'quoted-#include closure resolved through PRESENTER_INCLUDE_DIRS, '
                "plus Python recipe entrypoints and declared native build-check inputs; "
                "plus every tracked file in presenter-consumed packages from "
                "the shared runtime-data contract and PRESENTER_BUILD_ROOTS; "
                "plus every present file under the explicit current-runtime "
                "ownership root, whether staged or unstaged; "
                "no .d files, no content regex; "
                "explicit profile limits: third-party vendor trees beyond declared build roots, "
                "the full article corpus beyond FL-4512 intake media, generated binary outputs, "
                "and visual proof stay out of this source-path index"
            ),
            "makefiles": list(BUILD_SPEC_MAKEFILES),
            "include_dirs": list(PRESENTER_INCLUDE_DIRS),
            "runtime_roots": list(PRESENTER_RUNTIME_ROOTS),
            "runtime_data_contract": RUNTIME_DATA_CONTRACT_PATH,
            "runtime_data_packages": list(PRESENTER_DATA_PACKAGES),
            "build_roots": list(PRESENTER_BUILD_ROOTS),
            "direct_sources": len(direct_sources),
            "include_closure": len(include_closure),
            "build_tool_inputs": sorted(tool_inputs),
        },
        "skill_routing": {
            "skill_name": "fl4512-rendering-npr-rendering-techniques",
            "skill_path": PRIVATE_SKILL,
            "boundary": (
                "read-only 86-source corpus projection; never owns runtime "
                "state, proof, acceptance, or closure"
            ),
            "durable_index": DURABLE_INDEX,
            "extraction_manifest": str(DEFAULT_MANIFEST),
            "fl_context": (
                'python3 scripts/analyze_runs.py fl context FL-4512 '
                '--task "<bounded task>"'
            ),
        },
        "materialize_consumer": (
            "python3 scripts/fl4512_extract_manifest.py --materialize "
            "/tmp/fl4512-extract-<date> --manifest "
            + str(DEFAULT_MANIFEST)
        ),
    }


def semantic_view(manifest: dict) -> dict:
    return {
        "schema": manifest["schema"],
        "generator_version": manifest["generator_version"],
        "source_head": manifest["source_head"],
        "entries": manifest["entries"],
    }


def check_manifest(manifest_path: Path, *, include_content_scan: bool | None = None) -> int:
    """Fail (exit 1) when the committed manifest is stale. Return exit code.

    Stale means the entry set drifted (added/removed/recategorized paths) or
    the schema/generator moved. A bare source_head lag with identical entries
    passes with a note: recording HEAD inside the manifest means the commit
    carrying a regeneration always advances HEAD by one, so exact head
    equality can never converge.
    """
    try:
        committed = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"stale: committed manifest missing: {manifest_path}", file=sys.stderr)
        return 1
    scan = (
        bool(committed.get("content_scan", False))
        if include_content_scan is None
        else include_content_scan
    )
    fresh = build_manifest(include_content_scan=scan)
    problems: list[str] = []
    for key in ("schema", "generator_version"):
        if committed.get(key) != fresh[key]:
            problems.append(
                f"{key}: committed={committed.get(key)!r} current={fresh[key]!r}"
            )
    committed_paths = [e["path"] for e in committed.get("entries", [])]
    fresh_paths = [e["path"] for e in fresh["entries"]]
    if committed_paths != fresh_paths:
        only_committed = sorted(set(committed_paths) - set(fresh_paths))[:10]
        only_fresh = sorted(set(fresh_paths) - set(committed_paths))[:10]
        problems.append(
            f"entries differ: committed={len(committed_paths)} "
            f"fresh={len(fresh_paths)} "
            f"only_committed={only_committed} only_fresh={only_fresh}"
        )
    else:
        for old, new in zip(committed["entries"], fresh["entries"]):
            if old != new:
                problems.append(f"entry differs at {old.get('path')}: {old} != {new}")
                break
    if problems:
        print("stale extraction manifest:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print(f"regenerate: python3 {GENERATOR_PATH} --output {manifest_path}", file=sys.stderr)
        return 1
    if committed.get("source_head") != fresh["source_head"]:
        print(
            f"manifest entries current ({len(fresh_paths)} files); "
            f"source_head lags by provenance: "
            f"committed={str(committed.get('source_head'))[:12]} "
            f"current={fresh['source_head'][:12]}"
        )
        return 0
    print(
        f"manifest current: {len(fresh_paths)} files at {fresh['source_head'][:12]} "
        f"(generator {GENERATOR_VERSION})"
    )
    return 0


def _reject_materialize_dest(dest: Path) -> Path:
    if not dest.is_absolute():
        raise ValueError(f"materialize destination must be absolute: {dest}")
    tmp = Path(tempfile.gettempdir()).resolve()
    resolved = dest.resolve() if dest.exists() else (dest.parent.resolve() / dest.name) if dest.parent.exists() else dest.absolute()
    root_r = ROOT.resolve()
    if resolved == root_r or root_r in resolved.parents:
        raise ValueError(f"materialize destination must be outside the repository: {dest}")
    if resolved == tmp:
        raise ValueError("use a dedicated subdirectory under the temp dir, not the temp dir itself")
    home = Path.home().resolve()
    broad = {
        Path("/"),
        Path("/tmp"),
        Path("/Applications"),
        Path("/Library"),
        Path("/System"),
        Path("/bin"),
        Path("/etc"),
        Path("/usr"),
        home,
        home / "Downloads",
        home / "Desktop",
        home / "Documents",
    }
    if resolved in broad:
        raise ValueError(f"refusing broad or dangerous destination: {dest}")
    return resolved


def materialize(
    dest: Path, manifest: dict, *, overwrite: bool = False
) -> dict:
    """Copy manifest files under dest, preserving relative paths.

    Never deletes destination contents. Refuses to overwrite a differing
    existing file unless overwrite=True. Returns the receipt dict.
    """
    resolved = _reject_materialize_dest(dest)
    resolved.mkdir(parents=True, exist_ok=True)
    copied = 0
    skipped_identical = 0
    receipts: list[dict] = []
    for entry in manifest["entries"]:
        rel = entry["path"]
        src = ROOT / rel
        if not src.is_file():
            raise FileNotFoundError(f"manifest path missing in worktree: {rel}")
        target = resolved / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        digest_builder = hashlib.sha256()
        with src.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest_builder.update(chunk)
        digest = digest_builder.hexdigest()
        if target.exists():
            target_digest = hashlib.sha256()
            with target.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    target_digest.update(chunk)
            if target_digest.hexdigest() == digest:
                skipped_identical += 1
            elif not overwrite:
                raise FileExistsError(
                    f"destination file differs (pass --overwrite): {target}"
                )
            else:
                shutil.copy2(src, target)
                copied += 1
        else:
            shutil.copy2(src, target)
            copied += 1
        receipts.append(
            {"path": rel, "sha256": digest, "bytes": src.stat().st_size}
        )
    receipt = {
        "schema": "fl4512.extraction_materialize_receipt.v1",
        "source_head": manifest["source_head"],
        "generator_version": manifest.get("generator_version", GENERATOR_VERSION),
        "file_count": len(receipts),
        "copied": copied,
        "skipped_identical": skipped_identical,
        "destination": str(resolved),
        "files": receipts,
    }
    (resolved / "fl4512-extract-receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return receipt


def category_summary(manifest: dict) -> str:
    lines = [
        f"FL-4512 extraction manifest summary (generator {manifest.get('generator_version')})",
        f"source_head: {manifest.get('source_head')}",
        f"tracked_total: {manifest.get('tracked_total')}  "
        f"extraction_files: {manifest.get('counts', {}).get('extraction_files')}",
        "",
        "included by category:",
    ]
    for name in sorted(manifest.get("categories", {})):
        lines.append(f"  {name}: {len(manifest['categories'][name])}")
    lines.append("")
    lines.append("excluded by reason:")
    for name in sorted(manifest.get("excluded_counts", {})):
        lines.append(f"  {name}: {manifest['excluded_counts'][name]}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("json", "paths"), default="json")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--include-content-scan",
        action="store_true",
        help="run a slower git-grep content scan in addition to path and dependency matching",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail when the committed manifest is stale",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="committed manifest path for --check and --materialize",
    )
    parser.add_argument(
        "--materialize",
        type=Path,
        default=None,
        help="export manifest files under an explicit destination outside the repository",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="allow overwriting differing destination files during --materialize",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="print the bounded human-readable category/count summary",
    )
    ns = parser.parse_args()

    if ns.check:
        return check_manifest(ns.manifest)

    if ns.materialize is not None:
        try:
            manifest = json.loads(ns.manifest.read_text(encoding="utf-8"))
            receipt = materialize(ns.materialize, manifest, overwrite=ns.overwrite)
        except (ValueError, FileExistsError, FileNotFoundError, json.JSONDecodeError) as exc:
            print(f"materialize refused: {exc}", file=sys.stderr)
            return 1
        print(
            f"materialized {receipt['file_count']} files "
            f"({receipt['copied']} copied, {receipt['skipped_identical']} identical) "
            f"to {receipt['destination']}"
        )
        return 0

    manifest = build_manifest(include_content_scan=ns.include_content_scan)
    if ns.summary:
        body = category_summary(manifest)
    elif ns.format == "paths":
        body = "\n".join(manifest["files"]) + "\n"
    else:
        body = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if ns.output:
        ns.output.write_text(body, encoding="utf-8")
    else:
        print(body, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
