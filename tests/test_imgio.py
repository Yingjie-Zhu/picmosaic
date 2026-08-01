"""Tests for picmosaic.imgio: robust loading, discovery, and thumbnails."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from picmosaic.exceptions import PicMosaicError, TileLibraryError
from picmosaic.imgio import (
    SUPPORTED_EXTENSIONS,
    iter_image_paths,
    load_image,
    square_thumbnail,
)

_ORIENTATION_TAG = 274


def test_supported_extensions_match_spec() -> None:
    assert (
        frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"})
        == SUPPORTED_EXTENSIONS
    )


class TestLoadImage:
    def test_exif_orientation_applied(self, tmp_path: Path) -> None:
        # 40x20 landscape: left half red, right half blue; orientation 6
        # (rotate 90 CW) must yield a 20x40 portrait with red on top.
        img = Image.new("RGB", (40, 20), (0, 0, 255))
        img.paste(Image.new("RGB", (20, 20), (255, 0, 0)), (0, 0))
        exif = Image.Exif()
        exif[_ORIENTATION_TAG] = 6
        path = tmp_path / "oriented.jpg"
        img.save(path, exif=exif)

        loaded = load_image(path)
        assert loaded.size == (20, 40)  # width/height swapped by the transpose
        arr = np.asarray(loaded)
        r, g, b = arr[10, 10]
        assert r > 200 and g < 80 and b < 80  # red half rotated to the top

    def test_rgba_composited_over_white(self, tmp_path: Path) -> None:
        path = tmp_path / "transparent.png"
        Image.new("RGBA", (8, 8), (255, 0, 0, 0)).save(path)
        loaded = load_image(path)
        assert loaded.mode == "RGB"
        assert_arr = np.asarray(loaded)
        assert (assert_arr == 255).all()  # fully transparent -> pure white

    def test_partial_alpha_blend(self, tmp_path: Path) -> None:
        path = tmp_path / "half.png"
        Image.new("RGBA", (4, 4), (255, 0, 0, 128)).save(path)
        arr = np.asarray(load_image(path))
        np.testing.assert_allclose(arr[0, 0], [255, 127, 127], atol=2)

    def test_palette_converted(self, tmp_path: Path) -> None:
        path = tmp_path / "palette.png"
        Image.new("RGB", (8, 8), (0, 255, 0)).convert("P", palette=Image.ADAPTIVE).save(path)
        loaded = load_image(path)
        assert loaded.mode == "RGB"
        assert loaded.getpixel((4, 4)) == (0, 255, 0)

    def test_grayscale_converted(self, tmp_path: Path) -> None:
        path = tmp_path / "gray.png"
        Image.new("L", (8, 8), 128).save(path)
        loaded = load_image(path)
        assert loaded.mode == "RGB"
        assert loaded.getpixel((0, 0)) == (128, 128, 128)

    def test_corrupt_file_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "corrupt.jpg"
        path.write_bytes(bytes(range(256)) * 8)
        with pytest.raises(PicMosaicError):
            load_image(path)

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(PicMosaicError):
            load_image(tmp_path / "does-not-exist.png")


class TestIterImagePaths:
    @pytest.fixture()
    def image_tree(self, tmp_path: Path) -> Path:
        (tmp_path / "b.png").write_bytes(b"x")
        (tmp_path / "a.JPG").write_bytes(b"x")
        (tmp_path / "notes.txt").write_text("hi")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "c.PnG").write_bytes(b"x")
        return tmp_path

    def test_recursive_sorted_case_insensitive(self, image_tree: Path) -> None:
        paths = iter_image_paths(image_tree, recursive=True)
        assert [p.name for p in paths] == ["a.JPG", "b.png", "c.PnG"]
        assert paths == sorted(paths, key=lambda p: p.as_posix())

    def test_non_recursive_excludes_subdirs(self, image_tree: Path) -> None:
        paths = iter_image_paths(image_tree, recursive=False)
        assert [p.name for p in paths] == ["a.JPG", "b.png"]

    def test_missing_root_raises(self, tmp_path: Path) -> None:
        with pytest.raises(TileLibraryError):
            iter_image_paths(tmp_path / "nope")

    def test_file_root_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "file.png"
        f.write_bytes(b"x")
        with pytest.raises(TileLibraryError):
            iter_image_paths(f)


class TestSquareThumbnail:
    def test_center_crop_wide_image(self) -> None:
        # 30x10 with the middle third white: the center crop must keep only white.
        img = Image.new("RGB", (30, 10), (0, 0, 0))
        img.paste(Image.new("RGB", (10, 10), (255, 255, 255)), (10, 0))
        thumb = square_thumbnail(img, 10)
        assert thumb.size == (10, 10)
        assert (np.asarray(thumb) == 255).all()

    def test_center_crop_tall_image(self) -> None:
        img = Image.new("RGB", (10, 30), (0, 0, 0))
        img.paste(Image.new("RGB", (10, 10), (255, 255, 255)), (0, 10))
        thumb = square_thumbnail(img, 10)
        assert thumb.size == (10, 10)
        assert (np.asarray(thumb) == 255).all()

    def test_resizes_to_requested_size(self) -> None:
        thumb = square_thumbnail(Image.new("RGB", (100, 60), (10, 20, 30)), 8)
        assert thumb.size == (8, 8)

    def test_invalid_size_raises(self) -> None:
        with pytest.raises(ValueError):
            square_thumbnail(Image.new("RGB", (10, 10)), 0)


class TestHighBitDepth:
    """Regression tests: 16/32-bit images must be rescaled, not clipped."""

    def test_16bit_png_loads_as_midtones(self, tmp_path: Path) -> None:
        arr16 = np.full((32, 32), 32768, dtype=np.uint16)  # 50% gray in 16-bit
        path = tmp_path / "gray16.png"
        Image.fromarray(arr16).save(path)
        loaded = np.asarray(load_image(path))
        assert loaded.shape == (32, 32, 3)
        # 32768 / 257 ~= 127 — must be mid-gray, not clipped white.
        assert 120 <= loaded.mean() <= 135

    def test_16bit_gradient_preserves_range(self, tmp_path: Path) -> None:
        arr16 = np.linspace(0, 65535, 64 * 64).reshape(64, 64).astype(np.uint16)
        path = tmp_path / "grad16.png"
        Image.fromarray(arr16).save(path)
        loaded = np.asarray(load_image(path))
        assert loaded.min() <= 5
        assert loaded.max() >= 250
        assert 110 <= loaded.mean() <= 145  # ideal ~127.5

    def test_mode_i_image_scaled_by_peak(self, tmp_path: Path) -> None:
        arr32 = np.full((16, 16), 40000, dtype=np.int32)
        path = tmp_path / "mode_i.tiff"
        Image.fromarray(arr32, mode="I").save(path)
        loaded = np.asarray(load_image(path))
        # Peak scaling maps the (uniform) max to 255.
        assert loaded.max() == 255


def test_decompression_bomb_wrapped_as_picmosaic_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "bomb.png"
    Image.new("RGB", (64, 64)).save(path)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 16)
    with pytest.raises(PicMosaicError, match="Cannot load image"):
        load_image(path)


def test_normalize_image_matches_load_image_for_16bit(tmp_path: Path) -> None:
    """PIL.Image inputs must get the same 16-bit hardening as file paths."""
    from picmosaic.imgio import normalize_image

    arr16 = np.full((24, 24), 32768, dtype=np.uint16)
    path = tmp_path / "g16.png"
    Image.fromarray(arr16).save(path)
    via_path = np.asarray(load_image(path))
    with Image.open(path) as im:
        im.load()
        via_image = np.asarray(normalize_image(im))
    assert (via_path == via_image).all()


def test_normalize_image_matches_load_image_for_rgba(tmp_path: Path) -> None:
    from picmosaic.imgio import normalize_image

    rgba = Image.new("RGBA", (16, 16), (255, 0, 0, 0))  # fully transparent red
    path = tmp_path / "t.png"
    rgba.save(path)
    via_path = np.asarray(load_image(path))
    via_image = np.asarray(normalize_image(rgba))
    assert (via_path == via_image).all()
    assert via_path.mean() == 255  # composited over white
