"""
Blender MCP client for remote control.

Communicates with Blender MCP server (port 9876) to trigger renders and receive output.
"""

import socket
import json
import os
from typing import Optional, Any, Dict
from PIL import Image
import io
from pathlib import Path

from scripts.pipeline.schemas.render_contract import RenderRequest, RenderResponse


class BlenderMCPError(Exception):
    """Custom exception for MCP client errors."""

    pass


class BlenderMCPClient:
    """Client for communicating with Blender MCP server."""

    def __init__(self, host: str = "localhost", port: int = 9876, timeout: int = 30):
        """
        Initialize MCP client.

        Args:
            host: MCP server host
            port: MCP server port (default 9876)
            timeout: Socket timeout in seconds
        """
        self.host = host
        env_port = os.getenv("BLENDER_MCP_PORT")
        if env_port and port == 9876:
            try:
                port = int(env_port)
            except ValueError:
                pass
        self.port = port
        self.timeout = timeout
        self.socket: Optional[socket.socket] = None
        self.connected = False

    def connect(self) -> bool:
        """
        Connect to Blender MCP server.

        Returns:
            True if connection successful, False otherwise

        Raises:
            BlenderMCPError: If connection fails
        """
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(self.timeout)
            self.socket.connect((self.host, self.port))
            self.connected = True
            return True
        except ConnectionRefusedError:
            raise BlenderMCPError(
                f"Cannot connect to MCP server at {self.host}:{self.port}. "
                f"Is Blender running with addon loaded?"
            )
        except socket.timeout:
            raise BlenderMCPError(f"Connection timeout to {self.host}:{self.port}")
        except Exception as e:
            raise BlenderMCPError(f"Connection failed: {e}")

    def disconnect(self):
        """Close socket connection."""
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass
            self.socket = None
            self.connected = False

    def _send(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send JSON command to MCP server.

        Args:
            data: Dictionary to send as JSON

        Returns:
            Response from server as dictionary

        Raises:
            BlenderMCPError: If send fails or response invalid
        """
        if not self.connected:
            raise BlenderMCPError("Not connected to MCP server")

        try:
            # Send JSON
            # [PROTOCOL-FIX]: Server expects raw JSON, not HTTP-style headers.
            json_data = json.dumps(data)
            self.socket.sendall(json_data.encode("utf-8"))

            # Receive response
            response = self._receive_response()
            return response

        except json.JSONDecodeError as e:
            raise BlenderMCPError(f"Invalid JSON response: {e}")
        except Exception as e:
            raise BlenderMCPError(f"Send failed: {e}")

    def _receive_response(self) -> Dict[str, Any]:
        """
        Receive response from MCP server.

        Returns:
            Response as dictionary

        Raises:
            BlenderMCPError: If receive fails
        """
        try:
            buffer = ""
            headers_received = False
            content_length = None

            while True:
                chunk = self.socket.recv(4096).decode("utf-8")
                if not chunk:
                    raise BlenderMCPError("Connection closed by server")

                buffer += chunk

                if not headers_received:
                    # FALLBACK: If we see a '{' before any headers, assume raw JSON (no headers)
                    if buffer.lstrip().startswith("{") and "\r\n\r\n" not in buffer:
                        # We'll try to parse the entire buffer as JSON
                        # For very large responses we might need to accumulate more,
                        # but for small commands this works.
                        try:
                            # Try to find the end of the JSON object
                            return json.loads(buffer)
                        except json.JSONDecodeError:
                            # Incomplete JSON, continue receiving
                            continue

                    headers_end = buffer.find("\r\n\r\n")
                    if headers_end != -1:
                        header_block = buffer[:headers_end]
                        body = buffer[headers_end + 4 :]

                        # Parse Content-Length
                        for line in header_block.split("\r\n"):
                            if line.lower().startswith("content-length:"):
                                content_length = int(line.split(":")[1].strip())
                                break

                        headers_received = True

                        if content_length is not None:
                            # Check if we have full body
                            while len(body) < content_length:
                                chunk = self.socket.recv(4096).decode("utf-8")
                                body += chunk

                            # Parse JSON body
                            return json.loads(body)
                        else:
                            # No content-length, try parsing
                            return json.loads(body) if body else {}
                else:
                    # Already received headers, this is body
                    if content_length:
                        if len(buffer) >= content_length:
                            return json.loads(buffer[:content_length])
                    else:
                        return json.loads(buffer)

        except json.JSONDecodeError as e:
            raise BlenderMCPError(f"Invalid JSON response: {e}")
        except Exception as e:
            raise BlenderMCPError(f"Receive failed: {e}")

    def execute_code(self, code: str) -> Any:
        """
        Execute Python code in Blender context.

        Args:
            code: Python code string to execute

        Returns:
            Result from code execution

        Raises:
            BlenderMCPError: If execution fails
        """
        # [PROTOCOL-FIX]: Server expects {"type": "execute_code", "params": {"code": "..."}}
        command = {"type": "execute_code", "params": {"code": code}}

        response = self._send(command)

        if response.get("status") == "error":
            raise BlenderMCPError(f"Execution error: {response.get('message')}")

        # [PROTOCOL-FIX]: Server returns result inside {"status": "success", "result": {"executed": True, "result": "..."}}
        inner_result = response.get("result", {})
        captured_stdout = inner_result.get("result")

        # [PROTOCOL-FIX]: If the captured stdout is a JSON string, parse it.
        # This is how we pass structured data (like images) back through MCP.
        if captured_stdout:
            try:
                # Look for the last line that is valid JSON (in case of other prints)
                lines = captured_stdout.strip().split("\n")
                for line in reversed(lines):
                    if line.strip().startswith("{") and line.strip().endswith("}"):
                        return json.loads(line)
                return captured_stdout
            except json.JSONDecodeError:
                return captured_stdout

        return captured_stdout

    def render_asset(self, asset_def: Dict[str, Any], output_path: str) -> str:
        """
        Render an asset via MCP.

        Args:
            asset_def: Asset definition dictionary
            output_path: Path to save rendered PNG

        Returns:
            Path to rendered PNG file

        Raises:
            BlenderMCPError: If render fails
        """
        # Build RenderRequest for the addon-level render_asset command.
        asset_name = asset_def.get("asset_name") or asset_def.get("name") or Path(output_path).stem
        object_name = asset_def.get("object_name") or asset_def.get("blender_object")

        frames = asset_def.get("frames")
        if frames is None:
            frames = asset_def.get("anims", 1)
        if isinstance(frames, list):
            frames = sum(frames)

        resolution = asset_def.get("resolution")
        if resolution is None:
            resolution = (96, 96)

        request = RenderRequest(
            asset_name=asset_name,
            object_name=object_name,
            resolution=tuple(resolution),
            frames=int(frames),
            angles=int(asset_def.get("angles", 8)),
            transparent_bg=bool(asset_def.get("transparent_bg", True)),
            background_color=asset_def.get("background_color", "#FF00FF"),
            convert_to_magenta=bool(asset_def.get("convert_to_magenta", True)),
            output_dir=str(Path(output_path).parent),
            return_bytes=bool(asset_def.get("return_bytes", False)),
            order=asset_def.get("frame_order", "angle-major"),
            seed=int(asset_def.get("seed", 0)),
        )

        response = self._send({"type": "render_asset", "params": request.to_dict()})

        if response.get("status") == "error":
            # Fallback to legacy execute_code path if addon doesn't support render_asset.
            message = response.get("message", "")
            if "Unknown command type" in message:
                return self._render_asset_via_execute_code(asset_def, output_path)
            raise BlenderMCPError(f"Render failed: {message}")

        result = response.get("result", {})
        render = RenderResponse.from_dict(result)

        if not render.success:
            raise BlenderMCPError(f"Render failed: {result.get('error', 'Unknown error')}")

        # If base64 data is returned but file path is missing, save manually.
        if render.base64_data and (not render.filepath or not os.path.exists(render.filepath)):
            self.save_buffer(render.base64_data, output_path)
            return output_path

        return render.filepath or output_path

    def _render_asset_via_execute_code(self, asset_def: Dict[str, Any], output_path: str) -> str:
        """
        Legacy MCP render path: send a generated render script via execute_code.
        Used as fallback when render_asset command is not available.
        """
        import sys

        scripts_dir = Path(__file__).parent
        blender_dir = scripts_dir / "blender"
        if str(blender_dir) not in sys.path:
            sys.path.insert(0, str(blender_dir))

        from render_payload import get_render_script

        script = get_render_script(asset_def)
        result = self.execute_code(script)

        if not result or "image" not in result:
            raise BlenderMCPError("No image returned from render")

        self.save_buffer(result["image"], output_path)

        return output_path

    def save_buffer(self, buffer_data: str, output_path: str):
        """
        Save image buffer to file.

        Args:
            buffer_data: Base64-encoded image buffer
            output_path: Output file path

        Raises:
            BlenderMCPError: If save fails
        """
        try:
            import base64
            from io import BytesIO

            # Decode base64
            image_bytes = base64.b64decode(buffer_data)

            # Save with PIL
            img = Image.open(BytesIO(image_bytes))
            img.save(output_path)

        except Exception as e:
            raise BlenderMCPError(f"Failed to save buffer: {e}")

    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect()

    def __del__(self):
        """Cleanup on deletion."""
        self.disconnect()


def render_character_via_mcp(
    object_name: str, angles: int = 8, output_path: str = "sprite_sheet.png"
) -> str:
    """
    Convenience function to render a character via MCP.

    Args:
        object_name: Name of object in Blender scene
        angles: Number of angles to render
        output_path: Output PNG path

    Returns:
        Path to rendered sprite sheet

    Raises:
        BlenderMCPError: If render fails
    """
    asset_def = {
        "object_name": object_name,
        "angles": angles,
        "anims": [4],  # Default 4 frames
        "type": "character",
    }

    with BlenderMCPClient() as client:
        return client.render_asset(asset_def, output_path)


if __name__ == "__main__":
    # Test MCP connection
    print("Testing MCP connection...")
    try:
        with BlenderMCPClient() as client:
            print("✓ Connected to MCP server")

            # Try a simple command
            # WHY: execute_code captures stdout, so we must print the result inside the code.
            result = client.execute_code("print(bpy.data.filepath)")
            print(f"✓ Command executed: {result.strip() if result else '(no output)'}")

    except BlenderMCPError as e:
        print(f"✗ MCP test failed: {e}")
        print("  (This is expected if Blender is not running with MCP addon loaded)")
