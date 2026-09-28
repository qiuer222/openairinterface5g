import json
import os
import struct
import tempfile
import unittest

import numpy as np

from gui.analyze_srs_capture import (
    CAPTURE_MAGIC,
    CAPTURE_VERSION,
    HEADER,
    head_metrics,
    read_capture,
)


class AnalyzeSrsCaptureTest(unittest.TestCase):
    def _write_capture(self, directory):
        os.makedirs(directory, exist_ok=True)
        manifest = {"format_version": 1, "role": "gnb", "run_id": "test"}
        with open(os.path.join(directory, "manifest.json"), "w", encoding="utf-8") as fp:
            json.dump(manifest, fp)

        values = np.asarray([10 + 20j, 20 + 10j, 15 + 15j, 5 + 25j], dtype=np.complex128)
        payload = np.column_stack((values.real, values.imag)).astype("<i2").tobytes()
        header = HEADER.pack(
            CAPTURE_MAGIC,
            CAPTURE_VERSION,
            4,
            len(payload),
            0,
            1,
            12,
            3,
            123,
            0x1234,
            0,
            0,
            0,
            0,
            1,
            values.size,
            4,
            4,
            0,
            0,
            30000,
            150,
            0,
            b"\0" * 9,
        )
        with open(os.path.join(directory, "capture.bin"), "wb") as fp:
            fp.write(header)
            fp.write(payload)

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_capture(tmpdir)
            manifest, records = read_capture(tmpdir)
            self.assertEqual(manifest["role"], "gnb")
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].kind_name, "srs_ls")
            np.testing.assert_allclose(
                records[0].data.reshape(-1),
                [10 + 20j, 20 + 10j, 15 + 15j, 5 + 25j],
            )

    def test_truncated_payload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_capture(tmpdir)
            path = os.path.join(tmpdir, "capture.bin")
            with open(path, "r+b") as fp:
                fp.truncate(HEADER.size + 7)
            with self.assertRaises(ValueError):
                read_capture(tmpdir)

    def test_head_metrics(self):
        values = np.ones(20, dtype=np.complex128)
        values[:2] = 100
        metrics = head_metrics(values, 0.1)
        self.assertEqual(metrics["head_subcarriers"], 2)
        self.assertGreater(metrics["head_to_middle_db"], 30)
        self.assertAlmostEqual(metrics["phase_step_max_rad"], 0.0)


if __name__ == "__main__":
    unittest.main()
