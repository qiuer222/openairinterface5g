/*
 * SPDX-License-Identifier: LicenseRef-CSSL-1.0
 */

/*!
 * \brief Implementation of telnet CI functions for nrUE
 */

#include <sys/types.h>
#include <stdio.h>
#include <unistd.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <strings.h>
#include "openair2/LAYER2/NR_MAC_UE/mac_defs.h"
#include "openair2/LAYER2/NR_MAC_UE/mac_proto.h"
#include "openair2/RRC/NR_UE/rrc_proto.h"
#include "openair1/PHY/defs_nr_common.h"
#include "openair1/PHY/defs_nr_UE.h"
#include "openair3/NAS/NR_UE/nr_nas_msg.h"
#include "openair1/PHY/phy_extern_nr_ue.h"

#define TELNETSERVERCODE
#include "telnetsrv.h"

#define ERROR_MSG_RET(mSG, aRGS...) do { prnt(mSG, ##aRGS); return 1; } while (0)

/* UE L2 state string */
const char* NR_UE_L2_STATE_STR[] = {
#define UE_STATE(state) #state,
  NR_UE_L2_STATES
#undef UE_STATE
};

static int get_default_ue_id(void)
{
  NR_UE_RRC_INST_t *rrc = get_NR_UE_rrc_inst(0);
  if (!rrc)
    return -1;
  return rrc->ue_id;
}

/**
 * Get the synchronization state of a UE.
 *
 * @param buf    User input buffer containing UE ID
 * @param debug  Debug flag (not used)
 * @param prnt   Function to print output
 * @return       0 on success, error code otherwise
 */
int get_sync_state(char *buf, int debug, telnet_printfunc_t prnt)
{
  UNUSED(debug);
  int ue_id = -1;
  if (!buf) {
    ERROR_MSG_RET("no UE ID provided to telnet command\n");
  } else {
    ue_id = strtol(buf, NULL, 10);
    if (ue_id < 0)
      ERROR_MSG_RET("UE ID needs to be positive\n");
  }
  /* get sync state */
  int sync_state = nr_ue_get_sync_state(ue_id);
  prnt("UE sync state = %s\n", NR_UE_L2_STATE_STR[sync_state]);
  return 0;
}

/**
 * Force RLF on UE
 */
int force_rlf(char *buf, int debug, telnet_printfunc_t prnt)
{
  UNUSED(debug);
  UNUSED(buf);
  UNUSED(prnt);
  NR_UE_RRC_INST_t *rrc = get_NR_UE_rrc_inst(0);
  handle_rlf_detection(rrc);
  return 0;
}

/**
 * Send UE to RRC_IDLE
 */
int force_RRC_IDLE(char *buf, int debug, telnet_printfunc_t prnt)
{
  UNUSED(debug);
  UNUSED(buf);
  UNUSED(prnt);
  NR_UE_RRC_INST_t *rrc = get_NR_UE_rrc_inst(0);
  nr_rrc_going_to_IDLE(rrc, OTHER, NULL);
  return 0;
}

/** @brief Trigger RA with Msg3 C-RNTI */
int force_crnti_ra(char *buf, int debug, telnet_printfunc_t prnt)
{
  UNUSED(debug);
  UNUSED(buf);
  UNUSED(prnt);
  NR_UE_MAC_INST_t *mac = get_mac_inst(0);
  trigger_MAC_UE_RA(mac, NULL);
  return 0;
}

static int force_deregistration(char *buf, int debug, telnet_printfunc_t prnt)
{
  UNUSED(debug);
  UNUSED(buf);
  UNUSED(prnt);
  MessageDef *msg = itti_alloc_new_message(TASK_NAS_NRUE, 0, NAS_DEREGISTRATION_REQ);
  NAS_DEREGISTRATION_REQ(msg).cause = AS_DETACH;
  itti_send_msg_to_task(TASK_NAS_NRUE, 0, msg);
  return 0;
}

extern float get_prs_max_dl_toa(prs_meas_t *prs_meas);
static int get_dl_toa(char *buf, int debug, telnet_printfunc_t prnt)
{
  UNUSED(debug);
  UNUSED(buf);
  // TODO multiple antennas, resources, gNBs?
  int gNB_id = 0;
  int rsc_id = 0;
  int ant = 0;

  PHY_VARS_NR_UE *UE = nrPHY_vars_UE_g[0][0];
  if (!UE || !UE->prs_vars[gNB_id])
    ERROR_MSG_RET("no UE/prs_vars found!\n");
  NR_PRS_RESOURCE_t *prs_res = &UE->prs_vars[gNB_id]->prs_resource[rsc_id];
  if (!prs_res->prs_meas || !prs_res->prs_meas[ant])
    ERROR_MSG_RET("prs_meas not initialized!\n");

  float max = get_prs_max_dl_toa(prs_res->prs_meas[ant]);
  prnt("UE max PRS DL ToA %.3f\n", max);
  return 0;
}

static int add_pdu_session(char *buf, int debug, telnet_printfunc_t prnt)
{
  UNUSED(debug);
  int ue_id = -1;
  int pdusession_id = -1;

  if (!buf) {
    ERROR_MSG_RET("Missing argument: expected PDUSessionID[,UE_ID]\n");
  }

  // Try parsing values in the form: "PDUSessionID[,UE_ID]"
  int n = sscanf(buf, "%d,%d", &pdusession_id, &ue_id);
  if (n == 1) {
    // Only PDUSessionID provided: use default UE ID
    ue_id = get_default_ue_id();
    if (ue_id < 0)
      ERROR_MSG_RET("No default UE context found\n");
  } else if (n != 2) {
    ERROR_MSG_RET("Invalid format: expected PDUSessionID[,UE_ID]\n");
  }

  if (pdusession_id < 0 || pdusession_id > 255)
    ERROR_MSG_RET("PDUSessionID must be in range [0,255]\n");
  if (ue_id < 0)
    ERROR_MSG_RET("UE_ID must be >= 0\n");

  nr_ue_nas_t *nas = get_ue_nas_info(ue_id);
  if (!nas)
    ERROR_MSG_RET("No NAS context found for UE_ID %d\n", ue_id);

  DevAssert(nas->uicc);
  nssai_t nssai = {nas->uicc->nssai_sst, nas->uicc->nssai_sd};
  pdu_session_config_t c = {pdusession_id, 1 /* = PDU_SESSION_TYPE_IPV4 */, nssai, nas->uicc->dnnStr};
  nas->uicc->pdu_sessions[nas->uicc->n_pdu_sessions++] = c;
  request_pdusession(nas, &c);
  prnt("Triggered PDU session request for UE %d with ID %d\n", ue_id, pdusession_id);
  return 0;
}

static int set_layers(char *buf, int debug, telnet_printfunc_t prnt)
{
  UNUSED(debug);
  char direction[8];
  char value[16];
  char extra;
  if (!buf || sscanf(buf, "%7s %15s %c", direction, value, &extra) != 2)
    ERROR_MSG_RET("Usage: ciUE set_layers dl <1..4|auto>\n");

  if (strcasecmp(direction, "dl") != 0)
    ERROR_MSG_RET("Invalid direction '%s'; use ciUE set_layers dl <1..4|auto>\n", direction);

  int layers;
  if (strcasecmp(value, "auto") == 0) {
    layers = 0;
  } else {
    char *end = NULL;
    errno = 0;
    long parsed = strtol(value, &end, 10);
    if (errno || !end || *end != '\0' || parsed < 1 || parsed > NR_MAX_NB_LAYERS)
      ERROR_MSG_RET("Invalid DL layer count '%s'; expected 1..%d or auto\n", value, NR_MAX_NB_LAYERS);
    layers = parsed;
  }

  PHY_VARS_NR_UE *ue = (nrPHY_vars_UE_g && nrPHY_vars_UE_g[0]) ? nrPHY_vars_UE_g[0][0] : NULL;
  if (!ue || !ue->nr_csi_info)
    ERROR_MSG_RET("UE PHY is not initialized\n");

  int effective_layers = layers;
  if (layers > 0) {
    NR_UE_MAC_INST_t *mac = get_mac_inst(0);
    if (!mac)
      ERROR_MSG_RET("UE MAC is not initialized\n");

    long max_supported_layers = mac->sc_info.maxMIMO_Layers_PDSCH ? *mac->sc_info.maxMIMO_Layers_PDSCH
                                                                  : (long)mac->uecap_maxMIMO_PDSCH_layers;
    if (max_supported_layers <= 0)
      ERROR_MSG_RET("PDSCH MIMO configuration is not available\n");

    effective_layers = min(layers, (int)max_supported_layers);
  }

  if (nr_csi_set_dl_layers_override(ue, effective_layers) != 0)
    ERROR_MSG_RET("Could not update DL layer override\n");

  if (effective_layers == 0)
    prnt("DL layers override cleared (auto)\n");
  else if (effective_layers != layers)
    prnt("DL layers override requested %d, effective %d (clamped by UE configuration/implementation)\n", layers, effective_layers);
  else
    prnt("DL layers override set to %d\n", effective_layers);
  return 0;
}

/* Telnet shell command definitions */
static telnetshell_cmddef_t cicmds[] = {
  {"sync_state", "[UE_ID(int,opt)]", get_sync_state},
  {"force_rlf", "", force_rlf},
  {"force_RRC_IDLE", "", force_RRC_IDLE},
  {"force_crnti_ra", "", force_crnti_ra},
  {"deregistration", "", force_deregistration},
  {"get_max_dl_toa", "[ant]", get_dl_toa},
  {"add_pdu_session", "[PDUSessionID(int)],[UE_ID(int,opt)]", add_pdu_session},
  {"set_layers", "dl <1..4|auto>", set_layers},
  {"", "", NULL},
};

/* Telnet shell variable definitions (if needed) */
static telnetshell_vardef_t civars[] = {
  {"", 0, 0, NULL}
};

/* Add CI UE commands */
void add_ciUE_cmds(void) {
  add_telnetcmd("ciUE", civars, cicmds);
}
