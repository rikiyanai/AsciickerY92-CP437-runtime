import subprocess
import time
import select
import base64
import fcntl
import os
import logging
from PIL import Image
from io import BytesIO

logger = logging.getLogger(__name__)

class EngineClient:
    """
    Client for communicating with the asciiid C++ engine via MCP.
    """
    def __init__(self, executable_path="./.run/asciiid"):
        self.executable_path = executable_path
        self.proc = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def start(self):
        if self.proc: return
        
        logger.info(f"Starting engine: {self.executable_path}")
        self.proc = subprocess.Popen(
            [self.executable_path, "--mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        # Set stdout to non-blocking
        fd = self.proc.stdout.fileno()
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        
        # Wait for initialization
        time.sleep(3)
        if self.proc.poll() is not None:
            raise RuntimeError(f"Engine failed to start: {self.proc.returncode}")

    def stop(self):
        if not self.proc: return
        
        try:
            self.proc.stdin.write("QUIT\n")
            self.proc.stdin.flush()
        except: pass
        
        time.sleep(0.5)
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except:
                self.proc.kill()
        self.proc = None

    def render(self) -> Image.Image:
        """
        Trigger a RENDER command and return the resulting PIL Image.
        """
        if not self.proc: self.start()
        
        logger.info("Sending RENDER to engine...")
        self.proc.stdin.write("RENDER\n")
        self.proc.stdin.flush()
        
        output_buffer = ""
        start_time = time.time()
        found_start = False
        found_end = False
        
        w, h = 0, 0
        
        while time.time() - start_time < 15.0:
            rlist, _, _ = select.select([self.proc.stdout], [], [], 0.1)
            if rlist:
                try:
                    chunk = self.proc.stdout.read(8192)
                    if chunk:
                        output_buffer += chunk
                        if "[RENDER_DATA_START]" in output_buffer and not found_start:
                            found_start = True
                            # Parse metadata
                            for line in output_buffer.splitlines():
                                if "[RENDER_DATA_START]" in line:
                                    parts = line.split()
                                    for p in parts:
                                        if p.startswith("w="): w = int(p[2:])
                                        if p.startswith("h="): h = int(p[2:])
                                    break
                        
                        if "[RENDER_DATA_END]" in output_buffer:
                            found_end = True
                            break
                except: break
            
            if self.proc.poll() is not None:
                raise RuntimeError("Engine crashed during render")

        if not (found_start and found_end):
            raise RuntimeError("Failed to capture full render from engine")

        # Extract b64
        start_marker = "[RENDER_DATA_START]"
        end_marker = "[RENDER_DATA_END]"
        
        start_idx = output_buffer.find(start_marker)
        end_idx = output_buffer.find(end_marker)
        
        # The data is between the metadata line and the end marker
        lines = output_buffer[start_idx:end_idx].splitlines()
        content = "".join(lines[1:]).strip()
        
        raw_data = base64.b64decode(content)
        
        img = Image.frombytes("RGB", (w, h), raw_data)
        return img