#!/usr/bin/env python3
"""CSI-RS shared memory reader and channel-quality computation.

The channel array written by OAI is ``c16_t[nb_rx_ant][num_ports][fft_size]``.
Subcarriers outside the configured CSI-RS allocation are zero-filled, so only
subcarriers with non-zero energy are used for capacity / condition-number /
rank averaging.
"""

from __future__ import annotations

import ctypes
import mmap
import os
from typing import Dict, Optional

import numpy as np


UE_SHM_MAGIC = 0x5545534D
CSI_RS_SHM = "/dev/shm/csi_rs_channel"
CSI_RS_MAX_RX_ANT = 4
CSI_RS_MAX_PORTS = 8
CSI_RS_MAX_FFT = 4096
CSI_RS_CHAN_BYTES = CSI_RS_MAX_RX_ANT * CSI_RS_MAX_PORTS * CSI_RS_MAX_FFT * 4


class CsiRsShmHdr(ctypes.Structure):
    """Packed header of ``csi_rs_shm_hdr_t`` from ``ue_shm.h``."""

    _pack_ = 1
    _fields_ = [
        ("magic",             ctypes.c_uint32),
        ("seq",               ctypes.c_uint64),
        ("frame",             ctypes.c_uint32),
        ("slot",              ctypes.c_uint32),
        ("num_rx_ant",        ctypes.c_uint8),
        ("num_ports",         ctypes.c_uint8),
        ("fft_size",          ctypes.c_uint16),
        ("n_rb_dl",           ctypes.c_uint16),
        ("subcarrier_spacing", ctypes.c_uint32),
    ]


CSI_RS_HDR_SIZE = ctypes.sizeof(CsiRsShmHdr)
CSI_RS_SHM_TOTAL_SIZE = CSI_RS_HDR_SIZE + CSI_RS_CHAN_BYTES
_COND_CAP = 1000.0
_SVD_EPS = 1e-9


class CsiRsReader:
    """Read the latest CSI-RS channel estimate and compute per-slot quality."""

    def __init__(self, path: str = CSI_RS_SHM, snr_db: float = 20.0):
        self.path = path
        self.snr_db = float(snr_db)
        self.fd: Optional[int] = None
        self.mm: Optional[mmap.mmap] = None
        self.prev_seq: int = 0

    def open(self) -> bool:
        if not os.path.exists(self.path):
            return False
        try:
            self.fd = os.open(self.path, os.O_RDONLY)
            self.mm = mmap.mmap(self.fd, CSI_RS_SHM_TOTAL_SIZE,
                                mmap.MAP_SHARED, mmap.PROT_READ)
            return True
        except OSError:
            self.close()
            return False

    def close(self) -> None:
        if self.mm is not None:
            self.mm.close()
            self.mm = None
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def read(self) -> Optional[Dict]:
        """Return fresh channel-quality metrics, or None when no new data."""
        if self.mm is None and not self.open():
            return None
        raw = self.mm[:CSI_RS_SHM_TOTAL_SIZE]
        hdr = CsiRsShmHdr.from_buffer_copy(raw)
        if hdr.magic != UE_SHM_MAGIC or hdr.seq == 0 or hdr.seq == self.prev_seq:
            return None
        self.prev_seq = hdr.seq

        n_elems = hdr.num_rx_ant * hdr.num_ports * hdr.fft_size
        dtype = np.dtype([("r", np.int16), ("i", np.int16)])
        arr = np.frombuffer(raw, dtype=dtype, count=n_elems, offset=CSI_RS_HDR_SIZE)
        arr = arr.reshape(hdr.num_rx_ant, hdr.num_ports, hdr.fft_size)
        h = arr["r"].astype(np.float64) + 1j * arr["i"].astype(np.float64)

        metrics = self._channel_metrics(
            h,
            num_rx=hdr.num_rx_ant,
            num_ports=hdr.num_ports,
            n_rb_dl=hdr.n_rb_dl,
            fft_size=hdr.fft_size,
            snr_db=self.snr_db,
        )
        metrics["frame"] = hdr.frame
        metrics["slot"] = hdr.slot
        metrics["channel"] = h
        return metrics

    @staticmethod
    def _channel_metrics(
        h: np.ndarray,
        num_rx: int,
        num_ports: int,
        n_rb_dl: int,
        fft_size: int,
        snr_db: float = 20.0,
    ) -> Dict:
        """Compute average capacity, condition number and rank over valid subcarriers."""
        rho = 10.0 ** (snr_db / 10.0)
        max_k = min(fft_size, n_rb_dl * 12) if n_rb_dl > 0 else fft_size
        capacities: list[float] = []
        conditions: list[float] = []
        ranks: list[int] = []
        sigma_sums: Optional[list[float]] = None
        sigma_count = 0
        valid = 0

        for k in range(max_k):
            hk = h[:, :, k]
            energy = float(np.sum(np.abs(hk) ** 2))
            if energy <= 0:
                continue
            sigma = np.linalg.svd(hk, compute_uv=False)
            sigma = sigma[sigma > _SVD_EPS]
            if sigma.size == 0:
                continue

            valid += 1
            sig2 = sigma**2
            capacities.append(float(np.sum(np.log2(1.0 + rho * sig2))))

            smax = float(sigma[0])
            smin = float(sigma[-1])
            conditions.append(min(smax / max(smin, _SVD_EPS), _COND_CAP))

            rank_threshold = max(_SVD_EPS, 1e-3 * smax)
            ranks.append(int(np.sum(sigma > rank_threshold)))
            if sigma_sums is None:
                sigma_sums = [0.0] * sigma.size
            for i, s in enumerate(sigma):
                if i < len(sigma_sums):
                    sigma_sums[i] += float(s)
            sigma_count += 1

        if valid == 0:
            return {
                "capacity": 0.0,
                "condition_number": 0.0,
                "rank": 0,
                "valid_subcarriers": 0,
                "singular_values": [],
            }

        return {
            "capacity": float(np.mean(capacities)),
            "condition_number": float(np.mean(conditions)),
            "rank": int(round(float(np.mean(ranks)))),
            "valid_subcarriers": valid,
            "singular_values": (
                [s / sigma_count for s in sigma_sums]
                if sigma_sums and sigma_count
                else []
            ),
        }
