# OAI Signal Energy, Noise Energy, and SINR

## 1. Scope

This document summarizes where OAI calculates noise power or signal energy for
NR and how those values are converted into SNR/SINR:

- gNB PUSCH;
- gNB PUCCH;
- gNB SRS;
- UE SSB SINR;
- UE wideband CQI;
- UE PDSCH MMSE noise variance.

The values are not interchangeable. They use different reference signals,
different noise estimators, different antenna normalization, and sometimes
different fixed-point scaling.

## 2. Digital Power Terminology

The main primitive is in `openair1/PHY/TOOLS/signal_energy.c`:

```text
signal_energy_nodc(x, L) =
    (1 / L) * sum(n=0..L-1) (x[n].r^2 + x[n].i^2)
```

`x` is a sequence of `c16_t` fixed-point complex samples. The result is an
average squared magnitude, used as a digital power proxy:

```text
P_digital = mean(|x|^2)
```

It is not calibrated watts, dBm, or joules. Converting to physical units
requires RF gain, ADC scaling, FFT scaling, and reference-signal calibration.

`signal_energy()` is similar but removes the DC component before computing the
average.

The dB conversion is in `openair1/PHY/TOOLS/dB_routines.c`:

```text
dB_fixed(x)    ~= 10 * log10(x)
dB_fixed_x10(x) ~= 100 * log10(x)
```

Both are fixed-point approximations. `dB_fixed(0)` is represented as `-90`
instead of negative infinity, and several call sites explicitly clamp values
to at least 1 before taking a ratio.

## 3. gNB Noise Estimates

### 3.1 Unused-RB I0 / N0

Function:

```text
openair1/PHY/NR_ESTIMATION/nr_measurements_gNB.c
gNB_I0_measurements()
```

The gNB scans PRBs that are not marked as used in the current UL slot. For each
RX antenna and PRB it computes the average energy of the 12 subcarriers:

```text
N0_raw[ant, rb, symbol] =
    (1 / 12) * sum(sc=0..11) |rxdataF[ant, sc]|^2
```

It averages over the valid symbols:

```text
N0_symbol_avg[ant, rb] =
    (1 / N_symbols) * sum(symbol) N0_raw[ant, rb, symbol]
```

Then it applies an exponential moving average:

```text
N0[ant, rb] = 0.9 * N0[ant, rb] + 0.1 * N0_symbol_avg[ant, rb]
```

The linear per-antenna value `N0[ant, rb]` is used by PUSCH. PUCCH uses a dB
value averaged over RX antennas:

```text
N0_db[rb] =
    dB_fixed((1 / N_ant) * sum(ant) N0[ant, rb])
```

This is a noise-plus-interference floor measured on unused PRBs. It is not a
thermal-noise-only value.

PUSCH uses:

```text
n0_subband_power[ant][rb]
```

PUCCH uses:

```text
n0_subband_power_tot_dB[rb]
```

### 3.2 SRS Noise

Function:

```text
openair1/PHY/NR_ESTIMATION/nr_ul_channel_estimation.c
nr_srs_noise_power_estimation()
```

The SRS receive path keeps samples from SRS subcarriers that do not contain the
expected SRS sequence. Those samples form `srs_received_noise`.

The implemented wideband noise formula is:

```text
P_noise_code[ant] =
    signal_energy_nodc(srs_received_noise[ant], N_sc) / N_sc

                = (1 / N_sc^2) * sum(k) |noise[k]|^2
```

The result is averaged over RX antennas:

```text
P_noise_avg = (1 / N_ant) * sum(ant) P_noise_code[ant]
```

The extra division by `N_sc` is significant. The per-RB calculation uses:

```text
P_noise_rb =
    signal_energy_nodc(srs_received_noise[ant], 12)

            = (1 / 12) * sum(sc=0..11) |noise[sc]|^2
```

It does not apply the additional division by the number of SRS subcarriers.
Therefore the wideband SRS SNR and per-RB SRS SNR have different normalization
unless the noise value is corrected.

### 3.3 UE SSS Edge-Tone Noise

Function:

```text
openair1/PHY/NR_UE_ESTIMATION/nr_ue_measurements.c
nr_ue_rrc_measurements()
```

The UE measures noise on eight tones below and eight tones above the SSS
spectrum:

```text
P_n_ant =
    (1 / 16) * sum(16 SSS-edge tones) |rxdataF|^2
```

It then sums over RX antennas:

```text
P_n_tot = sum(ant) P_n_ant
```

The filtered value uses:

```text
K1 = 512
K2 = 1024 - K1 = 512

P_n_avg = (K1 * P_n_avg_old + K2 * P_n_tot) / 1024
```

In steady state this is approximately:

```text
P_n_avg = 0.5 * P_n_avg_old + 0.5 * P_n_tot
```

This is also a noise-plus-interference estimate from selected SSS edge tones,
not an absolute thermal-noise measurement.

### 3.4 UE PDSCH MMSE Noise Variance

Functions:

```text
openair1/PHY/NR_UE_ESTIMATION/nr_dl_channel_estimation.c
NFAPI_NR_DMRS_TYPE1_linear_interp()
NFAPI_NR_DMRS_TYPE2_linear_interp()
```

The UE compares the raw LS channel estimate with the interpolated/filtered
channel:

```text
residual[k] = H_LS[k] - H_filtered[k]

nvar_estimate =
    sum(k) |residual[k]|^2
    / (N_estimates * N_RX_antennas)
```

The value is used by:

```text
openair1/PHY/NR_UE_TRANSPORT/nr_dlsch_demodulation.c
nr_dlsch_mmse()
```

as the diagonal regularization:

```text
H^H H + nvar * I
```

This is a receiver-internal MMSE noise/estimation-error term. It is not the
same as the SSS edge-tone noise used for `wideband_cqi`, and it is not exposed
as a GUI SINR.

Implementation caveat: each interpolation call overwrites its `nvar` output
for the current RX antenna. `nr_ue_pdsch_procedures()` subsequently sums
per-layer/per-symbol calls and divides by symbols, layers, and RX antennas.
Use it as an implementation parameter rather than exact calibrated noise
power.

## 4. Signal Energy Estimates

### 4.1 PUSCH

Function:

```text
openair1/PHY/NR_TRANSPORT/nr_ulsch_demodulation.c
nr_rx_pusch_tp()
```

For each spatial stream:

```text
P_pusch[s] =
    (1 / N_symbols) *
    sum(symbol) signal_energy_nodc(
                    allocated PUSCH REs,
                    N_RB * 12)
```

Thus it is the average received PUSCH energy per allocated RE, averaged over
the PUSCH symbols.

The matching noise is:

```text
P_pusch_noise[s] =
    average(N0[s][rb] over allocated PRBs)
```

### 4.2 SRS

Function:

```text
openair1/PHY/NR_ESTIMATION/nr_ul_channel_estimation.c
nr_srs_channel_interpolation()
```

For each RX antenna and SRS port:

```text
P_srs[ant, port] =
    signal_energy_nodc(
        srs_estimated_channel_freq[ant, port],
        N_SRS_subcarriers)

    = mean(k) |H_est[k]|^2
```

The wideband signal estimate is:

```text
P_srs_avg =
    (1 / (N_ant * N_ports))
    * sum(ant, port) P_srs[ant, port]
```

### 4.3 UE SSB RSRP

Functions:

```text
openair1/PHY/NR_UE_ESTIMATION/nr_ue_measurements.c
nr_ue_calculate_ssb_rsrp()
nr_ue_ssb_rsrp_measurements()
```

The SSS/SSB reference-signal energy is:

```text
P_rsrp =
    (1 / (N_ant * N_SSS_RE))
    * sum(ant, SSS tone) |rxdataF|^2
```

The physical SS-RSRP conversion is:

```text
RSRP_dBm =
    10 * log10(P_rsrp)
    + 30
    - SQ15_SQUARED_NORM_FACTOR_DB
    - G_rx
    - 10 * log10(fft_size)
```

where:

```text
G_rx = rx_gain - rx_gain_offset
SQ15_SQUARED_NORM_FACTOR_DB = 90.3089986992
```

### 4.4 UE PDSCH Channel Estimate and Wideband CQI

Function:

```text
openair1/PHY/NR_UE_ESTIMATION/nr_ue_measurements.c
nr_ue_measurements()
```

For each TX/RX pair:

```text
P_rx_spatial[ant_tx, ant_rx] =
    signal_energy_nodc(dl_ch_estimates, number_rbs * 12)
```

The total and filtered values are:

```text
P_rx_tot =
    sum(ant_tx, ant_rx) P_rx_spatial[ant_tx, ant_rx]

P_rx_avg = 0.5 * P_rx_avg_old + 0.5 * P_rx_tot
```

The wideband proxy is:

```text
wideband_cqi_tot =
    dB_fixed(P_rx_tot) - dB_fixed(P_n_tot)

wideband_cqi_avg =
    dB_fixed(P_rx_avg) - dB_fixed(P_n_avg)
```

It compares estimated PDSCH channel energy with the SSS edge-tone noise
estimate. It is not the SSB SINR and is not calibrated to absolute SNR.

## 5. SINR/SNR Formulas by Receiver

### 5.1 gNB PUSCH

PHY:

```text
P_signal_tot = sum(stream) P_pusch[stream]
P_noise_tot  = sum(stream) P_pusch_noise[stream]

SNR_x10 =
    dB_fixed_x10(P_signal_tot)
    - dB_fixed_x10(P_noise_tot)
```

Location:

```text
openair1/SCHED_NR/phy_procedures_nr_gNB.c
```

CQI conversion:

```text
ul_cqi = clamp((640 + SNR_x10) / 5, 0, 255)
```

MAC reconstruction:

```text
pusch_snrx10 =
    ul_cqi * 5 - 640 - 10 * phr_txpower_calc
```

Filtering:

```text
avg_snr = 0.975 * avg_snr
        + 0.025 * pusch_snrx10 / 10
```

The GUI UL `ul_sinr` records `pusch_pc.avg_snr`. Power control may use
`avg_snr + tpc_in_flight` through `nr_mac_get_snr()`, but the GUI does not
include `tpc_in_flight`.

PUSCH DTX detection compares:

```text
dB_fixed_x10(P_signal_tot)
    < dB_fixed_x10(P_noise_tot) + pusch_thres
```

### 5.2 gNB PUCCH

For PUCCH format 0/1:

```text
N0_dB =
    max(n0_subband_power_tot_dB[first_hop],
        n0_subband_power_tot_dB[second_hop])

SNR_x10 =
    10 * dB_fixed(P_pucch) - 10 * N0_dB
```

`P_pucch` is the average received energy over the PUCCH resource elements and
symbols. Because it is measured from the received resource before separating
signal and noise, it is a total received-energy-to-noise ratio, not a
noise-free signal SINR.

The same CQI mapping is used:

```text
ul_cqi = clamp((640 + SNR_x10) / 5, 0, 255)
```

The MAC reconstructs:

```text
pucch_snrx10 = ul_cqi * 5 - 640
```

and feeds it into the PUCCH power-control filter. PUCCH format 2 handling is
marked TODO/incorrect in the current MAC code.

### 5.3 gNB SRS

Wideband:

```text
SRS_SNR_dB =
    dB_fixed(P_srs_avg)
    - dB_fixed(max(P_noise_avg, 1))
```

Per RB:

```text
SRS_SNR_rb_dB =
    dB_fixed(P_srs_avg)
    - dB_fixed(max(P_noise_rb / N_ant, 1))
```

The GUI `srs_snr` uses the wideband formula. Because `P_noise_avg` includes an
extra `1 / N_sc`, the wideband value is not directly comparable with the
per-RB value without correcting the normalization.

### 5.4 UE SSB SINR

```text
P_signal = max(P_rsrp - P_n_avg, 0)

SSB_SINR_x10 =
    dB_fixed_x10(P_signal)
    - dB_fixed_x10(P_n_avg)
```

This is the value delivered to the gNB CSI report and later recorded as
`dl_sinr`.

The SSS edge-tone noise estimate may contain interference. Also,
`P_rsrp` averages over RX antennas, while `P_n_tot` and `P_n_avg` sum over RX
antennas. This antenna-normalization difference should be considered for
multi-RX measurements.

### 5.5 UE Wideband CQI

```text
wideband_cqi_tot =
    dB_fixed(P_rx_tot) - dB_fixed(P_n_tot)
```

This is a channel-estimate-to-SSS-noise proxy. It is intentionally recorded
separately from `dl_sinr`.

## 6. Mapping to GUI Records

| GUI field | Physical interpretation | Source |
|---|---|---|
| `dl_sinr` in gNB GUI | quantized UE-reported SSB SINR | UE CSI report |
| `sinr_dB` in UE GUI | UE SSB SINR | `ue->measurements.ssb_sinr_dB` |
| `wideband_cqi_dB` | UE PDSCH-channel-estimate / SSS-noise proxy | UE `nr_ue_measurements()` |
| `ul_sinr` in gNB GUI | filtered gNB PUSCH SNR | `pusch_pc.avg_snr` |
| `srs_snr` in gNB GUI | gNB wideband SRS SNR | NRS SRS estimator |
| `rssi` | received signal strength proxy | PUSCH/PUCCH RSSI fields |

The gNB GUI `dl_sinr` is never replaced by PUCCH or SRS SNR. The UE GUI
`wideband_cqi_dB` is not the same metric as `sinr_dB`.

## 7. Important Normalization Differences

1. PUSCH signal and noise are both average per-RE digital energies and are
   therefore directly comparable.
2. PUCCH signal energy includes received noise; it is not a separated signal
   power.
3. SRS wideband noise has an extra `1 / N_sc` compared with the per-RB noise
   estimate.
4. UE SSB signal energy is averaged across RX antennas, while SSS noise is
   summed across RX antennas before filtering.
5. UE PDSCH `nvar` is an MMSE residual/regularization estimate, not the SSS
   noise field.
6. None of these values is automatically a calibrated dBm or watts value.
   Physical calibration requires the RF gain, ADC/FFT scaling, and reference
   signal assumptions described in
   `snr_and_channel_scaling.md`.
