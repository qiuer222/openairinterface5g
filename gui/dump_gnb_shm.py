#!/usr/bin/env python3
"""Dump the current gNB shared-memory DL/UL/SRS snapshots for debugging."""

from __future__ import annotations

import ctypes
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gui.gnb_meas_reader import GNB_DL_MEAS_SHM, GNB_UL_MEAS_SHM, GnbDlShm, GnbUlShm
from gui.srs_reader import GNB_SRS_SHM, GNB_SRS_SHM_TOTAL_SIZE, GnbSrsShmHdr


def _read_struct(path: str, struct_type, expected_size: int):
    print(f"\n== {path} ==")
    if not os.path.exists(path):
        print("missing")
        return None
    size = os.path.getsize(path)
    print(f"file size {size} (expected {expected_size})")
    if size < expected_size:
        print("ERROR: file is smaller than the reader expects; rebuild/restart gNB")
        return None
    with open(path, "rb") as f:
        raw = f.read(expected_size)
    return struct_type.from_buffer_copy(raw)


def main() -> None:
    dl = _read_struct(GNB_DL_MEAS_SHM, GnbDlShm, ctypes.sizeof(GnbDlShm))
    if dl is not None:
        print(
            f"seq={dl.seq} frame={dl.frame} slot={dl.slot} rnti=0x{dl.rnti:04x} "
            f"received={dl.dlsch_received} errors={dl.dlsch_errors} "
            f"bler_x1000={dl.bler_x1000} sinr_db_x10={dl.sinr_db_x10} "
            f"mcs={dl.mcs} qm={dl.qam_mod_order} nprb={dl.num_rbs} "
            f"tbs={dl.tbs} cqi={dl.cqi} ri={dl.ri} pmi=({dl.pmi_x1},{dl.pmi_x2})"
        )

    ul = _read_struct(GNB_UL_MEAS_SHM, GnbUlShm, ctypes.sizeof(GnbUlShm))
    if ul is not None:
        print(
            f"seq={ul.seq} frame={ul.frame} slot={ul.slot} rnti=0x{ul.rnti:04x} "
            f"received={ul.ulsch_received} errors={ul.ulsch_errors} "
            f"bler_x1000={ul.bler_x1000} sinr_db_x10={ul.sinr_db_x10} "
            f"mcs={ul.mcs} qm={ul.qam_mod_order} nprb={ul.num_rbs} "
            f"tbs={ul.tbs} ta={ul.timing_advance} ul_cqi={ul.ul_cqi}"
        )

    srs = _read_struct(GNB_SRS_SHM, GnbSrsShmHdr, GNB_SRS_SHM_TOTAL_SIZE)
    if srs is not None:
        print(
            f"magic=0x{srs.magic:08x} seq={srs.seq} frame={srs.frame} "
            f"slot={srs.slot} rnti=0x{srs.rnti:04x} "
            f"ants={srs.num_rx_ant} ports={srs.num_ports} "
            f"fft={srs.fft_size} n_rb={srs.n_rb} snr_db_x10={srs.snr_db_x10}"
        )


if __name__ == "__main__":
    main()
