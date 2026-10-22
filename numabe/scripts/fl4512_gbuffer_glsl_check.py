"""FL-4512: glslang check of true_ff_gbuffer.glsl vertex and fragment stages.

usage: fl4512_gbuffer_glsl_check.py [BLOB]  (BLOB: HEAD-based candidate blob)
"""
import subprocess,tempfile,re,io,tarfile,pathlib,sys
R=pathlib.Path(__file__).resolve().parents[1]
def expand_text(text,proj):
    out=[]
    for line in text.splitlines():
        m=re.match(r'\s*#include\s+"res://([^"]+)"',line)
        if m: out.append(expand_text((proj/m.group(1)).read_text(),proj)); continue
        if line.strip().startswith('#[') or line.strip().startswith('#version'): continue
        out.append(line)
    return "\n".join(out)
def check(proj,label):
    raw=(proj/'render/ascii/structure/true_ff_gbuffer.glsl').read_text()
    parts=re.split(r'^#\[(vertex|fragment)\]\s*$',raw,flags=re.M)
    for i in range(1,len(parts),2):
        stage='vert' if parts[i]=='vertex' else 'frag'
        with tempfile.NamedTemporaryFile('w',suffix='.'+stage,delete=False) as f: f.write("#version 450\n"+expand_text(parts[i+1],proj))
        r=subprocess.run(['glslangValidator','-V','-S',stage,'-o','/dev/null',f.name],capture_output=True,text=True)
        print(label,stage,'OK' if r.returncode==0 else 'FAIL '+r.stdout[-400:])
check(R/'godot_project','worktree')
if len(sys.argv)>1:
    d=pathlib.Path(tempfile.mkdtemp())
    tar=subprocess.run(['git','archive','HEAD','godot_project/render'],capture_output=True,cwd=R).stdout
    tarfile.open(fileobj=io.BytesIO(tar)).extractall(d)
    (d/'godot_project/render/ascii/structure/true_ff_gbuffer.glsl').write_bytes(subprocess.run(['git','cat-file','-p',sys.argv[1]],capture_output=True,cwd=R).stdout)
    check(d/'godot_project','HEAD+change')
