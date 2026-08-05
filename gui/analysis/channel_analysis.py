#!/usr/bin/env python3
"""Run the OAI CSI + iperf channel analysis described by analyze.md."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List

import numpy as np
import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from gui.analysis.csi_matcher import load_channel_map, match_csi
from gui.analysis.channel_plotting import plot_channel_analysis, plot_position_metrics
from gui.analysis.data_cleaner import clean_measurements
from gui.analysis.mimo_analysis import MAX_STREAMS, analyze_channel, effective_snr_db
from gui.analysis.summary import write_markdown_summary


def _write_text(path: str, lines: List[str]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        f.write("\n")


def _build_analysis_rows(cleaned: pd.DataFrame) -> pd.DataFrame:
    records: List[Dict] = []

    for _, row in cleaned.iterrows():
        rsrp = float(row.get("rsrp_dBm", np.nan))
        snr_db = effective_snr_db(rsrp) if np.isfinite(rsrp) else float("nan")
        rho = 10.0 ** (snr_db / 10.0) if np.isfinite(snr_db) else float("nan")
        csi_file = row.get("CSI_File", "")

        eigen = [float("nan")] * 4
        svd = [float("nan")] * 4
        zf = [float("nan")] * 4
        sinr = np.zeros((4, 4))

        if isinstance(csi_file, str) and csi_file:
            h = np.load(csi_file, allow_pickle=False)
            metrics = analyze_channel(h, rsrp)
            eigen = metrics["eigenvalues"].tolist()
            svd = metrics["svd_capacities"].tolist()
            zf = metrics["zf_capacities"].tolist()
            sinr = metrics["sinr_matrix"]

        base = {
            "Timestamp": row["timestamp"],
            "PositionID": row["PositionID"],
            "SampleIndex": row["SampleIndex"],
            "ThroughputMbps": row["throughput_mbps"],
            "RSRP_dBm": rsrp,
            "EffectiveSNR_dB": snr_db,
            "EffectiveSNR_Linear": rho,
            "Eigenvalue1": eigen[0],
            "Eigenvalue2": eigen[1],
            "Eigenvalue3": eigen[2],
            "Eigenvalue4": eigen[3],
        }

        for k in range(1, MAX_STREAMS + 1):
            record = {
                **base,
                "K": k,
                "CapacitySVD": svd[k - 1],
                "CapacityZFMMSE": zf[k - 1],
                "SINR_Stream1": sinr[k - 1, 0],
                "SINR_Stream2": sinr[k - 1, 1],
                "SINR_Stream3": sinr[k - 1, 2],
                "SINR_Stream4": sinr[k - 1, 3],
            }
            records.append(record)

    return pd.DataFrame(records)


def _write_validation_report(
    cleaned: pd.DataFrame,
    output_dir: str,
    tolerance: float = 1e-6,
) -> Dict:
    records: List[Dict] = []
    max_power_rel = 0.0
    max_trace_rel = 0.0
    max_sinr_negative = 0.0
    checked = 0

    for _, row in cleaned.iterrows():
        csi_file = row.get("CSI_File", "")
        if not isinstance(csi_file, str) or not csi_file:
            continue
        h = np.load(csi_file, allow_pickle=False)
        rsrp = float(row.get("rsrp_dBm", np.nan))
        metrics = analyze_channel(h, rsrp)
        if not metrics["validation"]:
            continue
        checked += len(metrics["validation"])
        row_power_rel = max(entry["power_relative_error"] for entry in metrics["validation"])
        finite_trace = [entry["zf_trace_relative_error"] for entry in metrics["validation"] if np.isfinite(entry["zf_trace_relative_error"])]
        row_trace_rel = max(finite_trace) if finite_trace else 0.0
        row_sinr_negative = max(entry["sinr_negative_error"] for entry in metrics["validation"])
        max_power_rel = max(max_power_rel, row_power_rel)
        max_trace_rel = max(max_trace_rel, row_trace_rel)
        max_sinr_negative = max(max_sinr_negative, row_sinr_negative)
        reason = ""
        if row_power_rel > tolerance:
            reason = "Frobenius power does not match eigenvalue sum"
        if row_trace_rel > tolerance:
            reason += "; ZF trace(W W^H) deviates from K"
        if row_sinr_negative > tolerance:
            reason += "; negative SINR detected"
        records.append({
            "row_id": f"{row['PositionID']}-{row['SampleIndex']}",
            "csi_file": os.path.basename(csi_file),
            "validated_subcarriers": len(metrics["validation"]),
            "expected_power": max(entry["frobenius_norm2"] for entry in metrics["validation"]),
            "eigenvalue_sum": max(entry["eigenvalue_sum"] for entry in metrics["validation"]),
            "power_relative_error": row_power_rel,
            "zf_trace_relative_error": row_trace_rel,
            "sinr_negative_error": row_sinr_negative,
            "possible_reason": reason,
        })

    pd.DataFrame(records).to_csv(os.path.join(output_dir, "validation_report.csv"), index=False)
    passed = checked > 0 and max_power_rel <= tolerance and max_trace_rel <= tolerance and max_sinr_negative <= tolerance
    summary = [
        "Validation summary",
        "==================",
        f"validated subcarriers: {checked}",
        f"max power relative error: {max_power_rel:.3e}",
        f"max ZF trace relative error: {max_trace_rel:.3e}",
        f"max negative SINR error: {max_sinr_negative:.3e}",
        f"tolerance: {tolerance:.1e}",
        f"result: {'PASS' if passed else 'FAIL'}",
    ]
    _write_text(os.path.join(output_dir, "validation_summary.txt"), summary)
    if not passed:
        raise RuntimeError("mathematical validation failed; see validation_report.csv")
    return {
        "validated_subcarriers": checked,
        "max_power_relative_error": max_power_rel,
        "max_zf_trace_relative_error": max_trace_rel,
        "max_sinr_negative_error": max_sinr_negative,
        "result": "PASS",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", help="GUI CSV measurement file")
    parser.add_argument("--csi", help="CSI directory or npz; defaults to CSV stem")
    parser.add_argument("--gap-threshold-s", type=float, default=3.0, help="idle gap threshold (default: 3.0)")
    parser.add_argument("--max-samples-per-position", type=int, default=30)
    parser.add_argument("--csi-tolerance-ms", type=float, default=200.0)
    parser.add_argument("--snr-offset-db", type=float, default=100.0)
    parser.add_argument("--output-dir", default="gui/channel_analysis_results")
    parser.add_argument("--no-plots", action="store_true", help="skip figure generation")
    args = parser.parse_args()

    csv_path = args.csv
    csi_path = args.csi
    if csv_path is None:
        raise SystemExit("--csv is required")
    if csi_path is None:
        csi_path = os.path.splitext(csv_path)[0]
        print(f"--csi not provided; derived from CSV stem: {csi_path}")

    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)
    csv_stem = os.path.splitext(os.path.basename(csv_path))[0]

    df = pd.read_csv(csv_path)
    cleaned, cleaning_logs = clean_measurements(
        df,
        gap_threshold_s=args.gap_threshold_s,
        max_samples_per_position=args.max_samples_per_position,
    )
    cleaned.to_csv(os.path.join(output_dir, "cleaned_measurement.csv"), index=False)
    cleaning_log = [
        "CSV cleaning log",
        "================",
        f"input rows: {len(df)}",
        f"cleaned rows: {len(cleaned)}",
        f"positions: {int(cleaned['PositionID'].nunique()) if not cleaned.empty else 0}",
        "",
        *cleaning_logs,
    ]
    _write_text(os.path.join(output_dir, "cleaning_log.txt"), cleaning_log)

    channel_map = load_channel_map(csi_path)
    csi_paths, matched_timestamps, match_warnings = match_csi(
        cleaned,
        channel_map,
        tolerance_ms=args.csi_tolerance_ms,
    )
    cleaned["CSI_File"] = csi_paths
    cleaned["CSI_Timestamp"] = matched_timestamps
    matching_log = [
        "CSI matching log",
        "================",
        f"CSV rows: {len(cleaned)}",
        f"CSI files found: {len(channel_map)}",
        f"matched rows: {int(csi_paths.notna().sum())}",
        f"missing rows: {int(csi_paths.isna().sum())}",
        "",
        *match_warnings,
    ]
    _write_text(os.path.join(output_dir, "csi_matching_log.txt"), matching_log)

    analysis = _build_analysis_rows(cleaned)
    analysis.to_csv(os.path.join(output_dir, "channel_analysis.csv"), index=False)
    validation = _write_validation_report(cleaned, output_dir)

    plot_path = None
    position_plot_path = None
    if not args.no_plots:
        plot_path = plot_channel_analysis(
            analysis_csv=os.path.join(output_dir, "channel_analysis.csv"),
            cleaned_csv=os.path.join(output_dir, "cleaned_measurement.csv"),
            samples_per_position=args.max_samples_per_position,
            output_path=os.path.join(output_dir, "figures", f"channel_analysis_{csv_stem}.png"),
        )
        position_plot_path = plot_position_metrics(
            analysis_csv=os.path.join(output_dir, "channel_analysis.csv"),
            cleaned_csv=os.path.join(output_dir, "cleaned_measurement.csv"),
            samples_per_position=args.max_samples_per_position,
            output_path=os.path.join(
                output_dir,
                "figures",
                f"channel_analysis_position_means_{csv_stem}.png",
            ),
        )

    markdown_summary = write_markdown_summary(
        analysis=analysis,
        csv_path=csv_path,
        csi_path=csi_path,
        output_path=os.path.join(output_dir, "analysis_summary.md"),
        validation=validation,
        figure_path=plot_path,
        position_figure_path=position_plot_path,
    )

    summary = [
        "Analysis complete",
        "=================",
        f"CSV: {csv_path}",
        f"CSI: {csi_path}",
        f"cleaned rows: {len(cleaned)}",
        f"matched CSI rows: {int(csi_paths.notna().sum())}",
        f"channel_analysis rows: {len(analysis)}",
        f"markdown summary: {markdown_summary}",
        f"output directory: {output_dir}",
    ]
    if plot_path:
        summary.append(f"plot: {plot_path}")
    if position_plot_path:
        summary.append(f"position plot: {position_plot_path}")
    _write_text(os.path.join(output_dir, "analysis_summary.txt"), summary)
    print("\n".join(summary))


if __name__ == "__main__":
    main()
