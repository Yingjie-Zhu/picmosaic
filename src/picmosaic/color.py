"""Vectorized color math: sRGB -> CIELAB conversion and grid cell features.

All functions are pure numpy and fully vectorized -- no Python loops over
pixels. ``"lab"`` (perceptual CIELAB, D65) is the default feature space and
generally gives better-looking matches; ``"rgb"`` uses raw 0-255 values.
"""

from __future__ import annotations

import numpy as np

#: Linear sRGB -> XYZ matrix (IEC 61966-2-1, D65 white point).
_SRGB_TO_XYZ = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ],
    dtype=np.float64,
)

#: D65 reference white in XYZ (normalized to Y = 1).
_D65_WHITE = np.array([0.95047, 1.0, 1.08883], dtype=np.float64)

_DELTA = 6.0 / 29.0

VALID_COLOR_SPACES: frozenset[str] = frozenset({"lab", "rgb"})


def srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """Convert sRGB values to CIELAB (D65), fully vectorized.

    Args:
        rgb: Array of shape ``(..., 3)``, uint8 or float, values in [0, 255].

    Returns:
        float32 array of shape ``(..., 3)`` holding (L, a, b) with L in
        [0, 100]. White maps to (100, 0, 0), black to (0, 0, 0).

    Raises:
        ValueError: If the last dimension is not 3.
    """
    arr = np.asarray(rgb, dtype=np.float64)
    if arr.shape[-1] != 3:
        raise ValueError(f"expected shape (..., 3), got {arr.shape}")
    c = arr / 255.0
    # Standard sRGB linearization (gamma expansion).
    linear = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    xyz = linear @ _SRGB_TO_XYZ.T
    t = xyz / _D65_WHITE
    f = np.where(t > _DELTA**3, np.cbrt(t), t / (3.0 * _DELTA**2) + 4.0 / 29.0)
    fx, fy, fz = f[..., 0], f[..., 1], f[..., 2]
    lab = np.stack(
        [116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)],
        axis=-1,
    )
    return lab.astype(np.float32)


def cell_features(
    img: np.ndarray,
    rows: int,
    cols: int,
    detail: int,
    color_space: str,
) -> np.ndarray:
    """Compute a feature vector per grid cell of an image.

    Each cell is split into ``detail`` x ``detail`` subcells; the mean color
    of every subcell is computed, converted to ``color_space`` ("lab" converts
    the mean RGB values to CIELAB; "rgb" keeps raw 0-255 means), and the
    subcell features are concatenated. Implemented with pure reshape/mean --
    no Python loops over pixels.

    Args:
        img: ``(H, W, 3)`` uint8 array with ``H % (rows * detail) == 0`` and
            ``W % (cols * detail) == 0`` (the caller guarantees this by
            resizing).
        rows: Number of grid rows.
        cols: Number of grid columns.
        detail: Subcell grid resolution per cell (>= 1).
        color_space: ``"lab"`` or ``"rgb"``.

    Returns:
        float32 array of shape ``(rows, cols, detail * detail * 3)``.

    Raises:
        ValueError: On bad shapes, non-divisible dimensions, or an unknown
            color space.
    """
    arr = np.asarray(img)
    if arr.ndim != 3 or arr.shape[2] != 3:
        raise ValueError(f"img must have shape (H, W, 3), got {arr.shape}")
    if rows < 1 or cols < 1 or detail < 1:
        raise ValueError(f"rows, cols and detail must be >= 1, got {rows}, {cols}, {detail}")
    if color_space not in VALID_COLOR_SPACES:
        raise ValueError(
            f"color_space must be one of {sorted(VALID_COLOR_SPACES)!r}, got {color_space!r}"
        )
    height, width = arr.shape[:2]
    if height % (rows * detail) != 0 or width % (cols * detail) != 0:
        raise ValueError(
            f"image size {width}x{height} is not divisible by the "
            f"{cols * detail}x{rows * detail} subcell grid"
        )
    sub_h = height // (rows * detail)
    sub_w = width // (cols * detail)
    # (rows, detail, sub_h, cols, detail, sub_w, 3) -> mean over pixel axes.
    means = arr.reshape(rows, detail, sub_h, cols, detail, sub_w, 3).mean(axis=(2, 5))
    # (rows, detail, cols, detail, 3) -> (rows, cols, detail, detail, 3)
    means = means.transpose(0, 2, 1, 3, 4)
    feats = srgb_to_lab(means) if color_space == "lab" else means.astype(np.float32)
    return np.ascontiguousarray(feats.reshape(rows, cols, detail * detail * 3), dtype=np.float32)


def tile_features(tile: np.ndarray, detail: int, color_space: str) -> np.ndarray:
    """Compute the feature vector of one square tile.

    Args:
        tile: ``(R, R, 3)`` uint8 array with ``R % detail == 0``.
        detail: Subcell grid resolution (>= 1).
        color_space: ``"lab"`` or ``"rgb"``.

    Returns:
        float32 array of shape ``(detail * detail * 3,)`` -- identical to what
        :func:`cell_features` produces for a 1x1 grid.

    Raises:
        ValueError: On bad shapes or an unknown color space.
    """
    return cell_features(tile, 1, 1, detail, color_space).reshape(-1)
