"""CSI file loading and timestamp matching."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from gui.analysis.data_cleaner import parse_timestamp


def load_channel_map(csi_path: str) -> Dict[str, str]:
    """Build timestamp -> CSI path map from a directory or npz archive."""
    channels: Dict[str, str] = {}
    if os.path.isdir(csi_path):
        for name in sorted(os.listdir(csi_path)):
            if name.startswith("channel_") and name.endswith(".npy"):
                ts = name[len("channel_"):-len(".npy")]
                channels[ts] = os.path.join(csi_path, name)
    elif csi_path.endswith(".npz"):
        data = np.load(csi_path, allow_pickle=False)
        if "timestamps" in data and "channels" in data:
            for ts, _ in zip(data["timestamps"], data["channels"]):
                channels[str(ts)] = csi_path
        else:
            for key in data.files:
                ts = key[len("channel_"):] if key.startswith("channel_") else key
                channels[ts] = csi_path
    else:
        raise ValueError(f"unsupported CSI path: {csi_path}")
    return channels


def _timestamp_seconds(ts: str) -> float:
    return parse_timestamp(ts).timestamp()


def match_csi(
    df: pd.DataFrame,
    channel_map: Dict[str, str],
    tolerance_ms: float = 200.0,
) -> Tuple[pd.Series, pd.Series, List[str]]:
    """Return CSI path and matched CSI timestamp series, plus warnings."""
    tolerance_s = tolerance_ms / 1000.0
    paths: List[Optional[str]] = []
    matched_timestamps: List[Optional[str]] = []
    warnings: List[str] = []

    for ts in df["timestamp"]:
        if ts in channel_map:
            paths.append(channel_map[ts])
            matched_timestamps.append(ts)
            continue

        if not channel_map:
            paths.append(None)
            matched_timestamps.append(None)
            warnings.append(f"no CSI files available for {ts}")
            continue

        nearest = min(channel_map, key=lambda key: abs(_timestamp_seconds(key) - _timestamp_seconds(ts)))
        delta = abs(_timestamp_seconds(nearest) - _timestamp_seconds(ts))
        if delta <= tolerance_s:
            paths.append(channel_map[nearest])
            matched_timestamps.append(nearest)
            warnings.append(f"nearest CSI for {ts}: {nearest} (delta={delta * 1000.0:.1f} ms)")
        else:
            paths.append(None)
            matched_timestamps.append(None)
            warnings.append(f"no CSI within {tolerance_ms:.0f} ms for {ts}")

    return pd.Series(paths, index=df.index, dtype="object"), pd.Series(matched_timestamps, index=df.index, dtype="object"), warnings
