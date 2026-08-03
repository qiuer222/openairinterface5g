Final analysis report
=====================

1. Highest Pearson correlation with measured throughput:
   none

2. Most accurate throughput predictor:
   lowest RMSE: linear / rsrp_dBm (41.165 Mbps)
   highest R2: poly / best_zf_capacity (0.000)
   Note: all four analyzed predictors are constant in this record, so RMSE is the mean-only baseline.

3. Adaptive stream selection vs fixed-stream capacity:
   mean best-stream SVD capacity = 9.205 bits/s/Hz
   mean all-stream SVD capacity = 9.205 bits/s/Hz
   best-stream selection is better on average: False

4. SVD vs ZF stream selection accuracy:
   SVD stream accuracy = 100.0%
   ZF stream accuracy = 100.0%
   Better predictor = SVD

5. Scaling findings:
   OAI records SQ15/FFT/RF-scaled CSI estimates; exact RX gain is not present in the CSV.
   The analysis uses RSRP-calibrated unit-power normalization with a fixed configurable SNR.
   See scaling_report.txt for source references and raw power consistency checks.
   Because every CSI snapshot is identical, normalization does not change the correlation outcome in this record.

6. Channel-condition performance:
   This record has one RSRP value, one channel condition number, and identical CSI matrices on every sample.
   Conditional accuracy is therefore not differentiated; stream selection was deterministic across the dataset.

Additional finding:
   Pearson(throughput, MCS) = 0.880
   Pearson(throughput, NPRB) = nan
   Pearson(throughput, SINR) = -0.067
   This indicates the throughput changes in this dataset are scheduler/MCS driven rather than CSI driven.

