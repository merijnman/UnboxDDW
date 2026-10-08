"""Estimated timing before any audio exists (`unbox plan`)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .model import Entry, ShowFile, format_time

WORDS_PER_SECOND = 2.5
PAUSE_PER_LINE_BREAK = 0.35
TIGHT_GAP = 1.0


def estimate_seconds(lines: list[str], speed: float = 1.0) -> float:
    """Words / 2.5 per second, divided by speed, plus 0.35 s per line break."""
    words = sum(len(line.split()) for line in lines)
    return words / WORDS_PER_SECOND / speed + PAUSE_PER_LINE_BREAK * (len(lines) - 1)


def paced_offsets(first_duration: float, count: int, pace: float) -> list[float]:
    """Start offsets for a paced entry (the countdown).

    The first line ("T minus ten seconds.") plays at 0. The remaining lines sit
    on a strict grid of `pace` seconds, starting one beat after the first line
    has finished, rounded up to a whole beat.
    """
    lead = math.ceil(round(first_duration / pace, 6)) * pace
    return [0.0] + [lead + k * pace for k in range(1, count)]


@dataclass
class PlanRow:
    start: float
    entry: str
    speaker: str = ""
    words: int = 0
    estimate: float | None = None
    end: float | None = None
    gap: float | None = None
    flag: str = ""


def _describe(entry: Entry) -> str:
    parts = []
    if entry.timer:
        if entry.timer.seconds is not None:
            parts.append(f"timer {entry.timer.seconds:g} s")
        else:
            parts.append(f"timer until {entry.timer.until}")
    for field in ("screen", "overlay", "sound", "big"):
        value = getattr(entry, field)
        if value:
            parts.append(f"{field} {value}")
    if entry.ops:
        parts.append("ops")
    return ", ".join(parts) or "-"


def entry_estimate(show: ShowFile, entry: Entry) -> float:
    """Estimated spoken length of a `say` entry, from its start to the end of its last clip."""
    speed = show.voices[entry.say].settings.speed if entry.say in show.voices else 1.0
    texts = [line.en for line in entry.lines]
    if entry.pace_seconds:
        offsets = paced_offsets(estimate_seconds(texts[:1], speed), len(texts), entry.pace_seconds)
        return offsets[-1] + estimate_seconds(texts[-1:], speed)
    return estimate_seconds(texts, speed)


def build_plan(show: ShowFile) -> list[PlanRow]:
    rows: list[PlanRow] = []
    timeline = show.timeline
    for index, entry in enumerate(timeline):
        start = entry.at_s
        if entry.say:
            estimate = entry_estimate(show, entry)
            row = PlanRow(
                start=start,
                entry=entry.id or "-",
                speaker=entry.say,
                words=sum(len(line.en.split()) for line in entry.lines),
                estimate=estimate,
                end=start + estimate,
            )
            following = next((e for e in timeline[index + 1 :] if e.at_s > start), None)
            if following is not None:
                row.gap = following.at_s - row.end
                if row.gap < 0:
                    row.flag = "OVERLAP"
                elif row.gap < TIGHT_GAP:
                    row.flag = "tight"
        else:
            row = PlanRow(start=start, entry=_describe(entry))
            if entry.timer:
                row.end = entry.timer.end_s(start)
            elif entry.sound and show.sounds.get(entry.sound) and show.sounds[entry.sound].seconds:
                row.end = start + show.sounds[entry.sound].seconds
        rows.append(row)
    return rows


def characters_per_voice(show: ShowFile) -> dict[str, int]:
    """Characters sent to the speech API for one full render of the show."""
    totals: dict[str, int] = {name: 0 for name in show.voices}
    for entry in show.say_entries():
        if entry.pace_seconds:
            chars = sum(len(line.en) for line in entry.lines)
        else:
            chars = len(entry.text)
        totals[entry.say] = totals.get(entry.say, 0) + chars
    return totals


def preshow_characters(show: ShowFile) -> int:
    chatter = show.preshow.chatter if show.preshow else None
    return sum(len(line) for line in chatter.lines) if chatter else 0


def format_plan(show: ShowFile) -> str:
    rows = build_plan(show)
    header = f"{'Start':>5}  {'Entry':<34} {'Speaker':<16} {'Words':>5} {'Est.':>6}  {'Ends':>5} {'Gap':>6}  Flag"
    out = [header, "-" * len(header)]
    for row in rows:
        est = f"{row.estimate:5.1f}s" if row.estimate is not None else ""
        end = format_time(row.end) if row.end is not None else ""
        gap = f"{row.gap:5.1f}s" if row.gap is not None else ""
        words = str(row.words) if row.speaker else ""
        out.append(
            f"{format_time(row.start):>5}  {row.entry[:34]:<34} {row.speaker:<16} {words:>5} {est:>6}  {end:>5} {gap:>6}  {row.flag}"
        )
    out.append("")
    out.append("Characters per voice (show):")
    totals = characters_per_voice(show)
    for name, chars in totals.items():
        out.append(f"  {name:<16} {chars:>6}")
    out.append(f"  {'total':<16} {sum(totals.values()):>6}")
    if show.preshow and show.preshow.chatter:
        out.append(f"Pre-show chatter ({show.preshow.chatter.voice}): {preshow_characters(show)}")
    return "\n".join(out)
