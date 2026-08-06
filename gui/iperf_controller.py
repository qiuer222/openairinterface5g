#!/usr/bin/env python3
"""iperf3 process control and real-time throughput parsing.

The controller runs iperf3 in a QThread, appends every stdout line to the
configured log file, and emits a parsed throughput sample for every interval
line such as ``[  5]  0.00-1.00 sec  5.50 MBytes  46.1 Mbits/sec``.
Final iperf3 summary lines such as ``0.00-30.00 sec ... sender`` are ignored.
"""

from __future__ import annotations

import math
import re
import shutil
import subprocess
from typing import List, Optional

from PyQt5.QtCore import QThread, pyqtSignal


_IPERF_INTERVAL_RE = re.compile(
    r"\[\s*\S+\]\s+"
    r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\s+sec\s+"
    r".*?(\d+(?:\.\d+)?)\s*([KMGTk]?)bits/sec"
)
_BIT_SUFFIX = {"": 1.0, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}


def parse_iperf3_line(line: str) -> Optional[float]:
    """Return bits/sec from a 1-second iperf3 interval line, or None."""
    m = _IPERF_INTERVAL_RE.search(line)
    if not m:
        return None
    start = float(m.group(1))
    end = float(m.group(2))
    if not math.isclose(end - start, 1.0, rel_tol=0.0, abs_tol=1e-9):
        return None
    try:
        return float(m.group(3)) * _BIT_SUFFIX[m.group(4)]
    except (ValueError, KeyError):
        return None


class IperfController(QThread):
    """Run one iperf3 process and emit parsed throughput samples."""

    throughput_updated = pyqtSignal(float)
    log_line = pyqtSignal(str)
    process_finished = pyqtSignal(str)
    process_error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.latest_bps: float = 0.0
        self._proc: Optional[subprocess.Popen] = None
        self._cmd: List[str] = []
        self._log_path = "iperf.log"

    def start_ul(self, bs_ip: str, port: int, duration: int, log_path: str) -> None:
        """Start UE→BS upload using iperf3 client mode."""
        self._log_path = log_path
        self._cmd = [
            "iperf3",
            "-c", bs_ip,
            "-p", str(port),
            "-t", str(duration),
            "-i", "1",
        ]
        self.start()

    def start_dl_server(self, port: int, log_path: str) -> None:
        """Start DL server mode on the UE; the BS must run the matching client."""
        self._log_path = log_path
        #self._cmd = ["iperf3", "-s", "-p", str(port), "-i", "1"]
        self._cmd = ["iperf3", "-B", "10.0.0."+str(port), "-c", "192.168.70.135", "-t", "30"]
        print("running iperf3 server with command:", " ".join(self._cmd))
        self.start()

    def start_dl_reverse(self, bs_ip: str, port: int, duration: int, log_path: str) -> None:
        """Start BS→UE download using iperf3 reverse client mode."""
        self._log_path = log_path
        self._cmd = [
            "iperf3",
            "-c", bs_ip,
            "-p", str(port),
            "-t", str(duration),
            "-i", "1",
            "-R",
        ]
        self.start()

    def start_ul_server(self, port: int, log_path: str) -> None:
        """Start gNB-side UL server for UE→gNB upload."""
        self._log_path = log_path
        #self._cmd = ["iperf3", "-s", "-p", str(port), "-i", "1"]
        self._cmd = ["sudo", "docker", "exec", "-it", "oai-ext-dn", "iperf3", "-s", "-p", str(port)]
        print("running iperf3 server with command:", " ".join(self._cmd))
        self.start()

    def start_dl_client(self, ue_ip: str, port: int, duration: int, log_path: str) -> None:
        """Start gNB-side DL client for gNB→UE download."""
        self._log_path = log_path
        self._cmd = [
            "iperf3",
            "-c", ue_ip,
            "-p", str(port),
            "-t", str(duration),
            "-i", "1",
        ]
        self.start()

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self.requestInterruption()
        self.wait(2000)

    def run(self) -> None:
        if not self._cmd:
            self.process_error.emit("No iperf command configured")
            return
        try:
            cmd = self._cmd
            # iperf3 uses full buffering when stdout is a pipe; stdbuf forces
            # line-buffered output so the GUI can plot while the test runs.
            if shutil.which("stdbuf"):
                cmd = ["stdbuf", "-oL", "-eL"] + cmd
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError:
            self.process_error.emit("iperf3 not found on PATH")
            return
        except OSError as exc:
            self.process_error.emit(str(exc))
            return

        with open(self._log_path, "a", encoding="utf-8") as log:
            for raw in self._proc.stdout:
                if self.isInterruptionRequested():
                    break
                log.write(raw)
                log.flush()
                line = raw.rstrip()
                self.log_line.emit(line)
                bps = parse_iperf3_line(line)
                if bps is not None:
                    self.latest_bps = bps
                    self.throughput_updated.emit(bps)

        self._proc.wait()
        code = self._proc.returncode
        self.process_finished.emit(f"iperf3 exited with code {code}")
