import socket,json,os,time,statistics
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
def cap(n):
    d=f"/tmp/pdrag_{n}"; os.makedirs(d,exist_ok=True)
    send("CAPTURE_UI_FRAME", d, idle=2.5, hard=10.0); time.sleep(0.5)
    return os.path.join(d,"ui_frame.png")
send("FL4260_FOCUS_SIDEBAR_TAB","9"); time.sleep(0.4); send("FL4260_SCROLL_Y","0"); time.sleep(0.5)
before=cap("before")
# drag Dracula tile (580,184) onto terrain:0 row (387,287)
print("DRAG", send("RUN_SDL_MOUSE_DRAG_PROBE","580 184 387 287 18")[-50:].replace("\n"," "))
time.sleep(1.5)
after=cap("after")
print("before/after captured:", os.path.exists(before), os.path.exists(after))
from PIL import Image
def avg(im,x0,y0,x1,y1):
    px=[im.getpixel((x,y)) for x in range(x0,x1,6) for y in range(y0,y1,6)]
    return tuple(round(statistics.mean(c[i] for c in px)) for i in range(3))
ia=Image.open(before).convert("RGB"); ib=Image.open(after).convert("RGB")
W,H=ia.size
# terrain region in the 3D view (right side, grass)
reg=(int(W*0.62),int(H*0.55),int(W*0.82),int(H*0.78))
print("terrain avg BEFORE:", avg(ia,*reg))
print("terrain avg AFTER :", avg(ib,*reg))
