"""Robust image I/O helpers: loading, discovery, and square thumbnails."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from .exceptions import TargetImageError, TileLibraryError

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
)


def load_image(path: str | Path) -> Image.Image:
    """Open an image file robustly and return it as an RGB image.

    The image is EXIF-orientation corrected (``PIL.ImageOps.exif_transpose``)
    and converted from any source mode (L, LA, P, RGBA, CMYK, ...) to RGB.
    Images with an alpha channel are composited over a white background first.

    Args:
        path: Path to the image file.

    Returns:
        A fully-decoded ``PIL.Image.Image`` in RGB mode, detached from the
        underlying file.

    Raises:
        TargetImageError: If the file is missing, not an image, or cannot be
            decoded (wraps ``OSError`` / ``UnidentifiedImageError``).
    """
    p = Path(path)
    try:
        with Image.open(p) as im:
            im.load()
            return normalize_image(im)
    except (
        OSError,
        UnidentifiedImageError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombError,
    ) as exc:
        raise TargetImageError(f"Cannot load image {p}: {exc}") from exc


def normalize_image(img: Image.Image) -> Image.Image:
    """Normalize an already-decoded image to RGB the same way ``load_image`` does.

    Applies EXIF orientation, rescales 16/32-bit integer and float modes to
    8-bit (Pillow's ``convert("RGB")`` would clip them), composites alpha over
    a white background, and converts to RGB. The input image is not modified.

    Args:
        img: Any decoded ``PIL.Image.Image``.

    Returns:
        A new RGB image.
    """
    transposed = ImageOps.exif_transpose(img)
    if transposed is not None:  # exif_transpose may return None on very old Pillow
        img = transposed
    img = _downscale_high_depth(img)
    has_alpha = img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
    if has_alpha:
        rgba = img.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(background, rgba).convert("RGB")
    return img.convert("RGB")


def _downscale_high_depth(img: Image.Image) -> Image.Image:
    """Rescale 16/32-bit integer and float modes to 8-bit grayscale.

    Pillow's ``convert("RGB")`` clips values above 255 instead of rescaling,
    which would turn e.g. a 16-bit grayscale PNG into a nearly all-white image.
    """
    if img.mode in ("I;16", "I;16B", "I;16L", "I;16N"):
        arr16 = np.asarray(img.convert("I"), dtype=np.uint32)
        return Image.fromarray((arr16 // 257).astype(np.uint8), mode="L")
    if img.mode in ("I", "F"):
        arr = np.asarray(img, dtype=np.float64)
        peak = float(arr.max()) if arr.size else 0.0
        if peak > 255.0:
            arr = arr * (255.0 / peak)
        return Image.fromarray(np.clip(np.rint(arr), 0.0, 255.0).astype(np.uint8), mode="L")
    return img


def iter_image_paths(root: str | Path, recursive: bool = True) -> list[Path]:
    """List all image files under ``root`` with a supported extension.

    Extension matching is case-insensitive. The result is sorted by POSIX
    path string so the order is deterministic across platforms.

    Args:
        root: Directory to search.
        recursive: Also search subdirectories when True.

    Returns:
        Sorted list of image file paths.

    Raises:
        TileLibraryError: If ``root`` is not a directory.
    """
    root_path = Path(root)
    if not root_path.is_dir():
        raise TileLibraryError(f"Tile folder does not exist or is not a directory: {root_path}")
    iterator = root_path.rglob("*") if recursive else root_path.glob("*")
    paths = [p for p in iterator if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS]
    paths.sort(key=lambda p: p.as_posix())
    return paths


def square_thumbnail(img: Image.Image, size: int) -> Image.Image:
    """Center-crop an image to a square, then resize to ``size`` x ``size``.

    Aspect-fill: the largest centered square is cropped from the source (no
    distortion), then LANCZOS-resampled to the requested size.

    Args:
        img: Source image.
        size: Edge length of the resulting square thumbnail, in pixels.

    Returns:
        A new ``size`` x ``size`` image.

    Raises:
        ValueError: If ``size`` is not a positive integer.
    """
    if size < 1:
        raise ValueError(f"size must be >= 1, got {size}")
    width, height = img.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    cropped = img.crop((left, top, left + side, top + side))
    return cropped.resize((size, size), Image.Resampling.LANCZOS)
