"""Dataset discovery and pairing of OAI UE/gNB measurement files."""

from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S_%f"


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


def _discover_archive_pairs(
    dataset_dir: str,
    direction: Optional[str] = None,
) -> List[MeasurementSet]:
    """Discover the new ``gnb_*``/``ue_*`` paired archive layout."""
    grouped: Dict[str, Dict[str, str]] = {"gnb": {}, "ue": {}}
    for entry in os.scandir(dataset_dir):
        if not entry.is_dir():
            continue
        match = re.fullmatch(r"(gnb|ue)_(.+)", entry.name)
        if match:
            grouped[match.group(1)][match.group(2)] = entry.path

    common_suffixes = sorted(
        set(grouped["gnb"]).intersection(grouped["ue"])
    )
    result: List[MeasurementSet] = []
    for suffix in common_suffixes:
        gnb_dir = grouped["gnb"][suffix]
        ue_dir = grouped["ue"][suffix]
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
        result.append(
            MeasurementSet(
                name=os.path.basename(os.path.normpath(dataset_dir)) or suffix,
                direction="",
                ue_csv=ue_csv,
                ue_csi_dir=ue_csi_dir,
                gnb_csv=gnb_csv,
                gnb_srs_dir=gnb_srs_dir,
            )
        )
    return result


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
    paired = _discover_archive_pairs(dataset_dir, direction=direction)
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

    frame = frame.assign(
        set=measurement.name,
        direction=direction,
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
