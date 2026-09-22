"""Tests for the single-snapshot channel analysis CLI."""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest

import numpy as np

from gui.analyze_channel import (
    analyze_channel,
    load_channel_npy,
    main,
)


class AnalyzeChannelTests(unittest.TestCase):
    def test_loads_3d_and_singleton_symbol_4d(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path_3d = os.path.join(tmpdir, "channel_3d.npy")
            path_4d = os.path.join(tmpdir, "srs_4d.npy")
            h = np.ones((2, 2, 16), dtype=np.complex128)
            np.save(path_3d, h)
            np.save(path_4d, h[:, :, None, :])
            np.testing.assert_array_equal(load_channel_npy(path_3d), h)
            np.testing.assert_array_equal(load_channel_npy(path_4d), h)

    def test_loads_structured_real_imaginary_array(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "srs_structured.npy")
            packed = np.zeros((1, 1, 8), dtype=[("r", "<i2"), ("i", "<i2")])
            packed["r"][0, 0] = np.arange(8)
            packed["i"][0, 0] = -np.arange(8)
            np.save(path, packed)
            result = load_channel_npy(path)
            np.testing.assert_array_equal(
                result[0, 0], np.arange(8) - 1j * np.arange(8)
            )

    def test_flat_channel_has_expected_shape_and_low_frequency_ripple(self):
        h = np.zeros((2, 2, 32), dtype=np.complex128)
        h[0, 0] = 1.0
        h[1, 1] = 0.5
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "channel_flat.npy")
            np.save(path, h)
            metrics, _ = analyze_channel(
                path,
                kind="csi",
                n_rb=1,
                scs_hz=30000.0,
                subcarrier_offset=0,
            )
        self.assertEqual(metrics["array"]["shape"], [2, 2, 32])
        self.assertEqual(metrics["array"]["active_subcarriers"], 32)
        self.assertLess(metrics["paths"][0]["ripple_std_db"], 1e-6)
        self.assertAlmostEqual(
            metrics["mimo"]["condition_number"]["mean"], 2.0, places=8
        )
        energy = metrics["subcarrier_energy"]
        self.assertAlmostEqual(energy["linear"]["mean"], 1.25, places=8)
        self.assertAlmostEqual(energy["linear"]["std"], 0.0, places=8)
        self.assertEqual(energy["peak_subcarrier"], 0)
        self.assertEqual(energy["minimum_subcarrier"], 0)

    def test_subcarrier_energy_tracks_frequency_selectivity(self):
        h = np.zeros((1, 1, 8), dtype=np.complex128)
        h[0, 0, 2:6] = np.array([1.0, 2.0, 0.5, 1.5])
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "channel_selective.npy")
            np.save(path, h)
            metrics, _ = analyze_channel(
                path,
                kind="csi",
                n_rb=1,
                subcarrier_offset=0,
            )
        energy = metrics["subcarrier_energy"]
        self.assertAlmostEqual(energy["linear"]["mean"], 1.875, places=8)
        self.assertEqual(energy["peak_subcarrier"], 3)
        self.assertEqual(energy["minimum_subcarrier"], 4)
        self.assertAlmostEqual(
            energy["peak_to_average_db"],
            10.0 * np.log10(4.0 / 1.875),
            places=8,
        )
        self.assertAlmostEqual(
            energy["peak_to_trough_db"],
            10.0 * np.log10(4.0 / 0.25),
            places=8,
        )

    def test_frequency_axis_uses_oai_offsets_and_optional_center(self):
        h = np.zeros((1, 1, 32), dtype=np.complex128)
        h[0, 0, 2:6] = 1.0
        with tempfile.TemporaryDirectory() as tmpdir:
            csi_path = os.path.join(tmpdir, "channel_frequency.npy")
            srs_path = os.path.join(tmpdir, "srs_frequency.npy")
            np.save(csi_path, h)
            np.save(srs_path, h)
            csi_metrics, csi_plot = analyze_channel(
                csi_path,
                kind="csi",
                n_rb=1,
                scs_hz=30000.0,
            )
            srs_metrics, srs_plot = analyze_channel(
                srs_path,
                kind="srs",
                n_rb=1,
                scs_hz=30000.0,
                center_frequency_hz=3.6e9,
            )

        np.testing.assert_allclose(
            csi_plot["frequencies_hz"],
            np.array([-4.0, -3.0, -2.0, -1.0]) * 30000.0,
        )
        self.assertIsNone(csi_metrics["assumptions"]["center_frequency_hz"])
        np.testing.assert_allclose(
            srs_plot["frequencies_hz"],
            np.array([-14.0, -13.0, -12.0, -11.0]) * 30000.0
            + 3.6e9,
        )
        self.assertEqual(
            srs_metrics["assumptions"]["center_frequency_hz"], 3.6e9
        )

    def test_two_tap_channel_recovers_sparse_delays(self):
        fft_size = 32
        k = np.arange(fft_size)
        h = np.zeros((1, 1, fft_size), dtype=np.complex128)
        h[0, 0] = 1.0 + 0.5 * np.exp(
            -2j * np.pi * k * 3 / fft_size
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "srs_two_tap.npy")
            np.save(path, h)
            metrics, _ = analyze_channel(
                path,
                kind="srs",
                n_rb=1,
                scs_hz=30000.0,
                subcarrier_offset=0,
                tap_len=8,
                max_active_taps=4,
                prune_db=-20,
            )
        sparse = metrics["time_domain"]["per_path"][0]["sparse"]
        self.assertIn(0, sparse["delays"])
        self.assertIn(3, sparse["delays"])
        self.assertLess(sparse["fit_nmse"], 1e-6)
        self.assertGreater(sparse["rms_delay_spread_ns"], 0.0)

    def test_empty_active_channel_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "channel_zero.npy")
            np.save(path, np.zeros((2, 2, 16), dtype=np.complex128))
            with self.assertRaisesRegex(ValueError, "no active subcarriers"):
                analyze_channel(path, kind="csi", n_rb=1)

    def test_cli_writes_text_json_and_figures(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "channel_cli.npy")
            output_dir = os.path.join(tmpdir, "analysis")
            h = np.ones((1, 1, 32), dtype=np.complex128)
            np.save(path, h)
            with contextlib.redirect_stdout(io.StringIO()):
                result = main(
                    [
                        path,
                        "--kind",
                        "csi",
                        "--n-rb",
                        "1",
                        "--subcarrier-offset",
                        "0",
                        "--center-frequency-hz",
                        "3600000000",
                        "--output-dir",
                        output_dir,
                    ]
                )
            self.assertEqual(result, 0)
            with open(
                os.path.join(output_dir, "channel_analysis.json"),
                encoding="utf-8",
            ) as fp:
                data = json.load(fp)
            self.assertEqual(data["array"]["active_subcarriers"], 32)
            self.assertEqual(
                data["assumptions"]["center_frequency_hz"], 3.6e9
            )
            self.assertTrue(
                os.path.isfile(
                    os.path.join(output_dir, "channel_analysis.txt")
                )
            )
            self.assertTrue(
                os.path.getsize(
                    os.path.join(
                        output_dir,
                        "figures",
                        "channel_magnitude.png",
                    )
                )
                > 0
            )
            self.assertTrue(
                os.path.getsize(
                    os.path.join(
                        output_dir,
                        "figures",
                        "channel_subcarrier_energy.png",
                    )
                )
                > 0
            )


if __name__ == "__main__":
    unittest.main()
