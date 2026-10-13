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

from gui.analysis.analysis.correlation import (
    POSITION_PREDICTORS,
    PREDICTORS,
    safe_pearson,
)


DIRECTION_COLORS = {
    "ul": "#1f77b4",
    "dl": "#d62728",
}

LAYER_LINE_STYLES = {
    "ul": {"color": "#d62728", "linestyle": "-"},
    "dl": {"color": "#d62728", "linestyle": "--"},
}

METRIC_COLORS = {
    "throughput_mbps": "#1f77b4",
    "actual_layers": "#d62728",
    "rsrp_dBm": "#2ca02c",
    "max_rsrp_dBm": "#8c564b",
    "mcs_scheduled_se": "#7f7f7f",
    "shannon_capacity": "#9467bd",
    "svd_capacity": "#ff7f0e",
    "svd_optimal_stream": "#e377c2",
    "selected_stream_capacity": "#17becf",
    "position_covariance_capacity": "#e377c2",
}

METRIC_MARKERS = {
    "throughput_mbps": "o",
    "actual_layers": "s",
    "rsrp_dBm": "^",
    "max_rsrp_dBm": "v",
    "mcs_scheduled_se": "o",
    "shannon_capacity": "D",
    "svd_capacity": "v",
    "svd_optimal_stream": "P",
    "selected_stream_capacity": "X",
    "position_covariance_capacity": "P",
}

METRIC_NAMES = {
    "throughput_mbps": "Throughput",
    "actual_layers": "Actual layers",
    "rsrp_dBm": "RSRP",
    "max_rsrp_dBm": "Max antenna RSRP",
    "mcs_scheduled_se": "MCS scheduled SE",
    "shannon_capacity": "Shannon capacity",
    "svd_capacity": "SVD capacity",
    "svd_optimal_stream": "SVD selected layers",
    "selected_stream_capacity": "Selected-stream capacity",
    "position_covariance_capacity": "Position covariance capacity",
}


def _figure_dir(output_dir: str) -> str:
    path = os.path.join(output_dir, "figures")
    os.makedirs(path, exist_ok=True)
    return path


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


def _layer_step_coordinates(
    x: pd.Series,
    layers: pd.Series,
) -> tuple[np.ndarray, np.ndarray]:
    """Return connected horizontal layer segments centered on positions."""
    position = pd.to_numeric(x, errors="coerce").to_numpy(dtype=float)
    layer = pd.to_numeric(layers, errors="coerce").to_numpy(dtype=float)
    valid = np.isfinite(position) & np.isfinite(layer)
    position = position[valid]
    layer = layer[valid]
    if len(position) == 0:
        return np.empty(0), np.empty(0)

    order = np.argsort(position, kind="stable")
    position = position[order]
    layer = layer[order]
    position_min = float(position[0])
    position_max = float(position[-1])

    x_values = []
    y_values = []
    for position_value, layer_value in zip(position, layer):
        start = max(position_value - 0.5, position_min)
        end = min(position_value + 0.5, position_max)
        if x_values and start < x_values[-1]:
            start = x_values[-1]
        x_values.extend([start, end])
        y_values.extend([layer_value, layer_value])
    return np.asarray(x_values), np.asarray(y_values)


def _tx_antenna_count(df: pd.DataFrame) -> int | None:
    """Read the transmit-antenna count recorded by the analysis pipeline."""
    if "nb_tx_antennas" not in df.columns:
        return None
    values = pd.to_numeric(df["nb_tx_antennas"], errors="coerce")
    values = values[np.isfinite(values) & (values > 0)]
    if values.empty:
        return None
    return int(round(float(values.max())))


def _positional_max(group: pd.DataFrame, x: pd.Series, metric: str):
    """Return the index and coordinates of one position-level maximum."""
    if metric not in group.columns:
        return None
    values = pd.to_numeric(group[metric], errors="coerce")
    valid = values[np.isfinite(values)]
    if valid.empty:
        return None
    index = valid.idxmax()
    x_value = pd.to_numeric(x.loc[index], errors="coerce")
    if not np.isfinite(x_value):
        return None
    return index, float(x_value), float(valid.loc[index])


def _annotate_target_point(
    ax,
    x_value: float,
    y_value: float,
    text: str,
    color: str,
    vertical_offset: float,
) -> None:
    """Mark a target value at a predicted position."""
    ax.plot(
        [x_value],
        [y_value],
        marker="*",
        markersize=11,
        markeredgecolor="white",
        markeredgewidth=0.6,
        color=color,
        linestyle="none",
        clip_on=False,
        zorder=6,
    )
    x_min, x_max = ax.get_xlim()
    place_right = x_value < (x_min + x_max) / 2.0
    ax.annotate(
        text,
        (x_value, y_value),
        textcoords="offset points",
        xytext=(8 if place_right else -8, vertical_offset),
        ha="left" if place_right else "right",
        va="bottom" if vertical_offset >= 0 else "top",
        fontsize=8,
        color=color,
        bbox={
            "boxstyle": "round,pad=0.25",
            "facecolor": "white",
            "edgecolor": color,
            "alpha": 0.9,
        },
        arrowprops={
            "arrowstyle": "-",
            "color": color,
            "linewidth": 0.7,
        },
        annotation_clip=False,
        zorder=7,
    )


def _metric_label(metric: str) -> str:
    return METRIC_NAMES.get(metric, metric)


def _target_label(target_metric: str) -> str:
    return {
        "throughput_mbps": "Throughput",
        "mcs_scheduled_se": "MCS scheduled SE",
    }.get(target_metric, target_metric)


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
    predictors: list[str] = PREDICTORS,
    target: str = "throughput_mbps",
    target_label: str = "Throughput (Mbps)",
) -> None:
    rows = (len(predictors) + 1) // 2
    fig, axes = plt.subplots(rows, 2, figsize=(11, 4.5 * rows))
    for ax, predictor in zip(axes.flat, predictors):
        for direction, color in DIRECTION_COLORS.items():
            sub = df[df["direction"] == direction]
            if sub.empty or predictor not in sub.columns:
                continue
            x = pd.to_numeric(sub[predictor], errors="coerce").to_numpy(dtype=float)
            y = pd.to_numeric(sub[target], errors="coerce").to_numpy(dtype=float)
            mask = np.isfinite(x) & np.isfinite(y)
            ax.scatter(x[mask], y[mask], s=22, alpha=0.75, color=color, label=direction)
            xs, ys = _fit_line(x, y)
            if xs is not None:
                ax.plot(xs, ys, color=color, linestyle="--", alpha=0.85)
        ax.set_xlabel(predictor)
        ax.set_ylabel(target_label)
        ax.grid(alpha=0.3)
        if ax is axes.flat[0]:
            ax.legend()
    for ax in axes.flat[len(predictors) :]:
        ax.set_axis_off()
    fig.suptitle(title, fontsize=14)
    fig.tight_layout()
    fig.savefig(os.path.join(_figure_dir(output_dir), filename), dpi=200)
    plt.close(fig)


def plot_correlation_comparison(
    corr_second: pd.DataFrame,
    corr_position: pd.DataFrame,
    output_dir: str,
    filename: str = "correlation_comparison.png",
    title: str = "Channel Predictor Correlations by Analysis Mode and Direction",
    ylabel: str = "Pearson correlation with throughput",
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
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(_figure_dir(output_dir), filename), dpi=200)
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
    ax.set_title("SVD Predicted Rank Accuracy vs Measured Layers")
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
    corr_second_mcs: pd.DataFrame,
    corr_position_mcs: pd.DataFrame,
    stream_summary: pd.DataFrame,
    output_dir: str,
) -> None:
    mcs_predictors = [
        predictor for predictor in PREDICTORS
        if predictor != "mcs_scheduled_se"
    ]
    position_mcs_predictors = [
        predictor for predictor in POSITION_PREDICTORS
        if predictor != "mcs_scheduled_se"
    ]
    plot_scatter_predictors(
        second_df,
        output_dir,
        "scatter_second_level.png",
        "Measured Throughput vs Predictors (Per Second)",
        predictors=PREDICTORS,
    )
    plot_scatter_predictors(
        position_df,
        output_dir,
        "scatter_position_level.png",
        "Measured Throughput vs Predictors (Per Position)",
        predictors=POSITION_PREDICTORS,
    )
    plot_scatter_predictors(
        second_df,
        output_dir,
        "scatter_second_level_mcs_se.png",
        "MCS Scheduled SE vs Predictors (Per Second)",
        predictors=mcs_predictors,
        target="mcs_scheduled_se",
        target_label="MCS scheduled SE (bit/s/Hz)",
    )
    plot_scatter_predictors(
        position_df,
        output_dir,
        "scatter_position_level_mcs_se.png",
        "MCS Scheduled SE vs Predictors (Per Position)",
        predictors=position_mcs_predictors,
        target="mcs_scheduled_se",
        target_label="MCS scheduled SE (bit/s/Hz)",
    )
    plot_correlation_comparison(corr_second, corr_position, output_dir)
    plot_correlation_comparison(
        corr_second_mcs,
        corr_position_mcs,
        output_dir,
        filename="correlation_mcs_se_comparison.png",
        title="Channel Predictor Correlations with MCS Scheduled SE",
        ylabel="Pearson correlation with MCS scheduled SE",
    )
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
    for target, suffix in (
        ("throughput_mbps", "throughput"),
        ("mcs_scheduled_se", "mcs_se"),
    ):
        paths.append(
            _plot_timeseries_mode(
                second_df,
                os.path.join(figure_dir, f"second_{suffix}_metrics.png"),
                f"Per-Second Metrics for {_target_label(target)}",
                x_column="timestamp",
                target_metric=target,
            )
        )
        paths.append(
            _plot_timeseries_mode(
                position_df,
                os.path.join(figure_dir, f"position_{suffix}_metrics.png"),
                f"Per-Position Metrics for {_target_label(target)}",
                x_column="position_id",
                target_metric=target,
            )
        )
    return [path for path in paths if path]


def plot_timeseries_frame(
    df: pd.DataFrame,
    output_dir: str,
    filename: str,
    title: str,
    x_column: str | None = None,
    target_metric: str = "throughput_mbps",
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
        target_metric=target_metric,
    )


def _plot_timeseries_mode(
    df: pd.DataFrame,
    output_path: str,
    title: str,
    x_column: str,
    directions: List[str] = ("ul", "dl"),
    target_metric: str = "throughput_mbps",
) -> str | None:
    if df.empty:
        return None

    target_label = _target_label(target_metric)
    target_unit = "Mbps" if target_metric == "throughput_mbps" else "bit/s/Hz"
    panels = [
        (
            target_metric,
            "actual_layers",
            f"{target_label} ({target_unit})",
            "Layers",
        ),
        ("rsrp_dBm", None, "RSRP (dBm)", None),
        ("max_rsrp_dBm", None, "Max antenna RSRP (dBm)", None),
    ]
    if target_metric != "mcs_scheduled_se":
        panels.append(
            (
                "mcs_scheduled_se",
                None,
                "MCS scheduled SE (bit/s/Hz)",
                None,
            )
        )
    panels.extend([
        ("shannon_capacity", None, "Shannon capacity (bits/s/Hz)", None),
        ("svd_capacity", None, "SVD capacity (bits/s/Hz)", None),
        (
            "selected_stream_capacity",
            None,
            "Selected-stream capacity (bits/s/Hz)",
            None,
        ),
    ])
    if "position_covariance_capacity" in df.columns:
        panels.insert(
            -1,
            (
                "position_covariance_capacity",
                None,
                "Position covariance capacity (bits/s/Hz)",
                None,
            ),
        )
    fig, axes = plt.subplots(
        len(panels),
        1,
        figsize=(10, 16),
        sharex=True,
        squeeze=False,
    )

    is_position = x_column == "position_id"
    primary_lw = 2.5 if is_position else 1.0
    tx_antenna_count = _tx_antenna_count(
        df[df["direction"].isin(directions)]
    )

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

        show_secondary = secondary_metric is not None
        ax2 = None
        if show_secondary:
            ax2 = ax.twinx()

        for (set_name, direction_name), group in sub.groupby(
            ["set", "direction"], sort=True
        ):
            group = group.sort_values(x_column)
            x = _series_x(group, x_column)
            direction = str(direction_name).lower()

            if metric in group.columns:
                values = pd.to_numeric(group[metric], errors="coerce")
                ax.plot(
                    x,
                    values,
                    label=(
                        f"{set_name} {direction.upper()} "
                        f"{_metric_label(metric)}"
                    ),
                    **_series_kwargs(metric, x_column, primary_lw),
                )

            if ax2 is not None and secondary_metric in group.columns:
                values = pd.to_numeric(group[secondary_metric], errors="coerce")
                layer_style = LAYER_LINE_STYLES.get(
                    direction,
                    {"color": "#d62728", "linestyle": "-"},
                )
                layer_label = (
                    f"{set_name} "
                    f"{direction.upper() if direction else ''} streams"
                )
                if is_position:
                    layer_x, layer_y = _layer_step_coordinates(
                        group[x_column],
                        values,
                    )
                    ax2.plot(
                        layer_x,
                        layer_y,
                        label=layer_label,
                        linewidth=0.5,
                        **layer_style,
                    )
                else:
                    ax2.plot(
                        x,
                        values,
                        label=layer_label,
                        linewidth=0.5,
                        drawstyle="steps-post",
                        **layer_style,
                    )

        ax.set_ylabel(ylabel)
        ax.grid(alpha=0.3)

        if is_position:
            for (set_name, direction_name), group in sub.groupby(
                ["set", "direction"], sort=True
            ):
                group = group.sort_values(x_column)
                x = _series_x(group, x_column)
                target_values = pd.to_numeric(
                    group.get(target_metric),
                    errors="coerce",
                )

                rsrp_metric = (
                    "max_rsrp_dBm"
                    if "max_rsrp_dBm" in group.columns
                    else "rsrp_dBm"
                )
                max_rsrp = _positional_max(group, x, rsrp_metric)
                if max_rsrp is not None:
                    max_rsrp_idx, max_rsrp_pos, _ = max_rsrp
                    if metric in ("rsrp_dBm", "max_rsrp_dBm") or row_idx == 0:
                        ax.axvline(
                            max_rsrp_pos,
                            linestyle=":",
                            color=METRIC_COLORS["rsrp_dBm"],
                            alpha=0.8,
                        )
                    if row_idx == 0:
                        target_at_rsrp = float(target_values.loc[max_rsrp_idx])
                        _annotate_target_point(
                            ax,
                            max_rsrp_pos,
                            target_at_rsrp,
                            f"{target_at_rsrp:.1f} {target_unit}\nmax RSRP",
                            METRIC_COLORS["rsrp_dBm"],
                            10,
                        )

                max_capacity = _positional_max(group, x, "shannon_capacity")
                if max_capacity is not None:
                    max_cap_idx, max_cap_pos, _ = max_capacity
                    if metric == "shannon_capacity" or row_idx == 0:
                        ax.axvline(
                            max_cap_pos,
                            linestyle=":",
                            color=METRIC_COLORS["shannon_capacity"],
                            alpha=0.8,
                        )
                    if row_idx == 0:
                        target_at_cap = float(target_values.loc[max_cap_idx])
                        target_at_rsrp = (
                            float(target_values.loc[max_rsrp_idx])
                            if max_rsrp is not None
                            else float("nan")
                        )
                        if np.isfinite(target_at_rsrp) and target_at_rsrp > 0:
                            enhancement = (
                                (target_at_cap - target_at_rsrp)
                                / target_at_rsrp
                                * 100.0
                            )
                            capacity_text = (
                                f"{target_at_cap:.1f} {target_unit} "
                                f"({enhancement:+.1f}%)\n"
                                "max Shannon capacity"
                            )
                        else:
                            capacity_text = (
                                f"{target_at_cap:.1f} {target_unit}\n"
                                "max Shannon capacity"
                            )
                        _annotate_target_point(
                            ax,
                            max_cap_pos,
                            target_at_cap,
                            capacity_text,
                            METRIC_COLORS["shannon_capacity"],
                            -38,
                        )

        if ax2 is not None:
            ax2.set_ylabel("Transmission streams")
            if is_position and tx_antenna_count is not None:
                ax2.set_ylim(0, tx_antenna_count)
                ax2.set_yticks([0, tx_antenna_count])
            else:
                ax2.yaxis.set_major_locator(MaxNLocator(integer=True))

        if row_idx > 0:
            r = safe_pearson(
                pd.to_numeric(sub[metric], errors="coerce"),
                pd.to_numeric(sub[target_metric], errors="coerce"),
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
