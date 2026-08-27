#!/usr/bin/env python3
"""Readers for the NR gNB shared-memory DL/UL transmission measurements."""

from __future__ import annotations

import ctypes
import mmap
import os
from typing import Dict, Optional


GNB_DL_MEAS_SHM = "/dev/shm/gnb_meas_dl"
GNB_UL_MEAS_SHM = "/dev/shm/gnb_meas_ul"


class GnbDlShm(ctypes.Structure):
    """Packed layout of ``gnb_dl_meas_shm_t`` from ``gNB_shm.h``."""

    _pack_ = 1
    _fields_ = [
        ("seq",                ctypes.c_uint64),
        ("frame",              ctypes.c_uint32),
        ("slot",               ctypes.c_uint32),
        ("rnti",               ctypes.c_uint16),
        ("dlsch_received",     ctypes.c_uint32),
        ("dlsch_errors",       ctypes.c_uint32),
        ("bler_x1000",         ctypes.c_uint16),
        ("sinr_db_x10",        ctypes.c_int16),
        ("mcs",                ctypes.c_uint8),
        ("qam_mod_order",      ctypes.c_uint8),
        ("tbs",                ctypes.c_uint32),
        ("num_layers",         ctypes.c_uint8),
        ("num_rbs",            ctypes.c_uint16),
        ("num_symbols",        ctypes.c_uint16),
        ("rv",                 ctypes.c_uint8),
        ("new_data_indicator", ctypes.c_uint8),
        ("target_code_rate",   ctypes.c_uint16),
        ("cqi",                ctypes.c_uint8),
        ("ri",                 ctypes.c_uint8),
        ("pmi_x1",             ctypes.c_uint8),
        ("pmi_x2",             ctypes.c_uint8),
        ("n_rb_dl",            ctypes.c_uint16),
    ]


class GnbUlShm(ctypes.Structure):
    """Packed layout of ``gnb_ul_meas_shm_t`` from ``gNB_shm.h``."""

    _pack_ = 1
    _fields_ = [
        ("seq",                ctypes.c_uint64),
        ("frame",              ctypes.c_uint32),
        ("slot",               ctypes.c_uint32),
        ("rnti",               ctypes.c_uint16),
        ("ulsch_received",     ctypes.c_uint32),
        ("ulsch_errors",       ctypes.c_uint32),
        ("bler_x1000",         ctypes.c_uint16),
        ("sinr_db_x10",        ctypes.c_int16),
        ("mcs",                ctypes.c_uint8),
        ("qam_mod_order",      ctypes.c_uint8),
        ("tbs",                ctypes.c_uint32),
        ("num_layers",         ctypes.c_uint8),
        ("num_rbs",            ctypes.c_uint16),
        ("num_symbols",        ctypes.c_uint16),
        ("rv",                 ctypes.c_uint8),
        ("new_data_indicator", ctypes.c_uint8),
        ("target_code_rate",   ctypes.c_uint16),
        ("timing_advance",     ctypes.c_uint16),
        ("ul_cqi",             ctypes.c_uint8),
        ("tpmi",               ctypes.c_uint8),
        ("rssi",               ctypes.c_int16),
        ("n_rb_ul",            ctypes.c_uint16),
    ]


GNB_DL_MEAS_SIZE = ctypes.sizeof(GnbDlShm)
GNB_UL_MEAS_SIZE = ctypes.sizeof(GnbUlShm)


class GnbDlReader:
    """Read the latest DL transmission snapshot from ``/dev/shm/gnb_meas_dl``."""

    def __init__(self, path: str = GNB_DL_MEAS_SHM):
        self.path = path
        self.fd: Optional[int] = None
        self.mm: Optional[mmap.mmap] = None
        self.prev_seq: int = 0

    def open(self) -> bool:
        if not os.path.exists(self.path):
            return False
        try:
            self.fd = os.open(self.path, os.O_RDONLY)
            self.mm = mmap.mmap(self.fd, GNB_DL_MEAS_SIZE, mmap.MAP_SHARED, mmap.PROT_READ)
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
        m = GnbDlShm.from_buffer_copy(self.mm[:GNB_DL_MEAS_SIZE])
        if m.seq == 0 or m.seq == self.prev_seq:
            return None
        self.prev_seq = m.seq
        received = int(m.dlsch_received)
        errors = int(m.dlsch_errors)
        sinr_db_x10 = int(m.sinr_db_x10)
        return {
            "frame": m.frame,
            "slot": m.slot,
            "rnti": int(m.rnti),
            "bler": m.bler_x1000 / 10.0,
            "sinr": 0.0 if sinr_db_x10 == -32768 else sinr_db_x10 / 10.0,
            "mcs": m.mcs,
            "qm": m.qam_mod_order,
            "tbs": m.tbs,
            "layers": m.num_layers,
            "nprb": m.num_rbs,
            "nsymb": m.num_symbols,
            "rv": m.rv,
            "ndi": m.new_data_indicator,
            "target_code_rate": m.target_code_rate,
            "cqi": m.cqi,
            "ri": m.ri,
            "pmi_x1": m.pmi_x1,
            "pmi_x2": m.pmi_x2,
            "n_rb_dl": m.n_rb_dl,
            "received": received,
            "errors": errors,
        }


class GnbUlReader:
    """Read the latest UL transmission snapshot from ``/dev/shm/gnb_meas_ul``."""

    def __init__(self, path: str = GNB_UL_MEAS_SHM):
        self.path = path
        self.fd: Optional[int] = None
        self.mm: Optional[mmap.mmap] = None
        self.prev_seq: int = 0

    def open(self) -> bool:
        if not os.path.exists(self.path):
            return False
        try:
            self.fd = os.open(self.path, os.O_RDONLY)
            self.mm = mmap.mmap(self.fd, GNB_UL_MEAS_SIZE, mmap.MAP_SHARED, mmap.PROT_READ)
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
        m = GnbUlShm.from_buffer_copy(self.mm[:GNB_UL_MEAS_SIZE])
        if m.seq == 0 or m.seq == self.prev_seq:
            return None
        self.prev_seq = m.seq
        received = int(m.ulsch_received)
        errors = int(m.ulsch_errors)
        sinr_db_x10 = int(m.sinr_db_x10)
        return {
            "frame": m.frame,
            "slot": m.slot,
            "rnti": int(m.rnti),
            "bler": m.bler_x1000 / 10.0,
            "sinr": 0.0 if sinr_db_x10 == -32768 else sinr_db_x10 / 10.0,
            "mcs": m.mcs,
            "qm": m.qam_mod_order,
            "tbs": m.tbs,
            "layers": m.num_layers,
            "nprb": m.num_rbs,
            "nsymb": m.num_symbols,
            "rv": m.rv,
            "ndi": m.new_data_indicator,
            "target_code_rate": m.target_code_rate,
            "timing_advance": m.timing_advance,
            "ul_cqi": m.ul_cqi,
            "tpmi": m.tpmi,
            "rssi": m.rssi,
            "n_rb_ul": m.n_rb_ul,
            "received": received,
            "errors": errors,
        }
