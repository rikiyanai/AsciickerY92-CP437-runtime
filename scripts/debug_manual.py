import sys
import os
import logging
import traceback

sys.path.append(os.getcwd())

from scripts.pipeline.schemas import AssetDef
import scripts.pipeline.pipeline
print(f"DEBUG: Pipeline file: {scripts.pipeline.pipeline.__file__}")
from scripts.pipeline.pipeline import AssetPipeline

logging.basicConfig(level=logging.INFO)

def test():
    try:
        asset = AssetDef(
            name="gemini_test",
            type="character",
            angles=8,
            frames=[1],
            source_type="file",
            transparency=False,
            normalization=True,
            target_cells_high=8
        )
        input_path = "scripts/Gemini_Generated_Image_653mno653mno653m.png"
        
        print(f"Initializing pipeline for {input_path}...")
        pipeline = AssetPipeline(asset, input_path)
        
        print("Running pipeline...")
        pipeline.run(algorithm="nearest")
        print("Done.")
    except Exception:
        traceback.print_exc()

if __name__ == "__main__":
    test()
