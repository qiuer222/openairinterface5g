/*
 * SPDX-License-Identifier: LicenseRef-CSSL-1.0
 */

#include "debug_capture.h"

#include <errno.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "common/utils/LOG/log.h"

typedef struct {
  bool configured;
  bool closed;
  bool active;
  bool start_reached;
  debug_capture_role_t role;
  int max_events;
  int start_sfn;
  int start_slot;
  int event_count;
  uint16_t rnti;
  uint32_t event_id;
  uint32_t first_frame;
  uint32_t first_slot;
  uint32_t last_frame;
  uint32_t last_slot;
  uint64_t sequence;
  char run_id[64];
  char directory[512];
  char capture_path[600];
  char manifest_path[600];
  FILE *capture;
  pthread_mutex_t mutex;
} debug_capture_state_t;

static debug_capture_state_t capture = {.mutex = PTHREAD_MUTEX_INITIALIZER};

static bool mkdir_if_needed(const char *path)
{
  if (mkdir(path, 0775) == 0)
    return true;
  return errno == EEXIST;
}

static bool make_directory(const char *path)
{
  char tmp[512];
  if (strlen(path) >= sizeof(tmp))
    return false;
  strcpy(tmp, path);

  for (char *p = tmp + 1; *p; p++) {
    if (*p != '/')
      continue;
    *p = '\0';
    if (!mkdir_if_needed(tmp))
      return false;
    *p = '/';
  }
  return mkdir_if_needed(tmp);
}

static void set_run_id(void)
{
  const char *env = getenv("OAI_DEBUG_CAPTURE_RUN_ID");
  if (env && env[0]) {
    snprintf(capture.run_id, sizeof(capture.run_id), "%s", env);
    return;
  }

  struct timespec ts;
  clock_gettime(CLOCK_REALTIME, &ts);
  struct tm tm;
  localtime_r(&ts.tv_sec, &tm);
  snprintf(capture.run_id,
           sizeof(capture.run_id),
           "%04d%02d%02d_%02d%02d%02d_%06ld",
           tm.tm_year + 1900,
           tm.tm_mon + 1,
           tm.tm_mday,
           tm.tm_hour,
           tm.tm_min,
           tm.tm_sec,
           ts.tv_nsec / 1000);
}

bool debug_capture_configure(debug_capture_role_t role, int max_srs_events, int start_sfn, int start_slot)
{
  if (capture.configured)
    return capture.active;
  if (max_srs_events < 2)
    return false;

  if (start_sfn < 0) {
    const char *value = getenv("OAI_DEBUG_CAPTURE_START_SFN");
    if (value && value[0])
      start_sfn = atoi(value);
  }
  if (start_slot < 0) {
    const char *value = getenv("OAI_DEBUG_CAPTURE_START_SLOT");
    if (value && value[0])
      start_slot = atoi(value);
  }

  capture.configured = true;
  capture.role = role;
  capture.max_events = max_srs_events;
  capture.start_sfn = start_sfn;
  capture.start_slot = start_slot;
  capture.start_reached = start_sfn < 0 || start_slot < 0;
  set_run_id();

  const char *root = getenv("OAI_DEBUG_CAPTURE_DIR");
  if (!root || !root[0])
    root = "/tmp/oai_debug_capture";

  const char *role_name = role == DEBUG_CAPTURE_ROLE_GNB ? "gnb" : "ue";
  snprintf(capture.directory, sizeof(capture.directory), "%s/%s/%s", root, capture.run_id, role_name);
  snprintf(capture.capture_path, sizeof(capture.capture_path), "%s/capture.bin", capture.directory);
  snprintf(capture.manifest_path, sizeof(capture.manifest_path), "%s/manifest.json", capture.directory);

  if (!make_directory(capture.directory)) {
    LOG_E(PHY, "debug_capture: cannot create %s: %s\n", capture.directory, strerror(errno));
    capture.configured = false;
    return false;
  }

  capture.capture = fopen(capture.capture_path, "wb");
  if (!capture.capture) {
    LOG_E(PHY, "debug_capture: cannot open %s: %s\n", capture.capture_path, strerror(errno));
    capture.configured = false;
    return false;
  }

  capture.active = true;
  LOG_I(PHY,
        "debug_capture: role=%s events=%d start=%d.%d output=%s\n",
        role_name,
        max_srs_events,
        start_sfn,
        start_slot,
        capture.directory);
  return true;
}

bool debug_capture_is_active(void)
{
  return capture.configured && capture.active;
}

static bool start_condition_met(uint32_t frame, uint32_t slot)
{
  if (capture.start_reached)
    return true;
  if ((int)frame == capture.start_sfn && (int)slot >= capture.start_slot) {
    capture.start_reached = true;
    return true;
  }
  return false;
}

bool debug_capture_srs_event(uint32_t frame, uint32_t slot, uint16_t rnti)
{
  if (!debug_capture_is_active())
    return false;
  if (!start_condition_met(frame, slot))
    return false;
  if (capture.event_count >= capture.max_events)
    return false;

  capture.event_count++;
  capture.event_id = (uint32_t)capture.event_count;
  capture.rnti = rnti;
  if (capture.event_count == 1) {
    capture.first_frame = frame;
    capture.first_slot = slot;
  }
  capture.last_frame = frame;
  capture.last_slot = slot;
  return true;
}

void debug_capture_end_srs_event(void)
{
  if (!debug_capture_is_active())
    return;
  if (capture.event_count >= capture.max_events)
    debug_capture_close();
}

bool debug_capture_dmrs_active(uint32_t frame, uint32_t slot, uint16_t rnti)
{
  (void)frame;
  (void)slot;
  return debug_capture_is_active() &&
         capture.event_count > 0 &&
         capture.event_count <= capture.max_events &&
         rnti == capture.rnti;
}

uint32_t debug_capture_current_event_id(void)
{
  return capture.event_id;
}

void debug_capture_write(debug_capture_kind_t kind,
                         uint32_t event_id,
                         const debug_capture_meta_t *meta,
                         const c16_t *data)
{
  if (!debug_capture_is_active() || !capture.capture || !meta || !data)
    return;
  if (!meta->rows || !meta->cols)
    return;

  const uint64_t samples = (uint64_t)meta->rows * meta->cols;
  if (samples > UINT32_MAX / sizeof(c16_t))
    return;

  debug_capture_record_header_t hdr = {
      .magic = DEBUG_CAPTURE_MAGIC,
      .version = DEBUG_CAPTURE_VERSION,
      .kind = (uint16_t)kind,
      .payload_bytes = (uint32_t)(samples * sizeof(c16_t)),
      .sequence = capture.sequence++,
      .event_id = event_id,
      .frame = meta->frame,
      .slot = meta->slot,
      .timestamp_ns = (uint64_t)time(NULL) * 1000000000ULL,
      .rnti = meta->rnti,
      .rx = meta->rx,
      .port = meta->port,
      .layer = meta->layer,
      .symbol = meta->symbol,
      .rows = meta->rows,
      .cols = meta->cols,
      .fft_size = meta->fft_size,
      .n_rb = meta->n_rb,
      .start_rb = meta->start_rb,
      .bwp_start = meta->bwp_start,
      .subcarrier_spacing = meta->subcarrier_spacing,
      .snr_db_x10 = meta->snr_db_x10,
      .flags = meta->flags,
  };

  pthread_mutex_lock(&capture.mutex);
  fwrite(&hdr, sizeof(hdr), 1, capture.capture);
  fwrite(data, sizeof(c16_t), (size_t)samples, capture.capture);
  pthread_mutex_unlock(&capture.mutex);
}

void debug_capture_close(void)
{
  if (!capture.configured || capture.closed)
    return;

  pthread_mutex_lock(&capture.mutex);
  if (capture.capture) {
    fflush(capture.capture);
    long bytes = ftell(capture.capture);
    fclose(capture.capture);
    capture.capture = NULL;

    FILE *manifest = fopen(capture.manifest_path, "w");
    if (manifest) {
      fprintf(manifest,
              "{\n"
              "  \"format_version\": %d,\n"
              "  \"role\": \"%s\",\n"
              "  \"run_id\": \"%s\",\n"
              "  \"events_requested\": %d,\n"
              "  \"events_recorded\": %d,\n"
              "  \"rnti\": %u,\n"
              "  \"first_frame\": %u,\n"
              "  \"first_slot\": %u,\n"
              "  \"last_frame\": %u,\n"
              "  \"last_slot\": %u,\n"
              "  \"capture_file\": \"capture.bin\",\n"
              "  \"capture_bytes\": %ld\n"
              "}\n",
              DEBUG_CAPTURE_VERSION,
              capture.role == DEBUG_CAPTURE_ROLE_GNB ? "gnb" : "ue",
              capture.run_id,
              capture.max_events,
              capture.event_count,
              capture.rnti,
              capture.first_frame,
              capture.first_slot,
              capture.last_frame,
              capture.last_slot,
              bytes);
      fclose(manifest);
    }
    LOG_I(PHY,
          "debug_capture: wrote %d/%d SRS events, %ld bytes to %s\n",
          capture.event_count,
          capture.max_events,
          bytes,
          capture.capture_path);
  }
  pthread_mutex_unlock(&capture.mutex);

  capture.active = false;
  capture.closed = true;
}
