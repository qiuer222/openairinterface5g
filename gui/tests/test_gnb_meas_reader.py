"""Tests for gNB UL shared-memory decoding."""

from __future__ import annotations

import ctypes
import os
import tempfile
import unittest

from gui.gnb_meas_reader import (
    GNB_RSSI_INVALID,
    GNB_TPMI_INVALID,
    GNB_UL_CQI_INVALID,
    GNB_UL_MEAS_SIZE,
    GnbUlReader,
    GnbUlShm,
)


def _write_ul_snapshot(path: str, snapshot: GnbUlShm) -> None:
    raw = ctypes.string_at(ctypes.byref(snapshot), GNB_UL_MEAS_SIZE)
    with open(path, "wb") as fp:
        fp.write(raw)


class GnbUlReaderTests(unittest.TestCase):
    def test_reads_ul_cqi_tpmi_rv_rssi_and_timing_advance(self):
        snapshot = GnbUlShm(
            seq=1,
            frame=42,
            slot=7,
            rnti=0x1234,
            sinr_db_x10=195,
            rv=2,
            new_data_indicator=1,
            target_code_rate=456,
            timing_advance=31,
            ul_cqi=125,
            tpmi=7,
            tpmi_valid=1,
            rssi=1280,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "gnb_meas_ul")
            _write_ul_snapshot(path, snapshot)
            result = GnbUlReader(path).read()

        self.assertIsNotNone(result)
        self.assertEqual(result["ul_cqi"], 125)
        self.assertEqual(result["tpmi"], 7)
        self.assertTrue(result["tpmi_valid"])
        self.assertEqual(result["sinr"], 19.5)
        self.assertEqual(result["rv"], 2)
        self.assertEqual(result["ndi"], 1)
        self.assertEqual(result["target_code_rate"], 456)
        self.assertEqual(result["timing_advance"], 31)
        self.assertEqual(result["rssi"], 0.0)
        self.assertEqual(result["rssi_fapi"], 1280)

    def test_maps_invalid_ul_measurements_to_none(self):
        snapshot = GnbUlShm(
            seq=1,
            timing_advance=0xFFFF,
            ul_cqi=GNB_UL_CQI_INVALID,
            tpmi=GNB_TPMI_INVALID,
            tpmi_valid=0,
            rssi=GNB_RSSI_INVALID,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "gnb_meas_ul")
            _write_ul_snapshot(path, snapshot)
            result = GnbUlReader(path).read()

        self.assertIsNotNone(result)
        self.assertIsNone(result["timing_advance"])
        self.assertIsNone(result["ul_cqi"])
        self.assertIsNone(result["tpmi"])
        self.assertFalse(result["tpmi_valid"])
        self.assertIsNone(result["rssi"])
        self.assertIsNone(result["rssi_fapi"])


if __name__ == "__main__":
    unittest.main()
