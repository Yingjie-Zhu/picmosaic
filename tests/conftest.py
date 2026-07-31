"""Shared pytest fixtures: synthetic tile folder and quadrant target image."""

from __future__ import annotations

import itertools
import random
from pathlib import Path

import pytest
from PIL import Image

#: Channel values used for the solid tile grid -> 3**3 == 27 distinct colors.
TILE_CHANNEL_VALUES = (0, 128, 255)

#: Number of valid (readable) tile images produced by the ``tile_dir`` fixture.
VALID_TILE_COUNT = len(TILE_CHANNEL_VALUES) ** 3


@pytest.fixture(autouse=True)
def _isolated_cache_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep any accidental default-cache-dir usage inside the test tmp dir."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))


@pytest.fixture(scope="session")
def tile_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Folder with 27 solid-color 64px tiles + one corrupt jpg + one text file."""
    root = tmp_path_factory.mktemp("tiles")
    for r, g, b in itertools.product(TILE_CHANNEL_VALUES, repeat=3):
        Image.new("RGB", (64, 64), (r, g, b)).save(root / f"tile_{r:03d}_{g:03d}_{b:03d}.png")
    rng = random.Random(42)
    (root / "broken.jpg").write_bytes(bytes(rng.randrange(256) for _ in range(2048)))
    (root / "notes.txt").write_text("not an image\n", encoding="utf-8")
    return root


def make_quadrant_image(size: int = 128) -> Image.Image:
    """Build a square image with four solid quadrants (red/green/blue/yellow)."""
    half = size // 2
    img = Image.new("RGB", (size, size))
    img.paste(Image.new("RGB", (half, half), (255, 0, 0)), (0, 0))
    img.paste(Image.new("RGB", (half, half), (0, 255, 0)), (half, 0))
    img.paste(Image.new("RGB", (half, half), (0, 0, 255)), (0, half))
    img.paste(Image.new("RGB", (half, half), (255, 255, 0)), (half, half))
    return img


@pytest.fixture(scope="session")
def target_quadrants(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Path to a 128x128 PNG with red/green/blue/yellow solid quadrants."""
    path = tmp_path_factory.mktemp("target") / "quadrants.png"
    make_quadrant_image(128).save(path)
    return path
