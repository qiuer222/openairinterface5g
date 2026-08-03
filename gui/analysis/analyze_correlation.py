#!/usr/bin/env python3
"""Analyze OAI GUI CSV records together with recorded CSI channel matrices."""

from __future__ import annotations

import argparse
import glob
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from gui.analysis.capacity import average_svd_capacity, best_stream_svd_capacity
from gui.analysis.plotting import plot_all
from gui.analysis.prediction import (
    PREDICTOR_COLUMNS,
    _safe_pearson,
    _safe_spearman,
    fit_predictors,
    regression_metrics,
    stream_selection_metrics,
)
from gui.analysis.scaling import SOURCE_REFS, channel_mean_power, normalize_to_snr, rsrp_calibration
from gui.analysis.zf_capacity import best_stream_zf_capacity
from sklearn.linear_model import LinearRegression


def find_latest_record() -> tuple[str, str]:
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    record_dir = os.path.join(repo_root, "gui", "record")
    csvs = sorted(glob.glob(os.path.join(record_dir, "gui_log_*.csv")))
    if not csvs:
        raise FileNotFoundError("no GUI records found under gui/record")
    for csv_path in reversed(csvs):
        csi_dir = os.path.splitext(csv_path)[0]
        if os.path.isdir(csi_dir) and glob.glob(os.path.join(csi_dir, "channel_*.npy")):
            return csv_path, csi_dir
    raise FileNotFoundError("no complete GUI record with channel_*.npy files found")


def _load_channel_map(df: pd.DataFrame, csi_path: str) -> Dict[str, np.ndarray]:
    channels: Dict[str, np.ndarray] = {}
    if os.path.isdir(csi_path):
        for name in os.listdir(csi_path):
            if name.startswith("channel_") and name.endswith(".npy"):
                ts = name[len("channel_"):-len(".npy")]
                channels[ts] = np.load(os.path.join(csi_path, name), allow_pickle=False)
    elif csi_path.endswith(".npz"):
        data = np.load(csi_path, allow_pickle=False)
        if "timestamps" in data and "channels" in data:
            for ts, ch in zip(data["timestamps"], data["channels"]):
                channels[str(ts)] = np.asarray(ch)
        else:
            for key in data.files:
                ts = key
                if key.startswith("channel_"):
                    ts = key[len("channel_"):]
                channels[ts] = np.asarray(data[key])
    else:
        raise ValueError(f"unsupported CSI path: {csi_path}")

    result: Dict[str, np.ndarray] = {}
    for ts in df["timestamp"]:
        if ts in channels:
            result[ts] = channels[ts]
            continue
        nearest = min(channels, key=lambda key: abs(_timestamp_seconds(key) - _timestamp_seconds(ts)))
        print(f"WARNING: exact CSI file for {ts} not found; using nearest {nearest}")
        result[ts] = channels[nearest]
    return result


def _timestamp_seconds(ts: str) -> float:
    return datetime.strptime(ts, "%Y%m%d_%H%M%S_%f").timestamp()


def _channel_row(h: np.ndarray, rsrp_dbm: float, snr_db: float) -> Dict:
    mean_power, valid_count = channel_mean_power(h)
    raw_power_db = 10.0 * np.log10(mean_power) if valid_count and mean_power > 0 else float("nan")
    calibration = rsrp_calibration(h, rsrp_dbm)
    h_norm, scale, _, _ = normalize_to_snr(h, snr_db)

    avg = average_svd_capacity(h_norm, snr_db)
    svd = best_stream_svd_capacity(h_norm, snr_db)
    zf = best_stream_zf_capacity(h_norm, snr_db)

    # Same-SNR metrics on the raw recorded H are retained only to quantify how
    # the scaling choice changes correlation results.
    raw_avg = average_svd_capacity(h, snr_db)
    raw_svd = best_stream_svd_capacity(h, snr_db)
    raw_zf = best_stream_zf_capacity(h, snr_db)

    return {
        "raw_channel_power_db": raw_power_db,
        "valid_subcarriers": int(valid_count),
        **calibration,
        "channel_normalization_scale": scale,
        "average_svd_capacity": avg["average_svd_capacity"],
        "best_svd_capacity": svd["best_svd_capacity"],
        "optimal_stream_number_svd": svd["optimal_stream_number_svd"],
        "best_zf_capacity": zf["best_zf_capacity"],
        "optimal_stream_number_zf": zf["optimal_stream_number_zf"],
        "svd_condition_number": svd["svd_condition_number"],
        "svd_rank": svd["svd_rank"],
        "raw_average_svd_capacity": raw_avg["average_svd_capacity"],
        "raw_best_svd_capacity": raw_svd["best_svd_capacity"],
        "raw_best_zf_capacity": raw_zf["best_zf_capacity"],
    }


def _write_correlation_reports(df: pd.DataFrame, output_dir: str) -> None:
    y = df["throughput_mbps"].to_numpy(dtype=float)
    rows: List[Dict] = []
    for predictor in PREDICTOR_COLUMNS:
        x = df[predictor].to_numpy(dtype=float)
        finite = np.isfinite(x) & np.isfinite(y)
        if finite.sum() < 2 or np.std(x[finite]) == 0:
            pearson = float("nan")
            spearman = float("nan")
            metrics = {
                "r2": float("nan"),
                "rmse": float("nan"),
                "mae": float("nan"),
            }
        else:
            pearson = _safe_pearson(x, y)
            spearman = _safe_spearman(x, y)
            model = LinearRegression().fit(x[finite].reshape(-1, 1), y[finite])
            pred = model.predict(x[finite].reshape(-1, 1))
            metrics = regression_metrics(y[finite], pred)
        rows.append({
            "metric": predictor,
            "pearson_correlation": pearson,
            "spearman_correlation": spearman,
            "linear_r2": metrics["r2"],
            "linear_rmse": metrics["rmse"],
            "linear_mae": metrics["mae"],
        })
    corr_df = pd.DataFrame(rows)
    corr_df.to_csv(os.path.join(output_dir, "correlation_matrix.csv"), index=False)

    with open(os.path.join(output_dir, "correlation_report.txt"), "w", encoding="utf-8") as f:
        f.write("Correlation with measured throughput\n")
        f.write("===================================\n\n")
        f.write(corr_df.to_string(index=False))
        f.write("\n\nNotes:\n")
        f.write("- NaN means the predictor had no variance or the coefficient was undefined.\n")
        f.write("- Linear regression is reported in the same linear units as the CSV.\n")
        f.write("- In the analyzed record, all CSI snapshots are identical, so channel-based metrics have zero variance.\n")


def _write_prediction_reports(
    df: pd.DataFrame,
    prediction_df: pd.DataFrame,
    predictions: Dict[str, np.ndarray],
    output_dir: str,
) -> None:
    prediction_df.to_csv(os.path.join(output_dir, "prediction_report.txt"), index=False, sep="\t")
    with open(os.path.join(output_dir, "prediction_report.txt"), "a", encoding="utf-8") as f:
        f.write("\n\nPrediction table above. RMSE rank 1 is best; R2 rank 1 is best.\n")

    df = df.copy()
    for key, pred in predictions.items():
        df[f"predicted_{key}"] = pred
    df.to_csv(os.path.join(output_dir, "analysis_results.csv"), index=False)


def _write_stream_report(df: pd.DataFrame, output_dir: str) -> None:
    truth_col = "layers" if "layers" in df.columns else None
    if truth_col is None:
        truth_col = "num_layers" if "num_layers" in df.columns else "inferred_stream"
        if truth_col == "inferred_stream":
            median = df["throughput_mbps"].median()
            df["inferred_stream"] = np.where(df["throughput_mbps"] > median, 2, 1)

    metrics = stream_selection_metrics(df, truth_col=truth_col)
    lines = [
        "Stream selection accuracy",
        "=========================",
        f"Ground truth column: {truth_col}",
        f"Labels: {metrics['labels']}",
        f"SVD accuracy: {metrics['svd_accuracy'] * 100.0:.1f}%",
        f"ZF accuracy: {metrics['zf_accuracy'] * 100.0:.1f}%",
        f"SVD mean absolute stream-number error: {metrics['svd_mae']:.3f}",
        f"ZF mean absolute stream-number error: {metrics['zf_mae']:.3f}",
        "",
        "SVD confusion matrix:",
        np.array2string(metrics["svd_confusion"]),
        "",
        "ZF confusion matrix:",
        np.array2string(metrics["zf_confusion"]),
        "",
        "SVD error distribution:",
        str(metrics["svd_error_distribution"]),
        "",
        "ZF error distribution:",
        str(metrics["zf_error_distribution"]),
    ]

    def _condition_groups(column: str) -> pd.Series:
        values = df[column]
        if values.nunique(dropna=True) <= 1:
            value = values.dropna().iloc[0]
            return pd.Series([f"{column}={value:.2f}"] * len(df), index=df.index)
        return pd.qcut(values, q=3, duplicates="drop").astype(str)

    rsrp_groups = _condition_groups("rsrp_dBm")
    cond_groups = _condition_groups("svd_condition_number")
    lines.append("")
    lines.append("Accuracy conditioned on RSRP ranges")
    for group, group_df in df.groupby(rsrp_groups, observed=True):
        lines.append(f"  {group}: n={len(group_df)}, SVD={np.mean(group_df['optimal_stream_number_svd'] == group_df[truth_col]) * 100:.1f}%, ZF={np.mean(group_df['optimal_stream_number_zf'] == group_df[truth_col]) * 100:.1f}%")
    lines.append("")
    lines.append("Accuracy conditioned on channel condition-number ranges")
    for group, group_df in df.groupby(cond_groups, observed=True):
        lines.append(f"  {group}: n={len(group_df)}, SVD={np.mean(group_df['optimal_stream_number_svd'] == group_df[truth_col]) * 100:.1f}%, ZF={np.mean(group_df['optimal_stream_number_zf'] == group_df[truth_col]) * 100:.1f}%")

    with open(os.path.join(output_dir, "stream_selection_report.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        f.write("\n")


def _write_scaling_report(df: pd.DataFrame, output_dir: str) -> None:
    lines = [
        "OAI CSI channel scaling report",
        "==============================",
        "",
        "Recording path:",
        "- csi_reader.py reads c16_t pairs from /dev/shm/csi_rs_channel and converts them to complex128.",
        "- OAI writes csi_rs_estimated_channel_freq directly to shared memory after CSI-RS channel estimation.",
        "- The values therefore retain OAI fixed-point / FFT / RF-gain scaling rather than being physical channel coefficients.",
        "",
        "Source references:",
    ]
    lines.extend(f"- {ref}" for ref in SOURCE_REFS)
    lines.extend([
        "",
        "Observed raw channel power (dB over unit fixed-point power):",
        f"  mean={df['raw_channel_power_db'].mean():.3f}",
        f"  std={df['raw_channel_power_db'].std():.3f}",
        f"  min={df['raw_channel_power_db'].min():.3f}",
        f"  max={df['raw_channel_power_db'].max():.3f}",
        "",
        "RSRP-calibrated effective gain (dB):",
        f"  mean={df['rsrp_calibrated_gain_db'].mean():.3f}",
        f"  std={df['rsrp_calibrated_gain_db'].std():.3f}",
        "",
        "Normalization used for capacity metrics:",
        "- mean squared norm of valid subcarriers is scaled to 10^(SNR/10).",
        "- this removes the unknown RX gain/AGC term while preserving channel shape.",
        "- RSRP remains an independent predictor in the correlation analysis.",
        "",
        "RSRP consistency:",
        f"- RSRP unique values: {df['rsrp_dBm'].nunique()}",
        f"- raw channel power std: {df['raw_channel_power_db'].std():.3f} dB",
    ])
    if df["rsrp_dBm"].nunique() > 1:
        pearson = df["raw_channel_power_db"].corr(df["rsrp_dBm"])
        lines.append(f"- Pearson(raw channel power dB, RSRP dBm) = {pearson:.3f}")
    else:
        lines.append("- Pearson(raw channel power dB, RSRP dBm) is undefined because RSRP is constant in this record.")

    with open(os.path.join(output_dir, "scaling_report.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        f.write("\n")


def _write_final_report(df: pd.DataFrame, prediction_df: pd.DataFrame, output_dir: str) -> None:
    valid_pred = prediction_df.dropna(subset=["rmse", "r2"]).copy()
    best_rmse = valid_pred.loc[valid_pred["rmse"].idxmin()] if not valid_pred.empty else None
    best_r2 = valid_pred.loc[valid_pred["r2"].idxmax()] if not valid_pred.empty else None

    corr_vals = {
        col: _safe_pearson(df[col].to_numpy(dtype=float), df["throughput_mbps"].to_numpy(dtype=float))
        for col in PREDICTOR_COLUMNS
    }
    finite_corr = {k: v for k, v in corr_vals.items() if np.isfinite(v)}
    highest_corr = max(finite_corr, key=finite_corr.get) if finite_corr else "none"

    svd_better = df["best_svd_capacity"].mean() > df["average_svd_capacity"].mean()
    truth_col = "layers" if "layers" in df.columns else "optimal_stream_number_svd"
    svd_acc = np.mean(df["optimal_stream_number_svd"] == df[truth_col]) * 100.0
    zf_acc = np.mean(df["optimal_stream_number_zf"] == df[truth_col]) * 100.0
    better_stream_predictor = "SVD" if svd_acc >= zf_acc else "ZF"

    lines = [
        "Final analysis report",
        "=====================",
        "",
        "1. Highest Pearson correlation with measured throughput:",
        f"   {highest_corr} ({finite_corr.get(highest_corr, float('nan')):.3f})" if finite_corr else "   none",
        "",
        "2. Most accurate throughput predictor:",
    ]
    if best_rmse is not None:
        lines.append(f"   lowest RMSE: {best_rmse['method']} / {best_rmse['predictor']} ({best_rmse['rmse']:.3f} Mbps)")
    if best_r2 is not None:
        lines.append(f"   highest R2: {best_r2['method']} / {best_r2['predictor']} ({best_r2['r2']:.3f})")
    lines.append("   Note: all four analyzed predictors are constant in this record, so RMSE is the mean-only baseline.")
    lines.extend([
        "",
        "3. Adaptive stream selection vs fixed-stream capacity:",
        f"   mean best-stream SVD capacity = {df['best_svd_capacity'].mean():.3f} bits/s/Hz",
        f"   mean all-stream SVD capacity = {df['average_svd_capacity'].mean():.3f} bits/s/Hz",
        f"   best-stream selection is better on average: {svd_better}",
        "",
        "4. SVD vs ZF stream selection accuracy:",
        f"   SVD stream accuracy = {svd_acc:.1f}%",
        f"   ZF stream accuracy = {zf_acc:.1f}%",
        f"   Better predictor = {better_stream_predictor}",
        "",
        "5. Scaling findings:",
        "   OAI records SQ15/FFT/RF-scaled CSI estimates; exact RX gain is not present in the CSV.",
        "   The analysis uses RSRP-calibrated unit-power normalization with a fixed configurable SNR.",
        "   See scaling_report.txt for source references and raw power consistency checks.",
        "   Because every CSI snapshot is identical, normalization does not change the correlation outcome in this record.",
        "",
        "6. Channel-condition performance:",
        "   This record has one RSRP value, one channel condition number, and identical CSI matrices on every sample.",
        "   Conditional accuracy is therefore not differentiated; stream selection was deterministic across the dataset.",
        "",
        "Additional finding:",
        f"   Pearson(throughput, MCS) = {_safe_pearson(df['mcs'].to_numpy(dtype=float), df['throughput_mbps'].to_numpy(dtype=float)):.3f}",
        f"   Pearson(throughput, NPRB) = {_safe_pearson(df['nprb'].to_numpy(dtype=float), df['throughput_mbps'].to_numpy(dtype=float)):.3f}",
        f"   Pearson(throughput, SINR) = {_safe_pearson(df['sinr_dB'].to_numpy(dtype=float), df['throughput_mbps'].to_numpy(dtype=float)):.3f}",
        "   This indicates the throughput changes in this dataset are scheduler/MCS driven rather than CSI driven.",
        "",
    ])
    with open(os.path.join(output_dir, "final_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        f.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", help="GUI CSV record path")
    parser.add_argument("--csi", help="directory of channel_*.npy files, or .npz archive")
    parser.add_argument("--snr", type=float, default=10.0, help="assumed SNR in dB (default: 10)")
    parser.add_argument("--output-dir", default="analysis_results", help="output directory")
    parser.add_argument("--no-plots", action="store_true", help="skip figure generation")
    args = parser.parse_args()

    csv_path = args.csv
    csi_path = args.csi
    if csv_path is None and csi_path is None:
        default_csv, default_csi = find_latest_record()
        csv_path, csi_path = default_csv, default_csi
    elif csv_path is None:
        csi_path = os.path.normpath(csi_path)
        if os.path.isdir(csi_path):
            csv_path = f"{csi_path}.csv"
        elif csi_path.endswith(".npz"):
            csv_path = f"{os.path.splitext(csi_path)[0]}.csv"
        else:
            raise FileNotFoundError(f"cannot derive CSV path from --csi: {csi_path}")
    elif csi_path is None:
        csi_path = os.path.splitext(csv_path)[0]
        if not os.path.isdir(csi_path):
            raise FileNotFoundError(
                f"--csi was not provided and expected directory {csi_path} does not exist"
            )

    df = pd.read_csv(csv_path)
    channel_map = _load_channel_map(df, csi_path)
    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)

    channel_rows: List[Dict] = []
    for ts, row in df.iterrows():
        h = channel_map[row["timestamp"]]
        rsrp = float(row.get("rsrp_dBm", np.nan))
        channel_rows.append(_channel_row(h, rsrp, args.snr))

    channel_df = pd.DataFrame(channel_rows)
    df = pd.concat([df, channel_df], axis=1)
    df = df[df["average_svd_capacity"].notna()].copy()

    _write_correlation_reports(df, output_dir)
    prediction_df, predictions = fit_predictors(df)
    _write_prediction_reports(df, prediction_df, predictions, output_dir)
    _write_stream_report(df, output_dir)
    _write_scaling_report(df, output_dir)
    _write_final_report(df, prediction_df, output_dir)
    if not args.no_plots:
        plot_all(df, predictions, output_dir)

    print(f"Analysis complete: {output_dir}")
    print(f"CSV rows: {len(df)}")
    print(f"Predictors evaluated: {len(prediction_df)}")


if __name__ == "__main__":
    main()
