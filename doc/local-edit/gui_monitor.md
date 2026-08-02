# OAI UE Downlink Performance Monitor (GUI)

## 1. Overview

The monitor consists of two parts:

1. **OAI NR UE C patches**: write the CSI-RS channel estimate and per-slot DL
   measurements into POSIX shared memory while the UE PHY runs.
2. **Python GUI (`gui/`)**: reads those shared memory regions, runs iperf3,
   plots real-time throughput / PDSCH metrics / CSI channel quality, and saves
   logs for offline analysis.

```
OAI NR UE (C)
  |
  |  csi_rx.c  ->  ue_shm_write_csi_rs()
  |  phy_procedures_nr_ue.c -> ue_shm_write_meas_dl()
  |
  v
/dev/shm/csi_rs_channel      /dev/shm/meas_dl
  |
  v
Python GUI (gui/oai_perf_monitor.py)
  |
  |-- gui/iperf_controller.py   -> iperf3 process + real-time parsing
  |-- gui/meas_reader.py        -> /dev/shm/meas_dl
  |-- gui/csi_reader.py         -> /dev/shm/csi_rs_channel + capacity
  |-- gui/plot_manager.py       -> pyqtgraph plots
  |
  v
gui/iperf.log
gui/record/gui_log_<timestamp>.csv
gui/record/gui_log_<timestamp>/channel_<timestamp>.npy
```

## 2. C side: data saving from OAI

### 2.1 Files

| File | Purpose |
|---|---|
| `openair1/PHY/NR_UE_TRANSPORT/ue_shm.h` | Shared structs and API declarations |
| `openair1/PHY/NR_UE_TRANSPORT/ue_shm.c` | POSIX `shm_open` / `mmap` implementation |
| `openair1/PHY/NR_UE_TRANSPORT/csi_rx.c` | Writes CSI-RS frequency-domain channel estimate |
| `openair1/SCHED_NR_UE/phy_procedures_nr_ue.c` | Writes per-slot DL measurements |
| `CMakeLists.txt` | Adds `ue_shm.c` to the build |

### 2.2 Shared memory regions

#### `/dev/shm/csi_rs_channel`

Fixed allocation:

```c
CSI_RS_MAX_RX_ANT = 4
CSI_RS_MAX_PORTS  = 8
CSI_RS_MAX_FFT    = 4096
```

Layout:

```text
[csi_rs_shm_hdr_t] + [channel data]

csi_rs_shm_hdr_t:
  magic, seq, frame, slot,
  num_rx_ant, num_ports, fft_size, n_rb_dl, subcarrier_spacing

channel data:
  c16_t[num_rx_ant][num_ports][fft_size]
  (each c16_t = int16 real + int16 imag, 4 bytes)
```

The channel is written from `nr_ue_csi_rs_procedures()` after
`nr_csi_rs_channel_estimation()`.

#### `/dev/shm/meas_dl`

Packed `meas_dl_shm_t`:

```text
seq, frame, slot,
mcs, qam_mod_order, tbs, num_layers, num_rbs, num_symbols,
rv, new_data_indicator, target_code_rate,
bitrate_bps, dlsch_received, dlsch_errors, dlsch_fer,
rsrp_dBm, rssi_dBm, wideband_sinr_dB,
n_rb_dl, subcarrier_spacing, freq_offset, nb_antennas_rx
```

### 2.3 Write behavior

- `ue_shm_write_csi_rs()` is called whenever CSI-RS channel estimation runs.
- `ue_shm_write_meas_dl()` is called only when PDSCH was scheduled in the
  current slot (`meas_active == true`). Idle slots do **not** overwrite the
  last valid measurement snapshot.
- MCS/Qm/TBS/layers/nPRB are recorded even if PDSCH demodulation or decoding
  fails, so the GUI still shows the scheduled parameters.
- RSRP is read from `ue->measurements.ssb_rsrp_dBm[frame_parms.ssb_index]`.
  The old `rsrp_dBm[0]` field is not populated by NR UE PHY and is no longer
  used.
- Both regions use a monotonic `seq` counter and a memory barrier. The writer
  fills data, executes `__sync_synchronize()`, then increments `seq`.

## 3. Python program structure

All files are under `gui/`.

| File | Role |
|---|---|
| `oai_perf_monitor.py` | Main PyQt5 window, 500 ms refresh timer, UL/DL buttons, CSV and channel saving |
| `iperf_controller.py` | QThread running iperf3, appends to `gui/iperf.log`, parses `X bits/sec` lines |
| `meas_reader.py` | Reads `/dev/shm/meas_dl` with `ctypes`, returns a dict of PDSCH/RF metrics |
| `csi_reader.py` | Reads `/dev/shm/csi_rs_channel`, computes capacity, singular values, rank, condition number |
| `plot_manager.py` | Three pyqtgraph panels: throughput, PDSCH metrics, CSI quality |
| `config.json` | `bs_ip`, `iperf_port`, `iperf_time`, `log_file`, `dl_reverse_client`, `snr_db` |
| `requirements.txt` | `PyQt5`, `pyqtgraph`, `numpy` |
| `run_perf_gui.sh` | Activates `.venv` and launches `python3 -m gui.oai_perf_monitor` |

### 3.1 Main refresh loop

Every 500 ms, `oai_perf_monitor.py` performs one synchronized sample:

```text
1. read /dev/shm/meas_dl
2. read /dev/shm/csi_rs_channel
3. if new CSI data -> save channel as .npy
4. if iperf3 running -> use latest parsed throughput
5. append one row to gui/record/gui_log_<timestamp>.csv
6. update throughput / PDSCH / CSI plots
```

### 3.2 iperf3 real-time output

iperf3 uses full buffering when stdout is a pipe, so all lines can arrive only
after the test finishes. `iperf_controller.py` therefore prefixes the command
with:

```bash
stdbuf -oL -eL iperf3 ...
```

when `stdbuf` is available. This forces line-by-line output and keeps the
throughput plot updating while the test is still running.

The parser accepts interval lines and summary lines, e.g.:

```text
[  5]   6.00-7.00   sec  19.2 MBytes   161 Mbits/sec
[  5]   0.00-30.00  sec   537 MBytes   150 Mbits/sec  sender
```

### 3.3 CSI channel-quality calculation

For every non-zero-energy subcarrier `H(k)`:

```text
sigma = svd(H(k))
capacity(k)  = sum_i log2(1 + rho * sigma_i^2), rho = 10^(snr_db/10)
condition(k) = sigma_max / max(sigma_min, eps), capped at 1000
rank(k)      = count(sigma_i > max(eps, 1e-3 * sigma_max))
```

Valid subcarriers are averaged. The average singular values are exposed as
`singular_values` and used in the CSV.

## 4. Outputs

### 4.1 `gui/iperf.log`

All iperf3 stdout is appended in real time.

### 4.2 `gui/record/gui_log_<timestamp>.csv`

Created when UL or DL is started. Column layout:

```text
timestamp, throughput_mbps,
sv0 ... sv7, capacity, rank, condition_number,
frame, slot, mcs, qm, tbs_bits, layers, nprb, nsymb, rv,
new_data_indicator, target_code_rate, bitrate_bps,
dlsch_received, dlsch_errors, bler,
rsrp_dBm, rssi_dBm, sinr_dB, freq_offset_hz,
n_rb_dl, scs, nb_antennas_rx
```

`timestamp` is local wall-clock time in ISO format:

```text
2026-08-02 22:09:03.019
```

### 4.3 `gui/record/gui_log_<timestamp>/channel_<timestamp>.npy`

When a fresh CSI-RS channel is read, the complex channel array used for
capacity calculation is saved with `numpy.save()` inside the same-name folder
as the CSV test record:

```text
shape = [num_rx_ant, num_ports, fft_size]
dtype = complex128
```

Example load:

```python
import numpy as np
h = np.load("gui/record/gui_log_20260802_220903/channel_20260802_220903_019759.npy")
print(h.shape, h.dtype)
```

## 5. Usage

### 5.1 Build OAI with the C patches

```bash
cd openairinterface5g
source oaienv
cd cmake_targets
./build_oai --nrUE --build-everything
```

Start the NR UE as usual. It automatically creates:

```text
/dev/shm/csi_rs_channel
/dev/shm/meas_dl
```

### 5.2 Create the Python environment

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r gui/requirements.txt
```

### 5.3 Run the GUI

```bash
./gui/run_perf_gui.sh
```

On a Wayland GNOME session, the launcher automatically sets
`QT_QPA_PLATFORM=wayland`.

### 5.4 GUI controls

| Button | Action |
|---|---|
| `UL` | Starts `iperf3 -c <bs_ip> -t <duration>` |
| `DL` | Starts the DL command from `config.json`; default is UE-side server, configurable to reverse client |
| `Stop` | Terminates iperf3 and closes the current CSV |

The left panel shows:

```text
Throughput: 150.0 Mbps
BLER / RSRP / SINR / MCS / NPRB / Layers / Qm / TBS / Freq offset
```

The right panel contains three plots:

1. Throughput
2. PDSCH metrics, switchable: `bler / rsrp / sinr / mcs / nprb`
3. CSI channel quality, switchable: `capacity / rank / condition_number`

## 6. Notes

- `gui/config.json` `log_file` points to `gui/iperf.log`.
- The DL iperf3 command is site-specific and can be edited in
  `gui/iperf_controller.py` (`start_dl_server`). The current implementation
  preserves the local test command:
  `iperf3 -B 10.0.0.<port> -c 192.168.70.135 -t 30`.
- If that command is used without `-R`, it is an uplink test; the UE receives
  little or no PDSCH and the measurement panel will show
  `No PDSCH samples yet.` Use a real DL flow to populate MCS/Qm/layers/nPRB.
- If the GUI cannot find an interactive Qt backend, it falls back to
  non-interactive plotting; install `python3-tk` or a Qt backend for the live
  window.
