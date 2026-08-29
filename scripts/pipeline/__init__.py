"""
__init__.py -- Asset Generation Pipeline Package.

ARCHITECTURE:
  This package implements the 4-stage sprite generation pipeline that converts
  raw images (from files, AI generators, or Blender renders) into CP437 glyph
  grids serialized as .xp files for the Asciicker game engine.

  Pipeline stages:
    [PIPELINE:GENERATE]   generator.py   -- Acquire/load source images
    [PIPELINE:SLICE]      slicer.py      -- Cut sprite sheets into frames
    [PIPELINE:PROCESS]    processor.py   -- Convert pixels to glyph grids
    [PIPELINE:ASSEMBLE]   assembler.py   -- Serialize grids to .xp format

  Supporting modules:
    schemas.py            -- AssetDef dataclass (pipeline config)
    presets.py            -- Pre-built AssetDef templates
    palette.py            -- Canonical 16-color ANSI palette + transparency
    quantizer.py          -- Pixel-to-palette color quantization
    matcher.py            -- CP437 glyph pattern matching
    color_correction.py   -- Background detection and magenta snap
    cli.py                -- Command-line interface
    pipeline.py           -- AssetPipeline orchestrator class

  Entry points:
    - ``python -m scripts.pipeline.cli`` (CLI)
    - ``AssetPipeline(asset_def).run()`` (programmatic)

PIPELINE CONTEXT:
  [FLOW:CLI]              -- CLI entry via ``python -m scripts.pipeline.cli``.
  [FLOW:TEMPLATE]         -- Template-driven entry via pipeline.py + loader.py.
  [DATA-CONTRACT:XP]      -- Final output is REXPaint .xp format.
  [DATA-CONTRACT:CP437]   -- Glyph matching uses CP437 codepage indices.
  [DATA-CONTRACT:PALETTE] -- 16-color ANSI palette defined in palette.py.
  [DEPENDENCY:PIL]        -- Pillow required for image processing stages.
  [DEPENDENCY:NUMPY]      -- NumPy required for vectorized quantization.
"""
