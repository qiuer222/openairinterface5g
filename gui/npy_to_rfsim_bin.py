#!/usr/bin/env python3
"""Convert GUI SRS/CSI-RS snapshots to sparse RFSim time-domain replay files.

The GUI saves the same fixed-point channel data as numpy complex arrays:

  CSI-RS: channel_*.npy, shape (n_rx, n_tx, fft_size)
  SRS:    srs_*.npy,     shape (n_rx, n_tx, n_symbols, fft_size)

This script writes those snapshots into the binary format already consumed by
radio/rfsimulator/apply_channel_fd.c.  For SRS arrays with multiple symbols the
first symbol is used, matching the original dump_srs_channel() behavior.

The converter estimates a compact time-domain impulse response per rx/tx path,
prunes weak taps, and writes an RSMH v3 file containing sparse ``(delay, tap)``
records. RFSim convolves these taps directly with the received sample stream.
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
FORMAT_VERSION = 3
AMP_SCALE_BITS = 9  # OAI reference amplitude: AMP == 1 << AMP_SCALE_BITS
TAP_SCALE_BITS_DEFAULT = 15
TAP_LEN_DEFAULT = 32
MAX_ACTIVE_TAPS_DEFAULT = 8
MAX_ACTIVE_TAPS_LIMIT = 64
TAP_PRUNING_DB = -20  # keep taps within 20 dB of the strongest recorded tap
TAP_REGULARIZATION = 1e-2
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
        "--transpose",
        action="store_true",
        help=(
            "Swap the first two H dimensions (rx <-> tx) before writing. "
            "Use this to convert a gNB-recorded SRS uplink channel into the "
            "reciprocal downlink channel for the UE replay side."
        ),
    )
    parser.add_argument(
        "--subcarrier-offset",
        type=int,
        default=-1,
        help=(
            "Spectral offset used while fitting taps. Default: FFT/2 for SRS "
            "recordings, first carrier offset for CSI-RS."
        ),
    )
    parser.add_argument(
        "--tap-len",
        type=int,
        default=TAP_LEN_DEFAULT,
        help=f"Number of time-domain taps estimated per path (default: {TAP_LEN_DEFAULT}).",
    )
    parser.add_argument(
        "--max-active-taps",
        type=int,
        default=MAX_ACTIVE_TAPS_DEFAULT,
        help=(
            "Maximum sparse taps kept per rx/tx path "
            f"(default: {MAX_ACTIVE_TAPS_DEFAULT}, max: {MAX_ACTIVE_TAPS_LIMIT})."
        ),
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify .bin header/slot layout instead of converting.",
    )
    parser.add_argument(
        "--reference",
        nargs="+",
        help="Reserved for compatibility; sparse tap files are verified structurally.",
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


def load_channel(
    path: str, kind: str, symbol: int, transpose: bool = False
) -> np.ndarray:
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

    if transpose:
        if h.ndim != 3:
            raise ValueError(
                f"failed to transpose channel with shape {h.shape}: {path}"
            )
        h = h.transpose(1, 0, 2)

    if h.ndim != 3:
        raise ValueError(f"failed to normalize channel shape from {h.shape}: {path}")
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
    """Estimate a compact time-domain impulse response from frequency-domain H.

    The fit is done only over measured (nonzero) subcarriers, using a small
    ridge regularisation to avoid ill-conditioning when the recorded channel is
    almost flat (a plain IDFT would generate a long sinc-like response because
    out-of-band bins are unknown/zero).

    Returns dense taps shaped (nrx, ntx, tap_len), in the same linear scale
    as ``h / AMP``.
    """
    nrx, ntx, n_sc = h.shape
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
    max_active_taps: int = MAX_ACTIVE_TAPS_DEFAULT,
    prune_db: float = TAP_PRUNING_DB,
) -> List[List[List[Tuple[int, complex]]]]:
    """Return sparse per-path taps ``[rx][tx][(delay, tap)]``."""
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
            # Keep the strongest taps first, capped to keep runtime bounded.
            keep = keep[np.argsort(-amps[keep])][:max_active_taps]
            keep = np.sort(keep)
            tx_paths.append([(int(delay), complex(taps[delay])) for delay in keep])
        result.append(tx_paths)
    return result


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
    max_active_taps: int,
) -> bytes:
    return struct.pack(
        HEADER_FORMAT,
        MAGIC,
        FORMAT_VERSION,
        nrx,
        ntx,
        fft_size,
        n_rb,
        scs,
        num_slots,
        max_active_taps,
        0,
        1,
        TAP_SCALE_BITS_DEFAULT,
    )


def write_sparse_taps(
    output: str,
    arrays: Sequence[np.ndarray],
    fft_size: int,
    n_rb: int,
    scs: int,
    slot_start: int,
    subcarrier_offset: int,
    tap_len: int,
    max_active_taps: int,
) -> Tuple[int, int, int, int]:
    """Write the current RSMH format: sparse time-domain taps per path."""
    nrx, ntx, _ = arrays[0].shape
    num_slots = len(arrays)
    sparse_arrays = [
        sparse_taps_for_slot(
            h,
            fft_size,
            subcarrier_offset,
            tap_len=tap_len,
            max_active_taps=max_active_taps,
        )
        for h in arrays
    ]

    with open(output, "wb") as fp:
        fp.write(make_header(
            nrx,
            ntx,
            fft_size,
            n_rb,
            scs,
            num_slots,
            max_active_taps,
        ))
        for slot_idx, paths in enumerate(sparse_arrays):
            fp.write(struct.pack("<I", slot_start + slot_idx))
            for rx in range(nrx):
                for tx in range(ntx):
                    active = paths[rx][tx]
                    fp.write(struct.pack("<H", len(active)))
                    for delay, tap in active:
                        fp.write(struct.pack("<H", delay))
                        scaled = tap * (1 << TAP_SCALE_BITS_DEFAULT)
                        fp.write(channel_to_c16(np.asarray([scaled])))
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
    if version != FORMAT_VERSION:
        raise ValueError(
            f"unsupported .bin version {version}, expected {FORMAT_VERSION}"
        )
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
    transpose: bool = False,
) -> Dict[str, int]:
    with open(path, "rb") as fp:
        header = fp.read(HEADER_SIZE)
        info = parse_header(header)
        for slot_idx in range(info["num_slots"]):
            if len(fp.read(4)) != 4:
                raise ValueError(
                    f"truncated slot {slot_idx}: missing 4-byte slot number"
                )
            for _ in range(info["nrx"] * info["ntx"]):
                count = fp.read(2)
                if len(count) != 2:
                    raise ValueError(
                        f"truncated slot {slot_idx}: missing tap count"
                    )
                count = struct.unpack("<H", count)[0]
                if count > info["n_subcarriers"]:
                    raise ValueError(
                        f"slot {slot_idx} path has {count} taps, "
                        f"header allows {info['n_subcarriers']}"
                    )
                for _ in range(count):
                    if len(fp.read(2 + 4)) != 6:
                        raise ValueError(
                            f"truncated slot {slot_idx}: missing tap record"
                        )
        if fp.read(1):
            raise ValueError(f"trailing bytes after {info['num_slots']} slots")

    if references:
        # Frequency-domain references require the same sparse estimation that
        # was used to create the file. Callers can rely on structural verify;
        # a comparison needs tap_len/max_active_taps, which are not recorded.
        raise ValueError(
            "--reference comparison is not available for sparse tap files"
        )
    return info


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    if args.verify:
        for path in args.inputs:
            info = verify_bin(
                path,
                args.reference,
                args.kind,
                args.symbol,
                args.transpose,
            )
            print(
                f"OK {path}: RSMH v{info['version']}, {info['num_slots']} slots, "
                f"{info['nrx']}x{info['ntx']}, fft={info['fft_size']}, "
                f"n_rb={info['n_rb']}, scs={info['scs']}, "
                f"subcarriers={info['n_subcarriers']}, offset={info['subcarrier_offset']}, "
                f"h_scale_bits={info['h_scale_bits']}"
            )
        return 0

    paths, kind = expand_inputs(args.inputs, args.kind)
    arrays, nrx, ntx, fft_size = load_slots(
        paths, kind, args.symbol, transpose=args.transpose
    )
    output = args.output
    if output is None:
        output = "/tmp/srs_channel.bin" if kind == "srs" else "/tmp/csi_rs_channel.bin"
    if not (1 <= args.max_active_taps <= MAX_ACTIVE_TAPS_LIMIT):
        raise ValueError(
            f"--max-active-taps must be between 1 and {MAX_ACTIVE_TAPS_LIMIT}"
        )

    # OAI's SRS channel estimator returns H in a coordinate system shifted by
    # half the FFT relative to the RX FFT bins used by the RFSim replay path.
    # Store that shift in the header so the C replay engine applies H[k] to
    # RX-FFT bin (k + fft_size/2) % fft_size.
    if args.subcarrier_offset >= 0:
        subcarrier_offset = args.subcarrier_offset
    else:
        if kind == "srs":
            subcarrier_offset = fft_size // 2
        else:
            subcarrier_offset = fft_size - (args.n_rb * 12 // 2)
    _, _, _, num_slots = write_sparse_taps(
        output,
        arrays,
        fft_size,
        args.n_rb,
        args.scs,
        args.slot_start,
        subcarrier_offset,
        args.tap_len,
        args.max_active_taps,
    )
    print(
        f"wrote {output}: {num_slots} slots, {nrx}x{ntx}, fft={fft_size}, "
        f"n_rb={args.n_rb}, scs={args.scs}, source={len(paths)} .npy file(s), "
        f"tap_scale_bits={TAP_SCALE_BITS_DEFAULT}"
        + (", transposed=rx<->tx" if args.transpose else "")
        + f", ls_taps={args.tap_len}, max_active_taps={args.max_active_taps}"
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
