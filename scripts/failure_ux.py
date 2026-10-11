"""Failure UX — shared seam for interactive prompt/display during recovery.

Seam: single entry point for every interactive prompt that a recovery path
shows to the operator.  Replaces the pattern of inline ``_ask_yes_no()`` /
``_prompt_text()`` calls that each recovery loop duplicates with its own
formatting.

UX dispatches on typed ``FailureDomain``, not on whichever code path caught
the error.  ``show_failure_ux()`` returns an ``OperatorDecision`` that the
caller uses to decide the next action (retry, block, override, abort).

Module has no imports from watchdog_run_canonical or scripts/launcher to
prevent circular imports.
"""

from __future__ import annotations

import enum
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Operator decision — what the operator chose to do about a failure
# ---------------------------------------------------------------------------


class OperatorDecision(enum.Enum):
    """Outcome of an interactive failure prompt.

    Returned by ``show_failure_ux()``.  The caller maps this to a specific
    action (retry the deploy, block with error message, override and proceed,
    or abort the entire run).
    """

    # -- Retry: the operator wants to retry the failed step, possibly after
    #    the recovery action embedded in the prompt (e.g., commit-all).
    RETRY = "retry"

    # -- Block: the operator declined the recovery path.  The caller should
    #    stop and propagate a CanonicalRunError with a clear blocker.
    BLOCK = "block"

    # -- Override and proceed: the operator acknowledged the risk and wants
    #    to proceed despite the failure.  Used for pre-commit bypass,
    #    unreachable server override, etc.
    OVERRIDE_AND_PROCEED = "override_and_proceed"

    # -- Abort: the operator chose to abort the entire run.  The caller
    #    should exit cleanly without a fatal error.
    ABORT = "abort"


# ---------------------------------------------------------------------------
# Error context — full context for a single failure event
# ---------------------------------------------------------------------------


@dataclass
class ErrorContext:
    """Contextual information for one failure event.

    Carried into ``show_failure_ux()`` so the UX has everything it needs
    to render the prompt, detail, and guidance without the callee having
    to reach into global state.

    ``prompt_template`` is a format string that the UX will render with
    available fields (e.g., ``{slot}``, ``{run_id}``).  When *None*, the
    UX uses a default template for the given ``FailureDomain``.
    """

    # -- Core identifiers
    phase: str = ""
    slot: str = ""
    run_id: str = ""

    # -- Error details
    error_kind: str | None = None
    detail: str = ""
    errors: list[str] = field(default_factory=list)

    # -- Blocker / guidance (when domain=LOGICAL or POLICY)
    blocker: dict[str, Any] | None = None
    next_action: str = ""

    # -- Prompt (for prompt-based decisions)
    prompt: str = ""
    prompt_yes_label: str = ""
    prompt_no_label: str = ""

    # -- Extra context for format-string templates
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# I/O abstraction — injectable for testing
# ---------------------------------------------------------------------------

_IO_WRITE: Callable[[str], Any] | None = None
_IO_PROMPT: Callable[[str], str] | None = None


def _set_io(
    write_fn: Callable[[str], Any] | None = None,
    prompt_fn: Callable[[str], str] | None = None,
) -> None:
    """Override I/O for testing.  Pass *None* to reset to defaults."""
    global _IO_WRITE, _IO_PROMPT
    if write_fn is not None:
        _IO_WRITE = write_fn
    if prompt_fn is not None:
        _IO_PROMPT = prompt_fn


def _write(text: str) -> None:
    if _IO_WRITE is not None:
        _IO_WRITE(text)
    else:
        print(text)


def _prompt_input(prompt: str) -> str:
    """Read a line from the operator, handling TTY fallback."""
    if _IO_PROMPT is not None:
        return _IO_PROMPT(prompt)

    # When panel_pause/resume is available (watchdog_run_canonical context),
    # pause the panel to read input.
    _try_panel_pause()

    try:
        if sys.stdin.isatty():
            return input(prompt)
        # stdin was consumed (wrun.py launcher) — try /dev/tty.
        try:
            with open("/dev/tty", "r") as tty:
                sys.stderr.write(prompt)
                sys.stderr.flush()
                return tty.readline().rstrip("\n")
        except OSError:
            raise EOFError from None
    finally:
        _try_panel_resume()


def _try_panel_pause() -> None:
    """Pause the active panel if available (watchdog_run_canonical panel_pause)."""
    try:
        import __main__ as main_mod
        if hasattr(main_mod, "panel_pause"):
            main_mod.panel_pause()
    except (ImportError, AttributeError):
        pass


def _try_panel_resume() -> None:
    """Resume the active panel if available."""
    try:
        import __main__ as main_mod
        if hasattr(main_mod, "panel_resume"):
            main_mod.panel_resume()
    except (ImportError, AttributeError):
        pass


# ---------------------------------------------------------------------------
# Interactive recovery check
# ---------------------------------------------------------------------------


def can_interactive_recovery(
    *,
    json_mode: bool = False,
    json_only: bool = False,
    non_interactive_env: bool = False,
) -> bool:
    """Return *True* if interactive prompts are permitted.

    Centralises the three conditions that previously appeared in every
    prompt-guard across the codebase:
      - stdin is a TTY
      - not in JSON-only output mode
      - not in a non-interactive env (``WATCHDOG_NON_INTERACTIVE``)
    """
    if json_mode or json_only:
        return False
    if non_interactive_env or os.environ.get("WATCHDOG_NON_INTERACTIVE"):
        return False
    return sys.stdin.isatty()


# ---------------------------------------------------------------------------
# Prompt formatting helpers
# ---------------------------------------------------------------------------


def _style_prompt(prompt: str) -> str:
    """Apply terminal styling to prompt text.

    Styling rules match the existing ``_console_prompt()`` in
    watchdog_run_canonical:
      - commit prompts → fail (red) style
      - dirty-tree / tmp-clone → warn (yellow) style
    """
    from cli_style import TEXT_STYLES as _styles

    lower_prompt = prompt.lower()
    if "commit all and reset" in lower_prompt:
        return f"\033[{_styles.get('warn', '93')}m{prompt}\033[0m"
    if "untracked" in lower_prompt or "ignored" in lower_prompt or "no verify" in lower_prompt:
        return f"\033[{_styles.get('fail', '91')}m{prompt}\033[0m"
    if "tmp-clone" in lower_prompt or "overlay" in lower_prompt:
        return f"\033[{_styles.get('warn', '93')}m{prompt}\033[0m"
    return prompt


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def show_failure_ux(
    domain: str,  # FailureDomain enum value or string
    context: ErrorContext | None = None,
    *,
    auto_yes: bool = False,
) -> OperatorDecision:
    """Show failure UX for a given domain and context.

    This is the single entry point for every interactive prompt during
    recovery.  It dispatches to domain-specific prompt handlers that:

    1. Render the error context (what failed, why)
    2. Show the prompt (what recovery action is available)
    3. Read the operator's decision
    4. Return a typed ``OperatorDecision``

    Args:
        domain: The ``FailureDomain`` value (or string).  Used to select
            the UX template.
        context: Full error context.  When *None*, a minimal context is
            created from the domain.
        auto_yes: When *True*, automatically return the affirmative
            decision without prompting (for ``--yes`` mode).

    Returns:
        An ``OperatorDecision`` that the caller uses to decide the next action.
    """
    dom = domain.value if hasattr(domain, "value") else str(domain).lower()
    ctx = context or ErrorContext()

    if not can_interactive_recovery() and not auto_yes:
        return OperatorDecision.BLOCK

    # Dispatch to domain-specific handler.
    if dom == "infrastructure":
        return _show_infrastructure_failure(ctx, auto_yes=auto_yes)
    elif dom == "policy":
        return _show_policy_failure(ctx)
    elif dom == "logical":
        return _show_logical_failure(ctx, auto_yes=auto_yes)

    # Fallback: treat unknown domains as logical blocks.
    return _show_logical_failure(ctx, auto_yes=auto_yes)


# ---------------------------------------------------------------------------
# Domain-specific handlers
# ---------------------------------------------------------------------------


def _show_infrastructure_failure(
    ctx: ErrorContext,
    *,
    auto_yes: bool = False,
) -> OperatorDecision:
    """Prompt for an infrastructure failure (SSH down, timeout, disk full).

    Infrastructure failures show the detail and offer retry or abort.
    """
    if ctx.detail:
        _write(f"  [ERROR] {ctx.detail}")

    if ctx.prompt:
        return _generic_yes_no(
            ctx.prompt,
            auto_yes=auto_yes,
            yes_decision=OperatorDecision.RETRY,
        )

    if ctx.next_action:
        _write(f"  next: {ctx.next_action}")

    # Default: ask to retry.
    return _generic_yes_no(
        "  Retry this step? (Y/N)",
        auto_yes=auto_yes,
        yes_decision=OperatorDecision.RETRY,
    )


def _show_policy_failure(ctx: ErrorContext) -> OperatorDecision:
    """Display a policy failure — always blocks, no override.

    Policy failures cannot be overridden interactively.  The message is
    displayed and the operator must abort.
    """
    _write("  [POLICY BLOCK] This action is not permitted by current policy.")
    if ctx.detail:
        _write(f"  reason: {ctx.detail}")
    if ctx.next_action:
        _write(f"  next: {ctx.next_action}")
    else:
        _write("  This is a hard block and cannot be overridden.")
    return OperatorDecision.BLOCK


def _show_logical_failure(
    ctx: ErrorContext,
    *,
    auto_yes: bool = False,
) -> OperatorDecision:
    """Prompt for a logical failure (dirty tree, bundle mismatch, etc.).

    Logical failures show the detail and offer a yes/no recovery path.
    The operator can accept the recovery (RETRY), decline (BLOCK),
    or abort (ABORT).
    """
    if ctx.errors:
        for error in ctx.errors[:3]:
            _write(f"  [DRIFT] {error}")

    if ctx.detail:
        _write(f"  {ctx.detail}")

    if ctx.prompt:
        return _generic_yes_no(
            ctx.prompt,
            auto_yes=auto_yes,
            yes_decision=OperatorDecision.RETRY,
        )

    if ctx.next_action:
        _write(f"  next: {ctx.next_action}")

    # Default: just block — operator needs guidance.
    return OperatorDecision.BLOCK


# ---------------------------------------------------------------------------
# Generic yes/no prompt
# ---------------------------------------------------------------------------


def _generic_yes_no(
    prompt: str,
    *,
    auto_yes: bool = False,
    yes_decision: OperatorDecision = OperatorDecision.RETRY,
    no_decision: OperatorDecision = OperatorDecision.BLOCK,
) -> OperatorDecision:
    """Show a styled yes/no prompt and return the operator's decision.

    Args:
        prompt: The prompt text (e.g., ``"Commit all and reset? (Y/N)"``).
        auto_yes: When *True*, automatically return ``yes_decision``.
        yes_decision: Decision to return when the operator answers yes.
        no_decision: Decision to return when the operator answers no.

    Returns:
        ``yes_decision``, ``no_decision``, or ``ABORT`` on EOF.
    """
    if auto_yes:
        _write(f"  {prompt} [auto-yes]")
        return yes_decision

    styled = _style_prompt(prompt)
    try:
        answer = _prompt_input(styled + " ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return OperatorDecision.ABORT

    if answer in ("y", "yes"):
        return yes_decision
    if answer in ("n", "no"):
        return no_decision
    # Default: treat unrecognised input as no.
    return no_decision


# ---------------------------------------------------------------------------
# Convenience wrappers for common prompt patterns
# ---------------------------------------------------------------------------


def prompt_commit_all_and_reset(*, auto_yes: bool = False) -> OperatorDecision:
    """Prompt for ``COMMIT_ALL_AND_RESET_PROMPT``.

    The operator must explicitly accept committing all changes and resetting.
    """
    return _generic_yes_no(
        "WORKTREE DIRTY OR RUNTIME IDENTITY PREFLIGHT BLOCKED. COMMIT ALL AND RESET? (Y/N)",
        auto_yes=auto_yes,
        yes_decision=OperatorDecision.RETRY,
    )


def prompt_commit_all_untracked(*, auto_yes: bool = False) -> OperatorDecision:
    """Prompt for ``COMMIT_ALL_UNTRACKED_PROMPT``."""
    return _generic_yes_no(
        "UNTRACKED FILES ARE PRESENT. INCLUDE THEM IN THE WATCHDOG COMMIT? (Y/N)",
        auto_yes=auto_yes,
        yes_decision=OperatorDecision.RETRY,
    )


def prompt_commit_all_ignored(*, auto_yes: bool = False) -> OperatorDecision:
    """Prompt for ``COMMIT_ALL_IGNORED_PROMPT``."""
    return _generic_yes_no(
        "IGNORED FILES ARE PRESENT. FORCE-ADD THEM WITH git add -f? (Y/N)",
        auto_yes=auto_yes,
        yes_decision=OperatorDecision.RETRY,
    )


def prompt_precommit_no_verify(*, auto_yes: bool = False) -> OperatorDecision:
    """Prompt for ``PRECOMMIT_NO_VERIFY_PROMPT``."""
    return _generic_yes_no(
        "FAILED PRECOMMIT: NO VERIFY? Y/N",
        auto_yes=auto_yes,
        yes_decision=OperatorDecision.OVERRIDE_AND_PROCEED,
    )


def prompt_tmp_clone_source_overlay(*, auto_yes: bool = False) -> OperatorDecision:
    """Prompt for ``TMP_CLONE_SOURCE_OVERLAY_PROMPT``."""
    return _generic_yes_no(
        "TMP-CLONE RECOVERY CAN OVERLAY DIRTY LOCAL SOURCE INPUTS BEFORE LAUNCH. DO THAT? (Y/N)",
        auto_yes=auto_yes,
        yes_decision=OperatorDecision.RETRY,
    )


def prompt_runtime_identity_reset(*, auto_yes: bool = False) -> OperatorDecision:
    """Prompt for runtime identity reset (preflight blocked)."""
    return _generic_yes_no(
        "RUNTIME IDENTITY PREFLIGHT BLOCKED. COMMIT ALL AND RESET? (Y/N)",
        auto_yes=auto_yes,
        yes_decision=OperatorDecision.RETRY,
    )


def prompt_confirm_run_intent(*, auto_yes: bool = False) -> OperatorDecision:
    """Prompt for run intent confirmation."""
    return _generic_yes_no(
        "Confirm this pre-run intent? (Y/N)",
        auto_yes=auto_yes,
        yes_decision=OperatorDecision.RETRY,
        no_decision=OperatorDecision.ABORT,
    )


def prompt_unreachable_server(
    slot: str,
    host: str,
    *,
    auto_yes: bool = False,
) -> OperatorDecision:
    """Prompt for unreachable server override."""
    return _generic_yes_no(
        f"{slot} server unreachable — SSH probe to {host}:22 failed. Continue anyway? (y/N)",
        auto_yes=auto_yes,
        yes_decision=OperatorDecision.OVERRIDE_AND_PROCEED,
    )


# ---------------------------------------------------------------------------
# Text prompt (free-form input)
# ---------------------------------------------------------------------------


def prompt_text(prompt: str) -> str:
    """Read a free-form text response from the operator.

    Returns the trimmed response, or empty string on EOF/abort.
    """
    try:
        return _prompt_input(_style_prompt(prompt) + " ").strip()
    except (EOFError, KeyboardInterrupt):
        return ""


# ---------------------------------------------------------------------------
# Resolve non-interactive or auto-yes decisions
# ---------------------------------------------------------------------------


def resolve_decision(
    domain: str,
    context: ErrorContext | None = None,
    *,
    auto_yes: bool = False,
    json_mode: bool = False,
    json_only: bool = False,
) -> OperatorDecision:
    """Resolve a failure UX decision, handling non-interactive environments.

    This is the high-level entry point that checks interactive capability
    first, then dispatches to ``show_failure_ux()``.

    In non-interactive environments (CI, JSON mode, non-TTY):
      - INFRASTRUCTURE → RETRY (auto-retry)
      - LOGICAL → BLOCK (can't prompt, so block)
      - POLICY → BLOCK

    In interactive environments: delegates to ``show_failure_ux()``.
    """
    if not can_interactive_recovery(json_mode=json_mode, json_only=json_only):
        # Non-interactive: resolve without prompting.
        dom = domain.value if hasattr(domain, "value") else str(domain).lower()
        if dom == "infrastructure":
            return OperatorDecision.RETRY
        return OperatorDecision.BLOCK

    return show_failure_ux(domain, context, auto_yes=auto_yes)
