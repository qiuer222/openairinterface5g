# UE and gNB GUI/CSV Variable Reference

This document is the authoritative definition of the live UE/gNB GUI fields
and the columns written to the UE/gNB CSV recordings.

## 1. Common Rules

### 1.1 Snapshots and recording

- Both GUIs poll their shared-memory readers every 500 ms.
- Shared-memory `seq` is a monotonically increasing publication counter. A
  reader ignores a snapshot when `seq` is zero or unchanged.
- The UI displays the latest valid snapshot retained by the reader.
- CSV rows are appended when the monitored iperf3 log gains a 1-second
  interval line. Average/summary lines do not create rows.
- One CSV is created per GUI process/run. Restarting the GUI starts a new CSV.
- `timestamp` is local wall-clock time in `%Y%m%d_%H%M%S_%f`.
- `test_round` is a GUI counter, not an OAI frame counter. Client-side tests
  increment it when a test starts. Server-side tests increment it when a new
  iperf3 client connects.
- `iperf_direction` is `DL`, `UL`, or empty when no direction is active.
- `throughput_mbps` is the latest 1-second iperf3 interval rate in Mbit/s.

### 1.2 Empty, zero, and unavailable values

- An empty CSV field means that the value was unavailable or not applicable.
- A numeric zero is kept when it is a valid measurement or index.
- The UE GUI displays unavailable SINR as `N/A`.
- The gNB GUI displays unavailable CQI/TPMI/RSSI/TA as `N/A` or empty CSV.
- `TBS`, `nprb`, and `nsymb` values of zero normally mean that no valid
  transmission snapshot was available when the row was written.

### 1.3 Derived spectral efficiency

PRB allocation bandwidth:

```text
bandwidth_hz = n_rb * 12 * subcarrier_spacing_khz * 1000
```

Scheduled spectral efficiency:

```text
sched_se = tbs_bits / (nprb * 12 * nsymb)   [bit/s/Hz]
```

Application spectral efficiency:

```text
app_se = iperf_throughput_bps / bandwidth_hz   [bit/s/Hz]
```

Application SE is only written when the active iperf direction matches the
link. It includes idle periods, retransmissions, scheduling gaps, and protocol
overhead. Scheduled SE describes one scheduled allocation and does not use
iperf.

## 2. UE GUI

The UE monitor is implemented by `gui/oai_ue_monitor.py`.

### 2.1 Controls and status

| GUI item | Meaning |
|---|---|
| `BS IP` | Destination gNB IP used by UE-initiated iperf3 tests |
| `Port` | iperf3 TCP port |
| `Duration (s)` | Requested iperf3 test duration |
| `DL reverse client (-R)` | Selects reverse-direction mode for the DL client |
| `UL` | Starts an uplink iperf3 client from the UE |
| `DL` | Starts an iperf3 client/server flow for downlink data |
| `Stop` | Stops the active iperf3 process |
| `Restart` | Restarts the GUI and reattaches shared-memory readers |
| `Throughput [direction]` | Latest iperf3 interval throughput |
| status label | iperf3 and recording status |
| `iperf Log` | Last 200 lines of the monitored iperf3 output |

### 2.2 DL Measurements panel

| GUI field | Reader key | Meaning | Unit / unavailable behavior |
|---|---|---|---|
| `Frame` | `frame` | Frame number of the latest scheduled PDSCH snapshot | integer |
| `Slot` | `slot` | Slot number of that PDSCH snapshot | integer |
| `BLER` | `bler` | UE DLSCH block error rate | percent; integer snapshot |
| `RSRP` | `rsrp` | Serving-cell SS-RSRP at the selected SSB | dBm; `0` means unavailable |
| `SSB SINR` | `sinr` | UE-measured SSB SINR | dB; `N/A` when invalid |
| `Wideband CQI proxy` | `wideband_cqi` | UE PHY channel-estimate/noise proxy, not a 3GPP CQI | integer dB |
| `MCS` | `mcs` | Scheduled PDSCH MCS index | index |
| `NPRB` | `nprb` | Number of allocated PRBs | PRBs |
| `Layers` | `layers` | Number of scheduled MIMO layers | layers |
| `TBS` | `tbs` | Transport block size | bits |
| `Qm` | `qm` | PDSCH modulation order | bits/symbol |
| `Freq offset` | `freq_offset` | UE frequency-error estimate | Hz |
| `DL BW` | derived from `n_rb_dl`, `scs` | Active DL bandwidth | MHz |
| `Sched SE` | derived | Scheduled PDSCH spectral efficiency | bit/s/Hz |
| `DL App SE` | derived | Downlink iperf throughput / DL bandwidth | bit/s/Hz; `N/A` when direction is not DL |
| `UL App SE` | derived | Uplink iperf throughput / UL bandwidth | bit/s/Hz; `N/A` when direction is not UL |
| `CSI: C` | `capacity` | Mean CSI-RS SVD capacity | bit/s/Hz under configured `snr_db` |
| `CSI: rank` | `rank` | Mean numerical channel rank | rank |
| `CSI: cond` | `condition_number` | Mean singular-value condition number, capped | ratio |
| `Ant RSRP` | `rsrp_per_ant` | Per-RX-antenna SS-RSRP values | dBm; `RX0`...`RX3` |

### 2.3 UE plots

| Panel | Selector value | GUI key | Meaning |
|---|---|---|---|
| Throughput | `throughput` | `throughput` | iperf3 throughput in Mbit/s |
| PDSCH Metrics | `rsrp` | `rsrp` | Serving-cell SS-RSRP in dBm |
| PDSCH Metrics | `bler` | `bler` | DLSCH BLER in percent |
| PDSCH Metrics | `sinr` | `sinr` | SSB SINR in dB |
| PDSCH Metrics | `wideband_cqi` | `wideband_cqi` | Legacy wideband CQI proxy in integer dB |
| PDSCH Metrics | `mcs` | `mcs` | PDSCH MCS index |
| PDSCH Metrics | `nprb` | `nprb` | Allocated PRBs |
| PDSCH Metrics | `dl_sched_se` | `dl_sched_se` | Scheduled DL spectral efficiency |
| PDSCH Metrics | `dl_app_se` | `dl_app_se` | Application-level DL spectral efficiency |
| PDSCH Metrics | `ul_app_se` | `ul_app_se` | Application-level UL spectral efficiency |
| CSI Channel Quality | `capacity` | `capacity` | CSI channel capacity |
| CSI Channel Quality | `rank` | `rank` | CSI channel rank |
| CSI Channel Quality | `condition_number` | `condition_number` | CSI channel condition number |
| Antenna RSRP | bar chart | `rsrp_per_ant` | Per-RX-antenna SS-RSRP |

### 2.4 UE CSV columns

The file is `gui/record/gui_ue_log_<timestamp>.csv`.

| CSV column | Source | Meaning | Unit / unavailable behavior |
|---|---|---|---|
| `timestamp` | GUI | Recording time | local `%Y%m%d_%H%M%S_%f` |
| `test_round` | GUI | iperf test round counter | integer |
| `iperf_direction` | GUI | Active iperf direction | `DL`, `UL`, or empty |
| `throughput_mbps` | iperf3 parser | Latest interval throughput | Mbit/s |
| `sv0` | CSI reader | Mean first singular value over active CSI-RS subcarriers | linear; zero when missing |
| `sv1` | CSI reader | Mean second singular value over active CSI-RS subcarriers | linear; zero when missing |
| `sv2` | CSI reader | Mean third singular value over active CSI-RS subcarriers | linear; zero when missing |
| `sv3` | CSI reader | Mean fourth singular value over active CSI-RS subcarriers | linear; zero when missing |
| `sv4` | CSI reader | Mean fifth singular value over active CSI-RS subcarriers | linear; zero when missing |
| `sv5` | CSI reader | Mean sixth singular value over active CSI-RS subcarriers | linear; zero when missing |
| `sv6` | CSI reader | Mean seventh singular value over active CSI-RS subcarriers | linear; zero when missing |
| `sv7` | CSI reader | Mean eighth singular value over active CSI-RS subcarriers | linear; zero when missing |
| `capacity` | CSI reader | Mean SVD capacity under configured SNR | bit/s/Hz |
| `rank` | CSI reader | Rounded mean numerical rank | integer |
| `condition_number` | CSI reader | Mean condition number, capped at 1000 | ratio |
| `frame` | UE PHY | Scheduled PDSCH frame | frame number |
| `slot` | UE PHY | Scheduled PDSCH slot | slot number |
| `mcs` | UE PHY | PDSCH MCS | index |
| `qm` | UE PHY | PDSCH modulation order | bits/symbol |
| `tbs_bits` | UE PHY | Transport block size | bits |
| `layers` | UE PHY | Number of PDSCH layers | layers |
| `nprb` | UE PHY | Allocated PRBs | PRBs |
| `nsymb` | UE PHY | Allocated PDSCH symbols | OFDM symbols |
| `rv` | UE PHY | Redundancy version | 0-3 |
| `new_data_indicator` | UE PHY | New-data indicator | 0/1 |
| `target_code_rate` | UE PHY | Target code rate | units of 1/1024 |
| `bitrate_bps` | UE PHY | UE DLSCH transport bitrate estimate | bit/s |
| `dlsch_received` | UE PHY | Cumulative DLSCH receive counter | count |
| `dlsch_errors` | UE PHY | Cumulative DLSCH error counter | count |
| `bler` | UE PHY | Recent DLSCH block error rate | percent |
| `rsrp_dBm` | UE PHY | Serving-cell SS-RSRP | dBm; `0` means unavailable |
| `rssi_dBm` | UE PHY | UE RX RSSI | dBm |
| `sinr_dB` | UE PHY | UE SSB SINR | dB; empty when invalid |
| `freq_offset_hz` | UE PHY | UE frequency offset | Hz |
| `rsrp_ant0_dBm` | UE PHY | RX antenna 0 SS-RSRP | dBm; zero when unavailable |
| `rsrp_ant1_dBm` | UE PHY | RX antenna 1 SS-RSRP | dBm; zero when unavailable |
| `rsrp_ant2_dBm` | UE PHY | RX antenna 2 SS-RSRP | dBm; zero when unavailable |
| `rsrp_ant3_dBm` | UE PHY | RX antenna 3 SS-RSRP | dBm; zero when unavailable |
| `n_rb_dl` | UE PHY | Active DL bandwidth | PRBs |
| `scs` | UE PHY | DL subcarrier spacing | Hz |
| `nb_antennas_rx` | UE PHY | Number of UE RX antennas | count |
| `wideband_cqi_dB` | UE PHY | Legacy wideband CQI proxy | integer dB; not a 3GPP CQI |
| `n_rb_ul` | UE PHY | Active UL bandwidth | PRBs |
| `dl_sched_se` | GUI-derived | Scheduled DL spectral efficiency | bit/s/Hz; empty when inputs are invalid |
| `dl_app_se` | GUI-derived | Application DL spectral efficiency | bit/s/Hz; empty when direction is not DL |
| `ul_app_se` | GUI-derived | Application UL spectral efficiency | bit/s/Hz; empty when direction is not UL |

## 3. gNB GUI

The gNB monitor is implemented by `gui/oai_gnb_monitor.py`.

### 3.1 Controls and status

| GUI item | Meaning |
|---|---|
| `UE IP` | UE IP used by the gNB DL iperf3 client |
| `Port` | iperf3 TCP port |
| `Duration (s)` | Requested iperf3 test duration |
| `UL` | Starts the gNB iperf3 upload server |
| `DL` | Starts the gNB downlink iperf3 client |
| `Stop` | Stops the active iperf3 process |
| `Restart` | Restarts the GUI and reattaches shared-memory readers |
| `Throughput [direction]` | Latest iperf3 interval throughput |
| status label | iperf3 and recording status |

### 3.2 DL Measurements panel

| GUI field | Reader key | Meaning | Unit / unavailable behavior |
|---|---|---|---|
| `Frame` | `frame` | Scheduled PDSCH frame | frame number |
| `Slot` | `slot` | Scheduled PDSCH slot | slot number |
| `RNTI` | `rnti` | Scheduled UE RNTI | hexadecimal identity |
| `BLER` | `bler` | gNB PDSCH HARQ BLER | percent |
| `SINR` | `sinr` | UE-reported SSB/CSI-RS SINR | dB; `N/A` when unavailable |
| `WB CQI` | `cqi` | UE-reported wideband CSI CQI | index 0-15 |
| `RI/Layers` | `ri` | UE RI, or scheduled layers when RI is unavailable | rank/layers |
| `MCS` | `mcs` | PDSCH MCS | index |
| `Qm` | `qm` | PDSCH modulation order | bits/symbol |
| `NPRB` | `nprb` | Allocated PRBs | PRBs |
| `Layers` | `layers` | Scheduled PDSCH layers | layers |
| `Symbols` | `nsymb` | Allocated PDSCH symbols | OFDM symbols |
| `TBS (bits)` | `tbs` | Transport block size | bits |
| `RV` | `rv` | Redundancy version | 0-3 |
| `NDI` | `ndi` | New-data indicator | 0/1 |
| `PMI` | `pmi_x1`, `pmi_x2` | UE-reported PMI fields | codebook indices |
| `SCS` | `scs_khz` | DL subcarrier spacing | kHz |
| `BWP BW` | derived from `n_rb_dl`, `scs_khz` | Active DL BWP bandwidth | MHz |
| `Sched SE` | GUI-derived | Scheduled DL spectral efficiency | bit/s/Hz |
| `App SE` | GUI-derived | DL iperf throughput / DL BWP bandwidth | bit/s/Hz; `N/A` when direction is not DL |

### 3.3 UL Measurements panel

| GUI field | Reader key | Meaning | Unit / unavailable behavior |
|---|---|---|---|
| `Frame` | `frame` | Scheduled PUSCH frame | frame number |
| `Slot` | `slot` | Scheduled PUSCH slot | slot number |
| `RNTI` | `rnti` | Scheduled UE RNTI | hexadecimal identity |
| `BLER` | `bler` | gNB PUSCH HARQ BLER | percent |
| `SINR` | `sinr` | Filtered PUSCH power-control SNR | dB |
| `TA` | `timing_advance` | Latest PUSCH timing-advance command | integer; `N/A` for `0xffff` |
| `MCS` | `mcs` | PUSCH MCS | index |
| `Qm` | `qm` | PUSCH modulation order | bits/symbol |
| `NPRB` | `nprb` | Allocated PRBs | PRBs |
| `Layers` | `layers` | Scheduled PUSCH layers | layers |
| `Symbols` | `nsymb` | Allocated PUSCH symbols | OFDM symbols |
| `TBS (bits)` | `tbs` | Transport block size | bits |
| `RV` | `rv` | Redundancy version | 0-3 |
| `NDI` | `ndi` | New-data indicator | 0/1 |
| `PUSCH CQI` | `ul_cqi` | PUSCH SNR quantized to 8 bits | index 0-255; `N/A` for `0xff` |
| `TPMI` | `tpmi`, `tpmi_valid` | Scheduled transmit precoder index | index; `(default)` when not backed by valid SRS feedback |
| `RSSI` | `rssi` | PUSCH RSSI | dBFS; `N/A` when unavailable |
| `SCS` | `scs_khz` | UL subcarrier spacing | kHz |
| `BWP BW` | derived from `n_rb_ul`, `scs_khz` | Active UL BWP bandwidth | MHz |
| `Sched SE` | GUI-derived | Scheduled UL spectral efficiency | bit/s/Hz |
| `App SE` | GUI-derived | UL iperf throughput / UL BWP bandwidth | bit/s/Hz; `N/A` when direction is not UL |

Important distinction:

- `PUSCH CQI` is a gNB UL SNR index in the range 0-255:

  ```text
  ul_cqi = clamp((640 + PUSCH_SNR_x10) / 5, 0, 255)
  ```

- `WB CQI` is the UE-reported downlink wideband CSI CQI in the range 0-15.
- These two values are different measurements and must not be compared
  directly.

### 3.4 SRS Channel panel

| GUI field | Reader key | Meaning | Unit |
|---|---|---|---|
| `SRS RNTI` | `rnti` | UE RNTI associated with the SRS estimate | hexadecimal identity |
| `frame` | `frame` | SRS snapshot frame | frame number |
| `slot` | `slot` | SRS snapshot slot | slot number |
| `PRB` | `n_rb` | SRS measurement bandwidth | PRBs |
| `fft` | `fft_size` | SRS FFT size | samples |
| `symbols` | `n_symbols` | Number of SRS symbols in the snapshot | count |
| `SNR` | `snr` | gNB estimated wideband SRS SNR | dB |
| `capacity` | `capacity` | Mean SVD capacity under configured SNR | bit/s/Hz |
| `rank` | `rank` | Mean numerical SRS channel rank | rank |
| `cond` | `condition_number` | Mean SRS condition number, capped | ratio |

### 3.5 gNB plots

| Panel | Selector values |
|---|---|
| Throughput | `throughput` |
| DL Measurements | `dl_sinr`, `dl_bler`, `dl_mcs`, `dl_nprb`, `dl_tbs`, `dl_sched_se`, `dl_app_se` |
| UL Measurements | `ul_sinr`, `ul_bler`, `ul_mcs`, `ul_nprb`, `ul_tbs`, `ul_sched_se`, `ul_app_se` |
| SRS Channel | `srs_capacity`, `srs_rank`, `srs_condition`, `srs_snr` |

The meanings are the same as the corresponding GUI fields and CSV columns
described above and below.

## 4. gNB CSV columns

The file is `gui/record/gui_gnb_log_<timestamp>.csv`.

### 4.1 Common columns

| CSV column | Source | Meaning | Unit |
|---|---|---|---|
| `timestamp` | GUI | Recording time | local `%Y%m%d_%H%M%S_%f` |
| `test_round` | GUI | iperf test round counter | integer |
| `iperf_direction` | GUI | Active iperf direction | `DL`, `UL`, or empty |
| `throughput_mbps` | iperf3 parser | Latest interval throughput | Mbit/s |

### 4.2 UL columns

| CSV column | SHM/source | Meaning | Unit / unavailable behavior |
|---|---|---|---|
| `ul_frame` | `frame` | PUSCH frame | frame number |
| `ul_slot` | `slot` | PUSCH slot | slot number |
| `ul_rnti` | `rnti` | UE RNTI | identity |
| `ul_bler` | `bler_x1000 / 10` | PUSCH HARQ BLER | percent |
| `ul_sinr` | `sinr_db_x10 / 10` | Filtered PUSCH power-control SNR | dB |
| `ul_mcs` | `mcs` | PUSCH MCS | index |
| `ul_nprb` | `num_rbs` | Allocated PRBs | PRBs |
| `ul_layers` | `num_layers` | Scheduled PUSCH layers | layers |
| `ul_qm` | `qam_mod_order` | PUSCH modulation order | bits/symbol |
| `ul_tbs` | `tbs` | Transport block size | bits |
| `ul_nsymb` | `num_symbols` | Allocated PUSCH symbols | OFDM symbols |
| `ul_n_rb_ul` | `n_rb_ul` | Active UL BWP size | PRBs |
| `ul_scs_khz` | `subcarrier_spacing_khz` | UL subcarrier spacing | kHz |
| `ul_sched_se` | derived | Scheduled UL spectral efficiency | bit/s/Hz; empty when inputs are invalid |
| `ul_app_se` | derived | Application UL spectral efficiency | bit/s/Hz; empty when direction is not UL |
| `ul_timing_advance` | `timing_advance` | PUSCH timing advance | integer; empty for `0xffff` |
| `ul_cqi` | `ul_cqi` | Quantized PUSCH SNR index | 0-255; empty for `0xff` |
| `ul_tpmi` | `tpmi` | Scheduled UL TPMI | index; empty for `0xff` |
| `ul_tpmi_valid` | `tpmi_valid` | Whether TPMI came from valid SRS feedback | 1/0 |
| `ul_rv` | `rv` | Redundancy version | 0-3 |
| `ul_ndi` | `new_data_indicator` | New-data indicator | 0/1 |
| `ul_target_code_rate` | `target_code_rate` | Target code rate | units of 1/1024 |
| `ul_rssi_dbfs` | `(rssi_fapi - 1280) / 10` | PUSCH RSSI | dBFS; empty when unavailable |

### 4.3 DL columns

| CSV column | SHM/source | Meaning | Unit / unavailable behavior |
|---|---|---|---|
| `dl_frame` | `frame` | PDSCH frame | frame number |
| `dl_slot` | `slot` | PDSCH slot | slot number |
| `dl_rnti` | `rnti` | UE RNTI | identity |
| `dl_bler` | `bler_x1000 / 10` | PDSCH HARQ BLER | percent |
| `dl_sinr` | `sinr_db_x10 / 10` | UE-reported SSB/CSI-RS SINR | dB; empty when unavailable |
| `dl_mcs` | `mcs` | PDSCH MCS | index |
| `dl_nprb` | `num_rbs` | Allocated PRBs | PRBs |
| `dl_layers` | `num_layers` | Scheduled PDSCH layers | layers |
| `dl_qm` | `qam_mod_order` | PDSCH modulation order | bits/symbol |
| `dl_tbs` | `tbs` | Transport block size | bits |
| `dl_cqi` | `cqi` | UE-reported wideband CSI CQI | index 0-15 |
| `dl_ri` | `ri` | UE RI or scheduled-layer fallback | rank/layers |
| `dl_nsymb` | `num_symbols` | Allocated PDSCH symbols | OFDM symbols |
| `dl_rv` | `rv` | Redundancy version | 0-3 |
| `dl_ndi` | `new_data_indicator` | New-data indicator | 0/1 |
| `dl_target_code_rate` | `target_code_rate` | Target code rate | units of 1/1024 |
| `dl_pmi_x1` | `pmi_x1` | UE CSI PMI part x1 | codebook index |
| `dl_pmi_x2` | `pmi_x2` | UE CSI PMI part x2 | codebook index |
| `dl_n_rb_dl` | `n_rb_dl` | Active DL BWP size | PRBs |
| `dl_scs_khz` | `subcarrier_spacing_khz` | DL subcarrier spacing | kHz |
| `dl_sched_se` | derived | Scheduled DL spectral efficiency | bit/s/Hz; empty when inputs are invalid |
| `dl_app_se` | derived | Application DL spectral efficiency | bit/s/Hz; empty when direction is not DL |

### 4.4 SRS columns

| CSV column | Source | Meaning | Unit / unavailable behavior |
|---|---|---|---|
| `srs_capacity` | SRS channel metrics | Mean SVD capacity under configured SNR | bit/s/Hz |
| `srs_rank` | SRS channel metrics | Mean numerical SRS rank | rank |
| `srs_condition` | SRS channel metrics | Mean SRS condition number, capped | ratio |
| `srs_snr` | `snr_db_x10 / 10` | Wideband SRS SNR | dB |

## 5. Cross-Monitor Comparisons

| Quantity | UE GUI/CSV | gNB GUI/CSV | Comparison rule |
|---|---|---|---|
| Downlink SINR | `sinr_dB` = UE SSB SINR | `dl_sinr` = UE-reported SSB/CSI-RS SINR | Similar link-side quantity, but sampling and filtering differ |
| PUSCH SNR | Not available | `ul_sinr` and `ul_cqi` | gNB-only UL measurement |
| Wideband CQI | `wideband_cqi_dB` proxy | `dl_cqi` 0-15 CSI index | Not equivalent |
| Channel capacity | CSI-RS `capacity` | SRS `srs_capacity` | Different reference signals and directions |
| Scheduled SE | `dl_sched_se` | `dl_sched_se`, `ul_sched_se` | Formula is the same; source snapshots differ |

In particular, `ul_cqi` must not be compared with `dl_cqi`: `ul_cqi` is an
8-bit PUSCH SNR index, while `dl_cqi` is the UE's 4-bit wideband CSI report.
