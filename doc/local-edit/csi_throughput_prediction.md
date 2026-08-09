# CSI-Based Throughput Prediction Analysis

## Purpose

This document describes the modular CSI throughput prediction framework under
`gui/analysis/`. The framework reads OAI UE/gNB measurement CSVs and recorded
CSI-RS channel snapshots, computes Shannon/SVD/ZF+MMSE capacity metrics, and
compares those metrics with measured throughput. It also evaluates how well
SVD and ZF predicted rank matches the actual scheduled layer count.

## Input Data

Each measurement round contains:

- A UE GUI CSV, for example `gui_ue_log_20260807_145628.csv`.
- A same-stem directory with `channel_*.npy` CSI-RS files.
- A gNB GUI CSV for UL rounds, containing `throughput_mbps`, `ul_mcs`, and
  `ul_layers`.

For DL rounds, throughput, MCS, and layers are read directly from the UE CSV.
For UL rounds, the gNB values are paired to UE CSI/RSRP rows by nearest
timestamp.

## Processing Steps

1. **Discovery and pairing**
   `data_loader.py` discovers UE CSVs with same-stem CSI directories, finds the
   matching gNB CSV, and builds a `MeasurementSet`.

2. **CSI parsing**
   `csi_parser.py` loads `channel_*.npy` files. A 3D array is interpreted as
   `H(rx, tx, subcarrier)`. A 4D SRS file is reduced by taking the first symbol.
   Subcarriers with zero channel energy are excluded from all calculations.

3. **Normalization and noise model**
   `normalization.py` intentionally preserves raw OAI `c16`/FFT channel power.
   All capacity metrics use the same configurable noise power, defaulting to
   `1.0` because OAI uses `1` as its CSI-RS zero-noise fallback. RSRP is not
   converted to SNR.

4. **Feature extraction**
   `feature_extraction.py` aggregates singular values, eigenvalues, Frobenius
   power, effective rank, and condition number across valid subcarriers.

5. **Capacity calculation**
   Capacity is computed per valid subcarrier and averaged over the valid
   CSI-RS bandwidth.

6. **Position aggregation**
   `test_round` is used as the position ID when it has more than one unique
   value; otherwise timestamp gaps are used. The top `50%` throughput samples
   are retained per position, and CSI/features are averaged over the same
   retained samples.

## Capacity Formulas

Let `H(k)` be the channel matrix on valid subcarrier `k`, `N_t` the number of
transmit antennas, and `N0` the common noise power. Let
`lambda_1 >= ... >= lambda_R` be the eigenvalues of `H(k) H(k)^H`.

### Shannon Capacity

```text
C_shannon = mean_k sum_i log2(1 + lambda_i(k) / (N_t * N0))
```

### SVD Precoding Capacity

For each candidate stream count `K = 1..4`:

```text
C_svd(K) = mean_k sum_{i=1..K} log2(1 + lambda_i(k) / (K * N0))
```

The best stream count is the `K` that maximizes `C_svd(K)`.

### ZF Precoding + MMSE Receiver Capacity

For each candidate `K`:

1. Build a ZF precoder `W` from the strongest `K` right singular vectors and
   singular values.
2. Normalize `W` so that `trace(W W^H) = 1`.
3. Compute the equivalent channel `H_eq = H W`.
4. Compute the MMSE receiver and per-stream SINRs.
5. Compute `C_zf(K) = sum_i log2(1 + SINR_i)` and average over valid
   subcarriers.

The best stream count is the `K` that maximizes `C_zf(K)`.

## Validation

The pipeline validates:

- Frobenius power versus eigenvalue sum.
- SVD capacity formula consistency.
- ZF precoder trace equal to `1`.
- Nonnegative SINR values.

If validation fails, `validation_report.csv` is written and the pipeline stops
before generating conclusions.

## Statistical Analysis

For each predictor:

- `rsrp_dBm`
- `shannon_capacity`
- `svd_capacity`
- `zf_capacity`

the framework computes:

- Pearson correlation with throughput.
- Spearman correlation with throughput.
- Linear regression `R2`, `MAE`, and `RMSE`.

It also computes SVD and ZF stream-selection accuracy against actual layers,
including confusion matrices and mean absolute error.

## Script Usage

Run one test round per invocation. Point `--dataset-dir` at the round folder
and pass a single `--direction`:

```bash
.venv/bin/python gui/analysis/main.py \
  --dataset-dir /media/qiuer/BEA6-BBCE/0807/round1 \
  --direction ul \
  --output-dir gui/analysis/analysis_results
```

For a DL round, pass that round's folder and `--direction dl`:

```bash
.venv/bin/python gui/analysis/main.py \
  --dataset-dir /media/qiuer/BEA6-BBCE/0807/round2 \
  --direction dl \
  --output-dir gui/analysis/analysis_results
```

The UE CSV, its same-stem CSI directory, and the gNB CSV for UL rounds are
expected inside the same round folder.

Plot time series directly from existing processed CSVs:

```bash
.venv/bin/python gui/analysis/plot_timeseries.py \
  --csv gui/analysis/analysis_results/processed_second_level.csv \
  --output-dir gui/analysis/analysis_results

.venv/bin/python gui/analysis/plot_timeseries.py \
  --csv gui/analysis/analysis_results/processed_position_level.csv \
  --output-dir gui/analysis/analysis_results
```

The plotting script can also regenerate the combined figures without a `--csv`
argument:

```bash
.venv/bin/python gui/analysis/plot_timeseries.py \
  --second-level gui/analysis/analysis_results/processed_second_level.csv \
  --position-level gui/analysis/analysis_results/processed_position_level.csv \
  --output-dir gui/analysis/analysis_results
```

## Time-Series Plot Details

The time-series figures are organized by direction. For each direction, the
figures contain five subplots:

1. Throughput and the actual scheduled layer count, using a left/right dual
   axis. The layer series uses the UL layer from the gNB CSV or the layer
   column from the UE CSV for DL.
2. RSRP with its Pearson correlation to throughput shown on the left and the
   legend on the right.
3. Shannon capacity with its Pearson correlation shown on the left and the
   legend on the right.
4. SVD capacity and SVD selected stream count, using a dual axis.
5. ZF capacity and ZF selected stream count, using a dual axis.

Capacity and throughput lines use line width 1; layer/stream-count lines use
line width 0.5. Every metric has a distinct line color, and position-level
plots additionally use a distinct marker per metric.

When `--csv` points to one processed CSV, the script splits the data by `set`
and writes one figure per round. When it is omitted, the script combines the
second-level and position-level frames into combined figures.

## Outputs

The default output directory is `gui/analysis/analysis_results/` and includes:

- `processed_second_level.csv`
- `processed_position_level.csv`
- `validation_report.csv` and `validation_summary.txt`
- correlation and regression tables
- stream-selection summaries and confusion matrices
- `figures/timeseries_second_*.png`
- `figures/timeseries_position_*.png`
- `figures/scatter_*.png`
- `final_report.md`

The generated result directory is ignored by Git.
