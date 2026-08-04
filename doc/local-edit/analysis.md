# GUI Output Capacity and Correlation Analysis

## 1. Purpose

This document explains the offline analysis code under `gui/analysis/`.
It uses the CSV file and recorded CSI channel matrices produced by
`gui/oai_perf_monitor.py` to compute theoretical link-capacity metrics,
correlation coefficients, throughput-prediction accuracy, and stream-selection
accuracy.

## 2. Files

| File | Responsibility |
|---|---|
| `gui/analysis/analyze_correlation.py` | CLI entry point and report generation |
| `gui/analysis/capacity.py` | Average SVD capacity and best-stream SVD capacity |
| `gui/analysis/zf_capacity.py` | Best-stream zero-forcing capacity |
| `gui/analysis/scaling.py` | OAI CSI scaling analysis and channel normalization |
| `gui/analysis/prediction.py` | Correlation, regression, and stream-selection evaluation |
| `gui/analysis/plotting.py` | Time-series, scatter, and predicted-vs-measured figures |

Run from the repository root:

```bash
.venv/bin/python gui/analysis/analyze_correlation.py \
  --csv gui/record/gui_log_20260802_223124.csv \
  --csi gui/record/gui_log_20260802_223124 \
  --snr 10 \
  --output-dir analysis_results
```

The CSI directory is normally the CSV filename without the `.csv` extension.
If only `--csv` is provided, the script derives the CSI directory from it. If
only `--csi` is provided, the script derives the CSV path by appending `.csv`.
Without either option, the script selects the latest complete GUI record under
`gui/record/`.

The newer `gui/analysis/channel_analysis.py` pipeline writes its output files to
`gui/channel_analysis_results/` by default.

## 3. OAI Channel Scaling and Normalization

The channel saved by the GUI is not a physical channel coefficient. It is the
OAI `csi_rs_estimated_channel_freq` array after CSI-RS least-squares estimation
and interpolation, exported to `/dev/shm/csi_rs_channel` and then read as
`c16_t` by `gui/csi_reader.py`.

Important scaling factors identified in the OAI source:

- `rxdataF` is produced by `dft()` without explicit `1/N` normalization.
- RSRP conversion uses `SQ15_SQUARED_NORM_FACTOR_DB = 90.309 dB`, the RF gain,
  and `10 * log10(fft_size)`.
- The generated CSI-RS signal uses `AMP = 512`, and the LS estimate is computed
  with `c16MulConjShift(..., csi_rs_generated_signal_bits)`.
- The exact RX gain/AGC value is not stored in the GUI CSV.

Therefore, before capacity calculation, `gui/analysis/scaling.py` normalizes
each channel so that the mean squared norm over valid subcarriers equals the
configured SNR:

```text
P_target = 10^(SNR_dB / 10)
mean_power = mean over valid subcarriers of ||H(k)||^2
H_norm = H * sqrt(P_target / mean_power)
```

This preserves channel shape while removing unknown fixed-point/FFT/RF gain
scaling. `RSRP` is kept as an independent predictor.

## 4. Capacity Metrics

Let `H(k)` be the normalized channel matrix for subcarrier `k`, with
`Nr = rows` and `Nt = columns`. Let the SVD be:

```text
H(k) = U(k) Σ(k) V^H(k)
```

with singular values:

```text
σ_1(k) >= σ_2(k) >= ... >= σ_min(k)
```

All metrics use the same assumed total signal-to-noise ratio:

```text
SNR = 10^(SNR_dB / 10)
```

### 4.1 Average SVD Capacity

Equal power is allocated to all available spatial streams:

```text
Ns = min(Nr, Nt)
C_avg = mean over valid k of sum_i log2(1 + (SNR / Ns) * σ_i(k)^2)
```

### 4.2 Best-Stream SVD Capacity

For each possible stream count `r = 1 .. min(Nr, Nt)`:

```text
C_svd(r) = mean over valid k of sum_{i=1}^r log2(1 + (SNR / r) * σ_i(k)^2)
```

The stream count maximizing `C_svd(r)` is selected:

```text
best_svd_capacity = max_r C_svd(r)
optimal_stream_number_svd = argmax_r C_svd(r)
```

### 4.3 Best-Stream ZF Capacity

For `r` streams, the ZF precoder is built from the first `r` singular vectors.
With total transmit power normalized and equal power per stream, the ZF
post-processing SINR is:

```text
trace_inverse(k) = sum_{i=1}^r 1 / σ_i(k)^2
SINR_zf(k) = SNR / (r * trace_inverse(k))
```

The per-stream capacity is identical for the ZF-equalized streams:

```text
C_zf(r) = mean over valid k of r * log2(1 + SINR_zf(k))
```

The stream count maximizing `C_zf(r)` is selected:

```text
best_zf_capacity = max_r C_zf(r)
optimal_stream_number_zf = argmax_r C_zf(r)
```

## 5. Correlation Coefficients

### 5.1 Pearson Correlation

For measured throughput `y` and a predictor `x`:

```text
r_pearson = sum((x_i - x_mean) * (y_i - y_mean)) /
            (sqrt(sum((x_i - x_mean)^2)) * sqrt(sum((y_i - y_mean)^2)))
```

Pearson measures linear association. It is undefined when either variable has
zero variance.

### 5.2 Spearman Correlation

Spearman is Pearson applied to ranks:

```text
r_spearman = r_pearson(rank(x), rank(y))
```

It measures monotonic association and is more robust to outliers than Pearson.

## 6. Throughput Prediction Metrics

For each predictor, the analysis fits:

- linear regression: `y_pred = a * x + b`
- polynomial regression of degree 2: `y_pred = a0 + a1*x + a2*x^2`

Accuracy is evaluated with:

```text
RMSE = sqrt(mean((y - y_pred)^2))
MAE  = mean(abs(y - y_pred))
MAPE = 100 * mean(abs((y - y_pred) / y)), computed over y > 0
R^2  = 1 - sum((y - y_pred)^2) / sum((y - y_mean)^2)
```

Predictors are ranked by RMSE and R².

## 7. Stream Selection

The CSV contains the scheduled `layers` value, so it is used as the measured
stream-count ground truth. The report compares:

- `optimal_stream_number_svd`
- `optimal_stream_number_zf`

and reports accuracy, confusion matrices, mean absolute stream-number error,
error distributions, and accuracy conditioned on RSRP / condition-number
ranges.

## 8. Important Finding on Current GUI Records

In the current `gui/record/*` outputs, every recorded `channel_*.npy` snapshot
is identical, with zero maximum difference between snapshots. RSRP and the
channel condition number are also constant.

Consequences:

- SVD/ZF capacity metrics have zero variance.
- Pearson/Spearman correlation with throughput cannot be meaningfully
  computed from those channel metrics.
- Throughput changes in the analyzed records are scheduler/MCS driven rather
  than CSI driven.
- Stream-selection accuracy is 100% because both the CSV `layers` and the
  predicted stream counts are 2, but this is not a strong validation.

The analysis pipeline is ready to produce useful rankings once new records
contain varying channel estimates, RSRP, or condition number.
