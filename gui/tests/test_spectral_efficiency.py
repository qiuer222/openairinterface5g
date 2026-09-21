import unittest

from gui.spectral_efficiency import (
    application_spectral_efficiency,
    bandwidth_hz,
    scheduled_spectral_efficiency,
)


class TestSpectralEfficiency(unittest.TestCase):
    def test_bandwidth_from_prbs_and_scs(self):
        self.assertEqual(bandwidth_hz(106, 30), 38_160_000.0)

    def test_scheduled_spectral_efficiency(self):
        self.assertAlmostEqual(
            scheduled_spectral_efficiency(100_000, 106, 14),
            100_000 / (106 * 12 * 14),
        )

    def test_application_spectral_efficiency(self):
        self.assertAlmostEqual(
            application_spectral_efficiency(100e6, 106, 30),
            100e6 / 38_160_000.0,
        )

    def test_unavailable_inputs_return_none(self):
        self.assertIsNone(scheduled_spectral_efficiency(0, 106, 14))
        self.assertIsNone(scheduled_spectral_efficiency(100_000, 0, 14))
        self.assertIsNone(application_spectral_efficiency(None, 106, 30))
        self.assertIsNone(application_spectral_efficiency(100e6, 0, 30))
        self.assertIsNone(application_spectral_efficiency(100e6, 106, 0))


if __name__ == "__main__":
    unittest.main()
