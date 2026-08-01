"""Download free, openly-licensed demo assets for the quickstart.

Fetches:
- ``examples/target.jpg`` — Vincent van Gogh, *The Starry Night* (1889),
  public domain, via Wikimedia Commons.
- ``examples/tiles/`` — 400 small photos served by Lorem Picsum
  (https://picsum.photos, sourced from Unsplash; free to use).

Requires network access. For a fully offline demo use
``python examples/make_demo_assets.py`` instead. Only the standard library
is used, so the script works even before picmosaic is installed.
"""

from __future__ import annotations

import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

TARGET_URL = (
    "https://upload.wikimedia.org/wikipedia/commons/thumb/e/ea/"
    "Van_Gogh_-_Starry_Night_-_Google_Art_Project.jpg/"
    "1920px-Van_Gogh_-_Starry_Night_-_Google_Art_Project.jpg"
)
TILE_URL = "https://picsum.photos/seed/pm{i}/128/128.jpg"
TILE_COUNT = 400
USER_AGENT = "picmosaic-demo-fetcher/1.0 (https://github.com/Yingjie-Zhu/picmosaic)"


def _fetch(url: str, dest: Path) -> bool:
    if dest.exists() and dest.stat().st_size > 1000:
        return True
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            data = resp.read()
    except OSError as exc:
        print(f"  failed: {dest.name} ({exc})", file=sys.stderr)
        return False
    if len(data) < 1000 or data.lstrip()[:1] == b"<":
        print(f"  failed: {dest.name} (unexpected response)", file=sys.stderr)
        return False
    dest.write_bytes(data)
    return True


def main() -> int:
    root = Path(__file__).resolve().parent
    tiles_dir = root / "tiles"
    tiles_dir.mkdir(exist_ok=True)

    print("Downloading target: The Starry Night (public domain, Wikimedia Commons)...")
    if not _fetch(TARGET_URL, root / "target.jpg"):
        print("Could not download the target image. Check your connection.", file=sys.stderr)
        return 1

    print(f"Downloading {TILE_COUNT} tile photos from picsum.photos...")
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(
            pool.map(
                lambda i: _fetch(TILE_URL.format(i=i), tiles_dir / f"tile{i:03d}.jpg"),
                range(1, TILE_COUNT + 1),
            )
        )
    ok = sum(results)
    print(f"Done: {ok}/{TILE_COUNT} tiles in {tiles_dir}")
    if ok < 100:
        print("Fewer than 100 tiles downloaded — expect a poor-looking mosaic.", file=sys.stderr)
        return 1

    print("\nNow build the demo mosaic with:")
    print(
        "  picmosaic examples/target.jpg --tiles examples/tiles "
        "--tile-size 16 --upscale 2 --overlay 0.25 --color-adjust 0.45 -o demo-mosaic.jpg"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
