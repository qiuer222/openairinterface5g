"""Dataset discovery and pairing of OAI UE/gNB measurement files."""

from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List, Optional

import numpy as np
import pandas as pd


TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S_%f"


@dataclass(frozen=True)
class MeasurementSet:
    """One recorded measurement round with its paired CSI directory."""

    name: str
    direction: str
    ue_csv: str
    ue_csi_dir: str
    gnb_csv: Optional[str] = None


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


def _has_channel_files(directory: str) -> bool:
    if not os.path.isdir(directory):
        return False
    return any(
        name.startswith("channel_") and name.endswith(".npy")
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


def discover_measurement_sets(dataset_dir: str) -> List[MeasurementSet]:
    """Discover the measurement set inside one round folder."""
    dataset_dir = os.path.abspath(dataset_dir)
    sets: List[MeasurementSet] = []
    seen: set = set()

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


def _rename_conflicting_columns(df: pd.DataFrame, other: pd.DataFrame) -> pd.DataFrame:
    rename = {
        col: f"{col}_gnb"
        for col in df.columns
        if col in other.columns and col not in ("ts_epoch",)
    }
    return other.rename(columns=rename)


def load_measurement_frame(
    measurement: MeasurementSet,
    direction: str,
    pair_tolerance_ms: float = 2000.0,
    position_gap_s: float = 3.0,
) -> pd.DataFrame:
    """Load a measurement set and pair UL rows with the gNB CSV."""
    if direction not in ("ul", "dl"):
        raise ValueError(f"direction must be 'ul' or 'dl', got {direction!r}")

    ue = pd.read_csv(measurement.ue_csv, dtype={"timestamp": str})
    ue["timestamp"] = ue["timestamp"].astype(str)
    ue["ts_epoch"] = timestamp_epoch_seconds(ue["timestamp"])

    if direction == "dl":
        frame = ue.copy()
    else:
        if not measurement.gnb_csv or not os.path.isfile(measurement.gnb_csv):
            raise FileNotFoundError(
                f"UL set {measurement.name} requires a gNB CSV; "
                f"none was found for {measurement.ue_csv}"
            )
        gnb = pd.read_csv(measurement.gnb_csv, dtype={"timestamp": str})
        gnb["timestamp"] = gnb["timestamp"].astype(str)
        gnb["ts_epoch"] = timestamp_epoch_seconds(gnb["timestamp"])
        gnb = _rename_conflicting_columns(ue, gnb)
        gnb = gnb.rename(columns={"ts_epoch": "ts_epoch_gnb"})
        gnb = gnb.sort_values("ts_epoch_gnb")

        ue_sorted = ue.sort_values("ts_epoch").reset_index(drop=True)
        frame = pd.merge_asof(
            ue_sorted,
            gnb,
            left_on="ts_epoch",
            right_on="ts_epoch_gnb",
            direction="nearest",
            tolerance=pair_tolerance_ms / 1000.0,
            allow_exact_matches=True,
        )
        frame["gnb_match_delta_ms"] = (
            (frame["ts_epoch"] - frame["ts_epoch_gnb"]).abs() * 1000.0
        )
        frame = frame[frame["ts_epoch_gnb"].notna()].copy()

        frame["throughput_mbps"] = frame["throughput_mbps_gnb"]
        frame["mcs"] = frame.get("ul_mcs", pd.Series(index=frame.index))
        frame["layers"] = frame.get("ul_layers", pd.Series(index=frame.index))
        frame["gnb_timestamp"] = frame.get("timestamp_gnb", pd.Series(index=frame.index))

    frame["set"] = measurement.name
    frame["direction"] = direction
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
