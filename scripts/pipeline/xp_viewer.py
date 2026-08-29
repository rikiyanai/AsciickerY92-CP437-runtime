"""
xp_viewer.py -- Engine-aligned XP sprite viewer with exact ASCIIID frame selection logic.

[DATA-CONTRACT:XP] [DEPENDENCY:PIL] [ENGINE-ALIGN:ASCIIID]

ARCHITECTURE:
    This module provides preview rendering that EXACTLY matches the ASCIIID editor's
    SpriteWidget (asciiid.cpp:6451-6492). The frame selection, metadata parsing, and
    atlas indexing are direct ports from the C++ engine to ensure visual parity.

    KEY DIFFERENCE FROM xp_core.py get_metadata():
    - When angles > 0, the engine sets projs = 2 (projection + reflection)
    - Grid is: fr_num_x = projs * sum(anim_lengths), fr_num_y = angles
    - Frame index uses: frame_idx[(refl * angles + angle) * anim.length + frame]

    This module should be used as the visual oracle for testing PNG->XP pipeline output.

PORTED FROM:
    - Frame selection: asciiid.cpp:6451-6492 (SpritePrefs, animation timing, angle calc)
    - Metadata parsing: sprite.cpp:780-875 (Layer 0 digit decoding, projs/angles/anims)
    - Atlas indexing: sprite.cpp:1129-1149 (frame_idx population and lookup)

USAGE:
    from scripts.pipeline.xp_viewer import XPViewer

    viewer = XPViewer("assets/sprites/player-nude.xp")
    print(viewer.meta)  # {'angles': 8, 'projs': 2, 'anims': [1, 8], ...}

    # Get frame for anim=1, frame_in_anim=3, yaw=90
    img = viewer.render_frame(anim=1, frame=3, yaw=90.0)
    img.save("preview.png")

Tags: [ENGINE-ALIGN:ASCIIID] [DATA-CONTRACT:XP] [DATA-CONTRACT:SPRITE]
"""

import math
import sys
from PIL import Image, ImageDraw
from typing import Optional, Tuple, List, Dict, Any

# [DATA-CONTRACT:XP] Import core XP file handling
try:
    from scripts.pipeline.xp_core import XPFile, XPLayer
except ModuleNotFoundError:
    from xp_core import XPFile, XPLayer

# [ENGINE-ALIGN] Swoosh/transparency color constants matching sprite_constants.h
_CYAN = (0, 255, 255)       # SPRITE_CYAN — swoosh marker in fg
_MAGENTA = (255, 0, 255)    # REXPaint native transparency in bk

# [ENGINE-ALIGN] Half-block glyph codes (sprite.cpp:1232-1243)
_HALF_BLOCK_GLYPHS = {220, 221, 222, 223}  # lower, left, right, upper

# Lighten step per palette level (51 = 255/5, one step in 6-level xterm cube)
_LIGHTEN_STEP = 51


class XPViewer:
    """Engine-aligned XP sprite viewer with exact ASCIIID frame selection.

    This class provides preview rendering that matches the ASCIIID editor's
    SpriteWidget display. All frame selection and metadata parsing logic is
    ported directly from the C++ codebase.

    Attributes:
        xp (XPFile): The loaded XP file.
        meta (dict): Engine-aligned metadata including projs=2 for multi-angle.
        fr_width (int): Frame width in cells.
        fr_height (int): Frame height in cells.
        fr_num_x (int): Total columns in atlas (projs * anim_sum).
        fr_num_y (int): Total rows in atlas (angles).
        frame_idx (list): 2D list [anim_idx][composite_idx] -> atlas flat index.
    """

    def __init__(self, filepath: str):
        """Load XP file and parse engine-aligned metadata.

        Args:
            filepath: Path to the .xp sprite file.
        """
        self.filepath = filepath
        self.xp = XPFile(filepath)
        self.meta = self._parse_metadata_engine_aligned()
        self._build_frame_index()

    def _parse_metadata_engine_aligned(self) -> Dict[str, Any]:
        """Parse Layer 0 metadata exactly as sprite.cpp does.

        [ENGINE-ALIGN:ASCIIID] Direct port of sprite.cpp:780-875.

        Critical difference from xp_core.py get_metadata():
        - If angles > 0: projs = 2 (not 1!)
        - Grid dimensions account for projection/reflection pairs

        Returns:
            dict with: angles, projs, anims (list), anim_sum, fr_num_x, fr_num_y,
                       fr_width, fr_height
        """
        if not self.xp.layers:
            return None

        l0 = self.xp.layers[0]
        width = l0.width
        height = l0.height

        # [ENGINE-ALIGN] Port of sprite.cpp AnsiCell::GetDigit()
        def get_digit(cell: Tuple) -> int:
            """Decode digit glyph to integer, matching C++ GetDigit()."""
            glyph = cell[0]
            if 48 <= glyph <= 57:  # '0'-'9'
                return glyph - 48
            if 65 <= glyph <= 90:  # 'A'-'Z'
                return glyph + 10 - 65
            if 97 <= glyph <= 122:  # 'a'-'z'
                return glyph + 10 - 97
            return -1

        # [ENGINE-ALIGN] sprite.cpp:805-806
        # Read angles from layer0[0] (col=0, row=0)
        # In column-major flat buffer: layer0[0] = cell at (x=0, y=0)
        # In our row-major data[y][x]: data[0][0]
        raw_angles = get_digit(l0.data[0][0])

        # [ENGINE-ALIGN] sprite.cpp:806-808
        # CRITICAL: if angles > 0, projs = 2 (projection + reflection)
        # This is what xp_core.py gets WRONG by hardcoding projs=1
        if raw_angles > 0:
            projs = 2
            angles = raw_angles

            # [ENGINE-ALIGN] sprite.cpp:809-822
            # Scan animation lengths from layer0[height*a] for a=1..
            # In column-major: flat[a * height + 0] = cell at (col=a, row=0)
            # In row-major data[y][x]: data[0][a]
            anim_sum = 0
            anim_len = []
            for a in range(1, width):
                length = get_digit(l0.data[0][a])
                if length > 0:
                    anim_sum += length
                    anim_len.append(length)
                else:
                    break

            if not anim_len:
                anim_len = [1]
                anim_sum = 1
        else:
            # [ENGINE-ALIGN] sprite.cpp:830-854
            # Single angle mode: angles=1, projs=1
            angles = 1
            projs = 1

            anim_sum = 0
            anim_len = []
            for a in range(1, width):
                length = get_digit(l0.data[0][a])
                if length > 0:
                    anim_sum += length
                    anim_len.append(length)
                else:
                    break

            if not anim_len:
                anim_len = [1]
                anim_sum = 1

        # [ENGINE-ALIGN] sprite.cpp:860-861
        # Grid dimensions
        fr_num_x = projs * anim_sum
        fr_num_y = angles

        # [ENGINE-ALIGN] sprite.cpp:884-885
        # Frame dimensions (use Layer 2 or Layer 1 dimensions)
        # C++ uses the merged layer0+1+2 dimensions which match layer 2
        if len(self.xp.layers) >= 3:
            visual_layer = self.xp.layers[2]
        else:
            visual_layer = self.xp.layers[1] if len(self.xp.layers) > 1 else l0

        visual_width = visual_layer.width
        visual_height = visual_layer.height

        # Validate divisibility — FL-3670: route to stderr so warnings don't
        # pollute launcher stdout when asciicker.xp is loaded as a banner fallback.
        if fr_num_x > 0 and visual_width % fr_num_x != 0:
            print(f"[WARN] width {visual_width} not divisible by fr_num_x {fr_num_x}", file=sys.stderr)
        if fr_num_y > 0 and visual_height % fr_num_y != 0:
            print(f"[WARN] height {visual_height} not divisible by fr_num_y {fr_num_y}", file=sys.stderr)

        fr_width = visual_width // fr_num_x if fr_num_x > 0 else visual_width
        fr_height = visual_height // fr_num_y if fr_num_y > 0 else visual_height

        return {
            "angles": angles,
            "projs": projs,
            "anims": anim_len,
            "anim_sum": anim_sum,
            "fr_num_x": fr_num_x,
            "fr_num_y": fr_num_y,
            "fr_width": fr_width,
            "fr_height": fr_height,
            "visual_width": visual_width,
            "visual_height": visual_height,
        }

    def _build_frame_index(self) -> None:
        """Build frame_idx lookup exactly as sprite.cpp does.

        [ENGINE-ALIGN] Direct port of sprite.cpp:1129-1149.

        The C++ code builds frame_idx[anim][(refl * angles + angle) * anim.length + frame]
        which maps (anim, refl, angle, frame) to atlas flat index.

        We build a Python structure: frame_idx[anim_idx][(refl, angle, frame)] = atlas_idx
        """
        if not self.meta:
            self.frame_idx = []
            return

        angles = self.meta["angles"]
        projs = self.meta["projs"]
        anims = self.meta["anims"]
        fr_num_x = self.meta["fr_num_x"]

        # frame_idx[anim_idx] = dict mapping (refl, angle, frame) -> atlas flat index
        self.frame_idx = []

        for anim_idx, anim_length in enumerate(anims):
            anim_map = {}
            self.frame_idx.append(anim_map)

        # [ENGINE-ALIGN] sprite.cpp:1132-1149
        # For each reflection mode (0=proj, 1=refl)
        for refl in range(2):
            # rx = offset for reflection half (0 for proj, half of fr_num_x for refl)
            rx = refl * fr_num_x // 2

            for angle in range(angles):
                x = rx
                y = angle

                for anim_idx, anim_length in enumerate(anims):
                    for frame in range(anim_length):
                        # Atlas flat index
                        idx = x + y * fr_num_x

                        # Store in our lookup
                        self.frame_idx[anim_idx][(refl, angle, frame)] = idx

                        x += 1

    def _get_color_key(self) -> Tuple[int, int, int]:
        """Return L0's background color at (0,0) — the engine's transparency color key.

        [ENGINE-ALIGN] sprite.cpp:1590 — bk_transp test: bk == layer0[cell].bk
        Any L2 cell whose bg matches this color is transparent, same as magenta.
        Defaults to magenta when L0 is absent (safe fallback).
        """
        if not self.xp.layers:
            return _MAGENTA
        l0 = self.xp.layers[0]
        return l0.data[0][0][2]  # data[row=0][col=0] = (glyph, fg, bg)

    def _is_transparent(self, bg: Tuple, color_key: Tuple) -> bool:
        """True when a cell's background should be rendered as transparent.

        [ENGINE-ALIGN] sprite.cpp:1590-1601 — two paths:
        1. bg == layer0.bk  (L0 color-key transparency)
        2. bg == magenta    (REXPaint native transparency)
        """
        return bg == color_key or bg == _MAGENTA

    def _lighten(self, c: int) -> int:
        """Lighten one RGB component by one palette step.

        [ENGINE-ALIGN] sprite.cpp:1341 — LightenColor adds SPRITE_LIGHTEN_AMOUNT (=51).
        """
        return min(255, c + _LIGHTEN_STEP)

    def _build_composite_cells(
        self, color_key: Tuple
    ) -> List[List[Tuple]]:
        """Build a composited cell grid from L2 + last-layer swoosh merge.

        [ENGINE-ALIGN] sprite.cpp:1191-1353 — applies layers 3..N onto L2.
        The engine only acts on the LAST layer (m == layers-1) and only for
        cells where fg == cyan. All intermediate layers 3..N-2 are iterated
        but not consumed (their cells have no effect on L2 in the C++ loop).

        Swoosh rules ported here (simplified — no half-block averaging):
          glyph=null/space + non-magenta bk → overwrite L2 cell entirely
          half-block (220-223) → lighten both fg and bg of the L2 cell
          other glyphs → lighten fg and bg of the L2 cell

        The half-block *averaging* path in C++ requires palette-index math
        (AverageGlyphTransp) which is not ported here. The lightening approximation
        is visually close for non-transparent underlying cells.

        Returns:
            2D list [y][x] of (glyph, fg_rgb, bg_rgb) composited cells.
            Cells where bg should be transparent have bg == color_key.
        """
        if not self.xp.layers or len(self.xp.layers) < 3:
            if self.xp.layers:
                l = self.xp.layers[-1]
                return [[(c[0], tuple(c[1]), tuple(c[2]))
                         for x, c in enumerate(row)]
                        for row in [l.data[y] for y in range(l.height)]]
            return [[]]

        l2 = self.xp.layers[2]
        h, w = l2.height, l2.width

        def _norm(cell):
            """Normalize a cell to (glyph, fg_tuple, bg_tuple)."""
            g, fg, bg = cell
            return (g, tuple(fg), tuple(bg))

        # Start with a mutable copy of L2 — each entry is [glyph, [r,g,b], [r,g,b]]
        composite = [
            [list(l2.data[y][x]) for x in range(w)]
            for y in range(h)
        ]

        n_layers = len(self.xp.layers)
        if n_layers <= 3:
            # No overlay layers — return L2 as-is
            return [[_norm(composite[y][x]) for x in range(w)] for y in range(h)]

        # [ENGINE-ALIGN] Only the LAST layer is processed for swoosh.
        # Layers 3..N-2 are read in C++ but their cells are never written to L2.
        last_layer = self.xp.layers[n_layers - 1]

        for y in range(min(h, last_layer.height)):
            for x in range(min(w, last_layer.width)):
                lgl, lfg, lbg = last_layer.data[y][x]

                # [ENGINE-ALIGN] Only cyan-fg cells on the last layer trigger swoosh
                if tuple(lfg) != _CYAN:
                    continue

                bgl, bfg_raw, bbg_raw = composite[y][x]
                bfg = tuple(bfg_raw)
                bbg = tuple(bbg_raw)

                bk_transp = self._is_transparent(bbg, color_key)
                fg_transp = self._is_transparent(bfg, color_key)

                if lgl in (0, 32):
                    # [ENGINE-ALIGN] null/space with non-magenta swoosh bk → overwrite
                    if tuple(lbg) != _MAGENTA:
                        composite[y][x] = [lgl, list(lfg), list(lbg)]
                elif lgl in _HALF_BLOCK_GLYPHS:
                    # [ENGINE-ALIGN] half-block: lighten fg and bg
                    # (simplified — C++ does AverageGlyphTransp which needs palette)
                    new_fg = tuple(self._lighten(c) for c in bfg) if not fg_transp else bfg
                    new_bg = tuple(self._lighten(c) for c in bbg) if not bk_transp else bbg
                    composite[y][x] = [lgl, list(new_fg), list(new_bg)]
                else:
                    # [ENGINE-ALIGN] full-block: lighten non-transparent components
                    if fg_transp and bk_transp:
                        composite[y][x] = [lgl, list(lfg), list(lbg)]
                    else:
                        new_fg = (tuple(self._lighten(c) for c in bfg)
                                  if not fg_transp else bfg)
                        new_bg = (tuple(self._lighten(c) for c in bbg)
                                  if not bk_transp else bbg)
                        composite[y][x] = [bgl, list(new_fg), list(new_bg)]

        return [[_norm(composite[y][x]) for x in range(w)] for y in range(h)]

    def select_preview_frame(
        self,
        anim: int = 0,
        frame: int = 0,
        yaw: float = 0.0,
        rot_yaw: float = 0.0,
        proj: int = 0,
        timing: Optional[Tuple[int, int, int, int]] = None,
        time_ms: Optional[int] = None
    ) -> Tuple[int, int, int]:
        """Select frame index exactly as ASCIIID SpriteWidget does.

        [ENGINE-ALIGN] Direct port of asciiid.cpp:6451-6492.

        Args:
            anim: Animation index (clamped to [0, num_anims-1]).
            frame: Frame index within animation (used if timing disabled).
            yaw: Sprite facing direction in degrees.
            rot_yaw: Camera rotation in degrees (subtracted from yaw).
            proj: Projection mode (0=projection, 1=reflection).
            timing: Optional 4-tuple (t0, t1, t2, t3) for animation timing.
                    If None or all zeros, uses simple frame % length.
            time_ms: Current time in milliseconds (for timing-based selection).

        Returns:
            Tuple of (atlas_flat_idx, angle, frame_in_anim)
        """
        if not self.meta:
            return (0, 0, 0)

        angles = self.meta["angles"]
        projs = self.meta["projs"]
        anims = self.meta["anims"]

        # [ENGINE-ALIGN] asciiid.cpp:6451-6453
        # Clamp anim to valid range
        if anim < 0 or anim >= len(anims):
            anim = 0

        anim_length = anims[anim]

        # [ENGINE-ALIGN] asciiid.cpp:6455-6479
        # Frame selection with optional timing
        if timing is None:
            timing = (0, 0, 0, 0)

        t0, t1, t2, t3 = timing
        total_len = t0 + t1 * anim_length + t2 + t3 * anim_length

        if total_len <= 0:
            # Simple mode: frame = input % length
            selected_frame = frame % anim_length
        else:
            # Timing mode: 4-segment animation
            if time_ms is None:
                time_ms = 0

            # [ENGINE-ALIGN] asciiid.cpp:6465 - time at 61.035 FPS (>> 14)
            # We accept time_ms directly for flexibility
            time = time_ms % total_len

            if time < t0:
                selected_frame = 0
            elif time < t0 + t1 * anim_length:
                selected_frame = (time - t0) // t1
            elif time < t0 + t1 * anim_length + t2:
                selected_frame = anim_length - 1
            else:
                selected_frame = anim_length - 1 - (time - t0 - t1 * anim_length - t2) // t3

        # Clamp frame to valid range
        if selected_frame < 0:
            selected_frame = 0
        if selected_frame >= anim_length:
            selected_frame = anim_length - 1

        # [ENGINE-ALIGN] asciiid.cpp:6485-6487
        # Angle selection
        angle = (yaw - rot_yaw) * angles / 360.0 + 0.5
        angle = int(math.floor(angle))

        # Handle negative modulo
        if angle >= 0:
            angle = angle % angles
        else:
            angle = (angle % angles + angles) % angles

        # [ENGINE-ALIGN] asciiid.cpp:6489-6492
        # Projection offset
        refl = proj if projs > 1 else 0

        # Look up in frame_idx
        if anim < len(self.frame_idx) and (refl, angle, selected_frame) in self.frame_idx[anim]:
            atlas_idx = self.frame_idx[anim][(refl, angle, selected_frame)]
        else:
            # Fallback: manual calculation
            atlas_idx = self._calculate_atlas_idx(anim, refl, angle, selected_frame)

        return (atlas_idx, angle, selected_frame)

    def _calculate_atlas_idx(self, anim: int, refl: int, angle: int, frame: int) -> int:
        """Calculate atlas flat index manually (fallback).

        [ENGINE-ALIGN] Uses the same formula as sprite.cpp:1143.

        Args:
            anim: Animation index.
            refl: Reflection mode (0=proj, 1=refl).
            angle: View angle index.
            frame: Frame within animation.

        Returns:
            Flat index into the atlas.
        """
        if not self.meta:
            return 0

        fr_num_x = self.meta["fr_num_x"]
        angles = self.meta["angles"]
        anims = self.meta["anims"]

        # Calculate x position
        rx = refl * fr_num_x // 2
        x = rx

        # Add frames from previous animations
        for i in range(anim):
            x += anims[i]

        # Add current frame
        x += frame

        # y is just the angle
        y = angle

        return x + y * fr_num_x

    def get_frame_rect(self, atlas_idx: int) -> Tuple[int, int, int, int]:
        """Get the pixel rectangle for an atlas frame.

        Args:
            atlas_idx: Flat index into the atlas.

        Returns:
            Tuple (x0, y0, x1, y1) in cells (not pixels).
        """
        if not self.meta:
            return (0, 0, 0, 0)

        fr_num_x = self.meta["fr_num_x"]
        fr_width = self.meta["fr_width"]
        fr_height = self.meta["fr_height"]

        # Convert flat index to grid position
        fr_x = atlas_idx % fr_num_x
        fr_y = atlas_idx // fr_num_x

        x0 = fr_x * fr_width
        y0 = fr_y * fr_height
        x1 = x0 + fr_width
        y1 = y0 + fr_height

        return (x0, y0, x1, y1)

    def render_frame(
        self,
        anim: int = 0,
        frame: int = 0,
        yaw: float = 0.0,
        rot_yaw: float = 0.0,
        proj: int = 0,
        font_path: Optional[str] = None,
        char_w: int = 12,
        char_h: int = 12,
        scale: int = 1
    ) -> Image.Image:
        """Render a single frame to a PIL Image.

        [ENGINE-ALIGN] Uses Layer 2 (visual layer) exactly as ASCIIID does.
        Respects magenta bg (255, 0, 255) as transparent.

        Args:
            anim: Animation index.
            frame: Frame index within animation.
            yaw: Sprite facing direction.
            rot_yaw: Camera rotation.
            proj: Projection mode (0=proj, 1=refl).
            font_path: Path to CP437 font sprite sheet (optional).
            char_w: Character width in pixels.
            char_h: Character height in pixels.
            scale: Output scale factor.

        Returns:
            PIL Image of the rendered frame.
        """
        if not self.meta or len(self.xp.layers) < 3:
            return Image.new("RGBA", (char_w, char_h), (0, 0, 0, 255))

        # Select frame
        atlas_idx, angle, sel_frame = self.select_preview_frame(
            anim=anim, frame=frame, yaw=yaw, rot_yaw=rot_yaw, proj=proj
        )

        # Get frame rectangle in cells
        x0, y0, x1, y1 = self.get_frame_rect(atlas_idx)
        fr_width = x1 - x0
        fr_height = y1 - y0

        # [FL-2814] Build L2 + last-layer swoosh composite with correct transparency.
        # sprite.cpp applies L3+ merge at load time; we replicate that here.
        color_key = self._get_color_key()
        composite = self._build_composite_cells(color_key)
        comp_h = len(composite)
        comp_w = len(composite[0]) if comp_h else 0

        # Create output image
        img_w = fr_width * char_w * scale
        img_h = fr_height * char_h * scale
        img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Load font if provided
        font = None
        if font_path:
            try:
                font = self._load_font(font_path, char_w, char_h)
            except Exception as e:
                print(f"[WARN] Failed to load font: {e}")

        # Render cells
        for cy in range(fr_height):
            for cx in range(fr_width):
                gx = x0 + cx
                gy = y0 + cy

                if gy >= comp_h or gx >= comp_w:
                    continue

                glyph, fg, bg = composite[gy][gx]

                # Calculate pixel position
                px = cx * char_w * scale
                py = cy * char_h * scale

                # [FL-2814] Full transparency: L0 color key AND magenta
                if self._is_transparent(bg, color_key):
                    continue

                # Draw background
                draw.rectangle(
                    [px, py, px + char_w * scale, py + char_h * scale],
                    fill=bg
                )

                # Draw glyph (if not null/space)
                if glyph != 0 and glyph != 32:
                    if font and glyph in font:
                        glyph_img = self._render_glyph(font[glyph], fg, char_w, char_h, scale)
                        img.alpha_composite(glyph_img, (px, py))

        return img

    def _load_font(self, font_path: str, char_w: int, char_h: int) -> Dict[int, Image.Image]:
        """Load a CP437 font sprite sheet.

        Delegates to _render_core.load_font_atlas() for shared implementation.
        """
        from scripts.pipeline._render_core import load_font_atlas
        return load_font_atlas(font_path, char_w=char_w, char_h=char_h)

    def _render_glyph(
        self,
        glyph_img: Image.Image,
        fg: Tuple[int, int, int],
        char_w: int,
        char_h: int,
        scale: int
    ) -> Image.Image:
        """Render a glyph with foreground color.

        Uses the glyph's red channel as alpha mask.
        Kept as instance method for API compatibility with render_frame/render_all_frames.
        """
        if glyph_img.mode != 'RGBA':
            glyph_img = glyph_img.convert('RGBA')

        colored = Image.new("RGBA", glyph_img.size, fg + (255,))
        r, g, b, a = glyph_img.split()
        colored.putalpha(r)

        if scale > 1:
            new_size = (char_w * scale, char_h * scale)
            colored = colored.resize(new_size, Image.NEAREST)

        return colored

    def render_all_frames(
        self,
        font_path: Optional[str] = None,
        char_w: int = 12,
        char_h: int = 12,
        scale: int = 1
    ) -> Image.Image:
        """Render the entire sprite sheet with all frames.

        Useful for comparison with the raw XP rendering.

        Returns:
            PIL Image of the entire sheet.
        """
        if not self.meta or len(self.xp.layers) < 3:
            return Image.new("RGBA", (1, 1), (0, 0, 0, 255))

        # [FL-2814] Build L2 + last-layer swoosh composite with correct transparency.
        color_key = self._get_color_key()
        composite = self._build_composite_cells(color_key)
        h = len(composite)
        w = len(composite[0]) if h else 0

        img = Image.new("RGBA", (w * char_w * scale, h * char_h * scale), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        font = None
        if font_path:
            try:
                font = self._load_font(font_path, char_w, char_h)
            except Exception:
                pass

        for cy in range(h):
            for cx in range(w):
                glyph, fg, bg = composite[cy][cx]

                px = cx * char_w * scale
                py = cy * char_h * scale

                # [FL-2814] Full transparency: L0 color key AND magenta
                if self._is_transparent(bg, color_key):
                    continue

                draw.rectangle(
                    [px, py, px + char_w * scale, py + char_h * scale],
                    fill=bg
                )

                if glyph != 0 and glyph != 32 and font and glyph in font:
                    glyph_img = self._render_glyph(font[glyph], fg, char_w, char_h, scale)
                    img.alpha_composite(glyph_img, (px, py))

        return img

    def debug_info(self) -> str:
        """Return debug information about the loaded sprite.

        Returns:
            Multi-line string with metadata and frame index details.
        """
        lines = [
            f"File: {self.filepath}",
            f"Layers: {len(self.xp.layers)}",
            "",
            "Engine-Aligned Metadata:",
        ]

        if self.meta:
            for k, v in self.meta.items():
                lines.append(f"  {k}: {v}")
        else:
            lines.append("  (no metadata)")

        lines.append("")
        lines.append("Frame Index Sample (first 5 per anim):")

        for anim_idx, anim_map in enumerate(self.frame_idx[:3]):  # First 3 anims
            lines.append(f"  Anim {anim_idx}:")
            entries = list(anim_map.items())[:5]
            for (refl, angle, frame), idx in entries:
                lines.append(f"    (refl={refl}, angle={angle}, frame={frame}) -> atlas[{idx}]")

        return "\n".join(lines)


# ---- CLI Entry Point ----

if __name__ == "__main__":
    import sys
    import os

    if len(sys.argv) < 2:
        print("Usage: python xp_viewer.py <sprite.xp> [--render] [--sheet] [--anim N] [--frame N] [--yaw N]")
        sys.exit(1)

    filepath = sys.argv[1]
    do_render = "--render" in sys.argv
    do_sheet = "--sheet" in sys.argv

    # Parse optional args
    anim = 0
    frame = 0
    yaw = 0.0

    for i, arg in enumerate(sys.argv):
        if arg == "--anim" and i + 1 < len(sys.argv):
            anim = int(sys.argv[i + 1])
        if arg == "--frame" and i + 1 < len(sys.argv):
            frame = int(sys.argv[i + 1])
        if arg == "--yaw" and i + 1 < len(sys.argv):
            yaw = float(sys.argv[i + 1])

    viewer = XPViewer(filepath)
    print(viewer.debug_info())

    if do_render or do_sheet:
        # Find font
        script_dir = os.path.dirname(os.path.abspath(__file__))
        font_paths = [
            os.path.join(script_dir, "../../assets/fonts/cp437_12x12.png"),
            os.path.join(script_dir, "../assets/fonts/cp437_12x12.png"),
            "assets/fonts/cp437_12x12.png",
        ]

        font_path = None
        for p in font_paths:
            if os.path.exists(p):
                font_path = p
                break

        if do_sheet:
            img = viewer.render_all_frames(font_path=font_path, scale=2)
            out_path = "xp_viewer_sheet.png"
            img.save(out_path)
            print(f"Saved full sprite sheet to {out_path}")
        else:
            atlas_idx, angle, sel_frame = viewer.select_preview_frame(anim=anim, frame=frame, yaw=yaw)
            print(f"\nSelected: atlas_idx={atlas_idx}, angle={angle}, frame={sel_frame}")

            img = viewer.render_frame(
                anim=anim, frame=frame, yaw=yaw,
                font_path=font_path, scale=2
            )

            out_path = "xp_viewer_preview.png"
            img.save(out_path)
            print(f"Saved preview to {out_path}")
