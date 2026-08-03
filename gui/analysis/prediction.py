"""Correlation, regression, prediction, and stream-selection reporting."""

from __future__ import annotations

from collections import Counter
from typing import Dict, List, Sequence, Tuple
import warnings

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import PolynomialFeatures


PREDICTOR_COLUMNS = [
    "rsrp_dBm",
    "average_svd_capacity",
    "best_svd_capacity",
    "best_zf_capacity",
]


def _safe_pearson(x: np.ndarray, y: np.ndarray) -> float:
    mask = np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 2 or np.std(x[mask]) == 0 or np.std(y[mask]) == 0:
        return float("nan")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        value, _ = stats.pearsonr(x[mask], y[mask])
    return float(value)


def _safe_spearman(x: np.ndarray, y: np.ndarray) -> float:
    mask = np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 2 or np.std(x[mask]) == 0 or np.std(y[mask]) == 0:
        return float("nan")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        value, _ = stats.spearmanr(x[mask], y[mask])
    return float(value)


def regression_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> Dict:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    mask = np.isfinite(y_true) & np.isfinite(y_pred)
    y_true, y_pred = y_true[mask], y_pred[mask]
    if len(y_true) == 0:
        return {
            "pearson": float("nan"),
            "spearman": float("nan"),
            "rmse": float("nan"),
            "mae": float("nan"),
            "mape": float("nan"),
            "r2": float("nan"),
            "samples": 0,
        }
    nonzero = y_true > 0
    mape = float(np.mean(np.abs((y_pred[nonzero] - y_true[nonzero]) / y_true[nonzero])) * 100.0) if np.any(nonzero) else float("nan")
    return {
        "pearson": _safe_pearson(y_true, y_pred),
        "spearman": _safe_spearman(y_true, y_pred),
        "rmse": float(np.sqrt(mean_squared_error(y_true, y_pred))),
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "mape": mape,
        "r2": float(r2_score(y_true, y_pred)),
        "samples": int(len(y_true)),
    }


def fit_predictors(
    df: pd.DataFrame,
    methods: Sequence[str] = ("linear", "poly"),
) -> Tuple[pd.DataFrame, Dict[str, np.ndarray]]:
    rows: List[Dict] = []
    predictions: Dict[str, np.ndarray] = {}
    y = df["throughput_mbps"].to_numpy(dtype=float)

    for predictor in PREDICTOR_COLUMNS:
        x = df[predictor].to_numpy(dtype=float).reshape(-1, 1)
        finite = np.isfinite(x[:, 0]) & np.isfinite(y)
        if finite.sum() < 2:
            continue
        for method in methods:
            if method == "linear":
                model = LinearRegression()
            elif method == "poly":
                model = make_pipeline(PolynomialFeatures(degree=2, include_bias=False), LinearRegression())
            else:
                raise ValueError(f"unknown prediction method: {method}")
            model.fit(x[finite], y[finite])
            pred = np.full(len(y), np.nan)
            pred[finite] = model.predict(x[finite])
            metrics = regression_metrics(y[finite], pred[finite])
            row = {
                "predictor": predictor,
                "method": method,
                **metrics,
            }
            if method == "linear":
                row["slope"] = float(model.coef_[0])
                row["intercept"] = float(model.intercept_)
            predictions[f"{method}_{predictor}"] = pred
            rows.append(row)

    result = pd.DataFrame(rows)
    if not result.empty:
        result["rmse_rank"] = result["rmse"].rank(ascending=True, method="min").astype(int)
        result["r2_rank"] = result["r2"].rank(ascending=False, method="min").astype(int)
    return result, predictions


def stream_selection_metrics(df: pd.DataFrame, truth_col: str = "layers") -> Dict:
    truth = df[truth_col].to_numpy(dtype=int)
    svd = df["optimal_stream_number_svd"].to_numpy(dtype=int)
    zf = df["optimal_stream_number_zf"].to_numpy(dtype=int)
    max_label = int(max(truth.max(), svd.max(), zf.max(), 2))
    labels = list(range(1, max_label + 1))

    from sklearn.metrics import confusion_matrix

    svd_confusion = confusion_matrix(truth, svd, labels=labels)
    zf_confusion = confusion_matrix(truth, zf, labels=labels)
    svd_errors = svd - truth
    zf_errors = zf - truth

    return {
        "labels": labels,
        "svd_accuracy": float(np.mean(truth == svd)),
        "zf_accuracy": float(np.mean(truth == zf)),
        "svd_mae": float(np.mean(np.abs(svd_errors))),
        "zf_mae": float(np.mean(np.abs(zf_errors))),
        "svd_confusion": svd_confusion,
        "zf_confusion": zf_confusion,
        "svd_error_distribution": dict(Counter(svd_errors.tolist())),
        "zf_error_distribution": dict(Counter(zf_errors.tolist())),
    }
