"""CSI normalization policy for raw OAI channel estimates.

The recorded ``c16_t`` channel values already carry OAI fixed-point, FFT, and
RF-gain scaling. This framework intentionally preserves that raw channel power
instead of renormalizing it to an RSRP-derived SNR. A single common noise
power is used by every capacity metric so the metrics remain comparable.
"""

from __future__ import annotations

import numpy as np

from gui.analysis.csi_parser import valid_subcarrier_indices


NOISE_POWER_DEFAULT = 1.0


def normalize_channel(h: np.ndarray, noise_power: float = NOISE_POWER_DEFAULT) -> np.ndarray:
    """Return the channel unchanged while enforcing a float complex dtype."""
    if noise_power <= 0:
        raise ValueError("noise_power must be positive")
    return h.astype(np.complex128, copy=False)


def raw_channel_power(
    h: np.ndarray,
    valid_indices: np.ndarray | None = None,
) -> float:
    """Return mean Frobenius power over valid CSI-RS subcarriers."""
    if valid_indices is None:
        valid_indices = valid_subcarrier_indices(h)
    if len(valid_indices) == 0:
        return float("nan")
    return float(np.mean(np.sum(np.abs(h[:, :, valid_indices]) ** 2, axis=(0, 1))))


def raw_channel_power_db(
    h: np.ndarray,
    valid_indices: np.ndarray | None = None,
) -> float:
    power = raw_channel_power(h, valid_indices=valid_indices)
    if not np.isfinite(power) or power <= 0:
        return float("nan")
    return float(10.0 * np.log10(power))
