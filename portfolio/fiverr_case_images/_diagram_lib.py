from PIL import Image, ImageDraw, ImageFont

F = "/usr/share/fonts/truetype/dejavu/"
def f(n, s): return ImageFont.truetype(F + n, s)

BG    = (13, 17, 23)
CARD  = (22, 27, 34)
EDGE  = (48, 54, 61)
GREEN = (29, 191, 115)
AMBER = (232, 160, 54)
RED   = (218, 84, 78)
WHITE = (240, 246, 252)
GREY  = (139, 148, 158)

W, H = 1024, 768

def new():
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 10, H], fill=GREEN)
    return img, d

def header(d, eyebrow, title):
    d.text((56, 52), eyebrow, font=f("DejaVuSans-Bold.ttf", 19), fill=GREEN)
    d.text((56, 84), title, font=f("DejaVuSans-Bold.ttf", 40), fill=WHITE)

def footer(d, tech, note=None):
    d.line([56, H - 96, W - 48, H - 96], fill=EDGE, width=2)
    d.text((56, H - 78), tech, font=f("DejaVuSans-Bold.ttf", 17), fill=GREY)
    if note:
        d.text((56, H - 50), note, font=f("DejaVuSans.ttf", 16), fill=GREY)

def box(d, x, y, w, h, label, sub=None, accent=EDGE, fill=CARD, lab_size=19, sub_size=14):
    d.rounded_rectangle([x, y, x + w, y + h], radius=9, fill=fill, outline=accent, width=2)
    fnt = f("DejaVuSans-Bold.ttf", lab_size)
    lines = label.split("\n")
    total = len(lines) * (lab_size + 5) + (sub_size + 6 if sub else 0)
    cy = y + (h - total) / 2
    for ln in lines:
        tw = d.textlength(ln, font=fnt)
        d.text((x + (w - tw) / 2, cy), ln, font=fnt, fill=WHITE)
        cy += lab_size + 5
    if sub:
        sf = f("DejaVuSans.ttf", sub_size)
        for ln in sub.split("\n"):
            tw = d.textlength(ln, font=sf)
            d.text((x + (w - tw) / 2, cy), ln, font=sf, fill=GREY)
            cy += sub_size + 3

def arrow(d, x1, y1, x2, y2, color=GREEN, width=3, head=9):
    d.line([x1, y1, x2, y2], fill=color, width=width)
    if x2 > x1:
        d.polygon([(x2, y2), (x2 - head, y2 - head * 0.7), (x2 - head, y2 + head * 0.7)], fill=color)
    elif y2 > y1:
        d.polygon([(x2, y2), (x2 - head * 0.7, y2 - head), (x2 + head * 0.7, y2 - head)], fill=color)
    elif y2 < y1:
        d.polygon([(x2, y2), (x2 - head * 0.7, y2 + head), (x2 + head * 0.7, y2 + head)], fill=color)
