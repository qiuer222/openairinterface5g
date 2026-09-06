# SRS / CSI-RS Record & Replay for RFSim

## Overview

The channel record & replay feature captures frequency-domain SRS/CSI-RS
channel estimates from a live RFSim/OTA run and replays them deterministically
in RFSim.

Two recording paths are supported:

| Path | Side | Signal | Command |
|------|------|--------|---------|
| SRS recording | gNB | SRS from UE | `--record-srs-ch` |
| CSI-RS recording | UE | CSI-RS from gNB | `--record-csi-ch` |

The GUI `.npy` snapshots are converted into **sparse time-domain tap files**
with `gui/npy_to_rfsim_bin.py`. RFSim then applies the taps with direct linear
convolution, so the replay path behaves like the normal time-domain channel
model instead of a fixed-size frequency-domain block filter.

## Architecture

```text
GUI gNB log:
  srs_*.npy   (frequency-domain SRS estimate)
        |
        v
npy_to_rfsim_bin.py
        |  IDFT / sparse tap estimation
        v
  RSMH v3 .bin (time-domain taps, per rx/tx path)
        |
        v
apply_channel_fd.c: rxAddInput_srsfile()
        |  linear convolution over the sample stream
        v
  RFSim receive path
```

## Recording modes

| Value | Mode | Behavior |
|-------|------|----------|
| `0` | Disabled | No recording (default) |
| `1` | Single slot | Always keeps the latest snapshot |
| `N >= 2` | Burst | Records up to `N` snapshots, then stops |

Native recording writes frequency-domain H snapshots (RSMH v1) for debugging
and inspection. The replay engine consumes **v3 sparse tap files**; the GUI
`.npy` converter is the normal path that produces replay-ready files.

## File format

The replay format is version 3 (magic `RSMH`). It stores **sparse taps** so the
number of active taps per rx/tx path is small (typically 1–8) and the replay
does not pay for a dense 2048-tap or fixed-32-tap convolution.

### Header (48 bytes)

| Offset | Size | Field | Description |
|--------|------|-------|-------------|
| 0 | 4 | magic | `0x48534D52` |
| 4 | 2 | version | `3` |
| 6 | 1 | num_rx_ant | RX antennas |
| 7 | 1 | num_tx_ant | TX antennas/ports |
| 8 | 2 | fft_size | OFDM symbol size |
| 10 | 2 | n_rb | Number of RBs |
| 12 | 4 | subcarrier_spacing | SCS in Hz |
| 16 | 4 | num_slots_recorded | Slot count |
| 20 | 2 | max_active_taps | Max taps stored per path |
| 22 | 2 | reserved | 0 |
| 24 | 1 | n_symbols | 1 |
| 25 | 1 | tap_scale_bits | Tap scale (15 in this build) |
| 26 | 22 | reserved | 0 |

### Per-slot data

```text
uint32 slot_number
for each rx, tx path:
  uint16 active_tap_count
  for each active tap:
    uint16 delay
    c16_t  tap   (scaled by 2^tap_scale_bits)
```

The C engine keeps the taps in memory per slot and cycles through slots during
replay, matching the behavior of multi-slot recordings.

## Converter usage

Convert GUI SRS snapshots to a sparse time-domain replay file:

```bash
.venv/bin/python gui/npy_to_rfsim_bin.py \
  --kind srs --n-rb 106 --scs 30000 \
  gui/record/gnb_log_20260806_215012/srs_*.npy \
  --output /tmp/srs_channel.bin
```

Convert GUI CSI-RS snapshots:

```bash
.venv/bin/python gui/npy_to_rfsim_bin.py \
  --kind csi --n-rb 106 --scs 30000 \
  gui/record/gui_log_20260806_222820/channel_*.npy \
  --output /tmp/csi_rs_channel.bin
```

Verify the file structure:

```bash
.venv/bin/python gui/npy_to_rfsim_bin.py \
  --verify --kind srs /tmp/srs_channel.bin
```

Converter options:

| Option | Default | Purpose |
|--------|---------|---------|
| `--tap-len` | 32 | Number of dense taps used in the LS/IDFT fit |
| `--max-active-taps` | 8 | Maximum taps retained per rx/tx path |
| `--transpose` | off | Swap rx/tx for reciprocal replay on the opposite side |
| `--subcarrier-offset` | auto | Spectral offset used during tap fitting |

## Replay usage

RFSim reads the file through the `CHANNEL_FILE` environment variable:

```bash
sudo env CHANNEL_FILE=/tmp/srs_channel.bin \
  ./cmake_targets/ran_build/build/nr-softmodem \
  -O targets/PROJECTS/GENERIC-NR-5GC/CONF/gnb.sa.band78.fr1.106PRB.2x2.usrpn300.conf \
  --rfsim
```

Expected load log:

```text
[HW] [rfsim] Loading channel file: /tmp/srs_channel.bin
[HW] [rfsim] Loaded SRS channel file: /tmp/srs_channel.bin
     (N slots, 2x2, fft=2048, taps/path<=8, tap_scale_bits=15)
```

If `CHANNEL_FILE` is unset or loading fails, RFSim falls back to its normal
channel model.

## Runtime behavior

The time-domain tap path is functionally correct for connection setup, but a
dense per-sample convolution is more expensive than the original FFT-based
approach. The converter avoids this cost by storing only active taps:

- each tap record contains its delay, so zero taps are not evaluated;
- the default pruning keeps taps within 20 dB of the strongest recorded tap;
- `--max-active-taps` caps worst-case cost per path.

For a typical diagonal 2x2 recording, each path reduces to one active tap and
the replay cost is equivalent to an AWGN channel. If a run is still too slow:

1. Lower `--tap-len` (e.g. `16`) or `--max-active-taps` (e.g. `4`).
2. Prefer burst/short recordings (single-slot files are cheaper to load).
3. Check host CPU load; RFSim must process samples in real time.

## Build

```bash
cd /home/qiuer/Documents/openairinterface5g/cmake_targets/ran_build
sudo ninja -C build librfsimulator
```

Or rebuild the full softmodem targets with:

```bash
cd /home/qiuer/Documents/openairinterface5g/cmake_targets
sudo ./build_oai -w USRP --ninja --nrUE --gNB
```

## Files reference

| File | Role |
|------|------|
| `gui/npy_to_rfsim_bin.py` | Convert `srs_*.npy` / `channel_*.npy` to sparse tap RSMH files |
| `radio/rfsimulator/apply_channel_fd.c` | Sparse tap loader and linear convolution replay |
| `radio/rfsimulator/rfsimulator.h` | Replay interface |
| `openair1/SCHED_NR/phy_procedures_nr_gNB.c` | SRS record call site |
| `openair1/PHY/NR_ESTIMATION/srs_channel_dump.c` | Native SRS frequency dump |
| `openair1/PHY/NR_UE_TRANSPORT/csi_rx.c` | CSI-RS recording |

## Known limitations

| Limitation | Impact |
|------------|--------|
| Last 100 slots are kept | Older history is not replayed |
| Sparse tap pruning | Weak taps below the pruning threshold are dropped |
| Direct convolution cost | Bounded by active tap count; use sparse/limited taps on constrained hosts |
| Per-slot taps, not per-OFDM-symbol | Intended for quasi-static channels |
