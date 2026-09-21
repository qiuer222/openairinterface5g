# OAI SNR Definitions and Fixed-Point Channel Scaling

## 1. Purpose

This document explains the different SNR values printed by OAI and shown by
the UE/gNB performance monitors. It also explains what the `c16_t` samples in
`rxdataF`, `csi_rs_estimated_channel_freq`, and `srs_estimated_channel_freq`
actually represent.

For the detailed signal-energy, noise-energy, and per-receiver SINR derivation,
see `doc/local-edit/measurements/noise_signal_sinr.md`.

## 2. Different SNR values in OAI

OAI computes SNR in several places. They all use dB, but they measure different
signals and use different fixed-point references. They should not be treated as
the same quantity.

### 2.1 UE-reported CSI / SSB SINR

The UE computes SSB SINR in:

```text
openair1/PHY/NR_UE_ESTIMATION/nr_ue_measurements.c
```

It is stored as:

```text
UE->measurements.ssb_sinr_dB[ssb_index]
```

and later delivered to the gNB through CSI reports. The gNB stores the latest
values in:

```text
NR_UE_sched_ctrl_t.CSI_report.ssb_rsrp_report.r[0].SINRx10
NR_UE_sched_ctrl_t.CSI_report.csirs_rsrp_report.r[0].SINRx10
UE->mac_stats.cumul_sinrx10
UE->mac_stats.num_sinr_meas
```

`SINRx10` is SINR multiplied by 10 to avoid fractional dB.

The implementation uses:

```text
P_rsrp = average |rxdataF|^2 over SSS tones and RX antennas
P_n_ant = average |rxdataF|^2 over 8 tones on each side of the SSS spectrum
P_n_tot = sum over RX antennas of P_n_ant
P_n_avg = 0.5 * P_n_avg_old + 0.5 * P_n_tot
P_signal = max(P_rsrp - P_n_avg, 0)
SSB_SINR_x10 = dB_fixed_x10(P_signal) - dB_fixed_x10(P_n_avg)
```

This is the value exposed as `ssb_sinr_dB`. The subtraction is performed in
the linear energy domain before converting to dB. The SSS edge tones supply a
noise-plus-interference estimate; OAI does not maintain a separate interference
covariance for this value.

Scaling caveat: `P_rsrp` is averaged over RX antennas and SSS tones, while
`P_n_tot` is summed over RX antennas before filtering. For more than one RX
antenna this is not the same antenna normalization. Treat the value as the
OAI SSB SINR implementation, not as a strictly calibrated multi-RX SINR.

The MAC usage message prints the average only when `num_sinr_meas > 0`:

```text
average SINR %d.%d dB (%d meas)
```

This is the only gNB-side DL SINR suitable for the gNB GUI. OpenAirInterface
does not calculate a gNB RF DL SINR. If the UE is not configured to report
SSB/CSI-RS SINR, the GUI reports `dl_sinr` as unavailable instead of using an
uplink estimate.

The UE also maintains a separate wideband CQI proxy:

```text
wideband_cqi_tot = rx_power_tot_dB - n0_power_tot_dB
wideband_cqi_avg = rx_power_avg_dB - n0_power_avg_dB
```

Both `dB_fixed()` values are integer dB, so these proxy values must not be
divided by 10. The proxy compares average DL channel-estimate energy with the
SSS-derived noise estimate. It is not the standards-based SSB SINR and is
reported separately as `wideband_cqi_dB`.

### 2.2 gNB PUCCH SNR

For PUCCH format 0/1, the gNB calculates the received PUCCH resource energy
and compares it with the unused-RB noise floor:

```text
P_pucch = average |rxdataF|^2 over PUCCH symbols and spatial streams
N0_dB = max(n0_subband_power_tot_dB[hop0],
            n0_subband_power_tot_dB[hop1])
SNR_x10 = 10 * dB_fixed(P_pucch) - 10 * N0_dB
ul_cqi = clamp((640 + SNR_x10) / 5, 0, 255)
```

For PUCCH power control, the MAC reconstructs:

```text
pucch_snrx10 = ul_cqi * 5 - 640
```

This is filtered by `nr_mac_pc_snr()` and can be read back with:

```text
nr_mac_get_snr(&sched_ctrl->pucch_pc)
```

This is the `SNR 16.1 dB` value shown in the MAC usage line:

```text
pucch0_DTX 0 (SNR 16.1+1.1 dB)
```

It is a PUCCH power-control estimate, not CSI SINR. The PUCCH energy includes
received signal plus noise because it is measured before sequence-based
separation. PUCCH format 2 SNR handling is explicitly marked as not correct in
the MAC code and should not be used as a calibrated metric.

### 2.3 gNB PUSCH SNR

For PUSCH, the gNB PHY estimates:

```text
SNRtimes10 = dB_fixed_x10(pusch->ulsch_power_tot)
             - dB_fixed_x10(pusch->ulsch_noise_power_tot)
```

The PHY reports this as a CQI:

```text
ul_cqi = clamp((640 + SNRtimes10) / 5, 0, 255)
```

The MAC reconstructs and filters the PUSCH SNR:

```text
pusch_snrx10 = ul_cqi * 5 - 640 - 10 * phr_txpower_calc
avg_snr = 0.975 * avg_snr + 0.025 * pusch_snrx10 / 10
```

`phr_txpower_calc` removes the delta-MCS power offset when that mode is
enabled. `avg_snr` is the filtered value stored in `pusch_pc.avg_snr`. The
power-control decision can additionally include `tpc_in_flight` through
`nr_mac_get_snr()`, but the gNB GUI records `avg_snr` rather than that
instantaneous adjusted value.

The MAC usage line shows the filtered PUSCH power-control SNR:

```text
SNR 22.1 (+2.1) dB
```

The gNB GUI UL panel uses the same filtered `pusch_pc.avg_snr` source.

### 2.4 gNB SRS SNR

For SRS, the gNB estimates:

```text
*snr = dB_fixed(signal_power_avg) - dB_fixed(noise_power_avg)
```

where:

```text
signal_power_avg = mean over RX antennas and SRS ports
noise_power_avg  = mean SRS noise power
```

This is stored in the SRS shared-memory header as `snr_db_x10`.

The wideband SRS noise calculation contains an additional division by the
number of SRS subcarriers:

```text
P_noise_code =
  (1 / N_sc) * sum_k |noise_k|^2 / N_sc

P_noise_avg = average over RX antennas of P_noise_code
SRS_SNR_dB = dB_fixed(P_signal_avg)
           - dB_fixed(max(P_noise_avg, 1))
```

The per-RB SRS SNR instead uses the average noise energy of 12 tones without
the extra `1 / N_sc`. Therefore the wideband SRS SNR and `snr_per_rb` are not
on identical normalization. The gNB GUI `srs_snr` uses the wideband value.

### 2.5 UE wideband CQI proxy

The UE `nr_ue_measurements()` path computes:

```text
P_rx_tot = sum over TX ports and RX antennas
           of average |dl_ch_estimates|^2
P_n_tot = sum over RX antennas of the SSS-edge noise estimate
wideband_cqi_tot = dB_fixed(P_rx_tot) - dB_fixed(P_n_tot)
wideband_cqi_avg = dB_fixed(P_rx_avg) - dB_fixed(P_n_avg)
```

The measurement and noise filters use `K1 = K2 = 512`, equivalent to a 0.5
first-order IIR update. This value compares DL channel-estimate energy with an
SSS-derived noise estimate. It is not SSB SINR and is not an absolute SNR.

### 2.6 UE PDSCH MMSE noise variance

For PDSCH channel estimation, OAI estimates a receiver noise variance from the
difference between the LS DMRS estimate and the interpolated/filtered channel:

```text
nvar_estimate =
  sum |H_LS(k) - H_est(k)|^2
  / (number_of_estimates * number_of_RX_antennas)
```

The value is used to regularize the MMSE receiver:

```text
H^H H + nvar * I
```

This `nvar` is a receiver-internal MMSE regularization term. It is not the
same as the SSS-edge noise power and is not exposed as an SINR in the GUI.
The current interpolation functions overwrite the per-call `nvar` for each RX
antenna, and `nr_ue_pdsch_procedures()` performs additional averaging by
symbols, layers, and RX antennas. Use this value only as an implementation
parameter, not as calibrated noise power.

### 2.7 Summary

| Metric | Signal estimate | Noise/interference estimate | Main use |
|---|---|---|---|
| gNB PUSCH instantaneous SNR | allocated PUSCH RE energy | unused-RB `n0_subband_power` | PHY CQI and UL power control |
| gNB PUSCH `ul_sinr` | filtered PUSCH SNR | same unused-RB estimate | gNB GUI UL panel |
| gNB PUCCH SNR | received PUCCH resource energy | unused-RB `n0_subband_power_tot_dB` | PUCCH power control |
| gNB SRS SNR | interpolated SRS channel energy | unoccupied SRS-tone noise | SRS GUI and SRS scheduling |
| UE SSB SINR | SSS/SSB RSRP energy | SSS edge-tone noise | UE and gNB DL SINR reports |
| UE wideband CQI | PDSCH channel-estimate energy | SSS edge-tone noise | legacy wideband proxy |
| UE PDSCH `nvar` | LS-vs-filtered channel residual | DMRS estimation residual | PDSCH MMSE regularization |

### 2.8 Analysis "effective SNR"

Offline analysis often uses:

```text
EffectiveSNR_dB = RSRP_dBm + 100
```

This is an analysis convention, not a PHY measurement. It is useful for
normalizing recorded channel matrices to a target SNR before capacity
calculation.

## 3. Which SNR is shown in the GUI

### UE GUI

The UE GUI displays:

- `sinr_dB`: the SSB SINR from `ue->measurements.ssb_sinr_dB`, quantized to
  0.1 dB in shared memory.
- `wideband_cqi_dB`: the integer-dB wideband channel-estimate/noise proxy.
- the SS-RSRP values from the UE measurement path.

### gNB GUI DL

The DL panel uses only:

```text
UE->mac_stats.cumul_sinrx10 / num_sinr_meas
```

when at least one UE SINR report is available. Otherwise the last valid
UE-reported SINR is read from per-UE scheduler state and copied by the next
PDSCH scheduler snapshot. `PUCCH power-control SNR` is never used as
`dl_sinr`. If no UE SINR report has ever been available, `dl_sinr` is empty in
CSV and shown as `N/A` in the GUI.

### gNB GUI UL

The UL panel uses:

```text
sched_ctrl->pusch_pc.avg_snr
```

which matches the filtered PUSCH SNR printed by MAC usage.

### gNB GUI SRS

The SRS panel uses `snr_db_x10` from the SRS shared-memory header.

### Previous value errors

The old gNB GUI could show `pucch_pc.avg_snr` as `dl_sinr` when no UE SINR
report was present. That value is an uplink PUCCH power-control estimate and
must not be interpreted as downlink SINR.

The old UE GUI stored `wideband_cqi_tot` in a field nominally interpreted as
dB x10, then divided it by 10 in Python. However, `wideband_cqi_tot` is the
difference of two `dB_fixed()` results and is therefore integer dB. For
example, a stored value of `-30` was displayed as `-3.0 dB`. The current GUI
uses the separately measured SSB SINR for `sinr_dB`, stores it explicitly as
dB x10, and records the legacy proxy separately as `wideband_cqi_dB`.

## 4. What is `c16_t`?

`c16_t` is a fixed-point complex sample:

```c
typedef struct {
  int16_t r;  /* real part */
  int16_t i;  /* imaginary part */
} c16_t;
```

It is 4 bytes and stores signed 16-bit real and imaginary components. It is
not a `float` and not a physical SI unit.

## 5. Is `rxdataF` a real value of the signal?

`rxdataF` is a real measured signal in the sense that it is produced from
received RF samples after down-conversion, ADC, gain, synchronization, FFT,
and fixed-point scaling. It is not the raw physical voltage at the antenna and
it is not a calibrated physical power in watts.

Important scaling facts:

- OAI performs `dft()` without an explicit `1/N` normalization.
- The frequency-domain samples retain an FFT-size-dependent scaling.
- The RF chain gain is applied before the samples are available to PHY.
- The samples are stored as signed 16-bit values, so they are quantized.
- RSRP/SINR conversion compensates with constants such as:

```text
SQ15_SQUARED_NORM_FACTOR_DB = 90.309 dB
```

and:

```text
10 * log10(fft_size)
```

Therefore `rxdataF` is a scaled, quantized fixed-point representation of the
received frequency-domain signal, not an absolute physical voltage.

## 6. Is the recorded channel a real value or quantized/scaled?

The recorded channel is a quantized and scaled estimate, not an absolute
physical channel coefficient.

### CSI-RS channel

`csi_rs_estimated_channel_freq` is produced by:

```text
nr_csi_rs_channel_estimation()
```

from:

```text
csi_rs_ls_estimated_channel
```

The generated CSI-RS sequence uses `AMP = 512`, and the LS estimate is
computed with fixed-point operations such as `c16MulConjShift()`. The result is
written to shared memory as `c16_t` samples.

The Python `gui/csi_reader.py` reads those `c16_t` samples and converts them
to `complex128`:

```text
h = arr["r"].astype(np.float64) + 1j * arr["i"].astype(np.float64)
```

This preserves the relative complex channel shape, but not absolute physical
units.

### SRS channel

`srs_estimated_channel_freq` is produced by:

```text
nr_srs_ls_channel_estimation()
nr_srs_channel_interpolation()
```

and is also written to shared memory as `c16_t`.

`gui/srs_reader.py` converts the raw `c16_t` data to `complex128` in the same
way.

### Scaling factor is not stored

The shared-memory channel files contain only the quantized `c16_t` channel
values. They do **not** contain:

- `G_rx`
- `rx_gain_offset`
- `AMP`
- `log2_maxh`
- LS/interpolation normalization
- ADC/RF gain calibration

Therefore, the recorded channel alone has no recoverable absolute scaling
factor. It preserves relative amplitude/phase and channel shape, but not
physical power.

To restore physical power, you must either export raw `rxdataF` with the
matching `G_rx`, or calibrate the recorded channel against a reference RSRP or
raw `rxdataF` power.

## 7. Consequences for analysis

Because the channel is quantized and scaled:

- Relative amplitude/phase between subcarriers and antennas is meaningful.
- Absolute magnitude is not calibrated to physical channel gain.
- Capacity analysis must normalize the channel to a chosen SNR.
- RSRP/SNR from the measurement path should be used as independent predictors,
  not as exact channel-calibration constants.

The offline analysis normalizes the recorded channel as:

```text
P_target = 10^(SNR_dB / 10)
mean_power = mean over valid subcarriers of ||H(k)||^2
H_norm = H * sqrt(P_target / mean_power)
```

This makes the absolute scaling irrelevant while preserving channel shape.

## 8. Restoring a power-calibrated channel

If you want to restore a physical-power-scaled channel, the cleanest source is
the raw `rxdataF` samples before channel estimation.

Let:

```text
x_c16 = rxdataF value as a c16_t sample
P_linear = mean over the selected REs of |x_c16|^2
```

OAI converts the same style of `P_linear` to RSRP dBm with:

```text
RSRP_dBm =
  10 * log10(P_linear)
  + 30
  - SQ15_SQUARED_NORM_FACTOR_DB
  - G_rx
  - 10 * log10(fft_size)
```

Therefore the physical power of the reference-signal REs is:

```text
P_W =
  P_linear
  * 10^(-(SQ15_SQUARED_NORM_FACTOR_DB + G_rx + 10*log10(fft_size)) / 10)
```

and the physical amplitude is:

```text
h_physical =
  x_c16
  * 10^(-(SQ15_SQUARED_NORM_FACTOR_DB + G_rx + 10*log10(fft_size)) / 20)
```

where:

```text
SQ15_SQUARED_NORM_FACTOR_DB = 90.309 dB
G_rx = openair0_cfg.rx_gain[ant] - openair0_cfg.rx_gain_offset[ant]
```

This raw-`rxdataF` restoration aligns with RSRP by construction, because RSRP is
computed from the same fixed-point received samples with the same gain/FFT
correction.

### Recorded CSI-RS or SRS estimated channel

The recorded `csi_rs_estimated_channel_freq` and
`srs_estimated_channel_freq` are LS-estimated and interpolated channels. They
are not raw `rxdataF`:

- CSI-RS generation uses `AMP = 512`.
- The LS estimate uses `c16MulConjShift(tx, rx, csi_rs_generated_signal_bits)`.
- Interpolation applies filter coefficients, so there is an additional scaling
  factor that is not part of the RSRP formula.

As an approximation, the recorded CSI-RS channel is roughly proportional to:

```text
H_approx = csi_rs_estimated_channel_freq / AMP
```

but the interpolation normalization means this is not exact. To restore a
recorded channel with physical power, the reliable approach is empirical
calibration:

```text
calibration_factor =
  mean(|H_calibrated_physical|^2) / mean(|H_recorded_c16|^2)
```

where `H_calibrated_physical` is obtained from raw `rxdataF` over the same REs,
or from a measured/reference RSRP.

Then:

```text
H_physical = H_recorded_c16 * sqrt(calibration_factor)
```

## 9. Will restored RSRP align?

- Restoring from raw `rxdataF` with the formula above aligns with OAI RSRP for
  the same reference signal.
- Restoring from the recorded LS/interpolated CSI-RS channel does not align
  automatically. It requires the additional LS/AMP/interpolation calibration
  factor.
- UE GUI RSRP is SSB-based while the CSI-RS channel is CSI-RS-based. Even after
  scaling correction, SSB RSRP and CSI-RS channel power may differ because they
  are different reference signals.

## 10. Is `rxdataF` scaled by the same number in one run?

Within one FFT/slot processing window, all `rxdataF` samples are produced with
the same RF gain, ADC/front-end scaling, and FFT fixed-point scaling. Therefore
the same multiplicative scale is applied to every subcarrier in that window.

The scale is not constant forever:

- If UE AGC is enabled, OAI can change `openair0_cfg.rx_gain[0]` between slots.
- The USRP driver may clamp the requested gain to the hardware range.
- `rx_gain_offset` changes with frequency/bandwidth calibration.

So `rxdataF` is uniformly scaled within one slot, but its absolute scale can
change across slots when the gain changes.

## 11. Real power and ADC adjustment

`rxdataF` does not directly contain physical power. Its squared magnitude is a
fixed-point power proxy:

```text
proxy_power = |rxdataF|^2
```

Physical power is obtained by applying the RSRP-style calibration:

```text
G_rx = openair0_cfg.rx_gain[ant] - openair0_cfg.rx_gain_offset[ant]
```

and:

```text
P_W = proxy_power
      * 10^(-(90.309 + G_rx + 10*log10(fft_size)) / 10)
```

ADC adjustment exists, but OAI does not normally expose a separate software
"ADC gain" field:

- The RF front-end gain is set through `openair0_cfg.rx_gain[]`.
- The USRP/radio driver applies `rx_gain - rx_gain_offset` and clamps it to the
  hardware ADC/RF gain range.
- The UE AGC path is `nrue_ru_adjust_rx_gain()`, which modifies
  `rx_gain[0]` and calls `trx_set_gains_func()`.
- The gNB RX gain is configured as `max_rxgain - att_rx` in `nr-ru.c`.

Therefore, if you restore power from `rxdataF`, you must use the actual
`rx_gain - rx_gain_offset` value for that antenna and slot, not a fixed nominal
value, especially when UE AGC is active.

## 12. How to observe `G_rx` changes

### UE

OAI logs the applied UE RX gain in `executables/nr-ue-ru.c`:

```text
Rxgain adjusted by <gain_change> dB, RX gain: <applied_rxgain> dB
```

where:

```text
applied_rxgain = openair0_cfg.rx_gain[0] - openair0_cfg.rx_gain_offset[0]
```

Every time UE AGC changes the gain, this line is printed. You can capture it
with:

```bash
./nr-uesoftmodem ... 2>&1 | grep -E "Rxgain adjusted|RX Gain|Actual RX gain"
```

For USRP devices, the driver also prints:

```text
RX Gain <chain> <rx_gain> (<offset>) => <rx_gain-offset> (max <max>)
Actual RX gain: <value>
```

Use `Actual RX gain` as the final applied value.

### gNB

The gNB RX gain is normally static:

```text
G_rx = max_rxgain - att_rx - rx_gain_offset
```

It can still be clamped by the radio driver. Check once during startup:

```bash
./nr-softmodem ... 2>&1 | grep -E "RX Gain|Actual RX gain"
```

If `Actual RX gain` is stable and equal to the configured requested value,
then `G_rx` is not changing during the run.

### Programmatically

The current value can be read from:

```c
openair0_cfg[ue->rf_map.card].rx_gain[ant]
  - openair0_cfg[ue->rf_map.card].rx_gain_offset[ant]
```

For the gNB, use:

```c
openair0_cfg[ru_id].rx_gain[ant]
  - openair0_cfg[ru_id].rx_gain_offset[ant]
```

If you want the GUI to show it, this value must be exported through the
shared-memory measurement struct or recorded in a log.

## 13. How `G_rx` adjusts

`G_rx` is not a single OAI variable. It is the difference:

```text
G_rx = openair0_cfg.rx_gain[ant] - openair0_cfg.rx_gain_offset[ant]
```

`rx_gain_offset` is set by the RF driver from calibration tables. It depends on
frequency, device type, and in some cases bandwidth. For a fixed carrier it
usually remains constant.

`rx_gain` changes in these cases:

### UE AGC

If UE AGC is enabled, `nrue_ru_adjust_rx_gain()` changes
`openair0_cfg.rx_gain[0]`:

```c
cfg0->rx_gain[0] += gain_change;
dev0->trx_set_gains_func(dev0, cfg0);
```

The requested hardware gain is:

```text
cfg0->rx_gain[0] - cfg0->rx_gain_offset[0]
```

If the USRP driver clamps this to the hardware maximum, it returns the gain
difference and OAI applies it back:

```c
cfg0->rx_gain[0] += ret_gain;
```

So after AGC, `rx_gain` changes and therefore `G_rx` changes.

### Initial synchronization

During initial sync, OAI computes:

```text
adjust_rxgain = TARGET_RX_POWER - rsrp_db_per_re
```

If AGC is enabled, this adjustment is applied through
`nrue_ru_adjust_rx_gain()`. If AGC is disabled, `G_rx` remains at the
configured initial value.

### gNB

The gNB RX gain is configured as:

```text
openair0_cfg.rx_gain[i] = RU->max_rxgain - RU->att_rx
```

The gNB does not run the same dynamic UE AGC path. Its `G_rx` is normally fixed
for the run, except for one-time driver clamping at startup.

## 14. Comparing channel capacity over time

Before comparing capacity across different times, define what you want to
compare:

### Case A: compare spatial channel quality only

Normalize every recorded channel to the same fixed SNR. This removes power
variation and lets you compare channel shape, rank, conditioning, and MIMO
spatial quality.

```text
P_target = 10^(SNR_dB / 10)
mean_power = mean over valid subcarriers of ||H(k)||^2
H_norm = H * sqrt(P_target / mean_power)
```

Use the same `SNR_dB` for every time sample, for example 20 dB.

### Case B: compare achievable capacity at the measured link quality

Use the measured/effective SNR from the same measurement, for example:

```text
EffectiveSNR_dB = RSRP_dBm + 100
```

or the UE CSI SINR / gNB PUSCH SNR / SRS SNR that corresponds to the channel.
In this case the SNR must be stored together with the channel, otherwise the
capacity values are not comparable.

### Raw channel vs recorded estimated channel

- Use raw `rxdataF` if you want to capture absolute power changes, AGC changes,
  and `G_rx` effects.
- Use the recorded CSI-RS/SRS estimated channel if you only need relative
  channel shape and are normalizing to a fixed SNR.

The recorded channel alone does not contain absolute power or `G_rx`, so do not
compare its raw power across different slots if AGC is enabled.

### Minimum metadata to store

For repeatable capacity comparison, store:

```text
timestamp / frame / slot
RNTI
num_rx_ant
num_ports
fft_size
n_rb
subcarrier_spacing
RSRP_dBm
SNR source and value
G_rx or rx_gain / rx_gain_offset
raw rxdataF when absolute power matters
```

## 15. Source locations

- `common/utils/nr/nr_common.h`: `SQ15_SQUARED_NORM_FACTOR_DB`
- `openair1/PHY/NR_UE_ESTIMATION/nr_ue_measurements.c`: SSB SINR and RSRP
- `openair1/PHY/NR_UE_TRANSPORT/csi_rx.c`: CSI-RS RSRP and CSI channel
- `openair1/SCHED_NR/phy_procedures_nr_gNB.c`: SRS channel estimation
- `openair2/LAYER2/NR_MAC_gNB/gNB_scheduler_uci.c`: CSI report parsing
- `openair2/LAYER2/NR_MAC_gNB/gNB_scheduler_ulsch.c`: PUSCH power control
- `gui/csi_reader.py`: `c16_t` to `complex128`
- `gui/srs_reader.py`: `c16_t` to `complex128`
