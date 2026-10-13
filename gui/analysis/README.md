# SRS/CSI Channel Analysis

Analyze one paired gNB/UE recording archive and compare channel capacity with
measured throughput.

## Input

```text
experiment/
  gnb_<timestamp>/
    gui_gnb_log_<timestamp>.csv
    gui_gnb_log_<timestamp>/srs_*.npy
  ue_<timestamp>/
    gui_ue_log_<timestamp>.csv
    gui_ue_log_<timestamp>/channel_*.npy
```

- `--direction ul`: gNB CSV is primary, SRS is the channel, UE CSV supplements
  RSRP and other UE-side fields.
- `--direction dl`: UE CSV is primary, CSI-RS is the channel, gNB CSV is
  optional supplemental data.
- All `test_round` groups in the archive pair are analyzed together.
- The `gnb_*` and `ue_*` folder timestamps may differ; only the prefixes are
  used to pair them. Multiple `gnb_*` or `ue_*` folders cause an error.
- Capacity uses a common noise power and raw channel matrices. By default,
  `N0` is derived from the first position's raw channel power and recorded
  SINR. Use `--noise-reference-position` to choose another position or
  `--noise-power` to provide `N0` explicitly.

## Run

```bash
python gui/analysis/main.py \
  --dataset-dir /path/to/experiment \
  --direction ul \
  --output-dir gui/analysis/analysis_results
```

Run the checked-in three-round mock fixture:

```bash
python gui/analysis/main.py \
  --dataset-dir gui/analysis/test_data/ul_3rounds \
  --direction ul \
  --snr 20 \
  --output-dir gui/analysis/analysis_results
```

Use `--no-plots` to skip figure generation.

## Outputs

The output directory contains second-level and position-level CSVs, validation
files, correlation/regression tables, stream-selection results,
split throughput/MCS-SE metric figures, other figures, and `final_report.md`.

Position-level results also include `position_covariance_capacity`, computed
from the mean covariance of all second-level channel snapshots at each
subcarrier.

Correlations are reported separately for throughput and MCS scheduled SE.
`mcs_scheduled_se` is calculated per row as
`TBS / (NPRB * 12 * OFDM_symbols)` and averaged within each position.

Capacity formulas, field definitions, plotting conventions, and all CLI
options are documented in
`doc/local-edit/csi_throughput_prediction.md`.
