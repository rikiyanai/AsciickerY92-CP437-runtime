"""Shared constants for VPS deploy and watchdog scripts.

Import from here rather than defining RECEIPT_PATH or PREFLIGHT_MAX_AGE_SECONDS
independently in multiple scripts. Both simplified_watchdog_preflight.py and
simplified_watchdog_vps_launcher.py must import from this module.
"""
from __future__ import annotations

from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT = SCRIPTS_DIR.parent

# Path where simplified_watchdog_preflight.py writes its receipt.
# Both the preflight and launcher import from here — never hardcode twice.
RECEIPT_PATH = ROOT / "artifacts" / "maintainer" / "watchdog_preflight_receipt.json"

# Maximum age (seconds) of a preflight receipt before the launcher considers it stale.
# 30 minutes: long enough to survive CTRL-C + retry; short enough to catch
# a server restart between preflight and proof.
PREFLIGHT_MAX_AGE_SECONDS = 1800

# VPS systemd unit name for the asciicker server process.
SYSTEMD_UNIT = "asciicker-server"

# Slot-local authoritative_state owner path relative to the service WorkingDirectory.
# Dual-slot hosts run candidate and current side-by-side, so watchdog readers must
# resolve the active unit's WorkingDirectory and read this slot-local path there.
VPS_AUTHORITATIVE_STATE_RELATIVE_PATH = ".web/authoritative_state.json"
