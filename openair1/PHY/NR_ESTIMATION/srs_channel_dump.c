/*
 * SPDX-License-Identifier: LicenseRef-CSSL-1.0
 *
 * SRS channel recording - dump interpolated channel estimates to binary file
 * for later replay in RFSim.
 */

#include "PHY/defs_common.h"
#include "common/platform_types.h"
#include "PHY/NR_ESTIMATION/nr_ul_estimation.h"

#include <stdio.h>
#include <string.h>

static FILE *srs_dump_fp = NULL;
static int srs_dump_slot_count = 0;

void dump_srs_channel(const c16_t *h_flat,
                       int nrx, int ntx, int fft_size,
                       int n_symb, int n_subcarriers, int subcarrier_offset,
                       uint32_t slot_number,
                       int n_rb, int subcarrier_spacing) {

  const int MAX_RECORD_SLOTS = 20;

  if (srs_dump_slot_count >= MAX_RECORD_SLOTS) {
    if (srs_dump_fp) { fclose(srs_dump_fp); srs_dump_fp = NULL; }
    return;
  }

  if (srs_dump_fp == NULL) {
    srs_dump_fp = fopen("/tmp/srs_channel.bin", "wb");
    if (!srs_dump_fp) return;

    struct __attribute__((packed)) {
      uint32_t magic;
      uint16_t version;
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
    } hdr = { .magic = 0x48534D52, .version = 1,
              .num_rx_ant = (uint8_t)nrx, .num_tx_ant = (uint8_t)ntx,
              .fft_size = (uint16_t)fft_size, .n_rb = (uint16_t)n_rb,
              .subcarrier_spacing = (uint32_t)subcarrier_spacing,
              .num_slots_recorded = 0,
              .n_subcarriers = (uint16_t)n_subcarriers,
              .subcarrier_offset = (uint16_t)subcarrier_offset,
              .n_srs_symbols = (uint8_t)n_symb };
    fwrite(&hdr, sizeof(hdr), 1, srs_dump_fp);
  }

  if (!srs_dump_fp) return;

  // Append per-slot header and H data (fp is at end of file)
  fwrite(&slot_number, sizeof(slot_number), 1, srs_dump_fp);

  for (int ra = 0; ra < nrx; ra++)
    for (int ta = 0; ta < ntx; ta++) {
      const c16_t *h_ptr = h_flat + (size_t)(ra * ntx + ta) * (size_t)(fft_size * n_symb);
      fwrite(h_ptr, sizeof(c16_t), n_subcarriers, srs_dump_fp);
    }

  srs_dump_slot_count++;

  // Update slot count in header (at offset 16: magic+ver+rx+tx+fft_size+n_rb+scs = 16)
  long saved_pos = ftell(srs_dump_fp);
  fseek(srs_dump_fp, 16, SEEK_SET);
  uint32_t sc = (uint32_t)srs_dump_slot_count;
  fwrite(&sc, sizeof(sc), 1, srs_dump_fp);
  fseek(srs_dump_fp, saved_pos, SEEK_SET); // back to end for next append
}
