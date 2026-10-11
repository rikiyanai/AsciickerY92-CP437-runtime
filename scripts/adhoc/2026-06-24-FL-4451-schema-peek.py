#!/usr/bin/env python3
"""Open TERM++ and capture one frame to inspect the cells+bridge JSONL schema."""
import socket, json, os, time
HOST, PORT = "localhost", 8765
OUT = "/tmp/fl4451_peek"; os.makedirs(OUT, exist_ok=True)
def send(cmd, params="", idle=2.0, hard=14.0):
    s=socket.socket(socket.AF_INET,socket.SOCK_STREAM); s.settimeout(4)
    try:
        s.connect((HOST,PORT)); s.sendall((json.dumps({"id":1,"method":cmd,"params":params})+"\n").encode())
        s.settimeout(idle); buf=b""; t0=time.time()
        while time.time()-t0<hard:
            try:
                c=s.recv(65536)
                if not c: break
                buf+=c
            except socket.timeout: break
        return buf.decode(errors="replace")
    finally: s.close()
print("OPEN", send("OPEN_TERMPP","harri=1", idle=3.0, hard=16.0)[-80:].replace("\n"," "))
time.sleep(2.5)
png=os.path.join(OUT,"f.png"); cells=os.path.join(OUT,"cells.jsonl"); bridge=os.path.join(OUT,"bridge.jsonl")
print("CAP", send("CAPTURE_TERMPP_FRAME_WITH_BUFFER", f"{png} {cells} {bridge}", idle=3.0, hard=18.0)[-90:].replace("\n"," "))
time.sleep(2.0)
for f in (cells, bridge):
    print(f"--- {f} exists={os.path.exists(f)} ---")
    if os.path.exists(f):
        with open(f) as fh:
            for i,line in enumerate(fh):
                if i<2: print(line.rstrip()[:300])
                else: break
