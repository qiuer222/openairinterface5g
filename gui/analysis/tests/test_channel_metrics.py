"""Tests for channel inspection/comparison metrics."""

from __future__ import annotations

import unittest

import numpy as np

from gui.channel_metrics import (
    channel_metrics,
    compare_channel_slots,
)


def _make_channel() -> np.ndarray:
    h = np.zeros((2, 2, 64), dtype=complex)
    rng = np.random.default_rng(0)
    k = np.arange(10, 50)
    for rx in range(2):
        for tx in range(2):
            h[rx, tx, k] = (1.0 if rx == tx else 0.2) * np.exp(
                1j * rng.uniform(-np.pi, np.pi, size=len(k))
            )
    return h


class ChannelMetricsTests(unittest.TestCase):
    def test_identical_slots_match_exactly(self):
        h = _make_channel()
        result = compare_channel_slots(h, h)
        for path in result["per_path"]:
            self.assertAlmostEqual(path["correlation"], 1.0, places=12)
            self.assertAlmostEqual(path["nmse"], 0.0, places=12)

    def test_complex_scale_is_invisible_after_best_fit(self):
        h = _make_channel()
        scaled = h * (1.7 + 2.1j)
        result = compare_channel_slots(h, scaled)
        for path in result["per_path"]:
            self.assertAlmostEqual(path["nmse"], 0.0, places=10)
        for singular in result["singular_value_comparison"]:
            self.assertAlmostEqual(singular["nmse"], 0.0, places=8)

    def test_metrics_report_active_subcarriers(self):
        summary = channel_metrics(_make_channel())
        self.assertEqual(summary["active_count"], 40)
        self.assertEqual(summary["nrx"], 2)
        self.assertEqual(summary["ntx"], 2)


if __name__ == "__main__":
    unittest.main()
