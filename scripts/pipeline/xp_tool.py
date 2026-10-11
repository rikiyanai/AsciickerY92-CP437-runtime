"""
xp_tool.py -- Tkinter-based GUI editor for .xp sprite files.

[DEPENDENCY:TKINTER] [DEPENDENCY:PIL] [DATA-CONTRACT:XP] [DATA-CONTRACT:CP437]

ARCHITECTURE:
    Interactive graphical editor for REXPaint-compatible .xp ASCII sprite files.
    This is the primary manual editing tool in the Asciicker asset pipeline,
    sitting downstream of generator.py / assembler.py (which produce .xp files)
    and upstream of the C++ engine (which consumes them via sprite.cpp).

    The tool is built on Tkinter (not curses) and uses PIL/Pillow for bitmap
    rendering of CP437 glyphs onto canvas widgets. All .xp file I/O is
    delegated to xp_core.py (XPFile / XPLayer classes).

    UI LAYOUT:
        +--------------------------------------------------+
        | Menu Bar (File, Edit, Help)                      |
        | Toolbar (Undo, Redo, Zoom slider, Info)          |
        +----------+---------------------------------------+
        | Sidebar  | Main Canvas (sprite sheet view)       |
        | - Active |   - Scrollable, zoomable              |
        |   Layer  |   - Green dashed grid lines show      |
        | - Visible|     angle/frame cell boundaries       |
        |   Layers |   - Click a cell to open CellEditor   |
        | - Preview|                                       |
        |   (anim) |                                       |
        +----------+---------------------------------------+

    MAIN CLASSES:
        SpriteEditor  -- Main application controller. Manages file I/O,
                         undo/redo, layer visibility, zoom, animation preview,
                         and orchestrates rendering of the full sprite sheet.
        CellEditor    -- Per-cell editing window (Toplevel). Provides zoomed-in
                         pixel-level editing with tools: Paint, Half Block,
                         Dropper, Select, Eraser, Replace Color. Supports
                         copy/paste of cell regions and angle/frame navigation.
        XPServer      -- Background TCP server (port 9877) for IPC. Allows
                         external tools (e.g. Blender render_sprite.py) to
                         trigger reload or query editor state via JSON commands.
        BitmapFont    -- Loads a CP437 sprite sheet (12x12 PNG) and renders
                         individual glyphs with arbitrary foreground colors.

    KEY BINDINGS:
        Ctrl+Z / Cmd+Z            -- Undo
        Ctrl+Shift+Z / Cmd+Shift+Z -- Redo
        Ctrl+C / Cmd+C            -- Copy selection (in CellEditor)
        Ctrl+V / Cmd+V            -- Paste selection (in CellEditor)

    SPRITE SHEET LAYOUT (governed by Layer 0 metadata):
        The sheet is a grid of cells: columns = animation frames, rows = angles.
        Layer 0 encodes metadata (angle count, frame counts per animation).
        Layers 1..N contain the actual sprite art. Layer 0 is hidden from the
        editing canvas but used to calculate grid dimensions.

    [DATA-CONTRACT:XP] File I/O uses xp_core.XPFile.load() / .save(), which
    handle gzip compression and column-major cell storage per the REXPaint spec.

    [DATA-CONTRACT:CP437] Glyph indices are CP437 codepoints (0-255). The
    BitmapFont class renders them from a sprite sheet PNG arranged in a 16-column
    grid matching the CP437 codepage layout.

    [DATA-CONTRACT:PALETTE] Colors are raw 24-bit RGB tuples. The ANSI_COLORS
    table (16 entries) is defined for convenience but actual cell colors are
    arbitrary RGB. Transparency is signaled by bg=(255,0,255) ("magic pink").

USAGE:
    Standalone:   python -m scripts.pipeline.xp_tool [path/to/file.xp]
    From editor:  Use sidebar to toggle layers, click cells to edit,
                  preview animations with Idle/Walk/Stop buttons.
    IPC reload:   echo '{"type":"reload"}' | nc localhost 9877
"""

import tkinter as tk
from tkinter import filedialog, messagebox, colorchooser, Toplevel, Canvas, Menu, simpledialog
from PIL import Image, ImageTk, ImageDraw
import math
import copy
import os
import sys
import socket
from pathlib import Path
import threading
import json
import traceback
import base64
from io import BytesIO
from .xp_core import XPFile, XPLayer

# [DATA-CONTRACT:PALETTE] Standard 16-color ANSI palette mapped to RGB tuples.
# WHY: Used as a quick-reference palette in the UI. Actual .xp cell colors
# are not constrained to this set -- they can be any 24-bit RGB value.
# This table matches the VGA text-mode defaults that REXPaint assumes.
ANSI_COLORS = [
    (0, 0, 0),        # 0: Black
    (128, 0, 0),      # 1: Red
    (0, 128, 0),      # 2: Green
    (128, 128, 0),    # 3: Yellow
    (0, 0, 128),      # 4: Blue
    (128, 0, 128),    # 5: Magenta
    (0, 128, 128),    # 6: Cyan
    (192, 192, 192),  # 7: White
    (128, 128, 128),  # 8: Bright Black
    (255, 0, 0),      # 9: Bright Red
    (0, 255, 0),      # 10: Bright Green
    (255, 255, 0),    # 11: Bright Yellow
    (0, 0, 255),      # 12: Bright Blue
    (255, 0, 255),    # 13: Bright Magenta
    (0, 255, 255),    # 14: Bright Cyan
    (255, 255, 255)   # 15: Bright White
]

def rgb_to_hex(rgb):
    """Convert an (R, G, B) tuple to a Tkinter-compatible hex color string."""
    return "#%02x%02x%02x" % rgb

class XPServer:
    """
    Background TCP Server for Inter-Process Communication.
    
    This server listens on port 9877 and allows external processes (e.g., Blender scripts)
    to trigger commands in the editor, such as 'reload'. avoiding the need to manually 
    re-open files after external generation.
    """
    def __init__(self, app, host='localhost', port=9877):
        """Initialize the IPC server with a reference to the editor app.

        Args:
            app: The SpriteEditor instance to control.
            host: Bind address for the TCP socket (default 'localhost').
            port: Port number to listen on (default 9877).
        """
        self.app = app
        self.host = host
        self.port = port
        self.running = False
        self.socket = None
        self.server_thread = None

    def start(self):
        """Bind the TCP socket and launch the accept-loop thread.

        [DEPENDENCY:MCP] This is the entry point for external IPC. Blender's
        render_sprite.py and other MCP tool integrations connect here.
        """
        if self.running: return
        self.running = True
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.socket.listen(1)
            
            self.server_thread = threading.Thread(target=self._server_loop)
            self.server_thread.daemon = True
            self.server_thread.start()
            print(f"XP Server started on {self.host}:{self.port}")
        except Exception as e:
            print(f"Failed to start server: {e}")
            self.stop()

    def stop(self):
        """Shut down the server socket and signal the accept loop to exit."""
        self.running = False
        if self.socket:
            try: self.socket.close()
            except: pass
            self.socket = None

    def _server_loop(self):
        """Accept loop running on a daemon thread. Uses 1s timeout for clean shutdown.

        [DEPENDENCY:MCP] Each accepted connection is dispatched to _handle_client
        on its own daemon thread (one-shot request/response protocol).

        WHY 1s timeout: socket.accept() blocks indefinitely by default. The
        timeout lets the loop re-check ``self.running`` once per second so the
        server can shut down cleanly when the editor exits.
        """
        self.socket.settimeout(1.0)
        while self.running:
            try:
                try:
                    client, _ = self.socket.accept()
                    client.settimeout(None)
                    t = threading.Thread(target=self._handle_client, args=(client,))
                    t.daemon = True
                    t.start()
                except socket.timeout:
                    continue
                except Exception as e:
                    print(f"Accept error: {e}")
            except Exception:
                if not self.running: break

    def _handle_client(self, client):
        """Read a single JSON command from a client socket, execute it, send response.

        [DEPENDENCY:MCP] Protocol: one-shot request/response per connection. The
        client sends a JSON object and receives a JSON object back before the
        socket is closed.

        WHY 4096 recv: Commands are small JSON dicts (typically < 200 bytes).
        4096 is more than sufficient and avoids the complexity of chunked reads.

        Args:
            client: Connected socket from accept().

        Raises:
            Silently catches all exceptions and logs to stdout.
        """
        try:
            data = client.recv(4096)
            if not data: return

            cmd = json.loads(data.decode('utf-8'))
            response = self._execute(cmd)

            client.sendall(json.dumps(response).encode('utf-8'))
        except Exception as e:
            print(f"Client error: {e}")
        finally:
            client.close()

    def _call_on_ui_thread(self, fn, timeout_s=10.0):
        """Run ``fn`` on Tk's main thread and wait for the result.

        WHY this exists: Tkinter widgets and state are not thread-safe. The TCP
        handlers run on worker threads, so any UI/state access must be routed to
        the main event loop thread.
        """
        done = threading.Event()
        result = {"ok": False, "value": None, "error": None}

        def runner():
            try:
                result["value"] = fn()
                result["ok"] = True
            except Exception as e:
                result["error"] = str(e)
            finally:
                done.set()

        self.app.root.after(0, runner)
        if not done.wait(timeout_s):
            return {"error": f"UI operation timed out after {timeout_s:.1f}s"}
        if not result["ok"]:
            return {"error": result["error"] or "UI operation failed"}
        return {"status": "ok", "data": result["value"]}

    def _execute(self, cmd):
        """Dispatch a command dict.

        [DEPENDENCY:MCP] This is the command router for the IPC protocol.

        Args:
            cmd: Parsed JSON dict with at least a ``type`` key.

        Returns:
            dict: JSON-serializable response with ``status`` or ``error`` key.
        """
        ctype = cmd.get("type")

        if ctype == "get_state":
            if not self.app.xp:
                return {"status": "empty", "filepath": None}
            
            return {
                "status": "loaded",
                "filepath": self.app.filepath,
                "layer_count": len(self.app.xp.layers),
                "metadata": self.app.meta
            }
        
        elif ctype == "reload":
            # WHY after_idle: Tkinter is not thread-safe. This handler runs on
            # the XPServer's accept thread, so we must schedule the reload on
            # the main Tk event loop to avoid race conditions with widget state.
            self.app.root.after_idle(self.app.reload_current)
            return {"status": "ok", "message": "Reload scheduled"}

        elif ctype == "get_preview_frame":
            anim_idx = int(cmd.get("anim_idx", 0))
            frame_idx = int(cmd.get("frame_idx", 0))
            angle_idx = int(cmd.get("angle_idx", 0))
            proj = int(cmd.get("proj", 0))
            scale = int(cmd.get("scale", 2))
            include_image = bool(cmd.get("include_image", True))
            return self._call_on_ui_thread(
                lambda: self.app.mcp_get_preview_frame(
                    anim_idx=anim_idx,
                    frame_idx=frame_idx,
                    angle_idx=angle_idx,
                    proj=proj,
                    scale=scale,
                    include_image=include_image,
                )
            )

        elif ctype == "get_preview_sequence":
            anim_idx = int(cmd.get("anim_idx", 0))
            angle_idx = int(cmd.get("angle_idx", 0))
            proj = int(cmd.get("proj", 0))
            scale = int(cmd.get("scale", 2))
            include_image = bool(cmd.get("include_image", True))
            return self._call_on_ui_thread(
                lambda: self.app.mcp_get_preview_sequence(
                    anim_idx=anim_idx,
                    angle_idx=angle_idx,
                    proj=proj,
                    scale=scale,
                    include_image=include_image,
                )
            )

        elif ctype == "analyze_sequence":
            anim_idx = int(cmd.get("anim_idx", 0))
            angle_idx = int(cmd.get("angle_idx", 0))
            proj = int(cmd.get("proj", 0))
            return self._call_on_ui_thread(
                lambda: self.app.mcp_analyze_sequence(
                    anim_idx=anim_idx,
                    angle_idx=angle_idx,
                    proj=proj,
                )
            )
            
        return {"error": "Unknown command"}


class BitmapFont:
    """Loads a CP437 glyph sprite sheet and renders colored characters.

    [DATA-CONTRACT:CP437] The sprite sheet must be a PNG arranged as a grid of
    256 glyphs (16 columns) in standard CP437 order. Each glyph cell is
    ``char_w x char_h`` pixels. The sheet is loaded once; individual glyphs are
    cropped and cached in ``self.glyphs``.

    Rendering uses alpha masking: the glyph's red channel is used as the alpha
    mask for a solid foreground-color image. This allows arbitrary FG coloring
    without per-glyph pre-tinting.
    """
    def __init__(self, path, char_w, char_h):
        """Load a CP437 glyph sprite sheet and cache individual glyph images.

        Delegates to _render_core.load_font_atlas() for shared implementation.

        Args:
            path: File path to the CP437 sprite sheet PNG.
            char_w: Width of each glyph cell in pixels.
            char_h: Height of each glyph cell in pixels.
        """
        self.char_w = char_w
        self.char_h = char_h
        self.glyphs = {}

        try:
            from scripts.pipeline._render_core import load_font_atlas
            self.glyphs = load_font_atlas(path, char_w=char_w, char_h=char_h)
            self.sheet = Image.open(path).convert("RGBA")
            self.cols = self.sheet.width // char_w
            self.rows = self.sheet.height // char_h
            print(f"Loaded font from {path}, cols={self.cols} rows={self.rows}")
        except Exception as e:
            print(f"Failed to load font {path}: {e}")
            self.sheet = None

    def render(self, char_idx, fg_rgb):
        """Return an RGBA PIL Image of glyph ``char_idx`` tinted to ``fg_rgb``.

        [DEPENDENCY:PIL] Uses Pillow Image compositing and alpha channel manipulation.

        If the font sheet is missing or the glyph index is out of range, returns
        a small X-cross placeholder image so rendering never crashes.

        Args:
            char_idx: CP437 codepoint (0-255) selecting the glyph.
            fg_rgb: (R, G, B) tuple for the foreground color.

        Returns:
            PIL.Image.Image: RGBA image of the rendered glyph, sized char_w x char_h.
        """
        if not self.sheet or char_idx not in self.glyphs:
             # Fallback: draw an X-cross placeholder so missing glyphs are visible
             img = Image.new("RGBA", (self.char_w, self.char_h), (0,0,0,0))
             d = ImageDraw.Draw(img)
             d.rectangle([0, 0, self.char_w-1, self.char_h-1], outline=fg_rgb)
             d.line([0,0, self.char_w-1, self.char_h-1], fill=fg_rgb)
             d.line([0,self.char_h-1, self.char_w-1, 0], fill=fg_rgb)
             return img

        glyph = self.glyphs[char_idx]
        if glyph.mode != 'RGBA':
            glyph = glyph.convert('RGBA')

        # WHY red-channel-as-alpha: The CP437 sprite sheet stores white glyphs
        # on a black background. The red channel of each glyph pixel effectively
        # encodes its opacity (white=opaque, black=transparent). By using it as
        # the alpha mask for a solid fg_rgb image, we get correctly colored,
        # anti-aliased text without per-color pre-rendering.
        colored = Image.new("RGBA", glyph.size, fg_rgb)
        r, g, b, a = glyph.split()
        colored.putalpha(r)
        return colored

# CP437 directional character rotation maps for box-drawing glyphs.
# CW rotation: top-left corner -> top-right -> bottom-right -> bottom-left -> top-left
# Horizontal/vertical lines swap. T-junctions rotate around the cycle.
CP437_ROTATE_CW = {
    218: 191,  # ┌ -> ┐
    191: 217,  # ┐ -> ┘
    217: 192,  # ┘ -> └
    192: 218,  # └ -> ┌
    196: 179,  # ─ -> │
    179: 196,  # │ -> ─
    195: 194,  # ├ -> ┬
    194: 180,  # ┬ -> ┤
    180: 193,  # ┤ -> ┴
    193: 195,  # ┴ -> ├
}
CP437_ROTATE_CCW = {v: k for k, v in CP437_ROTATE_CW.items()}


class FillSelectionDialog(Toplevel):
    """Dialog for filling all cells in a canvas selection with specific values.

    Provides glyph (0-255), FG color, and BG color controls, each with a
    "Change" checkbox. Only checked channels are overwritten. Operates on
    the parent editor's canvas_selection.
    """

    def __init__(self, parent_editor):
        super().__init__(parent_editor.root)
        self.parent_editor = parent_editor
        self.title("Fill Selection")
        self.geometry("380x320")
        self.transient(parent_editor.root)
        self.grab_set()

        self.fill_glyph = 0
        self.fill_fg = (255, 255, 255)
        self.fill_bg = (0, 0, 0)
        self.change_glyph_var = tk.BooleanVar(value=False)
        self.change_fg_var = tk.BooleanVar(value=True)
        self.change_bg_var = tk.BooleanVar(value=True)

        tk.Label(self, text="Fill Selection", font=("Arial", 12, "bold")).pack(pady=(10, 5))

        # Glyph row
        glyph_frame = tk.Frame(self)
        glyph_frame.pack(fill=tk.X, padx=20, pady=3)
        tk.Checkbutton(glyph_frame, text="Glyph (0-255):", variable=self.change_glyph_var).pack(side=tk.LEFT)
        self.glyph_entry = tk.Entry(glyph_frame, width=6)
        self.glyph_entry.insert(0, "0")
        self.glyph_entry.pack(side=tk.LEFT, padx=5)

        # FG row
        fg_frame = tk.Frame(self)
        fg_frame.pack(fill=tk.X, padx=20, pady=3)
        tk.Checkbutton(fg_frame, text="FG Color:", variable=self.change_fg_var).pack(side=tk.LEFT)
        self.fg_btn = tk.Button(
            fg_frame, bg="#ffffff", width=4,
            command=lambda: self._pick_color("fill_fg", self.fg_btn))
        self.fg_btn.pack(side=tk.LEFT, padx=5)

        # BG row
        bg_frame = tk.Frame(self)
        bg_frame.pack(fill=tk.X, padx=20, pady=3)
        tk.Checkbutton(bg_frame, text="BG Color:", variable=self.change_bg_var).pack(side=tk.LEFT)
        self.bg_btn = tk.Button(
            bg_frame, bg="#000000", width=4,
            command=lambda: self._pick_color("fill_bg", self.bg_btn))
        self.bg_btn.pack(side=tk.LEFT, padx=5)

        # Buttons
        btn_frame = tk.Frame(self)
        btn_frame.pack(pady=15)
        tk.Button(btn_frame, text="Fill", command=self._do_fill, bg="#90EE90", width=10).pack(side=tk.LEFT, padx=10)
        tk.Button(btn_frame, text="Cancel", command=self.destroy, width=10).pack(side=tk.LEFT, padx=10)

    def _pick_color(self, attr, btn):
        """Open system color chooser and store result."""
        chosen = colorchooser.askcolor()[0]
        if chosen:
            c = (int(chosen[0]), int(chosen[1]), int(chosen[2]))
            setattr(self, attr, c)
            btn.config(bg=rgb_to_hex(c))

    def _do_fill(self):
        """Fill all cells in the selection with checked channels."""
        editor = self.parent_editor
        if not editor.canvas_selection or not editor.xp:
            messagebox.showwarning("No Selection", "Select a region on the canvas first.")
            return

        # Parse glyph
        try:
            glyph_val = int(self.glyph_entry.get())
            if not 0 <= glyph_val <= 255:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid Glyph", "Glyph must be 0-255.")
            return

        change_glyph = self.change_glyph_var.get()
        change_fg = self.change_fg_var.get()
        change_bg = self.change_bg_var.get()

        if not change_glyph and not change_fg and not change_bg:
            messagebox.showwarning("Nothing Selected", "Check at least one channel to fill.")
            return

        editor.commit_action()
        x1, y1, x2, y2 = editor.canvas_selection
        layer = editor.xp.layers[editor.active_layer_idx]
        count = 0

        for y in range(y1, y2 + 1):
            for x in range(x1, x2 + 1):
                if y >= layer.height or x >= layer.width:
                    continue
                old_glyph, old_fg, old_bg = layer.data[y][x]
                new_glyph = glyph_val if change_glyph else old_glyph
                new_fg = self.fill_fg if change_fg else old_fg
                new_bg = self.fill_bg if change_bg else old_bg
                layer.data[y][x] = (new_glyph, new_fg, new_bg)
                count += 1

        editor.render_sheet()
        messagebox.showinfo("Done", f"Filled {count} cells.")
        self.destroy()


class FindReplaceDialog(Toplevel):
    """Dialog for finding and replacing colors/glyphs within a canvas selection.

    Find section: glyph + match checkbox, FG + match checkbox, BG + match checkbox.
    Replace section: glyph, FG, BG.
    Only cells matching ALL checked criteria get replaced.
    """

    def __init__(self, parent_editor):
        super().__init__(parent_editor.root)
        self.parent_editor = parent_editor
        self.title("Find & Replace")
        self.geometry("400x380")
        self.transient(parent_editor.root)
        self.grab_set()

        self.find_glyph = 0
        self.find_fg = (255, 255, 255)
        self.find_bg = (0, 0, 0)
        self.replace_glyph = 0
        self.replace_fg = (255, 255, 255)
        self.replace_bg = (0, 0, 0)
        self.match_glyph_var = tk.BooleanVar(value=False)
        self.match_fg_var = tk.BooleanVar(value=True)
        self.match_bg_var = tk.BooleanVar(value=True)

        # Find section
        tk.Label(self, text="Find", font=("Arial", 11, "bold")).pack(pady=(10, 5))

        find_glyph_frame = tk.Frame(self)
        find_glyph_frame.pack(fill=tk.X, padx=20, pady=2)
        tk.Checkbutton(find_glyph_frame, text="Glyph:", variable=self.match_glyph_var).pack(side=tk.LEFT)
        self.find_glyph_entry = tk.Entry(find_glyph_frame, width=6)
        self.find_glyph_entry.insert(0, "0")
        self.find_glyph_entry.pack(side=tk.LEFT, padx=5)

        find_color_frame = tk.Frame(self)
        find_color_frame.pack(fill=tk.X, padx=20, pady=2)
        tk.Checkbutton(find_color_frame, text="FG:", variable=self.match_fg_var).pack(side=tk.LEFT)
        self.find_fg_btn = tk.Button(
            find_color_frame, bg="#ffffff", width=4,
            command=lambda: self._pick("find_fg", self.find_fg_btn))
        self.find_fg_btn.pack(side=tk.LEFT, padx=5)
        tk.Checkbutton(find_color_frame, text="BG:", variable=self.match_bg_var).pack(side=tk.LEFT, padx=(10, 0))
        self.find_bg_btn = tk.Button(
            find_color_frame, bg="#000000", width=4,
            command=lambda: self._pick("find_bg", self.find_bg_btn))
        self.find_bg_btn.pack(side=tk.LEFT, padx=5)

        # Replace section
        tk.Label(self, text="Replace With", font=("Arial", 11, "bold")).pack(pady=(15, 5))

        repl_glyph_frame = tk.Frame(self)
        repl_glyph_frame.pack(fill=tk.X, padx=20, pady=2)
        tk.Label(repl_glyph_frame, text="Glyph:").pack(side=tk.LEFT)
        self.repl_glyph_entry = tk.Entry(repl_glyph_frame, width=6)
        self.repl_glyph_entry.insert(0, "0")
        self.repl_glyph_entry.pack(side=tk.LEFT, padx=5)

        repl_color_frame = tk.Frame(self)
        repl_color_frame.pack(fill=tk.X, padx=20, pady=2)
        tk.Label(repl_color_frame, text="FG:").pack(side=tk.LEFT)
        self.repl_fg_btn = tk.Button(
            repl_color_frame, bg="#ffffff", width=4,
            command=lambda: self._pick("replace_fg", self.repl_fg_btn))
        self.repl_fg_btn.pack(side=tk.LEFT, padx=5)
        tk.Label(repl_color_frame, text="BG:").pack(side=tk.LEFT, padx=(10, 0))
        self.repl_bg_btn = tk.Button(
            repl_color_frame, bg="#000000", width=4,
            command=lambda: self._pick("replace_bg", self.repl_bg_btn))
        self.repl_bg_btn.pack(side=tk.LEFT, padx=5)

        # Buttons
        btn_frame = tk.Frame(self)
        btn_frame.pack(pady=15)
        tk.Button(btn_frame, text="Replace", command=self._do_replace, bg="#90EE90", width=10).pack(side=tk.LEFT, padx=10)
        tk.Button(btn_frame, text="Cancel", command=self.destroy, width=10).pack(side=tk.LEFT, padx=10)

    def _pick(self, attr, btn):
        """Open system color chooser and store the result on *attr*."""
        chosen = colorchooser.askcolor()[0]
        if chosen:
            c = (int(chosen[0]), int(chosen[1]), int(chosen[2]))
            setattr(self, attr, c)
            btn.config(bg=rgb_to_hex(c))

    def _do_replace(self):
        """Execute find-and-replace over the parent editor's canvas selection."""
        editor = self.parent_editor
        if not editor.canvas_selection or not editor.xp:
            messagebox.showwarning("No Selection", "Select a region on the canvas first.")
            return

        match_glyph = self.match_glyph_var.get()
        match_fg = self.match_fg_var.get()
        match_bg = self.match_bg_var.get()

        if not match_glyph and not match_fg and not match_bg:
            messagebox.showwarning("No Criteria", "Check at least one match criterion.")
            return

        # Parse find/replace glyphs
        try:
            find_glyph_val = int(self.find_glyph_entry.get())
            repl_glyph_val = int(self.repl_glyph_entry.get())
        except ValueError:
            messagebox.showerror("Invalid Glyph", "Glyph must be an integer.")
            return

        editor.commit_action()
        x1, y1, x2, y2 = editor.canvas_selection
        layer = editor.xp.layers[editor.active_layer_idx]
        count = 0

        for y in range(y1, y2 + 1):
            for x in range(x1, x2 + 1):
                if y >= layer.height or x >= layer.width:
                    continue
                glyph, fg, bg = layer.data[y][x]
                glyph_match = (not match_glyph) or (glyph == find_glyph_val)
                fg_match = (not match_fg) or (fg == self.find_fg)
                bg_match = (not match_bg) or (bg == self.find_bg)
                if glyph_match and fg_match and bg_match:
                    new_glyph = repl_glyph_val if match_glyph else glyph
                    new_fg = self.replace_fg if match_fg else fg
                    new_bg = self.replace_bg if match_bg else bg
                    layer.data[y][x] = (new_glyph, new_fg, new_bg)
                    count += 1

        editor.render_sheet()
        messagebox.showinfo("Done", f"Replaced {count} cells.")
        self.destroy()


# Keep backward-compatible alias
ColorReplaceDialog = FindReplaceDialog


# ============================================================================
# Pure-logic functions (no tkinter) for testability
# ============================================================================

def fill_selection(layer, selection, glyph, fg, bg,
                   change_glyph=True, change_fg=True, change_bg=True):
    """Fill cells in a selection region.  Same algorithm as FillSelectionDialog._do_fill.

    Args:
        layer: XPLayer to modify (mutated in place).
        selection: (x1, y1, x2, y2) inclusive bounds.
        glyph: Glyph index (0-255) to fill.
        fg: (r, g, b) foreground color.
        bg: (r, g, b) background color.
        change_glyph: Whether to overwrite the glyph channel.
        change_fg: Whether to overwrite the foreground channel.
        change_bg: Whether to overwrite the background channel.

    Returns:
        int: Number of cells modified.
    """
    x1, y1, x2, y2 = selection
    count = 0
    for y in range(y1, y2 + 1):
        for x in range(x1, x2 + 1):
            if y >= layer.height or x >= layer.width:
                continue
            old_glyph, old_fg, old_bg = layer.data[y][x]
            new_glyph = glyph if change_glyph else old_glyph
            new_fg = fg if change_fg else old_fg
            new_bg = bg if change_bg else old_bg
            layer.data[y][x] = (new_glyph, new_fg, new_bg)
            count += 1
    return count


def find_replace(layer, selection, find_glyph, find_fg, find_bg,
                 repl_glyph, repl_fg, repl_bg,
                 match_glyph=True, match_fg=True, match_bg=True):
    """Find and replace cells matching criteria.  Same as FindReplaceDialog._do_replace.

    Args:
        layer: XPLayer to modify (mutated in place).
        selection: (x1, y1, x2, y2) inclusive bounds.
        find_glyph: Glyph to search for.
        find_fg: (r, g, b) foreground to search for.
        find_bg: (r, g, b) background to search for.
        repl_glyph: Replacement glyph.
        repl_fg: Replacement foreground.
        repl_bg: Replacement background.
        match_glyph: Whether to match on glyph channel.
        match_fg: Whether to match on foreground channel.
        match_bg: Whether to match on background channel.

    Returns:
        int: Number of cells replaced.
    """
    x1, y1, x2, y2 = selection
    count = 0
    for y in range(y1, y2 + 1):
        for x in range(x1, x2 + 1):
            if y >= layer.height or x >= layer.width:
                continue
            glyph, fg, bg = layer.data[y][x]
            glyph_ok = (not match_glyph) or (glyph == find_glyph)
            fg_ok = (not match_fg) or (fg == find_fg)
            bg_ok = (not match_bg) or (bg == find_bg)
            if glyph_ok and fg_ok and bg_ok:
                new_g = repl_glyph if match_glyph else glyph
                new_f = repl_fg if match_fg else fg
                new_b = repl_bg if match_bg else bg
                layer.data[y][x] = (new_g, new_f, new_b)
                count += 1
    return count


def resolve_xp_path(result):
    """Extract .xp file path from a pipeline result.

    Same logic as PNGImportDialog._on_complete path resolution.

    Args:
        result: Path, string, or object with xp_path/output_path attribute.

    Returns:
        str or None: Resolved path string, or None if not resolvable.
    """
    if isinstance(result, Path) or (isinstance(result, str) and str(result).endswith(".xp")):
        return str(result)
    if hasattr(result, 'xp_path'):
        return str(result.xp_path)
    if hasattr(result, 'output_path'):
        return str(result.output_path)
    return None


def raw_pixel_import(src_image, name, resolution, output_dir):
    """Map PNG pixels to XP cells using half-block encoding.

    COMPATIBILITY MODE: This function does NOT use the unified grid
    resolver (infer_sheet_spec / resolve_slicing). It applies a simpler
    pixel-to-cell mapping without angle/frame/projs decomposition.

    For proper grid-aware imports, use ImportRequest + AssetService.import_png_to_xp().

    Pure function (no tkinter dependency) for testability.

    Args:
        src_image: PIL Image to convert.
        name: Base name for the output file.
        resolution: Multiplier (1-3) determining cell pixel size.
        output_dir: Directory to write the .xp file into.

    Returns:
        str: Path to the written .xp file.
    """
    src = src_image.convert("RGB")
    cell_size = 12 * resolution

    chars_w = max(1, src.width // cell_size)
    chars_h = max(1, src.height // (cell_size // 2))
    if chars_h % 2 != 0:
        chars_h += 1

    scaled = src.resize((chars_w, chars_h), Image.NEAREST)
    xp_h = chars_h // 2
    xp_w = chars_w

    xp = XPFile()

    # Layer 0: colorkey (required by C++ loader)
    colorkey_layer = XPLayer(xp_w, xp_h)
    xp.layers.append(colorkey_layer)

    # Layer 1: height/metadata (angles=1, frames=1)
    meta_layer = XPLayer(xp_w, xp_h)
    meta_layer.data[0][0] = (ord('1'), (255, 255, 255), (0, 0, 0))
    if xp_w > 1:
        meta_layer.data[0][1] = (ord('1'), (255, 255, 255), (0, 0, 0))
    xp.layers.append(meta_layer)

    # Layer 2: visual data (half-block encoded)
    art_layer = XPLayer(xp_w, xp_h)
    for cy in range(xp_h):
        for cx in range(xp_w):
            top_y = cy * 2
            bot_y = cy * 2 + 1
            top_rgb = scaled.getpixel((cx, min(top_y, scaled.height - 1)))
            bot_rgb = scaled.getpixel((cx, min(bot_y, scaled.height - 1)))
            art_layer.data[cy][cx] = (220, bot_rgb, top_rgb)
    xp.layers.append(art_layer)

    xp_path = os.path.join(output_dir, f"{name}.xp")
    xp.save(xp_path)
    return xp_path


class PNGImportDialog(Toplevel):
    """Dialog for importing a PNG file into the editor with resolution selection.

    Presents a preview thumbnail, resolution radio buttons (1x/2x/3x corresponding
    to 12/24/36 px cell sizes), and a name field. On import, runs the asset pipeline
    in a background thread and loads the resulting .xp file into the parent editor.

    If the pipeline import fails, surfaces the error directly — no silent
    fallback to alternate code paths.
    """

    def __init__(self, parent_editor, png_path):
        """Initialize the PNG import dialog.

        Args:
            parent_editor: The SpriteEditor instance that opened this dialog.
            png_path: Absolute path to the PNG file to import.
        """
        super().__init__(parent_editor.root)
        self.parent_editor = parent_editor
        self.png_path = png_path
        self.title("Import PNG")
        self.geometry("500x550")
        self.resizable(False, False)

        # Header
        tk.Label(self, text="Import PNG as XP Sprite", font=("Arial", 13, "bold")).pack(pady=(10, 5))

        # Preview section
        preview_frame = tk.Frame(self)
        preview_frame.pack(fill=tk.X, padx=20, pady=5)

        self._src_image = None
        try:
            src = Image.open(png_path)
            self._src_image = src
            w, h = src.size
            tk.Label(preview_frame, text=f"Source: {os.path.basename(png_path)}").pack(anchor=tk.W)
            tk.Label(preview_frame, text=f"Dimensions: {w} x {h} px").pack(anchor=tk.W)

            # Thumbnail preview
            thumb = src.copy()
            thumb.thumbnail((200, 150), Image.NEAREST)
            self._thumb_tk = ImageTk.PhotoImage(thumb)
            tk.Label(self, image=self._thumb_tk).pack(pady=5)
        except Exception as e:
            tk.Label(preview_frame, text=f"Error loading: {e}", fg="red").pack()

        # Resolution selector
        tk.Label(self, text="Resolution (cell size):", font=("Arial", 11, "bold")).pack(pady=(10, 2))
        self.resolution_var = tk.IntVar(value=2)
        res_frame = tk.Frame(self)
        res_frame.pack(fill=tk.X, padx=20)
        tk.Radiobutton(res_frame, text="1x  (12px cells) - fast, low detail", variable=self.resolution_var, value=1).pack(anchor=tk.W)
        tk.Radiobutton(res_frame, text="2x  (24px cells) - recommended", variable=self.resolution_var, value=2).pack(anchor=tk.W)
        tk.Radiobutton(res_frame, text="3x  (36px cells)", variable=self.resolution_var, value=3).pack(anchor=tk.W)
        tk.Radiobutton(res_frame, text="4x  (48px cells) - high detail", variable=self.resolution_var, value=4).pack(anchor=tk.W)
        tk.Radiobutton(res_frame, text="6x  (72px cells) - very high detail", variable=self.resolution_var, value=6).pack(anchor=tk.W)
        tk.Radiobutton(res_frame, text="8x  (96px cells) - maximum", variable=self.resolution_var, value=8).pack(anchor=tk.W)

        # Name field
        name_frame = tk.Frame(self)
        name_frame.pack(fill=tk.X, padx=20, pady=10)
        tk.Label(name_frame, text="Name:").pack(side=tk.LEFT)
        self.name_var = tk.StringVar(value=os.path.splitext(os.path.basename(png_path))[0])
        tk.Entry(name_frame, textvariable=self.name_var, width=30).pack(side=tk.LEFT, padx=5)

        # Action buttons
        btn_frame = tk.Frame(self)
        btn_frame.pack(pady=10)
        self.import_btn = tk.Button(btn_frame, text="Import", command=self._do_import, width=12, bg="#90EE90")
        self.import_btn.pack(side=tk.LEFT, padx=10)
        tk.Button(btn_frame, text="Cancel", command=self.destroy, width=12).pack(side=tk.LEFT, padx=10)

        # Status label
        self.status_label = tk.Label(self, text="", wraplength=400)
        self.status_label.pack(pady=5)

    def _do_import(self):
        """Start the pipeline import in a background thread.

        Uses the same grid resolution path as CLI:
        ImportRequest -> build_job_from_import_request() -> resolve_config()
        -> AssetService.run() -> AssetPipeline.run()

        No duplicate geometry math lives here -- all grid inference
        and validation is delegated to service/slicing.py.
        """
        self.import_btn.config(state=tk.DISABLED, text="Importing...")
        self.status_label.config(text="Running pipeline...", fg="black")

        resolution = self.resolution_var.get()
        name = self.name_var.get() or "imported"

        def run_pipeline():
            try:
                # Route through unified import API (Phase 2 architecture)
                # Same path as CLI: ImportRequest -> resolve_config() -> pipeline
                from scripts.pipeline.service.adapters import ImportRequest
                from scripts.pipeline.service.asset_service import AssetService
                from scripts.pipeline.service.slicing import BackgroundSpec
                from PIL import Image as PILImage

                cell_size = 12 * resolution

                # Detect if source has alpha for editor import
                src_img = PILImage.open(str(self.png_path))
                editor_bg = None
                has_alpha = (
                    src_img.mode in ("RGBA", "LA", "PA")
                    or (src_img.mode == "P" and "transparency" in src_img.info)
                )
                if has_alpha:
                    editor_bg = BackgroundSpec(mode="alpha", alpha_threshold=128)
                src_img.close()

                # Build ImportRequest with explicit parameters
                request = ImportRequest(
                    name=name,
                    source_path=str(self.png_path),
                    source_type="file",
                    angles=1,
                    frames=[1],
                    render_resolution=cell_size,
                    import_mode="as_is",
                    source_projs=1,
                    reflection_policy="generate",
                    downscale_policy="off",
                    background=editor_bg,
                )

                svc = AssetService()

                # Pre-flight: resolve grid to surface errors early
                grid_info = svc.resolve_grid(
                    image_path=str(self.png_path),
                    angles=1,
                    frames=[1],
                )
                if not grid_info.get("divisible", True):
                    error_msg = grid_info.get("error", "Non-divisible geometry")
                    self.parent_editor.root.after_idle(
                        lambda: self._on_error(f"Grid check failed: {error_msg}")
                    )
                    return

                output = svc.import_png_to_xp(request)
                xp_path = output.xp_path

                self.parent_editor.root.after_idle(lambda: self._on_complete(xp_path))
            except Exception as e:
                self.parent_editor.root.after_idle(lambda: self._on_error(
                    f"Import failed: {e}"))

        t = threading.Thread(target=run_pipeline, daemon=True)
        t.start()

    def _on_complete(self, result):
        """Handle successful pipeline completion by loading the output XP file.

        Args:
            result: Path to the output .xp file, or an object with xp_path/output_path.
        """
        xp_path = None
        if isinstance(result, Path) or (isinstance(result, str) and result.endswith(".xp")):
            xp_path = str(result)
        elif hasattr(result, 'xp_path'):
            xp_path = str(result.xp_path)
        elif hasattr(result, 'output_path'):
            xp_path = str(result.output_path)

        if xp_path and os.path.exists(xp_path):
            self._load_result(xp_path)
        else:
            self.status_label.config(
                text="Pipeline completed but no XP file found.", fg="orange")
            self.import_btn.config(state=tk.NORMAL, text="Import")

    def _load_result(self, xp_path):
        """Load a generated XP file into the parent editor and close the dialog.

        Args:
            xp_path: Path to the .xp file to load.
        """
        self.status_label.config(text=f"Loading {xp_path}...", fg="black")
        try:
            self.parent_editor.xp = XPFile()
            self.parent_editor.xp.load(str(xp_path))
            self.parent_editor.filepath = str(xp_path)
            self.parent_editor.meta = self.parent_editor.xp.get_metadata()
            self.parent_editor.root.title(f"Asciicker XP Tool - {xp_path}")
            self.parent_editor.undo_stack = []

            # Rebuild layer UI
            for child in self.parent_editor.layer_radio_frame.winfo_children():
                child.destroy()
            for child in self.parent_editor.layer_check_frame.winfo_children():
                child.destroy()

            self.parent_editor.show_layers = {}
            for i in range(len(self.parent_editor.xp.layers)):
                if i == 0:
                    continue
                tk.Radiobutton(
                    self.parent_editor.layer_radio_frame, text=f"Layer {i}",
                    variable=self.parent_editor.active_layer_var, value=i,
                    bg="#d0d0d0", command=self.parent_editor.on_layer_change
                ).pack(anchor=tk.W)
                var = tk.BooleanVar(value=True)
                self.parent_editor.show_layers[i] = var
                tk.Checkbutton(
                    self.parent_editor.layer_check_frame, text=f"Layer {i}",
                    variable=var, command=self.parent_editor.render_sheet, bg="#d0d0d0"
                ).pack(anchor=tk.W)

            self.parent_editor.active_layer_var.set(1)
            self.parent_editor.render_sheet()
            self.destroy()
        except Exception as e:
            self.status_label.config(text=f"Load error: {e}", fg="red")
            self.import_btn.config(state=tk.NORMAL, text="Import")

    def _on_error(self, msg):
        """Display an error message and re-enable the import button.

        Args:
            msg: Error message string to display.
        """
        self.status_label.config(text=f"Error: {msg}", fg="red")
        self.import_btn.config(state=tk.NORMAL, text="Import")


class SpriteEditor:
    """
    Main Application Controller.

    Manages the Tkinter UI, loads/saves .xp files, and orchestrates the rendering pipeline.

    Attributes:
        xp (XPFile): The loaded file data model.
        meta (dict): Metadata extracted from Layer 0 (angles, animation frames).
        active_editors (list): List of open CellEditor windows.
    """
    def __init__(self, root):
        """Initialize the editor: build UI, load font, start IPC server.

        Args:
            root: The Tk root window instance.
        """
        self.root = root
        self.root.title("Asciicker XP Tool")
        self.root.geometry("1400x900")

        self.xp = None
        self.meta = None
        self.filepath = None

        # WHY deep-copy undo: XPFile contains nested mutable lists (layer data).
        # Shallow copies would share cell references, making undo ineffective.
        # The stack is capped at 30 entries to bound memory usage.
        self.undo_stack = []
        self.redo_stack = []

        self.active_editors = []

        # Drag-and-select state for the main canvas
        self.canvas_selection = None  # (char_x1, char_y1, char_x2, char_y2) or None
        self.drag_start = None        # (char_x, char_y) or None
        self.selection_mode = False

        # [DATA-CONTRACT:CP437] Font is a 12x12 pixel CP437 sprite sheet.
        # Robust font discovery searching script-relative and CWD paths.
        script_dir = os.path.dirname(os.path.abspath(__file__))
        search_paths = [
            os.path.join(script_dir, "../assets/fonts/cp437_12x12.png"),        # scripts/assets/fonts/
            os.path.join(script_dir, "../../assets/fonts/cp437_12x12.png"),     # root assets/fonts/
            os.path.join(os.getcwd(), "assets/fonts/cp437_12x12.png"),         # CWD assets/fonts/
            os.path.join(os.getcwd(), "cp437_12x12.png"),               # CWD root
        ]
        
        font_path = None
        for path in search_paths:
            if os.path.exists(path) and os.path.getsize(path) > 0:
                font_path = path
                break
        
        if not font_path:
            # Fallback to default name if not found in any known location
            font_path = "cp437_12x12.png"

        self.font = BitmapFont(font_path, 12, 12)

        # WHY layer 0 is skipped: Layer 0 is the metadata layer (angle/frame
        # counts encoded in cell glyphs). It is not visual art and must not be
        # directly edited, so it is excluded from the layer radio/checkbox UI.
        self.show_layers = {}
        self.active_layer_idx = 1
        
        self.setup_menu()
        self.setup_layout()

        # Start Server
        self.server = XPServer(self)
        self.server.start()

        # Keyboard shortcuts
        self.root.bind("<Control-z>", self.undo)
        self.root.bind("<Command-z>", self.undo)
        self.root.bind("<Control-Shift-z>", self.redo)
        self.root.bind("<Command-Shift-z>", self.redo)

        # Rotate selection shortcuts
        self.root.bind("<Control-r>", lambda e: self.rotate_selection(True))
        self.root.bind("<Command-r>", lambda e: self.rotate_selection(True))
        self.root.bind("<Control-Shift-R>", lambda e: self.rotate_selection(False))
        self.root.bind("<Command-Shift-R>", lambda e: self.rotate_selection(False))

        # Escape clears canvas selection
        self.root.bind("<Escape>", lambda e: self.clear_selection())

    def setup_menu(self):
        """Build the application menu bar (File, Edit, Help)."""
        menubar = Menu(self.root)
        
        filemenu = Menu(menubar, tearoff=0)
        filemenu.add_command(label="Open", command=self.load_file)
        filemenu.add_command(label="Save As", command=self.save_file)
        filemenu.add_command(label="Import PNG...", command=self.import_png)
        filemenu.add_command(label="Browse XP Files...", command=self.browse_xp)
        filemenu.add_separator()
        filemenu.add_command(label="Exit", command=self.root.quit)
        menubar.add_cascade(label="File", menu=filemenu)
        
        editmenu = Menu(menubar, tearoff=0)
        editmenu.add_command(label="Undo", command=self.undo)
        editmenu.add_command(label="Redo", command=self.redo)
        editmenu.add_separator()
        editmenu.add_command(label="Rotate Selection CW", command=lambda: self.rotate_selection(True))
        editmenu.add_command(label="Rotate Selection CCW", command=lambda: self.rotate_selection(False))
        editmenu.add_separator()
        editmenu.add_command(label="Fill Selection...", command=self.show_fill_selection)
        editmenu.add_command(label="Find & Replace...", command=self.show_find_replace)
        menubar.add_cascade(label="Edit", menu=editmenu)
        
        helpmenu = Menu(menubar, tearoff=0)
        helpmenu.add_command(label="Info / Instructions", command=self.show_info)
        menubar.add_cascade(label="Help", menu=helpmenu)
        
        self.root.config(menu=menubar)
        
    def setup_layout(self):
        """Build the main window layout: toolbar, sidebar, scrollable canvas."""
        # Toolbar
        self.toolbar = tk.Frame(self.root, bd=1, relief=tk.RAISED)
        self.toolbar.pack(side=tk.TOP, fill=tk.X)
        
        tk.Button(self.toolbar, text="Info", command=self.show_info).pack(side=tk.RIGHT, padx=5)
        
        # Explicit Undo/Redo Buttons in Toolbar
        tk.Button(self.toolbar, text="Redo", command=self.redo).pack(side=tk.LEFT, padx=5)
        tk.Button(self.toolbar, text="Undo", command=self.undo).pack(side=tk.LEFT, padx=5)

        # Selection toggle and rotation buttons
        self.select_btn = tk.Button(self.toolbar, text="Select", command=self.toggle_selection_mode, relief=tk.RAISED)
        self.select_btn.pack(side=tk.LEFT, padx=5)
        tk.Button(self.toolbar, text="CW", command=lambda: self.rotate_selection(True)).pack(side=tk.LEFT, padx=2)
        tk.Button(self.toolbar, text="CCW", command=lambda: self.rotate_selection(False)).pack(side=tk.LEFT, padx=2)

        tk.Label(self.toolbar, text="Zoom:").pack(side=tk.LEFT, padx=5)
        self.zoom_var = tk.IntVar(value=2)
        tk.Scale(self.toolbar, variable=self.zoom_var, from_=1, to=8, orient=tk.HORIZONTAL, command=self.update_view).pack(side=tk.LEFT)
        
        # Main Area
        self.main_pane = tk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        self.main_pane.pack(fill=tk.BOTH, expand=True)
        
        # Sidebar
        self.sidebar = tk.Frame(self.main_pane, width=250, bg="#d0d0d0")
        self.main_pane.add(self.sidebar)
        
        # Layers
        tk.Label(self.sidebar, text="Active Layer", bg="#d0d0d0", font=("Arial", 12, "bold")).pack(pady=5)
        self.active_layer_var = tk.IntVar(value=1)
        self.layer_radio_frame = tk.Frame(self.sidebar, bg="#d0d0d0")
        self.layer_radio_frame.pack(fill=tk.X, padx=5)

        tk.Label(self.sidebar, text="Visible Layers", bg="#d0d0d0", font=("Arial", 12, "bold")).pack(pady=(15,5))
        self.layer_check_frame = tk.Frame(self.sidebar, bg="#d0d0d0")
        self.layer_check_frame.pack(fill=tk.X, padx=5)
        
        # Preview
        tk.Label(self.sidebar, text="Preview", bg="#d0d0d0", font=("Arial", 12, "bold")).pack(pady=(20, 5))
        self.preview_canvas = Canvas(self.sidebar, width=200, height=200, bg="black") 
        self.preview_canvas.pack(padx=5)
        
        self.anim_btns = tk.Frame(self.sidebar, bg="#d0d0d0")
        self.anim_btns.pack(fill=tk.X, pady=5)
        tk.Button(self.anim_btns, text="Idle", command=lambda: self.start_anim(0)).pack(side=tk.LEFT, padx=2)
        tk.Button(self.anim_btns, text="Walk", command=lambda: self.start_anim(1)).pack(side=tk.LEFT, padx=2)
        tk.Button(self.anim_btns, text="Stop", command=self.stop_anim).pack(side=tk.LEFT, padx=2)
        
        # Canvas Scroll
        self.canvas_frame = tk.Frame(self.main_pane)
        self.main_pane.add(self.canvas_frame)
        
        self.canvas = Canvas(self.canvas_frame, bg="#202020")
        self.hbar = tk.Scrollbar(self.canvas_frame, orient=tk.HORIZONTAL)
        self.vbar = tk.Scrollbar(self.canvas_frame, orient=tk.VERTICAL)
        
        self.hbar.config(command=self.canvas.xview)
        self.vbar.config(command=self.canvas.yview)
        self.canvas.config(xscrollcommand=self.hbar.set, yscrollcommand=self.vbar.set)
        
        self.vbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.hbar.pack(side=tk.BOTTOM, fill=tk.X)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        self.canvas.bind("<Button-1>", self.on_canvas_click)
        self.canvas.bind("<Double-Button-1>", self.on_canvas_dblclick)
        self.canvas.bind("<B1-Motion>", self.on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_canvas_release)

        self.anim_running = False
        self.anim_angle = 0
        self.anim_frame = 0
        self.anim_idx = 0
        
    def show_info(self):
        """Display usage instructions in a popup dialog."""
        info = """
XP Animation Tool V7

1. NAVIGATION:
   - In Editor, use the arrow buttons to navigate:
     < > : Frames (Horizontal)
     ^ v : Angles (Vertical)

2. EDITING:
   - Click 'Undo' / 'Redo' buttons if shortcuts don't work.
   - Shortcuts: Ctrl+Z (Undo), Ctrl+Shift+Z (Redo)
   - Copy/Paste: Select area first.

3. LAYERS:
   - 'Active Layer' (Radio): The layer you are editing.
   - 'Visible Layers' (Check): Determine what is shown.
        """
        messagebox.showinfo("Instructions", info)

    def commit_action(self):
        """Snapshot current XPFile state onto the undo stack before a mutation.

        WHY deep-copy: XPFile contains nested mutable lists (layers -> rows -> cells).
        A shallow copy would alias the inner data, so mutations after the snapshot
        would corrupt the undo history. The stack is capped at 30 entries to bound
        memory (each snapshot is a full copy of every layer).
        """
        import copy
        if self.xp:
             snapshot = copy.deepcopy(self.xp)
             self.undo_stack.append(snapshot)
             if len(self.undo_stack) > 30:
                 self.undo_stack.pop(0)
             self.redo_stack.clear()
             print(f"Action committed. Stack size: {len(self.undo_stack)}")

    def undo(self, event=None):
        """Pop the last snapshot from the undo stack and restore it.

        Args:
            event: Optional Tkinter event (from keyboard shortcut binding).
        """
        if not self.undo_stack:
            print("Undo stack empty")
            return
            
        current = copy.deepcopy(self.xp)
        self.redo_stack.append(current)
        
        state = self.undo_stack.pop()
        self.xp = state
        print("Undo performed")
        
        self.render_sheet()
        for ed in self.active_editors:
            if ed.winfo_exists():
                ed.refresh_from_parent()

    def redo(self, event=None):
        """Pop the last snapshot from the redo stack and restore it.

        Args:
            event: Optional Tkinter event (from keyboard shortcut binding).
        """
        if not self.redo_stack:
            return
            
        current = copy.deepcopy(self.xp)
        self.undo_stack.append(current)
        
        state = self.redo_stack.pop()
        self.xp = state
        print("Redo performed")
        
        self.render_sheet()
        for ed in self.active_editors:
            if ed.winfo_exists():
                ed.refresh_from_parent()

    def load_file(self):
        """Open a .xp file via dialog, parse it, and rebuild the layer UI.

        [DATA-CONTRACT:XP] Delegates to xp_core.XPFile.load() for binary parsing.
        Metadata (angles, animation frame counts) is extracted from Layer 0.
        """
        path = filedialog.askopenfilename(filetypes=[("XP Files", "*.xp")])
        if not path:
            return
            
        try:
            self.xp = XPFile()
            self.xp.load(path)
            self.filepath = path
            self.meta = self.xp.get_metadata()
            self.root.title(f"Asciicker XP Tool - {path}")
            self.undo_stack = []
            
            for ed in self.active_editors:
                 if ed.winfo_exists(): ed.destroy()
            self.active_editors = []
            
            for child in self.layer_radio_frame.winfo_children(): child.destroy()
            for child in self.layer_check_frame.winfo_children(): child.destroy()
                
            self.show_layers = {}
            for i in range(len(self.xp.layers)):
                # WHY skip layer 0: it is the metadata layer, not visual art.
                if i == 0: continue

                tk.Radiobutton(self.layer_radio_frame, text=f"Layer {i}", variable=self.active_layer_var, value=i, bg="#d0d0d0", command=self.on_layer_change).pack(anchor=tk.W)

                var = tk.BooleanVar(value=True)
                self.show_layers[i] = var
                tk.Checkbutton(self.layer_check_frame, text=f"Layer {i}", variable=var, command=self.render_sheet, bg="#d0d0d0").pack(anchor=tk.W)

            self.active_layer_var.set(1)
            self.render_sheet()

        except Exception as e:
            messagebox.showerror("Error", str(e))

    def on_layer_change(self):
        """Update active_layer_idx when the user selects a different layer radio button."""
        self.active_layer_idx = self.active_layer_var.get()

    def import_png(self):
        """Open a file dialog to select a PNG and launch the import dialog."""
        path = filedialog.askopenfilename(
            filetypes=[("PNG Files", "*.png"), ("All Files", "*.*")])
        if not path:
            return
        PNGImportDialog(self, path)

    def browse_xp(self):
        """Open the XP file browser window."""
        from .xp_browser import XPBrowser
        XPBrowser(self)

    def reload_current(self):
        """Re-read the current .xp file from disk, preserving view/editor state.

        Called by XPServer on 'reload' IPC command or could be triggered manually.
        Saves zoom level, scroll position, active layer, and open CellEditor
        states, then re-loads the file and restores them.
        """
        if self.filepath and os.path.exists(self.filepath):
            print(f"Reloading {self.filepath}...")
            try:
                # Save view state
                zoom = self.zoom_var.get()
                active_layer = self.active_layer_var.get()
                x_view = self.canvas.xview()
                y_view = self.canvas.yview()
                
                # Check active editors
                editor_states = []
                for ed in self.active_editors:
                    if ed.winfo_exists():
                        editor_states.append({
                            'angle': ed.angle,
                            'frame': ed.frame,
                            'w': ed.cw,
                            'h': ed.ch,
                            'tool': ed.tool
                        })
                        ed.destroy()
                self.active_editors = []

                self.xp = XPFile()
                self.xp.load(self.filepath)
                self.meta = self.xp.get_metadata()
                self.undo_stack = [] # Clear undo stack on external reload as it invalidates history
                
                # Restore layers UI
                for child in self.layer_radio_frame.winfo_children(): child.destroy()
                for child in self.layer_check_frame.winfo_children(): child.destroy()
                
                self.show_layers = {}
                for i in range(len(self.xp.layers)):
                    if i == 0: continue 
                    
                    tk.Radiobutton(self.layer_radio_frame, text=f"Layer {i}", variable=self.active_layer_var, value=i, bg="#d0d0d0", command=self.on_layer_change).pack(anchor=tk.W)
                    
                    var = tk.BooleanVar(value=True)
                    self.show_layers[i] = var
                    tk.Checkbutton(self.layer_check_frame, text=f"Layer {i}", variable=var, command=self.render_sheet, bg="#d0d0d0").pack(anchor=tk.W)
                
                # Restore state
                if active_layer < len(self.xp.layers):
                    self.active_layer_var.set(active_layer)
                else:
                    # Default to layer 1 if previous active layer doesn't exist
                    if len(self.xp.layers) > 1:
                        self.active_layer_var.set(1)
                
                self.zoom_var.set(zoom)
                self.render_sheet()
                
                # Restore scroll (after render/update_view)
                self.canvas.xview_moveto(x_view[0])
                self.canvas.yview_moveto(y_view[0])

                # Restore editors (if they are still valid)
                if self.meta:
                    anims = self.meta['anims']
                    if anims:
                        total_frames = sum(anims)
                        angles = self.meta['angles']
                        if total_frames > 0 and angles > 0:
                            l1 = self.xp.layers[1]
                            cell_w = l1.width // total_frames
                            cell_h = l1.height // angles
                            
                            for es in editor_states:
                                # Check bounds
                                if es['angle'] < angles and es['frame'] < total_frames:
                                    self.open_cell_editor(es['angle'], es['frame'], cell_w, cell_h)
                                    if self.active_editors:
                                        self.active_editors[-1].set_tool(es['tool'])

                print("Reload complete.")
                # messagebox.showinfo("Reload", "File reloaded from disk.") # Optional: disable to avoid popup spam
            except Exception as e:
                print(f"Reload failed: {e}")
                traceback.print_exc()

    def render_sheet(self):
        """Render all visible layers into a single PIL Image and update the canvas.

        Iterates over every cell of every visible layer (skipping Layer 0),
        composites backgrounds and glyph foregrounds into ``self.raw_image``,
        then delegates to ``update_view()`` for zoom and grid overlay.

        [DATA-CONTRACT:XP] Cell data is ``(glyph, fg_rgb, bg_rgb)`` from xp_core.
        [DATA-CONTRACT:PALETTE] bg=(255,0,255) is the magic-pink transparency marker.

        Bug fix: Handles layers with different dimensions gracefully instead of
        assuming all layers match layer 1's dimensions (fixes player_idle_walk.xp crash).
        """
        if not self.xp:
            return

        # Use layer 1 as reference for canvas size, but don't assume all layers match
        if len(self.xp.layers) < 2:
            return  # Need at least 2 layers (metadata + one visual layer)

        l1 = self.xp.layers[1]
        w, h = l1.width, l1.height
        cw, ch = self.font.char_w, self.font.char_h

        img = Image.new("RGBA", (w * cw, h * ch), (0, 0, 0, 255))
        draw = ImageDraw.Draw(img)

        for i in range(2, len(self.xp.layers)):
            if not self.show_layers[i].get():
                continue
            layer = self.xp.layers[i]

            # Bug fix: Use layer's own dimensions, not l1's dimensions
            # This prevents IndexError when layer has different dimensions (e.g. 0x0)
            layer_w = min(layer.width, w)  # Don't exceed canvas bounds
            layer_h = min(layer.height, h)

            for y in range(layer_h):
                for x in range(layer_w):
                    glyph, fg, bg = layer.data[y][x]
                    # WHY magic pink check: bg=(255,0,255) is the REXPaint/Asciicker
                    # convention for transparent cells. We skip drawing the background
                    # rectangle so the underlying layer or canvas background shows through.
                    is_bg_trans = (bg[0] == 255 and bg[1] == 0 and bg[2] == 255)

                    if not is_bg_trans:
                        draw.rectangle([x * cw, y * ch, (x+1)*cw, (y+1)*ch], fill=bg)

                    # WHY skip glyph 0 and 32: 0 is the null glyph (empty cell) and
                    # 32 is ASCII space. Rendering them would overwrite transparency
                    # and waste cycles on invisible characters.
                    if glyph != 0 and glyph != 32:
                        char_img = self.font.render(glyph, fg)
                        img.alpha_composite(char_img, (x * cw, y * ch))

        self.raw_image = img
        self.update_view()
        
    def update_view(self, _=None):
        """Scale ``raw_image`` by the current zoom factor, draw grid lines.

        WHY NEAREST resampling: Pixel art must be scaled without interpolation
        to preserve sharp cell boundaries. Bilinear/bicubic would blur glyphs.

        Grid lines (green dashed) show the angle/frame cell boundaries derived
        from Layer 0 metadata. They help the artist see where each animation
        cell starts and ends on the sprite sheet.
        """
        if not hasattr(self, 'raw_image'):
            return

        scale = self.zoom_var.get()
        w, h = self.raw_image.size
        new_size = (w * scale, h * scale)
        
        resized = self.raw_image.resize(new_size, Image.NEAREST)
        self.tk_img = ImageTk.PhotoImage(resized)
        
        self.canvas.config(scrollregion=(0, 0, new_size[0], new_size[1]))
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self.tk_img, anchor=tk.NW)
        
        if self.meta:
            angles = self.meta['angles']
            anims = self.meta['anims']
            projs = self.meta.get('projs', 1)
            if not anims: return
            
            total_frames = sum(anims)
            total_columns = total_frames * projs
            if total_columns == 0: return
            
            l1 = self.xp.layers[1]
            cell_w_chars = l1.width // total_columns
            cell_h_chars = l1.height // angles
            
            cell_w_pix = cell_w_chars * self.font.char_w * scale
            cell_h_pix = cell_h_chars * self.font.char_h * scale
            
            for a in range(angles + 1):
                y = a * cell_h_pix
                self.canvas.create_line(0, y, new_size[0], y, fill="#00FF00", dash=(4,4))
                
            for f in range(total_columns + 1):
                x = f * cell_w_pix
                self.canvas.create_line(x, 0, x, new_size[1], fill="#00FF00", dash=(4,4))

        # Draw selection overlay (white dashed rectangle)
        if self.canvas_selection is not None:
            sx1, sy1, sx2, sy2 = self.canvas_selection
            char_w_pix = self.font.char_w * scale
            char_h_pix = self.font.char_h * scale
            px1 = sx1 * char_w_pix
            py1 = sy1 * char_h_pix
            px2 = (sx2 + 1) * char_w_pix
            py2 = (sy2 + 1) * char_h_pix
            self.canvas.create_rectangle(
                px1, py1, px2, py2,
                outline="white", dash=(6, 3), width=2)

    def _canvas_event_to_char(self, event):
        """Convert a canvas mouse event to character coordinates.

        Returns:
            (char_x, char_y) tuple, or None if out of bounds.
        """
        if not self.xp or not self.meta:
            return None
        scale = self.zoom_var.get()
        cw = self.font.char_w * scale
        ch = self.font.char_h * scale

        cx = self.canvas.canvasx(event.x)
        cy = self.canvas.canvasy(event.y)

        char_x = int(cx / cw)
        char_y = int(cy / ch)

        l1 = self.xp.layers[1]
        if char_x < 0 or char_y < 0 or char_x >= l1.width or char_y >= l1.height:
            return None
        return (char_x, char_y)

    def on_canvas_click(self, event):
        """Handle single click on the main canvas.

        In selection mode, starts a drag-selection rectangle.
        In normal mode, does nothing (double-click opens CellEditor).
        """
        coords = self._canvas_event_to_char(event)
        if coords is None:
            return

        char_x, char_y = coords

        # Selection mode: start drag
        if self.selection_mode:
            self.drag_start = (char_x, char_y)
            self.canvas_selection = (char_x, char_y, char_x, char_y)
            self.update_view()

    def on_canvas_dblclick(self, event):
        """Double-click on the main canvas opens the CellEditor for that cell.

        Converts the click position to character coordinates, then calculates
        which angle/frame cell was clicked based on metadata grid dimensions.
        """
        if self.selection_mode:
            return
        coords = self._canvas_event_to_char(event)
        if coords is None:
            return

        char_x, char_y = coords

        l1 = self.xp.layers[1]
        angles = self.meta['angles']
        projs = self.meta.get('projs', 1)
        total_frames = sum(self.meta['anims'])
        total_columns = total_frames * projs

        if total_columns == 0 or angles == 0:
            return

        cell_w = l1.width // total_columns
        cell_h = l1.height // angles

        if cell_w == 0 or cell_h == 0:
            return

        cell_col = char_x // cell_w
        cell_row = char_y // cell_h

        self.open_cell_editor(cell_row, cell_col, cell_w, cell_h)

    def on_canvas_drag(self, event):
        """Extend canvas selection rectangle during mouse drag."""
        if not self.selection_mode or self.drag_start is None:
            return
        coords = self._canvas_event_to_char(event)
        if coords is None:
            return
        cur_x, cur_y = coords
        sx, sy = self.drag_start
        self.canvas_selection = (min(sx, cur_x), min(sy, cur_y),
                                 max(sx, cur_x), max(sy, cur_y))
        self.update_view()

    def on_canvas_release(self, event):
        """Finalize canvas drag selection on mouse button release."""
        if self.selection_mode and self.drag_start is not None:
            self.drag_start = None

    def clear_selection(self):
        """Clear any active canvas selection and reset drag state."""
        self.canvas_selection = None
        self.drag_start = None
        self.update_view()

    def toggle_selection_mode(self):
        """Toggle the main canvas selection mode on or off."""
        self.selection_mode = not self.selection_mode
        self.select_btn.config(relief=tk.SUNKEN if self.selection_mode else tk.RAISED)
        if not self.selection_mode:
            self.canvas_selection = None
            self.update_view()

    def rotate_selection(self, clockwise):
        """Rotate the cells within the canvas selection 90 degrees.

        Rotates cell positions and remaps box-drawing CP437 glyphs via
        CP437_ROTATE_CW / CP437_ROTATE_CCW. If the rotated rectangle has
        different dimensions than the original, writes as much as fits within
        the layer bounds.

        Args:
            clockwise: True for CW rotation, False for CCW.
        """
        if not self.canvas_selection or not self.xp:
            return
        self.commit_action()
        x1, y1, x2, y2 = self.canvas_selection
        layer = self.xp.layers[self.active_layer_idx]

        h = y2 - y1 + 1
        w = x2 - x1 + 1

        # Extract cells from the selection rectangle
        cells = []
        for y in range(h):
            row = []
            for x in range(w):
                src_y = y1 + y
                src_x = x1 + x
                if src_y < layer.height and src_x < layer.width:
                    row.append(layer.data[src_y][src_x])
                else:
                    row.append((0, (255, 255, 255), (255, 0, 255)))
            cells.append(row)

        remap = CP437_ROTATE_CW if clockwise else CP437_ROTATE_CCW

        if clockwise:
            # CW: new[x][h-1-y] = old[y][x]  => new dims: h'=w, w'=h
            rotated = []
            for new_y in range(w):
                row = []
                for new_x in range(h):
                    glyph, fg, bg = cells[h - 1 - new_x][new_y]
                    glyph = remap.get(glyph, glyph)
                    row.append((glyph, fg, bg))
                rotated.append(row)
        else:
            # CCW: new[w-1-x][y] = old[y][x]  => new dims: h'=w, w'=h
            rotated = []
            for new_y in range(w):
                row = []
                for new_x in range(h):
                    glyph, fg, bg = cells[new_x][w - 1 - new_y]
                    glyph = remap.get(glyph, glyph)
                    row.append((glyph, fg, bg))
                rotated.append(row)

        new_h = len(rotated)
        new_w = len(rotated[0]) if rotated else 0

        # Write back (clamp to layer bounds)
        for y in range(min(new_h, layer.height - y1)):
            for x in range(min(new_w, layer.width - x1)):
                layer.data[y1 + y][x1 + x] = rotated[y][x]

        # Update selection bounds to reflect new dimensions
        self.canvas_selection = (x1, y1, x1 + new_w - 1, y1 + new_h - 1)
        self.render_sheet()

    def show_fill_selection(self):
        """Open the Fill Selection dialog (requires canvas_selection)."""
        if not self.canvas_selection:
            messagebox.showinfo("No Selection", "Select a region on the canvas first (use Select tool).")
            return
        FillSelectionDialog(self)

    def show_find_replace(self):
        """Open the Find & Replace dialog (requires canvas_selection)."""
        if not self.canvas_selection:
            messagebox.showinfo("No Selection", "Select a region on the canvas first (use Select tool).")
            return
        FindReplaceDialog(self)

    def show_color_replace(self):
        """Open the Find & Replace dialog (backward-compatible alias)."""
        self.show_find_replace()

    def open_cell_editor(self, angle_idx, frame_idx, w, h):
        """Create a new CellEditor Toplevel window for the given angle/frame cell."""
        ed = CellEditor(self, angle_idx, frame_idx, w, h)
        self.active_editors.append(ed)
        self.active_editors = [e for e in self.active_editors if e.winfo_exists()]
        
    def save_file(self):
        """Save the current XPFile to disk via a Save-As dialog.

        [DATA-CONTRACT:XP] Delegates to xp_core.XPFile.save() for binary serialization.
        """
        path = filedialog.asksaveasfilename(defaultextension=".xp", filetypes=[("XP Files", "*.xp")])
        if path:
            self.xp.save(path)
            messagebox.showinfo("Saved", f"Saved to {path}")

    # ---- Animation preview ----

    def start_anim(self, anim_idx):
        """Begin animation preview for the given animation index (0=idle, 1=walk, etc.).

        Args:
            anim_idx: Zero-based index into ``meta['anims']`` list (0=idle, 1=walk, ...).
        """
        if not self.xp or not self.meta: return
        self.anim_running = True
        self.anim_idx = anim_idx
        self.anim_frame = 0
        self.anim_loop()
        
    def stop_anim(self):
        """Stop the animation preview loop."""
        self.anim_running = False

    def anim_loop(self):
        """Main animation loop scheduled via Tk after().

        [DEPENDENCY:PIL] Crops and scales frames from ``raw_image`` for preview.

        Calculates the current frame based on metadata (anims list) and updates
        the preview canvas. Logic:
           - Global frame index determines X offset on the sprite sheet.
           - Current 'angle' index determines Y offset on the sprite sheet.
           - Cycles through all frames of the current animation, then advances
             to the next angle and wraps around.
        """
        if not self.anim_running: return

        frame_payload = self._build_preview_frame_payload(
            anim_idx=self.anim_idx,
            frame_idx=self.anim_frame,
            angle_idx=self.anim_angle,
            proj=0,  # Preview panel shows projection half by default.
            scale=2,
            include_image=False,
            target_h=150,
        )
        length = max(1, int(frame_payload["anim_length"]))
        frame_img = self._render_preview_frame_image(
            frame_payload["pixel_rect"],
            scale=2,
            target_h=150,
        )
        self.preview_tk = ImageTk.PhotoImage(frame_img)
        self.preview_canvas.delete("all")
        self.preview_canvas.create_image(100, 100, image=self.preview_tk, anchor=tk.CENTER)

        self.anim_frame += 1
        if self.anim_frame >= length:
             self.anim_frame = 0
             angles = max(1, int(self.meta.get("angles", 1)))
             self.anim_angle = (self.anim_angle + 1) % angles

        # [ENGINE-ALIGN] Speed matched to 100ms (~10 FPS) for preview
        self.root.after(100, self.anim_loop)

    # ---- MCP preview helpers ----

    def _get_preview_geometry(self):
        """Return validated atlas geometry derived from metadata/layer dimensions."""
        if not self.xp or not self.meta or len(self.xp.layers) < 2:
            raise ValueError("No XP loaded")
        anims = list(self.meta.get("anims", []))
        if not anims:
            raise ValueError("Metadata has no animations")
        total_frames = int(sum(anims))
        angles = max(1, int(self.meta.get("angles", 1)))
        projs = max(1, int(self.meta.get("projs", 1)))
        total_columns = total_frames * projs
        if total_columns <= 0:
            raise ValueError("Invalid atlas column count")
        l1 = self.xp.layers[1]
        cell_w = l1.width // total_columns
        cell_h = l1.height // angles
        if cell_w <= 0 or cell_h <= 0:
            raise ValueError("Invalid cell geometry")
        return {
            "anims": anims,
            "angles": angles,
            "projs": projs,
            "total_frames": total_frames,
            "total_columns": total_columns,
            "cell_w": cell_w,
            "cell_h": cell_h,
            "char_w": self.font.char_w,
            "char_h": self.font.char_h,
        }

    def _resolve_anim_frame(self, anim_idx, frame_idx, anims):
        """Resolve animation-local frame index to global frame index."""
        if not anims:
            return (0, 0, 1, 0)
        if anim_idx < 0 or anim_idx >= len(anims):
            anim_idx = 0
        start_frame = sum(int(v) for v in anims[:anim_idx])
        anim_length = max(1, int(anims[anim_idx]))
        frame_local = int(frame_idx) % anim_length
        frame_global = start_frame + frame_local
        return (anim_idx, start_frame, anim_length, frame_global)

    def _frame_pixel_rect(self, anim_idx=0, frame_idx=0, angle_idx=0, proj=0):
        """Compute pixel and cell rect for one preview frame crop."""
        g = self._get_preview_geometry()
        anim_idx, start_frame, anim_length, frame_global = self._resolve_anim_frame(
            anim_idx, frame_idx, g["anims"]
        )
        angle = int(angle_idx) % g["angles"]
        proj = 1 if int(proj) > 0 and g["projs"] > 1 else 0
        frame_column = frame_global + (g["total_frames"] if proj > 0 else 0)

        x_chars = frame_column * g["cell_w"]
        y_chars = angle * g["cell_h"]
        w_chars = g["cell_w"]
        h_chars = g["cell_h"]

        sx = x_chars * g["char_w"]
        sy = y_chars * g["char_h"]
        w_pix = w_chars * g["char_w"]
        h_pix = h_chars * g["char_h"]
        return {
            "anim_idx": anim_idx,
            "anim_start": start_frame,
            "anim_length": anim_length,
            "frame_local": int(frame_idx) % anim_length,
            "frame_global": frame_global,
            "angle_idx": angle,
            "proj": proj,
            "frame_column": frame_column,
            "cell_rect": [x_chars, y_chars, w_chars, h_chars],
            "pixel_rect": [sx, sy, w_pix, h_pix],
            "geometry": g,
        }

    def _render_preview_frame_image(self, pixel_rect, scale=2, target_h=None):
        """Crop and scale one preview frame from ``raw_image``."""
        if not hasattr(self, "raw_image"):
            self.render_sheet()
        sx, sy, w_pix, h_pix = [int(v) for v in pixel_rect]
        frame_img = self.raw_image.crop((sx, sy, sx + w_pix, sy + h_pix))
        if target_h is not None:
            target_h = max(1, int(target_h))
            factor = target_h / max(1, h_pix)
            target_w = max(1, int(w_pix * factor))
            frame_img = frame_img.resize((target_w, target_h), Image.NEAREST)
        else:
            scale = max(1, int(scale))
            if scale != 1:
                frame_img = frame_img.resize(
                    (frame_img.width * scale, frame_img.height * scale),
                    Image.NEAREST,
                )
        return frame_img

    def _frame_metrics(self, cell_rect):
        """Measure visibility/activity in a frame using visual layer cells."""
        if not self.xp:
            return {"active_cells": 0, "active_ratio": 0.0, "bbox": None, "centroid": None}
        x0, y0, w, h = [int(v) for v in cell_rect]
        layer_idx = 2 if len(self.xp.layers) > 2 else 1
        layer = self.xp.layers[layer_idx]
        max_x = min(layer.width, x0 + w)
        max_y = min(layer.height, y0 + h)
        active = 0
        sx = 0.0
        sy = 0.0
        min_x = None
        min_y = None
        max_xa = None
        max_ya = None
        for y in range(y0, max_y):
            for x in range(x0, max_x):
                glyph, _fg, _bg = layer.data[y][x]
                # Use glyph occupancy as the signal; background fills differ by
                # pipeline/source and can mask true frame activity.
                visible = glyph not in (0, 32)
                if not visible:
                    continue
                active += 1
                sx += (x - x0)
                sy += (y - y0)
                if min_x is None or x < min_x:
                    min_x = x
                if max_xa is None or x > max_xa:
                    max_xa = x
                if min_y is None or y < min_y:
                    min_y = y
                if max_ya is None or y > max_ya:
                    max_ya = y
        total = max(1, w * h)
        if active > 0:
            centroid = [sx / active, sy / active]
            bbox = [min_x - x0, min_y - y0, max_xa - x0, max_ya - y0]
        else:
            centroid = None
            bbox = None
        return {
            "active_cells": active,
            "active_ratio": float(active) / float(total),
            "bbox": bbox,
            "centroid": centroid,
        }

    def _frame_pixel_metrics(self, pixel_rect):
        """Measure frame activity from rendered pixels (color energy based)."""
        img = self._render_preview_frame_image(pixel_rect, scale=1, target_h=None).convert("RGB")
        w, h = img.size
        px = img.load()
        active_px = 0
        energy = 0.0
        sx = 0.0
        sy = 0.0
        for y in range(h):
            for x in range(w):
                r, g, b = px[x, y]
                e = float(max(r, g, b))
                if e <= 8.0:
                    continue
                active_px += 1
                energy += e
                sx += x * e
                sy += y * e
        total = max(1, w * h)
        centroid = [sx / energy, sy / energy] if energy > 0 else None
        return {
            "active_pixels": active_px,
            "active_pixel_ratio": float(active_px) / float(total),
            "pixel_energy": energy,
            "pixel_centroid": centroid,
        }

    def _build_preview_frame_payload(
        self,
        anim_idx=0,
        frame_idx=0,
        angle_idx=0,
        proj=0,
        scale=2,
        include_image=True,
        target_h=None,
    ):
        info = self._frame_pixel_rect(
            anim_idx=anim_idx, frame_idx=frame_idx, angle_idx=angle_idx, proj=proj
        )
        frame = {
            "anim_idx": info["anim_idx"],
            "anim_start": info["anim_start"],
            "anim_length": info["anim_length"],
            "frame_local": info["frame_local"],
            "frame_global": info["frame_global"],
            "frame_column": info["frame_column"],
            "angle_idx": info["angle_idx"],
            "proj": info["proj"],
            "cell_rect": info["cell_rect"],
            "pixel_rect": info["pixel_rect"],
        }
        frame["metrics"] = self._frame_metrics(info["cell_rect"])
        frame["pixel_metrics"] = self._frame_pixel_metrics(info["pixel_rect"])
        if include_image:
            img = self._render_preview_frame_image(
                info["pixel_rect"],
                scale=scale,
                target_h=target_h,
            )
            buf = BytesIO()
            img.save(buf, format="PNG")
            frame["image_png_b64"] = base64.b64encode(buf.getvalue()).decode("ascii")
            frame["image_size"] = [img.width, img.height]
        return frame

    def mcp_get_preview_frame(
        self, anim_idx=0, frame_idx=0, angle_idx=0, proj=0, scale=2, include_image=True
    ):
        """MCP command: return one animation preview frame with diagnostics."""
        return {
            "filepath": self.filepath,
            "metadata": self.meta,
            "frame": self._build_preview_frame_payload(
                anim_idx=anim_idx,
                frame_idx=frame_idx,
                angle_idx=angle_idx,
                proj=proj,
                scale=scale,
                include_image=include_image,
                target_h=None,
            ),
        }

    def mcp_get_preview_sequence(
        self, anim_idx=0, angle_idx=0, proj=0, scale=2, include_image=True
    ):
        """MCP command: return all frames for one animation track."""
        g = self._get_preview_geometry()
        anim_idx, _start, anim_length, _global = self._resolve_anim_frame(
            anim_idx, 0, g["anims"]
        )
        frames = []
        for local_frame in range(anim_length):
            frames.append(
                self._build_preview_frame_payload(
                    anim_idx=anim_idx,
                    frame_idx=local_frame,
                    angle_idx=angle_idx,
                    proj=proj,
                    scale=scale,
                    include_image=include_image,
                    target_h=None,
                )
            )
        return {
            "filepath": self.filepath,
            "metadata": self.meta,
            "anim_idx": anim_idx,
            "angle_idx": int(angle_idx) % g["angles"],
            "proj": 1 if int(proj) > 0 and g["projs"] > 1 else 0,
            "anim_length": anim_length,
            "frames": frames,
        }

    def mcp_analyze_sequence(self, anim_idx=0, angle_idx=0, proj=0):
        """MCP command: analyze one animation sequence for drift/flashing."""
        seq = self.mcp_get_preview_sequence(
            anim_idx=anim_idx,
            angle_idx=angle_idx,
            proj=proj,
            scale=1,
            include_image=False,
        )
        counts = [int(f["metrics"]["active_cells"]) for f in seq["frames"]]
        ratios = [float(f["metrics"]["active_ratio"]) for f in seq["frames"]]
        pixel_counts = [int(f["pixel_metrics"]["active_pixels"]) for f in seq["frames"]]
        pixel_energy = [float(f["pixel_metrics"]["pixel_energy"]) for f in seq["frames"]]
        nonzero = sorted(c for c in pixel_counts if c > 0)
        median = nonzero[len(nonzero) // 2] if nonzero else 0
        flash_threshold = max(1, int(median * 0.25)) if median > 0 else 0
        flashing_frames = [i for i, c in enumerate(pixel_counts) if c <= flash_threshold]

        centroids = []
        for f in seq["frames"]:
            c = f["pixel_metrics"].get("pixel_centroid")
            centroids.append(float(c[0]) if c is not None else None)

        deltas = []
        prev = None
        for c in centroids:
            if c is None:
                continue
            if prev is not None:
                deltas.append(c - prev)
            prev = c
        eps = 0.05
        left_steps = sum(1 for d in deltas if d < -eps)
        right_steps = sum(1 for d in deltas if d > eps)
        drift_score = (
            float(left_steps - right_steps) / float(len(deltas))
            if deltas
            else 0.0
        )

        return {
            "filepath": seq["filepath"],
            "metadata": seq["metadata"],
            "anim_idx": seq["anim_idx"],
            "angle_idx": seq["angle_idx"],
            "proj": seq["proj"],
            "anim_length": seq["anim_length"],
            "counts": counts,
            "active_ratios": ratios,
            "pixel_counts": pixel_counts,
            "pixel_energy": pixel_energy,
            "centroid_x": centroids,
            "centroid_dx": deltas,
            "flash_threshold": flash_threshold,
            "flashing_frames": flashing_frames,
            "left_steps": left_steps,
            "right_steps": right_steps,
            "drift_score": drift_score,
        }

class CellEditor(Toplevel):
    """Per-cell editing window for a single angle/frame region of the sprite sheet.

    Opens as a Toplevel (child window) showing a zoomed-in view of one cell
    (one angle row x one frame column). Provides drawing tools (paint, half-block,
    dropper, select, eraser, replace-color) and copy/paste for the selected region.

    [DATA-CONTRACT:XP] Reads/writes cell data directly in the parent's
    ``xp.layers[active_layer].data[gy + y][gx + x]`` -- mutations are in-place.
    [DATA-CONTRACT:CP437] Glyph selection uses ord(char) for CP437-range characters.

    Attributes:
        parent (SpriteEditor): The owning editor instance.
        angle (int): Row index in the sprite sheet grid.
        frame (int): Column index in the sprite sheet grid.
        cw, ch (int): Cell dimensions in characters (width, height).
        gx, gy (int): Top-left character coordinate of this cell in the full sheet.
        tool (str): Current active tool name.
        selection (tuple|None): (x1, y1, x2, y2) local cell coords, or None.
        clipboard (list|None): 2D list of (glyph, fg, bg) tuples for paste.
    """
    def __init__(self, parent, angle, frame, w, h):
        """Open a cell editing window for one angle/frame region.

        Args:
            parent: The owning SpriteEditor instance.
            angle: Row index (angle) in the sprite sheet grid.
            frame: Column index (frame) in the sprite sheet grid.
            w: Cell width in characters.
            h: Cell height in characters.
        """
        super().__init__(parent.root)
        self.parent = parent
        self.angle = angle
        self.frame = frame
        self.cw = w
        self.ch = h
        
        self.active_layer = parent.active_layer_idx
        
        self.update_title()
        self.geometry("950x800")
        
        self.gx = frame * w
        self.gy = angle * h
        
        # Default drawing tool state
        self.selected_glyph = 64  # '@' in CP437 -- a visible default glyph
        self.selected_fg = (255, 255, 255)
        # WHY default bg=(255,0,255): Magic pink is the transparency convention.
        # New strokes default to transparent background so artists must
        # explicitly choose a solid background color when they want one.
        self.selected_bg = (255, 0, 255)
        
        self.tool = "paint" 
        self.selection = None 
        self.clipboard = None
        
        self.setup_ui()
        self.render()
        
        # Bind shortcuts
        self.bind("<Control-c>", self.copy_selection)
        self.bind("<Control-v>", self.paste_selection)
        self.bind("<Command-c>", self.copy_selection)
        self.bind("<Command-v>", self.paste_selection)
        self.bind("<Control-z>", parent.undo)
        self.bind("<Command-z>", parent.undo)
        self.bind("<Control-Shift-z>", parent.redo)
        self.bind("<Command-Shift-z>", parent.redo)
    
    def update_title(self):
        """Set the window title to reflect the current layer, angle, and frame."""
        self.title(f"Editing Layer {self.active_layer} - A{self.angle} F{self.frame}")

    def refresh_from_parent(self):
        """Sync active layer from parent and re-render (called after undo/redo)."""
        self.active_layer = self.parent.active_layer_idx
        self.render()
        
    def setup_ui(self):
        """Build the CellEditor UI: navigation buttons, tool panel, palette, canvas."""
        # Tools Top
        top_frame = tk.Frame(self)
        top_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)
        
        # Navigation
        nav_frame = tk.Frame(top_frame)
        nav_frame.pack(side=tk.LEFT)
        tk.Button(nav_frame, text="^ Prev Angle", command=lambda: self.navigate(da=-1)).pack(side=tk.LEFT, padx=1)
        tk.Button(nav_frame, text="v Next Angle", command=lambda: self.navigate(da=1)).pack(side=tk.LEFT, padx=1)
        tk.Label(nav_frame, text=" | ").pack(side=tk.LEFT)
        tk.Button(nav_frame, text="< Prev Frame", command=lambda: self.navigate(df=-1)).pack(side=tk.LEFT, padx=1)
        tk.Button(nav_frame, text="> Next Frame", command=lambda: self.navigate(df=1)).pack(side=tk.LEFT, padx=1)
        
        # Explicit Undo/Redo in Editor
        tk.Label(top_frame, text=" | ").pack(side=tk.LEFT)
        tk.Button(top_frame, text="Undo", command=self.parent.undo, bg="#ffdddd").pack(side=tk.LEFT, padx=2)
        tk.Button(top_frame, text="Redo", command=self.parent.redo, bg="#ddffdd").pack(side=tk.LEFT, padx=2)
                
        # Right tools
        tools = tk.Frame(self)
        tools.pack(side=tk.RIGHT, fill=tk.Y, padx=5, pady=5)
        
        tk.Label(tools, text=f"Active: L{self.active_layer}", fg="blue", font=("Arial", 10, "bold")).pack()
        
        tk.Label(tools, text="Char:").pack(pady=(10,0))
        self.char_entry = tk.Entry(tools, width=5)
        self.char_entry.insert(0, "@")
        self.char_entry.pack()
        self.char_entry.bind("<KeyRelease>", self.update_char)
        
        tk.Label(tools, text="FG").pack(pady=(5,0))
        self.fg_btn = tk.Button(tools, bg="white", width=4, command=lambda: self.pick_color('fg'))
        self.fg_btn.pack()
        
        tk.Label(tools, text="BG").pack(pady=(5,0))
        self.bg_btn = tk.Button(tools, bg="magenta", width=4, command=lambda: self.pick_color('bg'))
        self.bg_btn.pack()
        
        tk.Label(tools, text="Tools").pack(pady=(15,5))
        tk.Button(tools, text="Paint (P)", command=lambda: self.set_tool("paint")).pack(fill=tk.X)
        tk.Button(tools, text="Half Block", command=lambda: self.set_tool("half")).pack(fill=tk.X)
        tk.Button(tools, text="Dropper (D)", command=lambda: self.set_tool("dropper")).pack(fill=tk.X)
        tk.Button(tools, text="Select (S)", command=lambda: self.set_tool("select")).pack(fill=tk.X)
        tk.Button(tools, text="Eraser (E)", command=lambda: self.set_tool("eraser")).pack(fill=tk.X)
        btn_replace = tk.Button(tools, text="Flood Repl", command=lambda: self.set_tool("replace"))
        btn_replace.pack(fill=tk.X)
        # Tooltip-style help text for the flood replace tool
        tk.Label(tools, text="(replaces all matching\nfg/bg in cell region)", font=("Arial", 7), fg="#666").pack()
        
        tk.Label(tools, text="Edit").pack(pady=(15,5))
        tk.Button(tools, text="Copy (C)", command=self.copy_selection).pack(fill=tk.X)
        tk.Button(tools, text="Paste (V)", command=self.paste_selection).pack(fill=tk.X)

        # Bottom Palette
        palette_frame = tk.Frame(self)
        palette_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=5)
        common = "@#$%&?*!/\\|[]{}<>"
        for c in common:
             tk.Button(palette_frame, text=c, command=lambda ch=c: self.select_char(ch), width=2).pack(side=tk.LEFT)
             
        # WHY these specific block glyphs: CP437 chars 220 (lower half), 223 (upper
        # half), and 219 (full block) are the most-used "pixel" primitives in ASCII
        # art. They allow sub-character vertical resolution (2 rows per cell).
        # [DATA-CONTRACT:CP437]
        tk.Label(palette_frame, text="| Blocks:").pack(side=tk.LEFT, padx=5)
        blocks = [220, 223, 219]
        for b in blocks:
             tk.Button(palette_frame, text=chr(b), command=lambda ch=chr(b): self.select_char(ch), width=2).pack(side=tk.LEFT)

        # Canvas
        self.canvas = Canvas(self, bg="#202020")
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        # TODO(PIPELINE-FIX): on_release is bound but never defined on CellEditor.
        # This will raise AttributeError on mouse button release. Either define
        # the method or remove this binding.
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
    
    def navigate(self, da=0, df=0):
        """Move this editor to an adjacent angle (da) or frame (df) cell.

        Args:
            da: Delta for angle index (-1 = previous, +1 = next).
            df: Delta for frame index (-1 = previous, +1 = next).
        """
        meta = self.parent.meta
        if not meta: return
        
        total_angles = meta['angles']
        total_frames = sum(meta['anims'])
        
        new_angle = self.angle + da
        new_frame = self.frame + df
        
        if 0 <= new_angle < total_angles and 0 <= new_frame < total_frames:
             self.angle = new_angle
             self.frame = new_frame
             self.gx = self.frame * self.cw
             self.gy = self.angle * self.ch
             self.update_title()
             self.render()
        else:
             print("Navigation out of bounds")

    def set_tool(self, tool):
        """Switch the active drawing tool (paint, half, dropper, select, eraser, replace).

        Args:
            tool: One of 'paint', 'half', 'dropper', 'select', 'eraser', 'replace'.
        """
        self.tool = tool
        self.update_title()
        if tool == "half":
            self.select_char(chr(220)) 
        
    def select_char(self, char):
        """Set the active glyph from a character (used by palette buttons).

        [DATA-CONTRACT:CP437] Converts the character via ord() to a CP437 codepoint index.

        Args:
            char: Single character string to set as the active glyph.
        """
        self.selected_glyph = ord(char)
        self.char_entry.delete(0, tk.END)
        self.char_entry.insert(0, char)

    def update_char(self, event):
        """Update selected glyph from the character entry field on each key release."""
        txt = self.char_entry.get()
        if len(txt) > 0:
            self.selected_glyph = ord(txt[0])
            
    def pick_color(self, target):
        """Open a system color chooser to set the FG or BG color.

        Args:
            target: Either 'fg' (foreground) or 'bg' (background).
        """
        color = colorchooser.askcolor()[0]
        if color:
            c = (int(color[0]), int(color[1]), int(color[2]))
            hex_c = rgb_to_hex(c)
            if target == 'fg':
                self.selected_fg = c
                self.fg_btn.config(bg=hex_c)
            else:
                self.selected_bg = c
                self.bg_btn.config(bg=hex_c)
    
    def copy_selection(self, event=None):
        """Copy the selected rectangle of cells from the active layer into the clipboard.

        [DATA-CONTRACT:XP] Reads cell tuples ``(glyph, fg, bg)`` from the active layer.

        Args:
            event: Optional Tkinter event (from keyboard shortcut binding).
        """
        if not self.selection:
            return
        x1, y1, x2, y2 = self.selection
        layer = self.parent.xp.layers[self.active_layer]
        self.clipboard = []
        for y in range(y1, y2+1):
            row = []
            for x in range(x1, x2+1):
                glyph, fg, bg = layer.data[self.gy + y][self.gx + x]
                row.append((glyph, fg, bg))
            self.clipboard.append(row)
        print("Copied")

    def paste_selection(self, event=None):
        """Paste clipboard cells into the active layer at the selection origin.

        [DATA-CONTRACT:XP] Writes cell tuples ``(glyph, fg, bg)`` into the active layer.
        Commits an undo snapshot before mutating.

        Args:
            event: Optional Tkinter event (from keyboard shortcut binding).
        """
        if not self.clipboard:
            return
        if self.selection:
            start_x, start_y, _, _ = self.selection
        else:
            start_x, start_y = 0, 0
        self.parent.commit_action()
        layer = self.parent.xp.layers[self.active_layer]
        rows = len(self.clipboard)
        cols = len(self.clipboard[0])
        for y in range(rows):
            for x in range(cols):
                target_x = start_x + x
                target_y = start_y + y
                if target_x < self.cw and target_y < self.ch:
                    data = self.clipboard[y][x]
                    layer.data[self.gy + target_y][self.gx + target_x] = data
        self.render()

    def render(self):
        """Redraw the zoomed cell view with checkerboard, all visible layers, and grid.

        [DEPENDENCY:PIL] Composites all visible layers into a PIL Image, then
        scales via NEAREST resampling for the zoomed editor view.

        [DATA-CONTRACT:XP] Reads cell tuples ``(glyph, fg, bg)`` from each visible
        layer within the cell's character coordinate range.

        WHY checkerboard background: The alternating grey pattern lets the artist
        distinguish transparent cells (magic pink bg) from solid black cells.
        Without it, both would appear identical on the dark canvas.

        WHY scale=32: Each character cell is rendered at 32x32 pixels in the editor
        view, providing sufficient zoom for pixel-level editing of 12x12 glyphs.
        """
        self.canvas.delete("all")
        scale = 32
        cw, ch = self.parent.font.char_w, self.parent.font.char_h
        
        img = Image.new("RGBA", (self.cw * cw, self.ch * ch), (64, 64, 64, 255))
        draw = ImageDraw.Draw(img)
        
        for y in range(self.ch):
            for x in range(self.cw):
                if (x+y)%2 == 0:
                     draw.rectangle([x*cw, y*ch, (x+1)*cw, (y+1)*ch], fill=(80,80,80))

        layers = self.parent.xp.layers
        show_layers = self.parent.show_layers
        
        for idx in range(1, len(layers)):
            if not show_layers[idx].get(): 
                continue
            
            layer = layers[idx]
            for y in range(self.ch):
                for x in range(self.cw):
                    gx = self.gx + x
                    gy = self.gy + y
                    glyph, fg, bg = layer.data[gy][gx]
                    
                    is_bg_trans = (bg[0] == 255 and bg[1] == 0 and bg[2] == 255)
                    if not is_bg_trans:
                        draw.rectangle([x*cw, y*ch, (x+1)*cw, (y+1)*ch], fill=bg)
                    
                    if glyph != 0 and glyph != 32:
                        char_img = self.parent.font.render(glyph, fg)
                        img.alpha_composite(char_img, (x*cw, y*ch))
                    
        zoomed = img.resize((self.cw * scale, self.ch * scale), Image.NEAREST)
        self.tk_img = ImageTk.PhotoImage(zoomed)
        self.canvas.create_image(0, 0, image=self.tk_img, anchor=tk.NW)
        
        for x in range(self.cw + 1):
            self.canvas.create_line(x * scale, 0, x * scale, self.ch * scale, fill="#606060")
        for y in range(self.ch + 1):
            self.canvas.create_line(0, y * scale, self.cw * scale, y * scale, fill="#606060")
            
        if self.selection:
            x1, y1, x2, y2 = self.selection
            px1, py1 = x1 * scale, y1 * scale
            px2, py2 = (x2+1) * scale, (y2+1) * scale
            self.canvas.create_rectangle(px1, py1, px2, py2, outline="white", dash=(4,4), width=2)
    
    def on_click(self, event):
        """Handle a mouse click: snapshot for undo, then dispatch to the active tool."""
        self.parent.commit_action()
        scale = 32
        x = int(event.x / scale)
        y = int(event.y / scale)
        
        if 0 <= x < self.cw and 0 <= y < self.ch:
            if self.tool == "select":
                self.selection = (x, y, x, y)
                self.render()
            elif self.tool == "dropper":
                gx, gy = self.gx + x, self.gy + y
                layer = self.parent.xp.layers[self.active_layer]
                glyph, fg, bg = layer.data[gy][gx]
                self.selected_glyph = glyph
                self.selected_fg = fg
                self.selected_bg = bg
                self.fg_btn.config(bg=rgb_to_hex(fg))
                self.bg_btn.config(bg=rgb_to_hex(bg))
                self.char_entry.delete(0, tk.END)
                if 32 <= glyph <= 255: self.char_entry.insert(0, chr(glyph))
                self.set_tool("paint")
            else:
                self.apply_tool(x, y)

    def on_drag(self, event):
        """Handle mouse drag: extend selection or apply paint tool continuously."""
        scale = 32
        x = int(event.x / scale)
        y = int(event.y / scale)
        if 0 <= x < self.cw and 0 <= y < self.ch:
            if self.tool == "select":
                if self.selection:
                    x1, y1, _, _ = self.selection
                    self.selection = (min(x1, x), min(y1, y), max(x1, x), max(y1, y))
                    self.render()
            else:
                self.apply_tool(x, y)

    def on_release(self, event):
        """Handle mouse release for drag operations.

        Bound from setup_ui() to complete drag interactions. A previous refactor
        left the binding in place without a handler, which raised AttributeError
        on every mouse-up event in the editor canvas.
        """
        # No finalization step is required right now; on_drag/on_click already
        # apply edits and re-render incrementally.
        return

    def apply_tool(self, x, y):
        """Apply the current tool at local cell coordinate (x, y).

        Tools:
            paint/half: Set glyph, fg, bg to the current selection.
            eraser: Reset cell to transparent empty (glyph=0, bg=magic pink).
            replace: Flood-replace all cells in this cell-region that match the
                     clicked cell's fg/bg with the currently selected fg/bg.
        """
        gx = self.gx + x
        gy = self.gy + y
        layer = self.parent.xp.layers[self.active_layer]

        if self.tool == "paint" or self.tool == "half":
             layer.data[gy][gx] = (self.selected_glyph, self.selected_fg, self.selected_bg)
        elif self.tool == "eraser":
             # WHY these specific values: glyph=0 is null (invisible), fg=white
             # is irrelevant since glyph is null, bg=magic pink marks transparency.
             layer.data[gy][gx] = (0, (255,255,255), (255,0,255))
        elif self.tool == "replace":
             # WHY whole-cell scan: "Flood Replace" acts as a global find-replace
             # within this animation cell, not a flood-fill from the click point.
             # It replaces ALL cells with matching fg/bg, regardless of adjacency.
             self.parent.commit_action()
             target_bg = layer.data[gy][gx][2]
             target_fg = layer.data[gy][gx][1]
             for cy in range(self.ch):
                 for cx in range(self.cw):
                     cgx, cgy = self.gx + cx, self.gy + cy
                     cbg = layer.data[cgy][cgx][2]
                     cfg = layer.data[cgy][cgx][1]
                     gl, f, b = layer.data[cgy][cgx]
                     if cbg == target_bg: b = self.selected_bg
                     if cfg == target_fg: f = self.selected_fg
                     layer.data[cgy][cgx] = (gl, f, b)
        self.render()
        
    def destroy(self):
        """Clean up on window close: stop any owned server, re-render parent sheet.

        TODO(PIPELINE-FIX): The ``hasattr(self, 'server')`` check is defensive --
        CellEditor never creates its own server. This guard may be leftover from
        a refactor. It is harmless but confusing.
        """
        if hasattr(self, 'server'):
            self.server.stop()
        self.parent.render_sheet()
        super().destroy()

# ---- Entry point ----
# TODO(PIPELINE-FIX): The CLI auto-load below duplicates the layer-setup logic
# from load_file() / reload_current(). This should be refactored to call a shared
# helper to avoid three copies of the same layer-UI-building code.

if __name__ == "__main__":
    # Handle --help before Tk initialization to avoid opening a blank window.
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print("usage: xp_tool.py [FILE.xp]")
        print()
        print("Asciicker XP Sprite Editor/Viewer")
        print()
        print("positional arguments:")
        print("  FILE.xp    XP file to open (optional)")
        print()
        print("options:")
        print("  -h, --help  show this help message and exit")
        sys.exit(0)

    root = tk.Tk()
    app = SpriteEditor(root)

    # Auto-load a file if provided as a command-line argument.
    if len(sys.argv) > 1:
        path = sys.argv[1]
        if os.path.exists(path):
            try:
                app.xp = XPFile()
                app.xp.load(path)
                app.filepath = path
                app.meta = app.xp.get_metadata()
                app.root.title(f"Asciicker XP Tool - {path}")

                # Setup layer UI (skip layer 0 -- metadata only)
                for i in range(len(app.xp.layers)):
                    if i == 0: continue
                    tk.Radiobutton(app.layer_radio_frame, text=f"Layer {i}", variable=app.active_layer_var, value=i, bg="#d0d0d0", command=app.on_layer_change).pack(anchor=tk.W)
                    var = tk.BooleanVar(value=True)
                    app.show_layers[i] = var
                    tk.Checkbutton(app.layer_check_frame, text=f"Layer {i}", variable=var, command=app.render_sheet, bg="#d0d0d0").pack(anchor=tk.W)

                app.active_layer_var.set(1)
                app.render_sheet()
                print(f"Auto-loaded: {path}")
            except Exception as e:
                print(f"Failed to auto-load {path}: {e}")

    root.mainloop()
