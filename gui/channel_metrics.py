"""Numerical summaries and plots for OAI GUI channel .npy snapshots."""

from __future__ import annotations

import os
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np


EPS = 1e-12


def _active_indices(h: np.ndarray) -> np.ndarray:
    """Subcarrier indices that contain energy in any rx/tx path."""
    return np.nonzero(np.sum(np.abs(h) ** 2, axis=(0, 1)) > EPS)[0]


def _stats(values: np.ndarray) -> Dict[str, float]:
    values = np.asarray(values, dtype=float)
    if values.size == 0:
        return {"mean": 0.0, "std": 0.0, "p10": 0.0, "p50": 0.0, "p90": 0.0}
    return {
        "mean": float(np.mean(values)),
        "std": float(np.std(values)),
        "p10": float(np.percentile(values, 10)),
        "p50": float(np.percentile(values, 50)),
        "p90": float(np.percentile(values, 90)),
    }


def _svd_per_subcarrier(h: np.ndarray, active: np.ndarray) -> np.ndarray:
    return np.linalg.svd(h[:, :, active].transpose(2, 0, 1), compute_uv=False)


def channel_metrics(h: np.ndarray) -> Dict:
    """Aggregate SVD/condition/rank metrics over active subcarriers."""
    if h.ndim != 3:
        raise ValueError(f"expected (rx, tx, fft), got {h.shape}")
    nrx, ntx, fft = h.shape
    active = _active_indices(h)
    sv = _svd_per_subcarrier(h, active)

    rank_count = min(nrx, ntx)
    singular: List[Dict[str, float]] = []
    for idx in range(rank_count):
        singular.append(_stats(sv[:, idx]))

    condition = np.full(len(active), np.inf)
    with np.errstate(divide="ignore", invalid="ignore"):
        min_sv = np.maximum(sv[:, -1], EPS)
        condition = sv[:, 0] / min_sv
    finite_condition = condition[np.isfinite(condition)]

    # Effective rank uses a -30 dB threshold relative to the largest singular
    # value, mirroring the OAI readers.
    ranks = np.sum(
        sv > np.maximum(EPS, sv[:, :1] * 1e-3), axis=1
    )
    rank_counts = {
        str(int(rank)): int(np.count_nonzero(ranks == rank))
        for rank in sorted(np.unique(ranks))
    }
    cond_thresholds = {}
    for threshold in (2.0, 5.0, 10.0, 20.0, 100.0):
        if finite_condition.size == 0:
            cond_thresholds[str(int(threshold))] = 0.0
        else:
            cond_thresholds[str(int(threshold))] = float(
                np.mean(finite_condition <= threshold)
            )

    per_path = []
    for rx in range(nrx):
        for tx in range(ntx):
            values = h[rx, tx, active]
            power = np.mean(np.abs(values) ** 2)
            per_path.append(
                {
                    "rx": rx,
                    "tx": tx,
                    "rms": float(np.sqrt(power)),
                    "mean_mag_db": float(10 * np.log10(max(power, EPS))),
                }
            )

    return {
        "shape": list(h.shape),
        "nrx": nrx,
        "ntx": ntx,
        "fft_size": fft,
        "active_count": int(len(active)),
        "active_first": int(active[0]) if len(active) else -1,
        "active_last": int(active[-1]) if len(active) else -1,
        "per_path": per_path,
        "singular_value_stats": singular,
        "condition_stats": _stats(finite_condition),
        "condition_threshold_ratio": cond_thresholds,
        "rank_counts": rank_counts,
    }


def _complex_correlation(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.sqrt(np.sum(np.abs(a) ** 2) * np.sum(np.abs(b) ** 2))
    if denom <= EPS:
        return 0.0
    return float(np.abs(np.sum(a * np.conj(b))) / denom)


def _best_scale(a: np.ndarray, b: np.ndarray) -> complex:
    denom = np.sum(np.abs(a) ** 2)
    if denom <= EPS:
        return complex(1.0, 0.0)
    return complex(np.sum(b * np.conj(a)) / denom)


def compare_channel_slots(a: np.ndarray, b: np.ndarray) -> Dict:
    """Compare one slot pair using the common active-subcarrier mask."""
    if a.shape != b.shape:
        raise ValueError(f"shape mismatch: {a.shape} vs {b.shape}")
    nrx, ntx, _ = a.shape
    active = np.intersect1d(_active_indices(a), _active_indices(b))
    if len(active) == 0:
        raise ValueError("no common active subcarriers")

    per_path = []
    for rx in range(nrx):
        for tx in range(ntx):
            av = a[rx, tx, active]
            bv = b[rx, tx, active]
            corr = _complex_correlation(av, bv)
            scale = _best_scale(av, bv)
            err = bv - scale * av
            nmse = float(
                np.sum(np.abs(err) ** 2) / max(np.sum(np.abs(bv) ** 2), EPS)
            )
            rmse = float(np.sqrt(np.mean(np.abs(err) ** 2)))
            per_path.append(
                {
                    "rx": rx,
                    "tx": tx,
                    "correlation": corr,
                    "nmse": nmse,
                    "rmse": rmse,
                    "best_scale_abs": float(np.abs(scale)),
                    "best_scale_phase": float(np.angle(scale)),
                }
            )

    # Normalize each per-subcarrier channel matrix so singular-value
    # comparison is invariant to the overall amplitude/scale of the recording.
    def _normalize(h: np.ndarray) -> np.ndarray:
        out = h.copy()
        selected = out[:, :, active]
        power = np.sum(np.abs(selected) ** 2, axis=(0, 1))
        scale = np.sqrt(np.maximum(power, EPS))
        out[:, :, active] = selected / scale
        return out

    sv_a = _svd_per_subcarrier(a, active)
    sv_b = _svd_per_subcarrier(b, active)
    sv_norm_a = _svd_per_subcarrier(_normalize(a), active)
    sv_norm_b = _svd_per_subcarrier(_normalize(b), active)
    rank_count = min(nrx, ntx)
    singular_comparison = []
    for idx in range(rank_count):
        va, vb = sv_a[:, idx], sv_b[:, idx]
        van, vbn = sv_norm_a[:, idx], sv_norm_b[:, idx]
        singular_comparison.append(
            {
                "index": idx,
                "correlation": _complex_correlation(va, vb),
                # Real-valued scalar correlation for singular value curves.
                "scalar_correlation": float(
                    np.corrcoef(van, vbn)[0, 1] if van.size > 1 else 0.0
                ),
                "nmse": float(
                    np.sum((van - vbn) ** 2)
                    / max(np.sum(vbn**2), EPS)
                ),
                "ratio_stats": _stats(va / np.maximum(vb, EPS)),
            }
        )

    cond_a = np.maximum(sv_a[:, -1], EPS)
    cond_a = sv_a[:, 0] / cond_a
    cond_b = np.maximum(sv_b[:, -1], EPS)
    cond_b = sv_b[:, 0] / cond_b

    return {
        "active_count": int(len(active)),
        "per_path": per_path,
        "singular_value_comparison": singular_comparison,
        "condition_a": _stats(cond_a),
        "condition_b": _stats(cond_b),
    }


def aggregate_comparison(results: Sequence[Dict]) -> Dict:
    """Aggregate per-slot comparison metrics."""
    paths = len(results[0]["per_path"]) if results else 0
    path_rows = []
    for idx in range(paths):
        rows = [r["per_path"][idx] for r in results]
        path_rows.append(
            {
                "rx": rows[0]["rx"],
                "tx": rows[0]["tx"],
                "correlation": _stats([r["correlation"] for r in rows]),
                "nmse": _stats([r["nmse"] for r in rows]),
            }
        )

    sing_count = len(results[0]["singular_value_comparison"]) if results else 0
    singular_rows = []
    for idx in range(sing_count):
        rows = [r["singular_value_comparison"][idx] for r in results]
        singular_rows.append(
            {
                "index": idx,
                "scalar_correlation": _stats([r["scalar_correlation"] for r in rows]),
                "nmse": _stats([r["nmse"] for r in rows]),
            }
        )

    return {
        "slots": len(results),
        "per_path": path_rows,
        "singular_value_comparison": singular_rows,
        "condition_a": _stats(
            np.asarray([r["condition_a"]["mean"] for r in results])
        )
        if results
        else {},
        "condition_b": _stats(
            np.asarray([r["condition_b"]["mean"] for r in results])
        )
        if results
        else {},
    }


def tap_report(
    h: np.ndarray,
    sparse_paths: Sequence[Sequence[Sequence[Tuple[int, complex]]]],
    fft_size: int,
    subcarrier_offset: int,
) -> Dict:
    """Reconstruct each sparse path in frequency and report fit error."""
    nrx = len(sparse_paths)
    ntx = len(sparse_paths[0]) if nrx else 0
    active = _active_indices(h)
    phys = (active + subcarrier_offset) % fft_size

    report = []
    for rx in range(nrx):
        for tx in range(ntx):
            paths = sparse_paths[rx][tx]
            if not paths:
                report.append({"rx": rx, "tx": tx, "active_taps": 0, "delays": []})
                continue
            delays = np.array([d for d, _ in paths])
            taps = np.array([v for _, v in paths])
            basis = np.exp(
                -2j * np.pi * np.outer(phys, delays) / fft_size
            )
            reconstructed = basis @ taps
            reference = h[rx, tx, active] / float(1 << 9)
            error = np.linalg.norm(reconstructed - reference) / max(
                np.linalg.norm(reference), EPS
            )
            report.append(
                {
                    "rx": rx,
                    "tx": tx,
                    "active_taps": len(paths),
                    "delays": [int(d) for d, _ in paths],
                    "magnitudes": [float(np.abs(v)) for _, v in paths],
                    "frequency_fit_rmse": float(error),
                }
            )
    return report


def _ensure_plot_backend() -> None:
    import matplotlib

    matplotlib.use("Agg")


def save_channel_analysis_figures(
    h: np.ndarray,
    output_dir: str,
    prefix: str,
    tap_reports: Optional[List[Dict]] = None,
) -> List[str]:
    """Write channel inspection figures under output_dir."""
    _ensure_plot_backend()
    import matplotlib.pyplot as plt

    os.makedirs(output_dir, exist_ok=True)
    paths: List[str] = []

    fig, axes = plt.subplots(h.shape[0], h.shape[1], figsize=(3.0 * h.shape[1], 2.5 * h.shape[0]))
    axes = np.atleast_2d(axes)
    for rx in range(h.shape[0]):
        for tx in range(h.shape[1]):
            axes[rx, tx].plot(np.abs(h[rx, tx]))
            axes[rx, tx].set_title(f"|H| rx{rx} tx{tx}")
    fig.tight_layout()
    path = os.path.join(output_dir, f"{prefix}_magnitude.png")
    fig.savefig(path, dpi=160)
    plt.close(fig)
    paths.append(path)

    active = _active_indices(h)
    if len(active):
        sv = _svd_per_subcarrier(h, active)
        fig, ax = plt.subplots()
        for idx in range(sv.shape[1]):
            ax.plot(active, sv[:, idx], label=f"singular value {idx + 1}")
        ax.set_xlabel("subcarrier index")
        ax.legend()
        fig.tight_layout()
        path = os.path.join(output_dir, f"{prefix}_singular_values.png")
        fig.savefig(path, dpi=160)
        plt.close(fig)
        paths.append(path)

    return paths


def save_comparison_figures(
    a: np.ndarray,
    b: np.ndarray,
    output_dir: str,
    prefix: str = "channel_comparison",
) -> List[str]:
    _ensure_plot_backend()
    import matplotlib.pyplot as plt

    os.makedirs(output_dir, exist_ok=True)
    paths: List[str] = []

    fig, axes = plt.subplots(
        a.shape[0], a.shape[1], figsize=(3.2 * a.shape[1], 2.6 * a.shape[0])
    )
    axes = np.atleast_2d(axes)
    for rx in range(a.shape[0]):
        for tx in range(a.shape[1]):
            ax = axes[rx, tx]
            ax.plot(np.abs(a[rx, tx]), label="A")
            ax.plot(np.abs(b[rx, tx]), label="B", alpha=0.7)
            ax.set_title(f"|H| rx{rx} tx{tx}")
            ax.legend(fontsize=7)
    fig.tight_layout()
    path = os.path.join(output_dir, f"{prefix}_magnitude.png")
    fig.savefig(path, dpi=160)
    plt.close(fig)
    paths.append(path)

    common = np.intersect1d(_active_indices(a), _active_indices(b))
    if len(common):
        sv_a = _svd_per_subcarrier(a, common)
        sv_b = _svd_per_subcarrier(b, common)
        fig, axes = plt.subplots(1, 2, figsize=(12, 4))
        for idx in range(sv_a.shape[1]):
            axes[0].plot(common, sv_a[:, idx], label=f"A sv{idx + 1}")
            axes[1].plot(common, sv_b[:, idx], label=f"B sv{idx + 1}")
        axes[0].set_title("A singular values")
        axes[1].set_title("B singular values")
        axes[0].legend(); axes[1].legend()
        fig.tight_layout()
        path = os.path.join(output_dir, f"{prefix}_singular_values.png")
        fig.savefig(path, dpi=160)
        plt.close(fig)
        paths.append(path)
    return paths
