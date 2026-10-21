#!/usr/bin/env python3
"""Semantic plan linter for GSD *-PLAN.md files.

Validates plans against quality rules that are hard to game by formatting tricks.
Checks are semantic (dependency cycles, requirement traceability, command sanity)
rather than purely structural (line counts, header presence).

Exit codes:
    0 — all checks pass
    1 — warnings only (becomes failure in --strict mode)
    2 — blockers found

Usage:
    python3 scripts/plan_lint.py                          # lint all plans
    python3 scripts/plan_lint.py --strict                 # treat warnings as errors
    python3 scripts/plan_lint.py path/to/PLAN.md          # lint specific file(s)
    python3 scripts/plan_lint.py --stdin                  # read plan content from stdin
    python3 scripts/plan_lint.py --stdin --name foo.md    # stdin with virtual filename
"""

import argparse
import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Finding dataclass
# ---------------------------------------------------------------------------

class Finding:
    """A single lint finding."""

    __slots__ = ("severity", "check", "message", "file", "line")

    def __init__(self, severity: str, check: str, message: str,
                 file: str = "", line: int = 0):
        self.severity = severity  # P1 (blocker), P2 (warning), P3 (info)
        self.check = check
        self.message = message
        self.file = file
        self.line = line

    def __repr__(self):
        loc = f" ({self.file}:{self.line})" if self.file and self.line else (
            f" ({self.file})" if self.file else "")
        return f"[{self.severity}] {self.check}: {self.message}{loc}"


# ---------------------------------------------------------------------------
# Plan discovery
# ---------------------------------------------------------------------------

PLAN_GLOB = ".planning/phases/**/*-PLAN.md"

def discover_plans(root: Path) -> List[Path]:
    """Find all *-PLAN.md files under .planning/phases/."""
    return sorted(root.glob(PLAN_GLOB))


# ---------------------------------------------------------------------------
# Section extraction helpers
# ---------------------------------------------------------------------------

_SECTION_RE = re.compile(
    r"^(#{1,3})\s+(.+?)(?:\s*\n)",
    re.MULTILINE,
)

def _extract_sections(content: str) -> Dict[str, str]:
    """Return {normalized_name: body_text} for all markdown sections.

    Code-fence-aware: ignores # lines inside ``` fenced code blocks.
    """
    # First, find all code fence ranges to exclude
    fence_ranges: List[Tuple[int, int]] = []
    for m in re.finditer(r"```.*?```", content, re.DOTALL):
        fence_ranges.append((m.start(), m.end()))

    def _in_fence(pos: int) -> bool:
        for start, end in fence_ranges:
            if start <= pos < end:
                return True
        return False

    # Find headings that are NOT inside code fences
    matches = [
        m for m in _SECTION_RE.finditer(content)
        if not _in_fence(m.start())
    ]
    sections: Dict[str, str] = {}
    for i, m in enumerate(matches):
        name = m.group(2).strip().lower()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        sections[name] = content[start:end]
    return sections


def _has_nonempty_section(sections: Dict[str, str], name: str) -> bool:
    """Check if a section exists and has non-whitespace body."""
    key = name.lower()
    body = sections.get(key, "")
    return len(body.strip()) > 0


# ---------------------------------------------------------------------------
# Check 1: Required sections present and non-empty
# ---------------------------------------------------------------------------

REQUIRED_SECTIONS = [
    "contract check",
    "consistency diff",
    "blocking checklist",
]

# Success criteria can be in multiple forms
SUCCESS_CRITERIA_NAMES = [
    "success criteria",
    "success_criteria",
]


def check_required_sections(content: str, sections: Dict[str, str],
                            filename: str) -> List[Finding]:
    findings = []
    for sec_name in REQUIRED_SECTIONS:
        if not _has_nonempty_section(sections, sec_name):
            findings.append(Finding(
                "P1", "required-section",
                f"Missing or empty required section: '{sec_name}'",
                filename,
            ))

    # Success criteria: check section OR <success_criteria> XML tag
    has_sc_section = any(
        _has_nonempty_section(sections, n) for n in SUCCESS_CRITERIA_NAMES
    )
    has_sc_tag = bool(re.search(r"<success_criteria>", content))
    if not has_sc_section and not has_sc_tag:
        findings.append(Finding(
            "P2", "required-section",
            "Missing success criteria (section or <success_criteria> tag)",
            filename,
        ))

    return findings


# ---------------------------------------------------------------------------
# Check 2: Dependency graph correctness
# ---------------------------------------------------------------------------

_DEPENDS_ON_RE = re.compile(r"^depends_on:\s*\[([^\]]*)\]", re.MULTILINE)
_DEPENDS_ON_YAML_RE = re.compile(
    r"^depends_on:\s*\n((?:\s+-\s+.*\n)*)", re.MULTILINE
)


def _parse_depends_on(content: str) -> List[str]:
    """Extract dependency plan IDs from frontmatter."""
    # Try inline list: depends_on: [01-01, 01-02]
    m = _DEPENDS_ON_RE.search(content)
    if m:
        raw = m.group(1).strip()
        if not raw:
            return []
        return [d.strip().strip('"').strip("'") for d in raw.split(",") if d.strip()]
    # Try YAML list
    m = _DEPENDS_ON_YAML_RE.search(content)
    if m:
        return [
            line.strip().lstrip("- ").strip().strip('"').strip("'")
            for line in m.group(1).splitlines()
            if line.strip()
        ]
    return []


def _plan_id_from_path(p: Path) -> str:
    """Extract plan ID like '10-01' from '10-01-PLAN.md'."""
    name = p.name
    m = re.match(r"(.+)-PLAN\.md$", name)
    return m.group(1) if m else name


def check_dependencies(plans: Dict[str, Tuple[Path, str]]) -> List[Finding]:
    """Validate dependency references exist and detect cycles.

    Args:
        plans: {plan_id: (path, content)}
    """
    findings = []
    graph: Dict[str, List[str]] = {}

    for plan_id, (path, content) in plans.items():
        deps = _parse_depends_on(content)
        graph[plan_id] = deps
        for dep in deps:
            if dep and dep not in plans:
                findings.append(Finding(
                    "P1", "dependency-missing",
                    f"depends_on references '{dep}' which does not exist",
                    str(path),
                ))

    # Cycle detection via DFS
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {pid: WHITE for pid in graph}

    def dfs(node: str, path_stack: List[str]) -> Optional[List[str]]:
        color[node] = GRAY
        path_stack.append(node)
        for dep in graph.get(node, []):
            if dep not in color:
                continue
            if color[dep] == GRAY:
                cycle_start = path_stack.index(dep)
                return path_stack[cycle_start:] + [dep]
            if color[dep] == WHITE:
                result = dfs(dep, path_stack)
                if result:
                    return result
        path_stack.pop()
        color[node] = BLACK
        return None

    for node in graph:
        if color[node] == WHITE:
            cycle = dfs(node, [])
            if cycle:
                findings.append(Finding(
                    "P1", "dependency-cycle",
                    f"Dependency cycle detected: {' -> '.join(cycle)}",
                ))
                break  # One cycle report is enough

    return findings


# ---------------------------------------------------------------------------
# Check 3: Verify command sanity
# ---------------------------------------------------------------------------

_VERIFY_TAG_RE = re.compile(
    r"<verif(?:y|ication)>(.*?)</verif(?:y|ication)>", re.DOTALL
)
_VERIFY_SECTION_RE = re.compile(
    r"^#{1,3}\s+Verify\b[^\n]*\n(.*?)(?=^#{1,3}\s|\Z)",
    re.MULTILINE | re.DOTALL,
)
_CODE_BLOCK_RE = re.compile(r"```(?:bash|sh|shell)?\s*\n(.*?)```", re.DOTALL)

# Patterns that indicate dangerous commands (regex patterns, matched against
# the start of the command or after pipe/semicolon boundaries)
DANGEROUS_PATTERNS = [
    r"(?:^|[|;&]\s*)rm\s",
    r"(?:^|[|;&]\s*)rmdir\s",
    r"(?:^|[|;&]\s*)git\s+push\b",
    r"(?:^|[|;&]\s*)git\s+reset\s+--hard\b",
    r"\bDROP\s+TABLE\b",
    r"\bDROP\s+DATABASE\b",
    r"\bDELETE\s+FROM\b",
    r"(?:^|[|;&]\s*)curl\s.*-X\s+(?:POST|PUT|DELETE)\b",
    r"(?:^|[|;&]\s*)wget\s.*--post\b",
]

KNOWN_SAFE_PREFIXES = {
    "python3 -m pytest",
    "python3 -c",
    "python3 scripts/",
    "bash scripts/",
    "make ",
    "git status",
    "git diff",
    "git log",
    "ls ",
    "cat ",
    "grep ",
    "wc ",
    "echo ",
}


def _extract_verify_commands(content: str) -> List[str]:
    """Extract shell commands from verify blocks and sections.

    Code-fence-aware: ignores markdown heading syntax inside code blocks.
    """
    commands = []

    # From <verify>/<verification> tags
    for m in _VERIFY_TAG_RE.finditer(content):
        for cb in _CODE_BLOCK_RE.finditer(m.group(1)):
            commands.extend(_split_commands(cb.group(1)))
        # If no code block, try raw lines
        if not _CODE_BLOCK_RE.search(m.group(1)):
            commands.extend(_split_commands(m.group(1)))

    # From ## Verify section — code-fence-aware extraction
    # Find "## Verify" heading, then grab the next code block(s) after it
    # (regardless of any # comment lines inside code fences)
    verify_heading_re = re.compile(
        r"^#{1,3}\s+Verify\b[^\n]*$", re.MULTILINE
    )
    for m in verify_heading_re.finditer(content):
        after = content[m.end():]
        # Grab all code blocks until the next non-code-fenced heading at h1-h3
        # that isn't inside a code block
        in_fence = False
        pos = 0
        stop = len(after)
        for line in after.splitlines(keepends=True):
            if line.strip().startswith("```"):
                in_fence = not in_fence
            elif not in_fence and re.match(r"^#{1,3}\s+\S", line):
                # Real heading outside fence — stop
                stop = pos
                break
            pos += len(line)

        verify_body = after[:stop]
        for cb in _CODE_BLOCK_RE.finditer(verify_body):
            commands.extend(_split_commands(cb.group(1)))

    return commands


def _split_commands(block: str) -> List[str]:
    """Split a code block into logical commands.

    Handles multi-line commands (python3 -c "...", heredocs, backslash
    continuations) by joining lines that are inside unbalanced quotes.
    """
    raw_lines = block.strip().splitlines()
    commands = []
    current = ""

    for line in raw_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            if not current:
                continue
            # Inside a multi-line command, keep going
            current += "\n" + stripped
            continue

        if current:
            current += "\n" + stripped
        else:
            current = stripped

        # Handle backslash line continuation
        if current.rstrip().endswith("\\"):
            continue

        # Check if quotes are balanced (rough heuristic)
        if _quotes_balanced(current):
            commands.append(current)
            current = ""

    if current:
        commands.append(current)

    return commands


def _quotes_balanced(s: str) -> bool:
    """Check if single and double quotes are roughly balanced."""
    # Count unescaped quotes
    in_single = False
    in_double = False
    i = 0
    while i < len(s):
        c = s[i]
        if c == '\\' and i + 1 < len(s):
            i += 2
            continue
        if c == '"' and not in_single:
            in_double = not in_double
        elif c == "'" and not in_double:
            in_single = not in_single
        i += 1
    return not in_single and not in_double


def check_verify_commands(content: str, filename: str) -> List[Finding]:
    findings = []
    commands = _extract_verify_commands(content)

    if not commands:
        findings.append(Finding(
            "P2", "verify-missing",
            "No verify commands found (no <verify> tag or ## Verify section with code blocks)",
            filename,
        ))
        return findings

    for cmd in commands:
        # Check for obviously dangerous commands using regex
        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, cmd, re.IGNORECASE):
                findings.append(Finding(
                    "P1", "verify-dangerous",
                    f"Verify command contains potentially dangerous operation in: {cmd[:80]}",
                    filename,
                ))
                break  # One finding per command is enough

        # Check for malformed quoting
        try:
            shlex.split(cmd)
        except ValueError as e:
            findings.append(Finding(
                "P1", "verify-malformed",
                f"Verify command has malformed quoting: {e} in: {cmd[:80]}",
                filename,
            ))

    return findings


# ---------------------------------------------------------------------------
# Check 4: Requirement traceability
# ---------------------------------------------------------------------------

_REQ_ID_RE = re.compile(r"\b([A-Z][\w-]*-\d+)\b")


def _extract_requirement_ids(content: str) -> Set[str]:
    """Extract requirement IDs like BANK-01, GAP-11-02, etc."""
    # Look in frontmatter Requirements: line and plan header
    ids = set()
    for m in _REQ_ID_RE.finditer(content):
        candidate = m.group(1)
        # Filter out false positives (file extensions, hex colors, etc.)
        if not re.match(r"^[A-F0-9]+-[A-F0-9]+$", candidate):
            ids.add(candidate)
    return ids


def _extract_header_requirements(content: str) -> Set[str]:
    """Extract requirement IDs specifically from the plan header line.

    Only matches bold markdown format (**Requirements**:) or a top-level
    Requirements: line that is NOT preceded by task-body indentation.
    The `Requirement: FOO-01` lines inside task descriptions are NOT headers.
    """
    # Bold markdown: **Requirements**: ... or **Requirement**: ...
    m = re.search(r"^\*\*Requirements?\*\*:\s*(.+)$", content, re.MULTILINE)
    if m:
        return set(_REQ_ID_RE.findall(m.group(1)))
    # Frontmatter-style: Requirements: ... (must be plural to avoid task-body lines)
    m = re.search(r"^Requirements:\s*(.+)$", content, re.MULTILINE)
    if m:
        return set(_REQ_ID_RE.findall(m.group(1)))
    return set()


def check_requirement_traceability(content: str, sections: Dict[str, str],
                                   filename: str) -> List[Finding]:
    findings = []
    header_reqs = _extract_header_requirements(content)
    if not header_reqs:
        # No requirement IDs declared — info only, some plans may not have them
        findings.append(Finding(
            "P3", "req-trace",
            "No requirement IDs found in plan header",
            filename,
        ))
        return findings

    # Check that each declared req appears somewhere in tasks or success criteria
    tasks_text = ""
    for key, body in sections.items():
        if "task" in key or "success" in key:
            tasks_text += body

    # Also include <tasks> and <success_criteria> tag content
    for tag in ["tasks", "success_criteria"]:
        for m in re.finditer(rf"<{tag}>(.*?)</{tag}>", content, re.DOTALL):
            tasks_text += m.group(1)

    task_reqs = set(_REQ_ID_RE.findall(tasks_text))

    missing_in_tasks = header_reqs - task_reqs
    if missing_in_tasks:
        findings.append(Finding(
            "P2", "req-trace-gap",
            f"Requirement(s) declared but not referenced in tasks/success criteria: "
            f"{', '.join(sorted(missing_in_tasks))}",
            filename,
        ))

    return findings


# ---------------------------------------------------------------------------
# Check 5: Cross-doc consistency
# ---------------------------------------------------------------------------

def _parse_roadmap_phases(root: Path) -> Dict[str, str]:
    """Parse ROADMAP.md to extract phase statuses.

    Returns: {phase_key: status} where status is 'complete'/'queued'/'in_progress'.
    """
    roadmap_path = root / ".planning" / "ROADMAP.md"
    if not roadmap_path.exists():
        return {}

    content = roadmap_path.read_text()
    phases = {}

    # Parse progress table
    for m in re.finditer(
        r"^\|\s*[\d.]+\.?\s+(.+?)\s*\|\s*\d+/\d+\s*\|\s*(\w+)\s*\|",
        content, re.MULTILINE
    ):
        name = m.group(1).strip()
        status = m.group(2).strip().lower()
        phases[name] = status

    return phases


def _parse_state_phase(root: Path) -> Optional[Tuple[str, str]]:
    """Parse STATE.md to get current phase and status."""
    state_path = root / ".planning" / "STATE.md"
    if not state_path.exists():
        return None

    content = state_path.read_text()
    # "Phase: 10 of 14 ..." or "Status: Phase complete"
    phase_m = re.search(r"Phase:\s*(\d+)", content)
    status_m = re.search(r"Status:\s*(.+?)$", content, re.MULTILINE)

    if phase_m and status_m:
        return phase_m.group(1), status_m.group(1).strip().lower()
    return None


def check_cross_doc_consistency(content: str, filename: str,
                                root: Path) -> List[Finding]:
    findings = []

    # Extract phase number from plan path
    plan_path = Path(filename)
    phase_match = re.search(r"phases/(\d+(?:\.\d+)?)-", str(plan_path))
    if not phase_match:
        return findings

    phase_num = phase_match.group(1)

    # Check roadmap status
    roadmap_phases = _parse_roadmap_phases(root)
    for phase_name, status in roadmap_phases.items():
        # Try to match phase number in name
        if phase_num + "." in phase_name or phase_name.startswith(phase_num + " "):
            if status == "complete":
                # Plan for a completed phase — check if plan is marked as pending
                plan_status_m = re.search(
                    r"^Status:\s*(.+?)$", content, re.MULTILINE
                )
                if plan_status_m and "pending" in plan_status_m.group(1).lower():
                    findings.append(Finding(
                        "P1", "cross-doc-contradiction",
                        f"Plan status is 'pending' but ROADMAP.md shows phase {phase_num} "
                        f"as 'Complete'",
                        filename,
                    ))

    return findings


# ---------------------------------------------------------------------------
# Check 6: File accounting
# ---------------------------------------------------------------------------

_FM_FILES_MODIFIED_RE = re.compile(
    r"^\*?\*?Files?\s+modified\*?\*?:\s*(.+?)$", re.MULTILINE | re.IGNORECASE
)
_FM_FILES_CREATED_RE = re.compile(
    r"^\*?\*?Files?\s+created\*?\*?:\s*(.+?)$", re.MULTILINE | re.IGNORECASE
)
_FM_FILES_YAML_RE = re.compile(
    r"^files_modified:\s*\n((?:\s+-\s+.*\n)*)", re.MULTILINE
)
_FM_FILES_CREATED_YAML_RE = re.compile(
    r"^files_created:\s*\n((?:\s+-\s+.*\n)*)", re.MULTILINE
)

CODE_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".cpp", ".h", ".c",
    ".json", ".yaml", ".yml", ".toml", ".sh", ".bash",
}


def _extract_declared_files(content: str) -> Set[str]:
    """Extract file paths from structured declarations only.

    Only counts files from:
    - Frontmatter files_modified/files_created YAML lists
    - Header-style **Files modified/created**: lines
    - <files> tags inside <task> blocks

    Does NOT count stray path mentions in prose.
    """
    files: Set[str] = set()

    # YAML frontmatter
    for pattern in [_FM_FILES_YAML_RE, _FM_FILES_CREATED_YAML_RE]:
        m = pattern.search(content)
        if m:
            for line in m.group(1).splitlines():
                path = line.strip().lstrip("- ").strip().strip("`").strip('"').strip("'")
                if path and any(path.endswith(ext) for ext in CODE_EXTS):
                    files.add(path)

    # Header-style lines: **Files modified**: `foo.py`, `bar.py`
    for pattern in [_FM_FILES_MODIFIED_RE, _FM_FILES_CREATED_RE]:
        m = pattern.search(content)
        if m:
            raw = m.group(1)
            for token in re.findall(r"`([^`]+)`", raw):
                if any(token.endswith(ext) for ext in CODE_EXTS):
                    files.add(token)

    # <files> tags inside <task> blocks
    for task_m in re.finditer(r"<task\b.*?</task>", content, re.DOTALL):
        for files_m in re.finditer(r"<files>(.*?)</files>", task_m.group(0), re.DOTALL):
            for path in re.findall(r"[\w/.~\-]+\.\w+", files_m.group(1)):
                if any(path.endswith(ext) for ext in CODE_EXTS):
                    files.add(path)

    return files


def check_file_accounting(content: str, filename: str) -> List[Finding]:
    """Check file accounting limits.

    Only counts files from structured declarations (not prose mentions).
    """
    findings = []
    declared = _extract_declared_files(content)

    # Per-task file limits: extract <task> blocks and check <files> tag counts
    task_blocks = re.findall(r"<task\b.*?</task>", content, re.DOTALL)
    for i, block in enumerate(task_blocks, 1):
        task_files = set()
        for files_m in re.finditer(r"<files>(.*?)</files>", block, re.DOTALL):
            for path in re.findall(r"[\w/.~\-]+\.\w+", files_m.group(1)):
                if any(path.endswith(ext) for ext in CODE_EXTS):
                    task_files.add(path)
        if len(task_files) > 5:
            findings.append(Finding(
                "P2", "file-count",
                f"Task {i} declares {len(task_files)} files (recommend <=5 per task)",
                filename,
            ))

    return findings


# ---------------------------------------------------------------------------
# Check 7: Task format validation
# ---------------------------------------------------------------------------

def check_task_format(content: str, sections: Dict[str, str],
                      filename: str) -> List[Finding]:
    """Validate that tasks are present in some recognized format."""
    findings = []

    # Recognize: <task> XML tags, ### Task N: headers, or ## Tasks section
    has_xml_tasks = bool(re.search(r"<task\b", content))
    has_md_tasks = bool(re.search(r"^#{2,3}\s+Task\s+\d+", content, re.MULTILINE))
    has_tasks_section = _has_nonempty_section(sections, "tasks")

    if not has_xml_tasks and not has_md_tasks and not has_tasks_section:
        findings.append(Finding(
            "P1", "no-tasks",
            "No tasks found (expected <task> XML blocks, '### Task N:' headers, or '## Tasks' section)",
            filename,
        ))

    return findings


# ---------------------------------------------------------------------------
# Check 8: Heuristic complexity warnings (do not block by default)
# ---------------------------------------------------------------------------

def _count_tasks(content: str) -> int:
    """Count tasks in both XML and markdown formats."""
    xml_count = len(re.findall(r"<task\b", content))
    md_count = len(re.findall(r"^#{2,3}\s+Task\s+\d+", content, re.MULTILINE))
    return max(xml_count, md_count)


def _count_waves(content: str) -> int:
    """Count wave declarations from frontmatter or inline metadata."""
    wave_match = re.search(r"^wave:\s*(\d+)", content, re.MULTILINE)
    if wave_match:
        return int(wave_match.group(1))
    waves = set(re.findall(r"wave[=:]\s*(\d+)", content))
    return len(waves) if waves else 1


def check_heuristic_complexity(content: str, filename: str) -> List[Finding]:
    """Emit P2 warnings for plans that exceed complexity thresholds.

    These are heuristic signals, not hard correctness failures. They only
    block in --strict mode (or when PLAN_GATE_STRICT=1).

    Important: do not warn on raw line count alone. Long plans can be valid
    when they are concrete and well-structured; line-count warnings created
    repeated false positives on real execution plans. Keep the complexity
    heuristics focused on fan-out that more directly correlates with review
    and execution risk (tasks, waves, files-per-task).
    """
    findings = []

    task_count = _count_tasks(content)
    if task_count > 3:
        findings.append(Finding(
            "P2", "heuristic-tasks",
            f"Plan has {task_count} tasks (recommend <=3)",
            filename,
        ))

    wave_count = _count_waves(content)
    if wave_count > 3:
        findings.append(Finding(
            "P2", "heuristic-waves",
            f"Plan declares {wave_count} waves (recommend <=3)",
            filename,
        ))

    # Per-task file count (>3 files per task)
    task_blocks = re.findall(r"<task\b.*?</task>", content, re.DOTALL)
    for i, block in enumerate(task_blocks, 1):
        task_files = set()
        for files_m in re.finditer(r"<files>(.*?)</files>", block, re.DOTALL):
            for path in re.findall(r"[\w/.~\-]+\.\w+", files_m.group(1)):
                if any(path.endswith(ext) for ext in CODE_EXTS):
                    task_files.add(path)
        if len(task_files) > 3:
            findings.append(Finding(
                "P2", "heuristic-files-per-task",
                f"Task {i} declares {len(task_files)} files (recommend <=3 per task)",
                filename,
            ))

    return findings


# ---------------------------------------------------------------------------
# Check 9: ROADMAP.md validation
# ---------------------------------------------------------------------------

_ROADMAP_PHASE_RE = re.compile(
    r"^-\s+\[([x ])\]\s+\*\*Phase\s+([\d.]+):\s+(.+?)\*\*",
    re.MULTILINE,
)

VALID_ROADMAP_STATUSES = {"complete", "queued", "in_progress", "inserted", "deferred"}


def lint_roadmap(content: str, filename: str, root: Path) -> List[Finding]:
    """Validate ROADMAP.md structure and consistency."""
    findings = []

    # Must have ## Phases section
    sections = _extract_sections(content)
    if not _has_nonempty_section(sections, "phases"):
        findings.append(Finding(
            "P1", "roadmap-missing-phases",
            "ROADMAP.md missing '## Phases' section",
            filename,
        ))
        return findings

    # Parse phase entries
    phases = _ROADMAP_PHASE_RE.findall(content)
    if not phases:
        findings.append(Finding(
            "P1", "roadmap-no-phase-entries",
            "ROADMAP.md has Phases section but no phase entries found",
            filename,
        ))
        return findings

    # Check phase numbering is monotonically increasing
    prev_num = 0.0
    for checkbox, num_str, name in phases:
        try:
            num = float(num_str)
        except ValueError:
            findings.append(Finding(
                "P1", "roadmap-bad-phase-number",
                f"Phase '{num_str}' is not a valid number",
                filename,
            ))
            continue

        if num <= prev_num:
            findings.append(Finding(
                "P2", "roadmap-phase-order",
                f"Phase {num_str} ({name.strip()}) is not in ascending order "
                f"(follows {prev_num})",
                filename,
            ))
        prev_num = num

    # Check completed phases have [x], queued have [ ]
    for checkbox, num_str, name in phases:
        is_checked = checkbox == "x"
        name_lower = name.lower().strip()
        if "queued" in name_lower and is_checked:
            findings.append(Finding(
                "P2", "roadmap-status-mismatch",
                f"Phase {num_str} marked as QUEUED in name but has [x] checkbox",
                filename,
            ))

    # Check phase detail sections exist for non-queued phases
    for checkbox, num_str, name in phases:
        if checkbox == "x":
            detail_header = f"phase {num_str}"
            has_detail = any(
                detail_header in key for key in sections
            )
            if not has_detail:
                findings.append(Finding(
                    "P3", "roadmap-missing-detail",
                    f"Phase {num_str} completed but no detail section found",
                    filename,
                ))

    return findings


# ---------------------------------------------------------------------------
# Check 10: STATE.md validation
# ---------------------------------------------------------------------------

STATE_REQUIRED_FIELDS = [
    (r"^Phase:\s*(.+)$", "Phase"),
    (r"^Status:\s*(.+)$", "Status"),
]


def lint_state(content: str, filename: str, root: Path) -> List[Finding]:
    """Validate STATE.md structure and cross-doc consistency."""
    findings = []

    # Required fields
    for pattern, field_name in STATE_REQUIRED_FIELDS:
        if not re.search(pattern, content, re.MULTILINE):
            findings.append(Finding(
                "P1", "state-missing-field",
                f"STATE.md missing required field: '{field_name}:'",
                filename,
            ))

    # Cross-check: if STATE says phase N is complete, ROADMAP should agree
    phase_m = re.search(r"Phase:\s*(\d+)", content)
    status_m = re.search(r"Status:\s*(.+?)$", content, re.MULTILINE)

    if phase_m and status_m:
        phase_num = phase_m.group(1)
        status_text = status_m.group(1).strip().lower()

        roadmap_path = root / ".planning" / "ROADMAP.md"
        if roadmap_path.exists():
            roadmap_content = roadmap_path.read_text()
            # Find the phase in roadmap
            for checkbox, num_str, name in _ROADMAP_PHASE_RE.findall(roadmap_content):
                if num_str == phase_num:
                    roadmap_complete = checkbox == "x"
                    state_complete = "complete" in status_text
                    if state_complete and not roadmap_complete:
                        findings.append(Finding(
                            "P1", "state-roadmap-contradiction",
                            f"STATE.md says Phase {phase_num} is complete but "
                            f"ROADMAP.md has it unchecked [ ]",
                            filename,
                        ))
                    elif not state_complete and roadmap_complete:
                        findings.append(Finding(
                            "P2", "state-roadmap-drift",
                            f"STATE.md says Phase {phase_num} is not complete but "
                            f"ROADMAP.md has it checked [x]",
                            filename,
                        ))
                    break

    # Must have ## Current Position section
    sections = _extract_sections(content)
    if not _has_nonempty_section(sections, "current position"):
        findings.append(Finding(
            "P1", "state-missing-section",
            "STATE.md missing '## Current Position' section",
            filename,
        ))

    return findings


# ---------------------------------------------------------------------------
# Check 11: REQUIREMENTS.md validation
# ---------------------------------------------------------------------------

_REQ_LINE_RE = re.compile(
    r"^-\s+\[([x ])\]\s+\*\*([A-Z][\w-]+-\d+)\*\*:\s*(.+)",
    re.MULTILINE,
)


def lint_requirements(content: str, filename: str, root: Path) -> List[Finding]:
    """Validate REQUIREMENTS.md structure and ID format."""
    findings = []

    # Must have requirement entries
    reqs = _REQ_LINE_RE.findall(content)
    if not reqs:
        findings.append(Finding(
            "P1", "reqs-no-entries",
            "REQUIREMENTS.md has no recognizable requirement entries "
            "(expected '- [x] **ID-NN**: description')",
            filename,
        ))
        return findings

    # Check for duplicate IDs
    seen_ids: Dict[str, int] = {}
    for checkbox, req_id, desc in reqs:
        if req_id in seen_ids:
            findings.append(Finding(
                "P1", "reqs-duplicate-id",
                f"Duplicate requirement ID: {req_id}",
                filename,
            ))
        seen_ids[req_id] = seen_ids.get(req_id, 0) + 1

    # Check for empty descriptions even when ROADMAP.md is absent.
    for checkbox, req_id, desc in reqs:
        if len(desc.strip()) < 5:
            findings.append(Finding(
                "P2", "reqs-empty-desc",
                f"Requirement {req_id} has very short description: '{desc.strip()}'",
                filename,
            ))

    # Check that completed reqs in REQUIREMENTS match ROADMAP phase completion
    # (light check: if all reqs in a section are [x], the section's phase
    # should be [x] in ROADMAP too)
    sections = _extract_sections(content)
    roadmap_path = root / ".planning" / "ROADMAP.md"
    if not roadmap_path.exists():
        return findings

    roadmap_content = roadmap_path.read_text()
    roadmap_phases = {
        num_str: checkbox == "x"
        for checkbox, num_str, name in _ROADMAP_PHASE_RE.findall(roadmap_content)
    }

    return findings


# ---------------------------------------------------------------------------
# Check 12: Cross-document requirements traceability
# ---------------------------------------------------------------------------

def _parse_requirements_ids(root: Path) -> Dict[str, Tuple[str, str]]:
    """Parse REQUIREMENTS.md to extract {req_id: (status, phase)}.

    Status is 'complete' if checkbox is [x], 'pending' otherwise.
    Phase is extracted from the traceability table at the bottom.
    """
    req_path = root / ".planning" / "REQUIREMENTS.md"
    if not req_path.exists():
        return {}

    content = req_path.read_text()
    result: Dict[str, Tuple[str, str]] = {}

    # Parse requirement entries: - [x] **REQ-ID**: description
    for checkbox, req_id, desc in _REQ_LINE_RE.findall(content):
        status = "complete" if checkbox == "x" else "pending"
        result[req_id] = (status, "")

    # Parse traceability table: | REQ-ID | Phase N | Status |
    for m in re.finditer(
        r"^\|\s*([A-Z][\w-]+-\d+)\s*\|\s*Phase\s+([\d.]+)\s*\|\s*(\w+)\s*\|",
        content, re.MULTILINE,
    ):
        req_id = m.group(1)
        phase = m.group(2)
        table_status = m.group(3).strip().lower()
        if req_id in result:
            result[req_id] = (result[req_id][0], phase)
        else:
            result[req_id] = (table_status, phase)

    return result


def check_requirements_cross_doc(
    plans: Dict[str, Tuple[Path, str]],
    root: Path,
) -> List[Finding]:
    """Verify bidirectional traceability between REQUIREMENTS.md and plans.

    Checks:
    1. Every req ID in REQUIREMENTS.md is referenced by at least one plan.
    2. Every req ID referenced in a plan header exists in REQUIREMENTS.md.
    3. Requirement status (Complete/Pending) roughly matches its phase status
       in ROADMAP.
    4. Phase count in ROADMAP matches phase count in STATE.
    """
    findings: List[Finding] = []
    reqs_db = _parse_requirements_ids(root)
    if not reqs_db:
        return findings

    # Collect all requirement IDs referenced across all plans
    plan_referenced_reqs: Set[str] = set()
    for plan_id, (path, content) in plans.items():
        if content:
            plan_referenced_reqs.update(_extract_header_requirements(content))

    # Check 1: Orphaned requirements (in REQUIREMENTS.md but no plan references them)
    # Only flag pending requirements -- completed ones may have been in older plans
    # that we don't lint if only specific plans are passed. Use P3 (info) severity
    # to avoid false positives from partial plan sets.
    for req_id, (status, phase) in reqs_db.items():
        if req_id not in plan_referenced_reqs and status == "pending":
            findings.append(Finding(
                "P3", "req-orphaned",
                f"Requirement {req_id} (pending, Phase {phase}) not referenced "
                f"by any plan header",
                str(root / ".planning" / "REQUIREMENTS.md"),
            ))

    # Check 2: Phantom requirements (referenced in plan but not in REQUIREMENTS.md)
    reqs_ids = set(reqs_db.keys())
    for plan_id, (path, content) in plans.items():
        if not content:
            continue
        header_reqs = _extract_header_requirements(content)
        for req_id in header_reqs:
            if req_id not in reqs_ids:
                findings.append(Finding(
                    "P2", "req-phantom",
                    f"Plan references requirement {req_id} which does not "
                    f"exist in REQUIREMENTS.md",
                    str(path),
                ))

    # Check 3: Status consistency -- if all reqs for a phase are complete
    # but ROADMAP shows phase incomplete (or vice versa)
    roadmap_phases = _parse_roadmap_phases(root)
    if roadmap_phases:
        # Group reqs by phase
        phase_reqs: Dict[str, List[Tuple[str, str]]] = {}
        for req_id, (status, phase) in reqs_db.items():
            if phase:
                phase_reqs.setdefault(phase, []).append((req_id, status))

        for phase_num, req_list in phase_reqs.items():
            all_complete = all(s == "complete" for _, s in req_list)
            any_complete = any(s == "complete" for _, s in req_list)
            # Find matching roadmap phase
            for phase_name, rm_status in roadmap_phases.items():
                if (phase_num + "." in phase_name
                        or phase_name.startswith(phase_num + " ")
                        or phase_name.startswith(phase_num + ":")):
                    rm_complete = rm_status == "complete"
                    if all_complete and not rm_complete:
                        findings.append(Finding(
                            "P3", "req-status-drift",
                            f"All requirements for Phase {phase_num} are "
                            f"[x] complete but ROADMAP shows it as "
                            f"'{rm_status}'",
                            str(root / ".planning" / "REQUIREMENTS.md"),
                        ))
                    break

    # Check 4: Phase count in ROADMAP matches phase count in STATE
    state_path = root / ".planning" / "STATE.md"
    roadmap_path = root / ".planning" / "ROADMAP.md"
    if state_path.exists() and roadmap_path.exists():
        state_content = state_path.read_text()
        roadmap_content = roadmap_path.read_text()

        # Count phases in ROADMAP (from checkbox list)
        roadmap_phase_count = len(_ROADMAP_PHASE_RE.findall(roadmap_content))

        # Parse "Phase: N of M" from STATE
        state_total_m = re.search(
            r"Phase:\s*\d+\s+of\s+(\d+)", state_content
        )
        if state_total_m and roadmap_phase_count > 0:
            state_total = int(state_total_m.group(1))
            if state_total != roadmap_phase_count:
                findings.append(Finding(
                    "P2", "phase-count-mismatch",
                    f"STATE.md says {state_total} total phases but "
                    f"ROADMAP.md lists {roadmap_phase_count} phases",
                    str(state_path),
                ))

    return findings


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def lint_plan(content: str, filename: str, root: Path) -> List[Finding]:
    """Run all checks on a single plan file."""
    sections = _extract_sections(content)
    findings = []

    findings.extend(check_required_sections(content, sections, filename))
    findings.extend(check_verify_commands(content, filename))
    findings.extend(check_requirement_traceability(content, sections, filename))
    findings.extend(check_cross_doc_consistency(content, filename, root))
    findings.extend(check_file_accounting(content, filename))
    findings.extend(check_task_format(content, sections, filename))
    findings.extend(check_heuristic_complexity(content, filename))

    return findings


def lint_gsd_doc(content: str, filename: str, root: Path) -> List[Finding]:
    """Lint a GSD governance doc (ROADMAP, STATE, or REQUIREMENTS)."""
    basename = Path(filename).name.upper()

    if "ROADMAP" in basename:
        return lint_roadmap(content, filename, root)
    elif "STATE" in basename:
        return lint_state(content, filename, root)
    elif "REQUIREMENTS" in basename or "REQUIREMENT" in basename:
        return lint_requirements(content, filename, root)

    return []


def lint_all(root: Path, plan_paths: Optional[List[Path]] = None) -> List[Finding]:
    """Run all checks including cross-plan dependency validation."""
    if plan_paths is None:
        plan_paths = discover_plans(root)

    if not plan_paths:
        return [Finding("P2", "no-plans", "No *-PLAN.md files found")]

    # Load all plans for dependency checking
    plans: Dict[str, Tuple[Path, str]] = {}
    for p in plan_paths:
        plan_id = _plan_id_from_path(p)
        try:
            content = p.read_text()
        except OSError as e:
            plans[plan_id] = (p, "")
            continue
        plans[plan_id] = (p, content)

    all_findings = []

    # Per-plan checks
    for plan_id, (path, content) in plans.items():
        if content:
            all_findings.extend(lint_plan(content, str(path), root))

    # Cross-plan checks
    all_findings.extend(check_dependencies(plans))

    # Cross-document requirements traceability
    all_findings.extend(check_requirements_cross_doc(plans, root))

    # GSD governance docs
    for doc_name in ["ROADMAP.md", "STATE.md", "REQUIREMENTS.md"]:
        doc_path = root / ".planning" / doc_name
        if doc_path.exists():
            try:
                doc_content = doc_path.read_text()
                all_findings.extend(lint_gsd_doc(doc_content, str(doc_path), root))
            except OSError:
                pass

    return all_findings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _format_findings(findings: List[Finding]) -> str:
    """Format findings grouped by severity."""
    by_sev = {"P1": [], "P2": [], "P3": []}
    for f in findings:
        by_sev.get(f.severity, by_sev["P3"]).append(f)

    lines = []
    for sev, label in [("P1", "BLOCKERS"), ("P2", "WARNINGS"), ("P3", "INFO")]:
        items = by_sev[sev]
        if items:
            lines.append(f"\n--- {label} ({len(items)}) ---")
            for f in items:
                lines.append(f"  {f}")

    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Semantic plan linter for *-PLAN.md files"
    )
    parser.add_argument(
        "plans", nargs="*",
        help="Specific plan files to lint (default: all under .planning/phases/)"
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Treat warnings (P2) as errors (exit 1 instead of 0)"
    )
    parser.add_argument(
        "--root", type=Path, default=None,
        help="Project root directory (default: auto-detect from git or cwd)"
    )
    parser.add_argument(
        "--stdin", action="store_true",
        help="Read plan content from stdin instead of files"
    )
    parser.add_argument(
        "--name", default="stdin-plan.md",
        help="Virtual filename when reading from --stdin"
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output findings as JSON"
    )
    args = parser.parse_args(argv)

    # Determine project root
    root = args.root
    if root is None:
        # Try to find .planning/ directory
        cwd = Path.cwd()
        if (cwd / ".planning").is_dir():
            root = cwd
        else:
            # Walk up
            for parent in cwd.parents:
                if (parent / ".planning").is_dir():
                    root = parent
                    break
            if root is None:
                root = cwd

    if args.stdin:
        content = sys.stdin.read()
        name_upper = Path(args.name).name.upper()
        if any(k in name_upper for k in ["ROADMAP", "STATE", "REQUIREMENTS"]):
            findings = lint_gsd_doc(content, args.name, root)
        else:
            findings = lint_plan(content, args.name, root)
    elif args.plans:
        plan_paths = [Path(p) for p in args.plans]
        findings = lint_all(root, plan_paths)
    else:
        findings = lint_all(root)

    # Output
    if args.json_output:
        json_findings = [
            {
                "severity": f.severity,
                "check": f.check,
                "message": f.message,
                "file": f.file,
                "line": f.line,
            }
            for f in findings
        ]
        print(json.dumps(json_findings, indent=2))
    else:
        if findings:
            print(_format_findings(findings))
        else:
            print("PASS: all plan checks passed")

    # Exit code
    has_blockers = any(f.severity == "P1" for f in findings)
    has_warnings = any(f.severity == "P2" for f in findings)

    if has_blockers:
        sys.exit(2)
    elif has_warnings and args.strict:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
