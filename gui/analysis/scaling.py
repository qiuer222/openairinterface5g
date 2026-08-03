"""OAI CSI recording-path scaling analysis and channel normalization."""

from __future__ import annotations

from typing import Dict, Tuple

import numpy as np


SQ15_SQUARED_NORM_FACTOR_DB = 90.3089986992
SQ15_AMPLITUDE = 32768.0
OAI_AMP_SHIFT = 9
OAI_AMP = 1 << OAI_AMP_SHIFT

SOURCE_REFS = [
    "openair1/PHY/NR_UE_TRANSPORT/csi_rx.c:1086-1092: writes csi_rs_estimated_channel_freq to ue_shm_write_csi_rs",
    "openair1/PHY/NR_UE_TRANSPORT/csi_rx.c:430-441: LS estimate uses c16MulConjShift(tx,rx,csi_rs_generated_signal_bits)",
    "openair1/PHY/NR_UE_TRANSPORT/csi_rx.c:477-497: interpolation filter applied before shm export",
    "openair1/PHY/NR_UE_TRANSPORT/csi_rx.c:226-228: RSRP converts SQ15 FFT-domain power to dBm",
    "openair1/PHY/MODULATION/slot_fep_nr.c:27-31: rxdataF is produced by dft() without 1/N normalization",
    "common/utils/nr/nr_common.h:79-83: SQ15 squared-norm compensation = 90.309 dB",
    "openair1/PHY/impl_defs_top.h:196-202: AMP_SHIFT=9, AMP=512 for the generated CSI-RS signal",
    "openair1/PHY/NR_REFSIG/nr_gen_mod_table.c:23-28: QPSK modulation table amplitude is 32768",
    "openair1/PHY/TOOLS/tools_defs.h:176-182: c16MulConjShift arithmetic",
]


def channel_mean_power(h: np.ndarray, energy_threshold: float = 1e-12) -> Tuple[float, int]:
    """Mean squared-magnitude over valid subcarriers and all MIMO entries."""
    if h.ndim != 3:
        raise ValueError(f"expected channel shape (rx, tx, fft), got {h.shape}")
    energy = np.sum(np.abs(h) ** 2, axis=(0, 1))
    valid = np.flatnonzero(energy > energy_threshold)
    if len(valid) == 0:
        return 0.0, 0
    return float(np.mean(energy[valid])), int(len(valid))


def normalize_to_snr(h: np.ndarray, snr_db: float) -> Tuple[np.ndarray, float, float, int]:
    """Scale H so its average squared norm equals the configured SNR linear power."""
    mean_power, valid_count = channel_mean_power(h)
    if valid_count == 0 or mean_power <= 0.0:
        return np.zeros_like(h), 0.0, mean_power, valid_count
    target_power = 10.0 ** (snr_db / 10.0)
    scale = float(np.sqrt(target_power / mean_power))
    return h * scale, scale, mean_power, valid_count


def rsrp_calibration(h: np.ndarray, rsrp_dbm: float, fft_size: int = 2048) -> Dict:
    """Derive RSRP-consistent calibration factors from OAI's SQ15/FFT RSRP formula."""
    mean_power, valid_count = channel_mean_power(h)
    if valid_count == 0 or mean_power <= 0.0 or not np.isfinite(rsrp_dbm):
        return {
            "raw_channel_power_db": float("nan"),
            "uncompensated_channel_power_dbm": float("nan"),
            "rsrp_calibrated_gain_db": float("nan"),
            "rsrp_calibrated_scale": float("nan"),
        }

    raw_power_db = 10.0 * np.log10(mean_power)
    fft_db = 10.0 * np.log10(float(fft_size))
    uncompensated_dbm = raw_power_db + 30.0 - SQ15_SQUARED_NORM_FACTOR_DB - fft_db
    gain_db = uncompensated_dbm - float(rsrp_dbm)
    rsrp_linear = 10.0 ** (float(rsrp_dbm) / 10.0)
    scale_to_rsrp = np.sqrt(rsrp_linear / mean_power)
    return {
        "raw_channel_power_db": float(raw_power_db),
        "uncompensated_channel_power_dbm": float(uncompensated_dbm),
        "rsrp_calibrated_gain_db": float(gain_db),
        "rsrp_calibrated_scale": float(scale_to_rsrp),
    }

