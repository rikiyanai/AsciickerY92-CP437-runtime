# Ad hoc script: FL-4260 RQ-154c headed CDP proof client: drives asciiid GPU winner pass in PROFILE mode and dumps bridge/parity counters
# Created: 2026-06-15
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4260 RQ-154c headed proof CDP client.

Connects to a running `.run/asciiid --cdp PORT`, sends a sequence of MCP
commands (newline-delimited JSON {"id","method","params"}), and prints each
response. Used to mount the TERM++ GPU FBO presenter, set PROFILE mode, let the
render loop run the routed winner shader, then dump the GPU bridge so CPU/GPU
parity (cpu_gpu_disagree) and the routed winner can be observed headed.

Usage: python3 <this> PORT "CMD1|args" "CMD2|args" ... [--sleep SECONDS]
Each arg is "METHOD" or "METHOD|PARAMS". --sleep waits between commands so the
GL render loop runs frames (default 0.5s).
"""
import socket, sys, json, time

def main():
    args = [a for a in sys.argv[1:]]
    sleep = 0.5
    if "--sleep" in args:
        i = args.index("--sleep"); sleep = float(args[i+1]); del args[i:i+2]
    port = int(args[0]); cmds = args[1:]
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    s.settimeout(10)
    buf = b""
    rid = 0
    for c in cmds:
        rid += 1
        if "|" in c:
            method, params = c.split("|", 1)
        else:
            method, params = c, ""
        req = json.dumps({"id": rid, "method": method, "params": params}) + "\n"
        s.sendall(req.encode())
        # read one newline-terminated response
        deadline = time.time() + 10
        while b"\n" not in buf and time.time() < deadline:
            try:
                chunk = s.recv(65536)
                if not chunk: break
                buf += chunk
            except socket.timeout:
                break
        line, _, buf = buf.partition(b"\n")
        print(f">>> {method} {params}".rstrip())
        try:
            obj = json.loads(line.decode(errors="replace"))
            print(obj.get("result", obj))
        except Exception:
            print(line.decode(errors="replace"))
        time.sleep(sleep)
    s.close()

if __name__ == "__main__":
    main()
