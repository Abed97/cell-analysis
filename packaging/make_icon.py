"""Draw the Mitosight app icon: a green and a red ring (two daughter nuclei) overlapping into a
yellow eye with a dark pupil, on a dark rounded square laid out on Apple's 1024 px icon grid.

python packaging/make_icon.py  ->  packaging/icon.png, packaging/icon.icns, pipfucci/web/icon.png
"""
from math import cos, radians, sin
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

S = 1024
SS = 4                      # supersampling for smooth edges
here = Path(__file__).parent

GROUND = (21, 26, 32, 255)      # #151A20
EDGE = (42, 48, 56, 255)        # #2A3038
GREEN = (52, 211, 153, 255)     # #34D399  G1
RED = (251, 69, 112, 255)       # #FB4570  S
YELLOW = (252, 196, 25, 255)    # #FCC419  G2/M


def draw(size: int = S) -> Image.Image:
    n = size * SS
    tile = 824 / 1024 * n          # Apple's icon grid: 824 px tile, 100 px margin
    off = (n - tile) / 2
    u = tile / 100                 # the mark is drawn in a 0-100 box, like the SVG

    def P(x, y):
        return off + x * u, off + y * u

    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    # soft shadow under the tile
    sh = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([off, off + 0.012 * n, off + tile, off + tile + 0.012 * n],
                                         radius=0.225 * tile, fill=(0, 0, 0, 110))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(0.018 * n)))

    d = ImageDraw.Draw(img)
    d.rounded_rectangle([off, off, off + tile, off + tile], radius=0.225 * tile, fill=GROUND,
                        outline=EDGE, width=max(1, int(0.6 * u)))

    # the yellow lens where the two cells overlap
    a = np.degrees(np.arctan2(np.sqrt(22 ** 2 - 10 ** 2), 10))
    lens = [P(40 + 22 * cos(radians(t)), 50 + 22 * sin(radians(t))) for t in np.linspace(-a, a, 90)]
    lens += [P(60 + 22 * cos(radians(t)), 50 + 22 * sin(radians(t))) for t in np.linspace(180 - a, 180 + a, 90)]
    d.polygon(lens, fill=YELLOW)

    def ring(cx, cy, r, w, col):
        x0, y0 = P(cx - r - w / 2, cy - r - w / 2)
        x1, y1 = P(cx + r + w / 2, cy + r + w / 2)
        d.ellipse([x0, y0, x1, y1], outline=col, width=int(round(w * u)))

    ring(40, 50, 22, 5, GREEN)
    ring(60, 50, 22, 5, RED)
    x0, y0 = P(44, 44)
    x1, y1 = P(56, 56)
    d.ellipse([x0, y0, x1, y1], fill=GROUND)   # pupil
    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    im = draw()
    im.save(here / "icon.png")
    im.save(here / "icon.icns", sizes=[(16, 16), (32, 32), (64, 64), (128, 128), (256, 256), (512, 512), (1024, 1024)])
    im.resize((512, 512), Image.LANCZOS).save(here.parent / "pipfucci" / "web" / "icon.png")
    print("icon written")
