
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from xp_core import XPFile

def analyze_colors(path):
    xp = XPFile()
    xp.load(path)
    
    colors = {}
    
    for l_idx, layer in enumerate(xp.layers):
        for row in layer.data:
            for cell in row:
                glyph, fg, bg = cell
                # FG
                if fg not in colors: colors[fg] = 0
                colors[fg] += 1
                # BG
                if bg not in colors: colors[bg] = 0
                colors[bg] += 1
                
    print("Top colors:")
    # Sort by freq
    sorted_colors = sorted(colors.items(), key=lambda x: x[1], reverse=True)
    for c, freq in sorted_colors[:10]:
        print(f"RGB{c} Hex: #%02x%02x%02x - Count: {freq}" % c)

if __name__ == "__main__":
    REPO_ROOT = os.environ.get("ASCIICKER_REPO", os.path.dirname(os.path.abspath(__file__)))
    analyze_colors(os.path.join(REPO_ROOT, "assets/sprites/wolfie-0000.xp"))
