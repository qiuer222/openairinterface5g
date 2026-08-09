"""SVD precoding capacity with one stream count across the whole bandwidth."""

from __future__ import annotations

from typing import List, Sequence

import numpy as np

from gui.analysis.csi_parser import valid_subcarrier_indices


def svd_capacities_from_eigenvalues(
    eigenvalues_by_subcarrier: Sequence[np.ndarray],
    noise_power: float = 1.0,
    max_streams: int | None = None,
) -> List[float]:
    """Return average SVD capacity for K = 1..max_streams."""
    if len(eigenvalues_by_subcarrier) == 0:
        return []
    if noise_power <= 0:
        raise ValueError("noise_power must be positive")
    rows = [np.asarray(eig, dtype=float) for eig in eigenvalues_by_subcarrier]
    rows = [row for row in rows if row.size > 0 and np.all(np.isfinite(row))]
    if not rows:
        return []
    arr = np.vstack(rows)
    if max_streams is None:
        max_streams = arr.shape[1]
    max_streams = min(max_streams, arr.shape[1])

    capacities: List[float] = []
    for k in range(1, max_streams + 1):
        per_subcarrier = np.sum(
            np.log2(1.0 + np.maximum(arr[:, :k], 0.0) / (float(k) * float(noise_power))),
            axis=1,
        )
        capacities.append(float(np.mean(per_subcarrier)))
    return capacities


def svd_capacity(h: np.ndarray, noise_power: float = 1.0) -> dict:
    """Compute per-K SVD capacity and the best stream count for one channel."""
    valid = valid_subcarrier_indices(h)
    eigenvalues = []
    for k in valid:
        gram = h[:, :, k] @ h[:, :, k].conj().T
        eigenvalues.append(np.linalg.eigvalsh(gram)[::-1])
    capacities = svd_capacities_from_eigenvalues(
        eigenvalues,
        noise_power=noise_power,
        max_streams=min(h.shape[:2]),
    )
    if not capacities:
        return {
            "capacities": [],
            "capacity": float("nan"),
            "optimal_streams": int(0),
        }
    best_index = int(np.argmax(capacities))
    return {
        "capacities": capacities,
        "capacity": capacities[best_index],
        "optimal_streams": best_index + 1,
    }
