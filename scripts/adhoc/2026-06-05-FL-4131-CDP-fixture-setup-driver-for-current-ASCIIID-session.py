# Ad hoc script: FL-4131 CDP fixture setup driver for current ASCIIID session
# Created: 2026-06-05
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
import json, socket, sys, time
HOST='127.0.0.1'
PORT=int(sys.argv[1]) if len(sys.argv)>1 else 9333
CMDS=sys.argv[2:]
if not CMDS:
    CMDS=['GET_LOADED_MAP']
s=socket.create_connection((HOST,PORT),timeout=10)
s.settimeout(30)
buf=b''
def req(i,method,params=''):
    global buf
    s.sendall((json.dumps({'id':i,'method':method,'params':params})+'\n').encode())
    while b'\n' not in buf:
        chunk=s.recv(65536)
        if not chunk: raise RuntimeError('cdp closed')
        buf += chunk
    line, buf = buf.split(b'\n',1)
    return json.loads(line.decode()).get('result','')
for i,cmdline in enumerate(CMDS,1):
    parts=cmdline.split(' ',1)
    method=parts[0]
    params=parts[1] if len(parts)>1 else ''
    out=req(i,method,params)
    print(f'[{method}] {out.strip()}')
s.close()
