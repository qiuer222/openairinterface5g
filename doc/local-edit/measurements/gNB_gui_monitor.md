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
/dev/shm/gnb_meas_dl  - DL HARQ feedback, UE-reported SINR/CQI/RI, MCS, NPRB...
/dev/shm/gnb_meas_ul  - UL PUSCH decode, PHY SINR, MCS, NPRB, TBS, TA...
/dev/shm/srs_channel  - latest SRS channel estimate
```

DL measurements are written from `gNB_scheduler_uci.c` when DL HARQ feedback is
processed and from `gNB_scheduler_dlsch.c` for scheduler-time snapshots. UL
measurements are written from `gNB_scheduler_ulsch.c` when UL HARQ CRC feedback
is processed. The SRS channel is written from `phy_procedures_nr_gNB.c` after
SRS channel estimation.

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
GUI run. `Stop`, `UL`, and `DL` do not create new files.

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
| `dl_sinr` | `sinr_db_x10` | averaged CSI SINR or `nr_mac_get_snr(pucch_pc)` | dB x10 |
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

`ul_tbs` is also stored as bits (`harq->sched_pusch.tb_size * 8`), and both
DL/UL `rv` fields now use `nr_get_rv(harq->round % 4)` instead of a hard-coded
zero.

Each CSV row includes `test_round` to identify the iperf test round. On the gNB
DL client side, `test_round` increments every time DL is clicked. On the gNB UL
server side, it increments each time the iperf3 server reports a new
`Accepted connection from ...` line. The counter resets to zero when the GUI
process restarts.

CSV rows are only appended when the newest line in the monitored iperf log is
a 1-second instantaneous throughput interval. Final iperf3 average/summary
lines are ignored.

At startup the GUI clears the configured iperf log. On exit it asks whether the
CSV, SRS channel data, and iperf log should be moved into
`gui/record/<user_folder>/`. The default folder name is the first CSV timestamp;
if the folder already exists, the GUI asks for another name. The SRS channel
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

## 4. Build

The new C files are part of the normal gNB build:

```bash
cd cmake_targets
./build_oai --gNB --build-everything
```

After rebuilding `nr-gnb`, restart the gNB before starting the GUI so the new
shared-memory regions are created with the expected layout.
