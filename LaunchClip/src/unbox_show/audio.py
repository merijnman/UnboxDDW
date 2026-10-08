"""Small ffmpeg/ffprobe helpers. All audio and video work goes through ffmpeg."""

from __future__ import annotations

import functools
import shutil
import subprocess
from pathlib import Path

SAMPLE_RATE = 48000
CHANNELS = 2

FFMPEG_MISSING = (
    "ffmpeg was not found on PATH.\n"
    "Install it with:  winget install Gyan.FFmpeg\n"
    "then open a new terminal so PATH is refreshed."
)


class FfmpegError(RuntimeError):
    pass


def check_ffmpeg() -> None:
    """Raise FfmpegError with install instructions when ffmpeg or ffprobe is missing."""
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise FfmpegError(FFMPEG_MISSING)


def run_ffmpeg(args: list[str], cwd: Path | None = None, input: bytes | None = None) -> bytes:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args]
    result = subprocess.run(cmd, cwd=cwd, input=input, capture_output=True)
    if result.returncode != 0:
        raise FfmpegError(f"ffmpeg failed: {' '.join(cmd)}\n{result.stderr.decode(errors='replace')}")
    return result.stdout


def probe_duration(path: Path) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise FfmpegError(f"ffprobe failed on {path}: {result.stderr}")
    return float(result.stdout.strip())


@functools.lru_cache(maxsize=256)
def silence_mp3(seconds: float) -> bytes:
    """MP3 bytes of silence, used by the fake client."""
    return run_ffmpeg(
        [
            "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono",
            "-t", f"{seconds:.3f}", "-c:a", "libmp3lame", "-b:a", "32k", "-f", "mp3", "pipe:1",
        ]
    )


def to_wav(src: Path, dst: Path) -> Path:
    """Convert any audio file to 48 kHz stereo 16-bit wav for mixing."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".tmp.wav")
    run_ffmpeg(["-i", str(src), "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), "-c:a", "pcm_s16le", str(tmp)])
    tmp.replace(dst)
    return dst
