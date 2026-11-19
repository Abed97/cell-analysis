"""Draw the app icon (three overlapping phase-coloured nuclei on a dark rounded square)."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 1024
here = Path(__file__).parent


def draw(size=S):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = int(size * 0.09)
    d.rounded_rectangle([pad, pad, size - pad, size - pad], radius=int(size * 0.2), fill=(21, 24, 27, 255))
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    r = size * 0.2
    for (cx, cy, col) in [(0.40, 0.42, (63, 191, 106)), (0.60, 0.42, (229, 72, 110)), (0.50, 0.60, (242, 201, 76))]:
        x, y = cx * size, cy * size
        g.ellipse([x - r, y - r, x + r, y + r], fill=col + (215,))
    glow_blur = glow.filter(ImageFilter.GaussianBlur(size * 0.03))
    img.alpha_composite(glow_blur)
    img.alpha_composite(glow)
    return img


if __name__ == "__main__":
    im = draw()
    im.save(here / "icon.png")
    im.save(here / "icon.icns", sizes=[(16, 16), (32, 32), (64, 64), (128, 128), (256, 256), (512, 512), (1024, 1024)])
    print("icon written")
