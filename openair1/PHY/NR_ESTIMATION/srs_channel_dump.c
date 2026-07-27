/*
 * SPDX-License-Identifier: LicenseRef-CSSL-1.0
 *
 * SRS channel recording - dump interpolated channel estimates to binary file
 * for later replay in RFSim.
 *
 * Two modes (controlled by max_slots parameter):
 *   max_slots == 1  : single-slot mode, file rewritten each time (always 1 latest slot)
 *   max_slots >= 2  : burst mode, record up to max_slots slots then stop
 */

#include "PHY/defs_common.h"
#include "common/platform_types.h"
#include "PHY/NR_ESTIMATION/nr_ul_estimation.h"

#include <stdio.h>
#include <string.h>

static FILE *srs_dump_fp = NULL;
static int srs_dump_slot_count = 0;
static int srs_dump_max = 0;     /* snapshot of max_slots from first call */
static int srs_dump_active = 0;  /* burst mode active (0 = pending init) */

/* Write header + a single slot to fp (used by both modes) */
static void write_header_and_slot(FILE *fp,
                                   const c16_t *h_flat,
                                   int nrx, int ntx, int fft_size,
                                   int n_symb, int n_subcarriers,
                                   int subcarrier_offset,
                                   uint32_t slot_number,
                                   int n_rb, int subcarrier_spacing,
                                   uint32_t slot_count_val)
{
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
            .num_slots_recorded = slot_count_val,
            .n_subcarriers = (uint16_t)n_subcarriers,
            .subcarrier_offset = (uint16_t)subcarrier_offset,
            .n_srs_symbols = (uint8_t)n_symb };
  fwrite(&hdr, sizeof(hdr), 1, fp);

  fwrite(&slot_number, sizeof(slot_number), 1, fp);

  for (int ra = 0; ra < nrx; ra++)
    for (int ta = 0; ta < ntx; ta++) {
      const c16_t *h_ptr = h_flat + (size_t)(ra * ntx + ta) * (size_t)(fft_size * n_symb);
      fwrite(h_ptr, sizeof(c16_t), n_subcarriers, fp);
    }
}

void dump_srs_channel(const c16_t *h_flat,
                       int nrx, int ntx, int fft_size,
                       int n_symb, int n_subcarriers, int subcarrier_offset,
                       uint32_t slot_number,
                       int n_rb, int subcarrier_spacing,
                       int max_slots)
{
  /* --- Single-slot mode: rewrite file every time, always 1 slot --- */
  if (max_slots == 1) {
    FILE *fp = fopen("/tmp/srs_channel.bin", "wb");
    if (!fp) return;
    write_header_and_slot(fp, h_flat, nrx, ntx, fft_size,
                          n_symb, n_subcarriers, subcarrier_offset,
                          slot_number, n_rb, subcarrier_spacing, 1);
    fclose(fp);
    return;
  }

  /* --- Burst mode: record max_slots slots then stop --- */

  /* First call: capture max_slots and reset counter */
  if (srs_dump_max == 0) {
    srs_dump_max = max_slots;
    srs_dump_slot_count = 0;
    srs_dump_active = 1;
  }

  if (!srs_dump_active) return;
  if (srs_dump_slot_count >= srs_dump_max) {
    if (srs_dump_fp) { fclose(srs_dump_fp); srs_dump_fp = NULL; }
    srs_dump_active = 0;
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

  /* Append slot */
  fwrite(&slot_number, sizeof(slot_number), 1, srs_dump_fp);
  for (int ra = 0; ra < nrx; ra++)
    for (int ta = 0; ta < ntx; ta++) {
      const c16_t *h_ptr = h_flat + (size_t)(ra * ntx + ta) * (size_t)(fft_size * n_symb);
      fwrite(h_ptr, sizeof(c16_t), n_subcarriers, srs_dump_fp);
    }

  srs_dump_slot_count++;

  long saved_pos = ftell(srs_dump_fp);
  fseek(srs_dump_fp, 16, SEEK_SET);
  uint32_t sc = (uint32_t)srs_dump_slot_count;
  fwrite(&sc, sizeof(sc), 1, srs_dump_fp);
  fseek(srs_dump_fp, saved_pos, SEEK_SET);
}
