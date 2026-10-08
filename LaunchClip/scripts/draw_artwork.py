"""Draw the show's artwork in the Unbox house style, as flat retro-poster illustrations.

Stand-in for generated images while the ElevenLabs Image API is not available
(it needs a Pro plan). Writes the files listed under `artwork:` in show.yaml to
assets/screens/ and marks them as drawn in generated.json, so `unbox images`
may replace them later. Files that someone supplied by hand are left alone.

    uv run python scripts/draw_artwork.py            # draw the missing or drawn ones
    uv run python scripts/draw_artwork.py rocket.png # only this one

Layout rules: screen text sits in the upper middle (y 300-620), subtitles at the
bottom (y > 760), countdown numbers in the centre, thought clouds at y 470-720.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unbox_show.model import load_show  # noqa: E402

PROJECT = Path(__file__).resolve().parents[1]
W, H = 1920, 1080
S = 2  # supersampling for smooth edges

SHOW = load_show(PROJECT / "show.yaml")
C = {name: SHOW.style.rgb(name, (0, 0, 0)) for name in SHOW.style.colours}
DARK = C["purple_dark"]
NIGHT = tuple(max(0, round(v * 0.6)) for v in DARK)
PURPLE = C["purple"]
MAUVE = C["purple_muted"]
YELLOW = C["yellow"]
YELLOW_LIGHT = C["yellow_light"]
BLUE = C["blue_light"]
WHITE = (250, 248, 255)
RED = (226, 72, 72)


# --- helpers -------------------------------------------------------------------

def canvas(size=(W, H)):
    image = Image.new("RGBA", (size[0] * S, size[1] * S), (0, 0, 0, 0))
    return image, ImageDraw.Draw(image)


def p(*values):
    """Scale coordinates to the supersampled canvas."""
    return [round(v * S) for v in values]


def gradient(top, bottom, size=(W, H)):
    w, h = size[0] * S, size[1] * S
    column = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / (h - 1)
        column.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    return column.resize((w, h)).convert("RGBA")


def stars(draw, seed, count=160, area=(0, 0, W, H * 0.7), colour=WHITE):
    rnd = random.Random(seed)
    for _ in range(count):
        x, y = rnd.uniform(area[0], area[2]), rnd.uniform(area[1], area[3])
        r = rnd.choice((1, 1, 1.5, 2, 2.5))
        alpha = rnd.randint(90, 230)
        draw.ellipse(p(x - r, y - r, x + r, y + r), fill=(*colour, alpha))


def sparkle(draw, x, y, r, colour=YELLOW_LIGHT):
    draw.polygon(p(x, y - r, x + r * 0.25, y - r * 0.25, x + r, y, x + r * 0.25, y + r * 0.25,
                   x, y + r, x - r * 0.25, y + r * 0.25, x - r, y, x - r * 0.25, y - r * 0.25), fill=colour)


def grain(image):
    """A little print grain, as on a screen-printed poster."""
    noise = Image.effect_noise((image.width // 4, image.height // 4), 40).resize(image.size)
    rgb = image.convert("RGB")
    return Image.blend(rgb, Image.merge("RGB", (noise, noise, noise)), 0.04)


def layer(size=(W, H)):
    return canvas(size)


def paste(base, overlay):
    base.alpha_composite(overlay)


def rocket(draw, cx, base_y, height, tilt=0.0, flame=0.0):
    """A retro rocket: rounded body, yellow nose, purple fins, round window."""
    w = height * 0.26
    top = base_y - height

    def rot(x, y):
        if not tilt:
            return x, y
        a = math.radians(tilt)
        dx, dy = x - cx, y - (base_y - height / 2)
        return cx + dx * math.cos(a) - dy * math.sin(a), base_y - height / 2 + dx * math.sin(a) + dy * math.cos(a)

    def poly(points, fill):
        draw.polygon([v for x, y in points for v in p(*rot(x, y))], fill=fill)

    if flame:
        poly([(cx - w * 0.32, base_y - height * 0.02), (cx + w * 0.32, base_y - height * 0.02),
              (cx + w * 0.18, base_y + height * 0.35 * flame), (cx, base_y + height * 0.62 * flame),
              (cx - w * 0.18, base_y + height * 0.35 * flame)], YELLOW)
        poly([(cx - w * 0.17, base_y), (cx + w * 0.17, base_y), (cx, base_y + height * 0.38 * flame)], YELLOW_LIGHT)
    # fins
    poly([(cx - w / 2, base_y - height * 0.32), (cx - w * 1.05, base_y + height * 0.02), (cx - w / 2, base_y - height * 0.04)], PURPLE)
    poly([(cx + w / 2, base_y - height * 0.32), (cx + w * 1.05, base_y + height * 0.02), (cx + w / 2, base_y - height * 0.04)], PURPLE)
    # body
    body = []
    for i in range(0, 181, 6):
        a = math.radians(i)
        body.append((cx - w / 2 * math.cos(a), top + height * 0.28 - height * 0.28 * math.sin(a) ** 0.6))
    body += [(cx + w / 2, base_y - height * 0.04), (cx - w / 2, base_y - height * 0.04)]
    poly(body, WHITE)
    # nose
    nose = []
    for i in range(0, 181, 6):
        a = math.radians(i)
        nose.append((cx - w / 2 * math.cos(a) * 0.999, top + height * 0.28 - height * 0.28 * math.sin(a) ** 0.6))
    nose = [pt for pt in nose if pt[1] <= top + height * 0.2] or nose
    poly(nose + [(cx + w * 0.36, top + height * 0.2), (cx - w * 0.36, top + height * 0.2)], YELLOW)
    # stripe and window
    poly([(cx - w / 2, base_y - height * 0.2), (cx + w / 2, base_y - height * 0.2),
          (cx + w / 2, base_y - height * 0.14), (cx - w / 2, base_y - height * 0.14)], MAUVE)
    wx, wy = rot(cx, top + height * 0.42)
    r = w * 0.24
    draw.ellipse(p(wx - r * 1.25, wy - r * 1.25, wx + r * 1.25, wy + r * 1.25), fill=PURPLE)
    draw.ellipse(p(wx - r, wy - r, wx + r, wy + r), fill=BLUE)


def cloud_puffs(draw, cx, cy, size, colour, seed):
    rnd = random.Random(seed)
    for _ in range(9):
        dx, dy = rnd.uniform(-1, 1) * size, rnd.uniform(-0.35, 0.35) * size
        r = rnd.uniform(0.35, 0.6) * size
        draw.ellipse(p(cx + dx - r, cy + dy - r, cx + dx + r, cy + dy + r), fill=colour)


def gantry(draw, x, ground, height, width=90):
    col = (*MAUVE, 255)
    draw.rectangle(p(x, ground - height, x + 14, ground), fill=col)
    draw.rectangle(p(x + width - 14, ground - height, x + width, ground), fill=col)
    for y in range(int(ground - height), int(ground), 70):
        draw.line(p(x, y, x + width, y + 70), fill=col, width=8 * S)
        draw.line(p(x + width, y, x, y + 70), fill=col, width=8 * S)
    for y in range(int(ground - height) + 40, int(ground), 140):
        draw.ellipse(p(x + width / 2 - 7, y - 7, x + width / 2 + 7, y + 7), fill=YELLOW)


# --- the pictures ---------------------------------------------------------------

def draw_title():
    img = gradient(NIGHT, PURPLE)
    over, d = layer()
    stars(d, 1, 220, (0, 0, W, 700))
    d.ellipse(p(170, 90, 330, 250), fill=(*YELLOW_LIGHT, 255))           # moon
    d.ellipse(p(-500, 760, 900, 1900), fill=(*MAUVE, 255))               # planet rim
    d.rectangle(p(0, 930, W, H), fill=(*NIGHT, 255))                     # ground
    gantry(d, 1440, 930, 560)
    rocket(d, 1640, 930, 600)
    d.rectangle(p(1520, 920, 1760, 945), fill=(*DARK, 255))
    for x in (260, 420, 1180):
        sparkle(d, x, 330 if x != 1180 else 180, 18)
    paste(img, over)
    return img


def draw_pen():
    img = gradient(NIGHT, DARK)
    over, d = layer()
    stars(d, 2, 260, (0, 0, W, H))
    d.ellipse(p(1300, 650, 2500, 1850), fill=(*BLUE, 255))               # planet
    d.ellipse(p(1380, 730, 2420, 1770), fill=(*PURPLE, 255))
    # the pen, diagonal across the upper part
    pen = Image.new("RGBA", tuple(p(1300, 120)), (0, 0, 0, 0))
    pd = ImageDraw.Draw(pen)
    pd.rounded_rectangle(p(150, 20, 1060, 100), radius=40 * S, fill=YELLOW)
    pd.rectangle(p(860, 20, 900, 100), fill=WHITE)
    pd.polygon(p(1060, 22, 1260, 60, 1060, 98), fill=YELLOW_LIGHT)
    pd.polygon(p(1200, 49, 1260, 60, 1200, 71), fill=NIGHT)
    pd.rounded_rectangle(p(100, 30, 170, 90), radius=16 * S, fill=MAUVE)
    pd.rounded_rectangle(p(260, 0, 640, 26), radius=13 * S, fill=WHITE)   # clip
    pen = pen.rotate(14, resample=Image.BICUBIC, expand=True)
    over.alpha_composite(pen, tuple(p(330, 70)))
    for x, y in ((260, 360), (1600, 140), (900, 420)):
        sparkle(d, x, y, 16)
    paste(img, over)
    return img


def draw_bridge():
    img = gradient(DARK, NIGHT)
    over, d = layer()
    # two big round windows, left and right of the text area
    for cx in (250, 1670):
        d.ellipse(p(cx - 230, 120, cx + 230, 580), fill=(*MAUVE, 255))
        d.ellipse(p(cx - 200, 150, cx + 200, 550), fill=(*NIGHT, 255))
    win, wd = layer()
    stars(wd, 3, 160, (0, 120, W, 580))
    wd.ellipse(p(1600, 330, 1820, 550), fill=(*BLUE, 255))
    wd.ellipse(p(120, 190, 210, 280), fill=(*YELLOW_LIGHT, 255))
    mask, md = canvas()
    for cx in (250, 1670):
        md.ellipse(p(cx - 200, 150, cx + 200, 550), fill=(255, 255, 255, 255))
    over.alpha_composite(Image.composite(win, Image.new("RGBA", win.size, (0, 0, 0, 0)), mask.split()[3]))
    # console along the bottom
    d.polygon(p(0, 760, 520, 680, 1400, 680, W, 760, W, H, 0, H), fill=(*PURPLE, 255))
    d.polygon(p(0, 800, 520, 720, 1400, 720, W, 800, W, H, 0, H), fill=(*DARK, 255))
    rnd = random.Random(4)
    for _ in range(46):
        x, y = rnd.uniform(60, W - 60), rnd.uniform(725, 760)
        colour = rnd.choice((YELLOW, YELLOW_LIGHT, BLUE, WHITE))
        d.rounded_rectangle(p(x - 12, y - 6, x + 12, y + 6), radius=4 * S, fill=(*colour, 255))
    paste(img, over)
    return img


def draw_stickers():
    img = Image.new("RGBA", (W * S, H * S), (*MAUVE, 255))
    over, d = layer()
    rnd = random.Random(5)
    colours = (YELLOW, YELLOW_LIGHT, BLUE, PURPLE, WHITE)
    shapes = ("star", "heart", "drop", "bolt", "circle", "leaf")
    spots = [(x, y) for x in range(130, W, 260) for y in (140, 980)] + [(x, y) for x in (130, 1790) for y in (400, 680)]
    spots += [(370, 560), (1550, 560), (330, 830), (1590, 830)]
    for i, (x, y) in enumerate(spots):
        x += rnd.uniform(-30, 30)
        y += rnd.uniform(-25, 25)
        r = rnd.uniform(85, 110)
        d.ellipse(p(x - r + 8, y - r + 10, x + r + 8, y + r + 10), fill=(*NIGHT, 90))      # shadow
        d.ellipse(p(x - r, y - r, x + r, y + r), fill=(*colours[i % len(colours)], 255))
        icon = DARK if colours[i % len(colours)] not in (PURPLE,) else WHITE
        shape = shapes[i % len(shapes)]
        s = r * 0.5
        if shape == "star":
            pts = []
            for k in range(10):
                a = math.radians(-90 + k * 36)
                rr = s if k % 2 == 0 else s * 0.45
                pts += [x + rr * math.cos(a), y + rr * math.sin(a)]
            d.polygon(p(*pts), fill=(*icon, 255))
        elif shape == "heart":
            d.ellipse(p(x - s, y - s * 0.7, x, y + s * 0.2), fill=(*icon, 255))
            d.ellipse(p(x, y - s * 0.7, x + s, y + s * 0.2), fill=(*icon, 255))
            d.polygon(p(x - s * 0.97, y - s * 0.1, x + s * 0.97, y - s * 0.1, x, y + s), fill=(*icon, 255))
        elif shape == "drop":
            d.ellipse(p(x - s * 0.6, y - s * 0.1, x + s * 0.6, y + s * 0.9), fill=(*icon, 255))
            d.polygon(p(x - s * 0.55, y + s * 0.25, x + s * 0.55, y + s * 0.25, x, y - s), fill=(*icon, 255))
        elif shape == "bolt":
            d.polygon(p(x + s * 0.2, y - s, x - s * 0.55, y + s * 0.15, x - s * 0.02, y + s * 0.15,
                        x - s * 0.25, y + s, x + s * 0.55, y - s * 0.15, x + s * 0.05, y - s * 0.15), fill=(*icon, 255))
        elif shape == "circle":
            d.ellipse(p(x - s, y - s, x + s, y + s), outline=(*icon, 255), width=12 * S)
            d.ellipse(p(x - s * 0.35, y - s * 0.35, x + s * 0.35, y + s * 0.35), fill=(*icon, 255))
        else:
            d.ellipse(p(x - s, y - s * 0.45, x + s, y + s * 0.45), fill=(*icon, 255))
            d.line(p(x - s, y, x + s, y), fill=(*colours[i % len(colours)], 255), width=5 * S)
    paste(img, over)
    return img


def draw_alert():
    img = gradient((70, 22, 40), NIGHT)
    over, d = layer()
    for cx in (230, 1690):   # warning beacons with light cones
        cone, cd = layer()
        cd.polygon(p(cx, 210, cx - 420, 1080, cx + 420, 1080), fill=(*RED, 60))
        over.alpha_composite(cone.filter(ImageFilter.GaussianBlur(30 * S)))
        d.rounded_rectangle(p(cx - 70, 120, cx + 70, 230), radius=40 * S, fill=(*RED, 255))
        d.rectangle(p(cx - 90, 225, cx + 90, 255), fill=(*MAUVE, 255))
        d.ellipse(p(cx - 30, 140, cx + 10, 180), fill=(255, 190, 190, 255))
    stripes, sd = layer()
    for x in range(-200, W + 200, 120):  # hazard stripes along the top
        sd.polygon(p(x, 0, x + 60, 0, x + 60 - 70, 70, x - 70, 70), fill=(*YELLOW, 255))
    sd.rectangle(p(0, 70, W, 82), fill=(*NIGHT, 255))
    over.alpha_composite(stripes)
    paste(img, over)
    return img


def draw_one_item():
    img = gradient(NIGHT, DARK)
    over, d = layer()
    stars(d, 6, 90, (0, 0, W, 300))
    beam, bd = layer()
    bd.polygon(p(900, 0, 1020, 0, 1220, 760, 700, 760), fill=(*YELLOW_LIGHT, 70))
    over.alpha_composite(beam.filter(ImageFilter.GaussianBlur(14 * S)))
    # the hatch: a rocket hull with one open compartment, low in the frame
    d.rounded_rectangle(p(560, 600, 1360, 780), radius=36 * S, fill=(*MAUVE, 255))
    d.rounded_rectangle(p(840, 625, 1080, 760), radius=20 * S, fill=(*NIGHT, 255))
    d.ellipse(p(930, 700, 990, 735), fill=(*YELLOW_LIGHT, 200))
    for x in (610, 1250):
        d.rounded_rectangle(p(x, 640, x + 60, 740), radius=14 * S, fill=(*PURPLE, 255))
    for x in range(600, 1340, 70):
        d.ellipse(p(x, 765, x + 10, 775), fill=(*DARK, 255))
    paste(img, over)
    return img


def draw_rocket():
    img = gradient(DARK, PURPLE)
    over, d = layer()
    stars(d, 7, 140, (0, 0, W, 520))
    d.ellipse(p(240, 110, 360, 230), fill=(*YELLOW_LIGHT, 255))
    d.rectangle(p(0, 900, W, H), fill=(*NIGHT, 255))
    gantry(d, 1380, 900, 660, 100)
    rocket(d, 1620, 900, 720)
    d.rectangle(p(1480, 890, 1770, 915), fill=(*DARK, 255))
    cloud_puffs(d, 1520, 905, 90, (*BLUE, 230), 8)
    cloud_puffs(d, 1730, 905, 80, (*WHITE, 220), 9)
    paste(img, over)
    return img


def draw_liftoff():
    img = gradient(NIGHT, (150, 90, 120))
    over, d = layer()
    stars(d, 10, 160, (0, 0, W, 450))
    glow, gd = layer()
    gd.ellipse(p(560, 380, 1360, 1180), fill=(*YELLOW, 90))
    over.alpha_composite(glow.filter(ImageFilter.GaussianBlur(60 * S)))
    rocket(d, 960, 600, 520, flame=1.0)
    for i, (x, size, colour) in enumerate(((560, 230, MAUVE), (1360, 230, MAUVE), (760, 200, BLUE),
                                           (1160, 200, BLUE), (960, 180, WHITE), (300, 200, PURPLE),
                                           (1620, 200, PURPLE))):
        cloud_puffs(d, x, 1000, size, (*colour, 255), 20 + i)
    paste(img, over)
    return img


def icon_canvas():
    img = Image.new("RGBA", (512 * S, 512 * S), (*YELLOW_LIGHT, 255))
    return img, ImageDraw.Draw(img)


def draw_cloud_rocket():
    img, d = icon_canvas()
    rocket(d, 256, 420, 340)
    return img


def draw_cloud_text():
    img, d = icon_canvas()
    d.rounded_rectangle(p(86, 120, 256, 400), radius=18 * S, fill=WHITE, outline=MAUVE, width=8 * S)
    d.rounded_rectangle(p(256, 120, 426, 400), radius=18 * S, fill=WHITE, outline=MAUVE, width=8 * S)
    for y in range(170, 380, 42):
        d.line(p(116, y, 230, y), fill=BLUE, width=8 * S)
        d.line(p(282, y, 396, y), fill=BLUE, width=8 * S)
    d.line(p(256, 110, 256, 410), fill=PURPLE, width=10 * S)
    return img


def draw_cloud_carrot():
    img, d = icon_canvas()
    d.polygon(p(150, 160, 290, 140, 330, 190, 390, 420), fill=(240, 140, 50))
    for x0, y0 in ((250, 150), (290, 140), (330, 160)):
        d.polygon(p(x0, y0, x0 - 30, 60, x0 + 10, y0 - 10), fill=(96, 160, 90))
    for y in (220, 280, 340):
        d.line(p(200 + (y - 200) * 0.5, y, 250 + (y - 200) * 0.45, y - 8), fill=(200, 100, 40), width=6 * S)
    return img


DRAWINGS = {
    "title.png": draw_title,
    "pen.png": draw_pen,
    "bridge.png": draw_bridge,
    "stickers.png": draw_stickers,
    "alert.png": draw_alert,
    "one_item.png": draw_one_item,
    "rocket.png": draw_rocket,
    "liftoff.png": draw_liftoff,
    "cloud_rocket.png": draw_cloud_rocket,
    "cloud_text.png": draw_cloud_text,
    "cloud_carrot.png": draw_cloud_carrot,
}


def main(only: list[str]) -> None:
    folder = PROJECT / "assets" / "screens"
    folder.mkdir(parents=True, exist_ok=True)
    manifest_path = folder / "generated.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    for name, draw in DRAWINGS.items():
        if only and name not in only:
            continue
        target = folder / name
        record = manifest.get(name)
        ours = record and record.get("key") == "drawn" and hashlib.sha256(target.read_bytes()).hexdigest() == record["sha256"]
        if target.exists() and not ours:
            print(f"keep   {name} (supplied or generated)")
            continue
        image = draw()
        size = (W, H) if image.width == W * S else (image.width // S, image.height // S)
        image = grain(image).resize(size, Image.LANCZOS)
        image.save(target)
        manifest[name] = {"key": "drawn", "take": 0, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
        print(f"drawn  {name}")
    manifest_path.write_text(json.dumps(dict(sorted(manifest.items())), indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1:])
