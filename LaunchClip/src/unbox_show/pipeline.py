"""Stages wired together, plus the budget gate in front of every paid call."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import mix, schedule, sfx, subtitles, tts, video
from .cache import Cache
from .eleven import Client
from .frames import Renderer
from .model import ShowFile

Confirm = Callable[[str], bool]
Echo = Callable[[str], None]

DRAFT_SIZE = (960, 540)


class Aborted(Exception):
    pass


@dataclass
class Paths:
    project: Path

    @property
    def build(self) -> Path:
        return self.project / "build"

    @property
    def dist(self) -> Path:
        return self.project / "dist"

    @property
    def cache(self) -> Cache:
        return Cache(self.build / "cache")


@dataclass
class Report:
    client: str
    speech: tts.StageStats = field(default_factory=tts.StageStats)
    sounds: tts.StageStats = field(default_factory=tts.StageStats)
    outputs: list[Path] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [f"Client: {self.client}"]
        for name, stats in (("Speech", self.speech), ("Sounds", self.sounds)):
            out.append(f"{name}: {len(stats.reused)} reused, {len(stats.generated)} generated")
            if stats.generated:
                out.append(f"  generated: {', '.join(stats.generated)}")
        spent = self.speech.characters if self.client != "fake" else 0
        out.append(f"Characters spent: {spent}")
        out += [f"Wrote {path}" for path in self.outputs]
        return out


def budget_gate(show: ShowFile, cache: Cache, client: Client, confirm: Confirm, speech: bool = True, sounds: bool = True) -> None:
    """Ask before anything is sent to the paid API. Cached results are free and not asked about."""
    if client.name == "fake":
        return
    speech_pending = tts.pending(show, cache, client.name) if speech else []
    sound_pending = sfx.pending(show, cache, client.name) if sounds else []
    unpicked = sorted({req.speaker for req in speech_pending if show.voices[req.speaker].voice_id.startswith("TODO")})
    if unpicked:
        raise Aborted(
            f"No voice chosen yet for: {', '.join(unpicked)}. Run `unbox audition` and fill in the voice_id "
            "in show.yaml, or use --fake. Nothing was sent to ElevenLabs."
        )
    if not speech_pending and not sound_pending:
        return
    chars = sum(len(req.text) for req in speech_pending)
    parts = []
    if speech_pending:
        parts.append(f"{len(speech_pending)} speech requests ({chars} characters): "
                     + ", ".join(req.clip_id for req in speech_pending))
    if sound_pending:
        parts.append(f"{len(sound_pending)} sound generations: {', '.join(sound_pending)}")
    if not confirm("ElevenLabs will be called for\n  " + "\n  ".join(parts) + "\nContinue?"):
        raise Aborted("Nothing was sent to ElevenLabs.")


def run_tts(show: ShowFile, client: Client, paths: Paths, confirm: Confirm) -> tts.StageStats:
    cache = paths.cache
    budget_gate(show, cache, client, confirm, sounds=False)
    return tts.render(show, client, cache, paths.build)[1]


def run_sfx(show: ShowFile, client: Client, paths: Paths, confirm: Confirm, warn: Echo) -> tts.StageStats:
    cache = paths.cache
    budget_gate(show, cache, client, confirm, speech=False)
    return sfx.render(show, client, cache, paths.build, warn=warn)[1]


def run_schedule(show: ShowFile, paths: Paths) -> dict:
    clips = schedule.load(paths.build / "tts.json")
    sounds = schedule.load(paths.build / "sounds.json")
    result = schedule.build_schedule(show, clips, sounds)
    schedule.write(result, paths.build / "schedule.json")
    return result


def build_show(show: ShowFile, client: Client, paths: Paths, *, draft: bool, confirm: Confirm, echo: Echo) -> Report:
    report = Report(client=client.name)
    cache = paths.cache
    budget_gate(show, cache, client, confirm)

    echo("speech ...")
    _, report.speech = tts.render(show, client, cache, paths.build)
    echo("sounds ...")
    sounds, report.sounds = sfx.render(show, client, cache, paths.build, warn=echo)
    echo("schedule ...")
    sched = run_schedule(show, paths)
    echo("subtitles ...")
    srt_stem = paths.dist / "unbox_show"
    subtitles.write_all(sched, paths.build / "subs.ass", srt_stem)
    echo("audio mix ...")
    mix_path = mix.build_mix(sched, sounds, paths.build, paths.build / "mix.wav")

    fps = show.show.video.fps
    size = DRAFT_SIZE if draft else (show.show.video.width, show.show.video.height)
    renderer = Renderer(show, size)
    frames_dir = paths.build / f"frames_{size[0]}x{size[1]}"

    def progress(done: int, total: int) -> None:
        if done == total or done % 100 == 0:
            echo(f"frames {done}/{total}")

    frames = video.render_frames(sched, renderer, frames_dir, fps, progress)
    concat = video.write_concat(frames, paths.build / f"frames_{size[0]}x{size[1]}.txt")
    out = paths.dist / ("unbox_show_draft.mp4" if draft else "unbox_show.mp4")
    echo("encoding video ...")
    video.assemble(paths.build, concat, paths.build / "subs.ass", mix_path, out, sched["duration"], fps)
    report.outputs = [out, Path(f"{srt_stem}.en.srt"), Path(f"{srt_stem}.nl.srt")]
    return report


def audition(show: ShowFile, speaker: str, voice_ids: list[str], client: Client, paths: Paths, confirm: Confirm) -> list[Path]:
    """Render the speaker's fixed audition passage once per candidate voice."""
    voice = show.voices[speaker]
    if not voice.audition:
        raise ValueError(f"voices.{speaker} has no `audition:` passage in show.yaml")
    cache = paths.cache
    candidates = [voice.model_copy(update={"voice_id": vid}) for vid in voice_ids]
    todo = [c for c in candidates
            if not cache.get("tts", tts.request_key(voice.audition, c, voice.seed, client.name), "mp3")]
    if todo and client.name != "fake":
        message = (f"ElevenLabs will be called for {len(todo)} audition takes of {len(voice.audition)} "
                   f"characters each ({len(todo) * len(voice.audition)} characters in total).\nContinue?")
        if not confirm(message):
            raise Aborted("Nothing was sent to ElevenLabs.")
    out_dir = paths.build / "audition"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for candidate in candidates:
        digest, _ = tts.synthesize(voice.audition, candidate, voice.seed, client, cache)
        target = out_dir / f"{speaker}__{candidate.voice_id}{'__fake' if client.name == 'fake' else ''}.mp3"
        shutil.copyfile(cache.path("tts", digest, "mp3"), target)
        written.append(target)
    return written
