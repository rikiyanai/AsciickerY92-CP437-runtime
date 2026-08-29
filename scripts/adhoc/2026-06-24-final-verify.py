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
# palette UI
send("FL4260_FOCUS_SIDEBAR_TAB","9"); time.sleep(0.4); send("FL4260_SCROLL_Y","0"); time.sleep(0.5)
d="/tmp/final_palette"; os.makedirs(d,exist_ok=True)
send("CAPTURE_UI_FRAME", d, idle=2.5, hard=10.0)
print("palette cap", os.path.exists(os.path.join(d,"ui_frame.png")))
# health bar toggle: open termpp, capture, press N, capture
print("OPEN", send("OPEN_TERMPP","harri=1", idle=3.0, hard=16.0)[-20:].replace("\n"," "))
time.sleep(2.5)
def cap(t):
    dd="/tmp/"+t; os.makedirs(dd,exist_ok=True)
    send("CAPTURE_TERMPP_FRAME_WITH_BUFFER", f"{dd}/f.png {dd}/c.jsonl {dd}/b.jsonl", idle=3.0, hard=18.0); time.sleep(1.2)
    return dd+"/f.png"
hb_on=cap("hb_on")
print("press N (SDL scancode 17)", send("RUN_SDL_KEY","17 1")[-30:].replace("\n"," "))
time.sleep(1.0)
hb_off=cap("hb_off")
print("hb_on", os.path.exists(hb_on), "hb_off", os.path.exists(hb_off))
