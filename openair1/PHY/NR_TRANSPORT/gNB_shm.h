/*
 * SPDX-License-Identifier: LicenseRef-CSSL-1.0
 *
 * Shared memory interface between OAI NR gNB PHY/MAC and the external Python
 * gNB monitor. Three regions are exported:
 *   /gnb_meas_dl - DL transmission measurements and HARQ/BLER counters
 *   /gnb_meas_ul - UL transmission measurements and HARQ/BLER counters
 *   /srs_channel - latest SRS channel estimate
 */

#ifndef __GNB_SHM_H__
#define __GNB_SHM_H__

#include <stdint.h>
#include <stdbool.h>

/* ---------- shared memory region names ---------- */
#define GNB_DL_MEAS_SHM_NAME "/gnb_meas_dl"
#define GNB_UL_MEAS_SHM_NAME "/gnb_meas_ul"
#define GNB_SRS_SHM_NAME     "/srs_channel"

/* ---------- SRS sizing ---------- */
#define GNB_SRS_MAX_RX_ANT   4
#define GNB_SRS_MAX_PORTS    8
#define GNB_SRS_MAX_FFT      4096
#define GNB_SRS_MAX_SYMBOLS  4
#define GNB_SRS_CHAN_BYTES   (GNB_SRS_MAX_RX_ANT * GNB_SRS_MAX_PORTS * GNB_SRS_MAX_FFT * GNB_SRS_MAX_SYMBOLS * 4)

/* ---------- DL measurements shared memory ---------- */
typedef struct __attribute__((packed)) {
  volatile uint64_t seq;
  uint32_t  frame;
  uint32_t  slot;
  uint16_t  rnti;
  uint32_t  dlsch_received;
  uint32_t  dlsch_errors;
  uint16_t  bler_x1000;
  int16_t   sinr_db_x10;
  uint8_t   mcs;
  uint8_t   qam_mod_order;
  uint32_t  tbs;
  uint8_t   num_layers;
  uint16_t  num_rbs;
  uint16_t  num_symbols;
  uint8_t   rv;
  uint8_t   new_data_indicator;
  uint16_t  target_code_rate;
  uint8_t   cqi;
  uint8_t   ri;
  uint8_t   pmi_x1;
  uint8_t   pmi_x2;
  uint16_t  n_rb_dl;
} gnb_dl_meas_shm_t;

/* ---------- UL measurements shared memory ---------- */
typedef struct __attribute__((packed)) {
  volatile uint64_t seq;
  uint32_t  frame;
  uint32_t  slot;
  uint16_t  rnti;
  uint32_t  ulsch_received;
  uint32_t  ulsch_errors;
  uint16_t  bler_x1000;
  int16_t   sinr_db_x10;
  uint8_t   mcs;
  uint8_t   qam_mod_order;
  uint32_t  tbs;
  uint8_t   num_layers;
  uint16_t  num_rbs;
  uint16_t  num_symbols;
  uint8_t   rv;
  uint8_t   new_data_indicator;
  uint16_t  target_code_rate;
  uint16_t  timing_advance;
  uint8_t   ul_cqi;
  int16_t   rssi;
  uint16_t  n_rb_ul;
} gnb_ul_meas_shm_t;

/* ---------- SRS channel shared memory ---------- */
#define GNB_SHM_MAGIC 0x474E424DUL  /* "GNBM" */

typedef struct __attribute__((packed)) {
  uint32_t  magic;
  volatile uint64_t seq;
  uint32_t  frame;
  uint32_t  slot;
  uint16_t  rnti;
  uint8_t   num_rx_ant;
  uint8_t   num_ports;
  uint16_t  fft_size;
  uint16_t  n_rb;
  uint32_t  subcarrier_spacing;
  uint8_t   n_srs_symbols;
  int16_t   snr_db_x10;
  /* channel data follows contiguously */
} gnb_srs_shm_hdr_t;

#define GNB_SRS_SHM_HDR_SIZE   ((uint32_t)sizeof(gnb_srs_shm_hdr_t))
#define GNB_SRS_SHM_TOTAL_SIZE (GNB_SRS_SHM_HDR_SIZE + GNB_SRS_CHAN_BYTES)

/* ---------- API ---------- */

/** Initialise/open all gNB monitor shared memory regions. */
bool gNB_shm_init(void);

/** Write the latest DL transmission measurement. */
void gNB_shm_write_dl_meas(const gnb_dl_meas_shm_t *m, bool crc_ok);

/** Write a DL transmission snapshot from the scheduler (does not count HARQ). */
void gNB_shm_write_dl_sched(const gnb_dl_meas_shm_t *m);

/** Update the latest DL CSI-derived fields after a successful CSI report. */
void gNB_shm_update_dl_csi(uint16_t rnti,
                           uint8_t  cqi,
                           uint8_t  ri,
                           int16_t  sinr_db_x10,
                           uint8_t  pmi_x1,
                           uint8_t  pmi_x2);

/** Write the latest UL transmission measurement. */
void gNB_shm_write_ul_meas(const gnb_ul_meas_shm_t *m, bool crc_ok);

/** Write the latest SRS frequency-domain channel estimate. */
void gNB_shm_write_srs(uint32_t frame,
                       uint32_t slot,
                       uint16_t rnti,
                       uint8_t  nb_rx_ant,
                       uint8_t  n_ap,
                       uint8_t  n_srs_symbols,
                       uint16_t fft_size,
                       uint16_t n_rb,
                       uint32_t subcarrier_spacing,
                       int16_t  snr_db_x10,
                       const void *channel_data);

/** Close and unlink all gNB monitor shared memory regions. */
void gNB_shm_close(void);

#endif /* __GNB_SHM_H__ */
