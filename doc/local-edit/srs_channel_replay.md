 # SRS Channel Record & Replay for RFSim
 
 ## Overview
 
 The SRS channel record & replay feature enables deterministic channel replay in RFSim by:
 
 1. **Recording**: During a gNB+UE run, the already-interpolated SRS channel estimate (full `ofdm_symbol_size` frequency grid) is dumped to a binary file.
 2. **Replay**: On subsequent RFSim runs, the recorded channel is applied via `DFT → multiply by H[k] → IDFT` instead of the normal time-domain TDL convolution.
 
 This gives reproducible channel conditions for algorithm testing without requiring OTA hardware or re-running a full channel model.
 
 ## File Architecture
 
 ```text
 Recording side (compile into nr-softmodem):
   phy_procedures_nr_gNB.c  ──calls──▸  srs_channel_dump.c  ──writes──▸  /tmp/srs_channel.bin
                                             nr_ul_estimation.h
 
 Replay side (compile into librfsimulator.so):
   simulator.cpp  ──calls──▸  rfsimulator.h  ◀──declares──  apply_channel_fd.c
                                    └── rxAddInput_srsfile()
                                    └── rfsim_load_srs_file()
                                    └── srs_replay_loaded (global flag)
 ```
 
 ## Recording Side
 
 ### File: `openair1/PHY/NR_ESTIMATION/srs_channel_dump.c`
 
 #### Entry Point
 
 Called from `handle_srs()` in `phy_procedures_nr_gNB.c` right after `nr_srs_rx_procedures()` returns, but before the SNR threshold check. This guarantees the interpolated channel is ready.
 
 #### Pseudocode
 
 ```c
 void dump_srs_channel(h_flat, nrx, ntx, fft_size, n_symb,
                       n_subcarriers, subcarrier_offset,
                       slot_number, n_rb, subcarrier_spacing) {
 
     if slot_count >= MAX_RECORD_SLOTS (20)
         close file, return
 
     if fp == NULL
         fp = fopen("srs_channel.bin", "wb")
         write header: magic, version, nrx, ntx, fft_size,
                       n_rb, scs, slot_count=0, n_subcarriers,
                       subcarrier_offset, n_srs_symbols
 
     write slot_number (uint32_t)
 
     for ra in 0..nrx-1
         for ta in 0..ntx-1
             src = h_flat + (ra * ntx + ta) * fft_size * n_symb
             write src as c16_t[n_subcarriers]
 
     slot_count++
     seek to header.num_slots_recorded
     write slot_count
     seek back to end
 }
 ```
 
 #### Important Variables
 
 | Variable | Type | Description |
 |----------|------|-------------|
 | `srs_dump_fp` | `FILE*` (static) | Open file handle; NULL until first call, kept open between slots |
 | `srs_dump_slot_count` | `int` (static) | Number of slots written so far |
 | `MAX_RECORD_SLOTS` | `const`, 20 | Maximum slots to record, then stops |
 | `srs_estimated_channel_freq[ant_rx][p_ind][ofdm_symbol_size * N_symb_SRS]` | `c16_t[]` | The fully interpolated channel estimate from OAI's SRS estimation |
 
 #### Binary File Format (`/tmp/srs_channel.bin`)
 
 ```text
 [Header, 48 bytes, packed]
   0: uint32  magic         0x48534D52 ("SRSH")
   4: uint16  version       1
   6: uint8   num_rx_ant
   7: uint8   num_tx_ant
   8: uint16  fft_size
  10: uint16  n_rb
  12: uint32  subcarrier_spacing
  16: uint32  num_slots_recorded
  20: uint16  n_subcarriers      (typically fft_size)
  22: uint16  subcarrier_offset  (0 for full grid)
  24: uint8   n_srs_symbols
  25: uint8[23] reserved
 
 [Per-slot record, repeated num_slots_recorded times]
   0: uint32  slot_number
   4: c16_t   H[n_rx][n_tx][n_subcarriers]
 ```
 
 ## Replay Side
 
 ### File: `radio/rfsimulator/apply_channel_fd.c`
 
 #### Entry Point
 
 `rxAddInput_srsfile()` — called from `simulator.cpp` in the channel-apply loop, once per RX antenna per buffer. It replaces `rxAddInput()` unconditionally.
 
 On the **first call**, it checks `getenv("SRS_CHANNEL_FILE")`:
 - If set → try to load the file. Success → SRS replay. Failure → log warning, call `rxAddInput()`.
 - If not set → call `rxAddInput()` (normal TDL model).
 
 #### Pseudocode
 
 ```c
 // Load phase (first call only)
 rfsim_load_srs_file(path) {
     open /tmp/srs_channel.bin (or env var path)
     read & validate header (magic, version)
     for each slot:
         skip slot_number (uint32_t)
         read H[rx][tx][subcarrier] into memory
     close file
     srs_replay_loaded = 1
     resolve dft/idft function pointers via dlsym()
 }
 
 // Apply phase (every call)
 rxAddInput_srsfile(input_sig, after_channel_sig, rxAnt, channelDesc, nsamps) {
     if first call and not loaded:
         try to load from SRS_CHANNEL_FILE env var
         if fails: rxAddInput(original); return
 
     fft_size = srs_replay.fft_size
     slot_idx = current_slot % num_slots  // cycle through recorded slots
     h_slot = h_data[slot_idx][rxAnt][*][*]
 
     clear output buffer
 
     for each block (size = fft_size):
         for each TX antenna ta:
             DFT(input_sig[ta][block]) → freq_buf
             for each subcarrier k:
                 freq_buf[k] *= h_slot[ta][k]
             IDFT(freq_buf) → time_buf
             accumulate time_buf → rxAnt output
 
     apply path_loss, noise (same as original rxAddInput)
     advance slot counter on last RX antenna call
 }
 ```
 
 #### Important Variables (Replay State)
 
 | Variable | Type | Scope | Description |
 |----------|------|-------|-------------|
 | `srs_replay_loaded` | `int` | **global** | Flag checked by `simulator.cpp` (currently called unconditionally anyway) |
 | `srs_replay.loaded` | `int` | static | Whether file was successfully loaded |
 | `srs_replay.num_rx` | `int` | static | Number of RX antennas from recorded file |
 | `srs_replay.num_tx` | `int` | static | Number of TX ports from recorded file |
 | `srs_replay.fft_size` | `int` | static | OFDM symbol size (e.g. 2048) |
 | `srs_replay.n_sc` | `int` | static | Number of stored subcarriers (typically = fft_size) |
 | `srs_replay.sc_off` | `int` | static | First subcarrier index in FFT grid |
 | `srs_replay.num_slots` | `int` | static | Number of recorded slots (typically ≤ 20) |
 | `srs_replay.current_slot` | `int` | static | Current replay slot index (cycles) |
 | `srs_replay.h_data` | `c16_t*` | static | Loaded H data: `[num_slots * num_rx * num_tx * n_sc]` |
 | `p_dft` | `dftfunc_t` | static | Resolved DFT function pointer |
 | `p_idft` | `idftfunc_t` | static | Resolved IDFT function pointer |
 | `freq_buf` / `work_buf` / `time_buf` | `c16_t*` | static | Work buffers (allocated once at max fft_size) |
 
 #### DFT/IDFT Resolution
 
 The DFT and IDFT function pointers are resolved at runtime from the main OAI binary using `dlsym()`:
 
 ```c
 p_dft  = dlsym(RTLD_DEFAULT, "dft");
 p_idft = dlsym(RTLD_DEFAULT, "idft");
 ```
 
 This works because `nr-softmodem` is compiled with `-rdynamic`, exporting all symbols to dynamically-loaded libraries. The function pointers operate on `int16_t*` (i.e. `c16_t*`) arrays with standard OAI fixed-point scaling.
 
 ## Control Flow
 
 ### Simulation.cpp Integration
 
 In `rfsimulator_read_internal()` (around line 1325), the original:
 ```cpp
 rxAddInput(input, temp_array[aarx], aarx, ptr->channel_model, nsamps);
 ```
 
 is replaced with:
 ```cpp
 rxAddInput_srsfile(input, temp_array[aarx], aarx, ptr->channel_model, nsamps);
 ```
 
 The function internally decides whether to run SRS replay or fall back to the original `rxAddInput()`.
 
 ## Usage
 
 ### Recording
 
 No special build flags needed. The recording runs automatically on any gNB with SRS enabled:
 
 ```bash
 ./build_oai -w USRP --ninja --nrUE --gNB --build-lib "nrscope"
 
 # Run once (OTA or with any channel model)
 sudo ./nr-softmodem -O gnb.conf --rfsimulator.serveraddr 127.0.0.1
 sudo ./nr-uesoftmodem -O ue.conf --rfsimulator.serveraddr 127.0.0.1
 ```
 
 After ~20 SRS slots, the file `/tmp/srs_channel.bin` is written and recording stops.
 
 ### Replay
 
 ```bash
 # Same build; set SRS_CHANNEL_FILE to enable replay:
 SRS_CHANNEL_FILE=/tmp/srs_channel.bin \
   sudo ./nr-softmodem -O gnb.conf \
     --rfsimulator.serveraddr 127.0.0.1
 
 sudo ./nr-uesoftmodem -O ue.conf --rfsimulator.serveraddr 127.0.0.1
 ```
 
 Without `SRS_CHANNEL_FILE`, the normal RFSim TDL channel model runs as usual.
 
 ### Switching modes
 
 | Method | Effect |
 |--------|--------|
 | `SRS_CHANNEL_FILE=/path sudo ./nr-softmodem ...` | SRS replay (if file loads), else rxAddInput fallback |
 | `unset SRS_CHANNEL_FILE; sudo ./nr-softmodem ...` | Normal TDL channel (AWGN/EPA/EVA/ETU) |
 | `rm -f /tmp/srs_channel.bin` | Also forces normal mode (env var default path won't find a file) |
 
 ## Key Design Decisions
 
 1. **Environment variable, not config option**: The OAI config parser runs before `librfsimulator.so` is loaded, so command-line options in the rfsimulator section would be rejected. The env var `SRS_CHANNEL_FILE` bypasses this.
 
 2. **Lazy loading**: The file is not loaded at init — it's loaded on the first hardware channel-apply call. This avoids ordering dependencies between device init and PHY init.
 
 3. **Slot cycling**: With ≤ 20 recorded slots, the replay cycles: `slot_idx = current_slot % num_slots`. For quasi-static channels this is fine.
 
 4. **Block-based FFT**: The input is processed in `fft_size`-sample blocks to avoid FFTing the entire buffer at once.
 
 5. **AWGN and path loss**: These are applied separately (same as original `rxAddInput`) so SNR and gain settings work identically in both modes.
 
 ## Known Limitations
 
 | Limitation | Impact |
 |------------|--------|
 | Per-slot H, not per-OFDM-symbol | Phase discontinuity at symbol boundaries within a slot. Acceptable for quasi-static. |
 | No interpolation fill for unrecorded subcarriers | Only the interpolated SRS grid is stored; zeros outside SRS bandwidth. |
 | DFT must support `fft_size` | OAI supports standard sizes (128..4096). Check your config. |
 | `dlsym()` fallback | Requires `-rdynamic` on the main binary (already enabled). |
