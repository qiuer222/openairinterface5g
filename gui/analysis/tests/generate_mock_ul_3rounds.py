#!/usr/bin/env python3
"""Generate a deterministic three-round UL gNB/UE recording fixture."""

from __future__ import annotations

import argparse
import csv
import os
import shutil
from datetime import datetime, timedelta

import numpy as np


GNB_HEADER = [
    "timestamp", "test_round", "iperf_direction", "throughput_mbps",
    "ul_frame", "ul_slot", "ul_rnti", "ul_bler", "ul_sinr",
    "ul_mcs", "ul_nprb", "ul_layers", "ul_qm", "ul_tbs",
    "ul_nsymb", "ul_n_rb_ul", "ul_scs_khz", "ul_sched_se", "ul_app_se",
    "ul_timing_advance", "ul_cqi", "ul_tpmi", "ul_tpmi_valid",
    "ul_rv", "ul_ndi", "ul_target_code_rate", "ul_rssi_dbfs",
    "dl_frame", "dl_slot", "dl_rnti", "dl_bler", "dl_sinr",
    "dl_mcs", "dl_nprb", "dl_layers", "dl_qm", "dl_tbs",
    "dl_cqi", "dl_ri", "dl_nsymb", "dl_rv", "dl_ndi",
    "dl_target_code_rate", "dl_pmi_x1", "dl_pmi_x2", "dl_n_rb_dl",
    "dl_scs_khz", "dl_sched_se", "dl_app_se",
    "srs_capacity", "srs_rank", "srs_condition", "srs_snr",
]

UE_HEADER = [
    "timestamp", "test_round", "iperf_direction", "throughput_mbps",
    "sv0", "sv1", "sv2", "sv3", "sv4", "sv5", "sv6", "sv7",
    "capacity", "rank", "condition_number",
    "frame", "slot", "mcs", "qm", "tbs_bits", "layers", "nprb",
    "nsymb", "rv", "new_data_indicator", "target_code_rate",
    "bitrate_bps", "dlsch_received", "dlsch_errors", "bler",
    "rsrp_dBm", "rssi_dBm", "sinr_dB", "freq_offset_hz",
    "rsrp_ant0_dBm", "rsrp_ant1_dBm", "rsrp_ant2_dBm", "rsrp_ant3_dBm",
    "n_rb_dl", "scs", "nb_antennas_rx", "wideband_cqi_dB",
    "n_rb_ul", "dl_sched_se", "dl_app_se", "ul_app_se",
]


def _timestamp(index: int) -> str:
    start = datetime(2026, 10, 5, 12, 0, 0)
    return (start + timedelta(seconds=index)).strftime("%Y%m%d_%H%M%S_%f")


def _channel(
    *,
    srs: bool,
    sample_index: int,
) -> np.ndarray:
    subcarriers = np.arange(64)
    h = np.zeros((2, 2, 1, 64) if srs else (2, 2, 64), dtype=np.complex128)
    for rx in range(2):
        for tx in range(2):
            amplitude = (1.0 + 0.2 * sample_index) * (
                1.0 if rx == tx else 0.25
            )
            phase = (
                2.0
                * np.pi
                * (sample_index + 1)
                * subcarriers
                / 64.0
            )
            values = amplitude * np.exp(1j * phase)
            if srs:
                h[rx, tx, 0, :] = values
            else:
                h[rx, tx, :] = values
    return h


def _gnb_row(round_index: int, sample_index: int, timestamp: str) -> dict:
    throughput = 10.0 * round_index + 1.5 * sample_index
    layers = 1 if round_index != 2 else 2
    return {
        "timestamp": timestamp,
        "test_round": round_index,
        "iperf_direction": "UL",
        "throughput_mbps": throughput,
        "ul_frame": 100 + round_index,
        "ul_slot": sample_index,
        "ul_rnti": 0x1234,
        "ul_bler": 1.0 * sample_index,
        "ul_sinr": 18.0 + round_index,
        "ul_mcs": 8 + round_index + sample_index,
        "ul_nprb": 100,
        "ul_layers": layers,
        "ul_qm": 2,
        "ul_tbs": 4000 + 100 * sample_index,
        "ul_nsymb": 13,
        "ul_n_rb_ul": 106,
        "ul_scs_khz": 30,
        "ul_sched_se": 2.0 + 0.1 * round_index,
        "ul_app_se": throughput / (106 * 12 * 30e3) * 1e6,
        "ul_timing_advance": 31 + sample_index,
        "ul_cqi": 120 + 10 * round_index,
        "ul_tpmi": 2 if round_index == 2 else 0,
        "ul_tpmi_valid": 1,
        "ul_rv": sample_index % 4,
        "ul_ndi": sample_index % 2,
        "ul_target_code_rate": 400 + 10 * round_index,
        "ul_rssi_dbfs": -20.0 + round_index,
        "dl_frame": 0,
        "dl_slot": 0,
        "dl_rnti": 0,
        "dl_bler": 0,
        "dl_sinr": "",
        "dl_mcs": 0,
        "dl_nprb": 0,
        "dl_layers": 0,
        "dl_qm": 0,
        "dl_tbs": 0,
        "dl_cqi": 0,
        "dl_ri": 0,
        "dl_nsymb": 0,
        "dl_rv": 0,
        "dl_ndi": 0,
        "dl_target_code_rate": 0,
        "dl_pmi_x1": 0,
        "dl_pmi_x2": 0,
        "dl_n_rb_dl": 0,
        "dl_scs_khz": 0,
        "dl_sched_se": "",
        "dl_app_se": "",
        "srs_capacity": 5.0 + round_index,
        "srs_rank": layers,
        "srs_condition": 1.2,
        "srs_snr": 19.0 + round_index,
    }


def _ue_row(round_index: int, sample_index: int, timestamp: str) -> dict:
    rsrp = -75.0 - round_index
    return {
        "timestamp": timestamp,
        "test_round": round_index,
        "iperf_direction": "UL",
        "throughput_mbps": 9.0 * round_index + sample_index,
        "sv0": 1.0 + 0.1 * round_index,
        "sv1": 0.3,
        "sv2": 0.0,
        "sv3": 0.0,
        "sv4": 0.0,
        "sv5": 0.0,
        "sv6": 0.0,
        "sv7": 0.0,
        "capacity": 4.0 + round_index,
        "rank": 2,
        "condition_number": 3.0,
        "frame": 100 + round_index,
        "slot": sample_index,
        "mcs": 8 + round_index,
        "qm": 2,
        "tbs_bits": 4000,
        "layers": 1 if round_index != 2 else 2,
        "nprb": 100,
        "nsymb": 13,
        "rv": sample_index % 4,
        "new_data_indicator": sample_index % 2,
        "target_code_rate": 400 + 10 * round_index,
        "bitrate_bps": 10_000_000 + round_index * 1_000_000,
        "dlsch_received": 10 * round_index,
        "dlsch_errors": 0,
        "bler": 0.0,
        "rsrp_dBm": rsrp,
        "rssi_dBm": -50.0,
        "sinr_dB": 15.0 + round_index,
        "freq_offset_hz": 0,
        "rsrp_ant0_dBm": rsrp,
        "rsrp_ant1_dBm": rsrp - 1.0,
        "rsrp_ant2_dBm": 0,
        "rsrp_ant3_dBm": 0,
        "n_rb_dl": 106,
        "scs": 30000,
        "nb_antennas_rx": 2,
        "wideband_cqi_dB": 16 + round_index,
        "n_rb_ul": 106,
        "dl_sched_se": 0.0,
        "dl_app_se": 0.0,
        "ul_app_se": 2.0 + round_index,
    }


def generate(output_dir: str) -> None:
    os.makedirs(output_dir, exist_ok=True)
    base = (
        datetime(2026, 10, 5, 12, 0, 0).strftime(
            "%Y%m%d_%H%M%S_%f"
        )
    )
    gnb_archive = os.path.join(output_dir, f"gnb_{base}")
    ue_archive = os.path.join(output_dir, f"ue_{base}")
    for archive in (gnb_archive, ue_archive):
        if os.path.isdir(archive):
            shutil.rmtree(archive)
    gnb_csv = os.path.join(gnb_archive, f"gui_gnb_log_{base}.csv")
    ue_csv = os.path.join(ue_archive, f"gui_ue_log_{base}.csv")
    gnb_srs_dir = os.path.splitext(gnb_csv)[0]
    ue_csi_dir = os.path.splitext(ue_csv)[0]
    for directory in (
        gnb_archive,
        ue_archive,
        gnb_srs_dir,
        ue_csi_dir,
    ):
        os.makedirs(directory, exist_ok=True)

    gnb_rows = []
    ue_rows = []
    sample_index = 0
    for round_index in range(1, 4):
        for round_sample in range(3):
            timestamp = _timestamp(sample_index)
            gnb_rows.append(_gnb_row(round_index, round_sample, timestamp))
            ue_rows.append(_ue_row(round_index, round_sample, timestamp))
            np.save(
                os.path.join(gnb_srs_dir, f"srs_{timestamp}.npy"),
                _channel(srs=True, sample_index=sample_index),
            )
            np.save(
                os.path.join(ue_csi_dir, f"channel_{timestamp}.npy"),
                _channel(srs=False, sample_index=sample_index),
            )
            sample_index += 1

    with open(gnb_csv, "w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=GNB_HEADER)
        writer.writeheader()
        writer.writerows(gnb_rows)
    with open(ue_csv, "w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=UE_HEADER)
        writer.writeheader()
        writer.writerows(ue_rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a three-round UL gNB/UE analysis fixture."
    )
    parser.add_argument(
        "--output-dir",
        default="/tmp/oai_ul_3rounds",
    )
    args = parser.parse_args()
    generate(args.output_dir)
    print(f"generated {args.output_dir}")


if __name__ == "__main__":
    main()
