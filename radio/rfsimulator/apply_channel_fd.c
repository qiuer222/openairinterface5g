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
#include "PHY/impl_defs_top.h"
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
    dftfunc_t *dft_ptr  = (dftfunc_t *) dlsym(RTLD_DEFAULT, "dft");
    idftfunc_t *idft_ptr = (idftfunc_t *) dlsym(RTLD_DEFAULT, "idft");
    if (!dft_ptr || !idft_ptr || !*dft_ptr || !*idft_ptr) return -1;
    p_dft  = *dft_ptr;
    p_idft = *idft_ptr;
  }
  return 0;
}

// ---- File format (matches recording side) ----
typedef struct {
  uint32_t magic;              // 0x48534D52
  uint16_t version;            // 1: raw H, 2: normalized H + per-slot gains
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
  int    tried_load;  /* avoid retrying load on every RX antenna/buffer */
  int    num_rx;
  int    num_tx;
  int    fft_size;
  int    n_sc;
  int    sc_off;
  int    h_amp_bits;  /* fixed-point scale of recorded H: unit gain == 2^h_amp_bits */
  int    num_slots;
  int    current_slot;
  c16_t *h_data;   // [num_slots][rx][tx][subcarrier]
  float *slot_gains;  // [num_slots]; NULL for version-1 files (implicit gain 1.0)
} srs_replay = {0};

/*
 * Recorded H is produced by OAI SRS/CSI-RS LS estimation in the TX reference
 * amplitude scale (AMP).  In this build AMP = 1 << 9 = 512, so an identity
 * channel is stored as H ~= 512, not 32768.  c16mulShift(..., h_amp_bits)
 * therefore restores the true channel gain.  RFSIM_H_SCALE_BITS can override
 * the value when files from another AMP build are replayed.
 */
#ifndef AMP_SHIFT
#define RFSIM_H_AMP_BITS_DEFAULT 9
#else
#define RFSIM_H_AMP_BITS_DEFAULT AMP_SHIFT
#endif

// ---- Load function ----
int rfsim_load_srs_file(const char *path) {
  FILE *fp = fopen(path, "rb");
  if (!fp) return -1;

  srs_file_header_t hdr;
  if (fread(&hdr, sizeof(hdr), 1, fp) != 1) { fclose(fp); return -1; }
  if (hdr.magic != 0x48534D52 || (hdr.version != 1 && hdr.version != 2)) {
    fclose(fp);
    return -1;
  }

  free(srs_replay.h_data);
  free(srs_replay.slot_gains);
  srs_replay.h_data = NULL;
  srs_replay.slot_gains = NULL;

  srs_replay.num_rx    = hdr.num_rx_ant;
  srs_replay.num_tx    = hdr.num_tx_ant;
  srs_replay.fft_size  = hdr.fft_size;
  srs_replay.n_sc      = hdr.n_subcarriers;
  srs_replay.sc_off    = hdr.subcarrier_offset;

  const char *scale_env = getenv("RFSIM_H_SCALE_BITS");
  int scale_bits = 0;
  if (hdr.version == 2 && hdr.reserved[0] != 0)
    scale_bits = hdr.reserved[0];
  else if (scale_env)
    scale_bits = atoi(scale_env);
  else
    scale_bits = RFSIM_H_AMP_BITS_DEFAULT;
  if (scale_bits < 1)
    scale_bits = 1;
  if (scale_bits > 15)
    scale_bits = 15;
  srs_replay.h_amp_bits = scale_bits;

  // Limit loaded slots to avoid excessive memory use with continuous recording
  static const int MAX_REPLAY_SLOTS = 100;
  int total_slots = hdr.num_slots_recorded;
  int load_count = (total_slots > MAX_REPLAY_SLOTS) ? MAX_REPLAY_SLOTS : total_slots;
  int skip_count = total_slots - load_count;

  srs_replay.num_slots = load_count;
  srs_replay.current_slot = 0;

  int h_per_slot = hdr.num_rx_ant * hdr.num_tx_ant * hdr.n_subcarriers;
  int slot_data_bytes = h_per_slot * (int)sizeof(c16_t);
  int slot_total_bytes = (int)sizeof(uint32_t) + slot_data_bytes;

  // Skip old slots if the file has more than MAX_REPLAY_SLOTS
  if (skip_count > 0) {
    fseek(fp, skip_count * slot_total_bytes, SEEK_CUR);
  }

  if (load_count <= 0) {
    LOG_W(HW, "[rfsim] Channel file %s has no recorded slots\n", path);
    fclose(fp);
    return -1;
  }

  // Version-2 files carry a per-slot linear gain array right after the header.
  if (hdr.version == 2) {
    if (skip_count > 0)
      fseek(fp, skip_count * (long)sizeof(float), SEEK_CUR);
    srs_replay.slot_gains = calloc(load_count, sizeof(float));
    if (!srs_replay.slot_gains ||
        fread(srs_replay.slot_gains, sizeof(float), load_count, fp) != (size_t)load_count) {
      free(srs_replay.slot_gains);
      srs_replay.slot_gains = NULL;
      fclose(fp);
      return -1;
    }
  }

  srs_replay.h_data = calloc(load_count * h_per_slot, sizeof(c16_t));
  if (!srs_replay.h_data) {
    free(srs_replay.slot_gains);
    srs_replay.slot_gains = NULL;
    fclose(fp);
    return -1;
  }

  for (int s = 0; s < load_count; s++) {
    // Skip per-slot header (uint32_t slot_number)
    uint32_t slot_hdr;
    if (fread(&slot_hdr, sizeof(slot_hdr), 1, fp) != 1) { fclose(fp); return -1; }
    c16_t *dst = srs_replay.h_data + (size_t)s * h_per_slot;
    size_t sz = (size_t)slot_data_bytes;
    if (fread(dst, 1, sz, fp) != sz) { fclose(fp); return -1; }
  }
  fclose(fp);

  if (skip_count > 0) {
    LOG_I(HW, "[rfsim] Skipped %d old slots, loaded last %d of %d recorded\n", skip_count, load_count, total_slots);
  }

  // Resolve DFT function pointers first: dft/idft are function-pointer variables,
  // so dlsym gives their address and the actual pointer must be dereferenced.
  if (resolve_dft() != 0) {
    fprintf(stderr, "[rfsim] ERROR: cannot resolve DFT function pointers\n");
    free(srs_replay.h_data);
    free(srs_replay.slot_gains);
    srs_replay.h_data = NULL;
    srs_replay.slot_gains = NULL;
    return -1;
  }

  srs_replay.loaded = 1;
  srs_replay_loaded = 1;

  LOG_I(HW, "[rfsim] Loaded SRS channel file: %s (%d slots, %dx%d, fft=%d, h_scale_bits=%d, gains=%d)\n",
        path, srs_replay.num_slots, srs_replay.num_rx, srs_replay.num_tx,
        srs_replay.fft_size, srs_replay.h_amp_bits,
        srs_replay.slot_gains ? srs_replay.num_slots : 0);
  return 0;
}

// ---- Apply function: replaces rxAddInput() for recorded channel ----
// Called per RX antenna (same signature as rxAddInput).
void rxAddInput_srsfile(c16_t **input_sig, cf_t *after_channel_sig,
                         int rxAnt, channel_desc_t *channelDesc, int nbSamples) {

  // First call: if CHANNEL_FILE env var is set, try to load it.
  // If file loads → SRS replay. If not set or load fails → fall back to rxAddInput.
  if (!srs_replay.loaded && !srs_replay.tried_load) {
    srs_replay.tried_load = 1;
    const char *srs_path = getenv("CHANNEL_FILE");
    if (srs_path) {
      LOG_I(HW, "[rfsim] Loading channel file: %s\n", srs_path);
      if (rfsim_load_srs_file(srs_path) != 0) {
        LOG_W(HW, "Failed to load channel file: %s\n", srs_path);
      }
    } else {
      LOG_I(HW, "[rfsim] CHANNEL_FILE not set, using normal channel model\n");
    }
  }

  if (!srs_replay.loaded) {
    rxAddInput(input_sig, after_channel_sig, rxAnt, channelDesc, nbSamples);
    return;
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

  if (srs_replay.num_slots <= 0) {
    memset(after_channel_sig, 0, nbSamples * sizeof(cf_t));
    return;
  }

  // Cycle through recorded slots
  const int slot_idx = srs_replay.current_slot % srs_replay.num_slots;
  const int h_per_slot = nrx * ntx * n_sc;
  const c16_t *h_slot = srs_replay.h_data + (size_t)slot_idx * h_per_slot;
  float slot_gain = 1.0f;
  if (srs_replay.slot_gains) {
    slot_gain = srs_replay.slot_gains[slot_idx];
    if (!isfinite(slot_gain) || slot_gain <= 0.0f)
      slot_gain = 1.0f;
  }

  // Work buffers (static, allocated once at max fft_size).
  // OAI's dft/idft wrappers require 32-byte-aligned output buffers, so use
  // posix_memalign() instead of calloc() (which only guarantees 16 bytes).
  static c16_t *freq_buf  = NULL;
  static c16_t *work_buf  = NULL;
  static cf_t  *time_buf  = NULL;  // float accumulator to avoid int16 overflow
  static int    alloc_fft = 0;

  if (alloc_fft != fft_size) {
    free(freq_buf); free(work_buf); free(time_buf);
    freq_buf = NULL;
    work_buf = NULL;
    time_buf = NULL;

    const size_t c16_bytes = (size_t)fft_size * sizeof(c16_t);
    const size_t cf_bytes  = (size_t)fft_size * sizeof(cf_t);
    int aligned_ok = 0;
    aligned_ok |= posix_memalign((void **)&freq_buf, 32, c16_bytes);
    aligned_ok |= posix_memalign((void **)&work_buf, 32, c16_bytes);
    aligned_ok |= posix_memalign((void **)&time_buf, 32, cf_bytes);
    if (aligned_ok != 0) {
      free(freq_buf);
      free(work_buf);
      free(time_buf);
      freq_buf = NULL;
      work_buf = NULL;
      time_buf = NULL;
      alloc_fft = 0;
      memset(after_channel_sig, 0, nbSamples * sizeof(cf_t));
      return;
    }
    memset(freq_buf, 0, c16_bytes);
    memset(work_buf, 0, c16_bytes);
    memset(time_buf, 0, cf_bytes);
    alloc_fft = fft_size;
  }

  memset(after_channel_sig, 0, nbSamples * sizeof(cf_t));

  // Number of sample blocks to process
  const int block_size = fft_size;
  const int n_blocks = (nbSamples + block_size - 1) / block_size;

  for (int blk = 0; blk < n_blocks; blk++) {
    const int blk_start = blk * block_size;
    const int blk_len = min(block_size, nbSamples - blk_start);
    const int in_offset = blk_start;  // input_sig reads from same index

    // Sum over TX antennas (float accumulator to avoid int16 overflow)
    memset(time_buf, 0, (size_t)fft_size * sizeof(cf_t));

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
        work_buf[idx] = c16mulShift(work_buf[idx], h_ra_ta[k], srs_replay.h_amp_bits);
      }

      // IDFT back to time domain, accumulate
      p_idft(ids, (int16_t*)freq_buf, (int16_t*)work_buf, 1);
      for (int n = 0; n < blk_len; n++) {
        time_buf[n].r += freq_buf[n].r;
        time_buf[n].i += freq_buf[n].i;
      }
    }

    // Copy block to output. OAI dft/idft already use matched 1/sqrt(N)
    // normalization, so no extra division by fft_size is allowed here.
    // Version-2 files store normalized H; slot_gain restores the recorded
    // absolute channel power (version-1 files use slot_gain = 1.0).
    for (int n = 0; n < blk_len; n++) {
      after_channel_sig[blk_start + n].r += time_buf[n].r * slot_gain;
      after_channel_sig[blk_start + n].i += time_buf[n].i * slot_gain;
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
