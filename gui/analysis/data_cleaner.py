"""CSV cleaning for type-a OAI GUI measurement records."""

from __future__ import annotations

from datetime import datetime
from typing import List, Tuple

import pandas as pd


def parse_timestamp(ts: str) -> datetime:
    """Parse the GUI timestamp format ``%Y%m%d_%H%M%S_%f``."""
    return datetime.strptime(str(ts).strip(), "%Y%m%d_%H%M%S_%f")


def clean_measurements(
    df: pd.DataFrame,
    gap_threshold_s: float = 3.0,
    max_samples_per_position: int = 30,
) -> Tuple[pd.DataFrame, List[str]]:
    """Split CSV rows into measurement positions and keep valid samples per position.

    A new position starts when the time gap to the previous CSV row is larger
    than ``gap_threshold_s``. If a position contains more than
    ``max_samples_per_position`` rows, the highest-throughput rows are kept;
    otherwise all rows are retained and a warning is logged.
    """
    df = df.copy()
    if "timestamp" not in df.columns:
        raise ValueError("CSV is missing the 'timestamp' column")
    if "throughput_mbps" not in df.columns:
        raise ValueError("CSV is missing the 'throughput_mbps' column")

    times = df["timestamp"].apply(parse_timestamp)
    position_ids = [0]
    for i in range(1, len(df)):
        gap = (times.iloc[i] - times.iloc[i - 1]).total_seconds()
        position_ids.append(position_ids[-1] + 1 if gap > gap_threshold_s else position_ids[-1])
    df["_position_raw"] = position_ids

    cleaned_parts: List[pd.DataFrame] = []
    logs: List[str] = []

    for raw_pos, group in df.groupby("_position_raw", sort=True):
        group = group.sort_values("timestamp")
        selected = group
        if len(group) > max_samples_per_position:
            selected = (
                group.sort_values("throughput_mbps", ascending=False)
                .head(max_samples_per_position)
                .sort_values("timestamp")
            )
            logs.append(
                f"position {raw_pos + 1}: trimmed {len(group)} rows to "
                f"{len(selected)} highest-throughput rows"
            )
        elif len(group) < max_samples_per_position:
            logs.append(
                f"warning: position {raw_pos + 1} has only {len(group)} rows "
                f"(expected {max_samples_per_position})"
            )

        selected = selected.copy()
        selected["PositionID"] = raw_pos + 1
        selected["SampleIndex"] = range(1, len(selected) + 1)
        cleaned_parts.append(selected)

    if not cleaned_parts:
        return df.drop(columns=["_position_raw"]), logs

    cleaned = pd.concat(cleaned_parts, ignore_index=True)
    cleaned = cleaned.drop(columns=["_position_raw"])
    return cleaned, logs
