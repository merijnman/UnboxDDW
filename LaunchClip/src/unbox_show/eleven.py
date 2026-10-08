"""ElevenLabs client and its fake twin.

Every ElevenLabs call in the project goes through `ElevenClient`. Tests and the
first milestone use `FakeElevenClient`, which never touches the network.
"""

from __future__ import annotations

import base64
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import httpx
from dotenv import load_dotenv

from .audio import silence_mp3
from .model import Voice
from .plan import PAUSE_PER_LINE_BREAK, WORDS_PER_SECOND

API_URL = "https://api.elevenlabs.io"
OUTPUT_FORMAT = "mp3_44100_128"
SFX_MODEL = "eleven_text_to_sound_v2"
SFX_PROMPT_INFLUENCE = 0.5


class ElevenError(RuntimeError):
    pass


@dataclass
class Alignment:
    """Per-character timing of a TTS clip, in seconds from the start of the clip."""

    characters: list[str]
    starts: list[float]
    ends: list[float]

    @classmethod
    def from_api(cls, data: dict) -> Alignment:
        return cls(
            characters=list(data["characters"]),
            starts=[float(t) for t in data["character_start_times_seconds"]],
            ends=[float(t) for t in data["character_end_times_seconds"]],
        )

    def to_api(self) -> dict:
        return {
            "characters": self.characters,
            "character_start_times_seconds": self.starts,
            "character_end_times_seconds": self.ends,
        }

    @property
    def duration(self) -> float:
        return self.ends[-1] if self.ends else 0.0


def tts_body(text: str, voice: Voice, seed: int | None) -> dict:
    """Request body for one speech request. Also the core of its cache key."""
    body = {
        "text": text,
        "model_id": voice.model_id,
        "voice_settings": voice.settings.model_dump(),
    }
    if seed is not None:
        body["seed"] = seed
    return body


def sfx_body(prompt: str, seconds: float, loop: bool) -> dict:
    return {
        "text": prompt,
        "duration_seconds": seconds,
        "prompt_influence": SFX_PROMPT_INFLUENCE,
        "loop": loop,
        "model_id": SFX_MODEL,
    }


class Client(Protocol):
    name: str

    def tts(self, text: str, voice: Voice, seed: int | None = None) -> tuple[bytes, Alignment]: ...

    def sfx(self, prompt: str, seconds: float, loop: bool = False) -> bytes: ...


class ElevenClient:
    """The real thing. Requests are sequential; 429 and 5xx are retried with backoff."""

    name = "elevenlabs"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = API_URL,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 120.0,
        max_retries: int = 5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = httpx.Client(
            base_url=base_url,
            headers={"xi-api-key": api_key},
            transport=transport,
            timeout=timeout,
        )
        self._max_retries = max_retries
        self._sleep = sleep

    def _post(self, path: str, body: dict, params: dict | None = None) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            response = self._http.post(path, params=params, json=body)
            retryable = response.status_code == 429 or response.status_code >= 500
            if retryable and attempt < self._max_retries:
                retry_after = response.headers.get("retry-after", "")
                delay = float(retry_after) if retry_after.replace(".", "", 1).isdigit() else 2.0**attempt
                self._sleep(delay)
                continue
            if response.is_error:
                raise ElevenError(f"ElevenLabs {path} returned {response.status_code}: {response.text[:500]}")
            return response
        raise AssertionError("unreachable")

    def tts(self, text: str, voice: Voice, seed: int | None = None) -> tuple[bytes, Alignment]:
        response = self._post(
            f"/v1/text-to-speech/{voice.voice_id}/with-timestamps",
            tts_body(text, voice, seed),
            params={"output_format": OUTPUT_FORMAT},
        )
        data = response.json()
        return base64.b64decode(data["audio_base64"]), Alignment.from_api(data["alignment"])

    def sfx(self, prompt: str, seconds: float, loop: bool = False) -> bytes:
        return self._post(
            "/v1/sound-generation",
            sfx_body(prompt, seconds, loop),
            params={"output_format": OUTPUT_FORMAT},
        ).content


_SENTENCE_BREAK = re.compile(r"[.!?:]\s+\S")


def fake_tts_seconds(text: str, speed: float) -> float:
    """Same rule as the plan: words / 2.5 / speed, plus a pause at every sentence break."""
    words = len(text.split())
    breaks = len(_SENTENCE_BREAK.findall(text))
    return max(0.3, words / WORDS_PER_SECOND / speed + PAUSE_PER_LINE_BREAK * breaks)


class FakeElevenClient:
    """Silence of the estimated length, with a linear character alignment. No network."""

    name = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def tts(self, text: str, voice: Voice, seed: int | None = None) -> tuple[bytes, Alignment]:
        self.calls.append(("tts", text))
        seconds = round(fake_tts_seconds(text, voice.settings.speed), 3)
        step = seconds / len(text)
        alignment = Alignment(
            characters=list(text),
            starts=[round(i * step, 3) for i in range(len(text))],
            ends=[round((i + 1) * step, 3) for i in range(len(text))],
        )
        alignment.ends[-1] = seconds
        return silence_mp3(seconds), alignment

    def sfx(self, prompt: str, seconds: float, loop: bool = False) -> bytes:
        self.calls.append(("sfx", prompt))
        return silence_mp3(round(seconds, 3))


def api_key(project_dir: Path) -> str | None:
    """ELEVENLABS_API_KEY from the environment or from <project>/.env."""
    load_dotenv(project_dir / ".env", override=False)
    return os.getenv("ELEVENLABS_API_KEY") or None


def make_client(project_dir: Path, fake: bool) -> Client:
    """The fake client when asked for, or when no API key is set."""
    key = None if fake else api_key(project_dir)
    return ElevenClient(key) if key else FakeElevenClient()
