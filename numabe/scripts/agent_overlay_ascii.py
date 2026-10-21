
import asyncio
import os
import sys

# Ensure mcp is importable
from mcp import ClientSession, StdioServerParameters, stdio_client

async def main():
    REPO_ROOT = os.environ.get("ASCIICKER_REPO", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    target_file = os.path.join(REPO_ROOT, "assets/sprites/asciicker.xp")
    
    # ASCII Art (81x10)
    ascii_art = '░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░▓▒░░░░░░░░░░▒░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░▓██▒░░░░░░░░▓▓░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░▓███▓░░░░░░▒▓██▓░░░░░░░░░░░░░░░░░░░░░░░░░░░▒▓██▒██▓██████████▒░░░░░░░░░░░░░░░░░░░▒▓████████▓░▒████████████▒░░░░░░░░░░░░░░░░░▒▓██████████▒░░████████████▒░░░░░░░░░░░░░▒██████████████████████████████▓░░░░░░░░░░▒▓██████▒▒▒▒▒▒▒▒▒░░░██████▒▒▒▒█████▒░░░░░░░▒▓▓███████▓▒░░░░░▒▒▓████▓▒███▒██▓▓███▒░░░░░░░▒▓█████▒░░░░▒▒▒░▒▒███▒▒▒▒▒▒▒▒▒▓▓▒▓███▓░░░░░░▓█████▓███████████████▒▒▒▒█▓▓▓▓█▓▒▒▓▓██▓▒░░░░░▓████▓▓▒░░░░░░▒██████▓▒▒▒▒▒▓▓▓▓▓▓▒▒▒▓███▓░░░█▓▓░░░░░░░░░░░░██▓██▓▒▓████▓▒▒▒███▓▓█████▒░░░░░░░░░░░░░░░░░░░▒█████▓▓▓▓▓███▒▒▒▓▒▓▒█▓▒░░░░░░░░░░░░░░░░░░░░░▓▒▒░░░░░░░░░░▓██▓▒▒▓█▓░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░▒▓█▓▓▒░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░'
    
    server_script = os.path.abspath(os.path.join(os.path.dirname(__file__), "xp_mcp_server.py"))
    server_params = StdioServerParameters(command=sys.executable, args=[server_script], env=None)
    
    bridge_script = os.path.abspath(os.path.join(os.path.dirname(__file__), "xp_mcp_bridge.py"))
    bridge_params = StdioServerParameters(command=sys.executable, args=[bridge_script], env=None)
    
    print(f"Connecting to XP MCP Server...")
    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                
                # 1. Resize Canvas
                print("Resizing canvas to 81x11...")
                result = await session.call_tool("resize_xp_file", arguments={
                    "path": target_file,
                    "width": 81,
                    "height": 11 # 1 extra row for margin? Or just 10? Live 11 is safer.
                })
                print(result.content[0].text)
                
                # 2. Write ASCII Block on Layer 1 (Foreground)
                # Centered roughly. 
                # Canvas 81x11. Art 81x10.
                # x=0, y=0 or y=1 if we want centered vertically.
                print("Writing ASCII block...")
                result = await session.call_tool("write_ascii_block", arguments={
                    "path": target_file,
                    "layer_idx": 1, 
                    "x": 0, "y": 0,
                    "width": 81,
                    "text": ascii_art,
                    "fg_hex": "#FFFFFF",
                    "bg_hex": "#000000" 
                })
                print(result.content[0].text)
                
    except Exception as e:
        print(f"Error during modification: {e}")
        return

    # 3. Reload Tool
    print(f"Connecting to XP Bridge...")
    try:
         async with stdio_client(bridge_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                
                print("Triggering reload on live tool...")
                result = await session.call_tool("refresh_tool_view")
                print(result.content[0].text)
                
    except Exception as e:
        print(f"Error triggering reload: {e}")

if __name__ == "__main__":
    asyncio.run(main())
