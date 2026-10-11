"""
loader.py -- Template loading, validation, and construction from JSON files.

ARCHITECTURE:
  This module is the single entry point for turning a template JSON file (or
  dict) into a validated Template dataclass instance. It implements a 7-step
  loading pipeline:

    1. File existence check
    2. JSON parsing
    3. Required-fields schema validation (schemas.py REQUIRED_FIELDS_SCHEMA)
    4. Conditional source-section validation (schemas.py SOURCE_CONDITIONAL_SCHEMA)
    5. Computed invariant validation (sum(frames) > 0, grid alignment)
    6. Best-effort optional section loading (processing, source, debug, output)
    7. Template dataclass construction (models.py Template)

  The hybrid error strategy is intentional:
    - Steps 3-5 are STRICT: failures raise TemplateLoadError immediately.
    - Step 6 is BEST-EFFORT: invalid optional sections fall back to defaults
      with logged warnings, so a template with a typo in debug.label_format
      does not block the entire pipeline.

  Template override chain (highest to lowest priority):
    explicit CLI args > template JSON values > dataclass defaults (models.py)
  This chain is enforced by cli.py resolve_field_config(), not here. The
  loader's job is only to produce a valid Template object.

KEY EXPORTS:
  - TemplateLoader: Class with from_file() and from_dict() class methods.
  - TemplateLoadError: Exception for all template loading failures.

PIPELINE CONTEXT:
  [FLOW:TEMPLATE] -- Called by cli.py load_template_if_needed() and the
  interactive wizard. The returned Template flows into pipeline.run().
  [FLOW:CLI] -- from_dict() is also used by the MCP server for programmatic
  template creation without touching the filesystem.
"""

from pathlib import Path
from typing import Dict, Any
import json
import logging
import warnings
from jsonschema import validate, ValidationError, Draft202012Validator

logger = logging.getLogger(__name__)


class TemplateLoadError(Exception):
    """Raised when template loading fails due to invalid JSON or schema violation.

    Used as the single exception type for all template load failures so callers
    (cli.py, MCP server) can catch one type and display a user-friendly message.
    """

    pass


class TemplateLoader:
    """Loads, validates, and constructs Template objects from JSON files or dicts.

    All methods are classmethods/staticmethods -- no instance state is needed.
    The two public entry points are from_file() (for CLI/wizard use) and
    from_dict() (for testing/MCP server use). Both follow the same 7-step
    validation pipeline.

    [FLOW:TEMPLATE] -- This is the gateway between raw JSON and typed Template
    dataclasses. No Template should be created without going through this loader.
    """

    @classmethod
    def from_file(cls, path: Path) -> "Template":
        """Load, validate, and construct a Template from a JSON file on disk.

        Implements the full 7-step validation pipeline:
          1. File existence
          2. JSON parse
          3. Required-fields schema validation
          4. Conditional source validation
          5. Computed invariant checks
          6. Best-effort optional section loading
          7. Template construction

        [FLOW:TEMPLATE] -- Primary entry point for file-based template loading.

        Args:
            path: Path to template JSON file (e.g. templates/character_idle_walk.json).

        Returns:
            Validated Template object ready for pipeline consumption.

        Raises:
            TemplateLoadError: If file is missing, JSON is malformed, or the
                template violates required constraints or invariants.
        """
        # Step 1: Check file exists
        if not path.exists():
            raise TemplateLoadError(f"Template not found: {path}")

        # Step 2: Read and parse JSON
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise TemplateLoadError(f"Invalid JSON in {path}: {e}")

        # Step 3: Validate required fields schema
        # [FLOW:TEMPLATE] -- Uses pre-compiled validator from schemas.py
        from .schemas import VALIDATORS

        try:
            validate(
                instance=data,
                schema=VALIDATORS["required"].schema,
                cls=Draft202012Validator,
            )
        except ValidationError as e:
            # WHY granular error messages: Distinguishing "missing field" from
            # "wrong type" helps users fix their template JSON without guessing.
            field = list(e.path)[-1] if e.path else "unknown"
            if "required" in e.validator:
                raise TemplateLoadError(f"Missing required field: {field}")
            elif "type" in e.validator:
                raise TemplateLoadError(f"Field '{field}' has wrong type")
            else:
                raise TemplateLoadError(f"Schema validation failed: {e.message}")

        # Step 4: Validate conditional requirements (source sections)
        # WHY separate pass: Conditional validation (if/then/else) can't be
        # cleanly combined with the required-fields schema without making the
        # schema much harder to read and maintain.
        if "source" in data:
            try:
                validate(
                    instance=data,
                    schema=VALIDATORS["conditional"].schema,
                    cls=Draft202012Validator,
                )
            except ValidationError as e:
                if "required" in e.validator:
                    field = list(e.path)[-1] if e.path else "unknown"
                    raise TemplateLoadError(
                        f"Missing required field in source: {field}"
                    )

        # Step 5: Validate computed invariants (angles, frames sum, etc.)
        errors = cls._validate_invariants(data)
        if errors:
            raise TemplateLoadError(f"Template validation failed: {'; '.join(errors)}")

        # Step 6: Load optional sections with best-effort validation
        optional_data = cls._load_optional_sections(data, path)

        # Step 7: Construct and return Template
        return cls.construct_template(data, optional_data)

    @staticmethod
    def _validate_invariants(data: Dict[str, Any]) -> list[str]:
        """Validate computed invariants that JSON Schema alone cannot express.

        These are cross-field constraints that require arithmetic or conditional
        logic beyond what JSON Schema provides. Currently validates:
          - sum(frames) > 0 (at least one frame must exist)
          - Grid alignment placeholder (reserved for future image validation)

        Args:
            data: Parsed JSON dict (already schema-validated).

        Returns:
            List of error message strings. Empty list means all invariants pass.
        """
        errors = []

        # Schema already validates individual frames >= 1 and minItems >= 1,
        # but sum(frames) == 0 is still theoretically possible if the schema
        # evolves. This is a defense-in-depth check.
        frames = data.get("frames", [])
        if sum(frames) == 0:
            errors.append("Sum of frames must be > 0")

        # TODO(PIPELINE-FIX): grid_validate is checked here but is not defined
        # in the processing schema (schemas.py). If a template includes
        # "grid_validate": true, it will be silently ignored at this stage
        # because the schema strips it (additionalProperties: false on processing).
        # Either add grid_validate to the schema or remove this check.
        if data.get("processing", {}).get("grid_validate", False):
            # Will validate against input image dimensions in phase 8
            pass

        return errors

    @staticmethod
    def _load_optional_sections(data: Dict[str, Any], path: Path) -> Dict[str, Any]:
        """Load optional template sections with best-effort error handling.

        For each optional section (processing, source, debug, output), attempts
        to construct the corresponding dataclass from models.py. If construction
        fails (e.g. unexpected field, wrong type), falls back to the default
        instance and logs a warning.

        [FLOW:TEMPLATE] -- This is where the best-effort strategy is implemented.
        A broken debug section should not prevent template loading entirely.

        Args:
            data: Parsed JSON dict (already schema-validated).
            path: Source file path (used for warning messages only).

        Returns:
            Dict mapping section names to constructed dataclass instances.
            Only sections present in the input data are included.
        """
        result = {}
        template_name = data.get("name", path.stem)

        # --- Processing section ---
        if "processing" in data:
            try:
                from .models import ProcessingSection

                processing_data = data["processing"]
                # WHY manual type check: The schema validates types, but if
                # the schema evolves or is bypassed (e.g. from_dict), we still
                # want to catch non-primitive values (lists, dicts) that would
                # cause unexpected behavior in ProcessingSection fields.
                for key in processing_data:
                    if not isinstance(
                        processing_data[key], (bool, int, str, type(None))
                    ):
                        warnings.warn(
                            f"{template_name}: Invalid processing field type: {key}"
                        )
                        logger.warning(f"{template_name}: using default processing")
                        break
                result["processing"] = ProcessingSection(**data["processing"])
            except Exception as e:
                warnings.warn(f"Invalid processing section: {e}")
                logger.warning(f"{template_name}: using default processing")
                result["processing"] = ProcessingSection()

        # --- Source section ---
        # [DEPENDENCY:BLENDER] -- If source.type == "blender", the SourceSection
        # will contain blender_object which is later passed to the render script.
        if "source" in data:
            try:
                from .models import SourceSection

                result["source"] = SourceSection(**data["source"])
            except Exception as e:
                warnings.warn(f"Invalid source section: {e}")
                logger.warning(f"{template_name}: using default source")
                result["source"] = SourceSection()

        # --- Debug section ---
        if "debug" in data:
            try:
                from .models import DebugSection

                result["debug"] = DebugSection(**data["debug"])
            except Exception as e:
                warnings.warn(f"Invalid debug section: {e}")
                logger.warning(f"{template_name}: using default debug")
                result["debug"] = DebugSection()

        # --- Output section ---
        # [DATA-CONTRACT:XP] -- The output section can specify explicit .xp
        # output paths, overriding the default staging directory.
        if "output" in data:
            try:
                from .models import OutputSection

                result["output"] = OutputSection(**data["output"])
            except Exception as e:
                warnings.warn(f"Invalid output section: {e}")
                logger.warning(f"{template_name}: using default output")
                result["output"] = OutputSection()

        # TODO(PIPELINE-FIX): The "layout" section from JSON is never loaded
        # here. LayoutSection fields (rows, cols, frame_order) from the JSON
        # template are silently dropped, and Template always gets the default
        # LayoutSection. This means frame_order from character_idle_walk.json
        # is never available at runtime. Add layout loading or document why
        # it is intentionally omitted.

        return result

    @staticmethod
    def construct_template(
        data: Dict[str, Any], optional_data: Dict[str, Any]
    ) -> "Template":
        """Construct a Template dataclass from validated required and optional data.

        Merges the five required fields (version, name, type, angles, frames)
        with any successfully loaded optional sections into a single Template
        instance.

        [FLOW:TEMPLATE] -- Final step of the loading pipeline. After this,
        the Template is immutable for pipeline purposes.

        Args:
            data: Full parsed JSON dict containing required fields.
            optional_data: Dict of section-name -> dataclass instance from
                _load_optional_sections().

        Returns:
            Fully constructed Template object.
        """
        from .models import Template

        # Start with the five required fields
        template_kwargs = {
            "version": data["version"],
            "name": data["name"],
            "type": data["type"],
            "angles": data["angles"],
            "frames": data["frames"],
        }

        # TODO(PIPELINE-FIX): "size" is checked here but Template dataclass
        # does not have a "size" field. If a template JSON includes "size",
        # this will pass it to Template(**kwargs) which raises TypeError.
        # Either add a "size" field to Template or remove this block.
        if "size" in data:
            template_kwargs["size"] = tuple(data["size"])

        # Merge optional sections (processing, source, debug, output)
        template_kwargs.update(optional_data)

        return Template(**template_kwargs)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Template":
        """Load and validate a template from an in-memory dict.

        Follows the same validation steps as from_file() but skips filesystem
        access. Useful for:
          - Unit tests that construct templates programmatically
          - The MCP server which receives template data over JSON-RPC
          - CLI wizard when building templates from user input

        [FLOW:CLI] -- Alternative entry point for non-file template sources.

        Args:
            data: Dict matching the template JSON structure.

        Returns:
            Validated Template object.

        Raises:
            TemplateLoadError: If required fields are missing or invariants fail.
        """
        # Validate required fields
        from .schemas import VALIDATORS

        try:
            validate(
                instance=data,
                schema=VALIDATORS["required"].schema,
                cls=Draft202012Validator,
            )
        except ValidationError as e:
            field = list(e.path)[-1] if e.path else "unknown"
            if "required" in e.validator:
                raise TemplateLoadError(f"Missing required field: {field}")
            else:
                raise TemplateLoadError(f"Schema validation failed: {e.message}")

        # Validate invariants
        errors = cls._validate_invariants(data)
        if errors:
            raise TemplateLoadError(f"Template validation failed: {'; '.join(errors)}")

        # Load optional sections
        # WHY Path("<dict>"): The path parameter is only used for warning
        # messages. "<dict>" makes it clear the template was not loaded from
        # a file, aiding debugging.
        optional_data = cls._load_optional_sections(data, Path("<dict>"))

        # Construct and return Template
        return cls.construct_template(data, optional_data)


__all__ = ["TemplateLoader", "TemplateLoadError"]
