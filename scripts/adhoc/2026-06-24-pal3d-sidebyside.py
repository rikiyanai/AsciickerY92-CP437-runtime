from PIL import Image, ImageDraw
import os
a="/tmp/pal3d_before/ui_frame.png"; b="/tmp/pal3d_after/ui_frame.png"
# crop the 3D view (right ~half) for both
ia=Image.open(a).convert("RGB"); ib=Image.open(b).convert("RGB")
W,H=ia.size
box=(int(W*0.52),0,W,H)
ca=ia.crop(box); cb=ib.crop(box)
out=Image.new("RGB",(ca.width*2+16,ca.height+20),(20,20,20))
d=ImageDraw.Draw(out)
out.paste(ca,(0,20)); out.paste(cb,(ca.width+16,20))
d.text((4,4),"BEFORE (green)",fill=(255,255,0)); d.text((ca.width+20,4),"AFTER Dracula click",fill=(255,255,0))
dst="/tmp/pal3d_sidebyside.png"; out.save(dst); print("WROTE",dst,out.size)
# also sample average terrain color in a grass region of both
import statistics
def avg(im, x0,y0,x1,y1):
    px=[im.getpixel((x,y)) for x in range(x0,x1,8) for y in range(y0,y1,8)]
    return tuple(round(statistics.mean(c[i] for c in px)) for i in range(3))
print("before grass-region avg:", avg(ia, int(W*0.6),int(H*0.55),int(W*0.8),int(H*0.75)))
print("after  grass-region avg:", avg(ib, int(W*0.6),int(H*0.55),int(W*0.8),int(H*0.75)))
