# SRS/CSI Channel-Based Throughput Prediction Analysis

## Purpose

This document describes the modular channel throughput prediction framework
under `gui/analysis/`. The framework reads paired OAI gNB/UE measurement CSVs
and recorded SRS or CSI-RS channel snapshots, computes Shannon,
SVD-candidate-stream, and actual-layer-selected stream capacities, and
compares those metrics with measured throughput and MCS scheduled spectral
efficiency. It also evaluates how well SVD predicted rank matches the actual
scheduled layer count.

## Input Data

The experiment root contains one gNB archive and one matching UE archive:

- `gnb_<timestamp>/gui_gnb_log_<timestamp>.csv`.
- `gnb_<timestamp>/gui_gnb_log_<timestamp>/srs_*.npy`.
- `ue_<timestamp>/gui_ue_log_<timestamp>.csv`.
- `ue_<timestamp>/gui_ue_log_<timestamp>/channel_*.npy`.

The gNB and UE folder timestamps do not need to match. The analyzer identifies
exactly one folder with the `gnb_` prefix and one with the `ue_` prefix. If
multiple folders use either prefix, it stops with an error. Each CSV may
contain multiple test rounds; `test_round` is used as the position ID.

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

4. **Noise model**
   `normalization.py` rescales the stored channel from the `c16_t` 16-bit
   fixed-point integer range to `[-1, 1)` by dividing by `32768`. The channel
   matrix is not normalized per row.

   One common noise power is selected from a reference position:

   ```text
   N0 = mean_position(raw_channel_power)
        / 10^(reference_SINR_db / 10)
   ```

   For UL/SRS, the reference SINR is normally the median recorded `ul_sinr`
   of the first position. For DL/CSI-RS, the analyzer uses the recorded UE
   `sinr_dB`. `--noise-reference-position` selects another position, while
   `--noise-power` bypasses the derivation with an explicit fixed value. The
   same `N0` is used for every second-level and position-level channel sample.

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

### Position Covariance Capacity

For each position, all second-level channel snapshots use the same raw channel
scaling and common noise power. For each measured subcarrier `k`:

```text
R(k) = (1/N) sum_n H_n(k) H_n(k)^H
C_cov(k) = sum_i log2(1 + lambda_i(R(k)) / (N_t * N0))
C_position_covariance = mean_k C_cov(k)
```

`N` is the number of second-level samples in the position. This metric uses
all second samples, before the top-throughput ratio selection, and is stored
only in the position-level output.

## Validation

The pipeline validates:

- Frobenius power versus eigenvalue sum.
- SVD capacity formula consistency.

If validation fails, `validation_report.csv` is written and the pipeline stops
before generating conclusions.

## Statistical Analysis

For each predictor:

- `rsrp_dBm`
- `max_rsrp_dBm`
- `mcs_scheduled_se`
- `shannon_capacity`
- `svd_capacity`
- `selected_stream_capacity`
- `position_covariance_capacity` (position-level only)

the framework computes:

- Pearson and Spearman correlation with throughput.
- Pearson and Spearman correlation with MCS scheduled SE.
- Linear regression `R2`, `MAE`, and `RMSE`.

It also computes SVD stream-selection accuracy against actual layers,
including confusion matrices and mean absolute error.

`mcs_scheduled_se` is computed per second-level row from the scheduled
allocation:

```text
mcs_scheduled_se = TBS / (NPRB * 12 * OFDM_symbols)
```

The position-level value is the mean of its retained second-level rows.

## Script Usage

Point `--dataset-dir` at the experiment root and pass a single `--direction`:

Run the checked-in UL mock data:

```bash
.venv/bin/python gui/analysis/main.py \
  --dataset-dir gui/analysis/test_data/ul_3rounds \
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

The common noise power is derived from the first position by default. Select a
specific reference position with:

```bash
.venv/bin/python gui/analysis/main.py \
  --dataset-dir /path/to/experiment \
  --direction ul \
  --noise-reference-position 3 \
  --output-dir gui/analysis/analysis_results
```

Alternatively, provide an explicit common noise power with `--noise-power`.
`--snr` is only a fallback reference SINR when no recorded `ul_sinr` or UE
`sinr_dB` is available.

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
pair. Metrics are plotted in two prediction groups:

- Throughput prediction:
  `second_throughput_metrics.png`, `position_throughput_metrics.png`.
- MCS scheduled-SE prediction:
  `second_mcs_se_metrics.png`, `position_mcs_se_metrics.png`.

Within each group, the selected target is plotted first, followed by the
predictor metrics:

1. The selected target, either throughput or MCS scheduled SE, with actual
   transmission streams on a right-hand dual axis as a thin red line. UL uses
   a solid red line and DL uses a dashed red line.
2. RSRP with its Pearson correlation labeled `r = xx` on the left and the
   legend on the right.
3. Maximum RSRP over all available antenna RSRP values.
4. MCS scheduled SE from TBS, allocated PRBs, and OFDM symbols, when the target
   is throughput.
5. Shannon capacity with its Pearson correlation labeled `r = xx`.
6. SVD candidate capacity.
7. Position covariance capacity, when the input frame is position-level.
8. Actual-layer selected stream capacity.

The second-level figures use real timestamps as the x-axis (rendered as a
sample index), while the position-level figures use `position_id`. Every target
panel includes the actual transmission-stream count on a thin red right-hand
axis: solid for UL and dashed for DL.

The second-level figure uses line width 1 for capacity/throughput and 0.5 for
the stream-count series. The position-level figure uses a bolder line width
(2.5) and markers for capacity metrics. Its stream-count series is drawn as a
horizontal segment at each position `i`, spanning
`[max(i - 0.5, min(position_id)), min(i + 0.5, max(position_id))]`.
Adjacent segments are connected in position order, and the right axis is
limited to `0..nb_tx_ant` with only those two ticks. Each metric has a
distinct color.

For the position-level figure, the predicted positions are selected by taking
the maximum aggregated position-level `max_rsrp_dBm` and
`shannon_capacity`, not the maximum second-level sample. The two positions are
marked with vertical dotted lines in the target and corresponding metric
subplots. In the target subplot, labeled stars annotate the target value at
both positions, and the max-capacity annotation also shows the percentage
change relative to the target at the max-RSRP position.

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
| `capacity_noise_power` | Fixed common noise power `N0` used by every capacity metric |
| `noise_reference_position` | Position used to derive `N0` |
| `noise_reference_snr_db` | Reference SINR used to derive `N0` |
| `normalization_snr_source` | Reference SINR source; retained for compatibility |
| `nb_tx_antennas` | Number of transmit antenna ports in the channel matrix |
| `max_rsrp_dBm` | Maximum valid per-antenna RSRP for the sample |
| `mcs_scheduled_se` | `TBS / (NPRB * 12 * OFDM_symbols)` for the row |

The processed position-level CSV additionally includes:

| Column | Meaning |
|---|---|
| `position_covariance_capacity` | Capacity from the mean spatial covariance across all second-level snapshots at each subcarrier, averaged over subcarriers |

ZF capacity columns, ZF SINR columns, and ZF validation metrics are not
generated.

## Outputs

The default output directory is `gui/analysis/analysis_results/` and includes:

- `processed_second_level.csv`
- `processed_position_level.csv`
- `validation_report.csv` and `validation_summary.txt`
- correlation and regression tables
- `correlation_second_level_mcs_se.csv` and
  `correlation_position_level_mcs_se.csv`
- stream-selection summaries and confusion matrices
- `figures/second_throughput_metrics.png`
- `figures/position_throughput_metrics.png`
- `figures/second_mcs_se_metrics.png`
- `figures/position_mcs_se_metrics.png`
- `figures/timeseries_second_*.png`
- `figures/timeseries_position_*.png`
- `figures/scatter_*.png`
- `final_report.md`

All four metric figures are generated automatically by the main analysis
pipeline. The additional `timeseries_*.png` files can be regenerated with
`plot_timeseries.py`.
