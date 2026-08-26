"""The table surface, the two warps, and per-state rendering.

Everything is drawn onto a single pygame.Surface (the "table surface") at
config['table_surface_size']. Camera frames are warped into that space with
H_cam before overlays are drawn; the whole surface is warped into projector
output space with H_proj as the very last step of every frame. Render
functions here are pure: given a surface and some data, they draw. All
state/timing logic lives in main.py.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import cv2
import numpy as np
import pygame

logger = logging.getLogger(__name__)

_font_cache: dict[tuple[str, int], "pygame.font.Font"] = {}


def get_font(size: int, bold: bool = False) -> "pygame.font.Font":
    key = ("bold" if bold else "regular", size)
    if key not in _font_cache:
        f = pygame.font.SysFont("arial", size, bold=bold)
        _font_cache[key] = f
    return _font_cache[key]


def make_table_surface(config: dict[str, Any]) -> "pygame.Surface":
    w, h = config["table_surface_size"]
    return pygame.Surface((w, h))


# --------------------------------------------------------------------------
# Homographies
# --------------------------------------------------------------------------

def _scale_h(src_size: tuple[int, int], dst_size: tuple[int, int]) -> np.ndarray:
    sx = dst_size[0] / src_size[0]
    sy = dst_size[1] / src_size[1]
    return np.array([[sx, 0, 0], [0, sy, 0], [0, 0, 1]], dtype=np.float64)


def get_h_cam(config: dict[str, Any]) -> np.ndarray:
    """Camera frame -> table surface. Falls back to a plain rescale if not
    yet calibrated."""
    h = config.get("H_cam")
    if h is None:
        vw, vh = config["video_size"]
        tw, th = config["table_surface_size"]
        return _scale_h((vw, vh), (tw, th))
    return np.array(h, dtype=np.float64)


def get_h_proj(config: dict[str, Any], out_size: tuple[int, int]) -> np.ndarray:
    """Table surface -> projector output. Falls back to a plain rescale if
    not yet calibrated."""
    h = config.get("H_proj")
    if h is None:
        tw, th = config["table_surface_size"]
        return _scale_h((tw, th), out_size)
    return np.array(h, dtype=np.float64)


# --------------------------------------------------------------------------
# Warps / pygame <-> numpy conversions
# --------------------------------------------------------------------------

def surface_to_bgr(surface: "pygame.Surface") -> np.ndarray:
    """pygame surface (RGB, W x H x 3 array layout) -> OpenCV BGR (H x W x 3)."""
    rgb = pygame.surfarray.array3d(surface)  # shape (W, H, 3)
    rgb = np.transpose(rgb, (1, 0, 2))  # -> (H, W, 3)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def bgr_to_surface(bgr: np.ndarray) -> "pygame.Surface":
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    rgb = np.transpose(rgb, (1, 0, 2))  # -> (W, H, 3)
    return pygame.surfarray.make_surface(rgb)


def warp_camera_frame_to_table(frame_bgr: np.ndarray, h_cam: np.ndarray, table_size: tuple[int, int]) -> "pygame.Surface":
    warped = cv2.warpPerspective(frame_bgr, h_cam, table_size)
    return bgr_to_surface(warped)


def warp_table_to_output(table_surface: "pygame.Surface", h_proj: np.ndarray, out_size: tuple[int, int]) -> "pygame.Surface":
    bgr = surface_to_bgr(table_surface)
    warped = cv2.warpPerspective(bgr, h_proj, out_size)
    return bgr_to_surface(warped)


# --------------------------------------------------------------------------
# Per-state rendering (pure: draw onto table_surface, no timing decisions)
# --------------------------------------------------------------------------

def clear(table_surface: "pygame.Surface", color=(10, 10, 10)) -> None:
    table_surface.fill(color)


def draw_video_frame(table_surface: "pygame.Surface", frame_surface: Optional["pygame.Surface"]) -> None:
    if frame_surface is not None:
        table_surface.blit(frame_surface, (0, 0))


def draw_bottom_bar(table_surface: "pygame.Surface", text: str, config: dict[str, Any]) -> None:
    w, h = table_surface.get_size()
    bar_h = max(60, h // 8)
    overlay = pygame.Surface((w, bar_h), pygame.SRCALPHA)
    alpha = int(config.get("idle_overlay_alpha", 160))
    overlay.fill((0, 0, 0, alpha))
    table_surface.blit(overlay, (0, h - bar_h))

    font = get_font(int(bar_h * 0.4))
    label = font.render(text, True, (255, 255, 255))
    rect = label.get_rect(center=(w // 2, h - bar_h // 2))
    table_surface.blit(label, rect)


def draw_centered_text(table_surface: "pygame.Surface", text: str, size: int, color=(20, 20, 20)) -> None:
    w, h = table_surface.get_size()
    font = get_font(size, bold=True)
    label = font.render(text, True, color)
    rect = label.get_rect(center=(w // 2, h // 2))
    table_surface.blit(label, rect)


def render_idle(table_surface: "pygame.Surface", config: dict[str, Any], frame_surface: Optional["pygame.Surface"],
                 prompt_text: Optional[str] = None) -> None:
    clear(table_surface)
    if frame_surface is not None:
        draw_video_frame(table_surface, frame_surface)
    else:
        draw_centered_text(table_surface, config["text_error_no_video"], 36, (200, 200, 200))
    draw_bottom_bar(table_surface, prompt_text or config["text_idle_prompt"], config)


def render_countdown(table_surface: "pygame.Surface", config: dict[str, Any], seconds_left: int) -> None:
    table_surface.fill(tuple(config["countdown_light_color"]))
    draw_centered_text(table_surface, str(max(1, seconds_left)), 220, (20, 20, 20))


def render_recording(table_surface: "pygame.Surface", config: dict[str, Any], fraction_elapsed: float, seconds_left: int) -> None:
    table_surface.fill(tuple(config["recording_light_color"]))

    w, h = table_surface.get_size()
    border = int(config.get("recording_border_width", 24))
    border_color = tuple(config.get("recording_border_color", [200, 0, 0]))
    pygame.draw.rect(table_surface, border_color, (0, 0, w, h), border)

    # shrinking progress bar along the bottom, inside the border
    bar_h = max(20, h // 20)
    margin = border + 20
    full_w = w - 2 * margin
    remaining_w = int(full_w * max(0.0, 1.0 - fraction_elapsed))
    pygame.draw.rect(table_surface, (60, 60, 60), (margin, h - margin - bar_h, full_w, bar_h))
    pygame.draw.rect(table_surface, border_color, (margin, h - margin - bar_h, remaining_w, bar_h))

    font = get_font(48)
    label = font.render(f"{seconds_left}s", True, (20, 20, 20))
    table_surface.blit(label, (margin, margin))

    draw_centered_text(table_surface, config["text_recording"], 40, (20, 20, 20))


def render_review_waiting(table_surface: "pygame.Surface", config: dict[str, Any]) -> None:
    table_surface.fill((30, 30, 30))
    draw_centered_text(table_surface, config["text_review_waiting"], 44, (230, 230, 230))


def render_review_playing(table_surface: "pygame.Surface", config: dict[str, Any], frame_surface: Optional["pygame.Surface"]) -> None:
    clear(table_surface)
    draw_video_frame(table_surface, frame_surface)
    draw_bottom_bar(table_surface, config["text_review"], config)


def render_error(table_surface: "pygame.Surface", config: dict[str, Any], message: str) -> None:
    table_surface.fill((40, 10, 10))
    draw_centered_text(table_surface, message, 34, (240, 200, 200))
