#!/usr/bin/env python3
"""Typed score-domain identities for FL-4512 temporal calibration tooling."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TemporalScorerSpec:
    scorer_kind: int
    scorer_version: int
    scorer_id: str
    score_domain: str
    delta_type: str

    @property
    def key(self) -> tuple[int, int, str]:
        return (self.scorer_kind, self.scorer_version, self.score_domain)


# Version 1 of scorer kind 1 is the retired binary32 Harri comparison.  The
# product exact selector evaluates the separately named C.10 F8/W4 integer
# program and therefore has a disjoint version and score domain.  Keeping both
# identities here prevents calibration/replay tooling from silently applying a
# float-domain margin to the exact integer score.
LEGACY_HARRI_BINARY32 = TemporalScorerSpec(
    scorer_kind=1,
    scorer_version=1,
    scorer_id="fl4260.shape6_weighted_l2.v1",
    score_domain="fl4260.shape6_weighted_l2.v1.binary32",
    delta_type="ieee754_binary32",
)
EXACT_HARRI_F8_W4 = TemporalScorerSpec(
    scorer_kind=1,
    scorer_version=2,
    scorer_id="fl4512.shape6_f8_w4.v2",
    score_domain="fl4512.shape6_f8_w4.v2.score_int.u32",
    delta_type="u32",
)
U7_MASK = TemporalScorerSpec(
    scorer_kind=2,
    scorer_version=1,
    scorer_id="u7.mask.v1",
    score_domain="u7.mask.v1.score_num.u32",
    delta_type="u32",
)
S_PF = TemporalScorerSpec(
    scorer_kind=3,
    scorer_version=1,
    scorer_id="S_pf.v1",
    score_domain="S_pf.v1.score_num.u32",
    delta_type="u32",
)

SUPPORTED_TEMPORAL_SCORERS = (
    LEGACY_HARRI_BINARY32,
    EXACT_HARRI_F8_W4,
    U7_MASK,
    S_PF,
)

# These are the scorers reachable from the exact compiled-program product
# selector.  A product table is emitted only when the corpus contributes a
# positive flip distribution for every one of them.
PRODUCT_TEMPORAL_SCORERS = (
    EXACT_HARRI_F8_W4,
    U7_MASK,
    S_PF,
)

SCORER_BY_KEY = {spec.key: spec for spec in SUPPORTED_TEMPORAL_SCORERS}
SCORER_BY_ID = {spec.scorer_id: spec for spec in SUPPORTED_TEMPORAL_SCORERS}
INTEGER_SCORE_DOMAINS = {
    spec.score_domain for spec in SUPPORTED_TEMPORAL_SCORERS
    if spec.delta_type == "u32"
}
