# SRS / CSI-RS Channel Record & Replay for RFSim

A frequency-domain channel recording and replay feature for NR. The gNB can record SRS channel estimates, the UE can record CSI-RS channel estimates, and RFSim can replay either file using the same binary format and replay engine.

## Recording Modes

| Value | Mode | Behavior |
|-------|------|----------|
| `0` | Disabled | No recording (default) |
| `1` | Single-slot | Rewrites the `.bin` file on every channel estimation; file always contains the latest 1 slot |
| `N >= 2` | Burst | Appends up to `N` slots, then stops |

## Architecture Overview

```text
gNB recording (nr-softmodem):
  --record-srs-ch
       │
       ▼
  phy_procedures_nr_gNB.c ──call──▸  srs_channel_dump.c ──write──▸ /tmp/srs_channel.bin

UE recording (nr-uesoftmodem):
  --record-csi-ch
       │
       ▼
  csi_rx.c ──call──▸  dump_csi_rs_channel() ──write──▸ /tmp/csi_rs_channel.bin

Replay (librfsimulator.so):
  env CHANNEL_FILE
       │
       ▼
  apply_channel_fd.c ──rxAddInput_srsfile()──▸ DFT → multiply by H[k] → IDFT → output
```

## Command-Line Flags

| Flag | Side | Default | Output |
|------|------|---------|--------|
| `--record-srs-ch` | gNB (`nr-softmodem`) | `0` | `/tmp/srs_channel.bin` |
| `--record-csi-ch` | UE (`nr-uesoftmodem`) | `0` | `/tmp/csi_rs_channel.bin` |

Examples:

```bash
# Single-slot SRS record (always latest channel)
sudo ./nr-softmodem -O gnb.conf --rfsim --record-srs-ch 1

# Burst SRS record (20 slots then stop)
sudo ./nr-softmodem -O gnb.conf --rfsim --record-srs-ch 20

# Single-slot CSI-RS record
sudo ./nr-uesoftmodem -O ue.conf --rfsim --record-csi-ch 1

# Burst CSI-RS record
sudo ./nr-uesoftmodem -O ue.conf --rfsim --record-csi-ch 20
```

## Binary File Format

Both dumps share the same packed format:

### Header (48 bytes, packed)

| Offset | Size | Field | Description |
|--------|------|-------|-------------|
| 0 | 4 | `magic` | `0x48534D52` (ASCII `"RSMH"`) |
| 4 | 2 | `version` | `1` |
| 6 | 1 | `num_rx_ant` | RX antennas |
| 7 | 1 | `num_tx_ant` | TX ports |
| 8 | 2 | `fft_size` | OFDM symbol size |
| 10 | 2 | `n_rb` | Number of resource blocks |
| 12 | 4 | `subcarrier_spacing` | SCS in Hz |
| 16 | 4 | `num_slots_recorded` | Updated after each slot append |
| 20 | 2 | `n_subcarriers` | Stored subcarriers (typically `fft_size`) |
| 22 | 2 | `subcarrier_offset` | DC offset |
| 24 | 1 | `n_srs_symbols` / `n_csi_symbols` | Symbol count |
| 25 | 23 | `reserved` | Padding |

### Per-Slot Record

| Size | Content |
|------|---------|
| 4 bytes | `uint32_t slot_number` |
| `nrx × ntx × n_subcarriers × 4` bytes | `c16_t` channel estimates (16-bit I/Q interleaved) |

## Key Files

| File | Role |
|------|------|
| `openair1/PHY/NR_ESTIMATION/srs_channel_dump.c` | SRS recording implementation (gNB) |
| `openair1/PHY/NR_ESTIMATION/nr_ul_estimation.h` | Declaration of `dump_srs_channel()` |
| `openair1/SCHED_NR/phy_procedures_nr_gNB.c` | SRS dump call site |
| `openair1/PHY/NR_UE_TRANSPORT/csi_rx.c` | CSI-RS recording (static `dump_csi_rs_channel()`) |
| `radio/rfsimulator/apply_channel_fd.c` | Replay engine (DFT → multiply → IDFT) |
| `radio/rfsimulator/rfsimulator.h` | Replay interface |
| `radio/rfsimulator/CMakeLists.txt` | Build: `librfsimulator.so` |
| `gui/npy_to_rfsim_bin.py` | Convert GUI `.npy` snapshots to RFSim `.bin` |
| `executables/softmodem-common.h` | `--record-srs-ch` config |
| `executables/nr-uesoftmodem.h` | `--record-csi-ch` config |

## Replay Usage

The replay engine reads the `.bin` file at startup via the `CHANNEL_FILE` environment variable and cycles through the recorded slots (at most the last 100 slots are loaded).

> `sudo` strips environment variables by default, so use `sudo -E` or `sudo env CHANNEL_FILE=...`.

```bash
# Export first, then use sudo -E
export CHANNEL_FILE=/tmp/srs_channel.bin
sudo -E ./nr-softmodem -O gnb.conf --rfsim

# Or pass through sudo explicitly
sudo env CHANNEL_FILE=/tmp/csi_rs_channel.bin \
  ./nr-softmodem -O gnb.conf --rfsim
```

Expected logs:

```
[HW] [rfsim] Loading channel file: /tmp/srs_channel.bin
[HW] [rfsim] Loaded SRS channel file: /tmp/srs_channel.bin (N slots, 1x2, fft=4096)
```

### Replaying GUI `.npy` Recordings

The GUI saves CSI-RS snapshots as `channel_*.npy` and SRS snapshots as
`srs_*.npy`. Convert them to the same `.bin` format before setting
`CHANNEL_FILE`:

```bash
.venv/bin/python gui/npy_to_rfsim_bin.py \
  --kind csi --n-rb 106 --scs 30000 \
  gui/record/gui_log_20260806_222820/channel_*.npy \
  --output /tmp/csi_rs_channel.bin

.venv/bin/python gui/npy_to_rfsim_bin.py --verify --kind csi \
  /tmp/csi_rs_channel.bin \
  --reference gui/record/gui_log_20260806_222820/channel_*.npy
```

## Replay Details

- `rfsim_load_srs_file(path)` validates magic/version, loads up to 100 most recent slots, and resolves OAI's `dft` / `idft` function-pointer variables via `dlsym()` (dereferenced before use).
- `rxAddInput_srsfile(...)` applies recorded H in the frequency domain: DFT → multiply by H[k] → IDFT → accumulate across TX antennas, then path loss + AWGN.
- If `CHANNEL_FILE` is unset or loading fails, RFSim logs the fallback and uses the normal TDL channel model.

## Related: General IQ Recording / Playback

`radio/COMMON/record_player.c` / `.h` and `radio/iqplayer/iqplayer_lib.c` provide a coarser raw-IQ record/replay mechanism (`--subframes-record` / `--subframes-replay`), not specific to SRS or CSI-RS channels.
