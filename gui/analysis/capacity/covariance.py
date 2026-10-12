"""Capacity from the average spatial covariance within one position."""

from __future__ import annotations

from typing import Sequence

import numpy as np


EPS = 1e-12


def covariance_capacity_from_channels(
    channels: Sequence[np.ndarray],
    noise_power: float = 1.0,
) -> float:
    """Average covariance capacity over the common measured subcarriers.

    For each subcarrier k:
    ``R(k) = mean_n(H_n(k) H_n(k)^H)``.
    """
    if not channels:
        return float("nan")
    if noise_power <= 0:
        raise ValueError("noise_power must be positive")

    arrays = [np.asarray(channel, dtype=np.complex128) for channel in channels]
    shape = arrays[0].shape
    if len(shape) != 3 or any(array.shape != shape for array in arrays):
        raise ValueError("all channel snapshots must have the same 3D shape")

    common = np.ones(shape[-1], dtype=bool)
    for array in arrays:
        energy = np.sum(np.abs(array) ** 2, axis=(0, 1))
        common &= np.all(np.isfinite(array), axis=(0, 1)) & (energy > EPS)
    if not np.any(common):
        return float("nan")

    stacked = np.stack(arrays, axis=0)
    tx_count = shape[1]
    capacities = []
    for subcarrier in np.flatnonzero(common):
        matrices = stacked[:, :, :, subcarrier]
        covariance = np.mean(
            matrices @ matrices.conj().transpose(0, 2, 1),
            axis=0,
        )
        eigenvalues = np.maximum(
            np.linalg.eigvalsh(covariance)[::-1],
            0.0,
        )
        capacities.append(
            float(
                np.sum(
                    np.log2(
                        1.0
                        + eigenvalues
                        / (float(tx_count) * float(noise_power))
                    )
                )
            )
        )
    return float(np.mean(capacities))
