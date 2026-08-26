"""Two calibration modes, both modal / blocking (they pause the visitor
flow while an operator uses them):

- calibrate_projector: drag the 4 corners of a grid, projected live onto
  the table, until the projected rectangle matches the physical table
  edge. Saves H_proj.
- calibrate_camera: grab one still frame, click the 4 corners of the table
  in that frame. Saves H_cam.

Status (corner coordinates) is printed to the console, which stays visible
on the laptop screen while the projector output is fullscreen on the other
display.
"""
from __future__ import annotations

import logging
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pygame

import config as cfg
import projection
from capture import CREATE_NO_WINDOW, _ffmpeg, _input_arg, _posix  # noqa: F401 (internal reuse)

logger = logging.getLogger(__name__)

CORNER_NAMES = ["linksboven", "rechtsboven", "rechtsonder", "linksonder"]


def _table_corners(config: dict[str, Any]) -> np.ndarray:
    w, h = config["table_surface_size"]
    return np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float32)


def calibrate_projector(config: dict[str, Any], screen: "pygame.Surface") -> None:
    out_size = screen.get_size()
    src = _table_corners(config)

    h0 = projection.get_h_proj(config, out_size)
    dst = cv2.perspectiveTransform(src.reshape(-1, 1, 2), h0).reshape(-1, 2).astype(np.float64)

    selected = 0
    table_surface = projection.make_table_surface(config)
    clock = pygame.time.Clock()

    print("\n=== Projectorkalibratie ===")
    print("1-4: selecteer hoek | pijltjes: verplaats 1px | shift+pijltjes: 10px | S: opslaan | ESC: sluiten\n")
    _print_status(dst, selected)

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key in (pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4):
                    selected = event.key - pygame.K_1
                    _print_status(dst, selected)
                elif event.key == pygame.K_s:
                    h_final = cv2.getPerspectiveTransform(src, dst.astype(np.float32))
                    config["H_proj"] = h_final.tolist()
                    cfg.save_config(config)
                    print(f"Opgeslagen: H_proj -> {h_final.tolist()}")
                elif event.key in (pygame.K_UP, pygame.K_DOWN, pygame.K_LEFT, pygame.K_RIGHT):
                    mods = pygame.key.get_mods()
                    step = config["calibration_step_px_shift"] if (mods & pygame.KMOD_SHIFT) else config["calibration_step_px"]
                    dx, dy = 0, 0
                    if event.key == pygame.K_UP:
                        dy = -step
                    elif event.key == pygame.K_DOWN:
                        dy = step
                    elif event.key == pygame.K_LEFT:
                        dx = -step
                    elif event.key == pygame.K_RIGHT:
                        dx = step
                    dst[selected][0] += dx
                    dst[selected][1] += dy
                    _print_status(dst, selected)

        h_current = cv2.getPerspectiveTransform(src, dst.astype(np.float32))
        _draw_calibration_grid(table_surface, config, selected)
        out = projection.warp_table_to_output(table_surface, h_current, out_size)
        screen.blit(out, (0, 0))
        pygame.display.flip()
        clock.tick(30)

    print("=== Projectorkalibratie gesloten ===\n")


def _print_status(dst: np.ndarray, selected: int) -> None:
    parts = []
    for i, name in enumerate(CORNER_NAMES):
        marker = ">" if i == selected else " "
        parts.append(f"{marker}{i + 1}:{name}=({dst[i][0]:.0f},{dst[i][1]:.0f})")
    print(" | ".join(parts))


def _draw_calibration_grid(table_surface: "pygame.Surface", config: dict[str, Any], selected: int) -> None:
    table_surface.fill((0, 0, 0))
    w, h = table_surface.get_size()
    step = 100
    for x in range(0, w + 1, step):
        pygame.draw.line(table_surface, (0, 150, 255), (x, 0), (x, h), 1)
    for y in range(0, h + 1, step):
        pygame.draw.line(table_surface, (0, 150, 255), (0, y), (w, y), 1)
    pygame.draw.rect(table_surface, (255, 255, 0), (0, 0, w, h), 4)

    corners = [(0, 0), (w, 0), (w, h), (0, h)]
    font = projection.get_font(28, bold=True)
    for i, (cx, cy) in enumerate(corners):
        color = (255, 60, 60) if i == selected else (0, 255, 0)
        pygame.draw.circle(table_surface, color, (cx, cy), 16)
        label = font.render(str(i + 1), True, (255, 255, 255))
        table_surface.blit(label, (cx - 8, cy - 14))


def calibrate_camera(config: dict[str, Any]) -> None:
    print("\n=== Camerakalibratie ===")
    video_device = config.get("video_device", "")
    if not video_device:
        print("Geen 'video_device' geconfigureerd in config.json. Annuleren.")
        return

    with tempfile.TemporaryDirectory() as tmp:
        still_path = Path(tmp) / "still.jpg"
        if not _grab_still(config, still_path):
            print("Kon geen beeld van de camera vastleggen. Annuleren.")
            return

        frame = cv2.imread(str(still_path))
        if frame is None:
            print(f"Kon {still_path} niet lezen. Annuleren.")
            return

    points: list[tuple[int, int]] = []
    window = "Camerakalibratie - klik 4 hoeken (LB, RB, RO, LO), ESC om te annuleren"

    def on_mouse(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN and len(points) < 4:
            points.append((x, y))
            print(f"Hoek {len(points)} ({CORNER_NAMES[len(points) - 1]}): ({x}, {y})")

    cv2.namedWindow(window)
    cv2.setMouseCallback(window, on_mouse)

    print("Klik de 4 hoeken van het tafelblad in deze volgorde: linksboven, rechtsboven, rechtsonder, linksonder.")

    cancelled = False
    while len(points) < 4:
        display = frame.copy()
        for i, p in enumerate(points):
            cv2.circle(display, p, 8, (0, 255, 0), -1)
            cv2.putText(display, str(i + 1), (p[0] + 10, p[1]), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        cv2.imshow(window, display)
        key = cv2.waitKey(30) & 0xFF
        if key == 27:  # ESC
            cancelled = True
            break
        if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
            cancelled = True
            break

    cv2.destroyAllWindows()

    if cancelled or len(points) < 4:
        print("Camerakalibratie geannuleerd.\n")
        return

    src = np.array(points, dtype=np.float32)
    dst = _table_corners(config)
    h_cam = cv2.getPerspectiveTransform(src, dst)
    config["H_cam"] = h_cam.tolist()
    cfg.save_config(config)
    print(f"Opgeslagen: H_cam -> {h_cam.tolist()}")
    print("=== Camerakalibratie gesloten ===\n")


def _grab_still(config: dict[str, Any], out_path: Path, use_mjpeg: bool = True) -> bool:
    vw, vh = config["video_size"]
    args = [_ffmpeg(config), "-y", "-f", "dshow", "-rtbufsize", "512M"]
    if use_mjpeg:
        args += ["-vcodec", "mjpeg"]
    args += [
        "-video_size", f"{vw}x{vh}", "-i", _input_arg(config),
        "-frames:v", "1", "-q:v", "2", _posix(out_path),
    ]
    try:
        result = subprocess.run(args, capture_output=True, timeout=15, creationflags=CREATE_NO_WINDOW)
    except FileNotFoundError:
        print(f"ffmpeg niet gevonden op '{_ffmpeg(config)}'.")
        return False
    except subprocess.TimeoutExpired:
        print("ffmpeg reageerde niet op tijd.")
        return False

    if result.returncode != 0 or not out_path.exists():
        if use_mjpeg:
            logger.warning("Snapshot met mjpeg mislukt, probeer zonder")
            return _grab_still(config, out_path, use_mjpeg=False)
        print(result.stderr.decode(errors="replace")[-800:])
        return False
    return True
