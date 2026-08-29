"""Smart Derive Engine for FL-1601 Proof Run Builder.

Cross-references three data sources to auto-compose watchdog_runner.py
CLI arguments, eliminating manual 30-flag composition:

1. analyze_runs.py list --json  → latest run(s), false gates, baseline candidates
2. analyze_failure_log.py required-fields FL-NNN --json  → open FL entries, required fields, gate-to-FL mapping
3. git log  → commits since baseline HEAD, classified as fix-attempt or introduced-by

Architecture laws:
- NEVER import watchdog_runner.py internals — compose CLI args only
- Call analyze_runs.py and analyze_failure_log.py as subprocesses with --json
- Call git log via subprocess.run
- Return DerivedRunIntent consumed by wizard / profiles / TUI
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DIFF_CORPUS_CHOICES = {"gameplay", "watchdog", "launcher", "all"}


@dataclass
class DerivedRunIntent:
    """Fully derived proof run intent — ready to compose into CLI args."""

    baseline_runs: list[str] = field(default_factory=list)
    fl_targets: list[str] = field(default_factory=list)
    fix_attempt_refs: list[str] = field(default_factory=list)
    introduced_by_refs: list[str] = field(default_factory=list)
    required_fields: list[str] = field(default_factory=list)
    suggested_observation: str = ""
    suggested_intent: str = ""
    mode: str = "full"
    target: str = "candidate"
    proof_profile: str = "full_candidate"
    diff_corpus: str = "gameplay"
    diff_mode: str = "latest_relevant"

    # Diagnostics — not CLI args, but useful for TUI display
    recent_runs: list[dict] = field(default_factory=list)
    open_fl_entries: list[dict] = field(default_factory=list)
    pending_commits: list[dict] = field(default_factory=list)
    derive_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_cli_args(self, *, commit_all_and_reset: bool = True) -> list[str]:
        """Compose the full watchdog_runner.py CLI argument list."""
        args = [
            "python3", "scripts/watchdog_runner.py",
            "--mode", self.mode,
            "--target", self.target,
            "--proof-profile", self.proof_profile,
            "--auto-fl",
        ]
        if commit_all_and_reset:
            args.append("--commit-all-and-reset")
        if self.suggested_observation:
            args.extend(["--observation", self.suggested_observation])
        if self.suggested_intent:
            args.extend(["--intent", self.suggested_intent])
        for ref in self.introduced_by_refs:
            args.extend(["--intent-introduced-by", ref])
        for ref in self.fix_attempt_refs:
            args.extend(["--intent-fix-attempt", ref])
        for run_id in self.baseline_runs:
            args.extend(["--intent-baseline-run", run_id])
        for fld in self.required_fields:
            args.extend(["--intent-required-fields", fld])
        if self.diff_corpus:
            args.extend(["--intent-diff-corpus", self.diff_corpus])
        if self.diff_mode:
            args.extend(["--intent-diff-mode", self.diff_mode])
        return args


def _run_subprocess(cmd: list[str], cwd: Path | None = None) -> tuple[str, int]:
    """Run a subprocess and return (stdout, returncode)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=cwd or REPO_ROOT,
        )
        return result.stdout, result.returncode
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        return str(exc), 1


def _python() -> str:
    """Return the Python interpreter path."""
    return sys.executable


def fetch_recent_runs(limit: int = 10) -> tuple[list[dict], list[str]]:
    """Fetch recent runs from analyze_runs.py list --json.

    Returns (runs, errors). If --json is not yet supported, returns empty
    with an error message — the caller should degrade gracefully.
    """
    stdout, rc = _run_subprocess([
        _python(), "scripts/analyze_runs.py", "list",
        "--limit", str(limit), "--json",
    ])
    if rc != 0:
        return [], [f"analyze_runs.py list --json failed (rc={rc}): {stdout[:200]}"]
    try:
        data = json.loads(stdout)
        if isinstance(data, list):
            return data, []
        if isinstance(data, dict) and "runs" in data:
            return data["runs"], []
        return [], [f"unexpected analyze_runs.py JSON shape: {type(data)}"]
    except json.JSONDecodeError as exc:
        return [], [f"analyze_runs.py JSON parse error: {exc}"]


def fetch_fl_ids_for_gates(gate_names: list[str]) -> tuple[list[str], list[str]]:
    """Map false gate names to FL IDs via analyze_failure_log.py by-symbol.

    Uses by-symbol (not by-gate) because by-symbol is lightweight — it only
    loads FL entries, not all runs. Returns (fl_ids, errors).
    Only returns entries that are still open/actionable per ledger state.
    """
    gate_names = [g for g in gate_names if g and g.strip()]
    if not gate_names:
        return [], []
    stdout, rc = _run_subprocess([
        _python(), "scripts/analyze_failure_log.py",
        "by-symbol", *gate_names, "--json",
    ])
    if rc != 0:
        return [], [f"analyze_failure_log.py by-symbol failed (rc={rc}): {stdout[:200]}"]
    try:
        data = json.loads(stdout)
        records = data.get("records", [])
        # Filter to actionable entries only (skip accounted/proven terminal states)
        skip_risks = {"resolved-accounted", "resolved-proven"}
        fl_ids = []
        seen: set[str] = set()
        for rec in records:
            fl_id = rec.get("fl", "")
            risk = rec.get("risk", "")
            if fl_id and fl_id not in seen and risk not in skip_risks:
                fl_ids.append(fl_id)
                seen.add(fl_id)
        return fl_ids, []
    except json.JSONDecodeError as exc:
        return [], [f"by-symbol JSON parse error: {exc}"]


def fetch_fl_required_fields(fl_ids: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    """Fetch required fields for each FL entry.

    Returns ({fl_id: [field, ...]}, errors). If required-fields subcommand
    is not yet supported, returns empty with an error.
    """
    all_fields: dict[str, list[str]] = {}
    errors: list[str] = []
    for fl_id in fl_ids:
        stdout, rc = _run_subprocess([
            _python(), "scripts/analyze_failure_log.py",
            "required-fields", fl_id, "--json",
        ])
        if rc != 0:
            errors.append(f"analyze_failure_log.py required-fields {fl_id} failed (rc={rc})")
            continue
        try:
            data = json.loads(stdout)
            fields = data.get("required_fields", [])
            if fields:
                all_fields[fl_id] = fields
        except json.JSONDecodeError:
            errors.append(f"required-fields {fl_id} JSON parse error")
    return all_fields, errors


def fetch_commits_since(baseline_head: str) -> tuple[list[dict], list[str]]:
    """Fetch commits from baseline HEAD to current HEAD.

    Returns ([{hash, subject, is_fix_attempt}], errors).
    """
    stdout, rc = _run_subprocess([
        "git", "log", "--oneline", "--no-decorate",
        f"{baseline_head}..HEAD",
    ])
    if rc != 0:
        return [], [f"git log {baseline_head}..HEAD failed (rc={rc})"]
    commits = []
    for line in stdout.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split(None, 1)
        if len(parts) >= 2:
            commits.append({
                "hash": parts[0],
                "subject": parts[1],
                "is_fix_attempt": parts[1].startswith("fix(") or parts[1].startswith("fix:"),
            })
        elif parts:
            commits.append({"hash": parts[0], "subject": "", "is_fix_attempt": False})
    return commits, []


def _extract_false_gates(run: dict) -> list[str]:
    """Extract false gate names from a run record."""
    gates = run.get("false_gates")
    if isinstance(gates, list):
        return gates
    return []


def _diff_path_corpora(path: str) -> set[str]:
    normalized = str(path or "").replace("\\", "/").lstrip("./")
    corpora: set[str] = set()
    if (
        normalized.startswith("engine/")
        or normalized.startswith("server/")
        or normalized.startswith("web/")
        or (
            normalized.startswith("scripts/multiplayer")
            and normalized != "scripts/multiplayer_visual_watchdog.js"
        )
    ):
        corpora.add("gameplay")
    if (
        normalized.startswith("scripts/watchdog_")
        or normalized in {
            "scripts/watchdog_source.py",
            "scripts/analyze_runs.py",
            "scripts/analyze_failure_log.py",
            "scripts/multiplayer_visual_watchdog.js",
            "scripts/simplified_watchdog_vps_launcher.py",
        }
    ):
        corpora.add("watchdog")
    if (
        normalized == "scripts/launcher.py"
        or normalized.startswith("scripts/launcher_lib/")
        or normalized.startswith("scripts/launcher_ui/")
    ):
        corpora.add("launcher")
    return corpora


def _commit_matches_corpus(commit_ref: str, corpus: str) -> bool:
    if corpus not in DIFF_CORPUS_CHOICES or corpus == "all":
        return True
    stdout, rc = _run_subprocess(["git", "show", "--name-only", "--format=", commit_ref])
    if rc != 0:
        return False
    for line in stdout.splitlines():
        if corpus in _diff_path_corpora(line.strip()):
            return True
    return False


def _classify_commits(
    commits: list[dict],
    fl_targets: list[str],
) -> tuple[list[str], list[str]]:
    """Classify commits as fix-attempt or introduced-by.

    Heuristic: commits whose subject contains 'fix(' or 'fix:' or references
    any FL target are fix-attempts. Everything else is introduced-by.
    """
    fix_attempts = []
    introduced_by = []
    fl_lower = {fl.lower() for fl in fl_targets}
    for commit in commits:
        subj = commit.get("subject", "").lower()
        is_fix = commit.get("is_fix_attempt", False)
        # Check if subject references any FL target
        if not is_fix:
            for fl in fl_lower:
                if fl.replace("-", "").lower() in subj.replace("-", "").lower():
                    is_fix = True
                    break
        if is_fix:
            fix_attempts.append(commit["hash"])
        else:
            introduced_by.append(commit["hash"])
    return fix_attempts, introduced_by


def derive(
    baseline_run_ids: list[str] | None = None,
    fl_targets: list[str] | None = None,
    mode: str = "full",
    target: str = "candidate",
    proof_profile: str = "full_candidate",
    diff_corpus: str = "gameplay",
    on_progress: "Callable[[int, int, str], None] | None" = None,
) -> DerivedRunIntent:
    """Auto-derive a proof run intent from current repo and run state.

    If baseline_run_ids is None, uses the latest run(s) with false gates.
    If fl_targets is None, derives from the baseline's false gates.
    on_progress(step, total, label) is called at each stage for UI feedback.
    """
    _progress = on_progress or (lambda s, t, m: None)
    chosen_diff_corpus = diff_corpus if diff_corpus in DIFF_CORPUS_CHOICES else "gameplay"
    intent = DerivedRunIntent(
        mode=mode,
        target=target,
        proof_profile=proof_profile,
        diff_corpus=chosen_diff_corpus,
        diff_mode="between_runs" if chosen_diff_corpus == "all" else "latest_relevant",
    )

    # Step 1: Fetch recent runs
    _progress(1, 5, "Loading recent runs")
    runs, run_errors = fetch_recent_runs()
    intent.derive_errors.extend(run_errors)
    intent.recent_runs = runs

    # Step 2: Select baseline runs
    if baseline_run_ids:
        intent.baseline_runs = list(baseline_run_ids)
    else:
        # Auto-select: most recent runs with false gates
        for run in runs:
            false_gates = _extract_false_gates(run)
            if false_gates:
                head_ref = str(run.get("head") or run.get("git_head") or "").strip()
                if head_ref and not _commit_matches_corpus(head_ref, intent.diff_corpus):
                    continue
                run_id = run.get("run_id") or run.get("id") or run.get("label", "")
                if run_id:
                    intent.baseline_runs.append(run_id)
                    if len(intent.baseline_runs) >= 2:
                        break
        if not intent.baseline_runs:
            intent.derive_errors.append("no baseline runs with false gates found")

    # Step 3: Derive FL targets from false gates
    _progress(2, 5, "Mapping gates to FL entries")
    if fl_targets:
        intent.fl_targets = list(fl_targets)
    else:
        # Collect false gates from baseline runs
        all_false_gates: set[str] = set()
        for run in runs:
            run_id = run.get("run_id") or run.get("id") or run.get("label", "")
            if run_id in intent.baseline_runs:
                all_false_gates.update(_extract_false_gates(run))
        # Cross-reference false gates with open FL entries via by-symbol
        if all_false_gates:
            fl_ids, gate_errors = fetch_fl_ids_for_gates(sorted(all_false_gates))
            intent.derive_errors.extend(gate_errors)
            if fl_ids:
                intent.fl_targets = fl_ids
            else:
                intent.derive_errors.append(
                    f"auto-derived {len(all_false_gates)} false gates but no open FL entries "
                    "cite them — the wizard will let the operator enter FL IDs manually"
                )

    # Step 4: Fetch required fields from FL targets
    _progress(3, 5, "Collecting required fields")
    if intent.fl_targets:
        fl_fields, fl_errors = fetch_fl_required_fields(intent.fl_targets)
        intent.derive_errors.extend(fl_errors)
        # Flatten all required fields, deduplicated
        seen: set[str] = set()
        for fields in fl_fields.values():
            for f in fields:
                if f not in seen:
                    intent.required_fields.append(f)
                    seen.add(f)
        intent.open_fl_entries = [
            {"fl_id": fl_id, "required_fields": fields}
            for fl_id, fields in fl_fields.items()
        ]

    # Step 5: Fetch commits since baseline HEAD
    _progress(4, 5, "Classifying commits")
    if intent.baseline_runs:
        # Use the first baseline run's HEAD if available
        baseline_head = None
        for run in runs:
            run_id = run.get("run_id") or run.get("id") or run.get("label", "")
            if run_id == intent.baseline_runs[0]:
                baseline_head = run.get("head") or run.get("git_head")
                break
        if baseline_head:
            commits, commit_errors = fetch_commits_since(baseline_head)
            intent.derive_errors.extend(commit_errors)
            intent.pending_commits = commits
            # Classify
            fix_attempts, introduced_by = _classify_commits(commits, intent.fl_targets)
            intent.fix_attempt_refs = fix_attempts
            intent.introduced_by_refs = introduced_by
        else:
            intent.derive_errors.append(
                f"baseline run {intent.baseline_runs[0]} has no HEAD ref; cannot fetch commits"
            )

    # Step 6: Compose observation and intent text
    _progress(5, 5, "Composing intent")
    if intent.fl_targets:
        intent.suggested_intent = (
            f"Verify fix attempts against {', '.join(intent.fl_targets)}: "
            f"targeted gates must hold while non-targeted diagnostics are treated as separate lanes. "
            f"Show the latest {intent.diff_corpus} diff in post-run analysis."
        )
    if intent.baseline_runs:
        intent.suggested_observation = (
            f"Rerun after baseline {', '.join(intent.baseline_runs)} "
            f"with {len(intent.fix_attempt_refs)} fix-attempt commit(s) "
            f"and {intent.diff_corpus} diff scope."
        )

    return intent
