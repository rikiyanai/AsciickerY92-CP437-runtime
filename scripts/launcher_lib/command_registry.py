"""Command registry for launcher text commands.

Derives executable commands from the canonical option tree.
Owns fuzzy matching, tab completion, and dispatch to launcher handlers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import partial
from typing import Callable

from scripts.launcher_lib.option_tree import flatten_options, derive_command_name


@dataclass
class Command:
    name: str
    aliases: list[str] = field(default_factory=list)
    description: str = ""
    handler_ref: str = ""
    action: str | None = None
    kind: str = "action"
    support_state: str = "planned"
    menu_path: list[str] = field(default_factory=list)
    subcommands: dict[str, "Command"] | None = None


class CommandRegistry:
    def __init__(self, handler_globals: dict[str, Callable]):
        self._commands: list[Command] = []
        self._by_name: dict[str, Command] = {}
        self._by_alias: dict[str, Command] = {}
        self._handler_globals = handler_globals

    def build_from_option_tree(self) -> None:
        """Derive all commands from flatten_options().

        Skips leaves with kind in {'command', 'back'} — those are navigation
        surfaces, not executable commands.
        Skips leaves with support_state in {'planned','deferred'}
        (marked unavailable, shown dim in completions).
        Includes 'manual-cli-required', 'implemented-unproven',
        and 'non-authoritative' (marked [retired]).
        """
        self._commands.clear()
        self._by_name.clear()
        self._by_alias.clear()

        items = flatten_options()
        for item in items:
            kind = item.get("kind", "")
            if kind in ("command", "back", "exit"):
                continue

            name = derive_command_name(item)
            if not name:
                continue

            label = item.get("label", "")
            handler = item.get("handler", "")
            action = item.get("action")
            support_state = item.get("support_state", "planned")
            menu_id = item.get("menu_id", "")
            key = item.get("key", "")

            # Derive aliases
            aliases: list[str] = []
            if key and menu_id == "main":
                aliases.append(key)
            # First-letter initials of each word
            words = [w for w in re.split(r"[\s-]+", name) if w]
            initials = "".join(w[0] for w in words)
            if initials and initials != name and initials not in aliases:
                aliases.append(initials)
            # First letter only
            if words and words[0][0] not in aliases:
                aliases.append(words[0][0])

            cmd = Command(
                name=name,
                aliases=list(set(a for a in aliases if a)),
                description=label or item.get("failure_surface", ""),
                handler_ref=handler,
                action=action,
                kind=kind,
                support_state=support_state,
                menu_path=[menu_id] if menu_id else [],
            )

            self._commands.append(cmd)
            self._by_name[name] = cmd
            for a in cmd.aliases:
                self._by_alias[a] = cmd

        # Add synthetic quit command
        quit_cmd = Command(
            name="quit",
            aliases=["q", "exit"],
            description="Exit launcher",
            handler_ref="__quit__",
            kind="exit",
            support_state="manual-cli-required",
        )
        self._commands.append(quit_cmd)
        self._by_name["quit"] = quit_cmd
        for a in quit_cmd.aliases:
            self._by_alias[a] = quit_cmd

    def match(self, text: str) -> list[tuple[Command, int]]:
        """Return (command, score) sorted by score descending.

        Scoring tiers:
          100 — exact alias match
           90 — exact name match
           80 — prefix on command name
           60 — substring on name OR description
           40 — character-gap fuzzy (all chars in order with gaps)
            0 — no match (excluded from results)

        Max 8 results returned.
        """
        text = text.lower().strip()
        if not text:
            return []

        scores: dict[str, tuple[Command, int]] = {}
        for cmd in self._commands:
            score = 0
            name_lower = cmd.name.lower()
            desc_lower = cmd.description.lower()
            aliases_lower = [a.lower() for a in cmd.aliases]

            if text in aliases_lower:
                score = 100
            elif text == name_lower:
                score = 90
            elif name_lower.startswith(text):
                score = 80
            elif text in name_lower or text in desc_lower:
                score = 60
            elif _fuzzy_match(text, name_lower) or _fuzzy_match(text, desc_lower):
                score = 40

            if score > 0:
                scores[cmd.name] = (cmd, score)

        results = sorted(scores.values(), key=lambda x: (-x[1], x[0].name))
        return results[:8]

    def complete(self, text: str) -> str | None:
        """Return the best completion for tab.

        If exactly one match, return its full name.
        If multiple matches share a common prefix longer than text,
        return the common prefix.
        Otherwise return None.
        """
        matches = self.match(text)
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0][0].name
        names = [m[0].name for m in matches]
        prefix = _common_prefix(names)
        if len(prefix) > len(text):
            return prefix
        return None

    def dispatch(self, text: str) -> tuple[Callable | None, list[Command]]:
        """Resolve text to a callable handler.

        Priority: exact name > exact alias > single unambiguous fuzzy match.
        Returns (handler, candidates):
          - (handler, [])     — unambiguous match, ready to call
          - (None, [c1, c2])  — ambiguous, caller should show "Did you mean...?"
          - (None, [])        — no match at all

        When action is set on the matched Command, handler is
        partial(raw_handler, action=self.action) to preserve the action
        parameter for _run_command-style shared handlers.
        """
        text = text.lower().strip()
        if not text:
            return (None, [])

        # Exact name
        if text in self._by_name:
            cmd = self._by_name[text]
            return self._resolve_handler(cmd)

        # Exact alias
        if text in self._by_alias:
            cmd = self._by_alias[text]
            return self._resolve_handler(cmd)

        # Single unambiguous fuzzy match
        matches = self.match(text)
        if len(matches) == 1:
            cmd = matches[0][0]
            return self._resolve_handler(cmd)

        return (None, [m[0] for m in matches])

    def _resolve_handler(self, cmd: Command) -> tuple[Callable | None, list[Command]]:
        if cmd.support_state in ("planned", "deferred"):
            return (None, [cmd])
        if cmd.handler_ref in ("__quit__", "main loop break"):
            return (_QuitHandler(), [])
        if cmd.handler_ref == "_run_command":
            wrapper = self._handler_globals.get("_run_interactive_by_action")
            if wrapper and cmd.action:
                return (partial(wrapper, action=cmd.action), [])
            return (None, [cmd])
        # Dotted names like _wizard.run_vps_wizard or webbrowser.open
        if "." in cmd.handler_ref:
            parts = cmd.handler_ref.split(".")
            obj = self._handler_globals.get(parts[0])
            for part in parts[1:]:
                obj = getattr(obj, part, None) if obj else None
            handler = obj
            if callable(handler):
                return (handler, [])
            return (None, [cmd])
        handler = self._handler_globals.get(cmd.handler_ref)
        if not handler:
            return (None, [cmd])
        return (handler, [])

    def visible_commands(self) -> list[Command]:
        """Flat list of all commands for the 'help' display."""
        return [c for c in self._commands if c.support_state not in ("planned", "deferred")]

    def validate(self) -> list[str]:
        """Check every handler_ref resolves in handler_globals.
        Returns list of errors (empty = valid)."""
        errors = []
        for cmd in self._commands:
            if not cmd.handler_ref or cmd.handler_ref == "__quit__":
                continue
            if cmd.handler_ref not in self._handler_globals:
                errors.append(f"Missing handler: {cmd.handler_ref} for {cmd.name}")
        return errors


class _QuitHandler:
    """Callable sentinel for synthetic quit command."""

    def __call__(self) -> None:
        return None


def _fuzzy_match(query: str, target: str) -> bool:
    it = iter(target)
    return all(c in it for c in query)


def _common_prefix(strings: list[str]) -> str:
    if not strings:
        return ""
    prefix = strings[0]
    for s in strings[1:]:
        while not s.startswith(prefix):
            prefix = prefix[:-1]
            if not prefix:
                return ""
    return prefix
