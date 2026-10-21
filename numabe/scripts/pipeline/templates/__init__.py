"""
__init__.py -- Public API for the template subsystem.

ARCHITECTURE:
  This package implements the template-driven configuration layer for asset
  generation. Templates are JSON files (e.g. character_idle_walk.json) that
  define angles, frame counts, processing flags, layout, and debug settings.

  The loading pipeline:
    JSON file --> TemplateLoader (loader.py)
              --> schema validation (schemas.py)
              --> Template dataclass (models.py)
              --> pipeline.py / cli.py

  Template override chain (highest to lowest priority):
    explicit CLI args  >  template JSON values  >  dataclass defaults

KEY EXPORTS:
  - Template: Top-level template dataclass (from models.py).
  - ProcessingSection, DebugSection, LayoutSection, SourceSection, OutputSection:
      Section dataclasses for template sub-configurations (from models.py).
  - TemplateLoader: Entry point for loading/validating templates (from loader.py).
  - TemplateLoadError: Exception for all template load failures (from loader.py).

PIPELINE CONTEXT:
  [FLOW:TEMPLATE] -- Import this package to access the full template system.
  [FLOW:CLI] -- cli.py imports TemplateLoader and Template from here.
"""

from .models import (
    Template,
    ProcessingSection,
    DebugSection,
    LayoutSection,
    SourceSection,
    OutputSection,
)
from .loader import TemplateLoader, TemplateLoadError
