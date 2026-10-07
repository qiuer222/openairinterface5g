#!/usr/bin/env python3
"""Run the SRS/CSI channel-based throughput prediction framework."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(
        0,
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    )

from gui.analysis.analysis.correlation import correlation_table
from gui.analysis.analysis.regression import regression_table
from gui.analysis.analysis.visualization import (
    plot_all,
    plot_timeseries_frame,
)
from gui.analysis.capacity.shannon import (
    shannon_capacity_from_eigenvalue_matrix,
    shannon_stream_capacities_from_eigenvalue_matrix,
)
from gui.analysis.capacity.svd import svd_capacities_from_eigenvalue_matrix
from gui.analysis.csi_parser import load_channel_and_valid
from gui.analysis.data_loader import (
    MeasurementSet,
    discover_measurement_sets,
    load_measurement_frame,
    parse_timestamp,
)
from gui.analysis.feature_extraction import (
    extract_channel_features_from_singular_values,
)
from gui.analysis.normalization import NOISE_POWER_DEFAULT, normalize_channel
from gui.analysis.report import write_final_report
from gui.analysis.stream_selection import stream_selection_tables


EPS = 1e-12
MODE_AGGREGATED_COLUMNS = {
    "actual_layers",
    "channel_kind",
    "cqi",
    "dl_cqi",
    "dl_layers",
    "dl_ndi",
    "dl_ri",
    "dl_rv",
    "layers",
    "new_data_indicator",
    "normalization_snr_source",
    "iperf_direction",
    "ri",
    "rv",
    "selected_stream_count",
    "ul_cqi",
    "ul_layers",
    "ul_ndi",
    "ul_rv",
    "ul_tpmi",
    "ul_tpmi_valid",
}


CHANNEL_PREFIXES = {
    "csi": "channel_",
    "srs": "srs_",
}


def _load_channel_map(
    channel_dir: str,
    channel_kind: str,
) -> Dict[float, str]:
    prefix = CHANNEL_PREFIXES[channel_kind]
    result: Dict[float, str] = {}
    for name in sorted(os.listdir(channel_dir)):
        if not (name.startswith(prefix) and name.endswith(".npy")):
            continue
        ts = name[len(prefix) : -len(".npy")]
        parsed = parse_timestamp(ts)
        if parsed is not None:
            result[parsed.timestamp()] = os.path.join(channel_dir, name)
    return result


def _match_channel(
    df: pd.DataFrame,
    channel_map: Dict[float, str],
    tolerance_ms: float,
    channel_kind: str,
) -> Tuple[pd.DataFrame, List[str]]:
    paths = []
    timestamps = []
    deltas = []
    warnings = []
    keys = np.asarray(sorted(channel_map), dtype=float)
    for ts in df["timestamp"]:
        parsed = parse_timestamp(ts)
        if parsed is None:
            paths.append(None)
            timestamps.append(None)
            deltas.append(float("nan"))
            warnings.append(f"could not parse timestamp {ts}")
            continue
        epoch = parsed.timestamp()
        if epoch in channel_map:
            paths.append(channel_map[epoch])
            timestamps.append(epoch)
            deltas.append(0.0)
            continue
        if keys.size == 0:
            paths.append(None)
            timestamps.append(None)
            deltas.append(float("nan"))
            warnings.append(f"no {channel_kind.upper()} files for timestamp {ts}")
            continue
        index = int(np.argmin(np.abs(keys - epoch)))
        nearest = keys[index]
        delta_ms = abs(nearest - epoch) * 1000.0
        if delta_ms <= tolerance_ms:
            paths.append(channel_map[nearest])
            timestamps.append(nearest)
            deltas.append(delta_ms)
            if delta_ms > 0:
                warnings.append(
                    f"nearest {channel_kind.upper()} for {ts}: "
                    f"{nearest:.3f} ({delta_ms:.1f} ms)"
                )
        else:
            paths.append(None)
            timestamps.append(None)
            deltas.append(float("nan"))
            warnings.append(
                f"no {channel_kind.upper()} within {tolerance_ms:.0f} ms "
                f"for {ts}"
            )

    df = df.copy()
    df["channel_file"] = paths
    df["channel_timestamp"] = timestamps
    df["channel_match_delta_ms"] = deltas
    df["channel_kind"] = channel_kind
    return df, warnings


def _optimal_from_capacities(capacities: List[float]) -> int:
    values = np.asarray(capacities, dtype=float)
    if len(values) == 0 or not np.any(np.isfinite(values)):
        return 0
    return int(np.nanargmax(values) + 1)


def _selected_stream_index(value, max_streams: int) -> int:
    try:
        layers = int(float(value))
    except (TypeError, ValueError):
        return 0
    if layers < 1:
        return 0
    return min(layers, max_streams)


def _empty_channel_metrics(max_streams: int) -> dict:
    metrics = extract_channel_features_from_singular_values(
        np.empty((0, 0)), valid_subcarrier_count=0
    )
    metrics.update(
        {
            "raw_channel_power": float("nan"),
            "raw_channel_power_db": float("nan"),
            "shannon_capacity": float("nan"),
            "svd_capacity": float("nan"),
            "svd_optimal_stream": 0,
            "selected_stream_capacity": float("nan"),
            "selected_stream_count": 0,
        }
    )
    for k in range(1, max_streams + 1):
        metrics[f"svd_capacity_k{k}"] = float("nan")
        metrics[f"stream_capacity_k{k}"] = float("nan")
    return metrics


def _apply_selected_stream_capacity(
    row: dict,
    layers,
    max_streams: int,
) -> None:
    selected = _selected_stream_index(layers, max_streams)
    row["selected_stream_count"] = selected
    row["selected_stream_capacity"] = (
        row.get(f"stream_capacity_k{selected}")
        if selected > 0
        else float("nan")
    )


def _normalization_snr_db(
    record: dict,
    fallback_snr_db: float | None,
) -> tuple[float, str]:
    if str(record.get("direction", "")).lower() == "ul":
        try:
            ul_sinr_db = float(record.get("ul_sinr"))
        except (TypeError, ValueError):
            ul_sinr_db = float("nan")
        if np.isfinite(ul_sinr_db):
            return ul_sinr_db, "ul_sinr"
    if fallback_snr_db is not None:
        return float(fallback_snr_db), "cli_snr"
    return float("nan"), "raw"


def _channel_metrics(
    h: np.ndarray,
    valid: np.ndarray,
    noise_power: float,
    max_streams: int,
    snr_db: float | None,
) -> dict:
    """Compute all metrics for one channel using batched SVD over subcarriers."""
    if len(valid) == 0:
        raise ValueError("no valid channel subcarriers")
    h = normalize_channel(h, noise_power=noise_power, snr_db=snr_db)
    hv = h[:, :, valid].transpose(2, 0, 1)
    s = np.linalg.svd(hv, compute_uv=False)
    eig = s ** 2
    rank = eig.shape[1]

    frobenius2 = np.sum(np.abs(hv) ** 2, axis=(1, 2))
    eigenvalue_sum = np.sum(eig, axis=1)
    max_power_rel = float(
        np.max(np.abs(frobenius2 - eigenvalue_sum) / np.maximum(frobenius2, EPS))
    )
    raw_power = float(np.mean(frobenius2))

    svd_capacities = svd_capacities_from_eigenvalue_matrix(
        eig, noise_power=noise_power, max_streams=max_streams
    )
    max_svd_rel = 0.0
    for kk in range(1, rank + 1):
        cap_eig = np.sum(
            np.log2(
                1.0
                + np.maximum(eig[:, :kk], 0.0) / (float(kk) * float(noise_power))
            ),
            axis=1,
        )
        cap_sv = np.sum(
            np.log2(
                1.0
                + np.maximum(s[:, :kk] ** 2, 0.0) / (float(kk) * float(noise_power))
            ),
            axis=1,
        )
        rel = np.abs(cap_eig - cap_sv) / np.maximum(np.abs(cap_eig), EPS)
        max_svd_rel = max(max_svd_rel, float(np.max(rel)))

    shannon = shannon_capacity_from_eigenvalue_matrix(
        eig, noise_power=noise_power, tx_count=h.shape[1]
    )
    stream_capacities = shannon_stream_capacities_from_eigenvalue_matrix(
        eig,
        noise_power=noise_power,
        tx_count=h.shape[1],
        max_streams=max_streams,
    )
    features = extract_channel_features_from_singular_values(
        s, valid_subcarrier_count=len(valid)
    )
    return {
        "features": features,
        "shannon": shannon,
        "svd_capacities": svd_capacities,
        "stream_capacities": stream_capacities,
        "max_power_rel": max_power_rel,
        "max_svd_rel": max_svd_rel,
        "raw_power": raw_power,
    }


def process_measurements(
    df: pd.DataFrame,
    noise_power: float,
    max_streams: int = 4,
    snr_db: float | None = None,
) -> pd.DataFrame:
    """Build second-level rows with features, capacities, and validation data."""
    rows: List[dict] = []
    validation: List[dict] = []

    for record in df.to_dict("records"):
        channel_file = record.get("channel_file")
        normalization_snr_db, normalization_snr_source = (
            _normalization_snr_db(record, snr_db)
        )
        metadata = {
            key: value
            for key, value in record.items()
            if key not in ("ts_epoch", "ue_ts_epoch", "gnb_ts_epoch")
        }
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
            "channel_file": channel_file,
            "channel_timestamp": record.get("channel_timestamp"),
            "channel_match_delta_ms": record.get("channel_match_delta_ms"),
            "channel_kind": record.get("channel_kind"),
            "gnb_timestamp": record.get("gnb_timestamp"),
            "gnb_match_delta_ms": record.get("gnb_match_delta_ms"),
            "ue_timestamp": record.get("ue_timestamp"),
            "ue_match_delta_ms": record.get("ue_match_delta_ms"),
            "normalization_snr_db": normalization_snr_db,
            "normalization_snr_source": normalization_snr_source,
        }
        row = {
            **metadata,
            **base,
            **_empty_channel_metrics(max_streams),
        }
        val = dict(base)
        try:
            if not isinstance(channel_file, str) or not channel_file:
                raise ValueError(
                    f"no {record.get('channel_kind', 'channel').upper()} "
                    "file matched"
                )
            h, valid = load_channel_and_valid(channel_file)
            if len(valid) == 0:
                raise ValueError("no valid channel subcarriers")
            metrics = _channel_metrics(
                h,
                valid,
                noise_power=noise_power,
                max_streams=max_streams,
                snr_db=normalization_snr_db,
            )

            features = metrics["features"]
            svd_capacities = metrics["svd_capacities"]
            stream_capacities = metrics["stream_capacities"]
            raw_power = metrics["raw_power"]
            row.update(features)
            row.update(
                {
                    "raw_channel_power": raw_power,
                    "raw_channel_power_db": (
                        float(10.0 * np.log10(raw_power))
                        if raw_power > 0
                        else float("nan")
                    ),
                    "shannon_capacity": metrics["shannon"],
                }
            )
            for idx, capacity in enumerate(svd_capacities):
                row[f"svd_capacity_k{idx + 1}"] = capacity
            for idx, capacity in enumerate(stream_capacities):
                row[f"stream_capacity_k{idx + 1}"] = capacity

            row["svd_optimal_stream"] = _optimal_from_capacities(svd_capacities)
            if row["svd_optimal_stream"] > 0:
                row["svd_capacity"] = row[f"svd_capacity_k{row['svd_optimal_stream']}"]
            _apply_selected_stream_capacity(
                row,
                record.get("layers"),
                max_streams,
            )

            val.update(
                {
                    "validated_subcarriers": int(len(valid)),
                    "max_power_relative_error": metrics["max_power_rel"],
                    "max_svd_relative_error": metrics["max_svd_rel"],
                    "raw_channel_power": raw_power,
                    "eigenvalue_sum": features["eigenvalue_sum"],
                    "possible_reason": "",
                }
            )
        except Exception as exc:  # noqa: BLE001 - keep the pipeline resumable
            val.update(
                {
                    "validated_subcarriers": 0,
                    "max_power_relative_error": float("nan"),
                    "max_svd_relative_error": float("nan"),
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
        "ue_timestamp",
        "gnb_timestamp",
        "set",
        "direction",
        "position_id",
        "test_round",
        "channel_file",
        "channel_timestamp",
        "channel_match_delta_ms",
        "gnb_match_delta_ms",
        "ue_match_delta_ms",
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
            if col in MODE_AGGREGATED_COLUMNS:
                values = keep[col].dropna()
                row[col] = (
                    values.mode().iloc[0] if not values.empty else float("nan")
                )
                continue
            values = pd.to_numeric(keep[col], errors="coerce")
            row[col] = float(values.mean()) if values.notna().any() else float("nan")
        actual_layers = pd.to_numeric(keep["actual_layers"], errors="coerce").dropna()
        if not actual_layers.empty:
            row["actual_layers"] = float(actual_layers.mode().iloc[0])
        else:
            row["actual_layers"] = float("nan")
        svd_caps = [row.get(f"svd_capacity_k{k}") for k in range(1, 5)]
        row["svd_optimal_stream"] = _optimal_from_capacities(svd_caps)
        if row["svd_optimal_stream"] > 0:
            row["svd_capacity"] = row[f"svd_capacity_k{row['svd_optimal_stream']}"]
        _apply_selected_stream_capacity(
            row,
            row.get("actual_layers"),
            4,
        )
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
            "result": "FAIL",
        }
    max_power = float(pd.to_numeric(validation["max_power_relative_error"], errors="coerce").max())
    max_svd = float(pd.to_numeric(validation["max_svd_relative_error"], errors="coerce").max())
    all_valid = bool((pd.to_numeric(validation["validated_subcarriers"], errors="coerce") > 0).all())
    finite_errors = all(np.isfinite(value) for value in (max_power, max_svd))
    passed = all_valid and finite_errors and max(max_power, max_svd) <= tolerance
    return {
        "rows": len(validation),
        "max_power_relative_error": max_power if np.isfinite(max_power) else float("nan"),
        "max_svd_relative_error": max_svd if np.isfinite(max_svd) else float("nan"),
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
        required=True,
        help="experiment root containing paired gnb_* and ue_* archive "
        "folders, or a legacy single-round folder",
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
        help="analysis direction for the archive pair: ul or dl",
    )
    parser.add_argument("--noise-power", type=float, default=NOISE_POWER_DEFAULT)
    parser.add_argument(
        "--snr",
        type=float,
        default=None,
        help="fallback target SNR in dB. UL uses each row's ul_sinr when "
        "available; DL uses this value when set",
    )
    parser.add_argument("--top-ratio", type=float, default=0.5)
    parser.add_argument("--pair-tolerance-ms", type=float, default=2000.0)
    parser.add_argument(
        "--channel-tolerance-ms",
        "--csi-tolerance-ms",
        dest="channel_tolerance_ms",
        type=float,
        default=200.0,
        help="maximum CSV-to-channel timestamp matching error in ms",
    )
    parser.add_argument("--position-gap-s", type=float, default=3.0)
    parser.add_argument("--validation-tolerance", type=float, default=1e-6)
    parser.add_argument("--no-plots", action="store_true")
    args = parser.parse_args()

    if not 0.0 < args.top_ratio <= 1.0:
        raise SystemExit("--top-ratio must be in (0, 1]")
    if args.noise_power <= 0:
        raise SystemExit("--noise-power must be positive")
    snr_db = args.snr
    noise_power = args.noise_power
    if args.direction == "dl" and snr_db is not None:
        noise_power = NOISE_POWER_DEFAULT

    measurements = discover_measurement_sets(
        args.dataset_dir,
        direction=args.direction,
    )
    if not measurements:
        raise SystemExit(f"no measurement sets found under {args.dataset_dir}")
    if len(measurements) != 1:
        raise SystemExit(
            f"expected exactly one measurement set under {args.dataset_dir}, "
            f"found {[ms.name for ms in measurements]}; "
            "pass a root containing exactly one gnb_* and one ue_* archive"
        )

    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)
    logs: List[str] = []

    ms = measurements[0]
    direction = args.direction
    if direction == "ul" and ms.gnb_srs_dir:
        channel_kind = "srs"
        channel_dir = ms.gnb_srs_dir
        channel_source = "gNB SRS"
    else:
        channel_kind = "csi"
        channel_dir = ms.ue_csi_dir
        channel_source = "UE CSI"
    frame = load_measurement_frame(
        ms,
        direction,
        pair_tolerance_ms=args.pair_tolerance_ms,
        position_gap_s=args.position_gap_s,
    )
    channel_map = _load_channel_map(channel_dir, channel_kind)
    frame, channel_warnings = _match_channel(
        frame,
        channel_map,
        args.channel_tolerance_ms,
        channel_kind,
    )
    matched = int(frame["channel_file"].notna().sum())
    logs.append(
        f"{ms.name}: direction={direction.upper()}, rows={len(frame)}, "
        f"{channel_source} matched={matched}/{len(frame)}, "
        f"channel files={len(channel_map)}"
    )
    if direction == "ul":
        logs.append(
            "  UL primary CSV=gNB, supplemental CSV=UE, "
            f"UE paired={int(frame['ue_match_delta_ms'].notna().sum())}"
        )
    elif "gnb_match_delta_ms" in frame:
        logs.append(
            "  DL primary CSV=UE, supplemental CSV=gNB, "
            f"gNB paired={int(frame['gnb_match_delta_ms'].notna().sum())}"
        )
    if channel_warnings:
        logs.extend(f"  {item}" for item in channel_warnings[:20])
    frame = frame[frame["channel_file"].notna()].copy()
    if frame.empty:
        raise SystemExit(
            f"{ms.name}: no {channel_kind.upper()}-matched rows"
        )

    second_df, validation_df = process_measurements(
        frame,
        noise_power=noise_power,
        max_streams=4,
        snr_db=snr_db,
    )
    _write_dataframe(output_dir, "processed_second_level.csv", second_df)
    _write_dataframe(output_dir, "validation_report.csv", validation_df)
    validation_summary = _write_validation(
        output_dir,
        validation_df,
        args.validation_tolerance,
    )

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
        plot_timeseries_frame(
            position_df,
            output_dir,
            "position_metrics.png",
            "Position-Level Metrics",
            x_column="position_id",
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
        noise_power=noise_power,
        snr_db=snr_db,
        top_ratio=args.top_ratio,
        pair_tolerance_ms=args.pair_tolerance_ms,
        channel_kind=channel_kind,
    )

    with open(os.path.join(output_dir, "analysis_log.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(logs))
        f.write("\n")

    print("Processing complete")
    print(f"set: {ms.name} ({direction.upper()})")
    print(f"second-level rows: {len(second_df)}")
    print(f"position-level rows: {len(position_df)}")
    print(f"validation: {validation_summary['result']}")
    print(f"report: {report_path}")


if __name__ == "__main__":
    main()
