#!/usr/bin/env python3
"""Bake the app icon and the home screen's background from Kiel's streets.

    python3 ios/tools/bake-art.py ../kiel-earth/public/bike/landing-streets.json

Both are the kiel.earth landing page's sand, settled on the streets instead of
on a Chladni figure: grains scattered along every street of the landing page's
street file (kiel-earth tools/bake-landing-streets.mjs), heavier on main roads,
a few left loose. The icon zooms in on the förde and draws one ride along the
Kiellinie and Düsternbrooker Weg in ink; the background is the same streets
faint, at the landing page's scale. Seeded, so a re-run gives the same pixels.

Re-run when landing-streets.json changes. Needs Pillow.
"""
import json
import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ASSETS = Path(__file__).resolve().parent.parent / "KielEarth" / "Assets.xcassets"
GROUND = (10, 10, 10)      # Palette.ground
INK = (232, 232, 232)      # Palette.ink
MESH = (100, 100, 100)     # the icon's streets, so the ride stands out
FAINT = (58, 58, 58)       # the background's streets: about the site's plate at 0.3 opacity
RIDE = {"Kiellinie", "Düsternbrooker Weg"}


def ways(streets, names=None):
    """Each street as (render class, [(x, y)]) in metres from the file's
    origin, y to the south. Layout: [class, nameIdx, x0, y0, dx, dy, ...]."""
    U = streets["unit"]
    for w in streets["ways"]:
        if names is not None and (w[1] < 0 or streets["names"][w[1]] not in names):
            continue
        x, y = w[2], w[3]
        pts = [(x * U, y * U)]
        for i in range(4, len(w), 2):
            x += w[i]
            y += w[i + 1]
            pts.append((x * U, y * U))
        yield w[0], pts


class Sand:
    """Square grains on the ground, drawn at twice the size and scaled down."""

    def __init__(self, w, h, ss=2):
        self.w, self.h, self.ss = w, h, ss
        self.img = Image.new("RGB", (w * ss, h * ss), GROUND)
        self.draw = ImageDraw.Draw(self.img, "RGBA")

    def grain(self, x, y, size, colour, alpha=255):
        s = self.ss
        self.draw.rectangle([x * s, y * s, (x + size) * s - 1, (y + size) * s - 1], fill=colour + (alpha,))

    def image(self):
        return self.img.resize((self.w, self.h), Image.LANCZOS)


def view(w, h, span_m, cx_m, cy_m):
    """Metres to pixels: span_m across the longer side, centred on (cx_m, cy_m)."""
    s = max(w, h) / span_m
    return lambda x, y: (w / 2 + (x - cx_m) * s, h / 2 + (y - cy_m) * s)


def scatter(sand, rng, proj, lines, *, density, sigma, grain, colour, alpha=255, main=1.0):
    """Grains along each line, density per pixel of length, spread across it."""
    for cls, pts in lines:
        heavy = cls <= 1                     # motorway, trunk, primary, secondary
        boost = main if heavy else 1.0
        spread = sigma * (1.25 if heavy else 1.0)
        P = [proj(x, y) for x, y in pts]
        for (ax, ay), (bx, by) in zip(P, P[1:]):
            if max(ax, bx) < -20 or min(ax, bx) > sand.w + 20 or max(ay, by) < -20 or min(ay, by) > sand.h + 20:
                continue
            L = math.hypot(bx - ax, by - ay)
            n = L * density * boost
            k = int(n) + (1 if rng.random() < n - int(n) else 0)
            if L == 0:
                continue
            nx, ny = -(by - ay) / L, (bx - ax) / L
            for _ in range(k):
                t = rng.random()
                o = rng.gauss(0, spread)
                sand.grain(ax + (bx - ax) * t + nx * o - grain / 2,
                           ay + (by - ay) * t + ny * o - grain / 2, grain, colour, alpha)


def loose(sand, rng, n, grain, colour):
    for _ in range(n):
        sand.grain(rng.random() * sand.w, rng.random() * sand.h, grain, colour)


def icon(streets):
    # 6.4 km square over the förde: the city on the left, the water down the
    # middle, the east shore at the bottom right.
    S = 1024
    sand = Sand(S, S)
    proj = view(S, S, 6400, 1100, -800)
    rng = random.Random(3)
    main_roads = [(c, p) for c, p in ways(streets) if c in (0, 1, 2)]
    scatter(sand, rng, proj, main_roads, density=0.55, sigma=1.7, grain=4.5, colour=MESH, main=1.5)
    loose(sand, rng, 160, 4.5, MESH)
    ride = [(9, p) for _, p in ways(streets, RIDE)]
    scatter(sand, random.Random(9), proj, ride, density=3.4, sigma=4.0, grain=9, colour=INK)
    return sand.image()


def background(streets):
    # 402 x 874 pt at 3x, 9.5 km tall like the landing page, the förde on the right.
    W, H = 1206, 2622
    sand = Sand(W, H)
    proj = view(W, H, 9500, 300, -600)
    rng = random.Random(5)
    scatter(sand, rng, proj, list(ways(streets)), density=0.9, sigma=1.6, grain=3.4, colour=FAINT, main=1.5)
    loose(sand, rng, 500, 3.4, FAINT)
    return sand.image().convert("L")


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    streets = json.loads(Path(sys.argv[1]).read_text())
    icon(streets).save(ASSETS / "AppIcon.appiconset" / "AppIcon.png", optimize=True)
    background(streets).save(ASSETS / "Streets.imageset" / "Streets.png", optimize=True)


if __name__ == "__main__":
    main()
