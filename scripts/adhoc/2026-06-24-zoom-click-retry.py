import socket,json,os,time
HOST,PORT="localhost",8765
def send(cmd,p="",idle=2.0,hard=10.0):
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
send("FL4260_FOCUS_SIDEBAR_TAB","9"); time.sleep(0.4); send("FL4260_SCROLL_Y","0"); time.sleep(0.4)
# top zoom row at window y~40. A+ ~x=367, Keys ~x=540
for _ in range(4):
    send("RUN_MOUSE_CLICK_PROBE","367 40"); time.sleep(0.25)  # A+ x4
send("RUN_MOUSE_CLICK_PROBE","545 40"); time.sleep(0.4)       # Keys toggle
send("FL4260_SCROLL_Y","0"); time.sleep(0.4)
d="/tmp/zoom_bigger2"; os.makedirs(d,exist_ok=True)
send("CAPTURE_UI_FRAME", d, idle=2.5, hard=10.0)
print("CAP", os.path.exists(os.path.join(d,"ui_frame.png")))
