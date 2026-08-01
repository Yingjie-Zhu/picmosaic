"""Tests for picmosaic.color: sRGB->Lab conversion and grid cell features."""

from __future__ import annotations

import numpy as np
import pytest
from numpy.testing import assert_allclose, assert_array_equal

from picmosaic.color import cell_features, srgb_to_lab, tile_features


def _block_image() -> np.ndarray:
    """4x4 image made of four solid 2x2 blocks: red, green, blue, white."""
    img = np.zeros((4, 4, 3), dtype=np.uint8)
    img[0:2, 0:2] = (255, 0, 0)
    img[0:2, 2:4] = (0, 255, 0)
    img[2:4, 0:2] = (0, 0, 255)
    img[2:4, 2:4] = (255, 255, 255)
    return img


class TestSrgbToLab:
    def test_white(self) -> None:
        lab = srgb_to_lab(np.array([255, 255, 255], dtype=np.uint8))
        assert_allclose(lab, [100.0, 0.0, 0.0], atol=0.05)

    def test_black(self) -> None:
        lab = srgb_to_lab(np.array([0, 0, 0], dtype=np.uint8))
        assert_allclose(lab, [0.0, 0.0, 0.0], atol=0.05)

    def test_known_red(self) -> None:
        lab = srgb_to_lab(np.array([255, 0, 0], dtype=np.uint8))
        assert_allclose(lab, [53.2, 80.1, 67.2], atol=1.0)

    def test_dtype_and_shape(self) -> None:
        rng = np.random.default_rng(0)
        rgb = rng.integers(0, 256, size=(5, 7, 3), dtype=np.uint8)
        lab = srgb_to_lab(rgb)
        assert lab.dtype == np.float32
        assert lab.shape == (5, 7, 3)
        assert float(lab[..., 0].min()) >= -0.01
        assert float(lab[..., 0].max()) <= 100.01

    def test_bad_last_dim_raises(self) -> None:
        with pytest.raises(ValueError):
            srgb_to_lab(np.zeros((3, 4), dtype=np.uint8))


class TestCellFeatures:
    def test_shape_and_values_rgb(self) -> None:
        feats = cell_features(_block_image(), rows=2, cols=2, detail=1, color_space="rgb")
        assert feats.shape == (2, 2, 3)
        assert feats.dtype == np.float32
        assert_allclose(feats[0, 0], [255, 0, 0])
        assert_allclose(feats[0, 1], [0, 255, 0])
        assert_allclose(feats[1, 0], [0, 0, 255])
        assert_allclose(feats[1, 1], [255, 255, 255])

    def test_lab_matches_converted_means(self) -> None:
        feats = cell_features(_block_image(), rows=2, cols=2, detail=1, color_space="lab")
        assert feats.shape == (2, 2, 3)
        assert_allclose(feats[0, 0], srgb_to_lab(np.array([255.0, 0.0, 0.0])), atol=1e-3)
        assert_allclose(feats[1, 1], srgb_to_lab(np.array([255.0, 255.0, 255.0])), atol=1e-3)

    def test_subcell_order_row_major(self) -> None:
        # One cell with detail=2: the four 2x2 blocks concatenated row-major.
        feats = cell_features(_block_image(), rows=1, cols=1, detail=2, color_space="rgb")
        assert feats.shape == (1, 1, 12)
        expected = np.array([255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255], dtype=np.float32)
        assert_allclose(feats[0, 0], expected)

    def test_mean_of_mixed_pixels(self) -> None:
        img = np.zeros((2, 2, 3), dtype=np.uint8)
        img[0, 1] = 255
        img[1, 1] = 255
        feats = cell_features(img, rows=1, cols=1, detail=1, color_space="rgb")
        assert_allclose(feats[0, 0], [127.5, 127.5, 127.5])

    def test_vectorization_matches_per_cell_tile_features(self) -> None:
        rng = np.random.default_rng(0)
        img = rng.integers(0, 256, size=(12, 8, 3), dtype=np.uint8)
        feats = cell_features(img, rows=3, cols=2, detail=2, color_space="lab")
        assert feats.shape == (3, 2, 12)
        for r in range(3):
            for c in range(2):
                block = img[r * 4 : (r + 1) * 4, c * 4 : (c + 1) * 4]
                assert_allclose(feats[r, c], tile_features(block, 2, "lab"), atol=1e-4)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"rows": 0, "cols": 1, "detail": 1, "color_space": "rgb"},
            {"rows": 1, "cols": 1, "detail": 0, "color_space": "rgb"},
            {"rows": 1, "cols": 1, "detail": 1, "color_space": "hsv"},
            {"rows": 3, "cols": 1, "detail": 1, "color_space": "rgb"},  # 4 % 3 != 0
        ],
    )
    def test_invalid_arguments_raise(self, kwargs: dict) -> None:
        with pytest.raises(ValueError):
            cell_features(_block_image(), **kwargs)

    def test_bad_image_shape_raises(self) -> None:
        with pytest.raises(ValueError):
            cell_features(np.zeros((4, 4), dtype=np.uint8), 1, 1, 1, "rgb")


class TestTileFeatures:
    def test_equals_cell_features_1x1(self) -> None:
        rng = np.random.default_rng(1)
        tile = rng.integers(0, 256, size=(8, 8, 3), dtype=np.uint8)
        for space in ("rgb", "lab"):
            expected = cell_features(tile, 1, 1, 2, space).reshape(-1)
            assert_array_equal(tile_features(tile, 2, space), expected)

    def test_shape(self) -> None:
        tile = np.zeros((6, 6, 3), dtype=np.uint8)
        feats = tile_features(tile, 3, "rgb")
        assert feats.shape == (27,)
        assert feats.dtype == np.float32
