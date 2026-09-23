# RFSim Frequency-Domain Channel Replay

## 1. Purpose

This document describes the current RFSim replay implementation for recorded
SRS and CSI-RS channel snapshots.

The current implementation is a **single-slot frequency-domain replay**:

1. One GUI `.npy` snapshot is converted into one `FDCH` binary file.
2. RFSim loads that file once when the radio device is initialized.
3. The same H matrix is applied to every received NR slot.
4. Each TX OFDM symbol is transformed to frequency domain.
5. Every RX signal is formed as `Y[rx] = sum_tx(H[rx][tx] * X[tx])`.
6. The result is transformed back to time domain and the CP is restored.

The old time-domain sparse-tap replay and `apply_channel_fd.c` path are no
longer used.

## 2. Recorded Direction

`CHANNEL_FILE` controls the receive direction of the process where it is set.

| Recording | Recorded direction | Typical replay process |
|---|---|---|
| SRS on gNB | UE to gNB | gNB, to replay uplink |
| CSI-RS on UE | gNB to UE | UE, to replay downlink |
| Transposed recording | Reciprocal direction | Opposite side, when reciprocity is intended |

Use `--transpose` in the converter when the recorded matrix must be used in
the opposite direction.

Replay replaces the ordinary RFSim channel model. The configured
`channelDesc->path_loss_dB` and `channelDesc->noise_power_dB` are not applied
by the FD replay branch. The common RFSim global-noise path is unchanged.

## 3. Converter

The converter is:

```text
gui/npy_to_rfsim_bin.py
```

It accepts exactly one `.npy` snapshot. Passing multiple files, a directory
containing multiple snapshots, or a multi-slot recording is rejected.

### 3.1 Supported `.npy` layouts

```text
CSI-RS: (n_rx, n_tx, fft_size)
SRS:    (n_rx, n_tx, n_symbols, fft_size)
```

For a 4D SRS snapshot, `--symbol` selects one OFDM symbol. The resulting
frequency-domain matrix is still a single channel slot.

### 3.2 Convert SRS

```bash
.venv/bin/python gui/npy_to_rfsim_bin.py \
  --kind srs \
  --n-rb 106 \
  --scs 30000 \
  gui/record/gnb_log_20260806_215012/srs_*.npy \
  --output /tmp/srs_channel.bin
```

### 3.3 Convert CSI-RS

```bash
.venv/bin/python gui/npy_to_rfsim_bin.py \
  --kind csi \
  --n-rb 106 \
  --scs 30000 \
  gui/record/gui_log_20260806_222820/channel_*.npy \
  --output /tmp/csi_rs_channel.bin
```

### 3.4 Subcarrier mapping

OAI channel estimates are stored in estimator order. The converter maps each
saved subcarrier to a physical FFT bin:

```text
physical_bin = (saved_index + subcarrier_offset) mod fft_size
```

The resulting file is stored directly in FFT-bin order, so runtime replay does
not apply another offset.

Default offsets:

```text
SRS:    fft_size / 2
CSI-RS: fft_size - n_rb * 12 / 2
```

Override with:

```bash
--subcarrier-offset <value>
```

### 3.5 CP metadata

The converter records both NR CP lengths:

```text
cp_length  = fft_size / 128 * 9
cp_length0 = fft_size / 128 * (9 + 2^mu)
mu         = log2(scs / 15000)
```

`cp_length` is used by normal symbols. `cp_length0` is used by symbol 0 of
slots that use the long first CP.

The defaults can be overridden:

```bash
--symbols-per-slot 14
--cp-length 144
--cp-length0 176
```

### 3.6 Verify conversion

```bash
.venv/bin/python gui/npy_to_rfsim_bin.py \
  --verify /tmp/srs_channel.bin
```

Successful output:

```text
OK /tmp/srs_channel.bin: FDCH v1, 2x2, fft=2048,
symbols=14, cp=144, cp0=176, n_rb=106, scs=30000
```

Verification checks:

- magic and version;
- single-slot payload size;
- antenna, FFT, symbol, CP, PRB, and SCS fields;
- absence of trailing bytes;
- finite, non-zero coefficients.

## 4. Binary Format

All fields use little-endian encoding.

### 4.1 Header

The header is 64 bytes:

| Offset | Size | Field | Description |
|---:|---:|---|---|
| 0 | 4 | `magic` | ASCII `FDCH` |
| 4 | 2 | `version` | `1` |
| 6 | 1 | `num_rx_ant` | Number of RX antennas |
| 7 | 1 | `num_tx_ant` | Number of TX antennas |
| 8 | 2 | `fft_size` | FFT size in samples |
| 10 | 2 | `symbols_per_slot` | Normally 14 |
| 12 | 2 | `cp_length` | Normal CP length |
| 14 | 2 | `cp_length0` | Long CP length for symbol 0 |
| 16 | 2 | `n_rb` | Number of recorded PRBs |
| 18 | 2 | `reserved` | Zero |
| 20 | 4 | `subcarrier_spacing` | SCS in Hz |
| 24 | 4 | `num_slots` | Must be `1` |
| 28 | 36 | `reserved` | Zero |

### 4.2 Payload

The payload contains:

```text
num_rx_ant * num_tx_ant * fft_size complex64 values
```

Each complex64 value contains two little-endian float32 values:

```text
real, imaginary
```

Matrix order:

```text
[rx][tx][physical_fft_bin]
```

The file contains only one channel slot.

## 5. Runtime Replay

### 5.1 Activation

Run the commands from `cmake_targets/ran_build/build`.

Terminal 1, gNB:

```bash
sudo env CHANNEL_FILE=/tmp/srs_channel.bin \
  ./nr-softmodem \
  -O ../../../targets/PROJECTS/GENERIC-NR-5GC/CONF/gnb.sa.band78.fr1.106PRB.2x2.usrpn300.conf \
  --gNBs.[0].min_rxtxtime 3 \
  --rfsim \
  --rfsimulator.[0].serveraddr server
```

Terminal 2, UE:

```bash
sudo env CHANNEL_FILE=/tmp/srs_channel.bin \
  ./nr-uesoftmodem \
  -r 106 \
  --numerology 1 \
  --band 78 \
  -C 3319680000 \
  --ue-nb-ant-tx 2 \
  --ue-nb-ant-rx 2 \
  --uecap_file ../../../targets/PROJECTS/GENERIC-NR-5GC/CONF/uecap_ports2.xml \
  -O ../../../ci-scripts/conf_files/nrue.uicc.conf \
  --rfsim
```

`chanmod` is not required for FD replay.
`CHANNEL_FILE` should be set on each side whose receive direction should replay
the recorded H. The example sets it on both processes.

Expected load message:

```text
[HW] [rfsim] Loaded fd channel file: /tmp/srs_channel.bin
     (1 slot, 2x2, fft=2048, symbols=14, cp=144, cp0=176)
```

The file is loaded once in `device_init()`.

### 5.2 Load-time validation

The following are fatal:

- `CHANNEL_FILE` points to a missing or unreadable file;
- invalid magic or version;
- `num_slots != 1`;
- unsupported FFT size;
- antenna dimensions different from the configured RF channels;
- CP values larger than the FFT size;
- truncated or oversized payload;
- non-finite or all-zero H data.

### 5.3 Normalization

The loader computes one global RMS over all non-zero H coefficients:

```text
rms = sqrt(sum(|H|^2) / number_of_nonzero_coefficients)
H_normalized = H / rms
```

Zero coefficients remain zero. This keeps unused FFT bins from affecting the
normalization level.

### 5.4 Slot processing

For each received RFSim slot, `simulator.cpp`:

1. Combines incoming packets into TX time-domain buffers.
2. Validates the configured TX/RX dimensions against the file.
3. Selects the CP layout from the read-block size.
4. Removes the CP from each OFDM symbol.
5. Calls `fd_apply_symbol()`.
6. Restores the CP from the processed symbol tail.
7. Writes the result to the RFSim channel-modelling accumulator.

`fd_apply_symbol()` performs:

```text
for each TX:
    time-domain symbol -> forward FFT

for each RX:
    for each frequency bin:
        Y[rx][bin] = sum_tx X[tx][bin] * H[rx][tx][bin]

for each RX:
    Y[rx] -> inverse FFT
```

The forward and inverse transforms use OAI's `dft` and `idft`
implementations.

### 5.5 Supported read blocks

The replay layer accepts a complete sequence of one or more OFDM symbols from
the beginning of a slot:

```text
regular sequence:
  N * (fft_size + cp_length)

long-first sequence:
  fft_size + cp_length0
  + (N - 1) * (fft_size + cp_length)
```

where `1 <= N <= symbols_per_slot`. This includes:

- a complete regular-CP slot;
- a complete long-first-CP slot;
- a standalone regular-CP symbol;
- a standalone long-first-CP symbol.

The last case is used by `nr-uesoftmodem` immediately after synchronization.
For the usual 30 kHz/106 PRB configuration, the standalone long-CP symbol is:

```text
2048 + 176 = 2224 samples
```

This block is now processed by FD replay instead of disabling it.

If a synchronization or re-synchronization read ends with an unaligned sample
block, that individual block bypasses FD replay and uses the ordinary receive
path. Replay remains enabled and resumes on the next complete symbol or slot.
It is not permanently disabled by an unaligned read.

Dimension mismatches or errors inside FFT/IFFT processing remain fatal for the
active replay session and disable the FD branch.

The file contains only one H slot. It is reused for every successful replay
slot; there is no slot counter or SFN/slot-number lookup.

## 6. Build and Tests

Build the RFSim module:

```bash
cmake --build <build-dir> --target rfsimulator
```

The C test covers:

- `fd_cfft()` forward/inverse roundtrip;
- identity H replay;
- delayed frequency-domain H replay;
- single-slot loader behavior.

Run it with:

```bash
ctest --test-dir <build-dir> -R test_fd_channel --output-on-failure
```

Python converter tests:

```bash
.venv/bin/python -m unittest gui.tests.test_npy_to_rfsim_bin
```

## 7. Troubleshooting

### Replay file is not loaded

Check that `CHANNEL_FILE` is visible to the modem process. With `sudo`, use:

```bash
sudo env CHANNEL_FILE=/tmp/srs_channel.bin <modem-command>
```

### An unaligned read block is reported

Check the warning:

```text
[rfsim] fd channel replay bypasses unaligned read size ...
```

This is expected for a partial synchronization block. The current block uses
the ordinary receive path, while replay remains enabled for later complete
symbols and slots.

If replay is disabled permanently, check the configured FFT, CP, and antenna
dimensions against the runtime configuration.

### Channel dimensions do not match

The file antenna dimensions must match the RF device configuration. For
example, a 2x2 file cannot be loaded by a 1x1 RFSim configuration.

## 8. Implementation Map

| File | Responsibility |
|---|---|
| `radio/rfsimulator/fd_channel.c` | File loader, RMS normalization, FFT wrapper, symbol processing |
| `radio/rfsimulator/fd_channel.h` | Replay API |
| `radio/rfsimulator/simulator.cpp` | Device initialization, slot splitting, CP handling, fallback |
| `gui/npy_to_rfsim_bin.py` | `.npy` to `FDCH` converter and verifier |
| `radio/rfsimulator/tests/test_fd_channel.c` | Replay unit test |

## 9. Complete-Replay Alignment Options

The current implementation supports complete symbols and slots, but bypasses
an individual read block whose samples do not end on an OFDM symbol boundary.

UE timing correction can produce blocks such as:

```text
2224
28495
30176
```

For `fft_size=2048`, `cp_length=144`, and `cp_length0=176`:

```text
regular symbol = 2192 samples
long-CP symbol = 2224 samples

28495 = 13 * 2192 - 1
30176 = 13 * 2192 + 1680
```

`2224` is a complete long-CP symbol. The other two are not complete symbol
boundaries and require a buffering or alignment design to replay every sample.

### 9.1 Method 1: Align UE reads before RFSim

Add a UE-side sample FIFO around the RFSim read path, preferably as an
RFSim-replay-specific wrapper around `nrue_ru_read()`.

The FIFO would:

1. Receive arbitrary sample requests from the UE PHY.
2. Request symbol-aligned sample amounts from the RFSim device.
3. Return exactly the number and timestamp requested by the PHY.
4. Retain the extra samples for the next call.
5. Reset alignment state after synchronization, re-synchronization, or a
   timestamp discontinuity.

Important implementation points:

- preserve the existing `firstTS` and returned `ptimestamp` mapping;
- account for `iq_shift_to_apply` and timing-advance changes;
- support positive and negative timing shifts;
- maintain one FIFO per active RF device/UE when multiple RUs are used;
- bypass or reset the FIFO if the RFSim stream is restarted.

Estimated impact:

| Item | Assessment |
|---|---|
| Main files | `executables/nr-ue-ru.c`, limited integration in `executables/nr-ue.c` |
| Estimated code change | 150-300 lines |
| Main risk | UE synchronization and timestamp regressions |
| Added replay latency | None if one symbol is prefetched |
| Testing effort | Medium-high |
| Estimated effort | 3-5 engineering days |

Method 1 is appropriate when the requirement is complete replay specifically
for the UE side.

### 9.2 Method 2: Stateful alignment inside RFSim

Move symbol alignment and buffering into `fd_channel` or the RFSim read path.
Each peer and beam must maintain:

- current symbol and CP phase;
- partial input symbol history;
- processed output FIFO;
- absolute timestamp mapping;
- packet lookahead and packet lifetime state.

If the current request ends in the middle of an OFDM symbol, RFSim must obtain
samples from a later timestamp before it can perform the FFT. This requires
either:

- one-symbol lookahead, increasing read latency; or
- delayed output with timestamp compensation, which risks changing the
  timestamp contract expected by the modem.

Estimated impact:

| Item | Assessment |
|---|---|
| Main files | `radio/rfsimulator/fd_channel.c/.h`, `radio/rfsimulator/simulator.cpp`, `buffer_t` state |
| Estimated code change | 300-600 lines |
| Main risk | multi-peer, multi-beam, timestamp, and packet-lifetime regressions |
| Added replay latency | Usually one OFDM symbol |
| Testing effort | High |
| Estimated effort | 1-2 engineering weeks |

Method 2 is appropriate when complete replay must be guaranteed for every
caller and every arbitrary read split.

### 9.3 Recommended approach

For the current UE-side issue, Method 1 is preferred:

- its scope is limited to the UE RF adapter;
- the existing `fd_channel` contract remains simple;
- no additional replay latency is required;
- RFSim peer, beam, and packet handling remain unchanged.

Method 2 should be selected if complete replay is later required for arbitrary
modems, including gNB-side and multi-peer configurations.

Acceptance tests for Method 1 should include:

- `2224`, `28495`, and `30176` sample requests;
- full regular and long-first slots;
- initial synchronization and re-synchronization;
- positive and negative timing shifts;
- 2x2 and 4x4 antenna configurations;
- no timestamp or timing-advance regression;
- no steady-state bypass or replay-disable messages.

### 9.4 Method 1 implementation status

The first implementation of Method 1 is present in `executables/nr-ue-ru.c`.

It adds a per-card replay FIFO around the RFSim read function:

- the UE PHY can still request arbitrary sample counts;
- the adapter requests exactly one complete OFDM symbol from RFSim at a time;
- symbol length is selected from `get_samples_symbol_duration()`, so the
  adapter follows the normal CP and long-CP layout;
- requested samples are copied out of the FIFO at the original timestamp;
- extra prefetched samples remain queued for the next call.

The FIFO stores samples interleaved by antenna and allocates storage according
to the runtime antenna count. There is one state object per `MAX_CARDS`
entry, so the implementation has no fixed 2x2 assumption and can be used by
4x4 or multi-RU configurations.

Multi-antenna behavior:

- `num_antennas` is taken from each read call, and a change resets the FIFO;
- fetch buffers contain one contiguous symbol per antenna;
- queued samples are indexed as `[sample][antenna]`;
- each output antenna is copied independently.

The implementation is enabled only when:

```text
device type == RFSIMULATOR
CHANNEL_FILE is set
```

Initial 2x2 validation completed with:

- `CHANNEL_FILE` set on the UE;
- successful initial synchronization;
- sustained DL/UL scheduling;
- no `Disabling fd channel replay` messages;
- no unaligned bypass in steady state.

The 4x4 path uses the same dynamic per-antenna loops, but still requires an
end-to-end runtime test with a matching 4x4 `FDCH` file.
