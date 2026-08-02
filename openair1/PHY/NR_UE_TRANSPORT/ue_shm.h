/*
 * SPDX-License-Identifier: LicenseRef-CSSL-1.0
 *
 * Shared memory interface between OAI NR UE PHY and external Python monitor.
 * Two regions: /csi_rs_channel (latest CSI-RS channel estimate) and
 * /meas_dl (latest DL measurements incl MCS, Qm, TBS, BLER, RSRP, SINR...).
 */

#ifndef __UE_SHM_H__
#define __UE_SHM_H__

#include <stdint.h>
#include <stdbool.h>

/* ---------- shared memory region names ---------- */
#define CSI_RS_SHM_NAME  "/csi_rs_channel"
#define MEAS_DL_SHM_NAME "/meas_dl"

/* ---------- magic / sizing ---------- */
#define UE_SHM_MAGIC       0x5545534DUL   /* "UESM" */

/*
 * CSI-RS channel estimate shared memory layout.
 *
 * Memory layout:
 *   [csi_rs_shm_hdr_t]  +  [channel data: c16_t[nb_rx_ant][num_ports][fft_size]]
 *
 * The shm region is allocated at the maximum possible size
 * (CSI_RS_SHM_TOTAL_SIZE).  The actual dimensions are recorded in the
 * header so the Python reader knows the valid portion.
 */
#define CSI_RS_MAX_RX_ANT   4
#define CSI_RS_MAX_PORTS    8
#define CSI_RS_MAX_FFT      4096
#define CSI_RS_CHAN_BYTES   (CSI_RS_MAX_RX_ANT * CSI_RS_MAX_PORTS * CSI_RS_MAX_FFT * 4)

typedef struct __attribute__((packed)) {
  uint32_t  magic;            /* UE_SHM_MAGIC */
  volatile uint64_t seq;      /* incremented on every write */
  uint32_t  frame;
  uint32_t  slot;
  uint8_t   num_rx_ant;       /* actual # of RX antennas written */
  uint8_t   num_ports;        /* actual # of CSI-RS ports    */
  uint16_t  fft_size;         /* actual FFT size (ofdm_symbol_size) */
  uint16_t  n_rb_dl;          /* actual DL N_RB             */
  uint32_t  subcarrier_spacing;
  /* channel data follows contiguously */
} csi_rs_shm_hdr_t;

#define CSI_RS_SHM_HDR_SIZE   ((uint32_t)sizeof(csi_rs_shm_hdr_t))
#define CSI_RS_SHM_TOTAL_SIZE (CSI_RS_SHM_HDR_SIZE + CSI_RS_CHAN_BYTES)

/* ---------- DL measurements shared memory (single flat struct) ---------- */
typedef struct __attribute__((packed)) {
  volatile uint64_t seq;           /* incremented on every write    */
  uint32_t  frame;
  uint32_t  slot;
  /* PDSCH / DLSCH per-slot info */
  uint8_t   mcs;
  uint8_t   qam_mod_order;         /* Qm                            */
  uint32_t  tbs;                   /* transport block size (bits)   */
  uint8_t   num_layers;            /* Nl: number of MIMO layers     */
  uint16_t  num_rbs;               /* allocated PRBs                */
  uint16_t  num_symbols;           /* allocated PDSCH symbols       */
  uint8_t   rv;                    /* redundancy version            */
  uint8_t   new_data_indicator;
  uint16_t  target_code_rate;      /* in units of 1/1024           */
  /* PHY aggregated stats (updated by pdsch_processing) */
  uint32_t  bitrate_bps;           /* instantaneous bitrate         */
  uint32_t  dlsch_received;
  uint32_t  dlsch_errors;
  uint8_t   dlsch_fer;             /* percentage (0-100)            */
  /* PHY measurements */
  int32_t   rsrp_dBm;              /* serving cell RSRP in dBm      */
  int16_t   rssi_dBm;              /* RSSI in dBm                   */
  int16_t   wideband_sinr_dB;      /* SINR from wideband_cqi_tot   */
  /* system / carrier info */
  uint16_t  n_rb_dl;
  uint32_t  subcarrier_spacing;
  int32_t   freq_offset;           /* Hz                            */
  uint8_t   nb_antennas_rx;
} meas_dl_shm_t;

/* ---------- API ---------- */

/** Initialise / open both shared memory regions.  Must be called once
 *  before any write.  Returns true on success. */
bool ue_shm_init(void);

/** Write the latest CSI-RS frequency-domain channel estimate. */
void ue_shm_write_csi_rs(uint32_t frame, uint32_t slot,
                         uint8_t  num_rx_ant, uint8_t num_ports,
                         uint16_t fft_size, uint16_t n_rb_dl,
                         uint32_t subcarrier_spacing,
                         const void *channel_data);

/** Write the latest DL measurements snapshot. */
void ue_shm_write_meas_dl(const meas_dl_shm_t *m);

/** Close and unlink shared memory regions. */
void ue_shm_close(void);

#endif /* __UE_SHM_H__ */
