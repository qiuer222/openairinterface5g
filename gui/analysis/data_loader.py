"""Dataset discovery and pairing of OAI UE/gNB measurement files."""

from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import numpy as np
import pandas as pd


TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S_%f"
RSRP_ANTENNA_COLUMNS = (
    "rsrp_ant0_dBm",
    "rsrp_ant1_dBm",
    "rsrp_ant2_dBm",
    "rsrp_ant3_dBm",
    "ue_rsrp_ant0_dBm",
    "ue_rsrp_ant1_dBm",
    "ue_rsrp_ant2_dBm",
    "ue_rsrp_ant3_dBm",
)


@dataclass(frozen=True)
class MeasurementSet:
    """One paired gNB/UE recording archive."""

    name: str
    direction: str
    ue_csv: str
    ue_csi_dir: str
    gnb_csv: Optional[str] = None
    gnb_srs_dir: Optional[str] = None


def parse_timestamp(value) -> Optional[datetime]:
    """Parse GUI timestamps such as ``20260807_145628_353923``."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    text = str(value).strip()
    formats = (
        TIMESTAMP_FORMAT,
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y%m%d_%H%M%S",
    )
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"unsupported timestamp: {text!r}")


def timestamp_epoch_seconds(series: pd.Series) -> pd.Series:
    return series.map(lambda value: parse_timestamp(value).timestamp())


def _normalize_set_name(path: str) -> str:
    base = os.path.basename(os.path.normpath(path))
    base = re.sub(r"_(ue|gnb|data)$", "", base, flags=re.IGNORECASE)
    return re.sub(r"[^A-Za-z0-9_-]", "_", base) or "measurement"


def _has_channel_files(directory: str, prefix: str = "channel_") -> bool:
    if not os.path.isdir(directory):
        return False
    return any(
        name.startswith(prefix) and name.endswith(".npy")
        for name in os.listdir(directory)
    )


def _find_gnb_csv(dataset_dir: str, set_name: str, ue_dir: str) -> Optional[str]:
    # In the intended layout the gNB CSV is in the same round folder as the
    # UE CSV. The recursive fallback keeps legacy split-folder layouts working.
    local = sorted(glob.glob(os.path.join(ue_dir, "gui_gnb_log_*.csv")))
    if local:
        return local[0]

    candidates: List[str] = []
    for path in sorted(glob.glob(os.path.join(dataset_dir, "**", "gui_gnb_log_*.csv"), recursive=True)):
        parent = os.path.basename(os.path.dirname(path))
        if _normalize_set_name(parent) == set_name:
            candidates.append(path)
    return candidates[0] if candidates else None


def _read_gui_csv(path: str) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype={"timestamp": str})
    frame["timestamp"] = frame["timestamp"].astype(str)
    frame["ts_epoch"] = timestamp_epoch_seconds(frame["timestamp"])
    return frame


def _find_log_csv(directory: str, prefix: str) -> str:
    matches = sorted(glob.glob(os.path.join(directory, f"{prefix}*.csv")))
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one {prefix}*.csv in {directory}, "
            f"found {len(matches)}"
        )
    return matches[0]


def _discover_archive_pair(
    dataset_dir: str,
    direction: Optional[str] = None,
) -> List[MeasurementSet]:
    """Discover the new ``gnb_*``/``ue_*`` paired archive layout."""
    gnb_dirs = sorted(
        entry.path
        for entry in os.scandir(dataset_dir)
        if entry.is_dir() and entry.name.startswith("gnb_")
    )
    ue_dirs = sorted(
        entry.path
        for entry in os.scandir(dataset_dir)
        if entry.is_dir() and entry.name.startswith("ue_")
    )
    if not gnb_dirs and not ue_dirs:
        return []
    if len(gnb_dirs) != 1 or len(ue_dirs) != 1:
        raise ValueError(
            "expected exactly one gnb_* folder and one ue_* folder in "
            f"{dataset_dir}; found gnb={len(gnb_dirs)}, ue={len(ue_dirs)}"
        )

    gnb_dir = gnb_dirs[0]
    ue_dir = ue_dirs[0]
    gnb_csv = _find_log_csv(gnb_dir, "gui_gnb_log_")
    ue_csv = _find_log_csv(ue_dir, "gui_ue_log_")
    gnb_srs_dir = os.path.splitext(gnb_csv)[0]
    ue_csi_dir = os.path.splitext(ue_csv)[0]
    if direction in (None, "ul") and not _has_channel_files(
        gnb_srs_dir, "srs_"
    ):
        raise ValueError(f"no srs_*.npy files under {gnb_srs_dir}")
    if direction in (None, "dl") and not _has_channel_files(
        ue_csi_dir, "channel_"
    ):
        raise ValueError(f"no channel_*.npy files under {ue_csi_dir}")
    return [
        MeasurementSet(
            name=os.path.basename(os.path.normpath(dataset_dir)) or "measurement",
            direction="",
            ue_csv=ue_csv,
            ue_csi_dir=ue_csi_dir,
            gnb_csv=gnb_csv,
            gnb_srs_dir=gnb_srs_dir,
        )
    ]


def _discover_legacy_measurement_sets(
    dataset_dir: str,
) -> List[MeasurementSet]:
    """Discover the legacy round-folder layout."""
    dataset_dir = os.path.abspath(dataset_dir)
    sets: List[MeasurementSet] = []
    seen: set[str] = set()

    for root, _dirs, files in os.walk(dataset_dir):
        for filename in sorted(files):
            if not (filename.startswith("gui_ue_log_") and filename.endswith(".csv")):
                continue
            stem = os.path.splitext(filename)[0]
            csi_dir = os.path.join(root, stem)
            if not _has_channel_files(csi_dir):
                continue
            set_name = _normalize_set_name(root) or _normalize_set_name(csi_dir)
            if set_name in seen:
                continue
            seen.add(set_name)
            gnb_csv = _find_gnb_csv(dataset_dir, set_name, root)
            sets.append(
                MeasurementSet(
                    name=set_name,
                    direction="",
                    ue_csv=os.path.join(root, filename),
                    ue_csi_dir=csi_dir,
                    gnb_csv=gnb_csv,
                )
            )
    return sorted(sets, key=lambda item: item.name)


def discover_measurement_sets(
    dataset_dir: str,
    direction: Optional[str] = None,
) -> List[MeasurementSet]:
    """Discover either the new paired archive or a legacy round layout."""
    dataset_dir = os.path.abspath(dataset_dir)
    if not os.path.isdir(dataset_dir):
        return []
    paired = _discover_archive_pair(dataset_dir, direction=direction)
    if paired:
        return paired
    return _discover_legacy_measurement_sets(dataset_dir)


def _prefix_columns(
    frame: pd.DataFrame,
    prefix: str,
) -> pd.DataFrame:
    rename = {}
    for column in frame.columns:
        if column == "timestamp":
            rename[column] = f"{prefix}_timestamp"
        elif column == "ts_epoch":
            rename[column] = f"{prefix}_ts_epoch"
        else:
            rename[column] = f"{prefix}_{column}"
    return frame.rename(columns=rename)


def _pair_nearest(
    primary: pd.DataFrame,
    secondary: pd.DataFrame,
    prefix: str,
    tolerance_ms: float,
) -> pd.DataFrame:
    primary_sorted = primary.sort_values("ts_epoch").reset_index(drop=True)
    secondary_sorted = secondary.sort_values("ts_epoch").reset_index(drop=True)
    secondary_sorted = _prefix_columns(secondary_sorted, prefix)
    paired = pd.merge_asof(
        primary_sorted,
        secondary_sorted,
        left_on="ts_epoch",
        right_on=f"{prefix}_ts_epoch",
        direction="nearest",
        tolerance=tolerance_ms / 1000.0,
        allow_exact_matches=True,
    )
    paired[f"{prefix}_match_delta_ms"] = (
        (paired["ts_epoch"] - paired[f"{prefix}_ts_epoch"]).abs() * 1000.0
    )
    return paired


def _max_rsrp_dbm(record: dict) -> float:
    values = []
    for column in RSRP_ANTENNA_COLUMNS:
        try:
            value = float(record.get(column))
        except (TypeError, ValueError):
            continue
        if np.isfinite(value) and -190.0 < value < 0.0:
            values.append(value)
    return max(values) if values else float("nan")


def _spectrum_efficiency(record: dict, direction: str) -> float:
    try:
        throughput_bps = float(record.get("throughput_mbps")) * 1e6
    except (TypeError, ValueError):
        return float("nan")
    direction = direction.lower()
    if direction == "ul":
        n_rb = record.get("ul_nprb", record.get("nprb"))
        scs_value = record.get("ul_scs_khz")
        scs_scale = 1e3
    else:
        n_rb = record.get("nprb")
        scs_value = record.get("scs", record.get("dl_scs_khz"))
        scs_scale = 1.0 if record.get("scs") is not None else 1e3
    try:
        scs_hz = float(scs_value) * scs_scale
        bandwidth_hz = float(n_rb) * 12.0 * scs_hz
    except (TypeError, ValueError):
        return float("nan")
    if throughput_bps <= 0 or bandwidth_hz <= 0:
        return float("nan")
    return throughput_bps / bandwidth_hz


def load_measurement_frame(
    measurement: MeasurementSet,
    direction: str,
    pair_tolerance_ms: float = 2000.0,
    position_gap_s: float = 3.0,
) -> pd.DataFrame:
    """Load a paired archive and select the primary CSV for the direction."""
    if direction not in ("ul", "dl"):
        raise ValueError(f"direction must be 'ul' or 'dl', got {direction!r}")

    ue = _read_gui_csv(measurement.ue_csv)
    gnb = (
        _read_gui_csv(measurement.gnb_csv)
        if measurement.gnb_csv and os.path.isfile(measurement.gnb_csv)
        else None
    )

    if direction == "dl":
        frame = ue.copy()
        if gnb is not None:
            frame = _pair_nearest(
                frame,
                gnb,
                "gnb",
                tolerance_ms=pair_tolerance_ms,
            )
    else:
        if gnb is None:
            raise FileNotFoundError(
                f"UL set {measurement.name} requires a gNB CSV; "
                f"none was found for {measurement.ue_csv}"
            )
        frame = _pair_nearest(
            gnb,
            ue,
            "ue",
            tolerance_ms=pair_tolerance_ms,
        )
        derived = {
            "gnb_timestamp": frame["timestamp"],
            "gnb_match_delta_ms": 0.0,
        }
        if "ul_mcs" in frame.columns:
            derived["mcs"] = frame["ul_mcs"]
        if "ul_layers" in frame.columns:
            derived["layers"] = frame["ul_layers"]
        if "ue_rsrp_dBm" in frame.columns:
            derived["rsrp_dBm"] = frame["ue_rsrp_dBm"]
        frame = pd.concat(
            [
                frame,
                pd.DataFrame(derived, index=frame.index),
            ],
            axis=1,
        )

    derived = pd.DataFrame(
        {
            "set": measurement.name,
            "direction": direction,
            "max_rsrp_dBm": frame.apply(_max_rsrp_dbm, axis=1),
            "spectrum_efficiency": frame.apply(
                lambda record: _spectrum_efficiency(record, direction),
                axis=1,
            ),
        },
        index=frame.index,
    )
    frame = pd.concat(
        [frame, derived],
        axis=1,
    )
    frame = assign_position_ids(frame, position_gap_s=position_gap_s)
    return frame


def assign_position_ids(
    df: pd.DataFrame,
    position_gap_s: float = 3.0,
) -> pd.DataFrame:
    """Assign stable position IDs, preferring the test_round column."""
    df = df.copy()
    if (
        "test_round" in df.columns
        and df["test_round"].nunique(dropna=True) > 1
    ):
        codes, _ = pd.factorize(df["test_round"].fillna(-1), sort=True)
        df["position_id"] = codes + 1
    else:
        times = df["ts_epoch"].astype(float)
        gaps = times.diff().fillna(0.0)
        df["position_id"] = (gaps > position_gap_s).cumsum() + 1
    return df
