"""Correlation analysis between predictors and measured throughput."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats


PREDICTORS = [
    "rsrp_dBm",
    "max_rsrp_dBm",
    "mcs_scheduled_se",
    "shannon_capacity",
    "svd_capacity",
    "selected_stream_capacity",
]
POSITION_PREDICTORS = [*PREDICTORS, "position_covariance_capacity"]
TARGETS = {
    "throughput": "throughput_mbps",
    "mcs_scheduled_se": "mcs_scheduled_se",
}


def safe_pearson(x, y) -> float:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 2 or np.std(x[mask]) == 0 or np.std(y[mask]) == 0:
        return float("nan")
    with np.errstate(invalid="ignore"):
        value = np.corrcoef(x[mask], y[mask])[0, 1]
    return float(value)


def safe_spearman(x, y) -> float:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 2 or np.unique(x[mask]).size < 2 or np.unique(y[mask]).size < 2:
        return float("nan")
    value, _ = stats.spearmanr(x[mask], y[mask])
    return float(value)


def correlation_table(
    df: pd.DataFrame,
    mode: str,
    target: str = "throughput",
) -> pd.DataFrame:
    """Compute per-direction Pearson/Spearman correlation for one target."""
    target_column = TARGETS[target]
    rows = []
    for direction in sorted(df["direction"].dropna().unique()):
        sub = df[df["direction"] == direction]
        target_values = pd.to_numeric(sub.get(target_column), errors="coerce")
        predictors = (
            POSITION_PREDICTORS if mode == "position" else PREDICTORS
        )
        for predictor in predictors:
            if predictor == target_column or predictor not in sub.columns:
                continue
            values = pd.to_numeric(sub[predictor], errors="coerce")
            pearson = safe_pearson(values, target_values)
            spearman = safe_spearman(values, target_values)
            samples = int(
                (
                    np.isfinite(values.to_numpy(dtype=float))
                    & np.isfinite(target_values.to_numpy(dtype=float))
                ).sum()
            )
            rows.append(
                {
                    "mode": mode,
                    "direction": direction,
                    "target": target,
                    "predictor": predictor,
                    "samples": samples,
                    "pearson": pearson,
                    "spearman": spearman,
                }
            )
    result = pd.DataFrame(rows)
    if not result.empty:
        result["pearson_rank"] = (
            result.assign(abs_pearson=result["pearson"].abs())
            .groupby(["direction", "target"])["abs_pearson"]
            .rank(ascending=False, method="min")
            .astype("Int64")
            .reset_index(drop=True)
        )
        result["spearman_rank"] = (
            result.assign(abs_spearman=result["spearman"].abs())
            .groupby(["direction", "target"])["abs_spearman"]
            .rank(ascending=False, method="min")
            .astype("Int64")
            .reset_index(drop=True)
        )
    return result
