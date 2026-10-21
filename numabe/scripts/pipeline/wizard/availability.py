"""
availability.py -- Pipeline dependency availability checking.

ARCHITECTURE:
  internal design notes requirement:
    - Check all external deps (Blender, Python libs) at startup
    - Show "2) Render from Blender [unavailable]" instead of hiding
    - On selection of unavailable: block with specific error and install instructions

  Dependencies checked:
    - Blender (for render_blender intent)
    - PIL/Pillow (for image processing)
    - colorama (for colored output - optional)
    - questionary (for interactive prompts)

KEY EXPORTS:
  - check_pipeline_availability: Main availability checker
  - PipelineAvailability: Status container
  - DependencyStatus: Single dependency status
  - format_intent_choice: Add [unavailable] marker to choices
  - get_unavailable_error: Formatted error with install instructions

PIPELINE CONTEXT:
  [FLOW:WIZARD] -- Called at startup to check all dependencies
"""

import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional, List
from dataclasses import dataclass

# Add scripts/ to path for blender_utils import
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from blender_utils import get_blender_bin


@dataclass
class DependencyStatus:
    """Status of a single dependency."""
    available: bool
    reason: Optional[str] = None  # Why unavailable
    install_instructions: Optional[str] = None  # How to fix
    version: Optional[str] = None  # Detected version if available


@dataclass
class PipelineAvailability:
    """Availability status for all intents."""
    intents: Dict[str, DependencyStatus]
    warnings: List[str]  # Non-blocking issues


def check_blender_available() -> DependencyStatus:
    """Check if Blender is available in PATH or standard locations.

    Delegates to the central blender_utils.get_blender_bin() helper.

    Returns:
        DependencyStatus with availability and install instructions if missing
    """
    blender_path = get_blender_bin()

    if blender_path is None:
        return DependencyStatus(
            available=False,
            reason="Blender executable not found in PATH or standard locations",
            install_instructions=(
                "Install Blender:\n"
                "  - macOS: brew install --cask blender\n"
                "  - Linux: sudo apt install blender\n"
                "  - Or download from https://www.blender.org/download/"
            )
        )

    # Try to get version
    try:
        result = subprocess.run(
            [blender_path, "--factory-startup", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        version = result.stdout.split("\n")[0] if result.returncode == 0 else None
    except Exception:
        version = None

    return DependencyStatus(
        available=True,
        version=version
    )


def check_python_lib_available(module_name: str, package_name: Optional[str] = None) -> DependencyStatus:
    """Check if a Python library is importable.

    Args:
        module_name: Name to import (e.g., 'PIL')
        package_name: pip package name if different (e.g., 'Pillow')

    Returns:
        DependencyStatus with availability and install instructions
    """
    pip_name = package_name or module_name

    try:
        __import__(module_name)
        return DependencyStatus(available=True)
    except ImportError:
        return DependencyStatus(
            available=False,
            reason=f"Python module '{module_name}' not installed",
            install_instructions=f"pip install {pip_name}"
        )


def check_mcp_available() -> DependencyStatus:
    """Check if MCP server is running (for assisted Blender rendering).

    This is OPTIONAL - MCP is a recovery path, not required for basic operation.
    """
    try:
        # Use relative import to avoid circular dependency
        import sys
        from pathlib import Path
        
        # Add parent directory to path if not already there
        parent_dir = Path(__file__).parent.parent
        if str(parent_dir) not in sys.path:
            sys.path.insert(0, str(parent_dir))
        
        from mcp_session import is_mcp_available
        if is_mcp_available():
            return DependencyStatus(available=True)
        return DependencyStatus(
            available=False,
            reason="MCP server not running (optional for assisted rendering)",
            install_instructions="Start Blender with MCP addon loaded on port 9876"
        )
    except ImportError:
        return DependencyStatus(
            available=False,
            reason="MCP session module not found",
            install_instructions="MCP is optional - Blender rendering will use headless mode"
        )


def check_pipeline_availability() -> PipelineAvailability:
    """Check all pipeline dependencies at startup.

    internal design notes: "Check all external deps (Blender, Python libs) at startup"

    Returns:
        PipelineAvailability with status for each intent
    """
    warnings = []
    intents = {}

    # Check core Python libs (required for all intents)
    pil_status = check_python_lib_available("PIL", "Pillow")
    questionary_status = check_python_lib_available("questionary")

    # Optional libs
    colorama_status = check_python_lib_available("colorama")
    if not colorama_status.available:
        warnings.append("colorama not installed - colored output disabled")

    # Check Blender (required for render_blender)
    blender_status = check_blender_available()

    # Map intents to their dependency requirements
    # new_character: needs PIL for image processing
    intents["new_character"] = DependencyStatus(
        available=pil_status.available and questionary_status.available,
        reason=pil_status.reason or questionary_status.reason,
        install_instructions=pil_status.install_instructions or questionary_status.install_instructions
    )

    # convert_sheet: needs PIL
    intents["convert_sheet"] = DependencyStatus(
        available=pil_status.available and questionary_status.available,
        reason=pil_status.reason or questionary_status.reason,
        install_instructions=pil_status.install_instructions or questionary_status.install_instructions
    )

    # render_blender: needs Blender + PIL
    if not blender_status.available:
        intents["render_blender"] = DependencyStatus(
            available=False,
            reason=blender_status.reason,
            install_instructions=blender_status.install_instructions
        )
    elif not pil_status.available:
        intents["render_blender"] = DependencyStatus(
            available=False,
            reason=pil_status.reason,
            install_instructions=pil_status.install_instructions
        )
    else:
        intents["render_blender"] = DependencyStatus(
            available=True,
            version=blender_status.version
        )

    # import_mesh: needs Blender + PIL (same as render_blender)
    intents["import_mesh"] = DependencyStatus(
        available=intents["render_blender"].available,
        reason=intents["render_blender"].reason,
        install_instructions=intents["render_blender"].install_instructions,
        version=intents["render_blender"].version
    )

    # modify_xp: needs PIL for processing, xp_core for reading
    intents["modify_xp"] = DependencyStatus(
        available=pil_status.available and questionary_status.available,
        reason=pil_status.reason or questionary_status.reason,
        install_instructions=pil_status.install_instructions or questionary_status.install_instructions
    )

    return PipelineAvailability(intents=intents, warnings=warnings)


def format_intent_choice(intent: str, display_name: str, availability: PipelineAvailability) -> Dict:
    """Format intent choice with availability marker.

    Args:
        intent: Intent key
        display_name: Human-readable name
        availability: Pipeline availability status

    Returns:
        Choice dict for questionary with [unavailable] marker if needed
    """
    status = availability.intents.get(intent)

    if status and not status.available:
        return {
            "name": f"{display_name} [unavailable]",
            "value": intent,
            "disabled": False  # Allow selection to show error message
        }

    return {
        "name": display_name,
        "value": intent
    }


def get_unavailable_error(intent: str, availability: PipelineAvailability, log_path: Path) -> str:
    """Get formatted error message for unavailable intent selection.

    Args:
        intent: Selected intent
        availability: Pipeline availability status
        log_path: Path to wizard error log

    Returns:
        Formatted error message with install instructions
    """
    status = availability.intents.get(intent)

    if status is None or status.available:
        return ""

    lines = [
        f"[WIZARD] UNAVAILABLE: {intent} cannot be used",
        f"Reason: {status.reason}",
    ]

    if status.install_instructions:
        lines.append("")
        lines.append("To fix:")
        for line in status.install_instructions.split("\n"):
            lines.append(f"  {line}")

    lines.append("")
    lines.append(f"(see {log_path})")

    return "\n".join(lines)
