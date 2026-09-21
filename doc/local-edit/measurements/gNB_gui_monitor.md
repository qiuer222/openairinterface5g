# OAI NR gNB Performance Monitor (GUI)

## 1. Overview

The gNB monitor is the counterpart of `gui/oai_ue_monitor.py` for the UE.
It reads DL/UL transmission measurements and SRS channel estimates written by
OAI `nr-gnb` into POSIX shared memory, runs iperf3 for throughput, and plots
the results in a PyQt5 window.

## 2. OAI C side

The gNB shared-memory writer is implemented in:

- `openair1/PHY/NR_TRANSPORT/gNB_shm.h`
- `openair1/PHY/NR_TRANSPORT/gNB_shm.c`

It is initialized after `phy_init_nr_gNB()` in `executables/nr-gnb.c` and
closed when the gNB thread pool is terminated.

Three regions are exported:

```text
/dev/shm/gnb_meas_dl  - latest connected-UE PDSCH scheduler snapshot
/dev/shm/gnb_meas_ul  - UL PUSCH decode, PHY SINR, MCS, NPRB, TBS, TA...
/dev/shm/srs_channel  - latest SRS channel estimate
```

DL measurements are written only from `gNB_scheduler_dlsch.c` for connected-UE
PDSCH scheduler snapshots. HARQ feedback and CSI decoding update scheduler
state but do not write `gnb_meas_dl`. UL measurements are written from
`gNB_scheduler_ulsch.c` when UL HARQ CRC feedback is processed. The SRS
channel is written from `phy_procedures_nr_gNB.c` after SRS channel estimation.

## 3. GUI

Run from the repository root:

```bash
./gui/run_gnb_gui.sh
```

The GUI has:

- `UL`: starts `iperf3 -s` on the gNB for UE-to-gNB upload.
- `DL`: starts `iperf3 -c <ue_ip>` on the gNB for gNB-to-UE download.
- `Stop`: terminates iperf3 and closes the current CSV record.
- `Restart`: reopens the GUI and reattaches the shared-memory readers.

The left panel shows separate DL and UL measurement text boxes plus SRS status.
The right panel plots throughput, DL measurements, UL measurements, and SRS
channel quality.

CSV records are written to:

```text
gui/record/gui_gnb_log_<timestamp>.csv
gui/record/gui_gnb_log_<timestamp>/srs_<timestamp>.npy
```

The CSV is created once when the GUI starts; one CSV file is used for the whole
GUI run. `Stop`, `UL`, and `DL` do not create new files. Each GUI initialization
or Restart creates a new timestamped CSV and SRS directory.

The CSV column order is:

```text
common columns,
UL columns,
DL columns,
SRS columns
```

### 3.1 DL CSV field mapping

The DL snapshot is produced by `generate_dl_mac_pdu()` in
`openair2/LAYER2/NR_MAC_gNB/gNB_scheduler_dlsch.c`, stored in
`/dev/shm/gnb_meas_dl`, read by `gui/gnb_meas_reader.py`, and appended to the
CSV by `gui/oai_gnb_monitor.py`:

```text
generate_dl_mac_pdu()
    |
    | frame, slot, rnti
    | mcs, Qm, TBS (bits), nrOfLayers, rbSize, symbols
    | rv, ndi, R, CSI cqi/ri/pmi, SINR/BLER stats
    | active DL BWP PRBs and subcarrier spacing
    v
/dev/shm/gnb_meas_dl
    |
    v
gui/gnb_meas_reader.py GnbDlReader.read()
    |
    v
gui_gnb_log_<timestamp>.csv
```

DL CSV columns and their source variables:

| CSV column | SHM field | Source in OAI | Unit / meaning |
|---|---|---|---|
| `dl_frame` | `frame` | scheduled PDSCH frame | SFN |
| `dl_slot` | `slot` | scheduled PDSCH slot | slot in SFN |
| `dl_rnti` | `rnti` | UE RNTI | UE identifier |
| `dl_bler` | `bler_x1000` | `sched_ctrl->dl_bler_stats.bler * 1000` | percent (`/10` in Python) |
| `dl_sinr` | `sinr_db_x10` | averaged UE-reported SSB/CSI-RS SINR | dB x10; unavailable otherwise |
| `dl_mcs` | `mcs` | `sched_pdsch->mcs` | MCS index |
| `dl_nprb` | `num_rbs` | `sched_pdsch->rbSize` | allocated PRBs |
| `dl_layers` | `num_layers` | `sched_pdsch->nrOfLayers` | scheduled layers |
| `dl_qm` | `qam_mod_order` | `sched_pdsch->Qm` | modulation order |
| `dl_tbs` | `tbs` | `sched_pdsch->tb_size * 8` | transport block size in bits |
| `dl_nsymb` | `num_symbols` | `sched_pdsch->tda_info.nrOfSymbols` | allocated PDSCH symbols |
| `dl_rv` | `rv` | `nr_get_rv(harq->round % 4)` | redundancy version |
| `dl_ndi` | `new_data_indicator` | `harq->ndi` | new-data indicator |
| `dl_target_code_rate` | `target_code_rate` | `sched_pdsch->R` | code rate numerator, denominator 1024 |
| `dl_cqi` | `cqi` | `CSI_report.cri_ri_li_pmi_cqi_report.wb_cqi_1tb` | reported wideband CQI |
| `dl_ri` | `ri` | CSI RI + 1, fallback to scheduled layers | reported rank / layers |
| `dl_pmi_x1` | `pmi_x1` | CSI report PMI x1 | PMI field |
| `dl_pmi_x2` | `pmi_x2` | CSI report PMI x2 | PMI field |
| `dl_n_rb_dl` | `n_rb_dl` | `sched_pdsch->bwp_info.bwpSize` | active DL BWP size in PRBs |
| `dl_scs_khz` | `subcarrier_spacing_khz` | `15 << UE->current_DL_BWP.scs` | active DL subcarrier spacing in kHz |
| `dl_sched_se` | computed in GUI | `dl_tbs / (dl_nprb * 12 * dl_nsymb)` | scheduled DL spectral efficiency in bit/s/Hz |
| `dl_app_se` | computed in GUI | DL iperf throughput / active DL BWP bandwidth | application-level DL spectral efficiency in bit/s/Hz |

`ul_tbs` is also stored as bits (`harq->sched_pusch.tb_size * 8`), and both
DL/UL `rv` fields now use `nr_get_rv(harq->round % 4)` instead of a hard-coded
zero. The gNB UL CSV also records `ul_tpmi` from
`harq->sched_pusch.tpmi`. TPMI 0 is a valid value; unavailable TPMI is stored
as `0xff` in SHM and written as an empty CSV field.

DL CQI, RI, PMI, BLER, counters, and SINR are sampled when the PDSCH scheduler
snapshot is written. `dl_sinr` never falls back to PUCCH power-control SNR.
The last valid UE-reported SINR is retained in scheduler state until a newer
report arrives; without one, CSV is empty and the GUI shows `N/A`. Zero is a
valid CQI/PMI value and is not replaced by a previous non-zero sample. A CSI
report received after the last PDSCH becomes visible on the next PDSCH
snapshot.

### 3.2 Spectral efficiency and iperf direction

Each row records `iperf_direction` as `DL`, `UL`, or empty. Application-level
SE is only calculated when the iperf direction matches the measured link:

```text
dl_app_se = dl_throughput_mbps * 1e6
            / (dl_n_rb_dl * 12 * dl_scs_khz * 1000)

ul_app_se = ul_throughput_mbps * 1e6
            / (ul_n_rb_ul * 12 * ul_scs_khz * 1000)
```

These values use iperf3 interval throughput and the active BWP bandwidth. They
include idle slots, HARQ retransmissions, scheduling gaps, and protocol
overhead. If the current direction is not DL/UL, the corresponding application
SE is an empty CSV field.

Scheduled SE uses the current transport block and allocated resources:

```text
dl_sched_se = dl_tbs / (dl_nprb * 12 * dl_nsymb)
ul_sched_se = ul_tbs / (ul_nprb * 12 * ul_nsymb)
```

These values do not use iperf, exclude idle slots, and describe one scheduled
PDSCH/PUSCH allocation. Retransmissions are visible as separate snapshots.

Each CSV row includes `test_round` to identify the iperf test round. On the gNB
DL client side, `test_round` increments every time DL is clicked. On the gNB UL
server side, it increments each time the iperf3 server reports a new
`Accepted connection from ...` line. The counter resets to zero when the GUI
process restarts.

CSV rows are only appended when the newest line in the monitored iperf log is
a 1-second instantaneous throughput interval. Final iperf3 average/summary
lines are ignored.

At startup the GUI clears the configured iperf log. On exit and before Restart
it asks whether this run's CSV, SRS data, and iperf log should be stored:

- `Yes` opens the former folder-name prompt and archives the recordings.
- `No` asks for a second confirmation before deleting this run's files. If
  deletion is cancelled, the save/store question is shown again.

Archived files are placed in `gui/record/<user_folder>/`. The SRS channel
directory is preserved as a subfolder:

```text
gui/record/<user_folder>/gui_gnb_log_<timestamp>.csv
gui/record/<user_folder>/gui_gnb_log_<timestamp>/srs_<timestamp>.npy
```

iperf3 pairing:

- gNB UL button: gNB runs a server; the UE side should run the iperf3 client.
- gNB DL button: gNB runs a client; the UE side should run the iperf3 server.

Edit `gui/gnb_config.json` for the UE IP, port, duration, log file, and SRS SNR
assumption.

## 4. CSV variable reference

The CSV row is written by `_write_csv()` in `gui/oai_gnb_monitor.py`. This
section lists every column and where the value comes from in OAI / the GUI.

### 4.1 Common columns

| CSV column | GUI source | OAI source | Meaning |
|---|---|---|---|
| `timestamp` | `datetime.now()` in `_refresh()` | N/A | GUI local wall-clock time in `%Y%m%d_%H%M%S_%f` |
| `test_round` | GUI counter | N/A | iperf round; client-side click or server-side `Accepted connection` line |
| `iperf_direction` | active GUI test | UL/DL button handler | `DL`, `UL`, or empty when no iperf test is active |
| `throughput_mbps` | `IperfController.parse_iperf3_line()` | iperf3 stdout interval line | instantaneous bits/sec converted to Mbps |

### 4.2 DL columns

All DL fields are written by `generate_dl_mac_pdu()` in
`openair2/LAYER2/NR_MAC_gNB/gNB_scheduler_dlsch.c`, copied through
`/dev/shm/gnb_meas_dl`, and read by `gui/gnb_meas_reader.py`.

| CSV column | SHM field | OAI source | Meaning / unit |
|---|---|---|---|
| `dl_frame` | `frame` | `generate_dl_mac_pdu()` frame argument | PDSCH frame number |
| `dl_slot` | `slot` | `generate_dl_mac_pdu()` slot argument | PDSCH slot |
| `dl_rnti` | `rnti` | scheduled UE `rnti` | RNTI |
| `dl_bler` | `bler_x1000` | `sched_ctrl->dl_bler_stats.bler * 1000` | BLER in percent after Python `/10` |
| `dl_sinr` | `sinr_db_x10` | `UE->mac_stats.cumul_sinrx10 / num_sinr_meas`; last valid UE-reported SSB/CSI-RS SINR retained in SHM | SINR in dB after Python `/10`; empty when unavailable |
| `dl_mcs` | `mcs` | `sched_pdsch->mcs` | DL MCS |
| `dl_nprb` | `num_rbs` | `sched_pdsch->rbSize` | allocated PRBs |
| `dl_layers` | `num_layers` | `sched_pdsch->nrOfLayers` | scheduled DL layers |
| `dl_qm` | `qam_mod_order` | `sched_pdsch->Qm` | modulation order |
| `dl_tbs` | `tbs` | `sched_pdsch->tb_size * 8` | TBS in bits |
| `dl_nsymb` | `num_symbols` | `sched_pdsch->tda_info.nrOfSymbols` | allocated PDSCH symbols |
| `dl_rv` | `rv` | `nr_get_rv(harq->round % 4)` | redundancy version |
| `dl_ndi` | `new_data_indicator` | `harq->ndi` | new-data indicator |
| `dl_target_code_rate` | `target_code_rate` | `sched_pdsch->R` | code rate numerator, denominator 1024 |
| `dl_cqi` | `cqi` | `sched_ctrl->CSI_report.cri_ri_li_pmi_cqi_report.wb_cqi_1tb` | reported wideband CQI |
| `dl_ri` | `ri` | raw CSI `ri` + 1, else `sched_pdsch->nrOfLayers` | reported rank / layers |
| `dl_pmi_x1` | `pmi_x1` | same CSI report `pmi_x1` | PMI part 1 |
| `dl_pmi_x2` | `pmi_x2` | same CSI report `pmi_x2` | PMI part 2 |
| `dl_n_rb_dl` | `n_rb_dl` | `sched_pdsch->bwp_info.bwpSize` | active DL BWP size in PRBs |
| `dl_scs_khz` | `subcarrier_spacing_khz` | `15 << UE->current_DL_BWP.scs` | active DL subcarrier spacing in kHz |
| `dl_sched_se` | computed in GUI | `dl_tbs / (dl_nprb * 12 * dl_nsymb)` | scheduled DL SE in bit/s/Hz |
| `dl_app_se` | computed in GUI | `dl_throughput_mbps * 1e6 / (dl_n_rb_dl * 12 * dl_scs_khz * 1000)` | application-level DL SE in bit/s/Hz |

### 4.3 UL columns

UL fields are written by `handle_nr_ul_harq()` in
`openair2/LAYER2/NR_MAC_gNB/gNB_scheduler_ulsch.c`, copied through
`/dev/shm/gnb_meas_ul`, and read by `gui/gnb_meas_reader.py`.

| CSV column | SHM field | OAI source | Meaning / unit |
|---|---|---|---|
| `ul_frame` | `frame` | `harq->sched_pusch.frame` | PUSCH frame number |
| `ul_slot` | `slot` | `harq->sched_pusch.slot` | PUSCH slot |
| `ul_rnti` | `rnti` | `rnti` argument from CRC indication | RNTI |
| `ul_bler` | `bler_x1000` | `sched_ctrl->ul_bler_stats.bler * 1000` | BLER in percent after Python `/10` |
| `ul_sinr` | `sinr_db_x10` | `sched_ctrl->pusch_pc.avg_snr * 10` | SINR in dB after Python `/10` |
| `ul_mcs` | `mcs` | `harq->sched_pusch.mcs` | UL MCS |
| `ul_nprb` | `num_rbs` | `harq->sched_pusch.rbSize` | allocated PRBs |
| `ul_layers` | `num_layers` | `harq->sched_pusch.nrOfLayers` | scheduled UL layers |
| `ul_qm` | `qam_mod_order` | `harq->sched_pusch.Qm` | modulation order |
| `ul_tbs` | `tbs` | `harq->sched_pusch.tb_size * 8` | TBS in bits |
| `ul_nsymb` | `num_symbols` | `harq->sched_pusch.tda_info.nrOfSymbols` | allocated PUSCH symbols |
| `ul_n_rb_ul` | `n_rb_ul` | `harq->sched_pusch.bwp_info.bwpSize` | active UL BWP size in PRBs |
| `ul_scs_khz` | `subcarrier_spacing_khz` | `15 << UE->current_UL_BWP.scs` | active UL subcarrier spacing in kHz |
| `ul_sched_se` | computed in GUI | `ul_tbs / (ul_nprb * 12 * ul_nsymb)` | scheduled UL SE in bit/s/Hz |
| `ul_app_se` | computed in GUI | `ul_throughput_mbps * 1e6 / (ul_n_rb_ul * 12 * ul_scs_khz * 1000)` | application-level UL SE in bit/s/Hz |
| `ul_timing_advance` | `timing_advance` | reserved; not populated by current UL HARQ writer | currently 0 |
| `ul_cqi` | `ul_cqi` | reserved; not populated by current UL HARQ writer | currently 0 |
| `ul_tpmi` | `tpmi` | `harq->sched_pusch.tpmi` | UL TPMI index; empty when `tpmi` is unavailable |

### 4.4 SRS columns

SRS channel data is written by `handle_srs()` in
`openair1/SCHED_NR/phy_procedures_nr_gNB.c` through `/dev/shm/srs_channel`.
Capacity/rank/condition are computed in `gui/srs_reader.py`.

| CSV column | SHM source | OAI source | Meaning / unit |
|---|---|---|---|
| `srs_capacity` | computed from channel | `srs_estimated_channel_freq` via `CsiRsReader._channel_metrics()` | SVD capacity, depends on configured `snr_db` |
| `srs_rank` | computed from channel | same channel metrics | estimated SRS rank |
| `srs_condition` | computed from channel | same channel metrics | channel condition number |
| `srs_snr` | `gnb_srs_shm_hdr_t.snr_db_x10` | `nr_srs_rx_procedures()` returned `snr`, stored as `(int16_t)(snr * 10)` | SRS SNR in dB after Python `/10` |

## 5. Build

The new C files are part of the normal gNB build:

```bash
cd cmake_targets
./build_oai --gNB --build-everything
```

The DL and UL shared-memory layouts now include `subcarrier_spacing_khz`.
Rebuild `nr-gnb` and restart it before starting the GUI so the writer and
Python reader use the same layout. The UE shared-memory layout also gained
`n_rb_ul`, so rebuild and restart the UE before using UE-side UL application SE.
