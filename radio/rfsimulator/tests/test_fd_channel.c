/*
 * SPDX-License-Identifier: LicenseRef-CSSL-1.0
 */

#include <assert.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "common/platform_types.h"
#include "fd_channel.h"
#include "openair1/PHY/TOOLS/tools_defs.h"

#define TEST_FFT_SIZE 128
#define TEST_MAGIC 0x48434446u

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
} test_header_t;

static void write_test_file(const char *path, const cf_t *h)
{
  const test_header_t header = {
      .magic = TEST_MAGIC,
      .version = 1,
      .num_rx_ant = 1,
      .num_tx_ant = 1,
      .fft_size = TEST_FFT_SIZE,
      .symbols_per_slot = 14,
      .cp_length = 9,
      .cp_length0 = 11,
      .n_rb = 1,
      .reserved0 = 0,
      .subcarrier_spacing = 30000,
      .num_slots = 1,
      .reserved = {0},
  };
  FILE *fp = fopen(path, "wb");
  assert(fp != NULL);
  assert(fwrite(&header, sizeof(header), 1, fp) == 1);
  assert(fwrite(h, sizeof(*h), TEST_FFT_SIZE, fp) == TEST_FFT_SIZE);
  assert(fclose(fp) == 0);
}

static double apply_and_peak_magnitude(const char *path,
                                       const int source_index,
                                       int *peak_index)
{
  fd_channel_t *channel = fd_channel_load(path, 1, 1);
  assert(channel != NULL);

  c16_t input[TEST_FFT_SIZE] __attribute__((aligned(32))) = {0};
  c16_t *tx[1] = {input};
  cf_t output[TEST_FFT_SIZE] __attribute__((aligned(32))) = {0};
  cf_t *rx[1] = {output};
  input[source_index].r = 1000;

  assert(fd_apply_symbol(channel, tx, rx) == 0);
  *peak_index = 0;
  for (int i = 1; i < TEST_FFT_SIZE; i++) {
    if (hypotf(output[i].r, output[i].i)
        > hypotf(output[*peak_index].r, output[*peak_index].i))
      *peak_index = i;
  }

  fd_channel_free(channel);
  return hypot(output[*peak_index].r, output[*peak_index].i);
}

int main(void)
{
  load_dftslib();

  c16_t roundtrip_in[TEST_FFT_SIZE] __attribute__((aligned(32))) = {0};
  c16_t roundtrip_freq[TEST_FFT_SIZE] __attribute__((aligned(32))) = {0};
  c16_t roundtrip_out[TEST_FFT_SIZE] __attribute__((aligned(32))) = {0};
  roundtrip_in[3].r = 1000;
  assert(fd_cfft(roundtrip_in, roundtrip_freq, TEST_FFT_SIZE, false) == 0);
  assert(fd_cfft(roundtrip_freq, roundtrip_out, TEST_FFT_SIZE, true) == 0);
  int roundtrip_peak = 0;
  for (int i = 1; i < TEST_FFT_SIZE; i++)
    if (hypotf(roundtrip_out[i].r, roundtrip_out[i].i)
        > hypotf(roundtrip_out[roundtrip_peak].r,
                 roundtrip_out[roundtrip_peak].i))
      roundtrip_peak = i;
  assert(roundtrip_peak == 3);

  char path[128];
  snprintf(path, sizeof(path), "/tmp/test_fd_channel_%ld.bin", (long)getpid());

  cf_t identity[TEST_FFT_SIZE] __attribute__((aligned(32)));
  for (int k = 0; k < TEST_FFT_SIZE; k++) {
    identity[k].r = 2.0f;
    identity[k].i = 0.0f;
  }
  write_test_file(path, identity);

  int peak = -1;
  double peak_magnitude = apply_and_peak_magnitude(path, 3, &peak);
  assert(peak == 3);
  assert(peak_magnitude > 900.0);

  cf_t delay[TEST_FFT_SIZE] __attribute__((aligned(32)));
  const int delay_samples = 2;
  for (int k = 0; k < TEST_FFT_SIZE; k++) {
    const double phase =
        -2.0 * M_PI * (double)delay_samples * (double)k / TEST_FFT_SIZE;
    delay[k].r = cos(phase);
    delay[k].i = sin(phase);
  }
  write_test_file(path, delay);

  peak_magnitude = apply_and_peak_magnitude(path, 3, &peak);
  assert(peak == (3 + delay_samples) % TEST_FFT_SIZE);
  assert(peak_magnitude > 100.0);

  unlink(path);
  return 0;
}
