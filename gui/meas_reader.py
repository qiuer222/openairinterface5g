#!/usr/bin/env python3
"""Reader for the ``/dev/shm/meas_dl`` region written by OAI NR UE PHY."""

from __future__ import annotations

import ctypes
import mmap
import os
from typing import Dict, Optional


MEAS_DL_SHM = "/dev/shm/meas_dl"


class MeasDlShm(ctypes.Structure):
    """Packed layout of ``meas_dl_shm_t`` from ``ue_shm.h``."""

    _pack_ = 1
    _fields_ = [
        ("seq",               ctypes.c_uint64),
        ("frame",             ctypes.c_uint32),
        ("slot",              ctypes.c_uint32),
        ("mcs",               ctypes.c_uint8),
        ("qam_mod_order",     ctypes.c_uint8),
        ("tbs",               ctypes.c_uint32),
        ("num_layers",        ctypes.c_uint8),
        ("num_rbs",           ctypes.c_uint16),
        ("num_symbols",       ctypes.c_uint16),
        ("rv",                ctypes.c_uint8),
        ("new_data_indicator", ctypes.c_uint8),
        ("target_code_rate",  ctypes.c_uint16),
        ("bitrate_bps",       ctypes.c_uint32),
        ("dlsch_received",    ctypes.c_uint32),
        ("dlsch_errors",      ctypes.c_uint32),
        ("dlsch_fer",         ctypes.c_uint8),
        ("rsrp_dBm",          ctypes.c_int32),
        ("rssi_dBm",          ctypes.c_int16),
        ("wideband_sinr_dB",  ctypes.c_int16),
        ("n_rb_dl",           ctypes.c_uint16),
        ("subcarrier_spacing", ctypes.c_uint32),
        ("freq_offset",       ctypes.c_int32),
        ("nb_antennas_rx",    ctypes.c_uint8),
    ]


MEAS_DL_SIZE = ctypes.sizeof(MeasDlShm)


class MeasDlReader:
    """Read the latest DL measurement snapshot with seq-based freshness check."""

    def __init__(self, path: str = MEAS_DL_SHM):
        self.path = path
        self.fd: Optional[int] = None
        self.mm: Optional[mmap.mmap] = None
        self.prev_seq: int = 0

    def open(self) -> bool:
        if not os.path.exists(self.path):
            return False
        try:
            self.fd = os.open(self.path, os.O_RDONLY)
            self.mm = mmap.mmap(self.fd, MEAS_DL_SIZE, mmap.MAP_SHARED, mmap.PROT_READ)
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
        """Return a fresh measurement dict or None when no new data is available."""
        if self.mm is None and not self.open():
            return None
        raw = self.mm[:MEAS_DL_SIZE]
        m = MeasDlShm.from_buffer_copy(raw)
        if m.seq == 0 or m.seq == self.prev_seq:
            return None
        self.prev_seq = m.seq
        return {
            "frame": m.frame,
            "slot": m.slot,
            "bler": m.dlsch_fer,
            "rsrp": m.rsrp_dBm,
            "sinr": m.wideband_sinr_dB / 10.0,
            "mcs": m.mcs,
            "nprb": m.num_rbs,
            "qm": m.qam_mod_order,
            "tbs": m.tbs,
            "layers": m.num_layers,
            "nsymb": m.num_symbols,
            "rv": m.rv,
            "new_data_indicator": m.new_data_indicator,
            "target_code_rate": m.target_code_rate,
            "dlsch_received": m.dlsch_received,
            "dlsch_errors": m.dlsch_errors,
            "rssi": m.rssi_dBm,
            "freq_offset": m.freq_offset,
            "bitrate_bps": m.bitrate_bps,
            "n_rb_dl": m.n_rb_dl,
            "scs": m.subcarrier_spacing,
            "nb_antennas_rx": m.nb_antennas_rx,
        }
