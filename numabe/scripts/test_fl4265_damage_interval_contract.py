#!/usr/bin/env python3
# =============================================================================
# test_fl4265_damage_interval_contract.py
# =============================================================================
# WHAT THIS FILE IS FOR
# ---------------------
# The game has an aeroplane you can fly. When an enemy aeroplane shoots yours,
# your plane loses "structural HP" (a health number owned by the game server).
# Once the plane is hurt badly enough, the game draws a flickering BLUE FLAME
# trailing behind it. That flame is the visible proof that the damage system
# reached the screen.
#
# This script does NOT run the game and does NOT look at any picture. It reads
# the source code and checks that the numbers which decide WHEN that flame may
# appear still agree with each other across four different files written in
# four different languages:
#
#   1. colyseus_server/src/sim/world.ts .... the authoritative game server
#                                            (TypeScript). Owns the plane's HP.
#   2. godot_project/scripts/World.gd ...... the game client (GDScript). Reads
#                                            HP and decides whether to emit the
#                                            flame.
#   3. .../render/ASCIICompositorEffect.gd . the renderer host (GDScript). Turns
#                                            each emitted puff into geometry and
#                                            reports what the GPU contributed.
#   4. godot_project/shaders/
#          cell_owner_product.glsl .......... the GPU shader (GLSL). Actually
#                                            paints the blue flame.
#
# WHY THAT MATTERS
# ----------------
# These four numbers are spread across four files, so any one of them can be
# edited alone. If someone lowers the damage threshold to "make the flame show
# up in a test", the proof stops meaning anything — the flame would appear
# without the plane really being hurt. Pinning the arithmetic here makes that
# edit fail loudly instead of silently weakening the evidence.
#
# "Contract" here just means: a test that asserts the SHAPE of the source code,
# not its runtime behaviour.
#
# RUN WITH
#   python3 scripts/test_fl4265_damage_interval_contract.py
#
# EXIT CODES
#   0  every enforced check passed AND every destructive mutant was caught
#   1  at least one check failed, or a mutant slipped past unnoticed
#
# SCOPE LIMIT (read this before quoting a green run)
#   Structural only. This proves the numbers agree in source. It proves NOTHING
#   about a running game: no GPU ran, no frame was drawn, no blue pixel was
#   observed. Visual acceptance still requires a real capture.
#
# Failure refs: FL-4265 (flyable airplane / dogfight damage), FL-4431 (retained
# scene cutover), FL-4359 (jitter lane that shares this harness).
# =============================================================================

from __future__ import annotations

import re
import sys
from pathlib import Path


# -----------------------------------------------------------------------------
# Where the sources live
# -----------------------------------------------------------------------------
# This file sits in <repo>/scripts/, so one parent step reaches the repo root.
REPO = Path(__file__).resolve().parents[1]

# Each entry maps a short nickname (used throughout this file) to the real path.
# Reading through a table like this is what lets the mutation harness at the
# bottom swap in a DAMAGED copy of one file without touching the disk.
SOURCE_PATHS: dict[str, Path] = {
    "server": REPO / "colyseus_server/src/sim/world.ts",
    "world": REPO / "godot_project/scripts/World.gd",
    "compositor": REPO / "godot_project/render/api/ASCIICompositorEffect.gd",
    "product": REPO / "godot_project/render/product/cell_owner_product.glsl",
    # The FL-4265 dogfight harness used to be read here by the retired seventh
    # section (see the RETIRED block further down). Nothing reads it any more,
    # so it is removed rather than left as a file this contract silently
    # requires to exist without ever inspecting it.
}


def load_sources() -> dict[str, str]:
    """Read every source file once and return {nickname: file text}.

    Returns a plain dictionary so the mutation harness can copy it, damage one
    entry, and hand the damaged copy back to the same check functions. Nothing
    is ever written back to disk.
    """
    sources: dict[str, str] = {}
    for name, path in SOURCE_PATHS.items():
        if not path.exists():
            raise SystemExit(f"missing required source file: {path}")
        sources[name] = path.read_text(encoding="utf-8")
    return sources


def squeeze(text: str) -> str:
    """Delete every space, tab, and newline from `text`.

    WHY: the same expression can be written across one line or five, with tabs
    or spaces, and still mean exactly the same thing. Removing all whitespace
    lets a single search string match any of those layouts, so this contract
    complains about real meaning changes instead of about reformatting.
    """
    return re.sub(r"\s+", "", text)


def slice_between(text: str, start_marker: str, end_marker: str) -> str:
    """Return the part of `text` that sits between two landmark strings.

    WHY: some rules are true only inside one function. Searching the whole file
    for them would accuse unrelated code of a defect it does not have. Slicing
    the file down to the region that actually owns the rule keeps the check
    honest.

    Returns an empty string when either landmark is missing or they appear in
    the wrong order, which the caller reports as a failed check.
    """
    start = text.find(start_marker)
    if start < 0:
        return ""
    end = text.find(end_marker, start + len(start_marker))
    if end < 0:
        return ""
    return text[start:end]


def read_int_constant(text: str, pattern: str) -> int | None:
    """Pull one whole number out of `text` using the regular expression `pattern`.

    `pattern` must contain exactly one capture group, and that group must
    capture the digits. Returns the number as an int, or None when the pattern
    does not match at all (which the caller reports as a failed check rather
    than crashing).
    """
    match = re.search(pattern, text)
    if match is None:
        return None
    return int(match.group(1))


def read_float_constant(text: str, pattern: str) -> float | None:
    """Same idea as read_int_constant, but for a decimal number such as 0.45."""
    match = re.search(pattern, text)
    if match is None:
        return None
    return float(match.group(1))


# -----------------------------------------------------------------------------
# Check accumulator
# -----------------------------------------------------------------------------
class Checker:
    """Collects pass/fail results so one run can report every problem at once.

    A test that stops at the first failure hides the other nine. This class
    records them all, then the caller prints the whole list.
    """

    def __init__(self) -> None:
        # Each failure is stored as a human-readable sentence describing what
        # the source was supposed to say.
        self.failures: list[str] = []
        self.passed = 0

    def check(self, condition: bool, description: str) -> None:
        """Record one expectation. `condition` True means the source is fine."""
        if condition:
            self.passed += 1
        else:
            self.failures.append(description)

    @property
    def ok(self) -> bool:
        """True when nothing failed."""
        return not self.failures


# =============================================================================
# SECTION 1 — the server owns the damage arithmetic
# =============================================================================
def section_server_damage_arithmetic(sources: dict[str, str], c: Checker) -> None:
    """Pin the three server numbers that decide how much damage a hit does.

    None of these may be changed to make a proof easier. They are gameplay
    balance, and the client is not allowed to have an opinion about them.
    """
    server = sources["server"]
    server_squeezed = squeeze(server)

    # A brand-new plane starts with this much structural HP.
    max_hp = read_int_constant(
        server, r"export\s+const\s+AIRPLANE_MAX_HP\s*=\s*(\d+)\s*;"
    )
    c.check(
        max_hp == 100,
        "world.ts must export AIRPLANE_MAX_HP = 100 (a new plane's structural HP)",
    )

    # Every tracer round that connects removes this much HP. The aeroplane gun
    # does not declare its own damage, so it falls back to ATTACK_DAMAGE.
    attack_damage = read_int_constant(
        server, r"const\s+ATTACK_DAMAGE\s*=\s*(\d+)\s*;"
    )
    c.check(
        attack_damage == 10,
        "world.ts must define ATTACK_DAMAGE = 10 (damage of one tracer round)",
    )

    # The hull-hit path must use that default when the projectile carries no
    # explicit damage of its own. `??` is JavaScript's "use the left value
    # unless it is null/undefined, otherwise use the right one".
    c.check(
        "damageAirplane(world,planeHit.plane,planeHit.flight,projectile.damage??ATTACK_DAMAGE"
        in server_squeezed,
        "an airborne tracer that strikes an enemy hull must spend "
        "projectile.damage ?? ATTACK_DAMAGE through damageAirplane()",
    )

    # The next three rules are true only INSIDE damageAirplane. The per-tick
    # flight advance also mirrors HP onto the world item, so searching the whole
    # file would let a deleted mirror inside damageAirplane hide behind that
    # unrelated copy. Slice the function out first.
    damage_fn = squeeze(
        slice_between(server, "function damageAirplane(", "function clearAttackHit(")
    )
    c.check(
        damage_fn != "",
        "damageAirplane() must be locatable in the server source",
    )
    c.check(
        "flight.hp=Math.max(0,flight.hp-amount);" in damage_fn,
        "damageAirplane() must subtract the damage and clamp structural HP at 0",
    )
    c.check(
        "plane.hp=flight.hp;" in damage_fn,
        "damageAirplane() must mirror flight HP onto the broadcast world item "
        "so the client can read it",
    )

    # Reaching zero destroys the plane. That is the far edge of the window this
    # contract exists to protect: past it there is no damage flame, only the
    # kill explosion.
    c.check(
        "if(flight.hp>0){return;}flight.destroyed=true;" in damage_fn,
        "damageAirplane() must destroy the plane exactly when HP reaches 0 and "
        "return early while HP remains above 0",
    )


# =============================================================================
# SECTION 2 — the client only REACTS to server HP
# =============================================================================
def section_client_emission_predicate(sources: dict[str, str], c: Checker) -> None:
    """Pin the client-side rule that decides when a damage puff is emitted.

    Law 2 of the repository architecture says the server owns gameplay state.
    The client is allowed to look at HP and draw something; it is never allowed
    to change HP. These checks make that visible in the source.
    """
    world = sources["world"]
    world_squeezed = squeeze(world)

    # How hurt the plane must be before any flame is drawn, as a fraction of
    # its maximum HP. 0.45 means "at least 45% of the hull is gone".
    threshold = read_float_constant(
        world, r"const\s+AIRPLANE_SMOKE_DAMAGE_THRESHOLD\s*:=\s*([0-9.]+)"
    )
    c.check(
        threshold == 0.45,
        "World.gd must define AIRPLANE_SMOKE_DAMAGE_THRESHOLD := 0.45 — "
        "lowering it would fabricate damage evidence",
    )

    # damage_ratio is "fraction of hull lost", derived purely from server HP.
    c.check(
        "vardamage_ratio:=clampf(1.0-float(plane_hp)/float(plane_max_hp),0.0,1.0)"
        in world_squeezed,
        "World.gd must derive damage_ratio as 1 - hp/max_hp from server truth",
    )

    # The emission gate. All three conditions matter:
    #   is_airborne   - a plane sitting on the ground must not trail flame
    #   not is_dead   - a destroyed plane explodes instead of smoking
    #   ratio >= 0.45 - the plane must genuinely be badly hurt
    c.check(
        "ifis_airborneandnotis_deadanddamage_ratio>=AIRPLANE_SMOKE_DAMAGE_THRESHOLD:"
        in world_squeezed,
        "World.gd must emit the damage trail only while the plane is airborne, "
        "the pilot is alive, and damage_ratio has reached the threshold",
    )

    # The HP values must be read from the plane world item the server broadcast,
    # never invented locally.
    c.check(
        "varplane_hp:=int(pilot_plane.get(\"hp\",0))" in world_squeezed
        and "varplane_max_hp:=maxi(1,int(pilot_plane.get(\"max_hp\",plane_hp)))"
        in world_squeezed,
        "World.gd must read hp and max_hp from the server plane snapshot",
    )


# =============================================================================
# SECTION 3 — the derived capture window
# =============================================================================
def section_derived_damage_interval(sources: dict[str, str], c: Checker) -> None:
    """Compute the flame window from the pinned numbers and assert its edges.

    This is the section that actually protects the proof design. Given
    HP=100, damage=10 and threshold=0.45 the arithmetic is forced:

        hits:   0    1    2    3    4    5    6    7    8    9    10
        hp:   100   90   80   70   60   50   40   30   20   10     0
        lost: 0.00 0.10 0.20 0.30 0.40 0.50 0.60 0.70 0.80 0.90  1.00
        flame: no   no   no   no   no  YES  YES  YES  YES  YES  destroyed

    So the flame may only be captured while HP sits between 10 and 50
    inclusive — after the 5th on-target hit and before the 10th. If any of the
    three inputs moves, these expected edges move with it and this section
    turns red, which is the whole point.
    """
    server = sources["server"]
    world = sources["world"]

    max_hp = read_int_constant(
        server, r"export\s+const\s+AIRPLANE_MAX_HP\s*=\s*(\d+)\s*;"
    )
    damage = read_int_constant(server, r"const\s+ATTACK_DAMAGE\s*=\s*(\d+)\s*;")
    threshold = read_float_constant(
        world, r"const\s+AIRPLANE_SMOKE_DAMAGE_THRESHOLD\s*:=\s*([0-9.]+)"
    )

    # A missing constant already failed its own section; bail out rather than
    # crash on arithmetic with None.
    if max_hp is None or damage is None or threshold is None:
        c.check(
            False,
            "cannot derive the damage interval because one pinned constant is "
            "missing from its source file",
        )
        return

    # Walk the hits one at a time exactly as the server would.
    first_emitting_hit: int | None = None
    last_emitting_hit: int | None = None
    destruction_hit: int | None = None
    hp = max_hp
    for hit_number in range(1, max_hp * 2):
        hp = max(0, hp - damage)
        if hp <= 0:
            # The plane is destroyed. Emission has already stopped, because the
            # client's gate requires the pilot to still be alive.
            destruction_hit = hit_number
            break
        ratio = 1.0 - (hp / max_hp)
        if ratio >= threshold:
            if first_emitting_hit is None:
                first_emitting_hit = hit_number
            last_emitting_hit = hit_number

    c.check(
        first_emitting_hit == 5,
        "the damage flame must first become legal on the 5th on-target hull hit "
        f"(computed: {first_emitting_hit})",
    )
    c.check(
        last_emitting_hit == 9,
        "the damage flame must remain legal through the 9th hull hit "
        f"(computed: {last_emitting_hit})",
    )
    c.check(
        destruction_hit == 10,
        "the plane must be destroyed on the 10th hull hit, closing the window "
        f"(computed: {destruction_hit})",
    )

    # State the window in HP terms too, because that is the number a capture
    # harness can actually read out of a snapshot mid-flight.
    window_high = max_hp - damage * 5   # HP right after the 5th hit
    window_low = max_hp - damage * 9    # HP right after the 9th hit
    c.check(
        (window_high, window_low) == (50, 10),
        "the capture window must be structural HP in [10, 50] inclusive "
        f"(computed: [{window_low}, {window_high}])",
    )


# =============================================================================
# SECTION 4 — host and shader must agree on which bit means which material
# =============================================================================
def section_bit_and_material_mirrors(sources: dict[str, str], c: Checker) -> None:
    """Pin the shared numbering between the GPU shader and its host reader.

    The shader sets one bit in a 32-bit "marker word" per screen subcell to say
    "this stage changed the picture here". The host later reads that word back
    and counts the bits. If the two sides ever disagree about which bit number
    belongs to which effect, the receipt reports the WRONG lane while still
    looking perfectly healthy — the worst kind of failure, because it is green.
    """
    product = sources["product"]
    compositor = sources["compositor"]
    product_squeezed = squeeze(product)
    compositor_squeezed = squeeze(compositor)

    # Shader side. `1u << 26u` means "the 27th bit counting from zero".
    c.check(
        "constuintPRODUCT_CONTRIBUTED_AIRPLANE_DAMAGE_VAPOR_BIT=1u<<26u;"
        in product_squeezed,
        "the shader must reserve bit 26 for the airplane damage flame",
    )
    c.check(
        "constuintPRODUCT_CONTRIBUTED_AIRPLANE_CONTRAIL_BIT=1u<<31u;"
        in product_squeezed,
        "the shader must reserve bit 31 for the ordinary airplane contrail",
    )

    # Host side. Same two bit numbers, written in GDScript.
    c.check(
        "constPRODUCT_CONTRIBUTED_AIRPLANE_DAMAGE_VAPOR_BIT:=1<<26"
        in compositor_squeezed,
        "the renderer host must mirror the damage-flame bit as 1 << 26",
    )
    c.check(
        "constPRODUCT_CONTRIBUTED_AIRPLANE_CONTRAIL_BIT:=1<<31"
        in compositor_squeezed,
        "the renderer host must mirror the contrail bit as 1 << 31",
    )

    # The shader must actually SET bit 26 when it paints a material-211 face,
    # and bit 31 for material 207. Setting the wrong one would swap the lanes.
    c.check(
        "if(vapor_layers.airplane_vapor.facts.material_id==207u){"
        "secondary_effect_contribution_bits|="
        "PRODUCT_CONTRIBUTED_AIRPLANE_CONTRAIL_BIT;}elseif("
        "vapor_layers.airplane_vapor.facts.material_id==211u){"
        "secondary_effect_contribution_bits|="
        "PRODUCT_CONTRIBUTED_AIRPLANE_DAMAGE_VAPOR_BIT;}" in product_squeezed,
        "the shader must raise bit 31 for material 207 and bit 26 for material "
        "211, and must not swap them",
    )

    # The capture receipt must state the mapping in its own data, so a reader
    # of the receipt never has to guess which material a bit counted.
    c.check(
        "constFL4431_PRODUCT_MAT_AIRPLANE_CONTRAIL:=207" in compositor_squeezed
        and "constFL4431_PRODUCT_MAT_AIRPLANE_DAMAGE_FLAME:=211"
        in compositor_squeezed,
        "the renderer host must name material 207 (contrail) and material 211 "
        "(damage flame) as constants rather than bare literals in the receipt",
    )
    c.check(
        '"product_mat211_material_id":FL4431_PRODUCT_MAT_AIRPLANE_DAMAGE_FLAME,'
        in compositor_squeezed
        and '"product_mat211_contribution_bit":PRODUCT_CONTRIBUTED_AIRPLANE_DAMAGE_VAPOR_BIT,'
        in compositor_squeezed,
        "the capture receipt must publish which material id and which bit the "
        "material-211 count came from",
    )

    # The two airplane counts must both be present and both be summed from the
    # per-view receipts. A count that exists but is never accumulated is a
    # permanently-zero field that reads as proof of absence.
    c.check(
        '"product_mat211_contributed_subcell_count":0,' in compositor_squeezed
        and '"product_mat211_contributed_subcell_count"])+int(receipt.get("airplane_damage_vapor_contributed_subcells",0))'
        in compositor_squeezed,
        "the material-211 subcell count must be accumulated from the per-view "
        "airplane_damage_vapor_contributed_subcells receipts",
    )
    c.check(
        'summary["product_mat211_contribution_receipt_available"]=receipt_available'
        in compositor_squeezed,
        "the material-211 lane must publish receipt availability beside the "
        "material-207 lane",
    )


# =============================================================================
# SECTION 5 — both airplane paints ride ONE geometry selector
# =============================================================================
def section_shared_airframe_four_selector(sources: dict[str, str], c: Checker) -> None:
    """Pin the fact that contrail and damage flame share one geometry lane.

    Each puff of vapour or smoke is drawn as a small flat square ("quad") that
    always turns to face the camera. Both the white contrail and the blue damage
    flame are tagged with "airframe id 4", which the shader reads back as
    `actor_effect_kind == 4`. One selector picks the nearest such quad; only
    afterwards does a colour rule look at the material id.

    The consequence, and the reason this section exists: a GEOMETRY bug in that
    shared lane breaks BOTH paints at once, and proving the contrail works does
    NOT prove the damage flame works. They are two paints on one lane.
    """
    world = sources["world"]
    compositor = sources["compositor"]
    product = sources["product"]
    world_squeezed = squeeze(world)
    compositor_squeezed = squeeze(compositor)
    product_squeezed = squeeze(product)

    # Producer side: the damage smoke sample carries material 211 on airframe 4.
    c.check(
        "constAIRPLANE_MAT_SMOKE:=211" in world_squeezed,
        "World.gd must name the damage-flame material as 211",
    )
    c.check(
        "constVAPOR_DYNAMIC_AIRFRAME_ID:=4" in world_squeezed,
        "World.gd must name the shared vapor-lane airframe id as 4",
    )
    c.check(
        '"mat_id":AIRPLANE_MAT_SMOKE,"airframe_id":VAPOR_DYNAMIC_AIRFRAME_ID,'
        in world_squeezed,
        "the damage smoke sample must publish material 211 on the shared "
        "airframe-4 vapor lane",
    )

    # Host side: the contrail uses the same lane, so the two really are shared.
    c.check(
        '"mat_id":207,"airframe_id":4,' in compositor_squeezed,
        "the contrail sample must publish material 207 on the same airframe-4 "
        "lane as the damage flame",
    )
    c.check(
        "constFL4431_PRODUCT_VAPOR_SELECTOR_AIRFRAME_ID:=4" in compositor_squeezed
        and '"product_vapor_selector_airframe_id":FL4431_PRODUCT_VAPOR_SELECTOR_AIRFRAME_ID,'
        in compositor_squeezed,
        "the capture receipt must record that both airplane paints share the "
        "airframe-4 selector",
    )

    # Shader side: one composite traversal, gated on effect kind 4, routes
    # material 234 to bonfire smoke and materials 207/211 to airplane vapor.
    # Any other material reaches the explicit fail-closed branch.
    vapor_mover_region = squeeze(
        slice_between(
            product,
            "void vsb_retained_consider_vapor_mover(",
            "VsbRetainedVaporSelections vsb_retained_select_vapor_effect_candidates(",
        )
    )
    c.check(
        "if(facts.actor_effect_kind!=4u||facts.source_id==0u)continue;"
        in vapor_mover_region,
        "the retained vapor selector must accept only effect kind 4 faces",
    )
    c.check(
        "if(facts.material_id==234u){"
        "vsb_retained_consider_vapor_candidate("
        "ray,world_depth,mover.identity_range.x,local_face,"
        "facts,retained_anchor,selected.bonfire_smoke);"
        "}elseif(facts.material_id==207u||facts.material_id==211u){"
        "vsb_retained_consider_vapor_candidate("
        "ray,world_depth,mover.identity_range.x,local_face,"
        "facts,retained_anchor,selected.airplane_vapor);"
        "}else{" in vapor_mover_region
        and vapor_mover_region.count("continue;") == 3,
        "the vapor selector must admit exactly materials 207, 211 and 234 and "
        "fail closed on any other kind-4 material",
    )
    c.check(
        "||vapor.facts.actor_effect_kind!=4u" in product_squeezed,
        "the vapor paint style must re-check effect kind 4 before painting",
    )

    # The shared quad triangulation. A camera-facing square is stored as four
    # corners walking around its edge (bottom-left, bottom-right, top-right,
    # top-left). Splitting it into two triangles is only correct when both
    # triangles share the corner-0 to corner-2 diagonal: (0,1,2) and (0,2,3).
    #
    # Splitting on two DIFFERENT diagonals leaves a quarter of the square
    # covered by no triangle at all. Rays crossing that gap find no surface,
    # the selector returns nothing, and the paint silently never happens. That
    # is a real defect this lane has already suffered, so it is pinned here.
    #
    # Scope note: this rule is asserted ONLY over the dedicated vapor candidate
    # helper. Static world geometry and opaque airplane faces use different
    # owners and may legitimately carry different admission laws.
    dynamic_region = squeeze(
        slice_between(
            product,
            "void vsb_retained_consider_vapor_candidate(",
            "void vsb_retained_consider_vapor_mover(",
        )
    )
    c.check(
        dynamic_region != "",
        "the dynamic-mover selector function must be locatable in the shader",
    )
    correct_split = (
        "floatt_0=product_ray_triangle_t(ray,facts.row_0.xyz,facts.row_1.xyz,"
        "facts.row_2.xyz);floatt_1=product_ray_triangle_t(ray,facts.row_0.xyz,"
        "facts.row_2.xyz,facts.row_3.xyz);"
    )
    c.check(
        dynamic_region.count(correct_split) == 1,
        "the shared vapor candidate helper must split every admitted puff on "
        "the single corner-0 to corner-2 diagonal, as (0,1,2) + (0,2,3)",
    )
    broken_split = "facts.row_0.xyz,facts.row_1.xyz,facts.row_3.xyz"
    c.check(
        broken_split not in dynamic_region,
        "no dynamic-mover triangle may use the corner-1 to corner-3 diagonal; "
        "that mixed split leaves a quarter of every puff uncovered",
    )

    # The host must write the corners in the ring order the shader assumes.
    c.check(
        "raised_center-right-up,raised_center+right-up,raised_center+right+up,"
        "raised_center-right+up," in compositor_squeezed,
        "the host must emit puff corners in ring order (bottom-left, "
        "bottom-right, top-right, top-left) to match the shader's split",
    )


# =============================================================================
# SECTION 6 — absence must stay visible, never look like a measured zero
# =============================================================================
def section_absence_stays_visible(sources: dict[str, str], c: Checker) -> None:
    """Pin that an unavailable receipt reports null counts, not zero counts.

    A count of 0 means "Product looked and contributed nothing". A count of
    null means "nobody looked". Reporting the first when the second is true is
    how an unproven lane gets written up as a proven-empty one, so the receipt
    must blank its counts on every path where no measurement happened.
    """
    compositor = sources["compositor"]
    compositor_squeezed = squeeze(compositor)

    c.check(
        "func_fl4431_mark_product_contribution_counts_absent(summary:Dictionary)->void:"
        in compositor_squeezed,
        "the receipt must own one helper that blanks contributed-subcell counts "
        "when no measurement exists",
    )
    # Exactly two CALL sites are required: one for "the evidence switch was
    # never supplied" and one for "the readback produced nothing valid this
    # frame". The definition itself carries a type annotation, so it does not
    # match this call-shaped string and is not counted here.
    c.check(
        compositor_squeezed.count(
            "_fl4431_mark_product_contribution_counts_absent(summary)"
        ) >= 2,
        "both absence exits (evidence disabled, and no valid readback this "
        "frame) must blank the contributed-subcell counts",
    )
    c.check(
        "summary[count_field]=null" in compositor_squeezed,
        "the blanking helper must assign null, not zero",
    )
    c.check(
        'summary["absence_reason"]=("--fl4431-secondary-effect-evidencewasnotsupplied")'
        in compositor_squeezed
        and 'summary["absence_reason"]="novalidProductb62readbackthisframe"'
        in compositor_squeezed,
        "each absence path must also name its reason in words",
    )


# =============================================================================
# RETIRED (FL-4265 / FL-4431) — the former SECTION 7,
# "tracer-only gate is cross-lane"
# =============================================================================
# This contract used to carry a seventh section whose only job was to record,
# truthfully, that a too-weak pass criterion still lived in a file this contract
# did not own: run_fl4265_airplane_dogfight_proof.gd declared success whenever a
# tracer round was merely VISIBLE in the frame. A visible tracer is ordnance
# LEAVING a muzzle; it says nothing about ordnance ARRIVING, and so it showed no
# hull hit, no HP drop, no crossed damage threshold, no material-211 face, and
# no blue pixel.
#
# That section deliberately asserted the weak gate was STILL PRESENT, so that a
# passing run of this contract could never be misread as evidence the FL-4265
# criterion had been retired. Its own failure message carried the instruction
# for the day it finally went red: delete the check, do not weaken it.
#
# That day arrived. The FL-4265 dogfight lane rewrote its harness and physically
# removed the gate. The pass expression is now:
#
#     var ok := two_airplane_render_facts and bot_airborne_at >= 0 and captured
#
# with no tracer term at all. `has_tracer` survives in that file only as an
# observation feeding the `bot_tracer_observed` / `frames_to_tracer` receipt
# fields, which report what was seen without gating anything on it.
#
# So the section is deleted rather than rewritten. Re-pointing it at some new
# token would have been the "weakening" its own comment forbade: a check that
# quietly changes what it guards while still looking like the original
# protection. The sections carrying real load — the FL-4265 damage arithmetic,
# the material/bit identity, the shared airframe-4 selector, and the FL-4431
# absence-null rules — are untouched above.


# -----------------------------------------------------------------------------
# The full contract, as one ordered list
# -----------------------------------------------------------------------------
SECTIONS = [
    ("server damage arithmetic", section_server_damage_arithmetic),
    ("client emission predicate", section_client_emission_predicate),
    ("derived damage interval", section_derived_damage_interval),
    ("bit and material mirrors", section_bit_and_material_mirrors),
    ("shared airframe-4 selector", section_shared_airframe_four_selector),
    ("absence stays visible", section_absence_stays_visible),
]


def run_contract(sources: dict[str, str]) -> Checker:
    """Run every section against one set of source texts and return the results."""
    c = Checker()
    for _name, section in SECTIONS:
        section(sources, c)
    return c


# =============================================================================
# MUTATION HARNESS
# =============================================================================
# A green test is only worth something if it CAN turn red. This harness takes
# the real source text, damages one specific thing in memory, and re-runs the
# contract. If the contract still passes, the damage went unnoticed, which
# means the corresponding check is decorative and the harness reports that as a
# failure of this file — not of the code under test.
#
# Nothing here writes to disk. Each mutant is a string replacement applied to a
# COPY of the loaded sources.
# =============================================================================

# Each mutant is (label, source nickname, text to find, text to replace it with).
# The replacements are deliberately the kind of edit a hurried agent would make
# to force a proof green.
MUTANTS: list[tuple[str, str, str, str]] = [
    (
        "lower the damage threshold so a healthy plane appears wounded",
        "world",
        "const AIRPLANE_SMOKE_DAMAGE_THRESHOLD := 0.45",
        "const AIRPLANE_SMOKE_DAMAGE_THRESHOLD := 0.05",
    ),
    (
        "raise the plane's starting HP so the window silently moves",
        "server",
        "export const AIRPLANE_MAX_HP = 100;",
        "export const AIRPLANE_MAX_HP = 200;",
    ),
    (
        "double tracer damage so fewer hits are needed",
        "server",
        "const ATTACK_DAMAGE = 10;",
        "const ATTACK_DAMAGE = 20;",
    ),
    (
        "stop clamping structural HP at zero",
        "server",
        "flight.hp = Math.max(0, flight.hp - amount);",
        "flight.hp = flight.hp - amount;",
    ),
    (
        "stop mirroring flight HP onto the broadcast world item",
        "server",
        "  plane.hp = flight.hp;\n  if (flight.hp > 0) {",
        "  if (flight.hp > 0) {",
    ),
    (
        "drop the airborne requirement so a grounded plane trails flame",
        "world",
        "if is_airborne and not is_dead and damage_ratio >= AIRPLANE_SMOKE_DAMAGE_THRESHOLD:",
        "if not is_dead and damage_ratio >= AIRPLANE_SMOKE_DAMAGE_THRESHOLD:",
    ),
    (
        "publish damage smoke on a different airframe lane",
        "world",
        '"airframe_id": VAPOR_DYNAMIC_AIRFRAME_ID,\n\t\t\t"fade_seconds": SMOKE_DYNAMIC_FADE_SECONDS,',
        '"airframe_id": 7,\n\t\t\t"fade_seconds": SMOKE_DYNAMIC_FADE_SECONDS,',
    ),
    (
        "swap the shader bit numbers so 211 reports as the contrail",
        "product",
        "const uint PRODUCT_CONTRIBUTED_AIRPLANE_DAMAGE_VAPOR_BIT = 1u << 26u;",
        "const uint PRODUCT_CONTRIBUTED_AIRPLANE_DAMAGE_VAPOR_BIT = 1u << 31u;",
    ),
    (
        "desynchronise the host bit mirror from the shader",
        "compositor",
        "const PRODUCT_CONTRIBUTED_AIRPLANE_DAMAGE_VAPOR_BIT := 1 << 26",
        "const PRODUCT_CONTRIBUTED_AIRPLANE_DAMAGE_VAPOR_BIT := 1 << 25",
    ),
    (
        "widen the vapor selector to admit any kind-4 material",
        "product",
        "} else if (facts.material_id == 207u\n"
        "\t\t\t\t|| facts.material_id == 211u) {",
        "} else if (true) {",
    ),
    (
        "re-break the puff quad onto two different diagonals",
        "product",
        "float t_0 = product_ray_triangle_t(\n"
        "\t\tray, facts.row_0.xyz, facts.row_1.xyz, facts.row_2.xyz);\n"
        "\tfloat t_1 = product_ray_triangle_t(\n"
        "\t\tray, facts.row_0.xyz, facts.row_2.xyz, facts.row_3.xyz);\n"
        "\tfloat depth = 1.0e19;",
        "float t_0 = product_ray_triangle_t(\n"
        "\t\tray, facts.row_0.xyz, facts.row_1.xyz, facts.row_3.xyz);\n"
        "\tfloat t_1 = product_ray_triangle_t(\n"
        "\t\tray, facts.row_0.xyz, facts.row_3.xyz, facts.row_2.xyz);\n"
        "\tfloat depth = 1.0e19;",
    ),
    (
        "report an absent measurement as a measured zero",
        "compositor",
        "summary[count_field] = null",
        "summary[count_field] = 0",
    ),
    (
        "stop accumulating the material-211 subcell count",
        "compositor",
        'summary["product_mat211_contributed_subcell_count"] = (\n'
        '\t\t\tint(summary["product_mat211_contributed_subcell_count"])\n'
        '\t\t\t+ int(receipt.get(\n'
        '\t\t\t\t"airplane_damage_vapor_contributed_subcells", 0\n'
        '\t\t\t))\n'
        '\t\t)',
        "pass",
    ),
    (
        "hide the material-211 availability flag",
        "compositor",
        'summary["product_mat211_contribution_receipt_available"] = receipt_available',
        'summary["product_mat211_contribution_receipt_available"] = false',
    ),
]

# A control mutant that changes only a comment. It MUST NOT be caught: if this
# one turns the contract red, the contract is matching on prose rather than on
# meaning, which makes it brittle and untrustworthy for the opposite reason.
COMMENT_ONLY_CONTROL: tuple[str, str, str, str] = (
    "comment-only control (must NOT be caught)",
    "world",
    "# FL-4265 dogfighting damage VFX: a pure render reaction to server actor truth.",
    "# FL-4265 dogfighting damage VFX: reworded comment, identical behaviour.",
)


def apply_mutant(
    sources: dict[str, str], nickname: str, find: str, replace: str
) -> dict[str, str] | None:
    """Return a copy of `sources` with one text replacement applied.

    Returns None when `find` does not appear in the named source, which means
    the mutant itself has gone stale (the code it targeted was refactored). A
    stale mutant is reported as a failure so this file cannot quietly stop
    testing anything.
    """
    if find not in sources[nickname]:
        return None
    mutated = dict(sources)
    mutated[nickname] = sources[nickname].replace(find, replace, 1)
    return mutated


def run_mutation_suite(sources: dict[str, str]) -> list[str]:
    """Run every destructive mutant plus the control. Return a list of problems."""
    problems: list[str] = []

    for label, nickname, find, replace in MUTANTS:
        mutated = apply_mutant(sources, nickname, find, replace)
        if mutated is None:
            problems.append(
                f"STALE MUTANT: '{label}' no longer matches {nickname}; the "
                "targeted code moved, so this mutation tests nothing"
            )
            continue
        result = run_contract(mutated)
        if result.ok:
            problems.append(
                f"SURVIVED: '{label}' broke the source and every check still "
                "passed — the matching contract check is decorative"
            )

    label, nickname, find, replace = COMMENT_ONLY_CONTROL
    control = apply_mutant(sources, nickname, find, replace)
    if control is None:
        problems.append(
            f"STALE CONTROL: '{label}' no longer matches {nickname}"
        )
    else:
        result = run_contract(control)
        if not result.ok:
            problems.append(
                f"OVER-SENSITIVE: '{label}' changed nothing but a comment and "
                f"the contract still failed: {result.failures[0]}"
            )

    return problems


def main() -> int:
    """Run the contract against the real sources, then mutation-test it."""
    sources = load_sources()

    result = run_contract(sources)
    if not result.ok:
        print("FL-4265 damage-interval contract FAILED:")
        for failure in result.failures:
            print(f"  - {failure}")
        return 1

    problems = run_mutation_suite(sources)
    if problems:
        print("FL-4265 damage-interval contract: mutation suite FAILED:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(
        f"FL-4265 damage-interval contract: all {result.passed} enforced checks "
        f"passed; {len(MUTANTS)} destructive mutants all caught; comment-only "
        "control correctly ignored."
    )
    print(
        "Structural only. No runtime, no GPU, no frame, no blue pixel was "
        "observed. Visual acceptance still requires a real capture inside the "
        "HP [10, 50] window across two time-separated frames."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
