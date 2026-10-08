"""Load, save and default configuration for the installation.

Everything that could plausibly need tweaking on-site (paths, timings,
colors, texts, device names, homographies) lives in config.json. If the
file is missing, a documented default is written out so the app can still
start.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

# 3x3 identity, used as a "not yet calibrated" placeholder. Calibration
# (calibrate.py) overwrites this with a real homography and persists it.
_IDENTITY_H = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]

DEFAULT_CONFIG: dict[str, Any] = {
    # --- display / table surface -------------------------------------------------
    # The "table surface" is the internal canvas everything is drawn on,
    # matching the aspect ratio of the physical table top.
    "table_surface_size": [1200, 800],
    # Which physical display the projector output goes to (0-based, as
    # reported by pygame.display.get_desktop_sizes()). If only one display
    # is present, the app falls back to a window of window_fallback_size.
    "display_index": 1,
    "window_fallback_size": [1280, 800],

    # --- state timings --------------------------------------------------------
    "countdown_seconds": 3,
    "record_seconds": 30,
    "review_enabled": True,
    # Seconds after a state change during which TRIGGER is ignored, to
    # debounce a physical button.
    "lockout_seconds": 1.0,

    # --- visuals ---------------------------------------------------------------
    "recording_light_color": [245, 245, 240],
    "countdown_light_color": [255, 255, 255],
    "idle_overlay_alpha": 160,
    "recording_border_color": [200, 0, 0],
    "recording_border_width": 24,

    # --- Dutch visitor-facing texts --------------------------------------------
    "text_idle_prompt": "Druk op de knop om jouw verhaal te vertellen",
    "text_recording": "Je verhaal wordt opgenomen",
    "text_review": "Dit is jouw verhaal. Dank je wel.",
    "text_review_waiting": "Een moment...",
    "text_error_no_video": "Geen video beschikbaar",
    "text_error_generic": "Er ging iets mis. Probeer het opnieuw.",

    # --- capture (Windows / dshow) ----------------------------------------------
    "ffmpeg_path": "ffmpeg",
    "video_device": "",
    "audio_device": "",
    "video_size": [1280, 720],
    "fps": 30,
    "force_mjpeg": True,

    # --- library / selection -----------------------------------------------------
    "media_dir": "media",
    "videos_subdir": "videos",
    "seeds_subdir": "seeds",
    "index_file": "index.jsonl",
    # latest | random_recent | random_any | random_seed
    "selection_strategy": "latest",
    "selection_n": 5,

    # --- calibration -------------------------------------------------------------
    "H_cam": None,   # camera frame -> table surface, set by calibrate.py (V)
    "H_proj": None,  # table surface -> projector output, set by calibrate.py (C)
    "calibration_step_px": 1,
    "calibration_step_px_shift": 10,
}


def _deep_merge_defaults(loaded: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    """Fill in any keys missing from `loaded` with values from `defaults`."""
    merged = dict(defaults)
    merged.update(loaded)
    return merged


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        logger.info("Geen config.json gevonden, standaardconfiguratie wordt aangemaakt op %s", CONFIG_PATH)
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Kon config.json niet lezen (%s), standaardconfiguratie wordt gebruikt", exc)
        return dict(DEFAULT_CONFIG)

    return _deep_merge_defaults(loaded, DEFAULT_CONFIG)


def save_config(config: dict[str, Any]) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        logger.error("Kon config.json niet opslaan: %s", exc)


def videos_dir(config: dict[str, Any]) -> Path:
    return Path(config["media_dir"]) / config["videos_subdir"]


def seeds_dir(config: dict[str, Any]) -> Path:
    return Path(config["media_dir"]) / config["seeds_subdir"]


def index_path(config: dict[str, Any]) -> Path:
    return Path(config["media_dir"]) / config["index_file"]


def identity_h() -> list:
    return [row[:] for row in _IDENTITY_H]
