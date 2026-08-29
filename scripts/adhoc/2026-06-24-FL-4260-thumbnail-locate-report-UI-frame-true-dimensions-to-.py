# Ad hoc script: FL-4260 thumbnail locate: report UI frame true dimensions to recompute thumbnail crop box
# Created: 2026-06-24
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
from PIL import Image
import os
BASE=os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),'..','..',
     'docs/research/ascii/verification/fl4260/2026-06-24-thumbnail-visibility'))
im=Image.open(os.path.join(BASE,'03_thumbnail_preset_applied/ui_frame.png'))
print("SIZE", im.size)
