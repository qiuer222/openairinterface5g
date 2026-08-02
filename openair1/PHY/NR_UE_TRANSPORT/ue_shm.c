/*
 * SPDX-License-Identifier: LicenseRef-CSSL-1.0
 *
 * POSIX shared memory writer — exports CSI-RS channel estimate and
 * DL measurements from the NR UE PHY to an external Python monitor.
 */

#include <sys/mman.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/stat.h>
#include <string.h>
#include "ue_shm.h"

/* module-level state ------------------------------------------------- */
static int          csi_rs_fd   = -1;
static int          meas_dl_fd  = -1;
static void        *csi_rs_base = NULL;  /* mmap base for CSI-RS shm */
static void        *meas_dl_base = NULL; /* mmap base for meas_dl shm */
static uint64_t     csi_rs_seq  = 0;
static uint64_t     meas_dl_seq = 0;

bool ue_shm_init(void)
{
  /* --- CSI-RS channel shm --- */
  csi_rs_fd = shm_open(CSI_RS_SHM_NAME, O_CREAT | O_RDWR, 0666);
  if (csi_rs_fd < 0)
    return false;

  if (ftruncate(csi_rs_fd, (off_t)CSI_RS_SHM_TOTAL_SIZE) < 0)
    goto fail_csi_fd;

  csi_rs_base = mmap(NULL, CSI_RS_SHM_TOTAL_SIZE,
                     PROT_READ | PROT_WRITE, MAP_SHARED,
                     csi_rs_fd, 0);
  if (csi_rs_base == MAP_FAILED) {
    csi_rs_base = NULL;
    goto fail_csi_fd;
  }
  memset(csi_rs_base, 0, CSI_RS_SHM_TOTAL_SIZE);
  ((csi_rs_shm_hdr_t *)csi_rs_base)->magic = UE_SHM_MAGIC;

  /* --- DL measurements shm --- */
  meas_dl_fd = shm_open(MEAS_DL_SHM_NAME, O_CREAT | O_RDWR, 0666);
  if (meas_dl_fd < 0)
    goto fail_csi_all;

  if (ftruncate(meas_dl_fd, (off_t)sizeof(meas_dl_shm_t)) < 0)
    goto fail_meas_fd;

  meas_dl_base = mmap(NULL, sizeof(meas_dl_shm_t),
                      PROT_READ | PROT_WRITE, MAP_SHARED,
                      meas_dl_fd, 0);
  if (meas_dl_base == MAP_FAILED) {
    meas_dl_base = NULL;
    goto fail_meas_fd;
  }
  memset(meas_dl_base, 0, sizeof(meas_dl_shm_t));

  return true;

 fail_meas_fd:
  close(meas_dl_fd);
  meas_dl_fd = -1;
 fail_csi_all:
  munmap(csi_rs_base, CSI_RS_SHM_TOTAL_SIZE);
  csi_rs_base = NULL;
 fail_csi_fd:
  close(csi_rs_fd);
  csi_rs_fd = -1;
  return false;
}

/* ----------------------------------------------------------------- */
void ue_shm_write_csi_rs(uint32_t frame, uint32_t slot,
                         uint8_t  num_rx_ant, uint8_t num_ports,
                         uint16_t fft_size, uint16_t n_rb_dl,
                         uint32_t subcarrier_spacing,
                         const void *channel_data)
{
  csi_rs_shm_hdr_t *hdr = (csi_rs_shm_hdr_t *)csi_rs_base;
  if (!hdr)
    return;

  hdr->frame    = frame;
  hdr->slot     = slot;
  hdr->num_rx_ant      = num_rx_ant;
  hdr->num_ports       = num_ports;
  hdr->fft_size        = fft_size;
  hdr->n_rb_dl         = n_rb_dl;
  hdr->subcarrier_spacing = subcarrier_spacing;

  if (channel_data) {
    const uint32_t data_size = (uint32_t)num_rx_ant * num_ports * fft_size * 4;
    const uint32_t copy_sz   = (data_size < CSI_RS_CHAN_BYTES) ? data_size : CSI_RS_CHAN_BYTES;
    memcpy((uint8_t *)csi_rs_base + CSI_RS_SHM_HDR_SIZE, channel_data, copy_sz);
  }

  /* memory barrier: ensure header + channel data visible before seq bump */
  __sync_synchronize();
  hdr->seq = __sync_add_and_fetch(&csi_rs_seq, 1);
}

/* ----------------------------------------------------------------- */
void ue_shm_write_meas_dl(const meas_dl_shm_t *m)
{
  meas_dl_shm_t *dst = (meas_dl_shm_t *)meas_dl_base;
  if (!dst || !m)
    return;

  const uint64_t old_seq = dst->seq;   /* preserve reader-visible seq */

  /* copy everything except the volatile seq field */
  memcpy(&dst->frame, &m->frame,
         sizeof(meas_dl_shm_t) - sizeof(uint64_t));

  dst->seq = old_seq;                  /* restore (not yet updated) */

  __sync_synchronize();
  dst->seq = __sync_add_and_fetch(&meas_dl_seq, 1);
}

/* ----------------------------------------------------------------- */
void ue_shm_close(void)
{
  if (csi_rs_base) {
    munmap(csi_rs_base, CSI_RS_SHM_TOTAL_SIZE);
    csi_rs_base = NULL;
  }
  if (csi_rs_fd >= 0) {
    close(csi_rs_fd);
    shm_unlink(CSI_RS_SHM_NAME);
    csi_rs_fd = -1;
  }
  if (meas_dl_base) {
    munmap(meas_dl_base, sizeof(meas_dl_shm_t));
    meas_dl_base = NULL;
  }
  if (meas_dl_fd >= 0) {
    close(meas_dl_fd);
    shm_unlink(MEAS_DL_SHM_NAME);
    meas_dl_fd = -1;
  }
}
