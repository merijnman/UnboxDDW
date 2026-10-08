import json

import pytest

from conftest import SNAPSHOTS
from unbox_show.subtitles import MAX_ROW_CHARS, MAX_ROWS, chunk, cues_from_schedule, split_once, srt_text, ass_text


@pytest.fixture(scope="module")
def schedule():
    return json.loads((SNAPSHOTS / "schedule_show.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def cues(schedule):
    return cues_from_schedule(schedule)


def test_every_cue_fits_in_two_rows(cues):
    for cue in cues:
        assert 1 <= len(cue.en) <= MAX_ROWS
        assert len(cue.nl) <= MAX_ROWS
        assert all(len(row) <= MAX_ROW_CHARS["en"] for row in cue.en)
        assert all(len(row) <= MAX_ROW_CHARS["nl"] for row in cue.nl)


def test_no_two_cues_overlap(cues):
    for before, after in zip(cues, cues[1:]):
        assert before.end <= after.start + 1e-9, (before, after)
        assert before.start < before.end


def test_cues_follow_the_schedule(schedule, cues):
    subs = [e for e in schedule["events"] if e["type"] == "sub"]
    starts = {round(c.start, 3) for c in cues}
    for event in subs:
        assert round(event["t"], 3) in starts
    first = cues[0]
    assert first.start == subs[0]["t"]
    assert first.speaker == "COMMANDER" and first.en[0].startswith("COMMANDER: ")


def test_long_line_is_split_with_proportional_time():
    long_en = ("If you would like to dive deeper into metaphors, visit the Unbox exhibition space "
               "around the corner, or check out our website, and bring all your friends along with you.")
    event = {"type": "sub", "t": 10.0, "end": 20.0, "en": long_en, "nl": "Kort.", "speaker": None, "italic": False}
    cues = cues_from_schedule({"events": [event]})
    assert len(cues) == 2
    assert cues[0].start == 10.0 and cues[-1].end == 20.0
    assert cues[0].end == cues[1].start
    share = len(" ".join(cues[0].en)) / (len(" ".join(cues[0].en)) + len(" ".join(cues[1].en)))
    assert (cues[0].end - 10.0) / 10.0 == pytest.approx(share, abs=0.02)


def test_split_prefers_a_comma_near_the_middle():
    first, second = split_once("For me, as commander, this pen might be a metaphor, because it is long and pointy.")
    assert first.endswith(",")
    assert chunk("short text", 56) == ["short text"]


def test_short_cue_is_extended_but_not_into_the_next(cues):
    yikes = [c for c in cues if c.en[-1].endswith("Yikes.")]
    assert yikes and yikes[0].end - yikes[0].start >= 1.2 - 1e-6


def test_aside_is_italic(cues):
    aside = next(c for c in cues if "Brave idiots." in c.en[-1])
    assert aside.italic
    assert "{\\i1}" in ass_text([aside])
    assert "<i>" in srt_text([aside], "en")


def test_ass_has_both_styles_and_nl_below_en(cues):
    text = ass_text(cues)
    assert "Style: EN,Arial,54" in text and "Style: NL,Arial,46" in text
    first_nl = next(line for line in text.splitlines() if line.startswith("Dialogue") and ",NL," in line)
    first_en = next(line for line in text.splitlines() if line.startswith("Dialogue") and ",EN," in line)
    nl_margin = int(first_nl.split(",")[7])
    en_margin = int(first_en.split(",")[7])
    assert en_margin > nl_margin


def test_srt_numbering(cues):
    text = srt_text(cues, "nl")
    assert text.startswith("1\n00:00:03,000 --> ")
