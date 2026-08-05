# OAI GUI Output Analysis

Run from the repository root:

```bash
python gui/analysis/analyze_correlation.py \
  --csv gui/record/gui_log_20260802_223124.csv \
  --csi gui/record/gui_log_20260802_223124 \
  --snr 10 \
  --output-dir analysis_results
```

The CSI directory is usually the CSV filename without the `.csv` extension.
If only `--csv` is given, the script derives `--csi` from it automatically.
If only `--csi` is given, the script derives the CSV path by appending `.csv`.
Without either option, the script auto-selects the latest complete
`gui/record/gui_log_*.csv` and its matching CSI directory.

Required dependencies are listed in `gui/analysis/requirements.txt`.

## channel_analysis.py

The current task pipeline is implemented in `channel_analysis.py`:

```bash
.venv/bin/python gui/analysis/channel_analysis.py \
  --csv gui/record/gui_log_20260804_212102.csv \
  --output-dir gui/channel_analysis_results
```

If `--csi` is omitted, it is derived from the CSV stem (same-name CSI folder).
The default output directory is `gui/channel_analysis_results`.

Outputs:

- `cleaned_measurement.csv`
- `cleaning_log.txt`
- `csi_matching_log.txt`
- `channel_analysis.csv`
- `validation_report.csv`
- `validation_summary.txt`
- `analysis_summary.txt`
- `analysis_summary.md`
- `figures/channel_analysis_<csv_stem>.png`, where `<csv_stem>` is the source CSV filename without `.csv`
- `figures/channel_analysis_position_means_<csv_stem>.png`, with one point per position

The summary reports per-position metric means and correlations between each
position's top-50% throughput mean and the position's mean channel/radio
metric.
