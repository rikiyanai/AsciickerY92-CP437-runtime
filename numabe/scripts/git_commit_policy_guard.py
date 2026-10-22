#!/usr/bin/env python3
"""Local commit-policy guard for the non-LFS rebuilt repo."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_ARCHIVE = "scripts/launcher_helper_bot/index/chroma.tar.gz"
BANNED_FL4359_STATIONARY_ANALYZER = "scripts/analyze_fl4359_jitter.py"
LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"
AI_IDENTITY_RE = re.compile(
	r"(?<![A-Za-z0-9_])(?:claude|anthropic|codex|openai|chatgpt|copilot|cursor|devin|gemini|agent)(?![A-Za-z0-9_])",
	re.IGNORECASE,
)
AI_TRAILER_RE = re.compile(
	r"^(?:\s*(?:(?:co-)?authored-by|author|committer)\s*:.*(?:claude|anthropic|codex|openai|chatgpt|copilot|cursor|devin|gemini|agent)|\s*(?:claude|codex|agent|cursor|devin|gemini)(?:-session)?\s*:)",
	re.IGNORECASE | re.MULTILINE,
)
BANNED_CHOICE_TOKEN = chr(111) + chr(114)
BANNED_CHOICE_TOKEN_RE = re.compile(rf"(?<![A-Za-z0-9_]){BANNED_CHOICE_TOKEN}(?![A-Za-z0-9_])", re.IGNORECASE)
# Planning-surface governance (operator correction, 2026-09-13).
#
# Product truth flows through exactly three canonical surfaces:
#     docs/plans/2026-03-22-multiplayer-canonical-spec.md  requirements (RQ rows)
#     docs/FAILURE_LOG.md + docs/FAILURE_LOG.overlay/      operational ownership (FL)
#     docs/plans/GODOT_GOAL.MD                             ordered execution projection
#
# Every other tracked roadmap-shaped Markdown file is authority drift: one agent
# writes a plan, the next reads GODOT_GOAL, a third reads the FL log, and three
# different sequences now exist. This guard rejects NEWLY ADDED roadmap-shaped
# Markdown unless the commit explicitly registers why a new canonical surface is
# necessary, via a New-Canonical-Surface: <reason> trailer.
#
# Scope note: this fires on added files only. Existing dated design records stay
# where they are; the goal is to stop the population from growing, not to
# relitigate history.
PLANNING_SURFACE_TOKEN_RE = re.compile(
	r"(?<![a-z0-9])(plan|plans|roadmap|handoff|handoffs|todo|todos|next[_-]?steps"
	r"|mvp|backlog|phases?|goal|goals|strategy|proposal)(?![a-z0-9])",
	re.IGNORECASE,
)
# This repo names roadmap surfaces <THING>GOAL.MD (BLOCKGOAL.MD, GODOT_GOAL.MD),
# so the compound form is the local drift pattern and a standalone-token rule
# alone would miss it.
PLANNING_SURFACE_COMPOUND_RE = re.compile(r"goal[s]?\.(md|markdown)$", re.IGNORECASE)
PLANNING_SURFACE_SUFFIXES = (".md", ".markdown")
CANONICAL_PLANNING_PATHS = frozenset(
	{
		"docs/plans/2026-03-22-multiplayer-canonical-spec.md",
		"docs/plans/GODOT_GOAL.MD",
		"docs/FAILURE_LOG.md",
		"AGENTS.md",
		"CLAUDE.md",
		"README.md",
	}
)
# Evidence and receipts are not authority and are never roadmap surfaces.
# research/scratchpad/ is owned by the operator-approved Stop-hook
# auto-preserver (FL-0035/FL-0036 research losses): scratch by path, never
# read as authority, so a scratch file named next-steps.md or plan.md must
# not trip the canonical-surface rule and break the preservation commit.
PLANNING_EXEMPT_PREFIXES = (
	"docs/research/",
	"research/scratchpad/",
	"artifacts/",
	"articles/",
	".run/",
	"docs/agent/",
)
PLANNING_EXEMPT_SUBSTRINGS = ("node_modules/",)
# The tool-call hook (scripts/maintainer/hooks/md_file_ban_hook.py) refuses to
# CREATE a stray .md/.txt; this is the same policy applied to what actually
# lands in a commit. Two doors, one rule — a shell redirect, an editor, or a
# host with no hooks at all still has to pass here. Parity between the two
# lists is pinned by test_stray_doc_policy_matches_the_md_ban_hook.
# Imported, not restated. Two hand-maintained copies of this rule drifted on
# ".markdown" and the parity test missed it (FL-4853).
_HOOKS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "maintainer", "hooks")


def _policy():
	"""Import the shared policy on demand.

	Lazy on purpose: the FL-4512 paper-index pre-commit stage runs this suite
	inside a sparse checkout that carries scripts/tests but not the hook tree,
	and a module-level import there breaks collection of unrelated tests. At
	commit time the tree is whole, and a missing module raises rather than
	silently degrading to a weaker rule.
	"""
	if _HOOKS_DIR not in sys.path:
		sys.path.insert(0, _HOOKS_DIR)
	import doc_placement_policy

	return doc_placement_policy
NEW_CANONICAL_SURFACE_RE = re.compile(
	r"^New-Canonical-Surface:[ \t]*\S.*$", re.IGNORECASE | re.MULTILINE
)

LFS_CONFIG_PATTERNS = (
	"filter=lfs",
	"diff=lfs",
	"merge=lfs",
	"git-lfs",
	"git lfs",
)
FL4512_PAPER_CONTENT_PREFIXES = (
	"docs/agent/glyph-rendering-paper/",
	"docs/research/glyph-rendering/",
)
FL4512_PAPER_TOOL_PATHS = {
	".github/workflows/fl4512-paper.yml",
	".githooks/pre-commit",
	".githooks/commit-msg",
	".githooks/pre-push",
	"scripts/git_commit_policy_guard.py",
	"scripts/tests/test_git_commit_policy_guard.py",
	"scripts/tests/test_fl4512_paper_contract.py",
	"scripts/tests/test_fl4512_paper_contracts.py",
	"scripts/validate_fl4512_paper_lane.py",
	"docs/agent/glyph-rendering-paper/scripts/build_versioned_paper.py",
	"docs/agent/glyph-rendering-paper/scripts/validate_fl4512_paper_lane.py",
}
FL4512_PAPER_DEPENDENCY_PATHS = {
	"docs/plans/2026-03-22-multiplayer-canonical-spec.md",
	"docs/research/ascii/verification/fl4260/CURRENT_UI_INVENTORY.csv",
	"docs/research/ascii/verification/fl4512/decisions/2026-07-11-remaining-v1/receipt.json",
	"docs/research/ascii/verification/fl4512/decisions/2026-07-11-execution-lock-validation-v1/audit.json",
}
FL4512_PAPER_DEPENDENCY_PREFIXES = (
	"docs/research/ascii/verification/fl4512/",
	"docs/research/ascii/verification/fl4208/",
	"docs/research/ascii/verification/fl4260/",
	"docs/research/ascii/verification/fl4547/",
	"docs/research/ascii/verification/fl4548/",
	"docs/research/ascii/verification/fl4652/",
	"assets/glyphs/",
)
FL4512_PAPER_VALIDATOR = (
	"docs/agent/glyph-rendering-paper/scripts/validate_fl4512_paper_lane.py"
)
# Commit-tree lanes do not get a second chance to route ownership through a
# human review.  Keep the narrow path-to-FL boundary here so a renderer or
# feature-registry commit cannot cite the unrelated FL-4512 research lane.
# Prefixes are intentionally explicit: the render-lane helper remains the
# source of truth for which individual files are FF/non-FF, while this map
# answers the separate question "which FL owns this source family?".
PATH_FL_OWNERS: tuple[tuple[str, tuple[str, ...]], ...] = (
	("godot_project/render/ascii/", ("FL-4877",)),
	("config/godot_feature_regressions.json", ("FL-4781",)),
)
FL4512_SNAPSHOT_TOKEN = "{FL4512_PAPER_INDEX_ROOT}"
FL4512_PAPER_SELF_TESTS = (
	"scripts/tests/test_git_commit_policy_guard.py",
	"scripts/tests/test_fl4512_paper_contracts.py::test_prose_hygiene_flags_status_dashboard_and_source_line_audit_language",
	"scripts/tests/test_fl4512_paper_contracts.py::test_versioned_builder_owns_isolated_latex_sidecars",
	"scripts/tests/test_fl4512_paper_contracts.py::test_root_fl4512_paper_validator_cli_delegates_to_canonical_gate",
)
FL4359_STATIONARY_JITTER_MARKERS = (
	"stationary" + "_snap",
	"stationary residual " + "gate",
	"evidence_fl4359_stationary" + "_snap_sanity",
)
UPSTREAM_REMOTE_NAME = "upstream"
UPSTREAM_REPOSITORY_RE = re.compile(
	r"(?:github\.com[:/])msokalski/asciicker(?:\.git)?/?$",
	re.IGNORECASE,
)


def _git(*args: str, text: bool = True) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=text,
    )


def staged_paths() -> list[str]:
    proc = _git("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMRD")
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip() or "git diff --cached failed")
    return [path for path in proc.stdout.split("\0") if path]


def staged_blob_text(path: str) -> str:
    proc = _git("show", f":{path}")
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip() or f"git show :{path} failed")
    return proc.stdout


def tracked_paths_under(prefix: str) -> list[str]:
	pathspec = prefix if prefix else "."
	proc = _git("ls-files", "-z", pathspec)
	if proc.returncode != 0:
		raise RuntimeError((proc.stderr or proc.stdout).strip() or f"git ls-files {pathspec} failed")
	return [path for path in proc.stdout.split("\0") if path]


def safe_staged_text(path: str) -> str:
	try:
		return staged_blob_text(path)
	except RuntimeError:
		try:
			return (REPO_ROOT / path).read_text(encoding="utf-8")
		except (OSError, UnicodeDecodeError):
			return ""


def add_json_path_refs(paths: set[str], text: str) -> None:
	try:
		payload = json.loads(text)
	except json.JSONDecodeError:
		return

	def visit(value) -> None:
		if isinstance(value, dict):
			path = value.get("path")
			if isinstance(path, str):
				paths.add(path)
			reader_output = value.get("reader_output")
			if isinstance(reader_output, str):
				paths.add(reader_output)
			for nested in value.values():
				visit(nested)
		elif isinstance(value, list):
			for nested in value:
				visit(nested)
		elif isinstance(value, str) and re.match(r"^(?:docs|assets|engine|platform|scripts)/", value):
			paths.add(value)

	visit(payload)


def add_text_path_refs(paths: set[str], text: str) -> None:
	for rel in re.findall(
		r"((?:docs|assets)/[A-Za-z0-9_./-]*(?:receipt|manifest)[A-Za-z0-9_./-]*\.(?:json|jsonl))",
		text,
	):
		paths.add(rel.rstrip(".,;)`'\""))


def fl4512_required_snapshot_paths(staged: list[str], blob_loader=safe_staged_text) -> list[str]:
	paths: set[str] = {
		"pyproject.toml",
		"docs/agent/glyph-rendering-paper/2026-07-07-fl4512-first-rough-draft.tex",
		"docs/agent/glyph-rendering-paper/PAPER_ARTIFACT_INDEX.md",
		"docs/agent/glyph-rendering-paper/README.md",
		"docs/agent/glyph-rendering-paper/references.bib",
		"docs/agent/glyph-rendering-paper/2026-07-07-fl4512-first-rough-draft.bbl",
		"docs/agent/glyph-rendering-paper/figure_dispositions.v1.json",
		"docs/agent/glyph-rendering-paper/2026-06-30-fl4512-source-accurate-rendering-pipeline-callgraph.md",
		"docs/research/glyph-rendering/README.md",
		"docs/research/glyph-rendering/references.bib",
		"docs/plans/2026-03-22-multiplayer-canonical-spec.md",
		"docs/research/ascii/verification/fl4260/CURRENT_UI_INVENTORY.csv",
		"docs/research/ascii/verification/fl4512/decisions/2026-07-11-remaining-v1/receipt.json",
		"docs/research/ascii/verification/fl4512/decisions/2026-07-11-execution-lock-validation-v1/audit.json",
		# The paper artifact index names this diagnostic Pietà panel and its
		# provenance sidecar directly. They are not receipt/manifest JSON paths,
		# so add_text_path_refs cannot discover them from the index prose. Keep
		# both tracked inputs in every paper snapshot so INDEX_SNAPSHOT_MODE sees
		# the same diagnostic context as the live repository.
		"docs/research/ascii/verification/fl4260/fl4548/2026-07-13-pieta-edge-yaw-observation/review-panel-paired-v3.png",
		"docs/research/ascii/verification/fl4260/fl4548/2026-07-13-pieta-edge-yaw-observation/review-panel-paired-v3.provenance.json",
		"scripts/fl4512_projected_scale.py",
		"scripts/fl4512_projected_feature_extent.py",
		"scripts/__init__.py",
	}
	paths.update(FL4512_PAPER_TOOL_PATHS)
	paths.update(path for path in staged if fl4512_paper_gate_path(path))
	paths.update(tracked_paths_under("docs/agent/glyph-rendering-paper/figures"))
	paths.update(tracked_paths_under("docs/agent/glyph-rendering-paper/figure_provenance"))
	paths.update(tracked_paths_under("docs/research/glyph-rendering"))
	paths.update(tracked_paths_under("docs/agent/glyph-rendering-paper/scripts"))
	paths.update(
		path
		for path in tracked_paths_under("docs/agent/glyph-rendering-paper")
		if "/builds/" not in path
	)

	for rel in list(paths):
		if rel.endswith(".json"):
			add_json_path_refs(paths, blob_loader(rel))
		if rel.endswith((".tex", ".md")):
			add_text_path_refs(paths, blob_loader(rel))
	for rel in list(paths):
		if rel.startswith("docs/agent/glyph-rendering-paper/figure_provenance/") and rel.endswith(".json"):
			add_json_path_refs(paths, blob_loader(rel))
	for rel in list(paths):
		if rel.startswith("docs/agent/glyph-rendering-paper/figure_dispositions.v1.json"):
			add_json_path_refs(paths, blob_loader(rel))
	tracked = set(tracked_paths_under(""))
	return sorted(path for path in paths if path and not path.endswith("/") and path in tracked)


def fl4512_paper_gate_path(path: str) -> bool:
	return (
		path in FL4512_PAPER_TOOL_PATHS
		or path in FL4512_PAPER_DEPENDENCY_PATHS
		or any(path.startswith(prefix) for prefix in FL4512_PAPER_CONTENT_PREFIXES)
		or any(path.startswith(prefix) for prefix in FL4512_PAPER_DEPENDENCY_PREFIXES)
	)


def fl4512_paper_content_path(path: str) -> bool:
	"""Is this staged file part of the paper ITSELF, as opposed to something the
	paper merely reads?

	This distinction decides whether editing a file starts the whole paper
	validation run, so it has to be drawn in exactly the right place.

	CONTENT is the paper: its manuscript prose, its figures, and the provenance
	records that pin those figures. Editing any of them can genuinely invalidate
	the paper, so they must be validated.

	A DEPENDENCY is something the paper quotes or points at but does not own:
	the canonical specification, the compiled glyph assets, engine sources cited
	by a figure, and runtime evidence receipts. These are INPUTS. They need to be
	present in the snapshot whenever validation runs, so the validator sees a
	complete picture, but touching one is not itself a reason to validate the
	paper.

	REPAIRED BOUNDARY (FL-4716, 2026-08-09). This predicate used to answer "yes"
	for the dependency lists as well, which made a dependency indistinguishable
	from manuscript prose. The consequence was that a commit changing one line of
	the canonical specification, or adding an offline measurement under
	assets/glyphs/, had to satisfy the entire paper hygiene gate. When that gate
	is red for reasons those commits cannot influence - stale figure provenance
	pinned against engine sources they never touch, or a build record hash that
	only rebuilding the document clears - such work cannot land at all, no matter
	how correct it is.

	That was never the declared intent. The TRIGGER BOUNDARY note in
	fl4512_paper_gate_error already states the rule plainly: only a paper-owned
	staged path may start paper validation. FL-4665 applied that rule to
	dynamically discovered engine sources; the static dependency lists were left
	behind inside this predicate. The module keeps two separate sets of constants
	for two genuinely different ideas, and this was the one place that collapsed
	them. Now it does not.

	Dependencies are still reached through fl4512_paper_gate_path, so they keep
	their place in the snapshot; they simply no longer pull the trigger.
	"""
	# Paper TOOLING (the validator, the builder, this guard, their tests) is
	# handled by its own branch, which runs the paper self-tests rather than the
	# document validator. It must not be classified as content here.
	if path in FL4512_PAPER_TOOL_PATHS:
		return False
	# Scripts living inside the paper directory are tooling too, not prose.
	if path.startswith("docs/agent/glyph-rendering-paper/scripts/"):
		return False
	# What remains: only the paper's own content prefixes.
	return any(path.startswith(prefix) for prefix in FL4512_PAPER_CONTENT_PREFIXES)


def fl4512_paper_tool_path(path: str) -> bool:
	return path in FL4512_PAPER_TOOL_PATHS


def export_git_index_snapshot(snapshot_root: Path, paths: list[str]) -> None:
	proc = subprocess.run(
		["git", "checkout-index", "--force", f"--prefix={snapshot_root}/", "--stdin"],
		cwd=REPO_ROOT,
		check=False,
		input="\n".join(paths) + "\n",
		capture_output=True,
		text=True,
	)
	if proc.returncode != 0:
		raise RuntimeError((proc.stderr or proc.stdout).strip() or "git checkout-index failed")


def run_fl4512_snapshot_command(
	args: list[str],
	*,
	command_runner=subprocess.run,
	snapshot_builder=export_git_index_snapshot,
	required_paths: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
	with tempfile.TemporaryDirectory(prefix="fl4512-paper-index-") as temp_dir:
		snapshot_root = Path(temp_dir) / "repo"
		snapshot_root.mkdir()
		snapshot_builder(snapshot_root, required_paths or [])
		env = os.environ.copy()
		env["FL4512_PAPER_REPO_ROOT"] = str(snapshot_root)
		env["PYTHONPATH"] = str(snapshot_root)
		snapshot_args = [arg.replace(FL4512_SNAPSHOT_TOKEN, str(snapshot_root)) for arg in args]
		return command_runner(
			snapshot_args,
			cwd=snapshot_root,
			env=env,
			check=False,
			capture_output=True,
			text=True,
		)


def fl4512_paper_gate_error(
	paths: list[str],
	*,
	command_runner=subprocess.run,
	snapshot_builder=export_git_index_snapshot,
) -> str | None:
	required_paths = fl4512_required_snapshot_paths(paths)
	# TRIGGER BOUNDARY (FL-4665, 2026-07-31).
	#
	# Only a PAPER-OWNED staged path may start paper validation. `required_paths`
	# is a much wider set: it is discovered dynamically by reading the figure
	# provenance files and collecting every input artifact they cite — which
	# includes ordinary engine sources such as platform/terminal_gl_present.cpp,
	# engine/render/render_resolve.cpp, and engine/game_render_bridge.cpp.
	#
	# Treating that discovered set as a TRIGGER made any engine commit run the
	# whole paper validator, so unrelated renderer work was blocked by the paper
	# lane's unpublished provenance (stale figure hashes, a missing receipt, an
	# untracked figure input). That is the wrong owner: the builder should stay
	# red until the figures are refreshed by the paper lane, but engine commits
	# must not inherit that red.
	#
	# `required_paths` is still passed to the snapshot below, so once a paper
	# path legitimately triggers validation the validator sees every input it
	# needs. Discovery belongs in the snapshot, not in the trigger.
	if not any(fl4512_paper_gate_path(path) for path in paths):
		return None
	# COMPLETING THE TRIGGER BOUNDARY (FL-4716, 2026-08-09).
	#
	# The note above concludes: "Discovery belongs in the snapshot, not in the
	# trigger." The code did not fully follow it. Alongside the content test
	# there used to be a second condition - `path in required_paths and not
	# fl4512_paper_tool_path(path)` - which put the dynamically discovered set
	# straight back into the trigger for everything that was not paper tooling.
	# FL-4665 removed engine sources from the trigger by excluding tool paths,
	# but the canonical specification, the compiled glyph assets, and runtime
	# evidence receipts are all discovered inputs too, so they kept starting
	# full paper validation.
	#
	# The rule is now applied without exception: paper CONTENT starts paper
	# validation, and nothing else does. `required_paths` is still computed and
	# still handed to the snapshot below, so whenever validation does run the
	# validator sees every input it needs.
	if any(fl4512_paper_content_path(path) for path in paths):
		result = run_fl4512_snapshot_command(
			[sys.executable, f"{FL4512_SNAPSHOT_TOKEN}/{FL4512_PAPER_VALIDATOR}", "--prebuild-source"],
			command_runner=command_runner,
			snapshot_builder=snapshot_builder,
			required_paths=required_paths,
		)
		if result.returncode != 0:
			output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
			return (
				"GIT GUARD BLOCKED: FL-4512 paper staged source gate failed. "
				"Run python3 "
				+ FL4512_PAPER_VALIDATOR
				+ " --prebuild-source.\n"
				+ output
			)
	if any(fl4512_paper_tool_path(path) for path in paths):
		result = run_fl4512_snapshot_command(
			[sys.executable, "-m", "pytest", "-q", *FL4512_PAPER_SELF_TESTS],
			command_runner=command_runner,
			snapshot_builder=snapshot_builder,
			required_paths=required_paths,
		)
		if result.returncode != 0:
			output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
			return "GIT GUARD BLOCKED: FL-4512 paper enforcement self-tests failed.\n" + output
	return None


def pre_commit_error(
	paths: list[str] | None = None,
	blob_loader=staged_blob_text,
	command_runner=subprocess.run,
	snapshot_builder=export_git_index_snapshot,
) -> str | None:
    staged = paths if paths is not None else staged_paths()
    for path in staged:
        if path == FORBIDDEN_ARCHIVE:
            return (
                "GIT GUARD BLOCKED: scripts/launcher_helper_bot/index/chroma.tar.gz must not be recommitted. "
                "Keep helper-bot archives local and move to the documented chunked lane after cutover."
            )
        if path == BANNED_FL4359_STATIONARY_ANALYZER:
            return (
                "GIT GUARD BLOCKED: scripts/analyze_fl4359_jitter.py is banned. "
                "FL-4359 accepts moving-camera evidence only."
            )
        try:
            blob_text = blob_loader(path)
        except (RuntimeError, UnicodeDecodeError):
            continue
        if path.startswith("scripts/") and any(marker in blob_text for marker in FL4359_STATIONARY_JITTER_MARKERS):
            return (
                f"GIT GUARD BLOCKED: stationary FL-4359 jitter evidence is banned in {path}. "
                "Use moving-camera evidence only."
            )
        preview = "\n".join(blob_text.splitlines()[:5])
        # Git LFS plumbing vector, closed 2026-09-15. The check below was gated
        # on an exact top-level ".gitattributes", so two routes stayed open and
        # one of them was actually used: `git lfs install` wrote its stubs into
        # .githooks (because core.hooksPath already pointed there) and commit
        # f782f22b6 tracked them, after which every clone ran `git lfs pre-push`
        # on every push for roughly seven weeks. A nested foo/.gitattributes
        # carrying filter=lfs would have passed just as cleanly. Scope stays
        # narrow on PURPOSE: hook files, any-depth .gitattributes, and
        # .lfsconfig. Prose that merely names LFS -- this guard, the failure
        # log, docs recording the ban -- must remain committable.
        basename = path.rsplit("/", 1)[-1]
        # .lfsconfig is banned by PATH, not by content: a valid one can name an
        # endpoint without containing any of the patterns below. Caught in
        # testing, which is the only reason this branch exists.
        if basename == ".lfsconfig":
            return (
                f"GIT GUARD BLOCKED: Git LFS plumbing is forbidden ({path}). "
                "Repository policy bans Git LFS; .lfsconfig must never be committed."
            )
        lfs_plumbing_path = (
            path.startswith(".githooks/")
            or path.startswith(".git/hooks/")
            or basename == ".gitattributes"
        )
        if lfs_plumbing_path and any(
            pattern in blob_text for pattern in LFS_CONFIG_PATTERNS + ("git lfs",)
        ):
            return (
                f"GIT GUARD BLOCKED: Git LFS plumbing is forbidden ({path}). "
                "Repository policy bans Git LFS. If `git lfs install` wrote a "
                "hook, delete it; do not commit it and do not bypass this hook "
                "with a temporary core.hooksPath."
            )
        if preview.startswith(LFS_POINTER_PREFIX):
            return f"GIT GUARD BLOCKED: LFS pointer blobs are forbidden ({path})."
    paper_error = fl4512_paper_gate_error(
        staged,
        command_runner=command_runner,
        snapshot_builder=snapshot_builder,
    )
    if paper_error:
        return paper_error
    return None


class GitQueryError(RuntimeError):
	"""The guard could not read the staged index."""


def added_paths() -> list[str]:
	"""Newly ADDED staged paths.

	Raises instead of returning [] on git failure: an empty list reads as
	"nothing to check" and waves the commit through, which is the wrong answer
	when the truth is "we do not know" (FL-4853).
	"""
	proc = _git("diff", "--cached", "--name-only", "-z", "--diff-filter=AR")
	if proc.returncode != 0:
		raise GitQueryError((proc.stderr or "").strip() or "git diff --cached failed")
	return [path for path in proc.stdout.split("\0") if path]


def _is_stray_doc(path: str) -> bool:
	"""True when the placement policy forbids CREATING this path."""
	return _policy().is_forbidden_new_doc(path)


def planning_surface_paths(paths: list[str]) -> list[str]:
	"""Return roadmap-shaped Markdown paths that are not a canonical surface."""
	flagged: list[str] = []
	for path in paths:
		# FL-4853/FL-4855: evidence exemptions cannot bypass the shared
		# creation predicate. The name-based roadmap rule is additional.
		if _is_stray_doc(path):
			flagged.append(path)
			continue
		if path in CANONICAL_PLANNING_PATHS:
			continue
		if path.startswith(PLANNING_EXEMPT_PREFIXES):
			continue
		if any(part in path for part in PLANNING_EXEMPT_SUBSTRINGS):
			continue
		if not _policy().is_prose_surface(path):
			continue
		# Two independent reasons to flag:
		#   (a) roadmap-shaped name — matched on the filename AND on any
		#       directory segment, so steam-mvp-plan.md and
		#       recovered-agent-plans/NOTES.md are both caught;
		#   (b) any new doc the placement policy forbids, whatever it is
		#       called. Generic names (.xx/AGENTS.md, notes.md) used to walk
		#       straight past the name rule (FL-4853).
		if (
			PLANNING_SURFACE_TOKEN_RE.search(path)
			or PLANNING_SURFACE_COMPOUND_RE.search(path)
			or _is_stray_doc(path)
		):
			flagged.append(path)
	return flagged


def planning_surface_error(
	paths: list[str] | None = None, message_text: str = ""
) -> str | None:
	try:
		candidates = paths if paths is not None else added_paths()
	except GitQueryError as exc:
		return (
			"GIT GUARD BLOCKED: could not read the staged index to check new doc "
			f"placement ({exc}).\nRefusing to guess. Fix the git error and retry."
		)
	flagged = planning_surface_paths(candidates)
	if not flagged:
		return None
	if NEW_CANONICAL_SURFACE_RE.search(message_text):
		return None
	listed = "\n  ".join(flagged)
	return (
		"GIT GUARD BLOCKED: new documentation surface(s) are not allowed here:\n  "
		+ listed
		+ "\n\nProduct truth has exactly three canonical surfaces:\n"
		"  docs/plans/2026-03-22-multiplayer-canonical-spec.md  requirements (RQ rows)\n"
		"  docs/FAILURE_LOG.md + docs/FAILURE_LOG.overlay/      operational ownership (FL)\n"
		"  docs/plans/GODOT_GOAL.MD                             ordered execution projection\n\n"
		"Route the content into those instead: new product requirements become RQ rows, "
		"actionable gaps and proof debt become FL entries, ordering becomes a GODOT_GOAL "
		"phase. Supporting evidence may live under docs/research/ or artifacts/. "
		"Scratch planning stays untracked and is never repo authority.\n\n"
		"If a new canonical surface is genuinely necessary, register why with a trailer:\n"
		"  New-Canonical-Surface: <reason this cannot be an RQ row, FL entry or GODOT_GOAL phase>"
	)


def path_fl_owner_error(
	paths: list[str] | None = None, message_text: str = ""
) -> str | None:
	"""Require the owning FL id for protected source families.

	This runs at commit-msg as well as through the temporary-index commit lane.
	The path map is deliberately small and exact; unlisted paths continue to use
	the ordinary FL routing contract.
	"""
	candidates = paths if paths is not None else staged_paths()
	ids = set(re.findall(r"\bFL-\d+\b", message_text, flags=re.IGNORECASE))
	ids = {value.upper() for value in ids}
	for path in candidates:
		for prefix, owners in PATH_FL_OWNERS:
			if path == prefix or path.startswith(prefix):
				missing = [owner for owner in owners if owner not in ids]
				if missing:
					return (
						"GIT GUARD BLOCKED: staged path "
						f"{path} requires owning FL trailer(s): {', '.join(missing)}."
					)
	return None


def commit_identity_error(author_ident: str | None = None, committer_ident: str | None = None) -> str | None:
	if author_ident is None:
		proc = _git("var", "GIT_AUTHOR_IDENT")
		if proc.returncode != 0:
			raise RuntimeError((proc.stderr or proc.stdout).strip() or "git var GIT_AUTHOR_IDENT failed")
		author_ident = proc.stdout
	if committer_ident is None:
		proc = _git("var", "GIT_COMMITTER_IDENT")
		if proc.returncode != 0:
			raise RuntimeError((proc.stderr or proc.stdout).strip() or "git var GIT_COMMITTER_IDENT failed")
		committer_ident = proc.stdout
	if AI_IDENTITY_RE.search(f"{author_ident}\n{committer_ident}"):
		return "GIT GUARD BLOCKED: AI identity is forbidden as commit author or committer."
	return None


def feature_impact_error(message_text: str) -> str | None:
	"""FL-4781: refuse a staged change to a registered feature's source
	fingerprint unless the Godot-Feature-Impact trailer names that feature.

	The gate owns the fingerprint and the trailer contract; this guard only
	calls it. It fingerprints only features whose owner files (GLSL: include
	closure) are staged, at HEAD and at the index, so an unrelated commit
	pays one `git diff --cached`. It runs at commit-msg, outside the
	pre-commit window. Lazy import for the same reason as _policy().
	Fails closed: a fingerprint that cannot be computed blocks the commit.
	"""
	scripts_dir = os.path.dirname(os.path.abspath(__file__))
	if scripts_dir not in sys.path:
		sys.path.insert(0, scripts_dir)
	import godot_feature_regression_gate as feature_gate

	try:
		errors = feature_gate.staged_feature_impact_errors(message_text, base_dir=REPO_ROOT)
	except (OSError, ValueError, feature_gate.OwnerUnresolved) as exc:
		return f"GIT GUARD BLOCKED (FL-4781): feature fingerprint check failed: {exc}"
	return "\n".join(errors) if errors else None


def render_lane_error(message_text: str) -> str | None:
	"""FL-4877: True FF is a separate renderer.  Refuse a staged change to a
	protected non-FF render file without a Non-FF-Render-Change trailer, and
	refuse any non-FF shader that includes an FF-private file.  The boundary
	owner is scripts/render_lane_boundary.py; it also owns the merge, no-HEAD
	and unreadable-config policy.  Fails closed: any exception from the check
	blocks the commit, because "the check crashed" is not "nothing changed".
	"""
	scripts_dir = os.path.dirname(os.path.abspath(__file__))
	if scripts_dir not in sys.path:
		sys.path.insert(0, scripts_dir)
	try:
		import render_lane_boundary as lane

		errors = lane.staged_errors(message_text, base_dir=REPO_ROOT)
	except Exception as exc:  # noqa: BLE001 - fail closed on every failure
		return f"GIT GUARD BLOCKED (FL-4877): render lane check failed: {type(exc).__name__}: {exc}"
	return "\n".join(errors) if errors else None


def commit_msg_error(message_text: str) -> str | None:
	identity_error = commit_identity_error()
	if identity_error:
		return identity_error
	if AI_TRAILER_RE.search(message_text):
		return "GIT GUARD BLOCKED: AI attribution trailers are forbidden in commit messages."
	if BANNED_CHOICE_TOKEN_RE.search(message_text):
		return "GIT GUARD BLOCKED: standalone choice token is banned in commit messages."
	planning_error = planning_surface_error(message_text=message_text)
	if planning_error:
		return planning_error
	path_owner_error = path_fl_owner_error(message_text=message_text)
	if path_owner_error:
		return path_owner_error
	feature_error = feature_impact_error(message_text)
	if feature_error:
		return feature_error
	lane_error = render_lane_error(message_text)
	if lane_error:
		return lane_error
	return None


def pre_push_error(remote_name: str, remote_url: str) -> str | None:
	"""Reject every Git push route to the read-only original repository."""
	if remote_name == UPSTREAM_REMOTE_NAME or UPSTREAM_REPOSITORY_RE.search(remote_url):
		return (
			"GIT GUARD BLOCKED (FL-3856): msokalski/asciicker is read-only source authority. "
			"Push Y9-2 changes to origin only."
		)
	return None


def ledger_preservation_error() -> str | None:
    """FL-4855: compare HEAD with the real index, including private indexes.

    Nonblank ledger lines may be appended or moved, never silently removed.
    A failed Git read is not an empty ledger. This is a transition check, not
    an inference from worktree prose or an unrelated fix-attempt marker.
    """
    from collections import Counter
    path = 'docs/FAILURE_LOG.md'
    changed = _git('diff', '--cached', '--name-only', '-z', '--', path)
    if changed.returncode:
        return 'GIT GUARD BLOCKED: cannot inspect staged FL preservation.'
    if path not in changed.stdout.split('\0'):
        return None
    old = _git('show', f'HEAD:{path}')
    new = _git('show', f':{path}')
    if old.returncode or new.returncode:
        return 'GIT GUARD BLOCKED: cannot read both ledger snapshots; deletion or unreadable ledger.'
    before = Counter(line for line in old.stdout.splitlines() if line.strip())
    after = Counter(line for line in new.stdout.splitlines() if line.strip())
    removed = before - after
    if removed:
        examples = '\n'.join(list(removed)[:5])
        return 'GIT GUARD BLOCKED: existing FL lines removed; append a correction instead.\n' + examples
    return None


def cmd_pre_commit(_: argparse.Namespace) -> int:
    preservation = ledger_preservation_error()
    if preservation:
        print(preservation, file=sys.stderr)
        return 1
    error = pre_commit_error()
    if error:
        print(error, file=sys.stderr)
        return 1
    return 0


def cmd_commit_msg(args: argparse.Namespace) -> int:
    message_text = Path(args.message_file).read_text(encoding="utf-8")
    error = commit_msg_error(message_text)
    if error:
        print(error, file=sys.stderr)
        return 1
    return 0


def cmd_pre_push(args: argparse.Namespace) -> int:
    error = pre_push_error(args.remote_name, args.remote_url)
    if error:
        print(error, file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    pre_commit = sub.add_parser("pre-commit", help="Validate staged content before commit.")
    pre_commit.set_defaults(func=cmd_pre_commit)

    commit_msg = sub.add_parser("commit-msg", help="Validate the commit message file.")
    commit_msg.add_argument("message_file")
    commit_msg.set_defaults(func=cmd_commit_msg)

    pre_push = sub.add_parser("pre-push", help="Reject writes to read-only upstream repositories.")
    pre_push.add_argument("remote_name")
    pre_push.add_argument("remote_url")
    pre_push.set_defaults(func=cmd_pre_push)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except RuntimeError as exc:
        print(f"GIT GUARD BLOCKED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
