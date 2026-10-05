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

## Run

```bash
python gui/analysis/main.py \
  --dataset-dir /path/to/experiment \
  --direction ul \
  --output-dir gui/analysis/analysis_results
```

Generate the three-round mock fixture:

```bash
python gui/analysis/tests/generate_mock_ul_3rounds.py
```

Then run it:

```bash
python gui/analysis/main.py \
  --dataset-dir /tmp/oai_ul_3rounds \
  --direction ul \
  --snr 20 \
  --output-dir gui/analysis/analysis_results
```

Use `--no-plots` to skip figure generation.

## Outputs

The output directory contains second-level and position-level CSVs, validation
files, correlation/regression tables, stream-selection results,
`figures/position_metrics.png`, other figures, and `final_report.md`.

Capacity formulas, field definitions, plotting conventions, and all CLI
options are documented in
`doc/local-edit/csi_throughput_prediction.md`.
