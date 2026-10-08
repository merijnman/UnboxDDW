"""Speech stage: one request per `say` entry (one per line for paced entries).

Writes build/tts.json, the index the scheduler reads: per clip its wav,
duration, alignment and where each subtitle line starts in the text.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import cache as cache_mod
from .audio import probe_duration, to_wav
from .cache import Cache
from .eleven import OUTPUT_FORMAT, Alignment, Client, tts_body
from .model import ShowFile, Voice


@dataclass
class ClipRequest:
    clip_id: str
    entry_id: str
    speaker: str
    lines: list[int]
    texts: list[str]
    seed: int | None

    @property
    def text(self) -> str:
        return " ".join(self.texts)

    @property
    def line_offsets(self) -> list[int]:
        offsets, position = [], 0
        for text in self.texts:
            offsets.append(position)
            position += len(text) + 1
        return offsets


@dataclass
class StageStats:
    reused: list[str] = field(default_factory=list)
    generated: list[str] = field(default_factory=list)
    characters: int = 0


def clip_requests(show: ShowFile) -> list[ClipRequest]:
    requests = []
    for entry in show.say_entries():
        voice = show.voices[entry.say]
        texts = [line.en for line in entry.lines]
        if entry.pace_seconds:
            for index, text in enumerate(texts):
                requests.append(
                    ClipRequest(f"{entry.id}#{index:02d}", entry.id, entry.say, [index], [text], voice.seed)
                )
        else:
            requests.append(ClipRequest(entry.id, entry.id, entry.say, list(range(len(texts))), texts, voice.seed))
    return requests


def request_key(text: str, voice: Voice, seed: int | None, client_name: str) -> str:
    data = {**tts_body(text, voice, seed), "voice_id": voice.voice_id, "output_format": OUTPUT_FORMAT}
    if client_name == "fake":
        # Never let fake silence stand in for a real take, or the other way round.
        data["fake"] = True
    return cache_mod.key(data)


def pending(show: ShowFile, cache: Cache, client_name: str) -> list[ClipRequest]:
    """Requests that are not in the cache yet, i.e. the ones that would cost characters."""
    return [
        req
        for req in clip_requests(show)
        if not cache.get("tts", request_key(req.text, show.voices[req.speaker], req.seed, client_name), "mp3")
    ]


def synthesize(text: str, voice: Voice, seed: int | None, client: Client, cache: Cache) -> tuple[str, bool]:
    """Make sure the mp3 + alignment for this request is cached. Returns (hash, generated)."""
    digest = request_key(text, voice, seed, client.name)
    if cache.get("tts", digest, "mp3") and cache.get("tts", digest, "json"):
        return digest, False
    audio, alignment = client.tts(text, voice, seed)
    cache.put("tts", digest, "mp3", audio)
    cache.put_json("tts", digest, alignment.to_api())
    return digest, True


def render(show: ShowFile, client: Client, cache: Cache, build_dir: Path) -> tuple[dict, StageStats]:
    stats = StageStats()
    index: dict[str, dict] = {}
    for req in clip_requests(show):
        voice = show.voices[req.speaker]
        digest, generated = synthesize(req.text, voice, req.seed, client, cache)
        if generated:
            stats.generated.append(req.clip_id)
            stats.characters += len(req.text)
        else:
            stats.reused.append(req.clip_id)

        wav = cache.get("tts", digest, "wav") or to_wav(cache.path("tts", digest, "mp3"), cache.path("tts", digest, "wav"))
        alignment = Alignment.from_api(cache.get_json("tts", digest))
        duration = alignment.duration
        measured = probe_duration(wav)
        if abs(measured - duration) > 0.5:
            # The alignment can end before trailing breath or silence; trust the longer one.
            duration = max(duration, measured)
        if client.name != "fake":
            cache.record(req.clip_id, digest, duration, len(req.text))
        index[req.clip_id] = {
            "entry": req.entry_id,
            "speaker": req.speaker,
            "lines": req.lines,
            "text": req.text,
            "line_offsets": req.line_offsets,
            "hash": digest,
            "wav": wav.relative_to(build_dir).as_posix(),
            "duration": round(duration, 3),
            "alignment": alignment.to_api(),
            "fake": client.name == "fake",
        }
    cache.save_manifest()
    build_dir.mkdir(parents=True, exist_ok=True)
    (build_dir / "tts.json").write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8")
    return index, stats
