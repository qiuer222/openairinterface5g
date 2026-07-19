/*
 * SPDX-License-Identifier: LicenseRef-CSSL-1.0
 *
 * Frequency-domain channel replay for RFSim.
 * Replaces the time-domain TDL convolution with FFT-based channel
 * from recorded SRS channel estimates.
 */

#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <complex.h>

#include "PHY/TOOLS/tools_defs.h"
#include "openair1/SIMULATION/TOOLS/sim.h"
#include "common/platform_types.h"
#include "rfsimulator.h"

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

// ---- DFT function pointers (resolved at runtime from main OAI binary) ----
static dftfunc_t  p_dft  = NULL;
static idftfunc_t p_idft = NULL;

static int resolve_dft(void) {
  if (!p_dft) {
    p_dft  = (dftfunc_t) dlsym(RTLD_DEFAULT, "dft");
    p_idft = (idftfunc_t) dlsym(RTLD_DEFAULT, "idft");
    if (!p_dft || !p_idft) return -1;
  }
  return 0;
}

// ---- File format (matches recording side) ----
typedef struct {
  uint32_t magic;              // 0x48534D52
  uint16_t version;            // 1
  uint8_t  num_rx_ant;
  uint8_t  num_tx_ant;
  uint16_t fft_size;
  uint16_t n_rb;
  uint32_t subcarrier_spacing;
  uint32_t num_slots_recorded;
  uint16_t n_subcarriers;
  uint16_t subcarrier_offset;
  uint8_t  n_srs_symbols;
  uint8_t  reserved[23];
} __attribute__((packed)) srs_file_header_t;

// ---- Global flag for simulator.cpp ----
int srs_replay_loaded = 0;

// ---- Replay state ----
static struct {
  int    loaded;
  int    num_rx;
  int    num_tx;
  int    fft_size;
  int    n_sc;
  int    sc_off;
  int    num_slots;
  int    current_slot;
  c16_t *h_data;   // [num_slots][rx][tx][subcarrier]
} srs_replay = {0};

// ---- Load function ----
int rfsim_load_srs_file(const char *path) {
  FILE *fp = fopen(path, "rb");
  if (!fp) return -1;

  srs_file_header_t hdr;
  if (fread(&hdr, sizeof(hdr), 1, fp) != 1) { fclose(fp); return -1; }
  if (hdr.magic != 0x48534D52 || hdr.version != 1) { fclose(fp); return -1; }

  srs_replay.num_rx    = hdr.num_rx_ant;
  srs_replay.num_tx    = hdr.num_tx_ant;
  srs_replay.fft_size  = hdr.fft_size;
  srs_replay.n_sc      = hdr.n_subcarriers;
  srs_replay.sc_off    = hdr.subcarrier_offset;
  srs_replay.num_slots = hdr.num_slots_recorded;
  srs_replay.current_slot = 0;

  int h_per_slot = hdr.num_rx_ant * hdr.num_tx_ant * hdr.n_subcarriers;
  srs_replay.h_data = calloc(hdr.num_slots_recorded * h_per_slot, sizeof(c16_t));
  if (!srs_replay.h_data) { fclose(fp); return -1; }

  for (int s = 0; s < hdr.num_slots_recorded; s++) {
    // Skip per-slot header (uint32_t slot_number)
    uint32_t slot_hdr;
    if (fread(&slot_hdr, sizeof(slot_hdr), 1, fp) != 1) { fclose(fp); return -1; }
    c16_t *dst = srs_replay.h_data + (size_t)s * h_per_slot;
    size_t sz = (size_t)h_per_slot * sizeof(c16_t);
    if (fread(dst, 1, sz, fp) != sz) { fclose(fp); return -1; }
  }
  fclose(fp);

  srs_replay.loaded = 1;
  srs_replay_loaded = 1;

  // Resolve DFT function pointers
  if (resolve_dft() != 0) {
    fprintf(stderr, "[rfsim] ERROR: cannot resolve DFT function pointers\n");
    return -1;
  }

  LOG_I(HW, "[rfsim] Loaded SRS channel file: %s (%d slots, %dx%d, fft=%d)\n",
        path, srs_replay.num_slots, srs_replay.num_rx, srs_replay.num_tx, srs_replay.fft_size);
  return 0;
}

// ---- Apply function: replaces rxAddInput() for recorded channel ----
// Called per RX antenna (same signature as rxAddInput).
void rxAddInput_srsfile(c16_t **input_sig, cf_t *after_channel_sig,
                         int rxAnt, channel_desc_t *channelDesc, int nbSamples) {

  // First call: if SRS_CHANNEL_FILE env var is set, try to load it.
  // If file loads → SRS replay. If not set or load fails → fall back to rxAddInput.
  if (!srs_replay.loaded) {
    const char *srs_path = getenv("SRS_CHANNEL_FILE");
    if (srs_path) {
      if (rfsim_load_srs_file(srs_path) != 0) {
        LOG_W(HW, "Failed to load SRS channel file: %s\n", srs_path);
      }
    }
    if (!srs_replay.loaded) {
      rxAddInput(input_sig, after_channel_sig, rxAnt, channelDesc, nbSamples);
      return;
    }
  }

  const int fft_size = srs_replay.fft_size;
  const int nrx  = srs_replay.num_rx;
  const int ntx  = srs_replay.num_tx;
  const int n_sc = srs_replay.n_sc;
  const int sc_off = srs_replay.sc_off;

  // Bounds check
  if (rxAnt >= nrx) {
    memset(after_channel_sig, 0, nbSamples * sizeof(cf_t));
    return;
  }

  // Get DFT size index
  dft_size_idx_t  ds  = get_dft(fft_size);
  idft_size_idx_t ids = get_idft(fft_size);

  if (ds == DFT_SIZE_IDXTABLESIZE || ids == IDFT_SIZE_IDXTABLESIZE) {
    memset(after_channel_sig, 0, nbSamples * sizeof(cf_t));
    return;
  }

  // Cycle through recorded slots
  const int slot_idx = srs_replay.current_slot % srs_replay.num_slots;
  const int h_per_slot = nrx * ntx * n_sc;
  const c16_t *h_slot = srs_replay.h_data + (size_t)slot_idx * h_per_slot;

  // Work buffers (static, allocated once at max fft_size)
  static c16_t *freq_buf  = NULL;
  static c16_t *work_buf  = NULL;
  static c16_t *time_buf  = NULL;
  static int    alloc_fft = 0;

  if (alloc_fft != fft_size) {
    free(freq_buf); free(work_buf); free(time_buf);
    freq_buf = calloc(fft_size, sizeof(c16_t));
    work_buf = calloc(fft_size, sizeof(c16_t));
    time_buf = calloc(fft_size, sizeof(c16_t));
    alloc_fft = fft_size;
    if (!freq_buf || !work_buf || !time_buf) {
      memset(after_channel_sig, 0, nbSamples * sizeof(cf_t));
      return;
    }
  }

  memset(after_channel_sig, 0, nbSamples * sizeof(cf_t));

  // Number of sample blocks to process
  const int block_size = fft_size;
  const int n_blocks = (nbSamples + block_size - 1) / block_size;

  for (int blk = 0; blk < n_blocks; blk++) {
    const int blk_start = blk * block_size;
    const int blk_len = min(block_size, nbSamples - blk_start);
    const int in_offset = blk_start;  // input_sig reads from same index

    // Sum over TX antennas
    memset(time_buf, 0, fft_size * sizeof(c16_t));

    for (int ta = 0; ta < ntx; ta++) {
      // DFT input_sig[ta][in_offset .. in_offset+blk_len-1] → freq_buf
      // Pad the rest with zeros (safe: input_sig has enough space allocated)
      memset(freq_buf, 0, fft_size * sizeof(c16_t));
      memcpy(freq_buf, &input_sig[ta][in_offset], blk_len * sizeof(c16_t));

      p_dft(ds, (int16_t*)work_buf, (int16_t*)freq_buf, 1);

      // Multiply by recorded H[slot_idx][rxAnt][ta][k]
      const c16_t *h_ra_ta = h_slot + ((size_t)rxAnt * ntx + ta) * n_sc;
      for (int k = 0; k < n_sc; k++) {
        int idx = (sc_off + k) % fft_size;
        work_buf[idx] = c16mulShift(work_buf[idx], h_ra_ta[k], 15);
      }

      // IDFT back to time domain, accumulate
      p_idft(ids, (int16_t*)freq_buf, (int16_t*)work_buf, 1);
      for (int n = 0; n < blk_len; n++) {
        time_buf[n].r += freq_buf[n].r;
        time_buf[n].i += freq_buf[n].i;
      }
    }

    // Copy block to output with IFFT normalization (÷fft_size)
    const float norm = 1.0f / fft_size;
    for (int n = 0; n < blk_len; n++) {
      after_channel_sig[blk_start + n].r += time_buf[n].r * norm;
      after_channel_sig[blk_start + n].i += time_buf[n].i * norm;
    }
  }

  // Apply path loss and AWGN (same as original rxAddInput)
  const double pathLossLinear = pow(10, channelDesc->path_loss_dB / 20.0);
  const double noise_per_sample = pow(10, channelDesc->noise_power_dB / 10.0) * 256;
  for (int n = 0; n < nbSamples; n++) {
    after_channel_sig[n].r *= pathLossLinear;
    after_channel_sig[n].i *= pathLossLinear;
    after_channel_sig[n].r += noise_per_sample * gaussZiggurat(0.0, 1.0);
    after_channel_sig[n].i += noise_per_sample * gaussZiggurat(0.0, 1.0);
  }

  // Advance slot counter (increment on the last RX antenna's call)
  // The caller calls rxAddInput per RX antenna. We increment when rxAnt == nrx-1.
  // This is a heuristic: the slot boundary is determined by the caller's loop.
  if (rxAnt == nrx - 1) {
    srs_replay.current_slot++;
  }
}
