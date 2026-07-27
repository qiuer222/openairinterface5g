# SRS Channel Recording & RFSIM Replay

A targeted, frequency-domain approach for capturing Sounding Reference Signal (SRS) channel estimates on the gNB and replaying them in the RFSimulator, bypassing the random/TDL channel model.

## Architecture Overview

```
  gNB (production / testbed)
  │
  ├─ SRS estimation (nr_est_srs_channel)
  │     ↓
  ├─ dump_srs_channel()          ──►  /tmp/srs_channel.bin
  │     [srs_channel_dump.c]           (binary, ≤20 slots)
  │
  └─ (file copied offline)
            │
            ▼
  RFSIM replay environment
  │
  ├─ env CHANNEL_FILE=/tmp/srs_channel.bin
  │
  ├─ rxAddInput_srsfile()        ──►  applies recorded H in frequency domain
  │     [apply_channel_fd.c]
  │
  └─ librfsimulator.so           ──►  compiled & loaded by rfsim
```

## File Format (Binary)

### Header (48 bytes, packed)

| Offset | Size  | Field                | Value                        |
|--------|-------|----------------------|------------------------------|
| 0      | 4     | magic                | `0x48534D52` (ASCII `"RSMH"`) |
| 4      | 2     | version              | `1`                          |
| 6      | 1     | num_rx_ant           | e.g. 1, 2, 4                 |
| 7      | 1     | num_tx_ant           | e.g. 1                       |
| 8      | 2     | fft_size             | e.g. 4096                    |
| 10     | 2     | n_rb                 | e.g. 106                     |
| 12     | 4     | subcarrier_spacing   | e.g. 30000 (Hz)              |
| 16     | 4     | num_slots_recorded   | updated after each append    |
| 20     | 2     | n_subcarriers        | actual subcarriers stored    |
| 22     | 2     | subcarrier_offset    | DC offset                    |
| 24     | 1     | n_srs_symbols        | number of SRS symbols        |
| 25     | 23    | reserved             | padding                      |

### Per-Slot Record

| Size (bytes) | Content                                          |
|-------------|--------------------------------------------------|
| 4           | `uint32_t slot_number`                           |
| `nrx × ntx × n_subcarriers × 4` | `c16_t` channel estimates (I/Q interleaved, 16‑bit each) |

## SRS Recording (gNB side)

**File:** [`openair1/PHY/NR_ESTIMATION/srs_channel_dump.c`](/home/qiuer/Documents/openairinterface5g/openair1/PHY/NR_ESTIMATION/srs_channel_dump.c)

- Exposes `dump_srs_channel(h_flat, nrx, ntx, fft_size, n_symb, n_subcarriers, subcarrier_offset, slot_number, n_rb, subcarrier_spacing)`.
- Called from [`openair1/SCHED_NR/phy_procedures_nr_gNB.c:854-856`](/home/qiuer/Documents/openairinterface5g/openair1/SCHED_NR/phy_procedures_nr_gNB.c:854) immediately after `nr_est_srs_channel()`.
- Writes a maximum of 20 slots to `/tmp/srs_channel.bin`, then closes the file.
- Updates `num_slots_recorded` in the header after each slot append (seeks back to offset 16).
- Header is declared as `__attribute__((packed))`.
- No configuration flags / no interaction with MAC — it is hard‑coded on when the gNB processes SRS.
- **Declaration:** [`openair1/PHY/NR_ESTIMATION/nr_ul_estimation.h:109`](/home/qiuer/Documents/openairinterface5g/openair1/PHY/NR_ESTIMATION/nr_ul_estimation.h:109)

## RFSIM Replay (channel emulator side)

**File:** [`radio/rfsimulator/apply_channel_fd.c`](/home/qiuer/Documents/openairinterface5g/radio/rfsimulator/apply_channel_fd.c)

Works at the frequency‑domain level (DFT → multiply by H → IDFT → sum TX antennas → AWGN + path loss), replacing the time‑domain TDL convolution.

### Key Functions

`rfsim_load_srs_file(path)`
- Opens the binary file, validates magic (`0x48534D52`) and version (1).
- Reads header dimensions and pre‑allocates a `c16_t` buffer for all slots.
- Skips per‑slot `uint32_t` headers and loads the H data flat.
- Resolves `dft()` / `idft()` function pointers from the main OAI binary via `dlsym(RTLD_DEFAULT, …)`.
- Sets global flag `srs_replay_loaded = 1`.

`rxAddInput_srsfile(input_sig, after_channel_sig, rxAnt, channelDesc, nbSamples)`
- **One‑time auto‑load:** If `srs_replay` is not loaded, checks `CHANNEL_FILE` environment variable and calls `rfsim_load_srs_file()`. Falls back to `rxAddInput()` (normal TDL) on failure or if the env var is unset.
- **Processing per RX antenna:**
  1. Divides sample stream into blocks of `fft_size` samples.
  2. For each TX antenna: DFTs the block → multiplies each occupied subcarrier by the recorded `c16_t` H value → IDFT → accumulates.
  3. Applies path‑loss scaling and AWGN.
  4. Copies accumulated time‑domain output to `after_channel_sig`.
- **Slot cycling:** Increments the recorded‑slot index on the last RX antenna (`rxAnt == nrx − 1`), cycling through available slots.
- Allocates static DFT work buffers once at the largest `fft_size` seen.

### Environment Variable

| Variable            | Default | Purpose                                           |
|--------------------|---------|---------------------------------------------------|
| `CHANNEL_FILE` | unset   | Path to the binary file produced by `dump_srs_channel` |

**Header:** [`radio/rfsimulator/rfsimulator.h`](/home/qiuer/Documents/openairinterface5g/radio/rfsimulator/rfsimulator.h) — declares `rfsim_load_srs_file`, `rxAddInput_srsfile`, and `extern int srs_replay_loaded`.

**Build:** [`radio/rfsimulator/CMakeLists.txt`](/home/qiuer/Documents/openairinterface5g/radio/rfsimulator/CMakeLists.txt) — `apply_channel_fd.c` is compiled into `librfsimulator.so`.

## Usage Workflow

1. **Record on the real gNB/testbed** (no config change needed — recording is always active):
   - Run the gNB as usual.
   - After SRS processing, `/tmp/srs_channel.bin` is produced (20 slots max, then closed).
2. **Copy the file** to the replay environment.
3. **Replay in RFSIM:**
   ```bash
   export CHANNEL_FILE=/path/to/srs_channel.bin
   # run nr-softmodem / nr-ue with --rfsim as usual
   ```
   The replay loads automatically on the first channel‑apply call.

## Related: General IQ Recording / Playback

A separate, coarser mechanism exists for recording and replaying raw time‑domain IQ samples (not SRS‑specific):

- **`radio/COMMON/record_player.c`** + **`radio/COMMON/record_player.h`** — Configurable via `--subframes-record` / `--subframes-replay` flags. Uses BELL Labs IQ format with per‑subframe timestamps. Default file: `/tmp/iqfile`.
- **`radio/iqplayer/iqplayer_lib.c`** — Device driver that replays IQ files through OAI's standard `openair0_device` interface. Supports mmap and per‑subframe read modes.
- Config section: `device.recplay` with options for file path, record/replay mode, loop count, read/write delays, and mmap control.

## File Reference

| File | Role |
|------|------|
| [`openair1/PHY/NR_ESTIMATION/srs_channel_dump.c`](/home/qiuer/Documents/openairinterface5g/openair1/PHY/NR_ESTIMATION/srs_channel_dump.c) | SRS recording (gNB) |
| [`openair1/SCHED_NR/phy_procedures_nr_gNB.c`](/home/qiuer/Documents/openairinterface5g/openair1/SCHED_NR/phy_procedures_nr_gNB.c) | Call site for `dump_srs_channel` |
| [`openair1/PHY/NR_ESTIMATION/nr_ul_estimation.h`](/home/qiuer/Documents/openairinterface5g/openair1/PHY/NR_ESTIMATION/nr_ul_estimation.h) | Declaration of `dump_srs_channel` |
| [`radio/rfsimulator/apply_channel_fd.c`](/home/qiuer/Documents/openairinterface5g/radio/rfsimulator/apply_channel_fd.c) | SRS channel replay (RFSIM) |
| [`radio/rfsimulator/rfsimulator.h`](/home/qiuer/Documents/openairinterface5g/radio/rfsimulator/rfsimulator.h) | Header for replay functions |
| [`radio/rfsimulator/CMakeLists.txt`](/home/qiuer/Documents/openairinterface5g/radio/rfsimulator/CMakeLists.txt) | Build: `librfsimulator.so` |
| [`radio/COMMON/record_player.c`](/home/qiuer/Documents/openairinterface5g/radio/COMMON/record_player.c) | General IQ record/player control |
| [`radio/COMMON/record_player.h`](/home/qiuer/Documents/openairinterface5g/radio/COMMON/record_player.h) | IQ record/player types and config macros |
| [`radio/iqplayer/iqplayer_lib.c`](/home/qiuer/Documents/openairinterface5g/radio/iqplayer/iqplayer_lib.c) | IQ player device driver |
