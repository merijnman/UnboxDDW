import pytest

from conftest import SHOW_YAML, write_show
from unbox_show.validate import has_errors, load_and_check


def test_current_show_validates():
    show, issues = load_and_check(SHOW_YAML)
    assert show is not None
    assert not has_errors(issues), [str(i) for i in issues]
    # Voice ids are placeholders until the audition: a warning, not an error.
    assert any("TODO" in i.message and i.level == "warning" for i in issues)


def test_mini_show_has_no_issues(mini_show_path):
    _, issues = load_and_check(mini_show_path)
    assert issues == []


def _unknown_speaker(d):
    d["timeline"][1]["say"] = "captain"


def _missing_nl(d):
    del d["timeline"][1]["lines"][1]["nl"]


def _not_ascending(d):
    d["timeline"][2]["at"] = "00:01"


def _duplicate_id(d):
    d["timeline"][4]["id"] = "hello"


def _say_without_id(d):
    del d["timeline"][4]["id"]


def _unknown_screen(d):
    d["timeline"][1]["lines"][1]["screen"] = "moon"


def _todo_voice(d):
    d["voices"]["commander"]["voice_id"] = "TODO_COMMANDER_VOICE_ID"


@pytest.mark.parametrize(
    "breaks, level, where, message",
    [
        (_unknown_speaker, "error", "hello @ 00:02", "unknown speaker 'captain'"),
        (_missing_nl, "error", "hello @ 00:02 line 2", "no `nl` translation"),
        (_not_ascending, "error", "- @ 00:01", "earlier than the entry before it"),
        (_duplicate_id, "error", "hello @ 00:40", "duplicate id (also used @ 00:02)"),
        (_say_without_id, "error", "- @ 00:40", "needs an id"),
        (_unknown_screen, "error", "hello @ 00:02 line 2", "unknown screen 'moon'"),
        (_todo_voice, "warning", "voices.commander", "voice_id is still"),
    ],
)
def test_broken_show_gives_the_right_message(tmp_path, mini_data, breaks, level, where, message):
    breaks(mini_data)
    _, issues = load_and_check(write_show(tmp_path, mini_data))
    matching = [i for i in issues if i.level == level and i.where == where and message in i.message]
    assert matching, f"expected {level} at {where!r} containing {message!r}, got {[str(i) for i in issues]}"


def test_structural_error_names_the_path(tmp_path, mini_data):
    mini_data["timeline"][1]["at"] = "2 seconds"
    show, issues = load_and_check(write_show(tmp_path, mini_data))
    assert show is None
    assert any(i.where == "timeline.1.at" and "MM:SS" in i.message for i in issues)
