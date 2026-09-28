/*
 * SPDX-License-Identifier: LicenseRef-CSSL-1.0
 *
 * Small, event-driven capture helper for SRS/DMRS/CSI-RS debugging.
 */

#ifndef __NR_DEBUG_CAPTURE_H__
#define __NR_DEBUG_CAPTURE_H__

#include <stdbool.h>
#include <stdint.h>

#include "common/platform_types.h"

#define DEBUG_CAPTURE_MAGIC 0x31435044U /* "DPC1" */
#define DEBUG_CAPTURE_VERSION 1
#define DEBUG_CAPTURE_FLAG_DMRS_SYMBOL 0x01U

typedef enum {
  DEBUG_CAPTURE_ROLE_GNB = 0,
  DEBUG_CAPTURE_ROLE_UE = 1,
} debug_capture_role_t;

typedef enum {
  DEBUG_CAPTURE_SRS_RX = 1,
  DEBUG_CAPTURE_SRS_REF,
  DEBUG_CAPTURE_SRS_NOISE,
  DEBUG_CAPTURE_SRS_LS,
  DEBUG_CAPTURE_SRS_INTERP,
  DEBUG_CAPTURE_SRS_TIME,
  DEBUG_CAPTURE_DMRS_REF,
  DEBUG_CAPTURE_DMRS_RX,
  DEBUG_CAPTURE_DMRS_LS,
  DEBUG_CAPTURE_DMRS_INTERP,
  DEBUG_CAPTURE_DMRS_EQUALIZED,
  DEBUG_CAPTURE_CSIRS_RX,
  DEBUG_CAPTURE_CSIRS_REF,
  DEBUG_CAPTURE_CSIRS_LS,
  DEBUG_CAPTURE_CSIRS_INTERP,
} debug_capture_kind_t;

typedef struct __attribute__((packed)) {
  uint32_t magic;
  uint16_t version;
  uint16_t kind;
  uint32_t payload_bytes;
  uint64_t sequence;
  uint32_t event_id;
  uint32_t frame;
  uint32_t slot;
  uint64_t timestamp_ns;
  uint16_t rnti;
  uint8_t rx;
  uint8_t port;
  uint8_t layer;
  uint8_t symbol;
  uint16_t rows;
  uint16_t cols;
  uint16_t fft_size;
  uint16_t n_rb;
  uint16_t start_rb;
  uint16_t bwp_start;
  uint32_t subcarrier_spacing;
  int16_t snr_db_x10;
  uint8_t flags;
  uint8_t reserved[9];
} debug_capture_record_header_t;

typedef struct {
  uint32_t frame;
  uint32_t slot;
  uint16_t rnti;
  uint8_t rx;
  uint8_t port;
  uint8_t layer;
  uint8_t symbol;
  uint16_t rows;
  uint16_t cols;
  uint16_t fft_size;
  uint16_t n_rb;
  uint16_t start_rb;
  uint16_t bwp_start;
  uint32_t subcarrier_spacing;
  int16_t snr_db_x10;
  uint8_t flags;
} debug_capture_meta_t;

bool debug_capture_configure(debug_capture_role_t role, int max_srs_events, int start_sfn, int start_slot);
bool debug_capture_is_active(void);

/* Returns true only when this SRS event is inside the configured capture window. */
bool debug_capture_srs_event(uint32_t frame, uint32_t slot, uint16_t rnti);
void debug_capture_end_srs_event(void);

/* PUSCH DMRS data is accepted only after the first SRS and for the same RNTI. */
bool debug_capture_dmrs_active(uint32_t frame, uint32_t slot, uint16_t rnti);

void debug_capture_write(debug_capture_kind_t kind,
                         uint32_t event_id,
                         const debug_capture_meta_t *meta,
                         const c16_t *data);

uint32_t debug_capture_current_event_id(void);
void debug_capture_close(void);

#endif /* __NR_DEBUG_CAPTURE_H__ */
