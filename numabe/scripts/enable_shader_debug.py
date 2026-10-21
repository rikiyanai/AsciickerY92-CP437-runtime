import sys
import os
import argparse

SHADER_PATH = "godot_project/ansi_process.gdshader"

# The original block we want to replace/restore
ORIGINAL_BLOCK = """
	// Use Red channel for font data (font.png is RGB)
	COLOR.rgb = mix(bg, fg, glyph_val);
	COLOR.a = 1.0;
}"""

# Debug 1: Visualize Glyph Index (Blue Channel)
DEBUG_GLYPH_BLOCK = """
	// DEBUG: Visualize Glyph Index (Blue Channel)
	COLOR.rgb = vec3(mat_data.b, mat_data.b, mat_data.b);
	COLOR.a = 1.0;
}"""

# Debug 2: Visualize Font Sampling
DEBUG_FONT_BLOCK = """
	// DEBUG: Visualize Font Sampling
	COLOR.rgb = vec3(glyph_val, glyph_val, glyph_val);
	COLOR.a = 1.0;
}"""

# Debug 3: Visualize Background Index (Red Channel)
DEBUG_BG_BLOCK = """
	// DEBUG: Visualize BG Index (Red Channel)
	COLOR.rgb = vec3(mat_data.r, 0.0, 0.0);
	COLOR.a = 1.0;
}"""

def read_shader():
    if not os.path.exists(SHADER_PATH):
        print(f"Error: Shader not found at {SHADER_PATH}")
        sys.exit(1)
    with open(SHADER_PATH, 'r') as f:
        return f.read()

def write_shader(content):
    with open(SHADER_PATH, 'w') as f:
        f.write(content)
    print(f"Updated {SHADER_PATH}")

def reset_shader():
    content = read_shader()
    # Attempt to find any of the debug blocks and replace with original
    if DEBUG_GLYPH_BLOCK.strip() in content:
        content = content.replace(DEBUG_GLYPH_BLOCK.strip(), ORIGINAL_BLOCK.strip())
    elif DEBUG_FONT_BLOCK.strip() in content:
        content = content.replace(DEBUG_FONT_BLOCK.strip(), ORIGINAL_BLOCK.strip())
    elif DEBUG_BG_BLOCK.strip() in content:
        content = content.replace(DEBUG_BG_BLOCK.strip(), ORIGINAL_BLOCK.strip())
    else:
        print("Shader appears to be clean or unrecognized format.")
        return
    write_shader(content)

def apply_debug(block, name):
    reset_shader() # Clean first
    content = read_shader()
    if ORIGINAL_BLOCK.strip() not in content:
        # Fallback: Maybe whitespace mismatch?
        # Let's try to match just the last lines
        pass
    
    if ORIGINAL_BLOCK.strip() in content:
        content = content.replace(ORIGINAL_BLOCK.strip(), block.strip())
        write_shader(content)
        print(f"Applied {name} debug mode.")
    else:
        print("Error: Could not find original code block to replace. Shader might be modified.")

def main():
    parser = argparse.ArgumentParser(description="Inject debug logic into ansi_process.gdshader")
    parser.add_argument("mode", choices=["reset", "glyph", "font", "bg"], help="Debug mode to apply")
    
    args = parser.parse_args()
    
    if args.mode == "reset":
        reset_shader()
    elif args.mode == "glyph":
        apply_debug(DEBUG_GLYPH_BLOCK, "Glyph Index")
    elif args.mode == "font":
        apply_debug(DEBUG_FONT_BLOCK, "Font Sampling")
    elif args.mode == "bg":
        apply_debug(DEBUG_BG_BLOCK, "Background Index")

if __name__ == "__main__":
    main()
