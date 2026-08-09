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
C16_TO_FLOAT_SCALE = 1.0 / 32768.0


def normalize_channel(
    h: np.ndarray,
    noise_power: float = NOISE_POWER_DEFAULT,
    snr_db: float | None = None,
) -> np.ndarray:
    """Normalize a raw channel snapshot.

    The stored channel originates from ``c16_t`` fixed-point samples, so it is
    first rescaled from the 16-bit integer range to ``[-1, 1)`` by dividing by
    32768. With ``snr_db=None`` the channel is returned after that rescaling,
    enforcing a float complex dtype. With ``snr_db`` set, the channel is then
    scaled so its mean power over valid CSI-RS subcarriers equals
    ``noise_power * 10**(snr_db/10)``, removing absolute RX-gain scaling while
    preserving channel shape.
    """
    if noise_power <= 0:
        raise ValueError("noise_power must be positive")
    h = h.astype(np.complex128, copy=False) * C16_TO_FLOAT_SCALE
    if snr_db is None:
        return h
    target_power = float(noise_power) * 10.0 ** (float(snr_db) / 10.0)
    power = raw_channel_power(h)
    if not np.isfinite(power) or power <= 0:
        return h
    return h * np.sqrt(target_power / power)


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
