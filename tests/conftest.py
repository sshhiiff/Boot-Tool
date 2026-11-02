import numpy as np
import pytest
from PIL import Image, ImageDraw


def _noisy(arr, rng, sigma=6.0):
    h, w = arr.shape[:2]
    grad = np.linspace(-25, 10, w, dtype=np.float32)[None, :]
    if arr.ndim == 3:
        grad = grad[..., None]
    out = arr.astype(np.float32) + grad + rng.normal(0, sigma, arr.shape)
    return np.clip(out, 0, 255).astype(np.uint8)


def make_lineart(path, invert=False):
    rng = np.random.default_rng(1)
    img = Image.new("L", (600, 400), 235)
    d = ImageDraw.Draw(img)
    d.ellipse((80, 60, 300, 280), outline=20, width=6)
    d.rectangle((340, 100, 540, 300), outline=20, width=5)
    d.line((60, 350, 540, 350), fill=20, width=4)
    arr = _noisy(np.asarray(img), rng)
    if invert:
        arr = 255 - arr
    Image.fromarray(arr).save(path)
    return path


def make_color(path):
    rng = np.random.default_rng(2)
    img = Image.new("RGB", (600, 400), (250, 240, 220))
    d = ImageDraw.Draw(img)
    d.ellipse((60, 60, 260, 260), fill=(40, 80, 200), outline=(0, 0, 0), width=4)
    d.rectangle((320, 80, 540, 240), fill=(200, 40, 40), outline=(0, 0, 0), width=4)
    d.polygon([(150, 380), (300, 280), (450, 380)], fill=(40, 160, 70), outline=(0, 0, 0))
    Image.fromarray(_noisy(np.asarray(img), rng, sigma=3.0)).save(path)
    return path


@pytest.fixture
def lineart(tmp_path):
    return make_lineart(tmp_path / "line.png")


@pytest.fixture
def lineart_inverted(tmp_path):
    return make_lineart(tmp_path / "inv.png", invert=True)


@pytest.fixture
def color(tmp_path):
    return make_color(tmp_path / "color.png")


@pytest.fixture(autouse=True)
def _private_library(tmp_path_factory, monkeypatch):
    """Tests never touch the real ~/BootTool folders."""
    base = tmp_path_factory.mktemp("boot-home")
    monkeypatch.setenv("BOOTTOOL_LIBRARY", str(base / "Library"))
    monkeypatch.setenv("BOOTTOOL_FONTS", str(base / "Fonts"))
    monkeypatch.setenv("BOOTTOOL_PRINTS", str(base / "Prints"))
