"""Zero-forcing capacity estimates for recorded CSI channel matrices."""

from __future__ import annotations

from typing import Dict, List

import numpy as np

from gui.analysis.capacity import singular_values_by_subcarrier


EPS = 1e-12


def _zf_capacity_from_singular_values(svals: np.ndarray, snr_db: float, streams: int) -> float:
    if len(svals) == 0 or streams < 1:
        return 0.0
    snr_lin = 10.0 ** (snr_db / 10.0)
    selected = svals[:, :streams]
    good = selected.min(axis=1) > EPS
    if not np.any(good):
        return 0.0
    trace_inverse = np.sum(1.0 / selected[good] ** 2, axis=1)
    sinr = snr_lin / (streams * trace_inverse)
    return float(np.mean(streams * np.log2(1.0 + sinr)))


def best_stream_zf_capacity(h: np.ndarray, snr_db: float) -> Dict:
    """Choose the stream count maximizing equal-power ZF capacity."""
    max_streams = min(h.shape[0], h.shape[1])
    svals, valid = singular_values_by_subcarrier(h)
    per_stream: List[float] = []
    for r in range(1, max_streams + 1):
        per_stream.append(_zf_capacity_from_singular_values(svals, snr_db, r))

    best_index = int(np.argmax(per_stream))
    best_capacity = float(per_stream[best_index])
    return {
        "best_zf_capacity": best_capacity,
        "optimal_stream_number_zf": best_index + 1,
        "zf_capacity_per_stream": per_stream,
    }
