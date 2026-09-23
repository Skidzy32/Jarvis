"""make_icon.py -- draws Jarvis's app icon (arc-reactor style) with Pillow.
Only needed when the icon changes; the results are in assets/ and
viewer/assets/. Run: python make_icon.py (needs: pip install pillow)."""
import math, os
from PIL import Image, ImageDraw, ImageFilter

S = 1024
HERE = os.path.dirname(os.path.abspath(__file__))


def draw(size=S):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    c = size / 2
    d = ImageDraw.Draw(img)
    # dark rounded tile
    d.rounded_rectangle([size * 0.04, size * 0.04, size * 0.96, size * 0.96], radius=size * 0.22, fill=(8, 14, 26, 255))
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    cyan = (95, 227, 255, 255)
    def ring(r, w, col=cyan, start=0, end=360):
        g.arc([c - r, c - r, c + r, c + r], start, end, fill=col, width=int(w))
    ring(size * 0.38, size * 0.018, (95, 227, 255, 150))
    for a in range(0, 360, 6):                                  # tick ring
        x1, y1 = c + size * 0.345 * math.cos(math.radians(a)), c + size * 0.345 * math.sin(math.radians(a))
        x2, y2 = c + size * 0.365 * math.cos(math.radians(a)), c + size * 0.365 * math.sin(math.radians(a))
        g.line([x1, y1, x2, y2], fill=(95, 227, 255, 170), width=max(1, int(size * 0.006)))
    for s0 in (20, 140, 260):                                   # three arc segments
        ring(size * 0.30, size * 0.028, cyan, s0, s0 + 85)
    for s0 in range(0, 360, 30):                                # inner dashes
        ring(size * 0.225, size * 0.03, (159, 240, 255, 230), s0, s0 + 14)
    g.ellipse([c - size * 0.16, c - size * 0.16, c + size * 0.16, c + size * 0.16], outline=(95, 227, 255, 200), width=int(size * 0.01))
    blur = glow.filter(ImageFilter.GaussianBlur(size * 0.012))
    img = Image.alpha_composite(img, blur)
    img = Image.alpha_composite(img, glow)
    core = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    k = ImageDraw.Draw(core)
    for i in range(48, 0, -1):                                  # glowing core: cyan halo -> white-hot centre
        t = 1 - i / 48
        r = size * 0.19 * i / 48
        a = int(255 * min(1.0, 0.25 + t ** 0.5))
        k.ellipse([c - r, c - r, c + r, c + r], fill=(int(60 + 195 * t ** 2), int(200 + 55 * t), 255, a))
    img = Image.alpha_composite(img, core.filter(ImageFilter.GaussianBlur(size * 0.01)))
    return img


def main():
    big = draw()
    os.makedirs(os.path.join(HERE, "assets"), exist_ok=True)
    os.makedirs(os.path.join(HERE, "viewer", "assets"), exist_ok=True)
    big.resize((512, 512), Image.LANCZOS).save(os.path.join(HERE, "assets", "jarvis.png"))
    big.resize((256, 256), Image.LANCZOS).save(os.path.join(HERE, "viewer", "assets", "jarvis.png"))
    big.save(os.path.join(HERE, "assets", "jarvis.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    big.save(os.path.join(HERE, "assets", "jarvis.icns"))
    print("icons written to assets/ and viewer/assets/")


if __name__ == "__main__":
    main()
