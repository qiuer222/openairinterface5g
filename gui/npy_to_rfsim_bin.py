#!/usr/bin/env python3
"""Convert one GUI SRS/CSI-RS snapshot to an RFSim frequency-domain .bin."""

from __future__ import annotations

import argparse
import glob
import math
import os
import struct
import sys
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np


MAGIC = int.from_bytes(b"FDCH", "little")
VERSION = 1
HEADER_FORMAT = "<IHBBHHHHHHII9I"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
AMP_SCALE_BITS = 9
TAP_LEN_DEFAULT = 32
TAP_PRUNING_DB = -20
MAX_ACTIVE_TAPS_LIMIT = 64
TAP_REGULARIZATION = 1e-2


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert one GUI channel snapshot into a single-slot RFSim "
            "frequency-domain replay file, or verify an existing file."
        )
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help="One .npy file for conversion, or .bin files with --verify.",
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
        help="SRS symbol index to store for 4D input (default: 0).",
    )
    parser.add_argument(
        "--transpose",
        action="store_true",
        help="Swap RX and TX dimensions before writing.",
    )
    parser.add_argument(
        "--subcarrier-offset",
        type=int,
        default=None,
        help=(
            "Spectral offset from the recorded estimator order to FFT-bin "
            "order. Defaults to FFT/2 for SRS and first_carrier_offset for CSI-RS."
        ),
    )
    parser.add_argument(
        "--symbols-per-slot",
        type=int,
        default=14,
        help="OFDM symbols per slot (default: 14).",
    )
    parser.add_argument(
        "--cp-length",
        type=int,
        default=None,
        help="Normal cyclic-prefix length in samples.",
    )
    parser.add_argument(
        "--cp-length0",
        type=int,
        default=None,
        help="Long cyclic-prefix length for symbol 0 where applicable.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify one or more .bin files instead of converting.",
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
            names = (
                ["srs_*.npy", "channel_*.npy"]
                if kind == "auto"
                else [f"{kind}_*.npy"]
            )
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
            raise ValueError("cannot mix SRS and CSI-RS .npy files")
    return paths, detected


def load_channel(
    path: str, kind: str, symbol: int, transpose: bool = False
) -> np.ndarray:
    arr = np.load(path, allow_pickle=False)
    if arr.ndim == 3:
        h = arr
    elif arr.ndim == 4:
        if kind != "srs" and arr.shape[2] != 1:
            raise ValueError(f"4D CSI-RS file has unexpected shape {arr.shape}: {path}")
        if symbol < 0 or symbol >= arr.shape[2]:
            raise ValueError(
                f"symbol {symbol} out of range for {path} with shape {arr.shape}"
            )
        h = arr[:, :, symbol, :]
    else:
        raise ValueError(
            f"expected channel shape (rx, tx, fft) or (rx, tx, symbol, fft), "
            f"got {arr.shape}: {path}"
        )

    if transpose:
        h = h.transpose(1, 0, 2)

    if not np.issubdtype(h.dtype, np.complexfloating):
        if h.dtype.names == ("r", "i"):
            h = h["r"].astype(np.float64) + 1j * h["i"].astype(np.float64)
        else:
            h = h.astype(np.complex128)
    return h.astype(np.complex128, copy=False)


def load_slots(
    paths: Sequence[str], kind: str, symbol: int, transpose: bool = False
) -> Tuple[List[np.ndarray], int, int, int]:
    arrays: List[np.ndarray] = []
    first_shape: Optional[Tuple[int, ...]] = None
    for path in paths:
        h = load_channel(path, kind, symbol, transpose=transpose)
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


def _fit_dense_taps(
    h: np.ndarray,
    fft_size: int,
    subcarrier_offset: int,
    tap_len: int = TAP_LEN_DEFAULT,
    regularization: float = TAP_REGULARIZATION,
) -> np.ndarray:
    """Analysis helper retained for gui/analyze_channel.py."""
    nrx, ntx, _ = h.shape
    saved_idx = np.nonzero(np.abs(h).sum(axis=(0, 1)) > 0)[0]
    if len(saved_idx) == 0:
        return np.zeros((nrx, ntx, tap_len), dtype=np.complex128)

    phys_idx = (saved_idx + subcarrier_offset) % fft_size
    delays = np.arange(tap_len, dtype=np.float64)
    basis = np.exp(-2j * np.pi * np.outer(phys_idx, delays) / fft_size)
    gram = basis.conj().T @ basis
    gram += np.eye(tap_len, dtype=np.complex128) * regularization
    rhs = basis.conj().T

    taps = np.zeros((nrx, ntx, tap_len), dtype=np.complex128)
    for rx in range(nrx):
        for tx in range(ntx):
            y = h[rx, tx, saved_idx] / float(1 << AMP_SCALE_BITS)
            if np.max(np.abs(y)) == 0:
                continue
            taps[rx, tx] = np.linalg.solve(gram, rhs @ y)
    return taps


def sparse_taps_for_slot(
    h: np.ndarray,
    fft_size: int,
    subcarrier_offset: int,
    tap_len: int = TAP_LEN_DEFAULT,
    max_active_taps: int = 8,
    prune_db: float = TAP_PRUNING_DB,
) -> List[List[List[Tuple[int, complex]]]]:
    """Analysis helper retained for gui/analyze_channel.py."""
    dense = _fit_dense_taps(
        h, fft_size, subcarrier_offset, tap_len=tap_len
    )
    threshold_lin = 10.0 ** (prune_db / 20.0)
    nrx, ntx, _ = dense.shape
    global_peak = float(np.max(np.abs(dense))) if dense.size else 0.0
    if global_peak <= 0.0:
        return [[[] for _ in range(ntx)] for _ in range(nrx)]

    result: List[List[List[Tuple[int, complex]]]] = []
    for rx in range(nrx):
        tx_paths: List[List[Tuple[int, complex]]] = []
        for tx in range(ntx):
            taps = dense[rx, tx]
            amps = np.abs(taps)
            keep = np.where(amps >= global_peak * threshold_lin)[0]
            keep = keep[np.argsort(-amps[keep])][:max_active_taps]
            keep = np.sort(keep)
            tx_paths.append(
                [(int(delay), complex(taps[delay])) for delay in keep]
            )
        result.append(tx_paths)
    return result


def full_taps_for_slot(
    h: np.ndarray,
    fft_size: int,
    subcarrier_offset: int,
) -> np.ndarray:
    """Analysis helper retained for gui/analyze_channel.py."""
    nrx, ntx, _ = h.shape
    saved = np.nonzero(np.abs(h).sum(axis=(0, 1)) > 0)[0]
    spectrum = np.zeros((nrx, ntx, fft_size), dtype=np.complex128)
    if len(saved):
        phys = (saved + subcarrier_offset) % fft_size
        spectrum[:, :, phys] = h[:, :, saved] / float(1 << AMP_SCALE_BITS)
    return np.fft.ifft(spectrum, axis=-1)


def _resolve_offset(
    requested: Optional[int], kind: str, fft_size: int, n_rb: int
) -> int:
    if requested is None:
        offset = (
            fft_size // 2
            if kind == "srs"
            else fft_size - (n_rb * 12 // 2)
        )
    else:
        offset = requested
    if offset < 0 or offset >= fft_size:
        raise ValueError(
            f"subcarrier offset {offset} must be in [0, {fft_size - 1}]"
        )
    return offset


def _resolve_cp(
    fft_size: int,
    scs: int,
    cp_length: Optional[int],
    cp_length0: Optional[int],
) -> Tuple[int, int]:
    ratio = scs / 15000.0
    mu = int(round(math.log2(ratio)))
    if not math.isclose(ratio, 2**mu, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError(f"unsupported subcarrier spacing {scs} Hz")

    default_cp = fft_size // 128 * 9
    default_cp0 = fft_size // 128 * (9 + (1 << mu))
    cp = default_cp if cp_length is None else cp_length
    cp0 = default_cp0 if cp_length0 is None else cp_length0
    if cp < 0 or cp > fft_size or cp0 < 0 or cp0 > fft_size:
        raise ValueError("cyclic-prefix lengths must be in [0, fft_size]")
    return cp, cp0


def _to_fft_order(h: np.ndarray, fft_size: int, offset: int) -> np.ndarray:
    if h.shape[-1] > fft_size:
        raise ValueError(
            f"channel has {h.shape[-1]} subcarriers, exceeds FFT size {fft_size}"
        )
    indices = np.arange(h.shape[-1], dtype=np.int64)
    physical = (indices + offset) % fft_size
    if len(np.unique(physical)) != len(physical):
        raise ValueError("subcarrier mapping produces duplicate FFT bins")

    output = np.zeros((h.shape[0], h.shape[1], fft_size), dtype=np.complex64)
    output[:, :, physical] = h.astype(np.complex64, copy=False)
    return output


def make_header(
    nrx: int,
    ntx: int,
    fft_size: int,
    symbols_per_slot: int,
    cp_length: int,
    cp_length0: int,
    n_rb: int,
    scs: int,
) -> bytes:
    return struct.pack(
        HEADER_FORMAT,
        MAGIC,
        VERSION,
        nrx,
        ntx,
        fft_size,
        symbols_per_slot,
        cp_length,
        cp_length0,
        n_rb,
        0,
        scs,
        1,
        *([0] * 9),
    )


def write_bin(
    output: str,
    h: np.ndarray,
    fft_size: int,
    symbols_per_slot: int,
    cp_length: int,
    cp_length0: int,
    n_rb: int,
    scs: int,
    subcarrier_offset: int,
) -> Dict[str, int]:
    nrx, ntx, _ = h.shape
    if not (1 <= nrx <= 64 and 1 <= ntx <= 64):
        raise ValueError("RX and TX antenna counts must be in [1, 64]")
    if not (1 <= fft_size <= 65535):
        raise ValueError("fft_size must be in [1, 65535]")
    if not (1 <= symbols_per_slot <= 64):
        raise ValueError("symbols_per_slot must be in [1, 64]")
    if not (1 <= n_rb <= 65535):
        raise ValueError("n_rb must be in [1, 65535]")
    if not (1 <= scs <= 2**32 - 1):
        raise ValueError("subcarrier spacing must be a positive uint32")
    h_fft = _to_fft_order(h, fft_size, subcarrier_offset)
    if not np.all(np.isfinite(h_fft)):
        raise ValueError("channel contains non-finite values")
    if not np.any(h_fft != 0):
        raise ValueError("channel is all zero")

    with open(output, "wb") as fp:
        fp.write(
            make_header(
                nrx,
                ntx,
                fft_size,
                symbols_per_slot,
                cp_length,
                cp_length0,
                n_rb,
                scs,
            )
        )
        fp.write(np.asarray(h_fft, dtype="<c8", order="C").tobytes(order="C"))

    return {
        "nrx": nrx,
        "ntx": ntx,
        "fft_size": fft_size,
        "symbols_per_slot": symbols_per_slot,
        "cp_length": cp_length,
        "cp_length0": cp_length0,
        "n_rb": n_rb,
        "scs": scs,
        "subcarrier_offset": subcarrier_offset,
        "num_slots": 1,
    }


def parse_header(header: bytes) -> Dict[str, int]:
    if len(header) != HEADER_SIZE:
        raise ValueError(
            f"bad .bin header size: expected {HEADER_SIZE}, got {len(header)}"
        )
    (
        magic,
        version,
        nrx,
        ntx,
        fft_size,
        symbols_per_slot,
        cp_length,
        cp_length0,
        n_rb,
        _reserved0,
        scs,
        num_slots,
        *_reserved,
    ) = struct.unpack(HEADER_FORMAT, header)
    if magic != MAGIC:
        raise ValueError(f"bad .bin magic: 0x{magic:08X}")
    if version != VERSION:
        raise ValueError(
            f"unsupported .bin version {version}, expected {VERSION}"
        )
    if (
        nrx <= 0
        or ntx <= 0
        or fft_size <= 0
        or symbols_per_slot <= 0
        or cp_length < 0
        or cp_length > fft_size
        or cp_length0 < 0
        or cp_length0 > fft_size
        or n_rb <= 0
        or scs <= 0
        or num_slots != 1
    ):
        raise ValueError("invalid FDCH header fields")
    return {
        "magic": magic,
        "version": version,
        "nrx": nrx,
        "ntx": ntx,
        "fft_size": fft_size,
        "symbols_per_slot": symbols_per_slot,
        "cp_length": cp_length,
        "cp_length0": cp_length0,
        "n_rb": n_rb,
        "scs": scs,
        "num_slots": num_slots,
    }


def verify_bin(path: str) -> Dict[str, int]:
    with open(path, "rb") as fp:
        info = parse_header(fp.read(HEADER_SIZE))
        payload_count = info["nrx"] * info["ntx"] * info["fft_size"]
        payload = fp.read(payload_count * 8)
        if len(payload) != payload_count * 8:
            raise ValueError(
                f"truncated payload: expected {payload_count * 8} bytes, "
                f"got {len(payload)}"
            )
        if fp.read(1):
            raise ValueError("trailing bytes after single-slot payload")

    h = np.frombuffer(payload, dtype="<c8")
    if not np.all(np.isfinite(h)):
        raise ValueError("payload contains non-finite values")
    if not np.any(h != 0):
        raise ValueError("payload is all zero")
    return info


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    if args.verify:
        for path in args.inputs:
            info = verify_bin(path)
            print(
                f"OK {path}: FDCH v{info['version']}, "
                f"{info['nrx']}x{info['ntx']}, fft={info['fft_size']}, "
                f"symbols={info['symbols_per_slot']}, "
                f"cp={info['cp_length']}, cp0={info['cp_length0']}, "
                f"n_rb={info['n_rb']}, scs={info['scs']}"
            )
        return 0

    paths, kind = expand_inputs(args.inputs, args.kind)
    if len(paths) != 1:
        raise ValueError(
            f"frequency-domain replay requires exactly one slot, got {len(paths)} files"
        )
    if args.n_rb <= 0:
        raise ValueError("--n-rb must be positive")
    if not 1 <= args.symbols_per_slot <= 64:
        raise ValueError("--symbols-per-slot must be in [1, 64]")

    h = load_channel(paths[0], kind, args.symbol, transpose=args.transpose)
    fft_size = h.shape[-1]
    offset = _resolve_offset(
        args.subcarrier_offset, kind, fft_size, args.n_rb
    )
    cp_length, cp_length0 = _resolve_cp(
        fft_size, args.scs, args.cp_length, args.cp_length0
    )
    output = args.output
    if output is None:
        output = (
            "/tmp/srs_channel.bin"
            if kind == "srs"
            else "/tmp/csi_rs_channel.bin"
        )

    write_bin(
        output,
        h,
        fft_size,
        args.symbols_per_slot,
        cp_length,
        cp_length0,
        args.n_rb,
        args.scs,
        offset,
    )
    info = verify_bin(output)
    print(
        f"wrote {output}: FDCH v{info['version']}, 1 slot, "
        f"{info['nrx']}x{info['ntx']}, fft={info['fft_size']}, "
        f"symbols={info['symbols_per_slot']}, cp={info['cp_length']}, "
        f"cp0={info['cp_length0']}, n_rb={info['n_rb']}, "
        f"scs={info['scs']}, offset={offset}"
        + (", transposed=rx<->tx" if args.transpose else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
