# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-07-31

First public release. PicMosaic began as an unpublished 64-line prototype; 1.0.0
is a complete rewrite into a commercial-grade library, CLI, and zero-install
browser app — Pillow + NumPy based (no OpenCV), with multi-feature perceptual
matching in place of single mean-color lookup.

### Added

- **Library API**: `build_mosaic`, `MosaicConfig`, `MosaicResult`, `build_library`,
  `TileLibrary`, `TileMatcher`, and typed exceptions (`PicMosaicError`,
  `TargetImageError`, `TileLibraryError`); PEP 561 `py.typed` marker.
- **Perceptual matching**: vectorized sRGB -> CIELAB conversion (D65) and per-cell
  `detail x detail` subcell features (default detail 3) for edge-aware tile matching,
  with `rgb` available as an alternative color space.
- **Fast nearest-neighbor search**: `scipy.spatial.cKDTree` when SciPy is installed
  (`fast` extra), with a memory-bounded, chunked NumPy fallback that produces identical
  results.
- **Variety controls**: `repeat_radius` (Chebyshev no-repeat window), `max_usage`
  (per-tile placement cap), `candidates`, and seeded inverse-distance `randomness`
  sampling; cells visited in a seeded random permutation to avoid raster-scan artifacts.
- **Looks controls**: `color_adjust` (blend tiles toward the true cell color) and
  `overlay` (alpha-blend the original photo over the mosaic).
- **Tile library caching**: compressed `.npz` cache keyed by file paths, sizes, mtimes,
  and build parameters; automatic invalidation; corrupt caches are ignored and rebuilt.
- **Robust image I/O**: EXIF orientation handling, alpha compositing over white,
  CMYK/palette/grayscale conversion, case-insensitive extension matching for JPEG, PNG,
  WebP, BMP, and TIFF; unreadable tiles are skipped with a logged warning, never fatal.
- **Performance**: concurrent tile loading (thread pool), batched feature queries,
  row-by-row assembly that bounds peak memory, and an 8 GiB output memory guard with a
  clear error message.
- **CLI** (`picmosaic`): the full option set, with progress bars, quiet and verbose
  modes, friendly error messages, meaningful exit codes, and a result summary;
  guaranteed full-coverage output (no black edges) at approximately
  `target size x upscale` (rounded to the tile grid).
- **Browser app** (`docs/index.html`): the full engine ported to JavaScript in a single
  self-contained page — zero install, 100% client-side processing, bilingual (中文/EN),
  built-in one-click demo, works from GitHub Pages or a double-clicked local file.
- **Demo assets**: `examples/make_demo_assets.py` generates a procedural target image
  and ~600 synthetic tiles for an instant, offline demo.
- **Project infrastructure**: pytest suite, ruff + mypy configuration, pre-commit hooks,
  GitHub Actions CI across Linux/macOS/Windows and Python 3.10–3.14, Dependabot,
  PyPI Trusted Publishing workflow, issue forms, and bilingual documentation
  (English + Simplified Chinese).


[Unreleased]: https://github.com/Yingjie-Zhu/picmosaic/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/Yingjie-Zhu/picmosaic/releases/tag/v1.0.0
