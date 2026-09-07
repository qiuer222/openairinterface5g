# Channel Inspection and Comparison

This document describes the numerical inspection and comparison tooling added
for GUI SRS/CSI-RS channel `.npy` files. It complements the replay workflow in
[channel_record_replay.md](channel_record_replay.md).

## Overview

Two commands are provided:

| Command | Purpose |
|---------|---------|
| `gui/npy_to_rfsim_bin.py` | Convert `.npy` to sparse tap `.bin`; prints input analysis during conversion |
| `gui/compare_channels.py` | Compare two same-kind channel recordings slot-by-slot |

The common metrics/plotting code lives in `gui/channel_metrics.py`.

## Converter analysis

Every `.npy → .bin` conversion prints:

- channel dimensions and active subcarrier range/count;
- per-path RMS amplitude;
- per-subcarrier singular-value statistics and mean condition number;
- effective rank histogram;
- retained sparse taps per path: delays, magnitudes, and frequency fit error.

The sparse tap report shows only the taps actually written to the file, not all
dense LS candidates. For the checked-in SRS example:

```text
slot 0: active=1248 [388..1635]
  rx0-tx0: n=1 delays=[0] |tap|=[0.989] fit_rmse=0.0095
```

This means one delay-0 tap is kept for that path; other weak candidate taps
were pruned.

To keep detailed reports for every slot and generate figures, add
`--analysis-dir`:

```bash
.venv/bin/python gui/npy_to_rfsim_bin.py \
  --kind srs --n-rb 106 --scs 30000 \
  gui/record/gnb_log_20260806_212400/srs_20260806_212401_061474.npy \
  --output /tmp/srs_channel.bin \
  --analysis-dir /tmp/channel_analysis
```

Outputs:

```text
/tmp/channel_analysis/conversion_channel_analysis.json
/tmp/channel_analysis/figures/conversion_magnitude.png
/tmp/channel_analysis/figures/conversion_singular_values.png
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
| `gui/channel_metrics.py` | SVD/condition/tap/comparison metrics and plots |
| `gui/compare_channels.py` | Same-kind channel comparison CLI |
| `gui/npy_to_rfsim_bin.py` | Converter with integrated inspection |
| `gui/analysis/tests/test_channel_metrics.py` | Unit tests for metrics |
