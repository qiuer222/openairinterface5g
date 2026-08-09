#!/usr/bin/env python3
"""Run the CSI-based throughput prediction framework."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List

import numpy as np
import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(
        0,
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    )

from gui.analysis.analysis.correlation import correlation_table
from gui.analysis.analysis.regression import regression_table
from gui.analysis.analysis.visualization import plot_all
from gui.analysis.capacity.shannon import shannon_capacity_from_eigenvalues
from gui.analysis.capacity.svd import svd_capacities_from_eigenvalues
from gui.analysis.capacity.zf_mmse import aggregate_zf_mmse_from_svd
from gui.analysis.csi_parser import load_channel_and_valid
from gui.analysis.data_loader import (
    MeasurementSet,
    discover_measurement_sets,
    load_measurement_frame,
    parse_timestamp,
)
from gui.analysis.feature_extraction import extract_channel_features
from gui.analysis.normalization import NOISE_POWER_DEFAULT, raw_channel_power
from gui.analysis.report import write_final_report
from gui.analysis.stream_selection import (
    select_optimal_stream,
    stream_selection_tables,
)


EPS = 1e-12


def _load_csi_map(csi_dir: str) -> Dict[float, str]:
    result: Dict[float, str] = {}
    for name in sorted(os.listdir(csi_dir)):
        if not (name.startswith("channel_") and name.endswith(".npy")):
            continue
        ts = name[len("channel_") : -len(".npy")]
        parsed = parse_timestamp(ts)
        if parsed is not None:
            result[parsed.timestamp()] = os.path.join(csi_dir, name)
    return result


def _match_csi(
    df: pd.DataFrame,
    csi_map: Dict[float, str],
    tolerance_ms: float,
) -> pd.DataFrame:
    paths = []
    timestamps = []
    deltas = []
    warnings = []
    keys = np.asarray(sorted(csi_map), dtype=float)
    for ts in df["timestamp"]:
        parsed = parse_timestamp(ts)
        if parsed is None:
            paths.append(None)
            timestamps.append(None)
            deltas.append(float("nan"))
            warnings.append(f"could not parse timestamp {ts}")
            continue
        epoch = parsed.timestamp()
        if epoch in csi_map:
            paths.append(csi_map[epoch])
            timestamps.append(epoch)
            deltas.append(0.0)
            continue
        if keys.size == 0:
            paths.append(None)
            timestamps.append(None)
            deltas.append(float("nan"))
            warnings.append(f"no CSI files for timestamp {ts}")
            continue
        index = int(np.argmin(np.abs(keys - epoch)))
        nearest = keys[index]
        delta_ms = abs(nearest - epoch) * 1000.0
        if delta_ms <= tolerance_ms:
            paths.append(csi_map[nearest])
            timestamps.append(nearest)
            deltas.append(delta_ms)
            if delta_ms > 0:
                warnings.append(f"nearest CSI for {ts}: {nearest:.3f} ({delta_ms:.1f} ms)")
        else:
            paths.append(None)
            timestamps.append(None)
            deltas.append(float("nan"))
            warnings.append(f"no CSI within {tolerance_ms:.0f} ms for {ts}")

    df = df.copy()
    df["csi_file"] = paths
    df["csi_timestamp"] = timestamps
    df["csi_match_delta_ms"] = deltas
    return df, warnings


def _optimal_from_capacities(capacities: List[float]) -> int:
    values = np.asarray(capacities, dtype=float)
    if len(values) == 0 or not np.any(np.isfinite(values)):
        return 0
    return int(np.nanargmax(values) + 1)


def process_measurements(
    df: pd.DataFrame,
    noise_power: float,
    max_streams: int = 4,
) -> pd.DataFrame:
    """Build second-level rows with features, capacities, and validation data."""
    rows: List[dict] = []
    validation: List[dict] = []

    for record in df.to_dict("records"):
        csi_file = record.get("csi_file")
        base = {
            "timestamp": record.get("timestamp"),
            "set": record.get("set"),
            "direction": record.get("direction"),
            "test_round": record.get("test_round"),
            "position_id": record.get("position_id"),
            "throughput_mbps": record.get("throughput_mbps"),
            "actual_layers": record.get("layers"),
            "mcs": record.get("mcs"),
            "rsrp_dBm": record.get("rsrp_dBm"),
            "csi_file": csi_file,
            "csi_timestamp": record.get("csi_timestamp"),
            "csi_match_delta_ms": record.get("csi_match_delta_ms"),
            "gnb_timestamp": record.get("gnb_timestamp"),
            "gnb_match_delta_ms": record.get("gnb_match_delta_ms"),
        }
        row = dict(base)
        val = dict(base)
        try:
            if not isinstance(csi_file, str) or not csi_file:
                raise ValueError("no CSI file matched")
            h, valid = load_channel_and_valid(csi_file)
            if len(valid) == 0:
                raise ValueError("no valid CSI subcarriers")

            eigenvalue_rows = []
            svd_rows = []
            max_power_rel = 0.0
            max_svd_rel = 0.0
            raw_power_sum = 0.0
            for k in valid:
                hk = h[:, :, k]
                u, s, vh = np.linalg.svd(hk, full_matrices=False)
                eigenvalues = s ** 2
                eigenvalue_rows.append(eigenvalues)
                svd_rows.append((u, s, vh))
                frobenius2 = float(np.sum(np.abs(hk) ** 2))
                eigenvalue_sum = float(np.sum(eigenvalues))
                max_power_rel = max(
                    max_power_rel,
                    abs(frobenius2 - eigenvalue_sum) / max(frobenius2, EPS),
                )
                raw_power_sum += frobenius2

                for kk in range(1, len(eigenvalues) + 1):
                    cap_eig = float(
                        np.sum(
                            np.log2(
                                1.0
                                + np.maximum(eigenvalues[:kk], 0.0)
                                / (float(kk) * float(noise_power))
                            )
                        )
                    )
                    cap_sv = float(
                        np.sum(
                            np.log2(
                                1.0
                                + np.maximum(s[:kk] ** 2, 0.0)
                                / (float(kk) * float(noise_power))
                            )
                        )
                    )
                    denominator = max(abs(cap_eig), EPS)
                    max_svd_rel = max(
                        max_svd_rel, abs(cap_eig - cap_sv) / denominator
                    )

            features = extract_channel_features(
                [s for _u, s, _vh in svd_rows],
                valid_subcarrier_count=len(valid),
            )
            shannon = shannon_capacity_from_eigenvalues(
                eigenvalue_rows,
                noise_power=noise_power,
                tx_count=h.shape[1],
            )
            svd_capacities = svd_capacities_from_eigenvalues(
                eigenvalue_rows,
                noise_power=noise_power,
                max_streams=max_streams,
            )
            zf = aggregate_zf_mmse_from_svd(
                svd_rows,
                noise_power=noise_power,
                max_streams=max_streams,
            )

            raw_power = raw_power_sum / len(valid)
            row.update(features)
            row.update(
                {
                    "raw_channel_power": raw_power,
                    "raw_channel_power_db": (
                        float(10.0 * np.log10(raw_power))
                        if raw_power > 0
                        else float("nan")
                    ),
                    "shannon_capacity": shannon,
                    "svd_capacity": float("nan"),
                    "svd_optimal_stream": 0,
                    "zf_capacity": float("nan"),
                    "zf_optimal_stream": 0,
                }
            )
            for idx, capacity in enumerate(svd_capacities):
                row[f"svd_capacity_k{idx + 1}"] = capacity
            for idx in range(max_streams):
                key = f"svd_capacity_k{idx + 1}"
                if key not in row:
                    row[key] = float("nan")
            zf_capacities = zf["capacities"]
            for idx, capacity in enumerate(zf_capacities):
                row[f"zf_capacity_k{idx + 1}"] = capacity
            for idx in range(max_streams):
                key = f"zf_capacity_k{idx + 1}"
                if key not in row:
                    row[key] = float("nan")

            row["svd_optimal_stream"] = _optimal_from_capacities(svd_capacities)
            row["zf_optimal_stream"] = _optimal_from_capacities(zf_capacities)
            if row["svd_optimal_stream"] > 0:
                row["svd_capacity"] = row[f"svd_capacity_k{row['svd_optimal_stream']}"]
            if row["zf_optimal_stream"] > 0:
                row["zf_capacity"] = row[f"zf_capacity_k{row['zf_optimal_stream']}"]

            for k in range(1, max_streams + 1):
                sinrs = zf["sinr_matrix"][k - 1, :k]
                row[f"sinr_k{k}_mean"] = float(np.nanmean(sinrs)) if np.any(np.isfinite(sinrs)) else float("nan")
            for stream in range(1, max_streams + 1):
                row[f"sinr_optimal_stream{stream}"] = float("nan")
            if row["zf_optimal_stream"] > 0:
                k = row["zf_optimal_stream"]
                sinrs = zf["sinr_matrix"][k - 1, :]
                for stream in range(1, max_streams + 1):
                    if np.isfinite(sinrs[stream - 1]):
                        row[f"sinr_optimal_stream{stream}"] = float(sinrs[stream - 1])

            val.update(
                {
                    "validated_subcarriers": int(len(valid)),
                    "max_power_relative_error": max_power_rel,
                    "max_svd_relative_error": max_svd_rel,
                    "max_zf_trace_relative_error": float(
                        zf["max_trace_relative_error"]
                    ),
                    "max_sinr_negative_error": float(
                        zf["max_sinr_negative_error"]
                    ),
                    "raw_channel_power": raw_power,
                    "eigenvalue_sum": features["eigenvalue_sum"],
                    "possible_reason": "",
                }
            )
        except Exception as exc:  # noqa: BLE001 - keep the pipeline resumable
            row.update(
                {
                    "valid_subcarriers": 0,
                    "eigenvalue_1": float("nan"),
                    "eigenvalue_2": float("nan"),
                    "eigenvalue_3": float("nan"),
                    "eigenvalue_4": float("nan"),
                    "singular_value_1": float("nan"),
                    "singular_value_2": float("nan"),
                    "singular_value_3": float("nan"),
                    "singular_value_4": float("nan"),
                    "eigenvalue_sum": float("nan"),
                    "frobenius_norm2": float("nan"),
                    "channel_rank": float("nan"),
                    "condition_number": float("nan"),
                    "eigenvalue_sum_std": float("nan"),
                    "condition_number_std": float("nan"),
                    "raw_channel_power": float("nan"),
                    "raw_channel_power_db": float("nan"),
                    "shannon_capacity": float("nan"),
                    "svd_capacity": float("nan"),
                    "svd_optimal_stream": 0,
                    "zf_capacity": float("nan"),
                    "zf_optimal_stream": 0,
                }
            )
            for k in range(1, max_streams + 1):
                row[f"svd_capacity_k{k}"] = float("nan")
                row[f"zf_capacity_k{k}"] = float("nan")
                row[f"sinr_k{k}_mean"] = float("nan")
            for stream in range(1, max_streams + 1):
                row[f"sinr_optimal_stream{stream}"] = float("nan")
            val.update(
                {
                    "validated_subcarriers": 0,
                    "max_power_relative_error": float("nan"),
                    "max_svd_relative_error": float("nan"),
                    "max_zf_trace_relative_error": float("nan"),
                    "max_sinr_negative_error": float("nan"),
                    "raw_channel_power": float("nan"),
                    "eigenvalue_sum": float("nan"),
                    "possible_reason": str(exc),
                }
            )
        rows.append(row)
        validation.append(val)

    return pd.DataFrame(rows), pd.DataFrame(validation)


def aggregate_position_level(
    second_df: pd.DataFrame,
    top_ratio: float = 0.5,
) -> pd.DataFrame:
    """Aggregate the retained top-throughput samples per position."""
    group_cols = ["set", "direction", "position_id", "test_round"]
    rows = []
    exclude = {
        "timestamp",
        "set",
        "direction",
        "position_id",
        "test_round",
        "csi_file",
        "csi_timestamp",
        "csi_match_delta_ms",
        "gnb_timestamp",
        "gnb_match_delta_ms",
    }
    agg_cols = [col for col in second_df.columns if col not in exclude]

    for (set_name, direction, position_id, test_round), group in second_df.groupby(
        group_cols, dropna=False, sort=True
    ):
        if len(group) == 0:
            continue
        sorted_group = group.sort_values("throughput_mbps", ascending=False)
        keep_count = max(1, int(np.ceil(len(sorted_group) * top_ratio)))
        keep = sorted_group.head(keep_count)
        row = {
            "set": set_name,
            "direction": direction,
            "position_id": position_id,
            "test_round": test_round,
            "samples_in_position": len(group),
            "retained_samples": len(keep),
            "throughput_mbps": float(np.mean(pd.to_numeric(keep["throughput_mbps"], errors="coerce"))),
        }
        for col in agg_cols:
            if col in ("throughput_mbps", "actual_layers"):
                continue
            values = pd.to_numeric(keep[col], errors="coerce")
            row[col] = float(values.mean()) if values.notna().any() else float("nan")
        actual_layers = pd.to_numeric(keep["actual_layers"], errors="coerce").dropna()
        if not actual_layers.empty:
            row["actual_layers"] = float(actual_layers.mode().iloc[0])
        else:
            row["actual_layers"] = float("nan")
        svd_caps = [row.get(f"svd_capacity_k{k}") for k in range(1, 5)]
        zf_caps = [row.get(f"zf_capacity_k{k}") for k in range(1, 5)]
        row["svd_optimal_stream"] = _optimal_from_capacities(svd_caps)
        row["zf_optimal_stream"] = _optimal_from_capacities(zf_caps)
        if row["svd_optimal_stream"] > 0:
            row["svd_capacity"] = row[f"svd_capacity_k{row['svd_optimal_stream']}"]
        if row["zf_optimal_stream"] > 0:
            row["zf_capacity"] = row[f"zf_capacity_k{row['zf_optimal_stream']}"]
        rows.append(row)
    return pd.DataFrame(rows)


def _validation_passes(
    validation: pd.DataFrame,
    tolerance: float,
) -> dict:
    if validation.empty:
        return {
            "rows": 0,
            "max_power_relative_error": float("nan"),
            "max_svd_relative_error": float("nan"),
            "max_zf_trace_relative_error": float("nan"),
            "max_sinr_negative_error": float("nan"),
            "result": "FAIL",
        }
    max_power = float(pd.to_numeric(validation["max_power_relative_error"], errors="coerce").max())
    max_svd = float(pd.to_numeric(validation["max_svd_relative_error"], errors="coerce").max())
    max_trace = float(pd.to_numeric(validation["max_zf_trace_relative_error"], errors="coerce").max())
    max_sinr = float(pd.to_numeric(validation["max_sinr_negative_error"], errors="coerce").max())
    all_valid = bool((pd.to_numeric(validation["validated_subcarriers"], errors="coerce") > 0).all())
    finite_errors = all(np.isfinite(value) for value in (max_power, max_svd, max_trace, max_sinr))
    passed = all_valid and finite_errors and max(
        max_power, max_svd, max_trace, max_sinr
    ) <= tolerance
    return {
        "rows": len(validation),
        "max_power_relative_error": max_power if np.isfinite(max_power) else float("nan"),
        "max_svd_relative_error": max_svd if np.isfinite(max_svd) else float("nan"),
        "max_zf_trace_relative_error": max_trace if np.isfinite(max_trace) else float("nan"),
        "max_sinr_negative_error": max_sinr if np.isfinite(max_sinr) else float("nan"),
        "result": "PASS" if passed else "FAIL",
    }


def _write_validation(output_dir: str, validation: pd.DataFrame, tolerance: float) -> dict:
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "validation_report.csv")
    validation.to_csv(path, index=False)
    summary = _validation_passes(validation, tolerance)
    lines = [
        "Validation summary",
        "==================",
        f"validated rows: {summary['rows']}",
        f"max power relative error: {summary['max_power_relative_error']:.3e}",
        f"max SVD formula relative error: {summary['max_svd_relative_error']:.3e}",
        f"max ZF trace relative error: {summary['max_zf_trace_relative_error']:.3e}",
        f"max negative SINR error: {summary['max_sinr_negative_error']:.3e}",
        f"tolerance: {tolerance:.1e}",
        f"result: {summary['result']}",
    ]
    with open(os.path.join(output_dir, "validation_summary.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        f.write("\n")
    return summary


def _write_dataframe(output_dir: str, filename: str, frame: pd.DataFrame) -> str:
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, filename)
    frame.to_csv(path, index=False)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-dir",
        default="/media/qiuer/BEA6-BBCE/0807/round1",
        help="path to one test round folder containing gui_ue_log_*.csv, "
        "its same-stem CSI directory, and gui_gnb_log_*.csv for UL",
    )
    parser.add_argument(
        "--output-dir",
        default="gui/analysis/analysis_results",
    )
    parser.add_argument(
        "--direction",
        type=str.lower,
        choices=["ul", "dl"],
        required=True,
        help="analysis direction for this round: ul or dl",
    )
    parser.add_argument("--noise-power", type=float, default=NOISE_POWER_DEFAULT)
    parser.add_argument("--top-ratio", type=float, default=0.5)
    parser.add_argument("--pair-tolerance-ms", type=float, default=2000.0)
    parser.add_argument("--csi-tolerance-ms", type=float, default=200.0)
    parser.add_argument("--position-gap-s", type=float, default=3.0)
    parser.add_argument("--validation-tolerance", type=float, default=1e-6)
    parser.add_argument("--no-plots", action="store_true")
    args = parser.parse_args()

    if not 0.0 < args.top_ratio <= 1.0:
        raise SystemExit("--top-ratio must be in (0, 1]")
    if args.noise_power <= 0:
        raise SystemExit("--noise-power must be positive")

    measurements = discover_measurement_sets(args.dataset_dir)
    if not measurements:
        raise SystemExit(f"no measurement sets found under {args.dataset_dir}")
    if len(measurements) != 1:
        raise SystemExit(
            f"expected exactly one measurement set under {args.dataset_dir}, "
            f"found {[ms.name for ms in measurements]}; "
            "pass the test round folder directly, e.g. .../0807/round1"
        )

    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)
    all_second: List[pd.DataFrame] = []
    all_validation: List[pd.DataFrame] = []
    logs: List[str] = []

    for ms in measurements:
        direction = args.direction
        frame = load_measurement_frame(
            ms,
            direction,
            pair_tolerance_ms=args.pair_tolerance_ms,
            position_gap_s=args.position_gap_s,
        )
        csi_map = _load_csi_map(ms.ue_csi_dir)
        frame, csi_warnings = _match_csi(frame, csi_map, args.csi_tolerance_ms)
        matched = int(frame["csi_file"].notna().sum())
        if direction == "dl":
            logs.append(
                f"{ms.name}: UE rows={len(frame)}, CSI matched={matched}/{len(frame)}, "
                f"CSI files={len(csi_map)} (DL uses UE CSV directly)"
            )
        else:
            logs.append(
                f"{ms.name}: UE rows={len(frame)}, gNB paired="
                f"{int(frame['gnb_match_delta_ms'].notna().sum())}, "
                f"CSI matched={matched}/{len(frame)}, CSI files={len(csi_map)}"
            )
        if csi_warnings:
            logs.extend(f"  {item}" for item in csi_warnings[:20])
        frame = frame[frame["csi_file"].notna()].copy()
        if frame.empty:
            logs.append(f"{ms.name}: no CSI-matched rows, skipping")
            continue

        second, validation = process_measurements(
            frame,
            noise_power=args.noise_power,
            max_streams=4,
        )
        _write_dataframe(
            output_dir,
            f"processed_second_level_{ms.name}.csv",
            second,
        )
        _write_dataframe(
            output_dir,
            f"validation_report_{ms.name}.csv",
            validation,
        )
        all_second.append(second)
        all_validation.append(validation)

    if not all_second:
        raise SystemExit("no processed rows were generated")

    second_df = pd.concat(all_second, ignore_index=True)
    validation_df = pd.concat(all_validation, ignore_index=True)
    validation_summary = _write_validation(
        output_dir,
        validation_df,
        args.validation_tolerance,
    )
    _write_dataframe(output_dir, "processed_second_level.csv", second_df)

    if validation_summary["result"] != "PASS":
        with open(os.path.join(output_dir, "analysis_log.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(logs))
            f.write("\n")
        raise RuntimeError(
            "mathematical validation failed; see validation_report.csv and "
            "validation_summary.txt; conclusions were not generated"
        )

    position_df = aggregate_position_level(second_df, top_ratio=args.top_ratio)
    _write_dataframe(output_dir, "processed_position_level.csv", position_df)

    corr_second = correlation_table(second_df, "second")
    corr_position = correlation_table(position_df, "position")
    regression_second = regression_table(second_df, "second")
    regression_position = regression_table(position_df, "position")
    stream_second, confusion_second = stream_selection_tables(second_df, "second")
    stream_position, confusion_position = stream_selection_tables(position_df, "position")

    _write_dataframe(output_dir, "correlation_second_level.csv", corr_second)
    _write_dataframe(output_dir, "correlation_position_level.csv", corr_position)
    _write_dataframe(output_dir, "regression_second_level.csv", regression_second)
    _write_dataframe(output_dir, "regression_position_level.csv", regression_position)
    _write_dataframe(output_dir, "stream_selection_second_level.csv", stream_second)
    _write_dataframe(output_dir, "stream_selection_position_level.csv", stream_position)
    _write_dataframe(output_dir, "stream_confusion_second_level.csv", confusion_second)
    _write_dataframe(output_dir, "stream_confusion_position_level.csv", confusion_position)

    if not args.no_plots:
        plot_all(
            second_df,
            position_df,
            corr_second,
            corr_position,
            pd.concat([stream_second, stream_position], ignore_index=True),
            output_dir,
        )

    report_path = write_final_report(
        output_dir,
        second_df=second_df,
        position_df=position_df,
        corr_second=corr_second,
        corr_position=corr_position,
        regression_second=regression_second,
        regression_position=regression_position,
        stream_second=stream_second,
        stream_position=stream_position,
        validation_summary=validation_summary,
        noise_power=args.noise_power,
        top_ratio=args.top_ratio,
        pair_tolerance_ms=args.pair_tolerance_ms,
    )

    with open(os.path.join(output_dir, "analysis_log.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(logs))
        f.write("\n")

    print("Processing complete")
    print(f"sets: {[ms.name for ms in measurements]}")
    print(f"second-level rows: {len(second_df)}")
    print(f"position-level rows: {len(position_df)}")
    print(f"validation: {validation_summary['result']}")
    print(f"report: {report_path}")


if __name__ == "__main__":
    main()
