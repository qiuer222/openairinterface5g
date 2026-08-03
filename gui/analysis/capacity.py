"""SVD-based capacity metrics for recorded CSI channel matrices."""

from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np


EPS = 1e-12


def valid_subcarrier_indices(h: np.ndarray, energy_threshold: float = EPS) -> np.ndarray:
    """Return subcarrier indices whose H slice has non-zero energy."""
    if h.ndim != 3:
        raise ValueError(f"expected channel shape (rx, tx, fft), got {h.shape}")
    energy = np.sum(np.abs(h) ** 2, axis=(0, 1))
    return np.flatnonzero(energy > energy_threshold)


def singular_values_by_subcarrier(h: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Return an (n_valid, min(rx,tx)) matrix of singular values."""
    valid = valid_subcarrier_indices(h)
    max_rank = min(h.shape[0], h.shape[1])
    svals = np.zeros((len(valid), max_rank), dtype=float)
    for i, k in enumerate(valid):
        svals[i] = np.linalg.svd(h[:, :, k], compute_uv=False)
    return svals, valid


def _capacity_from_singular_values(svals: np.ndarray, snr_db: float, streams: int) -> float:
    if len(svals) == 0 or streams < 1:
        return 0.0
    snr_lin = 10.0 ** (snr_db / 10.0)
    per_stream_snr = snr_lin / streams
    capacity = np.sum(np.log2(1.0 + per_stream_snr * svals[:, :streams] ** 2), axis=1)
    return float(np.mean(capacity))


def average_svd_capacity(h: np.ndarray, snr_db: float, total_streams: int | None = None) -> Dict:
    """Average equal-power SVD capacity using all available spatial streams."""
    max_streams = min(h.shape[0], h.shape[1])
    streams = total_streams or max_streams
    svals, valid = singular_values_by_subcarrier(h)
    return {
        "average_svd_capacity": _capacity_from_singular_values(svals, snr_db, streams),
        "valid_subcarriers": int(len(valid)),
    }


def best_stream_svd_capacity(h: np.ndarray, snr_db: float) -> Dict:
    """Choose the stream count maximizing average SVD capacity."""
    max_streams = min(h.shape[0], h.shape[1])
    svals, valid = singular_values_by_subcarrier(h)
    per_stream: List[float] = []
    for r in range(1, max_streams + 1):
        per_stream.append(_capacity_from_singular_values(svals, snr_db, r))

    best_index = int(np.argmax(per_stream))
    best_capacity = float(per_stream[best_index])
    best_r = best_index + 1

    if len(svals) == 0:
        cond = 0.0
        rank = 0
    else:
        denom = np.maximum(svals[:, -1], EPS)
        cond = float(np.mean(svals[:, 0] / denom))
        rank = int(round(float(np.mean(np.sum(svals > np.maximum(EPS, svals[:, :1] * 1e-3), axis=1)))))

    return {
        "best_svd_capacity": best_capacity,
        "optimal_stream_number_svd": best_r,
        "svd_capacity_per_stream": per_stream,
        "svd_condition_number": cond,
        "svd_rank": rank,
    }

