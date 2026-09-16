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
rsrp_dBm, rsrp_per_ant_dBm[4], rssi_dBm,
wideband_sinr_dB, ssb_sinr_db_x10,
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
4. if changed and the newest log line is a 1-second iperf3 interval:
     save latest CSI channel as channel_<timestamp>.npy
     append one row to gui/record/gui_ue_log_<timestamp>.csv
5. final iperf3 average/summary lines are ignored
6. update throughput / PDSCH / CSI plots every 500 ms
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
n_rb_dl, scs, nb_antennas_rx, wideband_cqi_dB
```

`timestamp` is local wall-clock time in `%Y%m%d_%H%M%S_%f` format:

```text
20260802_223124_731830
```

`test_round` identifies the iperf test round. On client-side tests it is
incremented each time UL/DL is clicked. On server-side tests it is incremented
each time the iperf3 server reports a new `Accepted connection from ...` line.
The counter resets to zero when the GUI process restarts.

`sinr_dB` is the UE SSB SINR, not the old wideband CQI proxy. The legacy proxy
is appended as `wideband_cqi_dB` and is measured in integer dB. If SSB SINR is
not available yet, `sinr_dB` is empty in CSV and shown as `N/A` in the GUI.

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

These changes are implemented in `gui/oai_ue_monitor.py` and the archive flow
is mirrored by `gui/oai_gnb_monitor.py`.

### 7.1 CSV and channel recording

- One CSV file is created at GUI startup and used for the whole run. Clicking
  `UL`/`DL` or `Stop` does not create a new file.
- `test_round` is written in every CSV row. Client-side tests increment it on
  each `UL`/`DL` click; server-side tests increment it each time the iperf3
  server reports a new `Accepted connection from ...` line.
- `_iperf_log_changed()` compares the current `iperf.log` `st_size` and
  `st_mtime_ns` with the last seen state, then reads the newest appended line.
  It returns true only when that line is a 1-second iperf3 interval; final
  average/summary lines are skipped.
- `_reset_log_state()` records the file state when the run CSV is opened.
  This prevents old log contents from creating a false first sample.
- `_refresh()` only calls `_save_channel()` and `_write_csv()` when
  `_iperf_log_changed()` returns true.
- The channel snapshot and CSV row use the same timestamp, so they can be
  aligned directly in offline analysis.
- The CSV header and every appended row are flushed to disk immediately.

### 7.2 Startup cleanup and exit-time archive

- `closeEvent()` and the gNB/UE `Restart` action stop the timer and iperf3
  process, close the CSV, and close the shared-memory readers.
- iperf3 runs in its own process group. Closing or restarting terminates the
  whole group, and the gNB UL server also removes the `iperf3 -s` process
  inside the `oai-ext-dn` container.
- At startup, the configured `iperf.log` is cleared, so each GUI run starts with
  an empty log.
- Every GUI initialization creates a new timestamped CSV and channel/SRS
  directory. Restart starts a new process and therefore creates a new set.
- On exit or Restart, the GUI first asks whether to store this run's CSV,
  channel/SRS data, and iperf log.
- `Yes` opens the folder-name prompt and archives the recordings.
- `No` asks for a second confirmation before deleting this run's files. If
  deletion is cancelled, the store question is shown again.
- Archive selection moves the CSV, channel/srs files, and iperf log into:
  `gui/record/<user_folder>/`
- The CSI channel directory is preserved as a subfolder:

  ```text
  gui/record/<user_folder>/gui_ue_log_<timestamp>.csv
  gui/record/<user_folder>/gui_ue_log_<timestamp>/channel_<timestamp>.npy
  ```

### 7.3 Log path handling

- `_resolve_log_path()` resolves `config.json` `log_file` relative to the
  repository root when the path is not absolute.
- The resolved path is passed to `IperfController`, so the file being monitored
  is the same file iperf3 writes to.

These changes make each CSV row correspond to a real iperf3 instantaneous
throughput interval instead of being generated by the 500 ms UI timer or by
the final average/summary line.

## 8. SNR values recorded by the UE / gNB monitors

The two monitors record different SNR values. They are not the same quantity;
each is defined below with its source in the OAI code and the CSV column that
stores it.

### 8.1 UE monitor - `sinr_dB`

- CSV column: `sinr_dB`
- SHM: `/dev/shm/meas_dl` -> `meas_dl_shm_t.ssb_sinr_db_x10`
- Written by `ue_shm_write_meas_dl()` in
  `openair1/SCHED_NR_UE/phy_procedures_nr_ue.c:1357`
- Value (`openair1/PHY/NR_UE_ESTIMATION/nr_ue_measurements.c:223`):

  ```text
  signal_pwr = max(ssb_rsrp - n0_power_avg, 0)
  ssb_sinr_db_x10 = dB_fixed_x10(signal_pwr)
                    - dB_fixed_x10(n0_power_avg)
  ```

- Definition: UE-measured **SSB SINR**, stored in shared memory in dB x10. It
  is displayed as `SSB SINR: X.X dB`. `INT16_MIN` means no valid SSB SINR yet,
  and the GUI/CSV report `N/A` or an empty field.
- The legacy wideband proxy is now recorded separately as
  `wideband_cqi_dB`. It uses
  `wideband_cqi_tot = rx_power_tot_dB - n0_power_tot_dB`; both terms use
  integer-dB `dB_fixed()`, so no `/10` scaling is applied.
- Note: `wideband_cqi_dB` is a raw UE PHY channel-estimate/noise proxy, not
  SSB SINR and not a calibrated DL SINR.

### 8.2 gNB monitor - DL `dl_sinr`

- CSV column: `dl_sinr`
- SHM: `/dev/shm/gnb_meas_dl` -> `gnb_dl_meas_shm_t.sinr_db_x10`
- Writer: `gNB_shm_write_dl_sched()` in
  `gNB_scheduler_dlsch.c`, called once for every connected-UE PDSCH scheduler
  snapshot. There is no HARQ or CSI SHM writer.
- Source:
  - `UE->mac_stats.cumul_sinrx10 / num_sinr_meas` when the current MAC stats
    window has UE SINR measurements;
  - otherwise `sched_ctrl->dl_sinr_db_x10`, the last successfully decoded
    UE-reported SSB/CSI-RS SINR;
  - otherwise `INT16_MIN`, meaning no valid DL SINR.

- Read as `sinr = sinr_db_x10 / 10.0`; `INT16_MIN` becomes `None`, displayed
  as `SINR: N/A (UE SSB)`, and written as an empty CSV field.
- Note: DL SINR is the UE-reported SSB/CSI-RS SINR. It is not a gNB RF
  measurement and never uses PUCCH/PUSCH SNR.

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

## 9. gNB DL CSV recording variables

The gNB DL CSV row is written from `/dev/shm/gnb_meas_dl`. The scheduler
snapshot is generated in `generate_dl_mac_pdu()`
(`openair2/LAYER2/NR_MAC_gNB/gNB_scheduler_dlsch.c`) and copied through
`gui/gnb_meas_reader.py` to `gui_gnb_log_<timestamp>.csv`:

```text
gNB MAC PDSCH dispatch
  |
  | mcs, Qm, TBS (bits), layers, PRBs, symbols,
  | rv, NDI, code rate, CQI/RI/PMI, SINR, BLER
  v
/dev/shm/gnb_meas_dl
  |
  v
gui/gnb_meas_reader.py
  |
  v
gui_gnb_log_<timestamp>.csv
```

The corrected DL mapping is:

| CSV column | SHM field | Correct source | Unit |
|---|---|---|---|
| `dl_tbs` | `tbs` | `sched_pdsch->tb_size * 8` | bits |
| `dl_rv` | `rv` | `nr_get_rv(harq->round % 4)` | redundancy version |
| `dl_ri` | `ri` | CSI RI + 1, else `sched_pdsch->nrOfLayers` | rank / layers |
| `dl_ndi` | `new_data_indicator` | `harq->ndi` | NDI |
| `dl_target_code_rate` | `target_code_rate` | `sched_pdsch->R` | 1/1024 units |
| `dl_nsymb` | `num_symbols` | `sched_pdsch->tda_info.nrOfSymbols` | symbols |
| `dl_pmi_x1` / `dl_pmi_x2` | `pmi_x1` / `pmi_x2` | CSI report PMI fields | PMI |
| `dl_n_rb_dl` | `n_rb_dl` | `sched_pdsch->bwp_info.bwpSize` | PRBs |

The remaining DL columns (`dl_mcs`, `dl_nprb`, `dl_layers`, `dl_qm`,
`dl_cqi`, `dl_sinr`, `dl_bler`) are read directly from the corresponding
`gnb_dl_meas_shm_t` fields described in the full mapping under
`doc/local-edit/measurements/gNB_gui_monitor.md`.

After changing the C side, rebuild and restart `nr-gnb` so the shared-memory
regions are recreated with the corrected layout:

```bash
cd cmake_targets
./build_oai --gNB --build-everything
```
