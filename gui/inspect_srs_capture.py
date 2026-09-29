#!/usr/bin/env python3
"""Print selected samples from an OAI SRS/DMRS/CSI-RS capture."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gui.analyze_srs_capture import CaptureRecord, KIND_NAMES, read_capture


def parse_index_spec(value: str) -> List[int]:
    indices = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        if "-" in item:
            start_text, end_text = item.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if end < start:
                raise ValueError(f"invalid descending index range {item!r}")
            indices.extend(range(start, end + 1))
        else:
            indices.append(int(item))
    if not indices:
        raise ValueError("index list is empty")
    return sorted(set(indices))


def capture_paths(path: str, role: Optional[str]) -> List[str]:
    if os.path.isfile(path):
        return [path]
    if os.path.isfile(os.path.join(path, "capture.bin")):
        return [path]

    paths = []
    for candidate in ("gnb", "ue"):
        candidate_path = os.path.join(path, candidate)
        if os.path.isfile(os.path.join(candidate_path, "capture.bin")):
            if role is None or role == candidate:
                paths.append(candidate_path)
    if not paths:
        raise ValueError(f"no capture.bin found under {path}")
    return paths


def record_matches(record: CaptureRecord, args: argparse.Namespace) -> bool:
    filters = (
        (args.event, record.event_id),
        (args.rx, record.rx),
        (args.port, record.port),
        (args.layer, record.layer),
        (args.symbol, record.symbol),
    )
    return record.kind_name == args.kind and all(
        expected is None or expected == actual for expected, actual in filters
    )


def selected_rows(records: Iterable[CaptureRecord],
                  kind: str,
                  indices: Sequence[int]) -> List[Dict]:
    rows = []
    for record in records:
        if record.kind_name != kind:
            continue
        flat = record.data.reshape(record.data.shape[0], -1)
        for index in indices:
            if index < 0 or index >= flat.shape[1]:
                continue
            for row in range(flat.shape[0]):
                value = complex(flat[row, index])
                rows.append({
                    "role": record.role,
                    "event_id": record.event_id,
                    "kind": record.kind_name,
                    "frame": record.frame,
                    "slot": record.slot,
                    "rnti": record.rnti,
                    "rx": record.rx,
                    "port": record.port,
                    "layer": record.layer,
                    "symbol": record.symbol,
                    "row": row,
                    "index": index,
                    "real": value.real,
                    "imag": value.imag,
                    "magnitude": abs(value),
                    "phase_rad": float(np.angle(value)),
                })
    return rows


def print_records(records: Sequence[CaptureRecord]) -> None:
    seen = set()
    for record in records:
        key = (
            record.role,
            record.kind_name,
            record.event_id,
            record.frame,
            record.slot,
            record.rx,
            record.port,
            record.layer,
            record.symbol,
            record.cols,
        )
        if key in seen:
            continue
        seen.add(key)
        print(
            f"{record.role:3s} kind={record.kind_name:15s} event={record.event_id} "
            f"frame_slot={record.frame}.{record.slot} rnti={record.rnti} "
            f"rx={record.rx} port={record.port} layer={record.layer} "
            f"symbol={record.symbol} cols={record.cols}"
        )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Print stored complex values at specified indices. For SRS/CSI-RS "
            "frequency-domain records the index is an FFT bin/subcarrier index; "
            "for srs_time it is a time-domain tap index."
        )
    )
    parser.add_argument("input", help="run directory, role directory, or capture.bin")
    parser.add_argument("--kind", choices=sorted(KIND_NAMES.values()), default="srs_ls")
    parser.add_argument("--event", type=int)
    parser.add_argument("--rx", type=int)
    parser.add_argument("--port", type=int)
    parser.add_argument("--layer", type=int)
    parser.add_argument("--symbol", type=int)
    parser.add_argument("--index", help="indices such as 0,10,20-24")
    parser.add_argument("--list-records", action="store_true")
    parser.add_argument("--json", action="store_true", help="write selected values as JSON")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if not args.list_records and args.index is None:
        raise SystemExit("--index is required unless --list-records is used")

    try:
        records = []
        for path in capture_paths(args.input, None):
            _, capture_records = read_capture(path)
            records.extend(record for record in capture_records if record_matches(record, args))

        if args.list_records:
            print_records(records)
            return 0

        indices = parse_index_spec(args.index)
        rows = selected_rows(records, args.kind, indices)
        if not rows:
            raise ValueError("no matching records or all requested indices are out of range")

        if args.json:
            json.dump(rows, sys.stdout, indent=2)
            print()
            return 0

        for row in rows:
            print(
                f"{row['role']} event={row['event_id']} kind={row['kind']} "
                f"frame_slot={row['frame']}.{row['slot']} rx={row['rx']} "
                f"port={row['port']} layer={row['layer']} symbol={row['symbol']} "
                f"index={row['index']} real={row['real']:.6g} "
                f"imag={row['imag']:.6g} abs={row['magnitude']:.6g} "
                f"phase_rad={row['phase_rad']:.6g}"
            )
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
