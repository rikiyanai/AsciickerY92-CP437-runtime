"""Captured subprocess output with scrollable viewport.

Pipes subprocess stdout, stores lines in a buffer, and renders a viewport
through the Renderer's content zone.  Scroll controls: arrow keys, PgUp/PgDn.

Pattern origin: testing/launcher_panel.py contained_subprocess()
"""

from __future__ import annotations

import codecs
import subprocess
import os
import select
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .renderer import Renderer


class ScrollView:
    """Scrollable viewer for captured subprocess output."""

    _SPINNER_FRAMES = ("-", "\\", "|", "/")
    _IDLE_BLOCKER_THRESHOLD_SECONDS = 0.75

    def __init__(self, renderer: "Renderer") -> None:
        self._renderer = renderer
        self._lines: list[str] = []
        self._offset: int = 0  # first visible line index

    @property
    def line_count(self) -> int:
        return len(self._lines)

    @property
    def offset(self) -> int:
        return self._offset

    def clear(self) -> None:
        self._lines.clear()
        self._offset = 0

    # ── Subprocess execution ──────────────────────────────────────────────

    def run_captured(
        self,
        args: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> int:
        """Run a subprocess, capture stdout+stderr, display in scroll view.

        Returns the process exit code.
        """
        # Inject FORCE_COLOR=1 to preserve ANSI colors in piped output
        run_env = dict(os.environ)
        if env:
            run_env.update(env)
        run_env.setdefault("FORCE_COLOR", "1")
        run_env.setdefault("PYTHONUNBUFFERED", "1")

        self._lines.clear()
        self._offset = 0

        try:
            proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=cwd,
                env=run_env,
                bufsize=0,
            )
        except FileNotFoundError:
            self._lines.append(f"  Command not found: {args[0]}")
            self._render_viewport()
            return 127
        except Exception as exc:
            self._lines.append(f"  Error: {exc}")
            self._render_viewport()
            return 1

        try:
            assert proc.stdout is not None
            start = time.monotonic()
            last_output = start
            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            partial = ""
            stdout_fd = proc.stdout.fileno()
            spinner_index = 0
            self._render_loading_state(args, elapsed=0.0, timeout=timeout, spinner_index=spinner_index)
            while True:
                now = time.monotonic()
                if timeout is not None and now - start > timeout:
                    raise subprocess.TimeoutExpired(args, timeout)

                ready, _, _ = select.select([stdout_fd], [], [], 0.2)
                now = time.monotonic()
                if not ready:
                    if proc.poll() is not None:
                        break
                    spinner_index = (spinner_index + 1) % len(self._SPINNER_FRAMES)
                    self._render_loading_state(
                        args,
                        elapsed=now - start,
                        timeout=timeout,
                        spinner_index=spinner_index,
                        idle_seconds=now - last_output if self._lines else None,
                    )
                    continue

                chunk = os.read(stdout_fd, 4096)
                if not chunk:
                    if proc.poll() is not None:
                        break
                    continue

                last_output = now
                partial += decoder.decode(chunk)
                new_lines = partial.split("\n")
                partial = new_lines.pop()
                for line in new_lines:
                    # Strip trailing carriage return, preserve ANSI codes
                    self._lines.append(line.rstrip("\r"))

                if not self._lines and partial:
                    spinner_index = (spinner_index + 1) % len(self._SPINNER_FRAMES)
                    self._render_loading_state(
                        args,
                        elapsed=now - start,
                        timeout=timeout,
                        spinner_index=spinner_index,
                        idle_seconds=now - last_output,
                    )
                    continue

                if not self._lines:
                    break

                # Auto-scroll to tail during live output
                view_h = self._renderer.content_height
                if len(self._lines) > view_h:
                    self._offset = len(self._lines) - view_h
                spinner_index = (spinner_index + 1) % len(self._SPINNER_FRAMES)
                self._render_viewport(
                    status_override=self._running_status(
                        elapsed=now - start,
                        spinner_index=spinner_index,
                        idle_seconds=max(0.0, now - last_output),
                    ),
                )

            partial = self._drain_remaining_stdout(stdout_fd, decoder, partial)
            partial += decoder.decode(b"", final=True)
            if partial:
                self._lines.append(partial.rstrip("\r"))

            wait_timeout = None
            if timeout is not None:
                wait_timeout = max(0.0, timeout - (time.monotonic() - start))
            proc.wait(timeout=wait_timeout)
            self._render_viewport()
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            self._lines.append(f"  [timed out after {timeout}s]")
            self._render_viewport()
        except KeyboardInterrupt:
            proc.kill()
            proc.wait()
            self._lines.append("  [interrupted]")
            self._render_viewport()
        finally:
            if proc.stdout:
                proc.stdout.close()

        return proc.returncode if proc.returncode is not None else 1

    def _drain_remaining_stdout(
        self,
        stdout_fd: int,
        decoder: codecs.IncrementalDecoder,
        partial: str,
    ) -> str:
        """Drain any buffered pipe output after the child has exited.

        Without this, late post-run sections can be lost if the process exits
        between select() polls while stdout still has unread bytes buffered.
        """
        while True:
            chunk = os.read(stdout_fd, 4096)
            if not chunk:
                break
            partial += decoder.decode(chunk)
            new_lines = partial.split("\n")
            partial = new_lines.pop()
            for line in new_lines:
                self._lines.append(line.rstrip("\r"))
        return partial

    # ── Static content display ────────────────────────────────────────────

    def show_lines(self, lines: list[str]) -> None:
        """Load pre-built content lines and enter scrollable view.

        Use this for info cards, guides, and any large static text that
        would overflow the renderer's content zone.  Returns when the
        user presses Enter or q.
        """
        self._lines = list(lines)
        self._offset = 0
        self.scroll_loop()

    # ── Interactive scroll ────────────────────────────────────────────────

    def scroll_loop(self) -> None:
        """Enter interactive scroll mode.  Returns on Enter or 'q'."""
        self._render_viewport()
        while True:
            key = self._renderer.input_char(
                valid_keys={"up", "down", "pgup", "pgdn", "home", "end",
                            "q", "\n", "\r"},
            )
            if key in ("q", "\n", "\r", None):
                return

            view_h = self._renderer.content_height
            max_offset = max(0, len(self._lines) - view_h)

            if key == "up":
                self._offset = max(0, self._offset - 1)
            elif key == "down":
                self._offset = min(max_offset, self._offset + 1)
            elif key == "pgup":
                self._offset = max(0, self._offset - view_h)
            elif key == "pgdn":
                self._offset = min(max_offset, self._offset + view_h)
            elif key == "home":
                self._offset = 0
            elif key == "end":
                self._offset = max_offset

            self._render_viewport()

    # ── Viewport rendering ────────────────────────────────────────────────

    def _render_viewport(
        self,
        *,
        content_override: list[str] | None = None,
        status_override: str | None = None,
    ) -> None:
        """Render the current viewport slice through the Renderer."""
        view_h = self._renderer.content_height
        end = self._offset + view_h
        visible = content_override if content_override is not None else self._lines[self._offset:end]

        self._renderer.set_content(visible)

        # Status line with scroll position
        total = len(self._lines)
        if status_override is not None:
            status = status_override
        elif total == 0:
            status = "  (no output)  Enter/q to return"
        elif total <= view_h:
            status = f"  [{total} lines]  Enter/q to return"
        else:
            top = self._offset + 1
            bot = min(self._offset + view_h, total)
            status = f"  [lines {top}-{bot} of {total}]  \u2191\u2193 PgUp/PgDn scroll  Enter/q return"

        self._renderer.set_status(status)
        self._renderer.render()

    def _render_loading_state(
        self,
        args: list[str],
        *,
        elapsed: float,
        timeout: float | None,
        spinner_index: int,
        idle_seconds: float | None = None,
    ) -> None:
        elapsed_text = self._format_duration(elapsed)
        eta_text = self._rough_eta_hint(args, timeout)
        if self._lines:
            content_override = None
            if idle_seconds is not None and idle_seconds >= self._IDLE_BLOCKER_THRESHOLD_SECONDS:
                content_override = self._render_blocking_progress_tail(
                    spinner_index=spinner_index,
                    elapsed_text=elapsed_text,
                    idle_seconds=idle_seconds,
                    eta_text=eta_text,
                )
            self._render_viewport(
                content_override=content_override,
                status_override=self._running_status(
                    elapsed=elapsed,
                    spinner_index=spinner_index,
                    idle_seconds=idle_seconds,
                    eta_text=eta_text,
                ),
            )
            return

        content = [
            "  Launching subprocess...",
            "  No output yet. This can be normal during watchdog startup, deploy, or build steps.",
            f"  Elapsed: {elapsed_text}",
            f"  Rough ETA to first output: {eta_text}",
        ]
        if "watchdog_run_canonical.py" in " ".join(args) and "--commit-all-and-reset" in args:
            content.append(
                "  Reset & Redeploy Candidate may first fork a disposable tmp clone when local edits span multiple scopes."
            )
        if timeout is not None:
            content.append(f"  Hard timeout: {self._format_duration(timeout)}")
        self._render_viewport(
            content_override=content[: max(1, self._renderer.content_height)],
            status_override=f"  {self._SPINNER_FRAMES[spinner_index]} loading... elapsed {elapsed_text}  rough ETA {eta_text}",
        )

    def _running_status(
        self,
        *,
        elapsed: float,
        spinner_index: int,
        idle_seconds: float | None,
        eta_text: str | None = None,
    ) -> str:
        elapsed_text = self._format_duration(elapsed)
        if idle_seconds is None:
            return f"  {self._SPINNER_FRAMES[spinner_index]} running... elapsed {elapsed_text}"
        if eta_text and idle_seconds >= self._IDLE_BLOCKER_THRESHOLD_SECONDS:
            return (
                f"  {self._SPINNER_FRAMES[spinner_index]} running... elapsed {elapsed_text}  "
                f"last output {self._format_duration(idle_seconds)} ago  rough ETA {eta_text}"
            )
        return (
            f"  {self._SPINNER_FRAMES[spinner_index]} running... elapsed {elapsed_text}  "
            f"last output {self._format_duration(idle_seconds)} ago"
        )

    def _render_blocking_progress_tail(
        self,
        *,
        spinner_index: int,
        elapsed_text: str,
        idle_seconds: float,
        eta_text: str,
    ) -> list[str]:
        view_h = max(1, self._renderer.content_height)
        base_lines = list(self._lines)
        stage_hint = self._stage_hint_from_output(base_lines)
        tail = [
            "",
            f"  {self._SPINNER_FRAMES[spinner_index]} still running; waiting for next output line...",
            f"  Elapsed: {elapsed_text}  Last output: {self._format_duration(idle_seconds)} ago",
            f"  Rough ETA for this step: {eta_text}",
        ]
        if stage_hint:
            tail.append(f"  Active step: {stage_hint}")
        return (base_lines + tail)[-view_h:]

    @staticmethod
    def _format_duration(seconds: float) -> str:
        total = max(0, int(seconds))
        minutes, secs = divmod(total, 60)
        return f"{minutes:02d}:{secs:02d}"

    @classmethod
    def _rough_eta_hint(cls, args: list[str], timeout: float | None) -> str:
        mode = cls._arg_value(args, "--mode")
        command = " ".join(args)
        if "watchdog_run_canonical.py" in command:
            if mode == "full":
                return "~00:15-02:00"
            if mode == "watchdog-only":
                return "~00:10-00:45"
            if mode == "current-smoke":
                return "~00:10-00:30"
            return "~00:10-01:00"
        if timeout is not None:
            return f"<={cls._format_duration(min(timeout, 60.0))}"
        return "~00:05-00:20"

    @staticmethod
    def _arg_value(args: list[str], flag: str) -> str | None:
        try:
            index = args.index(flag)
        except ValueError:
            return None
        if index + 1 >= len(args):
            return None
        return args[index + 1]

    @staticmethod
    def _stage_hint_from_output(lines: list[str]) -> str | None:
        for line in reversed(lines[-8:]):
            text = line.strip()
            if not text:
                continue
            lowered = text.lower()
            if text.startswith("[WATCHDOG] TMP-CLONE COPY-BACK"):
                return "copying receipts/artifacts back from tmp clone"
            if text.startswith("[WATCHDOG] TMP-CLONE LAUNCH"):
                return "running canonical proof from committed HEAD tmp clone"
            if text.startswith("[WATCHDOG] TMP-CLONE PREP"):
                return "preparing disposable tmp clone fallback"
            if "[watchdog] making recipe" in lowered:
                return "making recipe from source run"
            if "[watchdog] loading next run" in lowered or "launching follow-up exact repeat" in lowered:
                return "loading follow-up run"
            if text.startswith("[WATCHDOG] FOLLOW-UP PREP") or text.startswith("[WATCHDOG] FOLLOW-UP REPEAT"):
                return "follow-up repeat handoff"
            if text.startswith("CLEARING previous build"):
                return "clearing previous build"
            if text.startswith("MAKING index"):
                return "building wasm/js/html bundle"
            if text.startswith("STAGING site"):
                return "staging site artifacts"
            if "Stamped .web/index.html" in text:
                return "writing stamped web build output"
            if text.startswith("==="):
                return "build/deploy step boundary"
        return None
