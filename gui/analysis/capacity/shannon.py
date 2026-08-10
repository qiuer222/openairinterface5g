"""Shannon MIMO capacity using a common fixed noise power."""

from __future__ import annotations

from typing import Iterable, Sequence

import numpy as np

from gui.analysis.csi_parser import valid_subcarrier_indices


def shannon_capacity_from_eigenvalue_matrix(
    eigenvalue_matrix: np.ndarray,
    noise_power: float = 1.0,
    tx_count: int | None = None,
) -> float:
    """Average capacity over subcarriers from an ``(N, rank)`` eigenvalue matrix."""
    arr = np.asarray(eigenvalue_matrix, dtype=float)
    if arr.ndim != 2 or arr.shape[0] == 0:
        return float("nan")
    if noise_power <= 0:
        raise ValueError("noise_power must be positive")
    if tx_count is None:
        tx_count = max(arr.shape[1], 1)
    if tx_count <= 0:
        return float("nan")
    per_subcarrier = np.sum(
        np.log2(1.0 + np.maximum(arr, 0.0) / (float(tx_count) * float(noise_power))),
        axis=1,
    )
    return float(np.mean(per_subcarrier))


def shannon_capacity_from_eigenvalues(
    eigenvalues_by_subcarrier: Sequence[np.ndarray],
    noise_power: float = 1.0,
    tx_count: int | None = None,
) -> float:
    """Average capacity over subcarriers: ``sum log2(1 + lambda/(N_t N0))``."""
    if len(eigenvalues_by_subcarrier) == 0:
        return float("nan")
    if noise_power <= 0:
        raise ValueError("noise_power must be positive")
    rows = [np.asarray(eig, dtype=float) for eig in eigenvalues_by_subcarrier]
    rows = [row for row in rows if row.size > 0 and np.all(np.isfinite(row))]
    if not rows:
        return float("nan")
    arr = np.vstack(rows)
    if tx_count is None:
        tx_count = max(arr.shape[1], 1)
    if tx_count <= 0:
        return float("nan")
    per_subcarrier = np.sum(
        np.log2(1.0 + np.maximum(arr, 0.0) / (float(tx_count) * float(noise_power))),
        axis=1,
    )
    return float(np.mean(per_subcarrier))


def shannon_capacity(h: np.ndarray, noise_power: float = 1.0) -> float:
    """Compute Shannon capacity directly from a channel array."""
    valid = valid_subcarrier_indices(h)
    eigenvalues = []
    for k in valid:
        gram = h[:, :, k] @ h[:, :, k].conj().T
        eigenvalues.append(np.linalg.eigvalsh(gram)[::-1])
    return shannon_capacity_from_eigenvalues(
        eigenvalues,
        noise_power=noise_power,
        tx_count=h.shape[1],
    )
