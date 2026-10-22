# =============================================================================
# test_fl4216_reflection_is_a_vsb_ray_query_contract.py
# =============================================================================
#
# WHAT THIS FILE IS
# -----------------
# This is a "contract test". It does not run the game and it does not look at
# any picture. It reads the project's own source files as plain text and checks
# that certain rules are still literally true in them. Contract tests exist to
# stop an architecture rule from quietly rotting away over months of edits.
#
# WHAT RULE IS BEING PROTECTED HERE
# ---------------------------------
# The game draws the world as text characters. A compute shader called
# visible_surface_builder.glsl ("VSB") shoots one straight line — a "ray" — out
# of the camera for every quarter-character cell on screen and decides what that
# cell is looking at. Water is one flat horizontal sheet.
#
# FL-4216 adds reflections on that water sheet by bouncing the SAME ray off the
# water and asking the SAME scene what stands above it in the mirrored direction.
# That is a "mirrored ray".
#
# WHAT THE ANSWER IS USED FOR (operator successor ruling)
# -------------------------------------------------------
# Paper darkening read as a water shadow. Generic reflected-mass glyph ladders
# hid the authored surface. Those paint owners are deleted. A valid daylight hit
# may now blend the hit material's existing matlib foreground/background pair
# beneath the authoritative wave glyph. Shoreline glyph plus foreground win last;
# full night selects the reflection-OFF VSB
# specialization while sparse moon glitter remains a Product light term.
#
# THE PREDECESSOR'S DEATH STORY, IN ONE SENTENCE
# ----------------------------------------------
# An earlier reflection implementation (godot_project/shaders/
# fl4129_reflected_terrain_product.glsl, physically deleted at commit 1322c355f)
# was removed not for being slow and not for being ugly, but because it had
# become a SECOND CAMERA: it carried a private copy of the camera matrix, read
# the raw depth buffer directly, wrote its own extra screen-sized images from
# its own extra GPU pass, and silently substituted a stale snapshot whenever it
# had no real answer — which hid the fact that 202 of its 357 water rows had no
# reflection at all.
#
# THE FOUR INVARIANTS THIS FILE ENFORCES
# --------------------------------------
#   G1  No second camera owner: no private inverse-view-projection matrix, no
#       second view or projection basis. The mirrored ray is derived from the
#       ray VSB already built.
#   G2  No raw depth-texture read: reflected surfaces are found only through the
#       scene_query_* helpers the primary ray already uses.
#   G3  No separate screen-resolution image products and no extra dispatch: the
#       answer rides on the record this invocation was already writing. The only
#       added storage is one small FIXED-SIZE counter block (896 bytes: seven
#       outcome lanes separated by a 128-byte stride intended to reduce false
#       sharing; its hardware effect is unmeasured). What makes
#       it legal is that its size never varies with the screen resolution.
#   G4  No silent fallback: a mirrored ray that finds nothing leaves water ink
#       exactly as it was AND increments a counter, so absence is always
#       countable and never disguised as a result.
#
# It also pins the launch flag as DEFAULT OFF and checks that the FL-4408
# deletion guard which forbids the dead predecessor file is still intact.
#
# HOW TO RUN IT
#   python3 -m pytest scripts/test_fl4216_reflection_is_a_vsb_ray_query_contract.py
#   python3 scripts/test_fl4216_reflection_is_a_vsb_ray_query_contract.py
# =============================================================================

from pathlib import Path

import pytest


# The repository root is two directories up from this file (scripts/ -> repo).
ROOT = Path(__file__).resolve().parents[1]


def text(rel: str) -> str:
    """Read one repository file as a plain string.

    `rel` is a path relative to the repository root. Every check below works on
    raw source text on purpose: the rules being protected are about what the
    source is allowed to CONTAIN, so reading it literally is the whole point.
    """
    return (ROOT / rel).read_text()


def strip_comments(source: str) -> str:
    """Return the same source with every "//" comment removed.

    The bans below are about what the shader DOES, not about what its prose
    says. This file's own explanations name the forbidden things on purpose
    (you cannot warn against "a private inverse-view-projection matrix" without
    writing that phrase down), and the shader's explanations do the same. So
    before asserting "this symbol must not appear", the commentary is stripped
    and only executable lines are searched.

    Only line comments are handled because this project's shaders use "//"
    exclusively; a "/* */" block would need its own removal step.
    """
    cleaned_lines = []
    for line in source.splitlines():
        comment_start = line.find("//")
        if comment_start >= 0:
            line = line[:comment_start]
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def _glsl_function(source: str, signature: str) -> str:
    """Return one complete GLSL function body, isolated from its neighbours.

    `signature` is the opening text of the function, for example
    "uint fl4216_reflection_word(". The function walks forward from there
    counting curly braces: every "{" goes one level deeper and every "}" comes
    one level back out, so when the count returns to zero we have reached the
    matching closing brace and therefore the end of that one function.

    This matters because a check like "this file must never mention depth_tex"
    is far too blunt; what we actually need is "the REFLECTION function must
    never mention depth_tex", and that requires cutting the function out first.
    """
    start = source.index(signature)
    opening_brace = source.index("{", start)
    depth = 0
    for index in range(opening_brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise ValueError(f"unterminated GLSL function: {signature}")


def comment_blocks(source: str, marker: str) -> list:
    """Group a source file's comment lines into contiguous BLOCKS.

    WHY THIS EXISTS
    ---------------
    The two tests at the bottom of this file police PROSE, not code: they check
    that the explanations written beside the FL-4216 reflection lane still
    describe what the lane actually does. Prose cannot be checked line by line,
    because a single explanation is normally spread over ten or twenty
    consecutive comment lines and the sentence that matters may be split across
    two of them. So the text is first re-assembled into blocks.

    A "block" here is simply a run of consecutive lines that are ALL comments,
    ended by the first line that is not a comment. That matches how these files
    are written: one explanation, then the code it explains.

    `marker` is the token a comment line starts with — "//" for the GLSL shaders
    and "#" for GDScript. Only WHOLE-LINE comments are collected; a comment
    written on the end of a code line is ignored, which is fine because every
    block this file cares about is a standalone prose paragraph.

    Returns a list of (first_line_number, block_text) pairs, where the line
    number is 1-based so it can be quoted straight into a failure message.
    """
    blocks = []
    current_lines = []
    current_start = 0
    for line_number, line in enumerate(source.splitlines(), start=1):
        if line.strip().startswith(marker):
            # Still inside a comment block: remember the line, and remember
            # where the block began if this is its first line.
            if not current_lines:
                current_start = line_number
            current_lines.append(line)
            continue
        # A non-comment line ends whatever block was open.
        if current_lines:
            blocks.append((current_start, "\n".join(current_lines)))
            current_lines = []
    if current_lines:
        blocks.append((current_start, "\n".join(current_lines)))
    return blocks


# Words that make a comment block "about the FL-4216 reflection lane".
#
# WHY THE SCOPING IS DONE THIS WAY, AND WHY IT MATTERS A LOT HERE
# ---------------------------------------------------------------
# ASCIICompositorEffect.gd contains an UNRELATED comment about the leaf-particle
# bank header, which really is a sixteen-byte buffer and correctly says so. If
# the tests below searched the whole file for the phrase "sixteen bytes" they
# would flag that innocent, accurate comment forever, and the usual response to a
# test that cries wolf is to delete the test.
#
# So a block is only examined when the block ITSELF mentions this lane: the FL
# number, the word reflection/reflected, the phrase "mirrored ray", or the
# binding number this lane owns. The leaf-particle block mentions none of those
# — it talks about leaf lanes, emitters, and a header magic value — so it can
# never be reached by these checks no matter what it says about byte counts.
FL4216_SCOPE_TOKENS = (
    "fl-4216",
    "fl4216",
    "reflection",
    "reflected",
    "mirrored ray",
    "binding 84",
)


def fl4216_comment_blocks(rel: str, marker: str) -> list:
    """Return only those comment blocks of a file that talk about this lane.

    `rel` is a repository-relative path and `marker` is that language's comment
    token. See FL4216_SCOPE_TOKENS above for exactly what "talks about this lane"
    means and why the scoping has to be this careful.
    """
    selected = []
    for line_number, block in comment_blocks(text(rel), marker):
        lowered = block.lower()
        if any(token in lowered for token in FL4216_SCOPE_TOKENS):
            selected.append((line_number, block))
    return selected


def block_containing(blocks: list, needle: str) -> str:
    """Return the one comment block that contains `needle`.

    Used to say "the block that carries invariant G3" and "the block that
    declares the receipt buffer" without hard-coding line numbers, which drift
    every time a paragraph is reworded.
    """
    matches = [block for _, block in blocks if needle in block]
    assert len(matches) == 1, f"expected exactly one block containing {needle!r}, found {len(matches)}"
    return matches[0]


def without_explicit_retrospective_sections(block: str) -> str:
    """Remove only delimited history paragraphs, preserving live text nearby.

    A whole comment block may contain both a historical explanation and the
    current contract. Exempting that whole block lets one retrospective marker
    hide a contradictory present-tense sentence. These boundaries remove only
    the two intentionally historical spans currently used by the FL-4216 source
    comments; every sentence before and after them remains subject to the ban.
    """
    boundaries = (
        ("HISTORICAL NOTE", "THE BOUNDARY THAT IS NOT RELAXED"),
        ("HISTORICAL NOTE.", "The flag is read once"),
        ("HISTORICAL NOTE.", "LIVE CONTRACT RESUMES."),
    )
    live = block
    for start_marker, end_marker in boundaries:
        lowered = live.lower()
        start = lowered.find(start_marker.lower())
        end = (
            lowered.find(end_marker.lower(), start + len(start_marker))
            if start >= 0
            else -1
        )
        if start >= 0 and end >= 0:
            live = live[:start] + live[end:]
    return live


# Paths of the files under contract, named once so a rename cannot silently
# turn a check into a no-op somewhere further down.
VSB_SHADER = "godot_project/render/backends/ortho/visibility/visible_surface_builder.glsl"
PRODUCT_SHADER = "godot_project/render/product/cell_owner_product.glsl"
COMPOSITOR_SHADER = "godot_project/render/ascii/raster/ascii_compositor.glsl"
COMPOSITOR_HOST = "godot_project/render/api/ASCIICompositorEffect.gd"
VSB_HOST = "godot_project/render/core/contracts/visibility/VisibleSurfaceABI.gd"
FL4408_GUARD_TEST = "scripts/test_fl4408_pixel_camera_context_contract.py"

# The exact file the FL-4408 deletion guard forbids. Recreating this path in any
# form would resurrect the deleted second-camera owner.
BANNED_PREDECESSOR_SHADER = "godot_project/shaders/fl4129_reflected_terrain_product.glsl"


def test_reflection_is_a_vsb_ray_query_not_a_second_camera_owner():
    """G1 and G2: one camera, one acceleration reader, no depth-buffer read."""
    vsb = text(VSB_SHADER)

    # The deleted predecessor's file must not come back under its own name.
    assert not ROOT.joinpath(BANNED_PREDECESSOR_SHADER).exists()

    reflection = _glsl_function(vsb, "uint fl4216_reflection_word(")
    # Executable text only; see strip_comments for why the prose is excluded.
    vsb_code = strip_comments(vsb)
    reflection_code = strip_comments(reflection)

    # G1 — no second camera owner anywhere in the shader that now casts the
    # mirrored ray. "inv_view_proj" is the name the predecessor used for its
    # private copy of the camera matrix; "inverse(" would be the other way to
    # manufacture one on the fly.
    assert "inv_view_proj" not in vsb_code
    assert "inverse(" not in reflection_code
    assert "mat4" not in reflection_code

    # G1 — the mirrored ray must be BUILT FROM the ray this shader already has,
    # not rebuilt from a projection. Both facts below come straight from the
    # primary ray and the already-selected water hit.
    assert "reflect(primary_ray.direction, plane_normal)" in reflection
    assert "water_hit.position" in reflection

    # G2 — no raw depth texture, no NDC unprojection, inside the reflection
    # function or anywhere else in this shader.
    assert "depth_tex" not in vsb_code
    assert "sampler2D depth" not in vsb_code
    assert "depth_tex" not in reflection_code

    # G2 — reflected surfaces use the primary ray's existing query entry points.
    # Dynamic kind 1 is the authoritative boat/airplane family and is filtered
    # before a leaf opens its face range. Water remains excluded.
    assert "scene_query_dynamic_tlas_prevalidated(" in reflection_code
    assert "dynamic_geometry_descriptor.counts_revision.x" in reflection_code
    assert "1u," in reflection_code
    assert "scene_query_static_tlas(mirrored_ray, reflected)" in reflection_code
    assert "scene_query_heightfield(mirrored_ray, reflected)" in reflection_code
    assert "scene_query_water(mirrored_ray" not in reflection_code
    dynamic_leaf = strip_comments(
        _glsl_function(vsb, "bool scene_query_dynamic_leaf(")
    )
    assert "required_kind_id" in dynamic_leaf
    assert dynamic_leaf.index("instance.identity_revision.x != required_kind_id") < dynamic_leaf.index(
        "float leaf_enter_depth;"
    )

    # The primary chain itself must remain exactly as it was.
    assert "scene_query_water(ray, nearest)" in vsb_code
    assert "scene_query_heightfield(ray, nearest)" in vsb_code


def test_reflection_adds_no_image_products_and_no_second_dispatch():
    """G3: no screen-sized image outputs, no extra GPU pass."""
    vsb = text(VSB_SHADER)
    host = text(COMPOSITOR_HOST)
    vsb_host = text(VSB_HOST)

    # G3 — the visible-surface builder writes storage buffers only. An image2D
    # declaration here would be the first step back toward the predecessor's
    # extra screen-resolution colour/depth pair. Prose is stripped first; see
    # strip_comments for why.
    vsb_code = strip_comments(vsb)
    assert "image2D" not in vsb_code
    assert "uniform image" not in vsb_code

    # G3 — the only new binding is a counter block whose length is spelled out of
    # two compile-time constants and therefore never varies at runtime. That
    # constant length is load-bearing: a receipt whose size does not scale with
    # the window cannot become a picture. 896 bytes is seven counters separated
    # by a 128-byte stride intended to reduce false sharing. Its hardware effect
    # is deliberately not asserted because it remains unmeasured (FL-4216).
    assert "layout(set = 0, binding = 84, std430) buffer Fl4216ReflectionReceipt" in vsb
    assert (
        "uint counters[FL4216_RECEIPT_LANE_COUNT * FL4216_RECEIPT_LANE_STRIDE];" in vsb
    )
    assert "const FL4216_RECEIPT_BUFFER_BYTES := 896" in vsb_host
    assert "const FL4216_RECEIPT_LANE_COUNT := 7" in vsb_host
    assert "const FL4216_RECEIPT_LANE_STRIDE_BYTES := 128" in vsb_host
    assert (
        "const BINDING_FL4216_REFLECTION_RECEIPT := 84" in vsb_host
    )

    # G3 — the host binds that block as a STORAGE BUFFER, never as an image.
    assert (
        "receipt_uniform.uniform_type = RenderingDevice.UNIFORM_TYPE_STORAGE_BUFFER"
        in host
    )

    # G3 — exactly one place binds the VSB pipeline into a compute list, so the
    # reflection work rides the dispatch that already existed. Two would mean a
    # second pass had been introduced.
    assert host.count(
        "rd.compute_list_bind_compute_pipeline(product_compute_list, vsb_dispatch_pipeline)"
    ) == 1
    assert "_vsb_reflection_off_pipeline = rd.compute_pipeline_create(" not in host
    assert "replacement_reflection_off_pipeline = rd.compute_pipeline_create(" in host

    # G3 — no reflection-specific shader file is compiled anywhere. The host is
    # GDScript, whose line comments also start with "#", so only the executable
    # text is searched here for the same reason as above.
    host_code = "\n".join(
        line.split("#")[0] for line in host.splitlines()
    )
    assert "fl4129_reflected_terrain_product" not in host_code
    assert "reflected_terrain" not in host_code


def test_reflection_absence_is_countable_and_never_substituted():
    """G4: an empty result keeps the existing colour and increments a counter."""
    vsb = text(VSB_SHADER)
    product = text(PRODUCT_SHADER)

    # G4 — every possible outcome of one mirrored ray has its OWN counter. A
    # single merged "misses" number is not enough: it cannot separate "traced
    # correctly and nothing was there" from "the scene query aborted", and those
    # two produce identical screenshots. Each lane below must exist by name.
    for lane in (
        "FL4216_RECEIPT_RAYS_CAST",
        "FL4216_RECEIPT_HITS",
        "FL4216_RECEIPT_NO_HIT",
        "FL4216_RECEIPT_CLIP_REJECTS",
        "FL4216_RECEIPT_DEGENERATE_DIRECTION",
        "FL4216_RECEIPT_INVALID_BOUNDS",
        "FL4216_RECEIPT_QUERY_INVALID",
    ):
        assert lane in vsb

    # G4 — and the shader must actually SET the cause bit at each early return,
    # otherwise the lanes would exist while every empty result still landed in
    # one bucket. These are the four distinct empty-result exits.
    assert "return word | FL4216_REFLECTION_DEGENERATE_DIRECTION;" in vsb
    assert "return word | FL4216_REFLECTION_INVALID_BOUNDS;" in vsb
    assert "return word | FL4216_REFLECTION_QUERY_INVALID;" in vsb
    # The bare `return word;` is the correct-and-empty exit: armed, no cause.
    assert "outcome_lane = FL4216_RECEIPT_NO_HIT;" in vsb

    # G4 — the counter increment must be data-driven from the outcome lane, so a
    # future outcome cannot be added without also being counted.
    assert (
        "fl4216_receipt_slot(outcome_lane)], 1u);" in vsb
    )

    # G4 — the predecessor's fatal habit was reaching for a stale snapshot when
    # it had no answer. No such symbol may exist in this lane. Prose is stripped
    # first because this project's own post-mortems name those dead symbols.
    vsb_code = strip_comments(vsb)
    product_code = strip_comments(product)
    assert "water_owner_snapshot" not in vsb_code
    assert "reflection_fallback" not in vsb_code
    assert "water_owner_snapshot" not in product_code

    # G4 — absence stays honest through the sole surviving colour-pair cue.
    decode = _glsl_function(
        product,
        "Fl4216ReflectedMaterial product_fl4216_decode_reflected_material(",
    )
    assert "if ((word & FL4216_REFLECTION_HIT) == 0u) return reflected;" in decode

    pair = _glsl_function(product, "void product_fl4216_apply_reflection_pair(")
    assert "if (reflected.present) {" in pair
    assert "if (!reflected.present) return;" not in pair

    # Reflection has no glyph selector. Generic, material, and morphology glyph
    # branches remain absent.
    candidate = _glsl_function(product, "uint water_glyph_candidate(")
    assert "return product_water_activity_glyph(s);" in candidate
    assert "vsb_product_selected_reflected_paint" not in candidate
    assert "morphology_phase" not in candidate
    assert "product_fl4216_reflection_background(" not in product_code


def test_fl4216_reflection_ray_is_canonical_default():
    """Normal runtime always enables reflection without launch-time opt-in."""
    vsb = text(VSB_SHADER)
    host = text(COMPOSITOR_HOST)

    # The switch is a specialization constant, resolved when the GPU program is
    # built. Direct shader compilation and the host path agree on enabled.
    assert (
        "layout(constant_id = 0) const bool VSB_FL4216_REFLECTION_RAY_ENABLED = true;"
        in vsb
    )

    # Every reflection side effect in main() sits behind that constant, so the
    # disarmed build cannot even branch into the new code path.
    main = _glsl_function(vsb, "void main() {")
    assert (
        "if (VSB_FL4216_REFLECTION_RAY_ENABLED && nearest.owner_kind == OWNER_WATER) {"
        in main
    )
    # The per-invocation receipt variable starts at zero, which is the value the
    # ABI has always carried in that lane.
    assert "uint fl4216_pending_reflection_word = 0u;" in vsb
    # The FL-4265 ownership contract pins this exact publication line, so the
    # reflection slice must not have altered it.
    assert (
        "visible_surface_products.records[record_index] = hit_record(nearest);"
        in main
    )

    _assert_reflection_atomic_ownership(vsb)

    # The consumer default agrees with VSB. Both are still specialized from the
    # same canonical host function so the producer and consumer cannot diverge.
    assert (
        "layout(constant_id = 18) const bool PRODUCT_FL4216_REFLECTION_RAY_ENABLED = true;"
        in text(PRODUCT_SHADER)
    )
    assert "product_fl4216_reflection_specialization.constant_id = 18" in host
    # A disarmed product build falls back to the ORIGINAL exactly-zero compare on
    # byte 92, which also preserves the cheap corruption tripwire that byte used
    # to provide for the whole 96-byte record.
    assert ": (record.reflection_word != 0u);" in text(PRODUCT_SHADER)

    # The DIAGNOSTIC is a separate switch from the FEATURE, so an armed run can
    # be measured without the counters and the readback distorting the sample.
    assert (
        "layout(constant_id = 1) const bool VSB_FL4216_REFLECTION_RECEIPTS_ENABLED = false;"
        in vsb
    )
    assert 'OS.get_cmdline_user_args().has("--fl4216-reflection-receipts")' in host
    assert "func is_fl4216_reflection_receipts_enabled() -> bool:" in host
    assert "fl4216_receipts_constant.constant_id = 1" in host
    # Receipts require the feature; counting rays that are never cast is
    # meaningless, so that state must be unreachable.
    assert "requested and is_fl4216_reflection_ray_enabled()" in host
    # Every atomic write sits behind the receipts constant, so a receipts-free
    # armed run performs no atomic traffic at all.
    # Candidate diagnostics have one independent atomic outside the reflection
    # receipt branch. The scoped helper above keeps that producer distinct.
    _assert_reflection_atomic_ownership(vsb)
    # The readback is cadence-limited rather than per-frame.
    assert "const FL4216_RECEIPT_PRINT_EVERY_N_DISPATCHES := 30" in host
    assert "if is_fl4216_reflection_receipts_enabled():" in host

    # Launch wrappers and environment state cannot silently disable reflection.
    assert '--fl4216-reflection-ray' not in host
    assert 'OS.get_environment("FL4216_REFLECTION_RAY")' not in host
    assert "func is_fl4216_reflection_ray_enabled() -> bool:" in host
    activation = host[
        host.index("func is_fl4216_reflection_ray_enabled() -> bool:") :
        host.index("func is_fl4216_reflection_receipts_enabled() -> bool:")
    ]
    assert "return true" in activation

    # The host feeds canonical activation into the pipeline as constant_id 0.
    assert "fl4216_reflection_constant.constant_id = 0" in host
    assert (
        "fl4216_reflection_constant.value = is_fl4216_reflection_ray_enabled()"
        in host
    )


def test_printed_receipt_field_names_claim_only_what_is_measured():
    """No printed evidence field may assert something it did not measure.

    This is the highest-stakes test in the file. The receipt line is the
    runtime-truth surface that gets pasted beside a screenshot of the water, so
    a field whose name overstates what it counted would silently corrupt the
    evidence record rather than the pixels.

    The original line printed the hit count under the name `tinted_subcells`,
    asserting that every reflected hit produced a visibly tinted water cell.
    That was not true: the paint owner is gated twice more, in a shader stage
    this counter cannot see, so the real painted count is strictly lower. The
    number is now reported as an explicitly labelled UPPER BOUND under a name
    that says what it actually is (FL-4216 review finding HIGH-1).
    """
    host = text(COMPOSITOR_HOST)

    # The fabricated identity must not come back in any PRINTED text. The
    # host's own comment block explains the old name on purpose, so only the
    # executable lines are searched.
    host_code = "\n".join(line.split("#")[0] for line in host.splitlines())
    assert "tinted_subcells" not in host_code

    # The honest name, marked in the printed text itself as an upper bound so a
    # reader of the log cannot mistake it even without this file to hand.
    assert "reflected_hits_bounced_to_product=%d(upper-bound,not-painted-count)" in host

    # The successor cue tints water by MATERIAL FAMILY, so the receipt now splits
    # the hit total by family — otherwise a green-tinted screenshot would have no
    # runtime-truth surface saying that foliage is what got reflected. Splitting a
    # number does not make it more certain, so the breakdown carries exactly the
    # same upper-bound warning in its own name as the total it came from.
    assert (
        "reflected_hits_by_material_family=[%s](upper-bound,not-painted-count)" in host
    )
    # The family labels are printed in the builder's packing order. A silent
    # reordering here would relabel every number in the receipt.
    assert (
        'const FL4216_RECEIPT_FAMILY_LABELS: Array[String] = [\n'
        '\t"unknown", "grass", "dirt", "rock", "foliage", "solid",\n'
        ']' in host
    )
    # The breakdown must be read out of the existing padded buffer, not out of new
    # counter lanes: new lanes would change the receipt buffer's size, which is a
    # constant owned by a file this lane does not touch.
    assert "func _fl4216_receipt_sub_lane(" in host
    assert "const FL4216_RECEIPT_SUB_FAMILY_BASE := 1" in host

    # The split causes must all reach the printed line, otherwise the receipt
    # still cannot separate "correctly empty" from "the query aborted".
    for field in (
        "rays_cast=%d",
        "hits=%d",
        "no_hit=%d",
        "query_invalid=%d",
        "degenerate_direction=%d",
        "invalid_bounds=%d",
    ):
        assert field in host

    # The structurally unreachable counter must be printed as a defensive
    # assertion, never as a statistic. Printing a permanent zero as a
    # measurement invites "no submerged geometry was rejected" to be read as a
    # positive result, when in truth the exclusion comes from where the mirrored
    # ray starts, not from that comparison (FL-4216 review finding MEDIUM-3).
    assert (
        "below_water_clip_rejects=%d(defensive,always-zero-by-construction)" in host
    )

    # The span must be stated, because the numbers cover many dispatches.
    assert "(span=%d vsb dispatches)" in host


def test_reflection_ray_code_lives_inside_visible_surface_builder():
    """The mirrored-ray query has exactly one home and exactly one caller."""
    vsb = text(VSB_SHADER)
    compositor = text(COMPOSITOR_SHADER)
    product = text(PRODUCT_SHADER)

    # The ray-casting function is defined in the visible-surface builder.
    assert "uint fl4216_reflection_word(SceneRay primary_ray, SurfaceHit water_hit) {" in vsb

    # EXACTLY ONE call site, and exactly one record-publication call site.
    #
    # Why this matters: the reflection answer travels to the record encoder
    # through a per-invocation variable rather than an argument, so a SECOND
    # publication call would silently ship whatever that variable happened to
    # hold. Counting the call sites is what keeps that carrier safe
    # (FL-4216 review finding LOW-2).
    vsb_code = strip_comments(vsb)
    assert vsb_code.count("fl4216_reflection_word(") == 2  # 1 definition + 1 call
    assert vsb_code.count("hit_record(") == 2  # 1 definition + 1 call

    # The final painting shader never rediscovers visibility, so it must not
    # contain any part of this lane.
    assert "fl4216_reflection" not in compositor
    assert "FL4216_REFLECTION" not in compositor

    # Product consumes the answer as one bounded authored-material replay. It
    # owns no ray query, mirroring maths, and distance reconstruction.
    pair = strip_comments(
        _glsl_function(product, "void product_fl4216_apply_reflection_pair(")
    )
    assert "scene_query" not in pair
    assert "reflect(" not in pair
    assert "distance" not in pair
    assert "FL4216_REFLECTION_SOURCE_INK_MAX" in pair
    assert "reflected.paper_mix" in pair
    assert "clamp(daylight, 0.0, 1.0)" in pair
    assert "product_fl4216_reflection_background(" not in product
    assert "FL4216_REFLECTED_MASS_GLYPH" not in product
    # The old raw-distance transport constants are retired, not merely unused: the
    # word no longer carries a raw distance for anyone to re-derive world units
    # from. (The class-field names that replaced them are different strings, so
    # these checks do not accidentally match the new layout.)
    assert "FL4216_REFLECTION_DISTANCE_SHIFT" not in product
    assert "FL4216_REFLECTION_DISTANCE_MAX" not in product
    assert "FL4216_REFLECTION_DISTANCE_QUANTUM" not in product
    # The retired ramp constants must be gone, not merely unreferenced here.
    assert "FL4216_REFLECTION_FADE_END_WORLD" not in product
    assert "FL4216_REFLECTION_MAX_DARKEN" not in product

    # Product decodes source material, directional normal, and authored shade.
    # The rejected distance/height morphology fields have no Product consumer.
    decode = strip_comments(
        _glsl_function(
            product,
            "Fl4216ReflectedMaterial product_fl4216_decode_reflected_material(",
        )
    )
    assert "reflected.distance_class" not in decode
    assert "reflected.height_class" not in decode
    assert "reflected.normal = normalize(" in decode
    assert "reflected.authored_shade = float(" in decode


def test_reflection_blends_only_the_hit_materials_authored_colour_pair():
    """The successor turns one matlib hit into a diffuse broken water mass."""
    product = text(PRODUCT_SHADER)
    product_code = strip_comments(product)
    pair = strip_comments(
        _glsl_function(product, "void product_fl4216_apply_reflection_pair(")
    )
    pair_compact = "".join(pair.split())
    candidate = strip_comments(
        _glsl_function(product, "uint water_glyph_candidate(")
    )

    assert "FL4216_REFLECTION_SOURCE_INK_MAX = 0.78" in product_code
    assert "FL4216_REFLECTION_MIN_SOURCE_VALUE_SEPARATION = 0.28" in product_code
    # F10 2026-08-13: the terrain ceiling carried 89.1 percent of the witnessed
    # reflected hits (2775 of 3115; grass alone is 74.3 percent) at less than half
    # the mesh ceiling. It is raised but stays strictly below the mesh ceiling, so
    # a retained object still out-reads a hillside.
    assert "FL4216_REFLECTION_TERRAIN_PAPER_MAX = 0.46" in product_code
    assert "FL4216_REFLECTION_MESH_PAPER_MAX = 0.56" in product_code
    assert "FL4216_REFLECTION_MIN_PAPER_VALUE_SEPARATION = 0.16" in product_code
    assert (
        "float minimum_value_separation = reflected.boat_contact_mass\n"
        "\t\t\t? FL4707_BOAT_CONTACT_MIN_VALUE_SEPARATION\n"
        "\t\t\t: FL4216_REFLECTION_MIN_SOURCE_VALUE_SEPARATION;" in pair
    )
    # ONE separation law, called by both channels. The inline ink-only copy was
    # deleted, not duplicated, when paper acquired the same guarantee.
    separation = strip_comments(
        _glsl_function(
            product, "vec3 product_fl4216_value_separated_source("
        )
    )
    assert "abs(value_delta) >= minimum_separation" in separation
    assert "reference_value + direction * minimum_separation" in separation
    assert "abs(value_delta) < minimum_value_separation" not in pair
    assert pair.count("product_fl4216_value_separated_source(") == 2
    assert (
        "source_ink = product_fl4216_value_separated_source(\n"
        "\t\t\tsource_ink, dot(ink, value_weights), minimum_value_separation);"
        in pair
    )
    assert "mix(ink,source_ink,amount)" in pair_compact
    assert "product_fl4216_reflection_mass_gain(" in pair
    assert (
        "reflected.boat_contact_mass\n"
        "\t\t\t? product_fl4707_boat_contact_mass_colour(paper)\n"
        "\t\t\t: product_fl4216_diffuse_reflection_colour(reflected.fg, paper)"
        in pair
    )
    # The boat reflection keeps its established endpoint. Ordinary reflected
    # silhouettes recover the transported material family's stronger foreground
    # chroma for ANSI paper; F11 contact is no longer a reflection branch.
    assert "vec3source_paper;if(reflected.boat_contact_mass){" in pair_compact
    assert (
        "source_paper = product_fl4707_boat_contact_mass_colour(paper);"
        in pair
    )
    assert "reflected.water_contact" not in pair
    assert "float authored_paper_luma = dot(reflected.bg, value_weights);" in pair
    assert "vec3 authored_pair_axis = mix(reflected.bg, reflected.fg, 0.46);" in pair
    assert (
        "authored_pair_axis*=authored_paper_luma/"
        "max(dot(authored_pair_axis,value_weights),0.04);" in pair_compact
    )
    assert (
        "source_paper=product_fl4216_diffuse_reflection_colour("
        "authored_pair_axis,paper);" in pair_compact
    )
    # The paper-side separation guarantee belongs to the broad non-boat mass.
    assert (
        "source_paper=product_fl4216_value_separated_source(source_paper,"
        "dot(paper,value_weights),FL4216_REFLECTION_MIN_PAPER_VALUE_SEPARATION);"
        in pair_compact
    )
    boat_branch = pair.split("if (reflected.boat_contact_mass) {", 1)[1]
    boat_branch = boat_branch.split("} else {", 1)[0]
    assert "FL4216_REFLECTION_MIN_PAPER_VALUE_SEPARATION" not in boat_branch
    assert "mix(paper,source_paper," in pair_compact
    assert "* mass_gain" in pair
    assert "mix(paper,reflected.bg," not in pair_compact
    mass = strip_comments(
        _glsl_function(product, "float product_fl4216_reflection_mass_gain(")
    )
    diffuse = strip_comments(
        _glsl_function(product, "vec3 product_fl4216_diffuse_reflection_colour(")
    )
    assert "product_water_wave_phase()" in mass
    assert "* 0.42" in mass
    assert "(water_world_xz + wobble_world) * 0.18" in mass
    assert "stable_value_noise(" in mass
    assert "smoothstep(0.24, 0.42, mass)" in mass
    assert "smoothstep(0.56, 0.76, mass)" in mass
    # F10 2026-08-13: the dead zone and both ramps are unchanged anti-popping
    # canon; only the diffuse plateau moved, and it must stay strictly between
    # zero and the strong-mass value so the field still reads as broken.
    assert "diffuse_admission\n\t\t* mix(0.86, 1.0, strong_admission)" in mass
    assert "mix(0.72, 1.0, strong_admission)" not in mass
    assert "clamp(grouped_mass * wave_gain, 0.0, 1.0)" in mass
    assert "clamp(mix(0.34, 1.0, broken) * wave_gain, 0.28, 1.0)" not in mass
    assert "broken < 0.34 ? 0.0" not in mass
    assert "mix(vec3(source_luma), source, 0.72)" in diffuse
    assert "mix(desaturated, water_colour, 0.20)" in diffuse
    # The two nested mixes resolve to a bounded diffuse family blend:
    # 57.6% authored chroma + 22.4% source luma + 20% local water.
    authored_weight = 0.72 * (1.0 - 0.20)
    source_luma_weight = (1.0 - 0.72) * (1.0 - 0.20)
    water_weight = 0.20
    assert authored_weight == 0.576
    assert abs(source_luma_weight - 0.224) < 1.0e-12
    assert authored_weight + source_luma_weight + water_weight == 1.0
    for forbidden in ("camera", "projection", "gl_FragCoord", "alpha"):
        assert forbidden not in mass
        assert forbidden not in diffuse
    transported_source = strip_comments(
        _glsl_function(product, "ProductWaterSourcePaint product_water_transported_source_paint(")
    )
    reflection_adapter = strip_comments(
        _glsl_function(product, "Fl4216ReflectedPaint product_fl4216_reflected_source_paint(")
    )
    assert "paint.glyph = uint(clamp(int(texelFetch(" in transported_source
    assert "paint.fg = texelFetch(" in transported_source
    assert "paint.bg = texelFetch(" in transported_source
    assert "source.owner_kind == OWNER_STATIC_MESH" in transported_source
    assert "product_static_rock_moss_proxy_material(" in transported_source
    assert "product_retained_tree_paint_pair(" in transported_source
    assert "vsb_product_selected_effective_sun_dir" in transported_source
    assert "product_lighting_value(source.normal, lambert_raw)" in transported_source
    assert "source.authored_shade" in transported_source
    assert "product_water_transported_source_paint(reflected, water_cell)" in reflection_adapter
    assert "source.fg" in reflection_adapter
    assert "source.bg" in reflection_adapter
    assert "reflected.owner_kind == OWNER_TERRAIN" in reflection_adapter
    assert "? FL4216_REFLECTION_TERRAIN_PAPER_MAX" in reflection_adapter
    assert ": FL4216_REFLECTION_MESH_PAPER_MAX" in reflection_adapter
    assert "texelFetch" not in reflection_adapter
    assert "product_static_rock_moss_proxy_material(" not in reflection_adapter
    assert "return uint(FL4216_GLYPH_ID" in candidate
    assert "return product_water_activity_glyph(s);" in candidate
    assert "vsb_product_selected_reflected_paint" not in candidate
    assert "morphology_phase" not in candidate
    assert "product_fl4216_reflection_background(" not in product_code
    assert "FL4216_REFLECTION_WATERLINE" not in product_code
    assert "FL4216_REFLECTED_MASS_GLYPH" not in product_code
    rock_owner = strip_comments(
        _glsl_function(product, "int product_static_rock_moss_proxy_material(")
    )
    assert "STATIC_WORLD_ROCK_MAT" in rock_owner
    assert "STATIC_WORLD_MOSS_MAT" in rock_owner
    assert "splat_mat_at(terrain_cell)" in rock_owner
    assert "if (material == STATIC_WORLD_ROCK_MAT) return 5" in rock_owner
    assert "ground_family == TERRAIN_FAMILY_GRASS ? ground_material : 1" in rock_owner
    assert product_code.count("product_static_rock_moss_proxy_material(") == 3


def test_reflection_cue_routes_through_the_single_water_colour_pair_owner():
    """Reflection is surface paint after water fog; shoreline ink stays authoritative."""
    product = text(PRODUCT_SHADER)
    compositor = text(COMPOSITOR_SHADER)
    product_code = strip_comments(product)
    candidate = strip_comments(_glsl_function(product, "uint water_glyph_candidate("))
    assert "return product_water_activity_glyph(s);" in candidate
    assert "vsb_product_selected_reflected_paint" not in candidate

    assert product_code.count("product_fl4216_decode_reflected_material(") == 2
    water_owner = strip_comments(
        _glsl_function(product, "void q3_probe_payload_apply_water(")
    )
    assert "product_fl4216_decode_reflected_material(" in water_owner
    assert "product_fl4216_reflected_source_paint(" in water_owner
    assert water_owner.index("product_fl4216_reflected_source_paint(") < water_owner.index(
        "water_glyph_candidate("
    )

    day_pair = strip_comments(
        _glsl_function(product, "void product_apply_day_water_pair(")
    )
    reflection_pair = strip_comments(
        _glsl_function(product, "void product_fl4216_apply_reflection_pair(")
    )
    assert day_pair.count("product_fl4216_apply_reflection_pair(") == 1
    assert (
        "vsb_product_selected_reflected_paint,\n"
        "\t\t\ts.world_xz,\n"
        "\t\t\ts.wave_height,\n"
        "\t\t\t1.0 - product_day_night_factor()" in day_pair
    )
    assert day_pair.index("background_fog_amount") < day_pair.index(
        "product_fl4216_apply_reflection_pair("
    )
    assert day_pair.index("foreground_fog_amount") < day_pair.index(
        "product_fl4216_apply_reflection_pair("
    )
    assert "!s.break_active && s.break_strength <= 0.0" in day_pair
    assert "background = product_fl4216" not in day_pair
    assert "FL4216_REFLECTED_MASS_GLYPH" not in product_code

    assert "fl4216_reflection" not in compositor
    assert "reflection_word" not in compositor


def test_reflection_admission_uses_shared_shoreline_distance_only():
    """F10 admission follows the universal shoreline ring, not mesh contact."""
    product = text(PRODUCT_SHADER)
    day_pair = strip_comments(
        _glsl_function(product, "void product_apply_day_water_pair(")
    )
    assert (
        "float shoreline_mirror_admission = smoothstep(\n"
        "\t\t\t0.75, 2.26, s.shore_dist_cells);"
        in day_pair
    )
    assert day_pair.count("shoreline_mirror_admission") == 2
    assert "direct_water_solid_contact" not in day_pair
    assert "submerged_source_present" not in day_pair[
        day_pair.index("shoreline_mirror_admission") :
    ]


def test_explicit_cell_dump_observes_existing_selected_surface_without_new_owner():
    """Attempt 245 exposes b64 facts only inside the already-explicit dump."""
    host = text(COMPOSITOR_HOST)
    dump = host[
        host.index("func build_final_render_cell_rows(") :
        host.index("func _final_dump_cell_average(")
    ]

    assert "selected_surface_rid: RID = _vsb_view_receipt.get" in dump
    assert "expected_selected_record_count := ansi_size.x * ansi_size.y * 4" in dump
    assert "rd.buffer_get_data(" in dump
    assert "VisibleSurfaceBuilderScript.validate_record_bytes(" in dump
    assert '"selected_surface_quarters": selected_surface_quarters' in dump
    assert "storage_buffer_create(" not in dump
    assert "compute_list_dispatch(" not in dump
    assert host.count('"selected_surface_quarters": selected_surface_quarters') == 1


def test_reflection_cue_is_structurally_inert_when_disarmed():
    """Constant 18 false deletes the surviving decode and ink cue."""
    product = text(PRODUCT_SHADER)
    water_owner = strip_comments(
        _glsl_function(product, "void q3_probe_payload_apply_water(")
    )
    decode_call = "product_fl4216_decode_reflected_material("
    assert decode_call in water_owner
    assert water_owner.index("PRODUCT_FL4216_REFLECTION_RAY_ENABLED") < water_owner.index(
        decode_call
    )

    day_pair = strip_comments(
        _glsl_function(product, "void product_apply_day_water_pair(")
    )
    pair_call = "product_fl4216_apply_reflection_pair("
    assert pair_call in day_pair
    assert day_pair.index("PRODUCT_FL4216_REFLECTION_RAY_ENABLED") < day_pair.index(
        pair_call
    )

    assert (
        "Fl4216ReflectedMaterial vsb_product_selected_reflected_material =\n"
        "\tFl4216ReflectedMaterial(\n"
        "\t\tfalse, OWNER_NONE, 0u, 0u, "
        "vec3(0.0, 1.0, 0.0), 0.5);" in product
    )
    # FL-4737 deliberately reuses this descriptor for a separate, pre-water
    # transport path. Pin the reflection lane locally instead of treating the
    # number of constructors in the whole shader as reflection ownership.
    decode = strip_comments(
        _glsl_function(
            product,
            "Fl4216ReflectedMaterial product_fl4216_decode_reflected_material(",
        )
    )
    reflection_adapter = strip_comments(
        _glsl_function(
            product,
            "Fl4216ReflectedPaint product_fl4216_reflected_source_paint(",
        )
    )
    assert (
        "Fl4216ReflectedMaterial(\n"
        "\t\t\tfalse, OWNER_NONE, 0u, 0u, vec3(0.0, 1.0, 0.0), 0.5)"
        in decode
    )
    assert "product_water_transported_source_paint(reflected, water_cell)" in reflection_adapter
    assert "return Fl4216ReflectedPaint(" in reflection_adapter


def test_reflection_source_shading_transport_uses_existing_hit_facts():
    """Attempt 244 transports normal and authored shade without another query."""
    vsb = text(VSB_SHADER)
    product = text(PRODUCT_SHADER)
    packer = strip_comments(_glsl_function(vsb, "uint fl4216_reflection_word("))
    decode = strip_comments(_glsl_function(
        product, "Fl4216ReflectedMaterial product_fl4216_decode_reflected_material("
    ))

    assert "reflected.normal" in packer
    assert "reflected.authored_shade" in packer
    assert "FL4216_REFLECTION_NORMAL_X_SHIFT" in packer
    assert "FL4216_REFLECTION_NORMAL_Z_SHIFT" in packer
    assert "FL4216_REFLECTION_NORMAL_Y_NEGATIVE" in packer
    assert "FL4216_REFLECTION_AUTHORED_SHADE_SHIFT" in packer
    assert "FL4216_REFLECTION_DISTANCE_CLASS" not in packer
    assert "FL4216_REFLECTION_HEIGHT_CLASS" not in packer
    assert "scene_query" not in decode


def test_celestial_glitter_is_sparse_product_water_paint_and_shared():
    """Moon and sunset share one sparse water-paint closure."""
    product = text(PRODUCT_SHADER)
    product_code = strip_comments(product)
    glitter = strip_comments(
        _glsl_function(product, "float celestial_water_sparse_glitter(")
    )
    envelope = strip_comments(
        _glsl_function(product, "float celestial_water_reflection_envelope(")
    )
    night_pair = strip_comments(
        _glsl_function(product, "void product_apply_night_water_pair(")
    )
    day_pair = strip_comments(
        _glsl_function(product, "void product_apply_day_water_pair(")
    )
    reflection_pair = strip_comments(
        _glsl_function(product, "void product_fl4216_apply_reflection_pair(")
    )

    assert "mix(0.45" not in glitter
    assert "mix(0.5" not in glitter
    assert "CELESTIAL_GLITTER_SPARSE_ENTER" in glitter
    assert "CELESTIAL_GLITTER_SPARSE_FULL" in glitter
    assert "CELESTIAL_GLITTER_SPARSE_ENTER = 0.42" in product_code
    assert "CELESTIAL_GLITTER_SPARSE_FULL = 0.62" in product_code
    assert "CELESTIAL_GLITTER_DEPTH_SPACING = 15.0" in product_code
    assert "CELESTIAL_GLITTER_DEPTH_CENTER_JITTER = 0.20" in product_code
    assert "CELESTIAL_GLITTER_DEPTH_WINDOW_CORE = 0.18" in product_code
    assert "CELESTIAL_GLITTER_DEPTH_WINDOW_EDGE = 0.25" in product_code
    assert "smoothstep(0.97, 0.999" not in glitter
    assert "depth_slot" in glitter
    assert "depth_gate" in glitter
    assert "gl_FragCoord" not in glitter
    assert "connective" not in glitter
    assert "float admitted = smoothstep(0.01, 0.12, envelope * sparse)" in glitter
    assert "return clamp(admitted * highlight_shape" in glitter
    assert "return clamp(column * depth * engagement" in envelope
    assert "0.42 * width_scale" in envelope
    assert "4.8 * width_scale" in envelope
    assert "* 1.6" in envelope
    assert "* 0.45" in envelope
    assert "5.8 * width_scale" not in envelope
    assert "* 5.2" not in envelope
    assert "time * 0.75" in glitter
    assert "float(s.activity_phase) * (1.0 / 3.0)" in glitter
    assert "time * 2.0" not in glitter

    assert "night_foreground = mix(" in night_pair
    assert "clamp(moon_glitter * 1.35, 0.0, 1.0)" in night_pair
    assert "moon_glitter" in night_pair
    assert "MOON_REFLECTION_REF_RADIUS_RAD = 0.32" in product_code
    assert ") * 0.05" in night_pair
    assert "ordinary_activity * 0.006" in night_pair
    assert "twilight_glitter" in day_pair
    assert "s, sun_direction, 1.35, twilight_engagement" in day_pair
    assert "product_fl4722_low_contrast_wave_ink(" not in reflection_pair
    water_compile = strip_comments(
        _glsl_function(product, "void q3_probe_payload_apply_water(")
    )
    assert "product_day_night_factor() <= 0.0001" in water_compile
    assert water_compile.index("product_apply_day_water_pair(") < water_compile.index(
        "product_day_night_factor() <= 0.0001"
    )
    assert product_code.count("product_day_night_factor() <= 0.0001") == 1
    assert product_code.count("celestial_water_sparse_glitter(") == 3


def _assert_reflection_atomic_ownership(vsb: str) -> None:
    """Keep diagnostic atomics distinct from the reflection receipt atomics.

    VSB has one pre-existing candidate-diagnostic atomic outside the reflection
    branch. Counting every atomic in the file as reflection work therefore
    confuses two independently owned diagnostic surfaces.
    """
    main = _glsl_function(vsb, "void main() {")
    receipt_start = main.index(
        "if (VSB_FL4216_REFLECTION_RECEIPTS_ENABLED) {"
    )
    receipt_end = main.index(
        "visible_surface_products.records[record_index] = hit_record(nearest);",
        receipt_start,
    )
    receipt = main[receipt_start:receipt_end]
    candidate_diag = _glsl_function(vsb, "void probe_candidate_diag(")
    assert receipt.count("atomicAdd(") == 3
    assert candidate_diag.count("atomicAdd(") == 1
    assert vsb.count("atomicAdd(") == 4


def test_reflection_atomic_owner_mutation_turns_red() -> None:
    """A moved receipt atomic must not be hidden by the candidate counter."""
    vsb = text(VSB_SHADER)
    _assert_reflection_atomic_ownership(vsb)
    mutant = vsb.replace(
        "            atomicAdd(\n                fl4216_reflection_receipt",
        "            probe_candidate_diag(0u); /* moved */\n",
        1,
    )
    with pytest.raises(AssertionError):
        _assert_reflection_atomic_ownership(mutant)


def test_day_night_helper_mutation_turns_red() -> None:
    """The canonical helper, rather than a stale local variable, owns night."""
    product = text(PRODUCT_SHADER)

    def assert_helper(source: str) -> None:
        water_compile = strip_comments(
            _glsl_function(source, "void q3_probe_payload_apply_water(")
        )
        assert "product_day_night_factor() <= 0.0001" in water_compile

    assert_helper(product)
    mutant = product.replace(
        "product_day_night_factor() <= 0.0001",
        "night_factor <= 0.0001",
        1,
    )
    with pytest.raises(AssertionError):
        assert_helper(mutant)


def test_full_night_selects_same_shader_reflection_off_pipeline():
    """Full night compiles mirrored traversal away without another dispatch."""
    host = text(COMPOSITOR_HOST)
    host_code = "\n".join(line.split("#")[0] for line in host.splitlines())

    assert "replacement_reflection_off_pipeline = rd.compute_pipeline_create(" in host_code
    assert "replacement_shader," in host_code
    assert "reflection_off_constant.constant_id = 0" in host_code
    assert "reflection_off_constant.value = false" in host_code
    assert "_celestial_frame_night_factor >= 0.999" in host_code
    assert "vsb_dispatch_pipeline = _vsb_reflection_off_pipeline" in host_code
    assert host_code.count(
        "rd.compute_list_bind_compute_pipeline(product_compute_list, vsb_dispatch_pipeline)"
    ) == 1

    # The diagnostic surface must name the policy decision beside the GPU
    # counts. Without this, non-zero stale work and a failed specialization are
    # indistinguishable from a correctly selected daytime program.
    assert '"celestial_night_factor=%.3f requested_pipeline=%s "' in host
    assert '"reflection_off"' in host
    assert '"reflection_on"' in host


def test_fl4408_deletion_guard_remains_literally_true_and_untouched():
    """The guard that forbids the dead predecessor file is intact."""
    guard = text(FL4408_GUARD_TEST)

    # The exact assertion row survives, unedited.
    assert (
        'assert not ROOT.joinpath("godot_project/shaders/fl4129_reflected_terrain_product.glsl").exists()'
        in guard
    )
    # It still sits inside its original register of deleted second render owners.
    assert "def test_dead_probe_and_leaf_projection_apis_are_not_runtime_surfaces():" in guard
    # And this lane did not edit that file to make room for itself. (The guard
    # file legitimately mentions unrelated FL-4216 shoreline symbols that
    # predate this slice, so only THIS lane's symbols are banned from it.)
    assert "fl4216_reflection" not in guard
    assert "reflection_ray" not in guard
    assert "fl4216_reflection_word" not in guard

    # The forbidden file really is absent on disk, not merely unreferenced.
    assert not ROOT.joinpath(BANNED_PREDECESSOR_SHADER).exists()


def test_reflection_word_shape_is_validated_on_both_sides():
    """A malformed receipt fails closed instead of becoming plausible paint."""
    product = text(PRODUCT_SHADER)
    vsb_host = text(VSB_HOST)

    # The GPU consumer rejects any word that violates the published bit layout,
    # and a rejected record becomes visible absence rather than a guessed tint.
    checker = _glsl_function(
        product, "bool vsb_product_fl4216_reflection_word_is_well_formed("
    )
    assert "if (word == 0u) return true;" in checker
    assert "FL4216_REFLECTION_OWNER_DYNAMIC" in checker
    assert "&& (word & FL4216_REFLECTION_OWNER_DYNAMIC) != 0u" in checker
    assert "FL4216_REFLECTION_HIGH_RESERVED_MASK" not in checker
    # At most one outcome bit may be set: one ray has one fate. A word claiming
    # both "found a surface" and "the query aborted" comes from a confused
    # producer, and a confused producer must never be allowed to paint.
    assert "FL4216_REFLECTION_OUTCOME_MASK" in checker
    assert "if ((outcome_bits & (outcome_bits - 1u)) != 0u) return false;" in checker
    # SUCCESSOR LAYOUT. Bits 8..16 carry the reflected source normal; family
    # stays in 17..19. Both source windows remain illegal without a real hit.
    assert "FL4216_REFLECTION_NORMAL_FIELD_MASK" in checker
    assert "if (!hit && normal_field != 0u) return false;" in checker
    assert "FL4216_REFLECTION_SOURCE_FIELD_MASK" in checker
    assert "if (!hit && source_field != 0u) return false;" in checker
    assert "FL4216_REFLECTION_WATER_CONTACT" not in checker
    assert "FL4216_REFLECTION_RESERVED_HIGH" in checker
    # The family field is three bits wide but only six families are defined. A
    # word naming family 6 and 7 came from a producer this consumer does not
    # understand, and must be refused rather than painted with a guessed tint.
    assert "if (family >= FL4216_REFLECTION_FAMILY_COUNT) return false;" in checker

    # The builder is the only place source normal and authored shade are packed.
    vsb = text(VSB_SHADER)
    packer = _glsl_function(vsb, "uint fl4216_reflection_word(")
    assert "FL4216_REFLECTION_NORMAL_COMPONENT_MAX" in packer
    assert "FL4216_REFLECTION_AUTHORED_SHADE_MAX" in packer
    assert "reflected.material, FL4216_REFLECTION_MATERIAL_MASK" in packer
    assert "source_normal.x * 0.5 + 0.5" in packer
    assert "source_normal.z * 0.5 + 0.5" in packer
    assert "source_normal.y < 0.0" in packer
    assert "reflected.authored_shade" in packer
    assert "reflected.owner_kind == OWNER_DYNAMIC_MESH" in packer
    assert "word |= FL4216_REFLECTION_OWNER_DYNAMIC" in packer
    assert "FL4216_REFLECTION_AUTHORED_SHADE_MAX = 7u" in vsb
    assert "FL4216_REFLECTION_WATER_CONTACT" not in vsb
    assert "WATER_CONTACT" not in packer
    # Material family comes from facts the hit ALREADY returned. Deriving it from
    # a fresh texture read would be a new acceleration reader, which is invariant
    # G2 and the defect that killed the predecessor.
    assert (
        "fl4216_reflection_material_family(\n"
        "        reflected.owner_kind, reflected.material);" in packer
    )
    family_classifier = strip_comments(
        _glsl_function(vsb, "uint fl4216_reflection_material_family(")
    )
    assert "texelFetch" not in family_classifier
    assert "texture(" not in family_classifier

    # The record decoder picks its rule at pipeline-build time: shape check when
    # armed, the original exactly-zero compare when disarmed.
    assert (
        "? !vsb_product_fl4216_reflection_word_is_well_formed(record.reflection_word)"
        in product
    )
    assert ": (record.reflection_word != 0u);" in product

    # The CPU-side record validator applies the same shape rule, and zero — the
    # value every disarmed record carries — remains valid on both sides.
    assert "static func fl4216_reflection_word_errors(word: int) -> Array[String]:" in vsb_host
    assert "const RECORD_REFLECTION_WORD_OFFSET := 92" in vsb_host

    # THE CROSS-FILE LIMIT, pinned so it cannot rot silently.
    #
    # The CPU validator applies the same no-source-without-hit rule to both
    # occupied source windows.
    assert "if source_normal != 0 and not hit:" in vsb_host
    assert "if source != 0 and not hit:" in vsb_host
    assert "const FL4216_REFLECTION_AUTHORED_SHADE_MAX := 0x7" in vsb_host
    assert "const FL4216_REFLECTION_RESERVED_HIGH := 1 << 31" in vsb_host
    assert "fl4216 reflection word sets reserved bit 31" in vsb_host
    assert "FL4216_REFLECTION_WATER_CONTACT" not in vsb_host
    assert '"reflection_authored_shade_q": (' in vsb_host
    assert '"direct_water_solid_contact": direct_water_solid_contact' in vsb_host
    # The reflection word is still ONE 32-bit value at record byte offset 92 on
    # a 96-byte record. ABI v4 adds retained-probe source epoch bits 30..31;
    # the reflection lane remains one 32-bit value at byte 92.
    assert "const uint VISIBLE_SURFACE_ABI_VERSION = 4u;" in text(VSB_SHADER)
    assert "const uint VSB_VISIBLE_SURFACE_ABI_VERSION = 4u;" in product
    assert "const VISIBLE_SURFACE_ABI_VERSION := 4" in vsb_host
    assert "const VSB_VISIBLE_SURFACE_ABI_VERSION := 4" in text(COMPOSITOR_HOST)
    assert "const uint STATUS_SOURCE_EPOCH_MASK = 0xc0000000u;" in text(VSB_SHADER)
    assert "const uint VSB_STATUS_SOURCE_EPOCH_MASK = 0xc0000000u;" in product
    assert "const RECORD_STRIDE_BYTES := 96" in vsb_host
    assert "const uint STATUS_SUBMERGED_SOURCE_MASK = 0x1fffff80u;" in text(VSB_SHADER)
    assert "const uint STATUS_DIRECT_WATER_SOLID_CONTACT = 1u << 29u;" in text(VSB_SHADER)
    assert "uint reflection_word;" in text(VSB_SHADER)


def test_reflection_receipt_comments_state_the_real_896_byte_seven_lane_shape():
    """No FL-4216 comment may still describe the OLD sixteen-byte receipt.

    WHAT A NON-PROGRAMMER NEEDS TO KNOW HERE
    ----------------------------------------
    The reflection lane keeps a small "receipt" in GPU memory: a handful of
    running totals saying how many mirrored rays were cast, how many found
    something, and how many came back empty and why. That receipt is what makes
    "this run produced no reflections" a countable fact rather than an invisible
    one.

    The receipt started life tiny: four totals packed into sixteen bytes. It is
    not that any more. It is 896 bytes, arranged as SEVEN lanes of 32 unsigned
    integers, one lane per outcome, with the lane totals deliberately spaced
    128 bytes apart to reduce false sharing. Whether that spacing changes cache
    contention on the target GPU remains unmeasured.

    The code changed; several explanations beside it did not, and an external
    review found them still describing the sixteen-byte version. A wrong comment
    about a receipt is worse than no comment, because the receipt is the surface
    that gets pasted next to a screenshot as evidence — a reader who trusts the
    stale prose would look for four numbers and conclude the other three were
    missing. This test freezes the corrected description in place.

    HOW THE SEARCH IS SCOPED, AND WHY THAT SCOPING IS LOAD-BEARING
    --------------------------------------------------------------
    ASCIICompositorEffect.gd contains a completely unrelated comment about the
    LEAF-PARTICLE bank header, which genuinely is sixteen bytes and correctly
    says so. That comment must never be flagged here. It cannot be, because this
    test only ever looks at comment blocks that mention this lane by name — see
    FL4216_SCOPE_TOKENS and fl4216_comment_blocks at the top of this file. The
    leaf-particle block mentions leaf lanes, an emitter, and a header magic
    value, and none of this lane's tokens, so it is outside the search by
    construction rather than by luck.
    """
    vsb_blocks = fl4216_comment_blocks(VSB_SHADER, "//")
    host_blocks = fl4216_comment_blocks(COMPOSITOR_HOST, "#")

    # ------------------------------------------- THE STALE CLAIM IS GONE
    # These are the exact shapes the retired description took. They are matched
    # case-insensitively and with either a space or a hyphen, so a reworded but
    # equally wrong sentence is caught too. If a future post-mortem needs to
    # record that the receipt USED to be sixteen bytes, it must say so without
    # restating the dead number as a fact — describe the change, do not reprint
    # the false figure.
    stale_receipt_claims = (
        "sixteen bytes",
        "sixteen-byte",
        "four counters",
        "four-counter",
        "four integers",
        "four-integer",
    )
    for source_name, blocks in ((VSB_SHADER, vsb_blocks), (COMPOSITOR_HOST, host_blocks)):
        for line_number, block in blocks:
            lowered = block.lower()
            for claim in stale_receipt_claims:
                assert claim not in lowered, (
                    f"{source_name}:{line_number} still describes the FL-4216 "
                    f"receipt as {claim!r}; it is 896 bytes in seven lanes"
                )

    # ------------------------------------ THE CORRECT SHAPE IS STATED, TWICE
    # Once where the buffer is declared in the shader, because that is the first
    # place a reader lands when they follow "binding 84"...
    declaration = block_containing(
        vsb_blocks, "Binding 84 is the FL-4216 reflection RECEIPT block"
    )
    assert "896" in declaration
    assert "seven" in declaration.lower()

    # ...and once in invariant G3 at the top of the shader, which is the rule
    # that permits the buffer to exist at all ("a receipt, not a picture").
    g3 = block_containing(vsb_blocks, "G3  NO SEPARATE SCREEN-RESOLUTION IMAGE PRODUCTS")
    assert "896" in g3
    assert "seven lanes" in g3.lower()
    assert "128-byte stride" in g3
    assert "hardware-dependent" in g3
    assert "UNMEASURED" in g3
    # G3's original justification must survive the rewrite: the block is legal
    # because its size never varies with the window, not because it is small. The
    # two halves of that sentence are checked separately because the paragraph is
    # hard-wrapped, and a future reflow would otherwise break a check that is
    # about wording rather than about the rule itself.
    assert "That is a receipt, not a picture," in g3
    assert "it is not screen-sized" in g3

    # And the host-side declaration of the same buffer agrees with both.
    host_receipt = block_containing(
        host_blocks, "Binding 84: the 896-byte reflection receipt block"
    )
    assert "seven counter lanes" in host_receipt.lower()
    assert "128-byte stride" in host_receipt
    assert "hardware-dependent" in host_receipt
    assert "unmeasured" in host_receipt.lower()

    # Every live FL-4216 source block that predicts a hardware benefit from the
    # stride must state the measurement ceiling in that same block. Layout-only
    # statements may name the 128-byte stride without pretending to know the
    # target GPU's cache mapping.
    for source_name, blocks in (
        (VSB_SHADER, vsb_blocks),
        (VSB_HOST, fl4216_comment_blocks(VSB_HOST, "#")),
        (COMPOSITOR_HOST, host_blocks),
    ):
        for line_number, block in blocks:
            lowered = " ".join(
                line.strip().lstrip("#/ ").strip() for line in block.splitlines()
            ).lower()
            predicts_hardware_benefit = any(
                phrase in lowered
                for phrase in (
                    "false sharing",
                    "contention reduction",
                    "reduce that contention",
                    "hardware cache/coherency",
                )
            )
            if predicts_hardware_benefit:
                states_measurement_ceiling = any(
                    phrase in lowered
                    for phrase in (
                        "unmeasured",
                        "not been measured",
                        "have not been measured",
                        "has not been measured",
                    )
                )
                assert states_measurement_ceiling, (
                    f"{source_name}:{line_number} predicts a receipt-padding "
                    "hardware benefit without saying it is unmeasured"
                )
    # The seven lanes are named with the shader's own constant names, so a reader
    # of the printed receipt can map each number back to the code that wrote it.
    for lane_name in (
        "FL4216_RECEIPT_RAYS_CAST",
        "FL4216_RECEIPT_HITS",
        "FL4216_RECEIPT_NO_HIT",
        "FL4216_RECEIPT_CLIP_REJECTS",
        "FL4216_RECEIPT_DEGENERATE_DIRECTION",
        "FL4216_RECEIPT_INVALID_BOUNDS",
        "FL4216_RECEIPT_QUERY_INVALID",
    ):
        assert lane_name in host_receipt
    # The countable-absence rationale is the reason the receipt exists, and it
    # must not be lost while the size description is repaired.
    assert "COUNTABLE rather than invisible" in host_receipt
    assert "stale snapshot" in host_receipt


def test_fl4216_comments_state_authored_material_colour_contract():
    """Shader and host prose agree with the one-owner colour blend."""
    vsb_blocks = fl4216_comment_blocks(VSB_SHADER, "//")
    host_blocks = fl4216_comment_blocks(COMPOSITOR_HOST, "#")
    header = block_containing(
        vsb_blocks,
        "WHAT THE ANSWER IS ALLOWED TO CHANGE: REFLECTED MATERIAL COLOUR",
    )
    assert "SINGLE WATER OWNER" in header
    assert "cell_owner_product.glsl" in header
    assert "matlib foreground/background pair" in header
    assert "wave glyph remains authoritative" in header.lower()
    assert "shoreline glyph plus foreground win" in header.lower()
    assert "FL-4359" in header
    assert "ink-threshold" in header.lower()
    assert "downstream owner" in header

    readiness = block_containing(host_blocks, "FL-4216 READINESS SLICE")
    assert "SINGLE WATER OWNER" in readiness
    assert "cell_owner_product.glsl" in readiness
    assert "authored matlib foreground/background pair" in readiness
    assert "wave glyph" in readiness

    host = text(COMPOSITOR_HOST)
    assert "blend the hit material's authored ink/paper pair" in host
    assert "beneath the wave glyph" in host
    assert "full night compiles mirrored traversal away" in host


# ---------------------------------------------------------------------------
# F10 delivered-strength laws (2026-08-13)
#
# The two string contracts above pin WHICH coefficients exist. These numeric
# checks pin what those coefficients have to DELIVER, and each one carries the
# mutant that used to be live so a silent revert is a red test rather than a
# quiet return of the "weak fringe" verdict the operator falsified.
# ---------------------------------------------------------------------------

_LUMA_WEIGHTS = (0.2126, 0.7152, 0.0722)


def _luma(colour):
    return sum(c * w for c, w in zip(colour, _LUMA_WEIGHTS))


def _smoothstep(edge0, edge1, x):
    t = min(max((x - edge0) / (edge1 - edge0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _grouped_mass(mass, diffuse_plateau):
    """The owned mass-gain grouping, wave gain excluded (it is a separate law)."""
    diffuse = _smoothstep(0.24, 0.42, mass)
    strong = _smoothstep(0.56, 0.76, mass)
    return diffuse * (diffuse_plateau + (1.0 - diffuse_plateau) * strong)


def _diffuse_reflection_luma(source_luma, water_luma):
    """product_fl4216_diffuse_reflection_colour is luma-affine.

    The 0.72 desaturation preserves luma exactly (mix toward the source's own
    luma), so only the 0.20 water tint moves it.
    """
    return 0.80 * source_luma + 0.20 * water_luma


def _value_separated_luma(source_luma, reference_luma, minimum_separation):
    if abs(source_luma - reference_luma) >= minimum_separation:
        return source_luma
    direction = 1.0 if source_luma >= reference_luma else -1.0
    if direction > 0.0 and (1.0 - reference_luma) < minimum_separation:
        direction = -1.0
    elif direction < 0.0 and reference_luma < minimum_separation:
        direction = 1.0
    return min(max(reference_luma + direction * minimum_separation, 0.02), 0.98)


def _delivered_paper_luma_delta(
    source_paper_luma,
    water_luma,
    paper_ceiling,
    mass,
    diffuse_plateau,
    paper_separation,
):
    """Signed luma the reflected mass actually moves the water paper by.

    Daylight is 1.0 (noon) and mirror admission is excluded on purpose: it is
    the FL-4737 F8 owner's factor, not this owner's, and it multiplies whatever
    this function returns.
    """
    reflected = _diffuse_reflection_luma(source_paper_luma, water_luma)
    if paper_separation is not None:
        reflected = _value_separated_luma(reflected, water_luma, paper_separation)
    return paper_ceiling * _grouped_mass(mass, diffuse_plateau) * (
        reflected - water_luma
    )


def test_f10_mass_gain_plateau_delivers_without_closing_the_gaps():
    """The plateau carries the broad masses; the dead zone still emits zero."""
    product_code = strip_comments(text(PRODUCT_SHADER))
    assert "mix(0.86, 1.0, strong_admission)" in product_code

    # A true dead zone is the accepted anti-haze law and must survive: below the
    # lower ramp edge the field emits EXACTLY zero, so gaps remain gaps.
    for mass in (0.0, 0.10, 0.24):
        assert _grouped_mass(mass, 0.86) == 0.0

    # The plateau is where most admitted cells sit, and it must now deliver most
    # of the strong-mass value instead of the 72 percent that read as a fringe.
    plateau = _grouped_mass(0.49, 0.86)
    assert abs(plateau - 0.86) < 1.0e-9
    mutant_plateau = _grouped_mass(0.49, 0.72)
    assert mutant_plateau < plateau
    assert plateau / mutant_plateau > 1.19

    # Broken, not mirrored: a strong mass must still out-read a diffuse one, so
    # the grouping is still visible as structure rather than a flat silhouette.
    strong = _grouped_mass(0.90, 0.86)
    assert strong == 1.0
    assert strong - plateau > 0.10


def test_f10_reflected_paper_reads_for_both_dark_and_mid_value_sources():
    """Delivered paper is what the operator sees; pin it, not just the mixes."""
    water_luma = _luma((0.25, 0.56, 0.60))  # WATER_MID_BG, the ordinary body band

    # Landed law.
    terrain_ceiling, mesh_ceiling = 0.46, 0.56
    plateau, paper_separation = 0.86, 0.16
    # The law this replaced, kept here so a revert cannot pass quietly.
    old_terrain_ceiling, old_plateau, old_paper_separation = 0.26, 0.72, None

    # Case 1 -- DARK source (tree canopy, wet rock) over a terrain-family hit.
    # Separation was never the problem here; delivered mix was.
    dark = 0.16
    dark_now = _delivered_paper_luma_delta(
        dark, water_luma, terrain_ceiling, 0.49, plateau, paper_separation
    )
    dark_before = _delivered_paper_luma_delta(
        dark, water_luma, old_terrain_ceiling, 0.49, old_plateau,
        old_paper_separation,
    )
    assert dark_now < 0.0 and dark_before < 0.0  # a dark source darkens water
    assert abs(dark_before) < 0.055  # the falsified weak fringe
    assert abs(dark_now) > 0.09
    assert abs(dark_now) / abs(dark_before) > 2.0

    # Case 2 -- MID-VALUE source (pillar, dry rock face, hull) whose authored
    # paper lands near the local water value. This is the silent failure: the
    # old law delivered essentially nothing at ANY mix strength, so raising the
    # ceiling alone could never have repaired it.
    mid = 0.52
    mid_before = _delivered_paper_luma_delta(
        mid, water_luma, 0.56, 0.49, old_plateau, old_paper_separation
    )
    assert abs(mid_before) < 0.01
    mid_now = _delivered_paper_luma_delta(
        mid, water_luma, mesh_ceiling, 0.49, plateau, paper_separation
    )
    assert abs(mid_now) > 0.06
    # Mutant: keep every ceiling and plateau change but drop the separation
    # floor. The mid-value source must go straight back to unreadable, which is
    # what proves the separation floor is the owner of this repair.
    mid_without_separation = _delivered_paper_luma_delta(
        mid, water_luma, mesh_ceiling, 0.49, plateau, None
    )
    assert abs(mid_without_separation) < 0.01

    # The reflected mass stays a MASS: bounded well below replacing the water.
    for delivered in (dark_now, mid_now):
        assert abs(delivered) < 0.30

    # A terrain hillside still reads weaker than a retained object at the same
    # source value, so the ceiling change did not erase the owner distinction.
    assert terrain_ceiling < mesh_ceiling


# Allow the file to be executed directly as well as under pytest, so a reviewer
# without pytest installed can still run the contract by hand.
if __name__ == "__main__":
    failures = 0
    for name, function in sorted(list(globals().items())):
        if not name.startswith("test_") or not callable(function):
            continue
        try:
            function()
            print(f"PASS {name}")
        except AssertionError as error:
            failures += 1
            print(f"FAIL {name}: {error}")
        except Exception as error:  # noqa: BLE001 - report any breakage plainly
            failures += 1
            print(f"ERROR {name}: {error!r}")
    raise SystemExit(1 if failures else 0)
