#!/usr/bin/env python3
"""Plot time series from existing processed analysis CSVs."""

from __future__ import annotations

import argparse
import os
import re
import sys

import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(
        0,
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    )

from gui.analysis.analysis.visualization import (
    plot_timeseries,
    plot_timeseries_frame,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--second-level",
        default="gui/analysis/analysis_results/processed_second_level.csv",
    )
    parser.add_argument(
        "--position-level",
        default="gui/analysis/analysis_results/processed_position_level.csv",
    )
    parser.add_argument(
        "--csv",
        help="single processed CSV; split into per-round figures",
    )
    parser.add_argument(
        "--output-dir",
        default="gui/analysis/analysis_results",
    )
    args = parser.parse_args()

    if args.csv:
        frame = pd.read_csv(args.csv)
        mode = "second" if "timestamp" in frame.columns else "position"
        x_column = "timestamp" if mode == "second" else "position_id"
        for set_name in sorted(frame["set"].dropna().unique()):
            safe_set = re.sub(r"[^A-Za-z0-9_-]+", "_", str(set_name))
            subset = frame[frame["set"] == set_name]
            path = plot_timeseries_frame(
                subset,
                args.output_dir,
                f"timeseries_{mode}_{safe_set}.png",
                f"Per-{mode.capitalize()} Time Series - {set_name}",
                x_column=x_column,
            )
            if path:
                print(f"wrote {path}")
        return

    second = pd.read_csv(args.second_level)
    position = pd.read_csv(args.position_level)
    paths = plot_timeseries(second, position, args.output_dir)
    for path in paths:
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
