"""Mixdown with test tones: duration, peak limit, clip placement, ducking, effects."""

import json
import subprocess

import pytest

from unbox_show import fx, mix
from unbox_show.audio import probe_duration, run_ffmpeg
from unbox_show.cache import Cache


def tone(path, seconds, freq=440, volume=0.5):
    run_ffmpeg(["-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}:sample_rate=48000",
                "-af", f"volume={volume}", "-ac", "2", "-c:a", "pcm_s16le", str(path)])
    return path


def samples(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "1", "-"],
                         capture_output=True).stdout
    import array

    data = array.array("h")
    data.frombytes(raw)
    return data


@pytest.fixture
def build(tmp_path):
    build = tmp_path / "build"
    (build / "cache" / "tts").mkdir(parents=True)
    (build / "cache" / "sfx").mkdir(parents=True)
    tone(build / "cache" / "tts" / "voice.wav", 2.0, 440)
    tone(build / "cache" / "sfx" / "bed.wav", 3.0, 110, 0.3)
    tone(build / "cache" / "sfx" / "crackle.wav", 1.0, 3000, 0.2)
    return build


SOUNDS = {
    "bed": {"wav": "cache/sfx/bed.wav", "duration": 3.0, "loop": True, "gain_db": 0, "fade_in": 0, "fade_out": 0},
    "radio_crackle": {"wav": "cache/sfx/crackle.wav", "duration": 1.0, "loop": False, "gain_db": 0,
                      "fade_in": 0, "fade_out": 0},
}


def schedule(voice_t=4.0, fx_name=None):
    return {
        "duration": 10.0, "bed": [], "bed_gain_db": 0, "duck_bed_under_speech_db": -6,
        "events": [{"t": voice_t, "type": "voice", "end": voice_t + 2.0, "clip": "v", "entry": "v",
                    "speaker": "s", "fx": fx_name, "wav": "cache/tts/voice.wav"}],
    }


class _Show:
    fx = None


def test_duration_peak_and_clip_offset(build):
    out = mix.build_mix(schedule(voice_t=4.0), SOUNDS, build, build / "mix.wav", _Show(), with_fx=False)
    assert probe_duration(out) == pytest.approx(10.0, abs=0.01)
    data = samples(out)
    first = next(i for i, v in enumerate(data) if abs(v) > 200)
    assert first / 48000 == pytest.approx(4.0, abs=0.01)  # the voice starts exactly at its scheduled time
    peak_db = 20 * __import__("math").log10(max(abs(v) for v in data) / 32768)
    assert peak_db <= mix.TRUE_PEAK + 0.1
    voice_only = build / "voice_part.wav"
    run_ffmpeg(["-ss", "4.0", "-t", "2.0", "-i", str(out), str(voice_only)])
    assert fx.measure_lufs(voice_only) == pytest.approx(mix.TARGET_LUFS, abs=1.0)


def test_bed_is_ducked_under_speech(build):
    sched = schedule(voice_t=5.0) | {"bed": ["bed"]}
    expr = mix.duck_expression(mix._speech_intervals(sched["events"]), -6)

    def gain_at(t):
        names = {"clip": lambda v, lo, hi: max(lo, min(hi, v)), "max": max, "min": min, "t": t}
        return eval(expr, names)  # the same expression ffmpeg evaluates

    inside, outside = gain_at(6.0), gain_at(1.0)
    assert outside == pytest.approx(1.0)
    assert inside == pytest.approx(10 ** (-6 / 20), abs=0.001)
    mix.build_mix(sched, SOUNDS, build, build / "mix.wav", _Show(), with_fx=False)  # the graph runs


def test_radio_fx_is_applied_and_cached(build):
    cache = Cache(build / "cache")
    raw = build / "cache" / "tts" / "voice.wav"
    once = fx.apply(raw, ["radio"], _Show(), cache)
    again = fx.apply(raw, ["radio"], _Show(), cache)
    assert once == again and once != raw
    assert probe_duration(once) == pytest.approx(probe_duration(raw), abs=0.02)


def test_radio_turn_gets_squelch_clicks(build):
    out = mix.build_mix(schedule(voice_t=4.0, fx_name="radio"), SOUNDS, build, build / "mix.wav", _Show())
    data = samples(out)
    first = next(i for i, v in enumerate(data) if abs(v) > 200)
    assert first / 48000 == pytest.approx(4.0 - mix.SQUELCH_SECONDS, abs=0.02)
