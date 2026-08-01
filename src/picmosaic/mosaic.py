"""End-to-end mosaic orchestration: config, result container, and builder."""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from tqdm import tqdm

from .color import cell_features
from .exceptions import PicMosaicError, TargetImageError
from .imgio import load_image, normalize_image
from .library import TileLibrary, build_library
from .matcher import TileMatcher

logger = logging.getLogger(__name__)

#: Maximum allowed size of the output pixel buffer (8 GiB).
_MAX_OUTPUT_BYTES = 8 * 1024**3


@dataclass
class MosaicConfig:
    """All tunable parameters for building a mosaic.

    Attributes:
        tile_size: Cell size in target pixels (grid density), >= 4.
        upscale: Output resolution multiplier (render size is
            ``tile_size * upscale``), >= 1.
        detail: Subcell matching detail per cell, 1..4.
        color_space: ``"lab"`` (perceptual, default) or ``"rgb"``.
        color_adjust: 0..1 blend of each placed tile toward the cell's mean
            color.
        overlay: 0..1 alpha of the original image over the finished mosaic.
        repeat_radius: Chebyshev no-repeat radius on the grid; 0 disables.
        max_usage: Maximum placements per tile; ``None`` for unlimited.
        candidates: Nearest-neighbor candidates considered per cell, >= 1.
        randomness: 0..1; 0 always picks the best match, higher values sample
            among candidates with inverse-distance weights.
        seed: Random seed for deterministic output; ``None`` for random.
        workers: Tile-loading thread count; ``None`` for automatic.
        recursive: Search the tile folder recursively.
        use_cache: Use the on-disk tile library cache.
        cache_dir: Cache directory override; ``None`` for the default.
    """

    tile_size: int = 32
    upscale: int = 1
    detail: int = 3
    color_space: str = "lab"
    color_adjust: float = 0.3
    overlay: float = 0.0
    repeat_radius: int = 1
    max_usage: int | None = None
    candidates: int = 12
    randomness: float = 0.0
    seed: int | None = None
    workers: int | None = None
    recursive: bool = True
    use_cache: bool = True
    cache_dir: str | Path | None = None

    def __post_init__(self) -> None:
        """Validate all parameter ranges.

        Raises:
            ValueError: With a clear message naming the offending field.
        """
        if self.tile_size < 4:
            raise ValueError(f"tile_size must be >= 4, got {self.tile_size}")
        if self.upscale < 1:
            raise ValueError(f"upscale must be >= 1, got {self.upscale}")
        if not 1 <= self.detail <= 4:
            raise ValueError(f"detail must be between 1 and 4, got {self.detail}")
        if self.color_space not in ("lab", "rgb"):
            raise ValueError(f'color_space must be "lab" or "rgb", got {self.color_space!r}')
        if not 0.0 <= self.color_adjust <= 1.0:
            raise ValueError(f"color_adjust must be in [0, 1], got {self.color_adjust}")
        if not 0.0 <= self.overlay <= 1.0:
            raise ValueError(f"overlay must be in [0, 1], got {self.overlay}")
        if self.repeat_radius < 0:
            raise ValueError(f"repeat_radius must be >= 0, got {self.repeat_radius}")
        if self.max_usage is not None and self.max_usage < 1:
            raise ValueError(f"max_usage must be None or >= 1, got {self.max_usage}")
        if self.candidates < 1:
            raise ValueError(f"candidates must be >= 1, got {self.candidates}")
        if not 0.0 <= self.randomness <= 1.0:
            raise ValueError(f"randomness must be in [0, 1], got {self.randomness}")


@dataclass
class MosaicResult:
    """The finished mosaic plus build statistics.

    Attributes:
        image: The rendered mosaic.
        grid: Grid dimensions ``(rows, cols)``.
        total_cells: Number of grid cells (``rows * cols``).
        unique_tiles: Number of distinct tiles used.
        library_size: Number of tiles in the library.
        elapsed: Wall-clock build time in seconds.
        assignments: ``(rows, cols)`` int32 tile index per cell.
    """

    image: Image.Image
    grid: tuple[int, int]
    total_cells: int
    unique_tiles: int
    library_size: int
    elapsed: float
    assignments: np.ndarray = field(repr=False)

    def save(self, path: str | Path, **kwargs: Any) -> None:
        """Save the mosaic image to ``path``.

        Applies a sane JPEG default (``quality=92``) when saving ``.jpg`` /
        ``.jpeg`` unless a quality is passed explicitly. All keyword arguments
        are forwarded to ``PIL.Image.Image.save``.

        Args:
            path: Destination file path (format inferred from the extension).
            **kwargs: Extra options for ``Image.save``.

        Raises:
            ValueError: If the extension maps to a format Pillow cannot save.
        """
        p = Path(path)
        if p.suffix.lower() in (".jpg", ".jpeg"):
            kwargs.setdefault("quality", 92)
        try:
            self.image.save(p, **kwargs)
        except KeyError as exc:  # PIL raises KeyError for save-unsupported formats
            raise ValueError(f"unsupported output image format: {p.suffix or p}") from exc


def _expected_render_size(tile_size: int, upscale: int, detail: int) -> int:
    """Tile render size: ``tile_size * upscale`` rounded up to a multiple of detail."""
    base = tile_size * upscale
    return ((base + detail - 1) // detail) * detail


def build_mosaic(
    target: str | Path | Image.Image,
    tile_dir: str | Path | None = None,
    config: MosaicConfig | None = None,
    *,
    library: TileLibrary | None = None,
    progress: bool = False,
) -> MosaicResult:
    """Build a photomosaic of ``target`` from a folder of tile images.

    Args:
        target: Target image path or an already-loaded ``PIL.Image.Image``.
        tile_dir: Folder with tile images (required unless ``library`` is
            passed).
        config: Build parameters; ``None`` uses :class:`MosaicConfig`
            defaults.
        library: A prebuilt :class:`TileLibrary` to reuse across runs; must
            match the config's render size, detail, and color space.
        progress: Show tqdm progress bars (tile loading and assembly).

    Returns:
        A :class:`MosaicResult` with the image and build statistics.

    Raises:
        TargetImageError: If the target image cannot be loaded.
        TileLibraryError: If the tile folder yields no usable tiles.
        ValueError: If neither ``tile_dir`` nor ``library`` is given, if a
            passed library mismatches the config, or if the estimated output
            exceeds the 8 GiB memory guard.
    """
    start = time.perf_counter()
    cfg = config if config is not None else MosaicConfig()
    if tile_dir is None and library is None:
        raise ValueError("Either tile_dir or a prebuilt library must be provided")

    # 1. Load target. Already-decoded images go through the same normalization
    # as file paths (EXIF, 16-bit rescale, alpha over white) so both routes
    # produce identical mosaics.
    if isinstance(target, Image.Image):
        img = normalize_image(target)
    else:
        try:
            img = load_image(target)
        except TargetImageError:
            raise
        except PicMosaicError as exc:
            raise TargetImageError(str(exc)) from exc

    # 2. Grid geometry. cell_analysis keeps the analysis image divisible by
    #    the subcell grid; the LANCZOS resize guarantees full coverage.
    width, height = img.size
    cols = max(1, round(width / cfg.tile_size))
    rows = max(1, round(height / cfg.tile_size))
    cell = math.ceil(cfg.tile_size / cfg.detail) * cfg.detail
    analysis = img.resize((cols * cell, rows * cell), Image.Resampling.LANCZOS)
    analysis_arr = np.asarray(analysis, dtype=np.uint8)
    logger.info("Grid: %d rows x %d cols (analysis cell %dpx)", rows, cols, cell)

    # Memory guard before any heavy work.
    render = _expected_render_size(cfg.tile_size, cfg.upscale, cfg.detail)
    estimated = rows * cols * render * render * 3
    if estimated > _MAX_OUTPUT_BYTES:
        raise ValueError(
            f"Estimated output {cols * render}x{rows * render} would need "
            f"{estimated / 1024**3:.1f} GiB (limit 8 GiB); lower --upscale "
            f"or downscale the target image"
        )

    # 3. Build or validate the tile library.
    if library is not None:
        if library.render_size != render:
            raise ValueError(
                f"Passed library render_size {library.render_size} does not match the "
                f"required {render} (tile_size {cfg.tile_size} x upscale {cfg.upscale}, "
                f"detail {cfg.detail}); rebuild the library with matching parameters"
            )
        if library.detail != cfg.detail:
            raise ValueError(
                f"Passed library detail {library.detail} does not match config detail "
                f"{cfg.detail}; rebuild the library with matching parameters"
            )
        if library.color_space != cfg.color_space:
            raise ValueError(
                f"Passed library color_space {library.color_space!r} does not match config "
                f"color_space {cfg.color_space!r}; rebuild the library with matching parameters"
            )
        lib = library
    else:
        assert tile_dir is not None  # for type-checkers; guarded above
        lib = build_library(
            tile_dir,
            render_size=cfg.tile_size * cfg.upscale,
            detail=cfg.detail,
            color_space=cfg.color_space,
            recursive=cfg.recursive,
            workers=cfg.workers,
            use_cache=cfg.use_cache,
            cache_dir=cfg.cache_dir,
            progress=progress,
        )
    render = lib.render_size

    # 4. Features and constrained matching.
    feats = cell_features(analysis_arr, rows, cols, cfg.detail, cfg.color_space)
    matcher = TileMatcher(lib.features, seed=cfg.seed)
    assignments = matcher.match_grid(
        feats,
        repeat_radius=cfg.repeat_radius,
        max_usage=cfg.max_usage,
        candidates=cfg.candidates,
        randomness=cfg.randomness,
    )

    # 5. Row-by-row assembly (bounds peak memory to one strip at a time).
    out = np.empty((rows * render, cols * render, 3), dtype=np.uint8)
    cell_means = analysis_arr.reshape(rows, cell, cols, cell, 3).mean(axis=(1, 3))
    adjust = cfg.color_adjust
    # disable=None auto-disables the bar when stderr is not a TTY (CI, redirects).
    row_iter = tqdm(range(rows), desc="Assembling", unit="row", disable=None if progress else True)
    for r in row_iter:
        strip = lib.tiles[assignments[r]]  # (cols, R, R, 3) uint8
        if adjust > 0.0:
            means = cell_means[r].astype(np.float32)[:, None, None, :]
            blended = strip.astype(np.float32) * (1.0 - adjust) + means * adjust
            strip = np.clip(blended, 0.0, 255.0).astype(np.uint8)
        out[r * render : (r + 1) * render] = strip.transpose(1, 0, 2, 3).reshape(
            render, cols * render, 3
        )

    # 6. Optional overlay of the original image.
    if cfg.overlay > 0.0:
        overlay_img = analysis.resize((cols * render, rows * render), Image.Resampling.LANCZOS)
        overlay_arr = np.asarray(overlay_img, dtype=np.float32)
        mixed = out.astype(np.float32) * (1.0 - cfg.overlay) + overlay_arr * cfg.overlay
        out = np.clip(mixed, 0.0, 255.0).astype(np.uint8)

    elapsed = time.perf_counter() - start
    result = MosaicResult(
        image=Image.fromarray(out),
        grid=(rows, cols),
        total_cells=rows * cols,
        unique_tiles=int(np.unique(assignments).size),
        library_size=int(lib.tiles.shape[0]),
        elapsed=elapsed,
        assignments=assignments,
    )
    logger.info(
        "Mosaic done: %dx%d px, %d cells, %d/%d unique tiles, %.2fs",
        result.image.width,
        result.image.height,
        result.total_cells,
        result.unique_tiles,
        result.library_size,
        elapsed,
    )
    return result
