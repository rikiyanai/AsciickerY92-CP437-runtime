"""Canonical path constants for the failure log.

Single source of truth for all tools that locate docs/FAILURE_LOG.md.
Import these instead of hard-coding the path string in each tool.
"""
from __future__ import annotations

from pathlib import Path

CANONICAL_FAILURE_LOG_REL = Path("docs/FAILURE_LOG.md")
LEGACY_FAILURE_LOG_REL = Path("docs/research/ascii/verification/FAILURE_LOG.md")
CANONICAL_FAILURE_LOG_CANDIDATES = (
    CANONICAL_FAILURE_LOG_REL,
    LEGACY_FAILURE_LOG_REL,
)

# Alias used by maintainer/lib/failure_log.py (which predates the _REL suffix convention)
CANONICAL_FAILURE_LOG = CANONICAL_FAILURE_LOG_REL
