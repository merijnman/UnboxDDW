import pytest
from typer.testing import CliRunner

from unbox_show import audio
from unbox_show.cli import app


def test_help_lists_the_commands():
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("validate", "plan", "tts", "sfx", "schedule", "subs", "build", "audition", "handouts"):
        assert command in result.output


def test_missing_ffmpeg_gives_install_hint(monkeypatch):
    monkeypatch.setattr(audio.shutil, "which", lambda name: None)
    with pytest.raises(audio.FfmpegError, match="winget install Gyan.FFmpeg"):
        audio.check_ffmpeg()
