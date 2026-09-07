# SRS / CSI-RS Channel Record & Replay for RFSim

This feature replays GUI-recorded SRS/CSI-RS channels in RFSim using
**time-domain replay only**. No legacy frequency-domain block replay is kept.

## Workflow

```text
GUI .npy SRS/CSI-RS frequency H
        │
        ▼
gui/npy_to_rfsim_bin.py
        │  sparse LS taps  (default)
        │  or --exact-taps (full IDFT response)
        ▼
RSMH time-domain tap file
        │
        ▼
apply_channel_fd.c: rxAddInput_srsfile()
        │  direct linear convolution
        ▼
RFSim
```

## Converter

```bash
# sparse time-domain taps (default)
.venv/bin/python gui/npy_to_rfsim_bin.py \
  --kind srs --n-rb 106 --scs 30000 \
  gui/record/.../srs_*.npy \
  --output /tmp/srs_channel.bin

# full fft_size IDFT response when sparse fit error is high
.venv/bin/python gui/npy_to_rfsim_bin.py \
  --kind srs --exact-taps --n-rb 106 --scs 30000 \
  gui/record/.../srs_*.npy \
  --output /tmp/srs_exact.bin
```

The converter also prints per-path RMS, SVD/condition summaries, retained taps
and frequency fit error. `--analysis-dir` writes JSON/PNG reports.

## Replay

```bash
sudo env CHANNEL_FILE=/tmp/srs_channel.bin \
  ./cmake_targets/ran_build/build/nr-softmodem \
  -O targets/PROJECTS/GENERIC-NR-5GC/CONF/gnb.sa.band78.fr1.106PRB.2x2.usrpn300.conf \
  --rfsim
```

## Comparison tool

```bash
.venv/bin/python gui/compare_channels.py \
  <source-A> <source-B> --kind srs \
  --output-dir /tmp/channel_compare
```

Reports per-path complex correlation, NMSE, singular-value curves and
condition-number distributions.

## Related documents

- `doc/local-edit/channel_record_replay.md`
- `doc/local-edit/channel_inspection_comparison.md`
