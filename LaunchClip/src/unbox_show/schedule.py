"""Scheduler: timeline + measured clips -> build/schedule.json.

The schedule is a flat, time-sorted list of events. Every later stage
(subtitles, mix, frames, video) reads only this file, never show.yaml timing.
The scheduler is the judge: it fails the build when voices overlap, a turn runs
past the next cue, or the show runs past its duration.
"""

from __future__ import annotations

import json
from pathlib import Path

from .model import Entry, ShowFile, format_time
from .plan import paced_offsets

LINE_TAIL = 0.3          # a subtitle stays up this long after its last character
PACED_MAX_FRACTION = 0.9  # paced clips longer than 0.9 x pace are trimmed with a short fade
EPS = 1e-6

_ORDER = {"screen": 0, "overlay": 1, "big": 2, "timer": 3, "ops": 4, "sound": 5, "voice": 6, "sub": 7}


class ScheduleError(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = problems


def _r(t: float) -> float:
    return round(t, 3)


def _actions(t: float, item, sounds: dict, duration: float) -> list[dict]:
    """Screen/overlay/big/sound/ops actions of an entry or a line, firing at t."""
    events = []
    if item.screen:
        events.append({"t": t, "type": "screen", "name": item.screen})
    if item.overlay:
        events.append({"t": t, "type": "overlay", "name": item.overlay})
    if item.big:
        events.append({"t": t, "type": "big", "text": item.big})
    if item.ops:
        events.append({"t": t, "type": "ops", "text": item.ops})
    if item.sound:
        info = sounds.get(item.sound)
        event = {"t": t, "type": "sound", "name": item.sound}
        if info is None:
            event.update(end=t, missing=True)
        else:
            event["end"] = min(t + info["duration"], duration)
        events.append(event)
    return events


def _line_start(clip: dict, line_pos: int) -> float:
    """Time of the line's first character, relative to the clip start."""
    starts = clip["alignment"]["character_start_times_seconds"]
    offset = clip["line_offsets"][line_pos]
    if offset < len(starts):
        return starts[offset]
    return clip["duration"] * offset / max(1, len(clip["text"]))


def _line_end(clip: dict, line_pos: int, text: str) -> float:
    ends = clip["alignment"]["character_end_times_seconds"]
    last = clip["line_offsets"][line_pos] + len(text) - 1
    return ends[last] if last < len(ends) else clip["duration"]


def _sub(t: float, end: float, entry: Entry, index: int, label: str | None) -> dict:
    line = entry.lines[index]
    return {
        "t": t,
        "type": "sub",
        "end": end,
        "entry": entry.id,
        "line": index,
        "en": line.en,
        "nl": line.nl or "",
        "speaker": label,
        "italic": entry.delivery == "aside",
    }


def _say_events(entry: Entry, show: ShowFile, clips: dict, sounds: dict) -> list[dict]:
    at = entry.at_s
    voice = show.voices[entry.say]
    duration = show.show.duration_s
    events: list[dict] = []

    if entry.pace_seconds:
        pace = entry.pace_seconds
        parts = [clips[f"{entry.id}#{i:02d}"] for i in range(len(entry.lines))]
        offsets = paced_offsets(parts[0]["duration"], len(parts), pace)
        starts = [at + off for off in offsets]
        for i, (clip, start) in enumerate(zip(parts, starts)):
            trim = 0 < i < len(parts) - 1 and clip["duration"] > PACED_MAX_FRACTION * pace
            end = start + (PACED_MAX_FRACTION * pace if trim else clip["duration"])
            voice_event = {
                "t": start, "type": "voice", "end": end, "clip": f"{entry.id}#{i:02d}", "entry": entry.id,
                "speaker": entry.say, "fx": voice.fx, "wav": clip["wav"],
            }
            if trim:
                voice_event["trim"] = True
            events.append(voice_event)
            line_t = start + _line_start(clip, 0)
            line_end = end + LINE_TAIL
            if i + 1 < len(starts):
                line_end = min(line_end, starts[i + 1])
            events.append(_sub(line_t, line_end, entry, i, voice.label if i == 0 else None))
            events.extend(_actions(line_t, entry.lines[i], sounds, duration))
        return events

    clip = clips[entry.id]
    events.append({
        "t": at, "type": "voice", "end": at + clip["duration"], "clip": entry.id, "entry": entry.id,
        "speaker": entry.say, "fx": voice.fx, "wav": clip["wav"],
    })
    line_starts = [at + _line_start(clip, i) for i in range(len(entry.lines))]
    for i, line in enumerate(entry.lines):
        end = at + _line_end(clip, i, line.en) + LINE_TAIL
        if i + 1 < len(line_starts):
            end = min(end, line_starts[i + 1])
        events.append(_sub(line_starts[i], end, entry, i, voice.label if i == 0 else None))
        events.extend(_actions(line_starts[i], line, sounds, duration))
    return events


def build_schedule(show: ShowFile, clips: dict, sounds: dict) -> dict:
    duration = show.show.duration_s
    events: list[dict] = []
    problems: list[str] = []
    timeline = show.timeline

    for index, entry in enumerate(timeline):
        at = entry.at_s
        events.extend(_actions(at, entry, sounds, duration))
        if entry.timer:
            events.append({"t": at, "type": "timer", "end": entry.timer.end_s(at)})
        if not entry.say:
            continue
        say_events = _say_events(entry, show, clips, sounds)
        events.extend(say_events)
        turn_end = max(e["end"] for e in say_events if e["type"] == "voice")
        following = next((e for e in timeline[index + 1 :] if e.at_s > at), None)
        if following is not None and turn_end > following.at_s + EPS:
            problems.append(
                f"{entry.where()}: turn ends at {format_time(turn_end, 1)}, after the next cue "
                f"({following.where()}). Move the next entry later or shorten this one."
            )

    voices = sorted((e for e in events if e["type"] == "voice"), key=lambda e: e["t"])
    for before, after in zip(voices, voices[1:]):
        if after["t"] < before["end"] - EPS:
            problems.append(
                f"voice overlap: {before['clip']} ends at {format_time(before['end'], 1)} but "
                f"{after['clip']} starts at {format_time(after['t'], 1)}"
            )
    for event in events:
        if event["type"] in ("voice", "timer") and event["end"] > duration + EPS:
            what = event.get("clip", "timer")
            problems.append(f"{what} ends at {format_time(event['end'], 1)}, after the show ({show.show.duration})")

    if problems:
        raise ScheduleError(problems)

    for event in events:
        event["t"] = _r(event["t"])
        if "end" in event:
            event["end"] = _r(event["end"])
    events.sort(key=lambda e: (e["t"], _ORDER[e["type"]]))
    return {
        "duration": duration,
        "bed": list(show.show.bed),
        "bed_gain_db": show.show.bed_gain_db,
        "duck_bed_under_speech_db": show.show.duck_bed_under_speech_db,
        "events": events,
    }


def write(schedule: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schedule, indent=1, ensure_ascii=False), encoding="utf-8")
    return path


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
