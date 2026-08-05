"""Plotting for channel_analysis.csv, including per-position averages."""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def _finite_max(values: np.ndarray) -> float:
    finite = values[np.isfinite(values)]
    return float(np.max(finite)) if len(finite) else float("nan")


def _top_half_mean(values: pd.Series) -> float:
    finite = pd.to_numeric(values, errors="coerce").dropna().sort_values(ascending=False)
    if finite.empty:
        return float("nan")
    top_count = max(1, int(np.ceil(len(finite) / 2.0)))
    return float(finite.iloc[:top_count].mean())


def _build_sample_metrics(
    analysis: pd.DataFrame,
    cleaned: Optional[pd.DataFrame],
    samples_per_position: int,
) -> pd.DataFrame:
    rows: List[Dict] = []
    for (ts, pos, sample_idx), group in analysis.groupby(
        ["Timestamp", "PositionID", "SampleIndex"], sort=False
    ):
        first = group.iloc[0]
        k = pd.to_numeric(group["K"], errors="coerce").to_numpy(dtype=float)
        svd = pd.to_numeric(group["CapacitySVD"], errors="coerce").to_numpy(dtype=float)
        zf = pd.to_numeric(group["CapacityZFMMSE"], errors="coerce").to_numpy(dtype=float)

        finite_svd = svd[np.isfinite(svd)]
        finite_zf = zf[np.isfinite(zf)]
        max_svd = _finite_max(svd)
        max_zf = _finite_max(zf)
        argmax_k = float("nan")
        if len(finite_zf):
            argmax_k = float(k[np.isfinite(zf)][int(np.argmax(finite_zf))])

        rows.append({
            "Timestamp": ts,
            "PositionID": int(pos),
            "SampleIndex": int(sample_idx),
            "throughput_mbps": pd.to_numeric(first.get("ThroughputMbps"), errors="coerce"),
            "rsrp_dBm": pd.to_numeric(first.get("RSRP_dBm"), errors="coerce"),
            "max_svd_capacity": max_svd,
            "max_zf_mmse_capacity": max_zf,
            "argmax_k_zf_mmse": argmax_k,
        })

    samples = pd.DataFrame(rows)
    samples["layers"] = float("nan")
    if cleaned is not None and "layers" in cleaned.columns:
        cleaned = cleaned.copy()
        cleaned["layers"] = pd.to_numeric(cleaned["layers"], errors="coerce")
        timestamp_col = "Timestamp" if "Timestamp" in cleaned.columns else "timestamp"
        layer_map = cleaned.set_index(timestamp_col)["layers"]
        samples["layers"] = samples["Timestamp"].map(layer_map)
    samples["sample_global"] = (
        samples_per_position * (samples["PositionID"] - 1) + samples["SampleIndex"]
    )
    return samples


def _position_mean_map(
    x: np.ndarray,
    y: np.ndarray,
    positions: List[int],
    samples_per_position: int,
) -> Dict[int, float]:
    means: Dict[int, float] = {}
    for pos in positions:
        start = samples_per_position * (pos - 1) + 1
        end = samples_per_position * pos
        mask = np.isfinite(y) & (x >= start) & (x <= end)
        if np.any(mask):
            means[pos] = float(np.mean(y[mask]))
    return means


def _draw_position_averages(
    ax,
    x: np.ndarray,
    y: np.ndarray,
    color: str,
    positions: List[int],
    samples_per_position: int,
) -> None:
    means = _position_mean_map(x, y, positions, samples_per_position)
    for pos, mean in means.items():
        start = samples_per_position * (pos - 1) + 1
        end = samples_per_position * pos
        ax.plot([start, end], [mean, mean], color=color, linestyle="--", linewidth=1.3, alpha=0.85)


def _draw_position_boundaries(
    ax,
    positions: List[int],
    samples_per_position: int,
    labels: Optional[Dict[int, str]] = None,
) -> None:
    ymin, ymax = ax.get_ylim()
    for pos in positions:
        boundary = samples_per_position * pos + 0.5
        ax.axvline(boundary, color="gray", linestyle="--", linewidth=0.9, alpha=0.8)
        center = samples_per_position * (pos - 1) + samples_per_position / 2
        label = f"P{pos}"
        if labels and pos in labels:
            label += f" {labels[pos]}"
        ax.text(
            center,
            ymax - 0.02 * (ymax - ymin),
            label,
            ha="center",
            va="top",
            fontsize=9,
        )


def plot_channel_analysis(
    analysis_csv: str,
    cleaned_csv: Optional[str] = None,
    samples_per_position: int = 30,
    output_path: Optional[str] = None,
) -> str:
    """Create the five stacked plots and return the saved PNG path."""
    analysis = pd.read_csv(analysis_csv)
    cleaned = None
    if cleaned_csv and os.path.exists(cleaned_csv):
        cleaned = pd.read_csv(cleaned_csv)

    samples = _build_sample_metrics(analysis, cleaned, samples_per_position)
    if samples.empty:
        raise ValueError("no samples to plot")

    x = samples["sample_global"].to_numpy(dtype=float)
    positions = sorted(int(v) for v in samples["PositionID"].unique())

    series_specs: List[Tuple[str, List[Tuple[str, np.ndarray, str]]]] = [
        ("Throughput", [("throughput_mbps", samples["throughput_mbps"].to_numpy(dtype=float), "#1f77b4")]),
        (
            "layers / argmax_k",
            [
                ("layers", samples["layers"].to_numpy(dtype=float), "#ff7f0e"),
                ("argmax K (ZF+MMSE)", samples["argmax_k_zf_mmse"].to_numpy(dtype=float), "#2ca02c"),
            ],
        ),
        ("RSRP", [("rsrp_dBm", samples["rsrp_dBm"].to_numpy(dtype=float), "#d62728")]),
        (
            "max sum_k log2(1+SINR_stream_k)",
            [("max ZF+MMSE capacity", samples["max_zf_mmse_capacity"].to_numpy(dtype=float), "#9467bd")],
        ),
        (
            "max CapacitySVD",
            [("max SVD capacity", samples["max_svd_capacity"].to_numpy(dtype=float), "#17becf")],
        ),
    ]

    fig, axes = plt.subplots(len(series_specs), 1, figsize=(12, 18), sharex=True)
    for ax, (title, series) in zip(axes, series_specs):
        series_means: List[Tuple[str, Dict[int, float]]] = []
        for label, values, color in series:
            mask = np.isfinite(values)
            ax.plot(x[mask], values[mask], marker="o", markersize=2.5, linestyle="-", linewidth=1.0, color=color, label=label)
            _draw_position_averages(ax, x, values, color, positions, samples_per_position)
            series_means.append((label, _position_mean_map(x, values, positions, samples_per_position)))
        ax.grid(alpha=0.3)
        ax.set_ylabel(title)
        ax.legend(loc="upper right", fontsize=8)
        position_labels: Dict[int, str] = {}
        short_names = {
            "layers": "layers",
            "argmax K (ZF+MMSE)": "argmaxK",
        }
        for pos in positions:
            parts: List[str] = []
            for label, pos_means in series_means:
                if pos not in pos_means:
                    continue
                short_name = short_names.get(label, label)
                if len(series) == 1:
                    parts.append(f"mean={pos_means[pos]:.3g}")
                else:
                    parts.append(f"{short_name}={pos_means[pos]:.3g}")
            position_labels[pos] = " ".join(parts)
        _draw_position_boundaries(ax, positions, samples_per_position, position_labels)

    axes[-1].set_xlabel("sample index = 30*(PositionID-1)+SampleIndex")
    fig.suptitle("OAI CSI + iperf Channel Analysis", fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, 0.985))

    output_path = output_path or os.path.join(os.path.dirname(analysis_csv), "figures", "channel_analysis_metrics.png")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


def plot_position_metrics(
    analysis_csv: str,
    cleaned_csv: Optional[str] = None,
    samples_per_position: int = 30,
    output_path: Optional[str] = None,
) -> str:
    """Create stacked per-position mean plots with one point per position."""
    analysis = pd.read_csv(analysis_csv)
    cleaned = None
    if cleaned_csv and os.path.exists(cleaned_csv):
        cleaned = pd.read_csv(cleaned_csv)

    samples = _build_sample_metrics(analysis, cleaned, samples_per_position)
    if samples.empty:
        raise ValueError("no samples to plot")

    positions = sorted(int(v) for v in samples["PositionID"].unique())
    x = np.asarray(positions, dtype=float)

    def _position_mean(column: str) -> np.ndarray:
        if column == "throughput_mbps":
            values = samples.groupby("PositionID")[column].apply(_top_half_mean)
        else:
            values = samples.groupby("PositionID")[column].mean()
        return values.reindex(positions).to_numpy(dtype=float)

    series_specs: List[Tuple[str, List[Tuple[str, np.ndarray, str]]]] = [
        ("Throughput (top-50% mean)", [("throughput_mbps", _position_mean("throughput_mbps"), "#1f77b4")]),
        (
            "layers / argmax_k",
            [
                ("layers", _position_mean("layers"), "#ff7f0e"),
                ("argmax K (ZF+MMSE)", _position_mean("argmax_k_zf_mmse"), "#2ca02c"),
            ],
        ),
        ("RSRP", [("rsrp_dBm", _position_mean("rsrp_dBm"), "#d62728")]),
        (
            "max sum_k log2(1+SINR_stream_k)",
            [("max ZF+MMSE capacity", _position_mean("max_zf_mmse_capacity"), "#9467bd")],
        ),
        (
            "max CapacitySVD",
            [("max SVD capacity", _position_mean("max_svd_capacity"), "#17becf")],
        ),
    ]

    fig, axes = plt.subplots(len(series_specs), 1, figsize=(12, 18), sharex=True)
    for ax, (title, series) in zip(axes, series_specs):
        for label, values, color in series:
            ax.plot(x, values, marker="o", markersize=6, linestyle="-", linewidth=1.0, color=color, label=label)
        ax.grid(alpha=0.3)
        ax.set_ylabel(title)
        ax.legend(loc="upper right", fontsize=8)

    axes[-1].set_xlabel("Position index")
    fig.suptitle("OAI CSI + iperf Position Means", fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, 0.985))

    output_path = output_path or os.path.join(
        os.path.dirname(analysis_csv), "figures", "channel_analysis_position_means.png"
    )
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path
