"""Tile library building with concurrent loading and on-disk caching."""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from tqdm import tqdm

from .color import tile_features
from .exceptions import TileLibraryError
from .imgio import iter_image_paths, load_image, square_thumbnail

logger = logging.getLogger(__name__)

#: Bump when the cache layout or feature computation changes incompatibly.
CACHE_VERSION = 1

#: Best-effort cap on the total size of the on-disk cache (oldest entries pruned).
CACHE_MAX_BYTES = 2 * 1024**3


@dataclass(frozen=True)
class TileLibrary:
    """An immutable, render-ready tile library.

    Attributes:
        tiles: ``(N, R, R, 3)`` uint8 array of square, render-ready tiles.
        features: ``(N, D)`` float32 feature matrix (``D = detail**2 * 3``).
        paths: Source file path per tile.
        render_size: Tile edge length ``R`` in pixels.
        detail: Subcell grid resolution used for the features.
        color_space: Feature color space (``"lab"`` or ``"rgb"``).
    """

    tiles: np.ndarray
    features: np.ndarray
    paths: tuple[str, ...]
    render_size: int
    detail: int
    color_space: str


def default_cache_dir() -> Path:
    """Return (and create on demand) the platform cache directory.

    ``~/.cache/picmosaic`` on POSIX (respecting ``XDG_CACHE_HOME``) and
    ``%LOCALAPPDATA%/picmosaic/cache`` on Windows.

    Returns:
        The cache directory path (created if missing).
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        base_path = Path(base) if base else Path.home() / "AppData" / "Local"
        cache_dir = base_path / "picmosaic" / "cache"
    else:
        base = os.environ.get("XDG_CACHE_HOME")
        base_path = Path(base) if base else Path.home() / ".cache"
        cache_dir = base_path / "picmosaic"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _cache_key(
    paths: list[Path],
    render_size: int,
    detail: int,
    color_space: str,
) -> str:
    """Hash the tile file list (path, mtime_ns, size) and build parameters."""
    hasher = hashlib.sha256()
    for p in paths:
        try:
            st = p.stat()
            mtime_ns, size = st.st_mtime_ns, st.st_size
        except OSError:
            mtime_ns, size = -1, -1
        hasher.update(f"{p.as_posix()}|{mtime_ns}|{size}\n".encode())
    hasher.update(
        f"render_size={render_size}|detail={detail}|"
        f"color_space={color_space}|cache_version={CACHE_VERSION}".encode()
    )
    return hasher.hexdigest()


def _load_cached_library(
    cache_path: Path,
    render_size: int,
    detail: int,
    color_space: str,
) -> TileLibrary | None:
    """Load a cached library, returning None on any corruption or mismatch."""
    if not cache_path.is_file():
        return None
    try:
        with np.load(cache_path, allow_pickle=False) as data:
            tiles = np.asarray(data["tiles"])
            features = np.asarray(data["features"])
            paths = tuple(str(p) for p in data["paths"].tolist())
        n = tiles.shape[0] if tiles.ndim == 4 else -1
        expected_dim = detail * detail * 3
        if (
            tiles.ndim != 4
            or tiles.dtype != np.uint8
            or tiles.shape[1:] != (render_size, render_size, 3)
            or features.shape != (n, expected_dim)
            or len(paths) != n
            or n < 1
        ):
            logger.warning("Ignoring incompatible cache file %s", cache_path)
            return None
        return TileLibrary(
            tiles=tiles,
            features=features.astype(np.float32, copy=False),
            paths=paths,
            render_size=render_size,
            detail=detail,
            color_space=color_space,
        )
    except Exception as exc:
        logger.warning("Ignoring corrupt cache file %s (%s); rebuilding", cache_path, exc)
        return None


def _save_cached_library(cache_path: Path, library: TileLibrary) -> None:
    """Atomically write the library cache; log and continue on failure."""
    tmp_path = cache_path.with_name(f".{cache_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_path, "wb") as fh:
            np.savez_compressed(
                fh,
                tiles=library.tiles,
                features=library.features,
                paths=np.array(library.paths),
            )
        os.replace(tmp_path, cache_path)
        logger.info("Saved tile library cache to %s", cache_path)
        _prune_cache(cache_path.parent, keep=cache_path)
    except OSError as exc:
        logger.warning("Could not write cache file %s: %s", cache_path, exc)
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)


def _prune_cache(cache_root: Path, *, keep: Path | None = None) -> None:
    """Best-effort size cap: delete oldest cache entries beyond CACHE_MAX_BYTES.

    Every tile-set or parameter change writes a new entry, so the cache grows
    without bound unless trimmed. Entries are removed oldest-modified first
    until the total drops under the cap; the just-written ``keep`` entry is
    never removed. All failures are swallowed — pruning must never break a
    build.
    """
    try:
        entries = []
        for p in cache_root.glob("*.npz"):
            with contextlib.suppress(OSError):
                st = p.stat()
                entries.append((st.st_mtime_ns, st.st_size, p))
        total = sum(size for _, size, _ in entries)
        if total <= CACHE_MAX_BYTES:
            return
        entries.sort()  # oldest first
        for _, size, p in entries:
            if keep is not None and p == keep:
                continue
            with contextlib.suppress(OSError):
                p.unlink()
                total -= size
                logger.info("Pruned old tile cache entry %s", p.name)
            if total <= CACHE_MAX_BYTES:
                break
    except OSError:  # pragma: no cover - directory listing failure
        pass


def build_library(
    tile_dir: str | Path,
    *,
    render_size: int = 32,
    detail: int = 3,
    color_space: str = "lab",
    recursive: bool = True,
    workers: int | None = None,
    use_cache: bool = True,
    cache_dir: str | Path | None = None,
    progress: bool = False,
) -> TileLibrary:
    """Build a :class:`TileLibrary` from a folder of images.

    Tiles are loaded concurrently (Pillow decoding releases the GIL), squared
    by center-crop, resized to ``render_size``, and featurized. Unreadable
    files are skipped with a warning. Results are cached on disk keyed by the
    file list (paths, mtimes, sizes) and the build parameters.

    Args:
        tile_dir: Folder containing the tile images.
        render_size: Tile edge length in pixels; rounded up to the next
            multiple of ``detail`` if needed.
        detail: Subcell grid resolution for matching features (>= 1).
        color_space: ``"lab"`` (perceptual, default) or ``"rgb"``.
        recursive: Search subdirectories too.
        workers: Thread count; ``None`` means ``min(8, cpu_count)``.
        use_cache: Load/store the on-disk ``.npz`` cache.
        cache_dir: Cache directory; ``None`` means :func:`default_cache_dir`.
        progress: Show a tqdm progress bar while loading tiles.

    Returns:
        The built (or cache-loaded) tile library.

    Raises:
        TileLibraryError: If ``tile_dir`` is not a directory, contains no
            image files, or contains zero usable images.
        ValueError: If ``render_size`` or ``detail`` is out of range.
    """
    if render_size < 1:
        raise ValueError(f"render_size must be >= 1, got {render_size}")
    if detail < 1:
        raise ValueError(f"detail must be >= 1, got {detail}")
    if color_space not in ("lab", "rgb"):
        raise ValueError(f'color_space must be "lab" or "rgb", got {color_space!r}')
    render_size = ((render_size + detail - 1) // detail) * detail

    paths = iter_image_paths(tile_dir, recursive=recursive)
    if not paths:
        raise TileLibraryError(f"No image files with a supported extension found in {tile_dir}")

    cache_path: Path | None = None
    if use_cache:
        # The cache is strictly best-effort: an unusable cache directory must
        # never abort the build.
        try:
            cache_root = Path(cache_dir) if cache_dir is not None else default_cache_dir()
            key = _cache_key(paths, render_size, detail, color_space)
            cache_path = cache_root / f"{key}.npz"
            cached = _load_cached_library(cache_path, render_size, detail, color_space)
        except OSError as exc:
            logger.warning("Tile cache unavailable (%s); building without cache", exc)
            cache_path = None
            cached = None
        if cached is not None:
            logger.info("Tile library cache hit: %d tiles from %s", len(cached.paths), cache_path)
            return cached
        logger.info("Tile library cache miss for %s (%d candidate files)", tile_dir, len(paths))

    if workers is None:
        workers = min(8, os.cpu_count() or 1)
    workers = max(1, workers)

    def _load_one(path: Path) -> tuple[np.ndarray, np.ndarray] | None:
        try:
            img = load_image(path)
            thumb = square_thumbnail(img, render_size)
            arr = np.asarray(thumb, dtype=np.uint8)
            feats = tile_features(arr, detail, color_space)
            return arr, feats
        except Exception as exc:
            logger.warning("Skipping unreadable tile %s: %s", path, exc)
            return None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results_iter = pool.map(_load_one, paths)
        wrapped = tqdm(
            results_iter,
            total=len(paths),
            desc="Loading tiles",
            unit="img",
            # disable=None auto-disables on non-TTY stderr (CI, redirects).
            disable=None if progress else True,
        )
        results = list(wrapped)

    tiles_list: list[np.ndarray] = []
    features_list: list[np.ndarray] = []
    kept_paths: list[str] = []
    skipped = 0
    for path, result in zip(paths, results, strict=True):
        if result is None:
            skipped += 1
            continue
        arr, feats = result
        tiles_list.append(arr)
        features_list.append(feats)
        kept_paths.append(str(path))

    if skipped:
        logger.warning("Skipped %d unreadable files", skipped)
    if not tiles_list:
        raise TileLibraryError(f"No usable tile images in {tile_dir} ({skipped} files skipped)")

    library = TileLibrary(
        tiles=np.stack(tiles_list),
        features=np.stack(features_list).astype(np.float32, copy=False),
        paths=tuple(kept_paths),
        render_size=render_size,
        detail=detail,
        color_space=color_space,
    )
    logger.info(
        "Built tile library: %d tiles at %dpx (detail=%d, %s)",
        len(library.paths),
        render_size,
        detail,
        color_space,
    )
    if cache_path is not None:
        _save_cached_library(cache_path, library)
    return library
