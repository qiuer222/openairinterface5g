"""Load OAI CSI channel snapshots and select valid CSI-RS subcarriers."""

from __future__ import annotations

import os
from typing import Tuple

import numpy as np


EPS = 1e-12


def load_channel(path: str) -> np.ndarray:
    """Load a channel snapshot and return it as ``H(rx, tx, subcarrier)``.

    CSI-RS files are stored as 3D complex arrays. SRS files can be stored as
    4D arrays with a singleton symbol axis, for example
    ``(rx, tx, symbol, fft)``; the first symbol is used in that case.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    h = np.load(path, allow_pickle=False)
    h = _to_three_dimensional(h)
    return h.astype(np.complex128, copy=False)


def _to_three_dimensional(h: np.ndarray) -> np.ndarray:
    if h.ndim == 3:
        return h
    if h.ndim == 4:
        if h.shape[2] == 1:
            return h[:, :, 0, :]
        if h.shape[0] == 1:
            return h[0]
        return h[:, :, 0, :]
    raise ValueError(
        f"expected a 3D or 4D channel array, got shape {h.shape}"
    )


def valid_subcarrier_indices(
    h: np.ndarray,
    energy_threshold: float = EPS,
) -> np.ndarray:
    """Return subcarrier indices whose channel energy is non-zero."""
    if h.ndim != 3:
        raise ValueError(
            f"expected channel shape (rx, tx, subcarrier), got {h.shape}"
        )
    energy = np.sum(np.abs(h) ** 2, axis=(0, 1))
    finite = np.all(np.isfinite(h), axis=(0, 1))
    return np.flatnonzero(finite & (energy > energy_threshold))


def load_channel_and_valid(path: str) -> Tuple[np.ndarray, np.ndarray]:
    """Load a channel and return ``(H, valid_subcarrier_indices)``."""
    h = load_channel(path)
    return h, valid_subcarrier_indices(h)
