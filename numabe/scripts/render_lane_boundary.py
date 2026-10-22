#!/usr/bin/env python3
"""FL-4877: keep True FF separate from the non-FF renderer, by source.

True FF is a separate renderer.  Its work must not change the source of the
non-FF renderer (Product ortho, the perspective probe backend, the shared
compositor and authority).  The FL-4781 feature registry does not own most of
that source, so a feature fingerprint alone cannot hold the boundary.

This module owns the boundary.  config/render_lane_boundary.json names the
protected files and the FF-private files.  Three modes:

  --staged MESSAGE_FILE   commit-msg mode.  Refuse a staged change to the
                          normalized source of a protected non-FF file unless
                          the message carries 'Non-FF-Render-Change: <reason>'.
                          A config change that removes protection from a
                          tracked file counts as a change to that file.
                          Refuse, without override, any protected shader whose
                          include closure reaches an FF-private file, any
                          ff_private_retired glob that matches a tracked file,
                          and a config that no longer protects itself.
  --diff BASE HEAD        list every protected non-FF file whose normalized
                          source changed in BASE..HEAD, with the commits that
                          touched it, and every non-FF shader root whose
                          expanded source changed.  Writes a content-addressed
                          receipt under artifacts/run_receipts/fl-4877/.
  --check                 integrity check of the config and include closures
                          at HEAD.

Normalization is the FL-4781 one: comment and whitespace edits do not count.
String literals, preprocessor lines (#define, #include paths) and all other
tokens do count.

Shaders are GLSL (.glsl/.glslinc, include closure owned by the FL-4781 gate)
and Godot shader language (.gdshader/.gdshaderinc, include closure owned here:
'#include "res://..."' and '#include "relative/path"', relative to the
including file, as the Godot shader preprocessor resolves them).

Commit-time policy (--staged), fail closed:

  merge      A merge commit is checked like any other commit, against HEAD,
             which during a merge is the first parent.  The first parent is
             the line the merge lands on, so every non-FF change the merge
             brings to that line needs the trailer.  FL-4781 skips merges;
             this boundary does not.
  no HEAD    Unborn branch or initial commit: every staged protected file
             counts as changed.
  HEAD has   The HEAD boundary is unknown: every staged file protected by the
  no config  index config counts as changed.
  config     Config malformed at HEAD, or missing or malformed in the index:
  unreadable the commit is refused without override.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import posixpath
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import godot_feature_regression_gate as gate  # noqa: E402

REPO_ROOT = SCRIPTS_DIR.parent
CONFIG_RELATIVE = "config/render_lane_boundary.json"
FL_ID = "FL-4877"
TRAILER = "Non-FF-Render-Change:"
SCHEMA = "render-lane-boundary/1"
GDSHADER_SUFFIXES = frozenset({".gdshader", ".gdshaderinc"})
SHADER_SUFFIXES = gate.GLSL_SUFFIXES | GDSHADER_SUFFIXES
BLOCKED = "GIT GUARD BLOCKED (FL-4877):"


class LaneConfigError(ValueError):
    """The lane config is missing or malformed."""


def _host_map(config: Mapping[str, Any], key: str) -> dict[str, dict[str, str]]:
    value = config.get(key, {})
    if not isinstance(value, dict):
        raise LaneConfigError(f"{CONFIG_RELATIVE}: {key} must map a path to {{name: reason}}")
    result: dict[str, dict[str, str]] = {}
    for path, entries in value.items():
        if not (
            isinstance(path, str)
            and path
            and isinstance(entries, dict)
            and all(isinstance(n, str) and n and isinstance(r, str) and r.strip() for n, r in entries.items())
        ):
            raise LaneConfigError(f"{CONFIG_RELATIVE}: {key}[{path!r}] must map names to non-empty reasons")
        result[path] = dict(entries)
    return result


class LaneBoundary:
    """Classify repository paths into the protected non-FF lane or not."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        if config.get("schema") != SCHEMA:
            raise LaneConfigError(f"{CONFIG_RELATIVE}: schema must be {SCHEMA!r}")
        lists = {}
        for key in ("protected", "excluded", "ff_private"):
            value = config.get(key)
            if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
                raise LaneConfigError(f"{CONFIG_RELATIVE}: {key} must be a list of glob strings")
            lists[key] = tuple(value)
        if not lists["protected"] or not lists["ff_private"]:
            raise LaneConfigError(f"{CONFIG_RELATIVE}: protected and ff_private must not be empty")
        retired = config.get("ff_private_retired", [])
        if not isinstance(retired, list) or not all(isinstance(item, str) and item for item in retired):
            raise LaneConfigError(f"{CONFIG_RELATIVE}: ff_private_retired must be a list of glob strings")
        self.protected = lists["protected"]
        self.excluded = lists["excluded"]
        self.ff_private = lists["ff_private"]
        # FF paths that exist only in history.  They classify old revisions;
        # a test holds that they match no current file.
        self.ff_private_retired = tuple(retired)
        # Host files (Main.gd, World.gd, project.godot) mix render inputs with
        # everything else, so only named parts of them are protected:
        # {path: {definition or setting selector: reason}}.
        self.protected_definitions = _host_map(config, "protected_definitions")
        self.protected_settings = _host_map(config, "protected_settings")

    def host_paths(self) -> set[str]:
        return set(self.protected_definitions) | set(self.protected_settings)

    @staticmethod
    def _match(path: str, patterns: Sequence[str]) -> bool:
        return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)

    def is_ff_private(self, path: str) -> bool:
        return self._match(path, self.ff_private) or self._match(path, self.ff_private_retired)

    def is_non_ff(self, path: str) -> bool:
        """True when the path belongs to the protected non-FF lane."""

        return (
            self._match(path, self.protected)
            and not self._match(path, self.excluded)
            and not self.is_ff_private(path)
        )


def load_boundary(text: str) -> LaneBoundary:
    try:
        config = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LaneConfigError(f"{CONFIG_RELATIVE}: invalid JSON: {exc}") from exc
    if not isinstance(config, dict):
        raise LaneConfigError(f"{CONFIG_RELATIVE}: top level must be an object")
    return LaneBoundary(config)


def _git(base_dir: Path, *args: str) -> str:
    return gate._git_text(base_dir, *args)


def _boundary_at(base_dir: Path, revision: str | None) -> LaneBoundary:
    """Config at a revision (':' index); worktree when absent there.

    For --diff and --check only: they classify history that predates the
    config.  The commit-time check uses _staged_boundaries, which never falls
    back to the worktree.
    """

    if revision is not None:
        prefix = ":" if revision == gate.INDEX_REVISION else f"{revision}:"
        proc = subprocess.run(
            ["git", "show", f"{prefix}{CONFIG_RELATIVE}"],
            cwd=base_dir,
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            return load_boundary(proc.stdout)
    path = base_dir / CONFIG_RELATIVE
    if not path.is_file():
        raise LaneConfigError(f"{CONFIG_RELATIVE} is missing")
    return load_boundary(path.read_text(encoding="utf-8"))


def _config_text(base_dir: Path, revision: str) -> str | None:
    """Config text at a commit or the index (':'); None when the path is absent there."""

    spec = f"{':' if revision == gate.INDEX_REVISION else revision + ':'}{CONFIG_RELATIVE}"
    probe = subprocess.run(["git", "cat-file", "-e", spec], cwd=base_dir, capture_output=True)
    if probe.returncode != 0:
        return None
    return _git(base_dir, "show", spec)


def _normalized(reader: gate._RevisionReader, path: str) -> str | None:
    oid = reader.oid(path)
    if oid is None:
        return None
    return gate.normalize_source(reader.read(oid), gate._source_language(path))


def _is_res_shader(path: str) -> bool:
    return Path(path).suffix.lower() in SHADER_SUFFIXES and path.startswith(
        gate.GODOT_RES_ROOT + "/"
    )


def godot_strip_comments(code: str) -> str:
    """Port of Godot 4.6 ShaderPreprocessor::CommentRemover::strip.

    servers/rendering/shader_preprocessor.cpp: '//' runs to the newline, '/*'
    to the next '*/' (or the end); a removed comment keeps its newlines and
    inserts nothing else, so 'a/**/b' becomes 'ab'.  A '"' toggles a string, and
    comment markers inside a string are text (so "res://x" is not a comment).
    An unmatched '*/' drops its '*'.  Line numbers are preserved.
    """

    out: list[str] = []
    index = 0
    size = len(code)
    in_string = False
    while index < size:
        char = code[index]
        index += 1
        if char == '"':
            in_string = not in_string
            out.append(char)
        elif char == "/" and not in_string and index < size and code[index] == "/":
            newline = code.find("\n", index)
            if newline < 0:
                index = size
            else:
                out.append("\n")
                index = newline + 1
        elif char == "/" and not in_string and index < size and code[index] == "*":
            index += 1
            while True:
                star = code.find("*", index)
                chunk = code[index:] if star < 0 else code[index:star]
                out.append("\n" * chunk.count("\n"))
                if star < 0:
                    index = size
                    break
                index = star + 1
                if index < size and code[index] == "/":
                    index += 1
                    break
        elif char == "*" and not in_string and index < size and code[index] == "/":
            continue
        else:
            out.append(char)
    return "".join(out)


def _skip_blanks_and_continuations(text: str, index: int) -> int:
    while index < len(text):
        if text[index] in " \t":
            index += 1
        elif text.startswith("\\\n", index):
            index += 2
        else:
            break
    return index


def include_directives(text: str, path: str) -> list[tuple[int, str]]:
    """(1-based line, raw include path) for every #include directive, Godot-style.

    After comment removal, a directive is a '#' preceded on its physical line
    only by blanks.  Blanks and backslash-newline continuations between '#' and
    'include' are skipped: Godot 4.6 accepts continuations there and rejects
    blanks ("Unknown directive"), and this scan counts both, so it can only
    over-report.  Conditionals are ignored: an include under '#if 0' counts.
    """

    stripped = godot_strip_comments(text)
    found: list[tuple[int, str]] = []
    line_start = 0
    line_number = 1
    while line_start <= len(stripped):
        line_end = stripped.find("\n", line_start)
        line_end = len(stripped) if line_end < 0 else line_end
        cursor = line_start
        while cursor < line_end and stripped[cursor] in " \t":
            cursor += 1
        if cursor < line_end and stripped[cursor] == "#":
            word = _skip_blanks_and_continuations(stripped, cursor + 1)
            after = word + len("include")
            if stripped.startswith("include", word) and not (
                after < len(stripped) and (stripped[after].isalnum() or stripped[after] == "_")
            ):
                opening = stripped.find('"', after)
                closing = stripped.find('"', opening + 1) if opening >= 0 else -1
                if opening < 0 or closing < 0:
                    raise gate.OwnerUnresolved(f"malformed shader include in {path}:{line_number}")
                target = stripped[opening + 1:closing].replace("\\\n", "")
                if not target or "\n" in target:
                    raise gate.OwnerUnresolved(f"malformed shader include in {path}:{line_number}")
                found.append((line_number, target))
        if line_end >= len(stripped):
            break
        line_start = line_end + 1
        line_number += 1
    return found


def _resolve_gdshader_include(target: str, path: str) -> str:
    """Godot resolution: res:// is the project root; a relative path is relative to the includer."""

    if target.startswith("res://"):
        resolved = posixpath.normpath(f"{gate.GODOT_RES_ROOT}/{target[len('res://'):]}")
    elif "://" in target:
        raise gate.OwnerUnresolved(f"shader include is not a res:// path in {path}: {target}")
    else:
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
    if not resolved.startswith(gate.GODOT_RES_ROOT + "/"):
        raise gate.OwnerUnresolved(f"shader include leaves res:// in {path}: {target}")
    return resolved


def loader_hidden_includes(text: str, path: str) -> list[str]:
    """GLSL lines that are #include directives but that RenderShaderSource.gd will not expand.

    RenderShaderSource.load_source expands only lines whose strip_edges()
    begins with '#include' (the FL-4781 gate mirrors it).  A directive hidden
    behind a comment or written '#  include' reaches glslang unexpanded.
    """

    lines = text.split("\n")
    hidden: list[str] = []
    for line_number, _ in include_directives(text, path):
        raw = lines[line_number - 1] if line_number - 1 < len(lines) else ""
        if not raw.strip().startswith("#include"):
            hidden.append(
                f"{path}:{line_number}: the loader will not expand this include "
                f"(RenderShaderSource.gd expands only lines that begin with #include): {raw.strip()}"
            )
    return hidden


def shader_closure(
    fingerprinter: gate.FeatureFingerprinter,
    reader: gate._RevisionReader,
    path: str,
    chain: tuple[str, ...] = (),
) -> list[str]:
    """A shader and every file its includes reach.  GLSL: the FL-4781 gate's closure."""

    if Path(path).suffix.lower() in gate.GLSL_SUFFIXES:
        return [item for item, _ in fingerprinter.source_files(reader, path, chain)]
    oid = reader.oid(path)
    if oid is None:
        if chain:
            raise gate.OwnerUnresolved(f"shader include is missing: {path} (from {chain[-1]})")
        raise gate.OwnerUnresolved(f"source file is missing: {path}")
    files = [path]
    for _, raw_target in include_directives(reader.read(oid), path):
        target = _resolve_gdshader_include(raw_target, path)
        if target in chain or target == path:
            raise gate.OwnerUnresolved(f"shader include is cyclic: {target}")
        files.extend(shader_closure(fingerprinter, reader, target, chain + (path,)))
    return files


def shader_expanded_text(
    fingerprinter: gate.FeatureFingerprinter, reader: gate._RevisionReader, path: str
) -> str:
    """Text whose digest changes when the shader or anything it includes changes.

    GLSL: the loader's expansion (FL-4781 gate).  Godot shader language: every
    closure file, in include order, each headed by its path.
    """

    closure = shader_closure(fingerprinter, reader, path)
    if Path(path).suffix.lower() in gate.GLSL_SUFFIXES:
        return fingerprinter._expanded_text(reader, path)
    parts = []
    for item in closure:
        oid = reader.oid(item)
        assert oid is not None
        parts.append(f"// @@ {item}\n{reader.read(oid)}")
    return "\n".join(parts)


def closure_violations(
    fingerprinter: gate.FeatureFingerprinter,
    reader: gate._RevisionReader,
    boundary: LaneBoundary,
    paths: Sequence[str],
) -> list[str]:
    """Protected shaders whose include closure reaches an FF-private file, and
    GLSL include directives in those closures that the loader will not expand."""

    errors: list[str] = []
    hidden_seen: set[str] = set()
    for path in sorted(paths):
        if not (_is_res_shader(path) and boundary.is_non_ff(path)):
            continue
        if reader.oid(path) is None:
            continue
        try:
            closure = shader_closure(fingerprinter, reader, path)
        except gate.OwnerUnresolved as exc:
            errors.append(f"{path}: include closure is unresolved: {exc}")
            continue
        reached = [item for item in closure if boundary.is_ff_private(item)]
        if reached:
            errors.append(f"{path} includes FF-private {', '.join(reached)}")
        for item in dict.fromkeys(closure):
            if Path(item).suffix.lower() in gate.GLSL_SUFFIXES and item not in hidden_seen:
                hidden_seen.add(item)
                oid = reader.oid(item)
                if oid is not None:
                    errors.extend(loader_hidden_includes(reader.read(oid), item))
    return errors


def _tree_paths(base_dir: Path, revision: str) -> list[str]:
    if revision == gate.INDEX_REVISION:
        text = _git(base_dir, "ls-files", "-z")
    else:
        text = _git(base_dir, "ls-tree", "-r", "-z", "--name-only", revision)
    return [item for item in text.split("\0") if item]


# --- host files: protected definitions and settings ---------------------------------------


def definition_fingerprints(text: str | None, names: Sequence[str]) -> dict[str, str | None]:
    """Normalized source of each named top-level GDScript definition (None when absent).

    Uses the FL-4781 gate's definition index (read-only).  A definition's
    fingerprint also covers every top-level const it reads, transitively
    through consts, so 'const CELL := 8' is protected when a protected function
    reads CELL.  Called functions are not followed: each is named on its own.
    """

    if text is None:
        return {name: None for name in names}
    unit = gate._SourceUnit(text, "gd")
    index, _ = unit.definitions()
    consts = {
        name
        for name, spans in index.items()
        if re.match(r"(?:@\w+(?:\([^)]*\))?\s+)*(?:static\s+)?const\b", text[spans[0][0]:spans[0][1]])
    }
    result: dict[str, str | None] = {}
    for name in names:
        spans = index.get(name)
        if not spans:
            result[name] = None
            continue
        parts = [unit.normalized(start, end) for start, end in spans]
        seen = {name}
        queue = list(spans)
        while queue:
            start, end = queue.pop(0)
            for ident in sorted(set(gate.IDENT_RE.findall(unit.mask, start, end))):
                if ident in seen or ident not in consts:
                    continue
                seen.add(ident)
                for span in index[ident]:
                    parts.append(unit.normalized(*span))
                    queue.append(span)
        result[name] = "\n".join(parts)
    return result


_SETTING_SECTION_RE = re.compile(r"^\[([^\]]+)\]\s*$")


def project_settings(text: str | None) -> dict[str, str]:
    """project.godot as {'section/key': normalized value}.  ';' lines are comments.

    A value runs across lines until its brackets and quotes balance (input maps,
    shader globals).  Whitespace outside strings is not significant.
    """

    settings: dict[str, str] = {}
    if text is None:
        return settings
    section = ""
    lines = text.split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        stripped = line.strip()
        if not stripped or stripped.startswith(";"):
            continue
        header = _SETTING_SECTION_RE.match(stripped)
        if header:
            section = header.group(1).strip()
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        chunks = [value]
        while not _balanced("\n".join(chunks)) and index < len(lines):
            chunks.append(lines[index])
            index += 1
        full_key = f"{section}/{key.strip()}" if section else key.strip()
        settings[full_key] = gate.normalize_source("\n".join(chunks), "c")
    return settings


def _balanced(value: str) -> bool:
    depth = 0
    in_string = False
    escaped = False
    for char in value:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
    return depth <= 0 and not in_string


def host_entries(boundary: LaneBoundary, path: str, text: str | None) -> dict[str, str | None]:
    """{entry name: fingerprint} for every protected part of one host file."""

    entries: dict[str, str | None] = {}
    names = sorted(boundary.protected_definitions.get(path, {}))
    if names:
        entries.update(definition_fingerprints(text, names))
    selectors = boundary.protected_settings.get(path, {})
    if selectors:
        for key, value in project_settings(text).items():
            if any(fnmatch.fnmatchcase(key, selector) for selector in selectors):
                entries[key] = value
        for selector in selectors:  # an exact key that is absent is recorded as absent
            if not any(char in selector for char in "*?[") and selector not in entries:
                entries[selector] = None
    return entries


def host_entry_changes(
    before: LaneBoundary | None, after: LaneBoundary, path: str, old: str | None, new: str | None
) -> tuple[list[str], list[str]]:
    """(entries whose fingerprint changed, entries whose protection the config removed).

    Protected at either side counts.  With no 'before' boundary every protected
    entry counts as changed (fail closed).
    """

    new_entries = host_entries(after, path, new)
    if before is None:
        return sorted(new_entries), []
    old_protected = host_entries(before, path, old)
    names = set(old_protected) | set(new_entries)
    old_all = {**host_entries(after, path, old), **old_protected}
    new_all = {**host_entries(before, path, new), **new_entries}
    changed = sorted(name for name in names if old_all.get(name) != new_all.get(name))
    removed = sorted(set(old_protected) - set(new_entries) - set(changed))
    return changed, removed


_RES_RE = re.compile(r"res://[^\s\"']+")


def host_ff_private_references(boundary: LaneBoundary, path: str, text: str | None) -> list[str]:
    """res:// string literals in a host file that name an FF-private file (comments ignored)."""

    if text is None:
        return []
    language = "gd" if path.endswith(".gd") else "ini"
    found: list[str] = []
    for kind, start, end in gate._source_segments(text, language):
        if kind != "string":
            continue
        for match in _RES_RE.finditer(text, start, end):
            target = f"{gate.GODOT_RES_ROOT}/{match.group(0)[len('res://'):]}"
            if boundary.is_ff_private(target):
                line = text.count("\n", 0, match.start()) + 1
                found.append(f"{path}:{line} loads FF-private {target}")
    return found


def missing_definitions(boundary: LaneBoundary, read) -> list[str]:
    """Named definitions and exact setting keys that no longer exist (renames drop protection)."""

    missing: list[str] = []
    for path in sorted(boundary.host_paths()):
        text = read(path)
        for name, value in host_entries(boundary, path, text).items():
            if value is None:
                missing.append(f"{path}::{name}")
    return missing


def trailer_reason(message_text: str) -> str | None:
    for line in message_text.splitlines():
        if line.startswith(TRAILER):
            reason = line[len(TRAILER):].strip()
            if reason and reason.lower() != "none":
                return reason
    return None


def _staged_boundaries(
    base_dir: Path, head: str | None
) -> tuple[LaneBoundary | None, LaneBoundary | None, list[str]]:
    """(HEAD boundary or None when unknown, index boundary, config errors)."""

    errors: list[str] = []
    head_boundary: LaneBoundary | None = None
    if head is not None:
        text = _config_text(base_dir, head)
        if text is not None:
            try:
                head_boundary = load_boundary(text)
            except LaneConfigError as exc:
                errors.append(f"{BLOCKED} {CONFIG_RELATIVE} is unreadable at HEAD: {exc}")
    index_boundary: LaneBoundary | None = None
    text = _config_text(base_dir, gate.INDEX_REVISION)
    if text is None:
        errors.append(
            f"{BLOCKED} {CONFIG_RELATIVE} is unreadable in the index: it is not staged. "
            "The lane boundary cannot be removed by a commit."
        )
    else:
        try:
            index_boundary = load_boundary(text)
        except LaneConfigError as exc:
            errors.append(f"{BLOCKED} {CONFIG_RELATIVE} is unreadable in the index: {exc}")
    if index_boundary is not None and not index_boundary.is_non_ff(CONFIG_RELATIVE):
        errors.append(f"{BLOCKED} {CONFIG_RELATIVE} must protect itself; keep it in 'protected'.")
    return head_boundary, index_boundary, errors


def _retired_revivals(boundary: LaneBoundary, paths: Sequence[str]) -> list[str]:
    return sorted(
        {path for path in paths for pattern in boundary.ff_private_retired if fnmatch.fnmatchcase(path, pattern)}
    )


def staged_errors(message_text: str, *, base_dir: Path = REPO_ROOT) -> list[str]:
    """commit-msg check.  Policy (merge, no HEAD, no config): see the module docstring."""

    base_dir = Path(base_dir)
    try:
        head: str | None = gate._resolve_commit(base_dir, "HEAD")
    except ValueError:
        head = None  # unborn branch: nothing to compare with, so everything is new
    staged = sorted(
        set(_git(base_dir, "diff", "--cached", "--no-renames", "--name-only", "-z").split("\0")) - {""}
    )
    if not staged:
        return []
    head_boundary, index_boundary, errors = _staged_boundaries(base_dir, head)
    if index_boundary is None or errors:
        return errors
    config_staged = CONFIG_RELATIVE in staged
    index_paths = _tree_paths(base_dir, gate.INDEX_REVISION) if config_staged else []
    if config_staged:
        live = index_paths
    else:  # staged paths still present in the index (a deletion revives nothing)
        live = [
            path
            for path in _git(
                base_dir, "diff", "--cached", "--no-renames", "--name-only", "--diff-filter=d", "-z"
            ).split("\0")
            if path
        ]
    revived = _retired_revivals(index_boundary, live)
    if revived:
        errors.append(
            f"{BLOCKED} ff_private_retired names FF paths that exist only in history, "
            "but it matches tracked file(s):\n  "
            + "\n  ".join(revived)
            + "\nRemove the retired glob, or move the path back to ff_private."
        )
    # Unknown HEAD boundary (no HEAD, or no config there): classify by the index.
    was_non_ff = head_boundary.is_non_ff if head_boundary is not None else index_boundary.is_non_ff
    candidates = [path for path in staged if was_non_ff(path) or index_boundary.is_non_ff(path)]
    # A config change that removes protection from a tracked file is a change to it.
    unprotected: list[str] = []
    if config_staged and head_boundary is not None:
        head_paths = set(_tree_paths(base_dir, head)) if head is not None else set()
        unprotected = sorted(
            path
            for path in head_paths.union(index_paths)
            if head_boundary.is_non_ff(path) and not index_boundary.is_non_ff(path)
        )
    touches_shader = any(_is_res_shader(path) for path in staged) or config_staged
    changed: list[str] = []
    host_paths = index_boundary.host_paths() | (head_boundary.host_paths() if head_boundary else set())
    with gate.FeatureFingerprinter(base_dir) as fingerprinter:
        index_reader = fingerprinter.reader(gate.INDEX_REVISION)
        head_reader = fingerprinter.reader(head) if head is not None else None

        def text_at(reader: gate._RevisionReader | None, path: str) -> str | None:
            oid = reader.oid(path) if reader is not None else None
            return None if oid is None else reader.read(oid)

        host_changed: list[str] = []
        host_hard: list[str] = []
        for path in sorted(host_paths):
            if path not in staged and not config_staged:
                continue
            old_text, new_text = text_at(head_reader, path), text_at(index_reader, path)
            entry_changed, entry_removed = host_entry_changes(
                head_boundary if head is not None else None, index_boundary, path, old_text, new_text
            )
            host_changed += [f"{path}::{name}" for name in entry_changed]
            unprotected += [f"{path}::{name}" for name in entry_removed]
            host_hard += host_ff_private_references(index_boundary, path, new_text)
        missing: list[str] = []
        if config_staged or host_paths & set(staged):
            missing = [
                row
                for row in missing_definitions(index_boundary, lambda path: text_at(index_reader, path))
                if config_staged or row.split("::", 1)[0] in staged
            ]
        if missing:
            errors.append(
                f"{BLOCKED} a protected host definition or setting no longer exists (renamed or deleted):\n  "
                + "\n  ".join(missing)
                + f"\nA rename must carry its protection: update {CONFIG_RELATIVE} in the same commit."
            )
        if host_hard:
            errors.append(
                f"{BLOCKED} a host file loads an FF-private file. True FF is a separate "
                "renderer; the host must not reach its private code.\n  " + "\n  ".join(host_hard)
            )
        if head is None or head_boundary is None:
            changed = list(candidates)  # fail closed: every staged protected file changed
        else:
            head_reader = fingerprinter.reader(head)
            changed = [
                path
                for path in candidates
                if _normalized(head_reader, path) != _normalized(index_reader, path)
            ]
        changed += host_changed
        if touches_shader:
            violations = closure_violations(
                fingerprinter,
                index_reader,
                index_boundary,
                [path for path in _tree_paths(base_dir, gate.INDEX_REVISION) if _is_res_shader(path)],
            )
            if violations:
                errors.append(
                    f"{BLOCKED} a non-FF shader includes an FF-private file. "
                    "True FF is a separate renderer; move the shared code into a non-FF file "
                    "or give FF its own copy.\n  " + "\n  ".join(violations)
                )
    listed = changed + [
        f"{path} (protection removed by {CONFIG_RELATIVE})" for path in unprotected if path not in changed
    ]
    if listed and trailer_reason(message_text) is None:
        basis = "" if head_boundary is not None else (
            " HEAD has no lane config (or no HEAD), so every staged protected file counts as changed."
        )
        errors.append(
            f"{BLOCKED} this commit changes the source of "
            f"{len(listed)} protected non-FF render file(s) or host entries:\n  "
            + "\n  ".join(listed)
            + f"\n{basis.strip()}\n" * bool(basis)
            + "\nTrue FF work must not change non-FF. If this change is meant for non-FF, "
            "say why in a trailer:\n"
            f"  {TRAILER} <reason>\n"
            f"The protected set is {CONFIG_RELATIVE}."
        )
    return errors


def integrity_report(
    base_dir: Path, revision: str = "HEAD", config_text: str | None = None
) -> dict[str, Any]:
    """--check: config integrity against the tree at REVISION ('HEAD' or ':' for the index).

    CONFIG_TEXT defaults to the config at REVISION.  Fails on include
    violations, retired globs that match tracked files, a config that does not
    protect itself, protected host definitions or exact setting keys that no
    longer exist, and host files that load FF-private files.
    """

    base_dir = Path(base_dir)
    spec = revision if revision == gate.INDEX_REVISION else gate._resolve_commit(base_dir, revision)
    if config_text is None:
        config_text = _config_text(base_dir, spec)
        if config_text is None:
            raise LaneConfigError(f"{CONFIG_RELATIVE} is absent at {revision}")
    boundary = load_boundary(config_text)
    paths = _tree_paths(base_dir, spec)
    with gate.FeatureFingerprinter(base_dir) as fingerprinter:
        reader = fingerprinter.reader(spec)

        def read(path: str) -> str | None:
            oid = reader.oid(path)
            return None if oid is None else reader.read(oid)

        violations = closure_violations(
            fingerprinter, reader, boundary, [path for path in paths if _is_res_shader(path)]
        )
        missing = missing_definitions(boundary, read)
        references = [
            row for path in sorted(boundary.host_paths()) for row in host_ff_private_references(boundary, path, read(path))
        ]
    revived = _retired_revivals(boundary, paths)
    unprotected_config = [] if boundary.is_non_ff(CONFIG_RELATIVE) else [CONFIG_RELATIVE]
    report = {
        "closure_violations": violations,
        "retired_globs_matching_tracked_files": revived,
        "config_not_self_protected": unprotected_config,
        "missing_definitions": missing,
        "host_ff_private_references": references,
    }
    return {"ok": not any(report.values()), **report}


def lane_diff(base: str, head: str, *, base_dir: Path = REPO_ROOT) -> dict[str, Any]:
    """Every protected non-FF file whose normalized source changed in BASE..HEAD."""

    base_dir = Path(base_dir)
    base_commit = gate._resolve_commit(base_dir, base)
    head_commit = gate._resolve_commit(base_dir, head)
    boundary = _boundary_at(base_dir, head_commit)
    touched = sorted(
        set(
            _git(base_dir, "diff", "--no-renames", "--name-only", "-z", base_commit, head_commit).split("\0")
        )
        - {""}
    )
    files: list[dict[str, Any]] = []
    roots: list[dict[str, Any]] = []
    with gate.FeatureFingerprinter(base_dir) as fingerprinter:
        before = fingerprinter.reader(base_commit)
        after = fingerprinter.reader(head_commit)
        for path in touched:
            if not boundary.is_non_ff(path):
                continue
            old, new = _normalized(before, path), _normalized(after, path)
            if old == new:
                continue
            kind = "added" if old is None else "deleted" if new is None else "modified"
            commits = _git(
                base_dir, "log", "--format=%h %s", f"{base_commit}..{head_commit}", "--", path
            ).splitlines()
            files.append({"path": path, "change": kind, "commits": commits})
        # Shader roots: every protected res:// shader present at either side.
        shader_paths = sorted(
            {
                path
                for revision in (base_commit, head_commit)
                for path in _tree_paths(base_dir, revision)
                if _is_res_shader(path) and boundary.is_non_ff(path)
            }
        )
        for path in shader_paths:
            digests = []
            for reader in (before, after):
                try:
                    if reader.oid(path) is None:
                        digests.append(None)
                        continue
                    text = shader_expanded_text(fingerprinter, reader, path)
                    digests.append(
                        hashlib.sha256(
                            gate.normalize_source(text, "c").encode("utf-8")
                        ).hexdigest()
                    )
                except gate.OwnerUnresolved as exc:
                    digests.append(f"UNRESOLVED:{exc}")
            if digests[0] != digests[1]:
                roots.append({"shader": path, "expanded_before": digests[0], "expanded_after": digests[1]})
        violations = closure_violations(fingerprinter, after, boundary, shader_paths)
        host_rows = _host_diff_rows(base_dir, fingerprinter, boundary, base_commit, head_commit, touched)
    return {
        "schema": "render-lane-diff/1",
        "fl": FL_ID,
        "base": base_commit,
        "head": head_commit,
        "config": CONFIG_RELATIVE,
        "normalization": "FL-4781 normalize_source: comments and whitespace ignored",
        "changed_files": files,
        "changed_expanded_shaders": roots,
        "changed_host_entries": host_rows,
        "closure_violations_at_head": violations,
    }


def _host_diff_rows(
    base_dir: Path,
    fingerprinter: gate.FeatureFingerprinter,
    boundary: LaneBoundary,
    base_commit: str,
    head_commit: str,
    touched: Sequence[str],
) -> list[dict[str, Any]]:
    """Protected host entries that changed in BASE..HEAD, each with the commits that changed it."""

    def read(revision: str, path: str) -> str | None:
        reader = fingerprinter.reader(revision)
        oid = reader.oid(path)
        return None if oid is None else reader.read(oid)

    rows: list[dict[str, Any]] = []
    for path in sorted(boundary.host_paths() & set(touched)):
        before = host_entries(boundary, path, read(base_commit, path))
        after = host_entries(boundary, path, read(head_commit, path))
        changed = sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
        if not changed:
            continue
        attribution: dict[str, list[str]] = {name: [] for name in changed}
        log = _git(base_dir, "log", "--reverse", "--format=%H %h %s", f"{base_commit}..{head_commit}", "--", path)
        for line in log.splitlines():
            full, _, label = line.partition(" ")
            old = host_entries(boundary, path, read(f"{full}^", path))
            new = host_entries(boundary, path, read(full, path))
            for name in changed:
                if old.get(name) != new.get(name):
                    attribution[name].append(label)
        for name in changed:
            kind = "added" if before.get(name) is None else "deleted" if after.get(name) is None else "modified"
            kind_of = "definition" if name in boundary.protected_definitions.get(path, {}) else "setting"
            rows.append({"path": path, "name": name, "kind": kind_of, "change": kind, "commits": attribution[name]})
    return rows


def render_markdown(receipt: Mapping[str, Any], preserved: str) -> str:
    lines = [
        f"# Non-FF lane diff {receipt['base'][:9]}..{receipt['head'][:9]}",
        "",
        f"Receipt: `{preserved}`. Config: `{receipt['config']}`. {receipt['normalization']}.",
        "",
        f"## Changed non-FF files ({len(receipt['changed_files'])})",
        "",
    ]
    for row in receipt["changed_files"]:
        lines.append(f"- `{row['path']}` ({row['change']})")
        lines.extend(f"  - {commit}" for commit in row["commits"])
    lines += ["", f"## Non-FF shaders whose expanded source changed ({len(receipt['changed_expanded_shaders'])})", ""]
    lines.extend(f"- `{row['shader']}`" for row in receipt["changed_expanded_shaders"])
    lines += ["", f"## Protected host entries that changed ({len(receipt.get('changed_host_entries', []))})", ""]
    for row in receipt.get("changed_host_entries", []):
        lines.append(f"- `{row['path']}::{row['name']}` ({row['kind']}, {row['change']})")
        lines.extend(f"  - {commit}" for commit in row["commits"])
    lines += ["", f"## Include violations at head ({len(receipt['closure_violations_at_head'])})", ""]
    lines.extend(f"- {row}" for row in receipt["closure_violations_at_head"])
    return "\n".join(lines) + "\n"


def write_receipt(
    receipt: Mapping[str, Any],
    *,
    base_dir: Path,
    working_root: Path | None = None,
    destination_root: Path | None = None,
) -> dict[str, str]:
    from run_receipt_preservation import preserve_run_receipt

    label = f"nonff-lane-diff_{receipt['base'][:9]}-{receipt['head'][:9]}"
    run_dir = (working_root or base_dir / ".run" / "render_lane_boundary") / label
    run_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = run_dir / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    digest = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    root = destination_root or base_dir / "artifacts" / "run_receipts"
    preserved = root / FL_ID.lower() / f"{label}__{digest[:16]}" / "receipt.json"
    try:
        display = preserved.resolve().relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        display = str(preserved)
    (run_dir / "summary.md").write_text(render_markdown(receipt, display), encoding="utf-8")
    manifest = preserve_run_receipt(
        repo_root=base_dir,
        receipt_path=receipt_path,
        fl_id=FL_ID,
        runner="scripts/render_lane_boundary.py --diff",
        destination_root=destination_root,
        companion_names=("summary.md",),
    )
    return {"working_receipt": str(receipt_path), "preserved_manifest": str(manifest)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", metavar="MESSAGE_FILE")
    mode.add_argument("--diff", nargs=2, metavar=("BASE", "HEAD"))
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--receipt-working-root", help=argparse.SUPPRESS)
    parser.add_argument("--receipt-destination-root", help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    base_dir = Path.cwd().resolve()
    try:
        if args.staged:
            errors = staged_errors(Path(args.staged).read_text(encoding="utf-8"), base_dir=base_dir)
            for error in errors:
                print(error, file=sys.stderr)
            return 1 if errors else 0
        if args.check:
            config_path = base_dir / CONFIG_RELATIVE
            report = integrity_report(
                base_dir, "HEAD", config_text=config_path.read_text(encoding="utf-8")
            )
            print(json.dumps(report, indent=2))
            return 0 if report["ok"] else 1
        receipt = lane_diff(args.diff[0], args.diff[1], base_dir=base_dir)
        written = write_receipt(
            receipt,
            base_dir=base_dir,
            working_root=Path(args.receipt_working_root) if args.receipt_working_root else None,
            destination_root=Path(args.receipt_destination_root) if args.receipt_destination_root else None,
        )
    except (OSError, ValueError, gate.OwnerUnresolved, RuntimeError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "ok": True,
                **written,
                "changed_files": len(receipt["changed_files"]),
                "changed_expanded_shaders": len(receipt["changed_expanded_shaders"]),
                "changed_host_entries": len(receipt["changed_host_entries"]),
                "closure_violations_at_head": receipt["closure_violations_at_head"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
