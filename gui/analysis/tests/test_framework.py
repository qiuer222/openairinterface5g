"""Unit tests for the CSI throughput prediction framework."""

from __future__ import annotations

import os
import tempfile
import unittest

import numpy as np
import pandas as pd

from gui.analysis.capacity.shannon import shannon_capacity
from gui.analysis.capacity.svd import svd_capacities_from_eigenvalues
from gui.analysis.capacity.zf_mmse import (
    aggregate_zf_mmse_from_svd,
    zf_mmse_subcarrier_from_svd,
)
from gui.analysis.csi_parser import load_channel, valid_subcarrier_indices
from gui.analysis.data_loader import (
    MeasurementSet,
    discover_measurement_sets,
    load_measurement_frame,
)
from gui.analysis.main import aggregate_position_level


def _diagonal_channel() -> np.ndarray:
    h = np.zeros((2, 2, 4), dtype=complex)
    h[:, :, 1] = np.diag([4.0, 2.0])
    return h


class AnalysisFrameworkTests(unittest.TestCase):
    def test_zero_subcarriers_are_ignored(self):
        h = _diagonal_channel()
        valid = valid_subcarrier_indices(h)
        self.assertEqual(len(valid), 1)
        self.assertEqual(valid[0], 1)
        capacity = shannon_capacity(h, noise_power=1.0)
        expected = np.log2(1.0 + 16.0 / 2.0) + np.log2(1.0 + 4.0 / 2.0)
        self.assertTrue(np.isclose(capacity, expected))

    def test_frobenius_power_matches_eigenvalue_sum(self):
        h = _diagonal_channel()
        hk = h[:, :, 1]
        eigenvalues = np.linalg.eigvalsh(hk @ hk.conj().T)[::-1]
        frobenius = np.sum(np.abs(hk) ** 2)
        self.assertTrue(np.isclose(frobenius, np.sum(eigenvalues)))

    def test_svd_capacity_matches_closed_form(self):
        h = _diagonal_channel()
        hk = h[:, :, 1]
        eigenvalues = np.linalg.eigvalsh(hk @ hk.conj().T)[::-1]
        capacities = svd_capacities_from_eigenvalues(
            [eigenvalues], noise_power=1.0, max_streams=2
        )
        self.assertTrue(np.isclose(capacities[0], np.log2(1.0 + 16.0)))
        self.assertTrue(
            np.isclose(
                capacities[1],
                np.log2(1.0 + 16.0 / 2.0) + np.log2(1.0 + 4.0 / 2.0),
            )
        )

    def test_zf_trace_one_and_sinr_nonnegative(self):
        h = _diagonal_channel()
        hk = h[:, :, 1]
        u, s, vh = np.linalg.svd(hk, full_matrices=False)
        capacity, sinrs, trace = zf_mmse_subcarrier_from_svd(
            u, s, vh, 2, noise_power=1.0
        )
        self.assertTrue(np.isclose(trace, 1.0))
        self.assertTrue(np.all(sinrs >= -1e-9))
        self.assertTrue(np.isfinite(capacity))

        aggregate = aggregate_zf_mmse_from_svd(
            [(u, s, vh)], noise_power=1.0, max_streams=2
        )
        self.assertTrue(np.all(np.asarray(aggregate["sinr_matrix"]) >= -1e-9))
        self.assertLess(aggregate["max_trace_relative_error"], 1e-9)

    def test_4x4_channel_capacities_are_finite(self):
        h = np.zeros((4, 4, 8), dtype=complex)
        rng = np.random.default_rng(7)
        h[:, :, 3] = rng.normal(size=(4, 4)) + 1j * rng.normal(size=(4, 4))
        valid = valid_subcarrier_indices(h)
        self.assertEqual(len(valid), 1)
        eigenvalues = np.linalg.eigvalsh(h[:, :, 3] @ h[:, :, 3].conj().T)[::-1]
        capacities = svd_capacities_from_eigenvalues(
            [eigenvalues], noise_power=1.0, max_streams=4
        )
        self.assertEqual(len(capacities), 4)
        self.assertTrue(all(np.isfinite(c) for c in capacities))

    def test_4d_srs_channel_uses_first_symbol(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "channel_test.npy")
            arr = np.zeros((4, 4, 1, 3), dtype=complex)
            arr[:, :, 0, 2] = np.eye(4)
            np.save(path, arr)
            h = load_channel(path)
            self.assertEqual(h.shape, (4, 4, 3))
            self.assertEqual(
                np.count_nonzero(np.any(h != 0, axis=(0, 1))), 1
            )

    def test_timestamp_pairing_uses_gnb_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ue_csv = os.path.join(tmpdir, "gui_ue_log_20260807_100000.csv")
            gnb_csv = os.path.join(tmpdir, "gui_gnb_log_20260807_095959.csv")
            pd.DataFrame(
                {
                    "timestamp": ["20260807_100000_000000"],
                    "test_round": [1],
                    "throughput_mbps": [10.0],
                    "mcs": [1],
                    "layers": [1],
                    "rsrp_dBm": [-80.0],
                }
            ).to_csv(ue_csv, index=False)
            pd.DataFrame(
                {
                    "timestamp": ["20260807_095959_500000"],
                    "test_round": [1],
                    "throughput_mbps": [88.0],
                    "ul_mcs": [27],
                    "ul_layers": [2],
                }
            ).to_csv(gnb_csv, index=False)

            ms = MeasurementSet(
                name="test",
                direction="ul",
                ue_csv=ue_csv,
                ue_csi_dir="",
                gnb_csv=gnb_csv,
            )
            frame = load_measurement_frame(ms, "ul", pair_tolerance_ms=2000)
            self.assertEqual(frame.loc[0, "throughput_mbps"], 88.0)
            self.assertEqual(frame.loc[0, "mcs"], 27)
            self.assertEqual(frame.loc[0, "layers"], 2)

    def test_discovers_ue_and_gnb_in_same_round_folder(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            round_dir = os.path.join(tmpdir, "round1")
            ue_stem = "gui_ue_log_20260807_100000"
            ue_csv = os.path.join(round_dir, f"{ue_stem}.csv")
            gnb_csv = os.path.join(round_dir, "gui_gnb_log_20260807_095959.csv")
            csi_dir = os.path.join(round_dir, ue_stem)
            os.makedirs(csi_dir)
            open(ue_csv, "w").close()
            open(gnb_csv, "w").close()
            open(os.path.join(csi_dir, "channel_test.npy"), "w").close()

            sets = discover_measurement_sets(round_dir)

            self.assertEqual(len(sets), 1)
            self.assertEqual(sets[0].name, "round1")
            self.assertEqual(sets[0].ue_csv, ue_csv)
            self.assertEqual(sets[0].ue_csi_dir, csi_dir)
            self.assertEqual(sets[0].gnb_csv, gnb_csv)
            self.assertEqual(sets[0].direction, "")

    def test_top_50_position_aggregation(self):
        df = pd.DataFrame(
            {
                "set": ["round1"] * 4,
                "direction": ["ul"] * 4,
                "position_id": [1] * 4,
                "test_round": [1] * 4,
                "throughput_mbps": [10.0, 30.0, 20.0, 40.0],
                "actual_layers": [1, 2, 1, 2],
                "mcs": [10, 20, 15, 25],
                "rsrp_dBm": [-80, -82, -81, -83],
                "raw_channel_power": [1, 2, 3, 4],
            }
        )
        position = aggregate_position_level(df, top_ratio=0.5)
        self.assertEqual(len(position), 1)
        self.assertEqual(position.loc[0, "retained_samples"], 2)
        self.assertTrue(np.isclose(position.loc[0, "throughput_mbps"], 35.0))
        self.assertEqual(position.loc[0, "actual_layers"], 2)


if __name__ == "__main__":
    unittest.main()
