"""ffmpeg-based capture on Windows (dshow). The camera is only ever opened
by ffmpeg, only during a recording -- the app never holds a persistent
cv2.VideoCapture on the camera device.

Flow: RecordingSession.start() launches one ffmpeg process for the full
countdown+record duration (started at the beginning of COUNTDOWN). Once it
exits, start_postprocess() trims off the countdown lead-in, extracts a wav
sidecar, and appends the index.jsonl entry -- all in a background thread so
the render loop never blocks.
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional

import config as cfg
import library

logger = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000  # avoid flashing a console window per ffmpeg call


def _posix(p: Path) -> str:
    return p.as_posix()


def _ffmpeg(config: dict[str, Any]) -> str:
    return config.get("ffmpeg_path", "ffmpeg")


def list_devices(config: dict[str, Any]) -> None:
    """Print available dshow video/audio devices (ffmpeg lists them on stderr)."""
    cmd = [_ffmpeg(config), "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"]
    print("Beschikbare dshow-devices:\n")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        print(result.stderr)
    except FileNotFoundError:
        print(f"ffmpeg niet gevonden op '{_ffmpeg(config)}'. Zet ffmpeg in PATH of configureer 'ffmpeg_path'.")
    except subprocess.TimeoutExpired:
        print("ffmpeg reageerde niet binnen 20 seconden.")


def open_camera_settings_dialog(config: dict[str, Any]) -> None:
    """Opens the dshow device property dialog so exposure/white balance can
    be set manually. Blocking; closes when the user closes the dialog."""
    video_device = config.get("video_device", "")
    if not video_device:
        print("Geen 'video_device' geconfigureerd in config.json.")
        return
    cmd = [
        _ffmpeg(config), "-f", "dshow", "-show_video_device_dialog", "true",
        "-i", f"video={video_device}",
    ]
    try:
        subprocess.run(cmd, timeout=120)
    except FileNotFoundError:
        print(f"ffmpeg niet gevonden op '{_ffmpeg(config)}'.")
    except subprocess.TimeoutExpired:
        logger.warning("Camera-instellingendialoog timeout")


def _input_arg(config: dict[str, Any]) -> str:
    video_device = config.get("video_device", "")
    audio_device = config.get("audio_device", "")
    return f"video={video_device}:audio={audio_device}"


class RecordingSession:
    """Owns the single ffmpeg process for one recording (countdown + record)."""

    def __init__(self, config: dict[str, Any], rec_id: str):
        self.config = config
        self.rec_id = rec_id
        self.tmp_path = cfg.videos_dir(config) / f"tmp_{rec_id}.mp4"
        self.duration = float(config["countdown_seconds"]) + float(config["record_seconds"])
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self.used_fallback = False
        self.start_error: Optional[str] = None
        self._log_file = None  # currently open handle for the running process's combined stdout/stderr
        self.log_path: Optional[Path] = None

    def _build_args(self, use_mjpeg: bool) -> list[str]:
        vw, vh = self.config["video_size"]
        fps = self.config["fps"]
        args = [_ffmpeg(self.config), "-y", "-f", "dshow", "-rtbufsize", "512M"]
        if use_mjpeg:
            args += ["-vcodec", "mjpeg"]
        args += [
            "-video_size", f"{vw}x{vh}",
            "-framerate", str(fps),
            "-i", _input_arg(self.config),
            "-t", str(self.duration),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-g", str(fps),
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "1",
            "-movflags", "+faststart",
            _posix(self.tmp_path),
        ]
        return args

    def _spawn(self, use_mjpeg: bool) -> Optional[subprocess.Popen]:
        # ffmpeg writes continuous progress output to stderr. If nothing
        # drains an unread PIPE, the OS pipe buffer fills within seconds and
        # ffmpeg blocks on write() -- the recording silently hangs until
        # something kills it. Route output to a log file instead.
        args = self._build_args(use_mjpeg)
        suffix = "" if not self.used_fallback else "_fallback"
        log_path = cfg.videos_dir(self.config) / f"tmp_{self.rec_id}{suffix}.ffmpeg.log"
        try:
            log_file = open(log_path, "wb")
        except OSError as exc:
            self.start_error = f"Kon ffmpeg-logbestand niet openen: {exc}"
            logger.error(self.start_error)
            return None
        try:
            proc = subprocess.Popen(
                args, stdin=subprocess.DEVNULL, stdout=log_file, stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW,
            )
        except FileNotFoundError:
            log_file.close()
            self.start_error = f"ffmpeg niet gevonden op '{_ffmpeg(self.config)}'"
            logger.error(self.start_error)
            return None
        self._log_file = log_file
        self.log_path = log_path
        return proc

    def start(self) -> None:
        cfg.videos_dir(self.config).mkdir(parents=True, exist_ok=True)
        use_mjpeg = bool(self.config.get("force_mjpeg", True))
        proc = self._spawn(use_mjpeg)
        with self._lock:
            self._process = proc
        if proc is None:
            return
        if use_mjpeg:
            threading.Thread(target=self._monitor_early_failure, daemon=True).start()

    def _monitor_early_failure(self) -> None:
        """If the mjpeg attempt dies almost immediately (bad flag / device
        rejects mjpeg), retry once without -vcodec mjpeg."""
        proc = self.process
        if proc is None:
            return
        deadline = time.time() + 1.5
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            time.sleep(0.1)
        if proc.poll() is not None and proc.returncode != 0:
            logger.warning("ffmpeg (mjpeg) stopte direct met code %s, val terug zonder mjpeg", proc.returncode)
            if self._log_file:
                try:
                    self._log_file.close()
                except OSError:
                    pass
            self.used_fallback = True
            fallback = self._spawn(use_mjpeg=False)
            with self._lock:
                self._process = fallback

    @property
    def process(self) -> Optional[subprocess.Popen]:
        with self._lock:
            return self._process


class PostProcessJob:
    def __init__(self):
        self.done = threading.Event()
        self.success = False
        self.error: Optional[str] = None
        self.entry: Optional[dict[str, Any]] = None


def start_postprocess(config: dict[str, Any], rec_id: str, parent_id: Optional[str],
                       session: RecordingSession) -> PostProcessJob:
    job = PostProcessJob()
    t = threading.Thread(target=_postprocess_worker, args=(config, rec_id, parent_id, session, job), daemon=True)
    t.start()
    return job


def _run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)


def _tail_log(log_path: Optional[Path], max_chars: int = 800) -> str:
    if log_path is None or not log_path.exists():
        return "(geen ffmpeg-log beschikbaar)"
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "(kon ffmpeg-log niet lezen)"
    return text[-max_chars:]


def _postprocess_worker(config: dict[str, Any], rec_id: str, parent_id: Optional[str],
                         session: RecordingSession, job: PostProcessJob) -> None:
    try:
        proc = session.process
        if proc is None:
            job.error = session.start_error or "opname kon niet gestart worden"
            return

        try:
            proc.wait(timeout=session.duration + 20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
            job.error = "ffmpeg reageerde niet (timeout), proces is beeindigd"
            return
        finally:
            if session._log_file:
                try:
                    session._log_file.close()
                except OSError:
                    pass

        if proc.returncode != 0:
            log_text = _tail_log(session.log_path)
            job.error = f"ffmpeg opname mislukt (code {proc.returncode}): {log_text}"
            logger.error(job.error)
            return

        videos = cfg.videos_dir(config)
        final_mp4 = videos / f"{rec_id}.mp4"
        final_wav = videos / f"{rec_id}.wav"
        countdown_seconds = float(config["countdown_seconds"])
        record_seconds = float(config["record_seconds"])

        trim_cmd = [
            _ffmpeg(config), "-y", "-ss", str(countdown_seconds), "-i", _posix(session.tmp_path),
            "-c", "copy", "-movflags", "+faststart", _posix(final_mp4),
        ]
        result = _run(trim_cmd, timeout=30)
        if result.returncode != 0 or not final_mp4.exists():
            job.error = f"trim mislukt: {result.stderr.decode(errors='replace')[-500:]}"
            logger.error(job.error)
            return

        try:
            session.tmp_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Kon tijdelijk bestand niet verwijderen: %s", exc)

        if session.log_path:
            try:
                session.log_path.unlink(missing_ok=True)
            except OSError:
                pass

        wav_cmd = [
            _ffmpeg(config), "-y", "-i", _posix(final_mp4),
            "-vn", "-acodec", "pcm_s16le", "-ar", "48000", "-ac", "1", _posix(final_wav),
        ]
        result = _run(wav_cmd, timeout=30)
        if result.returncode != 0:
            logger.warning("Kon audio-sidecar niet maken: %s", result.stderr.decode(errors="replace")[-500:])
            # not fatal: video still plays, just without a wav sidecar

        entry = {
            "id": rec_id,
            "file": final_mp4.name,
            "parent_id": parent_id,
            "duration": record_seconds,
            "hidden": False,
            "seed": False,
        }
        library.append_entry(config, entry)
        job.entry = entry
        job.success = True
    except Exception as exc:  # noqa: BLE001 - never let the worker thread crash silently
        logger.exception("Onverwachte fout bij nabewerking van opname %s", rec_id)
        job.error = str(exc)
    finally:
        job.done.set()
