import socket,json,os,time
from collections import Counter
HOST,PORT="localhost",8765
OUT="/tmp/palette_func"; os.makedirs(OUT,exist_ok=True)
def send(cmd,p="",idle=2.5,hard=16.0):
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
def cap(t):
    cells=os.path.join(OUT,t+".cells"); bridge=os.path.join(OUT,t+".bridge")
    send("CAPTURE_TERMPP_FRAME_WITH_BUFFER",f"{os.path.join(OUT,t+'.png')} {cells} {bridge}",idle=3.0,hard=20.0); time.sleep(1.3)
    return cells,bridge
def lc(p):
    d={}
    for ln in open(p):
        o=json.loads(ln)
        if o.get("kind")=="cell": d[(o["x"],o["y"])]=(o["fg"],o["bk"])
    return d
def lm(p):
    d={}
    for ln in open(p):
        o=json.loads(ln)
        if o.get("kind")=="cell": d[(o["x"],o["y"])]=o.get("material_id")
    return d
print("OPEN", send("OPEN_TERMPP","harri=1",idle=3.0,hard=16.0)[-30:].replace("\n"," "))
time.sleep(2.5)
# ensure RENDERING tab is focused so the palette tiles are present/clickable
send("FL4260_FOCUS_SIDEBAR_TAB","9"); time.sleep(0.4); send("FL4260_SCROLL_Y","0"); time.sleep(0.5)
a,ba=cap("before"); mat=lm(ba)
# click Dracula tile (window-logical coords ~ display*1.333)
print("CLICK Dracula", send("RUN_MOUSE_CLICK_PROBE","580 184")[-40:].replace("\n"," "))
time.sleep(1.5)
b,_=cap("after")
A=lc(a); B=lc(b)
terr=[k for k in A if A.get(k)!=B.get(k) and mat.get(k)==4]
allch=[k for k in A if A.get(k)!=B.get(k)]
print(f"terrain(mat4) cells recolored = {len(terr)} / total changed = {len(allch)}")
print(f"sample mat4 before->after fg/bk: ", [(A[k],B[k]) for k in terr[:3]])
