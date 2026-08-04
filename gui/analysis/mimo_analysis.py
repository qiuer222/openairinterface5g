"""MIMO channel normalization, SVD capacity, and ZF+MMSE capacity."""

from __future__ import annotations

from typing import Dict, List

import numpy as np


MAX_STREAMS = 4
EPS = 1e-12


def effective_snr_db(rsrp_dbm: float, snr_offset_db: float = 100.0) -> float:
    """Effective SNR defined by the task: RSRP + 100 dB."""
    return float(rsrp_dbm) + float(snr_offset_db)


def valid_subcarrier_indices(h: np.ndarray, energy_threshold: float = EPS) -> np.ndarray:
    """Return subcarriers with non-zero channel energy."""
    energy = np.sum(np.abs(h) ** 2, axis=(0, 1))
    return np.flatnonzero(energy > energy_threshold)


def _sorted_eigenvalues(hk: np.ndarray) -> np.ndarray:
    gram = hk @ hk.conj().T
    return np.linalg.eigvalsh(gram)[::-1]


def _svd_capacity(eigenvalues: np.ndarray, rho: float, k: int) -> float:
    if k < 1 or k > len(eigenvalues):
        return float("nan")
    # eigenvalues are already normalized so that their sum equals rho.
    return float(np.sum(np.log2(1.0 + eigenvalues[:k] / k)))


def zf_mmse_capacity(hk: np.ndarray, rho: float, k: int) -> tuple[float, List[float], float]:
    """ZF precoding with total power K and MMSE receiver SINR.

    Returns (capacity, SINR padded to 4 streams, trace(W W^H)).
    """
    nr, nt = hk.shape
    if k > min(nr, nt):
        return float("nan"), [0.0, 0.0, 0.0, 0.0], float("nan")

    u, s, vh = np.linalg.svd(hk, full_matrices=False)
    if k > len(s) or np.min(s[:k]) <= EPS:
        return float("nan"), [0.0, 0.0, 0.0, 0.0], float("nan")

    v_k = vh[:k].conj().T
    w_raw = v_k @ np.diag(1.0 / s[:k])
    trace_w_raw = float(np.sum(np.abs(w_raw) ** 2))
    scale = np.sqrt(k / trace_w_raw)
    w = w_raw * scale
    trace_w = float(np.sum(np.abs(w) ** 2))

    h_eq = hk @ w
    beta = k / rho
    g = np.linalg.inv(h_eq.conj().T @ h_eq + beta * np.eye(k)) @ h_eq.conj().T
    r = g @ h_eq

    stream_power = rho / k
    sinrs: List[float] = []
    for i in range(k):
        signal = abs(r[i, i]) ** 2 * stream_power
        interference = sum(abs(r[i, j]) ** 2 for j in range(k) if j != i) * stream_power
        noise = float(np.sum(np.abs(g[i, :]) ** 2))
        sinr = signal / (interference + noise) if interference + noise > 0 else float("nan")
        sinrs.append(sinr)

    capacity = float(np.sum(np.log2(1.0 + np.asarray(sinrs))))
    padded = sinrs + [0.0] * (MAX_STREAMS - len(sinrs))
    return capacity, padded, trace_w


def analyze_channel(
    h: np.ndarray,
    rsrp_dbm: float,
    snr_offset_db: float = 100.0,
) -> Dict:
    """Compute all per-sample metrics and normalization validation data."""
    empty = {
        "eigenvalues": np.full(MAX_STREAMS, float("nan")),
        "eigenvalue_sum": float("nan"),
        "frobenius_norm2": float("nan"),
        "svd_capacities": np.full(MAX_STREAMS, float("nan")),
        "zf_capacities": np.full(MAX_STREAMS, float("nan")),
        "sinr_matrix": np.zeros((MAX_STREAMS, MAX_STREAMS)),
        "validation": [],
        "actual_shape": h.shape if h is not None else None,
    }
    if h is None or h.ndim != 3 or not np.isfinite(rsrp_dbm):
        return empty

    valid = valid_subcarrier_indices(h)
    if len(valid) == 0:
        return empty

    rho = 10.0 ** (effective_snr_db(rsrp_dbm, snr_offset_db) / 10.0)
    max_rank = min(h.shape[0], h.shape[1], MAX_STREAMS)
    eigen_sum = np.zeros(MAX_STREAMS)
    frob_sum = 0.0
    svd_sum = np.zeros(MAX_STREAMS)
    zf_sum = np.zeros(MAX_STREAMS)
    svd_count = np.zeros(MAX_STREAMS)
    zf_count = np.zeros(MAX_STREAMS)
    sinr_sum = np.zeros((MAX_STREAMS, MAX_STREAMS))
    validation: List[Dict] = []

    for k in valid:
        hk = h[:, :, k]
        frob2_raw = float(np.sum(np.abs(hk) ** 2))
        if frob2_raw <= 0:
            continue

        eig_raw = _sorted_eigenvalues(hk)
        eig = np.zeros(MAX_STREAMS)
        eig[: len(eig_raw)] = eig_raw

        scale = np.sqrt(rho / frob2_raw)
        h_norm = hk * scale
        eig_norm = eig * (rho / frob2_raw)
        frob2_norm = float(np.sum(np.abs(h_norm) ** 2))
        eig_norm_sum = float(np.sum(eig_norm))
        power_rel = abs(frob2_norm - eig_norm_sum) / max(frob2_norm, EPS)

        trace_errors: List[float] = []
        sinr_negative_errors: List[float] = []
        for stream_k in range(1, max_rank + 1):
            svd_sum[stream_k - 1] += _svd_capacity(eig_norm, rho, stream_k)
            svd_count[stream_k - 1] += 1

            cap, sinrs, trace_w = zf_mmse_capacity(h_norm, rho, stream_k)
            if np.isfinite(cap):
                zf_sum[stream_k - 1] += cap
                zf_count[stream_k - 1] += 1
                sinr_sum[stream_k - 1, :] += np.asarray(sinrs)
                trace_errors.append(abs(trace_w - stream_k) / stream_k)
                for sinr in sinrs:
                    if np.isfinite(sinr):
                        sinr_negative_errors.append(max(0.0, -sinr))

        validation.append({
            "subcarrier": int(k),
            "frobenius_norm2": frob2_norm,
            "eigenvalue_sum": eig_norm_sum,
            "power_relative_error": power_rel,
            "zf_trace_relative_error": max(trace_errors) if trace_errors else float("nan"),
            "sinr_negative_error": max(sinr_negative_errors) if sinr_negative_errors else 0.0,
        })
        eigen_sum += eig_norm
        frob_sum += frob2_norm

    count = len(validation)
    if count == 0:
        return empty

    return {
        "eigenvalues": eigen_sum / count,
        "eigenvalue_sum": float(np.sum(eigen_sum) / count),
        "frobenius_norm2": frob_sum / count,
        "svd_capacities": np.where(svd_count > 0, svd_sum / np.maximum(svd_count, 1), np.nan),
        "zf_capacities": np.where(zf_count > 0, zf_sum / np.maximum(zf_count, 1), np.nan),
        "sinr_matrix": sinr_sum / np.maximum(zf_count[:, None], 1),
        "validation": validation,
        "actual_shape": h.shape,
    }
