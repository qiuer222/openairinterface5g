#!/usr/bin/env python3
"""Convert GUI SRS/CSI-RS channel snapshots to RFSim replay .bin files.

The GUI saves the same fixed-point channel data as numpy complex arrays:

  CSI-RS: channel_*.npy, shape (n_rx, n_tx, fft_size)
  SRS:    srs_*.npy,     shape (n_rx, n_tx, n_symbols, fft_size)

This script writes those snapshots into the binary format already consumed by
radio/rfsimulator/apply_channel_fd.c.  For SRS arrays with multiple symbols the
first symbol is used, matching the original dump_srs_channel() behavior.

Version 2 files written here normalize every slot's H to the stable OAI AMP
scale and store one float ``slot_gain_lin`` per slot after the header.  The
replay engine uses that gain to restore the recorded absolute channel power, so
path loss in the RFSim channel model remains an independent level control.
"""

from __future__ import annotations

import argparse
import glob
import os
import struct
import sys
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np


MAGIC = 0x48534D52
VERSION = 2
LEGACY_VERSION = 1
H_SCALE_BITS_DEFAULT = 9
C16_MAX_AMPLITUDE = 32767.0
HEADER_FORMAT = "<IHBBHHIIHHBB22x"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert GUI .npy channel snapshots to RFSim .bin replay files, "
            "or verify an existing .bin file."
        )
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help=".npy files, shell globs, or directories. With --verify, .bin files.",
    )
    parser.add_argument(
        "--output",
        help="Output .bin path. Defaults to /tmp/csi_rs_channel.bin or /tmp/srs_channel.bin.",
    )
    parser.add_argument(
        "--kind",
        choices=("auto", "srs", "csi"),
        default="auto",
        help="Record type. Auto detects from srs_/channel_ filenames.",
    )
    parser.add_argument(
        "--n-rb",
        type=int,
        default=106,
        help="NR resource blocks of the recording (default: 106).",
    )
    parser.add_argument(
        "--scs",
        type=int,
        default=30000,
        help="Subcarrier spacing in Hz (default: 30000).",
    )
    parser.add_argument(
        "--symbol",
        type=int,
        default=0,
        help="SRS symbol index to store (default: 0).",
    )
    parser.add_argument(
        "--slot-start",
        type=int,
        default=0,
        help="First slot number written to the .bin (default: 0).",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify .bin header/slot layout instead of converting.",
    )
    parser.add_argument(
        "--reference",
        nargs="+",
        help=(
            "With --verify, source .npy files/directories whose c16_t values "
            "must match the .bin slots. Put --reference after the .bin path."
        ),
    )
    return parser.parse_args(argv)


def infer_kind(path: str) -> str:
    base = os.path.basename(path)
    if base.startswith("srs_"):
        return "srs"
    if base.startswith("channel_"):
        return "csi"
    raise ValueError(f"cannot infer kind from filename: {path}")


def expand_inputs(inputs: Sequence[str], kind: str) -> Tuple[List[str], str]:
    paths: List[str] = []
    for item in inputs:
        if os.path.isdir(item):
            names = ["srs_*.npy", "channel_*.npy"] if kind == "auto" else [
                f"{kind}_*.npy"
            ]
            matches: List[str] = []
            for pattern in names:
                matches.extend(glob.glob(os.path.join(item, pattern)))
            if not matches:
                raise ValueError(f"no GUI channel .npy files found in {item}")
            paths.extend(sorted(matches))
        else:
            paths.extend(sorted(glob.glob(item)))

    if not paths:
        raise ValueError("no input .npy files matched")

    detected = infer_kind(paths[0]) if kind == "auto" else kind
    for path in paths:
        if kind == "auto" and infer_kind(path) != detected:
            raise ValueError("cannot mix SRS and CSI-RS .npy files in one .bin")
    return paths, detected


def load_channel(path: str, kind: str, symbol: int) -> np.ndarray:
    arr = np.load(path, allow_pickle=False)
    if arr.ndim == 3:
        h = arr
    elif arr.ndim == 4:
        if kind != "srs" and arr.shape[2] != 1:
            raise ValueError(f"4D CSI-RS file has unexpected shape {arr.shape}: {path}")
        if symbol >= arr.shape[2]:
            raise ValueError(
                f"symbol {symbol} out of range for {path} with shape {arr.shape}"
            )
        h = arr[:, :, symbol, :]
    else:
        raise ValueError(
            f"expected channel shape (rx, tx, fft) or (rx, tx, symbol, fft), got {arr.shape}: {path}"
        )

    if h.ndim != 3:
        raise ValueError(f"failed to normalize channel shape from {arr.shape}: {path}")
    if not np.issubdtype(h.dtype, np.complexfloating):
        if h.dtype.names == ("r", "i"):
            h = h["r"].astype(np.float64) + 1j * h["i"].astype(np.float64)
        else:
            h = h.astype(np.complex128)
    return h.astype(np.complex128, copy=False)


def load_slots(
    paths: Sequence[str], kind: str, symbol: int
) -> Tuple[List[np.ndarray], int, int, int]:
    arrays: List[np.ndarray] = []
    first_shape: Optional[Tuple[int, ...]] = None
    for path in paths:
        h = load_channel(path, kind, symbol)
        if first_shape is None:
            first_shape = h.shape
        elif h.shape != first_shape:
            raise ValueError(
                f"channel shapes differ: {first_shape} vs {h.shape} in {path}"
            )
        arrays.append(h)
    if first_shape is None:
        raise ValueError("no channels loaded")
    nrx, ntx, fft_size = first_shape
    return arrays, nrx, ntx, fft_size


def normalize_slot(
    h: np.ndarray, h_scale_bits: int = H_SCALE_BITS_DEFAULT
) -> Tuple[np.ndarray, float]:
    """Return (H scaled to the AMP scale, linear gain restoring raw H).

    A single scalar is used for the whole slot so the time-domain channel
    impulse response is only amplitude-scaled (zero bins stay zero).  The gain
    is ``1 / scale``; the replay engine multiplies its fixed-point result by
    this gain to recover the recorded raw channel level.
    """
    mag = np.abs(h)
    valid = mag > 0
    if not np.any(valid):
        return h.astype(np.complex128, copy=True), 1.0

    rms = float(np.sqrt(np.mean(mag[valid] ** 2)))
    peak = float(np.max(mag[valid]))
    target_scale = (1 << h_scale_bits) / rms
    # Never scale a c16 component beyond the 16-bit range.
    peak_scale = (C16_MAX_AMPLITUDE - 1.0) / peak
    scale = min(target_scale, peak_scale)
    return (h * scale).astype(np.complex128, copy=False), 1.0 / scale


def channel_to_c16(h: np.ndarray) -> bytes:
    if h.shape[-1] == 0:
        raise ValueError("channel has no subcarriers")
    re = np.clip(np.rint(h.real), -32768, 32767).astype("<i2")
    im = np.clip(np.rint(h.imag), -32768, 32767).astype("<i2")
    c16 = np.empty(h.shape, dtype=np.dtype([("r", "<i2"), ("i", "<i2")]))
    c16["r"] = re
    c16["i"] = im
    return c16.ravel(order="C").view("<i2").tobytes()


def make_header(
    nrx: int,
    ntx: int,
    fft_size: int,
    n_rb: int,
    scs: int,
    num_slots: int,
    n_symbols: int,
    h_scale_bits: int = H_SCALE_BITS_DEFAULT,
) -> bytes:
    return struct.pack(
        HEADER_FORMAT,
        MAGIC,
        VERSION,
        nrx,
        ntx,
        fft_size,
        n_rb,
        scs,
        num_slots,
        fft_size,
        0,
        n_symbols,
        h_scale_bits,
    )


def write_bin(
    output: str,
    arrays: Sequence[np.ndarray],
    n_rb: int,
    scs: int,
    slot_start: int,
) -> Tuple[int, int, int, int]:
    nrx, ntx, fft_size = arrays[0].shape
    num_slots = len(arrays)
    n_symbols = 1
    normalized: List[np.ndarray] = []
    gains: List[float] = []
    for h in arrays:
        h_norm, gain = normalize_slot(h)
        normalized.append(h_norm)
        gains.append(gain)
    with open(output, "wb") as fp:
        fp.write(make_header(nrx, ntx, fft_size, n_rb, scs, num_slots, n_symbols))
        fp.write(struct.pack(f"<{num_slots}f", *gains))
        for slot_idx, h in enumerate(normalized):
            fp.write(struct.pack("<I", slot_start + slot_idx))
            fp.write(channel_to_c16(h))
    return nrx, ntx, fft_size, num_slots


def parse_header(header: bytes) -> Dict[str, int]:
    if len(header) != HEADER_SIZE:
        raise ValueError(f"bad .bin header size: {len(header)}")
    (
        magic,
        version,
        nrx,
        ntx,
        fft_size,
        n_rb,
        scs,
        num_slots,
        n_sc,
        offset,
        n_symbols,
        h_scale_bits,
    ) = struct.unpack(HEADER_FORMAT, header)
    if magic != MAGIC:
        raise ValueError(f"bad .bin magic: 0x{magic:08X}")
    if version not in (LEGACY_VERSION, VERSION):
        raise ValueError(f"unsupported .bin version: {version}")
    return {
        "magic": magic,
        "version": version,
        "nrx": nrx,
        "ntx": ntx,
        "fft_size": fft_size,
        "n_rb": n_rb,
        "scs": scs,
        "num_slots": num_slots,
        "n_subcarriers": n_sc,
        "subcarrier_offset": offset,
        "n_symbols": n_symbols,
        "h_scale_bits": h_scale_bits,
    }


def verify_bin(
    path: str,
    references: Optional[Sequence[str]] = None,
    kind: str = "auto",
    symbol: int = 0,
) -> Dict[str, int]:
    slot_bytes = 0
    with open(path, "rb") as fp:
        header = fp.read(HEADER_SIZE)
        info = parse_header(header)
        slot_bytes = (
            info["nrx"]
            * info["ntx"]
            * info["n_subcarriers"]
            * 4
        )
        data_offset = HEADER_SIZE
        if info["version"] == VERSION:
            gain_bytes = info["num_slots"] * 4
            gains = fp.read(gain_bytes)
            if len(gains) != gain_bytes:
                raise ValueError("truncated slot-gain array")
            data_offset += gain_bytes
        fp.seek(data_offset)
        for slot_idx in range(info["num_slots"]):
            slot_hdr = fp.read(4)
            if len(slot_hdr) != 4:
                raise ValueError(
                    f"truncated slot {slot_idx}: missing 4-byte slot number"
                )
            data = fp.read(slot_bytes)
            if len(data) != slot_bytes:
                raise ValueError(
                    f"truncated slot {slot_idx}: expected {slot_bytes} bytes, got {len(data)}"
                )
        if fp.read(1):
            raise ValueError(f"trailing bytes after {info['num_slots']} slots")

    if references:
        ref_paths, ref_kind = expand_inputs(references, kind)
        ref_arrays, ref_nrx, ref_ntx, ref_fft = load_slots(
            ref_paths, ref_kind, symbol
        )
        if len(ref_arrays) != info["num_slots"]:
            raise ValueError(
                f"reference has {len(ref_arrays)} slots, .bin has {info['num_slots']}"
            )
        if (ref_nrx, ref_ntx, ref_fft) != (
            info["nrx"],
            info["ntx"],
            info["fft_size"],
        ):
            raise ValueError(
                f"reference dimensions {(ref_nrx, ref_ntx, ref_fft)} "
                f"do not match .bin {(info['nrx'], info['ntx'], info['fft_size'])}"
            )
        with open(path, "rb") as fp:
            fp.seek(HEADER_SIZE)
            if info["version"] == VERSION:
                fp.seek(info["num_slots"] * 4, os.SEEK_CUR)
            for slot_idx, h in enumerate(ref_arrays):
                slot_hdr = fp.read(4)
                if len(slot_hdr) != 4:
                    raise ValueError(f"truncated slot {slot_idx} during data compare")
                data = fp.read(slot_bytes)
                expected = h
                if info["version"] == VERSION:
                    expected, _ = normalize_slot(
                        h, info["h_scale_bits"] or H_SCALE_BITS_DEFAULT
                    )
                if data != channel_to_c16(expected):
                    raise ValueError(f"slot {slot_idx} data does not match reference")
    return info


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    if args.verify:
        for path in args.inputs:
            info = verify_bin(path, args.reference, args.kind, args.symbol)
            print(
                f"OK {path}: RSMH v{info['version']}, {info['num_slots']} slots, "
                f"{info['nrx']}x{info['ntx']}, fft={info['fft_size']}, "
                f"n_rb={info['n_rb']}, scs={info['scs']}, "
                f"subcarriers={info['n_subcarriers']}, offset={info['subcarrier_offset']}, "
                f"h_scale_bits={info['h_scale_bits']}"
            )
        return 0

    paths, kind = expand_inputs(args.inputs, args.kind)
    arrays, nrx, ntx, fft_size = load_slots(paths, kind, args.symbol)
    output = args.output
    if output is None:
        output = "/tmp/srs_channel.bin" if kind == "srs" else "/tmp/csi_rs_channel.bin"

    _, _, _, num_slots = write_bin(
        output,
        arrays,
        args.n_rb,
        args.scs,
        args.slot_start,
    )
    print(
        f"wrote {output}: {num_slots} slots, {nrx}x{ntx}, fft={fft_size}, "
        f"n_rb={args.n_rb}, scs={args.scs}, source={len(paths)} .npy file(s), "
        f"h_scale_bits={H_SCALE_BITS_DEFAULT}"
    )
    info = verify_bin(output)
    print(
        f"verified {output}: RSMH v{info['version']}, {info['num_slots']} slots, "
        f"{info['nrx']}x{info['ntx']}, fft={info['fft_size']}, "
        f"h_scale_bits={info['h_scale_bits']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
