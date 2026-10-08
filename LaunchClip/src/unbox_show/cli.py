"""`unbox` command line."""

from __future__ import annotations

import sys
from pathlib import Path
import typer

from . import pipeline, schedule as schedule_mod, subtitles
from .audio import FfmpegError, check_ffmpeg
from .eleven import make_client
from .model import ShowFile
from .plan import format_plan
from .validate import has_errors, load_and_check

app = typer.Typer(help="Turn show.yaml into the Unbox show videos.", no_args_is_help=True, add_completion=False)

ShowOption = typer.Option(Path("show.yaml"), "--show", help="Path to show.yaml.")
FakeOption = typer.Option(False, "--fake", help="Use the fake client (silence, no cost) even if an API key is set.")
YesOption = typer.Option(False, "--yes", "-y", help="Do not ask before spending ElevenLabs characters.")


def _load(path: Path, quiet_warnings: bool = True) -> ShowFile:
    show, issues = load_and_check(path)
    for issue in issues:
        if issue.level == "error" or not quiet_warnings:
            typer.echo(str(issue), err=True)
    if show is None or has_errors(issues):
        typer.echo(f"{path} has errors; run `unbox validate` for the full list.", err=True)
        raise typer.Exit(1)
    return show


def _ffmpeg() -> None:
    try:
        check_ffmpeg()
    except FfmpegError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)


def _confirm(yes: bool):
    def confirm(message: str) -> bool:
        typer.echo(message)
        if yes:
            return True
        if not sys.stdin.isatty():
            typer.echo("No terminal to ask in; re-run with --yes to allow this.", err=True)
            return False
        return typer.confirm("", default=False)

    return confirm


def _paths(show: ShowFile) -> pipeline.Paths:
    return pipeline.Paths(show.base_dir)


def _client(show: ShowFile, fake: bool):
    client = make_client(show.base_dir, fake)
    typer.echo(f"Client: {client.name}")
    return client


@app.command()
def validate(show_path: Path = ShowOption) -> None:
    """Check show.yaml: references, translations, ordering, ids, voice ids."""
    show, issues = load_and_check(show_path)
    for issue in issues:
        typer.echo(str(issue))
    errors = sum(i.level == "error" for i in issues)
    warnings = len(issues) - errors
    typer.echo(f"{show_path}: {errors} errors, {warnings} warnings")
    raise typer.Exit(1 if errors or show is None else 0)


@app.command()
def plan(show_path: Path = ShowOption) -> None:
    """Dry run: estimated timing per entry and characters per voice. No API calls."""
    typer.echo(format_plan(_load(show_path)))


@app.command()
def tts(show_path: Path = ShowOption, fake: bool = FakeOption, yes: bool = YesOption) -> None:
    """Render every `say` entry (cached) and write build/tts.json."""
    _ffmpeg()
    show = _load(show_path)
    try:
        stats = pipeline.run_tts(show, _client(show, fake), _paths(show), _confirm(yes))
    except pipeline.Aborted as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    typer.echo(f"Speech: {len(stats.reused)} reused, {len(stats.generated)} generated, {stats.characters} characters")


@app.command()
def sfx(show_path: Path = ShowOption, fake: bool = FakeOption, yes: bool = YesOption) -> None:
    """Generate every sound with a prompt (cached), convert `file:` sounds, write build/sounds.json."""
    _ffmpeg()
    show = _load(show_path)
    try:
        stats = pipeline.run_sfx(show, _client(show, fake), _paths(show), _confirm(yes), typer.echo)
    except pipeline.Aborted as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    typer.echo(f"Sounds: {len(stats.reused)} reused, {len(stats.generated)} generated")


@app.command()
def schedule(show_path: Path = ShowOption) -> None:
    """Resolve the timeline against measured audio into build/schedule.json."""
    show = _load(show_path)
    try:
        result = pipeline.run_schedule(show, _paths(show))
    except FileNotFoundError:
        typer.echo("Run `unbox tts` and `unbox sfx` first.", err=True)
        raise typer.Exit(1)
    except schedule_mod.ScheduleError as exc:
        typer.echo(f"Schedule problems:\n{exc}", err=True)
        raise typer.Exit(1)
    typer.echo(f"{len(result['events'])} events -> build/schedule.json")


@app.command()
def subs(show_path: Path = ShowOption) -> None:
    """Write build/subs.ass and dist/unbox_show.{en,nl}.srt from build/schedule.json."""
    show = _load(show_path)
    paths = _paths(show)
    sched = schedule_mod.load(paths.build / "schedule.json")
    cues = subtitles.write_all(sched, paths.build / "subs.ass", paths.dist / "unbox_show")
    typer.echo(f"{len(cues)} subtitle cues")


@app.command()
def build(
    show_path: Path = ShowOption,
    fake: bool = FakeOption,
    yes: bool = YesOption,
    draft: bool = typer.Option(False, "--draft", help="Render 960x540 for quick checks."),
    only: str = typer.Option("show", "--only", help="show | preshow | loop"),
) -> None:
    """Run every stage in order and report what was reused and what was regenerated."""
    if only != "show":
        typer.echo(f"--only {only} is not built yet (T16/T17). Only `show` works for now.", err=True)
        raise typer.Exit(1)
    _ffmpeg()
    show = _load(show_path)
    try:
        report = pipeline.build_show(
            show, _client(show, fake), _paths(show), draft=draft, confirm=_confirm(yes), echo=typer.echo
        )
    except pipeline.Aborted as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    except schedule_mod.ScheduleError as exc:
        typer.echo(f"Schedule problems:\n{exc}", err=True)
        raise typer.Exit(1)
    typer.echo("")
    for line in report.lines():
        typer.echo(line)


@app.command()
def audition(
    speaker: str = typer.Option(..., "--speaker", help="Voice name in show.yaml, e.g. commander."),
    voices: str = typer.Option(..., "--voices", help="Comma-separated candidate voice ids."),
    show_path: Path = ShowOption,
    fake: bool = FakeOption,
    yes: bool = YesOption,
) -> None:
    """Render the speaker's audition passage once per candidate voice into build/audition/."""
    _ffmpeg()
    show = _load(show_path)
    if speaker not in show.voices:
        typer.echo(f"Unknown speaker {speaker!r}; known: {', '.join(show.voices)}", err=True)
        raise typer.Exit(1)
    voice_ids = [v.strip() for v in voices.split(",") if v.strip()]
    try:
        written = pipeline.audition(show, speaker, voice_ids, _client(show, fake), _paths(show), _confirm(yes))
    except (pipeline.Aborted, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    for path in written:
        typer.echo(f"Wrote {path}")


@app.command()
def handouts(show_path: Path = ShowOption) -> None:
    """Run sheet and Dutch transcript card (T18, not built yet)."""
    typer.echo("Not built yet (T18).", err=True)
    raise typer.Exit(1)
