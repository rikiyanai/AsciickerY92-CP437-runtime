import socket,json,os,time
HOST,PORT="localhost",8765
def send(cmd,p="",idle=2.0,hard=12.0):
    s=socket.socket(); s.settimeout(4)
    try:
        s.connect((HOST,PORT)); s.sendall((json.dumps({"id":1,"method":cmd,"params":p})+"\n").encode())
        s.settimeout(idle); b=b""; t=time.time()
        while time.time()-t<hard:
            try:
                c=s.recv(65536)
                if not c: break
                b+=c
            except socket.timeout: break
        return b.decode(errors="replace")
    finally: s.close()
print("EDIT tab click", send("RUN_MOUSE_CLICK_PROBE","203 35")[-50:].replace("\n"," "))
time.sleep(0.6)
print("PALETTIZE click", send("RUN_MOUSE_CLICK_PROBE","60 200")[-50:].replace("\n"," "))  # PALETTIZE-ish area
time.sleep(0.6)
d="/tmp/palette_ui2"; os.makedirs(d,exist_ok=True)
send("CAPTURE_UI_FRAME", d, idle=2.5, hard=10.0)
print("CAP", os.path.exists(os.path.join(d,"ui_frame.png")))
