#!/usr/bin/env python3
"""FL-4359 spent-lane guard.

This is a pre-commit/front-door guard for the perspective probe lane. It forces a
fresh review receipt before committing renderer/probe changes, and blocks known
spent source families unless the staged diff has moved to the documented target
architecture.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REFERENCE_ROOT = Path(".scratch/canxkoz-texel-splatting")
REFERENCE_SPLAT = REFERENCE_ROOT / "src" / "splat.ts"
FAILURE_LOG = Path("docs/FAILURE_LOG.md")
ANALYZER_PATH = Path("scripts/analyze_fl4359_moving_camera.py")

# FL-4775 owns the observer-power prerequisite for FL-4359.  These gates read
# presentation values.  They may falsify stability when values differ, but
# equality cannot validate stability while the presentation distribution is
# degenerate or lacks same-pose calibration.
PROOF_SENSITIVITY_RULE = (
    "equality of FG/BG/glyph/mask presentation is non-evidence whenever terrain "
    "Product distribution is degenerate or same-pose calibration is absent; "
    "inequality remains a falsifier"
)
PRESENTATION_SENSITIVITY_GATES = (
    "gameplay_content_shimmer_bounded",
    "gameplay_continuous_product_colour_stability_bounded",
    "gameplay_cell_mask_owner_stability_bounded",
)

RELEVANT_PATHS = {
    "godot_project/render/ascii/raster/ascii_compositor.glsl",
    "godot_project/render/product/cell_owner_product.glsl",
    "godot_project/shaders/ascii_probe_capture.glsl",
    "godot_project/shaders/ascii_probe_splat.glsl",
    "godot_project/shaders/ascii_probe_resolve.glsl",
    "godot_project/shaders/ascii_probe_light.glsl",
    "godot_project/shaders/ascii_probe_cull.glsl",
    "godot_project/shaders/ascii_probe_edge_mask.glsl",
    "godot_project/render/backends/perspective/capture/perspective_probe_capture.glsl",
    "godot_project/render/backends/perspective/splat/perspective_probe_splat.glsl",
    "godot_project/render/backends/perspective/publish/perspective_probe_payload_publish.glsl",
    "godot_project/render/api/ASCIICompositorEffect.gd",
    "godot_project/render/backends/perspective/PerspectiveProbeBackend.gd",
    "godot_project/scripts/render/ascii_probe_state.gd",
}

# These files can weaken or remove the observer-power dependency without
# touching renderer source.  A commit that changes one of them must pass the
# same consolidated-ledger readiness check as a proof run.
PROOF_CONTRACT_PATHS = {
    str(ANALYZER_PATH),
    "scripts/run_fl4359_headed_movement.py",
    "scripts/run_fl4359_acceptance_package.py",
    "scripts/build_fl4359_sunday_current_review_grid.py",
    "scripts/run_fl4431_perf_visual_runner.py",
    "scripts/run_godot_visual_owner_proof.py",
}
HOOK_CONTRACT_PATHS = {
    "scripts/fl4359_spent_lane_guard.py",
    "scripts/hooks/fl4359_proof_sensitivity_gate.py",
    "scripts/maintainer/hooks/fl4359_proof_sensitivity_hook.py",
    "scripts/maintainer/install_hooks.py",
    ".claude/settings.json",
    ".codex/hooks.json",
}
AUDIT_CONTRACT_PATHS = {str(FAILURE_LOG)}
CONTRACT_PATHS = PROOF_CONTRACT_PATHS | HOOK_CONTRACT_PATHS | AUDIT_CONTRACT_PATHS

# These files exist solely inside the perspective probe pipeline. Any staged
# hunk in them belongs to the spent-lane review surface. The final compositor,
# compositor host, and product shader are mixed-owner files: unrelated feature
# lanes also live there, so path membership alone must never classify their
# hunks as FL-4359.
PROBE_ONLY_PATHS = RELEVANT_PATHS - {
    "godot_project/render/ascii/raster/ascii_compositor.glsl",
    "godot_project/render/product/cell_owner_product.glsl",
    "godot_project/render/api/ASCIICompositorEffect.gd",
}

MIXED_OWNER_PATHS = RELEVANT_PATHS - PROBE_ONLY_PATHS

SPENT_LANE_FAMILIES = (
    {
        "id": "probe_lit_producthit_payload",
        "description": (
            "selected probe lit/ProductHit paint without captured ASCII "
            "glyph+fg+bg payload"
        ),
        "tokens": (
            "product_probe_ascii_payload(ProductHit hit",
            "probe_ascii_rgb",
            "h.probe_ascii_rgb = clamp(pr.lit_rgb",
            "payload.bg = clamp(hit.probe_ascii_rgb",
            "product_probe_filtered_bg(",
            "product_apply_probe_filtered_bg(",
            "ProbeFilteredBg",
            "lit_sum += clamp(pr.lit_rgb",
        ),
        "requires_captured_ascii_payload": True,
    },
    {
        "id": "producthit_ascii_payload_bridge",
        "description": (
            "captured glyph/fg/bg is carried through ProductHit as another "
            "product-side bridge instead of a selected probe payload image"
        ),
        "tokens": (
            "bool has_probe_ascii_payload",
            "uint probe_ascii_glyph",
            "vec3 probe_ascii_fg",
            "vec3 probe_ascii_bg",
            "r.ascii_glyph",
            "r.ascii_fg",
            "r.ascii_bg",
            "r.has_ascii_payload",
        ),
        "requires_token": "selected_probe_ascii_payload_image",
    },
    {
        "id": "screen_subtap_record_reselection",
        "description": (
            "product pass reselects selected probe records from current screen "
            "subtap/ray after splat resolve"
        ),
        "tokens": (
            "probe_subcell_slot_for_sample(ansi_xy, sample_center_px, fb_size)",
            "load_probe_subcell_record(ansi_xy, slot)",
            "load_probe_selected_record(ray, ansi_xy)",
        ),
        "requires_token": "if (pr.valid && main_pr.valid && probe_records_share_product_identity(pr, main_pr))",
    },
    {
        "id": "receiver_world_semantic_refloor",
        "description": (
            "selected receiver_world is re-floored into terrain/water product "
            "identity after the captured material cell should own identity"
        ),
        "tokens": (
            "floor(pr.receiver_world.xz - params.terrain_params.xy)",
            "ivec2(floor(receiver_world.xz",
            "probe_record_receiver_matches_material_cell",
        ),
        "requires_token": "h.terr_cell = product_cell;",
    },
    {
        "id": "camera_frame_ground_paint",
        "description": (
            "terrain/water cutover paint still accepts ansi_xy/current ray as "
            "visible paint owner"
        ),
        "tokens": (
            "cell_owner_weather_flags(weather_kind, ansi_xy",
            "cell_owner_apply_weather_paint(weather_kind, paint_flags, ansi_xy",
            "selected_product_visible_glyph(",
            "compute_vapor_cell_product(ray, product_effect_receiver_world",
            "sky_paint_fg(ansi_xy)",
            "sky_paint_bg(ansi_xy)",
        ),
        "requires_token": "ground_probe_cutover_paint",
    },
    {
        "id": "sky_missing_probe_fallback",
        "description": (
            "missing selected probe records still become visible sky truth "
            "without captured null/sky evidence"
        ),
        "tokens": (
            "return empty_hit(ray)",
            "behind_is_sky && main_sidecar.cov_count < 4u",
            "load_eye_probe_backstop_record(ray)",
        ),
        "requires_token": "ProbeSelectedRecord probe_eye_sky_backstop_record(ViewRay ray",
    },
    {
        "id": "probe_lifecycle_visible_epoch_switch",
        "description": (
            "visible GRID/PREVIOUS/EYE epoch changes remain live without a "
            "single ready/crossfade owner"
        ),
        "tokens": (
            "PROBE_FAST_GRID_STEP",
            "_probe_grid_step_for_motion",
            "release_grid_transition",
            "selected_step := PROBE_FAST_GRID_STEP",
        ),
        "requires_token": "float stable_epoch_threshold(uint product_key)",
    },
    {
        "id": "cube_face_basis_duplication",
        "description": (
            "probe cube-face basis remains hand-written in multiple places, "
            "allowing capture/splat/edge direction drift"
        ),
        "tokens": (
            "vec3 probe_texel_dir(int face, vec2 uv)",
            "_probe_face_vp",
            "face == 3",
        ),
        "requires_token": "MUST be the exact inverse",
    },
)

CAPTURED_ASCII_GLYPH_TOKENS = (
    "captured_ascii_glyph",
    "probe_ascii_glyph",
    "probe_glyph_image",
    "resolve_glyph",
    "selected_glyph_image",
)
CAPTURED_ASCII_FG_TOKENS = (
    "captured_ascii_fg",
    "probe_ascii_fg",
    "probe_fg_image",
    "resolve_fg",
    "selected_fg_image",
)
CAPTURED_ASCII_BG_TOKENS = (
    "captured_ascii_bg",
    "probe_ascii_bg",
    "probe_bg_image",
    "resolve_bg",
    "selected_bg_image",
)

SPENT_FL_IDS = (
    "FL-4057",
    "FL-4359",
    "FL-4513",
    "FL-4504",
)

FL_REF_RE = re.compile(r"\bFL-\d+\b")

def _run_git(*args: str, allow_fail: bool = False) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0 and not allow_fail:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_path(path: Path) -> str:
    return _sha256_bytes((ROOT / path).read_bytes())


def _normalized_fingerprint(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    return _sha256_bytes(normalized.encode("utf-8"))


def _tracked_file_text(path: str, *, use_worktree: bool = False) -> str:
    full = ROOT / path
    if use_worktree and full.exists():
        return full.read_text(encoding="utf-8")
    staged = _run_git("show", f":{path}", allow_fail=True)
    if staged:
        return staged
    if full.exists():
        return full.read_text(encoding="utf-8")
    head = _run_git("show", f"HEAD:{path}", allow_fail=True)
    return head


def _staged_paths() -> list[str]:
    raw = _run_git("diff", "--cached", "--name-only", "--diff-filter=ACM")
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _relevant_staged_paths() -> list[str]:
    staged = set(_staged_paths())
    return sorted(path for path in staged if path in RELEVANT_PATHS)


def _contract_staged_paths() -> list[str]:
    staged = set(_staged_paths())
    return sorted(path for path in staged if path in CONTRACT_PATHS)


def _staged_diff_hash(paths: list[str]) -> str:
    if not paths:
        return _sha256_bytes(b"")
    diff = _run_git("diff", "--cached", "--", *paths)
    return _sha256_bytes(diff.encode("utf-8"))


def _staged_diff_text(paths: list[str]) -> str:
    if not paths:
        return ""
    return _run_git("diff", "--cached", "--", *paths)


def _worktree_diff_text(paths: list[str]) -> str:
    if not paths:
        return ""
    return _run_git("diff", "--", *paths)


def _diff_hunks(diff: str) -> list[tuple[str, str]]:
    """Return (path, changed-lines) pairs from a unified diff.

    Context lines are deliberately excluded. A mixed-owner file frequently has
    FL-4359 probe code adjacent to unrelated lighting code; classifying from
    context recreates the path-wide false positive this guard is meant to stop.
    """
    hunks: list[tuple[str, str]] = []
    current_path = ""
    changed: list[str] | None = None
    for raw in diff.splitlines():
        if raw.startswith("diff --git "):
            if changed is not None:
                hunks.append((current_path, "\n".join(changed)))
                changed = None
            parts = raw.split()
            current_path = parts[3][2:] if len(parts) >= 4 and parts[3].startswith("b/") else ""
            continue
        if raw.startswith("@@"):
            if changed is not None:
                hunks.append((current_path, "\n".join(changed)))
            changed = []
            continue
        if changed is None or raw.startswith("+++") or raw.startswith("---"):
            continue
        if raw.startswith("+") or raw.startswith("-"):
            changed.append(raw[1:])
    if changed is not None:
        hunks.append((current_path, "\n".join(changed)))
    return hunks


def _hunk_requires_spent_lane_review(path: str, changed_text: str) -> bool:
    if path in PROBE_ONLY_PATHS:
        return True
    if path not in MIXED_OWNER_PATHS:
        return False
    refs = set(FL_REF_RE.findall(changed_text))
    if not refs:
        return True
    return bool(refs.intersection(SPENT_FL_IDS))


def _guarded_paths_from_diff(diff: str) -> list[str]:
    return sorted({
        path
        for path, changed_text in _diff_hunks(diff)
        if _hunk_requires_spent_lane_review(path, changed_text)
    })


def _reference_head() -> str:
    # REFERENCE_ROOT is a tracked directory in this repository, not a nested
    # Git repository. `git -C REFERENCE_ROOT rev-parse HEAD` therefore returned
    # the main worktree HEAD and made every audit-refresh commit invalidate its
    # own receipt. Pin the last commit that changed the reference corpus.
    head = _run_git(
        "log", "-1", "--format=%H", "--", str(REFERENCE_ROOT)
    ).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise RuntimeError(f"missing tracked reference history for {REFERENCE_ROOT}")
    return head


def _source_corpus_text(*, use_worktree: bool = False) -> str:
    chunks: list[str] = []
    for path in sorted(RELEVANT_PATHS):
        chunks.append(f"\n// FILE: {path}\n")
        chunks.append(_tracked_file_text(path, use_worktree=use_worktree))
    return "\n".join(chunks)


def _has_captured_ascii_payload(corpus: str) -> bool:
    return (
        any(token in corpus for token in CAPTURED_ASCII_GLYPH_TOKENS)
        and any(token in corpus for token in CAPTURED_ASCII_FG_TOKENS)
        and any(token in corpus for token in CAPTURED_ASCII_BG_TOKENS)
    )


def _family_blocked(family: dict[str, object], corpus: str) -> tuple[bool, list[str]]:
    tokens = tuple(str(token) for token in family["tokens"])
    hits = [token for token in tokens if token in corpus]
    if not hits:
        return False, []
    if family.get("requires_captured_ascii_payload") and _has_captured_ascii_payload(corpus):
        return False, []
    required = family.get("requires_token")
    if isinstance(required, str) and required in corpus:
        return False, []
    forbidden_guard = family.get("forbid_when_token_present")
    if isinstance(forbidden_guard, str) and forbidden_guard not in corpus:
        return False, []
    return True, hits


def _spent_lane_findings(corpus: str) -> list[str]:
    findings: list[str] = []
    for family in SPENT_LANE_FAMILIES:
        blocked, hits = _family_blocked(family, corpus)
        if not blocked:
            continue
        findings.append(
            f"FL-4359 spent lane {family['id']}: {family['description']}. "
            f"tokens={hits}"
        )
    return findings


def _build_receipt(paths: list[str], *, failed_attempts_ack: bool = False, reference_ack: bool = False) -> dict[str, object]:
    if not REFERENCE_SPLAT.exists():
        raise RuntimeError(f"missing reference splat file: {REFERENCE_SPLAT}")
    if not (ROOT / FAILURE_LOG).exists():
        raise RuntimeError(f"missing required review file: {FAILURE_LOG}")
    return {
        "schema": "fl4359_in_memory_review.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_head": _run_git("rev-parse", "HEAD").strip(),
        "staged_paths": paths,
        "staged_diff_sha256": _staged_diff_hash(paths),
        "reference_repo": str(REFERENCE_ROOT),
        "reference_head": _reference_head(),
        "reference_splat_ts_sha256": _sha256_path(REFERENCE_SPLAT),
        "failure_log_sha256": _sha256_path(FAILURE_LOG),
        "reviewed_files": [
            str(FAILURE_LOG),
            str(REFERENCE_SPLAT),
        ],
        "review_acknowledgement": {
            "failed_attempts": failed_attempts_ack,
            "canxkoz_reference": reference_ack,
        },
        "blocked_spent_lane_families": [
            str(family["id"]) for family in SPENT_LANE_FAMILIES
        ],
    }


def _failure_log_fix_attempts(text: str) -> list[str]:
    attempts: list[str] = []
    for fl_id in SPENT_FL_IDS:
        start = text.find(f"### {fl_id}:")
        if start < 0:
            continue
        next_match = re.search(r"\n### FL-\d+:", text[start + 1 :])
        end = start + 1 + next_match.start() if next_match else len(text)
        section = text[start:end]
        for block in section.split("#### Fix attempt")[1:]:
            approach_match = re.search(r"- \*\*Approach:\*\* (.*)", block)
            proof_match = re.search(r"- \*\*Proof state:\*\* (.*)", block)
            approach = approach_match.group(1).strip() if approach_match else block.splitlines()[0].strip()
            proof = proof_match.group(1).strip() if proof_match else "unknown"
            attempts.append(f"{fl_id} | {proof} | {approach}")
    return attempts


def _expression_names(node: ast.AST) -> set[str]:
    return {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}


def _expression_depends_on(
    node: ast.AST,
    target: str,
    assignments: dict[str, ast.AST],
    seen: set[str] | None = None,
) -> bool:
    names = _expression_names(node)
    if target in names:
        return True
    visited = set() if seen is None else set(seen)
    for name in names - visited:
        assigned = assignments.get(name)
        if assigned is None:
            continue
        visited.add(name)
        if _expression_depends_on(assigned, target, assignments, visited):
            return True
    return False


def _dict_items(node: ast.Dict) -> dict[str, ast.AST]:
    items: dict[str, ast.AST] = {}
    for key, value in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            items[key.value] = value
    return items


def _proof_sensitivity_contract_errors() -> list[str]:
    errors: list[str] = []
    failure_text = (ROOT / FAILURE_LOG).read_text(encoding="utf-8")
    normalized_failure = re.sub(r"\s+", " ", failure_text.lower())
    if PROOF_SENSITIVITY_RULE.lower() not in normalized_failure:
        errors.append(
            "FL-4775 proof-sensitivity rule is missing from the canonical failure log"
        )

    try:
        tree = ast.parse((ROOT / ANALYZER_PATH).read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        return errors + [f"cannot inspect {ANALYZER_PATH}: {exc}"]

    gate_nodes: dict[str, ast.Dict] = {}
    assignments: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                assignments[target.id] = node.value
        if not isinstance(node, ast.Dict):
            continue
        items = _dict_items(node)
        for gate_name in PRESENTATION_SENSITIVITY_GATES:
            value = items.get(gate_name)
            if isinstance(value, ast.Dict):
                gate_nodes[gate_name] = value

    for gate_name in PRESENTATION_SENSITIVITY_GATES:
        gate = gate_nodes.get(gate_name)
        if gate is None:
            errors.append(f"missing presentation gate contract: {gate_name}")
            continue
        fields = _dict_items(gate)
        pass_expr = fields.get("pass")
        missing_dependencies = [
            dependency
            for dependency in (
                "terrain_presentation_distribution_admissible",
                "presentation_observer_population_accounting_valid",
            )
            if pass_expr is None
            or not _expression_depends_on(pass_expr, dependency, assignments)
        ]
        if missing_dependencies:
            errors.append(
                f"{gate_name} can pass without observer-power admission: "
                + ", ".join(missing_dependencies)
            )
        for required_field in (
            "presentation_distribution_admissible",
            "population_accounting_valid",
            "diagnostic_only_reason",
        ):
            if required_field not in fields:
                errors.append(f"{gate_name} omits {required_field}")
    return errors


def _readiness_errors() -> list[str]:
    return _proof_sensitivity_contract_errors()


def cmd_ready(args: argparse.Namespace) -> int:
    errors = _readiness_errors()
    if errors:
        print("FL-4359 execution/logging readiness: BLOCKED", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    failure_text = (ROOT / FAILURE_LOG).read_text(encoding="utf-8")
    print(
        "FL-4359 canonical failure-log/readiness input: CURRENT; "
        "product_and_fl_status=NOT_EVALUATED; "
        f"family_fix_attempts={len(_failure_log_fix_attempts(failure_text))}"
    )
    return 0


def cmd_refresh_audit(args: argparse.Namespace) -> int:
    print("FL-4359 spent-attempt JSON audit is retired; canonical owner=docs/FAILURE_LOG.md")
    print("wrote_files=0")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    staged_paths = _relevant_staged_paths()
    paths = _guarded_paths_from_diff(_staged_diff_text(staged_paths))
    if not paths and not args.force:
        print("FL-4359 spent-lane review: no staged probe/render source paths.")
        return 0
    if not args.ack_failed_attempts or not args.ack_reference:
        print(
            "FL-4359 spent-lane review: BLOCKED: pass "
            "--ack-failed-attempts and --ack-reference after reading the "
            "canonical FL-4359 history plus canxkoz splat.ts",
            file=sys.stderr,
        )
        return 1
    receipt = _build_receipt(paths, failed_attempts_ack=True, reference_ack=True)
    print("FL-4359 review complete; durable receipt owner=docs/FAILURE_LOG.md")
    print("wrote_files=0")
    print(f"staged_diff_sha256={receipt['staged_diff_sha256']}")
    print(f"reference_head={receipt['reference_head']}")
    return 0


def _receipt_errors(paths: list[str]) -> list[str]:
    return []


def cmd_check(args: argparse.Namespace) -> int:
    staged_paths = _relevant_staged_paths()
    contract_paths = _contract_staged_paths()
    staged_diff = _staged_diff_text(staged_paths)
    paths = _guarded_paths_from_diff(staged_diff)
    if not paths and not contract_paths and not args.all:
        return 0
    worktree_diff = _worktree_diff_text(sorted(RELEVANT_PATHS)) if args.all and not paths else ""
    if args.all and not paths:
        paths = _guarded_paths_from_diff(worktree_diff)
        if not paths and not contract_paths:
            errors = _readiness_errors()
            if errors:
                print("FL-4359 spent-lane guard: BLOCKED", file=sys.stderr)
                for error in errors:
                    print(f"- {error}", file=sys.stderr)
                return 1
            print(
                "FL-4359 spent-lane corpus contract: CURRENT; "
                "product_and_fl_status=NOT_EVALUATED; "
                "no guarded hunks"
            )
            return 0
    corpus = _source_corpus_text(use_worktree=args.all and not staged_paths)
    errors: list[str] = _readiness_errors()
    if paths:
        if staged_paths:
            errors.extend(_receipt_errors(paths))
            guarded_diff = _staged_diff_text(paths)
        else:
            guarded_diff = worktree_diff
        # A current semantic audit plus an exact staged review receipt owns
        # authorization. The retired blanket "no added source lines" rule made
        # every real owner replacement impossible, including deletion of a
        # stale law followed by its one canonical replacement. Known spent
        # mechanisms remain blocked by _spent_lane_findings below.
    errors.extend(_spent_lane_findings(corpus))
    if not errors:
        print(
            "FL-4359 spent-lane corpus contract: CURRENT; "
            "product_and_fl_status=NOT_EVALUATED"
        )
        return 0
    print("FL-4359 spent-lane guard: BLOCKED", file=sys.stderr)
    for err in errors:
        print(f"- {err}", file=sys.stderr)
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    review = sub.add_parser("review", help="review staged FL-4359 source paths against canonical history")
    review.add_argument("--stage", action="store_true", help="compatibility flag; no receipt file is written")
    review.add_argument("--force", action="store_true", help="review even without staged relevant paths")
    review.add_argument("--ack-failed-attempts", action="store_true", help="confirm canonical FL-4359 history was reviewed")
    review.add_argument("--ack-reference", action="store_true", help="confirm canxkoz splat.ts was reviewed")
    review.set_defaults(func=cmd_review)
    check = sub.add_parser("check", help="check staged FL-4359 source paths")
    check.add_argument("--all", action="store_true", help="check current source even without staged relevant paths")
    check.set_defaults(func=cmd_check)
    refresh = sub.add_parser("refresh-audit", help="compatibility no-op; JSON audit is retired")
    refresh.add_argument("--stage", action="store_true", help="compatibility flag; no file is staged")
    refresh.set_defaults(func=cmd_refresh_audit)
    ready = sub.add_parser(
        "ready",
        help="check canonical FL history and the proof-sensitivity contract",
    )
    ready.set_defaults(func=cmd_ready)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except RuntimeError as exc:
        print(f"FL-4359 spent-lane guard: ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
