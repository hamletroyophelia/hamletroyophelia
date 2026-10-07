#!/usr/bin/env python3
"""Compose the generated original pixel assets. Local-only: requires Pillow.

No image model credentials or data values are used by this compositor.
GitHub Actions runs sync_profile.py (stdlib) and does not run this script.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import math

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT/'assets'
FONT = '/System/Library/Fonts/Courier.ttc'

def compose():
    background = Image.open(ASSETS/'ai/hero-plate.png').convert('RGBA').resize((960,480), Image.Resampling.NEAREST)
    sheet = Image.open(ASSETS/'ai/character-sheet.png').convert('RGBA')
    poses = []
    for i in range(6):
        sprite = sheet.crop(((i%3)*512,(i//3)*512,(i%3+1)*512,(i//3+1)*512))
        # Consistent cells preserve the head/keyboard anchor across poses.
        sprite = sprite.resize((270,270),Image.Resampling.NEAREST)
        poses.append(sprite)
    frames=[]
    for frame in range(60):
        canvas=background.copy()
        pose=0 if frame<8 else 1 if frame<11 else 2 if frame<30 else 0 if frame<36 else 3 if frame<43 else 4 if frame<51 else 5
        canvas.alpha_composite(poses[pose],(605,100))
        draw=ImageDraw.Draw(canvas)
        # Crisp text is code-rendered so model lettering cannot corrupt labels.
        draw.text((68,120),'ROY',font=ImageFont.truetype(FONT,76),fill='#f4d58d',stroke_width=1)
        draw.text((73,209),'BARD / ENGINEER',font=ImageFont.truetype(FONT,25),fill='#a3ebcb')
        draw.text((73,251),'CODE · MUSIC · NEW QUESTS',font=ImageFont.truetype(FONT,17),fill='#edb6ce')
        draw.line((73,291,373,291),fill='#43546b',width=2)
        draw.text((73,316),'A new idea is a new quest.',font=ImageFont.truetype(FONT,16),fill='#c6b7ed')
        # Six-step sound bars, floating glyphs and star pulses join the sprite loop.
        for j in range(12):
            h=3+int((1+math.sin(frame*.28+j*.7))*6)
            draw.rectangle((74+j*9,381-h,78+j*9,381),fill='#a3ebcb' if j%3 else '#edb6ce')
        for j,(sx,sy) in enumerate([(235,62),(384,83),(517,142),(775,66),(492,282)]):
            pulse=(frame+j*9)%30
            if pulse<9:
                n=1+int(pulse<4)
                draw.line((sx-n*2,sy,sx+n*2,sy),fill='#f4d58d',width=1)
                draw.line((sx,sy-n*2,sx,sy+n*2),fill='#f4d58d',width=1)
        if 12<=frame<32:
            for j in range(3):
                x=770+j*27; y=150+j*16-int((frame-12)*1.2)
                draw.text((x,y),'♪',font=ImageFont.truetype('/System/Library/Fonts/Avenir Next.ttc',20),fill=['#a3ebcb','#edb6ce','#f4d58d'][j])
        frames.append(canvas.convert('RGB'))
    frames[0].save(ASSETS/'roy-rpg-still.png',optimize=True)
    palette=frames[0].quantize(colors=192,method=Image.Quantize.MEDIANCUT)
    quantized=[f.quantize(palette=palette,dither=Image.Dither.NONE) for f in frames]
    quantized[0].save(ASSETS/'roy-rpg-idle.gif',save_all=True,append_images=quantized[1:],duration=100,loop=0,optimize=True,disposal=1)
    print({'frames':len(frames),'gif_bytes':(ASSETS/'roy-rpg-idle.gif').stat().st_size})

if __name__=='__main__': compose()
