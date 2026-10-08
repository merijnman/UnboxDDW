"""Statemachine + main loop for the Dutch Design Week installation.

State transitions live entirely here; projection.py only draws what it's
told to, given data. See README.md for setup and calibration order.
"""
from __future__ import annotations

# SetProcessDpiAwareness MUST be called before pygame.init() touches the
# display subsystem, and before anything else that could trigger it.
import ctypes

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except (AttributeError, OSError):
    pass  # older Windows without shcore, or not running on Windows

import argparse
import logging
import subprocess
import sys
import time
from enum import Enum, auto
from pathlib import Path
from typing import Optional

import pygame

import calibrate
import capture
import config as cfg
import library
import playback
import projection

logger = logging.getLogger(__name__)


class State(Enum):
    IDLE = auto()
    COUNTDOWN = auto()
    RECORDING = auto()
    REVIEW = auto()


def setup_logging() -> None:
    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "app.log", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def setup_display(config: dict) -> tuple["pygame.Surface", tuple[int, int], bool]:
    sizes = pygame.display.get_desktop_sizes()
    if len(sizes) >= 2:
        idx = config.get("display_index", 1)
        if idx >= len(sizes):
            idx = 1
        size = sizes[idx]
        screen = pygame.display.set_mode(size, pygame.FULLSCREEN, display=idx)
        pygame.mouse.set_visible(False)
        return screen, size, True

    size = tuple(config["window_fallback_size"])
    screen = pygame.display.set_mode(size, display=0)
    return screen, size, False


ACTIONS = {
    pygame.K_SPACE: "TRIGGER",
    pygame.K_c: "CALIBRATE_PROJECTOR",
    pygame.K_v: "CALIBRATE_CAMERA",
    pygame.K_h: "HIDE_LAST",
    pygame.K_ESCAPE: "QUIT",
}


class App:
    def __init__(self, config: dict):
        self.config = config
        self.screen: Optional["pygame.Surface"] = None
        self.out_size = (1280, 800)
        self.table_surface = projection.make_table_surface(config)

        self.state = State.IDLE
        self.state_entered_at = time.time()
        self.last_state_change_at = 0.0  # allows an immediate TRIGGER at startup

        self.idle_entry = None
        self.idle_player: Optional[playback.VideoPlayer] = None

        self.rec_id: Optional[str] = None
        self.pending_parent_id: Optional[str] = None
        self.session: Optional[capture.RecordingSession] = None
        self.postjob: Optional[capture.PostProcessJob] = None

        self.review_phase = "waiting"
        self.review_waiting_since = 0.0
        self.review_player: Optional[playback.VideoPlayer] = None

        self.transient_message: Optional[str] = None
        self.transient_until = 0.0

    # -- setup -----------------------------------------------------------
    def start(self) -> None:
        self.screen, self.out_size, _fullscreen = setup_display(self.config)
        pygame.display.set_caption("DDW installatie")
        self.load_idle_video()

    # -- library / video loading ------------------------------------------
    def load_idle_video(self) -> None:
        if self.idle_player is not None:
            self.idle_player.stop()
            self.idle_player = None

        entry = library.select_previous(self.config)
        self.idle_entry = entry
        if entry is None:
            logger.warning("Geen video's beschikbaar (geen opnames, geen seeds)")
            return

        h_cam = projection.get_h_cam(self.config)
        table_size = tuple(self.config["table_surface_size"])
        player = playback.VideoPlayer(entry.path, entry.wav_path, h_cam, table_size, loop=True)
        if not player.valid:
            logger.error("Kon vorige video niet laden: %s", entry.path)
            self.idle_entry = None
            return
        player.start()
        self.idle_player = player

    def set_transient_message(self, text: str, seconds: float = 6.0) -> None:
        self.transient_message = text
        self.transient_until = time.time() + seconds

    # -- transitions -------------------------------------------------------
    def transition(self, new_state: State) -> None:
        logger.info("State: %s -> %s", self.state.name, new_state.name)
        self.state = new_state
        now = time.time()
        self.state_entered_at = now
        self.last_state_change_at = now

    def go_idle_with_error(self, message: str) -> None:
        self.set_transient_message(message)
        self.transition(State.IDLE)
        self.load_idle_video()

    # -- input ---------------------------------------------------------------
    def handle_action(self, action: str) -> None:
        if action == "TRIGGER":
            self.handle_trigger()
        elif action == "CALIBRATE_PROJECTOR":
            calibrate.calibrate_projector(self.config, self.screen)
        elif action == "CALIBRATE_CAMERA":
            calibrate.calibrate_camera(self.config)
        elif action == "HIDE_LAST":
            self.handle_hide_last()
        elif action == "QUIT":
            raise SystemExit

    def handle_trigger(self) -> None:
        if self.state != State.IDLE:
            return
        if time.time() - self.last_state_change_at < self.config["lockout_seconds"]:
            return

        self.pending_parent_id = self.idle_entry.id if self.idle_entry else None
        self.rec_id = time.strftime("%Y%m%d-%H%M%S")

        if self.idle_player is not None:
            self.idle_player.stop()
            self.idle_player = None

        session = capture.RecordingSession(self.config, self.rec_id)
        session.start()
        self.session = session

        self.transition(State.COUNTDOWN)

    def handle_hide_last(self) -> None:
        entry = library.latest_recording(self.config)
        if entry is None:
            logger.info("Geen opname om te verbergen")
            return
        if library.set_hidden(self.config, entry.id, True):
            logger.info("Opname %s verborgen", entry.id)
            if self.state == State.IDLE:
                self.load_idle_video()

    # -- per-frame update ----------------------------------------------------
    def update_and_render(self) -> None:
        now = time.time()
        if self.state == State.IDLE:
            self._update_idle(now)
        elif self.state == State.COUNTDOWN:
            self._update_countdown(now)
        elif self.state == State.RECORDING:
            self._update_recording(now)
        elif self.state == State.REVIEW:
            self._update_review(now)

        h_proj = projection.get_h_proj(self.config, self.out_size)
        out_surface = projection.warp_table_to_output(self.table_surface, h_proj, self.out_size)
        self.screen.blit(out_surface, (0, 0))
        pygame.display.flip()

    def _update_idle(self, now: float) -> None:
        frame = self.idle_player.get_table_frame() if self.idle_player else None
        prompt = None
        if self.transient_message and now < self.transient_until:
            prompt = self.transient_message
        projection.render_idle(self.table_surface, self.config, frame, prompt)

    def _update_countdown(self, now: float) -> None:
        elapsed = now - self.state_entered_at
        seconds_left = int(self.config["countdown_seconds"]) - int(elapsed)
        projection.render_countdown(self.table_surface, self.config, seconds_left)
        if elapsed >= self.config["countdown_seconds"]:
            self.transition(State.RECORDING)

    def _update_recording(self, now: float) -> None:
        elapsed = now - self.state_entered_at
        record_seconds = float(self.config["record_seconds"])
        fraction = min(1.0, elapsed / record_seconds) if record_seconds > 0 else 1.0
        seconds_left = max(0, int(record_seconds - elapsed))
        projection.render_recording(self.table_surface, self.config, fraction, seconds_left)

        if elapsed >= record_seconds:
            self.postjob = capture.start_postprocess(self.config, self.rec_id, self.pending_parent_id, self.session)
            self.review_phase = "waiting"
            self.review_waiting_since = now
            self.review_player = None
            self.transition(State.REVIEW)

    def _update_review(self, now: float) -> None:
        if self.review_phase == "waiting":
            projection.render_review_waiting(self.table_surface, self.config)
            if self.postjob is not None and self.postjob.done.is_set():
                self._on_postprocess_done()
            elif now - self.review_waiting_since > 25.0:
                logger.error("Nabewerking timeout voor opname %s", self.rec_id)
                self.go_idle_with_error(self.config["text_error_generic"])
        elif self.review_phase == "playing":
            frame = self.review_player.get_table_frame()
            if frame is None:
                self.review_player.stop()
                self.transition(State.IDLE)
                self.load_idle_video()
            else:
                projection.render_review_playing(self.table_surface, self.config, frame)

    def _on_postprocess_done(self) -> None:
        job = self.postjob
        if not job.success:
            logger.error("Opname %s mislukt: %s", self.rec_id, job.error)
            self.go_idle_with_error(self.config["text_error_generic"])
            return

        if not self.config.get("review_enabled", True):
            self.transition(State.IDLE)
            self.load_idle_video()
            return

        entry = job.entry
        videos = cfg.videos_dir(self.config)
        path = videos / entry["file"]
        wav = path.with_suffix(".wav")
        h_cam = projection.get_h_cam(self.config)
        table_size = tuple(self.config["table_surface_size"])
        player = playback.VideoPlayer(path, wav, h_cam, table_size, loop=False)
        if not player.valid:
            logger.error("Kon zojuist opgenomen video niet openen: %s", path)
            self.go_idle_with_error(self.config["text_error_generic"])
            return
        player.start()
        self.review_player = player
        self.review_phase = "playing"

    # -- cleanup ---------------------------------------------------------------
    def shutdown(self) -> None:
        if self.idle_player:
            self.idle_player.stop()
        if self.review_player:
            self.review_player.stop()


def make_dummy_seed(config: dict) -> None:
    """Generates a short synthetic seed video with ffmpeg (testsrc + tone)
    so the statemachine/rendering can be tried out without a real camera."""
    seeds = cfg.seeds_dir(config)
    seeds.mkdir(parents=True, exist_ok=True)
    vw, vh = config["video_size"]
    mp4_path = seeds / "dummy-0001.mp4"
    wav_path = seeds / "dummy-0001.wav"

    cmd = [
        capture._ffmpeg(config), "-y",
        "-f", "lavfi", "-i", f"testsrc=size={vw}x{vh}:rate={config['fps']}:duration=10",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "1",
        "-movflags", "+faststart", capture._posix(mp4_path),
    ]
    print("Genereer dummy seed video...")
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        print(result.stderr.decode(errors="replace")[-1000:])
        print("Kon dummy seed niet genereren. Is ffmpeg geinstalleerd en in PATH?")
        return

    wav_cmd = [
        capture._ffmpeg(config), "-y", "-i", capture._posix(mp4_path),
        "-vn", "-acodec", "pcm_s16le", "-ar", "48000", "-ac", "1", capture._posix(wav_path),
    ]
    subprocess.run(wav_cmd, capture_output=True)
    print(f"Klaar: {mp4_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="DDW installatie")
    parser.add_argument("--list-devices", action="store_true", help="Toon beschikbare dshow video/audio devices")
    parser.add_argument("--camera-settings", action="store_true", help="Open het dshow camera-instellingendialoog")
    parser.add_argument("--make-dummy-seed", action="store_true", help="Genereer een testvideo in media/seeds/")
    args = parser.parse_args()

    setup_logging()
    config = cfg.load_config()

    if args.list_devices:
        capture.list_devices(config)
        return
    if args.camera_settings:
        capture.open_camera_settings_dialog(config)
        return
    if args.make_dummy_seed:
        make_dummy_seed(config)
        return

    pygame.mixer.pre_init(48000, -16, 1, 512)
    pygame.init()
    pygame.mixer.init()

    app = App(config)
    app.start()

    clock = pygame.time.Clock()
    running = True
    while running:
        try:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    action = ACTIONS.get(event.key)
                    if action:
                        try:
                            app.handle_action(action)
                        except SystemExit:
                            running = False

            if running:
                app.update_and_render()
        except Exception:
            logger.exception("Onverwachte fout in hoofdlus, val terug op IDLE")
            try:
                app.go_idle_with_error(config["text_error_generic"])
            except Exception:
                logger.exception("Kon niet terugvallen op IDLE, applicatie stopt")
                running = False

        clock.tick(30)

    app.shutdown()
    pygame.quit()


if __name__ == "__main__":
    main()
