"""Tests for the picmosaic CLI contract."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from PIL import Image

import picmosaic
import picmosaic.mosaic
from picmosaic import cli


def _exit_code(argv: list[str]) -> int:
    """Run cli.main and normalize a possible SystemExit (e.g. argparse --version)."""
    try:
        rc = cli.main(argv)
    except SystemExit as exc:  # argparse actions raise SystemExit directly
        rc = exc.code
    if rc is None:
        return 0
    return rc if isinstance(rc, int) else 1


class TestCliErrors:
    def test_missing_target_returns_1_with_stderr(
        self, tmp_path: Path, tile_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = _exit_code([str(tmp_path / "missing.png"), "--tiles", str(tile_dir), "--no-cache"])
        assert rc == 1
        captured = capsys.readouterr()
        assert captured.err.strip() != ""

    def test_empty_tiles_dir_returns_1_with_stderr(
        self, tmp_path: Path, target_quadrants: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        empty = tmp_path / "empty-tiles"
        empty.mkdir()
        rc = _exit_code([str(target_quadrants), "--tiles", str(empty), "--no-cache", "-q"])
        assert rc == 1
        captured = capsys.readouterr()
        assert captured.err.strip() != ""

    def test_keyboard_interrupt_returns_130(
        self,
        tmp_path: Path,
        tile_dir: Path,
        target_quadrants: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def _boom(*args: object, **kwargs: object) -> None:
            raise KeyboardInterrupt

        # Patch every plausible binding site so the test is independent of how
        # cli.py imported build_mosaic.
        for obj in (cli, picmosaic, picmosaic.mosaic):
            if hasattr(obj, "build_mosaic"):
                monkeypatch.setattr(obj, "build_mosaic", _boom)
        rc = _exit_code([str(target_quadrants), "--tiles", str(tile_dir), "--no-cache", "-q"])
        assert rc == 130


class TestCliSuccess:
    def test_end_to_end_writes_output(
        self,
        tmp_path: Path,
        tile_dir: Path,
        target_quadrants: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        out_path = tmp_path / "result.jpg"
        rc = _exit_code(
            [
                str(target_quadrants),
                "--tiles",
                str(tile_dir),
                "-o",
                str(out_path),
                "--tile-size",
                "32",
                "--detail",
                "2",
                "--seed",
                "0",
                "--repeat-radius",
                "0",
                "--color-adjust",
                "0.0",
                "--randomness",
                "0.0",
                "--no-cache",
            ]
        )
        assert rc == 0
        assert out_path.is_file()
        with Image.open(out_path) as img:
            assert img.size == (128, 128)
        captured = capsys.readouterr()
        assert out_path.name in captured.out  # success summary mentions the output

    def test_default_output_name_derived_from_target(
        self, tmp_path: Path, tile_dir: Path, target_quadrants: Path
    ) -> None:
        target = tmp_path / "holiday.png"
        shutil.copy(target_quadrants, target)
        rc = _exit_code(
            [
                str(target),
                "-t",
                str(tile_dir),
                "--tile-size",
                "32",
                "--detail",
                "2",
                "--seed",
                "0",
                "--no-cache",
                "-q",
            ]
        )
        assert rc == 0
        assert (tmp_path / "holiday-mosaic.jpg").is_file()

    def test_version_prints_and_exits_0(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = _exit_code(["--version"])
        assert rc == 0
        captured = capsys.readouterr()
        assert picmosaic.__version__ in captured.out


class TestEntrypoint:
    def test_entrypoint_exits_with_main_code(
        self, tmp_path: Path, tile_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            ["picmosaic", str(tmp_path / "missing.png"), "--tiles", str(tile_dir), "-q"],
        )
        with pytest.raises(SystemExit) as excinfo:
            cli.entrypoint()
        assert excinfo.value.code == 1


class TestOutputPreflight:
    """The output path is validated before any heavy work (regression)."""

    def _target(self, tmp_path: Path) -> Path:
        path = tmp_path / "target.png"
        Image.new("RGB", (32, 32), (200, 60, 60)).save(path)
        return path

    def test_missing_output_dir_fails_fast(
        self, tmp_path: Path, tile_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        target = self._target(tmp_path)
        rc = _exit_code(
            [
                str(target),
                "--tiles",
                str(tile_dir),
                "-o",
                str(tmp_path / "no" / "such" / "dir" / "out.jpg"),
                "--no-cache",
            ]
        )
        assert rc == 1
        assert "output directory does not exist" in capsys.readouterr().err

    def test_output_is_directory_fails_fast(
        self, tmp_path: Path, tile_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        target = self._target(tmp_path)
        rc = _exit_code([str(target), "--tiles", str(tile_dir), "-o", str(tmp_path), "--no-cache"])
        assert rc == 1
        assert "is a directory" in capsys.readouterr().err

    def test_unknown_output_extension_fails_fast(
        self, tmp_path: Path, tile_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        target = self._target(tmp_path)
        rc = _exit_code(
            [
                str(target),
                "--tiles",
                str(tile_dir),
                "-o",
                str(tmp_path / "out.xyz"),
                "--no-cache",
            ]
        )
        assert rc == 1
        assert "unsupported output image format" in capsys.readouterr().err


def test_readonly_output_format_fails_fast(
    tmp_path: Path, tile_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Formats Pillow can read but not save must be rejected in the preflight."""
    Image.init()
    readonly = next(
        (e for e, f in sorted(Image.registered_extensions().items()) if f not in Image.SAVE),
        None,
    )
    if readonly is None:
        pytest.skip("this Pillow build can save every registered format")
    target = tmp_path / "t.png"
    Image.new("RGB", (32, 32), (10, 200, 30)).save(target)
    rc = _exit_code(
        [
            str(target),
            "--tiles",
            str(tile_dir),
            "-o",
            str(tmp_path / f"out{readonly}"),
            "--no-cache",
        ]
    )
    assert rc == 1
    assert "unsupported output image format" in capsys.readouterr().err
