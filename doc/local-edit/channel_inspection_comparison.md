# Channel Inspection and Comparison

This document describes the numerical inspection and comparison tooling added
for GUI SRS/CSI-RS channel `.npy` files. It complements the replay workflow in
[channel_record_replay.md](channel_record_replay.md).

## Overview

Three commands are provided:

| Command | Purpose |
|---------|---------|
| `gui/analyze_channel.py` | Analyze one CSI-RS/SRS `.npy` snapshot in frequency, time, MIMO, and capacity domains |
| `gui/npy_to_rfsim_bin.py` | Convert one `.npy` slot to frequency-domain `FDCH` replay format |
| `gui/compare_channels.py` | Compare two same-kind channel recordings slot-by-slot |

The common metrics/plotting code lives in `gui/channel_metrics.py`.

## Single-channel analysis

`gui/analyze_channel.py` accepts one channel filename:

```bash
.venv/bin/python gui/analyze_channel.py \
  gui/record/gui_gnb_log_20260907_201732/srs_20260907_201812_285240.npy
```

It supports `(rx, tx, subcarrier)` arrays and the current SRS-compatible
`(rx, tx, 1, subcarrier)` singleton-symbol layout. A report is always printed
to the console. Add `--output-dir` to also write JSON, text, and figures:

```bash
.venv/bin/python gui/analyze_channel.py \
  gui/record/gui_gnb_log_20260907_201732/srs_20260907_201812_285240.npy \
  --output-dir /tmp/channel_analysis
```

The report includes:

- input dimensions, active/invalid subcarrier counts, and assumptions;
- per-RX/TX path power, magnitude ripple, peak-to-average ratio, phase slope,
  group delay, frequency autocorrelation, coherence bandwidth, and a
  Rician-like K factor;
- per-subcarrier singular values, variance, coefficient of variation,
  condition number, rank histograms, RX/TX covariance eigenvalues, and stream
  orthogonality;
- direct IDFT impulse response and regularized sparse-tap reconstruction with
  delay, RMS delay spread, maximum excess delay, and fit error;
- SVD capacity for every stream count, best stream count, and Shannon capacity.

`n_rb`, `scs`, and FFT offset are not stored in `.npy`; defaults are
`106 PRB`, `30 kHz`, SRS offset `fft_size/2`, and CSI-RS offset
`fft_size - n_rb*12/2`. Override them with `--n-rb`, `--scs`, and
`--subcarrier-offset`.

Doppler, temporal correlation, and mobility cannot be derived from one
snapshot; the script reports them as unavailable.

See [channel_single_file_analysis.md](channel_single_file_analysis.md) for the
full command examples, generated-file descriptions, figure interpretation, and
metric definitions.

## Converter verification

Every conversion writes one slot of complex64 H data in physical FFT-bin order
and then verifies the resulting header and payload. The converter does not
perform sparse-tap analysis. Use `gui/analyze_channel.py` on the source `.npy`
when detailed channel metrics or figures are needed.

```bash
.venv/bin/python gui/npy_to_rfsim_bin.py \
  --kind srs --n-rb 106 --scs 30000 \
  gui/record/gnb_log_20260806_212400/srs_20260806_212401_061474.npy \
  --output /tmp/srs_channel.bin
```

Verify it later with:

```bash
.venv/bin/python gui/npy_to_rfsim_bin.py --verify /tmp/srs_channel.bin
```

## Comparison CLI

`gui/compare_channels.py` accepts either a single `.npy`, a glob, or a folder
for each side:

```bash
.venv/bin/python gui/compare_channels.py \
  <source-A> <source-B> \
  --kind srs --output-dir /tmp/channel_compare
```

Only same-kind inputs (`srs_*` vs `srs_*`, or `channel_*` vs `channel_*`) are
allowed. If the two sources contain different numbers of snapshots, the
comparison uses the common sorted prefix and prints a warning.

Per-slot console metrics:

- per-path complex correlation;
- NMSE after optimal complex scalar fit;
- RMSE, best-fit amplitude and phase;
- singular-value complex/Pearson correlation and normalized NMSE;
- mean condition numbers.

Aggregate output summarizes mean/p10/p90 over all compared slots.

Example using the checked-in SRS source and a replay-recorded gNB SRS folder:

```bash
.venv/bin/python gui/compare_channels.py \
  gui/record/gnb_log_20260806_212400/srs_20260806_212401_061474.npy \
  gui/record/gui_gnb_log_20260907_201732 \
  --kind srs --output-dir /tmp/channel_compare
```

Typical expected result when replay is correct:

```text
rx0-tx0: corr=0.9981 nmse=0.0038
rx1-tx1: corr=0.9983 nmse=0.0035
```

With `--output-dir`, the CLI writes:

```text
channel_comparison.json
figures/channel_comparison_magnitude.png
figures/channel_comparison_singular_values.png
```

## Metric definitions

For a path with common active subcarrier vectors `a` and `b`:

```text
complex_correlation = |sum(a*conj(b))| /
                      sqrt(sum(|a|^2) * sum(|b|^2))

best scale alpha = sum(b*conj(a)) / sum(|a|^2)

NMSE = sum(|b - alpha*a|^2) / sum(|b|^2)
```

Singular-value comparison normalizes each per-subcarrier channel matrix by its
Frobenius norm before computing the singular curves, so overall recording
amplitude/phase does not distort the shape comparison.

## Important interpretation notes

- SRS and CSI-RS snapshots use different frequency ordering and estimator
  scales; `compare_channels.py` intentionally rejects mixed-kind inputs.
- When SRS and CSI-RS recordings are compared, the full-band channel shape can
  still match, but alignment (for example slicing the overlapping 104 RB and a
  scale factor) must be handled by the user/analysis code.
- A five-subcarrier window is not representative; use the full-band metrics.

## Files

| File | Role |
|------|------|
| `gui/analyze_channel.py` | Single-channel frequency/time/MIMO/capacity analysis CLI |
| `gui/channel_metrics.py` | SVD/condition/tap/comparison metrics and plots |
| `gui/compare_channels.py` | Same-kind channel comparison CLI |
| `gui/npy_to_rfsim_bin.py` | Converter with integrated inspection |
| `gui/analysis/tests/test_channel_metrics.py` | Unit tests for metrics |
