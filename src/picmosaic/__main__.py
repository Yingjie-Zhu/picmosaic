"""Allow running the CLI as ``python -m picmosaic``."""

from __future__ import annotations

from .cli import entrypoint

if __name__ == "__main__":
    entrypoint()
