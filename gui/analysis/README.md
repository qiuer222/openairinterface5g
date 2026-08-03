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
