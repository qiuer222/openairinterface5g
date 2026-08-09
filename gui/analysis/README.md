# CSI-Based Throughput Prediction Framework

This package analyzes OAI CSI-RS channel snapshots together with UE/gNB GUI
measurement CSVs. It computes Shannon, SVD-precoding, and ZF+MMSE capacities
from the raw OAI channel, keeps RSRP as an independent baseline, and evaluates
which CSI-derived metric best predicts measured throughput.

## Run

The CLI processes one test round per invocation. Point `--dataset-dir` at the
round folder and pass one `--direction` value:

```bash
python gui/analysis/main.py \
  --dataset-dir /media/qiuer/BEA6-BBCE/0807/round1 \
  --direction ul \
  --output-dir gui/analysis/analysis_results
```

```bash
python gui/analysis/main.py \
  --dataset-dir /media/qiuer/BEA6-BBCE/0807/round2 \
  --direction dl \
  --output-dir gui/analysis/analysis_results
```

For a UL round, the UE CSV, the same-stem CSI directory, and the gNB CSV are
expected inside the same round folder.

To regenerate the time-series figures directly from existing processed CSVs
without rerunning the data-processing pipeline:

```bash
python gui/analysis/plot_timeseries.py \
  --second-level gui/analysis/analysis_results/processed_second_level.csv \
  --position-level gui/analysis/analysis_results/processed_position_level.csv \
  --output-dir gui/analysis/analysis_results
```

To plot one processed CSV at a time and split it into per-round figures:

```bash
python gui/analysis/plot_timeseries.py --csv gui/analysis/analysis_results/processed_second_level.csv
python gui/analysis/plot_timeseries.py --csv gui/analysis/analysis_results/processed_position_level.csv
```

## Key Options

- `--noise-power`: common fixed noise power, default `1.0` matching OAI's
  CSI-RS zero-noise fallback.
- `--snr`: optional target SNR in dB. When set, each channel is scaled so its
  mean power over valid CSI-RS subcarriers equals `10^(snr/10)` and the noise
  power is forced to `1.0`, removing absolute RX-gain scaling while preserving
  channel shape. When unset, the raw channel power is kept as-is.

The stored channel originates from `c16_t` fixed-point samples. Both modes
divide the channel by `32768` first, converting the 16-bit integer range to
`[-1, 1)`.
- `--top-ratio`: retained highest-throughput fraction per position, default
  `0.5`.
- `--pair-tolerance-ms`: UL gNB/UE timestamp pairing tolerance, default `2000`.
- `--csi-tolerance-ms`: CSI timestamp matching tolerance, default `200`.
- `--position-gap-s`: timestamp-gap segmentation threshold used when the CSV
  does not have a multi-value `test_round` column.
- `--direction`: required per-call direction, either `ul` or `dl`.

## Outputs

The pipeline writes processed second-level and position-level CSVs, validation
reports, correlation and regression tables, stream-selection summaries and
confusion matrices, publication figures, and a Markdown final report under the
configured output directory.
