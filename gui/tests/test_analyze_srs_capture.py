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
    read_capture,
)
from gui.inspect_srs_capture import parse_index_spec


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

    def test_parse_index_spec(self):
        self.assertEqual(parse_index_spec("0,3,5-7"), [0, 3, 5, 6, 7])
        with self.assertRaises(ValueError):
            parse_index_spec("7-5")

if __name__ == "__main__":
    unittest.main()
