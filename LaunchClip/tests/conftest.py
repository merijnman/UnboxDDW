from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
import yaml
from PIL import Image, ImageChops

PROJECT = Path(__file__).resolve().parents[1]
SHOW_YAML = PROJECT / "show.yaml"
GOLDEN = Path(__file__).parent / "golden"
SNAPSHOTS = Path(__file__).parent / "snapshots"
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"

MINI_SHOW = {
    "show": {"title": "Mini", "duration": "01:00", "video": {"width": 640, "height": 360, "fps": 25}, "bed": ["hum"]},
    "voices": {
        "commander": {
            "label": "COMMANDER", "voice_id": "voice_a", "model_id": "eleven_multilingual_v2",
            "settings": {"stability": 0.45, "similarity_boost": 0.75, "style": 0.35, "speed": 1.0}, "seed": 1,
            "audition": "Welcome, crew. Yikes.",
        },
        "mission_control": {
            "label": "MISSION CONTROL", "voice_id": "voice_b",
            "settings": {"stability": 0.7, "similarity_boost": 0.75}, "seed": 2,
        },
    },
    "sounds": {"hum": {"prompt": "low hum", "seconds": 2, "loop": True}, "beep": {"prompt": "beep", "seconds": 1}},
    "screens": {"title": {"image": "title.png", "text_en": "TITLE", "text_nl": "TITEL"}, "pen": {"image": "pen.png"}},
    "overlays": {"cloud": {"image": "cloud.png"}},
    "timeline": [
        {"at": "00:00", "screen": "title", "sound": "beep"},
        {"at": "00:02", "id": "hello", "say": "commander", "lines": [
            {"en": "Welcome, crew. This is a test.", "nl": "Welkom, crew. Dit is een test."},
            {"en": "Look at the pen.", "nl": "Kijk naar de pen.", "screen": "pen", "overlay": "cloud"},
        ]},
        {"at": "00:10", "timer": {"seconds": 10}},
        {"at": "00:22", "id": "count", "say": "mission_control", "pace_seconds": 1.0, "lines": [
            {"en": "T minus three.", "nl": "Nog drie."},
            {"en": "Three.", "nl": "Drie.", "big": "3"},
            {"en": "Two.", "nl": "Twee.", "big": "2"},
            {"en": "One.", "nl": "Een.", "big": "1", "ops": "SMOKE"},
        ]},
        {"at": "00:40", "id": "bye", "say": "commander", "lines": [{"en": "Goodbye.", "nl": "Dag."}]},
    ],
}


def write_show(folder: Path, data: dict) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "show.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


@pytest.fixture
def mini_data() -> dict:
    import copy

    return copy.deepcopy(MINI_SHOW)


@pytest.fixture
def mini_show_path(tmp_path, mini_data) -> Path:
    return write_show(tmp_path / "mini", mini_data)


@pytest.fixture
def real_show_copy(tmp_path) -> Path:
    """show.yaml copied into an empty project folder: no artwork, no cache, no .env."""
    folder = tmp_path / "project"
    folder.mkdir()
    shutil.copy(SHOW_YAML, folder / "show.yaml")
    return folder / "show.yaml"


def image_difference(a: Image.Image, b: Image.Image) -> float:
    """Mean absolute difference per channel, 0..255."""
    a, b = a.convert("RGB"), b.convert("RGB")
    if a.size != b.size:
        return 255.0
    hist = ImageChops.difference(a, b).histogram()
    total = sum(i % 256 * count for i, count in enumerate(hist))
    return total / (a.width * a.height * 3)


def assert_golden(image: Image.Image, name: str, tolerance: float = 0.5) -> None:
    path = GOLDEN / f"{name}.png"
    if UPDATE or not path.exists():
        if not UPDATE:
            pytest.fail(f"golden image {path.name} missing; run with UPDATE_GOLDEN=1 and inspect it")
        GOLDEN.mkdir(parents=True, exist_ok=True)
        image.save(path)
        return
    diff = image_difference(image, Image.open(path))
    assert diff <= tolerance, f"{name}: differs from golden by {diff:.2f} (tolerance {tolerance})"
