"""Tests for the single-slot frequency-domain replay converter."""

from __future__ import annotations

import os
import tempfile
import unittest

import numpy as np

from gui.npy_to_rfsim_bin import (
    HEADER_SIZE,
    _resolve_cp,
    _to_fft_order,
    load_channel,
    parse_header,
    verify_bin,
    write_bin,
)


class NpyToRfsimBinTests(unittest.TestCase):
    def test_fft_order_remap_moves_saved_bin_to_physical_bin(self):
        saved = np.zeros((1, 1, 8), dtype=np.complex64)
        saved[0, 0, 0] = 1 + 2j
        result = _to_fft_order(saved, fft_size=16, offset=3)
        self.assertEqual(result.shape, (1, 1, 16))
        self.assertEqual(result[0, 0, 3], 1 + 2j)
        self.assertEqual(np.count_nonzero(result), 1)

    def test_write_and_verify_single_slot_header_and_payload(self):
        h = np.zeros((2, 1, 8), dtype=np.complex64)
        h[0, 0, 0] = 1.0
        h[1, 0, 0] = 0.5j
        with tempfile.TemporaryDirectory() as tmpdir:
            output = os.path.join(tmpdir, "channel.bin")
            write_bin(
                output,
                h,
                fft_size=8,
                symbols_per_slot=14,
                cp_length=2,
                cp_length0=3,
                n_rb=1,
                scs=30000,
                subcarrier_offset=0,
            )
            with open(output, "rb") as fp:
                info = parse_header(fp.read(HEADER_SIZE))
                payload = np.frombuffer(fp.read(), dtype="<c8")
            verified = verify_bin(output)

        self.assertEqual(info["num_slots"], 1)
        self.assertEqual(info["symbols_per_slot"], 14)
        self.assertEqual(info["cp_length"], 2)
        self.assertEqual(info["cp_length0"], 3)
        self.assertEqual(payload.size, 2 * 1 * 8)
        self.assertEqual(verified["nrx"], 2)

    def test_srs_4d_symbol_selection_and_transpose(self):
        h = np.zeros((2, 3, 2, 8), dtype=np.complex64)
        h[:, :, 1, 0] = 7
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "srs_test.npy")
            np.save(path, h)
            selected = load_channel(
                path, kind="srs", symbol=1, transpose=True
            )
        self.assertEqual(selected.shape, (3, 2, 8))
        np.testing.assert_array_equal(selected[:, :, 0], np.full((3, 2), 7))

    def test_cp_defaults_follow_oai_formula(self):
        cp, cp0 = _resolve_cp(
            fft_size=1024, scs=30000, cp_length=None, cp_length0=None
        )
        self.assertEqual(cp, 72)
        self.assertEqual(cp0, 88)


if __name__ == "__main__":
    unittest.main()
