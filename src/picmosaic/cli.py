"""Command-line interface for picmosaic (argparse, no extra dependencies)."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from PIL import Image

from . import __version__
from .exceptions import PicMosaicError
from .mosaic import MosaicConfig, build_mosaic

logger = logging.getLogger(__name__)


def _check_output_path(output: Path) -> str | None:
    """Validate the output path up front, before any heavy work.

    Returns an error message, or None if the path looks writable.
    """
    if output.is_dir():
        return f"output path is a directory: {output}"
    parent = output.parent
    if not parent.is_dir():
        return f"output directory does not exist: {parent}"
    if not os.access(parent, os.W_OK):
        return f"output directory is not writable: {parent}"
    ext = output.suffix.lower()
    if not ext:
        return f"output path has no image extension: {output}"
    Image.init()  # populate the registry of save handlers
    fmt = Image.registered_extensions().get(ext)
    if fmt is None or fmt not in Image.SAVE:  # some formats are read-only in Pillow
        return f"unsupported output image format: {ext}"
    return None


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser for the ``picmosaic`` command."""
    parser = argparse.ArgumentParser(
        prog="picmosaic",
        usage="picmosaic TARGET --tiles DIR [options]",
        description="Rebuild a photo as a mosaic of many small tile images.",
    )
    parser.add_argument("target", metavar="TARGET", help="the photo to rebuild as a mosaic")
    parser.add_argument(
        "-t",
        "--tiles",
        metavar="DIR",
        required=True,
        help="folder with tile images (searched recursively)",
    )

    out = parser.add_argument_group("output")
    out.add_argument(
        "-o",
        "--output",
        metavar="PATH",
        default=None,
        help="output image path (default: <target_stem>-mosaic.jpg next to the target)",
    )

    grid = parser.add_argument_group("grid")
    grid.add_argument(
        "--tile-size",
        type=int,
        default=32,
        metavar="INT",
        help="cell size in target pixels; smaller values give a finer grid (default: 32)",
    )
    grid.add_argument(
        "--upscale",
        type=int,
        default=1,
        metavar="INT",
        help="output resolution multiplier (default: 1)",
    )

    matching = parser.add_argument_group("matching")
    matching.add_argument(
        "--detail",
        type=int,
        default=3,
        metavar="INT",
        help="subcell matching detail, 1..4 (default: 3)",
    )
    matching.add_argument(
        "--color-space",
        choices=("lab", "rgb"),
        default="lab",
        help="feature color space (default: lab)",
    )
    matching.add_argument(
        "--candidates",
        type=int,
        default=12,
        metavar="INT",
        help="nearest-neighbor candidates considered per cell (default: 12)",
    )

    variety = parser.add_argument_group("variety")
    variety.add_argument(
        "--repeat-radius",
        type=int,
        default=1,
        metavar="INT",
        help="no-repeat Chebyshev radius on the grid; 0 disables (default: 1)",
    )
    variety.add_argument(
        "--max-usage",
        type=int,
        default=None,
        metavar="INT",
        help="maximum placements per tile (default: unlimited)",
    )
    variety.add_argument(
        "--randomness",
        type=float,
        default=0.0,
        metavar="FLOAT",
        help="0..1 candidate sampling randomness; 0 always picks the best match (default: 0.0)",
    )

    looks = parser.add_argument_group("looks")
    looks.add_argument(
        "--color-adjust",
        type=float,
        default=0.3,
        metavar="FLOAT",
        help="blend each placed tile toward the cell's mean color, 0..1 (default: 0.3)",
    )
    looks.add_argument(
        "--overlay",
        type=float,
        default=0.0,
        metavar="FLOAT",
        help="alpha of the original image over the finished mosaic, 0..1 (default: 0.0)",
    )

    misc = parser.add_argument_group("misc")
    misc.add_argument(
        "--seed", type=int, default=None, metavar="INT", help="random seed for reproducible output"
    )
    misc.add_argument(
        "--workers",
        type=int,
        default=None,
        metavar="INT",
        help="tile-loading thread count (default: auto)",
    )
    misc.add_argument("--no-cache", action="store_true", help="disable the tile library cache")
    misc.add_argument(
        "--no-recursive", action="store_true", help="do not search the tile folder recursively"
    )
    misc.add_argument(
        "-q", "--quiet", action="store_true", help="only warnings and errors; no progress bars"
    )
    misc.add_argument(
        "-v", "--verbose", action="store_true", help="debug logging and full tracebacks"
    )
    misc.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return an exit code (0 ok, 1 error, 130 interrupted)."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # --version/--help exit 0; argparse errors exit 2
        code = exc.code
        return code if isinstance(code, int) else 0 if code is None else 1

    if args.quiet:
        level = logging.WARNING
    elif args.verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO
    logging.basicConfig(format="%(levelname)s %(message)s")
    logging.getLogger().setLevel(level)

    target = Path(args.target)
    if not target.is_file():
        print(f"Error: target image not found: {target}", file=sys.stderr)
        return 1

    output = Path(args.output) if args.output else target.with_name(f"{target.stem}-mosaic.jpg")
    problem = _check_output_path(output)
    if problem:
        print(f"Error: {problem}", file=sys.stderr)
        return 1

    try:
        config = MosaicConfig(
            tile_size=args.tile_size,
            upscale=args.upscale,
            detail=args.detail,
            color_space=args.color_space,
            color_adjust=args.color_adjust,
            overlay=args.overlay,
            repeat_radius=args.repeat_radius,
            max_usage=args.max_usage,
            candidates=args.candidates,
            randomness=args.randomness,
            seed=args.seed,
            workers=args.workers,
            recursive=not args.no_recursive,
            use_cache=not args.no_cache,
        )
        result = build_mosaic(target, args.tiles, config, progress=not args.quiet)
        result.save(output)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except (PicMosaicError, ValueError, OSError) as exc:
        if args.verbose:
            raise
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    rows, cols = result.grid
    print(f"Saved:   {output}")
    print(f"Size:    {result.image.width}x{result.image.height}")
    print(f"Grid:    {rows}x{cols} ({result.total_cells} cells)")
    print(f"Tiles:   {result.unique_tiles} unique / {result.library_size} in library")
    print(f"Elapsed: {result.elapsed:.2f}s")
    return 0


def entrypoint() -> None:
    """Console-script entry point (``picmosaic``)."""
    sys.exit(main())
