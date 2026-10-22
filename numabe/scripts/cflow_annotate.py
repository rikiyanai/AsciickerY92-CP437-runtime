#!/usr/bin/env python3
"""
cflow_annotate.py — Annotated ASCII call graph for C++ architecture.

Overlays FL family annotations, phase labels, and ownership boundaries on
cflow-generated call trees. Server defaults stay available, but ad hoc engine
queries can also route through this front door.

Primary interface: analyze_runs.py callgraph (delegates to this script)
Direct use: python3 scripts/cflow_annotate.py [OPTIONS]

Data flow:
  analyze_runs.py fl open → FL IDs
  analyze_runs.py fl code FL-NNN → AnchorFunctions / CodeRefs / remediation
  direct anchor trace (preferred) or fail-closed FL routing
  cflow --main=<entry> <files> → raw call tree
  parse + annotate → rendered output

NEVER parses docs/FAILURE_LOG.md directly. All FL data via analyze_runs.py fl.
"""

import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

# Optional: tree-sitter + tree-sitter-cpp for CodeRef → function resolution (OPTION A).
# If absent, CodeRefs-only FLs fall back to the existing ANCHOR GAP remediation (OPTION B).
try:
    import tree_sitter_cpp as _ts_cpp  # type: ignore[import-not-found]
    from tree_sitter import Language as _ts_Language, Parser as _TSParser  # type: ignore[import-not-found]
    _TREE_SITTER_AVAILABLE = True
except ImportError:
    _ts_cpp = None  # type: ignore[assignment]
    _ts_Language = None  # type: ignore[assignment]
    _TSParser = None  # type: ignore[assignment]
    _TREE_SITTER_AVAILABLE = False

_TS_CPP_LANGUAGE = None  # cached Language object; populated on first successful use
_TS_CPP_INIT_FAILED = False  # set True on first Language() failure; short-circuits all later calls


# ---------------------------------------------------------------------------
# Stdlib / libc / POSIX function blocklist
# These must never appear as FL annotations — they are noise, not project functions.
# Any function in this set is excluded from the FL index.
# ---------------------------------------------------------------------------

_STDLIB_BLOCKLIST: frozenset[str] = frozenset({
    # stdio
    "printf", "fprintf", "sprintf", "snprintf", "vprintf", "vfprintf", "vsprintf",
    "scanf", "fscanf", "sscanf", "fopen", "fclose", "fread", "fwrite", "fflush",
    "fseek", "ftell", "rewind", "fgets", "fputs", "fputc", "fgetc", "perror",
    "puts", "rename", "remove",
    # string
    "strlen", "strcpy", "strncpy", "strcat", "strncat", "strcmp", "strncmp",
    "strchr", "strrchr", "strstr", "strtok", "strdup", "memcpy", "memmove",
    "memset", "memcmp", "memchr",
    # stdlib
    "malloc", "calloc", "realloc", "free", "exit", "abort", "atoi", "atof",
    "atol", "strtol", "strtod", "rand", "srand", "qsort", "bsearch",
    # math
    "sqrt", "sin", "cos", "tan", "atan", "atan2", "pow", "fabs", "floor",
    "ceil", "fmod", "floorf", "ceilf", "fabsf", "sqrtf", "sinf", "cosf",
    # POSIX / socket
    "accept", "bind", "connect", "listen", "socket", "send", "recv",
    "sendto", "recvfrom", "setsockopt", "getsockopt", "poll", "select",
    "fcntl", "ioctl", "close", "read", "write", "open", "stat",
    # threading
    "pthread_create", "pthread_join", "pthread_mutex_lock", "pthread_mutex_unlock",
    "usleep", "nanosleep",
    # misc
    "signal", "clock_gettime", "gettimeofday", "getenv", "setenv",
    "realpath", "strrchr", "memchr",
    # Windows
    "GetFullPathNameA", "GetTickCount",
    # crypto/hash (low-level, not project functions)
    "SHA1",
})


# ---------------------------------------------------------------------------
# Phase label table (static config)
# ---------------------------------------------------------------------------

# Exact-match entries take priority over prefix-match entries.
_PHASE_EXACT: dict[str, str] = {
    "AcceptThreadEntry": "ACCEPT",
    "IOThreadEntry": "IO",
    "ServerTickLoop": "TICK_LOOP",
    "ServerTick": "TICK",
    "SvrResolveCombat": "COMBAT",
    "SvrUpdateNpcAI": "AI",
    "SvrPublishOutbound": "SNAPSHOT",
    "SvrPublishAuthoritativeState": "SNAPSHOT",
    "SvrBroadcastSnapshot": "SNAPSHOT",
    "SvrFlushEvents": "SNAPSHOT",
    "SvrProcessRespawns": "RULES",
    "SvrProcessDisconnects": "RULES",
    "SvrProcessItemProximity": "RULES",
    "SvrSampleTerrainHeight": "SPAWN",
    "SvrResolveSafePlayerSpawn": "SPAWN",
    "SvrStateInit": "INIT",
    "SvrInitNpcs": "INIT",
    # IO thread internals (missing from cflow, provided via static supplement)
    "io_send": "IO_SEND",
    "IODisconnectClient": "IO_DISC",
    "IOLogDisconnectReason": "IO_LOG",
    "WS_FRAME_ENCODE": "IO_ENCODE",
}

_PHASE_PREFIX: list[tuple[str, str]] = [
    ("SvrEnsureDebug", "RULES"),
    ("MpStep", "PHYSICS"),
    ("MpMove", "PHYSICS"),
]


def _phase_label(func_name: str) -> str:
    if func_name in _PHASE_EXACT:
        return _PHASE_EXACT[func_name]
    for prefix, label in _PHASE_PREFIX:
        if func_name.startswith(prefix):
            return label
    return ""


# ---------------------------------------------------------------------------
# Default source files and entry points
# ---------------------------------------------------------------------------

DEFAULT_ENTRIES = ["AcceptThreadEntry", "IOThreadEntry", "ServerTickLoop"]
DEFAULT_FILES = ["server/server_tick.cpp", "server/game_svr.cpp", "server/mp_step.cpp"]
ENGINE_FILES = ["engine/game.cpp", "engine/render.cpp"]
WEB_FILES = ["web/game_web.cpp"]


def _repo_root() -> Path:
    """Find repo root by walking up from this script's location."""
    p = Path(__file__).resolve().parent
    while p != p.parent:
        if (p / ".git").exists() or (p / ".worktrees").exists():
            return p
        # Check if we're inside a worktree
        if (p.parent.parent / ".git").exists():
            return p
        p = p.parent
    return Path.cwd()


def _candidate_files_for_anchor_function(func_name: str, repo_root: Path | None = None) -> list[str]:
    """Pick the narrowest likely cflow file set for a resolved anchor function.

    FL-filtered callgraph queries are more useful when they start from the
    resolved anchor itself instead of building the full server-root tree and
    filtering afterward. This helper keeps that direct-anchor path bounded.
    """
    if func_name.startswith("Web"):
        return WEB_FILES
    if (
        func_name in DEFAULT_ENTRIES
        or func_name.startswith(("Accept", "IO", "Svr", "Mp"))
        or func_name.startswith("Server")
    ):
        return DEFAULT_FILES
    if repo_root is not None:
        candidates = (
            WEB_FILES,
            DEFAULT_FILES,
            ENGINE_FILES,
        )
        needle = re.compile(rf"\b{re.escape(func_name)}\s*\(")
        for file_group in candidates:
            for rel_path in file_group:
                path = repo_root / rel_path
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if needle.search(text):
                    return file_group
    return ENGINE_FILES


def _candidate_files_for_repo_path(path: str) -> list[str] | None:
    if path.startswith("engine/"):
        return ENGINE_FILES
    if path.startswith("web/"):
        return WEB_FILES
    if path.startswith("server/"):
        return DEFAULT_FILES
    return None


def _single_fl_candidate_files(fl_id: str, repo_root: Path) -> list[str] | None:
    """Return a single authoritative file family for an FL, or None when mixed."""
    try:
        result = subprocess.run(
            [sys.executable, "scripts/analyze_runs.py", "fl", "show", fl_id, "--json"],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=20,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return None

    overlay = data.get("overlay") or {}
    group_keys: set[tuple[str, ...]] = set()
    for ref in list(overlay.get("CodeRefs") or []):
        rel_path = str(ref).rsplit(":", 1)[0]
        files = _candidate_files_for_repo_path(rel_path)
        if files:
            group_keys.add(tuple(files))
    for rel_path in list(overlay.get("TouchedFiles") or []):
        files = _candidate_files_for_repo_path(str(rel_path))
        if files:
            group_keys.add(tuple(files))

    if len(group_keys) != 1:
        return None
    return list(next(iter(group_keys)))


def _preferred_direct_anchor_functions(func_names: list[str], repo_root: Path | None = None) -> list[str]:
    """Prefer the narrowest direct-anchor set when multiple anchor families exist."""
    server_funcs = [
        fn for fn in func_names
        if _candidate_files_for_anchor_function(fn, repo_root) == DEFAULT_FILES
    ]
    if server_funcs:
        return server_funcs
    web_funcs = [
        fn for fn in func_names
        if _candidate_files_for_anchor_function(fn, repo_root) == WEB_FILES
    ]
    if web_funcs:
        return web_funcs
    return func_names


# ---------------------------------------------------------------------------
# FL index construction
# ---------------------------------------------------------------------------

def _get_open_fl_ids(repo_root: Path) -> list[str]:
    """Return list of open FL IDs via analyze_runs.py fl open."""
    try:
        result = subprocess.run(
            [sys.executable, "scripts/analyze_runs.py", "fl", "open"],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return []
    fl_ids = []
    for line in result.stdout.splitlines():
        # Lines like: "FL-642    2026-04-07  -    Server spawn z wrong..."
        m = re.match(r"^\s*(FL-\d+)\s", line)
        if m:
            fl_ids.append(m.group(1))
    return fl_ids


# ---------------------------------------------------------------------------
# CodeRef → enclosing-function resolver (OPTION A, tree-sitter backend)
# ---------------------------------------------------------------------------

def _resolve_coderef_to_function(filepath: str, lineno: int) -> str | None:
    """Resolve a file:line CodeRef to its enclosing C/C++ function name via tree-sitter.

    Returns the bare function identifier (e.g. ``SvrSampleTerrainHeight``), or ``None``
    when:
    - ``_TREE_SITTER_AVAILABLE`` is False — package absent; caller falls back to OPTION B.
    - *filepath* does not exist or cannot be read.
    - *lineno* is not enclosed by any ``function_definition`` node (file scope, macro, etc.).
    """
    global _TS_CPP_LANGUAGE, _TS_CPP_INIT_FAILED
    if not _TREE_SITTER_AVAILABLE or _TS_CPP_INIT_FAILED:
        return None
    path = Path(filepath)
    if not path.exists():
        return None
    try:
        source = path.read_bytes()
    except OSError:
        return None
    try:
        if _TS_CPP_LANGUAGE is None:
            _TS_CPP_LANGUAGE = _ts_Language(_ts_cpp.language())  # type: ignore[call-arg]
        parser = _TSParser(_TS_CPP_LANGUAGE)  # type: ignore[call-arg]
        tree = parser.parse(source)
    except Exception as exc:
        _TS_CPP_INIT_FAILED = True
        import sys as _sys
        _sys.stderr.write(
            f"cflow_annotate: tree-sitter Language init failed ({exc!r}); "
            "AST-backed CodeRef resolution disabled for this run. "
            "Run: pip install --upgrade tree-sitter tree-sitter-cpp\n"
        )
        return None
    # tree-sitter row indices are 0-based
    return _walk_for_enclosing_function(tree.root_node, lineno - 1)


def _walk_for_enclosing_function(node, target_row: int) -> str | None:
    """Walk an AST subtree and return the name of the innermost function_definition
    whose source range contains *target_row* (0-indexed), or ``None``."""
    if node.start_point[0] > target_row or node.end_point[0] < target_row:
        return None
    # Visit children first so the deepest (innermost) match is returned.
    for child in node.children:
        inner = _walk_for_enclosing_function(child, target_row)
        if inner is not None:
            return inner
    if node.type == "function_definition":
        return _extract_func_name_from_node(node)
    return None


def _extract_func_name_from_node(func_node) -> str | None:
    """Extract the bare identifier name from a tree-sitter function_definition node."""
    declarator = func_node.child_by_field_name("declarator")
    if declarator is None:
        return None
    return _name_from_declarator_node(declarator)


def _name_from_declarator_node(node) -> str | None:
    """Recursively unwrap a declarator chain to the innermost identifier name."""
    t = node.type
    if t == "identifier":
        return node.text.decode("utf-8", errors="replace")
    if t == "function_declarator":
        inner = node.child_by_field_name("declarator")
        if inner:
            return _name_from_declarator_node(inner)
    if t == "qualified_identifier":
        # e.g. ``Game::Render`` → take the ``name`` field (last identifier)
        name_node = node.child_by_field_name("name")
        if name_node:
            # strip template suffix: ``Foo<int>`` → ``Foo``
            return name_node.text.decode("utf-8", errors="replace").split("<")[0]
    if t in ("pointer_declarator", "reference_declarator"):
        inner = node.child_by_field_name("declarator")
        if inner:
            return _name_from_declarator_node(inner)
    if t == "destructor_name":
        return node.text.decode("utf-8", errors="replace")
    return None


def _get_fl_function_refs(fl_id: str, repo_root: Path) -> tuple[list[str], bool, list[str]]:
    """Return ``(function_names, anchor_gap, unresolvable_refs)`` via ``fl code --json``.

    *function_names* — anchors usable for callgraph routing.
    *anchor_gap* — True when routing is not possible (no anchors or AST lookup failed).
    *unresolvable_refs* — non-empty only when tree-sitter was available and AST lookup
    was attempted for every CodeRef but all failed; caller should emit
    ``CODEREF_UNRESOLVABLE:`` rather than the generic ``ANCHOR GAP:``.
    When tree-sitter is absent the third element is always ``[]`` and the caller
    falls back to the existing OPTION-B (ANCHOR GAP) remediation.
    """
    try:
        result = subprocess.run(
            [sys.executable, "scripts/analyze_runs.py", "fl", "code", fl_id, "--json"],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        return [], False, []
    if result.returncode != 0:
        # Subprocess error (import failure, unhandled exception) — log to stderr
        # so operators can distinguish a lookup failure from a genuine anchor-less FL.
        print(
            f"[cflow_annotate] WARNING: fl code {fl_id} exited {result.returncode}; "
            f"treating as no anchors. stderr: {result.stderr[:200]!r}",
            file=sys.stderr,
        )
        return [], False, []
    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return [], False, []
    anchor_gap = bool(data.get("anchor_gap", False))
    # Prefer fn_names (always present, rg-independent) over functions.keys() so that
    # callers get the full name list even on hosts without ripgrep installed (COR-001).
    fn_names = data.get("fn_names")
    if isinstance(fn_names, list) and fn_names:
        names = fn_names
    else:
        functions = data.get("functions") or {}
        names = list(functions.keys()) if isinstance(functions, dict) else []

    # --- OPTION A: CodeRefs-only FL → attempt AST-backed function lookup ---
    # Only runs when anchor_gap=True (no AnchorFunctions), tree-sitter is installed,
    # and fn_names came back empty. OPTION B behaviour is preserved when
    # tree-sitter is absent (_TREE_SITTER_AVAILABLE=False): third element stays [].
    if anchor_gap and not names and _TREE_SITTER_AVAILABLE:
        raw_refs = [r for r in (data.get("refs") or [])
                    if r.get("exists") and r.get("resolved_path")]
        found: list[str] = []
        failed: list[str] = []
        for r in raw_refs:
            ref_str = r["ref"]
            # ref format: "path/to/file.cpp:1234" — split on last colon for lineno
            parts = ref_str.rsplit(":", 1)
            if len(parts) == 2 and parts[1].isdigit():
                lineno = int(parts[1])
                if lineno < 1:
                    # Line 0 is not a valid source line; treat as unresolvable.
                    failed.append(ref_str)
                    continue
                abs_path = r["resolved_path"]
                name = _resolve_coderef_to_function(abs_path, lineno)
                if name and name not in _STDLIB_BLOCKLIST:
                    if name not in found:
                        found.append(name)
                else:
                    failed.append(ref_str)
            else:
                failed.append(ref_str)
        if found:
            # AST lookup succeeded — feed recovered names back as anchors
            return found, False, []
        if failed:
            # tree-sitter was available, lookup attempted, none succeeded
            return [], True, failed
        # No refs with existing files — fall through to OPTION B

    return names, anchor_gap, []


def _get_fl_family_ids(fl_id: str, repo_root: Path) -> list[str]:
    """Return clustered FL ids via analyze_runs.py fl family FL-NNN."""
    try:
        result = subprocess.run(
            [sys.executable, "scripts/analyze_runs.py", "fl", "family", fl_id],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return [fl_id]

    family_ids: list[str] = []
    for line in result.stdout.splitlines():
        match = re.match(r"^\s*(FL-\d+)\b", line)
        if match:
            fl = match.group(1)
            if fl not in family_ids:
                family_ids.append(fl)
    return family_ids or [fl_id]


def build_fl_function_index(repo_root: Path, open_only: bool = True,
                             fl_ids_filter: list[str] | None = None) -> dict[str, list[str]]:
    """
    Returns dict: function_name -> list[FL-NNN]
    Uses analyze_runs.py fl code for each open FL.
    Never parses docs/FAILURE_LOG.md directly.
    """
    if fl_ids_filter:
        fl_ids = fl_ids_filter
    elif open_only:
        fl_ids = _get_open_fl_ids(repo_root)
    else:
        fl_ids = _get_open_fl_ids(repo_root)  # extend later if closed FLs needed

    result: dict[str, list[str]] = {}
    for fl_id in fl_ids:
        funcs, _, _ = _get_fl_function_refs(fl_id, repo_root)
        for func in funcs:
            if func in _STDLIB_BLOCKLIST:
                continue  # never annotate stdlib/libc/POSIX functions
            result.setdefault(func, []).append(fl_id)
    return result


# ---------------------------------------------------------------------------
# cflow invocation and parsing
# ---------------------------------------------------------------------------

def _run_cflow(entry: str, files: list[str], depth: int, repo_root: Path) -> list[str]:
    """Invoke cflow for a single entry point. Returns stdout lines."""
    cppopts = _compile_db_cppopts(files, repo_root)
    commands = []
    if cppopts:
        commands.append(["cflow", "--cpp", f"--main={entry}", f"--depth={depth}", *cppopts, *files])
    commands.append(["cflow", f"--main={entry}", f"--depth={depth}", *files])

    last_stdout = ""
    for cmd in commands:
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=repo_root
        )
        if result.stdout.strip():
            return result.stdout.splitlines()
        last_stdout = result.stdout

    return last_stdout.splitlines()


def _compile_db_cppopts(files: list[str], repo_root: Path) -> list[str]:
    """
    Extract only the preprocessor-relevant flags cflow understands from the
    repo compile database.

    This keeps the callgraph front door anchored to the real makefile include
    and define surface instead of duplicating flag lists by hand.
    """
    db_path = repo_root / "compile_commands.json"
    if not db_path.exists():
        return []

    try:
        data = json.loads(db_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    wanted = {(repo_root / rel).resolve() for rel in files}
    opts: list[str] = []
    seen: set[tuple[str, str]] = set()

    for entry in data:
        raw_file = entry.get("file")
        raw_dir = entry.get("directory", str(repo_root))
        if not raw_file:
            continue
        directory = Path(raw_dir)
        file_path = Path(raw_file)
        if not file_path.is_absolute():
            file_path = (directory / file_path).resolve()
        else:
            file_path = file_path.resolve()
        if file_path not in wanted:
            continue

        argv = entry.get("arguments")
        if not argv and entry.get("command"):
            try:
                argv = shlex.split(entry["command"])
            except ValueError:
                argv = None
        if not argv:
            continue

        i = 0
        while i < len(argv):
            tok = argv[i]
            flag = None
            value = None
            consumed_pair = False

            if tok in ("-D", "-U", "-I", "-isystem"):
                if i + 1 < len(argv):
                    flag = tok
                    value = argv[i + 1]
                    consumed_pair = True
            elif tok.startswith("-D") and tok != "-D":
                flag = "-D"
                value = tok[2:]
            elif tok.startswith("-U") and tok != "-U":
                flag = "-U"
                value = tok[2:]
            elif tok.startswith("-I") and tok != "-I":
                flag = "-I"
                value = tok[2:]

            if flag and value:
                if flag in ("-I", "-isystem"):
                    inc = Path(value)
                    if not inc.is_absolute():
                        inc = (directory / inc).resolve()
                    key = ("-I", str(inc))
                    if key not in seen:
                        seen.add(key)
                        opts.extend(["-I", str(inc)])
                else:
                    key = (flag, value)
                    if key not in seen:
                        seen.add(key)
                        opts.extend([flag, value])

            i += 2 if consumed_pair else 1

    return opts


# cflow output line format:
#   "    funcname() <return_type funcname (params) at file.cpp:NNN>:"
# or with recursive marker:
#   "    funcname() [see N]"
# Indent is 4 spaces per level.

_CFLOW_LINE_RE = re.compile(
    r"^(\s*)"                              # group 1: leading whitespace (indent)
    r"(\w+)\(\)"                           # group 2: function name
    r"(?:\s+<[^>]*\bat\s+"                 # optional: " <... at "
    r"([^:>]+):(\d+)"                      # group 3: file, group 4: line
    r"[^>]*>)?"                            # rest of <...>
    r"(?:\s+\[see \d+\])?"                # optional recursive marker
)


class Node:
    __slots__ = ("name", "file", "line", "depth", "phase", "fls", "children", "static")

    def __init__(self, name: str, file: str, line: int, depth: int):
        self.name = name
        self.file = file
        self.line = line
        self.depth = depth
        self.phase = ""
        self.fls: list[str] = []
        self.children: list["Node"] = []
        self.static = False  # True = injected from _STATIC_SUPPLEMENTS, not from cflow


def _parse_cflow_lines(lines: list[str], fl_index: dict[str, list[str]]) -> list[Node]:
    """Parse cflow output into annotated Node tree. Returns list of root nodes."""
    roots: list[Node] = []
    stack: list[Node] = []  # (depth, node)

    for raw in lines:
        m = _CFLOW_LINE_RE.match(raw)
        if not m:
            continue
        indent = len(m.group(1))
        # cflow uses 4 spaces per indent level
        depth = indent // 4
        name = m.group(2)
        file = m.group(3) or ""
        # Strip path prefix to get basename
        if file:
            file = Path(file).name
        line_num = int(m.group(4)) if m.group(4) else 0

        node = Node(name=name, file=file, line=line_num, depth=depth)
        node.phase = _phase_label(name)
        node.fls = fl_index.get(name, [])

        if depth == 0:
            roots.append(node)
            stack = [node]
        else:
            # Pop stack until parent depth is depth-1
            while len(stack) > depth:
                stack.pop()
            if stack:
                stack[-1].children.append(node)
            # Push current node
            if len(stack) == depth:
                stack.append(node)
            else:
                stack.append(node)

    return roots


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render_text(roots: list[Node], git_head: str, entries: list[str],
                 open_fl_count: int, highlight_fls: set[str] | None = None,
                 phase_filter: str | None = None,
                 overlay_note: str | None = None) -> str:
    lines = []
    lines.append(f"# Generated: {_today()} git:{git_head[:8]}")
    lines.append(f"# Entries: {', '.join(entries)} | FLs with code refs in graph: {open_fl_count} (of {_count_open_fls()} open)")
    if overlay_note:
        lines.append(f"# Note: {overlay_note}")
    lines.append("# † = static supplement (cflow-invisible: nested loop/conditional call)")
    lines.append("")

    def _walk(node: Node, prefix: str, is_last: bool):
        # Phase label
        phase_str = f"[{node.phase}] " if node.phase else ""
        # FL annotation
        fl_str = ""
        if node.fls:
            markers = []
            for fl in node.fls:
                if highlight_fls and fl in highlight_fls:
                    markers.append(f"★ {fl}")
                else:
                    markers.append(fl)
            fl_str = "  " + " ".join(markers)

        # File:line
        loc = ""
        if node.file and node.line:
            loc = f"  {node.file}:{node.line}"
        elif node.file:
            loc = f"  {node.file}"

        connector = "└─ " if is_last else "├─ "
        static_marker = "†" if node.static else ""
        label = f"{phase_str}{node.name}(){static_marker}{loc}{fl_str}"
        lines.append(prefix + connector + label)

        child_prefix = prefix + ("   " if is_last else "│  ")
        for i, child in enumerate(node.children):
            _walk(child, child_prefix, i == len(node.children) - 1)

    for i, root in enumerate(roots):
        # Root node
        phase_str = f"[{root.phase}] " if root.phase else ""
        fl_str = ""
        if root.fls:
            fl_str = "  " + " ".join(root.fls)
        loc = f"  {root.file}:{root.line}" if root.file and root.line else ""
        lines.append(f"{phase_str}{root.name}(){loc}{fl_str}")
        for j, child in enumerate(root.children):
            _walk(child, "", j == len(root.children) - 1)
        # Incomplete-body notice for functions with known cflow gaps
        if root.name in _INCOMPLETE_BODY_NOTICES:
            lines.append("   " + _INCOMPLETE_BODY_NOTICES[root.name])
        if i < len(roots) - 1:
            lines.append("")

    return "\n".join(lines)


def _node_to_dict(node: Node) -> dict:
    return {
        "name": node.name,
        "file": node.file,
        "line": node.line,
        "depth": node.depth,
        "phase": node.phase,
        "fls": node.fls,
        "static": node.static,
        "children": [_node_to_dict(c) for c in node.children],
    }


def _render_json(roots: list[Node], git_head: str, entries: list[str]) -> str:
    data = {
        "generated_at": _today(),
        "git_head": git_head[:8],
        "entry_points": entries,
        "nodes": [_node_to_dict(r) for r in roots],
    }
    return json.dumps(data, indent=2)


def _today() -> str:
    import datetime
    return datetime.date.today().isoformat()


_OPEN_FL_COUNT_CACHE: int | None = None


def _count_open_fls() -> int:
    """Return total number of open FL entries (cached for render calls)."""
    global _OPEN_FL_COUNT_CACHE
    if _OPEN_FL_COUNT_CACHE is not None:
        return _OPEN_FL_COUNT_CACHE
    repo_root = _repo_root()
    result = subprocess.run(
        [sys.executable, "scripts/analyze_runs.py", "fl", "open"],
        capture_output=True, text=True, cwd=repo_root
    )
    count = sum(1 for l in result.stdout.splitlines() if re.match(r"^\s*FL-\d+\s", l))
    _OPEN_FL_COUNT_CACHE = count
    return count


# ---------------------------------------------------------------------------
# Static supplements — hand-coded call edges cflow cannot trace
#
# Used for functions whose bodies contain calls inside nested loops/conditionals
# that cflow's simple C parser misses (not macros, just deep nesting).
#
# Each entry: list of (child_name, file, line_number)
# Supplement nodes are injected AFTER cflow children, deduplicating by name.
# They are marked node.static=True and rendered with † prefix.
#
# Source verification for IOThreadEntry (server_tick.cpp):
#   :3758 WS_FRAME_ENCODE  — lag echo encode (inside POLLIN if-block)
#   :3759 io_send          — lag echo send; also :3832 outbound snapshot send (POLLOUT)
#   :3818 IOLogDisconnectReason — recv error path; also :3846/:3853 (various)
#   :3820 IODisconnectClient   — recv error path; also :3644/:3848/:3854 (various)
#   IODisconnectClient itself calls TCP_CLOSE + IOEnqueueSynthetic(DISCONNECT)
# ---------------------------------------------------------------------------

_STATIC_SUPPLEMENTS: dict[str, list[tuple[str, str, int]]] = {
    "IOThreadEntry": [
        # POLLIN recv path — cflow misses these due to nested for+if blocks
        ("WS_FRAME_ENCODE",       "server_tick.cpp", 3758),  # lag echo frame encode
        ("io_send",               "server_tick.cpp", 3759),  # lag echo + POLLOUT outbound send
        ("IOLogDisconnectReason", "server_tick.cpp", 3818),  # recv/send/hup error logging
        ("IODisconnectClient",    "server_tick.cpp", 3820),  # close + synthetic DISCONNECT
    ],
}


def _inject_supplements(roots: list[Node], fl_index: dict[str, list[str]]) -> None:
    """
    Walk tree and inject static supplement children for functions cflow can't trace.
    Modifies tree in-place. Deduplicates by function name (skips if already present).
    """
    for root in roots:
        _inject_node_supplements(root, fl_index)


def _inject_node_supplements(node: Node, fl_index: dict[str, list[str]]) -> None:
    # Recurse into existing children first
    for child in node.children:
        _inject_node_supplements(child, fl_index)

    if node.name not in _STATIC_SUPPLEMENTS:
        return

    existing_names = {c.name for c in node.children}
    for child_name, child_file, child_line in _STATIC_SUPPLEMENTS[node.name]:
        if child_name in existing_names:
            continue  # cflow already found it — don't duplicate
        child = Node(child_name, child_file, child_line, node.depth + 1)
        child.phase = _phase_label(child_name)
        child.fls = fl_index.get(child_name, [])
        child.static = True
        node.children.append(child)


# Functions whose bodies are incompletely traced by cflow — note shown in output.
# Keys that have _STATIC_SUPPLEMENTS get an "augmented" note instead of "incomplete".
_INCOMPLETE_BODY_NOTICES: dict[str, str] = {
    # IOThreadEntry: static supplement covers the missing calls — no notice needed
    # (supplement nodes are marked with † to indicate static origin)
}


def _git_head(repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True, text=True, cwd=repo_root
    )
    return result.stdout.strip()[:8] if result.returncode == 0 else "unknown"


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _cache_dir(repo_root: Path) -> Path:
    d = repo_root / ".run" / "cflow"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_key(git_head: str, entries: list[str], files: list[str], depth: int) -> str:
    """Cache key encodes head + run parameters to avoid stale depth/entry hits."""
    import hashlib
    params = f"{','.join(sorted(entries))}|{','.join(sorted(files))}|{depth}"
    params_hash = hashlib.md5(params.encode()).hexdigest()[:6]
    return f"{git_head[:8]}-{params_hash}"


def _load_cached(git_head: str, entries: list[str], files: list[str],
                  depth: int, repo_root: Path) -> dict | None:
    key = _cache_key(git_head, entries, files, depth)
    cache_file = _cache_dir(repo_root) / f"{key}.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text())
        except Exception:
            return None
    return None


def _save_cache(git_head: str, entries: list[str], files: list[str],
                 depth: int, data: dict, repo_root: Path):
    key = _cache_key(git_head, entries, files, depth)
    cache_file = _cache_dir(repo_root) / f"{key}.json"
    cache_file.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------

def _collect_all_nodes(nodes: list[Node]) -> list[Node]:
    result = []
    for n in nodes:
        result.append(n)
        result.extend(_collect_all_nodes(n.children))
    return result


def _subtree_contains_names(node: Node, names: set[str]) -> bool:
    if node.name in names:
        return True
    return any(_subtree_contains_names(c, names) for c in node.children)


def _filter_by_func_names(roots: list[Node], names: set[str]) -> list[Node]:
    """Keep only subtrees that contain at least one of the given function names."""
    filtered = []
    for root in roots:
        if _subtree_contains_names(root, names):
            filtered.append(_filter_node(root, names))
    return filtered


def _filter_node(node: Node, names: set[str]) -> Node:
    """Return a copy of node with only children that contain names."""
    new_node = Node(node.name, node.file, node.line, node.depth)
    new_node.phase = node.phase
    new_node.fls = node.fls
    new_node.children = [
        _filter_node(c, names)
        for c in node.children
        if _subtree_contains_names(c, names)
    ]
    return new_node


def _filter_by_phase(roots: list[Node], phase: str) -> list[Node]:
    """Keep nodes matching phase and their ancestors up to root."""
    def has_phase(node: Node) -> bool:
        if node.phase.upper() == phase.upper():
            return True
        return any(has_phase(c) for c in node.children)

    def filter_node_phase(node: Node) -> Node | None:
        if not has_phase(node):
            return None
        new_node = Node(node.name, node.file, node.line, node.depth)
        new_node.phase = node.phase
        new_node.fls = node.fls
        new_node.children = [
            r for r in (filter_node_phase(c) for c in node.children) if r is not None
        ]
        return new_node

    result = []
    for root in roots:
        filtered = filter_node_phase(root)
        if filtered:
            result.append(filtered)
    return result


# ---------------------------------------------------------------------------
# Main generate function
# ---------------------------------------------------------------------------

def generate(
    entries: list[str],
    files: list[str],
    depth: int,
    repo_root: Path,
    open_only: bool = True,
    fl_ids_filter: list[str] | None = None,
    fl_filter: str | None = None,
    phase_filter: str | None = None,
    fmt: str = "text",
    no_cache: bool = False,
) -> str:
    git_head = _git_head(repo_root)
    overlay_note: str | None = None
    open_fl_count = 0
    fl_funcs: list[str] = []
    fl_anchor_gap = False
    fl_unresolvable_refs: list[str] = []

    # Try cache (only for full, unfiltered runs)
    cached = None
    if not no_cache and not fl_filter and not phase_filter and not fl_ids_filter:
        cached = _load_cached(git_head, entries, files, depth, repo_root)

    if cached:
        # Reconstruct node list from cached JSON for display
        roots_data = cached.get("nodes", [])
        if fmt == "json":
            return json.dumps(cached, indent=2)
        # Re-render text from cached data — rebuild Node objects
        roots = _dicts_to_nodes(roots_data, 0)
        open_fl_count = cached.get("open_fl_count", 0)
        return _render_text(roots, git_head, entries, open_fl_count)

    if fl_filter:
        fl_funcs, fl_anchor_gap, fl_unresolvable_refs = _get_fl_function_refs(fl_filter, repo_root)
        if not fl_funcs:
            if fl_anchor_gap:
                if fl_unresolvable_refs:
                    refs_str = ", ".join(fl_unresolvable_refs[:3])
                    return (
                        f"CODEREF_UNRESOLVABLE: {fl_filter} — tree-sitter AST lookup failed "
                        f"for all CodeRefs ({refs_str}). Lines may be at file scope, inside "
                        f"macros, or in a file tree-sitter could not parse. Add "
                        f"AnchorFunctions to the FL overlay to enable callgraph --fl routing."
                    )
                return (
                    f"ANCHOR GAP: {fl_filter} — AnchorFunctions are required for callgraph "
                    f"--fl routing. CodeRefs identify code areas but are not sufficient for "
                    f"function-level routing. Add AnchorFunctions to the FL overlay to enable "
                    f"callgraph --fl routing. Run: python3 scripts/analyze_runs.py fl code "
                    f"{fl_filter} for remediation."
                )
            return f"# No function references found for {fl_filter}"

        direct_roots: list[Node] = []
        single_fl_files = _single_fl_candidate_files(fl_filter, repo_root)
        direct_funcs = (
            fl_funcs[:] if single_fl_files else _preferred_direct_anchor_functions(fl_funcs, repo_root)
        )
        direct_index = {fn: [fl_filter] for fn in direct_funcs if fn not in _STDLIB_BLOCKLIST}
        for fn in direct_funcs:
            route_files = single_fl_files or _candidate_files_for_anchor_function(fn, repo_root)
            lines = _run_cflow(fn, route_files, depth, repo_root)
            direct_roots.extend(_parse_cflow_lines(lines, direct_index))
        _inject_supplements(direct_roots, direct_index)
        direct_roots = [
            root for root in direct_roots
            if _subtree_contains_names(root, set(direct_funcs))
        ]
        if direct_roots:
            if fmt == "json":
                return _render_json(direct_roots, git_head, direct_funcs)
            return _render_text(
                direct_roots,
                git_head,
                direct_funcs,
                open_fl_count,
                highlight_fls={fl_filter},
                phase_filter=phase_filter,
                overlay_note="Direct anchor trace for FL-filtered query",
            )

    # Build FL index.
    # Full open-FL overlay sweeps are appropriate for the default server graph,
    # but they are too expensive for ad hoc engine entry/file queries.
    effective_fl_ids = fl_ids_filter
    if fl_filter and not effective_fl_ids:
        effective_fl_ids = _get_fl_family_ids(fl_filter, repo_root)
    use_global_overlay = bool(
        effective_fl_ids
        or fl_filter
        or (entries == DEFAULT_ENTRIES and files == DEFAULT_FILES)
    )
    if use_global_overlay:
        fl_index = build_fl_function_index(
            repo_root,
            open_only=open_only,
            fl_ids_filter=effective_fl_ids,
        )
        open_fl_count = len(set(fl for fls in fl_index.values() for fl in fls))
    else:
        fl_index = {}
        open_fl_count = 0
        overlay_note = (
            "FL overlays disabled for custom entry/file query; pass --fl or "
            "--only-fls to annotate without sweeping every open FL."
        )

    # Run cflow per entry point, collect all roots
    all_roots: list[Node] = []
    for entry in entries:
        lines = _run_cflow(entry, files, depth, repo_root)
        roots = _parse_cflow_lines(lines, fl_index)
        all_roots.extend(roots)

    # Inject static supplements for functions cflow can't trace through nested loops/ifs
    _inject_supplements(all_roots, fl_index)

    # Apply filters
    highlight_fls: set[str] | None = None
    if fl_filter:
        func_names = set(fl_funcs)
        all_roots = _filter_by_func_names(all_roots, func_names)
        # If no nodes survived (funcs live outside the default server files),
        # supplement with broader engine/web trees rooted at the anchor functions.
        if not all_roots:
            engine_files = [
                str(repo_root / "engine" / "game.cpp"),
                str(repo_root / "engine" / "render.cpp"),
                str(repo_root / "web" / "game_web.cpp"),
            ]
            supp_roots: list[Node] = []
            for fn in fl_funcs:
                supp_lines = _run_cflow(fn, engine_files, depth, repo_root)
                supp_roots.extend(_parse_cflow_lines(supp_lines, fl_index))
            all_roots = _filter_by_func_names(supp_roots, func_names)
        # If still empty after supplemental pass, surface a diagnostic rather than
        # silently returning an annotated graph with zero nodes.
        if not all_roots:
            return (
                f"# No callgraph nodes found for {fl_filter} "
                f"(functions not reachable from server, engine, or web anchor entry points: "
                f"{', '.join(sorted(func_names))})"
            )
        highlight_fls = {fl_filter}

    if phase_filter:
        all_roots = _filter_by_phase(all_roots, phase_filter)

    # Cache full result (before filtering) — only if no filters applied
    if not no_cache and not fl_filter and not phase_filter and not fl_ids_filter:
        cache_data = {
            "generated_at": _today(),
            "git_head": git_head[:8],
            "entry_points": entries,
            "open_fl_count": open_fl_count,
            "nodes": [_node_to_dict(r) for r in all_roots],
        }
        _save_cache(git_head, entries, files, depth, cache_data, repo_root)

    if fmt == "json":
        return _render_json(all_roots, git_head, entries)

    return _render_text(all_roots, git_head, entries, open_fl_count,
                        highlight_fls=highlight_fls, phase_filter=phase_filter,
                        overlay_note=overlay_note)


def _dicts_to_nodes(dicts: list[dict], base_depth: int) -> list[Node]:
    result = []
    for d in dicts:
        node = Node(d["name"], d["file"], d["line"], d["depth"])
        node.phase = d.get("phase", "")
        node.fls = d.get("fls", [])
        node.static = d.get("static", False)
        node.children = _dicts_to_nodes(d.get("children", []), base_depth + 1)
        result.append(node)
    return result


def _build_index_from_nodes(nodes: list[Node]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for node in nodes:
        if node.fls:
            index[node.name] = node.fls
        index.update(_build_index_from_nodes(node.children))
    return index


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Annotated call graph for C++ entrypoints with optional FL overlays. "
            "FL queries prefer direct anchor traces and fail closed when routing "
            "is unavailable."
        )
    )
    parser.add_argument(
        "--entry", nargs="+", default=DEFAULT_ENTRIES,
        metavar="FUNC",
        help="Entry point function(s) (default: AcceptThreadEntry IOThreadEntry ServerTickLoop)"
    )
    parser.add_argument(
        "--files", nargs="+", default=DEFAULT_FILES,
        metavar="FILE",
        help="Source files to analyze (default: server/server_tick.cpp server/game_svr.cpp server/mp_step.cpp)"
    )
    parser.add_argument(
        "--depth", type=int, default=4,
        help="Max call tree depth (default: 4)"
    )
    parser.add_argument(
        "--fl", metavar="FL-NNN",
        help=(
            "Show subtree for this FL; prefers direct AnchorFunctions / AST-resolved "
            "CodeRefs and otherwise fails closed with remediation"
        )
    )
    parser.add_argument(
        "--only-fls", nargs="+", metavar="FL-NNN",
        help="Annotate only these specific FLs (default: all open FLs)"
    )
    parser.add_argument(
        "--phase", metavar="PHASE",
        help="Show only nodes with this phase label (e.g. COMBAT, SNAPSHOT)"
    )
    parser.add_argument(
        "--open-only", action="store_true", default=True,
        help="Annotate only open FLs (default: true)"
    )
    parser.add_argument(
        "--format", choices=["text", "json"], default="text",
        help="Output format (default: text)"
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Force regeneration even if cached result exists"
    )
    args = parser.parse_args()

    repo_root = _repo_root()

    output = generate(
        entries=args.entry,
        files=args.files,
        depth=args.depth,
        repo_root=repo_root,
        open_only=args.open_only,
        fl_ids_filter=args.only_fls,
        fl_filter=args.fl,
        phase_filter=args.phase,
        fmt=args.format,
        no_cache=args.no_cache,
    )
    print(output)


if __name__ == "__main__":
    main()
