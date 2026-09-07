#!/usr/bin/env python3
"""Compare two same-kind OAI GUI channel .npy sources."""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from typing import Optional, Sequence

import numpy as np

try:
    from gui.channel_metrics import (
        aggregate_comparison,
        compare_channel_slots,
        save_comparison_figures,
    )
except ModuleNotFoundError:
    from channel_metrics import (
        aggregate_comparison,
        compare_channel_slots,
        save_comparison_figures,
    )

try:
    from gui.npy_to_rfsim_bin import expand_inputs, load_slots
except ModuleNotFoundError:
    from npy_to_rfsim_bin import expand_inputs, load_slots


def _expand_one(
    source: str, kind: str, symbol: int
) -> tuple:
    paths, detected = expand_inputs([source], kind)
    arrays, nrx, ntx, fft_size = load_slots(
        paths, detected, symbol, transpose=False
    )
    return paths, detected, arrays, nrx, ntx, fft_size


def _print_slot(index: int, result: dict) -> None:
    print(f"slot {index}:")
    print("  per-path:")
    for item in result["per_path"]:
        print(
            f"    rx{item['rx']} tx{item['tx']}: corr={item['correlation']:.4f} "
            f"nmse={item['nmse']:.4f} rmse={item['rmse']:.3f} "
            f"|scale|={item['best_scale_abs']:.3f} "
            f"phase={item['best_scale_phase']:.3f}"
        )
    print(
        f"  condition mean A={result['condition_a']['mean']:.2f} "
        f"B={result['condition_b']['mean']:.2f}"
    )
    for sv in result["singular_value_comparison"]:
        print(
            f"  singular value {sv['index'] + 1}: "
            f"complex_corr={sv['correlation']:.4f} "
            f"pearson={sv['scalar_correlation']:.4f} "
            f"nmse={sv['nmse']:.4f}"
        )


def _print_aggregate(aggregate: dict) -> None:
    print("aggregate:")
    print(f"  slots={aggregate['slots']}")
    for item in aggregate["per_path"]:
        corr = item["correlation"]
        nmse = item["nmse"]
        print(
            f"  path rx{item['rx']} tx{item['tx']}: corr mean={corr['mean']:.4f} "
            f"p10={corr['p10']:.4f} p90={corr['p90']:.4f} "
            f"nmse mean={nmse['mean']:.4f}"
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_a", help=".npy file, glob, or directory A")
    parser.add_argument("source_b", help=".npy file, glob, or directory B")
    parser.add_argument("--kind", choices=("auto", "srs", "csi"), default="auto")
    parser.add_argument("--symbol", type=int, default=0)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args(argv)

    a_paths, kind_a, a_arrays, _, _, _ = _expand_one(
        args.source_a, args.kind, args.symbol
    )
    b_paths, kind_b, b_arrays, _, _, _ = _expand_one(
        args.source_b, args.kind, args.symbol
    )
    if kind_a != kind_b:
        parser.error(f"inputs have different kinds: {kind_a} vs {kind_b}")

    count = min(len(a_arrays), len(b_arrays))
    if len(a_arrays) != len(b_arrays):
        print(
            f"warning: slot counts differ ({len(a_arrays)} vs {len(b_arrays)}); "
            f"comparing first {count} slots"
        )

    results = []
    for idx in range(count):
        try:
            result = compare_channel_slots(a_arrays[idx], b_arrays[idx])
        except ValueError as exc:
            print(f"slot {idx}: skipped ({exc})")
            continue
        results.append(result)
        _print_slot(idx, result)

    if not results:
        print("no comparable slots")
        return 1

    aggregate = aggregate_comparison(results)
    _print_aggregate(aggregate)

    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)
        with open(
            os.path.join(args.output_dir, "channel_comparison.json"), "w"
        ) as fp:
            json.dump(
                {
                    "a": a_paths,
                    "b": b_paths,
                    "kind": kind_a,
                    "slots": results,
                    "aggregate": aggregate,
                },
                fp,
                indent=2,
                default=_json_default,
            )
        print(f"wrote {os.path.join(args.output_dir, 'channel_comparison.json')}")
        figures = save_comparison_figures(
            a_arrays[0],
            b_arrays[0],
            os.path.join(args.output_dir, "figures"),
        )
        for figure in figures:
            print(f"wrote {figure}")
    return 0


def _json_default(value):
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.ndarray,)):
        return value.tolist()
    if isinstance(value, (complex, np.complexfloating)):
        return {"re": value.real, "im": value.imag}
    return str(value)


if __name__ == "__main__":
    sys.exit(main())
