# Approval record: WVR-4809-001 (Mapillary CCTV singleton omission)

## Decision

The operator APPROVES the proposed omission of
`feature:mapillary_object/937664923692529` (Mapillary `object--cctv-camera`,
single frame, geometry confidence 0.6) as an intentional omission with no
stand-in: no honest proxy exists and the frame is not retrievable for
verification.

## Provenance of this decision

- Asked in the FL-4876 verification-boundary session, 2026-10-12, as the
  `cctv` decision next to the route-density and bridge-deck decisions:
  "CCTV singleton (Mapillary detection, 0.6 confidence, no retrievable
  frame, sub-pixel in our captures): approve its omission?"
- Operator answer, verbatim: "Approve the existing proposal".
- Principal identity, separately confirmed by the operator in the same
  session ("Use commit identity"):
  RIKI YANAI HERNANDEZ <33168192+rikiyanai@users.noreply.github.com>.
- Manual verification attempted first and documented: the served position
  (E 379809.8, N 3938519.8) falls inside the pole-top crop of capture
  mapillary_469104751032996, where no camera is discernible at the
  available resolution. The detection is therefore unverifiable with the
  corpus, not merely unobserved.

## What this approval does and does not do

- The waived row keeps its observation, keeps `asserts_physical_presence`
  false, and is emitted as `intentional_omission` with
  `policy_rule = authored_waiver:<this commit>`.
- It removes no evidence, lowers no threshold, and approves nothing beyond
  this single feature under the conditions hashed in the waiver row. If the
  feature, its observations, the requirements, or the debt decision change,
  this approval stops applying by the conditions law.
