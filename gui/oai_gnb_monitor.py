#!/usr/bin/env python3
"""Main PyQt5 window for the OAI NR gNB performance monitor."""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QApplication,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gui.gnb_meas_reader import GnbDlReader, GnbUlReader
from gui.iperf_controller import IperfController
from gui.plot_manager import GnbPlotManager
from gui.srs_reader import SrsReader


DEFAULT_GNB_CONFIG = {
    "ue_ip": "192.168.70.135",
    "iperf_port": 5201,
    "iperf_time": 30,
    "log_file": "gui/gnb_iperf.log",
    "snr_db": 20.0,
}
REFRESH_MS = 500
GNB_GUI_DIR = os.path.dirname(os.path.abspath(__file__))
RESTART_EXIT_CODE = 77


def load_gnb_config(path: Optional[str] = None) -> Dict:
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "gnb_config.json")
    user: Dict = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            user = json.load(f)
    except Exception:
        pass
    cfg = dict(DEFAULT_GNB_CONFIG)
    cfg.update(user)
    return cfg


class GnbMainWindow(QMainWindow):
    def __init__(self, config: Dict):
        super().__init__()
        self.config = config
        self.setWindowTitle("OAI NR gNB - DL/UL/SRS Performance Monitor")
        self.resize(1440, 900)

        self.dl_reader = GnbDlReader()
        self.ul_reader = GnbUlReader()
        self.srs_reader = SrsReader(snr_db=config.get("snr_db", 20.0))
        self.iperf = IperfController(self)
        self.iperf.throughput_updated.connect(self._on_iperf_throughput)
        self.iperf.log_line.connect(self._on_iperf_log)
        self.iperf.process_finished.connect(self._on_iperf_finished)
        self.iperf.process_error.connect(self._on_iperf_error)

        self._log_path = self._resolve_log_path(config.get("log_file", DEFAULT_GNB_CONFIG["log_file"]))
        self._log_state: Optional[tuple[int, int]] = None
        self._iperf_bps: float = 0.0
        self._last_dl: Dict = {}
        self._last_ul: Dict = {}
        self._last_srs: Dict = {}
        self._t0 = time.monotonic()
        self._csv_fd: object = None
        self._csv_writer = None
        self._csv_path: Optional[str] = None
        self._srs_channel_dir: Optional[str] = None
        self._csv_start_ts = ""
        self._test_round = 0
        self._log_based_test_round = False
        self._last_log_change_time: Optional[float] = None

        self._clear_log_file()
        self._build_ui()
        self._open_csv()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(REFRESH_MS)

    def _build_ui(self) -> None:
        central = QWidget()
        root = QHBoxLayout(central)

        left = QVBoxLayout()
        control = QGroupBox("iperf3 Control")
        form = QFormLayout(control)
        self.ue_ip_edit = QLineEdit(self.config.get("ue_ip", ""))
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(int(self.config.get("iperf_port", 5201)))
        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(1, 86400)
        self.duration_spin.setValue(int(self.config.get("iperf_time", 30)))
        form.addRow("UE IP", self.ue_ip_edit)
        form.addRow("Port", self.port_spin)
        form.addRow("Duration (s)", self.duration_spin)

        buttons = QHBoxLayout()
        self.ul_button = QPushButton("UL")
        self.dl_button = QPushButton("DL")
        self.stop_button = QPushButton("Stop")
        self.restart_button = QPushButton("Restart")
        self.restart_button.setToolTip(
            "Close and reopen the GUI to reattach shared memory"
        )
        self.ul_button.clicked.connect(self._start_ul)
        self.dl_button.clicked.connect(self._start_dl)
        self.stop_button.clicked.connect(self._stop_iperf)
        self.restart_button.clicked.connect(self._restart_gui)
        buttons.addWidget(self.ul_button)
        buttons.addWidget(self.dl_button)
        buttons.addWidget(self.stop_button)
        buttons.addWidget(self.restart_button)
        form.addRow(buttons)
        self.throughput_label = QLabel("Throughput: --")
        left.addWidget(self.throughput_label)
        left.addWidget(control)

        dl_group = QGroupBox("DL Measurements")
        dl_layout = QVBoxLayout(dl_group)
        self.dl_label = QLabel("No DL HARQ feedback yet.")
        self.dl_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.dl_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        dl_layout.addWidget(self.dl_label)
        left.addWidget(dl_group)

        ul_group = QGroupBox("UL Measurements")
        ul_layout = QVBoxLayout(ul_group)
        self.ul_label = QLabel("No UL PUSCH decode yet.")
        self.ul_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.ul_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        ul_layout.addWidget(self.ul_label)
        left.addWidget(ul_group)

        srs_group = QGroupBox("SRS Channel")
        srs_layout = QVBoxLayout(srs_group)
        self.srs_label = QLabel("No SRS estimate yet.")
        self.srs_label.setWordWrap(True)
        self.srs_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        srs_layout.addWidget(self.srs_label)
        left.addWidget(srs_group)

        self.status_label = QLabel("Ready")
        left.addWidget(self.status_label)

        self.plot_manager = GnbPlotManager()

        root.addLayout(left, 3)
        root.addWidget(self.plot_manager, 7)
        self.setCentralWidget(central)

    def _start_ul(self) -> None:
        self._log_based_test_round = True
        cfg = self._ui_config()
        self.status_label.setText(
            f"UL server: iperf3 -s -p {cfg['port']} (UE runs iperf3 client)"
        )
        self.iperf.start_ul_server(cfg["port"], cfg["log_file"])

    def _start_dl(self) -> None:
        self._test_round += 1
        self._log_based_test_round = False
        cfg = self._ui_config()
        self.status_label.setText(
            f"DL client: iperf3 -c {cfg['ue_ip']} -t {cfg['duration']} "
            "(UE runs iperf3 server)"
        )
        self.iperf.start_dl_client(cfg["ue_ip"], cfg["port"], cfg["duration"], cfg["log_file"])

    def _stop_iperf(self) -> None:
        self.iperf.stop()
        self.status_label.setText("iperf3 stopped")

    def _restart_gui(self) -> None:
        self._stop_resources()
        app = QApplication.instance()
        if app is not None:
            app.exit(RESTART_EXIT_CODE)

    def _stop_resources(self) -> None:
        self.timer.stop()
        self.iperf.stop()
        self._close_csv()
        self.dl_reader.close()
        self.ul_reader.close()
        self.srs_reader.close()

    def _ui_config(self) -> Dict:
        return {
            "ue_ip": self.ue_ip_edit.text().strip(),
            "port": self.port_spin.value(),
            "duration": self.duration_spin.value(),
            "log_file": self._log_path,
        }

    def _refresh(self) -> None:
        self.throughput_label.setText(
            f"Throughput: {self._iperf_bps / 1e6:.1f} Mbps"
        )

        dl = self.dl_reader.read()
        if dl is not None:
            self._last_dl = dl
            self._update_dl_text(dl)

        ul = self.ul_reader.read()
        if ul is not None:
            self._last_ul = ul
            self._update_ul_text(ul)

        srs = self.srs_reader.read()
        if srs is not None:
            self._last_srs = srs
            self._update_srs_text(srs)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        log_changed = self._iperf_log_changed()
        if log_changed:
            if self._log_based_test_round:
                now = time.monotonic()
                if self._last_log_change_time is None or now - self._last_log_change_time > 1.0:
                    self._test_round += 1
                self._last_log_change_time = now
            self._save_srs(self._last_srs.get("channel"), stamp)
            self._write_csv(stamp)

        t = time.monotonic() - self._t0
        values: Dict[str, float] = {"throughput": self._iperf_bps / 1e6}
        values.update({
            "dl_sinr": float(self._last_dl.get("sinr", 0)),
            "dl_bler": float(self._last_dl.get("bler", 0)),
            "dl_mcs": float(self._last_dl.get("mcs", 0)),
            "dl_nprb": float(self._last_dl.get("nprb", 0)),
            "dl_tbs": float(self._last_dl.get("tbs", 0)),
        })
        values.update({
            "ul_sinr": float(self._last_ul.get("sinr", 0)),
            "ul_bler": float(self._last_ul.get("bler", 0)),
            "ul_mcs": float(self._last_ul.get("mcs", 0)),
            "ul_nprb": float(self._last_ul.get("nprb", 0)),
            "ul_tbs": float(self._last_ul.get("tbs", 0)),
        })
        values.update({
            "srs_capacity": float(self._last_srs.get("capacity", 0)),
            "srs_rank": float(self._last_srs.get("rank", 0)),
            "srs_condition": float(self._last_srs.get("condition_number", 0)),
            "srs_snr": float(self._last_srs.get("snr", 0)),
        })

        self.plot_manager.throughput.add_sample(t, values)
        self.plot_manager.dl.add_sample(t, values)
        self.plot_manager.ul.add_sample(t, values)
        self.plot_manager.srs.add_sample(t, values)

    def _update_dl_text(self, m: Dict) -> None:
        lines = [
            f"Frame: {m.get('frame', 0)}  Slot: {m.get('slot', 0)}  "
            f"RNTI: {int(m.get('rnti', 0)):#06x}",
            f"BLER: {m.get('bler', 0):.2f} %",
            f"SINR: {m.get('sinr', 0):.1f} dB  CQI: {m.get('cqi', 0)}  "
            f"RI/Layers: {m.get('ri', 0)}",
            f"MCS: {m.get('mcs', 0)}  Qm: {m.get('qm', 0)}  NPRB: {m.get('nprb', 0)}",
            f"Layers: {m.get('layers', 0)}  Symbols: {m.get('nsymb', 0)}  "
            f"TBS: {m.get('tbs', 0)}",
            f"RV: {m.get('rv', 0)}  NDI: {m.get('ndi', 0)}  "
            f"PMI: ({m.get('pmi_x1', 0)},{m.get('pmi_x2', 0)})",
        ]
        self.dl_label.setText("\n".join(lines))

    def _update_ul_text(self, m: Dict) -> None:
        lines = [
            f"Frame: {m.get('frame', 0)}  Slot: {m.get('slot', 0)}  "
            f"RNTI: {int(m.get('rnti', 0)):#06x}",
            f"BLER: {m.get('bler', 0):.2f} %",
            f"SINR: {m.get('sinr', 0):.1f} dB  TA: {m.get('timing_advance', 0)}",
            f"MCS: {m.get('mcs', 0)}  Qm: {m.get('qm', 0)}  NPRB: {m.get('nprb', 0)}",
            f"Layers: {m.get('layers', 0)}  Symbols: {m.get('nsymb', 0)}  "
            f"TBS: {m.get('tbs', 0)}",
            f"RV: {m.get('rv', 0)}  NDI: {m.get('ndi', 0)}  "
            f"UL CQI: {m.get('ul_cqi', 0)}  RSSI: {m.get('rssi', 0)}",
        ]
        self.ul_label.setText("\n".join(lines))

    def _update_srs_text(self, m: Dict) -> None:
        self.srs_label.setText(
            f"SRS RNTI {int(m.get('rnti', 0)):#06x} frame {m.get('frame', 0)}."
            f"{m.get('slot', 0)} | {m.get('n_rb', 0)} PRB, fft {m.get('fft_size', 0)}, "
            f"{m.get('n_symbols', 0)} symbols | SNR {m.get('snr', 0):.1f} dB | "
            f"capacity {m.get('capacity', 0):.2f}, rank {m.get('rank', 0)}, "
            f"cond {m.get('condition_number', 0):.1f}"
        )

    def _on_iperf_throughput(self, bps: float) -> None:
        self._iperf_bps = bps

    def _on_iperf_log(self, line: str) -> None:
        pass

    def _on_iperf_finished(self, msg: str) -> None:
        self.status_label.setText(msg)

    def _on_iperf_error(self, msg: str) -> None:
        self.status_label.setText(f"iperf error: {msg}")

    def closeEvent(self, event) -> None:
        self._stop_resources()
        self._ask_archive_recordings()
        super().closeEvent(event)

    @staticmethod
    def _restart_command() -> List[str]:
        return [sys.executable, os.path.abspath(__file__), *sys.argv[1:]]

    @staticmethod
    def _resolve_log_path(path: str) -> str:
        if not path:
            return os.path.join(GNB_GUI_DIR, "gnb_iperf.log")
        if os.path.isabs(path):
            return path
        return os.path.abspath(os.path.join(os.path.dirname(GNB_GUI_DIR), path))

    def _iperf_log_changed(self) -> bool:
        try:
            st = os.stat(self._log_path)
        except FileNotFoundError:
            self._log_state = None
            return False
        state = (st.st_size, st.st_mtime_ns)
        if self._log_state is None or state != self._log_state:
            self._log_state = state
            return True
        return False

    def _reset_log_state(self) -> None:
        try:
            st = os.stat(self._log_path)
            self._log_state = (st.st_size, st.st_mtime_ns)
        except FileNotFoundError:
            self._log_state = None

    # -- CSV / SRS channel recording --

    def _open_csv(self) -> None:
        self._close_csv()
        ts = time.strftime("%Y%m%d_%H%M%S")
        self._csv_start_ts = ts
        record_dir = os.path.join(GNB_GUI_DIR, "record")
        os.makedirs(record_dir, exist_ok=True)
        csv_base = f"gui_gnb_log_{ts}"
        self._csv_path = os.path.join(record_dir, f"{csv_base}.csv")
        self._srs_channel_dir = os.path.join(record_dir, csv_base)
        os.makedirs(self._srs_channel_dir, exist_ok=True)
        self._csv_fd = open(self._csv_path, "w", newline="", encoding="utf-8")
        self._csv_writer = csv.writer(self._csv_fd)
        self._reset_log_state()
        self._csv_writer.writerow([
            "timestamp", "test_round", "throughput_mbps",
            "dl_frame", "dl_slot", "dl_rnti", "dl_bler", "dl_sinr",
            "dl_mcs", "dl_nprb", "dl_layers", "dl_qm", "dl_tbs", "dl_cqi", "dl_ri",
            "ul_frame", "ul_slot", "ul_rnti", "ul_bler", "ul_sinr",
            "ul_mcs", "ul_nprb", "ul_layers", "ul_qm", "ul_tbs",
            "ul_timing_advance", "ul_cqi",
            "srs_capacity", "srs_rank", "srs_condition", "srs_snr",
        ])
        self._csv_fd.flush()
        self.status_label.setText(f"logging to {self._csv_path}")

    def _write_csv(self, stamp: Optional[str] = None) -> None:
        if self._csv_writer is None:
            return
        if stamp is None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        dl = self._last_dl
        ul = self._last_ul
        srs = self._last_srs
        self._csv_writer.writerow([
            stamp,
            self._test_round,
            self._iperf_bps / 1e6,
            dl.get("frame", 0), dl.get("slot", 0), dl.get("rnti", 0),
            dl.get("bler", 0), dl.get("sinr", 0),
            dl.get("mcs", 0), dl.get("nprb", 0), dl.get("layers", 0),
            dl.get("qm", 0), dl.get("tbs", 0), dl.get("cqi", 0), dl.get("ri", 0),
            ul.get("frame", 0), ul.get("slot", 0), ul.get("rnti", 0),
            ul.get("bler", 0), ul.get("sinr", 0),
            ul.get("mcs", 0), ul.get("nprb", 0), ul.get("layers", 0),
            ul.get("qm", 0), ul.get("tbs", 0),
            ul.get("timing_advance", 0), ul.get("ul_cqi", 0),
            srs.get("capacity", 0), srs.get("rank", 0),
            srs.get("condition_number", 0), srs.get("snr", 0),
        ])
        self._csv_fd.flush()

    def _save_srs(self, channel, stamp: Optional[str] = None) -> None:
        if channel is None or self._srs_channel_dir is None:
            return
        if stamp is None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        np.save(os.path.join(self._srs_channel_dir, f"srs_{stamp}.npy"), channel)

    def _close_csv(self) -> None:
        if self._csv_fd is not None:
            self._csv_fd.close()
        self._csv_fd = None
        self._csv_writer = None

    def _clear_log_file(self) -> None:
        try:
            log_dir = os.path.dirname(self._log_path)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)
            open(self._log_path, "w", encoding="utf-8").close()
        except OSError:
            pass

    def _ask_archive_recordings(self) -> None:
        if not self._csv_path or not os.path.exists(self._csv_path):
            return
        default_name = self._csv_start_ts or time.strftime("%Y%m%d_%H%M%S")
        text, ok = QInputDialog.getText(
            self,
            "Save recorded data?",
            "Save CSV, SRS channel data and iperf log as folder name:",
            text=default_name,
        )
        if not ok:
            return

        record_dir = os.path.join(GNB_GUI_DIR, "record")
        folder_name = os.path.basename(text.strip())
        dest = os.path.join(record_dir, folder_name)
        while not folder_name or os.path.exists(dest):
            QMessageBox.warning(
                self,
                "Folder exists",
                f"{dest} already exists. Choose another name.",
            )
            text, ok = QInputDialog.getText(
                self,
                "Save recorded data?",
                "Save CSV, SRS channel data and iperf log as folder name:",
                text=default_name,
            )
            if not ok:
                return
            folder_name = os.path.basename(text.strip())
            dest = os.path.join(record_dir, folder_name)

        os.makedirs(dest, exist_ok=True)
        shutil.move(self._csv_path, os.path.join(dest, os.path.basename(self._csv_path)))
        if self._srs_channel_dir and os.path.isdir(self._srs_channel_dir):
            for name in os.listdir(self._srs_channel_dir):
                shutil.move(
                    os.path.join(self._srs_channel_dir, name),
                    os.path.join(dest, name),
                )
            os.rmdir(self._srs_channel_dir)
        if os.path.exists(self._log_path):
            shutil.move(self._log_path, os.path.join(dest, os.path.basename(self._log_path)))
        self.status_label.setText(f"recordings saved to {dest}")


def main() -> None:
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" and not os.environ.get(
        "QT_QPA_PLATFORM"
    ):
        os.environ["QT_QPA_PLATFORM"] = "wayland"
    app = QApplication(sys.argv)
    config = load_gnb_config()
    win = GnbMainWindow(config)
    win.show()
    exit_code = app.exec_()
    if exit_code == RESTART_EXIT_CODE:
        try:
            subprocess.Popen(GnbMainWindow._restart_command())
        except OSError as exc:
            print(f"failed to restart GUI: {exc}", file=sys.stderr)
            exit_code = 1
        else:
            exit_code = 0
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
