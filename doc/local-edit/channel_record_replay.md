# SRS / CSI-RS Record & Replay for RFSim

## Overview

The channel record & replay feature captures frequency-domain channel estimates from real OTA runs and replays them deterministically in RFSim. Two complementary recording paths are supported:

| Path | Side | Signal | Command |
|------|------|--------|---------|
| **SRS recording** | gNB | SRS (Sounding Reference Signal) from UE | `--record-srs-ch` |
| **CSI-RS recording** | UE | CSI-RS (Channel State Information RS) from gNB | `--record-csi-ch` |

Both produce the **same binary file format**, so either file can be replayed by the same `apply_channel_fd.c` engine in RFSim.

---

## Architecture

```text
Recording - gNB side (nr-softmodem):
  --record-srs-ch
       │
       ▼
  phy_procedures_nr_gNB.c ──call──▸  srs_channel_dump.c ──write──▸ /tmp/srs_channel.bin
                                         dump_srs_channel()

Recording - UE side (nr-uesoftmodem):
  --record-csi-ch
       │
       ▼
  csi_rx.c ──call──▸  dump_csi_rs_channel() (static) ──write──▸ /tmp/csi_rs_channel.bin
       nr_ue_csi_rs_procedures()

Replay (shared, librfsimulator.so):
  env CHANNEL_FILE
       │
       ▼
  apply_channel_fd.c ──rxAddInput_srsfile()──▸ DFT → multiply by H[k] → IDFT → output
       rfsimulator.h (declares interface)
```

---

## Recording Modes

Each recording flag accepts an integer value selecting the mode:

| Value | Mode | Behavior |
|-------|------|----------|
| `0` | Disabled | No recording (default) |
| `1` | **Single-slot** | Rewrites the `.bin` file on every channel estimation. The file always contains exactly 1 slot — the latest channel snapshot. |
| `N ≥ 2` | **Burst** | Appends up to `N` slots into the `.bin` file, then stops. The file contains `N` consecutive channel snapshots. |

---

## Command-Line Flags

### `--record-srs-ch` (gNB)

| Attribute | Detail |
|-----------|--------|
| Binary | `nr-softmodem` (and `lte-softmodem`, flag accepted but ignored) |
| Config | `softmodem_params_t.record_srs_ch` |
| Default | `0` (disabled) |
| Values | `0`=off, `1`=single-slot, `N≥2`=burst |
| Output | `/tmp/srs_channel.bin` |

In `softmodem-common.h`:
```c
#define CONFIG_HLP_RECORD_SRS_CH \
  "Record SRS channel: 1=single-slot overwrite, N>=2=burst (N slots then stop). File: /tmp/srs_channel.bin\n"

{"record-srs-ch", CONFIG_HLP_RECORD_SRS_CH, 0,
 .iptr=&softmodem_params.record_srs_ch, .defintval=0, TYPE_INT, 0},
```

### `--record-csi-ch` (UE)

| Attribute | Detail |
|-----------|--------|
| Binary | `nr-uesoftmodem` |
| Config | `nrUE_params_t.record_csi_ch` |
| Default | `0` (disabled) |
| Values | `0`=off, `1`=single-slot, `N≥2`=burst |
| Output | `/tmp/csi_rs_channel.bin` |

In `nr-uesoftmodem.h`:
```c
{"record-csi-ch", CONFIG_HLP_RECORD_CSI_CH, 0,
 .iptr=&nrUE_params.record_csi_ch, .defintval=0, TYPE_INT, 0},
```

---

## Binary File Format

Both dumps share the same packed format, enabling direct reuse by the replay engine.

### Header (48 bytes, packed)

| Offset | Size | Field | Description |
|--------|------|-------|-------------|
| 0 | 4 | `magic` | `0x48534D52` (ASCII `"RSMH"`) |
| 4 | 2 | `version` | `1`: raw H (native recorders); `2`: normalized H + per-slot gains (GUI converter) |
| 6 | 1 | `num_rx_ant` | RX antennas (e.g. 1, 2, 4) |
| 7 | 1 | `num_tx_ant` | TX ports (SRS: UE ports, CSI-RS: gNB ports) |
| 8 | 2 | `fft_size` | OFDM symbol size (e.g. 4096) |
| 10 | 2 | `n_rb` | Number of resource blocks |
| 12 | 4 | `subcarrier_spacing` | SCS in Hz (e.g. 30000) |
| 16 | 4 | `num_slots_recorded` | Updated after each slot append |
| 20 | 2 | `n_subcarriers` | Subcarriers stored (typically `fft_size`) |
| 22 | 2 | `subcarrier_offset` | DC offset |
| 24 | 1 | `n_csi_symbols` / `n_srs_symbols` | Symbol count |
| 25 | 1 | `h_scale_bits` | H AMP scale used by version-2 files (9 in this build); 0 for version-1 files |
| 26 | 22 | `reserved` | Zero padding |

### Version-2 Gain Array

Immediately after the 48-byte header, version-2 files contain `num_slots × float` linear gain values (`slot_gain_lin`), one per slot, followed by the per-slot records below. The stored H is normalized to the AMP scale; replay multiplies its result by `slot_gain_lin` to restore the recorded absolute channel power.

### Per-Slot Record

| Size | Content |
|------|---------|
| 4 bytes | `uint32_t slot_number` |
| `nrx × ntx × n_subcarriers × 4` bytes | `c16_t` channel estimates (16-bit I/Q interleaved) |

---

## Recording: Implementation Details

### SRS Recording — `srs_channel_dump.c`

**File:** `openair1/PHY/NR_ESTIMATION/srs_channel_dump.c`

Called from `handle_srs()` in `openair1/SCHED_NR/phy_procedures_nr_gNB.c` after SRS channel estimation completes, gated by `get_softmodem_params()->record_srs_ch`. The `record_srs_ch` value is passed through as the `max_slots` parameter.

```c
void dump_srs_channel(const c16_t *h_flat,
                       int nrx, int ntx, int fft_size,
                       int n_symb, int n_subcarriers,
                       int subcarrier_offset,
                       uint32_t slot_number,
                       int n_rb, int subcarrier_spacing,
                       int max_slots)
```

Key behavior:
- `max_slots == 1`: single-slot mode — opens the file with `"wb"`, writes header + 1 slot, closes. Every call replaces the file.
- `max_slots >= 2`: burst mode — first call opens the file and writes header, each subsequent call appends a slot and updates `num_slots_recorded` in the header. After `max_slots` slots, the file is closed.
- Static `FILE*` handle and slot counter track burst progress.

### CSI-RS Recording — `csi_rx.c` (static function)

**File:** `openair1/PHY/NR_UE_TRANSPORT/csi_rx.c`

The dump function `dump_csi_rs_channel()` is a static function inside `csi_rx.c`. Called from `nr_ue_csi_rs_procedures()` after `nr_csi_rs_channel_estimation()` returns, gated by `get_nrUE_params()->record_csi_ch`.

```c
static void dump_csi_rs_channel(const c16_t *h_data,
                                 int nrx, int ntx, int fft_size,
                                 int n_rb, int subcarrier_spacing,
                                 int max_slots)
```

Data shape: `csi_rs_estimated_channel_freq[ant_rx][port_tx][ofdm_symbol_size]` — the fully interpolated frequency-domain channel estimate.

Two modes (selected by `max_slots`): same semantics as SRS recording above.

---

## Replay: `apply_channel_fd.c`

**File:** `radio/rfsimulator/apply_channel_fd.c`

### Entry Point

`rxAddInput_srsfile()` — called from `simulator.cpp` in the channel-apply loop, once per RX antenna per buffer.

On the **first call**, it checks `getenv("CHANNEL_FILE")`:
- If set → try to load the file. Success → SRS replay. Failure → log warning, call `rxAddInput()`.
- If not set → log a message and call `rxAddInput()` (normal TDL model).

The DFT/IDFT implementation is resolved from OAI's exported `dft` / `idft` function-pointer variables via `dlsym()`, then dereferenced to the real implementation loaded by `load_dftslib()`.

### Processing Per RX Antenna

```c
rxAddInput_srsfile(input_sig, after_channel_sig, rxAnt, channelDesc, nsamps) {
    // One-time load
    if first call and not loaded:
        path = getenv("CHANNEL_FILE")
        if path and load succeeds → set srs_replay_loaded
        else → rxAddInput(); return

    slot_idx = current_slot % num_slots
    h_slot = h_data[slot_idx][rxAnt][*][*]

    for each TX antenna ta:
        DFT(input_sig[ta][block]) → freq_buf
        for each k in subcarriers:
            freq_buf[k] *= h_slot[ta][k]
        IDFT(freq_buf) → time_buf
        accumulate

    apply path_loss, noise → after_channel_sig
}
```

### Key State

The recorded slots are loaded into memory once at startup. **Only the last 100 slots are kept** (constant `MAX_REPLAY_SLOTS` in `apply_channel_fd.c`). If the file has more than 100 slots, the oldest ones are skipped by seeking past them during load. This keeps memory bounded regardless of recording duration.

During replay the slot index cycles: `slot_idx = current_slot % num_slots`, wrapping when the loaded count is exceeded.

The replay DFT/IDFT work buffers are allocated 32-byte aligned as required by
OAI's `dft`/`idft` wrappers. Recorded H is interpreted in the OAI reference
amplitude scale (`AMP = 2^9 = 512` in this build, i.e. unit gain is stored as
H ≈ 512). Set `RFSIM_H_SCALE_BITS` (default `9`) to override the scale for
files recorded from another AMP build.

| Variable | Type | Scope | Description |
|----------|------|-------|-------------|
| `srs_replay_loaded` | `int` | global | Flag checked by `simulator.cpp` |
| `srs_replay.loaded` | `int` | static | File loaded successfully |
| `srs_replay.num_rx/tx` | `int` | static | Antenna dimensions |
| `srs_replay.fft_size` | `int` | static | OFDM symbol size |
| `srs_replay.num_slots` | `int` | static | Loaded slot count (≤ 100) |
| `srs_replay.current_slot` | `int` | static | Cycles through loaded slots |
| `srs_replay.h_data` | `c16_t*` | static | Loaded frequency-domain H |
| `p_dft` / `p_idft` | func ptr | static | Resolved via `dlsym(RTLD_DEFAULT, ...)` |

---

## Usage Examples

### 1. Record SRS (gNB side)

```bash
# Single-slot mode: always the latest channel
sudo ./nr-softmodem -O gnb.conf --rfsim --record-srs-ch 1

# Burst mode: 20 slots then stop
sudo ./nr-softmodem -O gnb.conf --rfsim --record-srs-ch 20
```

### 2. Record CSI-RS (UE side)

```bash
# Single-slot mode: always the latest channel
sudo ./nr-uesoftmodem -O ue.conf --rfsim --record-csi-ch 1

# Burst mode: 20 slots then stop
sudo ./nr-uesoftmodem -O ue.conf --rfsim --record-csi-ch 20
```

### 3. Replay in RFSim

The replay engine reads the `.bin` file at startup via the `CHANNEL_FILE` environment variable and cycles through the recorded slots. Works with both single-slot (1 slot cycles on itself) and burst (up to 100 slots) recordings.

> **Note:** `sudo` strips environment variables by default. Use `sudo -E` after exporting the variable, or use `sudo env CHANNEL_FILE=...` so the replay process actually sees it.

```bash
# Export first, then use sudo -E
export CHANNEL_FILE=/tmp/srs_channel.bin
sudo -E ./nr-softmodem -O gnb.conf --rfsim

# Or pass through sudo explicitly
sudo env CHANNEL_FILE=/tmp/srs_channel.bin \
  ./nr-softmodem -O gnb.conf --rfsim

# Or replay CSI-RS recording
sudo env CHANNEL_FILE=/tmp/csi_rs_channel.bin \
  ./nr-softmodem -O gnb.conf --rfsim

# UE connects normally
sudo ./nr-uesoftmodem -O ue.conf --rfsim
```

Expected replay log when loading succeeds:

```
[HW] [rfsim] Loading channel file: /tmp/srs_channel.bin
[HW] [rfsim] Loaded SRS channel file: /tmp/srs_channel.bin (N slots, 1x2, fft=4096, h_scale_bits=9, gains=N)
```

If the variable is missing or the file fails to load, the log clearly shows the fallback:

```
[HW] [rfsim] CHANNEL_FILE not set, using normal channel model
[HW] Failed to load channel file: /tmp/missing.bin
```

### 3.5 Replay GUI `.npy` Recordings

The GUI saves CSI-RS snapshots as `channel_*.npy` and SRS snapshots as
`srs_*.npy`. The converter writes version-2 `.bin` files: H is normalized to
the OAI AMP scale for fixed-point stability, and each slot's linear gain is
stored after the header so the replay engine restores the recorded power.
Version-1 native recorder files remain supported unchanged.

```bash
# CSI-RS GUI snapshots -> multi-slot .bin
.venv/bin/python gui/npy_to_rfsim_bin.py \
  --kind csi --n-rb 106 --scs 30000 \
  gui/record/gui_log_20260806_222820/channel_*.npy \
  --output /tmp/csi_rs_channel.bin

# SRS GUI snapshots -> multi-slot .bin
.venv/bin/python gui/npy_to_rfsim_bin.py \
  --kind srs --n-rb 106 --scs 30000 \
  gui/record/gnb_log_20260806_215012/srs_*.npy \
  --output /tmp/srs_channel.bin

# Verify the generated binary format and compare every slot against the .npy source
.venv/bin/python gui/npy_to_rfsim_bin.py --verify --kind csi \
  /tmp/csi_rs_channel.bin \
  --reference gui/record/gui_log_20260806_222820/channel_*.npy

.venv/bin/python gui/npy_to_rfsim_bin.py --verify --kind srs \
  /tmp/srs_channel.bin \
  --reference gui/record/gnb_log_20260806_215012/srs_*.npy
```

For a 2x2 CSI-RS replay, use the 106 PRB 2x2 RFSim gNB config, pass the
converted file to the gNB, and connect a 2x2 UE:

```bash
sudo env CHANNEL_FILE=/tmp/csi_rs_channel.bin \
  ./cmake_targets/ran_build/build/nr-softmodem \
  -O targets/PROJECTS/GENERIC-NR-5GC/CONF/gnb.sa.band78.fr1.106PRB.2x2.usrpn300.conf \
  --rfsim
```

```bash
sudo ./cmake_targets/ran_build/build/nr-uesoftmodem \
  -r 106 --numerology 1 --band 78 -C 3319680000 \
  --ue-nb-ant-tx 2 --ue-nb-ant-rx 2 \
  --uecap_file targets/PROJECTS/GENERIC-NR-5GC/CONF/uecap_ports2.xml \
  -O ci-scripts/conf_files/nrue.uicc.conf \
  --rfsim --rfsimulator.[0].serveraddr 127.0.0.1
```

Verify that RFSim loaded the converted file:

```text
[HW] [rfsim] Loading channel file: /tmp/csi_rs_channel.bin
[HW] [rfsim] Loaded SRS channel file: /tmp/csi_rs_channel.bin (N slots, 2x2, fft=2048, h_scale_bits=9, gains=N)
```

### 4. Build

```bash
./build_oai -w USRP --ninja --nrUE --gNB
```

### 5. Verified 2x2 Record Command

```bash
sudo ./cmake_targets/ran_build/build/nr-uesoftmodem \
  -r 106 --numerology 1 --band 78 -C 3319680000 \
  --ue-nb-ant-tx 2 --ue-nb-ant-rx 2 \
  --uecap_file targets/PROJECTS/GENERIC-NR-5GC/CONF/uecap_ports2.xml \
  -O ci-scripts/conf_files/nrue.uicc.conf \
  --rfsim --record-csi-ch 1
```

---

## Mode Selection Summary

| Method | Effect |
|--------|--------|
| `--record-srs-ch 1` | Single-slot SRS record (rewrite each time) to `/tmp/srs_channel.bin` |
| `--record-srs-ch 20` | Burst SRS record (20 slots then stop) to `/tmp/srs_channel.bin` |
| `--record-csi-ch 1` | Single-slot CSI-RS record (rewrite each time) to `/tmp/csi_rs_channel.bin` |
| `--record-csi-ch 20` | Burst CSI-RS record (20 slots then stop) to `/tmp/csi_rs_channel.bin` |
| `CHANNEL_FILE=/path` with `sudo -E` (or `sudo env`) | Replay recorded channel (SRS or CSI-RS) |
| `unset CHANNEL_FILE` | Normal TDL channel model (AWGN/EPA/EVA/ETU) |

---

## Files Reference

| File | Role |
|------|------|
| `openair1/PHY/NR_ESTIMATION/srs_channel_dump.c` | SRS recording implementation (gNB) |
| `openair1/PHY/NR_ESTIMATION/nr_ul_estimation.h` | Declaration of `dump_srs_channel()` |
| `openair1/SCHED_NR/phy_procedures_nr_gNB.c` | Call site for SRS dump (gated by `record_srs_ch`) |
| `openair1/PHY/NR_UE_TRANSPORT/csi_rx.c` | CSI-RS recording (static `dump_csi_rs_channel()`) |
| `radio/rfsimulator/apply_channel_fd.c` | Replay engine (DFT→multiply→IDFT) |
| `radio/rfsimulator/rfsimulator.h` | Replay interface declarations |
| `radio/rfsimulator/CMakeLists.txt` | Build: `apply_channel_fd.c` → `librfsimulator.so` |
| `gui/npy_to_rfsim_bin.py` | Convert GUI `channel_*.npy` / `srs_*.npy` to RFSim `.bin` |
| `executables/softmodem-common.h` | `--record-srs-ch` config definition |
| `executables/nr-uesoftmodem.h` | `--record-csi-ch` config definition |
| `radio/COMMON/record_player.c` / `.h` | Legacy IQ record/player (not SRS-specific) |
| `radio/iqplayer/iqplayer_lib.c` | Legacy IQ player device driver |

---

## Known Limitations

| Limitation | Impact |
|------------|--------|
| Only loads last 100 slots at replay | Prevents memory exhaustion; older history is discarded. Not an issue for burst mode (≤ 100 slots) or single-slot mode (1 slot). |
| Per-slot H, not per-OFDM-symbol | Phase discontinuity within slot boundaries (acceptable for quasi-static channels) |
| DFT must support `fft_size` | OAI supports standard sizes 128–4096 — check your config |
| `dlsym()` fallback | Requires `-rdynamic` on the main binary (already enabled) |
| Recording requires explicit CLI flags | Both sides are opt-in, controlled by `--record-srs-ch` / `--record-csi-ch` |
