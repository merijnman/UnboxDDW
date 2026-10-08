import json

import pytest

from conftest import SNAPSHOTS, UPDATE
from unbox_show import pipeline, sfx, tts
from unbox_show.eleven import FakeElevenClient
from unbox_show.model import load_show
from unbox_show.schedule import ScheduleError, build_schedule


def clip(text: str, duration: float, lines: list[str] | None = None) -> dict:
    """A clip index entry with a linear alignment, like the fake client produces."""
    lines = lines or [text]
    text = " ".join(lines)
    step = duration / len(text)
    offsets, pos = [], 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1
    return {
        "text": text,
        "line_offsets": offsets,
        "duration": duration,
        "wav": "cache/tts/x.wav",
        "alignment": {
            "characters": list(text),
            "character_start_times_seconds": [i * step for i in range(len(text))],
            "character_end_times_seconds": [(i + 1) * step for i in range(len(text))],
        },
    }


def mini_clips(show, hello=5.0, count_first=1.2, bye=1.0):
    hello_lines = [line.en for line in show.timeline[1].lines]
    clips = {"hello": clip("", hello, hello_lines), "bye": clip("Goodbye.", bye)}
    for i, line in enumerate(show.timeline[3].lines):
        clips[f"count#{i:02d}"] = clip(line.en, count_first if i == 0 else 0.5)
    return clips


SOUNDS = {"beep": {"duration": 1.0}, "hum": {"duration": 2.0}}


def of_type(schedule, kind):
    return [e for e in schedule["events"] if e["type"] == kind]


def test_normal_turn(mini_show_path):
    show = load_show(mini_show_path)
    sched = build_schedule(show, mini_clips(show), SOUNDS)
    voice = next(e for e in of_type(sched, "voice") if e["clip"] == "hello")
    assert (voice["t"], voice["end"]) == (2.0, 7.0)
    subs = [e for e in of_type(sched, "sub") if e["entry"] == "hello"]
    # Line 2 starts at its first character: offset 31 of 47 characters over 5 s.
    assert subs[0]["t"] == 2.0
    assert subs[1]["t"] == pytest.approx(2.0 + 5.0 * 31 / 47, abs=0.001)
    assert subs[0]["end"] == subs[1]["t"]  # capped at the next line's start
    assert subs[1]["end"] == pytest.approx(7.0 + 0.3, abs=0.001)
    assert subs[0]["speaker"] == "COMMANDER" and subs[1]["speaker"] is None
    assert subs[1]["nl"] == "Kijk naar de pen."


def test_line_action_fires_at_line_start(mini_show_path):
    show = load_show(mini_show_path)
    sched = build_schedule(show, mini_clips(show), SOUNDS)
    line2 = [e for e in of_type(sched, "sub") if e["entry"] == "hello"][1]["t"]
    assert [e["t"] for e in of_type(sched, "screen") if e["name"] == "pen"] == [line2]
    assert [e["t"] for e in of_type(sched, "overlay")] == [line2]


def test_turn_running_into_next_cue_is_an_error(mini_show_path):
    show = load_show(mini_show_path)
    with pytest.raises(ScheduleError, match=r"hello @ 00:02: turn ends at 00:10.5, after the next cue"):
        build_schedule(show, mini_clips(show, hello=8.5), SOUNDS)


def test_voice_overlap_is_an_error(mini_show_path, mini_data):
    show = load_show(mini_show_path)
    clips = mini_clips(show, hello=5.0)
    show.timeline.pop(2)  # without the timer, the next cue after "hello" is the countdown
    show.timeline[2].at = "00:05"
    with pytest.raises(ScheduleError) as info:
        build_schedule(show, clips, SOUNDS)
    assert any("voice overlap: hello ends at 00:07.0 but count#00 starts at 00:05.0" in p for p in info.value.problems)


def test_show_longer_than_duration_is_an_error(mini_show_path):
    show = load_show(mini_show_path)
    with pytest.raises(ScheduleError, match="after the show"):
        build_schedule(show, mini_clips(show, bye=25.0), SOUNDS)


def test_paced_countdown(mini_show_path):
    show = load_show(mini_show_path)
    sched = build_schedule(show, mini_clips(show, count_first=1.2), SOUNDS)
    voices = [e for e in of_type(sched, "voice") if e["entry"] == "count"]
    # Lead-in 1.2 s rounds up to 2 beats, then "Three." one beat later and one per second after.
    assert [e["t"] for e in voices] == [22.0, 25.0, 26.0, 27.0]
    bigs = [(e["t"], e["text"]) for e in of_type(sched, "big")]
    assert bigs == [(25.0, "3"), (26.0, "2"), (27.0, "1")]
    assert [e["t"] for e in of_type(sched, "ops")] == [27.0]


def test_paced_clip_longer_than_the_beat_is_trimmed(mini_show_path):
    show = load_show(mini_show_path)
    clips = mini_clips(show)
    clips["count#01"]["duration"] = 1.4
    sched = build_schedule(show, clips, SOUNDS)
    three = next(e for e in of_type(sched, "voice") if e["clip"] == "count#01")
    assert three["trim"] is True
    assert three["end"] - three["t"] == pytest.approx(0.9)


def test_full_show_snapshot(real_show_copy):
    """The whole show through the fake client. Re-run with UPDATE_GOLDEN=1 after an intended change."""
    show = load_show(real_show_copy)
    paths = pipeline.Paths(show.base_dir)
    client = FakeElevenClient()
    clips, _ = tts.render(show, client, paths.cache, paths.build)
    sounds, _ = sfx.render(show, client, paths.cache, paths.build, warn=lambda m: None)
    sched = build_schedule(show, clips, sounds)
    snapshot = SNAPSHOTS / "schedule_show.json"
    text = json.dumps(sched, indent=1, ensure_ascii=False)
    if UPDATE or not snapshot.exists():
        if not UPDATE:
            pytest.fail("snapshot missing; run with UPDATE_GOLDEN=1")
        snapshot.write_text(text, encoding="utf-8")
    assert json.loads(text) == json.loads(snapshot.read_text(encoding="utf-8"))
