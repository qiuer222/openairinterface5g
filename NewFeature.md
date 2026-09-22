# SRS / CSI-RS Frequency-Domain Replay for RFSim

RFSim replay now applies one recorded frequency-domain channel snapshot to
every received NR slot. The implementation is in
`radio/rfsimulator/fd_channel.c`.

## Workflow

```text
GUI .npy SRS/CSI-RS H
        |
        v
gui/npy_to_rfsim_bin.py
        |  map to physical FFT bins, write complex64
        v
single-slot FDCH .bin
        |
        v
simulator.cpp: fd_channel_load()
        |  global non-zero RMS normalization
        v
fd_apply_symbol(): per-TX FFT -> per-RX H multiply -> IFFT
        |
        v
RFSim receive slot
```

## Converter

```bash
.venv/bin/python gui/npy_to_rfsim_bin.py \
  --kind srs --n-rb 106 --scs 30000 \
  gui/record/.../srs_*.npy \
  --output /tmp/srs_channel.bin
```

The converter accepts exactly one `.npy` snapshot. It stores full
`fft_size`-ordered complex64 H data and records the FFT size, antenna count,
symbols per slot, normal and long CP lengths, PRB count, and subcarrier
spacing.

## Replay

```bash
CHANNEL_FILE=/tmp/srs_channel.bin \
  ./cmake_targets/ran_build/build/nr-softmodem \
  -O targets/PROJECTS/GENERIC-NR-5GC/CONF/gnb.sa.band78.fr1.106PRB.2x2.usrpn300.conf \
  --rfsim
```

`CHANNEL_FILE` does not require the legacy `chanmod` option. A missing or
invalid configured file is fatal. During runtime, an unsupported read-block
size disables FD replay and falls back to the ordinary RFSim path.

## Related Documents

- `doc/local-edit/channel_record_replay.md`
