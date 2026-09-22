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

Set `CHANNEL_FILE` on the modem process whose receive path should be replayed:

```bash
CHANNEL_FILE=/tmp/srs_channel.bin \
  ./cmake_targets/ran_build/build/nr-softmodem \
  -O targets/PROJECTS/GENERIC-NR-5GC/CONF/gnb.sa.band78.fr1.106PRB.2x2.usrpn300.conf \
  --rfsim
```

`chanmod` is not required for FD replay.

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

### 5.5 CP layouts

Fd replay accepts exactly one of these slot sizes:

Regular first CP:

```text
symbols_per_slot * (fft_size + cp_length)
```

Long first CP:

```text
cp_length0
+ fft_size
+ (symbols_per_slot - 1) * (fft_size + cp_length)
```

Any other read-block size disables FD replay and falls back to the ordinary
RFSim receive path.

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

### Replay disables during runtime

Check the warning:

```text
[rfsim] Disabling fd channel replay: unsupported read size ...
```

The configured CP and FFT metadata must match the runtime slot layout.

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
