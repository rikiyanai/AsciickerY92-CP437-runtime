# =============================================================================
# test_fl4665_frame_timing_contract.py
# =============================================================================
# WHAT THIS FILE CHECKS
# ---------------------
# FL-4665 is a bug where the game window freezes for seconds at a time after
# entering the world, so macOS marks it "Not Responding" and it must be force
# quit. Nobody yet knows WHICH part of a frame burns that time — the only
# measurement so far brackets one whole loop iteration, which names a range of
# suspects rather than a culprit.
#
# To find the culprit, the renderer was fitted with a measuring instrument: it
# times each stage of a frame separately and writes one line of numbers per
# frame. These tests do not run the game (running it is separately authorized).
# They check the INSTRUMENT ITSELF against the contract it was built to, by
# reading the source code, because a measuring instrument that quietly breaks
# its own rules produces confident numbers that are wrong.
#
# The three rules that matter most, in plain terms:
#
#   1. THE INSTRUMENT MUST NOT CAUSE THE PROBLEM IT MEASURES.
#      There is a way to time the graphics card that is easy and wrong: tell
#      the program "stop and wait until the graphics card is completely
#      finished" (the `glFinish` command), then look at a clock. That forces
#      the program to wait when it otherwise would not — which is *precisely*
#      the freeze under investigation. The instrument would create a stall and
#      then proudly measure it. So `glFinish`, and any other command that waits,
#      is forbidden here.
#
#   2. IT MUST ASK "ARE YOU DONE?" BEFORE ASKING "HOW LONG DID YOU TAKE?".
#      The graphics card is asked to time itself using a "query". Reading a
#      query's answer directly WAITS for that answer — same problem as rule 1.
#      The safe order is to first ask the non-blocking question "is the answer
#      ready?" and only read the number when the reply is yes.
#
#   3. IT MUST BE OFF UNLESS EXPLICITLY SWITCHED ON.
#      Timing code is controlled by an environment variable. If it were on by
#      default it would add cost to every normal run of the game.
#
# The tests also confirm the instrument actually covers the two rival suspects
# for the freeze, because a receipt that measures only one of them cannot
# distinguish between them:
#
#   SUSPECT A — the readback inside the renderer (`HarriReadPackedU3Stage`),
#     which copies results from the graphics card back to the main program and
#     waits while doing so.
#   SUSPECT B — the window "present" (`SDL_GL_SwapWindow`) in the host loop.
#     This is not a guess: the source at platform/backend/host_sdl2.cpp already
#     records a measured 2,364 ms stall inside that exact call on macOS.
#
# Run with:  python3 -m pytest scripts/tests/test_fl4665_frame_timing_contract.py
# =============================================================================

import re
from pathlib import Path

import pytest

# The repository root, found by walking up from this file (scripts/tests/x.py).
ROOT = Path(__file__).resolve().parents[2]

# The two source files that carry the instrument. The timing module lives in the
# renderer; the host loop only calls two small hooks around the window present.
PRESENT = ROOT / "platform/terminal_gl_present.cpp"
HOST = ROOT / "platform/backend/host_sdl2.cpp"


def read(path: Path) -> str:
    """Read a source file as text.

    Kept as a helper so every test reads the files the same way and a missing
    file fails with a clear error instead of a confusing exception later.
    """
    assert path.is_file(), f"expected source file is missing: {path}"
    return path.read_text(encoding="utf-8", errors="replace")


def strip_comments(src: str) -> str:
    """Remove C/C++ comments, leaving only code that the compiler acts on.

    WHY THIS IS NECESSARY: several tests below assert that a forbidden command
    such as `glFinish` never appears. But the instrument's own documentation
    explains at length WHY `glFinish` is forbidden — so the word legitimately
    appears in prose. Searching the raw file would fail on the explanation
    rather than on any real call, and the obvious "fix" would be to delete the
    explanation. That is exactly backwards: the rule is about what the program
    DOES, so the check must run against code with the prose removed.

    String literals are preserved, because a "//" inside a quoted string is
    ordinary text and not the start of a comment.
    """
    out = []
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        # A quoted string: copy it verbatim, honouring backslash escapes so an
        # escaped quote does not look like the end of the string.
        if ch in ('"', "'"):
            quote = ch
            out.append(ch)
            i += 1
            while i < n:
                out.append(src[i])
                if src[i] == "\\" and i + 1 < n:   # escape: copy the pair whole
                    out.append(src[i + 1])
                    i += 2
                    continue
                if src[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        # A // comment: skip to end of line, but keep the newline so that line
        # numbers and line-based structure survive.
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        # A /* */ comment: skip to the closing marker.
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            end = src.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


@pytest.fixture(scope="module")
def present_src() -> str:
    """Full text of the renderer source file."""
    return read(PRESENT)


@pytest.fixture(scope="module")
def host_src() -> str:
    """Full text of the host-loop source file."""
    return read(HOST)


@pytest.fixture(scope="module")
def module_src(present_src: str) -> str:
    """Just the FL-4665 timing module, sliced out of the renderer file.

    WHY SLICE: the renderer file is over eight thousand lines and mentions
    FL-4665 in other places for other reasons. Rules like "this code must never
    call glFinish" are about the INSTRUMENT, not about the whole renderer, so
    they must be checked against the instrument's own text only. Otherwise an
    unrelated line elsewhere in the file could either mask a real violation or
    raise a false alarm.

    The module starts at its banner comment and ends where term_render begins.
    """
    start_marker = "// FL-4665 — FRAME-SPAN INSTRUMENTATION"
    end_marker = "void term_render(A3D_WND *wnd)"
    start = present_src.find(start_marker)
    assert start != -1, "FL-4665 timing module banner not found"
    end = present_src.find(end_marker, start)
    assert end != -1, "term_render not found after the FL-4665 timing module"
    return present_src[start:end]


@pytest.fixture(scope="module")
def module_code(module_src: str) -> str:
    """The timing module with all commentary removed — code the compiler sees.

    Used by every test that asks "does this program DO X?", so that an
    explanation of why X is forbidden cannot be mistaken for X itself.
    """
    return strip_comments(module_src)


# -----------------------------------------------------------------------------
# Rule 1 — the instrument must never block the thread it is measuring.
# -----------------------------------------------------------------------------

def test_module_never_calls_glfinish(module_code: str):
    """`glFinish` stops the program until the graphics card is idle.

    Using it here would manufacture the very stall FL-4665 is trying to locate,
    so any measurement taken with it would be self-inflicted and worthless.
    """
    assert "glFinish" not in module_code, (
        "FL-4665 timing module calls glFinish, which forces the CPU to wait for "
        "the GPU. That creates a stall rather than measuring one."
    )


def test_module_never_calls_other_blocking_sync_primitives(module_code: str):
    """Other commands that also wait, and are therefore equally forbidden.

    `glFlush` sends queued work off but is a synchronization hint; `glWaitSync`
    and `glClientWaitSync` wait outright; `glReadPixels` copies from the
    graphics card and waits while doing so. None belongs in a passive timer.
    """
    for blocking_call in ("glFlush", "glWaitSync", "glClientWaitSync", "glReadPixels"):
        assert blocking_call not in module_code, (
            f"FL-4665 timing module calls {blocking_call}, which can block the "
            "render thread. The instrument must observe, never wait."
        )


def test_query_result_is_read_only_after_availability_check(module_code: str):
    """Rule 2: ask "are you done?" before asking "how long did you take?".

    `GL_QUERY_RESULT_AVAILABLE` is the non-blocking question. `GL_QUERY_RESULT`
    is the answer, and reading it before the driver reports availability makes
    the program wait. So the availability check must come FIRST in the source,
    and there must be an early-exit between them so the read is genuinely
    skipped while the answer is not ready.
    """
    avail_at = module_code.find("GL_QUERY_RESULT_AVAILABLE")
    assert avail_at != -1, "no GL_QUERY_RESULT_AVAILABLE check found"

    # Find the blocking read specifically: GL_QUERY_RESULT *not* followed by
    # "_AVAILABLE". The negative lookahead keeps the two constants apart, since
    # one name is a prefix of the other.
    result_reads = [m.start() for m in re.finditer(r"GL_QUERY_RESULT(?!_AVAILABLE)", module_code)]
    assert result_reads, "no GL_QUERY_RESULT read found; the timer never collects results"

    for at in result_reads:
        assert avail_at < at, (
            "GL_QUERY_RESULT is read before GL_QUERY_RESULT_AVAILABLE is checked. "
            "That read blocks the render thread until the GPU answers."
        )
        between = module_code[avail_at:at]
        assert "continue" in between or "return" in between, (
            "no early-exit between the availability check and the result read, so "
            "the result would be read even when the GPU has not finished."
        )


def test_query_ring_is_deeper_than_one_frame(module_src: str):
    """Each timed pass owns several query objects, used in rotation.

    If there were only one, the instrument would have to reuse it while the
    previous frame's measurement was still in flight — and the only ways out of
    that are to wait (forbidden) or to discard every sample. A depth of at least
    two is what makes "never wait, occasionally skip" possible.
    """
    match = re.search(r"#define\s+FL4665_QUERY_RING_DEPTH\s+(\d+)", module_src)
    assert match, "FL4665_QUERY_RING_DEPTH is not defined"
    depth = int(match.group(1))
    assert depth >= 2, (
        f"query ring depth is {depth}; with fewer than 2 slots the instrument "
        "must either block or drop every sample."
    )


def test_busy_ring_slot_is_dropped_not_awaited(module_src: str):
    """When every stopwatch is still running, the sample is skipped and counted.

    Skipping loses one data point. Waiting would corrupt the entire experiment.
    The `dropped` counter is what makes the skip visible in the output rather
    than silently thinning the data.
    """
    assert "in_flight) { st.dropped++; return; }" in module_src, (
        "a busy query slot is not skipped-and-counted; the instrument may be "
        "waiting for a slot instead of dropping the sample."
    )
    assert "dropped" in module_src, "no dropped-sample counter is published"


# -----------------------------------------------------------------------------
# Rule 3 — off by default, and inert when off.
# -----------------------------------------------------------------------------

def test_instrumentation_is_environment_gated(module_src: str):
    """Timing only switches on when an environment variable asks for it."""
    assert 'getenv("FL4665_FRAME_TIMING_PATH")' in module_src
    assert 'getenv("FL4665_FRAME_TIMING")' in module_src


def test_every_entry_point_returns_early_when_disabled(module_src: str):
    """Each public function must bail out immediately when timing is off.

    This is what makes the cost of leaving the instrument compiled into the
    shipping game a single predictable branch instead of real work.
    """
    entry_points = (
        "Fl4665CpuBegin",
        "Fl4665CpuEnd",
        "Fl4665GpuBegin",
        "Fl4665GpuEnd",
        "Fl4665CollectGpuResults",
        "Fl4665FrameBegin",
        "Fl4665FrameReport",
        "Fl4665HostSwapBegin",
        "Fl4665HostSwapEnd",
    )
    for name in entry_points:
        # Locate the function DEFINITION (name followed by a parameter list and
        # an opening brace), then check its first statement is the gate.
        match = re.search(re.escape(name) + r"\s*\([^)]*\)\s*\{", module_src)
        assert match, f"{name} is not defined in the FL-4665 timing module"
        body_start = match.end()
        first_chunk = module_src[body_start:body_start + 120]
        assert "if (!Fl4665FrameTimingEnabled()) return;" in first_chunk, (
            f"{name} does not return early when FL-4665 timing is disabled; it "
            "would cost time on ordinary runs."
        )


# -----------------------------------------------------------------------------
# The instrument must not change what the renderer draws.
# -----------------------------------------------------------------------------

def test_module_does_not_mutate_render_state(module_code: str):
    """A measuring instrument must not alter the thing it measures.

    If the timing code bound a texture, changed a shader, or switched render
    targets, then pixels would differ between a timed run and a normal run — and
    the timed run would no longer be evidence about the normal one.
    """
    forbidden = (
        "glUseProgram",     # would change the active shader
        "glBindTexture",    # would change which image is sampled
        "glBindFramebuffer",  # would change where drawing lands
        "glViewport",       # would change the drawn region
        "glDrawArrays",     # would draw extra geometry
        "glClear",          # would erase real output
        "glEnable",
        "glDisable",
    )
    for call in forbidden:
        assert call not in module_code, (
            f"FL-4665 timing module calls {call}, which changes render state. "
            "Timed and untimed frames must produce identical pixels."
        )


# -----------------------------------------------------------------------------
# Coverage — the instrument must actually measure both rival suspects.
# -----------------------------------------------------------------------------

def test_cpu_and_gpu_span_names_match_their_enums(module_src: str):
    """Every span has both an id and a printable name, in the same order.

    The name arrays are indexed positionally by the enum values. If the two ever
    drifted apart, every reported number would carry the wrong label — the worst
    possible failure for a diagnostic, because the output still looks valid.
    """
    for enum_name, count_token, array_name in (
        ("Fl4665CpuSpan", "FL4665_CPU_SPAN_COUNT", "kFl4665CpuSpanName"),
        ("Fl4665GpuSpan", "FL4665_GPU_SPAN_COUNT", "kFl4665GpuSpanName"),
    ):
        enum_match = re.search(r"enum\s+" + enum_name + r"\s*\{(.*?)\}", module_src, re.S)
        assert enum_match, f"enum {enum_name} not found"
        body = enum_match.group(1)
        # Count the members, excluding the trailing _COUNT sentinel.
        members = [
            m for m in re.findall(r"^\s*(FL4665_[A-Z0-9_]+)", body, re.M)
            if m != count_token
        ]
        array_match = re.search(
            re.escape(array_name) + r"\s*\[" + re.escape(count_token) + r"\]\s*=\s*\{(.*?)\};",
            module_src, re.S)
        assert array_match, f"name array {array_name} not found"
        names = re.findall(r'"([^"]+)"', array_match.group(1))
        assert len(members) == len(names), (
            f"{enum_name} declares {len(members)} spans but {array_name} lists "
            f"{len(names)} names; reported numbers would be mislabelled."
        )


@pytest.mark.parametrize("span", [
    "FL4665_CPU_TERM_RENDER_TOTAL",   # the whole frame, the outer bound
    "FL4665_CPU_FINAL_TUPLE_COMMIT",
    "FL4665_CPU_PRESENT_UPLOAD",
    "FL4665_CPU_PRESENT_DRAW",
])
def test_required_cpu_span_is_actually_used_at_a_call_site(present_src: str, span: str):
    """Declaring a span is not enough — it must wrap real code.

    A span that exists only in the enum would silently report 0.000 ms forever,
    which reads as "this stage is fast" when it really means "nobody measured
    it". So each one must appear in a matched Begin/End pair somewhere in the
    renderer.
    """
    assert f"Fl4665CpuBegin({span})" in present_src, f"{span} is never started"
    assert f"Fl4665CpuEnd({span})" in present_src, f"{span} is never stopped"


@pytest.mark.parametrize("span", [
    "FL4665_GPU_PRESENT_DRAW",
])
def test_required_gpu_span_is_actually_used_at_a_call_site(present_src: str, span: str):
    """Same requirement for the graphics-card stopwatches.

    These are what separate "the readback is slow" from "the readback is waiting
    for slow GPU work" — the central question FL-4665 has not yet answered. A
    missing one leaves that question open.

    Two wiring styles are both acceptable, and the test accepts either:

      * DIRECT — `Fl4665GpuBegin(FL4665_GPU_WINNER)` written at the draw site.
      * VIA HELPER — five of the passes are drawn by two shared helper functions
        (`run_sample_pass` and `run_stage6_pass`) that take the span id as a
        parameter and start/stop the stopwatch inside themselves. At those call
        sites the span appears as a trailing ARGUMENT, not inside a Begin call.
        The companion test below separately proves those helpers really do wrap
        their draw, so passing the id is sufficient evidence the pass is timed.
    """
    direct = f"Fl4665GpuBegin({span})" in present_src and f"Fl4665GpuEnd({span})" in present_src
    # A trailing argument such as "..., 6, FL4665_GPU_INTERNAL6);"
    via_helper = re.search(r",\s*" + re.escape(span) + r"\s*\)", present_src) is not None
    assert direct or via_helper, (
        f"{span} is declared but never reaches a timed draw, so it would report "
        "0.000 ms forever — which reads as 'fast' when it means 'unmeasured'.")


def test_retired_selection_pass_helpers_are_absent(present_src: str):
    assert "run_sample_pass" not in present_src
    assert "run_stage6_pass" not in present_src


def test_gpu_stopwatches_wrap_only_the_draw_call(present_src: str):
    """Each GPU stopwatch must sit immediately around a draw command.

    A graphics-card timer measures the commands between its start and stop. If
    it also enclosed the shader/texture setup above the draw, it would fold
    main-program work into a number labelled "GPU time" and point the
    investigation at the wrong component.
    """
    for match in re.finditer(r"Fl4665GpuBegin\((FL4665_GPU_[A-Z0-9_]+)\);(.*?)Fl4665GpuEnd\(",
                             present_src, re.S):
        span, between = match.group(1), match.group(2)
        assert "glDrawArrays" in between, (
            f"GPU stopwatch {span} does not enclose a draw call")
        for setup_call in ("glUseProgram", "glUniform", "glActiveTexture", "glBindTexture"):
            assert setup_call not in between, (
                f"GPU stopwatch {span} also encloses {setup_call}, which is CPU-side "
                "setup. That would be reported as graphics-card time.")


def test_retired_blocking_readback_span_is_absent(present_src: str):
    assert "FL4665_CPU_PACKED_U3_READBACK" not in present_src
    assert "HarriReadPackedU3Stage" not in present_src


def test_frame_span_opens_and_closes_exactly_once(present_src: str):
    """The whole-frame span is the outer bound every other number sits inside.

    If the parts add up to far less than the total, the missing time is in code
    nobody has instrumented yet — which is itself a finding. That subtraction
    only works if the total is opened and closed once per frame.
    """
    assert present_src.count("Fl4665CpuBegin(FL4665_CPU_TERM_RENDER_TOTAL)") == 1
    assert present_src.count("Fl4665CpuEnd(FL4665_CPU_TERM_RENDER_TOTAL)") == 1
    # These two counts are 2 because the name appears once where the function is
    # DEFINED and once where it is CALLED. A count of 3 would mean a second call
    # site, which would reset or re-emit a frame's record halfway through it.
    assert present_src.count("Fl4665FrameBegin()") == 2
    assert present_src.count("Fl4665FrameReport(") == 2


# -----------------------------------------------------------------------------
# SUSPECT B — the window present, in the host loop.
# -----------------------------------------------------------------------------

def test_host_swap_hooks_are_declared_extern_c(host_src: str):
    """The host loop reaches the timer through two plain C declarations.

    They are `extern "C"` so the two files agree on the symbol names without a
    shared header. This is safe because every build target that compiles the
    host loop also compiles the renderer that defines them.
    """
    assert 'extern "C" void Fl4665HostSwapBegin(const void* window);' in host_src, (
        "the swap-start hook must take the window being presented; without it "
        "the receipt cannot name which terminal produced those pixels")
    assert 'extern "C" void Fl4665HostSwapEnd();' in host_src


def test_every_window_present_is_timed(host_src: str):
    """EVERY SDL_GL_SwapWindow call must be bracketed by the timer.

    There are two: one for the final window (the normal single-player path, with
    VSync on — this is SUSPECT B) and one intermediate present used when more
    than one window is mapped. Leaving either untimed would let present cost
    escape the receipt in some configuration, and the whole point of this run is
    that no boundary goes unmeasured.
    """
    swaps = [m.start() for m in re.finditer(r"SDL_GL_SwapWindow\(", host_src)
             if "// - OpenGL:" not in host_src[max(0, m.start() - 200):m.start()]]
    timed = 0
    for at in swaps:
        before = host_src[max(0, at - 400):at]
        after = host_src[at:at + 400]
        if "Fl4665HostSwapBegin(" in before and "Fl4665HostSwapEnd();" in after:
            timed += 1
    assert timed >= 2, (
        f"only {timed} of {len(swaps)} SDL_GL_SwapWindow call sites are bracketed "
        "by the FL-4665 timing hooks; an untimed present can hide the stall.")


def test_idle_sleep_is_excluded_from_the_swap_measurement(host_src: str):
    """A deliberate sleep must not be counted as a stall.

    The editor idle path calls SDL_Delay(16) to yield the processor on purpose.
    If that sat inside the timed region it would show up as 16 ms of "present
    stall" every idle frame and pollute the evidence, so the stopwatch must stop
    before it.
    """
    sleep_at = host_src.find("SDL_Delay(16);")
    assert sleep_at != -1, "the editor idle SDL_Delay(16) was not found"
    # Look BACKWARDS from the sleep for the two landmarks. Whichever is nearer
    # tells us whether the stopwatch had already stopped before the sleep began.
    # (Searching forwards from the first hook instead would skip past the other
    # swap call site entirely and compare the wrong pair.)
    last_end = host_src.rfind("Fl4665HostSwapEnd();", 0, sleep_at)
    last_swap = host_src.rfind("SDL_GL_SwapWindow(", 0, sleep_at)
    assert last_end != -1, "no swap-timing stop precedes the idle sleep"
    assert last_end > last_swap, (
        "the idle SDL_Delay(16) falls inside the timed swap region; a deliberate "
        "sleep would be reported as 16 ms of present stall every idle frame.")


# -----------------------------------------------------------------------------
# The receipt itself — a line must be interpretable on its own.
# -----------------------------------------------------------------------------

@pytest.mark.parametrize("field", [
    "frame",       # which frame this line describes
    "bridge_gen",  # frame identity shared with the renderer's other receipts
    "wnd",         # which window was drawn to
    "term",        # which terminal instance owned the frame
    "cells_w", "cells_h",  # viewport in character cells
    "px_w", "px_h",        # viewport in device pixels
])
def test_report_line_carries_identity_fields(module_src: str, field: str):
    """Without identity, a number cannot be attributed to a state.

    The project rule for runtime evidence is that a measurement must be paired
    with the state that produced it. A bare duration with no frame, window, or
    viewport attached cannot support any claim about what was on screen.
    """
    assert f'\\"{field}\\"' in module_src, (
        f"the FL-4665 report line does not publish the '{field}' identity field")


def test_gpu_results_publish_their_age(module_src: str):
    """Graphics-card timings arrive late, and the report must admit it.

    A query issued on one frame is usually answered a few frames later, so the
    GPU numbers printed on a line describe EARLIER work. Publishing `age` (how
    many frames back) and `src_frame` keeps that honest; omitting them would
    present stale numbers as if they described the current frame.
    """
    assert '\\"age\\"' in module_src, "GPU spans do not publish a query age"
    assert '\\"src_frame\\"' in module_src, "GPU spans do not publish a source frame"
    assert "last_age" in module_src, "query age is never computed"


def test_swap_receipt_is_published_by_the_hook_itself(module_code: str):
    """The completed swap must publish its own line, not ride on a later frame.

    The earlier design accumulated the duration and printed it on the NEXT
    frame's line. That failed in the one case that matters most: FL-4665 ends in
    a force quit, so if the freeze is inside the present there IS no next frame,
    and the single most diagnostic measurement was guaranteed to be discarded.

    Publishing from inside the end hook makes the receipt survive the freeze.
    """
    match = re.search(r"Fl4665HostSwapEnd\s*\(\s*\)\s*\{(.*?)\n\}", module_code, re.S)
    assert match, "Fl4665HostSwapEnd is not defined"
    body = match.group(1)
    assert "fprintf" in body, (
        "the swap hook does not emit its own receipt; the measurement would be "
        "carried to a later frame and lost on a force-quit")
    assert "fflush" in body, (
        "the swap receipt is not flushed, so a force-quit inside the present "
        "would discard the very line that records it")


def test_swap_receipt_is_joined_to_its_own_frame_and_window(module_code: str):
    """The receipt must carry the render frame and window that preceded it.

    A duration alone cannot be attributed. With several windows mapped, the host
    loop interleaves render and swap across them, so "the most recent frame" is
    not a safe stand-in for "the frame that produced these pixels".
    """
    match = re.search(r"Fl4665HostSwapEnd\s*\(\s*\)\s*\{(.*?)\n\}", module_code, re.S)
    assert match, "Fl4665HostSwapEnd is not defined"
    body = match.group(1)
    for field in ("render_frame", "wnd", "term", "swap_ms", "matched"):
        assert field in body, f"the swap receipt omits the '{field}' field"


def test_unknown_swap_window_is_reported_unmatched_not_guessed(module_code: str):
    """An unattributable swap must say so rather than borrow an owner.

    If the swapped window has no recorded render, the honest output is
    matched=false with null identity. Filling it in from the most recent frame
    would manufacture a fact about a surface that did not produce it — the same
    class of error as inferring a lane from an absent record.
    """
    match = re.search(r"Fl4665HostSwapEnd\s*\(\s*\)\s*\{(.*?)\n\}", module_code, re.S)
    assert match
    body = match.group(1)
    assert 'rec ? "true" : "false"' in body, (
        "the swap receipt does not distinguish a matched owner from an unmatched one")
    assert "rec ? rec->term : nullptr" in body, (
        "an unmatched swap does not report a null owner; it may be inheriting "
        "identity from an unrelated frame")


def test_frame_line_no_longer_carries_a_cross_frame_swap_field(module_code: str):
    """The mis-joined field must be gone, not merely supplemented.

    Leaving `host_swap_prev_ms` on the frame line beside the new receipt would
    keep publishing a number attributed to the wrong frame, and a reader would
    have two disagreeing answers with no way to tell which is sound.
    """
    assert "host_swap_prev_ms" not in module_code, (
        "the frame line still carries the previous frame's swap duration; that "
        "join is exactly what the separate swap receipt replaces")


def test_line_records_are_discriminated(module_code: str):
    """Frame lines and swap lines share one stream, so each must name its type."""
    assert '\\"record\\":\\"frame\\"' in module_code
    assert '\\"record\\":\\"swap\\"' in module_code


def test_report_is_flushed_immediately(module_src: str):
    """Lines must reach disk as they are written.

    FL-4665 runs end in a force quit. Anything still sitting in a memory buffer
    at that moment is lost — and the most valuable line is the last one before
    the freeze, which is exactly the one that would be lost.
    """
    assert "fflush(out);" in module_src, (
        "the FL-4665 report does not flush; a force-quit would discard the "
        "final and most diagnostic lines.")
