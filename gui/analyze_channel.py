#!/usr/bin/env python3
"""Analyze one OAI CSI-RS or SRS channel snapshot from a .npy file."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gui.analysis.normalization import normalize_channel
from gui.npy_to_rfsim_bin import (
    AMP_SCALE_BITS,
    MAX_ACTIVE_TAPS_LIMIT,
    TAP_LEN_DEFAULT,
    TAP_PRUNING_DB,
    full_taps_for_slot,
    sparse_taps_for_slot,
)


EPS = 1e-12
DEFAULT_MAX_ACTIVE_TAPS = 8


def infer_kind(path: str, requested: str = "auto") -> str:
    """Return ``csi`` or ``srs`` using the filename and optional override."""
    if requested != "auto":
        return requested
    name = os.path.basename(path).lower()
    if name.startswith("srs_"):
        return "srs"
    if name.startswith("channel_"):
        return "csi"
    raise ValueError(
        f"cannot infer channel kind from {name!r}; pass --kind csi or --kind srs"
    )


def parse_timestamp_from_name(path: str) -> Optional[str]:
    match = re.search(r"(\d{8}_\d{6}(?:_\d{6})?)", os.path.basename(path))
    return match.group(1) if match else None


def load_channel_npy(path: str) -> np.ndarray:
    """Load one channel and normalize it to ``(rx, tx, subcarrier)``."""
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    arr = np.load(path, allow_pickle=False)
    if arr.dtype.names:
        if set(arr.dtype.names) != {"r", "i"}:
            raise ValueError(f"unsupported structured dtype {arr.dtype} in {path}")
        arr = arr["r"].astype(np.float64) + 1j * arr["i"].astype(np.float64)
    if not np.issubdtype(arr.dtype, np.complexfloating):
        arr = arr.astype(np.complex128)
    else:
        arr = arr.astype(np.complex128, copy=False)

    if arr.ndim == 3:
        h = arr
    elif arr.ndim == 4 and arr.shape[2] == 1:
        h = arr[:, :, 0, :]
    elif arr.ndim == 4:
        raise ValueError(
            "only 4D channel arrays with a singleton symbol dimension are "
            f"supported; got {arr.shape}"
        )
    else:
        raise ValueError(
            "expected channel shape (rx, tx, subcarrier) or "
            f"(rx, tx, 1, subcarrier), got {arr.shape}"
        )
    if h.shape[0] == 0 or h.shape[1] == 0 or h.shape[2] == 0:
        raise ValueError(f"channel has an empty dimension: {h.shape}")
    return h


def active_subcarriers(h: np.ndarray) -> np.ndarray:
    """Return finite subcarriers carrying non-zero channel energy."""
    if h.ndim != 3:
        raise ValueError(f"expected (rx, tx, subcarrier), got {h.shape}")
    energy = np.sum(np.abs(h) ** 2, axis=(0, 1))
    finite = np.all(np.isfinite(h), axis=(0, 1))
    return np.flatnonzero(finite & (energy > EPS))


def _subcarrier_energy_metrics(
    energy: np.ndarray, subcarrier_indices: np.ndarray
) -> Dict:
    """Summarize total channel energy across subcarriers."""
    energy = np.asarray(energy, dtype=float)
    subcarrier_indices = np.asarray(subcarrier_indices)
    if energy.ndim != 1 or energy.size == 0:
        raise ValueError("subcarrier energy must be a non-empty 1D array")
    if subcarrier_indices.shape != energy.shape:
        raise ValueError(
            "subcarrier indices must have the same shape as subcarrier energy"
        )

    energy_db = 10.0 * np.log10(np.maximum(energy, EPS))
    linear_stats = _stats(energy)
    db_stats = _stats(energy_db)
    peak_index = int(np.argmax(energy))
    minimum_index = int(np.argmin(energy))
    mean_energy = linear_stats["mean"]
    return {
        "linear": linear_stats,
        "db": db_stats,
        "coefficient_of_variation": float(
            linear_stats["std"] / max(abs(mean_energy), EPS)
        ),
        "peak_to_average_db": float(
            10.0
            * np.log10(
                max(linear_stats["max"] / max(abs(mean_energy), EPS), EPS)
            )
        ),
        "peak_to_trough_db": float(db_stats["max"] - db_stats["min"]),
        "peak_subcarrier": int(subcarrier_indices[peak_index]),
        "minimum_subcarrier": int(subcarrier_indices[minimum_index]),
    }


def _stats(values: np.ndarray) -> Dict[str, float]:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return {
            "mean": float("nan"),
            "std": float("nan"),
            "min": float("nan"),
            "max": float("nan"),
            "p10": float("nan"),
            "p50": float("nan"),
            "p90": float("nan"),
        }
    return {
        "mean": float(np.mean(values)),
        "std": float(np.std(values)),
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "p10": float(np.percentile(values, 10)),
        "p50": float(np.percentile(values, 50)),
        "p90": float(np.percentile(values, 90)),
    }


def _frequency_autocorrelation(values: np.ndarray) -> np.ndarray:
    """Return normalized linear frequency autocorrelation for lags 0..N-1."""
    values = np.asarray(values, dtype=np.complex128)
    power = float(np.sum(np.abs(values) ** 2))
    if values.size < 2 or power <= EPS:
        return np.ones(1, dtype=float)
    normalized = values / np.sqrt(power)
    nfft = 1 << int(np.ceil(np.log2(2 * values.size)))
    spectrum = np.fft.fft(normalized, n=nfft)
    autocorr = np.fft.ifft(np.abs(spectrum) ** 2).real[: values.size]
    return autocorr / max(float(autocorr[0]), EPS)


def _coherence_lag(autocorr: np.ndarray, threshold: float) -> int:
    if autocorr.size <= 1:
        return 0
    below = np.flatnonzero(autocorr[1:] < threshold)
    return int(below[0] + 1) if below.size else int(autocorr.size - 1)


def _delay_stats(
    power: np.ndarray,
    delays: np.ndarray,
    scs_hz: float,
    fft_size: int,
    threshold_db: float = -30.0,
) -> Dict:
    """Return common-power-delay statistics for one PDP."""
    power = np.asarray(power, dtype=float)
    delays = np.asarray(delays, dtype=float)
    total = float(np.sum(power))
    if total <= EPS:
        return {
            "peak_delay_samples": 0,
            "peak_delay_ns": 0.0,
            "mean_delay_samples": 0.0,
            "mean_delay_ns": 0.0,
            "rms_delay_spread_samples": 0.0,
            "rms_delay_spread_ns": 0.0,
            "max_excess_delay_samples": 0.0,
            "max_excess_delay_ns": 0.0,
            "significant_taps": 0,
        }
    peak = int(np.argmax(power))
    significant = power >= float(np.max(power)) * 10.0 ** (threshold_db / 10.0)
    significant_delays = delays[significant]
    mean_delay = float(np.sum(delays * power) / total)
    rms_spread = float(
        np.sqrt(np.sum(((delays - mean_delay) ** 2) * power) / total)
    )
    sample_seconds = 1.0 / (float(scs_hz) * float(fft_size))
    return {
        "peak_delay_samples": int(delays[peak]),
        "peak_delay_ns": float(delays[peak] * sample_seconds * 1e9),
        "mean_delay_samples": mean_delay,
        "mean_delay_ns": mean_delay * sample_seconds * 1e9,
        "rms_delay_spread_samples": rms_spread,
        "rms_delay_spread_ns": rms_spread * sample_seconds * 1e9,
        "max_excess_delay_samples": float(
            np.max(significant_delays) - np.min(significant_delays)
        ),
        "max_excess_delay_ns": float(
            (np.max(significant_delays) - np.min(significant_delays))
            * sample_seconds
            * 1e9
        ),
        "significant_taps": int(np.count_nonzero(significant)),
    }


def _path_metrics(
    values: np.ndarray,
    subcarrier_indices: np.ndarray,
    scs_hz: float,
    fft_size: int,
) -> Dict:
    magnitude = np.abs(values)
    power = magnitude**2
    magnitude_db = 20.0 * np.log10(np.maximum(magnitude, EPS))
    phase = np.unwrap(np.angle(values))
    slope = (
        float(np.polyfit(subcarrier_indices, phase, 1)[0])
        if values.size > 1
        else 0.0
    )
    adjacent_phase = (
        np.angle(values[1:] * np.conj(values[:-1]))
        if values.size > 1
        else np.asarray([], dtype=float)
    )
    autocorr = _frequency_autocorrelation(values)
    coherence_50 = _coherence_lag(autocorr, 0.5)
    coherence_90 = _coherence_lag(autocorr, 0.9)
    mean_complex = np.mean(values)
    diffuse_power = float(np.mean(np.abs(values - mean_complex) ** 2))
    rician_like_k = (
        float(np.abs(mean_complex) ** 2 / diffuse_power)
        if diffuse_power > EPS
        else float("inf")
    )
    return {
        "mean_power": float(np.mean(power)),
        "rms_amplitude": float(np.sqrt(np.mean(power))),
        "mean_power_db": float(10.0 * np.log10(max(np.mean(power), EPS))),
        "magnitude_db": _stats(magnitude_db),
        "ripple_std_db": float(np.std(magnitude_db)),
        "peak_to_average_db": float(
            10.0 * np.log10(max(np.max(power) / max(np.mean(power), EPS), EPS))
        ),
        "peak_to_trough_db": float(np.max(magnitude_db) - np.min(magnitude_db)),
        "phase_slope_rad_per_sc": slope,
        "group_delay_samples": float(-slope / (2.0 * np.pi)),
        "group_delay_ns": float(
            -slope / (2.0 * np.pi) / (scs_hz * fft_size) * 1e9
        ),
        "mean_adjacent_phase_rad": (
            float(np.mean(adjacent_phase)) if adjacent_phase.size else 0.0
        ),
        "coherence_50_subcarriers": coherence_50,
        "coherence_90_subcarriers": coherence_90,
        "coherence_50_hz": float(coherence_50 * scs_hz),
        "coherence_90_hz": float(coherence_90 * scs_hz),
        "rician_like_k_db": (
            float(10.0 * np.log10(rician_like_k))
            if np.isfinite(rician_like_k) and rician_like_k > 0
            else float("inf")
        ),
    }


def _rank_metrics(sv: np.ndarray) -> Dict:
    result = {}
    for threshold_db in (-10, -20, -30):
        cutoff = sv[:, :1] * 10.0 ** (threshold_db / 20.0)
        ranks = np.sum(sv > np.maximum(cutoff, EPS), axis=1)
        unique, counts = np.unique(ranks, return_counts=True)
        result[f"{threshold_db}_db"] = {
            "mean_rank": float(np.mean(ranks)),
            "histogram": {
                str(int(rank)): int(count)
                for rank, count in zip(unique, counts)
            },
        }
    return result


def _covariance_metrics(matrix: np.ndarray) -> Dict:
    eigenvalues = np.linalg.eigvalsh(matrix)[::-1]
    eigenvalues = np.maximum(eigenvalues, 0.0)
    positive = eigenvalues[eigenvalues > EPS]
    rank = int(
        np.count_nonzero(
            eigenvalues > max(eigenvalues[0] * 1e-3, EPS)
        )
    ) if eigenvalues.size else 0
    condition = (
        float(eigenvalues[0] / eigenvalues[-1])
        if eigenvalues.size and eigenvalues[-1] > EPS
        else float("inf")
    )
    return {
        "eigenvalues": [float(value) for value in eigenvalues],
        "rank_30db": rank,
        "condition_number": condition,
        "trace": float(np.sum(eigenvalues)),
        "effective_dimension": (
            float(np.sum(eigenvalues) ** 2 / max(np.sum(eigenvalues**2), EPS))
            if positive.size
            else 0.0
        ),
    }


def _spatial_correlation(matrix: np.ndarray) -> Dict:
    """Return mean/max normalized off-diagonal correlations of weighted rows."""
    norms = np.sqrt(np.maximum(np.real(np.diag(matrix)), EPS))
    denominator = np.outer(norms, norms)
    corr = np.abs(matrix) / np.maximum(denominator, EPS)
    if corr.shape[0] <= 1:
        return {"mean": 0.0, "max": 0.0}
    mask = ~np.eye(corr.shape[0], dtype=bool)
    return {
        "mean": float(np.mean(corr[mask])),
        "max": float(np.max(corr[mask])),
    }


def _mimo_metrics(
    h: np.ndarray,
    active: np.ndarray,
    h_normalized: np.ndarray,
    noise_power: float,
) -> Tuple[Dict, np.ndarray]:
    matrices = h[:, :, active].transpose(2, 0, 1)
    sv = np.linalg.svd(matrices, compute_uv=False)
    singular_stats = []
    for idx in range(sv.shape[1]):
        stats = _stats(sv[:, idx])
        stats.update(
            {
                "variance_across_subcarriers": float(np.var(sv[:, idx])),
                "coefficient_of_variation": float(
                    np.std(sv[:, idx]) / max(abs(np.mean(sv[:, idx])), EPS)
                ),
                "min_over_max": float(
                    np.min(sv[:, idx]) / max(np.max(sv[:, idx]), EPS)
                ),
            }
        )
        singular_stats.append(stats)

    condition = sv[:, 0] / np.maximum(sv[:, -1], EPS)
    covariance_rx = np.mean(matrices @ matrices.conj().transpose(0, 2, 1), axis=0)
    covariance_tx = np.mean(matrices.conj().transpose(0, 2, 1) @ matrices, axis=0)

    normalized_matrices = h_normalized[:, :, active].transpose(2, 0, 1)
    column_correlations = []
    row_correlations = []
    for matrix in normalized_matrices:
        if matrix.shape[1] > 1:
            gram = matrix.conj().T @ matrix
            norms = np.sqrt(np.maximum(np.real(np.diag(gram)), EPS))
            corr = np.abs(gram) / np.maximum(np.outer(norms, norms), EPS)
            column_correlations.append(corr[~np.eye(corr.shape[0], dtype=bool)])
        if matrix.shape[0] > 1:
            gram = matrix @ matrix.conj().T
            norms = np.sqrt(np.maximum(np.real(np.diag(gram)), EPS))
            corr = np.abs(gram) / np.maximum(np.outer(norms, norms), EPS)
            row_correlations.append(corr[~np.eye(corr.shape[0], dtype=bool)])

    eigenvalues = np.linalg.eigvalsh(
        normalized_matrices @ normalized_matrices.conj().transpose(0, 2, 1)
    )
    eigenvalues = np.maximum(eigenvalues, 0.0)
    capacities = []
    max_streams = sv.shape[1]
    for streams in range(1, max_streams + 1):
        per_subcarrier = np.sum(
            np.log2(
                1.0
                + eigenvalues[:, -streams:]
                / (float(streams) * float(noise_power))
            ),
            axis=1,
        )
        capacities.append(float(np.mean(per_subcarrier)))
    shannon = float(
        np.mean(np.sum(np.log2(1.0 + eigenvalues / noise_power), axis=1))
    )
    best_streams = int(np.argmax(capacities) + 1) if capacities else 0
    return (
        {
            "singular_values": singular_stats,
            "condition_number": _stats(condition),
            "condition_threshold_ratios": {
                str(threshold): float(np.mean(condition <= threshold))
                for threshold in (2.0, 5.0, 10.0, 20.0, 100.0)
            },
            "rank": _rank_metrics(sv),
            "rx_covariance": _covariance_metrics(covariance_rx),
            "tx_covariance": _covariance_metrics(covariance_tx),
            "stream_orthogonality": {
                "tx_columns_mean": (
                    float(np.mean(np.concatenate(column_correlations)))
                    if column_correlations
                    else 0.0
                ),
                "tx_columns_max": (
                    float(np.max(np.concatenate(column_correlations)))
                    if column_correlations
                    else 0.0
                ),
                "rx_rows_mean": (
                    float(np.mean(np.concatenate(row_correlations)))
                    if row_correlations
                    else 0.0
                ),
                "rx_rows_max": (
                    float(np.max(np.concatenate(row_correlations)))
                    if row_correlations
                    else 0.0
                ),
            },
            "capacity_per_streams": capacities,
            "best_streams": best_streams,
            "best_svd_capacity": capacities[best_streams - 1] if capacities else 0.0,
            "shannon_capacity": shannon,
        },
        sv,
    )


def _time_domain_metrics(
    h: np.ndarray,
    active: np.ndarray,
    fft_size: int,
    subcarrier_offset: int,
    scs_hz: float,
    tap_len: int,
    max_active_taps: int,
    prune_db: float,
) -> Tuple[Dict, np.ndarray, np.ndarray]:
    full_taps = full_taps_for_slot(h, fft_size, subcarrier_offset)
    shifted_taps = np.fft.fftshift(full_taps, axes=-1)
    shifted_delays = np.arange(-(fft_size // 2), fft_size - (fft_size // 2))
    dense_delays = np.arange(fft_size)

    sparse_paths = sparse_taps_for_slot(
        h,
        fft_size,
        subcarrier_offset,
        tap_len=tap_len,
        max_active_taps=max_active_taps,
        prune_db=prune_db,
    )
    physical = (active + subcarrier_offset) % fft_size
    per_path = []
    aggregate_pdp = np.zeros(fft_size, dtype=float)
    for rx in range(h.shape[0]):
        for tx in range(h.shape[1]):
            pdp = np.abs(full_taps[rx, tx]) ** 2
            aggregate_pdp += pdp
            paths = sparse_paths[rx][tx]
            if paths:
                delays = np.asarray([item[0] for item in paths], dtype=float)
                taps = np.asarray([item[1] for item in paths], dtype=np.complex128)
                powers = np.abs(taps) ** 2
                basis = np.exp(-2j * np.pi * np.outer(physical, delays) / fft_size)
                reconstructed = basis @ taps
                reference = h[rx, tx, active] / float(1 << AMP_SCALE_BITS)
                error = reconstructed - reference
                fit_nmse = float(
                    np.sum(np.abs(error) ** 2)
                    / max(np.sum(np.abs(reference) ** 2), EPS)
                )
                fit_rmse = float(np.sqrt(np.mean(np.abs(error) ** 2)))
                sparse_delay_stats = _delay_stats(
                    powers, delays, scs_hz, fft_size
                )
            else:
                delays = np.asarray([], dtype=float)
                taps = np.asarray([], dtype=np.complex128)
                fit_nmse = 1.0
                fit_rmse = 0.0
                sparse_delay_stats = _delay_stats(
                    np.zeros(1), np.zeros(1), scs_hz, fft_size
                )

            per_path.append(
                {
                    "rx": rx,
                    "tx": tx,
                    "idft": _delay_stats(
                        pdp, dense_delays, scs_hz, fft_size
                    ),
                    "sparse": {
                        **sparse_delay_stats,
                        "delays": [int(delay) for delay in delays],
                        "tap_magnitudes": [float(item) for item in np.abs(taps)],
                        "tap_powers": [float(item) for item in powers],
                        "fit_nmse": fit_nmse,
                        "fit_rmse": fit_rmse,
                    },
                }
            )

    return (
        {
            "subcarrier_offset": int(subcarrier_offset),
            "tap_len": int(tap_len),
            "prune_db": float(prune_db),
            "max_active_taps": int(max_active_taps),
            "per_path": per_path,
            "aggregate_idft": _delay_stats(
                aggregate_pdp, dense_delays, scs_hz, fft_size
            ),
        },
        shifted_taps,
        shifted_delays,
    )


def analyze_channel(
    path: str,
    *,
    kind: str = "auto",
    n_rb: int = 106,
    scs_hz: float = 30000.0,
    subcarrier_offset: Optional[int] = None,
    noise_power: float = 1.0,
    snr_db: Optional[float] = None,
    tap_len: int = TAP_LEN_DEFAULT,
    max_active_taps: int = DEFAULT_MAX_ACTIVE_TAPS,
    prune_db: float = TAP_PRUNING_DB,
    transpose: bool = False,
) -> Tuple[Dict, Dict]:
    """Analyze one channel and return ``(metrics, plot_data)``."""
    if n_rb <= 0:
        raise ValueError("n_rb must be positive")
    if scs_hz <= 0:
        raise ValueError("scs_hz must be positive")
    if noise_power <= 0:
        raise ValueError("noise_power must be positive")
    if tap_len <= 0:
        raise ValueError("tap_len must be positive")
    if max_active_taps <= 0:
        raise ValueError("max_active_taps must be positive")
    if max_active_taps > MAX_ACTIVE_TAPS_LIMIT:
        raise ValueError(
            f"max_active_taps must not exceed {MAX_ACTIVE_TAPS_LIMIT}"
        )

    resolved_kind = infer_kind(path, kind)
    h = load_channel_npy(path)
    if transpose:
        h = h.transpose(1, 0, 2)
    active = active_subcarriers(h)
    if active.size == 0:
        raise ValueError("channel has no active subcarriers")
    subcarrier_energy = np.sum(
        np.abs(h[:, :, active]) ** 2, axis=(0, 1)
    )
    subcarrier_energy_metrics = _subcarrier_energy_metrics(
        subcarrier_energy, active
    )
    fft_size = h.shape[-1]
    if subcarrier_offset is None:
        subcarrier_offset = (
            fft_size // 2
            if resolved_kind == "srs"
            else fft_size - (n_rb * 12 // 2)
        )

    finite_count = int(np.count_nonzero(np.all(np.isfinite(h), axis=(0, 1))))
    invalid_count = int(h.shape[-1] - finite_count)
    h_for_capacity = normalize_channel(
        h, noise_power=noise_power, snr_db=snr_db
    )

    paths = []
    for rx in range(h.shape[0]):
        for tx in range(h.shape[1]):
            metrics = _path_metrics(
                h[rx, tx, active], active, scs_hz, fft_size
            )
            metrics["rx"] = rx
            metrics["tx"] = tx
            paths.append(metrics)

    mimo, sv = _mimo_metrics(h, active, h_for_capacity, noise_power)
    time_domain, shifted_taps, shifted_delays = _time_domain_metrics(
        h,
        active,
        fft_size,
        subcarrier_offset,
        scs_hz,
        tap_len,
        max_active_taps,
        prune_db,
    )
    condition = sv[:, 0] / np.maximum(sv[:, -1], EPS)
    metrics = {
        "file": {
            "path": os.path.abspath(path),
            "name": os.path.basename(path),
            "size_bytes": os.path.getsize(path),
            "kind": resolved_kind,
            "timestamp": parse_timestamp_from_name(path),
        },
        "assumptions": {
            "n_rb": int(n_rb),
            "scs_hz": float(scs_hz),
            "subcarrier_offset": int(subcarrier_offset),
            "noise_power": float(noise_power),
            "snr_db": snr_db,
            "transpose": bool(transpose),
        },
        "array": {
            "shape": list(h.shape),
            "n_rx": int(h.shape[0]),
            "n_tx": int(h.shape[1]),
            "fft_size": int(fft_size),
            "finite_subcarriers": finite_count,
            "invalid_subcarriers": invalid_count,
            "active_subcarriers": int(active.size),
            "active_first": int(active[0]),
            "active_last": int(active[-1]),
        },
        "paths": paths,
        "subcarrier_energy": subcarrier_energy_metrics,
        "mimo": mimo,
        "time_domain": time_domain,
    }
    plot_data = {
        "h": h,
        "active": active,
        "subcarrier_energy": subcarrier_energy,
        "singular_values": sv,
        "condition": condition,
        "impulse": shifted_taps,
        "delays": shifted_delays,
    }
    return metrics, plot_data


def _format_report(metrics: Dict) -> str:
    lines = []
    file_info = metrics["file"]
    assumption = metrics["assumptions"]
    array = metrics["array"]
    lines.extend(
        [
            "=== File ===",
            f"path: {file_info['path']}",
            f"name: {file_info['name']}",
            f"kind: {file_info['kind']}",
            f"timestamp: {file_info['timestamp']}",
            f"size: {file_info['size_bytes']} bytes",
            "",
            "=== Array and Assumptions ===",
            (
                f"shape: {array['shape']}  "
                f"rx={array['n_rx']} tx={array['n_tx']} fft={array['fft_size']}"
            ),
            (
                f"active subcarriers: {array['active_subcarriers']} "
                f"[{array['active_first']}..{array['active_last']}]  "
                f"invalid={array['invalid_subcarriers']}"
            ),
            (
                f"assumptions: n_rb={assumption['n_rb']} "
                f"scs={assumption['scs_hz']:.0f} Hz "
                f"offset={assumption['subcarrier_offset']} "
                f"noise_power={assumption['noise_power']} "
                f"snr_db={assumption['snr_db']}"
            ),
            "",
            "=== Frequency-Domain Paths ===",
        ]
    )
    for path in metrics["paths"]:
        lines.append(
            f"rx{path['rx']} tx{path['tx']}: "
            f"power={path['mean_power_db']:.2f} dB "
            f"ripple={path['ripple_std_db']:.2f} dB "
            f"peak/avg={path['peak_to_average_db']:.2f} dB "
            f"group_delay={path['group_delay_ns']:.2f} ns "
            f"coherence50={path['coherence_50_hz'] / 1e3:.1f} kHz "
            f"K-like={path['rician_like_k_db']:.2f} dB"
        )

    energy = metrics["subcarrier_energy"]
    lines.extend(
        [
            "",
            "=== Subcarrier Energy ===",
            (
                f"total energy across rx/tx: "
                f"mean={energy['linear']['mean']:.6g} "
                f"std={energy['linear']['std']:.6g} "
                f"min={energy['linear']['min']:.6g} "
                f"max={energy['linear']['max']:.6g}"
            ),
            (
                f"variation: CV={energy['coefficient_of_variation']:.4f} "
                f"ripple_std={energy['db']['std']:.2f} dB "
                f"peak/average={energy['peak_to_average_db']:.2f} dB "
                f"peak/trough={energy['peak_to_trough_db']:.2f} dB"
            ),
            (
                f"peak subcarrier={energy['peak_subcarrier']} "
                f"minimum subcarrier={energy['minimum_subcarrier']}"
            ),
        ]
    )

    lines.extend(["", "=== MIMO / Singular Values ==="])
    for idx, singular in enumerate(metrics["mimo"]["singular_values"]):
        lines.append(
            f"SV{idx + 1}: mean={singular['mean']:.4f} "
            f"std={singular['std']:.4f} "
            f"var={singular['variance_across_subcarriers']:.4f} "
            f"p10={singular['p10']:.4f} p90={singular['p90']:.4f} "
            f"CV={singular['coefficient_of_variation']:.4f}"
        )
    condition = metrics["mimo"]["condition_number"]
    lines.append(
        f"condition: mean={condition['mean']:.3f} "
        f"std={condition['std']:.3f} p90={condition['p90']:.3f}"
    )
    for threshold in ("-10_db", "-20_db", "-30_db"):
        rank = metrics["mimo"]["rank"][threshold]
        lines.append(
            f"rank {threshold.replace('_db', ' dB')}: "
            f"mean={rank['mean_rank']:.3f} histogram={rank['histogram']}"
        )
    orthogonality = metrics["mimo"]["stream_orthogonality"]
    lines.append(
        f"stream orthogonality: tx mean={orthogonality['tx_columns_mean']:.4f} "
        f"max={orthogonality['tx_columns_max']:.4f}; "
        f"rx mean={orthogonality['rx_rows_mean']:.4f} "
        f"max={orthogonality['rx_rows_max']:.4f}"
    )

    lines.extend(["", "=== Time Domain ==="])
    aggregate = metrics["time_domain"]["aggregate_idft"]
    lines.append(
        f"aggregate IDFT: peak={aggregate['peak_delay_ns']:.2f} ns "
        f"mean={aggregate['mean_delay_ns']:.2f} ns "
        f"RMS spread={aggregate['rms_delay_spread_ns']:.2f} ns "
        f"max excess={aggregate['max_excess_delay_ns']:.2f} ns"
    )
    for path in metrics["time_domain"]["per_path"]:
        sparse = path["sparse"]
        lines.append(
            f"rx{path['rx']} tx{path['tx']}: "
            f"sparse taps={sparse['significant_taps']} "
            f"delays={sparse['delays']} "
            f"|tap|={[round(value, 4) for value in sparse['tap_magnitudes']]} "
            f"delay RMS={sparse['rms_delay_spread_ns']:.2f} ns "
            f"fit NMSE={sparse['fit_nmse']:.6f}"
        )

    lines.extend(["", "=== Capacity ==="])
    lines.append(
        "SVD capacity by streams: "
        + ", ".join(
            f"K={index + 1}: {value:.4f}"
            for index, value in enumerate(
                metrics["mimo"]["capacity_per_streams"]
            )
        )
    )
    lines.append(
        f"best streams={metrics['mimo']['best_streams']} "
        f"capacity={metrics['mimo']['best_svd_capacity']:.4f} "
        f"Shannon={metrics['mimo']['shannon_capacity']:.4f}"
    )
    lines.extend(
        [
            "",
            "Doppler, temporal correlation, and mobility metrics are unavailable "
            "from a single channel snapshot.",
        ]
    )
    return "\n".join(lines) + "\n"


def _save_figures(plot_data: Dict, output_dir: str) -> List[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(output_dir, exist_ok=True)
    h = plot_data["h"]
    active = plot_data["active"]
    sv = plot_data["singular_values"]
    condition = plot_data["condition"]
    impulse = plot_data["impulse"]
    delays = plot_data["delays"]
    subcarrier_energy = plot_data["subcarrier_energy"]
    outputs: List[str] = []

    fig, axes = plt.subplots(
        h.shape[0],
        h.shape[1],
        figsize=(3.2 * h.shape[1], 2.5 * h.shape[0]),
        squeeze=False,
    )
    for rx in range(h.shape[0]):
        for tx in range(h.shape[1]):
            axes[rx, tx].plot(active, np.abs(h[rx, tx, active]))
            axes[rx, tx].set_title(f"|H| rx{rx} tx{tx}")
            axes[rx, tx].set_xlabel("subcarrier")
    fig.tight_layout()
    path = os.path.join(output_dir, "channel_magnitude.png")
    fig.savefig(path, dpi=160)
    plt.close(fig)
    outputs.append(path)

    fig, ax = plt.subplots(figsize=(10, 5))
    energy_db = 10.0 * np.log10(np.maximum(subcarrier_energy, EPS))
    ax.plot(active, energy_db, color="#1f77b4", linewidth=1.5)
    ax.axhline(
        float(np.mean(energy_db)),
        color="#d62728",
        linestyle="--",
        linewidth=1.0,
        label="mean",
    )
    ax.set_title("Total channel energy across rx/tx paths")
    ax.set_xlabel("subcarrier")
    ax.set_ylabel("channel energy (dB)")
    ax.grid(alpha=0.25)
    ax.legend()
    fig.tight_layout()
    path = os.path.join(output_dir, "channel_subcarrier_energy.png")
    fig.savefig(path, dpi=160)
    plt.close(fig)
    outputs.append(path)

    fig, axes = plt.subplots(
        h.shape[0],
        h.shape[1],
        figsize=(3.2 * h.shape[1], 2.5 * h.shape[0]),
        squeeze=False,
    )
    for rx in range(h.shape[0]):
        for tx in range(h.shape[1]):
            axes[rx, tx].plot(active, np.unwrap(np.angle(h[rx, tx, active])))
            axes[rx, tx].set_title(f"phase rx{rx} tx{tx}")
            axes[rx, tx].set_xlabel("subcarrier")
    fig.tight_layout()
    path = os.path.join(output_dir, "channel_phase.png")
    fig.savefig(path, dpi=160)
    plt.close(fig)
    outputs.append(path)

    fig, ax = plt.subplots(figsize=(10, 5))
    for idx in range(sv.shape[1]):
        ax.plot(active, sv[:, idx], label=f"SV{idx + 1}")
    ax.set_xlabel("subcarrier")
    ax.set_ylabel("singular value")
    ax.legend()
    fig.tight_layout()
    path = os.path.join(output_dir, "channel_singular_values.png")
    fig.savefig(path, dpi=160)
    plt.close(fig)
    outputs.append(path)

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(active, condition)
    ax.set_yscale("log")
    ax.set_xlabel("subcarrier")
    ax.set_ylabel("condition number")
    fig.tight_layout()
    path = os.path.join(output_dir, "channel_condition_number.png")
    fig.savefig(path, dpi=160)
    plt.close(fig)
    outputs.append(path)

    fig, axes = plt.subplots(
        h.shape[0],
        h.shape[1],
        figsize=(3.2 * h.shape[1], 2.5 * h.shape[0]),
        squeeze=False,
    )
    for rx in range(h.shape[0]):
        for tx in range(h.shape[1]):
            axes[rx, tx].plot(delays, 20 * np.log10(np.maximum(np.abs(impulse[rx, tx]), EPS)))
            axes[rx, tx].set_title(f"impulse rx{rx} tx{tx}")
            axes[rx, tx].set_xlabel("delay (samples)")
    fig.tight_layout()
    path = os.path.join(output_dir, "channel_impulse_response.png")
    fig.savefig(path, dpi=160)
    plt.close(fig)
    outputs.append(path)

    fig, ax = plt.subplots(figsize=(10, 5))
    for rx in range(h.shape[0]):
        for tx in range(h.shape[1]):
            ax.plot(
                delays,
                10 * np.log10(np.maximum(np.abs(impulse[rx, tx]) ** 2, EPS)),
                label=f"rx{rx} tx{tx}",
            )
    ax.set_xlabel("delay (samples)")
    ax.set_ylabel("PDP (dB)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    path = os.path.join(output_dir, "channel_pdp.png")
    fig.savefig(path, dpi=160)
    plt.close(fig)
    outputs.append(path)

    capacities = plot_data.get("capacities", [])
    if capacities:
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.plot(np.arange(1, len(capacities) + 1), capacities, marker="o")
        ax.set_xlabel("streams")
        ax.set_ylabel("capacity")
        fig.tight_layout()
        path = os.path.join(output_dir, "channel_capacity.png")
        fig.savefig(path, dpi=160)
        plt.close(fig)
        outputs.append(path)
    return outputs


def _json_safe(value):
    """Convert NumPy and non-finite numeric values to strict JSON values."""
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (np.floating, float)):
        result = float(value)
        return result if np.isfinite(result) else None
    if isinstance(value, (complex, np.complexfloating)):
        return {"real": float(value.real), "imag": float(value.imag)}
    return value


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="one CSI-RS/SRS channel .npy file")
    parser.add_argument("--kind", choices=("auto", "csi", "srs"), default="auto")
    parser.add_argument("--n-rb", type=int, default=106)
    parser.add_argument("--scs", type=float, default=30000.0)
    parser.add_argument(
        "--subcarrier-offset",
        type=int,
        default=None,
        help="physical FFT offset; defaults to SRS FFT/2 or CSI first carrier",
    )
    parser.add_argument("--noise-power", type=float, default=1.0)
    parser.add_argument("--snr-db", type=float, default=None)
    parser.add_argument("--tap-len", type=int, default=TAP_LEN_DEFAULT)
    parser.add_argument("--max-active-taps", type=int, default=DEFAULT_MAX_ACTIVE_TAPS)
    parser.add_argument("--prune-db", type=float, default=TAP_PRUNING_DB)
    parser.add_argument("--transpose", action="store_true")
    parser.add_argument("--output-dir")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        metrics, plot_data = analyze_channel(
            args.input,
            kind=args.kind,
            n_rb=args.n_rb,
            scs_hz=args.scs,
            subcarrier_offset=args.subcarrier_offset,
            noise_power=args.noise_power,
            snr_db=args.snr_db,
            tap_len=args.tap_len,
            max_active_taps=args.max_active_taps,
            prune_db=args.prune_db,
            transpose=args.transpose,
        )
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    report = _format_report(metrics)
    print(report, end="")
    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)
        with open(
            os.path.join(args.output_dir, "channel_analysis.json"),
            "w",
            encoding="utf-8",
        ) as fp:
            json.dump(
                _json_safe(metrics),
                fp,
                indent=2,
                allow_nan=False,
            )
        with open(
            os.path.join(args.output_dir, "channel_analysis.txt"),
            "w",
            encoding="utf-8",
        ) as fp:
            fp.write(report)
        plot_data["capacities"] = metrics["mimo"]["capacity_per_streams"]
        figures = _save_figures(
            plot_data, os.path.join(args.output_dir, "figures")
        )
        print(
            "wrote "
            + os.path.join(args.output_dir, "channel_analysis.json")
        )
        print(
            "wrote "
            + os.path.join(args.output_dir, "channel_analysis.txt")
        )
        for figure in figures:
            print(f"wrote {figure}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
