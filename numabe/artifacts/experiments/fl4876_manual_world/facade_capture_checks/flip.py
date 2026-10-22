"""flip.py A.png [B.png ...] -> A_up.png: the Classic AnsiCell buffer is bottom-up (row 0 = screen bottom);
web_classic_cells_to_png.py writes row 0 at the top, so its PNGs are upside down."""
import sys
from PIL import Image

for p in sys.argv[1:]:
    Image.open(p).transpose(Image.FLIP_TOP_BOTTOM).save(p[:-4] + "_up.png")
    print(p[:-4] + "_up.png")
