/*
 * SPDX-License-Identifier: LicenseRef-CSSL-1.0
 *
 * POSIX shared memory writer for the NR gNB external monitor.
 */

#include "gNB_shm.h"

#include <fcntl.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

static int    dl_fd  = -1;
static int    ul_fd  = -1;
static int    srs_fd = -1;
static void  *dl_base  = NULL;
static void  *ul_base  = NULL;
static void  *srs_base = NULL;
static uint64_t dl_seq  = 0;
static uint64_t ul_seq  = 0;
static uint64_t srs_seq = 0;
static uint32_t dl_received = 0;
static uint32_t dl_errors   = 0;
static uint32_t ul_received = 0;
static uint32_t ul_errors   = 0;
static int      init_count  = 0;
static uint8_t  last_dl_cqi = 0;
static uint8_t  last_dl_ri  = 0;
static int16_t  last_dl_sinr_db_x10 = 0;
static uint8_t  last_dl_pmi_x1 = 0;
static uint8_t  last_dl_pmi_x2 = 0;

static bool open_region(const char *name, size_t size, int *fd, void **base)
{
  if (*fd >= 0 && *base != NULL)
    return true;

  int new_fd = shm_open(name, O_CREAT | O_RDWR, 0666);
  if (new_fd < 0)
    return false;

  if (ftruncate(new_fd, (off_t)size) < 0) {
    close(new_fd);
    return false;
  }

  void *new_base = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, new_fd, 0);
  if (new_base == MAP_FAILED) {
    close(new_fd);
    return false;
  }

  memset(new_base, 0, size);
  *fd = new_fd;
  *base = new_base;
  return true;
}

bool gNB_shm_init(void)
{
  if (dl_fd >= 0 && dl_base != NULL && ul_fd >= 0 && ul_base != NULL &&
      srs_fd >= 0 && srs_base != NULL) {
    init_count++;
    return true;
  }

  if (init_count == 0) {
    dl_received = 0;
    dl_errors = 0;
    ul_received = 0;
    ul_errors = 0;
    last_dl_cqi = 0;
    last_dl_ri = 0;
    last_dl_sinr_db_x10 = 0;
    last_dl_pmi_x1 = 0;
    last_dl_pmi_x2 = 0;
  }

  if (!open_region(GNB_DL_MEAS_SHM_NAME, sizeof(gnb_dl_meas_shm_t), &dl_fd, &dl_base))
    goto fail;
  if (!open_region(GNB_UL_MEAS_SHM_NAME, sizeof(gnb_ul_meas_shm_t), &ul_fd, &ul_base))
    goto fail;
  if (!open_region(GNB_SRS_SHM_NAME, GNB_SRS_SHM_TOTAL_SIZE, &srs_fd, &srs_base))
    goto fail;

  ((gnb_srs_shm_hdr_t *)srs_base)->magic = GNB_SHM_MAGIC;
  init_count = 1;
  return true;

fail:
  gNB_shm_close();
  return false;
}

static void write_dl_snapshot(const gnb_dl_meas_shm_t *m)
{
  if (dl_base == NULL || m == NULL)
    return;

  gnb_dl_meas_shm_t tmp = *m;
  if (tmp.cqi == 0 && last_dl_cqi != 0)
    tmp.cqi = last_dl_cqi;
  if (tmp.ri == 0 && last_dl_ri != 0)
    tmp.ri = last_dl_ri;
  if (tmp.sinr_db_x10 == 0 && last_dl_sinr_db_x10 != 0)
    tmp.sinr_db_x10 = last_dl_sinr_db_x10;
  if (tmp.pmi_x1 == 0 && last_dl_pmi_x1 != 0)
    tmp.pmi_x1 = last_dl_pmi_x1;
  if (tmp.pmi_x2 == 0 && last_dl_pmi_x2 != 0)
    tmp.pmi_x2 = last_dl_pmi_x2;

  if (tmp.cqi != 0)
    last_dl_cqi = tmp.cqi;
  if (tmp.ri != 0)
    last_dl_ri = tmp.ri;
  if (tmp.sinr_db_x10 != 0)
    last_dl_sinr_db_x10 = tmp.sinr_db_x10;
  if (tmp.pmi_x1 != 0)
    last_dl_pmi_x1 = tmp.pmi_x1;
  if (tmp.pmi_x2 != 0)
    last_dl_pmi_x2 = tmp.pmi_x2;

  gnb_dl_meas_shm_t *dst = (gnb_dl_meas_shm_t *)dl_base;
  memcpy(dst, &tmp, sizeof(tmp) - sizeof(uint64_t));
  __sync_synchronize();
  dst->seq = __sync_add_and_fetch(&dl_seq, 1);
}

void gNB_shm_write_dl_meas(const gnb_dl_meas_shm_t *m, bool crc_ok)
{
  if (dl_base == NULL || m == NULL)
    return;

  dl_received++;
  if (!crc_ok)
    dl_errors++;

  gnb_dl_meas_shm_t tmp = *m;
  tmp.dlsch_received = dl_received;
  tmp.dlsch_errors = dl_errors;
  write_dl_snapshot(&tmp);
}

void gNB_shm_write_dl_sched(const gnb_dl_meas_shm_t *m)
{
  write_dl_snapshot(m);
}

void gNB_shm_update_dl_csi(uint16_t rnti,
                           uint8_t  cqi,
                           uint8_t  ri,
                           int16_t  sinr_db_x10,
                           uint8_t  pmi_x1,
                           uint8_t  pmi_x2)
{
  if (dl_base == NULL)
    return;

  gnb_dl_meas_shm_t *dst = (gnb_dl_meas_shm_t *)dl_base;
  dst->rnti = rnti;
  if (cqi != 0)
    dst->cqi = cqi;
  if (ri != 0)
    dst->ri = ri;
  if (sinr_db_x10 != 0)
    dst->sinr_db_x10 = sinr_db_x10;
  if (pmi_x1 != 0)
    dst->pmi_x1 = pmi_x1;
  if (pmi_x2 != 0)
    dst->pmi_x2 = pmi_x2;

  if (dst->cqi != 0)
    last_dl_cqi = dst->cqi;
  if (dst->ri != 0)
    last_dl_ri = dst->ri;
  if (dst->sinr_db_x10 != 0)
    last_dl_sinr_db_x10 = dst->sinr_db_x10;
  if (dst->pmi_x1 != 0)
    last_dl_pmi_x1 = dst->pmi_x1;
  if (dst->pmi_x2 != 0)
    last_dl_pmi_x2 = dst->pmi_x2;

  __sync_synchronize();
  dst->seq = __sync_add_and_fetch(&dl_seq, 1);
}

void gNB_shm_write_ul_meas(const gnb_ul_meas_shm_t *m, bool crc_ok)
{
  if (ul_base == NULL || m == NULL)
    return;

  ul_received++;
  if (!crc_ok)
    ul_errors++;

  gnb_ul_meas_shm_t tmp = *m;
  tmp.ulsch_received = ul_received;
  tmp.ulsch_errors = ul_errors;

  gnb_ul_meas_shm_t *dst = (gnb_ul_meas_shm_t *)ul_base;
  memcpy(dst, &tmp, sizeof(tmp) - sizeof(uint64_t));
  __sync_synchronize();
  dst->seq = __sync_add_and_fetch(&ul_seq, 1);
}

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
                       const void *channel_data)
{
  if (srs_base == NULL || channel_data == NULL)
    return;
  if (nb_rx_ant == 0 || n_ap == 0 || n_srs_symbols == 0 || fft_size == 0)
    return;
  if (nb_rx_ant > GNB_SRS_MAX_RX_ANT || n_ap > GNB_SRS_MAX_PORTS ||
      fft_size > GNB_SRS_MAX_FFT || n_srs_symbols > GNB_SRS_MAX_SYMBOLS)
    return;

  gnb_srs_shm_hdr_t *hdr = (gnb_srs_shm_hdr_t *)srs_base;
  const size_t bytes = (size_t)nb_rx_ant * n_ap * n_srs_symbols * fft_size * 4;
  if (bytes > GNB_SRS_CHAN_BYTES)
    return;

  hdr->frame = frame;
  hdr->slot = slot;
  hdr->rnti = rnti;
  hdr->num_rx_ant = nb_rx_ant;
  hdr->num_ports = n_ap;
  hdr->fft_size = fft_size;
  hdr->n_rb = n_rb;
  hdr->subcarrier_spacing = subcarrier_spacing;
  hdr->n_srs_symbols = n_srs_symbols;
  hdr->snr_db_x10 = snr_db_x10;

  memcpy((uint8_t *)srs_base + GNB_SRS_SHM_HDR_SIZE, channel_data, bytes);
  __sync_synchronize();
  hdr->seq = __sync_add_and_fetch(&srs_seq, 1);
}

void gNB_shm_close(void)
{
  if (init_count > 0)
    init_count--;
  if (init_count > 0)
    return;

  if (dl_base != NULL) {
    munmap(dl_base, sizeof(gnb_dl_meas_shm_t));
    dl_base = NULL;
  }
  if (dl_fd >= 0) {
    close(dl_fd);
    shm_unlink(GNB_DL_MEAS_SHM_NAME);
    dl_fd = -1;
  }

  if (ul_base != NULL) {
    munmap(ul_base, sizeof(gnb_ul_meas_shm_t));
    ul_base = NULL;
  }
  if (ul_fd >= 0) {
    close(ul_fd);
    shm_unlink(GNB_UL_MEAS_SHM_NAME);
    ul_fd = -1;
  }

  if (srs_base != NULL) {
    munmap(srs_base, GNB_SRS_SHM_TOTAL_SIZE);
    srs_base = NULL;
  }
  if (srs_fd >= 0) {
    close(srs_fd);
    shm_unlink(GNB_SRS_SHM_NAME);
    srs_fd = -1;
  }
}
