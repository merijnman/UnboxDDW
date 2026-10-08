"""Video assembly: schedule.json -> stills -> ffmpeg concat -> burned subtitles -> mp4."""

from __future__ import annotations

import math
from pathlib import Path

from .audio import run_ffmpeg
from .frames import FrameState, Renderer

OPS_MARKER_SECONDS = 10.0
TIMER_LINGER = 1.0  # "00:00" stays up this long after a timer runs out


def _change_points(schedule: dict) -> list[float]:
    points = {0.0, schedule["duration"]}
    for event in schedule["events"]:
        kind = event["type"]
        if kind in ("screen", "overlay", "big"):
            points.add(event["t"])
        elif kind == "ops":
            points.update((event["t"], event["t"] + OPS_MARKER_SECONDS))
        elif kind == "timer":
            start, end = event["t"], event["end"]
            points.update((start, end + TIMER_LINGER))
            k = 0
            while end - k > start:
                points.add(end - k)
                k += 1
    return sorted(p for p in points if 0 <= p <= schedule["duration"])


def state_at(schedule: dict, t: float) -> FrameState:
    screen, overlays, big, timer, ops = None, [], None, None, None
    for event in schedule["events"]:
        if event["t"] > t + 1e-6:
            break
        kind = event["type"]
        if kind == "screen":
            screen, overlays, big = event["name"], [], None
        elif kind == "overlay":
            overlays.append(event["name"])
        elif kind == "big":
            big = event["text"]
        elif kind == "ops":
            ops = event["text"] if t < event["t"] + OPS_MARKER_SECONDS - 1e-6 else None
        elif kind == "timer" and t < event["end"] + TIMER_LINGER - 1e-6:
            timer = max(0, math.ceil(round(event["end"] - t, 6)))
    return FrameState(screen=screen, overlays=tuple(overlays), big=big, timer=timer, ops=ops)


def segments(schedule: dict, fps: int) -> list[tuple[float, float, FrameState]]:
    """Constant-picture intervals, snapped to the frame grid."""
    snapped = sorted({round(p * fps) / fps for p in _change_points(schedule)})
    result: list[tuple[float, float, FrameState]] = []
    for start, end in zip(snapped, snapped[1:]):
        state = state_at(schedule, start)
        if result and result[-1][2] == state:
            result[-1] = (result[-1][0], end, state)
        else:
            result.append((start, end, state))
    return result


def render_frames(schedule: dict, renderer: Renderer, frames_dir: Path, fps: int, progress=None) -> list[tuple[Path, float]]:
    """Render each distinct state once (reused across builds); return (png, seconds) pairs."""
    frames_dir.mkdir(parents=True, exist_ok=True)
    signature = renderer.asset_signature()
    rendered: list[tuple[Path, float]] = []
    segs = segments(schedule, fps)
    for i, (start, end, state) in enumerate(segs):
        path = frames_dir / f"{state.digest(signature)}.png"
        if not path.exists():
            tmp = path.with_name(path.stem + ".tmp.png")
            renderer.render(state).save(tmp, optimize=False, compress_level=1)
            tmp.replace(path)
        rendered.append((path, end - start))
        if progress:
            progress(i + 1, len(segs))
    return rendered


def write_concat(frames: list[tuple[Path, float]], path: Path) -> Path:
    base = path.parent
    lines = ["ffconcat version 1.0"]
    for png, seconds in frames:
        lines += [f"file '{png.relative_to(base).as_posix()}'", f"duration {seconds:.6f}"]
    # The concat demuxer ignores the last duration unless the last file is listed again.
    lines.append(f"file '{frames[-1][0].relative_to(base).as_posix()}'")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def assemble(build_dir: Path, concat: Path, subs: Path, audio: Path, out: Path, duration: float, fps: int) -> Path:
    """Encode the video. ffmpeg runs in build_dir so the `ass=` filter sees a plain relative path
    (it chokes on Windows drive letters and backslashes)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".tmp.mp4")
    run_ffmpeg(
        [
            "-f", "concat", "-safe", "0", "-i", concat.relative_to(build_dir).as_posix(),
            "-i", audio.relative_to(build_dir).as_posix(),
            "-vf", f"fps={fps},ass={subs.relative_to(build_dir).as_posix()},format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage", "-crf", "20",
            "-r", str(fps), "-g", str(fps * 2),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-t", f"{duration:.3f}", "-movflags", "+faststart",
            str(tmp.resolve()),
        ],
        cwd=build_dir,
    )
    tmp.replace(out)
    return out
