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
Python GUI (gui/oai_ue_monitor.py)
  |
  |-- gui/iperf_controller.py   -> iperf3 process + real-time parsing
  |-- gui/meas_reader.py        -> /dev/shm/meas_dl
  |-- gui/csi_reader.py         -> /dev/shm/csi_rs_channel + capacity
  |-- gui/plot_manager.py       -> pyqtgraph plots
  |
  v
gui/iperf.log
gui/record/gui_ue_log_<timestamp>.csv
gui/record/gui_ue_log_<timestamp>/channel_<timestamp>.npy
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
rsrp_dBm, rsrp_per_ant_dBm[4], rssi_dBm, wideband_sinr_dB,
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
  Per-antenna SS-RSRP is copied from
  `ue->measurements.ssb_rsrp_per_ant_dBm[ssb_index][]` into
  `meas_tmp.rsrp_per_ant_dBm[]`.
  The old `rsrp_dBm[0]` field is not populated by NR UE PHY and is no longer
  used.
- Both regions use a monotonic `seq` counter and a memory barrier. The writer
  fills data, executes `__sync_synchronize()`, then increments `seq`.

## 3. Python program structure

All files are under `gui/`.

| File | Role |
|---|---|
| `oai_ue_monitor.py` | Main PyQt5 window, 500 ms refresh timer, UL/DL buttons, iperf.log-triggered CSV/channel saving |
| `iperf_controller.py` | QThread running iperf3, appends to `gui/iperf.log`, parses `X bits/sec` lines |
| `meas_reader.py` | Reads `/dev/shm/meas_dl` with `ctypes`, returns a dict of PDSCH/RF metrics |
| `csi_reader.py` | Reads `/dev/shm/csi_rs_channel`, computes capacity, singular values, rank, condition number |
| `plot_manager.py` | Three pyqtgraph panels: throughput, PDSCH metrics, CSI quality, plus the per-antenna RSRP bar chart |
| `config.json` | `bs_ip`, `iperf_port`, `iperf_time`, `log_file`, `dl_reverse_client`, `snr_db` |
| `requirements.txt` | `PyQt5`, `pyqtgraph`, `numpy` |
| `run_ue_gui.sh` | Activates `.venv` and launches `python3 -m gui.oai_ue_monitor` |

### 3.1 Main refresh loop

Every 500 ms, `oai_ue_monitor.py` refreshes the UI. CSV and channel
recording are no longer tied to that fixed timer; they are triggered by changes
to `iperf.log`.

```text
1. read /dev/shm/meas_dl
2. read /dev/shm/csi_rs_channel
3. check whether gui/iperf.log changed (size and mtime)
4. if changed:
     save latest CSI channel as channel_<timestamp>.npy
     append one row to gui/record/gui_ue_log_<timestamp>.csv
5. update throughput / PDSCH / CSI plots every 500 ms
```

The log-change baseline is reset when the run CSV is opened at GUI startup, so
an existing `iperf.log` is not treated as a new event just because the GUI was
restarted.

### 3.2 iperf3 real-time output

iperf3 uses full buffering when stdout is a pipe, so all lines can arrive only
after the test finishes. `iperf_controller.py` therefore prefixes the command
with:

```bash
stdbuf -oL -eL iperf3 ...
```

when `stdbuf` is available. This forces line-by-line output and keeps the
throughput plot updating while the test is still running.

The parser accepts 1-second interval lines and ignores the final total-time
summary lines, e.g.:

```text
[  5]   6.00-7.00   sec  19.2 MBytes   161 Mbits/sec
[  5]   0.00-30.00  sec   537 MBytes   150 Mbits/sec  sender  # ignored
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

The file is cleared when the GUI starts, so each GUI run begins with an empty
iperf log.

### 4.2 `gui/record/gui_ue_log_<timestamp>.csv`

Created once when the GUI starts; one CSV file is used for the whole GUI run.
Column layout:

```text
timestamp, test_round, throughput_mbps,
sv0 ... sv7, capacity, rank, condition_number,
frame, slot, mcs, qm, tbs_bits, layers, nprb, nsymb, rv,
new_data_indicator, target_code_rate, bitrate_bps,
dlsch_received, dlsch_errors, bler,
rsrp_dBm, rssi_dBm, sinr_dB, freq_offset_hz,
rsrp_ant0_dBm, rsrp_ant1_dBm, rsrp_ant2_dBm, rsrp_ant3_dBm,
n_rb_dl, scs, nb_antennas_rx
```

`timestamp` is local wall-clock time in `%Y%m%d_%H%M%S_%f` format:

```text
20260802_223124_731830
```

`test_round` identifies the iperf test round. On client-side tests it is
incremented each time UL/DL is clicked. On server-side tests it is incremented
when the iperf log resumes after an idle gap. The counter resets to zero when
the GUI process restarts.

### 4.3 `gui/record/gui_ue_log_<timestamp>/channel_<timestamp>.npy`

When `iperf.log` changes, the latest complex channel array used for capacity
calculation is saved with `numpy.save()` inside the same-name folder as the CSV
test record. The CSV row and channel `.npy` file use the same timestamp:

```text
shape = [num_rx_ant, num_ports, fft_size]
dtype = complex128
```

Example load:

```python
import numpy as np
h = np.load("gui/record/gui_ue_log_20260802_220903/channel_20260802_220903_019759.npy")
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
./gui/run_ue_gui.sh
```

On a Wayland GNOME session, the launcher automatically sets
`QT_QPA_PLATFORM=wayland`.

### 5.4 GUI controls

| Button | Action |
|---|---|
| `UL` | Starts `iperf3 -c <bs_ip> -t <duration>` |
| `DL` | Starts the DL command from `config.json`; default is UE-side server, configurable to reverse client |
| `Stop` | Terminates iperf3; does not close or create a new CSV |
| `Restart` | Closes shared-memory readers and relaunches the GUI, which creates a new CSV for the new run |

The left panel shows:

```text
Throughput: 150.0 Mbps
BLER / RSRP / SINR / MCS / NPRB / Layers / Qm / TBS / Freq offset
Ant RSRP: RX0: -88 dBm, RX1: -91 dBm
per-antenna RSRP bar chart
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

## 7. Local modifications: iperf.log-triggered recording and exit cleanup

These changes are implemented in `gui/oai_ue_monitor.py`.

### 7.1 CSV and channel recording

- One CSV file is created at GUI startup and used for the whole run. Clicking
  `UL`/`DL` or `Stop` does not create a new file.
- `test_round` is written in every CSV row. Client-side tests increment it on
  each `UL`/`DL` click; server-side tests increment it when the iperf log
  resumes after an idle gap.
- `_iperf_log_changed()` compares the current `iperf.log` `st_size` and
  `st_mtime_ns` with the last seen state.
- `_reset_log_state()` records the file state when the run CSV is opened.
  This prevents old log contents from creating a false first sample.
- `_refresh()` only calls `_save_channel()` and `_write_csv()` when
  `_iperf_log_changed()` returns true.
- The channel snapshot and CSV row use the same timestamp, so they can be
  aligned directly in offline analysis.
- The CSV header and every appended row are flushed to disk immediately.

### 7.2 Startup cleanup and exit-time archive

- `closeEvent()` stops the timer and iperf3 process, closes the CSV, and closes
  the shared-memory readers.
- At startup, the configured `iperf.log` is cleared, so each GUI run starts with
  an empty log.
- On exit, the GUI asks whether the CSV, channel data, and iperf log should be
  saved.
- The default archive folder name is the first CSV timestamp. If the folder
  already exists, the GUI asks for another name.
- Choosing **OK** moves the CSV, channel/srs files, and iperf log into:
  `gui/record/<user_folder>/`
- Choosing **Cancel** leaves the data in their original locations.

### 7.3 Log path handling

- `_resolve_log_path()` resolves `config.json` `log_file` relative to the
  repository root when the path is not absolute.
- The resolved path is passed to `IperfController`, so the file being monitored
  is the same file iperf3 writes to.

These changes make each CSV row and channel record correspond to a real iperf
log update event instead of being generated by the 500 ms UI timer.

## 8. SNR values recorded by the UE / gNB monitors

The two monitors record different SNR values. They are not the same quantity;
each is defined below with its source in the OAI code and the CSV column that
stores it.

### 8.1 UE monitor - `sinr_dB`

- CSV column: `sinr_dB`
- SHM: `/dev/shm/meas_dl` -> `meas_dl_shm_t.wideband_sinr_dB`
- Written by `ue_shm_write_meas_dl()` in
  `openair1/SCHED_NR_UE/phy_procedures_nr_ue.c:1357`
- Value (`openair1/PHY/NR_UE_ESTIMATION/nr_ue_measurements.c:99`):

  ```text
  wideband_cqi_tot[gNB_id] = rx_power_tot_dB[gNB_id] - n0_power_tot_dB
  ```

- Definition: a UE-PHY **wideband DL SINR** (in dB x10) derived from the DL
  channel estimates (`dl_ch_estimates`) over the full bandwidth
  (`number_rbs * NR_NB_SC_PER_RB` REs) as total received signal power minus the
  noise-power estimate. It is read back as `sinr = wideband_sinr_dB / 10.0`
  (`gui/meas_reader.py:97`) and displayed as `SINR: X.X dB`.
- Note: this is not SSB SINR (`ssb_sinr_dB`) and not the CSI CQI; it is the
  UE PHY's internal wideband SINR proxy.

### 8.2 gNB monitor - DL `dl_sinr`

- CSV column: `dl_sinr`
- SHM: `/dev/shm/gnb_meas_dl` -> `gnb_dl_meas_shm_t.sinr_db_x10`
- Writers:

  1. `gNB_shm_update_dl_csi()` (`openair1/PHY/NR_TRANSPORT/gNB_shm.c`), called
     from `gNB_scheduler_uci.c:900` after each decoded CSI report: stores the
     UE-reported SSB SINR
     `CSI_report.ssb_rsrp_report.r[0].SINRx10` (dB x10).
  2. DL measurement / scheduler snapshots (`gNB_shm_write_dl_meas()` in
     `gNB_scheduler_uci.c:367`, `gNB_shm_write_dl_sched()` in
     `gNB_scheduler_dlsch.c:1193`):
     - `UE->mac_stats.cumul_sinrx10 / num_sinr_meas` when CSI SINR
       measurements exist;
     - otherwise the gNB **PUCCH power-control SNR**
       `nr_mac_get_snr(&sched_ctrl->pucch_pc)`, derived from the UE-reported
       PUCCH CQI via `pucch_snrx10 = ul_cqi * 5 - 640`
       (`gNB_scheduler_uci.c:1020`). The fallback triggers when `SINRx10 == 0`.

- Read as `sinr = sinr_db_x10 / 10.0` (`gui/gnb_meas_reader.py:122`), displayed
  as `SINR: X.X dB  CQI: N  RI: N  PMI: (a,b)`.
- Note: DL SINR is either the UE-reported SSB SINR (when a CSI SINR report is
  configured) or the gNB-side PUCCH-SNR proxy; it is not a gNB RF measurement
  of the DL signal.

### 8.3 gNB monitor - UL `ul_sinr`

- CSV column: `ul_sinr`
- SHM: `/dev/shm/gnb_meas_ul` -> `gnb_ul_meas_shm_t.sinr_db_x10`
- Written by `handle_nr_ul_harq()` in
  `openair2/LAYER2/NR_MAC_gNB/gNB_scheduler_ulsch.c`:
  - `UE->mac_stats.cumul_sinrx10 / num_sinr_meas` when PUSCH SINR measurements
    exist;
  - otherwise the filtered **PUSCH power-control SNR**
    `nr_mac_get_snr(&sched_ctrl->pusch_pc)` (dB x10).
- Displayed as `SINR: X.X dB  TA: N`.

### 8.4 gNB monitor - SRS `srs_snr`

- CSV column: `srs_snr`
- SHM: `/dev/shm/srs_channel` -> `gnb_srs_shm_hdr_t.snr_db_x10`
- Written by `gNB_shm_write_srs()` (`openair1/PHY/NR_TRANSPORT/gNB_shm.c`),
  called from `openair1/SCHED_NR/phy_procedures_nr_gNB.c:855` after SRS channel
  estimation.
- Value (`nr_srs_rx_procedures()`, `openair1/SCHED_NR/phy_procedures_nr_gNB.c`):

  ```text
  snr = dB_fixed(signal_power_avg) - dB_fixed(max(noise_power_avg, 1))
  ```

  where `signal_power_avg` is the mean SRS received power over RX antennas and
  SRS ports and `noise_power_avg` the mean SRS noise power. Stored as
  `snr_db_x10 = (int16_t)(snr * 10)`.
- Read as `snr = hdr.snr_db_x10 / 10.0` (`gui/srs_reader.py:112`), displayed
  in the SRS status line as `SNR X.X dB`.
