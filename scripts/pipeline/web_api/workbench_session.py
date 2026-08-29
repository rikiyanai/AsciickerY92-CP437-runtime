"""
workbench_session.py -- In-memory workbench session state manager.

Manages grid cells, source images, undo/redo history, and frame data
for the sprite workbench UI. Sessions are ephemeral (no persistence).

ARCHITECTURE:
    Each session holds an angles x frames grid of PIL Image cells.
    Operations (transform, swap, fill, import) are recorded for undo/redo.
    Sources are stored as named PIL Images for extraction and population.

Tags: [FLOW:WORKBENCH] [DATA-CONTRACT:SESSION]
"""

import base64
import copy
import io
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageOps

from scripts.pipeline.assembler import encode_digit

# Thumbnail size for grid cell previews
THUMB_SIZE = (96, 96)


@dataclass
class GridCell:
    """A single cell in the workbench grid.

    Native XP cells are stored in xp_cells as a list of (glyph, fg_rgb, bg_rgb)
    tuples per layer. When xp_cells is populated, the cell can be rendered
    natively in the browser via cp437_renderer.js instead of using the
    lossy PIL Image thumbnail.
    """
    angle: int = 0
    anim: int = 0
    frame: int = 0
    proj: int = 0
    image: Optional[Image.Image] = None
    width: int = 0
    height: int = 0
    xp_cells: Optional[list] = None  # [(glyph, (r,g,b), (r,g,b))] per layer


@dataclass
class Operation:
    """A recorded operation for undo/redo."""
    op_id: str = ""
    op_type: str = ""  # transform, swap, fill, import, populate
    snapshot: list = field(default_factory=list)  # list of (index, GridCell-copy) tuples


@dataclass
class SourceImage:
    """A stored source image."""
    source_id: str = ""
    image: Optional[Image.Image] = None
    filename: str = ""
    width: int = 0
    height: int = 0


class WorkbenchSession:
    """In-memory workbench session managing a grid of sprite cells."""

    def __init__(self, job_id: str, angles: int, frames: list,
                 projs: int, cell_w: int, cell_h: int):
        self.job_id = job_id
        self.angles = angles
        self.frames_list = frames  # e.g., [1, 8] for 2 anims
        self.projs = projs
        self.cell_w = cell_w
        self.cell_h = cell_h

        # Build flat grid: angles rows x total_cols columns.
        # Iteration order matches assembler contract (assembler.py:139):
        # angles (outer) -> projs -> anims -> frames (inner).
        # total_cols = sum(frames) * projs.
        self.grid: list[GridCell] = []
        for a in range(angles):
            for p in range(projs):
                for anim_idx, frame_count in enumerate(frames):
                    for f in range(frame_count):
                        self.grid.append(GridCell(
                            angle=a, anim=anim_idx, frame=f, proj=p,
                        ))

        # Undo/redo stacks
        self.undo_stack: list[Operation] = []
        self.redo_stack: list[Operation] = []

        # Source images
        self.sources: dict[str, SourceImage] = {}

    @property
    def total_cols(self) -> int:
        """Total columns including projs multiplier (matches assembler contract)."""
        return sum(self.frames_list) * self.projs

    @property
    def frame_count(self) -> int:
        return len(self.grid)

    @property
    def op_count(self) -> int:
        return len(self.undo_stack)

    @property
    def redo_count(self) -> int:
        return len(self.redo_stack)

    @property
    def has_native_cells(self) -> bool:
        """True if any grid cell has native XP cell data."""
        return any(c.xp_cells is not None for c in self.grid)

    def get_cell_data(self, idx: int) -> Optional[dict]:
        """Get native cell data for a grid cell across all layers."""
        if idx < 0 or idx >= len(self.grid):
            return None
        cell = self.grid[idx]
        if cell.xp_cells is None:
            return None
        layers = []
        for layer_data in cell.xp_cells:
            glyph, fg, bg = layer_data
            layers.append({
                "glyph": glyph,
                "fg": "#{:02x}{:02x}{:02x}".format(*fg),
                "bg": "#{:02x}{:02x}{:02x}".format(*bg),
            })
        return {"layers": layers}

    def set_cell_data(self, idx: int, layer: int, glyph: int, fg: tuple, bg: tuple) -> bool:
        """Update a single layer's cell data for a grid cell."""
        if idx < 0 or idx >= len(self.grid):
            return False
        cell = self.grid[idx]
        if cell.xp_cells is None:
            return False
        if layer < 0 or layer >= len(cell.xp_cells):
            return False
        self._push_op("cell_edit", [idx])
        cell.xp_cells[layer] = (glyph, fg, bg)
        return True

    def _save_snapshot(self, indices: list[int]) -> list:
        """Save cell state for undo."""
        snap = []
        for i in indices:
            cell = self.grid[i]
            snap.append((i, GridCell(
                angle=cell.angle, anim=cell.anim, frame=cell.frame,
                proj=cell.proj,
                image=cell.image.copy() if cell.image else None,
                width=cell.width, height=cell.height,
                xp_cells=copy.deepcopy(cell.xp_cells) if cell.xp_cells else None,
            )))
        return snap

    def _push_op(self, op_type: str, indices: list[int]) -> str:
        """Record an operation for undo. Returns op_id."""
        op_id = str(uuid.uuid4())[:8]
        self.undo_stack.append(Operation(
            op_id=op_id,
            op_type=op_type,
            snapshot=self._save_snapshot(indices),
        ))
        self.redo_stack.clear()
        return op_id

    def undo(self) -> dict:
        """Undo the most recent operation."""
        if not self.undo_stack:
            return {"warning": "Nothing to undo"}
        op = self.undo_stack.pop()
        # Save current state for redo
        indices = [s[0] for s in op.snapshot]
        redo_snap = self._save_snapshot(indices)
        # Restore old state
        for idx, old_cell in op.snapshot:
            self.grid[idx] = old_cell
        self.redo_stack.append(Operation(
            op_id=op.op_id, op_type=op.op_type, snapshot=redo_snap,
        ))
        return {
            "undone_op_id": op.op_id,
            "thumbnails": self._thumbnails_for(indices),
        }

    def redo(self) -> dict:
        """Redo the most recently undone operation."""
        if not self.redo_stack:
            return {"warning": "Nothing to redo"}
        op = self.redo_stack.pop()
        indices = [s[0] for s in op.snapshot]
        undo_snap = self._save_snapshot(indices)
        for idx, new_cell in op.snapshot:
            self.grid[idx] = new_cell
        self.undo_stack.append(Operation(
            op_id=op.op_id, op_type=op.op_type, snapshot=undo_snap,
        ))
        return {
            "redone_op_id": op.op_id,
            "thumbnails": self._thumbnails_for(indices),
        }

    def _cell_index(self, angle: int, anim: int, frame: int, proj: int = 0) -> int:
        """Find index of a cell by its coordinates."""
        for i, c in enumerate(self.grid):
            if c.angle == angle and c.anim == anim and c.frame == frame and c.proj == proj:
                return i
        return -1

    def _thumbnails_for(self, indices: list[int]) -> dict:
        """Generate base64 thumbnails for given cell indices."""
        thumbs = {}
        for i in indices:
            if 0 <= i < len(self.grid):
                thumbs[str(i)] = _cell_thumbnail(self.grid[i])
        return thumbs

    def populate_from_sprites(self, source_id: str, sprites: list,
                              target_indices: Optional[list] = None) -> dict:
        """Place sprite regions from a source image into grid cells.

        Args:
            source_id: ID of the stored source image.
            sprites: List of sprite dicts with bbox [x, y, w, h].
            target_indices: Optional list of cell indices to populate.
                           If None, populates sequentially from index 0.
        """
        src = self.sources.get(source_id)
        if src is None or src.image is None:
            return {"error": f"Source not found: {source_id}"}

        if target_indices is None:
            target_indices = list(range(min(len(sprites), len(self.grid))))

        affected = []
        for si, ti in enumerate(target_indices):
            if si >= len(sprites) or ti >= len(self.grid):
                break
            sprite = sprites[si]
            bbox = sprite.get("bbox", [0, 0, 0, 0])
            x, y, w, h = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
            crop = src.image.crop((x, y, x + w, y + h))
            affected.append(ti)

        # Record undo before modifying
        self._push_op("populate", affected)

        for si, ti in enumerate(target_indices):
            if si >= len(sprites) or ti >= len(self.grid):
                break
            sprite = sprites[si]
            bbox = sprite.get("bbox", [0, 0, 0, 0])
            x, y, w, h = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
            crop = src.image.crop((x, y, x + w, y + h))
            self.grid[ti].image = crop
            self.grid[ti].width = w
            self.grid[ti].height = h

        return {
            "populated": len(affected),
            "frame_count": self.frame_count,
            "thumbnails": self._thumbnails_for(affected),
        }

    def populate_from_images(self, images: list[Image.Image]) -> dict:
        """Place PIL Images directly into grid cells sequentially.

        Used by Load XP flow where frames are already decoded images
        rather than crops from a source sheet.

        Args:
            images: List of PIL Image objects to place into the grid.
        """
        count = min(len(images), len(self.grid))
        affected = list(range(count))

        self._push_op("populate", affected)

        for i in range(count):
            img = images[i]
            self.grid[i].image = img.copy()
            self.grid[i].width = img.width
            self.grid[i].height = img.height

        return {
            "populated": count,
            "frame_count": self.frame_count,
            "thumbnails": self._thumbnails_for(affected),
        }

    def transform_cells(self, targets: list, transform: dict) -> dict:
        """Apply a transform (flip/rotate) to target cells.

        Args:
            targets: List of {angle, anim, frame, proj} dicts.
            transform: {flip_h, flip_v, rotate_deg}.
        """
        indices = []
        for t in targets:
            idx = self._cell_index(t["angle"], t["anim"], t["frame"], t.get("proj", 0))
            if idx >= 0:
                indices.append(idx)

        self._push_op("transform", indices)

        for idx in indices:
            cell = self.grid[idx]
            if cell.image is None:
                continue
            img = cell.image
            if transform.get("flip_h"):
                img = ImageOps.mirror(img)
            if transform.get("flip_v"):
                img = ImageOps.flip(img)
            rotate_deg = transform.get("rotate_deg", 0)
            if rotate_deg:
                img = img.rotate(-rotate_deg, expand=True)
                cell.width = img.width
                cell.height = img.height
            cell.image = img

        return {"thumbnails": self._thumbnails_for(indices)}

    def swap_cells(self, cell_a: dict, cell_b: dict) -> dict:
        """Swap two cells."""
        idx_a = self._cell_index(cell_a["angle"], cell_a["anim"], cell_a["frame"], cell_a.get("proj", 0))
        idx_b = self._cell_index(cell_b["angle"], cell_b["anim"], cell_b["frame"], cell_b.get("proj", 0))
        if idx_a < 0 or idx_b < 0:
            return {"error": "Cell not found"}

        self._push_op("swap", [idx_a, idx_b])

        a, b = self.grid[idx_a], self.grid[idx_b]
        a.image, b.image = b.image, a.image
        a.width, b.width = b.width, a.width
        a.height, b.height = b.height, a.height

        return {"thumbnails": self._thumbnails_for([idx_a, idx_b])}

    def fill_from_slot(self, source: dict, targets: list) -> dict:
        """Copy one cell's content into multiple targets."""
        src_idx = self._cell_index(source["angle"], source["anim"], source["frame"], source.get("proj", 0))
        if src_idx < 0:
            return {"error": "Source cell not found"}

        target_indices = []
        for t in targets:
            idx = self._cell_index(t["angle"], t["anim"], t["frame"], t.get("proj", 0))
            if idx >= 0:
                target_indices.append(idx)

        self._push_op("fill", target_indices)

        src_cell = self.grid[src_idx]
        for idx in target_indices:
            cell = self.grid[idx]
            cell.image = src_cell.image.copy() if src_cell.image else None
            cell.width = src_cell.width
            cell.height = src_cell.height

        return {"thumbnails": self._thumbnails_for(target_indices)}

    def import_external(self, image: Image.Image, targets: list,
                        blend_mode: str = "replace",
                        fit_mode: str = "nearest_stretch") -> dict:
        """Import an external image into target cells."""
        target_indices = []
        for t in targets:
            idx = self._cell_index(t["angle"], t["anim"], t["frame"], t.get("proj", 0))
            if idx >= 0:
                target_indices.append(idx)

        self._push_op("import", target_indices)

        for idx in target_indices:
            cell = self.grid[idx]
            if fit_mode == "nearest_stretch" and (self.cell_w > 0 and self.cell_h > 0):
                resized = image.resize((self.cell_w, self.cell_h), Image.NEAREST)
            else:
                resized = image.copy()

            if blend_mode == "replace" or cell.image is None:
                cell.image = resized
            else:
                # Alpha composite for overlay mode
                if cell.image.mode != "RGBA":
                    cell.image = cell.image.convert("RGBA")
                if resized.mode != "RGBA":
                    resized = resized.convert("RGBA")
                cell.image = Image.alpha_composite(cell.image, resized)
            cell.width = cell.image.width
            cell.height = cell.image.height

        return {"thumbnails": self._thumbnails_for(target_indices)}

    def export_to_xp(self, name: str, output_dir: Path,
                     repair_12px: bool = False,
                     skip_reflections: bool = False) -> dict:
        """Export the grid to an XP file.

        Args:
            name: Sprite name for the output file.
            output_dir: Directory for the output .xp file.
            repair_12px: If True, resize cells to 12px multiples.
        """
        # Determine cell dimensions
        cell_w = self.cell_w
        cell_h = self.cell_h
        suggested_w = None
        suggested_h = None
        repair_available = False

        if cell_w % 12 != 0 or cell_h % 12 != 0:
            repair_available = True
            suggested_w = ((cell_w + 11) // 12) * 12
            suggested_h = ((cell_h + 11) // 12) * 12

        if repair_12px and repair_available:
            cell_w = suggested_w
            cell_h = suggested_h

        output_path = output_dir / f"{name}.xp"
        total_cols = self.total_cols

        # Native path: write xp_cells directly to XP layers (lossless)
        if self.has_native_cells:
            _native_to_xp(
                self.grid, output_path,
                total_cols=total_cols,
                angles=self.angles,
                frames_list=self.frames_list,
            )
            composite_png = output_dir / f"{name}_composite.png"
            # Generate composite PNG for reference even in native mode
            grid_w = total_cols * cell_w
            grid_h = self.angles * cell_h
            composite = Image.new("RGBA", (grid_w, grid_h), (255, 0, 255, 255))
            frames_sum = sum(self.frames_list)
            for i, cell in enumerate(self.grid):
                if cell.image is None:
                    continue
                row = cell.angle
                # AMD-6: proj offset so reflection columns don't overwrite primary
                proj_offset = cell.proj * frames_sum
                col = proj_offset + sum(self.frames_list[:cell.anim]) + cell.frame
                img = cell.image
                if img.size != (cell_w, cell_h):
                    img = img.resize((cell_w, cell_h), Image.NEAREST)
                if img.mode != "RGBA":
                    img = img.convert("RGBA")
                composite.paste(img, (col * cell_w, row * cell_h))
            composite.save(str(composite_png))
        else:
            # Legacy path: composite image → reverse renderer
            grid_w = total_cols * cell_w
            grid_h = self.angles * cell_h
            composite = Image.new("RGBA", (grid_w, grid_h), (255, 0, 255, 255))
            frames_sum = sum(self.frames_list)
            for i, cell in enumerate(self.grid):
                if cell.image is None:
                    continue
                row = cell.angle
                # AMD-6: proj offset so reflection columns don't overwrite primary
                proj_offset = cell.proj * frames_sum
                col = proj_offset + sum(self.frames_list[:cell.anim]) + cell.frame
                img = cell.image
                if img.size != (cell_w, cell_h):
                    img = img.resize((cell_w, cell_h), Image.NEAREST)
                if img.mode != "RGBA":
                    img = img.convert("RGBA")
                composite.paste(img, (col * cell_w, row * cell_h))

            composite_png = output_dir / f"{name}_composite.png"
            composite.save(str(composite_png))
            _composite_to_xp(
                composite, output_path,
                cell_w=cell_w, cell_h=cell_h,
                angles=self.angles,
                frames_list=self.frames_list,
            )

        # After successful repair, mark repair as no longer available
        if repair_12px:
            repair_available = False

        return {
            "xp_path": str(output_path),
            "composite_path": str(composite_png),
            "name": name,
            "frame_count": self.frame_count,
            "grid_layout": {
                "angles": self.angles,
                "total_cols": total_cols,
                "cell_w": cell_w,
                "cell_h": cell_h,
            },
            "repair_available": repair_available,
            "suggested_w": suggested_w,
            "suggested_h": suggested_h,
            "skip_reflections": skip_reflections,
        }

    def store_source(self, source_id: str, image: Image.Image, filename: str = ""):
        """Store a source image for extraction."""
        self.sources[source_id] = SourceImage(
            source_id=source_id,
            image=image,
            filename=filename,
            width=image.width,
            height=image.height,
        )

    def to_json(self) -> dict:
        """Serialize session to JSON-safe dict for persistence.

        Native xp_cells are compact (~100 bytes/cell). PIL Images are stored
        as base64 PNG thumbnails (heavier, only for non-native sessions).
        Undo/redo stacks are NOT persisted (too large, session-scoped).
        """
        cells = []
        for cell in self.grid:
            c = {
                "angle": cell.angle, "anim": cell.anim,
                "frame": cell.frame, "proj": cell.proj,
                "width": cell.width, "height": cell.height,
            }
            if cell.xp_cells is not None:
                c["xp_cells"] = cell.xp_cells
            elif cell.image is not None:
                buf = io.BytesIO()
                cell.image.save(buf, format="PNG")
                c["image_b64"] = base64.b64encode(buf.getvalue()).decode("ascii")
            cells.append(c)
        return {
            "version": 2,
            "job_id": self.job_id,
            "angles": self.angles,
            "frames_list": self.frames_list,
            "projs": self.projs,
            "cell_w": self.cell_w,
            "cell_h": self.cell_h,
            "cells": cells,
        }

    @classmethod
    def from_json(cls, data: dict) -> "WorkbenchSession":
        """Restore session from a JSON dict.

        Rejects version < 2 sessions (grid layout changed for projs support).
        """
        version = data.get("version", 1)
        if version < 2:
            raise ValueError(
                f"Session grid format version {version} is incompatible "
                f"(projs layout changed in version 2). Please recreate the session."
            )
        session = cls(
            job_id=data["job_id"],
            angles=data["angles"],
            frames=data["frames_list"],
            projs=data["projs"],
            cell_w=data["cell_w"],
            cell_h=data["cell_h"],
        )
        for i, c in enumerate(data.get("cells", [])):
            if i >= len(session.grid):
                break
            cell = session.grid[i]
            cell.angle = c.get("angle", 0)
            cell.anim = c.get("anim", 0)
            cell.frame = c.get("frame", 0)
            cell.proj = c.get("proj", 0)
            cell.width = c.get("width", 0)
            cell.height = c.get("height", 0)
            if "xp_cells" in c:
                # Restore tuples from JSON arrays
                cell.xp_cells = [
                    (layer[0], tuple(layer[1]), tuple(layer[2]))
                    for layer in c["xp_cells"]
                ]
            elif "image_b64" in c:
                img_bytes = base64.b64decode(c["image_b64"])
                cell.image = Image.open(io.BytesIO(img_bytes))
                cell.width = cell.image.width
                cell.height = cell.image.height
        return session


def _cell_thumbnail(cell: GridCell) -> Optional[str]:
    """Generate a base64-encoded PNG thumbnail for a grid cell."""
    if cell.image is None:
        return None
    thumb = cell.image.copy()
    thumb.thumbnail(THUMB_SIZE, Image.NEAREST)
    buf = io.BytesIO()
    thumb.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _encode_digit(value: int) -> int:
    """Encode integer 0-35 as CP437 digit glyph code point.

    Delegates to assembler.encode_digit() — single source of truth.
    """
    return encode_digit(value)


def _composite_to_xp(
    composite: Image.Image,
    output_path: Path,
    cell_w: int,
    cell_h: int,
    angles: int,
    frames_list: list,
) -> None:
    """Convert a composite RGBA image to a 4-layer XP file.

    Uses reverse_render_sheet for 12px-aligned cells (glyph recovery),
    or a solid-block fallback for non-12px cells.
    """
    from scripts.pipeline.xp_core import XPFile, XPLayer

    MAGIC_PINK = (255, 0, 255)
    CELL_PX = 12

    sheet_w_cells = composite.width // cell_w
    sheet_h_cells = composite.height // cell_h
    if sheet_w_cells <= 0 or sheet_h_cells <= 0:
        raise ValueError(
            f"Composite {composite.size} too small for cell {cell_w}x{cell_h}"
        )

    use_reverse_render = (cell_w % CELL_PX == 0 and cell_h % CELL_PX == 0)

    if use_reverse_render:
        # High-fidelity: use font atlas glyph matching
        from scripts.pipeline._render_core import (
            find_font_atlas, load_glyph_masks, reverse_render_sheet,
        )
        atlas_path = find_font_atlas()
        if atlas_path:
            masks = load_glyph_masks(atlas_path)
            # Reverse-render at 12px glyph pitch for glyph recovery.
            # The resulting grid has (composite.width // CELL_PX) columns,
            # but XP layer dimensions stay at cell_w/cell_h-based values
            # so metadata and layer structure match the workbench grid.
            rr_grid = reverse_render_sheet(composite, masks, CELL_PX, CELL_PX)
            rr_w = composite.width // CELL_PX
            rr_h = composite.height // CELL_PX
            # Downsample reverse-render grid to logical cell grid by averaging
            # glyphs within each logical cell (cell_w/CELL_PX x cell_h/CELL_PX block).
            cells_per_col = cell_w // CELL_PX
            cells_per_row = cell_h // CELL_PX
            grid = []
            for cy in range(sheet_h_cells):
                row = []
                for cx in range(sheet_w_cells):
                    # Pick the center glyph from the reverse-render block
                    rr_y = cy * cells_per_row + cells_per_row // 2
                    rr_x = cx * cells_per_col + cells_per_col // 2
                    if rr_y < rr_h and rr_x < rr_w:
                        row.append(rr_grid[rr_y][rr_x])
                    else:
                        row.append((32, (0, 0, 0), MAGIC_PINK))
                grid.append(row)
        else:
            use_reverse_render = False

    if not use_reverse_render:
        # Solid-block fallback: average each cell's color
        import numpy as np
        arr = np.array(composite.convert("RGBA"))
        grid = []
        for cy in range(sheet_h_cells):
            row = []
            for cx in range(sheet_w_cells):
                px_x = cx * cell_w
                px_y = cy * cell_h
                block = arr[px_y:px_y + cell_h, px_x:px_x + cell_w]
                if block.shape[2] == 4 and np.mean(block[:, :, 3]) < 128:
                    row.append((32, (0, 0, 0), MAGIC_PINK))
                else:
                    avg = block[:, :, :3].mean(axis=(0, 1)).astype(int)
                    color = (int(avg[0]), int(avg[1]), int(avg[2]))
                    if color == MAGIC_PINK:
                        color = (254, 0, 255)
                    row.append((219, color, color))
            grid.append(row)

    xp_file = XPFile()
    xp_file.version = -1

    # Layer 0: Metadata
    meta_layer = XPLayer(sheet_w_cells, sheet_h_cells)
    for y in range(sheet_h_cells):
        for x in range(sheet_w_cells):
            meta_layer.data[y][x] = (32, (0, 0, 0), (0, 0, 0))
    meta_layer.data[0][0] = (
        _encode_digit(angles), (255, 255, 255), (0, 0, 0)
    )
    for i, count in enumerate(frames_list):
        if i + 1 < sheet_w_cells:
            meta_layer.data[0][i + 1] = (
                _encode_digit(count), (255, 255, 255), (0, 0, 0)
            )
    xp_file.layers.append(meta_layer)

    # Layer 1: Depth (uniform '9')
    depth_layer = XPLayer(sheet_w_cells, sheet_h_cells)
    for y in range(sheet_h_cells):
        for x in range(sheet_w_cells):
            depth_layer.data[y][x] = (ord('9'), (200, 200, 200), (0, 0, 0))
    xp_file.layers.append(depth_layer)

    # Layer 2: Visual
    visual_layer = XPLayer(sheet_w_cells, sheet_h_cells)
    for y in range(sheet_h_cells):
        for x in range(sheet_w_cells):
            if y < len(grid) and x < len(grid[y]):
                visual_layer.data[y][x] = grid[y][x]
            else:
                visual_layer.data[y][x] = (32, (0, 0, 0), MAGIC_PINK)
    xp_file.layers.append(visual_layer)

    # Layer 3: Detail overlay (transparent)
    detail_layer = XPLayer(sheet_w_cells, sheet_h_cells)
    for y in range(sheet_h_cells):
        for x in range(sheet_w_cells):
            detail_layer.data[y][x] = (0, (0, 0, 0), MAGIC_PINK)
    xp_file.layers.append(detail_layer)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    xp_file.save(str(output_path))


def _native_to_xp(
    grid: list,
    output_path: Path,
    total_cols: int,
    angles: int,
    frames_list: list,
) -> None:
    """Write native xp_cells directly to an XP file (lossless).

    Each grid cell's xp_cells contains per-layer (glyph, fg_rgb, bg_rgb) tuples.
    This bypasses the composite image → reverse render path entirely.
    """
    from scripts.pipeline.xp_core import XPFile, XPLayer

    MAGIC_PINK = (255, 0, 255)

    # Determine max layer count from cell data
    max_layers = 0
    for cell in grid:
        if cell.xp_cells:
            max_layers = max(max_layers, len(cell.xp_cells))
    max_layers = max(max_layers, 3)  # Minimum 3 layers per XP spec

    xp_file = XPFile()
    xp_file.version = -1

    # Build all layers from native cell data
    for layer_idx in range(max_layers):
        layer = XPLayer(total_cols, angles)
        # Fill with defaults
        for y in range(angles):
            for x in range(total_cols):
                if layer_idx == 0:
                    layer.data[y][x] = (32, (0, 0, 0), (0, 0, 0))
                elif layer_idx == 1:
                    layer.data[y][x] = (ord('9'), (200, 200, 200), (0, 0, 0))
                else:
                    layer.data[y][x] = (0, (0, 0, 0), MAGIC_PINK)
        xp_file.layers.append(layer)

    # Populate from grid cell native data
    for idx, cell in enumerate(grid):
        if not cell.xp_cells:
            continue
        row = idx // total_cols
        col = idx % total_cols
        if row >= angles or col >= total_cols:
            continue
        for layer_idx, cell_data in enumerate(cell.xp_cells):
            if layer_idx < max_layers:
                xp_file.layers[layer_idx].data[row][col] = cell_data

    # Write metadata to layer 0
    meta_layer = xp_file.layers[0]
    meta_layer.data[0][0] = (
        _encode_digit(angles), (255, 255, 255), (0, 0, 0)
    )
    for i, count in enumerate(frames_list):
        if i + 1 < total_cols:
            meta_layer.data[0][i + 1] = (
                _encode_digit(count), (255, 255, 255), (0, 0, 0)
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    xp_file.save(str(output_path))


def extract_sprites_from_source(image: Image.Image,
                                alpha_threshold: int = 128,
                                min_size: int = 16,
                                bg_color: Optional[str] = None) -> dict:
    """Extract sprite bounding boxes from a source image.

    Uses alpha channel or background color detection to find
    contiguous sprite regions.

    Args:
        image: Source PIL Image.
        alpha_threshold: Alpha value below which pixels are background.
        min_size: Minimum sprite dimension (width or height).
        bg_color: Optional hex color to treat as background.

    Returns:
        Dict with sprites list and detection metadata.
    """
    from scripts.pipeline.sprite_extract import extract_sprites

    parsed_bg = None
    if bg_color:
        token = str(bg_color).strip().lstrip("#")
        if len(token) == 6:
            parsed_bg = (
                int(token[0:2], 16),
                int(token[2:4], 16),
                int(token[4:6], 16),
            )

    extracted = extract_sprites(
        image,
        mode="bbox",
        alpha_threshold=int(alpha_threshold),
        bg_color=parsed_bg,
        min_size=int(min_size),
        color_tolerance=30.0,
    )
    sprites = [
        {
            "bbox": [sprite.bbox[0], sprite.bbox[1], sprite.bbox[2], sprite.bbox[3]],
            "width": sprite.bbox[2],
            "height": sprite.bbox[3],
        }
        for sprite in extracted
    ]
    return {
        "sprites": sprites,
        "method": "sprite_extract_bbox",
        "filtered_count": 0,
    }


# ============================================================================
# Session registry (module-level, in-memory)
# ============================================================================

_sessions: dict[str, WorkbenchSession] = {}


def create_session(angles: int, frames: list, projs: int,
                   cell_w: int, cell_h: int) -> WorkbenchSession:
    """Create a new workbench session."""
    job_id = str(uuid.uuid4())
    session = WorkbenchSession(
        job_id=job_id, angles=angles, frames=frames,
        projs=projs, cell_w=cell_w, cell_h=cell_h,
    )
    _sessions[job_id] = session
    return session


def get_session(job_id: str) -> Optional[WorkbenchSession]:
    """Retrieve a session by job_id, loading from disk if needed."""
    session = _sessions.get(job_id)
    if session is not None:
        return session
    # Try loading from disk
    return load_session(job_id)


# ---- Session persistence ----

import json as _json
import time as _time

_SESSIONS_DIR = Path("staging/workbench_sessions")
_last_save_time: dict[str, float] = {}
_SAVE_DEBOUNCE_SECS = 2.0


def save_session(session: WorkbenchSession) -> Path:
    """Persist a session to disk as JSON."""
    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = _SESSIONS_DIR / f"{session.job_id}.json"
    path.write_text(_json.dumps(session.to_json()), encoding="utf-8")
    _last_save_time[session.job_id] = _time.time()
    return path


def save_session_debounced(session: WorkbenchSession) -> Optional[Path]:
    """Save session with debounce (at most once per 2 seconds)."""
    last = _last_save_time.get(session.job_id, 0)
    if _time.time() - last < _SAVE_DEBOUNCE_SECS:
        return None
    return save_session(session)


def load_session(job_id: str) -> Optional[WorkbenchSession]:
    """Load a session from disk if it exists."""
    path = _SESSIONS_DIR / f"{job_id}.json"
    if not path.exists():
        return None
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
        session = WorkbenchSession.from_json(data)
        _sessions[job_id] = session
        return session
    except Exception:
        return None


def list_sessions() -> list[dict]:
    """List all saved sessions with metadata."""
    if not _SESSIONS_DIR.exists():
        return []
    results = []
    for p in sorted(_SESSIONS_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            data = _json.loads(p.read_text(encoding="utf-8"))
            results.append({
                "job_id": data.get("job_id", p.stem),
                "angles": data.get("angles", 0),
                "frames_list": data.get("frames_list", []),
                "cell_count": len(data.get("cells", [])),
                "has_native_cells": any("xp_cells" in c for c in data.get("cells", [])),
                "last_modified": p.stat().st_mtime,
            })
        except Exception:
            continue
    return results


def delete_session(job_id: str) -> bool:
    """Delete a saved session from disk and memory."""
    _sessions.pop(job_id, None)
    path = _SESSIONS_DIR / f"{job_id}.json"
    if path.exists():
        path.unlink()
        return True
    return False
