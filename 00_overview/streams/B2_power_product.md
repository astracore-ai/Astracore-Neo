# Stream B2 -- power and product mechanics: clock gating, tile power-gating hooks, telemetry counters, per-partition NoC
# priority, dual-tile lockstep, the bridges' queues and the command queue, the self-serve deadlock, the router reset

Branch `b2-power-product`. Read COMMON.md first, then `00_overview/ROADMAP.md` §1 / §4 and the B1 row's "left for B2",
`08_ip_product/neo_top_integration.md` (B1: the geometry, the bridge roles, the address map, the crossings), the RTL
`01_rtl/neo_top.sv`, `noc_axi_bridge.sv`, `top_ctrl.sv`, `mesh_ctrl.sv`, `cdc_*.sv`, `neo_tile.sv`, `tile_nic.sv`,
`noc_router.sv`, `host_if.sv`, `neo_core.sv`, `02_dv/dv/cocotb/test_neo_top.py` and the tile suites, `04_software/neo_host.c`
/ `neo_host.h`. Streams A3 (timing items in bank_bist_wrap / prog_mem / core_seq / the check PE; the pnr flow), C2 (the
vector unit on the core's drain path, INT4 at the weight-buffer load) and D2 (the fault campaign) run beside you: keep your
RTL edits to the files named per item below and say in the PR which lines of shared files (`neo_core.sv`, `tile_nic.sv`,
`noc_router.sv`, `host_if.sv`) you touched.

## The items (each with its test; one PR, or two if the chip-top half is long)

1. **Architectural clock gating.** Per block an idle condition and a `clk_en`: the core's array and feeder (`busy` low
   and no drain in flight), each requant, the NIC's receive and transmit halves (no beat in flight, FIFOs empty), the
   router's per-port output stages, the DMA pair when `prog_done`. Implement with one `neo_icg` module (a latch-based
   integrated-clock-gating cell in behavioural form -- enable sampled while the clock is low, no glitch -- that the
   synthesis flow maps to the library's ICG cell: ASAP7 has `ICGx*` cells; add the mapping to `synth.py`'s flow (a
   `dfflibmap`-style substitution or a `techmap` of `neo_icg` onto the cell) and a gate in the report: the number of ICG
   cells and the clock-gated flop fraction per unit). Every simulator must agree (Verilator with `--timing`, Icarus,
   neosim: a latch-based ICG is not in neosim's subset -- give `neo_icg` a `SIM_MODEL` parameter or an `ifdef` that neosim
   and the testbenches take as an AND gate with the enable registered on the falling edge, and say so in the header).
   Measure: in the cocotb tile run (M-series) and the core run, the fraction of cycles each gated block is off (a counter
   per `clk_en` in the testbench or the telemetry of item 3), reported per test. The suites' results and end times are
   unchanged (gating is invisible functionally; a test that fails is a wrong idle condition).
2. **Tile power-gating hooks.** `tile_pwr_en` / `iso_en` per tile from the control plane (B1's tile-enable map becomes the
   power map: a fused or sleeping tile is isolated at its boundary -- isolation cells as a `neo_iso` module clamping the
   tile's outputs to safe values while `iso_en` -- and its clock gated); the sequencing (iso on, clock off, power off;
   power on, reset, clock on, iso off) in `top_ctrl` with a state per tile readable by the host; retention is a plan, not
   RTL: write what is retained (nothing -- a tile re-loads its program and tables) in the integration note. The router of
   an isolated tile keeps forwarding: give `neo_tile` the separate `router_rst_n` B1 asked for, so a fused tile's router
   runs while the tile is in reset / off. Test: `top` suite T6 -- a tile isolated and clocked off while the other three run
   a layer through its router position, then powered on, reset and re-used.
3. **Telemetry counters per tile** behind the host registers (read-only 32-bit saturating counters with a snapshot /
   clear register, in `host_if.sv`'s map at a fixed offset): array valid cycles (MAC utilization), feeder stall cycles,
   fetch-wait cycles (the DMA waiting on a FETCH), drain cycles, per-port link stall cycles (router output valid & !ready),
   bank-port busy cycles, bridge-side wait cycles on the bridge nodes. Validate each against the simulators' own counts in
   the cocotb runs (the testbench counts the same events from the ports; equal within the snapshot's cycle). Mirror the map
   in `neo_host.h` and the `neo_host.c` reader; the perf model (`neo_perf_v3.py`) gains an input to compare its predicted
   utilization with a measured counter for M18 / M19-class runs (one table in the report).
4. **Per-partition NoC priority.** The flit head's seq / partition field carries a partition; the router's one-hot
   arbitration gets two priority classes (a per-partition priority bit in a `host_if` / `top_ctrl` register, defaulting to
   equal): a high-class request wins over low-class ones; round-robin within a class; a low-class request waits at most N
   grants (N a parameter, default 8) before it is promoted for one grant -- the bound a mixed-criticality integrator asks
   for. The formal harness `02_dv/formal/noc_router_fv.sv` has a fairness property: extend it to the two classes (the
   bound N) and keep the full proof; neosim `run_noc.py` N4: two partitions contending, the high class's latency and the
   low class's bound measured. The grant order with equal priority is unchanged (N3 against the 0.40 router still passes).
5. **Dual-tile lockstep.** A `lockstep_pair` register per pair in `top_ctrl` (partner tile index, enable); each tile's NIC
   exports its last drain message's CRC and a message count (`drain_crc_last`, `drain_msg_cnt`, new status ports of
   `neo_tile` through `tile_mesh` / `neo_top`); when enabled, the control plane compares the two tiles' (count, CRC) pairs
   as they advance and raises a sticky `lockstep_err` cause in the error vector on a mismatch (the comparison in the mesh
   clock domain, the sticky crossed to the AXI side as the other causes). Test: `top` T7 -- two tiles run the same
   descriptor on the same data (the compiler emits the pair's programs: same inputs, outputs to separate banks), no error;
   then one tile with a fault hook asserted -> `lockstep_err`.
6. **The bridges' queues and the command queue (B1's leftovers).** The memory bridge: several AXI bursts in flight
   (a request queue of depth 4 per direction, AXI IDs per outstanding burst, responses reordered if the slave reorders --
   or in-order IDs and a note), read requests queued at the mesh side instead of blocking the input; write combining of a
   drain's beats into bursts of up to 4 KB. The command queue in `top_ctrl`: a small ring of commands (tile mask +
   descriptor-register image address in a bank + a doorbell) that the control plane plays into the tiles' register
   windows without the host in the loop, completion records (tile, done time from the global timer) in a readable ring,
   an interrupt per completion. Tests: `top` T3b with the deeper queue (throughput measured against B1's one-in-flight),
   T8 a two-layer program issued through the command queue.
7. **The self-serve deadlock** (B1's finding): a tile that computes while serving its own bank can deadlock -- a
   contributor's read request waits at the tile's input in the router port its own response must return through. Fix it
   where it belongs: requests from a tile to its own bank never enter the mesh (a loopback at the NIC: the command port's
   requests with (x, y) == (my_x, my_y) are served by the NIC's serve path directly, responses returned on the local
   beat path), and the router's local port keeps one entry reserved for responses (or responses and requests use separate
   virtual channels on the local port -- say which and why). Test: neosim / cocotb M22 -- the placement B1's suite avoided
   (a tile fetching from its own bank while serving a contributor) completes; mesh8 unchanged.

## Verification and report

Local: `make lint` 0 warnings (the chip-top passes included); `make verilator`; neosim `run_noc.py` (N1–N4), `run_nic.py`,
`run_tile_test.py M3`; cocotb `tile` and `top` (new T6–T8); `make formal` with the extended router harness (a full
proof); unit synthesis of `noc_router`, `tile_nic`, `host_if`, `noc_axi_bridge`, `top_ctrl` before / after with the ICG
count. The PR (`gh pr create` or `gh api -X POST repos/astracore-ai/Astracore-Neo/pulls -f title=... -f head=b2-power-product
-f base=main -f body=...`), green on every job. Report: per item what was built and its test, the gated fractions per
block per test, the telemetry validation table and the model-vs-measured utilization, the priority bound measured, the
lockstep test, the bridge throughput before / after, the deadlock fix and its test, the synthesis deltas, the end times
with their accounting, anything unresolved, the PR number(s).
