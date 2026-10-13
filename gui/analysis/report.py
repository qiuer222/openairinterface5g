"""Markdown final report generation."""

from __future__ import annotations

import os

import numpy as np
import pandas as pd


def _fmt(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not np.isfinite(number):
        return "nan"
    if number != 0 and abs(number) < 0.001:
        return f"{number:.3e}"
    return f"{number:.3f}"


def _markdown_table(df: pd.DataFrame, index_name: str = "metric") -> str:
    if df.empty:
        return "No rows."
    lines = ["| " + " | ".join([index_name] + [str(c) for c in df.columns]) + " |"]
    lines.append("|" + "---|" * (len(df.columns) + 1))
    for index, row in df.iterrows():
        values = [_fmt(index if index_name == "metric" else row.get(index_name, index))]
        values += [_fmt(v) for v in row]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def _best_predictors(corr: pd.DataFrame) -> str:
    lines = []
    for direction, group in corr.groupby("direction"):
        valid = group.dropna(subset=["pearson", "spearman"])
        if valid.empty:
            lines.append(f"- {direction}: no finite Pearson correlation")
            continue
        pearson_best = valid.loc[valid["pearson"].abs().idxmax()]
        spearman_row = valid.loc[valid["spearman"].abs().idxmax()]
        lines.append(
            f"- {direction}: best Pearson = `{pearson_best['predictor']}` "
            f"({_fmt(pearson_best['pearson'])}); best Spearman = "
            f"`{spearman_row['predictor']}` ({_fmt(spearman_row['spearman'])})"
        )
    return "\n".join(lines)


def write_final_report(
    output_dir: str,
    *,
    second_df: pd.DataFrame,
    position_df: pd.DataFrame,
    corr_second: pd.DataFrame,
    corr_position: pd.DataFrame,
    corr_second_mcs: pd.DataFrame,
    corr_position_mcs: pd.DataFrame,
    regression_second: pd.DataFrame,
    regression_position: pd.DataFrame,
    stream_second: pd.DataFrame,
    stream_position: pd.DataFrame,
    validation_summary: dict,
    noise_power: float,
    noise_reference: dict | None,
    top_ratio: float,
    pair_tolerance_ms: float,
    channel_kind: str,
) -> str:
    output_path = os.path.join(output_dir, "final_report.md")
    if "normalization_snr_source" in second_df:
        snr_sources = (
            second_df["normalization_snr_source"]
            .value_counts(dropna=False)
            .to_dict()
        )
    else:
        snr_sources = {}
    noise_reference = noise_reference or {}
    reference_position = noise_reference.get("position_id")
    reference_snr_db = noise_reference.get("snr_db")
    reference_source = noise_reference.get("source", "unknown")
    if reference_position is None:
        reference_setting = "- Noise reference: explicitly supplied `--noise-power`"
    else:
        reference_setting = (
            "- Noise reference: "
            f"position `{reference_position}`, "
            f"`{reference_source}` = `{_fmt(reference_snr_db)}` dB"
        )
    normalization_note = (
        "Capacity metrics use the raw OAI `c16`/FFT channel matrix after the",
        "common `1/32768` conversion. The same fixed noise power is applied",
        "to every second-level and position-level sample.",
    )
    lines = [
        "# SRS/CSI Channel-Based Throughput Prediction Report",
        "",
        "## Analysis Setup",
        "",
        f"- Second-level samples: `{len(second_df)}`",
        f"- Position-level samples: `{len(position_df)}`",
        f"- Channel kind: `{channel_kind}`",
        "- Selected-stream capacity: first actual-layer singular values, "
        "normalized by transmit antennas and noise power",
        "- Position covariance capacity: mean covariance across second-level "
        "snapshots per subcarrier, then mean capacity over subcarriers",
        "- MCS scheduled SE: `TBS / (NPRB * 12 * symbols)` per second-level "
        "sample, averaged within each position",
        f"- Fixed common noise power: `{noise_power}`",
        reference_setting,
        f"- Noise reference SNR sources: `{snr_sources}`",
        f"- Retained throughput ratio per position: `{top_ratio:.0%}`",
        f"- CSV pairing tolerance: `{pair_tolerance_ms:.0f} ms`",
        "",
        *normalization_note,
        "",
        "## Validation",
        "",
        f"- Validated channel rows: `{validation_summary.get('rows', 0)}`",
        f"- Max Frobenius/eigenvalue relative error: `{_fmt(validation_summary.get('max_power_relative_error', float('nan')))}`",
        f"- Max SVD formula relative error: `{_fmt(validation_summary.get('max_svd_relative_error', float('nan')))}`",
        f"- Result: `{validation_summary.get('result', 'FAIL')}`",
        "",
        "## Per-Second Correlations with Throughput",
        "",
        _markdown_table(corr_second.set_index("predictor"), "predictor"),
        "",
        "## Per-Position Correlations with Throughput",
        "",
        _markdown_table(corr_position.set_index("predictor"), "predictor"),
        "",
        "## Per-Second Correlations with MCS Scheduled SE",
        "",
        _markdown_table(corr_second_mcs.set_index("predictor"), "predictor"),
        "",
        "## Per-Position Correlations with MCS Scheduled SE",
        "",
        _markdown_table(corr_position_mcs.set_index("predictor"), "predictor"),
        "",
        "## Best Predictor",
        "",
        "Per second:",
        "",
        _best_predictors(corr_second),
        "",
        "Per position:",
        "",
        _best_predictors(corr_position),
        "",
        "## Linear Regression",
        "",
        "Per second:",
        "",
        _markdown_table(regression_second.set_index("predictor"), "predictor"),
        "",
        "Per position:",
        "",
        _markdown_table(regression_position.set_index("predictor"), "predictor"),
        "",
        "## Stream Selection Accuracy",
        "",
        "Per second:",
        "",
        _markdown_table(stream_second.set_index("algorithm"), "algorithm"),
        "",
        "Per position:",
        "",
        _markdown_table(stream_position.set_index("algorithm"), "algorithm"),
        "",
        "## Outputs",
        "",
        "- `processed_second_level.csv`",
        "- `processed_position_level.csv`",
        "- `validation_report.csv` and `validation_summary.txt`",
        "- `correlation_second_level.csv`, `correlation_position_level.csv`",
        "- `correlation_second_level_mcs_se.csv`, "
        "`correlation_position_level_mcs_se.csv`",
        "- `regression_second_level.csv`, `regression_position_level.csv`",
        "- `stream_selection_second_level.csv`, `stream_selection_position_level.csv`",
        "- `stream_confusion_second_level.csv`, `stream_confusion_position_level.csv`",
        "- Publication figures under `figures/`",
        "",
    ]
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        f.write("\n")
    return output_path
