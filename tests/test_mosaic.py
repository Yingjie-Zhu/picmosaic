"""Tests for picmosaic.mosaic: config validation and end-to-end building."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from picmosaic import MosaicConfig, MosaicResult, build_library, build_mosaic
from picmosaic.exceptions import TargetImageError

#: Expected mean color per output quadrant (row slice, col slice, RGB).
_QUADRANTS = [
    (slice(0, 64), slice(0, 64), (255, 0, 0)),
    (slice(0, 64), slice(64, 128), (0, 255, 0)),
    (slice(64, 128), slice(0, 64), (0, 0, 255)),
    (slice(64, 128), slice(64, 128), (255, 255, 0)),
]


@pytest.fixture(scope="module")
def quadrant_result(tile_dir: Path, target_quadrants: Path) -> MosaicResult:
    config = MosaicConfig(
        tile_size=32,
        detail=2,
        color_adjust=0.0,
        repeat_radius=0,
        seed=0,
        use_cache=False,
    )
    return build_mosaic(target_quadrants, tile_dir, config)


class TestQuadrantMosaic:
    def test_each_quadrant_matches_solid_tile(self, quadrant_result: MosaicResult) -> None:
        arr = np.asarray(quadrant_result.image).astype(np.float64)
        for rows, cols, expected in _QUADRANTS:
            mean = arr[rows, cols].reshape(-1, 3).mean(axis=0)
            np.testing.assert_allclose(mean, expected, atol=10)

    def test_result_statistics(self, quadrant_result: MosaicResult) -> None:
        res = quadrant_result
        assert res.image.size == (128, 128)
        assert res.grid == (4, 4)
        assert res.total_cells == 16
        assert res.library_size == 27
        assert 1 <= res.unique_tiles <= 16
        assert res.elapsed >= 0.0
        assert res.assignments.shape == (4, 4)
        assert res.assignments.dtype == np.int32
        assert (res.assignments >= 0).all()
        assert (res.assignments < res.library_size).all()

    def test_save_jpeg_and_png(self, quadrant_result: MosaicResult, tmp_path: Path) -> None:
        jpg = tmp_path / "out.jpg"
        quadrant_result.save(jpg)
        with Image.open(jpg) as img:
            assert img.format == "JPEG"
            assert img.size == quadrant_result.image.size
        png = tmp_path / "out.png"
        quadrant_result.save(png)
        with Image.open(png) as img:
            assert img.format == "PNG"


class TestGeometry:
    def test_output_size_equals_target_times_upscale(
        self, tile_dir: Path, target_quadrants: Path
    ) -> None:
        config = MosaicConfig(tile_size=32, upscale=2, detail=2, seed=0, use_cache=False)
        result = build_mosaic(target_quadrants, tile_dir, config)
        assert result.image.size == (256, 256)  # 128x128 target, upscale=2

    def test_full_coverage_non_divisible_target(self, tile_dir: Path) -> None:
        # 130x97 with tile 32 -> grid rounds to 4x3; the whole canvas is filled.
        target = Image.new("RGB", (130, 97), (255, 128, 0))
        config = MosaicConfig(tile_size=32, detail=2, color_adjust=1.0, seed=0, use_cache=False)
        result = build_mosaic(target, tile_dir, config)
        assert result.grid == (3, 4)
        assert result.image.size == (128, 96)
        arr = np.asarray(result.image)
        # No black rim / unfilled strip anywhere: every pixel keeps the target color.
        assert (arr.sum(axis=2) > 0).all()
        np.testing.assert_allclose(arr.reshape(-1, 3).mean(axis=0), [255, 128, 0], atol=5)

    def test_overlay_one_reproduces_target(self, tile_dir: Path, target_quadrants: Path) -> None:
        config = MosaicConfig(
            tile_size=32, detail=2, overlay=1.0, color_adjust=0.0, seed=0, use_cache=False
        )
        result = build_mosaic(target_quadrants, tile_dir, config)
        arr = np.asarray(result.image).astype(np.float64)
        for rows, cols, expected in _QUADRANTS:
            mean = arr[rows, cols].reshape(-1, 3).mean(axis=0)
            np.testing.assert_allclose(mean, expected, atol=10)


class TestConfigValidation:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"tile_size": 3},
            {"upscale": 0},
            {"detail": 0},
            {"detail": 5},
            {"color_space": "hsv"},
            {"color_adjust": -0.1},
            {"color_adjust": 1.1},
            {"overlay": -0.1},
            {"overlay": 1.5},
            {"repeat_radius": -1},
            {"max_usage": 0},
            {"candidates": 0},
            {"randomness": -0.1},
            {"randomness": 1.1},
        ],
    )
    def test_each_bad_field_raises(self, kwargs: dict) -> None:
        with pytest.raises(ValueError):
            MosaicConfig(**kwargs)

    def test_defaults(self) -> None:
        config = MosaicConfig()
        assert config.tile_size == 32
        assert config.detail == 3  # amendment A1
        assert config.color_space == "lab"
        assert config.randomness == 0.0  # amendment A2
        assert config.repeat_radius == 1
        assert config.max_usage is None


class TestErrors:
    def test_requires_tile_dir_or_library(self, target_quadrants: Path) -> None:
        with pytest.raises(ValueError):
            build_mosaic(target_quadrants)

    def test_missing_target_raises(self, tile_dir: Path, tmp_path: Path) -> None:
        with pytest.raises(TargetImageError):
            build_mosaic(tmp_path / "missing.png", tile_dir)

    def test_memory_guard_on_absurd_upscale(self, tile_dir: Path, target_quadrants: Path) -> None:
        config = MosaicConfig(tile_size=32, upscale=100_000, detail=2, use_cache=False)
        with pytest.raises(ValueError, match="upscale"):
            build_mosaic(target_quadrants, tile_dir, config)


class TestLibraryReuse:
    def test_prebuilt_library_used(self, tile_dir: Path, target_quadrants: Path) -> None:
        lib = build_library(tile_dir, render_size=16, detail=2, use_cache=False)
        config = MosaicConfig(
            tile_size=16, detail=2, color_adjust=0.0, repeat_radius=0, seed=0, use_cache=False
        )
        result = build_mosaic(target_quadrants, config=config, library=lib)
        assert result.library_size == 27
        assert result.image.size == (128, 128)

    def test_render_size_mismatch_raises(self, tile_dir: Path, target_quadrants: Path) -> None:
        lib = build_library(tile_dir, render_size=16, detail=2, use_cache=False)
        config = MosaicConfig(tile_size=32, detail=2, use_cache=False)
        with pytest.raises(ValueError, match="render_size"):
            build_mosaic(target_quadrants, config=config, library=lib)

    def test_detail_mismatch_raises(self, tile_dir: Path, target_quadrants: Path) -> None:
        lib = build_library(tile_dir, render_size=32, detail=4, use_cache=False)
        config = MosaicConfig(tile_size=32, detail=2, use_cache=False)
        with pytest.raises(ValueError, match="detail"):
            build_mosaic(target_quadrants, config=config, library=lib)

    def test_color_space_mismatch_raises(self, tile_dir: Path, target_quadrants: Path) -> None:
        lib = build_library(tile_dir, render_size=32, detail=2, color_space="rgb", use_cache=False)
        config = MosaicConfig(tile_size=32, detail=2, color_space="lab", use_cache=False)
        with pytest.raises(ValueError, match="color_space"):
            build_mosaic(target_quadrants, config=config, library=lib)


def test_end_to_end_seed_determinism(tile_dir: Path, target_quadrants: Path) -> None:
    config = MosaicConfig(tile_size=32, detail=2, randomness=0.5, seed=7, use_cache=False)
    r1 = build_mosaic(target_quadrants, tile_dir, config)
    r2 = build_mosaic(target_quadrants, tile_dir, config)
    np.testing.assert_array_equal(r1.assignments, r2.assignments)
    np.testing.assert_array_equal(np.asarray(r1.image), np.asarray(r2.image))
