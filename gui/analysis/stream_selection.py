"""Predicted stream selection and accuracy evaluation."""

from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np
import pandas as pd


def select_optimal_stream(
    capacities: List[float],
    max_streams: Optional[int] = None,
) -> int:
    """Return the stream count maximizing average capacity."""
    values = np.asarray(capacities, dtype=float)
    if max_streams is not None:
        values = values[:max_streams]
    if len(values) == 0 or not np.any(np.isfinite(values)):
        return 0
    return int(np.nanargmax(values) + 1)


def stream_selection_tables(
    df: pd.DataFrame,
    mode: str,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Build summary and long-format confusion tables for a processed frame."""
    summary_rows = []
    confusion_rows = []

    for direction in sorted(df["direction"].dropna().unique()):
        sub = df[df["direction"] == direction]
        truth = pd.to_numeric(sub.get("actual_layers"), errors="coerce")
        for algorithm, predicted_col in (
            ("svd", "svd_optimal_stream"),
            ("zf", "zf_optimal_stream"),
        ):
            predicted = pd.to_numeric(sub.get(predicted_col), errors="coerce")
            valid = (
                truth.notna()
                & predicted.notna()
                & (truth >= 1)
                & (predicted >= 1)
            )
            y_true = truth[valid].astype(int)
            y_pred = predicted[valid].astype(int)
            if len(y_true) == 0:
                summary_rows.append(
                    {
                        "mode": mode,
                        "direction": direction,
                        "algorithm": algorithm,
                        "samples": 0,
                        "accuracy": float("nan"),
                        "mae": float("nan"),
                    }
                )
                continue

            summary_rows.append(
                {
                    "mode": mode,
                    "direction": direction,
                    "algorithm": algorithm,
                    "samples": len(y_true),
                    "accuracy": float(np.mean(y_true == y_pred)),
                    "mae": float(np.mean(np.abs(y_pred - y_true))),
                }
            )
            counts = pd.DataFrame(
                {"true": y_true, "predicted": y_pred}
            ).value_counts().reset_index(name="count")
            counts.insert(0, "mode", mode)
            counts.insert(1, "direction", direction)
            counts.insert(2, "algorithm", algorithm)
            confusion_rows.append(counts)

    summary = pd.DataFrame(summary_rows)
    confusion = (
        pd.concat(confusion_rows, ignore_index=True)
        if confusion_rows
        else pd.DataFrame(
            columns=["mode", "direction", "algorithm", "true", "predicted", "count"]
        )
    )
    return summary, confusion
