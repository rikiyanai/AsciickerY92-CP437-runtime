"""
schemas.py -- JSON Schema definitions for template validation.

ARCHITECTURE:
  This module defines the JSON Schemas that gate template loading. Before a
  template JSON file is hydrated into the Template dataclass (models.py), it
  must pass two validation stages defined here:

  1. REQUIRED_FIELDS_SCHEMA -- Validates that all mandatory fields (version,
     name, type, angles, frames) are present and correctly typed. Also defines
     the structure of every optional section (processing, source, layout, debug,
     output, animations, size).

  2. SOURCE_CONDITIONAL_SCHEMA -- Enforces conditional requirements on the
     source section: blender sources must include blender_object, while file
     and ai sources must include path.

  Validation is performed by loader.py using pre-compiled Draft202012Validator
  instances (the VALIDATORS dict) for performance.

KEY EXPORTS:
  - REQUIRED_FIELDS_SCHEMA: JSON Schema dict for core + optional field validation.
  - SOURCE_CONDITIONAL_SCHEMA: JSON Schema dict for conditional source requirements.
  - VALIDATORS: Pre-compiled {name: Draft202012Validator} mapping used by loader.py.

PIPELINE CONTEXT:
  [FLOW:TEMPLATE] -- These schemas are the first line of defense in template
  loading. Invalid templates are rejected before any pipeline processing begins.
"""

from typing import Dict, Any
from jsonschema import Draft202012Validator

# ---------------------------------------------------------------------------
# REQUIRED_FIELDS_SCHEMA
# ---------------------------------------------------------------------------
# Validates the overall structure of a template JSON file. The five required
# fields (version, name, type, angles, frames) are enforced by the "required"
# key at the bottom. All optional sections are validated structurally but are
# not required.
#
# [FLOW:TEMPLATE] -- Used by loader.py from_file() and from_dict() as the
# first validation pass.
REQUIRED_FIELDS_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "version": {"type": "integer"},
        "name": {"type": "string"},
        # WHY enum not "string": Constraining type to a known set prevents
        # typos (e.g. "chars" instead of "character") and lets the pipeline
        # apply type-specific defaults (e.g. characters must have 8 angles).
        "type": {"enum": ["character", "item", "ui", "custom"]},
        # WHY enum [1,4,8]: These are the only angle counts the engine supports.
        # 1 = static item, 4 = cardinal directions, 8 = full rotation including
        # diagonals. The slicer and .xp assembler both assume one of these values.
        "angles": {"enum": [1, 4, 8]},
        # WHY array of ints: Each element represents the frame count for one
        # animation. For example, [1, 8] means 1 idle frame + 8 walk frames.
        # The sum determines the sprite sheet column count.
        "frames": {
            "type": "array",
            "items": {"type": "integer", "minimum": 1},
            "minItems": 1,
        },
        # [DATA-CONTRACT:XP] -- size is a [width, height] tuple in character
        # cells. Used for items and UI elements with fixed dimensions.
        "size": {
            "type": "array",
            "prefixItems": [
                {"type": "integer", "minimum": 1},
                {"type": "integer", "minimum": 1},
            ],
            # WHY items:False -- Draft 2020-12 idiom for a fixed-length tuple.
            # Prevents extra elements beyond the two defined in prefixItems.
            "items": False,
        },
        # WHY animations section: Provides human-readable names for frame ranges
        # (e.g. "idle", "walk", "attack"). Used by debug label formatting and
        # the wizard's template description display.
        "animations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "length": {"type": "integer", "minimum": 1},
                },
                "required": ["name", "length"],
                "additionalProperties": False,
            },
            "minItems": 1,
        },
        # [DEPENDENCY:BLENDER] -- source section links the template to its
        # input: a file path, an AI-generated PNG, or a Blender scene object.
        "source": {
            "type": "object",
            "properties": {
                "type": {"enum": ["file", "ai", "blender"]},
                "path": {"type": "string"},
                "blender_object": {"type": "string"},
            },
            # WHY additionalProperties:True -- Allows forward-compatible fields
            # like render_resolution and transparency without schema breakage.
            "additionalProperties": True,
        },
        "processing": {
            "type": "object",
            "properties": {
                "magenta_snap": {"type": "boolean"},
                "palette_quantize": {"type": "boolean"},
                "downscale": {"enum": ["nearest", "box", "area", "block-majority"]},
                "crop_center": {"type": "boolean"},
                # WHY: normalization auto-scales each angle strip to a target cell
                # height and adds 2px top/bottom padding. Critical for characters
                # whose raw renders vary in height across angles.
                "normalization": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        "layout": {
            "type": "object",
            "properties": {
                "rows": {"type": "string"},
                "cols": {"type": "string"},
                "frame_order": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
        "debug": {
            "type": "object",
            "properties": {
                "labels": {"type": "boolean"},
                "label_format": {"type": "string"},
                "label_sheet": {"type": "string"},
                "save_intermediate": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        # [DATA-CONTRACT:XP] -- output section allows templates to specify
        # explicit .xp and .png output paths instead of using the default
        # staging directory layout.
        "output": {
            "type": "object",
            "properties": {
                "xp_path": {"type": "string"},
                "png_path": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    # These five fields are the minimum viable template. Everything else is optional.
    "required": ["version", "name", "type", "angles", "frames"],
    # WHY additionalProperties:True at top level -- Templates may contain
    # keys not yet defined in the schema (forward-compatibility). For example,
    # the "animations" key is validated if present but not required.
    "additionalProperties": True,
}

# ---------------------------------------------------------------------------
# SOURCE_CONDITIONAL_SCHEMA
# ---------------------------------------------------------------------------
# Enforces conditional requirements on the source section using JSON Schema
# if/then/else chains. This is the second validation pass in loader.py.
#
# Rules:
#   - source.type == "blender"  --> source.blender_object is required
#   - source.type == "file"     --> source.path is required
#   - source.type == "ai"       --> source.path is required
#
# [DEPENDENCY:BLENDER] -- The blender_object requirement ensures the pipeline
# knows which Blender scene object to render before launching the subprocess.
#
# TODO(PIPELINE-FIX): The nested if/then/else chain is valid JSON Schema but
# fragile. Adding a new source type (e.g. "url") requires modifying the
# innermost else branch. Consider refactoring to use "oneOf" with per-type
# sub-schemas for better maintainability.
SOURCE_CONDITIONAL_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"source": {"type": "object"}},
    "if": {"properties": {"source": {"properties": {"type": {"const": "blender"}}}}},
    "then": {
        "required": ["source"],
        "properties": {"source": {"required": ["blender_object"]}},
    },
    "else": {
        "if": {"properties": {"source": {"properties": {"type": {"const": "file"}}}}},
        "then": {
            "required": ["source"],
            "properties": {"source": {"required": ["path"]}},
        },
        "else": {
            "if": {"properties": {"source": {"properties": {"type": {"const": "ai"}}}}},
            "then": {
                "required": ["source"],
                "properties": {"source": {"required": ["path"]}},
            },
        },
    },
}

# ---------------------------------------------------------------------------
# Pre-compiled validators
# ---------------------------------------------------------------------------
# WHY pre-compiled: Draft202012Validator compilation is expensive (schema
# traversal + ref resolution). Compiling once at import time avoids repeated
# work on every template load.
VALIDATORS = {
    "required": Draft202012Validator(REQUIRED_FIELDS_SCHEMA),
    "conditional": Draft202012Validator(SOURCE_CONDITIONAL_SCHEMA),
}

# Export schemas for documentation/testing
__all__ = ["REQUIRED_FIELDS_SCHEMA", "SOURCE_CONDITIONAL_SCHEMA", "VALIDATORS"]
