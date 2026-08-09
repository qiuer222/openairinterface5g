"""Correlation analysis between predictors and measured throughput."""

from __future__ import annotations

from typing import Iterable, List

import numpy as np
import pandas as pd
from scipy import stats


PREDICTORS = ["rsrp_dBm", "shannon_capacity", "svd_capacity", "zf_capacity"]


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


def correlation_table(df: pd.DataFrame, mode: str) -> pd.DataFrame:
    """Compute per-direction Pearson/Spearman correlation for all predictors."""
    rows = []
    for direction in sorted(df["direction"].dropna().unique()):
        sub = df[df["direction"] == direction]
        throughput = pd.to_numeric(sub.get("throughput_mbps"), errors="coerce")
        for predictor in PREDICTORS:
            if predictor not in sub.columns:
                continue
            values = pd.to_numeric(sub[predictor], errors="coerce")
            pearson = safe_pearson(values, throughput)
            spearman = safe_spearman(values, throughput)
            samples = int(
                (
                    np.isfinite(values.to_numpy(dtype=float))
                    & np.isfinite(throughput.to_numpy(dtype=float))
                ).sum()
            )
            rows.append(
                {
                    "mode": mode,
                    "direction": direction,
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
            .groupby("direction")["abs_pearson"]
            .rank(ascending=False, method="min")
            .astype("Int64")
            .reset_index(drop=True)
        )
        result["spearman_rank"] = (
            result.assign(abs_spearman=result["spearman"].abs())
            .groupby("direction")["abs_spearman"]
            .rank(ascending=False, method="min")
            .astype("Int64")
            .reset_index(drop=True)
        )
    return result
