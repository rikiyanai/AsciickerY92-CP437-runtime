# Upstream XP Full-Cell Contract Ledger

This directory is the generated FL-4162 / RQ-200 evidence ledger for the finite
actor-visual XP corpus. It is `authority:false` and `is_proposal:true`. It does
not replace immutable hand evidence, family contract review, compiler source,
server reachability, semantic masks, runtime parity evidence, or operator
signoff.

`manifest.json` binds six family JSONL shards to the immutable
`state_FINAL_20260521-163326.json` SHA-256 and reports complete layer/cell
coverage. Each JSONL row represents one reviewed raw XP layer.

## Cell Addressing

Every raw atlas coordinate is covered exactly once. `cell_values` is a palette
of exact `(glyph, fg, bg)` values plus engine cell type, `render_operation`,
pending `semantic_contributions`, and `semantic_review_state`. Render behavior
and semantic attribution are separate fields. Each `cell_spans` entry is:

```text
[angle, frame, local_y, local_x_start, length, cell_value_index]
```

Atlas coordinates derive from `frame_geometry`:

```text
atlas_x = frame * frame_width + local_x
atlas_y = angle * frame_height + local_y
```

The compact span encoding is lossless and validated for complete, nonoverlapping
coverage, including transparent cells. `source_xp.raw_layer_sha256` pins the
expanded raw layer bytes.

## Review States

- `layer_role_reviewed_cell_semantics_unverified`: the hand-reviewed layer has
  one candidate role; cell-level meaning still needs source-contract review.
- `reviewed_composite_cell_assignment_pending`: the layer carries multiple
  roles; no role is silently assigned to every visible cell.
- `rejected_fragment_needs_contract`: the hand corpus identified a fragment but
  its final source contract remains unresolved.
- `unresolved_hand_evidence`: one of the ten explicitly withheld hand-review
  decisions.

`review_queue.json` groups all 573 raw layers into 203 exact-fingerprint units.
`cell_role_decisions.jsonl` stores reviewed full-cell assignments and remains
proposal-only. `cell_review_state_decisions.jsonl` records manual findings that
disprove a normalized single-role candidate without rewriting the immutable
ledger. Each review-state decision is bound to the review-unit id, raw-layer
SHA-256, frame geometry, and complete exact-match member set. Drift fails
closed.

The six-family review is complete: all 203 exact-fingerprint units have active
full-cell decisions, covering all 573 raw layers and 6,807,104 raw cells with
zero pending units. The three original `needs_source_contract` units were
resolved without rewriting immutable hand evidence: the player-nude alias/body
layer, the player armor/shield-context overlay, and the bigbee rider/shield
replacement fragment. Twenty prior false-clean decisions were deleted first,
preserved by canonical hash in `cell_review_state_decisions.jsonl`, and replaced
with distinct expanded semantic assignments. The queue's semantic-honesty gate
requires all 20 replacements before reporting ready. A full-atlas subset
comparison also resolves
`plydie-0011-L3` as shield reflection only: all 589 visible target cells are
byte-identical at the same coordinates to the reviewed shield-only
`plydie-1011-L3` reference, with no target-only cells. The immutable hand prose
that also named sword is preserved as a contradicted observation rather than
rewritten.

The first bigbee rider-plus-shield pair is also segmented against the reviewed
limbless-rider baseline. `bigbee-0010-L3` and `bigbee-0110-L3` retain
byte-identical baseline cells as `rider_torso`; changed and target-only cells
carry `shield`. This explicitly retracts the false-clean implication of the
normalized `bigbee_shield_regular` wording without changing the hand corpus.
The byte-near `bigbee-0111-L5` replacement follows the same source contract:
489 cells match the reviewed rider baseline exactly and the remaining visible
cells contribute shield.

The first bigbee rider-plus-sword units are segmented by the same replacement
contract. `bigbee-0001-L4` and the seven-member exact-fingerprint unit
represented by `bigbee-0011-L4` retain only byte-identical cells from the
reviewed `bigbee-0000-L3` limbless-rider reference as `rider_torso`; changed
and target-only cells contribute `sword`. The two sword units share identical
occupied coordinates and differ at only nine raw-value coordinates, so both
encode the same family operation without collapsing the layer to a clean sword
mask.

The next bigbee pass separates clean equipment masks from contextual
replacement fragments. The exact `bigbee-1011-L6` / `bigbee-1111-L7` unit and
the near variant `bigbee-1110-L5` are clean shield layers. `bigbee-1010-L5`
contains the same 381 clean-shield cells plus six hand-noted rider-context
cells, so those six cells remain `rider_torso` rather than being hidden by a
uniform shield label. The `bigbee-1012-L6` / `bigbee-1112-L7` unit retains
exact clean-shield cells while changed and target-only cells contribute the
crossbow string. `bigbee-1011-L5` and its two exact duplicates are an exact
440-cell subset of reviewed clean armor; the following layer owns shield, so
the immutable armor-plus-shield prose is preserved as contradicted context.
The rider-plus-crossbow Bigbee units are now partitioned by raw color component:
rider palette contributes `rider_torso`, weapon material contributes `crossbow`,
and mixed glyph cells retain both contributions.

The armor-mask pass confirms three player exact-fingerprint units and six
plydie units as clean family armor overlays. Their visible-cell counts change
when neighboring equipment covers armor pixels, but the raw layers add no
body, shield, weapon, and helmet contribution. The full-cell decisions use
`player_armor_regular` and `plydie_armor_regular` while retaining every
original generic or family-qualified hand label. Plydie sword layers are now
partitioned against reviewed body references, preserving body semantics at exact
coordinates and assigning sword only to changed and target-only cells.

The wolfie armor pass confirms `wolfie-1001-L3` and `wolfie-1002-L4` as
clean armor overlays. The three-member exact unit represented by
`wolfie-1012-L4` is a 1,400-cell exact subset of reviewed
`wolfie-1002-L4`; it contains zero target-only sword cells, so immutable prose
mentioning sword is retained as contradicted context. Wolfie crossbow and sword
layers are now segmented by raw body/equipment color components, including
mixed-role cells. Full-atlas later-frame review resolves the Plydie H-bit layers
as helmet-context overlays despite empty frame zero.

The coordinate recorder supports three explicitly reviewed operations without
inferring roles: a uniform-visible assignment for a human-confirmed clean mask,
an exact-reference partition that assigns reviewed reference semantics to
byte-identical coordinates plus reviewer-supplied delta semantics everywhere
else, and an exact-reference subset path that fails unless every visible target
cell is byte-identical to an already reviewed semantic at the same coordinate.
All remain fingerprint-bound proposal records. Composite candidates cannot
enter the uniform-visible path.

`semantic_honesty_audit.json` records the corpus-wide equipment false-clean
review. `family_contract_freeze.json` binds the complete queue, full cell-role
decisions, retraction provenance, ledger manifest, topology contracts, immutable
`state_FINAL` hash, and honesty audit. The freeze is reviewed source-contract
authority only; runtime authority remains false. Profile promotion stays
fail-closed until `compiler_cutover.json` proves that each requested source ID
uses the full-cell contract compiler and its legacy merge route has been deleted.

Regenerate and validate with:

```bash
python3 pipeline-v3/scripts/build_upstream_xp_cell_contract_ledger.py --check
python3 pipeline-v3/scripts/build_upstream_xp_cell_contract_ledger.py
python3 pipeline-v3/scripts/build_upstream_xp_cell_review_queue.py --check
```
