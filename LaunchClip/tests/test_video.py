"""End-to-end: `unbox build --fake` on a clean copy of the project. Takes a few minutes."""

import pytest
from PIL import Image

from conftest import assert_golden
from unbox_show import pipeline
from unbox_show.audio import probe_duration, run_ffmpeg
from unbox_show.eleven import FakeElevenClient
from unbox_show.model import load_show

pytestmark = pytest.mark.slow

CHECKPOINTS = {"00:40": 40, "01:50": 110, "08:05": 485, "12:45": 765}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    import shutil

    from conftest import SHOW_YAML

    folder = tmp_path_factory.mktemp("build")
    shutil.copy(SHOW_YAML, folder / "show.yaml")
    show = load_show(folder / "show.yaml")
    paths = pipeline.Paths(show.base_dir)
    report = pipeline.build_show(show, FakeElevenClient(), paths, draft=False, confirm=lambda m: False, echo=lambda m: None)
    return paths, report


def test_full_build_is_fifteen_minutes(built):
    paths, report = built
    video = paths.dist / "unbox_show.mp4"
    assert video in report.outputs
    assert abs(probe_duration(video) - 900.0) <= 1 / 25
    assert (paths.dist / "unbox_show.en.srt").exists() and (paths.dist / "unbox_show.nl.srt").exists()


@pytest.mark.parametrize("label", CHECKPOINTS)
def test_frames_match_golden(built, label):
    paths, _ = built
    out = paths.build / f"check_{CHECKPOINTS[label]}.png"
    run_ffmpeg(["-ss", str(CHECKPOINTS[label]), "-i", str(paths.dist / "unbox_show.mp4"), "-frames:v", "1", str(out)])
    # Compared at quarter size: tolerant of encoder noise, strict about layout and content.
    image = Image.open(out).convert("RGB").resize((480, 270), Image.BILINEAR)
    assert_golden(image, f"video_{label.replace(':', '')}", tolerance=2.0)


def test_second_build_reuses_everything(built):
    paths, _ = built
    show = load_show(paths.project / "show.yaml")
    client = FakeElevenClient()
    report = pipeline.build_show(show, client, paths, draft=True, confirm=lambda m: False, echo=lambda m: None)
    assert client.calls == []
    assert report.speech.generated == [] and report.sounds.generated == []
