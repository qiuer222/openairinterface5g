#!/usr/bin/env python3
"""Reader for the gNB SRS channel estimate exported through shared memory."""

from __future__ import annotations

import ctypes
import mmap
import os
from typing import Dict, Optional

import numpy as np

from gui.csi_reader import CsiRsReader


GNB_SRS_SHM = "/dev/shm/srs_channel"
GNB_SRS_MAGIC = 0x474E424D
GNB_SRS_MAX_RX_ANT = 4
GNB_SRS_MAX_PORTS = 8
GNB_SRS_MAX_FFT = 4096
GNB_SRS_MAX_SYMBOLS = 4
GNB_SRS_CHAN_BYTES = (
    GNB_SRS_MAX_RX_ANT * GNB_SRS_MAX_PORTS * GNB_SRS_MAX_FFT * GNB_SRS_MAX_SYMBOLS * 4
)


class GnbSrsShmHdr(ctypes.Structure):
    """Packed layout of ``gnb_srs_shm_hdr_t`` from ``gNB_shm.h``."""

    _pack_ = 1
    _fields_ = [
        ("magic",              ctypes.c_uint32),
        ("seq",                ctypes.c_uint64),
        ("frame",              ctypes.c_uint32),
        ("slot",               ctypes.c_uint32),
        ("rnti",               ctypes.c_uint16),
        ("num_rx_ant",         ctypes.c_uint8),
        ("num_ports",          ctypes.c_uint8),
        ("fft_size",           ctypes.c_uint16),
        ("n_rb",               ctypes.c_uint16),
        ("subcarrier_spacing", ctypes.c_uint32),
        ("n_srs_symbols",      ctypes.c_uint8),
        ("snr_db_x10",         ctypes.c_int16),
    ]


GNB_SRS_HDR_SIZE = ctypes.sizeof(GnbSrsShmHdr)
GNB_SRS_SHM_TOTAL_SIZE = GNB_SRS_HDR_SIZE + GNB_SRS_CHAN_BYTES


class SrsReader:
    """Read the latest SRS channel estimate and compute channel-quality metrics."""

    def __init__(self, path: str = GNB_SRS_SHM, snr_db: float = 20.0):
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
            self.mm = mmap.mmap(self.fd, GNB_SRS_SHM_TOTAL_SIZE, mmap.MAP_SHARED, mmap.PROT_READ)
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
        if self.mm is None and not self.open():
            return None
        raw = self.mm[:GNB_SRS_SHM_TOTAL_SIZE]
        hdr = GnbSrsShmHdr.from_buffer_copy(raw)
        if hdr.magic != GNB_SRS_MAGIC or hdr.seq == 0 or hdr.seq == self.prev_seq:
            return None
        self.prev_seq = hdr.seq

        n_elems = hdr.num_rx_ant * hdr.num_ports * hdr.fft_size * hdr.n_srs_symbols
        dtype = np.dtype([("r", np.int16), ("i", np.int16)])
        arr = np.frombuffer(raw, dtype=dtype, count=n_elems, offset=GNB_SRS_HDR_SIZE)
        arr = arr.reshape(hdr.num_rx_ant, hdr.num_ports, hdr.n_srs_symbols, hdr.fft_size)
        h = arr["r"].astype(np.float64) + 1j * arr["i"].astype(np.float64)

        h_first_symbol = h[:, :, 0, :]
        metrics = CsiRsReader._channel_metrics(
            h_first_symbol,
            num_rx=hdr.num_rx_ant,
            num_ports=hdr.num_ports,
            n_rb_dl=hdr.n_rb,
            fft_size=hdr.fft_size,
            snr_db=self.snr_db,
        )
        metrics.update({
            "frame": hdr.frame,
            "slot": hdr.slot,
            "rnti": int(hdr.rnti),
            "n_rb": hdr.n_rb,
            "fft_size": hdr.fft_size,
            "scs": hdr.subcarrier_spacing,
            "n_symbols": hdr.n_srs_symbols,
            "snr": hdr.snr_db_x10 / 10.0,
            "channel": h,
        })
        return metrics
