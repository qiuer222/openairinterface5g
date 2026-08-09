"""Linear regression metrics for throughput prediction evaluation."""

from __future__ import annotations

import numpy as np
import pandas as pd

from gui.analysis.analysis.correlation import PREDICTORS


def linear_metrics(x, y) -> dict:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 2 or np.std(x[mask]) == 0:
        return {
            "samples": int(mask.sum()),
            "slope": float("nan"),
            "intercept": float("nan"),
            "r2": float("nan"),
            "mae": float("nan"),
            "rmse": float("nan"),
        }
    slope, intercept = np.polyfit(x[mask], y[mask], 1)
    predicted = slope * x[mask] + intercept
    residuals = y[mask] - predicted
    ss_res = float(np.sum(residuals ** 2))
    ss_tot = float(np.sum((y[mask] - np.mean(y[mask])) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return {
        "samples": int(mask.sum()),
        "slope": float(slope),
        "intercept": float(intercept),
        "r2": float(r2),
        "mae": float(np.mean(np.abs(residuals))),
        "rmse": float(np.sqrt(np.mean(residuals ** 2))),
    }


def regression_table(df: pd.DataFrame, mode: str) -> pd.DataFrame:
    """Compute linear R2/MAE/RMSE per direction and predictor."""
    rows = []
    for direction in sorted(df["direction"].dropna().unique()):
        sub = df[df["direction"] == direction]
        throughput = pd.to_numeric(sub.get("throughput_mbps"), errors="coerce")
        for predictor in PREDICTORS:
            if predictor not in sub.columns:
                continue
            values = pd.to_numeric(sub[predictor], errors="coerce")
            metrics = linear_metrics(values, throughput)
            rows.append(
                {
                    "mode": mode,
                    "direction": direction,
                    "predictor": predictor,
                    **metrics,
                }
            )
    return pd.DataFrame(rows)
