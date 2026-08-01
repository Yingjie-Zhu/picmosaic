"""Tests for picmosaic.library: building, skipping bad tiles, and caching."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from picmosaic.exceptions import TileLibraryError
from picmosaic.library import build_library, default_cache_dir

_LIB_LOGGER = "picmosaic.library"

#: Valid tiles produced by the ``tile_dir`` fixture (see conftest.py).
VALID_TILE_COUNT = 27


class TestBuildLibrary:
    def test_builds_and_skips_unreadable(
        self, tile_dir: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=_LIB_LOGGER):
            lib = build_library(tile_dir, render_size=8, detail=2, use_cache=False)
        assert lib.tiles.shape == (VALID_TILE_COUNT, 8, 8, 3)
        assert lib.tiles.dtype == np.uint8
        assert lib.features.shape == (VALID_TILE_COUNT, 2 * 2 * 3)
        assert lib.features.dtype == np.float32
        assert len(lib.paths) == VALID_TILE_COUNT
        assert all(p.endswith(".png") for p in lib.paths)
        assert lib.render_size == 8
        assert lib.detail == 2
        assert lib.color_space == "lab"
        # The corrupt jpg is skipped with warnings; notes.txt is never listed.
        assert "broken.jpg" in caplog.text
        assert "Skipped 1 unreadable" in caplog.text

    def test_detail_defaults_to_3(self, tile_dir: Path) -> None:
        lib = build_library(tile_dir, render_size=6, use_cache=False)
        assert lib.detail == 3
        assert lib.features.shape == (VALID_TILE_COUNT, 3 * 3 * 3)

    def test_render_size_rounded_up_to_detail_multiple(self, tile_dir: Path) -> None:
        lib = build_library(tile_dir, render_size=7, detail=2, use_cache=False)
        assert lib.render_size == 8
        assert lib.tiles.shape[1:] == (8, 8, 3)

    def test_empty_dir_raises(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(TileLibraryError):
            build_library(empty, use_cache=False)

    def test_no_usable_images_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad"
        bad.mkdir()
        (bad / "junk.png").write_bytes(bytes(range(256)))
        with pytest.raises(TileLibraryError):
            build_library(bad, use_cache=False)

    def test_invalid_parameters_raise(self, tile_dir: Path) -> None:
        with pytest.raises(ValueError):
            build_library(tile_dir, render_size=0, use_cache=False)
        with pytest.raises(ValueError):
            build_library(tile_dir, detail=0, use_cache=False)
        with pytest.raises(ValueError):
            build_library(tile_dir, color_space="hsv", use_cache=False)


class TestCache:
    def test_cache_file_created_and_hit(
        self, tile_dir: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        cache = tmp_path / "cache"
        with caplog.at_level(logging.INFO, logger=_LIB_LOGGER):
            lib1 = build_library(tile_dir, render_size=8, detail=2, cache_dir=cache)
        npz_files = list(cache.glob("*.npz"))
        assert len(npz_files) == 1
        assert "cache miss" in caplog.text.lower()

        caplog.clear()
        with caplog.at_level(logging.INFO, logger=_LIB_LOGGER):
            lib2 = build_library(tile_dir, render_size=8, detail=2, cache_dir=cache)
        assert "cache hit" in caplog.text.lower()
        assert len(list(cache.glob("*.npz"))) == 1
        np.testing.assert_array_equal(lib1.tiles, lib2.tiles)
        np.testing.assert_array_equal(lib1.features, lib2.features)
        assert lib1.paths == lib2.paths
        assert lib2.render_size == 8
        assert lib2.detail == 2

    def test_cache_invalidated_on_mtime_change(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        tiles = tmp_path / "tiles"
        tiles.mkdir()
        for i, color in enumerate([(255, 0, 0), (0, 255, 0), (0, 0, 255)]):
            Image.new("RGB", (16, 16), color).save(tiles / f"t{i}.png")
        cache = tmp_path / "cache"

        build_library(tiles, render_size=8, detail=2, cache_dir=cache)
        assert len(list(cache.glob("*.npz"))) == 1

        touched = tiles / "t0.png"
        st = touched.stat()
        os.utime(touched, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))

        caplog.clear()
        with caplog.at_level(logging.INFO, logger=_LIB_LOGGER):
            build_library(tiles, render_size=8, detail=2, cache_dir=cache)
        assert "cache hit" not in caplog.text.lower()
        assert "cache miss" in caplog.text.lower()
        assert len(list(cache.glob("*.npz"))) == 2

    def test_corrupt_cache_file_is_ignored(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        tiles = tmp_path / "tiles"
        tiles.mkdir()
        Image.new("RGB", (16, 16), (10, 20, 30)).save(tiles / "t.png")
        cache = tmp_path / "cache"

        build_library(tiles, render_size=8, detail=2, cache_dir=cache)
        npz = next(iter(cache.glob("*.npz")))
        npz.write_bytes(b"this is not a valid npz file")

        with caplog.at_level(logging.WARNING, logger=_LIB_LOGGER):
            lib = build_library(tiles, render_size=8, detail=2, cache_dir=cache)
        assert lib.tiles.shape == (1, 8, 8, 3)  # rebuilt fine despite corruption

    def test_no_cache_writes_nothing(self, tile_dir: Path, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        build_library(tile_dir, render_size=8, detail=2, use_cache=False, cache_dir=cache)
        assert not cache.exists() or not list(cache.glob("*.npz"))


def test_default_cache_dir_created(tmp_path: Path) -> None:
    # The autouse fixture points XDG_CACHE_HOME / LOCALAPPDATA at tmp_path.
    path = default_cache_dir()
    assert path.is_dir()
    if sys.platform == "win32":
        assert path == tmp_path / "localappdata" / "picmosaic" / "cache"
    else:
        assert path == tmp_path / "xdg-cache" / "picmosaic"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission semantics")
def test_unwritable_cache_dir_does_not_abort_build(tile_dir: Path, tmp_path: Path) -> None:
    """The cache is best-effort: an unusable cache dir must not break the build."""
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o000)
    try:
        lib = build_library(
            tile_dir, render_size=8, detail=1, use_cache=True, cache_dir=locked / "sub"
        )
        assert lib.tiles.shape[0] > 0
    finally:
        locked.chmod(0o755)


def test_cache_pruned_when_over_size_cap(
    tile_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Old cache entries are removed once the total size exceeds the cap."""
    import picmosaic.library as lib_mod

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    stale = cache_dir / ("0" * 64 + ".npz")
    stale.write_bytes(b"x" * 4096)
    old_time = 1_000_000_000
    os.utime(stale, (old_time, old_time))
    monkeypatch.setattr(lib_mod, "CACHE_MAX_BYTES", 1024)
    build_library(tile_dir, render_size=8, detail=1, use_cache=True, cache_dir=cache_dir)
    assert not stale.exists(), "stale oversized cache entry should have been pruned"
    assert list(cache_dir.glob("*.npz")), "the fresh entry must survive pruning"
