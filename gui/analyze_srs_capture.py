#!/usr/bin/env python3
"""Parse and visualize OAI SRS/DMRS/CSI-RS debug capture files."""

from __future__ import annotations

import argparse
import csv
import json
import os
import struct
import sys
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CAPTURE_MAGIC = 0x31435044
CAPTURE_VERSION = 1
HEADER = struct.Struct("<IHH I Q I I I Q H B B B B H H H H H H I h B 9s")
DMRS_SYMBOL_FLAG = 0x01

KIND_NAMES = {
    1: "srs_rx",
    2: "srs_ref",
    3: "srs_noise",
    4: "srs_ls",
    5: "srs_interp",
    6: "srs_time",
    7: "dmrs_ref",
    8: "dmrs_rx",
    9: "dmrs_ls",
    10: "dmrs_interp",
    11: "dmrs_equalized",
    12: "csirs_rx",
    13: "csirs_ref",
    14: "csirs_ls",
    15: "csirs_interp",
}


@dataclass
class CaptureRecord:
    role: str
    kind: int
    sequence: int
    event_id: int
    frame: int
    slot: int
    timestamp_ns: int
    rnti: int
    rx: int
    port: int
    layer: int
    symbol: int
    rows: int
    cols: int
    fft_size: int
    n_rb: int
    start_rb: int
    bwp_start: int
    subcarrier_spacing: int
    snr_db: Optional[float]
    flags: int
    data: np.ndarray

    @property
    def kind_name(self) -> str:
        return KIND_NAMES.get(self.kind, f"unknown_{self.kind}")


def load_pyplot():
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError(
            "matplotlib is required for plots; install gui/analysis/requirements.txt"
        ) from exc
    return plt


def infer_role(path: str, manifest: Dict) -> str:
    role = manifest.get("role")
    if role in ("gnb", "ue"):
        return role
    name = os.path.basename(os.path.normpath(path)).lower()
    if "gnb" in name or "softmodem" in name:
        return "gnb"
    if name == "ue" or "uesoftmodem" in name:
        return "ue"
    raise ValueError(f"cannot infer capture role from {path!r}")


def read_capture(path: str) -> Tuple[Dict, List[CaptureRecord]]:
    capture_path = path
    manifest_path = os.path.join(path, "manifest.json")
    if os.path.isfile(path):
        capture_path = path
        manifest_path = os.path.join(os.path.dirname(path), "manifest.json")
    else:
        capture_path = os.path.join(path, "capture.bin")

    manifest = {}
    if os.path.isfile(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as fp:
            manifest = json.load(fp)

    role = infer_role(os.path.dirname(capture_path) if os.path.isfile(path) else path, manifest)
    records: List[CaptureRecord] = []
    with open(capture_path, "rb") as fp:
        offset = 0
        while True:
            raw_header = fp.read(HEADER.size)
            if not raw_header:
                break
            if len(raw_header) != HEADER.size:
                raise ValueError(f"truncated record header at offset {offset}")

            values = HEADER.unpack(raw_header)
            (magic, version, kind, payload_bytes, sequence, event_id, frame, slot,
             timestamp_ns, rnti, rx, port, layer, symbol, rows, cols, fft_size,
             n_rb, start_rb, bwp_start, scs, snr_db_x10, flags, _reserved) = values
            if magic != CAPTURE_MAGIC:
                raise ValueError(f"bad capture magic at offset {offset}: 0x{magic:08x}")
            if version != CAPTURE_VERSION:
                raise ValueError(f"unsupported capture version {version} at offset {offset}")
            if payload_bytes != rows * cols * 4:
                raise ValueError(
                    f"payload size mismatch at offset {offset}: {payload_bytes} "
                    f"for shape {rows}x{cols}"
                )

            payload = fp.read(payload_bytes)
            if len(payload) != payload_bytes:
                raise ValueError(f"truncated payload at offset {offset}")

            pairs = np.frombuffer(payload, dtype="<i2").reshape(rows, cols, 2)
            data = pairs[..., 0].astype(np.float64) + 1j * pairs[..., 1].astype(np.float64)
            records.append(
                CaptureRecord(
                    role=role,
                    kind=kind,
                    sequence=sequence,
                    event_id=event_id,
                    frame=frame,
                    slot=slot,
                    timestamp_ns=timestamp_ns,
                    rnti=rnti,
                    rx=rx,
                    port=port,
                    layer=layer,
                    symbol=symbol,
                    rows=rows,
                    cols=cols,
                    fft_size=fft_size,
                    n_rb=n_rb,
                    start_rb=start_rb,
                    bwp_start=bwp_start,
                    subcarrier_spacing=scs,
                    snr_db=snr_db_x10 / 10.0,
                    flags=flags,
                    data=data,
                )
            )
            offset += HEADER.size + payload_bytes

    return manifest, records


def discover_inputs(args: argparse.Namespace) -> List[str]:
    inputs: List[str] = []
    if os.path.isfile(args.input):
        inputs.append(args.input)
    elif os.path.isfile(os.path.join(args.input, "gnb", "capture.bin")):
        inputs.append(os.path.join(args.input, "gnb"))
    elif os.path.isfile(os.path.join(args.input, "ue", "capture.bin")):
        inputs.append(os.path.join(args.input, "ue"))
    elif os.path.isfile(os.path.join(args.input, "capture.bin")):
        inputs.append(args.input)
    else:
        raise ValueError(f"no gnb/ue capture.bin found under {args.input}")

    if args.ue_dir:
        inputs.append(args.ue_dir)
    elif os.path.isfile(os.path.join(args.input, "ue", "capture.bin")):
        inputs.append(os.path.join(args.input, "ue"))
    return inputs


def db(values: np.ndarray, floor: float = 1e-12) -> np.ndarray:
    return 20.0 * np.log10(np.maximum(np.abs(values), floor))


def grouped(records: Iterable[CaptureRecord]) -> Dict[Tuple[str, int], List[CaptureRecord]]:
    result: Dict[Tuple[str, int], List[CaptureRecord]] = {}
    for record in records:
        result.setdefault((record.role, record.event_id), []).append(record)
    return result


def find_record(records: Sequence[CaptureRecord],
                kind: int,
                rx: int = 0,
                port: int = 0,
                layer: int = 0) -> Optional[CaptureRecord]:
    for record in records:
        if (record.kind == kind and record.rx == rx and record.port == port
                and record.layer == layer):
            return record
    return None


def path_keys(records: Sequence[CaptureRecord], kinds: Sequence[int]) -> List[Tuple[int, int]]:
    return sorted({(record.rx, record.port) for record in records if record.kind in kinds})


def split_frequency_record(record: CaptureRecord) -> Iterable[Tuple[int, np.ndarray]]:
    flat = record.data.reshape(-1)
    if record.fft_size > 0 and record.cols > record.fft_size and record.cols % record.fft_size == 0:
        symbols = record.cols // record.fft_size
        for symbol in range(symbols):
            start = symbol * record.fft_size
            yield symbol, flat[start:start + record.fft_size]
    else:
        yield record.symbol, flat


def plot_frequency_event(records: Sequence[CaptureRecord],
                         event: int,
                         output_dir: str) -> None:
    plt = load_pyplot()
    if not records:
        return
    role = records[0].role
    paths = path_keys(records, (4, 5, 14, 15))
    if not paths:
        return

    max_paths = 8
    paths = paths[:max_paths]
    fig, axes = plt.subplots(len(paths), 2, figsize=(13, 2.8 * len(paths)), squeeze=False)
    for row, (rx, port) in enumerate(paths):
        candidates = []
        for kind, label in (
            (4, "SRS LS"),
            (5, "SRS interp"),
            (14, "CSI-RS LS"),
            (15, "CSI-RS interp"),
        ):
            record = find_record(records, kind, rx, port)
            if record is not None:
                candidates.append((record, label))

        if not candidates:
            continue

        for record, label in candidates:
            for symbol, values in split_frequency_record(record):
                suffix = f" sym{symbol}" if record.cols > record.fft_size > 0 else ""
                x = np.arange(values.size)
                if record.kind in (4, 14):
                    valid = np.abs(values) > 0
                    axes[row, 0].scatter(x[valid], db(values[valid]), s=8,
                                         label=f"{label}{suffix}")
                    axes[row, 1].scatter(x[valid], np.angle(values[valid]), s=8,
                                         label=f"{label}{suffix}")
                else:
                    axes[row, 0].plot(x, db(values), label=f"{label}{suffix}", linewidth=1.0)
                    axes[row, 1].plot(x, np.unwrap(np.angle(values)),
                                      label=f"{label}{suffix}", linewidth=1.0)

        for col in range(2):
            axes[row, col].grid(True, alpha=0.25)
            axes[row, col].legend(loc="best", fontsize=8)
            axes[row, col].set_xlabel("FFT bin / subcarrier index")
        axes[row, 0].set_ylabel(f"rx{rx} port{port}\ndB")
        axes[row, 1].set_ylabel("phase (rad)")

    fig.suptitle(
        f"{role} event {event}: LS scatter and interpolated channel "
        "(x = FFT bin / subcarrier index)"
    )
    fig.tight_layout(rect=(0, 0, 1, 0.98))
    fig.savefig(os.path.join(output_dir, f"{role}_event_{event}_channel.png"), dpi=140)
    plt.close(fig)


def plot_srs_time(records: Sequence[CaptureRecord], event: int, output_dir: str) -> None:
    plt = load_pyplot()
    time_records = [record for record in records if record.kind == 6]
    if not time_records:
        return
    fig, ax = plt.subplots(figsize=(12, 5))
    for record in time_records[:8]:
        values = record.data.reshape(-1)
        ax.plot(np.arange(values.size), db(values),
                label=f"rx{record.rx} port{record.port}", linewidth=1.0)
    ax.set_xlabel("time-domain tap index (oversampled SRS IDFT output)")
    ax.set_ylabel("dB")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="best", fontsize=8)
    ax.set_title(f"{records[0].role} event {event}: SRS time-domain channel")
    fig.tight_layout()
    fig.savefig(os.path.join(output_dir, f"{records[0].role}_event_{event}_srs_time.png"), dpi=140)
    plt.close(fig)


def plot_dmrs(records: Sequence[CaptureRecord], event: int, output_dir: str) -> None:
    plt = load_pyplot()
    equalized = [record for record in records if record.kind == 11]
    if not equalized:
        return

    by_layer: Dict[int, List[np.ndarray]] = {}
    for record in equalized:
        by_layer.setdefault(record.layer, []).append(record.data.reshape(-1))

    fig, axes = plt.subplots(1, len(by_layer), figsize=(5 * len(by_layer), 5), squeeze=False)
    for col, (layer, arrays) in enumerate(sorted(by_layer.items())):
        values = np.concatenate(arrays)
        values = values[np.abs(values) > 0]
        if values.size:
            values = values / max(float(np.sqrt(np.mean(np.abs(values) ** 2))), 1e-12)
        axes[0, col].scatter(values.real, values.imag, s=3, alpha=0.35)
        axes[0, col].axhline(0, color="black", linewidth=0.5)
        axes[0, col].axvline(0, color="black", linewidth=0.5)
        axes[0, col].set_aspect("equal", adjustable="box")
        axes[0, col].set_title(f"layer {layer}")
        axes[0, col].set_xlabel("I")
        axes[0, col].set_ylabel("Q")
        axes[0, col].grid(True, alpha=0.2)

    fig.suptitle(f"{records[0].role} event {event}: equalized DMRS/PUSCH constellation")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(os.path.join(output_dir, f"{records[0].role}_event_{event}_dmrs_constellation.png"), dpi=140)
    plt.close(fig)


def write_reports(records: Sequence[CaptureRecord], manifests: Sequence[Dict], output_dir: str) -> None:
    metrics_rows = []
    event_summaries = []
    for (role, event), event_records in sorted(grouped(records).items()):
        event_metrics = []
        for record in event_records:
            row = {
                "role": role,
                "event_id": event,
                "kind": record.kind_name,
                "frame": record.frame,
                "slot": record.slot,
                "rnti": record.rnti,
                "rx": record.rx,
                "port": record.port,
                "layer": record.layer,
                "symbol": record.symbol,
                "samples": record.data.size,
                "snr_db": record.snr_db,
            }
            metrics_rows.append(row)
            if record.kind in (4, 5, 14, 15):
                event_metrics.append(row)
        if event_records:
            first = event_records[0]
            event_summaries.append({
                "role": role,
                "event_id": event,
                "frame": first.frame,
                "slot": first.slot,
                "rnti": first.rnti,
                "records": len(event_records),
                "channel_metrics": event_metrics,
            })

    summary = {
        "manifests": list(manifests),
        "record_count": len(records),
        "events": event_summaries,
    }
    with open(os.path.join(output_dir, "summary.json"), "w", encoding="utf-8") as fp:
        json.dump(summary, fp, indent=2)

    fieldnames = [
        "role", "event_id", "kind", "frame", "slot", "rnti", "rx", "port", "layer",
        "symbol", "samples", "snr_db",
    ]
    with open(os.path.join(output_dir, "events.csv"), "w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(metrics_rows)


def analyze(args: argparse.Namespace) -> None:
    records: List[CaptureRecord] = []
    manifests: List[Dict] = []
    for path in discover_inputs(args):
        manifest, role_records = read_capture(path)
        manifests.append({"path": path, **manifest})
        records.extend(role_records)

    if not records:
        raise ValueError("capture contains no records")

    output_dir = args.output_dir or os.path.join(args.input, "analysis")
    figures_dir = os.path.join(output_dir, "figures")
    os.makedirs(figures_dir, exist_ok=True)

    for (role, event), event_records in sorted(grouped(records).items()):
        plot_frequency_event(event_records, event, figures_dir)
        plot_srs_time(event_records, event, figures_dir)
        plot_dmrs(event_records, event, figures_dir)

    write_reports(records, manifests, output_dir)
    print(f"analyzed {len(records)} records from {len(manifests)} capture(s)")
    print(f"reports: {output_dir}")
    print(f"figures: {figures_dir}")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze OAI gNB/UE SRS, DMRS and CSI-RS debug capture files."
    )
    parser.add_argument("input", help="run directory, role directory, or capture.bin")
    parser.add_argument("--ue-dir", help="separate UE capture directory")
    parser.add_argument("--output-dir", help="analysis output directory")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        analyze(args)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
