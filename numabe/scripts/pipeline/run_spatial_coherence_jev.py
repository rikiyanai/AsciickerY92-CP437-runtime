#!/usr/bin/env python3
"""Ask JEV for bounded spatial-review proposals; never apply geometry.

Input comes from ``spatial_coherence_review.write_jev_input``.  Each failed
coherence row or upstream plan anomaly becomes one TypeSafe choice question.
Output remains a proposal document: it is not an authored decision and no
planner consumes it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from scripts.pipeline.jev_decisions import JevError, TypesafeBackend
from scripts.pipeline.spatial_coherence_review import JEV_INPUT_SCHEMA


PROPOSALS_SCHEMA = "y92.spatial_coherence_jev_proposals.v1"
QUESTION_ID = "placement_action"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TypesafeSpatialBackend(TypesafeBackend):
    """TypeSafe adapter constrained to the choices in one review job."""

    def propose(self, job: dict) -> dict:
        evidence = job.get("visual_evidence") or {}
        facts = job.get("facts") or {}
        options = list(job.get("options") or [])
        has_measured_candidate = any(
            isinstance(option.get("parameters"), dict)
            and bool(option["parameters"])
            for option in options)
        visual_findings = evidence.get("machine_readable_findings")
        # TypeSafe's endpoint is text-only. A filesystem image path does not
        # let the model see the image. Require either bounded geometry already
        # measured by the evidence stage, or explicit machine-readable visual
        # findings. Checker prose alone caused the invalid all-unresolved run.
        evidence_mode = (
            "measured_spatial_candidates" if has_measured_candidate
            else "visual_findings_and_structured"
            if evidence.get("state") == "ready" and visual_findings
            else None)
        if evidence_mode is None:
            raise JevError(
                f"spatial JEV evidence is not ready for {job.get('review_id')}; "
                "attach measured candidate options or machine-readable visual "
                "findings first; anomaly text and image paths are insufficient")
        option_by_choice = {
            str(option["choice"]): option for option in job["options"]
        }
        choices = {
            str(option["choice"]): str(option["criterion"])
            for option in options
        }
        question = {
            QUESTION_ID: {
                "type": "choice",
                "instructions": (
                    "Choose exactly one physically plausible review option "
                    "supported by the supplied evidence. Edge-shaped objects "
                    "such as fences, walls, hedges, and guardrails normally "
                    "run parallel at a road, path, platform, or rail-corridor "
                    "edge, not through an active carriageway or track centre. "
                    "Select only a supplied measured candidate; do not invent "
                    "coordinates, offsets, dimensions, hosts, or transforms. "
                    "Choose unresolved only when the supplied candidates and "
                    "evidence cannot distinguish a physically plausible "
                    "placement. This remains a proposal for human review."),
                "criteria": choices,
            }
        }
        state = {
            "review_id": job["review_id"],
            "feature_id": job.get("feature_id"),
            "instance_id": job.get("instance_id"),
            "placement_ids": list(job.get("placement_ids") or []),
            "observed_mismatch": dict(facts),
            "visual_evidence": dict(job.get("visual_evidence") or {}),
            "input_evidence_mode": evidence_mode,
            "existing_authored_decision_id": job.get(
                "existing_authored_decision_id"),
        }
        response = self._post(question, state)
        try:
            answer = response["answers"][QUESTION_ID]
            choice = str(answer["choice"])
            confidence = float(answer["confidence"])
            probabilities = {
                str(key): float(value)
                for key, value in answer["probabilities"].items()
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise JevError("spatial JEV answer is malformed") from exc
        if choice not in choices:
            raise JevError(
                f"spatial JEV chose unoffered action {choice!r}")
        if not 0.0 <= confidence <= 1.0:
            raise JevError("spatial JEV confidence is outside [0, 1]")
        if (choice not in probabilities
                or any(key not in choices for key in probabilities)
                or any(not 0.0 <= value <= 1.0
                       for value in probabilities.values())):
            raise JevError(
                "spatial JEV probabilities do not match the offered choices")
        selected = option_by_choice[choice]
        return {
            "selected_option": choice,
            "proposed_action": str(selected.get("action") or choice),
            "proposed_parameters": dict(selected.get("parameters") or {}),
            "confidence": confidence,
            "probabilities": probabilities,
            "input_evidence_mode": evidence_mode,
            "request_id": response.get("request_id"),
            "resolved_model": response.get("model"),
        }


def run(input_path: Path, out_path: Path, backend,
        *, limit: int | None = None, workers: int = 1) -> dict:
    document = json.loads(input_path.read_text(encoding="utf-8"))
    if document.get("schema") != JEV_INPUT_SCHEMA:
        raise JevError(
            f"spatial JEV input schema {document.get('schema')!r}; "
            f"expected {JEV_INPUT_SCHEMA!r}")
    jobs = list(document.get("jobs") or [])
    if limit is not None:
        if limit < 0:
            raise JevError("--limit must be non-negative")
        jobs = jobs[:limit]

    def propose(job):
        result = backend.propose(job)
        return {
            "review_id": job["review_id"],
            "feature_id": job.get("feature_id"),
            "instance_id": job.get("instance_id"),
            **result,
            "status": "proposed",
            "decision_authority": "jev_model_proposal_only",
            "human_acceptance_required": True,
            "post_jev_visual_verification": dict(
                job["post_jev_visual_verification"]),
        }

    if workers < 1 or workers > 16:
        raise JevError("workers must be within [1, 16]")
    if workers == 1:
        proposals = [propose(job) for job in jobs]
    else:
        # urllib opens one independent HTTPS connection per call. Keep the
        # pool bounded to avoid turning a 100-row review into an accidental
        # service flood while avoiding hour-long sequential execution.
        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix="spatial-jev") as pool:
            proposals = list(pool.map(propose, jobs))
    output = {
        "schema": PROPOSALS_SCHEMA,
        "inputs": {
            "jev_input": str(input_path.resolve()),
            "jev_input_sha256": _sha256(input_path),
        },
        "counts": {
            "input_jobs": len(document.get("jobs") or []),
            "requested_jobs": len(jobs),
            "proposals": len(proposals),
        },
        "contract": {
            "planner_consumable": False,
            "model_generated_geometry_forbidden": True,
            "supplied_geometry_candidate_selection_allowed": True,
            "human_acceptance_required": True,
            "fresh_rebuild_and_visual_verification_required": True,
        },
        "backend_stats": dict(getattr(backend, "stats", {})),
        "execution": {"workers": workers},
        "proposals": proposals,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args(argv)
    backend = TypesafeSpatialBackend()
    result = run(
        args.input, args.out, backend, limit=args.limit,
        workers=args.workers)
    print(
        f"[spatial-jev] proposed={result['counts']['proposals']} "
        f"of {result['counts']['input_jobs']} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
