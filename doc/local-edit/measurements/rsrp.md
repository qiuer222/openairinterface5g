# OAI SS-RSRP Measurement and Antenna Receive Gain

## 1. Scope

This document describes the SS-RSRP value shown by the OAI UE performance
monitor (`gui/`), how per-antenna RSRP is computed, and what "RX gain" means in
the OAI measurement chain for both the NR UE and the NR gNB.

The GUI reads `/dev/shm/meas_dl`. The shared-memory struct now carries both:

- `rsrp_dBm`: the serving-cell aggregate SS-RSRP in dBm.
- `rsrp_per_ant_dBm[4]`: the same SS-RSRP calculation applied separately to
  each active UE RX antenna.

## 2. How OAI calculates the aggregate SS-RSRP

The serving-cell RSRP is computed in
`openair1/PHY/NR_UE_ESTIMATION/nr_ue_measurements.c`.

The current implementation averages the squared magnitude of the received
frequency-domain SSS samples over:

- each UE RX antenna, and
- subcarriers `k = 56 .. 182` of the SSS window.

Let:

```text
P_linear = mean over RX antennas and SSS REs of |rxdataF[a][k]|^2
```

Then OAI converts the fixed-point received power to dBm with:

```text
SS-RSRP_dBm =
  10 * log10(P_linear)
  + 30
  - SQ15_SQUARED_NORM_FACTOR_DB
  - G_rx
  - 10 * log10(fft_size)
```

where:

- `+30` converts the OAI internal value from dBW to dBm.
- `SQ15_SQUARED_NORM_FACTOR_DB = 90.3089986992` dB is the Q15 squared-norm
  calibration constant defined in `common/utils/nr/nr_common.h`.
- `G_rx` is the RX chain gain used by the RSRP conversion:

```text
G_rx = openair0_cfg[ue->rf_map.card].rx_gain[ant]
       - openair0_cfg[ue->rf_map.card].rx_gain_offset[ant]
```

- `10 * log10(fft_size)` removes the FFT/OFDM-symbol energy scaling of
  `rxdataF`, because `dft()` in OAI does not apply an explicit `1/N`
  normalization.

This is the same family of formula used by CSI-RS RSRP in
`openair1/PHY/NR_UE_TRANSPORT/csi_rx.c`.

## 3. Per-antenna RSRP

The shared-memory `rsrp_per_ant_dBm[]` values are produced by the same code
path as the aggregate value, with one change: the squared power is averaged
only over the SSS REs of that antenna, and the antenna's own RX gain is used:

```text
ant_rsrp_linear[a] = mean over SSS REs of |rxdataF[a][k]|^2

ant_rsrp_dBm[a] =
  10 * log10(ant_rsrp_linear[a])
  + 30
  - SQ15_SQUARED_NORM_FACTOR_DB
  - G_rx[a]
  - 10 * log10(fft_size)
```

The GUI displays these values in the DL Measurements panel and in the small
`RX antenna` bar chart. Inactive RX antennas are not included.

## 4. What "true antenna receive gain" means

OAI's RSRP correction does not include the physical gain of a passive antenna
element or beamforming aperture. The "antenna receive gain" in the measurement
path is the RF receiver chain gain applied between the antenna connector and
the digital baseband samples:

```text
effective_rx_gain_dB =
  configured_rx_gain_dB
  - rx_gain_offset_dB
```

The calibration offset is device/frequency dependent. For USRP devices it is
chosen from tables in `radio/USRP/usrp_lib.cpp`, for example 44 dB at 3.5 GHz
on a B210 and 77 dB at 3.5 GHz on an X310. The RF driver clamps the requested
gain to the device's maximum RX gain range.

### 4.1 NR UE RX gain

The NR UE command-line default is:

```text
--ue-rxgain 110
```

This value is stored in `nrUE_params.rx_gain`. At startup:

```text
UE->rx_total_gain_dB = RU->max_rxgain - RU->att_rx
```

and `nr_rf_card_config_gain()` copies the same value into every active
`openair0_cfg[].rx_gain[i]`. If UE AGC is enabled,
`nrue_ru_adjust_rx_gain()` modifies `openair0_cfg[].rx_gain[0]` dynamically.

For a B210 at 3.5 GHz with `--ue-rxgain 110`:

```text
requested USRP RX gain = 110 - 44 = 66 dB
```

For an X310 at 3.5 GHz:

```text
requested USRP RX gain = 110 - 77 = 33 dB
```

The USRP driver prints `Actual RX gain`, which is the final value to use for
interpreting the RSRP measurement.

### 4.2 NR gNB RX gain

The gNB RF chain is configured in `executables/nr-ru.c` as:

```text
openair0_cfg[].rx_gain[i] = RU->max_rxgain - RU->att_rx
```

For the shipped B210 band-78 gNB config:

```text
max_rxgain = 114
att_rx     = 12
nominal RX gain = 102 dB
```

After the 3.5 GHz B210 calibration offset:

```text
requested USRP RX gain = 102 - 44 = 58 dB
```

For an X310:

```text
requested USRP RX gain = 102 - 77 = 25 dB
```

If `att_rx = 0`, the requested values become 70 dB for B210 and 37 dB for X310.
The UHD driver may clamp these values to the hardware maximum; the log line
`Actual RX gain` is authoritative.

Note that `gNB->rx_total_gain_dB` is initialized to 130 in
`openair1/PHY/INIT/nr_init.c` for some legacy measurement paths. That constant
is not a reliable description of the actual USRP receive chain gain. For
uplink measurement interpretation, use the configured
`max_rxgain - att_rx - rx_gain_offset` value and the driver's actual gain log.

## 5. How to verify the gain on the testbed

Start the NR UE or gNB with normal RF logging and look for:

```text
Actual RX gain: ...
RX Gain <chain> <configured> (<offset>) => <requested> (max <device_max>)
```

Use the `Actual RX gain` value as `G_rx` if the code path has not already
subtracted the calibration offset. In OAI's SS-RSRP formula, `G_rx` is
`rx_gain - rx_gain_offset`, so the requested value shown by the USRP driver is
already the gain used by RSRP.

## 6. Files involved

- `openair1/PHY/NR_UE_ESTIMATION/nr_ue_measurements.c`
- `openair1/PHY/NR_UE_TRANSPORT/ue_shm.h`
- `openair1/SCHED_NR_UE/phy_procedures_nr_ue.c`
- `gui/meas_reader.py`
- `gui/plot_manager.py`
- `gui/oai_perf_monitor.py`
