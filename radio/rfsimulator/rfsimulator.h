/*
 * SPDX-License-Identifier: LicenseRef-CSSL-1.0
 */

#ifndef __RFSIMULATOR_H
#define __RFSIMULATOR_H
#include <stdbool.h>
void rxAddInput(c16_t **input_sig, cf_t *after_channel_sig, int rxAnt, channel_desc_t *channelDesc, int nbSamples);
void update_channel_model(channel_desc_t *channelDesc, int nbSamples, uint64_t TS);

/* Recorded-channel replay (time-domain taps) */
int  rfsim_load_srs_file(const char *path);
void rxAddInput_srsfile(c16_t **input_sig, cf_t *after_channel_sig,
                         int rxAnt, channel_desc_t *channelDesc, int nbSamples);
extern int srs_replay_loaded;  /* flag checked by simulator.cpp */

#endif
