"""Optional rich menu rendering with plain-text fallback."""

from __future__ import annotations

import os
import sys

from rich.text import Text


def enabled() -> bool:
    return (
        sys.stdout.isatty()
        and os.environ.get("TERM", "") != "dumb"
        and not os.environ.get("CI")
        and not os.environ.get("NO_COLOR")
    )


def menu_panel(title: str, body: str) -> Text:
    del title
    return Text.from_markup(body)
