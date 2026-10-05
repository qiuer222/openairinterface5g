"""Unit tests for the CSI throughput prediction framework."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

import numpy as np
import pandas as pd

from gui.analysis.capacity.shannon import (
    shannon_capacity,
    shannon_stream_capacities_from_eigenvalue_matrix,
)
from gui.analysis.capacity.svd import svd_capacities_from_eigenvalues
from gui.analysis.csi_parser import load_channel, valid_subcarrier_indices
from gui.analysis.data_loader import (
    MeasurementSet,
    discover_measurement_sets,
    load_measurement_frame,
)
from gui.analysis.main import (
    _load_channel_map,
    _match_channel,
    aggregate_position_level,
    process_measurements,
)
from gui.analysis.normalization import raw_channel_power
from gui.analysis.tests.generate_mock_ul_3rounds import generate


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

    def test_snr_normalization_scales_mean_power_to_target(self):
        from gui.analysis.normalization import normalize_channel

        h = _diagonal_channel()
        snr_db = 20.0
        normalized = normalize_channel(h, noise_power=1.0, snr_db=snr_db)
        target_power = 10.0 ** (snr_db / 10.0)
        self.assertTrue(
            np.isclose(
                raw_channel_power(normalized),
                target_power,
            )
        )

    def test_no_snr_normalization_applies_c16_scale(self):
        from gui.analysis.normalization import normalize_channel

        h = _diagonal_channel()
        normalized = normalize_channel(h, noise_power=1.0)
        self.assertTrue(
            np.isclose(raw_channel_power(normalized), raw_channel_power(h) / 32768.0**2)
        )
        self.assertEqual(normalized.dtype, np.complex128)

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

    def test_selected_stream_capacity_uses_shannon_power_normalization(self):
        eigenvalues = np.array([[16.0, 4.0]])
        capacities = shannon_stream_capacities_from_eigenvalue_matrix(
            eigenvalues,
            noise_power=1.0,
            tx_count=2,
            max_streams=2,
        )
        expected_k1 = np.log2(1.0 + 16.0 / 2.0)
        expected_k2 = expected_k1 + np.log2(1.0 + 4.0 / 2.0)
        self.assertTrue(np.isclose(capacities[0], expected_k1))
        self.assertTrue(np.isclose(capacities[1], expected_k2))

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

    def test_mock_ul_three_rounds_uses_srs_and_preserves_new_fields(self):
        fixture = tempfile.mkdtemp(prefix="oai_ul_3rounds_")
        self.addCleanup(shutil.rmtree, fixture, True)
        generate(fixture)
        sets = discover_measurement_sets(fixture)
        self.assertEqual(len(sets), 1)
        measurement = sets[0]
        self.assertIsNotNone(measurement.gnb_srs_dir)

        frame = load_measurement_frame(measurement, "ul")
        srs_map = _load_channel_map(
            measurement.gnb_srs_dir, channel_kind="srs"
        )
        csi_map = _load_channel_map(
            measurement.ue_csi_dir, channel_kind="csi"
        )
        self.assertEqual(len(srs_map), 9)
        self.assertEqual(len(csi_map), 9)

        frame, warnings = _match_channel(
            frame,
            srs_map,
            tolerance_ms=200.0,
            channel_kind="srs",
        )
        self.assertFalse(warnings)
        self.assertEqual(set(frame["test_round"]), {1, 2, 3})
        self.assertEqual(set(frame["channel_kind"]), {"srs"})
        self.assertTrue(frame["channel_file"].str.contains("/srs_").all())
        self.assertTrue(frame["ue_rsrp_dBm"].notna().all())

        second, validation = process_measurements(
            frame,
            noise_power=1.0,
            max_streams=4,
            snr_db=20.0,
        )
        self.assertEqual(len(second), 9)
        self.assertIn("ul_tpmi", second.columns)
        self.assertIn("ul_rssi_dbfs", second.columns)
        self.assertIn("selected_stream_capacity", second.columns)
        self.assertTrue(second["selected_stream_capacity"].notna().all())
        self.assertFalse(
            any(column.startswith("zf_") for column in second.columns)
        )
        self.assertTrue(validation["validated_subcarriers"].gt(0).all())

        position = aggregate_position_level(second, top_ratio=0.5)
        self.assertEqual(set(position["test_round"]), {1, 2, 3})
        self.assertEqual(set(position["channel_kind"]), {"srs"})

        dl_frame = load_measurement_frame(measurement, "dl")
        dl_frame, dl_warnings = _match_channel(
            dl_frame,
            csi_map,
            tolerance_ms=200.0,
            channel_kind="csi",
        )
        self.assertFalse(dl_warnings)
        self.assertEqual(set(dl_frame["channel_kind"]), {"csi"})
        self.assertTrue(dl_frame["channel_file"].str.contains("/channel_").all())
        self.assertEqual(dl_frame.loc[0, "throughput_mbps"], 9.0)
        self.assertEqual(dl_frame.loc[0, "gnb_throughput_mbps"], 10.0)

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
