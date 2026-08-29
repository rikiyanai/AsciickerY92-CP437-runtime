"""XP File Browser -- thumbnail grid browser for .xp sprite files.

[DEPENDENCY:TKINTER] [DEPENDENCY:PIL] [DATA-CONTRACT:XP] [DATA-CONTRACT:CP437]

ARCHITECTURE:
    A scrollable thumbnail grid that lets users browse .xp files in a directory,
    preview them as rendered thumbnails, and open them in the parent SpriteEditor.

    This module is designed to be imported by xp_tool.py via a lazy import in
    SpriteEditor.browse_xp(). It depends on xp_core.XPFile for loading .xp data
    and on the parent editor's BitmapFont instance for glyph rendering.

    UI LAYOUT:
        +--------------------------------------------------+
        | Directory: [path entry] [Browse] [Refresh]       |
        +--------------------------------------------------+
        | +--------+  +--------+  +--------+  +--------+  |
        | | thumb  |  | thumb  |  | thumb  |  | thumb  |  |
        | | name   |  | name   |  | name   |  | name   |  |
        | +--------+  +--------+  +--------+  +--------+  |
        | +--------+  +--------+  +--------+  +--------+  |
        | | thumb  |  | thumb  |  | thumb  |  | thumb  |  |
        | | name   |  | name   |  | name   |  | name   |  |
        | +--------+  +--------+  +--------+  +--------+  |
        |                 (scrollable)                     |
        +--------------------------------------------------+
        | [status text]                          [Open]    |
        +--------------------------------------------------+

USAGE:
    from .xp_browser import XPBrowser
    XPBrowser(parent_editor)
"""

import tkinter as tk
from tkinter import filedialog, Toplevel, Canvas
from PIL import Image, ImageTk, ImageDraw
import os


class XPBrowser(Toplevel):
    """Scrollable thumbnail grid browser for .xp files.

    Scans a directory for .xp files, renders a small preview thumbnail of each,
    and displays them in a 4-column grid. Single-click selects a file (highlighted
    in blue), double-click or the Open button loads it into the parent editor.

    Thumbnails are loaded in batches of 8 with ``after()`` scheduling to keep the
    UI responsive during rendering.

    Attributes:
        parent_editor (SpriteEditor): The editor instance to load files into.
        selected_path (str|None): Path of the currently selected .xp file.
    """

    THUMB_SIZE = 100
    GRID_COLS = 4
    PADDING = 10

    def __init__(self, parent_editor):
        """Initialize the browser window and scan the default directory.

        Args:
            parent_editor: The SpriteEditor instance that opened this browser.
        """
        super().__init__(parent_editor.root)
        self.parent_editor = parent_editor
        self.title("XP File Browser")
        self.geometry("600x700")

        self.selected_path = None
        self._thumb_refs = []  # prevent GC of PhotoImages
        self._loading = False

        self._setup_ui()
        self._set_default_dir()

    def _setup_ui(self):
        """Build the browser UI: directory bar, scrollable grid, bottom buttons."""
        # Directory bar
        dir_frame = tk.Frame(self)
        dir_frame.pack(fill=tk.X, padx=5, pady=5)

        tk.Label(dir_frame, text="Directory:").pack(side=tk.LEFT)
        self.dir_var = tk.StringVar()
        self.dir_entry = tk.Entry(dir_frame, textvariable=self.dir_var, width=40)
        self.dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        tk.Button(dir_frame, text="Browse", command=self._browse_dir).pack(side=tk.LEFT)
        tk.Button(dir_frame, text="Refresh", command=self._refresh).pack(side=tk.LEFT, padx=5)

        # Scrollable canvas area
        self.canvas_frame = tk.Frame(self)
        self.canvas_frame.pack(fill=tk.BOTH, expand=True)

        self.canvas = Canvas(self.canvas_frame, bg="#303030")
        self.vbar = tk.Scrollbar(
            self.canvas_frame, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.config(yscrollcommand=self.vbar.set)

        self.vbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.inner_frame = tk.Frame(self.canvas, bg="#303030")
        self.canvas_window = self.canvas.create_window(
            (0, 0), window=self.inner_frame, anchor=tk.NW)

        self.inner_frame.bind("<Configure>", self._on_frame_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        # Mouse wheel scrolling
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.inner_frame.bind("<MouseWheel>", self._on_mousewheel)

        # Bottom buttons
        btn_frame = tk.Frame(self)
        btn_frame.pack(fill=tk.X, padx=5, pady=5)

        self.open_btn = tk.Button(
            btn_frame, text="Open", command=self._open_selected, state=tk.DISABLED)
        self.open_btn.pack(side=tk.RIGHT, padx=5)

        self.status_label = tk.Label(btn_frame, text="")
        self.status_label.pack(side=tk.LEFT)

    def _on_frame_configure(self, event=None):
        """Update scroll region when the inner frame resizes."""
        self.canvas.config(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event=None):
        """Stretch the inner frame to match the canvas width."""
        if event:
            self.canvas.itemconfig(self.canvas_window, width=event.width)

    def _on_mousewheel(self, event):
        """Handle mouse wheel scrolling on macOS and Windows/Linux."""
        # macOS sends delta as pixels, Windows/Linux as multiples of 120
        if event.delta:
            self.canvas.yview_scroll(-1 * (event.delta // abs(event.delta)), "units")

    def _set_default_dir(self):
        """Find and set a reasonable default directory for browsing.

        Searches for common XP output directories relative to the script location.
        Falls back to the current working directory if none are found.
        """
        base = os.path.dirname(os.path.abspath(__file__))
        candidates = [
            os.path.join(base, "staging", "xp"),
            os.path.join(os.path.dirname(base), "assets", "sprites"),
            os.path.join(os.path.dirname(os.path.dirname(base)), "assets", "sprites"),
        ]
        for d in candidates:
            if os.path.isdir(d):
                self.dir_var.set(d)
                self._refresh()
                return
        self.dir_var.set(os.getcwd())

    def _browse_dir(self):
        """Open a directory chooser dialog and refresh the grid."""
        d = filedialog.askdirectory()
        if d:
            self.dir_var.set(d)
            self._refresh()

    def _refresh(self):
        """Scan the selected directory for .xp files and start loading thumbnails."""
        if self._loading:
            return

        # Clear existing thumbnails
        for child in self.inner_frame.winfo_children():
            child.destroy()
        self._thumb_refs.clear()
        self.selected_path = None
        self.open_btn.config(state=tk.DISABLED)

        directory = self.dir_var.get()
        if not os.path.isdir(directory):
            self.status_label.config(text="Invalid directory")
            return

        xp_files = sorted([
            os.path.join(directory, f)
            for f in os.listdir(directory)
            if f.lower().endswith(".xp")
        ])

        self.status_label.config(text=f"Found {len(xp_files)} .xp files")

        if not xp_files:
            return

        self._loading = True
        self._load_batch(xp_files, 0)

    def _load_batch(self, files, idx):
        """Load thumbnails in batches of 8 to keep the UI responsive.

        Uses Tk after() to yield back to the event loop between batches so the
        window remains interactive during loading.

        Args:
            files: Full list of .xp file paths to load.
            idx: Starting index for this batch.
        """
        batch_size = 8
        end = min(idx + batch_size, len(files))

        for i in range(idx, end):
            self._add_thumbnail(files[i], i)

        if end < len(files):
            self.after(10, lambda: self._load_batch(files, end))
        else:
            self._loading = False
            self.status_label.config(text=f"Loaded {len(files)} files")

    def _add_thumbnail(self, xp_path, index):
        """Render a thumbnail for a single .xp file and add it to the grid.

        Loads the .xp file, renders visible layer data into a small PIL Image,
        scales it to THUMB_SIZE, and places it in the grid at the correct row/col.

        Args:
            xp_path: Absolute path to the .xp file.
            index: Zero-based index in the file list (determines grid position).
        """
        row = index // self.GRID_COLS
        col = index % self.GRID_COLS

        frame = tk.Frame(self.inner_frame, bg="#303030", padx=5, pady=5)
        frame.grid(row=row, column=col, sticky="nsew")

        # Try to render thumbnail
        try:
            from .xp_core import XPFile
            xp = XPFile()
            xp.load(xp_path)

            meta = xp.get_metadata()

            if len(xp.layers) >= 2:
                layer = xp.layers[min(2, len(xp.layers) - 1)]
                w, h = layer.width, layer.height

                # Render a small preview (cap at 40x40 chars to limit CPU)
                cw = self.parent_editor.font.char_w
                ch = self.parent_editor.font.char_h
                render_w = min(w, 40)
                render_h = min(h, 40)
                img = Image.new(
                    "RGBA", (render_w * cw, render_h * ch), (0, 0, 0, 255))
                draw = ImageDraw.Draw(img)

                for y in range(render_h):
                    for x in range(render_w):
                        glyph, fg, bg = layer.data[y][x]
                        is_trans = (
                            bg[0] == 255 and bg[1] == 0 and bg[2] == 255)
                        if not is_trans:
                            draw.rectangle(
                                [x * cw, y * ch, (x + 1) * cw, (y + 1) * ch],
                                fill=bg)
                        if glyph != 0 and glyph != 32:
                            char_img = self.parent_editor.font.render(glyph, fg)
                            img.alpha_composite(char_img, (x * cw, y * ch))

                # Scale to thumbnail
                img.thumbnail(
                    (self.THUMB_SIZE, self.THUMB_SIZE), Image.NEAREST)
                tk_img = ImageTk.PhotoImage(img)
                self._thumb_refs.append(tk_img)

                label = tk.Label(
                    frame, image=tk_img, bg="#303030", cursor="hand2")
                label.pack()
            else:
                tk.Label(
                    frame, text="[empty]", bg="#303030", fg="#888").pack()

            # Filename + metadata summary
            fname = os.path.basename(xp_path)
            if len(fname) > 18:
                fname = fname[:15] + "..."
            info = fname
            if meta:
                info += f"\n{meta.get('angles', '?')}a {len(xp.layers)}L"

            name_lbl = tk.Label(
                frame, text=info, bg="#303030", fg="white",
                font=("Arial", 9), justify=tk.CENTER, cursor="hand2")
            name_lbl.pack()

        except Exception:
            tk.Label(
                frame, text=os.path.basename(xp_path)[:15],
                bg="#303030", fg="#888").pack()

        # Click handlers for selection and double-click open
        def on_select(event, path=xp_path, f=frame):
            # Deselect previous
            for child in self.inner_frame.winfo_children():
                child.config(bg="#303030")
                for w_child in child.winfo_children():
                    if isinstance(w_child, tk.Label):
                        w_child.config(bg="#303030")
            # Highlight selected
            f.config(bg="#4488FF")
            for w_child in f.winfo_children():
                if isinstance(w_child, tk.Label):
                    w_child.config(bg="#4488FF")
            self.selected_path = path
            self.open_btn.config(state=tk.NORMAL)

        def on_double(event, path=xp_path):
            self.selected_path = path
            self._open_selected()

        for widget in frame.winfo_children():
            widget.bind("<Button-1>", on_select)
            widget.bind("<Double-Button-1>", on_double)
            widget.bind("<MouseWheel>", self._on_mousewheel)
        frame.bind("<Button-1>", on_select)
        frame.bind("<Double-Button-1>", on_double)
        frame.bind("<MouseWheel>", self._on_mousewheel)

    def _open_selected(self):
        """Load the selected .xp file into the parent editor and close the browser.

        Resets the editor state (undo stack, layer UI, active editors) and
        rebuilds the layer radio/checkbox widgets from the new file's layers.
        """
        if not self.selected_path:
            return

        editor = self.parent_editor
        try:
            from .xp_core import XPFile
            editor.xp = XPFile()
            editor.xp.load(self.selected_path)
            editor.filepath = self.selected_path
            editor.meta = editor.xp.get_metadata()
            editor.root.title(f"Asciicker XP Tool - {self.selected_path}")
            editor.undo_stack = []

            # Close any open cell editors
            for ed in editor.active_editors:
                if ed.winfo_exists():
                    ed.destroy()
            editor.active_editors = []

            # Rebuild layer UI
            for child in editor.layer_radio_frame.winfo_children():
                child.destroy()
            for child in editor.layer_check_frame.winfo_children():
                child.destroy()

            editor.show_layers = {}
            for i in range(len(editor.xp.layers)):
                if i == 0:
                    continue
                tk.Radiobutton(
                    editor.layer_radio_frame, text=f"Layer {i}",
                    variable=editor.active_layer_var, value=i,
                    bg="#d0d0d0", command=editor.on_layer_change
                ).pack(anchor=tk.W)
                var = tk.BooleanVar(value=True)
                editor.show_layers[i] = var
                tk.Checkbutton(
                    editor.layer_check_frame, text=f"Layer {i}",
                    variable=var, command=editor.render_sheet, bg="#d0d0d0"
                ).pack(anchor=tk.W)

            editor.active_layer_var.set(1)
            editor.render_sheet()
            self.destroy()
        except Exception as e:
            self.status_label.config(text=f"Error: {e}", fg="red")
