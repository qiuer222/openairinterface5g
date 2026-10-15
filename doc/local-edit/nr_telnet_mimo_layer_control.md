# Runtime NR MIMO Layer Control through telnetsrv

## 1. Purpose

This document describes how to change the NR uplink and downlink transmission
rank at runtime through the OAI telnet server.

The implementation uses two independent control points:

| Direction | Process | Control point | Telnet command |
|---|---|---|---|
| UL | `nr-softmodem` | gNB SRS RI, before TPMI and PUSCH scheduling | `ci set_layers ul <1..4\|auto>` |
| DL | `nr-uesoftmodem` | UE CSI-RS RI, before PMI/CQI report generation | `ciUE set_layers dl <1..4\|auto>` |

The override is process-wide and runtime-only. It is not written to the
configuration file and does not change the RRC MIMO configuration.

## 2. Data Flow

### 2.1 Uplink

1. The gNB receives an SRS report.
2. `nr_srs_ri_computation()` calculates the measured SRS rank.
3. If a UL override is active, the gNB replaces the zero-based `ul_ri` with
   the effective override value.
4. `nr_srs_tpmi_estimation()` calculates a TPMI using the selected rank.
5. The PUSCH scheduler uses `srs_feedback.ul_ri` for subsequent new
   transmissions.

The override is protected by `gNB_MAC_INST::sched_lock`.

### 2.2 Downlink

1. The UE receives CSI-RS.
2. `nr_csi_rs_ri_estimation()` calculates the measured rank.
3. If a DL override is active, the UE replaces `rank_indicator` before PMI and
   CQI calculation.
4. PMI and CQI are calculated using the overridden rank.
5. The RI, PMI, and CQI are encoded in the CSI report.
6. The gNB decodes the report and uses the reported RI when scheduling PDSCH.

The UE override is stored in `nr_csi_info_t` and accessed atomically because
the telnet thread and PHY thread run independently.

## 3. Build

Build the gNB, nrUE, and telnet shared libraries:

```bash
cd /path/to/openairinterface5g

./cmake_targets/build_oai \
  -d codex_build --ninja \
  --gNB --nrUE -w SIMU \
  --build-lib telnetsrv
```

The following files should be present in
`cmake_targets/codex_build/build`:

```text
nr-softmodem
nr-uesoftmodem
libtelnetsrv.so
libtelnetsrv_ci.so
libtelnetsrv_ciUE.so
```

## 4. Start the Modems

Use separate terminals for the gNB, nrUE, and telnet commands. If both
interactive telnet sessions must stay open at the same time, use four
terminals.

### 4.1 gNB

Run from the repository root:

```bash
./cmake_targets/codex_build/build/nr-softmodem \
  -O targets/PROJECTS/GENERIC-NR-5GC/CONF/gnb.sa.band78.fr1.106PRB.2x2.usrpn300.conf \
  --do-ra --rfsim \
  --rfsimulator.[0].serveraddr server \
  --uecap_file targets/PROJECTS/GENERIC-NR-5GC/CONF/uecap_ports2.xml \
  --gNBs.[0].min_rxtxtime 3 \
  --telnetsrv --telnetsrv.shrmod ci \
  --telnetsrv.listenport 19090 \
  --log_config.global_log_options level,nocolor,time
```

The gNB telnet endpoint is `127.0.0.1:19090`.

### 4.2 nrUE

Run in another terminal:

```bash
./cmake_targets/codex_build/build/nr-uesoftmodem \
  -O ci-scripts/conf_files/nrue.uicc.conf \
  -r 106 --numerology 1 --band 78 \
  -C 3319680000 --ssb 516 \
  --do-ra --rfsim \
  --rfsimulator.[0].serveraddr 127.0.0.1 \
  --ue-nb-ant-tx 2 --ue-nb-ant-rx 2 \
  --telnetsrv --telnetsrv.shrmod ciUE \
  --telnetsrv.listenport 19091 \
  --log_config.global_log_options level,nocolor,time
```

The nrUE telnet endpoint is `127.0.0.1:19091`.

Wait until the nrUE log reports successful synchronization and random access:

```text
RA procedure succeeded
UE synchronized
```

When running as a normal user, creating the TUN interface may fail with
`Operation not permitted`. This does not prevent PHY/MAC layer tests. Use
`sudo` only if the user-plane TUN interface is required.

## 5. Connect to the Telnet Server

Use the standard telnet client:

```bash
# gNB shell
telnet 127.0.0.1 19090

# nrUE shell
telnet 127.0.0.1 19091
```

The prompts are similar to:

```text
softmodem_gnb>
softmodem_5Gue>
```

For one-shot commands, Netcat can send one line and disconnect:

```bash
printf 'ci set_layers ul 2\n' | nc -N 127.0.0.1 19090
printf 'ciUE set_layers dl 2\n' | nc -N 127.0.0.1 19091
```

`nc` is an external test utility. It is not built, installed, or required by
OAI. The telnet command is not encrypted and has no authentication, so use it
only on a trusted test network.

## 6. UL Layer Commands

Set the requested number of PUSCH layers:

```text
ci set_layers ul 1
ci set_layers ul 2
ci set_layers ul 3
ci set_layers ul 4
```

Restore the measured SRS rank:

```text
ci set_layers ul auto
```

Example output:

```text
UL layers override set to 2 (effective layers are clamped per UE)
UL layers override cleared (auto)
```

The effective UL rank is the minimum of:

1. The requested value.
2. The UE's configured PUSCH `maxRank`.
3. `pusch_AntennaPorts`.
4. The number of UE SRS ports in the report.
5. The number of gNB receive antenna elements.
6. `NR_MAX_NB_LAYERS`.

The current TPMI search supports one- and two-layer optimization. For three or
four layers, the implementation falls back to TPMI 0 when the configured
codebook permits it.

If SRS has only one port, a request for two or more layers is clamped to one.

## 7. DL Layer Commands

Set the requested number of PDSCH layers:

```text
ciUE set_layers dl 1
ciUE set_layers dl 2
ciUE set_layers dl 3
ciUE set_layers dl 4
```

Restore the measured CSI-RS rank:

```text
ciUE set_layers dl auto
```

Example output:

```text
DL layers override set to 1
DL layers override requested 4, effective 2 (clamped by UE configuration/implementation)
DL layers override cleared (auto)
```

The effective DL rank is limited by the configured PDSCH maximum rank and by
the number of configured CSI-RS ports. The override selects the requested rank
when it is not greater than `maxMIMO_Layers_PDSCH`; higher requests are clamped
to the configured maximum.

## 8. Verification

### 8.1 Verify the Commands

```bash
printf 'ci set_layers ul 1\n' | nc -N 127.0.0.1 19090
sleep 2
printf 'ci set_layers ul 2\n' | nc -N 127.0.0.1 19090
sleep 2
printf 'ci set_layers ul auto\n' | nc -N 127.0.0.1 19090
```

```bash
printf 'ciUE set_layers dl 1\n' | nc -N 127.0.0.1 19091
sleep 2
printf 'ciUE set_layers dl 2\n' | nc -N 127.0.0.1 19091
sleep 2
printf 'ciUE set_layers dl auto\n' | nc -N 127.0.0.1 19091
```

Verify clamping:

```bash
printf 'ciUE set_layers dl 4\n' | nc -N 127.0.0.1 19091
```

Expected nrUE output:

```text
DL layers override requested 4, effective 2 (clamped by UE configuration/implementation)
```

### 8.2 Verify UL Scheduling

The gNB periodic UE statistics contain the SRS rank and TPMI:

```text
UE <rnti>: UL-RI <rank>, TPMI <tpmi>
```

For debug logs, start the gNB with `--log_config.mac_log_level debug` and
search for:

```text
UL layers override
```

The PUSCH PDU log also contains `nrOfLayers`.

### 8.3 Verify DL Reports

The nrUE CSI-RS log contains:

```text
RI = <rank> i1 = ... i2 = ... SINR = ... CQI = ...
```

After `ciUE set_layers dl 1`, subsequent CSI-RS measurement logs should report
`RI = 1`. After `ciUE set_layers dl auto`, the measured value should resume.

For debug logs, start nrUE with `--log_config.phy_log_level debug` and search
for:

```text
DL layers override
```

## 9. Invalid Input Tests

The following commands must return an error without changing the active
override:

```bash
printf 'ci set_layers dl 2\n'   | nc -N 127.0.0.1 19090
printf 'ci set_layers ul 5\n'   | nc -N 127.0.0.1 19090
printf 'ciUE set_layers ul 2\n' | nc -N 127.0.0.1 19091
printf 'ciUE set_layers dl 5\n' | nc -N 127.0.0.1 19091
```

## 10. Timing and Scope

- An UL change takes effect after the next valid SRS measurement.
- A DL change takes effect after the next CSI-RS measurement and report.
- Only new transmissions use the new rank.
- HARQ retransmissions continue with the rank stored in the HARQ process.
- DCI format 0_0 and DCI format 1_0 remain single-layer.
- The setting is global within one modem process. It is not per RNTI.
- The setting is reset when the modem process restarts.

## 11. Implementation References

| Function or state | Location |
|---|---|
| `ci set_layers ul` command | `common/utils/telnetsrv/telnetsrv_ci.c` |
| `ciUE set_layers dl` command | `common/utils/telnetsrv/telnetsrv_ciUE.c` |
| gNB UL override state | `gNB_MAC_INST::ul_layers_override` |
| UL SRS rank override | `openair2/LAYER2/NR_MAC_gNB/gNB_scheduler_ulsch.c` |
| nrUE DL override state | `nr_csi_info_t::dl_layers_override` |
| DL CSI-RS rank override | `openair1/PHY/NR_UE_TRANSPORT/csi_rx.c` |

## 12. Cleanup

Stop the modems with `Ctrl+C`. Do not use `Ctrl+Z`: it suspends the process
instead of terminating it. A suspended process keeps its telnet sockets,
RFSim port, shared memory, and other resources allocated.

If a later modem startup reports:

```text
Failed to bind socket
```

the most likely cause is an old modem process that is still alive. Inspect the
listening ports and processes:

```bash
sudo ss -ltnp | grep -E ':(19090|19091|4043)\b'
pgrep -a -x nr-softmodem
pgrep -a -x nr-uesoftmodem
ps -o pid,ppid,user,lstart,etime,stat,wchan:24,cmd \
  -p "$(pgrep -d, -x nr-softmodem)" \
  -p "$(pgrep -d, -x nr-uesoftmodem)"
```

A process state containing `T` means the process is stopped or suspended. It
still owns all sockets and ports. Port 4043 is the RFSim socket, while 19090
and 19091 are the gNB and nrUE telnet sockets.

Cleanly resume and interrupt the old processes:

```bash
sudo pkill -CONT -x nr-softmodem
sudo pkill -CONT -x nr-uesoftmodem

sleep 1

sudo pkill -INT -x nr-softmodem
sudo pkill -INT -x nr-uesoftmodem

sleep 2
```

The `CONT` signal is required because an interrupted signal may remain pending
while a process is suspended. If the processes do not exit after `INT`, force
them to terminate:

```bash
sudo pkill -KILL -x nr-softmodem
sudo pkill -KILL -x nr-uesoftmodem
```

Confirm that the processes and ports are gone:

```bash
pgrep -a -x nr-softmodem
pgrep -a -x nr-uesoftmodem
sudo ss -ltnp | grep -E ':(19090|19091|4043)\b'
```

All commands should produce no output. A process shown as
`[nr-softmodem] <defunct>` or `[nr-uesoftmodem] <defunct>` is already dead and
does not own a port. It is reaped when its parent process exits.

Finally remove files generated by the test:

```bash
rm -f reconfig.raw rbconfig.raw oaitelnet.history
```
