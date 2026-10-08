import re

from conftest import PROJECT, SHOW_YAML
from unbox_show.model import load_show, parse_time
from unbox_show.plan import build_plan, characters_per_voice, estimate_seconds, format_plan, paced_offsets

# The reference table in BACKLOG.md section 6 gives 15 s for the countdown, which no
# stated rule produces. Our grid rule ("Ten" one beat after the lead-in, then one line
# per second) puts liftoff ten seconds after "Ten" and gives 13.4 s.
REFERENCE_EXCEPTIONS = {"mc_countdown": 13.4}


def _reference_rows():
    text = (PROJECT / "BACKLOG.md").read_text(encoding="utf-8")
    section = text.split("## 6.")[1].split("## 7.")[0]
    for match in re.finditer(r"^\| (\d\d:\d\d) \| (\w+) \| ([\d.]+) s \|", section, re.M):
        yield match.group(1), match.group(2), float(match.group(3))


def test_estimates_match_the_reference_table():
    rows = {row.entry: row for row in build_plan(load_show(SHOW_YAML))}
    checked = 0
    for start, entry, length in _reference_rows():
        if entry not in rows:
            continue  # sound rows such as "alarm"
        row = rows[entry]
        assert abs(row.start - parse_time(start)) < 0.01
        expected = REFERENCE_EXCEPTIONS.get(entry, length)
        assert abs(row.estimate - expected) <= 0.5, f"{entry}: {row.estimate:.1f} vs {expected}"
        checked += 1
    assert checked >= 20


def test_estimate_rule():
    # 28 words / 2.5 / 0.95 + 2 line breaks * 0.35
    assert abs(estimate_seconds(["a " * 13, "b " * 6, "c " * 9], 0.95) - 12.49) < 0.01


def test_paced_offsets_are_on_a_one_second_grid():
    offsets = paced_offsets(1.6, 12, 1.0)
    assert offsets[:3] == [0.0, 3.0, 4.0]
    assert offsets[-1] == 13.0
    assert all(b - a == 1.0 for a, b in zip(offsets[1:], offsets[2:]))


def test_flags_overlap_and_tight_gaps(mini_show_path):
    show = load_show(mini_show_path)
    # "hello" is 10 words over two lines: 4.35 s from 00:02, so it ends at 00:06.35.
    show.timeline[2].at = "00:07"
    flags = {row.entry: row.flag for row in build_plan(show)}
    assert flags["hello"] == "tight"
    show.timeline[2].at = "00:06"
    flags = {row.entry: row.flag for row in build_plan(show)}
    assert flags["hello"] == "OVERLAP"


def test_character_totals():
    show = load_show(SHOW_YAML)
    totals = characters_per_voice(show)
    assert 2800 <= sum(totals.values()) <= 3000  # BACKLOG: about 2,900
    assert "Characters per voice" in format_plan(show)
