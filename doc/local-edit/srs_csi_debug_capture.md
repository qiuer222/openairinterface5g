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

The debug capture prints unconditional `[DEBUG_CAPTURE]` lines to stderr:

- `initialized` when the output directory and `capture.bin` are created;
- `waiting for start` while the configured start SFN/slot has not been reached;
- `accepted event=<n>/<N>` for every captured SRS event;
- `write kind=...` for every binary record written;
- `closed events=... bytes=...` after the requested SRS count is reached.

`capture.bin` is unbuffered and is flushed after every record, so a non-empty
file should grow while the test is running. A zero-byte file means that no SRS
event was accepted yet. Check the `[DEBUG_CAPTURE]` output for either a future
start SFN/slot or an SRS estimation failure.

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
figures/*_srs_time.png
figures/*_dmrs_constellation.png
```

In `*_channel.png`, the horizontal axis is the stored FFT bin index, also
referred to as the subcarrier index. It is not a time sample index. LS records
are drawn as scatter points; interpolated records are drawn as lines. The SRS
and CSI-RS frequency-domain records are split per OFDM symbol, so every plotted
curve covers `0 .. fft_size-1`.

In `*_srs_time.png`, the horizontal axis is the oversampled time-domain channel
tap index. DMRS records are indexed within the extracted PUSCH allocation, not
on the full FFT grid.

## Inspect selected subcarriers

Use `gui/inspect_srs_capture.py` to print individual stored complex values:

```bash
.venv/bin/python gui/inspect_srs_capture.py \
  /tmp/oai_debug_capture/test1 \
  --kind srs_ls \
  --event 1 \
  --rx 0 \
  --port 0 \
  --index 0,10,20-24
```

Use `--list-records` to list matching record metadata and `--json` for
machine-readable output. For frequency-domain records, `--index` is the FFT
bin/subcarrier index stored in `capture.bin`. For `srs_time`, it is the
time-domain tap index.

Run the parser tests with:

```bash
.venv/bin/python -m unittest gui.tests.test_analyze_srs_capture -v
```
