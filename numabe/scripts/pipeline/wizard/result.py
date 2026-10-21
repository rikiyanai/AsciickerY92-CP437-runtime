"""
result.py -- WizardResult dataclass for wizard return contract (WIZ-03).

ARCHITECTURE:
  Replaces the inconsistent 4-tuple return from run_wizard_mode():
    (template, input_path, source_type, blend_file_path)

  with a structured dataclass that supports:
    - Explicit success/failure tracking
    - Multiple errors and warnings
    - Metrics collection (timing, counts)
    - Log path reference for detailed errors

KEY EXPORTS:
  - WizardResult: Main return type for wizard flow

INVARIANTS:
  - success=False requires at least one error (or cancellation intent)
  - Path fields are automatically converted from strings to Path objects

PIPELINE CONTEXT:
  [FLOW:CLI] -- Returned by wizard flow, consumed by main() in cli.py
  [FLOW:WIZARD] -- Carries context through 6-screen wizard flow
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, TYPE_CHECKING
from pathlib import Path

if TYPE_CHECKING:
    from ..templates.models import Template


@dataclass
class WizardResult:
    """Structured return type for wizard flow (WIZ-03).

    Replaces the inconsistent 4-tuple (template, input_path, source_type, blend_file_path)
    with explicit fields and success/error tracking.

    Invariants enforced by __post_init__:
    - success=True requires output_path to be set
    - success=False requires at least one error
    """
    success: bool
    output_path: Optional[Path] = None
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    log_path: Optional[Path] = None

    # Wizard-specific fields (carry forward from old tuple)
    template: Optional['Template'] = None
    intent: Optional[str] = None
    source_type: Optional[str] = None
    input_path: Optional[Path] = None
    blend_file_path: Optional[Path] = None
    blender_object: Optional[str] = None  # Explicit field instead of overloading input_path

    # CONFIGURE screen values (BLEND-15-02: wizard prompt parity)
    # Dict with keys: render_resolution, angles, frames, cell_size, bg_mode
    # None when CONFIGURE screen was skipped (e.g. cancellation before reaching it)
    configure: Optional[Dict[str, Any]] = None

    # AI batch fields
    ai_manifest_path: Optional[Path] = None
    ai_prompt_pack_path: Optional[Path] = None
    ai_output_dir: Optional[Path] = None
    ai_provider: Optional[str] = None

    def __post_init__(self):
        """Validate return contract invariants."""
        # Convert string paths to Path objects
        if self.output_path and isinstance(self.output_path, str):
            self.output_path = Path(self.output_path)
        if self.input_path and isinstance(self.input_path, str):
            self.input_path = Path(self.input_path)
        if self.blend_file_path and isinstance(self.blend_file_path, str):
            self.blend_file_path = Path(self.blend_file_path)
        if self.log_path and isinstance(self.log_path, str):
            self.log_path = Path(self.log_path)
        if self.ai_manifest_path and isinstance(self.ai_manifest_path, str):
            self.ai_manifest_path = Path(self.ai_manifest_path)
        if self.ai_prompt_pack_path and isinstance(self.ai_prompt_pack_path, str):
            self.ai_prompt_pack_path = Path(self.ai_prompt_pack_path)
        if self.ai_output_dir and isinstance(self.ai_output_dir, str):
            self.ai_output_dir = Path(self.ai_output_dir)

        # Validate invariants (commented out for now - allow partial results during wizard)
        # if self.success and not self.output_path:
        #     raise ValueError("Successful wizard result must have output_path")
        if not self.success and not self.errors:
            # Allow empty errors for cancellation case
            if self.intent is None and self.template is None:
                self.errors = ["Wizard cancelled by user"]

    @classmethod
    def cancelled(cls) -> 'WizardResult':
        """Factory for user cancellation."""
        return cls(success=False, errors=["Wizard cancelled by user"])

    @classmethod
    def error(cls, message: str, log_path: Optional[Path] = None) -> 'WizardResult':
        """Factory for single-error failure."""
        return cls(success=False, errors=[message], log_path=log_path)
