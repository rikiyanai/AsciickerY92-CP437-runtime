
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
    
    print(f"Connecting to XP MCP Server...")
    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                
                print("Setting valid metadata (Angles=1, Anims=[1])...")
                # This ensures the tool calculates cell_w = width // 1, allowing editing
                await session.call_tool("set_metadata", arguments={
                    "path": target_file,
                    "angles": 1,
                    "anims": [1]
                })
                
    except Exception as e:
        print(f"Error: {e}")
        return

    # Reload
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
