import json

import httpx
import pytest
from PIL import Image

from conftest import write_show
from unbox_show import images, pipeline
from unbox_show.eleven import ElevenClient, FakeElevenClient
from unbox_show.frames import FrameState, Renderer
from unbox_show.model import load_show


class _PretendRealClient(FakeElevenClient):
    name = "elevenlabs"


@pytest.fixture
def art_show(tmp_path, mini_data):
    mini_data["artwork"] = {
        "style": "Flat poster style.",
        "images": {"title.png": {"prompt": "A rocket"}, "pen.png": {"prompt": "A pen"}},
    }
    return tmp_path, mini_data


def _run(folder, data, client=None, **kwargs):
    show = load_show(write_show(folder, data))
    client = client or _PretendRealClient()
    stats = pipeline.run_images(show, client, pipeline.Paths(show.base_dir), lambda m: True, **kwargs)
    return show, client, stats


def test_generates_only_missing_files_and_keeps_supplied_ones(art_show):
    folder, data = art_show
    screens = folder / "assets" / "screens"
    screens.mkdir(parents=True)
    Image.new("RGB", (16, 9), (1, 2, 3)).save(screens / "pen.png")  # supplied by the team

    _, client, stats = _run(folder, data)
    assert stats.generated == ["title.png"] and stats.kept == ["pen.png"]
    assert client.calls == [("image", "A rocket. Flat poster style.")]
    assert Image.open(screens / "pen.png").getpixel((0, 0)) == (1, 2, 3)

    _, client, stats = _run(folder, data)
    assert client.calls == [] and stats.generated == []


def test_changed_prompt_regenerates_only_untouched_generated_files(art_show):
    folder, data = art_show
    _run(folder, data)
    screens = folder / "assets" / "screens"
    data["artwork"]["images"]["title.png"]["prompt"] = "A bigger rocket"
    data["artwork"]["images"]["pen.png"]["prompt"] = "A golden pen"
    Image.new("RGB", (16, 9)).save(screens / "pen.png")  # someone replaced the generated pen by hand
    _, client, stats = _run(folder, data)
    assert stats.generated == ["title.png"]
    assert json.loads((screens / "generated.json").read_text())["title.png"]["take"] == 0


def test_reroll_makes_a_new_take(art_show):
    folder, data = art_show
    _run(folder, data)
    _, client, stats = _run(folder, data, reroll={"pen.png"})
    assert stats.generated == ["pen.png"] and len(client.calls) == 1


def test_fake_client_never_writes_artwork(art_show):
    folder, data = art_show
    with pytest.raises(pipeline.Aborted):
        _run(folder, data, client=FakeElevenClient())
    assert not (folder / "assets" / "screens").exists()


def test_real_client_starts_polls_and_downloads():
    png = FakeElevenClient().image("x", "m", "16:9", "1K")
    statuses = iter(["generating", "completed"])
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, request.headers.get("xi-api-key")))
        if request.method == "POST":
            assert json.loads(request.content) == {"model_id": "m", "prompt": "p", "aspect_ratio": "16:9", "resolution": "2K"}
            return httpx.Response(200, json={"id": "g1", "status": "pending"})
        if request.url.path == "/v1/flows/image/g1":
            status = next(statuses)
            body = {"id": "g1", "status": status}
            if status == "completed":
                body["output"] = {"content_url": "https://cdn.example/g1.png", "content_mime_type": "image/png"}
            return httpx.Response(200, json=body)
        return httpx.Response(200, content=png)

    client = ElevenClient("k", transport=httpx.MockTransport(handler), sleep=lambda s: None)
    assert client.image("p", "m", "16:9", "2K") == png
    assert seen[-1] == ("GET", "/g1.png", None)  # the signed URL gets no API key


def test_screen_text_is_drawn_over_real_artwork(tmp_path):
    from conftest import SHOW_YAML
    import shutil

    shutil.copy(SHOW_YAML, tmp_path / "show.yaml")
    screens = tmp_path / "assets" / "screens"
    screens.mkdir(parents=True)
    Image.new("RGB", (1920, 1080), (200, 120, 40)).save(screens / "bridge.png")
    Image.new("RGB", (512, 512), (0, 160, 0)).save(screens / "cloud_rocket.png")
    renderer = Renderer(load_show(tmp_path / "show.yaml"), (960, 540))
    frame = renderer.render(FrameState(screen="introduce"))
    assert frame.getpixel((480, 170)) != (200, 120, 40)  # text panel over the art
    assert frame.getpixel((20, 500)) == (200, 120, 40)    # the art itself elsewhere
    cloud = renderer.render(FrameState(screen="pen", overlays=("cloud_rocket",)))
    assert cloud.getpixel((192, 302)) == (0, 160, 0)      # icon inside the first cloud
