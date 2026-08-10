"""Channel feature extraction from per-subcarrier singular values."""

from __future__ import annotations

from typing import Iterable, List

import numpy as np


EPS = 1e-12


def _empty_features(valid_subcarrier_count: int) -> dict:
    return {
        "valid_subcarriers": int(valid_subcarrier_count),
        "eigenvalue_1": float("nan"),
        "eigenvalue_2": float("nan"),
        "eigenvalue_3": float("nan"),
        "eigenvalue_4": float("nan"),
        "singular_value_1": float("nan"),
        "singular_value_2": float("nan"),
        "singular_value_3": float("nan"),
        "singular_value_4": float("nan"),
        "eigenvalue_sum": float("nan"),
        "frobenius_norm2": float("nan"),
        "channel_rank": float("nan"),
        "condition_number": float("nan"),
        "eigenvalue_sum_std": float("nan"),
        "condition_number_std": float("nan"),
    }


def extract_channel_features_from_singular_values(
    sv: np.ndarray,
    valid_subcarrier_count: int,
) -> dict:
    """Aggregate per-subcarrier SVD statistics from an ``(N, rank)`` matrix."""
    empty = _empty_features(valid_subcarrier_count)
    sv = np.asarray(sv, dtype=float)
    if sv.ndim != 2 or sv.shape[0] == 0 or sv.shape[1] == 0:
        return empty
    if not np.all(np.isfinite(sv)):
        return empty

    eig = sv ** 2
    max_rank = sv.shape[1]

    with np.errstate(divide="ignore", invalid="ignore"):
        condition = sv[:, 0] / np.maximum(sv[:, -1], EPS)
        ranks = np.sum(sv > np.maximum(sv[:, :1] * 1e-3, EPS), axis=1)
    eigenvalue_sum = np.sum(eig, axis=1)

    result = {
        "valid_subcarriers": int(valid_subcarrier_count),
        "eigenvalue_sum": float(np.mean(eigenvalue_sum)),
        "frobenius_norm2": float(np.mean(eigenvalue_sum)),
        "channel_rank": float(np.mean(ranks)),
        "condition_number": float(np.nanmean(condition)),
        "eigenvalue_sum_std": float(np.std(eigenvalue_sum)),
        "condition_number_std": float(np.nanstd(condition)),
    }
    for idx in range(max_rank):
        result[f"eigenvalue_{idx + 1}"] = float(np.mean(eig[:, idx]))
        result[f"singular_value_{idx + 1}"] = float(np.mean(sv[:, idx]))
    return result


def extract_channel_features(
    singular_values_by_subcarrier: Iterable[np.ndarray],
    valid_subcarrier_count: int,
) -> dict:
    """Aggregate per-subcarrier SVD statistics into a flat feature dict."""
    empty = _empty_features(valid_subcarrier_count)
    rows = [np.asarray(s, dtype=float) for s in singular_values_by_subcarrier]
    rows = [row for row in rows if row.size > 0 and np.all(np.isfinite(row))]
    if not rows:
        return empty

    sv = np.vstack(rows)
    eig = sv ** 2
    max_rank = sv.shape[1]

    with np.errstate(divide="ignore", invalid="ignore"):
        condition = sv[:, 0] / np.maximum(sv[:, -1], EPS)
        ranks = np.sum(sv > np.maximum(sv[:, :1] * 1e-3, EPS), axis=1)
    eigenvalue_sum = np.sum(eig, axis=1)

    result = {
        "valid_subcarriers": int(valid_subcarrier_count),
        "eigenvalue_sum": float(np.mean(eigenvalue_sum)),
        "frobenius_norm2": float(np.mean(eigenvalue_sum)),
        "channel_rank": float(np.mean(ranks)),
        "condition_number": float(np.nanmean(condition)),
        "eigenvalue_sum_std": float(np.std(eigenvalue_sum)),
        "condition_number_std": float(np.nanstd(condition)),
    }
    for idx in range(max_rank):
        result[f"eigenvalue_{idx + 1}"] = float(np.mean(eig[:, idx]))
        result[f"singular_value_{idx + 1}"] = float(np.mean(sv[:, idx]))
    return result
