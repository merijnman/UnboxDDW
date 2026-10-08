import json

import pytest

from conftest import write_show
from unbox_show import cache, pipeline, tts
from unbox_show.eleven import FakeElevenClient
from unbox_show.model import load_show


def test_key_ignores_dict_order():
    assert cache.key({"a": 1, "b": {"x": 1, "y": 2}}) == cache.key({"b": {"y": 2, "x": 1}, "a": 1})
    assert cache.key({"a": 1}) != cache.key({"a": 2})


class _PretendRealClient(FakeElevenClient):
    """Behaves like the fake but is treated as paid: keys, gate and manifest as for the real API."""

    name = "elevenlabs"


def _render(show_path):
    show = load_show(show_path)
    client = FakeElevenClient()
    paths = pipeline.Paths(show.base_dir)
    index, stats = tts.render(show, client, paths.cache, paths.build)
    return client, index, stats, paths


def test_second_run_is_free_and_one_word_costs_one_call(tmp_path, mini_data):
    show_path = write_show(tmp_path, mini_data)
    client, index, stats, paths = _render(show_path)
    assert len(client.calls) == len(index) == 6  # hello, 4 countdown lines, bye
    assert stats.reused == []

    client, _, stats, _ = _render(show_path)
    assert client.calls == []
    assert len(stats.reused) == 6

    mini_data["timeline"][1]["lines"][0]["en"] = "Welcome, crew. This is a rehearsal."
    write_show(tmp_path, mini_data)
    client, _, stats, _ = _render(show_path)
    assert len(client.calls) == 1
    assert stats.generated == ["hello"]


def test_manifest_records_real_takes_only(tmp_path, mini_data):
    _, _, _, paths = _render(write_show(tmp_path, mini_data))
    assert not (paths.build / "cache" / "manifest.json").exists()

    show = load_show(tmp_path / "show.yaml")
    index, _ = tts.render(show, _PretendRealClient(), paths.cache, paths.build)
    manifest = json.loads((paths.build / "cache" / "manifest.json").read_text())
    assert manifest["hello"]["hash"] == index["hello"]["hash"]
    assert manifest["hello"]["characters"] == len(index["hello"]["text"])
    assert manifest["hello"]["duration"] == pytest.approx(index["hello"]["duration"])
    assert set(manifest["hello"]) == {"hash", "duration", "characters", "date"}


def test_fake_and_real_takes_never_share_a_key(mini_data, tmp_path):
    show = load_show(write_show(tmp_path, mini_data))
    voice = show.voices["commander"]
    assert tts.request_key("Hi.", voice, 1, "fake") != tts.request_key("Hi.", voice, 1, "elevenlabs")


def test_budget_gate_asks_before_paid_calls_and_aborts_on_no(tmp_path, mini_data):
    show = load_show(write_show(tmp_path, mini_data))
    paths = pipeline.Paths(show.base_dir)
    client = _PretendRealClient()
    questions = []

    def refuse(message):
        questions.append(message)
        return False

    with pytest.raises(pipeline.Aborted):
        pipeline.run_tts(show, client, paths, refuse)
    assert client.calls == []
    assert "6 speech requests" in questions[0]
    assert str(sum(len(r.text) for r in tts.clip_requests(show))) in questions[0]


def test_budget_gate_is_silent_when_everything_is_cached(tmp_path, mini_data):
    show = load_show(write_show(tmp_path, mini_data))
    paths = pipeline.Paths(show.base_dir)
    pipeline.run_tts(show, _PretendRealClient(), paths, lambda m: True)

    def must_not_ask(message):
        raise AssertionError("asked although nothing is to be paid")

    client = _PretendRealClient()
    pipeline.run_tts(show, client, paths, must_not_ask)
    assert client.calls == []


def test_audition_writes_one_file_per_voice(tmp_path, mini_data):
    show = load_show(write_show(tmp_path, mini_data))
    paths = pipeline.Paths(show.base_dir)
    written = pipeline.audition(show, "commander", ["v1", "v2", "v3"], FakeElevenClient(), paths, lambda m: True)
    assert [p.name for p in written] == [f"commander__v{i}__fake.mp3" for i in (1, 2, 3)]
    assert all(p.stat().st_size > 0 for p in written)


def test_real_run_refuses_unpicked_voices(tmp_path, mini_data):
    mini_data["voices"]["commander"]["voice_id"] = "TODO_COMMANDER_VOICE_ID"
    show = load_show(write_show(tmp_path, mini_data))
    client = _PretendRealClient()
    with pytest.raises(pipeline.Aborted, match="No voice chosen yet for: commander"):
        pipeline.run_tts(show, client, pipeline.Paths(show.base_dir), lambda m: True)
    assert client.calls == []
