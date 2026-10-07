# SRS/CSI Channel-Based Throughput Prediction Analysis

## Purpose

This document describes the modular channel throughput prediction framework
under `gui/analysis/`. The framework reads paired OAI gNB/UE measurement CSVs
and recorded SRS or CSI-RS channel snapshots, computes Shannon,
SVD-candidate-stream, and actual-layer-selected stream capacities, and
compares those metrics with measured throughput. It also evaluates how well
SVD predicted rank matches the actual scheduled layer count.

## Input Data

The experiment root contains one gNB archive and one matching UE archive:

- `gnb_<timestamp>/gui_gnb_log_<timestamp>.csv`.
- `gnb_<timestamp>/gui_gnb_log_<timestamp>/srs_*.npy`.
- `ue_<timestamp>/gui_ue_log_<timestamp>.csv`.
- `ue_<timestamp>/gui_ue_log_<timestamp>/channel_*.npy`.

The gNB and UE suffixes must match. Each CSV may contain multiple test rounds;
`test_round` is used as the position ID.

- UL mode uses the gNB CSV as the primary record and matches SRS snapshots.
  UE rows are paired by timestamp to provide RSRP and other UE-side fields.
- DL mode uses the UE CSV as the primary record and matches CSI-RS snapshots.
  The gNB CSV is optional supplemental data.

## Processing Steps

1. **Discovery and pairing**
   `data_loader.py` discovers matching `gnb_*`/`ue_*` folders, finds each CSV
   and same-stem channel directory, and builds a `MeasurementSet`. Legacy
   round-folder layouts remain supported as a fallback.

2. **Channel parsing**
   `csi_parser.py` loads `channel_*.npy` or `srs_*.npy`. A 3D array is
   interpreted as `H(rx, tx, subcarrier)`. A 4D SRS file is reduced by taking
   the first symbol. Zero-energy subcarriers are excluded.

3. **CSV synchronization**
   Primary CSV columns retain their names. Supplemental UE columns use a `ue_`
   prefix in UL mode; supplemental gNB columns use a `gnb_` prefix in DL mode.
   Canonical fields include `throughput_mbps`, `mcs`, `layers`, and
   `rsrp_dBm`.

4. **Normalization and noise model**
   `normalization.py` rescales the stored channel from the `c16_t` 16-bit
   fixed-point integer range to `[-1, 1)` by dividing by `32768` for every
   sample. This rescale is applied for both normalization modes:

   - **Raw mode (default):** after the `32768` rescale, the channel power is
     kept as-is. All capacity metrics use the same configurable noise power,
     defaulting to `1.0` because OAI uses `1` as its CSI-RS zero-noise
     fallback. RSRP is not converted to SNR.
   - **`--snr` mode:** each channel is additionally scaled so its mean power
     over valid measured subcarriers equals `10^(snr/10)` (with the noise power
     forced back to `1.0`), removing absolute RX-gain scaling while preserving
     channel shape.

   For UL/SRS analysis, the per-row `ul_sinr` value is used as `snr` when it is
   available. `ul_sinr` is stored as dB x10 in shared memory and converted to
   dB by the Python reader. The SRS channel is therefore normalized so:

   ```text
   mean_channel_power = noise_power * 10^(ul_sinr_db / 10)
   ```

   Missing or invalid `ul_sinr` falls back to `--snr`; if that is also unset,
   the raw c16-rescaled channel power is retained. DL/CSI-RS analysis continues
   to use the global `--snr` value.

5. **Feature extraction**
   `feature_extraction.py` aggregates singular values, eigenvalues, Frobenius
   power, effective rank, and condition number across valid subcarriers.

6. **Capacity calculation**
   Capacity is computed per valid subcarrier and averaged over the valid
   measured bandwidth.

7. **Position aggregation**
   `test_round` is used as the position ID when it has more than one unique
   value; otherwise timestamp gaps are used. The top `50%` throughput samples
   are retained per position, and channel features are averaged over those
   samples. Discrete fields such as layers, RV, NDI, CQI, RI, and TPMI use the
   mode.

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

### Actual-Layer Selected Stream Capacity

For each sample, `K` is the actual UL/DL layer count from the selected primary
CSV:

```text
C_selected(K) = mean_k sum_{i=1..K} log2(1 + lambda_i(k) / (N_t * N0))
```

This uses the same transmit-power normalization as Shannon capacity:
each eigenvalue is divided by `N_t * N0`. Unlike SVD candidate capacity, `K`
is not used again in the denominator.

## Validation

The pipeline validates:

- Frobenius power versus eigenvalue sum.
- SVD capacity formula consistency.

If validation fails, `validation_report.csv` is written and the pipeline stops
before generating conclusions.

## Statistical Analysis

For each predictor:

- `rsrp_dBm`
- `shannon_capacity`
- `svd_capacity`
- `selected_stream_capacity`

the framework computes:

- Pearson correlation with throughput.
- Spearman correlation with throughput.
- Linear regression `R2`, `MAE`, and `RMSE`.

It also computes SVD stream-selection accuracy against actual layers,
including confusion matrices and mean absolute error.

## Script Usage

Point `--dataset-dir` at the experiment root and pass a single `--direction`:

```bash
python gui/analysis/tests/generate_mock_ul_3rounds.py
```

Then run UL analysis:

```bash
.venv/bin/python gui/analysis/main.py \
  --dataset-dir /tmp/oai_ul_3rounds \
  --direction ul \
  --output-dir gui/analysis/analysis_results
```

For a DL experiment, pass that root and `--direction dl`:

```bash
.venv/bin/python gui/analysis/main.py \
  --dataset-dir /path/to/dl_experiment \
  --direction dl \
  --output-dir gui/analysis/analysis_results
```

Exactly one matching gNB/UE archive pair must be present per invocation. All
`test_round` values in that pair are processed together.

Optional `--snr` supplies a fallback normalization target for UL rows missing
`ul_sinr` and the normalization target for DL CSI-RS analysis:

```bash
.venv/bin/python gui/analysis/main.py \
  --dataset-dir /path/to/dl_experiment \
  --direction dl \
  --snr 20 \
  --output-dir gui/analysis/analysis_results
```

For DL, setting `--snr` forces noise power to `1.0`; without it, raw
`c16`/32768-rescaled channel power is used. For UL, `ul_sinr` takes priority
and `--noise-power` remains the common noise power in the normalization
target.

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

Each run analyzes one direction and all test rounds in the selected archive
pair. The time-series figures use a single column of five subplots; the
second-level and position-level figures share the same layout:

1. Throughput with actual transmission streams on a right-hand dual axis as a
   thin red line. UL uses a solid red line and DL uses a dashed red line.
2. RSRP with its Pearson correlation labeled `r = xx` on the left and the
   legend on the right.
3. Shannon capacity with its Pearson correlation labeled `r = xx`.
4. SVD candidate capacity.
5. Actual-layer selected stream capacity.

The second-level figure uses real timestamps as the x-axis (rendered as a
sample index), while the position-level figure uses `position_id`. The
throughput panel always includes the actual transmission-stream count on a
thin red right-hand axis: solid for UL and dashed for DL.

The second-level figure uses line width 1 for capacity/throughput and 0.5 for
the stream-count series. The position-level figure uses a bolder line width
(2.5) and markers for capacity metrics while retaining the thin stream line.
Each metric has a distinct color.

For the position-level figure, the max-RSRP and max-Shannon-capacity positions
are marked with vertical dotted lines across the subplots. In the throughput
subplot, dots annotate the throughput value at each of those positions, and the
max-capacity position also shows the percentage enhancement of its throughput
relative to the max-RSRP position.

When `--csv` points to one processed CSV, the script splits the data by `set`
and writes one figure per round. When it is omitted, the script combines the
second-level and position-level frames into combined figures.

## Derived Capacity Columns

The processed second-level CSV includes:

| Column | Meaning |
|---|---|
| `stream_capacity_k1` ... `stream_capacity_k4` | Cumulative Shannon-style capacity using the first K singular values |
| `selected_stream_count` | Actual layer count used as K, clamped to the available channel rank |
| `selected_stream_capacity` | `stream_capacity_k<K>` selected by the actual UL/DL layer count |
| `normalization_snr_db` | SNR value used to normalize that row's channel |
| `normalization_snr_source` | `ul_sinr`, `cli_snr`, or `raw` |

ZF capacity columns, ZF SINR columns, and ZF validation metrics are not
generated.

## Outputs

The default output directory is `gui/analysis/analysis_results/` and includes:

- `processed_second_level.csv`
- `processed_position_level.csv`
- `validation_report.csv` and `validation_summary.txt`
- correlation and regression tables
- stream-selection summaries and confusion matrices
- `figures/position_metrics.png`
- `figures/timeseries_second_*.png`
- `figures/timeseries_position_*.png`
- `figures/scatter_*.png`
- `final_report.md`

`position_metrics.png` is generated automatically by the main analysis
pipeline. The additional `timeseries_*.png` files can be regenerated with
`plot_timeseries.py`.
