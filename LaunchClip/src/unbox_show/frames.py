"""Frame renderer: one picture per distinct on-screen state.

Missing artwork is replaced by a labelled placeholder with the same name, so
the whole show renders before any real image exists. The bottom of the frame
is left free for the burned-in subtitles.
"""

from __future__ import annotations

import functools
import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .model import Screen, ShowFile

BASE_WIDTH = 1920
BACKGROUND = (11, 21, 48)
GRID = (22, 36, 72)
WHITE = (240, 244, 255)
DIM = (120, 135, 170)
NL_YELLOW = (255, 240, 153)
TIMER_RED = (235, 60, 60)
OPS_AMBER = (255, 176, 32)
TIMER_RED_FROM = 10  # seconds remaining


@dataclass(frozen=True)
class FrameState:
    screen: str | None = None
    overlays: tuple[str, ...] = ()
    big: str | None = None
    timer: int | None = None  # whole seconds remaining
    ops: str | None = None

    def digest(self, extra: str = "") -> str:
        return hashlib.sha256((repr(sorted(asdict(self).items())) + extra).encode()).hexdigest()[:20]


@functools.lru_cache(maxsize=64)
def font(size: int, fonts_dir: str | None = None) -> ImageFont.FreeTypeFont:
    """The first .ttf in assets/fonts/ if there is one, else Pillow's built-in font."""
    if fonts_dir:
        candidates = sorted(Path(fonts_dir).glob("*.ttf")) + sorted(Path(fonts_dir).glob("*.otf"))
        if candidates:
            return ImageFont.truetype(str(candidates[0]), size)
    return ImageFont.load_default(size=size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, fnt, max_width: int) -> list[str]:
    rows, row = [], ""
    for word in text.split():
        candidate = f"{row} {word}" if row else word
        if not row or draw.textlength(candidate, font=fnt) <= max_width:
            row = candidate
        else:
            rows.append(row)
            row = word
    if row:
        rows.append(row)
    return rows


def _text_block(draw, text: str, fnt, colour, center_x: int, top: int, max_width: int, spacing: float = 1.2) -> int:
    """Draw wrapped, centred text; return the y below it."""
    line_height = int(fnt.size * spacing)
    for row in _wrap(draw, text, fnt, max_width):
        draw.text((center_x, top), row, font=fnt, fill=colour, anchor="ma")
        top += line_height
    return top


def _cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Scale and centre-crop to fill the frame."""
    w, h = size
    scale = max(w / image.width, h / image.height)
    resized = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
    left, top = (resized.width - w) // 2, (resized.height - h) // 2
    return resized.crop((left, top, left + w, top + h))


class Renderer:
    def __init__(self, show: ShowFile, size: tuple[int, int] | None = None) -> None:
        self.show = show
        self.size = size or (show.show.video.width, show.show.video.height)
        self.scale = self.size[0] / BASE_WIDTH
        self.screens_dir = show.base_dir / "assets" / "screens"
        fonts_dir = show.base_dir / "assets" / "fonts"
        self.fonts_dir = str(fonts_dir) if fonts_dir.exists() else None

    def px(self, value: float) -> int:
        return max(1, round(value * self.scale))

    def font(self, size: float):
        return font(self.px(size), self.fonts_dir)

    def asset_signature(self) -> str:
        """Changes when an artwork file or font is added, replaced or removed."""
        parts = [f"{self.size}"]
        for folder in (self.screens_dir, Path(self.fonts_dir) if self.fonts_dir else None):
            if folder and folder.exists():
                parts += [f"{p.name}:{p.stat().st_size}:{p.stat().st_mtime_ns}" for p in sorted(folder.iterdir())]
        return "|".join(parts)

    # --- layers ------------------------------------------------------------

    def _image(self, name: str | None) -> Image.Image | None:
        if not name:
            return None
        path = self.screens_dir / name
        return Image.open(path) if path.exists() else None

    def _background(self, screen_name: str | None, quiet: bool) -> Image.Image:
        screen = self.show.screens.get(screen_name) if screen_name else None
        art = self._image(screen.image) if screen else None
        if art is not None:
            return _cover(art.convert("RGB"), self.size)
        return self._placeholder(screen_name, screen, quiet)

    def _placeholder(self, name: str | None, screen: Screen | None, quiet: bool) -> Image.Image:
        """quiet: leave the big screen name out, because something else (big text) takes the centre."""
        w, h = self.size
        image = Image.new("RGB", self.size, BACKGROUND)
        draw = ImageDraw.Draw(image)
        step = self.px(120)
        for x in range(0, w, step):
            draw.line([(x, 0), (x, h)], fill=GRID, width=1)
        for y in range(0, h, step):
            draw.line([(0, y), (w, y)], fill=GRID, width=1)
        if name is None:
            return image
        label = f"placeholder: {screen.image}" if screen and screen.image else f"screen: {name}"
        draw.text((w // 2, self.px(150)), label, font=self.font(28), fill=DIM, anchor="ma")
        if screen and (screen.text_en or screen.text_nl):
            y = _text_block(draw, screen.text_en, self.font(84), WHITE, w // 2, self.px(330), self.px(1500))
            if screen.text_nl:
                _text_block(draw, screen.text_nl, self.font(56), NL_YELLOW, w // 2, y + self.px(30), self.px(1500))
        elif not quiet:
            draw.text((w // 2, self.px(380)), name.upper(), font=self.font(72), fill=DIM, anchor="ma")
        return image

    def _overlay(self, frame: Image.Image, name: str, slot: int) -> None:
        overlay = self.show.overlays.get(name)
        art = self._image(overlay.image) if overlay else None
        if art is not None:
            art = art.convert("RGBA")
            if art.size == self.size or abs(art.width / art.height - self.size[0] / self.size[1]) < 0.01:
                art = art.resize(self.size, Image.LANCZOS)
                frame.paste(art, (0, 0), art)
                return
            box_w = self.px(480)
            art = art.resize((box_w, round(art.height * box_w / art.width)), Image.LANCZOS)
            frame.paste(art, self._slot_xy(slot, art.width), art)
            return
        # Placeholder: a pale cloud with the file name.
        x, y = self._slot_xy(slot, self.px(480))
        layer = Image.new("RGBA", self.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        for dx, dy, r in ((90, 120, 90), (200, 85, 110), (320, 110, 95), (400, 150, 70), (240, 170, 100), (120, 175, 70)):
            draw.ellipse(
                [x + self.px(dx - r), y + self.px(dy - r), x + self.px(dx + r), y + self.px(dy + r)],
                fill=(235, 240, 255, 230),
            )
        draw.text((x + self.px(240), y + self.px(135)), overlay.image if overlay else name,
                  font=self.font(28), fill=(40, 50, 80, 255), anchor="mm")
        frame.paste(layer, (0, 0), layer)

    def _slot_xy(self, slot: int, width: int) -> tuple[int, int]:
        # Up to three overlays side by side in the upper half, clear of the subtitles.
        centres = (0.2, 0.5, 0.8)
        cx = centres[slot % 3] * self.size[0]
        return round(cx - width / 2), self.px(470)

    def _big(self, draw: ImageDraw.ImageDraw, text: str) -> None:
        w, h = self.size
        fnt = self.font(420)
        centre = (w // 2, round(h * 0.45))
        draw.text((centre[0] + self.px(8), centre[1] + self.px(8)), text, font=fnt, fill=(0, 0, 0), anchor="mm")
        draw.text(centre, text, font=fnt, fill=WHITE, anchor="mm")

    def _timer(self, draw: ImageDraw.ImageDraw, seconds: int) -> None:
        text = f"{seconds // 60:02d}:{seconds % 60:02d}"
        fnt = self.font(96)
        colour = TIMER_RED if seconds <= TIMER_RED_FROM else WHITE
        right, top = self.size[0] - self.px(60), self.px(40)
        box = draw.textbbox((right, top), text, font=fnt, anchor="ra")
        pad = self.px(18)
        draw.rounded_rectangle([box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad],
                               radius=self.px(14), fill=(0, 0, 0))
        draw.text((right, top), text, font=fnt, fill=colour, anchor="ra")

    def _ops(self, draw: ImageDraw.ImageDraw, text: str) -> None:
        fnt = self.font(26)
        left, top = self.px(30), self.px(30)
        label = f"OPS > {text}"
        box = draw.textbbox((left, top), label, font=fnt)
        pad = self.px(10)
        draw.rectangle([box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad], fill=(30, 20, 0))
        draw.text((left, top), label, font=fnt, fill=OPS_AMBER)

    # --- public ------------------------------------------------------------

    def render(self, state: FrameState) -> Image.Image:
        frame = self._background(state.screen, quiet=state.big is not None)
        for slot, name in enumerate(state.overlays):
            self._overlay(frame, name, slot)
        draw = ImageDraw.Draw(frame)
        if state.big:
            self._big(draw, state.big)
        if state.timer is not None:
            self._timer(draw, state.timer)
        if state.ops and self.show.show.ops_markers:
            self._ops(draw, state.ops)
        return frame
