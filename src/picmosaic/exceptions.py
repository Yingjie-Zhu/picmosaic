"""Exception hierarchy for picmosaic."""

from __future__ import annotations


class PicMosaicError(Exception):
    """Base class for all picmosaic errors."""


class TargetImageError(PicMosaicError):
    """Target image missing or unreadable."""


class TileLibraryError(PicMosaicError):
    """Tile folder missing, empty, or contains no usable images."""
