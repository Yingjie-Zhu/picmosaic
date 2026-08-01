# Contributing to PicMosaic

Thanks for your interest in improving PicMosaic! This guide covers everything you need
to set up a development environment and land a pull request smoothly.

## Development setup

Requires Python 3.10+.

Using [uv](https://docs.astral.sh/uv/) (recommended):

```bash
git clone https://github.com/Yingjie-Zhu/picmosaic.git
cd picmosaic
uv venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
uv pip install -e ".[dev,fast]"
```

Or with plain pip:

```bash
git clone https://github.com/Yingjie-Zhu/picmosaic.git
cd picmosaic
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev,fast]"
```

Then install the git hooks:

```bash
pre-commit install
```

The hooks run ruff (lint + format) and basic hygiene checks on every commit.

## Quality checks

Run these locally before pushing — CI runs the same commands:

```bash
ruff check .               # lint
ruff format --check .      # formatting (ruff format . to fix)
mypy src                   # type checking
pytest                     # test suite
pytest --cov=picmosaic --cov-report=term-missing   # with coverage
```

Project conventions:

- Python >= 3.10, full type hints on public functions, `from __future__ import annotations`.
- Google-style docstrings on public API; English code, comments, and docstrings.
- Line length 100 (enforced by ruff).
- No `print()` in library code — use `logging` (`logging.getLogger(__name__)` per module).
  The CLI's user-facing output is the only exception.
- Runtime dependencies are limited to `numpy`, `Pillow`, and `tqdm`. SciPy is an optional
  extra and must be imported lazily with graceful degradation. The browser app
  (`docs/index.html`) must stay a single self-contained file with zero external requests.
- Use `pathlib` for all paths (the project must handle non-ASCII paths on Windows).
- Randomized behavior must be deterministic given a seed.

## Making changes

1. Fork the repository and create a branch from `main`:
   `git checkout -b fix/short-description`.
2. Make your change. Add or update tests for any behavior change — the test suite is fast
   and runs offline, and new features need coverage.
3. Update documentation (`README.md`, and `README.zh-CN.md` if user-facing) and add a
   bullet under `## [Unreleased]` in `CHANGELOG.md`.
4. Ensure all quality checks above pass.
5. Open a pull request using the PR template. Keep PRs focused — one logical change per
   PR reviews much faster than a grab bag.

### Commit messages

Use a short imperative summary line (<= 72 chars), e.g. `Fix cache invalidation on mtime change`.
Add a body when the "why" is not obvious from the diff.

### Reporting bugs and requesting features

Please use the [issue forms](https://github.com/Yingjie-Zhu/picmosaic/issues/new/choose).
For security issues, do **not** open a public issue — see [SECURITY.md](SECURITY.md).

## Release checklist (maintainers)

1. Ensure `main` is green in CI.
2. Bump `__version__` in `src/picmosaic/__init__.py` (the single source of truth;
   `pyproject.toml` reads it dynamically).
3. Move the `## [Unreleased]` entries in `CHANGELOG.md` into a new
   `## [X.Y.Z] - YYYY-MM-DD` section.
4. Commit: `git commit -am "Release X.Y.Z"`.
5. Tag: `git tag -a vX.Y.Z -m "picmosaic X.Y.Z"` and `git push origin main vX.Y.Z`.
6. Create a GitHub release from the tag, pasting the changelog section — publishing the
   release triggers the PyPI publish workflow (Trusted Publishing).
7. Verify the new version appears on PyPI and `pip install picmosaic==X.Y.Z` works.
8. After the first PyPI release, add the PyPI version badge to both READMEs:
   `[![PyPI](https://img.shields.io/pypi/v/picmosaic.svg)](https://pypi.org/project/picmosaic/)`

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md).
By participating, you agree to uphold it.
