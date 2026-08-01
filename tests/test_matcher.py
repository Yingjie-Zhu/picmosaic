"""Tests for picmosaic.matcher: k-NN queries and constrained grid assignment."""

from __future__ import annotations

import numpy as np
import pytest
from numpy.testing import assert_allclose, assert_array_equal

import picmosaic.matcher as matcher_mod
from picmosaic.matcher import TileMatcher


def _random_features(n: int, d: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.random((n, d)) * 100.0).astype(np.float32)


class TestQuery:
    def test_exact_nearest_trivial(self) -> None:
        feats = np.array([[0.0, 0.0], [3.0, 4.0], [10.0, 0.0]], dtype=np.float32)
        matcher = TileMatcher(feats, seed=0)
        dist, idx = matcher.query(np.array([[0.0, 1.0]]), k=2)
        assert idx.shape == (1, 2)
        assert_array_equal(idx[0], [0, 1])
        assert_allclose(dist[0], [1.0, np.sqrt(18.0)], atol=1e-6)

    def test_distances_sorted_ascending(self) -> None:
        feats = _random_features(30, 6, seed=2)
        q = _random_features(10, 6, seed=3)
        dist, _ = TileMatcher(feats, seed=0).query(q, k=5)
        assert (np.diff(dist, axis=1) >= -1e-9).all()

    def test_k_clamped_to_library_size(self) -> None:
        feats = _random_features(3, 4, seed=1)
        dist, idx = TileMatcher(feats, seed=0).query(_random_features(5, 4, seed=2), k=10)
        assert dist.shape == (5, 3)
        assert idx.shape == (5, 3)

    def test_accepts_single_vector(self) -> None:
        feats = _random_features(8, 4, seed=1)
        dist, idx = TileMatcher(feats, seed=0).query(feats[0], k=1)
        assert dist.shape == (1, 1)
        assert int(idx[0, 0]) == 0
        assert float(dist[0, 0]) == pytest.approx(0.0, abs=1e-6)

    def test_bad_query_shape_raises(self) -> None:
        matcher = TileMatcher(_random_features(5, 4, seed=1), seed=0)
        with pytest.raises(ValueError):
            matcher.query(np.zeros((2, 7)), k=1)


class TestFallbackEquivalence:
    def test_query_matches_default_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        feats = _random_features(40, 12, seed=7)
        q = _random_features(25, 12, seed=8).astype(np.float64)

        default = TileMatcher(feats, seed=0)
        d1, i1 = default.query(q, k=5)

        monkeypatch.setattr(matcher_mod, "_cKDTree", None)
        fallback = TileMatcher(feats, seed=0)
        assert fallback._tree is None  # the fallback path is really active
        d2, i2 = fallback.query(q, k=5)

        assert_array_equal(i1, i2)
        assert_allclose(d1, d2, rtol=1e-6, atol=1e-6)

    def test_chunked_fallback_matches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        feats = _random_features(40, 12, seed=7)
        q = _random_features(25, 12, seed=8)

        reference = TileMatcher(feats, seed=0).query(q, k=4)

        monkeypatch.setattr(matcher_mod, "_cKDTree", None)
        monkeypatch.setattr(matcher_mod, "_CHUNK_PAIRS", 50)  # force many tiny chunks
        chunked = TileMatcher(feats, seed=0).query(q, k=4)

        assert_array_equal(reference[1], chunked[1])
        assert_allclose(reference[0], chunked[0], rtol=1e-6, atol=1e-6)

    def test_match_grid_identical_assignments(self, monkeypatch: pytest.MonkeyPatch) -> None:
        feats = _random_features(40, 6, seed=11)
        target = (np.random.default_rng(12).random((6, 7, 6)) * 100.0).astype(np.float32)
        kwargs = {"repeat_radius": 1, "max_usage": 3, "candidates": 8, "randomness": 0.0}

        a_default = TileMatcher(feats, seed=99).match_grid(target, **kwargs)

        monkeypatch.setattr(matcher_mod, "_cKDTree", None)
        a_fallback = TileMatcher(feats, seed=99).match_grid(target, **kwargs)

        assert_array_equal(a_default, a_fallback)


class TestMatchGrid:
    def test_repeat_radius_prevents_adjacent_duplicates(self) -> None:
        feats = np.arange(30, dtype=np.float32)[:, None]  # 30 distinct tiles
        matcher = TileMatcher(feats, seed=1)
        target = np.zeros((5, 5, 1), dtype=np.float32)  # uniform target
        grid = matcher.match_grid(target, repeat_radius=1, candidates=12)
        assert grid.shape == (5, 5)
        assert grid.dtype == np.int32
        for r in range(5):
            for c in range(5):
                for dr in (-1, 0, 1):
                    for dc in (-1, 0, 1):
                        if dr == 0 and dc == 0:
                            continue
                        rr, cc = r + dr, c + dc
                        if 0 <= rr < 5 and 0 <= cc < 5:
                            assert grid[r, c] != grid[rr, cc]

    def test_max_usage_one_gives_unique_assignments(self) -> None:
        feats = np.arange(30, dtype=np.float32)[:, None]
        matcher = TileMatcher(feats, seed=2)
        target = np.zeros((4, 4, 1), dtype=np.float32)
        grid = matcher.match_grid(target, repeat_radius=0, max_usage=1, candidates=20)
        values = grid.ravel().tolist()
        assert len(set(values)) == 16  # every cell got a distinct tile

    def test_always_assigns_when_no_candidate_qualifies(self) -> None:
        feats = np.array([[0.0], [1.0]], dtype=np.float32)  # only 2 tiles
        matcher = TileMatcher(feats, seed=3)
        target = np.zeros((3, 3, 1), dtype=np.float32)
        grid = matcher.match_grid(target, repeat_radius=0, max_usage=1, candidates=5)
        assert (grid >= 0).all()
        assert set(grid.ravel().tolist()) <= {0, 1}

    def test_seed_determinism_with_randomness(self) -> None:
        feats = _random_features(40, 6, seed=5)
        target = (np.random.default_rng(6).random((6, 6, 6)) * 100.0).astype(np.float32)
        kwargs = {"repeat_radius": 1, "candidates": 8, "randomness": 0.8}
        a1 = TileMatcher(feats, seed=42).match_grid(target, **kwargs)
        a2 = TileMatcher(feats, seed=42).match_grid(target, **kwargs)
        assert_array_equal(a1, a2)

    def test_randomness_zero_picks_best(self) -> None:
        feats = np.arange(10, dtype=np.float32)[:, None]
        matcher = TileMatcher(feats, seed=0)
        target = np.zeros((1, 1, 1), dtype=np.float32)
        grid = matcher.match_grid(target, repeat_radius=0, candidates=5, randomness=0.0)
        assert int(grid[0, 0]) == 0  # nearest tile to feature 0 is tile 0

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"repeat_radius": -1},
            {"max_usage": 0},
            {"candidates": 0},
            {"randomness": -0.1},
            {"randomness": 1.5},
        ],
    )
    def test_invalid_parameters_raise(self, kwargs: dict) -> None:
        matcher = TileMatcher(_random_features(5, 2, seed=1), seed=0)
        with pytest.raises(ValueError):
            matcher.match_grid(np.zeros((2, 2, 2), dtype=np.float32), **kwargs)

    def test_bad_target_shape_raises(self) -> None:
        matcher = TileMatcher(_random_features(5, 2, seed=1), seed=0)
        with pytest.raises(ValueError):
            matcher.match_grid(np.zeros((2, 2, 3), dtype=np.float32))


class TestConstructor:
    def test_empty_features_raise(self) -> None:
        with pytest.raises(ValueError):
            TileMatcher(np.zeros((0, 3), dtype=np.float32))

    def test_one_dimensional_features_raise(self) -> None:
        with pytest.raises(ValueError):
            TileMatcher(np.zeros(5, dtype=np.float32))
