"""UL codebook precoding, MMSE post-SINR, and capped mutual information."""

from __future__ import annotations

from functools import lru_cache

import numpy as np


_UL_PRECODERS = {
    (2, 1): (
        (("1",), ("0",)),
        (("0",), ("1",)),
        (("1",), ("1",)),
        (("1",), ("n",)),
        (("1",), ("j",)),
        (("1",), ("o",)),
    ),
    (2, 2): (
        (("1", "0"), ("0", "1")),
        (("1", "1"), ("1", "n")),
        (("1", "1"), ("j", "o")),
    ),
    (4, 1): (
        (("1",), ("0",), ("0",), ("0",)),
        (("0",), ("1",), ("0",), ("0",)),
        (("0",), ("0",), ("1",), ("0",)),
        (("0",), ("0",), ("0",), ("1",)),
        (("1",), ("0",), ("1",), ("0",)),
        (("1",), ("0",), ("n",), ("0",)),
        (("1",), ("0",), ("j",), ("0",)),
        (("1",), ("0",), ("o",), ("0",)),
        (("0",), ("1",), ("0",), ("1",)),
        (("0",), ("1",), ("0",), ("n",)),
        (("0",), ("1",), ("0",), ("j",)),
        (("0",), ("1",), ("0",), ("o",)),
        (("1",), ("1",), ("1",), ("1",)),
        (("1",), ("1",), ("j",), ("j",)),
        (("1",), ("1",), ("n",), ("n",)),
        (("1",), ("1",), ("o",), ("o",)),
        (("1",), ("j",), ("1",), ("j",)),
        (("1",), ("j",), ("j",), ("n",)),
        (("1",), ("j",), ("n",), ("o",)),
        (("1",), ("j",), ("o",), ("1",)),
        (("1",), ("n",), ("1",), ("n",)),
        (("1",), ("n",), ("j",), ("o",)),
        (("1",), ("n",), ("n",), ("1",)),
        (("1",), ("n",), ("o",), ("j",)),
        (("1",), ("o",), ("1",), ("o",)),
        (("1",), ("o",), ("j",), ("1",)),
        (("1",), ("o",), ("n",), ("j",)),
        (("1",), ("o",), ("o",), ("n",)),
    ),
    (4, 2): (
        (("1", "0"), ("0", "1"), ("0", "0"), ("0", "0")),
        (("1", "0"), ("0", "0"), ("0", "1"), ("0", "0")),
        (("1", "0"), ("0", "0"), ("0", "0"), ("0", "1")),
        (("0", "0"), ("1", "0"), ("0", "1"), ("0", "0")),
        (("0", "0"), ("1", "0"), ("0", "0"), ("0", "1")),
        (("0", "0"), ("0", "0"), ("1", "0"), ("0", "1")),
        (("1", "0"), ("0", "1"), ("1", "0"), ("0", "o")),
        (("1", "0"), ("0", "1"), ("1", "0"), ("0", "j")),
        (("1", "0"), ("0", "1"), ("o", "0"), ("0", "1")),
        (("1", "0"), ("0", "1"), ("o", "0"), ("0", "n")),
        (("1", "0"), ("0", "1"), ("n", "0"), ("0", "o")),
        (("1", "0"), ("0", "1"), ("n", "0"), ("0", "j")),
        (("1", "0"), ("0", "1"), ("j", "0"), ("0", "1")),
        (("1", "0"), ("0", "1"), ("j", "0"), ("0", "n")),
        (("1", "1"), ("1", "1"), ("1", "n"), ("1", "n")),
        (("1", "1"), ("1", "1"), ("j", "o"), ("j", "o")),
        (("1", "1"), ("j", "j"), ("1", "n"), ("j", "o")),
        (("1", "1"), ("j", "j"), ("j", "o"), ("n", "1")),
        (("1", "1"), ("n", "n"), ("1", "n"), ("n", "1")),
        (("1", "1"), ("n", "n"), ("j", "o"), ("o", "j")),
        (("1", "1"), ("o", "o"), ("1", "n"), ("o", "j")),
        (("1", "1"), ("o", "o"), ("j", "o"), ("1", "n")),
    ),
    (4, 3): (
        (("1", "0", "0"), ("0", "1", "0"), ("0", "0", "1"), ("0", "0", "0")),
        (("1", "0", "0"), ("0", "1", "0"), ("1", "0", "0"), ("0", "0", "1")),
        (("1", "0", "0"), ("0", "1", "0"), ("n", "0", "0"), ("0", "0", "1")),
        (("1", "1", "1"), ("1", "n", "1"), ("1", "1", "n"), ("1", "n", "n")),
        (("1", "1", "1"), ("1", "n", "1"), ("j", "j", "o"), ("j", "o", "o")),
        (("1", "1", "1"), ("n", "1", "n"), ("1", "1", "n"), ("n", "1", "1")),
        (("1", "1", "1"), ("n", "1", "n"), ("j", "j", "o"), ("o", "j", "j")),
    ),
    (4, 4): (
        (("1", "0", "0", "0"), ("0", "1", "0", "0"), ("0", "0", "1", "0"), ("0", "0", "0", "1")),
        (("1", "1", "0", "0"), ("0", "0", "1", "1"), ("1", "n", "0", "0"), ("0", "0", "1", "n")),
        (("1", "1", "0", "0"), ("0", "0", "1", "1"), ("j", "o", "0", "0"), ("0", "0", "j", "o")),
        (("1", "1", "1", "1"), ("1", "n", "1", "n"), ("1", "1", "n", "n"), ("1", "n", "n", "1")),
        (("1", "1", "1", "1"), ("1", "n", "1", "n"), ("j", "j", "o", "o"), ("j", "o", "o", "j")),
    ),
}


@lru_cache(maxsize=None)
def _coef(value: str) -> complex:
    return {
        "0": 0.0,
        "1": 1.0,
        "n": -1.0,
        "j": 1.0j,
        "o": -1.0j,
    }[value]


def ul_precoder(tpmi: int, layers: int, num_ports: int) -> np.ndarray:
    """Return the OAI UL codebook precoder as an ``(Ntx, layers)`` matrix."""
    key = (int(num_ports), int(layers))
    if key not in _UL_PRECODERS:
        raise ValueError(
            f"UL TPMI codebook unsupported for {num_ports} ports and "
            f"{layers} layers"
        )
    table = _UL_PRECODERS[key]
    if tpmi < 0 or tpmi >= len(table):
        raise ValueError(
            f"UL TPMI {tpmi} out of range for {layers} layers and "
            f"{num_ports} ports"
        )
    return np.asarray(
        [[_coef(value) for value in row] for row in table[tpmi]],
        dtype=np.complex128,
    )


def mmse_capped_mi(
    channel: np.ndarray,
    valid_subcarriers: np.ndarray,
    precoder: np.ndarray,
    modulation_order: int,
    noise_power: float,
    sinr_cap_db: float = 30.0,
) -> dict:
    """Compute per-layer MMSE post-SINR and capped mutual-information SE."""
    if channel.ndim != 3:
        raise ValueError("channel must have shape (rx, tx, subcarrier)")
    if noise_power <= 0:
        raise ValueError("noise_power must be positive")
    if modulation_order not in (2, 4, 6, 8):
        raise ValueError(f"unsupported modulation order {modulation_order}")
    if not np.isfinite(sinr_cap_db) or sinr_cap_db <= 0:
        raise ValueError("sinr_cap_db must be positive and finite")

    precoder = np.asarray(precoder, dtype=np.complex128)
    if precoder.ndim != 2 or precoder.shape[0] != channel.shape[1]:
        raise ValueError(
            "precoder shape must be (tx, layers) and match channel tx ports"
        )
    layers = precoder.shape[1]
    if layers < 1 or layers > channel.shape[0]:
        raise ValueError("precoder layer count exceeds receive antennas")

    valid = np.asarray(valid_subcarriers, dtype=int)
    if len(valid) == 0:
        raise ValueError("no valid channel subcarriers")
    effective = np.einsum(
        "rtk,tl->klr",
        channel[:, :, valid],
        precoder,
        optimize=True,
    )

    effective_matrix = effective.conj().transpose(0, 2, 1)
    gram = effective @ effective_matrix
    gram += float(noise_power) * np.eye(layers, dtype=np.complex128)
    try:
        equalizer = np.linalg.solve(gram, effective)
    except np.linalg.LinAlgError:
        equalizer = np.linalg.pinv(gram) @ effective
    response = equalizer @ effective_matrix

    desired = np.abs(np.diagonal(response, axis1=1, axis2=2)) ** 2
    total = (
        float(noise_power) * np.sum(np.abs(equalizer) ** 2, axis=2)
        + np.sum(np.abs(response) ** 2, axis=2)
    )
    interference = np.maximum(total - desired, 0.0)
    post_sinr = desired / np.maximum(interference, np.finfo(float).tiny)

    cap_linear = 10.0 ** (float(sinr_cap_db) / 10.0)
    post_sinr = np.minimum(post_sinr, cap_linear)
    mi_per_layer = np.minimum(
        np.log2(1.0 + post_sinr),
        float(modulation_order),
    )
    return {
        "se": float(np.sum(np.mean(mi_per_layer, axis=0))),
        "post_sinr_db": np.mean(
            10.0 * np.log10(np.maximum(post_sinr, np.finfo(float).tiny)),
            axis=0,
        ),
        "mi_per_layer": np.mean(mi_per_layer, axis=0),
    }
