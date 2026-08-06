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
processed. UL measurements are written from `gNB_scheduler_ulsch.c` when UL
HARQ CRC feedback is processed. The SRS channel is written from
`phy_procedures_nr_gNB.c` after SRS channel estimation.

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

Each CSV row includes `test_round` to identify the iperf test round.

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
