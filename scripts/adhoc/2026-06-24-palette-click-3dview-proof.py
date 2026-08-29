import socket,json,os,time
HOST,PORT="localhost",8765
def send(cmd,p="",idle=2.5,hard=12.0):
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
def cap(name):
    d=f"/tmp/pal3d_{name}"; os.makedirs(d,exist_ok=True)
    send("CAPTURE_UI_FRAME", d, idle=2.5, hard=10.0); time.sleep(0.5)
    return os.path.join(d,"ui_frame.png")
send("FL4260_FOCUS_SIDEBAR_TAB","9"); time.sleep(0.4); send("FL4260_SCROLL_Y","0"); time.sleep(0.5)
p_before=cap("before")
print("before", os.path.exists(p_before))
print("CLICK Dracula", send("RUN_MOUSE_CLICK_PROBE","580 184")[-40:].replace("\n"," "))
time.sleep(1.2)
p_after=cap("after")
print("after", os.path.exists(p_after))
