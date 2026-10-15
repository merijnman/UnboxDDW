"""Pydantic models for show.yaml.

The models only check structure and types. Cross-references (does this speaker
exist, is `at` ascending, ...) are checked in `validate.py`, so that every
problem can be reported with the entry id and time instead of a pydantic path.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, PrivateAttr, field_validator, model_validator

_TIME_RE = re.compile(r"^(\d+):([0-5]\d)(\.\d+)?$")
_AUDIO_TAG = re.compile(r"\[[^\]]*\]")


def strip_tags(text: str) -> str:
    """Remove audio tags such as [clears throat]: spoken direction, never shown in subtitles."""
    return " ".join(_AUDIO_TAG.sub(" ", text).split())


def has_tags(text: str) -> bool:
    return bool(_AUDIO_TAG.search(text))


def parse_time(value: str) -> float:
    """'MM:SS' or 'MM:SS.s' to seconds."""
    match = _TIME_RE.match(value.strip())
    if not match:
        raise ValueError(f"time must look like 'MM:SS', got {value!r}")
    minutes, seconds, fraction = match.groups()
    return int(minutes) * 60 + int(seconds) + (float(fraction) if fraction else 0.0)


def format_time(seconds: float, decimals: int = 0) -> str:
    """Seconds to 'MM:SS' (or 'MM:SS.s' with decimals)."""
    sign = "-" if seconds < 0 else ""
    seconds = abs(seconds)
    minutes = int(seconds // 60)
    rest = seconds - minutes * 60
    if decimals:
        text = f"{rest:0{3 + decimals}.{decimals}f}"
        if text.startswith("60"):
            minutes, text = minutes + 1, f"{0:0{3 + decimals}.{decimals}f}"
        return f"{sign}{minutes:02d}:{text}"
    return f"{sign}{minutes:02d}:{int(rest):02d}"


def _check_time(value: str | None) -> str | None:
    if value is not None:
        parse_time(value)
    return value


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Video(Strict):
    width: int = 1920
    height: int = 1080
    fps: int = 25


class ShowMeta(Strict):
    title: str
    duration: str
    audio_language: str = "en"
    subtitles: list[str] = ["en", "nl"]
    video: Video = Video()
    bed: list[str] = []
    ops_markers: bool = True
    bed_gain_db: float = -22
    duck_bed_under_speech_db: float = -6

    _t = field_validator("duration")(_check_time)

    @property
    def duration_s(self) -> float:
        return parse_time(self.duration)


class VoiceSettings(Strict):
    stability: float
    similarity_boost: float
    style: float = 0.0
    use_speaker_boost: bool = True
    speed: float = 1.0


class Style(Strict):
    colours: dict[str, str] = {}
    heading_font: str | None = None
    body_font: str | None = None

    def rgb(self, name: str, default: tuple[int, int, int]) -> tuple[int, int, int]:
        value = self.colours.get(name)
        if not value:
            return default
        value = value.lstrip("#")
        return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


class Voice(Strict):
    label: str
    voice_id: str
    model_id: str = "eleven_multilingual_v2"
    settings: VoiceSettings
    seed: int | None = None
    fx: str | None = None
    squelch: bool = False  # radio crackle before and after every turn, also without the radio fx
    audition: str | None = None  # fixed test passage for `unbox audition`


class Sound(Strict):
    prompt: str | None = None
    file: str | None = None
    seconds: float | None = None
    loop: bool = False
    music: bool = False  # generate with ElevenLabs Music instead of sound effects
    filter: str | None = None  # ffmpeg filter chain applied in the mix, e.g. to tame a whine
    gain_db: float = 0.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    loop_length: float | None = None  # placed for longer than this: repeat the first loop_length s, crossfaded


class Screen(Strict):
    image: str | None = None
    text_en: str = ""
    text_nl: str = ""


class Overlay(Strict):
    image: str


class ArtworkImage(Strict):
    prompt: str
    aspect_ratio: str = "16:9"


class Artwork(Strict):
    model_id: str = "gemini-3.1-flash-image"
    resolution: str = "2K"
    style: str = ""
    images: dict[str, ArtworkImage] = {}

    def full_prompt(self, name: str) -> str:
        image = self.images[name]
        return f"{image.prompt}. {self.style}".strip() if self.style else image.prompt


class Timer(Strict):
    seconds: float | None = None
    until: str | None = None

    _t = field_validator("until")(_check_time)

    @model_validator(mode="after")
    def _one_of(self) -> Timer:
        if (self.seconds is None) == (self.until is None):
            raise ValueError("timer needs exactly one of `seconds` or `until`")
        return self

    def end_s(self, start: float) -> float:
        return start + self.seconds if self.seconds is not None else parse_time(self.until)


class Line(Strict):
    en: str  # spoken; may contain audio tags like [chuckling] for eleven_v3/v4
    nl: str | None = None
    subtitle: str | None = None  # shown instead of `en`, e.g. a new spelling without re-recording the take
    screen: str | None = None
    overlay: str | None = None
    big: str | None = None
    sound: str | None = None
    ops: str | None = None

    @property
    def shown(self) -> str:
        """The subtitle text: `subtitle` if given, else `en` without audio tags."""
        return strip_tags(self.subtitle or self.en)


class Entry(Strict):
    at: str
    id: str | None = None
    say: str | None = None
    delivery: Literal["aside"] | None = None
    interrupted: bool = False  # the next cue cuts this turn off instead of failing the schedule
    pace_seconds: float | None = None
    lines: list[Line] = []
    screen: str | None = None
    overlay: str | None = None
    big: str | None = None
    sound: str | None = None
    sound_until: str | None = None  # play `sound` until this time, looping it if needed (see Sound.loop_length)
    sound_envelope: list[tuple[str, float]] | None = None  # [time, dB] points for `sound`, straight lines between them
    timer: Timer | None = None
    ops: str | None = None

    _t = field_validator("at", "sound_until")(_check_time)

    @field_validator("sound_envelope")
    @classmethod
    def _envelope_times(cls, value):
        for time, _db in value or []:
            parse_time(time)
        return value

    @property
    def at_s(self) -> float:
        return parse_time(self.at)

    @property
    def text(self) -> str:
        """What one TTS request for this entry says: the `en` lines joined by a space."""
        return " ".join(line.en for line in self.lines)

    def where(self) -> str:
        return f"{self.id or '-'} @ {self.at}"


class Chatter(Strict):
    voice: str
    every_seconds: tuple[float, float]
    random_seed: int = 0
    subtitles: bool = False
    lines: list[str]


class Preshow(Strict):
    duration: str
    screen: Screen
    timer: Timer | None = None
    bed: list[str] = []
    bed_gain_db: float = -18
    chatter: Chatter | None = None
    finale: str | None = None  # a sound (music) placed so that it ends exactly when the pre-show ends

    _t = field_validator("duration")(_check_time)


class ShowFile(Strict):
    show: ShowMeta
    style: Style = Style()
    voices: dict[str, Voice]
    sounds: dict[str, Sound] = {}
    screens: dict[str, Screen] = {}
    overlays: dict[str, Overlay] = {}
    artwork: Artwork | None = None
    fx: dict[str, str] | None = None  # ffmpeg filter chains per effect name; defaults in fx.py
    timeline: list[Entry]
    preshow: Preshow | None = None

    _base_dir: Path = PrivateAttr(default_factory=Path.cwd)

    @property
    def base_dir(self) -> Path:
        """Folder of show.yaml; relative asset paths resolve against it."""
        return self._base_dir

    def say_entries(self) -> list[Entry]:
        return [e for e in self.timeline if e.say]


def load_show(path: str | Path) -> ShowFile:
    """Parse show.yaml. Raises yaml.YAMLError or pydantic.ValidationError."""
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    show = ShowFile.model_validate(data)
    show._base_dir = path.resolve().parent
    return show
