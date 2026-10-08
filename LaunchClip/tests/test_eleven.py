import base64
import json

import httpx
import pytest

from unbox_show.audio import probe_duration
from unbox_show.eleven import ElevenClient, ElevenError, FakeElevenClient, fake_tts_seconds, make_client
from unbox_show.model import Voice, VoiceSettings

VOICE = Voice(
    label="COMMANDER",
    voice_id="abc123",
    model_id="eleven_multilingual_v2",
    settings=VoiceSettings(stability=0.45, similarity_boost=0.75, style=0.35, use_speaker_boost=True, speed=0.95),
    seed=1101,
)
TEXT = "Welcome, crew. I will be your mission commander."


def test_fake_tts_returns_silence_and_linear_alignment(tmp_path):
    audio, alignment = FakeElevenClient().tts(TEXT, VOICE)
    expected = fake_tts_seconds(TEXT, 0.95)
    assert alignment.characters == list(TEXT)
    assert alignment.duration == pytest.approx(expected, abs=0.001)
    assert all(a <= b for a, b in zip(alignment.starts, alignment.starts[1:]))
    path = tmp_path / "x.mp3"
    path.write_bytes(audio)
    assert probe_duration(path) == pytest.approx(expected, abs=0.1)


def test_fake_sfx_has_the_requested_length(tmp_path):
    client = FakeElevenClient()
    path = tmp_path / "s.mp3"
    path.write_bytes(client.sfx("alarm", 2.5))
    assert probe_duration(path) == pytest.approx(2.5, abs=0.1)
    assert client.calls == [("sfx", "alarm")]


def _client(handler, **kwargs) -> ElevenClient:
    return ElevenClient("secret-key", transport=httpx.MockTransport(handler), sleep=lambda s: None, **kwargs)


def test_real_tts_request_shape():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(200, json={
            "audio_base64": base64.b64encode(b"ID3fake").decode(),
            "alignment": {
                "characters": ["H", "i"],
                "character_start_times_seconds": [0.0, 0.1],
                "character_end_times_seconds": [0.1, 0.25],
            },
            "normalized_alignment": {},
        })

    audio, alignment = _client(handler).tts(TEXT, VOICE, seed=1101)
    request = seen["request"]
    assert request.method == "POST"
    assert request.url.path == "/v1/text-to-speech/abc123/with-timestamps"
    assert request.url.params["output_format"] == "mp3_44100_128"
    assert request.headers["xi-api-key"] == "secret-key"
    assert json.loads(request.content) == {
        "text": TEXT,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.45, "similarity_boost": 0.75, "style": 0.35,
                           "use_speaker_boost": True, "speed": 0.95},
        "seed": 1101,
    }
    assert audio == b"ID3fake"
    assert alignment.duration == 0.25


def test_real_sfx_request_shape():
    seen = {}

    def handler(request):
        seen["request"] = request
        return httpx.Response(200, content=b"mp3bytes")

    assert _client(handler).sfx("Spaceship alarm", 7, loop=False) == b"mp3bytes"
    request = seen["request"]
    assert request.url.path == "/v1/sound-generation"
    assert json.loads(request.content) == {
        "text": "Spaceship alarm", "duration_seconds": 7, "prompt_influence": 0.5,
        "loop": False, "model_id": "eleven_text_to_sound_v2",
    }


def test_retries_on_429_and_5xx_then_succeeds():
    responses = [httpx.Response(429, headers={"retry-after": "3"}), httpx.Response(503), httpx.Response(200, content=b"ok")]
    sleeps = []
    client = ElevenClient("k", transport=httpx.MockTransport(lambda r: responses.pop(0)), sleep=sleeps.append)
    assert client.sfx("x", 1) == b"ok"
    assert sleeps == [3.0, 2.0]


def test_client_error_is_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(401, json={"detail": "invalid api key"})

    with pytest.raises(ElevenError, match="401"):
        _client(handler).sfx("x", 1)
    assert len(calls) == 1


def test_no_key_means_fake(tmp_path, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    assert make_client(tmp_path, fake=False).name == "fake"
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    assert make_client(tmp_path, fake=False).name == "elevenlabs"
    assert make_client(tmp_path, fake=True).name == "fake"
