"""Blender subprocess bridge — execute Python scripts inside Blender headlessly."""

import json
import os
import platform
import shutil
import subprocess
import tempfile
import textwrap


def _find_blender():
    """Auto-detect Blender executable path."""
    # Environment override
    env = os.environ.get("BLENDER_PATH")
    if env and os.path.isfile(env):
        return env

    system = platform.system()
    if system == "Darwin":
        candidates = [
            "/Applications/Blender.app/Contents/MacOS/Blender",
            os.path.expanduser("~/Applications/Blender.app/Contents/MacOS/Blender"),
        ]
    elif system == "Windows":
        candidates = [
            r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe",
            r"C:\Program Files\Blender Foundation\Blender 4.4\blender.exe",
            r"C:\Program Files\Blender Foundation\Blender 4.3\blender.exe",
        ]
    else:
        candidates = ["/usr/bin/blender", "/usr/local/bin/blender", "/snap/bin/blender"]

    for c in candidates:
        if os.path.isfile(c):
            return c

    # PATH fallback
    found = shutil.which("blender")
    if found:
        return found

    raise FileNotFoundError(
        "Blender not found. Set BLENDER_PATH or install Blender."
    )


class BlenderBridge:
    """Execute Python code inside Blender's embedded interpreter.

    All commands run in ``--background`` mode (no GUI).  Results are exchanged
    via a temporary JSON file to avoid stdout noise from Blender's own logging.
    """

    def __init__(self, blend_file=None, blender_path=None):
        self.blend_file = blend_file
        self.blender_path = blender_path or _find_blender()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(self, script, timeout=120):
        """Run *script* inside Blender and return the result ``dict``.

        The script **must** assign its output to a variable called ``_data``.
        Example::

            bridge.execute('''
            import bpy
            _data = {"objects": [o.name for o in bpy.data.objects]}
            ''')

        Returns ``{"ok": True, "data": ...}`` on success or
        ``{"ok": False, "error": "...", "traceback": "..."}`` on failure.
        """
        out_fd, out_path = tempfile.mkstemp(suffix=".json")
        os.close(out_fd)
        script_fd, script_path = tempfile.mkstemp(suffix=".py")
        os.close(script_fd)

        wrapped = self._wrap(script, out_path)
        try:
            with open(script_path, "w") as f:
                f.write(wrapped)

            cmd = [self.blender_path, "--background"]
            if self.blend_file:
                cmd.append(self.blend_file)
            cmd.extend(["--python", script_path])

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                with open(out_path) as f:
                    return json.load(f)

            # Fallback: script probably crashed before writing output
            return {
                "ok": False,
                "error": "No output produced",
                "stderr": proc.stderr[-2000:] if proc.stderr else "",
                "returncode": proc.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"Blender timed out after {timeout}s"}
        finally:
            for p in (script_path, out_path):
                try:
                    os.unlink(p)
                except OSError:
                    pass

    def version(self):
        """Return Blender version string."""
        proc = subprocess.run(
            [self.blender_path, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in proc.stdout.splitlines():
            if line.startswith("Blender"):
                return line.strip()
        return proc.stdout.strip().splitlines()[0] if proc.stdout else "unknown"

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _wrap(script, output_path):
        """Wrap user script so it writes JSON to *output_path*."""
        indented = textwrap.indent(script.strip(), "    ")
        return f'''\
import json, sys, traceback

_output_path = {output_path!r}

try:
{indented}
    _result = {{"ok": True, "data": _data}}
except Exception as _exc:
    _result = {{
        "ok": False,
        "error": str(_exc),
        "type": type(_exc).__name__,
        "traceback": traceback.format_exc(),
    }}

with open(_output_path, "w") as _f:
    json.dump(_result, _f)
'''
