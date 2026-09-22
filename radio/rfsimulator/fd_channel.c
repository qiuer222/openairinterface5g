/*
 * SPDX-License-Identifier: LicenseRef-CSSL-1.0
 *
 * Single-slot frequency-domain channel replay for RFSim.
 */

#define _POSIX_C_SOURCE 200112L

#include "fd_channel.h"

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "openair1/PHY/TOOLS/tools_defs.h"

#define FD_CHANNEL_MAGIC 0x48434446u
#define FD_CHANNEL_VERSION 1u
#define FD_CHANNEL_HEADER_SIZE 64u
#define FD_CHANNEL_MAX_ANTENNAS 64u
#define FD_CHANNEL_MAX_SYMBOLS 64u

typedef struct __attribute__((packed)) {
  uint32_t magic;
  uint16_t version;
  uint8_t num_rx_ant;
  uint8_t num_tx_ant;
  uint16_t fft_size;
  uint16_t symbols_per_slot;
  uint16_t cp_length;
  uint16_t cp_length0;
  uint16_t n_rb;
  uint16_t reserved0;
  uint32_t subcarrier_spacing;
  uint32_t num_slots;
  uint32_t reserved[9];
} fd_channel_file_header_t;

struct fd_channel {
  fd_channel_info_t info;
  int n_rb;
  uint32_t subcarrier_spacing;
  cf_t *h;
  c16_t *tx_freq;
  cf_t *rx_freq;
  c16_t *ifft_in;
  c16_t *ifft_out;
};

static uint16_t load_le16(const uint8_t *src)
{
  return (uint16_t)src[0] | ((uint16_t)src[1] << 8);
}

static uint32_t load_le32(const uint8_t *src)
{
  return (uint32_t)src[0] | ((uint32_t)src[1] << 8) | ((uint32_t)src[2] << 16)
         | ((uint32_t)src[3] << 24);
}

static int parse_header(const uint8_t *raw, fd_channel_file_header_t *header)
{
  header->magic = load_le32(raw);
  header->version = load_le16(raw + 4);
  header->num_rx_ant = raw[6];
  header->num_tx_ant = raw[7];
  header->fft_size = load_le16(raw + 8);
  header->symbols_per_slot = load_le16(raw + 10);
  header->cp_length = load_le16(raw + 12);
  header->cp_length0 = load_le16(raw + 14);
  header->n_rb = load_le16(raw + 16);
  header->reserved0 = load_le16(raw + 18);
  header->subcarrier_spacing = load_le32(raw + 20);
  header->num_slots = load_le32(raw + 24);
  memset(header->reserved, 0, sizeof(header->reserved));
  return 0;
}

static void *aligned_calloc(size_t count, size_t size)
{
  if (count == 0 || size == 0 || count > SIZE_MAX / size)
    return NULL;

  void *ptr = NULL;
  if (posix_memalign(&ptr, 32, count * size) != 0)
    return NULL;
  memset(ptr, 0, count * size);
  return ptr;
}

static void free_workspace(fd_channel_t *channel)
{
  if (!channel)
    return;

  free(channel->h);
  free(channel->tx_freq);
  free(channel->rx_freq);
  free(channel->ifft_in);
  free(channel->ifft_out);
}

static bool fd_supported_fft_size(int fft_size)
{
  switch (fft_size) {
#define FD_SUPPORTED_FFT_SIZE(Sz) case Sz: return true;
    FOREACH_IDFTSZ(FD_SUPPORTED_FFT_SIZE)
#undef FD_SUPPORTED_FFT_SIZE
    default:
      return false;
  }
}

fd_channel_t *fd_channel_load(const char *path, int expected_rx, int expected_tx)
{
  if (!path || expected_rx <= 0 || expected_tx <= 0)
    return NULL;

  FILE *fp = fopen(path, "rb");
  if (!fp)
    return NULL;

  uint8_t raw_header[FD_CHANNEL_HEADER_SIZE];
  fd_channel_file_header_t header;
  if (fread(raw_header, sizeof(raw_header), 1, fp) != 1
      || parse_header(raw_header, &header) != 0) {
    fclose(fp);
    return NULL;
  }

  if (header.magic != FD_CHANNEL_MAGIC || header.version != FD_CHANNEL_VERSION
      || header.num_slots != 1 || header.num_rx_ant == 0 || header.num_tx_ant == 0
      || header.fft_size == 0 || header.symbols_per_slot == 0
      || header.symbols_per_slot > FD_CHANNEL_MAX_SYMBOLS
      || !fd_supported_fft_size(header.fft_size)
      || header.cp_length > header.fft_size || header.cp_length0 > header.fft_size
      || header.num_rx_ant != expected_rx || header.num_tx_ant != expected_tx) {
    fclose(fp);
    return NULL;
  }

  const size_t fft_size = header.fft_size;
  const size_t num_rx = header.num_rx_ant;
  const size_t num_tx = header.num_tx_ant;
  if (num_rx > FD_CHANNEL_MAX_ANTENNAS || num_tx > FD_CHANNEL_MAX_ANTENNAS
      || num_rx > SIZE_MAX / num_tx || num_rx * num_tx > SIZE_MAX / fft_size
      || num_rx * num_tx * fft_size > SIZE_MAX / sizeof(cf_t)) {
    fclose(fp);
    return NULL;
  }

  fd_channel_t *channel = calloc(1, sizeof(*channel));
  if (!channel) {
    fclose(fp);
    return NULL;
  }

  channel->info.num_rx = num_rx;
  channel->info.num_tx = num_tx;
  channel->info.fft_size = fft_size;
  channel->info.symbols_per_slot = header.symbols_per_slot;
  channel->info.cp_length = header.cp_length;
  channel->info.cp_length0 = header.cp_length0;
  channel->n_rb = header.n_rb;
  channel->subcarrier_spacing = header.subcarrier_spacing;

  const size_t h_count = num_rx * num_tx * fft_size;
  channel->h = aligned_calloc(h_count, sizeof(*channel->h));
  channel->tx_freq = aligned_calloc(num_tx * fft_size, sizeof(*channel->tx_freq));
  channel->rx_freq = aligned_calloc(num_rx * fft_size, sizeof(*channel->rx_freq));
  channel->ifft_in = aligned_calloc(fft_size, sizeof(*channel->ifft_in));
  channel->ifft_out = aligned_calloc(fft_size, sizeof(*channel->ifft_out));
  if (!channel->h || !channel->tx_freq || !channel->rx_freq || !channel->ifft_in
      || !channel->ifft_out) {
    free_workspace(channel);
    free(channel);
    fclose(fp);
    return NULL;
  }

  if (fread(channel->h, sizeof(*channel->h), h_count, fp) != h_count) {
    free_workspace(channel);
    free(channel);
    fclose(fp);
    return NULL;
  }
  if (fgetc(fp) != EOF) {
    free_workspace(channel);
    free(channel);
    fclose(fp);
    return NULL;
  }
  fclose(fp);

  double sum_power = 0.0;
  size_t nonzero = 0;
  for (size_t i = 0; i < h_count; i++) {
    const cf_t value = channel->h[i];
    if (!isfinite(value.r) || !isfinite(value.i)) {
      free_workspace(channel);
      free(channel);
      return NULL;
    }
    if (value.r != 0.0f || value.i != 0.0f) {
      sum_power += (double)value.r * value.r + (double)value.i * value.i;
      nonzero++;
    }
  }

  if (nonzero == 0 || sum_power <= 0.0) {
    free_workspace(channel);
    free(channel);
    return NULL;
  }

  const float rms = (float)sqrt(sum_power / nonzero);
  for (size_t i = 0; i < h_count; i++) {
    if (channel->h[i].r == 0.0f && channel->h[i].i == 0.0f)
      continue;
    channel->h[i].r /= rms;
    channel->h[i].i /= rms;
  }

  return channel;
}

void fd_channel_free(fd_channel_t *channel)
{
  if (!channel)
    return;
  free_workspace(channel);
  free(channel);
}

const fd_channel_info_t *fd_channel_info(const fd_channel_t *channel)
{
  return channel ? &channel->info : NULL;
}

int fd_cfft(const c16_t *in, c16_t *out, int fft_size, bool inverse)
{
  if (!in || !out || !fd_supported_fft_size(fft_size) || !dft || !idft)
    return -1;

  if (inverse) {
    const idft_size_idx_t size = get_idft(fft_size);
    if (size == IDFT_SIZE_IDXTABLESIZE)
      return -1;
    idft(size, (int16_t *)in, (int16_t *)out, 1);
  } else {
    const dft_size_idx_t size = get_dft(fft_size);
    if (size == DFT_SIZE_IDXTABLESIZE)
      return -1;
    dft(size, (int16_t *)in, (int16_t *)out, 1);
  }
  return 0;
}

static int16_t float_to_c16(float value)
{
  const float rounded = nearbyintf(value);
  if (rounded > 32767.0f)
    return 32767;
  if (rounded < -32768.0f)
    return -32768;
  return (int16_t)rounded;
}

int fd_apply_symbol(fd_channel_t *channel,
                    c16_t *const tx_time[],
                    cf_t *const rx_time[])
{
  if (!channel || !tx_time || !rx_time)
    return -1;

  const fd_channel_info_t *info = &channel->info;
  const int fft_size = info->fft_size;
  const int num_tx = info->num_tx;
  const int num_rx = info->num_rx;

  for (int tx = 0; tx < num_tx; tx++) {
    if (!tx_time[tx])
      return -1;
    if (fd_cfft(tx_time[tx], channel->tx_freq + (size_t)tx * fft_size, fft_size,
                false)
        != 0)
      return -1;
  }

  for (int rx = 0; rx < num_rx; rx++) {
    if (!rx_time[rx])
      return -1;

    cf_t *rx_freq = channel->rx_freq + (size_t)rx * fft_size;
    memset(rx_freq, 0, (size_t)fft_size * sizeof(*rx_freq));

    for (int tx = 0; tx < num_tx; tx++) {
      const c16_t *tx_freq = channel->tx_freq + (size_t)tx * fft_size;
      const cf_t *h = channel->h + ((size_t)rx * num_tx + tx) * fft_size;
      for (int k = 0; k < fft_size; k++) {
        rx_freq[k].r += (float)tx_freq[k].r * h[k].r
                        - (float)tx_freq[k].i * h[k].i;
        rx_freq[k].i += (float)tx_freq[k].r * h[k].i
                        + (float)tx_freq[k].i * h[k].r;
      }
    }

    for (int k = 0; k < fft_size; k++) {
      if (!isfinite(rx_freq[k].r) || !isfinite(rx_freq[k].i))
        return -1;
      channel->ifft_in[k].r = float_to_c16(rx_freq[k].r);
      channel->ifft_in[k].i = float_to_c16(rx_freq[k].i);
    }

    if (fd_cfft(channel->ifft_in, channel->ifft_out, fft_size, true) != 0)
      return -1;

    for (int n = 0; n < fft_size; n++) {
      rx_time[rx][n].r = channel->ifft_out[n].r;
      rx_time[rx][n].i = channel->ifft_out[n].i;
    }
  }

  return 0;
}
