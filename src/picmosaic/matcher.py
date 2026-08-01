"""Nearest-tile matching with a k-d tree (scipy) or a numpy fallback."""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

try:  # Optional accelerator; the numpy fallback matches it (see TileMatcher docs).
    from scipy.spatial import cKDTree as _cKDTree
except ImportError:  # pragma: no cover - depends on the environment
    _cKDTree = None

#: Maximum number of (query, tile) distance-matrix entries per fallback chunk.
_CHUNK_PAIRS = 4_000_000
#: Hard cap on query rows per fallback chunk (bounds the exact-recompute buffer).
_MAX_CHUNK_ROWS = 16_384


class TileMatcher:
    """K-nearest-neighbor tile matcher with variety constraints.

    Uses ``scipy.spatial.cKDTree`` when scipy is importable, otherwise a
    chunked numpy brute-force fallback. Both backends break distance ties by
    ascending tile index and produce identical results, except in the corner
    case where several exactly equidistant tiles straddle the ``candidates``
    cutoff (e.g. duplicate tile files) — the backends may then admit a
    different member of the tied set.
    """

    def __init__(self, features: np.ndarray, *, seed: int | None = None) -> None:
        """Initialize the matcher.

        Args:
            features: ``(N, D)`` float32 tile feature matrix.
            seed: Seed for the internal random generator; ``None`` for
                nondeterministic behavior.

        Raises:
            ValueError: If ``features`` is not a non-empty 2-D array.
        """
        feats = np.asarray(features, dtype=np.float32)
        if feats.ndim != 2 or feats.shape[0] < 1:
            raise ValueError(f"features must be a non-empty (N, D) array, got shape {feats.shape}")
        self._features = feats
        self._n, self._dim = feats.shape
        self._rng = np.random.default_rng(seed)
        if _cKDTree is not None:
            self._tree = _cKDTree(feats)
            logger.debug("TileMatcher using scipy cKDTree (%d tiles)", self._n)
        else:
            self._tree = None
            self._features64 = feats.astype(np.float64)
            self._feat_sq = np.einsum("ij,ij->i", self._features64, self._features64)
            logger.debug("TileMatcher using numpy fallback (%d tiles)", self._n)

    def query(self, q: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
        """Find the ``k`` nearest tiles for each query vector.

        Args:
            q: ``(M, D)`` query feature matrix (a single ``(D,)`` vector is
                also accepted).
            k: Number of neighbors; clamped to the library size ``N``.

        Returns:
            Tuple ``(distances, indices)`` of shapes ``(M, k)`` -- Euclidean
            distances (float64) and tile indices, sorted ascending by
            distance.
        """
        q_arr = np.asarray(q, dtype=np.float64)
        if q_arr.ndim == 1:
            q_arr = q_arr[None, :]
        if q_arr.ndim != 2 or q_arr.shape[1] != self._dim:
            raise ValueError(f"q must have shape (M, {self._dim}), got {q_arr.shape}")
        k = max(1, min(k, self._n))
        m = q_arr.shape[0]
        if self._tree is not None:
            dist, idx = self._tree.query(q_arr, k=k)
            dist = np.asarray(dist, dtype=np.float64).reshape(m, k)
            idx = np.asarray(idx, dtype=np.int64).reshape(m, k)
            # Normalize tie order (ascending index on equal distances) so the
            # scipy and numpy backends return identical results.
            order = np.lexsort((idx, dist))
            return (
                np.take_along_axis(dist, order, axis=1),
                np.take_along_axis(idx, order, axis=1),
            )
        return self._query_fallback(q_arr, k)

    def _query_fallback(self, q: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
        """Chunked brute-force k-NN bounded to ``_CHUNK_PAIRS`` matrix entries."""
        feats = self._features64
        n, m = self._n, q.shape[0]
        distances = np.empty((m, k), dtype=np.float64)
        indices = np.empty((m, k), dtype=np.int64)
        chunk = max(1, min(_CHUNK_PAIRS // n, _MAX_CHUNK_ROWS))
        for start in range(0, m, chunk):
            qc = q[start : start + chunk]
            q_sq = np.einsum("ij,ij->i", qc, qc)
            # Squared distances via the norm expansion (one (chunk, N) matrix).
            d2 = q_sq[:, None] + self._feat_sq[None, :] - 2.0 * (qc @ feats.T)
            np.maximum(d2, 0.0, out=d2)
            if k < n:
                part = np.argpartition(d2, k - 1, axis=1)[:, :k]
            else:
                part = np.broadcast_to(np.arange(n), (qc.shape[0], n)).copy()
            # Recompute exact squared distances for the selected candidates so
            # results match scipy's direct computation bit-for-bit in practice.
            diff = qc[:, None, :] - feats[part]
            d2_exact = np.einsum("ckd,ckd->ck", diff, diff)
            order = np.lexsort((part, d2_exact))  # primary: distance, secondary: index
            indices[start : start + chunk] = np.take_along_axis(part, order, axis=1)
            distances[start : start + chunk] = np.sqrt(np.take_along_axis(d2_exact, order, axis=1))
        return distances, indices

    def match_grid(
        self,
        target_features: np.ndarray,
        *,
        repeat_radius: int = 1,
        max_usage: int | None = None,
        candidates: int = 12,
        randomness: float = 0.0,
    ) -> np.ndarray:
        """Assign one tile index to every grid cell under variety constraints.

        Cells are visited in a seeded random permutation (avoiding raster-scan
        bias). Each cell picks the best-ranked of its ``candidates`` nearest
        tiles that (a) is not already assigned within Chebyshev distance
        ``repeat_radius`` on the grid and (b) has been used fewer than
        ``max_usage`` times. If no candidate qualifies, the candidate with the
        lowest usage count (ties broken by better rank) is taken, so every
        cell is always assigned. With ``randomness > 0`` a qualifying
        candidate is sampled with inverse-distance weights
        ``w_i = (1 / (dist_i + 1e-6)) ** (1 / randomness)`` instead of always
        taking the best one.

        Args:
            target_features: ``(rows, cols, D)`` target cell features.
            repeat_radius: Chebyshev no-repeat radius; ``0`` disables.
            max_usage: Maximum placements per tile; ``None`` disables.
            candidates: Nearest neighbors considered per cell (>= 1).
            randomness: 0..1; 0 picks the best match, 1 is full
                inverse-distance sampling.

        Returns:
            ``(rows, cols)`` int32 array of tile indices. Deterministic for a
            given seed.

        Raises:
            ValueError: On bad shapes or out-of-range parameters.
        """
        tf = np.asarray(target_features, dtype=np.float32)
        if tf.ndim != 3 or tf.shape[2] != self._dim:
            raise ValueError(
                f"target_features must have shape (rows, cols, {self._dim}), got {tf.shape}"
            )
        if repeat_radius < 0:
            raise ValueError(f"repeat_radius must be >= 0, got {repeat_radius}")
        if max_usage is not None and max_usage < 1:
            raise ValueError(f"max_usage must be None or >= 1, got {max_usage}")
        if candidates < 1:
            raise ValueError(f"candidates must be >= 1, got {candidates}")
        if not 0.0 <= randomness <= 1.0:
            raise ValueError(f"randomness must be in [0, 1], got {randomness}")

        rows, cols = tf.shape[:2]
        m = rows * cols
        k = min(candidates, self._n)
        dist, idx = self.query(tf.reshape(m, self._dim), k)

        assignment = np.full((rows, cols), -1, dtype=np.int32)
        usage = np.zeros(self._n, dtype=np.int64)
        visit_order = self._rng.permutation(m)
        fallback_cells = 0

        for cell in visit_order:
            r, c = divmod(int(cell), cols)
            cand_idx = idx[cell]
            mask = np.ones(k, dtype=bool)
            if repeat_radius > 0:
                r0, r1 = max(0, r - repeat_radius), min(rows, r + repeat_radius + 1)
                c0, c1 = max(0, c - repeat_radius), min(cols, c + repeat_radius + 1)
                neighborhood = assignment[r0:r1, c0:c1]
                forbidden = neighborhood[neighborhood >= 0]
                if forbidden.size:
                    mask &= ~np.isin(cand_idx, forbidden)
            if max_usage is not None:
                mask &= usage[cand_idx] < max_usage
            qualifying = np.nonzero(mask)[0]
            if qualifying.size:
                if randomness <= 0.0 or qualifying.size == 1:
                    pick = qualifying[0]
                else:
                    pick = qualifying[self._sample_index(dist[cell][qualifying], randomness)]
                choice = int(cand_idx[pick])
            else:
                # Nothing qualifies: least-used candidate, ties -> better rank
                # (argmin returns the first minimum; cand_idx is rank-ordered).
                choice = int(cand_idx[int(np.argmin(usage[cand_idx]))])
                fallback_cells += 1
            assignment[r, c] = choice
            usage[choice] += 1
        if fallback_cells:
            logger.warning(
                "Variety constraints (repeat radius / max usage) could not be honored "
                "for %d of %d cells; the tile library is too small for these settings.",
                fallback_cells,
                m,
            )
        return assignment

    def _sample_index(self, dists: np.ndarray, randomness: float) -> int:
        """Sample a position with weights ``(1 / (d + 1e-6)) ** (1 / randomness)``.

        Computed in log space to avoid overflow; falls back to the best-ranked
        position (0) when every weight is zero or non-finite.
        """
        with np.errstate(divide="ignore", invalid="ignore"):
            log_w = -(1.0 / randomness) * np.log(dists + 1e-6)
        finite = np.isfinite(log_w)
        if not finite.any():
            return 0
        log_w = np.where(finite, log_w, -np.inf)
        weights = np.exp(log_w - log_w[finite].max())
        total = weights.sum()
        if not np.isfinite(total) or total <= 0.0:
            return 0
        return int(self._rng.choice(weights.size, p=weights / total))
