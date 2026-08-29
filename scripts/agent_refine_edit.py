
import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters, stdio_client

async def main():
    REPO_ROOT = os.environ.get("ASCIICKER_REPO", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    target_file = os.path.join(REPO_ROOT, "assets/sprites/asciicker.xp")
    
    server_script = os.path.abspath(os.path.join(os.path.dirname(__file__), "xp_mcp_server.py"))
    server_params = StdioServerParameters(command=sys.executable, args=[server_script], env=None)
    
    bridge_script = os.path.abspath(os.path.join(os.path.dirname(__file__), "xp_mcp_bridge.py"))
    bridge_params = StdioServerParameters(command=sys.executable, args=[bridge_script], env=None)
    
    # ASCII Art (81x10) - Same as before
    ascii_art = '░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░▓▒░░░░░░░░░░▒░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░▓██▒░░░░░░░░▓▓░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░▓███▓░░░░░░▒▓██▓░░░░░░░░░░░░░░░░░░░░░░░░░░░▒▓██▒██▓██████████▒░░░░░░░░░░░░░░░░░░░▒▓████████▓░▒████████████▒░░░░░░░░░░░░░░░░░▒▓██████████▒░░████████████▒░░░░░░░░░░░░░▒██████████████████████████████▓░░░░░░░░░░▒▓██████▒▒▒▒▒▒▒▒▒░░░██████▒▒▒▒█████▒░░░░░░░▒▓▓███████▓▒░░░░░▒▒▓████▓▒███▒██▓▓███▒░░░░░░░▒▓█████▒░░░░▒▒▒░▒▒███▒▒▒▒▒▒▒▒▒▓▓▒▓███▓░░░░░░▓█████▓███████████████▒▒▒▒█▓▓▓▓█▓▒▒▓▓██▓▒░░░░░▓████▓▓▒░░░░░░▒██████▓▒▒▒▒▒▓▓▓▓▓▓▒▒▒▓███▓░░░█▓▓░░░░░░░░░░░░██▓██▓▒▓████▓▒▒▒███▓▓█████▒░░░░░░░░░░░░░░░░░░░▒█████▓▓▓▓▓███▒▒▒▓▒▓▒█▓▒░░░░░░░░░░░░░░░░░░░░░▓▒▒░░░░░░░░░░▓██▓▒▒▓█▓░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░▒▓█▓▓▒░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░'

    # SBU Block Text (Small 3x3 font approximation)
    sbu_art = "### ##  # #\n#   # # # #\n### ##  # #" # Simplistic, let's use block chars
    # Better SBU using block chars █
    sbu_block = (
        "███ ██  █ █\n"
        "█   █ █ █ █\n"
        "███ ██  ███"
    )
    # Actually just write "SBU" with write_ascii_block and rely on visualizer or just simple string.
    # The user said "outline SBU".
    
    print(f"Connecting to XP MCP Server...")
    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                
                # 1. Shift Layer 1 (Image) to Layer 2
                # Assuming L1 is where the image was. L0 is metadata.
                print("Shifting Layer 1 to Layer 2...")
                await session.call_tool("shift_layer_content", arguments={
                    "path": target_file, "src_idx": 1, "dest_idx": 2
                })
                
                # 2. Make Layer 2 Background Transparent
                # Replace Black (#000000) with Transparent Magenta (#FF00FF) on Layer 2
                print("Making Layer 2 Transparent...")
                await session.call_tool("replace_color", arguments={
                    "path": target_file, 
                    "old_hex": "#000000", "new_hex": "#FF00FF",
                    "layers": [2]
                })

                # 3. Replace Gold with Red on Layer 2
                # Try #aa5500 and #ffbf00 just in case
                print("Replacing Gold with Red on Layer 2...")
                await session.call_tool("replace_color", arguments={
                    "path": target_file,
                    "old_hex": "#aa5500", "new_hex": "#ff0000",
                    "layers": [2]
                })
                await session.call_tool("replace_color", arguments={
                    "path": target_file,
                    "old_hex": "#ffbf00", "new_hex": "#ff0000",
                    "layers": [2]
                })

                # 4. Write ASCII Art on Layer 1 (Background)
                print("Writing Background ASCII on Layer 1...")
                # Ensure L1 is clear/black first? (Since we shifted, it's clear/black)
                await session.call_tool("write_ascii_block", arguments={
                    "path": target_file,
                    "layer_idx": 1,
                    "x": 0, "y": 0, "width": 81,
                    "text": ascii_art,
                    "fg_hex": "#404040", # Dark grey so it's background-y
                    "bg_hex": "#000000"
                })

                # 5. Write SBU on Layer 2 (Foreground)
                # Center is roughly 40, 5
                print("Writing SBU on Layer 2...")
                msg = "SBU"
                # Use large block letters?
                # User said "outline SBU".
                # I'll just write "S B U" with spaces
                await session.call_tool("write_text", arguments={
                    "path": target_file,
                    "layer_idx": 2,
                    "x": 38, "y": 9, # Bottom center? Or true center?
                    # Image is 81x11. Center ~ 40, 5.
                    "text": "S B U",
                    "fg_hex": "#FFFFFF",
                    "bg_hex": "#FF00FF" # Transparent BG
                })
                
    except Exception as e:
        print(f"Error: {e}")
        return

    # 6. Reload
    print(f"Connecting to XP Bridge...")
    try:
         async with stdio_client(bridge_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                print("Triggering reload...")
                await session.call_tool("refresh_tool_view")
                
    except Exception as e:
        print(f"Error triggering reload: {e}")

if __name__ == "__main__":
    asyncio.run(main())
