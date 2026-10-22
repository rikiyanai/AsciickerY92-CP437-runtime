"""Promotion gates for reconstructed geometry (FL-4812).

These are the operator's acceptance criteria written as CODE, because a gate
that lives in a comment is a gate that has never once refused anything.  The
whole point is the refusal: a candidate that does not clear every required
criterion stays ``unresolved`` evidence and is NEVER written out as a promoted
world feature.

REQUIRED vs PREFERRED
=====================
Two criteria have a hard floor and a better-practice level:

    viewpoints            required >= 2, preferred >= 3
    triangulation angle   required >= 3 deg, preferred >= 5 deg

A candidate between floor and preference is promoted but flagged, so the
receipt can show the difference between "accepted" and "accepted comfortably"
without inventing a second silent threshold.

AUTHORITY IS NOT A GATE OUTPUT
==============================
Clearing these gates makes a candidate *world truth*.  It does NOT make it
collision or walkability truth.  Nothing here returns a gameplay authority;
the emitter holds reconstructed geometry at ``presentation_only`` until a
separate, explicitly-passed fusion gate says otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

MIN_VIEWPOINTS = 2
PREFERRED_VIEWPOINTS = 3
MIN_TRIANGULATION_ANGLE_DEG = 3.0
PREFERRED_TRIANGULATION_ANGLE_DEG = 5.0
MAX_MEDIAN_REPROJECTION_PX = 2.0
MAX_SINGLE_REPROJECTION_PX = 4.0

DECISION_PROMOTE = "promote"
DECISION_UNRESOLVED = "unresolved"


@dataclass
class Criterion:
    name: str
    required: str
    observed: float | int | str | None
    passed: bool
    preferred_met: bool | None = None
    note: str = ""

    def as_record(self) -> dict:
        rec = {
            "criterion": self.name,
            "required": self.required,
            "observed": self.observed,
            "pass": self.passed,
        }
        if self.preferred_met is not None:
            rec["preferred_met"] = self.preferred_met
        if self.note:
            rec["note"] = self.note
        return rec


@dataclass
class GateDecision:
    decision: str
    criteria: list[Criterion] = field(default_factory=list)

    @property
    def promoted(self) -> bool:
        return self.decision == DECISION_PROMOTE

    @property
    def failed_criteria(self) -> list[str]:
        return [c.name for c in self.criteria if not c.passed]

    def as_record(self) -> dict:
        return {
            "decision": self.decision,
            "all_required_passed": self.promoted,
            "failed": self.failed_criteria,
            "criteria": [c.as_record() for c in self.criteria],
        }


def evaluate(
    *,
    viewpoints: int,
    triangulation_angle_deg: float,
    reprojection_px: list[float] | np.ndarray,
    covariance: np.ndarray | None,
    semantic_votes: dict[str, int] | None = None,
    expected_class: str | None = None,
) -> GateDecision:
    """Apply every promotion gate to one triangulated candidate."""
    errors = np.asarray(list(reprojection_px), dtype=float)
    criteria: list[Criterion] = []

    criteria.append(Criterion(
        name="useful_viewpoints",
        required=f">= {MIN_VIEWPOINTS} (preferred >= {PREFERRED_VIEWPOINTS})",
        observed=int(viewpoints),
        passed=viewpoints >= MIN_VIEWPOINTS,
        preferred_met=viewpoints >= PREFERRED_VIEWPOINTS,
    ))

    criteria.append(Criterion(
        name="triangulation_angle_deg",
        required=f">= {MIN_TRIANGULATION_ANGLE_DEG} (preferred >= {PREFERRED_TRIANGULATION_ANGLE_DEG})",
        observed=float(triangulation_angle_deg),
        passed=triangulation_angle_deg >= MIN_TRIANGULATION_ANGLE_DEG,
        preferred_met=triangulation_angle_deg >= PREFERRED_TRIANGULATION_ANGLE_DEG,
        note="widest angle subtended at the point by any two viewpoints",
    ))

    median = float(np.median(errors)) if errors.size else float("inf")
    criteria.append(Criterion(
        name="median_reprojection_px",
        required=f"<= {MAX_MEDIAN_REPROJECTION_PX}",
        observed=median,
        passed=errors.size > 0 and median <= MAX_MEDIAN_REPROJECTION_PX,
    ))

    worst = float(errors.max()) if errors.size else float("inf")
    criteria.append(Criterion(
        name="worst_accepted_observation_px",
        required=f"<= {MAX_SINGLE_REPROJECTION_PX} (else review)",
        observed=worst,
        passed=errors.size > 0 and worst <= MAX_SINGLE_REPROJECTION_PX,
        note="an observation above this is not silently accepted; it needs review",
    ))

    # "Finite uncertainty" is a real gate, not paperwork: a covariance with a
    # non-finite or non-positive eigenvalue means the point is unconstrained
    # along some direction, and a position that is free to slide is not a
    # measurement however small its reprojection error is.
    if covariance is None:
        finite, observed, note = False, None, "no covariance was computed"
    else:
        cov = np.asarray(covariance, dtype=float)
        if not np.all(np.isfinite(cov)):
            finite, observed, note = False, None, "covariance contains non-finite entries"
        else:
            eigenvalues = np.linalg.eigvalsh((cov + cov.T) / 2.0)
            finite = bool(np.all(eigenvalues > 0) and np.all(np.isfinite(eigenvalues)))
            observed = float(np.sqrt(max(eigenvalues.max(), 0.0)))
            note = "largest positional sigma in metres along the worst-constrained axis"
    criteria.append(Criterion(
        name="finite_positive_definite_uncertainty",
        required="positive definite, all entries finite",
        observed=observed,
        passed=finite,
        note=note,
    ))

    votes = semantic_votes or {}
    total = sum(votes.values())
    if total == 0:
        consistent, observed, note = False, None, "no semantic evidence was recorded"
    else:
        winner = max(votes, key=lambda k: (votes[k], k))
        top = votes[winner]
        # Unanimity alone is NOT consistency.  Five viewpoints agreeing that a
        # point is NOT on the wall it was associated with is unanimous and is
        # exactly the case this gate has to refuse, so the agreed class must
        # also be the class the candidate is claiming.
        consistent = top == total and (expected_class is None or winner == expected_class)
        observed = f"{winner} {top}/{total}"
        note = "every viewpoint must agree, AND agree on the class being claimed"
        if expected_class is not None:
            note += f" ({expected_class!r})"
    criteria.append(Criterion(
        name="consistent_semantics",
        required=(f"all viewpoints agree on {expected_class!r}"
                  if expected_class else "all viewpoints agree on one class"),
        observed=observed,
        passed=consistent,
        note=note,
    ))

    decision = DECISION_PROMOTE if all(c.passed for c in criteria) else DECISION_UNRESOLVED
    return GateDecision(decision=decision, criteria=criteria)
