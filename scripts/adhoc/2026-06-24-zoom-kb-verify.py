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
send("FL4260_FOCUS_SIDEBAR_TAB","9"); time.sleep(0.4); send("FL4260_SCROLL_Y","0"); time.sleep(0.5)
d="/tmp/zoom_default"; os.makedirs(d,exist_ok=True)
send("CAPTURE_UI_FRAME", d, idle=2.5, hard=10.0)
print("CAP default", os.path.exists(os.path.join(d,"ui_frame.png")))
# click "Keys >" (top row, after zoom buttons) and A+ twice to test zoom
# zoom row at panel top; Keys button ~ window x=470 y=160; A+ ~ x=275 y=160 (display->window *1.333)
send("RUN_MOUSE_CLICK_PROBE","465 160"); time.sleep(0.4)  # Keys toggle
send("RUN_MOUSE_CLICK_PROBE","262 160"); time.sleep(0.3)  # A+
send("RUN_MOUSE_CLICK_PROBE","262 160"); time.sleep(0.3)  # A+
send("FL4260_SCROLL_Y","0"); time.sleep(0.4)
d2="/tmp/zoom_keys_bigger"; os.makedirs(d2,exist_ok=True)
send("CAPTURE_UI_FRAME", d2, idle=2.5, hard=10.0)
print("CAP after", os.path.exists(os.path.join(d2,"ui_frame.png")))
