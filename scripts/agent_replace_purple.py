
import asyncio
import os
import sys

# Ensure mcp is importable
from mcp import ClientSession, StdioServerParameters, stdio_client

async def main():
    REPO_ROOT = os.environ.get("ASCIICKER_REPO", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    target_file = os.path.join(REPO_ROOT, "assets/sprites/wolfie-0000.xp")
    old_color = "#aa00aa" # Purple
    new_color = "#ff0000" # Red
    
    # 1. Connect to MCP Server to execute change
    server_script = os.path.abspath(os.path.join(os.path.dirname(__file__), "xp_mcp_server.py"))
    server_params = StdioServerParameters(command=sys.executable, args=[server_script], env=None)
    
    print(f"Connecting to XP MCP Server...")
    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                
                print(f"Replacing {old_color} with {new_color} in {target_file}...")
                result = await session.call_tool("replace_color", arguments={
                    "path": target_file,
                    "old_hex": old_color,
                    "new_hex": new_color
                })
                print(result.content[0].text)
                
    except Exception as e:
        print(f"Error during replacement: {e}")
        return

    # 2. Connect to Bridge to reload view
    bridge_script = os.path.abspath(os.path.join(os.path.dirname(__file__), "xp_mcp_bridge.py"))
    bridge_params = StdioServerParameters(command=sys.executable, args=[bridge_script], env=None)
    
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
