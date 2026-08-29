"""launcher_actions.py — Action dispatch registry for the asciicker launcher.

FL-2790: replaces the 700-line _execute_action if/elif chain with a
handler registry.  Each action maps to a function; launcher.py delegates
at a single call site.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).parent.parent.resolve()


class ActionRegistry:
    """Maps action strings to handler functions.

    Example::

        reg = ActionRegistry()
        reg.register("game-single-player", _run_single_player)
        reg.dispatch("game-single-player", args)  # calls _run_single_player()
    """

    def __init__(self) -> None:
        self._handlers: dict[str, Callable] = {}

    def register(self, name: str, handler: Callable) -> None:
        self._handlers[name] = handler

    def register_alias(self, name: str, target: str) -> None:
        def _alias(args: argparse.Namespace) -> int:
            return self.dispatch(target, args)
        self._handlers[name] = _alias

    def has(self, name: str) -> bool:
        return name in self._handlers

    def names(self) -> list[str]:
        return sorted(self._handlers)

    def dispatch(self, action: str, args: argparse.Namespace) -> int | None:
        handler = self._handlers.get(action)
        if handler is None:
            return None
        return handler(args)
