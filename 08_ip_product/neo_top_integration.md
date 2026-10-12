# `neo_top` — integration note (drop 0.44, stream B1)

What a licensee connects when Neo is delivered as the chip top: the tile mesh with a column of bridge nodes, AXI4 bridges
to external memory and to the host, one AXI4-Lite control plane, an interrupt and an error level, two clocks and two
resets. RTL: `01_rtl/neo_top.sv` (geometry, wiring, the fences), `noc_axi_bridge.sv` (the bridge engine), `top_ctrl.sv` /
`mesh_ctrl.sv` (the control plane's two halves), `cdc_fifo.sv`, `cdc_sync.sv`, `cdc_bits.sv` (the crossings). Software
constants: `04_software/neo_host.h` (`NEO_TOP_*`). Verification: `02_dv/dv/cocotb/test_neo_top.py` (the `top` suite in the
hdl-sim job). Everything below is what the RTL does; where a choice was made it says so.

## 1. Geometry and parameters

| Parameter | Silicon (default) | Meaning |
| --- | --- | --- |
| `NX`, `NY` | 8, 8 | tiles; the mesh is (`NX` + 1) × `NY` nodes — the bridge column at x = `NX` |
| `ROWS`, `COLS`, `ACC_ROWS`, `ABUF_DEPTH`, `WBUF_DEPTH`, `BANK_DEPTH`, `WPF`, `FETCH_TIMEOUT` | 32, 32, 512, 2048, 1024, 524288, 32, 2^20 − 1 | the tile's, as `tile_mesh` passes them |
| `AXI_DW` | 512 | the bridges' AXI4 data width: 128, 256 or 512 (a bank row is 512 bits; a wider AXI beat would carry two beats — not supported) |
| `AXI_IDW` | 4 | the host bridges' AXI4 ID width (IDs are echoed) |
| `N_MEM_BRIDGES` | 8 (= `NY`) | rows 0 .. `N_MEM_BRIDGES` − 1 have a memory bridge (AXI4 master `m_axi_*` port m = row m) |
| `N_HOST_BRIDGES` | 1 | rows 0 .. `N_HOST_BRIDGES` − 1 have a host bridge (AXI4 slave `s_axi_*` port h = row h); a row may have both roles |
| `HOST_TIMEOUT` | `FETCH_TIMEOUT` | aclk cycles a host read waits for its tile (scale it when aclk is faster than clk) |
| `CTRL_AW` | 18 | the AXI4-Lite byte-address width (at least 18) |
| `CDC_DEPTH` | 8 | entries per bridge FIFO (power of two, ≥ 4; 8 covers the pointer round trip at equal clocks) |
| derived | `XW` = clog2(`NX` + 1) = 4, `YW` = 3 | coordinate widths: the DMA instruction carries 4-bit coordinates, so `NX` ≤ 15 |

- **The bridge column.** Each row's node at x = `NX` is a `noc_router` with no tile; its local port goes to the row's bridge
  (through a `link_unpacker` / `link_packer` pair, the NIC's own, and one `cdc_fifo` per direction) or, in a row with
  neither role, to a sink that consumes and drops every flit. XY routing reaches a bridge as it reaches a tile: a FETCH
  from (`NX`, y) runs east along row y and leaves tile (`NX` − 1, y)'s east port into the bridge node; the response comes
  back the same way. Traffic between rows inside the column uses the bridge routers' N / S ports. **A request to a row
  with no bridge role (or to a role the row does not have) is dropped and times out at the requester (`fetch_timeout`),
  as against a dead tile.**
- **The tiles are elaborated with their `NX` parameter = `NX` + 1.** A tile keeps per-source receive state indexed
  y · NX + x; with 8 tiles per row a bridge at x = 8 would alias tile (0, y + 1) (and bridge (8, 7) would wrap onto tile
  (0, 0)), so a fetch from a bridge could be completed by a drain from that tile. With 9, every node of the mesh has its own
  source state: 72 sources in silicon (the partial-sum leaf 72 × 1,024 bits instead of 64 × 1,024). Nothing else in the
  tile depends on `NX` or `XW`; `tile_mesh` and the mesh builds are unchanged. The chip top's tile is therefore a
  parameterization of its own (`top_neo_tile` in the synthesis units).

## 2. Clocks and resets

| Signal | Domain | Drives |
| --- | --- | --- |
| `clk` | the mesh clock | every tile, the bridge column's routers and packer / unpacker pairs, the FIFOs' mesh sides, `mesh_ctrl` |
| `aclk` | the AXI clock | every AXI interface: `top_ctrl` (AXI4-Lite), each bridge engine (AXI4 master / slave), the FIFOs' AXI sides |
| `rst_n` | clk | asynchronous assertion, deassertion synchronous to clk (the licensee's reset synchronizer, as for the tile) |
| `aresetn` | aclk | the same, synchronous to aclk |

The two clocks are unrelated (any ratio; the suite runs 1.0 / 1.6 ns and 1.0 / 1.0 ns). **Reset rule:** `rst_n` and
`aresetn` are asserted together; at power-up `aresetn` is released first — the control plane comes up and the tile-enable
map can be read and written — then `rst_n`, and the tiles leave reset with the map written in between in effect. A tile
register window accessed before `rst_n` is released waits for it (the access completes after the release). Asserting one
reset alone is not supported: the bridge FIFOs and the register handshake reset each side's pointers / toggles with its own
reset, so a one-sided reset would leave the two sides out of step (a reset controller that sequences a mesh-only reset is
an item for B2).

## 3. Ports

- **Control plane:** `s_axil_*`, AXI4-Lite slave, 32-bit data, `CTRL_AW`-bit byte addresses, no PROT. One transaction at a
  time (writes before reads); AW and W are taken together; BRESP / RRESP OKAY, SLVERR (a disabled tile), DECERR (nothing at
  the address). Write strobes: the strobed bytes are written, the others as zeros (the tile's `host_axil` rule).
- **Host bridges:** `s_axi_*` bundles, bridge h at bits `[h·W +: W]` of each port: AWID / AWADDR (`HAW` bits) / AWLEN /
  AWSIZE / AWBURST / AWVALID / AWREADY, WDATA / WSTRB / WLAST / WVALID / WREADY, BID / BRESP / BVALID / BREADY, the same for
  AR / R with RID / RDATA / RRESP / RLAST. No AxLOCK, AxCACHE, AxPROT, AxQOS, AxREGION, USER (ignore them upstream).
  `HAW` = clog2(`NX`·`NY`) + clog2(`BANK_DEPTH`) + 2 = 27 bits (128 MB) in silicon: the licensee decodes the window's base.
- **Memory bridges:** `m_axi_*` bundles, bridge m at `[m·W +: W]`: AWADDR (40 bits) / AWLEN / AWSIZE / AWBURST / AWVALID /
  AWREADY, WDATA / WSTRB / WLAST / WVALID / WREADY, BRESP / BVALID / BREADY, ARADDR / ARLEN / ARSIZE / ARBURST / ARVALID /
  ARREADY, RDATA / RRESP / RLAST / RVALID / RREADY. Tie upstream: AxID = 0 (one ID: one burst in flight per direction, in
  order), AxLOCK = 0, AxCACHE as the system's coherency needs (0011 normal non-cacheable bufferable is the usual choice),
  AxPROT, AxQOS. Bursts are INCR, AxSIZE = the full width, never across a 4 KB boundary.
- **`irq`** (aclk, level): any unmasked `IRQ_STATUS` bit. **`err`** (aclk, level, to the safety island): any enabled
  tile's `err_pin`, any bridge error bit, any bridge router's parity flag — not maskable; it falls when the causes are
  cleared (a tile's through its CTRL error clear, the bridges' through their ERR registers).

## 4. Address maps

### 4.1 Control plane (AXI4-Lite byte addresses; `NEO_TOP_*` in `neo_host.h`)

| Address | Register | |
| --- | --- | --- |
| `0x0_0000 + 0x400·n` | tile n = y·`NX` + x: its `host_if` registers, register r at + 4·r (spec §7) | through the register handshake; SLVERR, no bus cycle, for a disabled tile |
| `0x1_0000 + 0x400·b` | bridge node b (row b): `0x00` CTRL (w: bit 1 clears the router's parity flag), `0x04` STATUS (r: bit 8 the flag), others read 0 | through the handshake |
| `0x2_0000` | ID (r) = `0x4E454F54` 'NEOT' | |
| `0x2_0004` | VERSION (r) = `0x00000044` | |
| `0x2_0008` | CONFIG (r) = {COLS[31:24], ROWS[23:16], N_HOST[15:12], N_MEM[11:8], NY[7:4], NX[3:0]} | |
| `0x2_000C` | CONFIG2 (r) = {log2 BANK_DEPTH[31:24], WPF[23:16], AXI_DW[15:0]} | |
| `0x2_0010` | SCRATCH (r/w) | |
| `0x2_0018` / `0x2_001C` | TIMER_LO / TIMER_HI (r): 64-bit count of aclk cycles from `aresetn`; reading LO latches HI | |
| `0x2_0020` / `0x2_0024` | TILE_EN lo / hi (r/w): the tile-enable request, bit n = tile n; reset: every tile | §7 |
| `0x2_0028` / `0x2_002C` | TILE_EN_EFF lo / hi (r): the map in effect | §7 |
| `0x2_0030` / `0x2_0034` | DONE lo / hi (r): per tile `prog_done && !drain_busy` (the program ended, its drain left), enabled tiles | |
| `0x2_0038` / `0x2_003C` | ERR lo / hi (r): per tile `err_pin`, enabled tiles | |
| `0x2_0040` | IRQ_STATUS (r; write 1 clears): bit 0 done (a tile's DONE rose), bit 1 err (a tile's ERR rose), bit 2 bridge (a bridge error event, or a bridge router's parity flag rose) | edge-captured: clearing it while a level persists does not re-raise it |
| `0x2_0044` | IRQ_MASK (r/w): 1 masks; reset `0x7` | |
| `0x2_0048` | STATUS (r): bit 0 `irq`, bit 1 `err` | |
| `0x2_0100 + 0x20·b` | bridge b: +0x0 WIN_LO (r/w: window base bits 31:6; 5:0 read 0), +0x4 WIN_HI (bits 39:32 in 7:0), +0x8 CTRL (bits 3:0: the partition the host role's requests carry; a tile drops flits of another partition), +0xC ERR (r; write 1 clears bits 4:0): 0 crc, 1 lost, 2 axi, 3 timeout, 4 host, 5 the router's parity flag (a level) | |

Anything else answers DECERR.

### 4.2 Host bridge window (AXI4 slave)

Byte address = {tile index n (clog2(`NX`·`NY`) bits), byte offset in the bank (clog2(`BANK_DEPTH`) + 2 bits)}, n = y·`NX` + x:
a window of `NX`·`NY` banks, 64 × 2 MB = 128 MB in silicon. A tile index past the mesh (only when `NX`·`NY` is not a power of
two) answers DECERR. `NEO_TOP_HOST_ADDR(n, word, baw)`.

### 4.3 Memory bridge window (what the DMA sees)

A bridge is addressed exactly like a tile's bank: a DMA instruction names x = `NX`, y = the bridge's row, and a 20-bit
**word** address (FETCH_A / FETCH_W `len` words from `addr`; DRAIN_WR to `addr`), byte address = `win_base + 4·addr` — a
4 MB window per bridge anywhere in the 40-bit AXI space, `win_base` 64-byte aligned (not necessarily 4 KB aligned), moved by
software between layers. *(The brief read `win_base + addr · 64` with 2^20 rows of 16 words; the DMA's address is a word
address everywhere — the bank server serves word-granular requests that start inside a row, the drain's WRHDR carries a
word address, the compiler's partial activation fetches start inside rows — so a row reading would make the bridge the one
endpoint the same instruction addresses differently, and it could not serve a fetch that starts inside a row.)*

## 5. The bridge protocol

The mesh side speaks the tile interface's messages (`tile_nic.sv`, spec §5) in 16-word beats; the flit head is {parity,
dst_y, dst_x, src_y, src_x, seq = {4'd0, partition}}; every data stream ends with a CRC flit {count[31:16], CRC-16-CCITT
over the data words[15:0]}, the receiver keeping CRC and count per source.

**Memory role (AXI4 master).**
- *RDREQ (len words at addr, from any tile; one served at a time, the next waits at the bridge's input as at a tile's server).*
  AXI read bursts cover bytes [`win_base` + 4·addr, + 4·len), beat-aligned, split at 4 KB boundaries (≤ 64 beats at 512 bits),
  one burst in flight. Each R beat becomes one RDRSP beat of the requested words it carries (the first from the offset in
  its beat), sent to the requester (its coordinates from the request's hdr, its partition echoed) as R data arrives — R is
  back-pressured by the mesh, never buffered beyond the FIFO. Then the CRC flit. An R response other than OKAY sets `axi`
  in the bridge's ERR and sends the CRC inverted, so the requesting tile flags `crc_err`: the error reaches the consumer of
  the data. A zero-length request is answered by its CRC flit alone. Throughput: one AXI beat per aclk cycle within a burst
  (a 16-word row per 512 / `AXI_DW` cycles), plus the AR-to-first-R latency once per burst.
- *WRHDR + WRDATA + CRC (a tile's drain into the window).* Per source, as the NIC: each source's write pointer, CRC and word
  count in its own registers, so drains from several tiles may interleave beat by beat. **Every WRDATA beat is written as
  it arrives** — one AXI burst of the 1..5 beats its words touch, byte strobes on exactly its words, split in two at a 4 KB
  boundary — and leaves the bridge's input when its write response is back; the CRC flit then checks the source's CRC and
  count (`crc`, `lost` in ERR); a write response other than OKAY sets `axi`. Throughput: one WRDATA beat (≤ 16 words) per
  AXI write round trip (AW and W together, then B). *(The brief asked for the burst to be written after the CRC checks and
  dropped on a bad CRC. Messages from different tiles interleave at the bridge beat by beat — each (source, destination)
  stream is in order on the XY mesh, sources interleave, which is why the NIC keeps per-source state — so holding a message
  until its CRC would take a buffer per source of the longest drain (64 × 512 rows of 33 words in silicon), or a rule that
  one tile drains into a bridge at a time, which neither the DMA nor the compiler can enforce (there is no flow control for
  WRDATA; RDY exists for partial sums only); and holding a second writer at the input stalls the router port the first
  writer's remaining beats arrive through — a deadlock. The bridge does what the NIC does: the data is written as it
  arrives and the CRC flags the error, it does not undo it.)*

**Host role (AXI4 slave).** One transaction at a time, writes before reads; INCR bursts of full-width beats (others SLVERR).
- *Write.* WRHDR (the first written word) + one WRDATA beat per AXI beat — its strobed words: whole words, one contiguous
  run per beat (leading / trailing words of a beat may be off; a run that does not continue the previous beat's starts a new
  WRHDR, which the tile's receiver takes inside the same message) + the CRC flit. A beat with a partial-word strobe or two
  runs is not written and the burst answers SLVERR. **BRESP is posted**: it is returned when the CRC flit has entered the
  bridge's FIFO toward the mesh. A read through the same bridge to the same tile follows the same XY route and is served
  after the writes — the fence a host uses before starting a tile on data it just wrote (`test_neo_top.py` does).
- *Read.* One RDREQ for the burst's beats (whole beats from the beat-aligned start, ≤ 4,095 words: any legal burst), the
  RDRSP beats assembled into R beats as they arrive (a 32-word stage; the host must accept R data — a held RREADY back-
  pressures the bridge's whole input). The last R beat waits for the CRC flit and carries its verdict: OKAY, or SLVERR when
  the CRC, the count or the length disagree. No complete response within `HOST_TIMEOUT` aclk cycles (a disabled or dead
  tile): the remaining beats are SLVERR and `timeout` is set in ERR; late beats of that response are dropped.

**Ordering and deadlock.** The bridge sends one message at a time (round-robin between its roles at message boundaries); an
RDREQ waits at the bridge's input while the memory role serves, exactly as at a tile's bank server, so the bridge inherits the
tile's head-of-line blocking (an RDREQ behind a busy server holds the input). A request queue removes it (B2).

## 6. Clock-domain crossings — exactly these, nothing else crosses

| # | Crossing | Structure | Constraint for the implementation flow |
| --- | --- | --- | --- |
| 1 | every bridge's data path, mesh → AXI and AXI → mesh | `cdc_fifo` per direction: `CDC_DEPTH` beats of 2·(`XW` + `YW`) + 552 bits (566 in silicon; the flit without its parity bit) in flops, binary + gray pointers, the gray pointer synchronized by `cdc_bits` (two flops) into the other clock; full / empty from the synchronized copies | `set_max_delay -datapath_only` ≤ one source period from each gray pointer register to its first synchronizer flop (bounds the skew between the code's bits); ≤ one destination period from `mem` to the read side's consumers (`u_rxq/mem → u_br/*`, `u_txq/mem → u_pack/*`) |
| 2 | the register bus to the tiles | `cdc_sync`: two-phase request / acknowledge toggles, each synchronized by two flops; the request payload `a_hold` held from before its toggle until the acknowledge, the response `b_hold` likewise | `set_max_delay -datapath_only` ≤ one destination period on `a_hold → mesh_ctrl` and `b_hold → a_rsp_data` (multicycle by protocol: the data is stable two edges before it is sampled) |
| 3 | levels | `cdc_bits` (two flops per bit): into aclk the tiles' `done` (`prog_done && !drain_busy`) and `err_pin` — each registered in clk and masked by the enable map first — the effective enable map, the bridge routers' parity flags; into clk the tile-enable request (no reset on this one: it holds the request through the mesh reset) | `set_false_path` (or `set_max_delay -datapath_only` one period) to every `sync1` flop |

Clock groups: `set_clock_groups -asynchronous -group clk -group aclk` after the max-delay exceptions above, or the
exceptions alone. Not crossings: the bridges' window bases, partitions and error events (aclk modules talking to aclk
modules); the tiles' status flags (`crc_err`, `fetch_timeout`, …) stay in clk and are read through the register windows.
The CDC structures are deliberately few: an open-source CDC lint is limited, so each is a recognizable module.

## 7. Tile enable (the fusing map)

`TILE_EN` is a request; `mesh_ctrl` applies bit n while the mesh is in reset, or while tile n is idle — not started since
reset (no CTRL write with bit 0 through the window), or its program has ended (`prog_done` rose after its start) — and holds
it pending otherwise, so a tile is never cut off in the middle of a run; `TILE_EN_EFF` reads what is in effect. A disabled
tile is **fenced**, not reset: on each of its four mesh inputs a flit addressed to it is consumed and dropped before the tile
sees it (so its bank does not answer — requests to it time out at the requester; a host read returns SLVERR after
`HOST_TIMEOUT`; host writes to it are dropped), every other flit passes (its router forwards the XY traffic through its
position); its register window answers SLVERR; its `done` and `err_pin` are masked out of the DONE / ERR maps, the
interrupt and `err`. Default: every tile enabled. The compiler takes the same map as its exclusion list
(`compile_network(exclude=...)`, `compile_layer(..., exclude=...)`).

*Why not held in reset (the brief's wording):* the router is inside the tile on the tile's `rst_n`, and a router held in
reset takes every flit it is offered (`in_ready = !occ[1] = 1`) and drops it — a tile in reset would black-hole every XY
route through its position. Holding a fused tile in reset needs the tile's router on a reset of its own (a `rtr_rst_n` port
on `neo_tile`): a tile change, left to the tile's owner; with it, the fence and the reset can coexist (the fence keeps
requests from reaching the reset tile's local port).

## 8. Errors and interrupts

| Source | Where it shows | Clear |
| --- | --- | --- |
| a tile's causes (spec §7: parity, CRC, ABFT, ECC, timeouts, watchdog, …) | the tile's ERR_CAUSE (its window), `err_pin` → ERR map, `err`, IRQ_STATUS.err on its rise | the tile's CTRL bit 1 |
| a bridge's received stream fails its CRC / count | bridge ERR crc / lost → `err`, IRQ_STATUS.bridge | write 1 to the bit |
| a memory-role AXI response other than OKAY | bridge ERR axi; on reads also the requester's `crc_err` (the inverted CRC) | write 1 |
| a host read timed out | bridge ERR timeout; the burst's remaining beats SLVERR | write 1 |
| a host transaction answered SLVERR / DECERR | bridge ERR host | write 1 |
| a bridge router dropped a flit with bad parity | bridge ERR parity (level) → `err`, IRQ_STATUS.bridge | the bridge node's CTRL bit 1 |
| a tile's program ended | DONE map, IRQ_STATUS.done | IRQ_STATUS write 1 |

## 9. What the licensee connects, in order

1. `clk`, `rst_n` and `aclk`, `aresetn` with synchronous deassertion each; release `aresetn` first.
2. The control plane on the system's register interconnect; program `TILE_EN` (the fusing map — from the licensee's fuse
   block or firmware) and the memory bridges' `WIN_LO` / `WIN_HI` before releasing `rst_n`.
3. Each memory bridge's AXI4 master to a memory-controller port (LPDDR), with the tie-offs of §3; each host bridge's
   AXI4 slave into the host's address map (PCIe / the CPU cluster), its 27-bit window decoded upstream.
4. `irq` to the interrupt controller, `err` to the safety island (with the tiles' own `err_pin` causes readable through the
   windows).
5. Software: `neo_host.h`'s `NEO_TOP_*` map; a tile's registers at `NEO_TOP_TILE_REG(y·NX + x, reg)`, the existing driver's
   `neo_load_and_start` / `neo_wait_done` over a `neo_wr_fn` / `neo_rd_fn` that write those addresses; banks filled and read
   through the host bridge (`NEO_TOP_HOST_ADDR`) with a read-back fence before starting tiles on the data.

## 10. Left for B2

The request queue in the bridges (several bursts in flight per direction, AXI IDs, RDREQs queued instead of blocking the
input), write combining / store-and-forward of memory-role writes, the command queue with doorbells and completion records
in the control plane, per-partition NoC priority, a reset controller (a mesh-only reset; the island → control plane → tiles
sequence with the island's reset), clock gating and the tile's router reset for fused tiles.
