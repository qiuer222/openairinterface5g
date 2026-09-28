# SRS / DMRS / CSI-RS Debug Capture

This branch adds a small event-driven capture path for investigating SRS
frequency-domain anomalies.

## Enable

gNB:

```bash
sudo env OAI_DEBUG_CAPTURE_RUN_ID=test1 \
  OAI_DEBUG_CAPTURE_START_SFN=500 \
  OAI_DEBUG_CAPTURE_START_SLOT=0 \
  ./nr-softmodem ... --record-srs-ch 5
```

UE:

```bash
sudo env OAI_DEBUG_CAPTURE_RUN_ID=test1 \
  OAI_DEBUG_CAPTURE_START_SFN=500 \
  OAI_DEBUG_CAPTURE_START_SLOT=0 \
  ./nr-uesoftmodem ... --record-csi-ch 5
```

Set the same `OAI_DEBUG_CAPTURE_RUN_ID` on both sides. Set
`OAI_DEBUG_CAPTURE_DIR` to override the default output root
`/tmp/oai_debug_capture`. If the start SFN/slot variables are omitted or set
to a negative value, capture starts at the first valid event.

The normal OAI behavior is unchanged when the record options are zero. Passing
`1` keeps the old single-slot dump behavior. Passing `N >= 2` enables the new
event capture and records N SRS events.

## Recorded data

The gNB records:

- SRS received frequency-domain signal and noise subcarriers;
- SRS reference sequence;
- SRS LS, interpolated and time-domain channel estimates;
- metadata including frame, slot, RNTI, FFT, SRS bandwidth and SNR.

During the same SRS window, the gNB records PUSCH data for the same RNTI:

- DMRS reference sequence;
- received PUSCH/DMRS frequency-domain signal;
- channel estimates at DMRS positions;
- interpolated channel estimates;
- equalized DMRS/PUSCH observations.

The UE records CSI-RS resources received between its first and N-th SRS
transmissions:

- received CSI-RS signal;
- CSI-RS reference sequence;
- CSI-RS LS and interpolated channel estimates;
- CSI-RS resource metadata.

No raw time-domain IQ is recorded by this implementation.

## Output

Each process writes:

```text
/tmp/oai_debug_capture/<run_id>/gnb/capture.bin
/tmp/oai_debug_capture/<run_id>/gnb/manifest.json
/tmp/oai_debug_capture/<run_id>/ue/capture.bin
/tmp/oai_debug_capture/<run_id>/ue/manifest.json
```

`capture.bin` is a sequence of little-endian records. Every record starts with
the packed `debug_capture_record_header_t` from
`openair1/PHY/NR_ESTIMATION/debug_capture.h`, followed by interleaved `int16`
I/Q samples. The role and record-kind enums are defined in the same header.

Copy the two role directories under one run directory before analysis.

## Analyze

```bash
.venv/bin/python gui/analyze_srs_capture.py \
  /tmp/merged_capture/test1 \
  --output-dir /tmp/srs_capture_analysis
```

The analyzer writes:

```text
summary.json
events.csv
figures/*_channel.png
figures/*_head.png
figures/*_srs_time.png
figures/*_dmrs_constellation.png
```

`*_head.png` zooms the leading 10 percent of each captured frequency-domain
vector by default. This is an additional view; the complete vectors remain in
the full-channel plot and in `capture.bin`. Use `--focus-fraction` to change
the zoom ratio.

Run the parser tests with:

```bash
.venv/bin/python -m unittest gui.tests.test_analyze_srs_capture -v
```
