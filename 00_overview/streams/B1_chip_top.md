# Stream B1 -- the chip top: `neo_top` with NoC-to-AXI4 bridges, an AXI4-Lite control plane, tile-enable, error and
# interrupt aggregation, two clock domains, and a `top` cocotb suite in CI

Branch `b1-chip-top`. Read COMMON.md first, then `00_overview/ROADMAP.md` §4 (the chip-top design as planned) and §1 (B1's
row), spec `00_overview/spec_sheet_v2_1.md` §5 (the host registers, `host_if.sv`) and §6 (the mesh, the NIC protocol), and
the RTL: `01_rtl/tile_mesh.sv` (the mesh: edges terminated with `i_valid = 0`, `o_ready = 1`; per-tile `h_*` register buses
and status flags exposed as arrays), `01_rtl/neo_tile_axil.sv` + `01_rtl/host_axil.sv` (one tile behind AXI4-Lite: the 10-bit
window, two-cycle transactions), `01_rtl/host_if.sv` (the register map), `01_rtl/tile_nic.sv` (the request / response
protocol a bridge must speak: RDREQ -> RDRSP beats, WRHDR + WRDATA beats + CRC, the CRC check, `fetch_timeout`, the RDY /
PSUM types a bridge never uses), `01_rtl/link_pack.sv` (beats of up to 16 words on the tile side, flits of up to 32 words on
the link), `01_rtl/noc_router.sv` (XY routing: X first, then Y; the flit head `parity | dst_y | dst_x | src_y | src_x | seq`),
`01_rtl/tile_dma.sv` (the instruction word: 4-bit x / y coordinate fields, a 20-bit address, 12-bit length -- a bridge is
addressed exactly like a tile's bank), `02_dv/dv/cocotb/` (the tile and mesh suites and their Makefile; `test_tile_mesh.py` /
the mesh8 build show how a mesh is built and driven), `03_models/compiler/neo_backend.py` (how programs name tiles and
addresses), `04_software/neo_host.c` (the host driver over the register bus).

New files: `01_rtl/neo_top.sv`, `01_rtl/noc_axi_bridge.sv` (and helpers: `cdc_fifo.sv`, `cdc_sync.sv`, `top_ctrl.sv` or
whatever split reads best -- one module per file, numbered-folder conventions), `02_dv/dv/cocotb/test_neo_top.py` (+ the
wrapper `neo_top_cocotb.sv` if the ports need packing like `tile_mesh_cocotb.sv`), a `top` target in the cocotb Makefile and
a step in the hdl-sim job (BOTH `07_ci/regress.yml` and `.github/workflows/regress.yml`, byte-identical), the integration
note `08_ip_product/neo_top_integration.md` (clocks, resets, address map, bridge protocol, CDCs, what the licensee connects).
You may extend `04_software/neo_host.c` / `neo_backend.py` with the top's address map (a tile's registers at `tile_base +
1 KB * node`) if the suite needs it; do not change the tile's RTL (`neo_tile.sv` and below) -- if the top needs something
from a tile, say so in the report rather than editing it.

## The design (ROADMAP §4, made concrete)

1. **Mesh geometry.** `neo_top` instantiates the NX x NY tile mesh plus one extra column of *bridge nodes* at x = NX: a
   `noc_router` with a bridge on its local port and no tile, one per row (so NY bridge positions; silicon 8 x 8 tiles + 8
   bridge nodes; coordinates need XW = $clog2(NX + 1) = 4 for NX = 8 -- the DMA instruction already carries 4-bit
   coordinates, the flit head grows by the extra coordinate bits, nothing else in the tile depends on XW = 3). XY routing
   reaches a bridge as it reaches a tile: a FETCH from bank (8, y) routes east along row y and leaves tile (7, y)'s east port
   into the bridge node; the response comes back the same way. Rows whose bridge position is unused get a bridge node with
   its AXI side tied off (requests to it time out like a dead tile -- `fetch_timeout` -- state that). Build `tile_mesh`'s
   wiring inside `neo_top` (or add a parameter to `tile_mesh` that appends the column; the mesh8 build must stay bit-identical
   when the column is absent -- its end time 117,755.01 ns + whatever A1's merge made it is a gate).
2. **The bridge (`noc_axi_bridge`)**, two roles in one module, each role optional by parameter:
   - *Memory role (AXI4 master):* the mesh side is a tile bank of 2^20 rows of 16 words as the DMA sees it; RDREQ (addr, len)
     becomes AXI4 read bursts (data width `AXI_DW`, default 512 -- a parameter; 1024-bit rows are assembled from AXI_DW beats)
     at `win_base + addr * 64` (a 64-bit `win_base` register per bridge in the control plane, 40-bit AXI addresses), the
     rows return as RDRSP beats with the sequence / tag the NIC expects; WRHDR + WRDATA + CRC become AXI4 write bursts after
     the CRC checks (a bad CRC drops the burst and raises the bridge's `crc_err`, exactly the NIC's behaviour). Outstanding
     requests: one burst in flight per direction is enough for B1 (state the throughput: a 16-word row per 1024 / AXI_DW
     AXI cycles); a request queue is B2's.
   - *Host role (AXI4 slave):* AXI4 writes to the bridge's slave window (address -> tile (x, y) and bank row: a window of
     NX x NY x bank size, the tile index in the upper bits) become WRHDR + WRDATA + CRC to that tile; AXI4 reads become RDREQ
     with the RDRSP beats returned as R data (the slave holds the read until the beats arrive; a `fetch_timeout` on the
     bridge's side returns SLVERR). The host fills banks and reads results back through this role.
   - The mesh side of either role is a `link_packer` / `link_unpacker` pair into the bridge node's router local port, the
     same modules the NIC uses; the bridge's beats are 16 words.
3. **The control plane (`top_ctrl`)**: one AXI4-Lite slave (32-bit data; address width a parameter) with: the tile register
   windows (`0x0_0000 + 1 KB * node` -> that tile's `h_*` bus through `host_axil`'s protocol, one outstanding transaction,
   the decode registered; the existing `host_axil` may become the per-tile leaf behind a decoder, or be generalised -- keep
   `neo_tile_axil` working as the single-tile view), then global registers at a fixed offset: ID / version, the tile-enable
   map (NX x NY bits: a disabled tile holds its `rst_n` low and its router still forwards -- the fusing map of the product
   family; default all enabled; a write takes effect only while the tile's `prog_done` is set or at reset -- state the rule),
   the bridges' `win_base` registers, interrupt status / mask / clear per cause (`done`, `err_pin`, bridge errors) with one
   `irq` output and a level `err` output to the safety island built from every tile's `err_pin` and the bridges' error
   bits, the aggregated error vector readable per tile (a 64-bit `err_pin` map and a 64-bit `done` map), a 64-bit free-running
   timer, a `scratch` register. Document the map in the integration note and mirror it in `neo_host.c`'s constants.
4. **Clocks and resets.** Two clock inputs: `clk` (the mesh and the tiles) and `aclk` (every AXI interface and the control
   plane's AXI side); `rst_n` (mesh) and `aresetn` (AXI). The crossings are exactly: the bridges' data paths (an asynchronous
   FIFO per direction, gray-code pointers, two-flop synchronisers -- `cdc_fifo.sv`), the control plane's register bus to the
   tiles (a request / acknowledge handshake synchroniser -- `cdc_sync.sv`), the status and error bits (two-flop synchronisers
   on levels; pulses stretched or captured sticky on the source side first). No other signal crosses; list each crossing in the
   integration note with its structure. The cocotb suite runs the two clocks at unrelated periods (e.g. 1.0 ns and 1.6 ns);
   also run once with `aclk == clk` to show the design does not depend on the ratio. Reset sequencing: `aresetn` released
   first (the control plane comes up, the tile-enable map readable), then `rst_n`; a tile whose enable bit is clear stays in
   reset.
5. **Parameters.** `neo_top #(NX, NY, ROWS, COLS, ... as tile_mesh, AXI_DW, N_MEM_BRIDGES, N_HOST_BRIDGES, FETCH_TIMEOUT)`;
   the silicon configuration (8 x 8, 32 x 32, 2 MB banks, WPF 32) must elaborate (lint it at those parameters -- `make lint`
   adds the top at silicon parameters) and synthesize as a unit in `synth.py` (add `neo_top`'s own logic -- the bridges, the
   control plane, the extra routers -- as units; `neo_tile` stays the black-boxed child; `07_ci/synth/synth.py` is A2's
   file this week: coordinate by keeping your change to a new UNITS entry and a parameter set, and say so in the PR). The
   cocotb suite uses 2 x 2 tiles of 16 x 8 cores (+ 2 bridge nodes) -- a build the runner finishes in minutes.

## The `top` cocotb suite (`test_neo_top.py`), in hdl-sim

- T1 registers: ID / version read through the control plane; every tile's `host_if` registers reachable through its window
  (write / read-back of a scratch-like register in each of the four tiles; the single-tile `neo_tile_axil` suite's checks
  reused); the timer counts; the tile-enable map read-back.
- T2 fill and read back: the host writes a channel tile and a weight tile into tile (0,0)'s bank through the host bridge,
  reads them back through the same bridge, bit-exact (CRC counted clean on the tile).
- T3 a layer across tiles: the compiler's program for a small convolution split over the four tiles (M6 / M18-class) with the
  activations and weights filled through the host bridge, started through the register windows, results drained to a bank
  and read back through the bridge -- bit-exact against the model; the same with the weights FETCHed by the tiles from the
  *memory* bridge (an AXI4 slave memory model in the testbench behind the bridge's master port).
- T4 errors: a tile's fault hook (an existing one through its registers: table parity or a bank double-bit error) raises its
  `err_pin`; the aggregated vector shows the tile, `irq` fires when unmasked, clears on write; a request to an unused bridge
  position times out and sets `fetch_timeout` on the requesting tile.
- T5 tile-enable: a disabled tile neither answers its registers nor its bank (requests time out), the others run T3's layer
  on the remaining tiles with the compiler's map excluding it (if the backend cannot exclude a tile yet, run a two-tile program
  and leave the excluded tile idle -- say which).
- The suite's build and run time on the runner (target < 15 min); add it to the hdl-sim job after the tile suites; keep the
  other suites' results and end times unchanged.

## Verification and report

Local: `make lint` at 0 warnings with the top in the lint set at silicon parameters; the `top` suite under cocotb (16 x 8,
2 x 2; your flat layout under /home/claude/work/flat_b1-chip-top); `make verilator` unchanged; one unit synthesis of the
bridge and the control plane (`--blocks noc_axi_bridge,top_ctrl` after you add them to `synth.py`'s UNITS -- report cells,
area, min period, the worst path; the bridge's CRC and packer paths should be at the NIC's level after A1). Then the PR
(`gh pr create` or `gh api -X POST repos/astracore-ai/Astracore-Neo/pulls`; `gh pr list` is blocked -- use the API; runs via
`gh run list --branch b1-chip-top`), green on every job including `formal` (your RTL adds no harness, but the job must stay
green). Report: the design as built (geometry, bridge roles, the address map, the crossings), the suite's tests and times,
the synthesis rows, what you left for B2 (the request queue, the command queue / doorbells, per-partition priority), the
integration note's path, the PR number.
