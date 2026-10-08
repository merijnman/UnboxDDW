"""Cross-reference checks for show.yaml (`unbox validate`)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import ValidationError

from .model import ShowFile, load_show

# Speech models we know to work with the with-timestamps endpoint (see ELEVENLABS.md).
KNOWN_TTS_MODELS = {
    "eleven_multilingual_v2",
    "eleven_v3",
    "eleven_flash_v2_5",
    "eleven_turbo_v2_5",
}
KNOWN_FX = {"radio", "radio_light", "aside"}


@dataclass
class Issue:
    level: Literal["error", "warning"]
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.level.upper():7} {self.where}: {self.message}"


def check(show: ShowFile) -> list[Issue]:
    issues: list[Issue] = []

    def error(where: str, message: str) -> None:
        issues.append(Issue("error", where, message))

    def warning(where: str, message: str) -> None:
        issues.append(Issue("warning", where, message))

    for name, voice in show.voices.items():
        where = f"voices.{name}"
        if voice.voice_id.startswith("TODO"):
            warning(where, f"voice_id is still {voice.voice_id!r}; pick one with `unbox audition` first")
        if voice.model_id not in KNOWN_TTS_MODELS:
            warning(where, f"unknown model_id {voice.model_id!r}; the backlog decision is eleven_multilingual_v2")
        if voice.fx and voice.fx not in KNOWN_FX:
            error(where, f"unknown fx {voice.fx!r} (known: {', '.join(sorted(KNOWN_FX))})")

    for name, sound in show.sounds.items():
        where = f"sounds.{name}"
        if (sound.prompt is None) == (sound.file is None):
            error(where, "needs exactly one of `prompt` or `file`")
        if sound.prompt is not None and not (sound.seconds and 0.5 <= sound.seconds <= 30):
            error(where, "a generated sound needs `seconds` between 0.5 and 30")
        if sound.file is not None and not (show.base_dir / sound.file).exists():
            warning(where, f"file {sound.file} not found; it will be silent until supplied")

    for name in show.show.bed:
        if name not in show.sounds:
            error("show.bed", f"unknown sound {name!r}")

    def refs(where: str, item) -> None:
        if item.screen and item.screen not in show.screens:
            error(where, f"unknown screen {item.screen!r}")
        if item.overlay and item.overlay not in show.overlays:
            error(where, f"unknown overlay {item.overlay!r}")
        if item.sound and item.sound not in show.sounds:
            error(where, f"unknown sound {item.sound!r}")

    duration = show.show.duration_s
    seen_ids: dict[str, str] = {}
    previous_at = -1.0
    for entry in show.timeline:
        where = entry.where()
        at = entry.at_s
        if at < previous_at:
            error(where, "`at` is earlier than the entry before it; keep the timeline ascending")
        previous_at = max(previous_at, at)
        if at >= duration:
            error(where, f"starts after the end of the show ({show.show.duration})")

        if entry.id:
            if entry.id in seen_ids:
                error(where, f"duplicate id (also used @ {seen_ids[entry.id]})")
            seen_ids.setdefault(entry.id, entry.at)

        refs(where, entry)
        if entry.timer and entry.timer.end_s(at) <= at:
            error(where, "timer ends before it starts")

        if entry.say:
            if not entry.id:
                error(where, "a `say` entry needs an id (it is the cache and subtitle key)")
            if entry.say not in show.voices:
                error(where, f"unknown speaker {entry.say!r}")
            if not entry.lines:
                error(where, "a `say` entry needs at least one line")
        elif entry.lines:
            error(where, "has `lines` but no `say` speaker")
        if entry.pace_seconds is not None and entry.pace_seconds <= 0:
            error(where, "pace_seconds must be positive")

        for number, line in enumerate(entry.lines, start=1):
            line_where = f"{where} line {number}"
            refs(line_where, line)
            if not line.nl:
                error(line_where, f"no `nl` translation for {line.en!r}")
            if not line.en.strip():
                error(line_where, "empty `en` text")

    if show.preshow:
        for name in show.preshow.bed:
            if name not in show.sounds:
                error("preshow.bed", f"unknown sound {name!r}")
        chatter = show.preshow.chatter
        if chatter and chatter.voice not in show.voices:
            error("preshow.chatter", f"unknown speaker {chatter.voice!r}")

    return issues


def _pydantic_issues(exc: ValidationError) -> list[Issue]:
    issues = []
    for err in exc.errors():
        where = ".".join(str(part) for part in err["loc"]) or "show.yaml"
        issues.append(Issue("error", where, err["msg"]))
    return issues


def load_and_check(path: str | Path) -> tuple[ShowFile | None, list[Issue]]:
    """Load and check a show file. The show is None when it could not be parsed."""
    try:
        show = load_show(path)
    except FileNotFoundError:
        return None, [Issue("error", str(path), "file not found")]
    except yaml.YAMLError as exc:
        return None, [Issue("error", str(path), f"not valid YAML: {exc}")]
    except ValidationError as exc:
        return None, _pydantic_issues(exc)
    return show, check(show)


def has_errors(issues: list[Issue]) -> bool:
    return any(issue.level == "error" for issue in issues)
