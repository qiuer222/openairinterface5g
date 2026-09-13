# Single Channel File Analysis

This document describes `gui/analyze_channel.py`, which analyzes one CSI-RS or
SRS channel `.npy` snapshot. It explains the test commands, generated files,
figures, metrics, and single-snapshot limitations.

## 1. Test Commands

Run the console analysis:

```bash
.venv/bin/python gui/analyze_channel.py \
  gui/record/gui_gnb_log_20260907_201732/srs_20260907_201812_285240.npy
```

Generate the text report, JSON report, and figures:

```bash
.venv/bin/python gui/analyze_channel.py \
  gui/record/gui_gnb_log_20260907_201732/srs_20260907_201812_285240.npy \
  --output-dir /tmp/channel_analysis_test
```

Analyze a CSI-RS file:

```bash
.venv/bin/python gui/analyze_channel.py \
  gui/record/gui_log_20260806_222820/channel_20260806_222849_049069.npy \
  --output-dir /tmp/csi_analysis_test
```

Run the unit tests:

```bash
.venv/bin/python -m unittest \
  gui.analysis.tests.test_analyze_channel \
  gui.analysis.tests.test_channel_metrics -v
```

The script accepts:

```text
(rx, tx, subcarrier)
(rx, tx, 1, subcarrier)  # legacy singleton-symbol SRS layout
```

Complex arrays and structured arrays whose fields are `r` and `i` are
supported. The channel kind is inferred from `channel_*` or `srs_*`; use
`--kind csi` or `--kind srs` to override it.

## 2. Generated Outputs

Without `--output-dir`, the script only prints the report. With
`--output-dir`, it creates:

```text
<output-dir>/channel_analysis.txt
<output-dir>/channel_analysis.json
<output-dir>/figures/channel_magnitude.png
<output-dir>/figures/channel_phase.png
<output-dir>/figures/channel_singular_values.png
<output-dir>/figures/channel_condition_number.png
<output-dir>/figures/channel_impulse_response.png
<output-dir>/figures/channel_pdp.png
<output-dir>/figures/channel_capacity.png
```

### `channel_analysis.txt`

The same human-readable report printed to the terminal. It contains file
metadata, assumed radio configuration, array dimensions, frequency-domain
path statistics, MIMO statistics, time-domain multipath statistics, and
capacity.

### `channel_analysis.json`

Machine-readable version of the complete report. It contains all text-report
metrics and nested per-path, MIMO, rank, covariance, time-domain, and capacity
results. Non-finite values, including unavailable Rician-like K factor, are
written as JSON `null`.

### `channel_magnitude.png`

One time-domain-independent frequency-response panel per RX/TX path. The
x-axis is the active subcarrier index and the y-axis is `abs(H)`.

Use it to identify:

- path gain differences;
- flat versus frequency-selective fading;
- periodic deep fades;
- narrowband notches or nulls;
- discontinuities in the measured subcarrier range.

### `channel_phase.png`

Unwrapped phase of every RX/TX path over active subcarriers.

Use it to identify:

- linear phase progression and therefore dominant delay;
- phase discontinuities that can indicate frequency-offset or wrapping issues;
- different delay slopes between paths;
- frequency-selective phase distortion.

The quantitative phase slope and group delay are reported in the text/JSON
files rather than only visible in the figure.

### `channel_singular_values.png`

Per-subcarrier singular values of the MIMO channel matrix.

For each subcarrier:

```text
H[k] = U[k] * diag(sigma_1[k], ..., sigma_r[k]) * Vh[k]
```

The curves show how each MIMO spatial stream changes over frequency. Stable
curves indicate a relatively flat MIMO channel; large variation indicates
frequency-dependent rank, spatial selectivity, or deep fades.

### `channel_condition_number.png`

Per-subcarrier condition number, using a logarithmic y-axis:

```text
condition(k) = sigma_max(k) / max(sigma_min(k), epsilon)
```

- Near 1: balanced singular values and well-conditioned spatial layers.
- Large values: one layer is much weaker than another.
- Very large values: numerical rank loss and difficult MIMO detection.

### `channel_impulse_response.png`

Direct IDFT impulse response per RX/TX path with the unavailable FFT bins set
to zero. The delay axis is centered for visualization.

This figure shows:

- primary arrival delay;
- visible secondary paths;
- delay-domain sparsity;
- sinc-like leakage caused by incomplete measured bandwidth.

The direct IDFT is useful for visualization. It is not a full physical channel
estimate when the recording covers only part of the FFT.

### `channel_pdp.png`

Power delay profile:

```text
PDP(tau) = abs(impulse_response(tau))^2
```

All RX/TX paths are overlaid in dB. It shows relative path arrivals, the
strongest delay, and the delay range containing significant energy.

### `channel_capacity.png`

SVD capacity for every possible stream count from 1 through
`min(n_rx, n_tx)`.

This helps compare:

- single-stream robustness;
- multi-stream gain;
- whether spatial multiplexing is beneficial for the recorded channel;
- the best stream count selected by the analysis.

## 3. Text and JSON Metrics

### File and input metadata

| Metric | Meaning |
|---|---|
| `path` | Absolute input `.npy` path |
| `name` | Input filename |
| `size_bytes` | File size |
| `kind` | `csi` or `srs` |
| `timestamp` | Timestamp parsed from the filename, if present |
| `shape` | Normalized `(rx, tx, subcarrier)` dimensions |
| `n_rx` | Number of receive branches |
| `n_tx` | Number of transmit ports or spatial paths |
| `fft_size` | FFT or subcarrier axis size |
| `finite_subcarriers` | Subcarriers containing only finite samples |
| `invalid_subcarriers` | Subcarriers containing a non-finite sample |
| `active_subcarriers` | Finite subcarriers with non-zero channel energy |
| `active_first`, `active_last` | First and last active subcarrier indices |

### Analysis assumptions

| Metric | Meaning |
|---|---|
| `n_rb` | Assumed resource-block bandwidth |
| `scs_hz` | Assumed subcarrier spacing in Hz |
| `subcarrier_offset` | Physical FFT-bin offset used for IDFT and tap fitting |
| `noise_power` | Noise power used by capacity calculations |
| `snr_db` | Optional target SNR used to normalize the channel before capacity calculation |
| `transpose` | Whether RX and TX axes were swapped before analysis |

`n_rb`, `scs_hz`, and `subcarrier_offset` are not stored in `.npy`. Defaults
are `106` PRB, `30000` Hz, SRS offset `fft_size/2`, and CSI-RS offset
`fft_size - n_rb*12/2`.

### Frequency-domain path metrics

| Metric | Meaning |
|---|---|
| `mean_power` | Mean `abs(H)^2` over active subcarriers |
| `rms_amplitude` | `sqrt(mean_power)` |
| `mean_power_db` | `10*log10(mean_power)` |
| `magnitude_db` | Mean, standard deviation, min, max, p10, p50, and p90 of `20*log10(abs(H))` |
| `ripple_std_db` | Magnitude standard deviation over subcarriers; high values indicate frequency selectivity |
| `peak_to_average_db` | Peak power divided by mean power in dB |
| `peak_to_trough_db` | Difference between strongest and weakest `abs(H)` |
| `phase_slope_rad_per_sc` | Linear slope of unwrapped phase versus subcarrier |
| `group_delay_samples` | Dominant delay inferred from phase slope |
| `group_delay_ns` | Group delay converted to nanoseconds |
| `mean_adjacent_phase_rad` | Circular mean phase increment between adjacent subcarriers |
| `coherence_50_subcarriers`, `coherence_90_subcarriers` | First correlation lag where frequency autocorrelation drops below 0.5 or 0.9 |
| `coherence_50_hz`, `coherence_90_hz` | Coherence bandwidth from the corresponding lag and configured SCS |
| `rician_like_k_db` | Heuristic `abs(mean(H))^2 / mean(abs(H-mean(H))^2)` in dB |

The Rician-like K factor is useful for relative comparison only. It assumes a
meaningful dominant component and constant diffuse power across the measured
band.

### MIMO and singular-value metrics

For each singular-value index, the report includes:

| Metric | Meaning |
|---|---|
| `mean` | Mean singular value over active subcarriers |
| `std` | Standard deviation over subcarriers |
| `min`, `max` | Minimum and maximum singular values |
| `p10`, `p50`, `p90` | Percentiles over subcarriers |
| `variance_across_subcarriers` | `var(singular_value[k])` |
| `coefficient_of_variation` | `std / abs(mean)` |
| `min_over_max` | Weakest divided by strongest singular value |

Additional MIMO metrics:

| Metric | Meaning |
|---|---|
| `condition_number` stats | Distribution of `sigma_max / sigma_min` |
| `condition_threshold_ratios` | Fraction of subcarriers below condition thresholds 2, 5, 10, 20, and 100 |
| `rank` | Mean rank and histogram using -10, -20, and -30 dB singular-value thresholds |
| `rx_covariance` | Mean `H H^H` eigenvalues, rank, condition number, trace, and effective dimension |
| `tx_covariance` | Mean `H^H H` eigenvalues, rank, condition number, trace, and effective dimension |
| `stream_orthogonality` | Mean and maximum normalized correlation between spatial columns and rows |

`effective_dimension` is:

```text
(sum(eigenvalues))^2 / sum(eigenvalues^2)
```

It indicates how many covariance eigenvalues contribute meaningfully.

### Time-domain metrics

Each path contains direct-IDFT and sparse-tap statistics.

| Metric | Meaning |
|---|---|
| `subcarrier_offset` | Offset used to map saved subcarriers into FFT bins |
| `tap_len` | Number of candidate delays used by sparse fitting |
| `prune_db` | Relative threshold used to discard weak taps |
| `max_active_taps` | Maximum retained sparse taps per path |
| `peak_delay_samples`, `peak_delay_ns` | Delay with highest PDP |
| `mean_delay_samples`, `mean_delay_ns` | Power-weighted first moment of PDP |
| `rms_delay_spread_samples`, `rms_delay_spread_ns` | Square root of the power-weighted second central moment |
| `max_excess_delay_samples`, `max_excess_delay_ns` | Delay range containing significant taps |
| `significant_taps` | Number of taps above the -30 dB relative PDP threshold |
| `delays` | Retained sparse-tap delays |
| `tap_magnitudes`, `tap_powers` | Complex-tap magnitude and power |
| `fit_nmse` | Sparse reconstruction normalized MSE |
| `fit_rmse` | Sparse reconstruction RMSE |

The direct-IDFT report is useful for shape inspection but contains leakage
because unmeasured FFT bins are zero-filled. The sparse-tap report estimates
delays over the measured band and includes a reconstruction error.

### Capacity metrics

| Metric | Meaning |
|---|---|
| `capacity_per_streams` | SVD capacity for K = 1 through available rank |
| `best_streams` | K with maximum SVD capacity |
| `best_svd_capacity` | Capacity at `best_streams` |
| `shannon_capacity` | Sum of all available spatial eigenmodes |

For K streams:

```text
C(K) = mean_k sum_i=1..K log2(1 + lambda_i[k] / (K * noise_power))
```

The eigenvectors are obtained from the normalized channel. Without
`--snr-db`, the channel is scaled from fixed-point units by `1/32768`.
With `--snr-db`, mean channel power is normalized to
`noise_power * 10^(snr_db/10)` before calculating capacity.

## 4. Single-Snapshot Limitations

A single `.npy` snapshot cannot provide:

- Doppler shift or Doppler spectrum;
- temporal correlation or coherence time;
- UE speed or mobility classification;
- fade duration or level-crossing rate;
- long-term average RSRP/SINR;
- causal frequency-selective fading statistics over time.

Those metrics require a sequence of channel snapshots with known time or slot
spacing.
