"""Video/audio playback: wallclock-paced frame decoding + a wav sidecar
played through pygame.mixer, warped into table-surface space every frame."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import pygame

import projection

logger = logging.getLogger(__name__)


class VideoPlayer:
    """Plays one video (+ optional wav sidecar) on wallclock time.

    Call start() once, then get_table_frame() every render frame. Returns
    None when a non-looping clip has finished; loops forever otherwise.
    """

    def __init__(self, mp4_path: Path, wav_path: Optional[Path], h_cam: np.ndarray,
                 table_size: tuple[int, int], loop: bool, fallback_fps: float = 30.0):
        self.mp4_path = mp4_path
        self.wav_path = wav_path if (wav_path and wav_path.exists()) else None
        self.h_cam = h_cam
        self.table_size = table_size
        self.loop = loop

        self.valid = False
        self._cap: Optional[cv2.VideoCapture] = None
        self._fps = fallback_fps
        self._total_frames = 0
        self._duration = 0.0
        self._current_index = -1
        self._last_frame_bgr: Optional[np.ndarray] = None
        self._start_time: Optional[float] = None
        self._finished = False

        if not mp4_path.exists():
            logger.error("Video niet gevonden: %s", mp4_path)
            return

        cap = cv2.VideoCapture(str(mp4_path))
        if not cap.isOpened():
            logger.error("Kon video niet openen: %s", mp4_path)
            return

        self._cap = cap
        fps = cap.get(cv2.CAP_PROP_FPS)
        self._fps = fps if fps and fps > 1 else fallback_fps
        self._total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if self._total_frames > 0:
            self._duration = self._total_frames / self._fps
        self.valid = True

    def start(self) -> None:
        self._start_time = time.time()
        self._finished = False
        if self.wav_path is not None:
            try:
                pygame.mixer.music.load(str(self.wav_path))
                pygame.mixer.music.play(loops=-1 if self.loop else 0)
            except pygame.error as exc:
                logger.warning("Kon audio niet afspelen (%s): %s", self.wav_path, exc)

    def stop(self) -> None:
        if self.wav_path is not None:
            try:
                pygame.mixer.music.stop()
            except pygame.error:
                pass
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    @property
    def finished(self) -> bool:
        return self._finished

    def _advance_to(self, target_index: int) -> None:
        """Read (and drop) frames until _current_index == target_index."""
        assert self._cap is not None
        if target_index < self._current_index:
            # loop wrap: rewind
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            self._current_index = -1
        while self._current_index < target_index:
            ok, frame = self._cap.read()
            if not ok:
                break
            self._current_index += 1
            self._last_frame_bgr = frame

    def get_table_frame(self) -> Optional["pygame.Surface"]:
        if not self.valid or self._cap is None or self._start_time is None:
            return None

        elapsed = time.time() - self._start_time

        if self.loop and self._duration > 0:
            elapsed = elapsed % self._duration
        elif not self.loop and self._total_frames > 0 and elapsed >= self._duration:
            self._finished = True
            return None

        target_index = int(elapsed * self._fps)
        self._advance_to(target_index)

        if self._last_frame_bgr is None:
            return None
        return self._surface_from_last()

    def _surface_from_last(self) -> "pygame.Surface":
        return projection.warp_camera_frame_to_table(self._last_frame_bgr, self.h_cam, self.table_size)
