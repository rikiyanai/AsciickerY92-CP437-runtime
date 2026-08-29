"""
xp_core.py -- .xp binary format reader/writer for Asciicker sprites

ARCHITECTURE:
    Core I/O module for the .xp sprite format. Every pipeline output flows through
    this module. Handles gzip compression, column-major cell storage, and multi-layer
    sprite data.

    This module is the canonical Python implementation of the REXPaint .xp file format
    used throughout the Asciicker asset pipeline. It provides low-level load/save of
    gzip-compressed .xp files and metadata extraction from Layer 0 (the engine's
    sprite atlas control layer).

    XPFile and XPLayer are the two exported classes. Nearly every pipeline stage
    depends on them: assembler.py writes .xp via XPFile.save(), validator.py reads
    metadata via XPFile.get_metadata(), xp_tool.py renders layers for editing,
    and the MCP server (xp_mcp_server.py) bridges them to external tools.

    Binary format: gzip( version_i32 | layer_count_u32 | layers[] )
    Layer format:  width_i32 | height_i32 | cells[width][height]
    Cell format:   glyph_u32 | fg_r | fg_g | fg_b | bg_r | bg_g | bg_b (10 bytes)

    Storage order: Column-major (iterate x, then y) for REXPaint compatibility.

BINARY FORMAT SPECIFICATION (REXPaint v1.02):
    .xp files are gzip-compressed binary blobs with the following structure:

    WHY gzip: REXPaint's native format mandates gzip compression. The C++ engine
    (sprite.cpp) and REXPaint editor both expect it. Uncompressed .xp files are
    not valid and will be rejected by all consumers. Gzip also provides ~60-80%
    size reduction on typical sprite data due to repetitive cell patterns.

    FILE HEADER (8 bytes):
        [0..3]  int32   version     -- Format version; current REXPaint uses -1 (0xFFFFFFFF).
        [4..7]  uint32  layer_count -- Number of layers in the file.

    PER-LAYER HEADER (8 bytes each):
        [0..3]  int32   width       -- Layer width in cells (columns).
        [4..7]  int32   height      -- Layer height in cells (rows).

    PER-LAYER CELL DATA (10 bytes per cell, width*height cells):
        Cells are stored in COLUMN-MAJOR order: the outer loop iterates over x (columns),
        the inner loop iterates over y (rows). So the stream order is:
            (x=0,y=0), (x=0,y=1), ..., (x=0,y=h-1), (x=1,y=0), (x=1,y=1), ...

        WHY column-major: REXPaint's native storage order. The C++ engine (sprite.cpp)
        reads cells into a flat buffer indexed as flat[x * height + y], which is
        column-major. Using the same order on disk avoids a transposition step in the
        engine's hot path. This Python module transposes to row-major (data[y][x]) for
        Pythonic convenience, absorbing the cost at load/save time.

        Each cell is 10 bytes:
            [0..3]  uint32  glyph   -- CP437 codepoint (0-255 used; 4 bytes for alignment).
            [4]     uint8   fg_r    -- Foreground red.
            [5]     uint8   fg_g    -- Foreground green.
            [6]     uint8   fg_b    -- Foreground blue.
            [7]     uint8   bg_r    -- Background red.
            [8]     uint8   bg_g    -- Background green.
            [9]     uint8   bg_b    -- Background blue.

        WHY 4 bytes for glyph: CP437 only needs 1 byte (0-255), but REXPaint uses a
        uint32 for alignment and potential future Unicode support. The struct packing
        '<I' (little-endian unsigned 32-bit) matches this exactly.

        Transparency convention: bg = (255, 0, 255) marks a transparent cell.

    [DATA-CONTRACT:PALETTE] Color data is raw RGB uint8 triples (0-255 per channel).
    No palette indirection or color space conversion is applied at the .xp level.
    Pipeline stages upstream (quantizer.py, color_correction.py) are responsible for
    mapping rendered colors to the target palette before cells reach this module.

ASCIICKER METADATA ENCODING (Layer 0):
    The game engine (sprite.cpp LoadSprite) reads Layer 0 as a flat column-major buffer
    and extracts sprite atlas metadata from specific cells:

        layer0[0]           -> cell (col=0, row=0): angle count (digit glyph).
        layer0[height*a]    -> cell (col=a, row=0): animation 'a' frame count (a=1..).

    WHY single-char encoding: The metadata must fit in existing cell glyphs without
    adding a separate header format. By reusing CP437 digit/letter characters ('0'-'9',
    'A'-'Z'), values 0-35 can be encoded in a single cell glyph. This is compact,
    visually inspectable in REXPaint, and directly parseable by the C++ engine's
    GetDigit() function without any additional decoding infrastructure.

    TODO(PIPELINE-FIX): The digit encoding scheme has no formal specification document.
    It is defined implicitly by sprite.cpp's GetDigit() and duplicated here. Any change
    to the encoding in either location will silently break the other. A shared spec
    (e.g., in docs/ASSET_SPECIFICATION.md) should be the single source of truth.

    The digit encoding maps CP437 glyphs to integers:
        '0'-'9' (48-57)  -> 0-9
        'A'-'Z' (65-90)  -> 10-35
        'a'-'z' (97-122) -> 10-35 (case-insensitive alias)

    If angles > 0, the engine sets projs=2 (projection + reflection).
    If angles == 0 or non-digit, the engine treats it as single-angle (projs=1).

    This module's get_metadata() reads the same cells from its row-major data[y][x]
    representation: data[0][0] for angles, data[0][a] for animation lengths.

KEY EXPORTS:
    XPLayer -- Single layer: width, height, and a row-major 2D grid of (glyph, fg, bg).
    XPFile  -- Multi-layer container with load/save/get_metadata.
    load_xp -- Implicit via XPFile(filename) or XPFile.load(filename).
    save_xp -- Implicit via XPFile.save(filename).

PIPELINE CONTEXT:
    [DATA-CONTRACT:XP]  -- Defines the .xp binary wire format
    [PIPELINE:ASSEMBLE] -- Final output stage of asset generation
    [DATA-CONTRACT:PALETTE] -- Color data handling at cell level

    [PIPELINE:ASSEMBLE] assembler.py -> XPFile.save()  (write final .xp)
    [PIPELINE:PROCESS]  validator.py -> XPFile.get_metadata()  (validate metadata)
    [FLOW:CLI]          xp_tool.py   -> XPFile.load()  (interactive editor)
    [FLOW:MCP]          xp_mcp_server.py -> XPFile load/save  (external tool bridge)

See also:
    - sprite.cpp LoadSprite()       -- C++ engine consumer of .xp files
    - scripts/png2rex-master/       -- C++ reference implementation (REXSpeeder)
    - docs/XP_ANALYSIS.md           -- Format analysis and asset inventory
    - docs/ASSET_SPECIFICATION.md   -- Grid layout and metadata contracts
    - assembler.py                  -- Builds multi-layer .xp from rendered frames
    - pipeline.py                   -- Orchestrates the full asset generation pipeline
    - xp_tool.py                    -- Interactive .xp editor (curses TUI)
    - xp_mcp_server.py              -- MCP server bridge for external tool integration

Tags: [DATA-CONTRACT:XP] [DATA-CONTRACT:CP437] [DATA-CONTRACT:PALETTE] [DEPENDENCY:GZIP]
"""

# [DEPENDENCY:GZIP] WHY gzip: REXPaint normally writes gzip-compressed .xp files.
# The reader also accepts raw upstream Asciicker XP payloads such as shot.xp.
import gzip
from pathlib import Path

# WHY struct: All .xp binary fields use fixed-width little-endian integers.
# struct.pack/unpack with '<i' (int32) and '<I' (uint32) maps directly to
# the on-disk layout without manual byte manipulation.
import struct

# WHY io.BytesIO: Used in save() to accumulate the full binary payload in memory
# before passing it to gzip for single-shot compression. Avoids repeated small
# writes through the gzip compressor, which would hurt compression ratio.
import io

# [DATA-CONTRACT:XP] Import custom validation errors for layer alignment checks.
# WHY try/except: Supports both module invocation (python -m scripts.pipeline...)
# and direct script execution (python scripts/pipeline/debug_sprite.py).
try:
    from scripts.pipeline.sprite_errors import SpriteValidationError
except ModuleNotFoundError:
    from sprite_errors import SpriteValidationError

# [DATA-CONTRACT:XP] Cell tuple format used throughout the pipeline:
#   (glyph: int, fg: tuple[int,int,int], bg: tuple[int,int,int])
# The default "empty" cell is (0, (0,0,0), (0,0,0)).
# Transparent cells use bg = (255, 0, 255) per REXPaint convention.
# [DATA-CONTRACT:PALETTE] fg and bg are raw RGB uint8 triples -- no palette
# indirection at this layer. Upstream stages handle palette mapping.

# [DATA-CONTRACT:XP] Layer 0 background color used as the transparency key
# for non-magenta sprites. Engine sprite.cpp compares each layer-2 cell's bg
# against layer-0's bg at the same position — matching cells get
# SPRITE_TRANSPARENT_INDEX. This constant is the conventional key used by
# body atlases, overlays, and generated reference sheets.
OVERLAY_KEY_RGB: tuple[int, int, int] = (1, 1, 1)

# [DATA-CONTRACT:XP] Legacy monolithic sprites use bright yellow as layer-0 key.
LEGACY_YELLOW_KEY_RGB: tuple[int, int, int] = (255, 255, 85)


def encode_digit(value: int) -> int:
    """Encode integer 0–35 as CP437 digit glyph ('0'–'9', 'A'–'Z').

    Used for layer-0 metadata encoding (angle count, anim lengths, ref offsets).
    """
    if 0 <= value <= 9:
        return ord("0") + value
    if 10 <= value <= 35:
        return ord("A") + (value - 10)
    raise ValueError(f"value out of encodable range [0,35]: {value}")


def rebase_visual_layer_transparency_keys(
    visual_layer: "XPLayer",
    source_key_layer: "XPLayer | None",
    output_key_layer: "XPLayer",
) -> None:
    """Translate transparency-key colors in a visual layer after L0 replacement.

    When a generated XP takes layer 0 from a reference (key A) but visual data
    from a source (key B), cells that matched key B in the source will NOT match
    key A in the output — the engine renders them as literal color instead of
    transparent. This function rewrites those cells so the visual layer's
    transparency-key colors match the output layer 0's key.

    Also catches the legacy yellow (255,255,85) hardcoded fallback — any yellow
    bg/fg is unconditionally rewritten to the output key, since no authored
    sprite intentionally uses bright yellow as a visible color.
    """
    yellow = LEGACY_YELLOW_KEY_RGB
    for y, row in enumerate(visual_layer.data):
        for x, cell in enumerate(row):
            source_key_rgb: tuple[int, int, int] | None = None
            if (
                source_key_layer is not None
                and y < source_key_layer.height
                and x < source_key_layer.width
            ):
                source_key_rgb = tuple(source_key_layer.data[y][x][2])
            if y >= output_key_layer.height or x >= output_key_layer.width:
                continue
            output_key_rgb = tuple(output_key_layer.data[y][x][2])
            glyph, fg, bg = cell
            fg_rgb = tuple(fg)
            bg_rgb = tuple(bg)
            if source_key_rgb is not None and fg_rgb == source_key_rgb:
                fg_rgb = output_key_rgb
            if source_key_rgb is not None and bg_rgb == source_key_rgb:
                bg_rgb = output_key_rgb
            if fg_rgb == yellow:
                fg_rgb = output_key_rgb
            if bg_rgb == yellow:
                bg_rgb = output_key_rgb
            if fg_rgb != tuple(fg) or bg_rgb != tuple(bg):
                visual_layer.data[y][x] = (glyph, fg_rgb, bg_rgb)


class XPLayer:
    """A single layer of an .xp file: a 2D grid of (glyph, fg_rgb, bg_rgb) cells.

    [DATA-CONTRACT:XP] This is the in-memory representation of a single .xp layer.
    The grid is stored row-major as data[y][x] for convenient Python indexing,
    even though the on-disk format is column-major. The load/save methods in
    XPFile handle the transposition transparently.

    Attributes:
        width:  Number of columns (cells).
        height: Number of rows (cells).
        data:   2D list, data[row][col] = (glyph, (fg_r, fg_g, fg_b), (bg_r, bg_g, bg_b)).
    """
    def __init__(self, width, height, data=None):
        """Initialize a layer with the given dimensions and optional cell data.

        Args:
            width:  Number of columns (cells) in this layer.
            height: Number of rows (cells) in this layer.
            data:   Optional 2D list of cell tuples, data[y][x] = (glyph, fg, bg).
                    If None, fills with default (0, 0, 0) sentinel cells.

        [DATA-CONTRACT:XP]
        """
        self.width = width
        self.height = height
        # data is a list of lists of (char, fg, bg) tuples.
        # stored as data[y][x]
        # WHY: data is always populated from load() or explicitly set, empty list is not a valid use case
        if data:
            self.data = data
        else:
            # WHY this format: (glyph, fg_tuple, bg_tuple) matches cell contract.
            # glyph=0 (null), black fg/bg. This is NOT transparent (use magenta bg for that).
            self.data = [[(0, (0, 0, 0), (0, 0, 0)) for _ in range(width)] for _ in range(height)]

# [DATA-CONTRACT:XP] XPFile is the authoritative Python representation of .xp files.
# All pipeline stages that read or write .xp files MUST go through this class
# to ensure consistent column-major <-> row-major transposition and gzip framing.

class XPFile:
    """Multi-layer .xp file container with load, save, and metadata extraction.

    The .xp format is REXPaint's native format: a gzip-compressed binary blob
    containing a version header, layer count, and per-layer cell grids stored
    in column-major order. See module docstring for the full binary spec.

    Typical usage::

        # Read
        xp = XPFile("assets/sprites/player-nude.xp")
        meta = xp.get_metadata()  # {'angles': 8, 'anims': [1, 8]}

        # Write
        xp = XPFile()
        xp.version = -1
        xp.layers.append(some_layer)
        xp.save("output.xp")

    Attributes:
        version: int32 format version (always -1 for current REXPaint).
        layers:  List[XPLayer] in stacking order (0 = bottom / metadata).
    """
    def __init__(self, filename=None):
        """Initialize an XPFile, optionally loading from disk.

        Args:
            filename: Optional path to a .xp file. If provided, the file is
                      loaded immediately via self.load(). If None, creates an
                      empty container ready for programmatic layer construction.

        [DATA-CONTRACT:XP]
        """
        # WHY version = -1: REXPaint v1.02+ uses -1 (0xFFFFFFFF as int32) as the
        # format version marker. All known consumers expect this value.
        self.version = -1
        self.layers = []
        if filename:
            self.load(filename)

    def _validate_for_save(self, filename: str) -> None:
        """Validate XPFile structure before saving.

        [DATA-CONTRACT:XP] Enforces XP_LAYER_SPEC requirements:
        - Minimum 3 layers (Layer 0=colorkey, Layer 1=height, Layer 2=visual)
        - All layers must have positive dimensions (width > 0, height > 0)
        - Glyph values must be in range 0-255

        Raises:
            SpriteValidationError: If validation fails
        """
        # WHY min 3 layers: C++ loader (sprite.cpp) requires Layer 0 (colorkey),
        # Layer 1 (height), Layer 2 (visual). Files with <3 cause undefined behavior.
        if len(self.layers) < 3:
            raise SpriteValidationError(
                filename,
                expected="minimum 3 layers",
                got=len(self.layers),
                message="insufficient layer count"
            )

        # WHY dimension validation: 0x0 layers cause crashes in xp_tool.py render_sheet()
        # and are semantically invalid. All layers must have positive dimensions.
        # Bug fix: Prevents corrupted files like player_idle_walk.xp (layer 3 was 0x0).
        for layer_idx, layer in enumerate(self.layers):
            if layer.width <= 0 or layer.height <= 0:
                raise SpriteValidationError(
                    filename,
                    expected="width > 0 and height > 0",
                    got=f"{layer.width}x{layer.height}",
                    message=f"invalid dimensions at layer {layer_idx}"
                )

        # WHY glyph validation: CP437 uses 0-255. Values >255 stored as uint32
        # in .xp format but C++ expects them in byte range. Invalid glyphs cause
        # rendering artifacts or crashes.
        for layer_idx, layer in enumerate(self.layers):
            for y in range(layer.height):
                for x in range(layer.width):
                    cell = layer.data[y][x]
                    # Handle both correct (glyph, fg, bg) and legacy (0, 0, 0) formats
                    if isinstance(cell, tuple) and len(cell) >= 1:
                        glyph = cell[0] if isinstance(cell[0], int) else 0
                    else:
                        glyph = 0

                    if not (0 <= glyph <= 255):
                        raise SpriteValidationError(
                            filename,
                            expected="glyph 0-255",
                            got=glyph,
                            message=f"invalid glyph at layer {layer_idx} cell ({x},{y})"
                        )

    # [PIPELINE:ASSEMBLE] Entry point for reading existing .xp assets.
    # [DEPENDENCY:GZIP] Most REXPaint files are gzip-decompressed before parsing.
    # Some upstream Asciicker assets are raw XP payloads; the reader accepts those
    # as source data but save() still writes gzip-compressed REXPaint output.
    def load(self, filename):
        """Load an .xp file from disk, decompressing and parsing all layers.

        [DATA-CONTRACT:XP] [PIPELINE:ASSEMBLE]

        Args:
            filename: Path to a gzip-compressed .xp file.

        Raises:
            Exception: On I/O errors or malformed binary data.

        Binary layout parsed (after optional gzip decompression):
            - 4 bytes: version (int32, little-endian)
            - 4 bytes: layer_count (uint32, little-endian)
            - Per layer:
                - 4 bytes: width (int32)
                - 4 bytes: height (int32)
                - width * height * 10 bytes: cell data (column-major)
        """
        # [SEC] Safety limits for untrusted input files
        _MAX_DECOMPRESSED_BYTES = 500 * 1024 * 1024  # 500 MB (local tool, not web-facing)
        _MAX_LAYERS = 64
        _MAX_CELLS_PER_LAYER = 10_000_000  # ~3162x3162 max (local tool)

        print(f"Loading {filename}...")
        try:
            raw = Path(filename).read_bytes()
            if raw.startswith(b"\x1f\x8b"):
                content = gzip.decompress(raw)
                if len(content) > _MAX_DECOMPRESSED_BYTES:
                    raise ValueError(
                        f"XP file exceeds {_MAX_DECOMPRESSED_BYTES // (1024*1024)}MB "
                        f"decompressed size limit: {filename}"
                    )
            else:
                if len(raw) > _MAX_DECOMPRESSED_BYTES:
                    raise ValueError(
                        f"raw XP file exceeds {_MAX_DECOMPRESSED_BYTES // (1024*1024)}MB "
                        f"size limit: {filename}"
                    )
                content = raw

            # [DATA-CONTRACT:XP] Parse the 8-byte file header:
            #   offset 0: version  (int32, little-endian, expected -1)
            #   offset 4: layer_count (uint32, little-endian)
            offset = 0

            if len(content) < 8:
                raise ValueError(f"XP file too small ({len(content)} bytes): {filename}")

            # [DATA-CONTRACT:XP] Version field: signed int32, always -1 for REXPaint v1.02+.
            version = struct.unpack('<i', content[offset:offset+4])[0]
            offset += 4
            self.version = version

            layer_count = struct.unpack('<I', content[offset:offset+4])[0]
            offset += 4

            # [SEC-02] Validate layer count against reasonable maximum
            if layer_count > _MAX_LAYERS:
                raise ValueError(
                    f"XP file claims {layer_count} layers (max {_MAX_LAYERS}): {filename}"
                )
            self.layers = []

            for _ in range(layer_count):
                # [DATA-CONTRACT:XP] Per-layer header: width and height as int32.
                if offset + 8 > len(content):
                    raise ValueError(f"XP file truncated at layer header: {filename}")
                width = struct.unpack('<i', content[offset:offset+4])[0]
                offset += 4
                height = struct.unpack('<i', content[offset:offset+4])[0]
                offset += 4

                # [SEC-02] Validate dimensions are positive and within bounds
                if width <= 0 or height <= 0:
                    raise ValueError(
                        f"Invalid layer dimensions {width}x{height}: {filename}"
                    )
                if width * height > _MAX_CELLS_PER_LAYER:
                    raise ValueError(
                        f"Layer {width}x{height} = {width*height} cells "
                        f"exceeds limit of {_MAX_CELLS_PER_LAYER}: {filename}"
                    )
                expected_bytes = width * height * 10
                if offset + expected_bytes > len(content):
                    raise ValueError(
                        f"XP file truncated: layer needs {expected_bytes} bytes "
                        f"but only {len(content) - offset} remain: {filename}"
                    )

                # Initialize grid with None
                layer_data = [[None for _ in range(width)] for _ in range(height)]

                # WHY: REXPaint stores cells in column-major order (x outer, y inner),
                # but we store them in row-major order (data[y][x]) for Pythonic access.
                # The loop reads column-major from the binary stream and transposes into
                # row-major by assigning layer_data[y][x]. This matches how the C++ engine
                # (sprite.cpp) indexes its flat buffer: flat_index = x * height + y.
                # REXPaint stores data column-major: x varies outer, y varies inner
                # Stream: (0,0), (0,1), ... (0, h-1), (1,0), (1,1)...
                for x in range(width):
                    for y in range(height):
                        # [DATA-CONTRACT:XP] Each cell: 4-byte glyph + 3-byte fg + 3-byte bg = 10 bytes.
                        # [DATA-CONTRACT:CP437] Glyph is a CP437 codepoint stored as uint32.
                        glyph = struct.unpack('<I', content[offset:offset+4])[0]
                        offset += 4
                        # [DATA-CONTRACT:PALETTE] fg/bg are raw RGB uint8 triples.
                        # WHY direct byte access: content[offset] returns uint8 directly,
                        # avoiding struct.unpack overhead for single bytes. This is the
                        # inner loop hot path (width * height * layers iterations).
                        fg_r = content[offset]
                        fg_g = content[offset+1]
                        fg_b = content[offset+2]
                        offset += 3
                        bg_r = content[offset]
                        bg_g = content[offset+1]
                        bg_b = content[offset+2]
                        offset += 3

                        layer_data[y][x] = (glyph, (fg_r, fg_g, fg_b), (bg_r, bg_g, bg_b))

                self.layers.append(XPLayer(width, height, layer_data))

            print(f"Loaded {len(self.layers)} layers.")

        except Exception as e:
            print(f"Failed to load {filename}: {e}")
            raise

    # [PIPELINE:ASSEMBLE] Entry point for writing final .xp assets to disk.
    # [DEPENDENCY:GZIP] Output is gzip-compressed for REXPaint/engine compatibility.
    def save(self, filename):
        """Save all layers to a gzip-compressed .xp file.

        Writes the binary data in column-major cell order to match REXPaint's
        expected format. The output is loadable by REXPaint, the C++ engine
        (sprite.cpp), and this module's load().

        Args:
            filename: Output path (conventionally with .xp extension).

        Raises:
            Exception: On I/O errors or if layer data tuples are malformed.

        [DATA-CONTRACT:XP] [PIPELINE:ASSEMBLE]
        """
        print(f"Saving to {filename}...")
        # [DATA-CONTRACT:XP] Validate before saving to prevent C++ loader failures
        self._validate_for_save(filename)
        try:
            # WHY BytesIO: Build the entire binary payload in memory before gzip-
            # compressing in one shot. This is simpler and faster than streaming
            # individual struct.pack calls through the gzip compressor.
            out_buffer = io.BytesIO()

            # [DATA-CONTRACT:XP] File header: version (int32) + layer_count (uint32).
            out_buffer.write(struct.pack('<i', self.version))
            out_buffer.write(struct.pack('<I', len(self.layers)))

            for layer in self.layers:
                # [DATA-CONTRACT:XP] Per-layer header: width + height as int32 pair.
                out_buffer.write(struct.pack('<ii', layer.width, layer.height))

                # WHY: Write in column-major order (x outer, y inner) to match
                # REXPaint's on-disk format, even though our in-memory representation
                # is row-major (data[y][x]). This is the inverse of the load() transposition.
                for x in range(layer.width):
                    for y in range(layer.height):
                        # [DATA-CONTRACT:XP] Cell: glyph_u32 + fg_rgb + bg_rgb = 10 bytes.
                        glyph, fg, bg = layer.data[y][x]
                        out_buffer.write(struct.pack('<I', glyph))
                        # [DATA-CONTRACT:PALETTE] fg and bg written as raw RGB bytes.
                        # TODO(PIPELINE-FIX): fg and bg are expected to be 3-element tuples
                        # of uint8 values (0-255). No clamping or validation is performed.
                        # If a caller passes values outside 0-255, bytes() will raise
                        # with an unhelpful "ValueError: bytes must be in range(0, 256)".
                        # Should validate and clamp at write time with a clear error message.
                        out_buffer.write(bytes(fg))
                        out_buffer.write(bytes(bg))

            with gzip.open(filename, 'wb') as f:
                f.write(out_buffer.getvalue())

        except Exception as e:
            print(f"Failed to save {filename}: {e}")
            raise

    # [DATA-CONTRACT:XP] Metadata extraction mirrors the C++ engine's LoadSprite()
    # in sprite.cpp (lines ~721-794). Both use the same digit-glyph encoding and
    # the same cell positions in Layer 0.
    def get_metadata(self):
        """Extract sprite atlas metadata from Layer 0.

        [DATA-CONTRACT:XP] The Asciicker engine encodes atlas layout metadata in
        Layer 0's cell glyphs. This method reads those cells and returns the
        decoded values.

        Metadata cell positions (row-major data[y][x] coordinates):
            data[0][0]   -> angle count (number of viewing directions)
            data[0][1..] -> animation frame counts (scanned until non-digit or zero)

        The C++ engine (sprite.cpp) reads the same data from a flat column-major
        buffer using: layer0[0] for angles, layer0[height*a] for animation 'a'.
        Since flat_index = col * height + row, and row=0 for metadata cells,
        layer0[height*a] == cell at (col=a, row=0) == data[0][a] in our layout.

        Digit encoding (matches sprite.cpp GetDigit()):
            CP437 '0'-'9' (48-57)   -> 0-9
            CP437 'A'-'Z' (65-90)   -> 10-35
            CP437 'a'-'z' (97-122)  -> 10-35 (case-insensitive)
            Anything else            -> -1 (invalid / stop sentinel)

        Returns:
            dict with keys:
                'angles': int -- Number of view angles (1 if not encoded or <= 0).
                'anims':  list[int] -- Frame count per animation sequence.
            Returns None if the file has no layers.

        Note:
            The C++ engine also reads Y/Z projection offsets from layer0 rows 1-2
            (cells data[1][0], data[1][1], data[2][0], data[2][1]). This method
            does NOT extract those -- they are only relevant to the rendering engine.
        """
        # Extract metadata from Layer 0 as per analysis
        # (0,0) -> Angle Count
        # (n*height, 0) -> Animation Lengths

        if not self.layers:
            return None

        l0 = self.layers[0]

        def get_digit(res):
            """Decode a digit glyph to its integer value.

            Mirrors sprite.cpp AnsiCell::GetDigit() exactly:
            '0'-'9' -> 0-9, 'A'-'Z'/'a'-'z' -> 10-35, else -1.
            """
            glyph, _, _ = res
            if 48 <= glyph <= 57: # '0'-'9'
                return glyph - 48
            if 65 <= glyph <= 90: # 'A'-'Z'
                return glyph + 10 - 65
            if 97 <= glyph <= 122: # 'a'-'z'
                return glyph + 10 - 97
            return -1

        # WHY: angles <= 0 defaults to 1, matching sprite.cpp behavior where
        # a missing or zero angle count means single-angle sprite (no rotation).
        raw_angles = get_digit(l0.data[0][0])

        # [ENGINE-ALIGN] sprite.cpp:806-808 — projs=2 when angles > 0
        # WHY: The C++ engine sets projs=2 for multi-angle sprites, meaning
        # the atlas has projection+reflection pairs side-by-side. This is
        # critical for correct frame indexing:
        #   fr_num_x = projs * sum(anim_lengths)
        # Existing sprites like player-nude.xp have 18 columns (2×9 frames).
        # Setting projs=1 caused the preview to show wrong frames.
        if raw_angles > 0:
            projs = 2
            angles = raw_angles
        else:
            projs = 1
            angles = 1

        # [DATA-CONTRACT:XP] Scan animation frame counts from Layer 0, row 0, cols 1..width-1.
        anims = []
        for a in range(1, l0.width):
            # WHY data[0][a]: In the C++ engine's column-major flat buffer, the metadata
            # cell for animation 'a' is at flat index = a * height + 0 (row=0, col=a).
            # In our row-major data[y][x], this is data[0][a]. The derivation:
            #   column-major flat_index = col * height + row
            #   for row=0: flat_index = col * height = a * height
            #   row-major equivalent: data[row][col] = data[0][a]
            length = get_digit(l0.data[0][a])
            if length > 0:
                anims.append(length)
            else:
                # WHY: Stop at the first non-digit or zero, matching sprite.cpp's
                # break-on-invalid behavior. Gaps in animation encoding are not supported.
                break

        return {
            "angles": angles,
            "projs": projs,
            "anims": anims
        }
