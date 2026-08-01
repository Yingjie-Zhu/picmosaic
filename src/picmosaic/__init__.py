"""PicMosaic: rebuild any photo as a mosaic of many small tile images."""

from __future__ import annotations

from .exceptions import PicMosaicError, TargetImageError, TileLibraryError
from .library import TileLibrary, build_library
from .matcher import TileMatcher
from .mosaic import MosaicConfig, MosaicResult, build_mosaic

__version__ = "1.0.0"

__all__ = [
    "MosaicConfig",
    "MosaicResult",
    "PicMosaicError",
    "TargetImageError",
    "TileLibrary",
    "TileLibraryError",
    "TileMatcher",
    "__version__",
    "build_library",
    "build_mosaic",
]
