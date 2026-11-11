"""Tests for the UL actual-precoder MMSE capped-MI metric."""

from __future__ import annotations

import unittest

import numpy as np

from gui.analysis.capacity.mmse import mmse_capped_mi, ul_precoder


class AnalysisMmseTests(unittest.TestCase):
    def test_ul_precoder_dimensions(self):
        for ports, layers, count in (
            (2, 1, 6),
            (2, 2, 3),
            (4, 1, 28),
            (4, 2, 22),
            (4, 3, 7),
            (4, 4, 5),
        ):
            for tpmi in range(count):
                self.assertEqual(
                    ul_precoder(tpmi, layers, ports).shape,
                    (ports, layers),
                )

    def test_siso_cap_and_qm_limit(self):
        channel = np.full((1, 2, 2), 0.01, dtype=np.complex128)
        result = mmse_capped_mi(
            channel,
            np.array([0, 1]),
            ul_precoder(0, 1, 2),
            modulation_order=6,
            noise_power=1e-8,
            sinr_cap_db=30.0,
        )
        self.assertTrue(np.allclose(result["post_sinr_db"], 30.0))
        self.assertAlmostEqual(result["se"], 6.0)

    def test_diagonal_two_layer_mimo(self):
        channel = np.zeros((2, 2, 1), dtype=np.complex128)
        channel[0, 0, 0] = 1.0
        channel[1, 1, 0] = 1.0
        result = mmse_capped_mi(
            channel,
            np.array([0]),
            ul_precoder(0, 2, 2),
            modulation_order=2,
            noise_power=1e-8,
            sinr_cap_db=30.0,
        )
        self.assertTrue(np.allclose(result["post_sinr_db"], 30.0))
        self.assertAlmostEqual(result["se"], 4.0)

    def test_invalid_precoder_inputs(self):
        channel = np.zeros((1, 2, 1), dtype=np.complex128)
        with self.assertRaises(ValueError):
            ul_precoder(6, 1, 2)
        with self.assertRaises(ValueError):
            mmse_capped_mi(
                channel,
                np.array([0]),
                ul_precoder(0, 1, 2),
                modulation_order=3,
                noise_power=1.0,
            )


if __name__ == "__main__":
    unittest.main()
