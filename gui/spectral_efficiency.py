"""Spectral-efficiency calculations shared by the UE and gNB monitors."""

from __future__ import annotations

from typing import Optional


def bandwidth_hz(n_rb: int, subcarrier_spacing_khz: int) -> float:
    """Return the bandwidth represented by a PRB allocation."""
    if n_rb <= 0 or subcarrier_spacing_khz <= 0:
        return 0.0
    return float(n_rb) * 12.0 * float(subcarrier_spacing_khz) * 1000.0


def scheduled_spectral_efficiency(
    tbs_bits: int,
    n_prb: int,
    n_symbols: int,
) -> Optional[float]:
    """Return per-allocation scheduled SE in bit/s/Hz."""
    if tbs_bits <= 0 or n_prb <= 0 or n_symbols <= 0:
        return None
    return float(tbs_bits) / (float(n_prb) * 12.0 * float(n_symbols))


def application_spectral_efficiency(
    throughput_bps: Optional[float],
    n_rb: int,
    subcarrier_spacing_khz: int,
) -> Optional[float]:
    """Return measured application-level SE in bit/s/Hz."""
    if throughput_bps is None:
        return None
    bw_hz = bandwidth_hz(n_rb, subcarrier_spacing_khz)
    if bw_hz <= 0.0:
        return None
    return max(0.0, float(throughput_bps)) / bw_hz
