"""Matplotlib plots for the CSI correlation analysis."""

from __future__ import annotations

import os
from typing import Dict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def _ensure_figures(outdir: str) -> str:
    figures_dir = os.path.join(outdir, "figures")
    os.makedirs(figures_dir, exist_ok=True)
    return figures_dir


def plot_time_series(df: pd.DataFrame, outdir: str) -> None:
    figures = _ensure_figures(outdir)
    cols = [
        "throughput_mbps",
        "rsrp_dBm",
        "average_svd_capacity",
        "best_svd_capacity",
        "best_zf_capacity",
    ]
    fig, axes = plt.subplots(len(cols), 1, figsize=(10, 10), sharex=True)
    x = np.arange(len(df))
    for ax, col in zip(axes, cols):
        ax.plot(x, df[col], linewidth=1.4)
        ax.set_ylabel(col)
        ax.grid(alpha=0.3)
    axes[-1].set_xlabel("Sample")
    fig.suptitle("OAI CSI Performance Metrics Over Recorded Samples")
    fig.tight_layout()
    fig.savefig(os.path.join(figures, "time_series.png"), dpi=130)
    plt.close(fig)


def plot_scatter(df: pd.DataFrame, outdir: str) -> None:
    figures = _ensure_figures(outdir)
    predictors = ["rsrp_dBm", "average_svd_capacity", "best_svd_capacity", "best_zf_capacity"]
    fig, axes = plt.subplots(2, 2, figsize=(10, 8))
    for ax, predictor in zip(axes.flat, predictors):
        ax.scatter(df[predictor], df["throughput_mbps"], s=22, alpha=0.8)
        ax.set_xlabel(predictor)
        ax.set_ylabel("Throughput (Mbps)")
        ax.grid(alpha=0.3)
    fig.suptitle("Measured Throughput vs Channel-Quality Predictors")
    fig.tight_layout()
    fig.savefig(os.path.join(figures, "scatter_throughput_vs_predictors.png"), dpi=130)
    plt.close(fig)


def plot_predictions(
    df: pd.DataFrame,
    predictions: Dict[str, np.ndarray],
    outdir: str,
) -> None:
    figures = _ensure_figures(outdir)
    predictors = ["rsrp_dBm", "average_svd_capacity", "best_svd_capacity", "best_zf_capacity"]
    for method in ("linear", "poly"):
        fig, axes = plt.subplots(2, 2, figsize=(10, 8))
        for ax, predictor in zip(axes.flat, predictors):
            key = f"{method}_{predictor}"
            if key not in predictions:
                continue
            y_true = df["throughput_mbps"].to_numpy(dtype=float)
            y_pred = predictions[key]
            mask = np.isfinite(y_true) & np.isfinite(y_pred)
            ax.scatter(y_true[mask], y_pred[mask], s=22, alpha=0.8)
            lo, hi = np.nanmin(np.concatenate([y_true[mask], y_pred[mask]])), np.nanmax(np.concatenate([y_true[mask], y_pred[mask]]))
            ax.plot([lo, hi], [lo, hi], "--", color="gray", label="y=x")
            ax.set_xlabel("Measured (Mbps)")
            ax.set_ylabel("Predicted (Mbps)")
            ax.set_title(predictor)
            ax.grid(alpha=0.3)
            ax.legend()
        fig.suptitle(f"Predicted vs Measured Throughput ({method})")
        fig.tight_layout()
        fig.savefig(os.path.join(figures, f"predicted_vs_measured_{method}.png"), dpi=130)
        plt.close(fig)


def plot_all(df: pd.DataFrame, predictions: Dict[str, np.ndarray], outdir: str) -> None:
    plot_time_series(df, outdir)
    plot_scatter(df, outdir)
    plot_predictions(df, predictions, outdir)

