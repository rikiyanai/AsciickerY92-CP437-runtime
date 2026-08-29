"""
xp_mcp_server.py -- MCP server exposing .xp file manipulation over JSON-RPC/TCP.

ARCHITECTURE:
    This module is a FastMCP-based server that exposes REXPaint .xp sprite file
    operations as remote-callable tools. It bridges external AI agents and editors
    to the Asciicker sprite pipeline by providing structured read/write access to
    .xp files through the Model Context Protocol (MCP).

    The server wraps xp_core.XPFile and XPLayer, providing cell-level and
    region-level operations (read, write, fill, resize, color-replace), plus
    metadata management for the animation atlas format (angles x frames).

    A lightweight TCP notification mechanism (_notify_tool) pushes reload events
    to the companion XP Tool viewer (port 9877) for live preview during edits.

KEY EXPORTS (MCP tools):
    read_xp_info       -- Read file metadata and layer dimensions.
    create_xp_file     -- Create a new blank .xp file.
    add_layer           -- Append a new empty layer.
    write_cell          -- Write a single cell (glyph + fg/bg colors).
    fill_rect           -- Fill a rectangular region uniformly.
    read_layer_region   -- Read a sub-region of cell data.
    set_metadata        -- Write animation metadata (angles, anim lengths) into Layer 0.
    replace_color       -- Global color find-and-replace across layers.
    resize_xp_file      -- Resize canvas with centered content re-positioning.
    write_ascii_block   -- Write multi-line text/art with CP437 glyph mapping.
    shift_layer_content -- Move content between layers (destructive copy).
    write_text          -- Write single-line text (convenience wrapper).

PIPELINE CONTEXT:
    Upstream: AI agents call these tools to author .xp sprite sheets.
    Downstream: Saved .xp files feed into assembler.py, validator.py,
    and ultimately the C++ engine's sprite loader (sprite.cpp).

    [DEPENDENCY:MCP] -- Requires the `mcp` Python package (FastMCP server framework).
    [DATA-CONTRACT:XP] -- All file I/O uses the REXPaint .xp binary format
        (gzip-compressed, column-major cells, 10 bytes/cell). See xp_core.py.
    [DATA-CONTRACT:CP437] -- Glyph values are CP437 code points (0-255).
        Glyph 0 = transparent/null in REXPaint; glyph 32 = visible space.
    [FLOW:CLI] -- Entry point: `python xp_mcp_server.py` or launched by an MCP
        host. All tools are synchronous request/response over the FastMCP transport.
"""

from mcp.server.fastmcp import FastMCP
import os
import sys
import copy
import socket
import json

# xp_core is a sibling module in scripts/pipeline/
from scripts.pipeline.xp_core import XPFile, XPLayer

# ============================================================================
# TRANSPARENCY CONVENTIONS
# ============================================================================
# The Asciicker pipeline uses TWO transparency markers:
#
# 1. GLYPH 0 (null cell):
#    - Represents an empty/unrendered cell in the .xp file.
#    - The C++ engine skips cells with glyph==0 during sprite processing.
#    - Used as the default fill for new layers and empty regions.
#
# 2. MAGENTA BACKGROUND (255, 0, 255):
#    - REXPaint's native transparency marker for visual editing.
#    - Cells with magenta bg are rendered as transparent in preview tools.
#    - Used by xp_viewer.py and other PNG export pipelines.
#
# 3. COLOR KEY (Layer 0 background):
#    - Per-column transparency reference for palette quantization.
#    - Each column's Layer 0 bg defines the "transparent" color for that frame.
#    - Used by the assembler to handle per-frame transparency variations.
#
# WHY dual convention: glyph==0 is structural (cell exists vs doesn't exist),
# while magenta bg is visual (how to render the cell). The engine checks both.
# Layer 0 color keys support per-frame palette variations in the atlas grid.
# ============================================================================

# WHY "XP Tool" name: This becomes the server identity string in MCP capability
# negotiation. MCP clients display it to the user when listing available servers.
# [DEPENDENCY:MCP] FastMCP handles JSON-RPC framing, tool schema advertisement,
# and transport (stdio for subprocess hosts, SSE for HTTP-based hosts).
mcp = FastMCP("XP Tool")

def _parse_color(color_str):
    """
    Parse a color string into an (R, G, B) tuple.

    Accepts two formats:
        - Hex:  "#RRGGBB" (case-insensitive)
        - CSS:  "rgb(r,g,b)"

    Returns (255, 255, 255) as a fallback for unrecognized formats.

    Args:
        color_str: A color string in "#RRGGBB" or "rgb(r,g,b)" format.

    Returns:
        Tuple[int, int, int]: (R, G, B) values in 0-255 range.

    Raises:
        ValueError: If hex digits are invalid (propagated from int()).
            Malformed rgb() strings also raise ValueError -- see TODO below.

    [DATA-CONTRACT:CP437] Colors are stored as 3-byte RGB triples in .xp cells.
    """
    if color_str.startswith("#"):
        c = color_str.lstrip("#")
        return tuple(int(c[i:i+2], 16) for i in (0, 2, 4))
    if color_str.startswith("rgb"):
        # TODO(PIPELINE-FIX): No validation on malformed rgb() strings; will
        # raise ValueError on non-integer parts or wrong element count.
        parts = color_str[4:-1].split(",")
        return tuple(int(p.strip()) for p in parts)
    return (255, 255, 255) # Fallback -- white for unrecognized input

def _format_color(rgb):
    """
    Format an (R, G, B) tuple as a lowercase hex string "#rrggbb".

    Inverse of _parse_color for hex format.

    Args:
        rgb: Tuple of (R, G, B) integer values 0-255.

    Returns:
        str: Lowercase hex string like "#ff00aa".
    """
    return "#%02x%02x%02x" % rgb

def _notify_tool(path: str):
    """
    Push a reload event to the companion XP Tool viewer over TCP.

    The XP Tool viewer (xp_viewer.py or similar) listens on localhost:9877
    for JSON reload commands. This provides live-preview feedback during
    MCP-driven sprite editing sessions.

    WHY fire-and-forget: The viewer is optional. If it is not running or the
    connection times out (0.5 s), we silently swallow the error so that
    file-save operations are never blocked by viewer availability.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        s.connect(('localhost', 9877))
        cmd = {
            "type": "reload",
            "filepath": path
        }
        s.sendall(json.dumps(cmd).encode('utf-8'))
        s.close()
    except Exception:
        pass  # It's okay if tool isn't running

@mcp.tool()
def read_xp_info(path: str) -> dict:
    """
    Reads metadata and structural info from an XP file.

    [DEPENDENCY:MCP] Exposed as an MCP tool for remote clients.
    [DATA-CONTRACT:XP] Parses REXPaint binary format via xp_core.XPFile.

    Args:
        path: Absolute path to the .xp file.

    Returns:
        dict: On success -- {"version", "layer_count", "layers": [...], "metadata"}.
            On failure -- {"error": str}.
    """
    if not os.path.exists(path):
        return {"error": "File not found"}
    
    try:
        xp = XPFile()
        xp.load(path)
        
        meta = xp.get_metadata()
        layers_info = []
        for i, layer in enumerate(xp.layers):
            layers_info.append({
                "index": i,
                "width": layer.width,
                "height": layer.height
            })
            
        return {
            "version": xp.version,
            "layer_count": len(xp.layers),
            "layers": layers_info,
            "metadata": meta
        }
    except Exception as e:
        return {"error": str(e)}

@mcp.tool()
def create_xp_file(path: str, width: int, height: int, layer_count: int = 3) -> str:
    """
    Creates a new blank XP file with specified dimensions and layer count.

    [DEPENDENCY:MCP] Exposed as an MCP tool.
    [DATA-CONTRACT:XP] Writes a valid REXPaint .xp file with gzip-compressed
    column-major cell data.

    Args:
        path: Absolute path to save the new file.
        width: Width of the sprite sheet in cells.
        height: Height of the sprite sheet in cells.
        layer_count: Number of layers (minimum 3 per XP_LAYER_SPEC, default 3).
            Layer 0 = color key/metadata
            Layer 1 = height encoding
            Layer 2 = primary visual
            Layers 3+ = swoosh overlays (optional)
            Values < 3 are clamped to 3.

    Returns:
        str: Human-readable status message. On error, prefixed with "Error".

    # TODO(PIPELINE-FIX): No validation on width/height bounds. Zero or negative
    # dimensions will create a degenerate file that may crash downstream tools.
    """
    try:
        xp = XPFile()
        # WHY version -1: REXPaint's current format version uses -1 (0xFFFFFFFF).
        # All consumers (C++ engine sprite.cpp, REXPaint editor) expect this value.
        xp.version = -1
        xp.layers = []

        # WHY min 3: XP_LAYER_SPEC requires Layer 0 (colorkey), Layer 1 (height),
        # Layer 2 (visual). C++ loader will reject files with fewer layers.
        for _ in range(max(3, layer_count)):
            # WHY (0, (200,200,200), (0,0,0)): glyph 0 = transparent/null in REXPaint.
            # fg (200,200,200) is a neutral grey default; bg (0,0,0) is black.
            # WHY glyph 0 vs magenta bg: The engine uses glyph==0 for transparency,
            # not a color key. Magenta bg (255,0,255) is REXPaint's native transparency
            # marker for cells that should be skipped during rendering.
            data = [[(0, (200, 200, 200), (0, 0, 0)) for _ in range(width)] for _ in range(height)]
            xp.layers.append(XPLayer(width, height, data))

        xp.save(path)
        _notify_tool(path)
        return f"Created {path} with {len(xp.layers)} layers ({width}x{height})"
    except Exception as e:
        return f"Error creating file: {str(e)}"

@mcp.tool()
def add_layer(path: str) -> str:
    """
    Adds a new layer to an existing XP file.

    The new layer inherits its dimensions from layer 0 and is filled with
    transparent cells (glyph 0).

    [DEPENDENCY:MCP] Exposed as an MCP tool.

    Layer semantics:
        Layer 0 = color key/metadata (angles, animation frame counts)
        Layer 1 = height encoding (vertical position data)
        Layer 2 = primary visual (base sprite appearance)
        Layers 3+ = swoosh overlays (attack trails, VFX)

    Args:
        path: Absolute path to the .xp file.

    Returns:
        str: Status message with new layer index.
            Returns an error string if the file has no existing layers.

    # TODO(PIPELINE-FIX): Assumes all layers share the same dimensions as
    # layer 0. If layers can have heterogeneous sizes, this would be wrong.
    """
    try:
        xp = XPFile()
        xp.load(path)
        
        if not xp.layers:
            return "Error: File has no existing layers to reference dimensions from."
            
        ref = xp.layers[0]
        # Copy transparent clear color from ref or default
        new_data = [[(0, (200, 200, 200), (0, 0, 0)) for _ in range(ref.width)] for _ in range(ref.height)]
        xp.layers.append(XPLayer(ref.width, ref.height, new_data))
        
        xp.save(path)
        _notify_tool(path)
        return f"Added layer {len(xp.layers)-1} to {path}"
    except Exception as e:
        return f"Error adding layer: {str(e)}"

@mcp.tool()
def write_cell(path: str, layer_idx: int, x: int, y: int, glyph: int, fg: str, bg: str) -> str:
    """
    Writes a single cell's glyph and colors.

    This is the lowest-level write operation. Higher-level tools (fill_rect,
    write_ascii_block, write_text) are built on top of the same pattern.

    [DEPENDENCY:MCP] Exposed as an MCP tool.
    [DATA-CONTRACT:CP437] Glyph must be a CP437 code point (0-255).

    Args:
        path: Absolute path to the .xp file.
        layer_idx: Index of the layer to modify (0-based).
        x: X coordinate (column, 0-based from left).
        y: Y coordinate (row, 0-based from top).
        glyph: Integer value of the glyph (CP437 code point, 0-255).
        fg: Foreground color as hex string ("#RRGGBB") or "rgb(r,g,b)".
        bg: Background color as hex string ("#RRGGBB") or "rgb(r,g,b)".

    Returns:
        str: Status message or error string.

    # TODO(PIPELINE-FIX): No validation that glyph is in 0-255 range. An
    # out-of-range glyph will be written and may corrupt the .xp file or
    # produce undefined behavior in the C++ engine.
    """
    try:
        xp = XPFile()
        xp.load(path)
        
        if layer_idx < 0 or layer_idx >= len(xp.layers):
            return f"Error: Layer index {layer_idx} out of bounds"
            
        layer = xp.layers[layer_idx]
        if x < 0 or x >= layer.width or y < 0 or y >= layer.height:
            return f"Error: Coordinates ({x}, {y}) out of bounds ({layer.width}x{layer.height})"
            
        fg_rgb = _parse_color(fg)
        bg_rgb = _parse_color(bg)
        
        layer.data[y][x] = (glyph, fg_rgb, bg_rgb)
        
        xp.save(path)
        _notify_tool(path)
        return f"Updated cell ({x}, {y}) on layer {layer_idx}"
    except Exception as e:
        return f"Error writing cell: {str(e)}"

@mcp.tool()
def fill_rect(path: str, layer_idx: int, x: int, y: int, w: int, h: int, glyph: int, fg: str, bg: str) -> str:
    """
    Fills a rectangular region with uniform glyph and color.

    Cells outside layer bounds are silently skipped (no error), allowing
    partially off-canvas rects.

    [DEPENDENCY:MCP] Exposed as an MCP tool.
    [DATA-CONTRACT:CP437] Glyph must be a CP437 code point (0-255).

    Args:
        path: Absolute path to the .xp file.
        layer_idx: Layer index (0-based).
        x: Top-left X coordinate (0-based).
        y: Top-left Y coordinate (0-based).
        w: Width of rect in cells.
        h: Height of rect in cells.
        glyph: CP437 glyph index (0-255).
        fg: Foreground color as hex "#RRGGBB" or "rgb(r,g,b)".
        bg: Background color as hex "#RRGGBB" or "rgb(r,g,b)".

    Returns:
        str: Status message with count of cells written, or error string.
    """
    try:
        xp = XPFile()
        xp.load(path)
        
        if layer_idx < 0 or layer_idx >= len(xp.layers):
            return f"Error: Layer index {layer_idx} out of bounds"
            
        layer = xp.layers[layer_idx]
        fg_rgb = _parse_color(fg)
        bg_rgb = _parse_color(bg)
        
        count = 0
        for cy in range(y, y + h):
            for cx in range(x, x + w):
                if 0 <= cx < layer.width and 0 <= cy < layer.height:
                    layer.data[cy][cx] = (glyph, fg_rgb, bg_rgb)
                    count += 1
                    
        xp.save(path)
        _notify_tool(path)
        return f"Filled {count} cells on layer {layer_idx}"
    except Exception as e:
        return f"Error filling rect: {str(e)}"

@mcp.tool()
def read_layer_region(path: str, layer_idx: int, x: int, y: int, w: int, h: int) -> dict:
    """
    Reads a rectangular sub-region of cells from a single layer.

    Out-of-bounds cells are returned as None, allowing callers to request
    regions that partially overlap the layer edges without errors.

    [DEPENDENCY:MCP] Exposed as an MCP tool.
    [DATA-CONTRACT:XP] Cell data is returned in row-major order (top-to-bottom,
    left-to-right), which is the natural JSON-friendly layout. Note this differs
    from the .xp file's native column-major storage -- xp_core handles the
    transposition on load.

    Args:
        path: Absolute path to the .xp file.
        layer_idx: Layer index (0-based).
        x: Start X coordinate (column, 0-based).
        y: Start Y coordinate (row, 0-based).
        w: Width to read in cells.
        h: Height to read in cells.

    Returns:
        dict: On success -- {"data": [[glyph, "#rrggbb", "#rrggbb"], ...]}.
            Each row is a list; out-of-bounds cells are None.
            On failure -- {"error": str}.
    """
    try:
        xp = XPFile()
        xp.load(path)
        
        if layer_idx < 0 or layer_idx >= len(xp.layers):
            return {"error": f"Layer index {layer_idx} out of bounds"}
            
        layer = xp.layers[layer_idx]
        result = []
        
        for cy in range(y, y + h):
            row = []
            for cx in range(x, x + w):
                if 0 <= cx < layer.width and 0 <= cy < layer.height:
                    g, f, b = layer.data[cy][cx]
                    row.append([g, _format_color(f), _format_color(b)])
                else:
                    row.append(None)
            result.append(row)
            
        return {"data": result}
    except Exception as e:
        return {"error": str(e)}

@mcp.tool()
def set_metadata(path: str, angles: int, anims: list[int]) -> str:
    """
    Updates the animation metadata stored in Layer 0.

    [DATA-CONTRACT:XP] Layer 0, row 0 encodes the sprite atlas layout:
        Cell (0,0) glyph = number of rotation angles (e.g. 8 for octagonal).
        Cells (1,0), (2,0), ... = frame counts per animation sequence.

    The glyph values use ASCII digit encoding: '0'-'9' for 0-9,
    'A'-'Z' for 10-35. This allows the C++ engine to parse metadata
    by simple character inspection without binary header extensions.

    Args:
        path: Absolute path to the .xp file.
        angles: Number of angles (e.g. 8).
        anims: List of animation lengths (e.g. [1, 8]).

    Returns:
        Status message.
    """
    try:
        xp = XPFile()
        xp.load(path)

        if not xp.layers:
            return "Error: No layers found"

        l0 = xp.layers[0]

        # WHY ASCII-digit encoding: The metadata must be human-readable in
        # REXPaint and parseable by the C++ engine via simple char math.
        def to_digit(n):
            """Convert integer 0-35 to its CP437 digit/letter glyph code."""
            if 0 <= n <= 9: return ord('0') + n
            if 10 <= n <= 35: return ord('A') + (n - 10)
            return ord('?')  # TODO(PIPELINE-FIX): Values >35 silently produce '?'

        # Write angles at (0,0) -- always the first metadata cell
        l0.data[0][0] = (to_digit(angles), (255, 255, 255), (0, 0, 0))

        # Write animation frame counts at (1,0), (2,0), ...
        # WHY starting at x=1: x=0 is reserved for angle count above.
        for i, length in enumerate(anims):
            if i + 1 >= l0.width:
                break  # TODO(PIPELINE-FIX): Silently truncates if more anims than layer width
            l0.data[0][i+1] = (to_digit(length), (255, 255, 255), (0, 0, 0))
            
        xp.save(path)
        _notify_tool(path)
        return f"Detailed metadata updated: Angles={angles}, Anims={anims}"
    except Exception as e:
        return f"Error setting metadata: {str(e)}"

@mcp.tool()
def replace_color(path: str, old_hex: str, new_hex: str, layers: list[int] = None) -> str:
    """
    Replaces all occurrences of a color with another across layers.

    Both foreground and background channels are checked. A single cell
    can be counted once even if both its fg and bg match (the count is
    per-cell, not per-channel).

    [DEPENDENCY:MCP] Exposed as an MCP tool.

    Args:
        path: Absolute path to the .xp file.
        old_hex: Color to find, as "#RRGGBB" or "rgb(r,g,b)".
        new_hex: Replacement color, same format.
        layers: Optional list of layer indices to restrict the search.
            Defaults to all layers. Invalid indices are silently skipped.

    Returns:
        str: Status message with count of cells modified.

    # WHY skip save when count==0: Avoids unnecessary disk I/O and viewer
    # reload notifications when the color was not actually present.
    """
    try:
        xp = XPFile()
        xp.load(path)
        
        old_rgb = _parse_color(old_hex)
        new_rgb = _parse_color(new_hex)
        
        count = 0
        layer_indices = layers if layers is not None else range(len(xp.layers))
        
        for idx in layer_indices:
            if idx < 0 or idx >= len(xp.layers): continue
            layer = xp.layers[idx]
            
            for y in range(layer.height):
                for x in range(layer.width):
                    glyph, fg, bg = layer.data[y][x]
                    modified = False
                    
                    if fg == old_rgb:
                        fg = new_rgb
                        modified = True
                    
                    if bg == old_rgb:
                        bg = new_rgb
                        modified = True
                        
                    if modified:
                        layer.data[y][x] = (glyph, fg, bg)
                        count += 1
                        
        if count > 0:
            xp.save(path)
            _notify_tool(path)
            
        return f"Replaced {count} cells matching {old_hex} with {new_hex}"
    except Exception as e:
        return f"Error replacing color: {str(e)}"

@mcp.tool()
def resize_xp_file(path: str, width: int, height: int) -> str:
    """
    Resizes the canvas of an XP file with centered content re-positioning.

    All layers are resized to the same new dimensions. Content is centered
    within the new canvas; any cells that fall outside the new bounds are
    silently clipped (data loss on shrink).

    [DEPENDENCY:MCP] Exposed as an MCP tool.
    [DATA-CONTRACT:XP] The resulting file remains a valid .xp with all
    layers having identical dimensions.

    Args:
        path: Absolute path to the .xp file.
        width: New canvas width in cells.
        height: New canvas height in cells.

    Returns:
        str: Status message or error string.

    # TODO(PIPELINE-FIX): Shrinking the canvas permanently clips content
    # with no warning. MCP callers cannot undo this operation.
    """
    try:
        xp = XPFile()
        xp.load(path)
        
        for i, layer in enumerate(xp.layers):
            old_w, old_h = layer.width, layer.height
            old_data = layer.data
            
            # WHY (0, (0,0,0), (0,0,0)): Unlike create_xp_file which uses grey fg,
            # resize fills new cells with full black. This inconsistency is minor
            # since glyph 0 means transparent regardless of color values.
            # TODO(PIPELINE-FIX): Default cell value differs from create_xp_file's
            # (200,200,200) fg. Consider extracting a shared EMPTY_CELL constant.
            new_data = [[(0, (0,0,0), (0,0,0)) for _ in range(width)] for _ in range(height)]

            # WHY centering: When growing the canvas, the existing art stays
            # visually centered rather than stuck to the top-left corner.
            # Integer division ensures pixel-perfect alignment.
            off_x = (width - old_w) // 2
            off_y = (height - old_h) // 2
            
            for y in range(old_h):
                for x in range(old_w):
                    # Check bounds in new image
                    ny = y + off_y
                    nx = x + off_x
                    if 0 <= ny < height and 0 <= nx < width:
                        new_data[ny][nx] = old_data[y][x]
            
            # Update layer
            layer.width = width
            layer.height = height
            layer.data = new_data
            
        xp.save(path)
        _notify_tool(path)
        return f"Resized {path} to {width}x{height}"
    except Exception as e:
        return f"Error resizing: {str(e)}"

@mcp.tool()
def write_ascii_block(path: str, layer_idx: int, x: int, y: int, width: int, text: str, fg_hex: str = "#FFFFFF", bg_hex: str = None) -> str:
    """
    Writes a block of text/ascii art to a layer.
    Wraps text at specific 'width' characters.
    Auto-maps common block characters (░▒▓█) to CP437 glyphs.
    
    Args:
        path: Absolute path to the .xp file.
        layer_idx: Layer to write to.
        x: Top-left X.
        y: Top-left Y.
        width: Width of the text block (chars per row).
        text: The string data.
        fg_hex: Foreground color.
        bg_hex: Background color (optional, if None keeps existing bg).
        
    Returns:
        Status message.
    """
    try:
        xp = XPFile()
        xp.load(path)
        
        if layer_idx < 0 or layer_idx >= len(xp.layers):
            return "Error: Invalid layer index"
            
        layer = xp.layers[layer_idx]
        fg = _parse_color(fg_hex)
        bg = _parse_color(bg_hex) if bg_hex else None
        
        # WHY explicit mapping: Unicode block-drawing characters are multi-byte in
        # UTF-8 and have no direct CP437 code point via ord(). This mapping translates
        # the most common box/shade characters to their CP437 equivalents.
        # [DATA-CONTRACT:CP437] Glyph values must be 0-255 CP437 code points.
        mapping = {
            '░': 176,  # CP437 light shade
            '▒': 177,  # CP437 medium shade
            '▓': 178,  # CP437 dark shade
            '█': 219,  # CP437 full block
            ' ': 0     # Initially mapped to 0 (transparent/null) but overridden
                       # to 32 (visible space) below -- see the per-char branch.
        }
        
        rows = []
        for i in range(0, len(text), width):
            rows.append(text[i:i+width])
            
        count = 0
        for r_idx, row_str in enumerate(rows):
            cy = y + r_idx
            if cy >= layer.height: break
            
            for c_idx, char in enumerate(row_str):
                cx = x + c_idx
                if cx >= layer.width: break
                
                # WHY fallback to 63 ('?'): Characters outside CP437 (ord >= 256)
                # cannot be represented; '?' is a visible placeholder.
                glyph = mapping.get(char, ord(char) if ord(char) < 256 else 63)
                if char == ' ': glyph = 32  # WHY override: space must be glyph 32 (visible), not 0 (null/transparent)
                
                # Get existing cell to preserve BG if needed
                old_g, old_fg, old_bg = layer.data[cy][cx]
                
                target_bg = bg if bg else old_bg
                
                layer.data[cy][cx] = (glyph, fg, target_bg)
                count += 1
                
        xp.save(path)
        _notify_tool(path)
        return f"Wrote {count} chars to {path} at ({x},{y})"
        
    except Exception as e:
        return f"Error writing block: {str(e)}"

@mcp.tool()
def shift_layer_content(path: str, src_idx: int, dest_idx: int) -> str:
    """
    Moves content from source layer to destination layer.
    Warning: Overwrites destination layer content.
    
    Args:
        path: Absolute path to the .xp file.
        src_idx: Source layer index.
        dest_idx: Destination layer index.
        
    Returns:
        Status message.
    """
    try:
        xp = XPFile()
        xp.load(path)
        
        if src_idx < 0 or src_idx >= len(xp.layers): return "Error: Invalid src index"
        if dest_idx < 0 or dest_idx >= len(xp.layers): return "Error: Invalid dest index"
        
        # WHY deep copy: Layer data is a nested list of mutable tuples. A shallow
        # copy would leave source and destination sharing the same row lists,
        # causing the subsequent source-clear to also wipe the destination.
        import copy
        xp.layers[dest_idx].data = copy.deepcopy(xp.layers[src_idx].data)

        # WHY clear source: "shift" semantics = move, not copy. The source layer
        # is reset to the same transparent default used by create_xp_file.
        # TODO(PIPELINE-FIX): The copy module is imported at module top *and*
        # re-imported here locally. The local import is redundant but harmless.
        w, h = xp.layers[src_idx].width, xp.layers[src_idx].height
        xp.layers[src_idx].data = [[(0, (200,200,200), (0,0,0)) for _ in range(w)] for _ in range(h)]
        
        xp.save(path)
        _notify_tool(path)
        return f"Shifted layer {src_idx} content to {dest_idx} in {path}"
    except Exception as e:
        return f"Error shifting layer: {str(e)}"

@mcp.tool()
def write_text(path: str, layer_idx: int, x: int, y: int, text: str, fg_hex: str = "#FFFFFF", bg_hex: str = None) -> str:
    """
    Writes a simple single-line string to the layer using CP437 mapping.
    
    Args:
        path: Absolute path to the .xp file.
        layer_idx: Layer to write to.
        x: Start X.
        y: Start Y.
        text: String to write.
        fg_hex: Foreground color.
        bg_hex: Background color (optional).
    """
    return write_ascii_block(path, layer_idx, x, y, len(text), text, fg_hex, bg_hex)

if __name__ == "__main__":
    # WHY mcp.run(): FastMCP handles transport negotiation (stdio or SSE)
    # based on environment. The server advertises all @mcp.tool() functions
    # to connected MCP clients (e.g. Claude Desktop, VS Code extensions).
    print("Starting XP Tool MCP Server...")
    mcp.run()