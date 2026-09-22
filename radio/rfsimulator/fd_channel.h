/*
 * SPDX-License-Identifier: LicenseRef-CSSL-1.0
 */

#ifndef __FD_CHANNEL_H
#define __FD_CHANNEL_H

#include <stdbool.h>

#include "common/platform_types.h"

typedef struct {
  int num_rx;
  int num_tx;
  int fft_size;
  int symbols_per_slot;
  int cp_length;
  int cp_length0;
} fd_channel_info_t;

typedef struct fd_channel fd_channel_t;

fd_channel_t *fd_channel_load(const char *path, int expected_rx, int expected_tx);
void fd_channel_free(fd_channel_t *channel);
const fd_channel_info_t *fd_channel_info(const fd_channel_t *channel);

int fd_cfft(const c16_t *in, c16_t *out, int fft_size, bool inverse);
int fd_apply_symbol(fd_channel_t *channel,
                    c16_t *const tx_time[],
                    cf_t *const rx_time[]);

#endif
