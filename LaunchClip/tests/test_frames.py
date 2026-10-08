import json

import pytest

from conftest import SNAPSHOTS, assert_golden
from unbox_show.frames import FrameState, Renderer
from unbox_show.model import load_show
from unbox_show.video import segments, state_at

SIZE = (960, 540)


@pytest.fixture(scope="module")
def renderer(tmp_path_factory):
    # A copy of show.yaml in an empty folder, so real artwork never changes the goldens.
    import shutil

    from conftest import SHOW_YAML

    folder = tmp_path_factory.mktemp("frames")
    shutil.copy(SHOW_YAML, folder / "show.yaml")
    return Renderer(load_show(folder / "show.yaml"), SIZE)


GOLDEN_STATES = {
    "frame_title": FrameState(screen="title"),
    "frame_pen_overlays": FrameState(screen="pen", overlays=("cloud_rocket", "cloud_text", "cloud_carrot")),
    "frame_timer": FrameState(screen="introduce", timer=27),
    "frame_timer_red_ops": FrameState(screen="share", timer=9, ops="Stand by: smoke machine and alarm light, in 15 seconds"),
    "frame_countdown": FrameState(screen="countdown", big="7", ops="SMOKE MACHINE ON"),
    "frame_end": FrameState(screen="end"),
}


@pytest.mark.parametrize("name", GOLDEN_STATES)
def test_golden_frames(renderer, name):
    assert_golden(renderer.render(GOLDEN_STATES[name]), name)


def test_every_state_of_the_show_renders_without_artwork(tmp_path_factory):
    import shutil

    from conftest import SHOW_YAML

    folder = tmp_path_factory.mktemp("allstates")
    shutil.copy(SHOW_YAML, folder / "show.yaml")
    small = Renderer(load_show(folder / "show.yaml"), (320, 180))
    schedule = json.loads((SNAPSHOTS / "schedule_show.json").read_text(encoding="utf-8"))
    states = {state for _, _, state in segments(schedule, 25)}
    assert len(states) > 500  # mostly timer ticks
    for state in states:
        assert small.render(state).size == (320, 180)


def test_state_at_follows_the_schedule():
    schedule = json.loads((SNAPSHOTS / "schedule_show.json").read_text(encoding="utf-8"))
    assert state_at(schedule, 1.0).screen == "title"
    timer = state_at(schedule, 94.0)  # timer of 30 s starts at 01:34
    assert timer.screen == "introduce" and timer.timer == 30
    assert state_at(schedule, 123.5).timer == 1
    assert state_at(schedule, 124.5).timer == 0
    assert state_at(schedule, 125.5).timer is None
    ops = state_at(schedule, 8 * 60 + 5)
    assert ops.ops == "SMOKE MACHINE ON, alarm light on" and ops.screen == "alert"
    assert state_at(schedule, 8 * 60 + 11).ops is None


def test_segments_cover_the_whole_show_on_the_frame_grid():
    schedule = json.loads((SNAPSHOTS / "schedule_show.json").read_text(encoding="utf-8"))
    segs = segments(schedule, 25)
    assert segs[0][0] == 0.0 and segs[-1][1] == 900.0
    for (s0, e0, a), (s1, e1, b) in zip(segs, segs[1:]):
        assert e0 == s1 and a != b
        assert round(s1 * 25, 6) == int(round(s1 * 25))
