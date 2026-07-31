#!/usr/bin/env python3
"""Generate synthetic demo assets for picmosaic.

Creates:
  - ``examples/tiles/``  — ~600 varied 128x128 synthetic tiles (solid colors across
    the full hue/lightness range, linear gradients, radial gradients, and subtle
    noise textures) providing broad color coverage for a good-looking mosaic.
  - ``examples/target.png`` — an 800x600 colorful procedural sunset scene (gradient
    sky, sun disc, mountain silhouettes, water with reflection bands).

Everything is seeded and fully deterministic. Only NumPy and Pillow are required.

Usage:
    python examples/make_demo_assets.py
"""

from __future__ import annotations

import colorsys
import itertools
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

SEED = 20260731
TILE_SIZE = 128
TARGET_W, TARGET_H = 800, 600
HORIZON = 380

Color = tuple[int, int, int]


# ---------------------------------------------------------------------------
# Small color helpers
# ---------------------------------------------------------------------------


def hls_color(hue: float, lightness: float, saturation: float) -> Color:
    """Convert HLS floats in [0, 1] to an 8-bit RGB tuple."""
    r, g, b = colorsys.hls_to_rgb(hue % 1.0, lightness, saturation)
    return (round(r * 255), round(g * 255), round(b * 255))


def random_color(rng: np.random.Generator) -> Color:
    """A random, reasonably saturated color drawn from the seeded generator."""
    hue = float(rng.uniform(0.0, 1.0))
    lightness = float(rng.uniform(0.2, 0.8))
    saturation = float(rng.uniform(0.35, 1.0))
    return hls_color(hue, lightness, saturation)


def lerp_color(c0: Color, c1: Color, t: float) -> Color:
    """Linear interpolation between two RGB colors, t in [0, 1]."""
    return tuple(round(a + (b - a) * t) for a, b in zip(c0, c1, strict=True))  # type: ignore[return-value]


def gradient_stops(stops: list[tuple[float, Color]], t: float) -> Color:
    """Sample a multi-stop gradient at position t in [0, 1]."""
    t = min(max(t, 0.0), 1.0)
    for (p0, c0), (p1, c1) in itertools.pairwise(stops):
        if t <= p1:
            span = p1 - p0
            local = 0.0 if span <= 0 else (t - p0) / span
            return lerp_color(c0, c1, local)
    return stops[-1][1]


# ---------------------------------------------------------------------------
# Tile generation
# ---------------------------------------------------------------------------


def _save_tile(arr: np.ndarray, index: int, out_dir: Path) -> None:
    Image.fromarray(arr.astype(np.uint8), "RGB").save(out_dir / f"tile_{index:04d}.png")


def _solid_tiles(out_dir: Path, start: int) -> int:
    """300 solids over a 30-hue x 5-lightness x 2-saturation grid, plus 16 grays."""
    index = start
    for hue in np.linspace(0.0, 1.0, 30, endpoint=False):
        for lightness in (0.15, 0.35, 0.5, 0.65, 0.85):
            for saturation in (0.9, 0.45):
                color = hls_color(float(hue), lightness, saturation)
                arr = np.full((TILE_SIZE, TILE_SIZE, 3), color, dtype=np.uint8)
                _save_tile(arr, index, out_dir)
                index += 1
    for value in np.linspace(8, 247, 16):
        arr = np.full((TILE_SIZE, TILE_SIZE, 3), round(float(value)), dtype=np.uint8)
        _save_tile(arr, index, out_dir)
        index += 1
    return index


def _linear_gradient_tiles(out_dir: Path, start: int, count: int, rng: np.random.Generator) -> int:
    """Random two-color linear gradients at random angles."""
    ys, xs = np.mgrid[0:TILE_SIZE, 0:TILE_SIZE].astype(np.float32) / (TILE_SIZE - 1)
    index = start
    for _ in range(count):
        angle = float(rng.uniform(0.0, np.pi))
        proj = xs * np.cos(angle) + ys * np.sin(angle)
        t = (proj - proj.min()) / max(float(proj.max() - proj.min()), 1e-6)
        c0 = np.array(random_color(rng), dtype=np.float32)
        c1 = np.array(random_color(rng), dtype=np.float32)
        arr = c0[None, None, :] * (1.0 - t[..., None]) + c1[None, None, :] * t[..., None]
        _save_tile(np.clip(arr, 0, 255), index, out_dir)
        index += 1
    return index


def _radial_gradient_tiles(out_dir: Path, start: int, count: int, rng: np.random.Generator) -> int:
    """Random two-color radial gradients with off-center origins."""
    ys, xs = np.mgrid[0:TILE_SIZE, 0:TILE_SIZE].astype(np.float32) / (TILE_SIZE - 1)
    index = start
    for _ in range(count):
        cx = float(rng.uniform(0.2, 0.8))
        cy = float(rng.uniform(0.2, 0.8))
        dist = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
        t = np.clip(dist / max(float(dist.max()), 1e-6), 0.0, 1.0)
        inner = np.array(random_color(rng), dtype=np.float32)
        outer = np.array(random_color(rng), dtype=np.float32)
        arr = inner[None, None, :] * (1.0 - t[..., None]) + outer[None, None, :] * t[..., None]
        _save_tile(np.clip(arr, 0, 255), index, out_dir)
        index += 1
    return index


def _noise_tiles(out_dir: Path, start: int, count: int, rng: np.random.Generator) -> int:
    """Solid base colors with subtle monochrome Gaussian noise texture."""
    index = start
    for _ in range(count):
        base = np.array(random_color(rng), dtype=np.float32)
        sigma = float(rng.uniform(6.0, 18.0))
        noise = rng.normal(0.0, sigma, size=(TILE_SIZE, TILE_SIZE, 1)).astype(np.float32)
        arr = np.clip(base[None, None, :] + noise, 0, 255)
        _save_tile(arr, index, out_dir)
        index += 1
    return index


def make_tiles(out_dir: Path, rng: np.random.Generator) -> int:
    """Generate the full synthetic tile library. Returns the number of tiles."""
    out_dir.mkdir(parents=True, exist_ok=True)
    index = _solid_tiles(out_dir, 0)  # 316 tiles
    index = _linear_gradient_tiles(out_dir, index, 120, rng)
    index = _radial_gradient_tiles(out_dir, index, 80, rng)
    index = _noise_tiles(out_dir, index, 84, rng)
    return index


# ---------------------------------------------------------------------------
# Target scene
# ---------------------------------------------------------------------------

SKY_STOPS: list[tuple[float, Color]] = [
    (0.0, (30, 25, 85)),  # deep dusk blue at the top
    (0.45, (150, 70, 110)),  # purple-pink mid sky
    (0.8, (240, 130, 80)),  # warm orange
    (1.0, (255, 190, 100)),  # bright glow at the horizon
]

WATER_STOPS: list[tuple[float, Color]] = [
    (0.0, (245, 165, 95)),  # horizon glow reflected on the water
    (0.35, (150, 90, 110)),
    (1.0, (25, 35, 80)),  # deep blue in the foreground
]

SUN_CENTER = (560, 290)
SUN_RADIUS = 46


def _ridge(
    rng: np.random.Generator, base_y: float, roughness: float, step: int
) -> list[tuple[int, int]]:
    """A jagged mountain ridge as a seeded random walk across the full width."""
    points: list[tuple[int, int]] = []
    y = base_y
    for x in range(0, TARGET_W + step, step):
        y = float(np.clip(y + rng.uniform(-roughness, roughness), base_y - 60, base_y + 25))
        points.append((min(x, TARGET_W), round(y)))
    return points


def _draw_sky(draw: ImageDraw.ImageDraw) -> None:
    for y in range(HORIZON):
        draw.line([(0, y), (TARGET_W, y)], fill=gradient_stops(SKY_STOPS, y / (HORIZON - 1)))


def _draw_sun(draw: ImageDraw.ImageDraw) -> None:
    cx, cy = SUN_CENTER
    # Soft glow: concentric discs blended toward the local sky color.
    sky_here = gradient_stops(SKY_STOPS, cy / (HORIZON - 1))
    for radius, strength in ((int(SUN_RADIUS * 2.1), 0.25), (int(SUN_RADIUS * 1.45), 0.5)):
        glow = lerp_color(sky_here, (255, 230, 170), strength)
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=glow)
    draw.ellipse(
        (cx - SUN_RADIUS, cy - SUN_RADIUS, cx + SUN_RADIUS, cy + SUN_RADIUS),
        fill=(255, 240, 190),
    )


def _draw_mountains(draw: ImageDraw.ImageDraw, rng: np.random.Generator) -> None:
    far = _ridge(rng, base_y=300.0, roughness=26.0, step=32)
    near = _ridge(rng, base_y=345.0, roughness=20.0, step=24)
    draw.polygon(
        [(0, HORIZON), *far, (TARGET_W, HORIZON)],
        fill=(96, 62, 112),  # hazy dusk purple, far range
    )
    draw.polygon(
        [(0, HORIZON), *near, (TARGET_W, HORIZON)],
        fill=(52, 36, 74),  # darker near range
    )


def _draw_water(draw: ImageDraw.ImageDraw, rng: np.random.Generator) -> None:
    depth = TARGET_H - HORIZON
    for y in range(HORIZON, TARGET_H):
        t = (y - HORIZON) / (depth - 1)
        draw.line([(0, y), (TARGET_W, y)], fill=gradient_stops(WATER_STOPS, t))
    # Sun reflection: a column of bright horizontal bands widening toward the viewer.
    sun_x = SUN_CENTER[0]
    y = HORIZON + 6
    while y < TARGET_H - 10:
        t = (y - HORIZON) / depth
        half_w = 18 + 60 * t + float(rng.uniform(-8, 8))
        offset = float(rng.uniform(-14, 14)) * t
        band_h = 2 + round(3 * t)
        water = gradient_stops(WATER_STOPS, t)
        band = lerp_color(water, (255, 215, 150), 0.75 - 0.35 * t)
        x0 = round(sun_x + offset - half_w)
        x1 = round(sun_x + offset + half_w)
        draw.rectangle((x0, y, x1, y + band_h), fill=band)
        y += band_h + 4 + round(9 * t)
    # General shimmer: faint lighter streaks scattered across the water.
    for _ in range(70):
        sy = int(rng.integers(HORIZON + 4, TARGET_H - 4))
        t = (sy - HORIZON) / depth
        sx = int(rng.integers(0, TARGET_W - 90))
        length = int(rng.integers(25, 90))
        water = gradient_stops(WATER_STOPS, t)
        streak = lerp_color(water, (255, 235, 200), 0.18)
        draw.line([(sx, sy), (sx + length, sy)], fill=streak, width=1)


def make_target(path: Path, rng: np.random.Generator) -> None:
    """Draw the procedural sunset scene and save it as a PNG."""
    img = Image.new("RGB", (TARGET_W, TARGET_H))
    draw = ImageDraw.Draw(img)
    _draw_sky(draw)
    _draw_sun(draw)
    _draw_mountains(draw, rng)
    _draw_water(draw, rng)
    img.save(path)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    examples_dir = Path(__file__).resolve().parent
    tiles_dir = examples_dir / "tiles"
    target_path = examples_dir / "target.png"

    rng = np.random.default_rng(SEED)
    count = make_tiles(tiles_dir, rng)
    print(f"Wrote {count} tiles to {tiles_dir}")
    make_target(target_path, rng)
    print(f"Wrote target scene to {target_path}")

    print()
    print("Now build the demo mosaic with:")
    print(
        "  picmosaic examples/target.png --tiles examples/tiles "
        "--tile-size 16 --upscale 4 --overlay 0.25 --seed 42 "
        "-o examples/demo-mosaic.jpg"
    )


if __name__ == "__main__":
    main()
