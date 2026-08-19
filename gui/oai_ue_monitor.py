#!/usr/bin/env python3
"""Main PyQt5 window for the OAI NR UE performance monitor."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import csv
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gui.csi_reader import CsiRsReader
from gui.iperf_controller import IperfController
from gui.meas_reader import MeasDlReader
from gui.plot_manager import AntennaRsrpPlot, PlotManager


DEFAULT_CONFIG = {
    "bs_ip": "192.168.1.100",
    "iperf_port": 5201,
    "iperf_time": 30,
    "log_file": "gui/iperf.log",
    "dl_reverse_client": False,
    "snr_db": 20.0,
}
REFRESH_MS = 500
GUI_DIR = os.path.dirname(os.path.abspath(__file__))
RESTART_EXIT_CODE = 77


def load_config(path: Optional[str] = None) -> Dict:
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    user: Dict = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            user = json.load(f)
    except Exception:
        pass
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(user)
    return cfg


class MainWindow(QMainWindow):
    def __init__(self, config: Dict):
        super().__init__()
        self.config = config
        self.setWindowTitle("OAI NR UE - Downlink Performance Monitor")
        self.resize(1280, 800)

        self.meas_reader = MeasDlReader()
        self.csi_reader = CsiRsReader(snr_db=config.get("snr_db", 20.0))
        self.iperf = IperfController(self)
        self.iperf.throughput_updated.connect(self._on_iperf_throughput)
        self.iperf.log_line.connect(self._on_iperf_log)
        self.iperf.process_finished.connect(self._on_iperf_finished)
        self.iperf.process_error.connect(self._on_iperf_error)

        self._log_path = self._resolve_log_path(
            config.get("log_file", DEFAULT_CONFIG["log_file"])
        )
        self._log_state: Optional[tuple[int, int]] = None
        self._iperf_bps: float = 0.0
        self._last_meas: Dict = {}
        self._last_csi: Dict = {}
        self._t0 = time.monotonic()
        self._csv_fd: object = None
        self._csv_writer = None
        self._csv_path: Optional[str] = None
        self._channel_dir: Optional[str] = None
        self._csv_start_ts = ""
        self._test_round = 0
        self._log_based_test_round = False

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
        self.bs_ip_edit = QLineEdit(self.config.get("bs_ip", ""))
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(int(self.config.get("iperf_port", 5201)))
        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(1, 86400)
        self.duration_spin.setValue(int(self.config.get("iperf_time", 30)))
        self.reverse_check = QCheckBox("DL reverse client (-R)")
        self.reverse_check.setChecked(bool(self.config.get("dl_reverse_client", False)))
        form.addRow("BS IP", self.bs_ip_edit)
        form.addRow("Port", self.port_spin)
        form.addRow("Duration (s)", self.duration_spin)
        form.addRow(self.reverse_check)

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

        meas_group = QGroupBox("DL Measurements")
        meas_layout = QVBoxLayout(meas_group)
        self.meas_label = QLabel("--")
        self.meas_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.meas_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        meas_layout.addWidget(self.meas_label)
        self.rsrp_plot = AntennaRsrpPlot()
        meas_layout.addWidget(self.rsrp_plot)
        left.addWidget(meas_group)

        log_group = QGroupBox("iperf Log")
        log_layout = QVBoxLayout(log_group)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(200)
        log_layout.addWidget(self.log_view)
        left.addWidget(log_group, 1)

        self.status_label = QLabel("Ready")
        left.addWidget(self.status_label)

        self.plot_manager = PlotManager()

        root.addLayout(left, 3)
        root.addWidget(self.plot_manager, 7)
        self.setCentralWidget(central)

    def _start_ul(self) -> None:
        self._test_round += 1
        self._log_based_test_round = False
        cfg = self._ui_config()
        self.status_label.setText(
            f"UL: iperf3 -c {cfg['bs_ip']} -t {cfg['duration']}"
        )
        self.iperf.start_ul(cfg["bs_ip"], cfg["port"], cfg["duration"], cfg["log_file"])

    def _start_dl(self) -> None:
        cfg = self._ui_config()
        if self.reverse_check.isChecked():
            self._test_round += 1
            self._log_based_test_round = False
            self.status_label.setText(
                f"DL reverse: iperf3 -c {cfg['bs_ip']} -R -t {cfg['duration']}"
            )
            self.iperf.start_dl_reverse(
                cfg["bs_ip"], cfg["port"], cfg["duration"], cfg["log_file"]
            )
        else:
            self._log_based_test_round = True
            self.status_label.setText(
                "DL server listening; run iperf3 -c <ue_ip> on the BS"
            )
            self.iperf.start_dl_server(cfg["port"], cfg["log_file"])

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
        self.meas_reader.close()
        self.csi_reader.close()

    def _ui_config(self) -> Dict:
        return {
            "bs_ip": self.bs_ip_edit.text().strip(),
            "port": self.port_spin.value(),
            "duration": self.duration_spin.value(),
            "log_file": self._log_path,
        }

    def _refresh(self) -> None:
        self.throughput_label.setText(
            f"Throughput: {self._iperf_bps / 1e6:.1f} Mbps"
        )
        meas = self.meas_reader.read()
        if meas is not None:
            self._last_meas = meas
            self._update_meas_text(meas)
        elif not self._last_meas:
            self.meas_label.setText(
                "No PDSCH samples yet.\n"
                "Start a DL data flow so the UE receives PDSCH."
            )

        csi = self.csi_reader.read()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        if csi is not None:
            self._last_csi = csi

        log_changed = self._iperf_log_changed()
        if log_changed:
            self._save_channel(self._last_csi.get("channel"), stamp)
            self._write_csv(stamp)

        t = time.monotonic() - self._t0
        values: Dict[str, float] = {"throughput": self._iperf_bps / 1e6}
        values.update({
            "bler": float(self._last_meas.get("bler", 0)),
            "rsrp": float(self._last_meas.get("rsrp", -140)),
            "sinr": float(self._last_meas.get("sinr", 0)),
            "mcs": float(self._last_meas.get("mcs", 0)),
            "nprb": float(self._last_meas.get("nprb", 0)),
        })
        values.update({
            "capacity": float(self._last_csi.get("capacity", 0)),
            "rank": float(self._last_csi.get("rank", 0)),
            "condition_number": float(self._last_csi.get("condition_number", 0)),
        })

        self.plot_manager.throughput.add_sample(t, values)
        self.plot_manager.pdsch.add_sample(t, values)
        self.plot_manager.csi.add_sample(t, values)

    def _update_meas_text(self, meas: Dict) -> None:
        rsrp_per_ant = meas.get("rsrp_per_ant", [])
        self.rsrp_plot.update_values(rsrp_per_ant)
        lines = [
            f"Frame: {meas.get('frame', 0)}  Slot: {meas.get('slot', 0)}",
            f"BLER: {meas.get('bler', 0)} %",
            f"RSRP: {meas.get('rsrp', 0)} dBm",
            f"SINR: {meas.get('sinr', 0):.1f} dB",
            f"MCS: {meas.get('mcs', 0)}",
            f"NPRB: {meas.get('nprb', 0)}",
            f"Layers: {meas.get('layers', 0)}  TBS: {meas.get('tbs', 0)}",
            f"Qm: {meas.get('qm', 0)}  Freq offset: {meas.get('freq_offset', 0)} Hz",
        ]
        if self._last_csi:
            lines.append(
                f"CSI: C={self._last_csi.get('capacity', 0):.2f} "
                f"rank={self._last_csi.get('rank', 0)} "
                f"cond={self._last_csi.get('condition_number', 0):.1f}"
            )
        if rsrp_per_ant:
            lines.append(
                "Ant RSRP: "
                + ", ".join(f"RX{i}: {int(v)} dBm" for i, v in enumerate(rsrp_per_ant))
            )
        self.meas_label.setText("\n".join(lines))

    def _on_iperf_throughput(self, bps: float) -> None:
        self._iperf_bps = bps

    def _on_iperf_log(self, line: str) -> None:
        self.log_view.appendPlainText(line)
        if self._log_based_test_round and "Accepted connection from" in line:
            self._test_round += 1

    def _on_iperf_finished(self, msg: str) -> None:
        self.status_label.setText(f"{msg} | iperf完成")
        if not self._log_based_test_round:
            QApplication.beep()

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
            return os.path.join(GUI_DIR, "iperf.log")
        if os.path.isabs(path):
            return path
        # The existing config path is repo-root relative, e.g. "gui/iperf.log".
        return os.path.abspath(os.path.join(os.path.dirname(GUI_DIR), path))

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

    # ── per-test CSV logging ─────────────────────────────────────────

    def _open_csv(self) -> None:
        self._close_csv()
        ts = time.strftime("%Y%m%d_%H%M%S")
        self._csv_start_ts = ts
        record_dir = os.path.join(GUI_DIR, "record")
        os.makedirs(record_dir, exist_ok=True)
        csv_base = f"gui_ue_log_{ts}"
        self._csv_path = os.path.join(record_dir, f"{csv_base}.csv")
        self._channel_dir = os.path.join(record_dir, csv_base)
        os.makedirs(self._channel_dir, exist_ok=True)
        self._csv_fd = open(self._csv_path, "w", newline="", encoding="utf-8")
        self._csv_writer = csv.writer(self._csv_fd)
        self._reset_log_state()
        self._csv_writer.writerow([
            "timestamp", "test_round", "throughput_mbps",
            "sv0", "sv1", "sv2", "sv3", "sv4", "sv5", "sv6", "sv7",
            "capacity", "rank", "condition_number",
            "frame", "slot", "mcs", "qm", "tbs_bits", "layers", "nprb",
            "nsymb", "rv", "new_data_indicator", "target_code_rate",
            "bitrate_bps", "dlsch_received", "dlsch_errors", "bler",
            "rsrp_dBm", "rssi_dBm", "sinr_dB", "freq_offset_hz",
            "rsrp_ant0_dBm", "rsrp_ant1_dBm", "rsrp_ant2_dBm", "rsrp_ant3_dBm",
            "n_rb_dl", "scs", "nb_antennas_rx",
        ])
        self._csv_fd.flush()
        self.status_label.setText(f"logging to {self._csv_path}")

    def _write_csv(self, stamp: Optional[str] = None) -> None:
        if self._csv_writer is None:
            return
        if stamp is None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        sv = list(self._last_csi.get("singular_values", []))[:8]
        sv += [0.0] * (8 - len(sv))
        rsrp_per_ant = list(self._last_meas.get("rsrp_per_ant", []))[:4]
        rsrp_per_ant += [0] * (4 - len(rsrp_per_ant))
        self._csv_writer.writerow([
            stamp,
            self._test_round,
            self._iperf_bps / 1e6,
            *sv,
            self._last_csi.get("capacity", 0.0),
            self._last_csi.get("rank", 0),
            self._last_csi.get("condition_number", 0.0),
            self._last_meas.get("frame", 0),
            self._last_meas.get("slot", 0),
            self._last_meas.get("mcs", 0),
            self._last_meas.get("qm", 0),
            self._last_meas.get("tbs", 0),
            self._last_meas.get("layers", 0),
            self._last_meas.get("nprb", 0),
            self._last_meas.get("nsymb", 0),
            self._last_meas.get("rv", 0),
            self._last_meas.get("new_data_indicator", 0),
            self._last_meas.get("target_code_rate", 0),
            self._last_meas.get("bitrate_bps", 0),
            self._last_meas.get("dlsch_received", 0),
            self._last_meas.get("dlsch_errors", 0),
            self._last_meas.get("bler", 0),
            self._last_meas.get("rsrp", 0),
            self._last_meas.get("rssi", 0),
            self._last_meas.get("sinr", 0.0),
            self._last_meas.get("freq_offset", 0),
            *rsrp_per_ant,
            self._last_meas.get("n_rb_dl", 0),
            self._last_meas.get("scs", 0),
            self._last_meas.get("nb_antennas_rx", 0),
        ])
        self._csv_fd.flush()

    def _save_channel(self, channel, stamp: Optional[str] = None) -> None:
        if channel is None or self._channel_dir is None:
            return
        if stamp is None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        fname = f"channel_{stamp}.npy"
        np.save(os.path.join(self._channel_dir, fname), channel)

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
            "Save CSV, channel data and iperf log as folder name:",
            text=default_name,
        )
        if not ok:
            return

        record_dir = os.path.join(GUI_DIR, "record")
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
                "Save CSV, channel data and iperf log as folder name:",
                text=default_name,
            )
            if not ok:
                return
            folder_name = os.path.basename(text.strip())
            dest = os.path.join(record_dir, folder_name)

        os.makedirs(dest, exist_ok=True)
        shutil.move(self._csv_path, os.path.join(dest, os.path.basename(self._csv_path)))
        if self._channel_dir and os.path.isdir(self._channel_dir):
            for name in os.listdir(self._channel_dir):
                shutil.move(
                    os.path.join(self._channel_dir, name),
                    os.path.join(dest, name),
                )
            os.rmdir(self._channel_dir)
        if os.path.exists(self._log_path):
            shutil.move(self._log_path, os.path.join(dest, os.path.basename(self._log_path)))
        self.status_label.setText(f"recordings saved to {dest}")


def main() -> None:
    # Qt's xcb plugin often lacks libxcb-cursor0 on Wayland GNOME; prefer the
    # native Wayland platform plugin when available.
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" and not os.environ.get(
        "QT_QPA_PLATFORM"
    ):
        os.environ["QT_QPA_PLATFORM"] = "wayland"
    app = QApplication(sys.argv)
    config = load_config()
    win = MainWindow(config)
    win.show()
    exit_code = app.exec_()
    if exit_code == RESTART_EXIT_CODE:
        try:
            subprocess.Popen(MainWindow._restart_command())
        except OSError as exc:
            print(f"failed to restart GUI: {exc}", file=sys.stderr)
            exit_code = 1
        else:
            exit_code = 0
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
