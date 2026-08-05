"""Markdown summary generation for the channel analysis pipeline."""

from __future__ import annotations

import os
from typing import Dict, List, Optional
import warnings

import numpy as np
import pandas as pd
from scipy import stats


def _safe_pearson(x: pd.Series, y: pd.Series) -> float:
    xv = x.to_numpy(dtype=float)
    yv = y.to_numpy(dtype=float)
    mask = np.isfinite(xv) & np.isfinite(yv)
    if mask.sum() < 2 or np.std(xv[mask]) == 0 or np.std(yv[mask]) == 0:
        return float("nan")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        value, _ = stats.pearsonr(xv[mask], yv[mask])
    return float(value)


def _safe_spearman(x: pd.Series, y: pd.Series) -> float:
    xv = x.to_numpy(dtype=float)
    yv = y.to_numpy(dtype=float)
    mask = np.isfinite(xv) & np.isfinite(yv)
    if mask.sum() < 2 or np.std(xv[mask]) == 0 or np.std(yv[mask]) == 0:
        return float("nan")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        value, _ = stats.spearmanr(xv[mask], yv[mask])
    return float(value)


def _fmt(value) -> str:
    if value is None:
        return "nan"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not np.isfinite(number):
        return "nan"
    if number != 0.0 and abs(number) < 0.001:
        return f"{number:.3e}"
    return f"{number:.3f}"


def _top_half_mean(values: pd.Series) -> float:
    finite = pd.to_numeric(values, errors="coerce").dropna().sort_values(ascending=False)
    if finite.empty:
        return float("nan")
    top_count = max(1, int(np.ceil(len(finite) / 2.0)))
    return float(finite.iloc[:top_count].mean())


def _markdown_table(df: pd.DataFrame, index_name: str = "metric") -> str:
    lines = [
        "| " + " | ".join([index_name] + [str(col) for col in df.columns]) + " |",
        "|" + "---|" * (len(df.columns) + 1),
    ]
    for index, row in df.iterrows():
        values = [_fmt(index)] + [_fmt(value) for value in row]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def _build_sample_metrics(analysis: pd.DataFrame) -> pd.DataFrame:
    rows: List[Dict] = []
    for (ts, pos, sample_idx), group in analysis.groupby(
        ["Timestamp", "PositionID", "SampleIndex"], sort=False
    ):
        first = group.iloc[0]
        row = {
            "Timestamp": ts,
            "PositionID": int(pos),
            "SampleIndex": int(sample_idx),
            "ThroughputMbps": pd.to_numeric(first.get("ThroughputMbps"), errors="coerce"),
            "RSRP_dBm": pd.to_numeric(first.get("RSRP_dBm"), errors="coerce"),
            "EffectiveSNR_dB": pd.to_numeric(first.get("EffectiveSNR_dB"), errors="coerce"),
            "Eigenvalue1": pd.to_numeric(first.get("Eigenvalue1"), errors="coerce"),
        }
        svd_values: List[float] = []
        zf_values: List[float] = []
        for _, krow in group.iterrows():
            k = int(pd.to_numeric(krow["K"], errors="coerce"))
            svd = pd.to_numeric(krow["CapacitySVD"], errors="coerce")
            zf = pd.to_numeric(krow["CapacityZFMMSE"], errors="coerce")
            row[f"CapacitySVD_K{k}"] = svd
            row[f"CapacityZFMMSE_K{k}"] = zf
            svd_values.append(float(svd) if np.isfinite(svd) else float("nan"))
            zf_values.append(float(zf) if np.isfinite(zf) else float("nan"))
        row["MaxSVDCapacity"] = float(np.nanmax(svd_values)) if any(np.isfinite(svd_values)) else float("nan")
        row["MaxZFMMSECapacity"] = float(np.nanmax(zf_values)) if any(np.isfinite(zf_values)) else float("nan")
        rows.append(row)
    return pd.DataFrame(rows)


def _metric_columns() -> List[str]:
    metrics = [
        "ThroughputMbps",
        "RSRP_dBm",
        "EffectiveSNR_dB",
        "Eigenvalue1",
    ]
    for k in range(1, 5):
        metrics.append(f"CapacitySVD_K{k}")
    for k in range(1, 5):
        metrics.append(f"CapacityZFMMSE_K{k}")
    metrics.extend(["MaxSVDCapacity", "MaxZFMMSECapacity"])
    return metrics


def _correlation_selection_table(samples: pd.DataFrame, metrics: List[str]) -> pd.DataFrame:
    position_throughput_means = samples.groupby("PositionID")["ThroughputMbps"].apply(_top_half_mean)
    optimal_throughput = float(position_throughput_means.max())
    optimal_candidates = position_throughput_means[
        np.isclose(position_throughput_means, optimal_throughput, rtol=1e-9, atol=1e-12)
    ].index
    optimal_position = int(min(optimal_candidates))

    rows: List[Dict] = []
    for metric in metrics:
        position_means = samples.groupby("PositionID")[metric].mean()
        if position_means.notna().any():
            max_value = float(position_means.max())
            candidates = position_means[
                np.isclose(position_means, max_value, rtol=1e-9, atol=1e-12)
            ].index
            selected_position = int(min(candidates))
            selected_actual_throughput = float(
                _top_half_mean(
                    samples.loc[samples["PositionID"] == selected_position, "ThroughputMbps"]
                )
            )
        else:
            selected_position = None
            selected_actual_throughput = float("nan")
        rows.append({
            "metric": metric,
            "pearson_with_throughput": _safe_pearson(samples[metric], samples["ThroughputMbps"]),
            "spearman_with_throughput": _safe_spearman(samples[metric], samples["ThroughputMbps"]),
            "selected_position": selected_position,
            "selected_actual_throughput_mbps": selected_actual_throughput,
            "optimal_throughput_position": optimal_position,
            "optimal_throughput_mbps": optimal_throughput,
            "throughput_gap_mbps": (
                optimal_throughput - selected_actual_throughput
                if selected_actual_throughput == selected_actual_throughput
                else float("nan")
            ),
        })
    return pd.DataFrame(rows)


def position_correlation_table(samples: pd.DataFrame, metrics: List[str]) -> pd.DataFrame:
    throughput_means = samples.groupby("PositionID")["ThroughputMbps"].apply(_top_half_mean)
    rows: List[Dict] = []

    for metric in metrics:
        if metric == "ThroughputMbps" or metric not in samples.columns:
            continue
        metric_means = samples.groupby("PositionID")[metric].mean()
        paired = pd.DataFrame({
            "top50_throughput_mbps": throughput_means,
            "metric_mean": metric_means,
        }).dropna()
        if len(paired) >= 2:
            pearson = _safe_pearson(paired["metric_mean"], paired["top50_throughput_mbps"])
            spearman = _safe_spearman(paired["metric_mean"], paired["top50_throughput_mbps"])
        else:
            pearson = float("nan")
            spearman = float("nan")
        rows.append({
            "metric": metric,
            "pearson_position_correlation": pearson,
            "spearman_position_correlation": spearman,
            "positions_used": len(paired),
        })

    return pd.DataFrame(rows)


def write_markdown_summary(
    analysis: pd.DataFrame,
    csv_path: str,
    csi_path: str,
    output_path: str,
    validation: Optional[Dict] = None,
    figure_path: Optional[str] = None,
    position_figure_path: Optional[str] = None,
) -> str:
    samples = _build_sample_metrics(analysis)
    metrics = [col for col in _metric_columns() if col in samples.columns]
    positions = sorted(samples["PositionID"].unique()) if not samples.empty else []

    position_means_rows: List[Dict] = []
    for metric in metrics:
        row = {"metric": metric}
        for pos in positions:
            values = samples.loc[samples["PositionID"] == pos, metric]
            if metric == "ThroughputMbps":
                row[f"P{pos}"] = _top_half_mean(values)
            else:
                finite = values.dropna()
                row[f"P{pos}"] = float(finite.mean()) if not finite.empty else float("nan")
        position_means_rows.append(row)
    position_means = pd.DataFrame(position_means_rows)

    corr_selection = _correlation_selection_table(samples, metrics)
    position_corr = position_correlation_table(samples, metrics)
    position_means = position_means.set_index("metric")
    corr_selection = corr_selection.set_index("metric")
    position_corr = position_corr.set_index("metric")
    non_throughput = corr_selection[corr_selection.index != "ThroughputMbps"].copy()
    non_throughput = non_throughput.dropna(subset=["throughput_gap_mbps"])
    if non_throughput.empty:
        best_note = "No non-throughput metric had a finite selection result."
    else:
        min_gap = float(non_throughput["throughput_gap_mbps"].min())
        tied = non_throughput[
            np.isclose(non_throughput["throughput_gap_mbps"], min_gap, rtol=1e-9, atol=1e-12)
        ]
        if len(tied) > 1:
            best_note = (
                f"Multiple non-throughput metrics are tied under the current data "
                f"(throughput gap {_fmt(min_gap)} Mbps), so no single predictor "
                f"conclusion can be drawn."
            )
            best_note += " This is expected because RSRP and CSI do not vary."
            best_row = None
        else:
            best_row = tied.iloc[0]
            best_note = (
                f"Under the current data, the best non-throughput predictor by this "
                f"selection procedure is `{best_row.name}` with throughput gap "
                f"{_fmt(best_row['throughput_gap_mbps'])} Mbps."
            )

    csv_stem = os.path.splitext(os.path.basename(csv_path))[0]
    lines = [
        "# OAI CSI + iperf Channel Analysis Summary",
        "",
        "## Inputs",
        "",
        f"- Source CSV: `{csv_path}`",
        f"- Source CSV stem: `{csv_stem}`",
        f"- CSI path: `{csi_path}`",
        f"- Samples: {len(samples)}",
        f"- Positions: {len(positions)}",
    ]
    if figure_path:
        lines.append(f"- Figure: `{figure_path}`")
    lines.extend(["", "## Per-position Metric Means", ""])
    lines.append(
        "For throughput, each position uses the mean of the highest-throughput "
        "top 50% of samples. Other metrics use all finite samples."
    )
    lines.append("")
    lines.append(_markdown_table(position_means))
    lines.extend(["", "## Per-position Throughput vs Metric Correlation", ""])
    lines.append(
        "Each row compares the per-position top-50% throughput mean with the "
        "per-position mean of the named metric."
    )
    lines.append("")
    lines.append(_markdown_table(position_corr))
    if position_figure_path:
        lines.extend(["", f"- Position figure: `{position_figure_path}`"])
    lines.extend(["", "## Throughput Correlations and Position Selection", ""])
    lines.append(
        "The correlation columns show Pearson and Spearman correlation between each "
        "channel metric and measured throughput. The selection procedure then chooses "
        "the position with the highest mean metric value for each predictor. "
        "`throughput_gap_mbps` is the difference between the top-50% throughput mean "
        "at the selected position and the best top-50% throughput position."
    )
    lines.append("")
    lines.append(_markdown_table(corr_selection))
    lines.append("")
    lines.append("NaN indicates that the metric has no variance in the current record.")
    lines.extend(["", "## Predictor Analysis", ""])
    lines.append(
        "The current record is not a validated real measurement set: RSRP is constant "
        "and the CSI snapshots do not vary, so these numbers are for validating the "
        "calculation pipeline rather than for drawing a final radio conclusion."
    )
    lines.append("")
    lines.append(best_note)
    lines.extend(["", "## Validation", ""])
    if validation:
        lines.append(f"- Validated subcarriers: {validation.get('validated_subcarriers', 0)}")
        lines.append(f"- Max power relative error: {_fmt(validation.get('max_power_relative_error', float('nan')))}")
        lines.append(f"- Max ZF trace relative error: {_fmt(validation.get('max_zf_trace_relative_error', float('nan')))}")
        lines.append(f"- Max negative SINR error: {_fmt(validation.get('max_sinr_negative_error', 0.0))}")
        lines.append(f"- Result: {validation.get('result', 'unknown')}")
    else:
        lines.append("- Validation summary not available.")
    lines.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        f.write("\n")
    return output_path
