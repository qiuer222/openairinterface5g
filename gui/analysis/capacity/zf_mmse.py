"""ZF precoding with an MMSE receiver and trace-one transmit precoder."""

from __future__ import annotations

from typing import List, Sequence, Tuple

import numpy as np

from gui.analysis.csi_parser import valid_subcarrier_indices


EPS = 1e-12


def zf_mmse_subcarrier_from_svd(
    u: np.ndarray,
    s: np.ndarray,
    vh: np.ndarray,
    k: int,
    noise_power: float = 1.0,
) -> Tuple[float, np.ndarray, float]:
    """Return ``(capacity, SINRs padded to 4, trace(W W^H))`` for one subcarrier."""
    max_rank = min(len(s), u.shape[1], vh.shape[0])
    if k > max_rank or k < 1 or noise_power <= 0:
        return float("nan"), np.zeros(4), float("nan")
    if np.min(s[:k]) <= EPS or not np.all(np.isfinite(s[:k])):
        return float("nan"), np.zeros(4), float("nan")

    v_k = vh[:k].conj().T
    w_raw = v_k / s[:k]
    trace_raw = float(np.sum(np.abs(w_raw) ** 2))
    if not np.isfinite(trace_raw) or trace_raw <= 0:
        return float("nan"), np.zeros(4), float("nan")

    scale = np.sqrt(1.0 / trace_raw)
    w = w_raw * scale
    trace_w = float(np.sum(np.abs(w) ** 2))
    h_eq = u[:, :k] * scale

    gram = h_eq.conj().T @ h_eq + float(noise_power) * np.eye(k)
    try:
        g = np.linalg.solve(gram, h_eq.conj().T)
    except np.linalg.LinAlgError:
        return float("nan"), np.zeros(4), trace_w
    r = g @ h_eq

    signal = np.abs(np.diag(r)) ** 2
    interference = np.sum(np.abs(r) ** 2, axis=1) - signal
    noise = float(noise_power) * np.sum(np.abs(g) ** 2, axis=1)
    denom = interference + noise
    sinr = np.where(denom > 0, signal / denom, 0.0)
    capacity = float(np.sum(np.log2(1.0 + np.maximum(sinr, 0.0))))
    padded = np.zeros(4)
    padded[:k] = sinr
    return capacity, padded, trace_w


def aggregate_zf_mmse_from_svd(
    svd_by_subcarrier: Sequence[Tuple[np.ndarray, np.ndarray, np.ndarray]],
    noise_power: float = 1.0,
    max_streams: int = 4,
) -> dict:
    """Aggregate ZF+MMSE capacities and SINRs across valid subcarriers."""
    capacities = np.full(max_streams, np.nan)
    sinr_matrix = np.full((max_streams, 4), np.nan)
    counts = np.zeros(max_streams, dtype=int)
    max_trace_error = 0.0
    max_sinr_negative_error = 0.0

    for u, s, vh in svd_by_subcarrier:
        for k in range(1, max_streams + 1):
            capacity, sinrs, trace_w = zf_mmse_subcarrier_from_svd(
                u, s, vh, k, noise_power=noise_power
            )
            if not np.isfinite(capacity) or not np.isfinite(trace_w):
                continue
            if np.isnan(capacities[k - 1]):
                capacities[k - 1] = 0.0
                sinr_matrix[k - 1, :] = 0.0
            capacities[k - 1] += capacity
            sinr_matrix[k - 1, :] += sinrs
            counts[k - 1] += 1
            max_trace_error = max(max_trace_error, abs(trace_w - 1.0))
            negative = float(np.maximum(0.0, -np.min(sinrs)))
            max_sinr_negative_error = max(max_sinr_negative_error, negative)

    if np.any(counts > 0):
        capacities = capacities / np.maximum(counts, 1)
        sinr_matrix = sinr_matrix / np.maximum(counts[:, None], 1)

    return {
        "capacities": capacities.tolist(),
        "sinr_matrix": sinr_matrix,
        "max_trace_relative_error": max_trace_error,
        "max_sinr_negative_error": max_sinr_negative_error,
    }


def zf_mmse_capacity(h: np.ndarray, noise_power: float = 1.0) -> dict:
    """Compute ZF+MMSE capacity directly from a channel array."""
    valid = valid_subcarrier_indices(h)
    svd_by_subcarrier = []
    for k in valid:
        u, s, vh = np.linalg.svd(h[:, :, k], full_matrices=False)
        svd_by_subcarrier.append((u, s, vh))
    return aggregate_zf_mmse_from_svd(
        svd_by_subcarrier,
        noise_power=noise_power,
        max_streams=min(h.shape[:2]),
    )
