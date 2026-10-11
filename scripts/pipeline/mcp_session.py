#!/usr/bin/env python3
"""
MCP Session Manager for Asset Maker.

Provides MCP server connection checking, status reporting, and rendering
capabilities for the Blender-backed sprite generation workflow.

ARCHITECTURE:
    This module abstracts the MCP (Model Context Protocol) transport layer
    that connects the Python asset pipeline to a running Blender instance.
    The Blender MCP server (blender_mcp_addon.py) exposes Blender operations
    over a TCP socket on localhost:9876. The XP MCP server (xp_mcp_server.py)
    is a separate FastMCP stdio server for .xp file manipulation.

    Current capabilities:
      - Connection probing     (is_mcp_available / get_mcp_status)
      - Resource enumeration   (list_mcp_resources)
      - Render dispatch        (render_via_mcp -- stub, not yet implemented)
      - Staging file copy      (copy_to_staging)

    The render path is currently a stub; actual rendering is done by
    invoking Blender headless scripts directly (render_sprite.py,
    render_turntable.py).  The MCP path will replace those once the
    protocol stabilizes.

KEY EXPORTS:
    - is_mcp_available:    TCP probe to check if MCP server is listening
    - get_mcp_status:      Structured status dict (port, availability, Blender PID)
    - render_via_mcp:      (STUB) Dispatch a render job to Blender via MCP
    - list_mcp_resources:  Enumerate known MCP tool names
    - copy_to_staging:     Copy an input file into the staging directory tree

PIPELINE CONTEXT:
    [DEPENDENCY:MCP]     -- Communicates with the MCP server over TCP.
    [DEPENDENCY:BLENDER] -- Blender must be running with the MCP add-on
        loaded for is_mcp_available() to return True.
    [FLOW:CLI]           -- Functions in this module are called from the CLI
        entry point (asset_gen_cli.py) and the pipeline orchestrator.
"""

import socket
import os
import logging
from typing import Dict, List, Tuple, Any, Optional
from pathlib import Path
import base64  # [DEPENDENCY:MCP] -- reserved for future base64-encoded frame streaming
import json  # [DEPENDENCY:MCP] -- reserved for future JSON-RPC request encoding
from scripts.pipeline.service.constants import DEFAULT_RENDER_RESOLUTION

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def is_mcp_available(port: int = 9876) -> bool:
    """
    Check if MCP server is available on the specified port.  [DEPENDENCY:MCP]

    Args:
        port: Port number to check (default: 9876).
            # TODO(PIPELINE-FIX): Default port duplicated across functions;
            # extract to MCP_DEFAULT_PORT constant.

    Returns:
        True if server is listening and accepting connections, False otherwise.
    """
    # WHY: We use connect_ex instead of connect so that a refused connection
    # returns an error code rather than raising an exception.  The 1-second
    # timeout prevents blocking the pipeline if the server is unreachable.
    # Allow environment override (BLENDER_MCP_PORT) when using default port.
    if port == 9876:
        env_port = os.getenv("BLENDER_MCP_PORT")
        if env_port:
            try:
                port = int(env_port)
            except ValueError:
                pass

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        result = s.connect_ex(("localhost", port))
        s.close()
        return result == 0
    except (socket.error, OSError):
        return False


def get_mcp_status() -> Dict[str, Any]:
    """
    Get current MCP server status.  [DEPENDENCY:MCP]

    Returns:
        Dictionary containing:
        - available: bool - whether MCP server is reachable
        - port: int - port checked
        - blender_running: bool - optional, if detectable
    """
    # TODO(PIPELINE-FIX): Port 9876 is hardcoded here and in is_mcp_available();
    # should be a single constant (e.g. MCP_DEFAULT_PORT) or read from config.
    port = 9876
    env_port = os.getenv("BLENDER_MCP_PORT")
    if env_port:
        try:
            port = int(env_port)
        except ValueError:
            port = 9876
    available = is_mcp_available(port)

    status = {
        "available": available,
        "port": port,
        "blender_running": None,  # Could detect via psutils if needed
    }

    # WHY: psutil is optional -- we only use it for a "nice to have" Blender
    # process detection.  If psutil is not installed, blender_running stays
    # None (unknown) rather than False (definitively not running).
    # TODO(PIPELINE-FIX): Process-name matching ("blender" substring) is
    # fragile; a renamed Blender binary would not be detected.  Consider
    # sending a ping command over the MCP socket instead.
    if available:
        try:
            import psutil

            status["blender_running"] = any(
                "blender" in proc.name().lower()
                for proc in psutil.process_iter(["name"])
            )
        except ImportError:
            pass

    return status


def render_via_mcp(asset_def: Any, blend_path: str) -> Tuple[bool, str]:
    """
    Render asset via MCP server connection.

    Args:
        asset_def: AssetDef object or dictionary with asset metadata
        blend_path: Path to Blender .blend file

    Returns:
        Tuple of (success: bool, path_or_error: str)
        - On success: path to rendered image in staging/renders/
        - On failure: error message
    """
    # TODO(PIPELINE-FIX): This is a stub.  The full implementation should:
    # 1. Open a TCP connection to the MCP server on port 9876
    # 2. Send a JSON-RPC request with the render_turntable.py script
    # 3. Stream back base64-encoded PNG frames
    # 4. Decode and save to staging/renders/<asset_name>/
    # Until this is implemented, Blender renders must be triggered via
    # the headless scripts in scripts/blender/ directly.
    try:
        from scripts.blender_client import BlenderMCPClient, BlenderMCPError
        from scripts.pipeline.staging import STAGING_DIR, ensure_staging_structure

        if not getattr(asset_def, "blender_object", None):
            return (False, "AssetDef must specify blender_object for MCP rendering")

        ensure_staging_structure()
        output_path = STAGING_DIR / "renders" / f"{asset_def.name}.png"

        frames_total = sum(getattr(asset_def, "frames", [1]))
        width_px = asset_def.size[0] * getattr(asset_def, "render_resolution", DEFAULT_RENDER_RESOLUTION)
        height_px = asset_def.size[1] * getattr(asset_def, "render_resolution", DEFAULT_RENDER_RESOLUTION)

        mcp_asset_def = {
            "asset_name": asset_def.name,
            "object_name": asset_def.blender_object,
            "angles": getattr(asset_def, "angles", 8),
            "frames": frames_total,
            "resolution": (width_px, height_px),
            "transparent_bg": True,
            "convert_to_magenta": True,
            "frame_order": "angle-major",
        }

        with BlenderMCPClient() as client:
            result_path = client.render_asset(mcp_asset_def, str(output_path))

        return (True, str(result_path))

    except BlenderMCPError as e:
        logger.error(f"MCP render failed: {e}")
        return (False, str(e))
    except Exception as e:
        logger.error(f"MCP render failed: {e}")
        return (False, f"Render error: {str(e)}")


def list_mcp_resources() -> List[str]:
    """
    Query MCP server for available resources.

    Returns:
        List of available objects/functions/tools
    """
    resources = []

    if not is_mcp_available():
        return resources

    # WHY: Hard-coded resource list mirrors the tools registered in
    # xp_mcp_server.py.  In a full implementation this would be queried
    # dynamically via the MCP "list_tools" RPC call.
    # TODO(PIPELINE-FIX): Replace with dynamic MCP introspection once the
    # server supports the standard list_tools endpoint.
    resources = [
        "read_xp_info",
        "create_xp_file",
        "add_layer",
        "write_cell",
        "fill_rect",
        "read_layer_region",
        "set_metadata",
        "replace_color",
        "resize_xp_file",
        "write_ascii_block",
        "shift_layer_content",
        "write_text",
    ]

    return resources


def copy_to_staging(input_path: str, staging_subdir: str = "inputs") -> str:
    """
    Copy a file to staging directory.  [FLOW:CLI]

    Called by the CLI to stage user-provided input files (PNGs, .blend files)
    into the canonical staging/ tree before pipeline execution begins.

    Args:
        input_path: Path to source file.
        staging_subdir: Subdirectory under staging/ (default: 'inputs').

    Returns:
        Absolute path to copied file in staging.

    Raises:
        FileNotFoundError: If input_path does not exist.
    """
    # WHY: shutil and asset_gen.staging are imported inside the function to
    # avoid circular imports -- mcp_session is imported early by the CLI
    # before the full asset_gen package is initialized.  [FLOW:CLI]
    import shutil
    from asset_gen.staging import get_staging_dir

    staging_dir = get_staging_dir(staging_subdir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    source = Path(input_path)
    if not source.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    dest = staging_dir / source.name
    shutil.copy2(source, dest)

    logger.info(f"Copied {source.name} to staging/{staging_subdir}/")

    return str(dest)
