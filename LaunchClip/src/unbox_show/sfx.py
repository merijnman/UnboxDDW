"""Sound stage: generate every `sounds` entry that has a prompt, convert `file:` entries.

Writes build/sounds.json: per sound its wav, duration and mix settings.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import cache as cache_mod
from .audio import probe_duration, to_wav
from .cache import Cache
from .eleven import Client, sfx_body
from .model import ShowFile, Sound
from .tts import StageStats


def sound_key(sound: Sound, client_name: str) -> str:
    data = sfx_body(sound.prompt, sound.seconds, sound.loop)
    if client_name == "fake":
        data["fake"] = True
    return cache_mod.key(data)


def pending(show: ShowFile, cache: Cache, client_name: str) -> list[str]:
    return [
        name
        for name, sound in show.sounds.items()
        if sound.prompt is not None and not cache.get("sfx", sound_key(sound, client_name), "mp3")
    ]


def render(show: ShowFile, client: Client, cache: Cache, build_dir: Path, warn=print) -> tuple[dict, StageStats]:
    stats = StageStats()
    index: dict[str, dict] = {}
    for name, sound in show.sounds.items():
        if sound.prompt is not None:
            digest = sound_key(sound, client.name)
            if cache.get("sfx", digest, "mp3"):
                stats.reused.append(name)
            else:
                cache.put("sfx", digest, "mp3", client.sfx(sound.prompt, sound.seconds, sound.loop))
                stats.generated.append(name)
            source = cache.path("sfx", digest, "mp3")
        else:
            source = show.base_dir / sound.file
            if not source.exists():
                warn(f"sound {name}: file {sound.file} not found, it stays silent until supplied")
                continue
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            stats.reused.append(name)

        wav = cache.get("sfx", digest, "wav") or to_wav(source, cache.path("sfx", digest, "wav"))
        index[name] = {
            "wav": wav.relative_to(build_dir).as_posix(),
            "duration": round(probe_duration(wav), 3),
            "loop": sound.loop,
            "gain_db": sound.gain_db,
            "fade_in": sound.fade_in,
            "fade_out": sound.fade_out,
            "fake": sound.prompt is not None and client.name == "fake",
        }
    build_dir.mkdir(parents=True, exist_ok=True)
    (build_dir / "sounds.json").write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8")
    return index, stats
