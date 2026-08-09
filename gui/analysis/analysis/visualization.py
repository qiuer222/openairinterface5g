"""Publication-oriented matplotlib figures for the analysis pipeline."""

from __future__ import annotations

import os
from typing import List

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import MaxNLocator

from gui.analysis.analysis.correlation import PREDICTORS, safe_pearson


DIRECTION_COLORS = {
    "ul": "#1f77b4",
    "dl": "#d62728",
}

METRIC_COLORS = {
    "throughput_mbps": "#1f77b4",
    "actual_layers": "#d62728",
    "rsrp_dBm": "#2ca02c",
    "shannon_capacity": "#9467bd",
    "svd_capacity": "#ff7f0e",
    "svd_optimal_stream": "#e377c2",
    "zf_capacity": "#17becf",
    "zf_optimal_stream": "#bcbd22",
}

METRIC_MARKERS = {
    "throughput_mbps": "o",
    "actual_layers": "s",
    "rsrp_dBm": "^",
    "shannon_capacity": "D",
    "svd_capacity": "v",
    "svd_optimal_stream": "P",
    "zf_capacity": "X",
    "zf_optimal_stream": "*",
}

METRIC_NAMES = {
    "throughput_mbps": "Throughput",
    "actual_layers": "Actual layers",
    "rsrp_dBm": "RSRP",
    "shannon_capacity": "Shannon capacity",
    "svd_capacity": "SVD capacity",
    "svd_optimal_stream": "SVD selected layers",
    "zf_capacity": "ZF capacity",
    "zf_optimal_stream": "ZF selected layers",
}


def _figure_dir(output_dir: str) -> str:
    path = os.path.join(output_dir, "figures")
    os.makedirs(path, exist_ok=True)
    return path


def _slug(name: str) -> str:
    return name.replace("_", "-").replace(".", "")


def _fit_line(x, y):
    mask = np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 2 or np.std(x[mask]) == 0:
        return None, None
    slope, intercept = np.polyfit(x[mask], y[mask], 1)
    xs = np.linspace(np.min(x[mask]), np.max(x[mask]), 100)
    return xs, slope * xs + intercept


def _series_x(group: pd.DataFrame, x_column: str) -> pd.Series:
    if x_column == "timestamp":
        return pd.Series(np.arange(len(group)), index=group.index)
    return pd.to_numeric(group[x_column], errors="coerce")


def _series_kwargs(metric: str, x_column: str, linewidth: float) -> dict:
    kwargs = {
        "color": METRIC_COLORS.get(metric, "#333333"),
        "linewidth": linewidth,
        "alpha": 0.9,
    }
    if x_column == "position_id":
        kwargs["marker"] = METRIC_MARKERS.get(metric, ".")
        kwargs["markersize"] = 4.0
    return kwargs


def _metric_label(metric: str) -> str:
    return METRIC_NAMES.get(metric, metric)


def _add_correlation_label(ax, r: float) -> None:
    text = f"r = {r:.2f}" if np.isfinite(r) else "r = n/a"
    ax.text(
        0.02,
        0.95,
        text,
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=9,
        bbox={
            "boxstyle": "round,pad=0.25",
            "facecolor": "white",
            "edgecolor": "0.6",
            "alpha": 0.85,
        },
    )


def plot_scatter_predictors(
    df: pd.DataFrame,
    output_dir: str,
    filename: str,
    title: str,
) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(11, 9))
    for ax, predictor in zip(axes.flat, PREDICTORS):
        for direction, color in DIRECTION_COLORS.items():
            sub = df[df["direction"] == direction]
            if sub.empty or predictor not in sub.columns:
                continue
            x = pd.to_numeric(sub[predictor], errors="coerce").to_numpy(dtype=float)
            y = pd.to_numeric(sub["throughput_mbps"], errors="coerce").to_numpy(dtype=float)
            mask = np.isfinite(x) & np.isfinite(y)
            ax.scatter(x[mask], y[mask], s=22, alpha=0.75, color=color, label=direction)
            xs, ys = _fit_line(x, y)
            if xs is not None:
                ax.plot(xs, ys, color=color, linestyle="--", alpha=0.85)
        ax.set_xlabel(predictor)
        ax.set_ylabel("Throughput (Mbps)")
        ax.grid(alpha=0.3)
        if ax is axes.flat[0]:
            ax.legend()
    fig.suptitle(title, fontsize=14)
    fig.tight_layout()
    fig.savefig(os.path.join(_figure_dir(output_dir), filename), dpi=200)
    plt.close(fig)


def plot_correlation_comparison(
    corr_second: pd.DataFrame,
    corr_position: pd.DataFrame,
    output_dir: str,
) -> None:
    combined = pd.concat([corr_second, corr_position], ignore_index=True)
    combined = combined.dropna(subset=["pearson"]).copy()
    if combined.empty:
        return
    combined["mode_direction"] = (
        combined["mode"] + " " + combined["direction"]
    )
    fig, ax = plt.subplots(figsize=(11, 7))
    pivot = combined.pivot_table(
        index="predictor",
        columns="mode_direction",
        values="pearson",
        aggfunc="mean",
    )
    pivot.plot(kind="bar", ax=ax)
    ax.set_ylabel("Pearson correlation with throughput")
    ax.set_title("CSI Predictor Correlations by Analysis Mode and Direction")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(_figure_dir(output_dir), "correlation_comparison.png"), dpi=200)
    plt.close(fig)


def plot_stream_selection(
    summary: pd.DataFrame,
    output_dir: str,
) -> None:
    if summary.empty:
        return
    valid = summary.dropna(subset=["accuracy"]).copy()
    if valid.empty:
        return
    valid["label"] = (
        valid["mode"] + " " + valid["direction"] + " " + valid["algorithm"]
    )
    fig, ax = plt.subplots(figsize=(11, 6))
    ax.bar(valid["label"], valid["accuracy"] * 100.0, color="#4c72b0")
    ax.set_xticks(np.arange(len(valid)))
    ax.set_ylabel("Stream prediction accuracy (%)")
    ax.set_title("SVD and ZF Predicted Rank Accuracy vs Measured Layers")
    ax.set_xticklabels(valid["label"], rotation=45, ha="right")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(_figure_dir(output_dir), "stream_selection_accuracy.png"), dpi=200)
    plt.close(fig)


def plot_second_vs_position(
    corr_second: pd.DataFrame,
    corr_position: pd.DataFrame,
    output_dir: str,
) -> None:
    second = corr_second[["direction", "predictor", "pearson"]].rename(
        columns={"pearson": "second_pearson"}
    )
    position = corr_position[["direction", "predictor", "pearson"]].rename(
        columns={"pearson": "position_pearson"}
    )
    merged = second.merge(position, on=["direction", "predictor"], how="inner").dropna()
    if merged.empty:
        return
    fig, ax = plt.subplots(figsize=(8, 6))
    for direction, color in DIRECTION_COLORS.items():
        sub = merged[merged["direction"] == direction]
        if not sub.empty:
            ax.scatter(
                sub["second_pearson"],
                sub["position_pearson"],
                s=50,
                color=color,
                label=direction,
            )
    ax.set_xlabel("Per-second Pearson correlation")
    ax.set_ylabel("Per-position Pearson correlation")
    ax.set_title("Per-second vs Per-position Predictor Correlations")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(_figure_dir(output_dir), "second_vs_position_correlation.png"), dpi=200)
    plt.close(fig)


def plot_all(
    second_df: pd.DataFrame,
    position_df: pd.DataFrame,
    corr_second: pd.DataFrame,
    corr_position: pd.DataFrame,
    stream_summary: pd.DataFrame,
    output_dir: str,
) -> None:
    plot_scatter_predictors(
        second_df,
        output_dir,
        "scatter_second_level.png",
        "Measured Throughput vs Predictors (Per Second)",
    )
    plot_scatter_predictors(
        position_df,
        output_dir,
        "scatter_position_level.png",
        "Measured Throughput vs Predictors (Per Position)",
    )
    plot_correlation_comparison(corr_second, corr_position, output_dir)
    plot_stream_selection(stream_summary, output_dir)
    plot_second_vs_position(corr_second, corr_position, output_dir)


def plot_timeseries(
    second_df: pd.DataFrame,
    position_df: pd.DataFrame,
    output_dir: str,
) -> List[str]:
    """Plot time/position series from processed CSV frames without reprocessing."""
    figure_dir = _figure_dir(output_dir)
    paths = []

    paths.append(
        _plot_timeseries_mode(
            second_df,
            os.path.join(figure_dir, "timeseries_second.png"),
            "Per-Second Time Series",
            x_column="timestamp",
        )
    )
    paths.append(
        _plot_timeseries_mode(
            position_df,
            os.path.join(figure_dir, "timeseries_position.png"),
            "Per-Position Mean Time Series",
            x_column="position_id",
        )
    )
    return [path for path in paths if path]


def plot_timeseries_frame(
    df: pd.DataFrame,
    output_dir: str,
    filename: str,
    title: str,
    x_column: str | None = None,
) -> str | None:
    """Plot one processed CSV frame to a single figure."""
    if x_column is None:
        x_column = "timestamp" if "timestamp" in df.columns else "position_id"
    directions = sorted(df["direction"].dropna().unique().tolist())
    return _plot_timeseries_mode(
        df,
        os.path.join(_figure_dir(output_dir), filename),
        title,
        x_column=x_column,
        directions=directions,
    )


def _plot_timeseries_mode(
    df: pd.DataFrame,
    output_path: str,
    title: str,
    x_column: str,
    directions: List[str] = ("ul", "dl"),
) -> str | None:
    if df.empty:
        return None

    panels = [
        ("throughput_mbps", "actual_layers", "Throughput (Mbps)", "Layers"),
        ("rsrp_dBm", None, "RSRP (dBm)", None),
        ("shannon_capacity", None, "Shannon capacity (bits/s/Hz)", None),
        ("svd_capacity", "svd_optimal_stream", "SVD capacity (bits/s/Hz)", "Selected layers"),
        ("zf_capacity", "zf_optimal_stream", "ZF capacity (bits/s/Hz)", "Selected layers"),
    ]
    fig, axes = plt.subplots(
        len(panels),
        1,
        figsize=(10, 16),
        sharex=True,
        squeeze=False,
    )

    is_position = x_column == "position_id"
    primary_lw = 2.5 if is_position else 1.0

    for row_idx, (metric, secondary_metric, ylabel, secondary_ylabel) in enumerate(panels):
        ax = axes[row_idx][0]
        sub = df[df["direction"].isin(directions)]
        if sub.empty:
            ax.text(
                0.5,
                0.5,
                f"No {'/'.join(d.upper() for d in directions)} samples",
                transform=ax.transAxes,
                ha="center",
                va="center",
            )
            ax.set_axis_off()
            continue

        show_secondary = secondary_metric is not None and not is_position
        ax2 = None
        if show_secondary:
            ax2 = ax.twinx()

        for set_name, group in sub.groupby("set", sort=True):
            group = group.sort_values(x_column)
            x = _series_x(group, x_column)

            if metric in group.columns:
                values = pd.to_numeric(group[metric], errors="coerce")
                ax.plot(
                    x,
                    values,
                    label=f"{set_name} {_metric_label(metric)}",
                    **_series_kwargs(metric, x_column, primary_lw),
                )

            if ax2 is not None and secondary_metric in group.columns:
                values = pd.to_numeric(group[secondary_metric], errors="coerce")
                ax2.plot(
                    x,
                    values,
                    label=f"{set_name} {_metric_label(secondary_metric)}",
                    **_series_kwargs(secondary_metric, x_column, 0.5),
                )

        ax.set_ylabel(ylabel)
        ax.grid(alpha=0.3)

        if is_position:
            for set_name, group in sub.groupby("set", sort=True):
                group = group.sort_values(x_column)
                x = _series_x(group, x_column)
                throughput = pd.to_numeric(group.get("throughput_mbps"), errors="coerce")

                rsrp = pd.to_numeric(group.get("rsrp_dBm"), errors="coerce")
                rsrp_valid = rsrp.dropna()
                if not rsrp_valid.empty:
                    max_rsrp_idx = rsrp_valid.idxmax()
                    max_rsrp_pos = float(x.loc[max_rsrp_idx])
                    if row_idx in (0, 1):
                        ax.axvline(
                            max_rsrp_pos,
                            linestyle=":",
                            color=METRIC_COLORS["rsrp_dBm"],
                            alpha=0.8,
                        )
                    if row_idx == 0:
                        throughput_at_rsrp = float(throughput.loc[max_rsrp_idx])
                        ax.plot(
                            [max_rsrp_pos],
                            [throughput_at_rsrp],
                            marker="o",
                            markersize=7,
                            color=METRIC_COLORS["rsrp_dBm"],
                            linestyle="none",
                            zorder=5,
                        )
                        ax.annotate(
                            f"{throughput_at_rsrp:.1f}",
                            (max_rsrp_pos, throughput_at_rsrp),
                            textcoords="offset points",
                            xytext=(0, 8),
                            ha="center",
                            fontsize=8,
                            color=METRIC_COLORS["rsrp_dBm"],
                        )

                capacity = pd.to_numeric(group.get("shannon_capacity"), errors="coerce")
                capacity_valid = capacity.dropna()
                if not capacity_valid.empty:
                    max_cap_idx = capacity_valid.idxmax()
                    max_cap_pos = float(x.loc[max_cap_idx])
                    if row_idx in (0, 2):
                        ax.axvline(
                            max_cap_pos,
                            linestyle=":",
                            color=METRIC_COLORS["shannon_capacity"],
                            alpha=0.8,
                        )
                    if row_idx == 0:
                        throughput_at_cap = float(throughput.loc[max_cap_idx])
                        ax.plot(
                            [max_cap_pos],
                            [throughput_at_cap],
                            marker="o",
                            markersize=7,
                            color=METRIC_COLORS["shannon_capacity"],
                            linestyle="none",
                            zorder=5,
                        )
                        if (
                            not rsrp_valid.empty
                            and float(throughput.loc[max_rsrp_idx]) > 0
                        ):
                            enhancement = (
                                (throughput_at_cap - float(throughput.loc[max_rsrp_idx]))
                                / float(throughput.loc[max_rsrp_idx])
                                * 100.0
                            )
                            ax.annotate(
                                f"{throughput_at_cap:.1f} (+{enhancement:.1f}%)",
                                (max_cap_pos, throughput_at_cap),
                                textcoords="offset points",
                                xytext=(0, -12),
                                ha="center",
                                fontsize=8,
                                color=METRIC_COLORS["shannon_capacity"],
                            )

        if ax2 is not None:
            ax2.set_ylabel(secondary_ylabel)
            ax2.yaxis.set_major_locator(MaxNLocator(integer=True))

        if row_idx in (1, 2, 3, 4):
            r = safe_pearson(
                pd.to_numeric(sub[metric], errors="coerce"),
                pd.to_numeric(sub["throughput_mbps"], errors="coerce"),
            )
            _add_correlation_label(ax, r)

        if ax2 is not None:
            handles1, labels1 = ax.get_legend_handles_labels()
            handles2, labels2 = ax2.get_legend_handles_labels()
            ax.legend(
                handles1 + handles2,
                labels1 + labels2,
                loc="upper right",
                fontsize=7,
            )
        else:
            ax.legend(loc="upper right", fontsize=7)

    axes[-1][0].set_xlabel(
        "Sample index" if x_column == "timestamp" else "Position ID"
    )

    fig.suptitle(title, fontsize=14)
    fig.tight_layout()
    fig.savefig(output_path, dpi=200)
    plt.close(fig)
    return output_path
