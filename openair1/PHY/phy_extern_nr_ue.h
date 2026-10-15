/*
 * SPDX-License-Identifier: LicenseRef-CSSL-1.0
 */

#ifndef __PHY_EXTERN_UE_NR_H__
#define __PHY_EXTERN_UE_NR_H__

#include "openair1/PHY/defs_nr_UE.h"

extern PHY_VARS_NR_UE ***nrPHY_vars_UE_g;

int nr_csi_set_dl_layers_override(PHY_VARS_NR_UE *ue, int layers);
uint8_t nr_csi_get_dl_layers_override(const PHY_VARS_NR_UE *ue);

#endif
