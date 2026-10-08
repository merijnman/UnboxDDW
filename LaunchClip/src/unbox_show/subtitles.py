"""Subtitles: schedule.json -> build/subs.ass (burned in) and dist/*.srt.

EN (white, larger) sits directly above NL (light yellow, smaller), both
bottom-centred on a dark box, at most two rows each. A line that needs more
than two rows is split into consecutive cues at a comma or the middle space,
with the time divided in proportion to the text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

MAX_ROW_CHARS = {"en": 56, "nl": 64}
MAX_ROWS = 2
MIN_CUE_SECONDS = 1.2

# ASS layout, in PlayRes pixels (1920x1080; libass scales to the video size).
EN_SIZE, NL_SIZE = 54, 46
BOX_PADDING = 8
NL_MARGIN_V = 40
NL_ROW_HEIGHT = 56
EN_NL_GAP = 14


@dataclass
class Cue:
    start: float
    end: float
    en: list[str]                     # rows
    nl: list[str] = field(default_factory=list)
    speaker: str | None = None        # label at the start of en[0]
    italic: bool = False


def wrap(text: str, width: int) -> list[str]:
    """Greedy word wrap."""
    rows: list[str] = []
    row = ""
    for word in text.split():
        candidate = f"{row} {word}" if row else word
        if len(candidate) <= width or not row:
            row = candidate
        else:
            rows.append(row)
            row = word
    if row:
        rows.append(row)
    return rows


def split_once(text: str) -> tuple[str, str]:
    """Split at the comma (or other mid-sentence mark) closest to the middle, else the middle space."""
    middle = len(text) / 2
    marks = [i + 1 for i, ch in enumerate(text[:-1]) if ch in ",;:.!?" and text[i + 1] == " "]
    spaces = [i for i, ch in enumerate(text) if ch == " "]
    candidates = [i for i in marks if 0.25 * len(text) <= i <= 0.75 * len(text)] or spaces
    if not candidates:
        return text, ""
    cut = min(candidates, key=lambda i: abs(i - middle))
    return text[:cut].strip(), text[cut:].strip()


def chunk(text: str, width: int, count: int = 1) -> list[str]:
    """Split text into at least `count` chunks that each wrap to <= MAX_ROWS rows."""
    chunks = [text] if text else []
    while chunks:
        longest = max(range(len(chunks)), key=lambda i: len(chunks[i]))
        too_long = len(wrap(chunks[longest], width)) > MAX_ROWS
        if not too_long and len(chunks) >= count:
            break
        first, second = split_once(chunks[longest])
        if not second:
            break
        chunks[longest : longest + 1] = [first, second]
    return chunks


def cues_from_schedule(schedule: dict) -> list[Cue]:
    subs = [e for e in schedule["events"] if e["type"] == "sub"]
    cues: list[Cue] = []
    for event in subs:
        label = event.get("speaker")
        en_text = f"{label}: {event['en']}" if label else event["en"]
        en_chunks = chunk(en_text, MAX_ROW_CHARS["en"])
        nl_chunks = chunk(event["nl"], MAX_ROW_CHARS["nl"], len(en_chunks)) if event["nl"] else []
        if len(nl_chunks) > len(en_chunks):
            en_chunks = chunk(en_text, MAX_ROW_CHARS["en"], len(nl_chunks))
        nl_chunks += [""] * (len(en_chunks) - len(nl_chunks))

        total = sum(len(c) for c in en_chunks)
        start, span = event["t"], event["end"] - event["t"]
        for i, (en, nl) in enumerate(zip(en_chunks, nl_chunks)):
            length = span * len(en) / total
            cues.append(
                Cue(
                    start=start,
                    end=start + length,
                    en=wrap(en, MAX_ROW_CHARS["en"]),
                    nl=wrap(nl, MAX_ROW_CHARS["nl"]) if nl else [],
                    speaker=label if i == 0 else None,
                    italic=event.get("italic", False),
                )
            )
            start += length

    cues.sort(key=lambda c: c.start)
    for cue, following in zip(cues, cues[1:] + [None]):
        limit = following.start if following else float("inf")
        if cue.end - cue.start < MIN_CUE_SECONDS:
            cue.end = cue.start + MIN_CUE_SECONDS
        cue.end = min(cue.end, limit)
    for cue in cues:
        cue.start, cue.end = round(cue.start, 3), round(cue.end, 3)
    return cues


# --- ASS -------------------------------------------------------------------

def _ass_time(t: float) -> str:
    cs = int(round(t * 100))
    h, rest = divmod(cs, 360000)
    m, rest = divmod(rest, 6000)
    s, cs = divmod(rest, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")")


def _ass_style(name: str, size: int, colour: str) -> str:
    # BorderStyle 3 = opaque box; the box takes OutlineColour, padded by Outline.
    return (
        f"Style: {name},Arial,{size},{colour},{colour},&H60000000,&H60000000,"
        f"0,0,0,0,100,100,0,0,3,{BOX_PADDING},0,2,80,80,{NL_MARGIN_V},1"
    )


def ass_text(cues: list[Cue], width: int = 1920, height: int = 1080) -> str:
    out = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        _ass_style("EN", EN_SIZE, "&H00FFFFFF"),
        _ass_style("NL", NL_SIZE, "&H0099F0FF"),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for cue in cues:
        start, end = _ass_time(cue.start), _ass_time(cue.end)
        italic = "{\\i1}" if cue.italic else ""
        rows = [_ass_escape(r) for r in cue.en]
        if cue.speaker and rows and rows[0].startswith(cue.speaker):
            rows[0] = "{\\b1}" + cue.speaker + ":{\\b0}" + rows[0][len(cue.speaker) + 1 :]
        en_margin = NL_MARGIN_V
        if cue.nl:
            nl_text = italic + "\\N".join(_ass_escape(r) for r in cue.nl)
            out.append(f"Dialogue: 0,{start},{end},NL,,0,0,{NL_MARGIN_V},,{nl_text}")
            en_margin = NL_MARGIN_V + len(cue.nl) * NL_ROW_HEIGHT + EN_NL_GAP
        out.append(f"Dialogue: 0,{start},{end},EN,,0,0,{en_margin},,{italic}" + "\\N".join(rows))
    return "\n".join(out) + "\n"


# --- SRT -------------------------------------------------------------------

def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, rest = divmod(ms, 3600000)
    m, rest = divmod(rest, 60000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def srt_text(cues: list[Cue], lang: str) -> str:
    blocks = []
    for cue in cues:
        rows = cue.en if lang == "en" else cue.nl
        if not rows:
            continue
        text = "\n".join(rows)
        if cue.italic:
            text = f"<i>{text}</i>"
        blocks.append(f"{len(blocks) + 1}\n{_srt_time(cue.start)} --> {_srt_time(cue.end)}\n{text}\n")
    return "\n".join(blocks)


def write_all(schedule: dict, ass_path: Path, srt_stem: Path) -> list[Cue]:
    cues = cues_from_schedule(schedule)
    ass_path.parent.mkdir(parents=True, exist_ok=True)
    ass_path.write_text(ass_text(cues), encoding="utf-8")
    srt_stem.parent.mkdir(parents=True, exist_ok=True)
    for lang in ("en", "nl"):
        Path(f"{srt_stem}.{lang}.srt").write_text(srt_text(cues, lang), encoding="utf-8")
    return cues
