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


class Voice(Strict):
    label: str
    voice_id: str
    model_id: str = "eleven_multilingual_v2"
    settings: VoiceSettings
    seed: int | None = None
    fx: str | None = None
    audition: str | None = None  # fixed test passage for `unbox audition`


class Sound(Strict):
    prompt: str | None = None
    file: str | None = None
    seconds: float | None = None
    loop: bool = False
    gain_db: float = 0.0
    fade_in: float = 0.0
    fade_out: float = 0.0


class Screen(Strict):
    image: str | None = None
    text_en: str = ""
    text_nl: str = ""


class Overlay(Strict):
    image: str


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
    en: str
    nl: str | None = None
    screen: str | None = None
    overlay: str | None = None
    big: str | None = None
    sound: str | None = None
    ops: str | None = None


class Entry(Strict):
    at: str
    id: str | None = None
    say: str | None = None
    delivery: Literal["aside"] | None = None
    pace_seconds: float | None = None
    lines: list[Line] = []
    screen: str | None = None
    overlay: str | None = None
    big: str | None = None
    sound: str | None = None
    timer: Timer | None = None
    ops: str | None = None

    _t = field_validator("at")(_check_time)

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

    _t = field_validator("duration")(_check_time)


class ShowFile(Strict):
    show: ShowMeta
    voices: dict[str, Voice]
    sounds: dict[str, Sound] = {}
    screens: dict[str, Screen] = {}
    overlays: dict[str, Overlay] = {}
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
