# `neo_top` — integration note (drop 0.42, stream B1; drop 0.43, stream B2)

What a licensee connects when Neo is delivered as the chip top: the tile mesh with a column of bridge nodes, AXI4 bridges
to external memory and to the host, one AXI4-Lite control plane, an interrupt and an error level, two clocks and two
resets. RTL: `01_rtl/neo_top.sv` (geometry, wiring, the fences), `noc_axi_bridge.sv` (the bridge engine), `top_ctrl.sv` /
`mesh_ctrl.sv` (the control plane's two halves), `cdc_fifo.sv`, `cdc_sync.sv`, `cdc_bits.sv` (the crossings). Software
constants: `04_software/neo_host.h` (`NEO_TOP_*`). Verification: `02_dv/dv/cocotb/test_neo_top.py` (the `top` suite in the
hdl-sim job). Everything below is what the RTL does; where a choice was made it says so.

Drop 0.43 (stream B2) added: the tile-enable map as the power map, with per-tile isolation, clock gating, reset and a power
switch enable, sequenced by the control plane (§7); dual-tile lockstep (§8); the memory bridges' request queue, posted and
combined writes (§5); the command queue (§4.4); the telemetry counters — the tiles' in their `host_if` windows, the bridges'
wait cycles in the control plane (§4.1); the routers' two priority classes per partition (§4.1); the tiles' clock gates
(invisible here: they change no behaviour at the ports).

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
| `LS_PAIRS` | 4 | lockstep pairs the control plane can compare at once (1 .. 8; §8) |
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
still open, §10; what drop 0.43 added is a reset per tile, under the power sequencing of §7, with the tile's router on
`rst_n`).

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
  ARREADY, RDATA / RRESP / RLAST / RVALID / RREADY. Tie upstream: AxID = 0 (one ID: up to four bursts in flight per
  direction since drop 0.43, which the slave returns in order — §5), AxLOCK = 0, AxCACHE as the system's coherency needs
  (0011 normal non-cacheable bufferable is the usual choice), AxPROT, AxQOS. Bursts are INCR, AxSIZE = the full width,
  never across a 4 KB boundary.
- **`irq`** (aclk, level): any unmasked `IRQ_STATUS` bit. **`err`** (aclk, level, to the safety island): any enabled
  tile's `err_pin`, any bridge error bit, any bridge router's parity flag, any lockstep pair's mismatch — not maskable; it
  falls when the causes are cleared (a tile's through its CTRL error clear, the bridges' through their ERR registers, a
  lockstep pair's by clearing its enable).
- **`tile_pwr_en[NX·NY − 1 : 0]`** (drop 0.43; a flop per tile in `mesh_ctrl`, clk): tile n's power switch enable — high
  while the tile's switchable domain is to be powered (§7). The integrator's power-intent (UPF) connects it to the domain's
  switch cells; with no switches it is left open (the sequencing still isolates, gates and resets the tile).

## 4. Address maps

### 4.1 Control plane (AXI4-Lite byte addresses; `NEO_TOP_*` in `neo_host.h`)

| Address | Register | |
| --- | --- | --- |
| `0x0_0000 + 0x400·n` | tile n = y·`NX` + x: its `host_if` registers, register r at + 4·r (spec §7) | through the register handshake; SLVERR, no bus cycle, for a disabled tile |
| `0x1_0000 + 0x400·b` | bridge node b (row b): `0x00` CTRL (w: bit 1 clears the router's parity flag), `0x04` STATUS (r: bit 8 the flag), others read 0 | through the handshake |
| `0x2_0000` | ID (r) = `0x4E454F54` 'NEOT' | |
| `0x2_0004` | VERSION (r) = `0x00000045` (drop 0.43) | |
| `0x2_0008` | CONFIG (r) = {COLS[31:24], ROWS[23:16], N_HOST[15:12], N_MEM[11:8], NY[7:4], NX[3:0]} | |
| `0x2_000C` | CONFIG2 (r) = {log2 BANK_DEPTH[31:24], WPF[23:16], AXI_DW[15:0]} | |
| `0x2_0010` | SCRATCH (r/w) | |
| `0x2_0018` / `0x2_001C` | TIMER_LO / TIMER_HI (r): 64-bit count of aclk cycles from `aresetn`; reading LO latches HI | |
| `0x2_0020` / `0x2_0024` | TILE_EN lo / hi (r/w): the tile-enable (power) request, bit n = tile n; reset: every tile | §7 |
| `0x2_0028` / `0x2_002C` | TILE_EN_EFF lo / hi (r): the tiles that are on (powered, out of reset, unisolated) | §7 |
| `0x2_0030` / `0x2_0034` | DONE lo / hi (r): per tile `prog_done && !drain_busy` (the program ended, its drain left), enabled tiles | |
| `0x2_0038` / `0x2_003C` | ERR lo / hi (r): per tile `err_pin`, enabled tiles | |
| `0x2_0040` | IRQ_STATUS (r; write 1 clears): bit 0 done (a tile's DONE rose), bit 1 err (a tile's ERR rose), bit 2 bridge (a bridge error event, or a bridge router's parity flag rose), bit 3 lockstep (a pair's mismatch rose), bit 4 command (the command queue wrote a completion record) | edge-captured: clearing it while a level persists does not re-raise it |
| `0x2_0044` | IRQ_MASK (r/w): 1 masks; reset `0x1F` | |
| `0x2_0048` | STATUS (r): bit 0 `irq`, bit 1 `err` | |
| `0x2_004C` | PRIO (r/w): the bridge column routers' priority map — bit p: partition p is the high class (§5, *Priority*) | drop 0.43 |
| `0x2_0050 + 4·k` | LS_PAIR k (r/w, k < `LS_PAIRS`): bit 31 enable, [13:8] tile b, [5:0] tile a | §8 |
| `0x2_0070` | LS_ERR (r): bit k — pair k mismatched (sticky on the mesh side; clears with the pair's enable) | §8 |
| `0x2_0080 + 4·j` | PWR_STATE j (r, j < 8): tile 8j + i's power sequencer state in bits [4i +: 3]: 0 ON, 1 ISO, 2 CLKOFF, 3 RST, 4 OFF, 5 PWRON, 6 RSTREL, 7 CLKON | §7 |
| `0x2_00A0` .. `0x2_00BC` | the command queue: CQ_CTRL, CQ_RING, CQ_TAIL (the doorbell), CQ_HEAD, CC_RING, CC_HEAD, CC_TAIL, CQ_STATUS | §4.4 |
| `0x2_00C0` | TEL_CTRL (w): bit 0 snapshot, bit 1 clear — the bridges' wait counters and the window's cycle count | drop 0.43 |
| `0x2_00C4` | TEL_CYCLES (r): the snapshot's aclk cycles | |
| `0x2_0100 + 0x20·b` | bridge b: +0x0 WIN_LO (r/w: window base bits 31:6; 5:0 read 0), +0x4 WIN_HI (bits 39:32 in 7:0), +0x8 CTRL (bits 3:0: the partition the host role's requests carry; a tile drops flits of another partition), +0xC ERR (r; write 1 clears bits 4:0): 0 crc, 1 lost, 2 axi, 3 timeout, 4 host, 5 the router's parity flag (a level); drop 0.43: +0x10 RD_WAIT / +0x14 WR_WAIT (r: snapshots — cycles a read / write burst of the memory role was outstanding with no data beat moving: the time it waited on the memory), +0x18 STATUS (r: bit 0 writes buffered or awaiting B) | |
| `0x2_1000 + 4·w` | the command memory (r/w, w < 1,024) | §4.4 |

A tile's own window (spec §7) gained in drop 0.43: `0x0C` PRIO (its router's priority map), `0x40` TEL_CTRL (w: bit 0
snapshot, bit 1 clear; r: [15:8] the number of counters, 12) and `0x41 .. 0x4C` the snapshots of its twelve 32-bit saturating
counters: cycles, array valid (MAC utilization = array valid / cycles), feeder stall, fetch wait, drain, link stall N / E /
S / W / L (a router output valid and not ready), bank read busy, bank write busy (`tile_telem.sv`; `NEO_TEL_*` and
`neo_telem_read` in `neo_host.h` / `neo_host.c`).

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

### 4.4 Command queue (drop 0.43)

Commands live in a ring in the command memory (`CQ_RING`: word address [9:0], 4-word aligned, log2 entries [19:16],
≤ 256; a ring of 2^n slots holds 2^n − 1), four words each: tile mask lo, tile mask hi, image {entries [31:16], no-wait
[15], word address [9:0]}, tag. The host writes commands, then the doorbell `CQ_TAIL` (the index past the last); with
`CQ_CTRL` bit 0 set the control plane plays them in order while the completion ring has room. Playing a command: its image's entries, two words each —
{single [14], tile [13:8], register [7:0]} and the value — are written into that `host_if` register of every tile of the
mask (of the named tile alone when single), through the same register handshake the host's window accesses use (a host
access waiting is served first; the two never overlap). The image normally ends with the CTRL start (broadcast). Then the
control plane waits until every tile of the mask is done (DONE: program ended and drained; `mesh_ctrl` drops it from the
start's bus cycle, so a start is never mistaken for its predecessor's done; a tile that is not on counts as done and its
writes as refused) or one raises `err_pin`, and until no memory bridge holds unanswered writes (the command's drains are in
memory), then writes the completion record — tag, {entries played [31:16], error [0]: a refused write or a tile's
`err_pin`}, the global timer at completion (lo, hi) — at `CC_TAIL` in the completion ring (`CC_RING`, the host advancing
`CC_HEAD`), advances `CC_TAIL` and `CQ_HEAD`, and raises IRQ_STATUS bit 4: an interrupt per completion. No-wait (image word
bit 15) completes a command without waiting (a configuration-only command). A write of `CQ_CTRL` with bit 1 resets the
queue (heads, the sticky error, a command in progress dropped). `neo_cq_image` (`neo_host.c`) and `cq_image`
(`compiler/neo_backend.py`) build a layer's image — every tile's 20 descriptor registers, cfg_m and program, then the start.
*(The brief put the images "in a bank": the control plane has no mesh port to read a bank with — the host bridge's slave is
the bank reader — so they are in the control plane's 4 KB command memory, which the host or a DMA fills as it would a bank.)*
Test: `top` T8, a two-layer program (a K-split 3×3 layer with INT8 output, its requantization tables in the image, then a
1×1 layer reading that output) from one doorbell.

## 5. The bridge protocol

The mesh side speaks the tile interface's messages (`tile_nic.sv`, spec §5) in 16-word beats; the flit head is {parity,
dst_y, dst_x, src_y, src_x, seq = {4'd0, partition}}; every data stream ends with a CRC flit {count[31:16], CRC-16-CCITT
over the data words[15:0]}, the receiver keeping CRC and count per source.

**Memory role (AXI4 master).**
- *RDREQ (len words at addr, from any tile).* Since drop 0.43 taken off the bridge's input into a **request queue of four**
  (B1 served one and held the next at the input — and every beat behind it). AXI read bursts cover bytes [`win_base` +
  4·addr, + 4·len), beat-aligned, split at 4 KB boundaries (≤ 64 beats at 512 bits); the AR engine issues them ahead
  while fewer than four bursts are outstanding — up to four bursts of up to four requests in flight — with **one AXI ID**,
  so the slave returns them in order and the responses leave in queue order. *(The brief offered IDs per burst with a
  reorder buffer, or in-order IDs and a note: this is the note — AXI orders same-ID responses, so one ID is correct with
  every slave; a slave that could reorder gains nothing from it, and the reorder buffer it would need — up to four bursts of
  64 beats in front of the RDRSP path — was not worth its area here.)* Each R beat becomes one RDRSP beat of the requested
  words it carries (the first from the offset in its beat), sent to the requester (its coordinates from the request's hdr,
  its partition echoed) as R data arrives — R is back-pressured by the mesh, never buffered beyond the FIFO. Then the CRC
  flit. An R response other than OKAY sets `axi` in the bridge's ERR and sends the CRC inverted, so the requesting tile
  flags `crc_err`: the error reaches the consumer of the data. A zero-length request is answered by its CRC flit alone. A
  request's AR waits until every write burst holding data the bridge took before the RDREQ has its write response: a read
  never overtakes an earlier drain to the window (the order B1's blocking input gave for free). Throughput: one AXI beat per
  aclk cycle within a burst, the AR-to-first-R latency hidden behind the other bursts in flight (B1 paid it per burst).
- *WRHDR + WRDATA + CRC (a tile's drain into the window).* Per source, as the NIC: each source's write pointer, CRC and word
  count in its own registers, so drains from several tiles may interleave beat by beat. **Since drop 0.43 the writes are
  posted and combined:** each WRDATA beat leaves the input for a register, a second one takes it with its AXI address
  formed (registers compared, no adder in the decision), and from there it is placed one AXI beat per cycle into the
  open burst — the continuation of the burst's last beat merges into it, the next beat of the same 4 KB page from the same
  source appends, anything else closes the burst and opens a new one; a burst also closes at its source's CRC flit, when an
  RDREQ is taken, and after 15 idle cycles. Closed bursts queue for AW (four), their beats in a 4 KB write buffer (64 AXI
  beats with word strobes, a `neo_mem_rf`), at most four awaiting B; nothing at the input waits for a write response. A
  drain of 36 INT32 rows (324 words) is two AXI bursts where B1 wrote 25 (`top` T3b). The CRC flit checks the source's CRC
  and count (`crc`, `lost` in ERR); a write response other than OKAY sets `axi`. The bridge's STATUS bit 0 (and the
  command queue) tells when every write is answered. *(The brief asked for the burst to be written after the CRC checks and
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
  runs is not written and the burst answers SLVERR. Each W beat is taken into a register with its strobes decoded and sent
  from there, one beat per cycle. **BRESP is posted**: it is returned when the CRC flit has entered the bridge's FIFO toward
  the mesh. A read through the same bridge to the same tile follows the same XY route and is served
  after the writes — the fence a host uses before starting a tile on data it just wrote (`test_neo_top.py` does).
- *Read.* One RDREQ for the burst's beats (whole beats from the beat-aligned start, ≤ 4,095 words: any legal burst), the
  RDRSP beats assembled into R beats as they arrive (a 32-word stage that takes a beat while it has room for a whole one —
  one R beat per cycle at 512 bits, where a read starts at a bank row; the host must accept R data — a held RREADY back-
  pressures the bridge's whole input). The last R beat waits for the CRC flit and carries its verdict: OKAY, or SLVERR when
  the CRC, the count or the length disagree. No complete response within `HOST_TIMEOUT` aclk cycles (a disabled or dead
  tile): the remaining beats are SLVERR and `timeout` is set in ERR; late beats of that response are dropped.

**Ordering and deadlock.** The bridge sends one message at a time (round-robin between its roles at message boundaries).
Since drop 0.43 an RDREQ no longer waits at the bridge's input behind a busy server — it queues (four deep; a fifth waits at
the input) — so the head-of-line blocking B1 inherited from the tile's server is gone up to the queue's depth.

**AXI compliance (drop 0.44, review #19) — what the bridges do not do, and what the masters must.** The AXI4 ports
implement the subset above and nothing beyond it. *Not supported, on either side:* exclusive and locked accesses (AxLOCK is
not an input of the master ports and is ignored on the host slave — a locked or exclusive transaction is treated as a
plain one, EXOKAY is never returned); FIXED and WRAP bursts, narrow transfers and unaligned starts on the host slave
(SLVERR, the beats consumed: §5 *Host role*); AxCACHE / AxPROT / AxQOS / AxREGION / xUSER (not present); multiple IDs on the
memory master (AxID 0, up to four bursts in flight per direction, in order). *The host slave's write burst:* its length is
AWLEN + 1 beats; a WLAST earlier than that (drop 0.44 — ignored before, which left the slave waiting for the missing beats
and every transaction behind them) ends the burst at that beat: the beats received are written, the CRC flit is sent,
BRESP is SLVERR and `host` is set in the bridge's ERR; a master that offers more beats than AWLEN + 1 is held on WREADY
(its own violation — there is no watchdog on W). *The host slave's read:* a response that does not complete within
`HOST_TIMEOUT` is answered SLVERR (`timeout` in ERR), the host must keep RREADY coming. *The control plane (`s_axil_*`,
`host_axil` in `neo_tile_axil`):* BRESP / RRESP are always OKAY on `host_axil` (an unknown address writes nothing and reads
zero, as a register block does); the chip top's `s_axil_*` answers SLVERR for a disabled tile and DECERR past the map. *No
protocol checker is built in:* the bridges assume AXI-legal masters and slaves (VALID held with its payload until READY,
responses only to requests), and the simulation-bound assertion set (`02_dv/dv/sva/`, drop 0.44) checks exactly those
rules on `m_axi_*` / `s_axi_*` in the cocotb builds — an integrator's protocol checker (a VIP) belongs on the SoC side.
*Outstanding limits:* memory master — four read bursts and four write bursts in flight (one AXI ID each way, responses in
order); host slave — one transaction at a time (writes before reads: AW is taken first when both are valid), AWREADY may
depend on AWVALID and WVALID being held together (`host_axil`: the write is accepted only when both are valid, legal for an
AXI4-Lite slave); control plane — one access in flight through the crossing (`cdc_sync`).

**Throughput, measured** (`top` T3b against a DRAM-like memory: 32 aclk from AR to a burst's first R beat, the model
pipelining reads as a controller does; 16 aclk from the last W beat to B): four tiles' 4,092-word fetches at once 2,082 ns
(B1 2,750 ns): 50.3 bytes per aclk cycle of 64 (B1 38.1); a tile's layer with its drain in memory 2,478 ns after its start
(B1 3,210 ns, 25 bursts → 2), two tiles' at once 2,744 ns (B1 4,163 ns, 48 bursts → 2).

**Priority (drop 0.43).** Every router — the tiles' and the bridge column's — arbitrates each output between two classes:
a request whose flit's partition (seq[3:0]) has its bit set in the router's priority map wins over the others, round-robin
within a class, and a waiting low-class request is promoted for one grant after `PRIO_N` = 8 high-class grants. The bound
(proved in `02_dv/formal/noc_router_fv.sv`, measured in `run_noc.py` N4): a high-class request waits at most 5 + ⌊4 / N⌋
grants to others, a low-class one at most 5·N + 4. Software writes the same map into every tile's PRIO (`0x0C`) and the
control plane's PRIO; all zeros (the reset) is the router of drop 0.42, grant for grant.

## 6. Clock-domain crossings — exactly these, nothing else crosses

| # | Crossing | Structure | Constraint for the implementation flow |
| --- | --- | --- | --- |
| 1 | every bridge's data path, mesh → AXI and AXI → mesh | `cdc_fifo` per direction: `CDC_DEPTH` beats of 2·(`XW` + `YW`) + 552 bits (566 in silicon; the flit without its parity bit) in flops, binary + gray pointers, the gray pointer synchronized by `cdc_bits` (two flops) into the other clock; full / empty from the synchronized copies | `set_max_delay -datapath_only` ≤ one source period from each gray pointer register to its first synchronizer flop (bounds the skew between the code's bits); ≤ one destination period from `mem` to the read side's consumers (`u_rxq/mem → u_br/*`, `u_txq/mem → u_pack/*`) |
| 2 | the register bus to the tiles | `cdc_sync`: two-phase request / acknowledge toggles, each synchronized by two flops; the request payload `a_hold` held from before its toggle until the acknowledge, the response `b_hold` likewise | `set_max_delay -datapath_only` ≤ one destination period on `a_hold → mesh_ctrl` and `b_hold → a_rsp_data` (multicycle by protocol: the data is stable two edges before it is sampled) |
| 3 | levels | `cdc_bits` (two flops per bit): into aclk the tiles' `done` (`prog_done && !drain_busy`, not restarted) and `err_pin` — each registered in clk and masked by the enable map first — the effective enable map, the bridge routers' parity flags, and since drop 0.43 the power sequencers' states (3 bits per tile, gray-coded: one bit changes per step, each step lasts ≥ 4 clk) and the lockstep pairs' errors; into clk the tile-enable request (no reset on this one: it holds the request through the mesh reset), and since drop 0.43 the bridge column's priority map and the lockstep pairs' configuration (both static: written before use) | `set_false_path` (or `set_max_delay -datapath_only` one period) to every `sync1` flop |

Clock groups: `set_clock_groups -asynchronous -group clk -group aclk` after the max-delay exceptions above, or the
exceptions alone. Not crossings: the bridges' window bases, partitions and error events (aclk modules talking to aclk
modules); the tiles' status flags (`crc_err`, `fetch_timeout`, …) stay in clk and are read through the register windows.
The CDC structures are deliberately few: an open-source CDC lint is limited, so each is a recognizable module.

## 7. Tile enable = the power map (drop 0.43; the fusing map of drop 0.42)

`TILE_EN` is a request; `mesh_ctrl` applies bit n while the mesh is in reset, or while tile n is idle — not started since
reset (no CTRL write with bit 0 through the window), or its program has ended (`prog_done` rose after its start) — and holds
it pending otherwise, so a tile is never cut off in the middle of a run.

**The tile's two domains.** `neo_tile` is an always-on part — its router, on the mesh's reset (`router_rst_n` = `rst_n`)
and the ungated clock, plus the domain's clock gate and isolation cells — and a switchable domain: everything else (the
NIC, the link packer / unpacker, the bank, the core, the DMA, `host_if`, the ESM, the telemetry), clocked through one
`neo_icg` (`tile_clk_en`), reset by its own `tile_rst_n`, powered through the switch `tile_pwr_en` drives, its outputs
reaching the always-on side through `neo_iso` cells clamped while `iso_en`: the link into the router (valid and flit 0),
the router's local output ready (1: whatever reaches it drains), the status toward the control plane (0), its router's
priority map and error clear (0). In UPF terms: one power domain per tile = `u_t` minus `u_router`, `u_icg_tile` and the
`u_iso_*` cells; the isolation strategy is those cells (a power-aware flow maps them onto the library's isolation cells —
an AND for clamp 0, an OR for clamp 1, which is what synthesis of the behavioural form gives); no retention strategy.

**The sequencing** (`mesh_ctrl`, a sequencer per tile in clk; each step `DWELL` = 4 clk, the power-on step `PWR_DWELL` =
16): down — ISO (isolation on), CLKOFF (the domain's clock gated), RST (its reset asserted), OFF (`tile_pwr_en` low); up —
PWRON (power on, reset held), RSTREL (the reset released with the clock still off: no edge meets the release), CLKON (the
clock on, still isolated), ON (isolation off). The outputs are flops, so none glitches (`tile_rst_n` = `rst_n` AND its
flop). The states form a gray-code cycle, crossed to aclk bit by bit and read as `PWR_STATE`; `TILE_EN_EFF` shows the tiles
that are ON. A sequence runs to its rest state before the request is looked at again; while the mesh is in reset each
sequencer loads the requested rest state directly, so a tile disabled before `rst_n` is released leaves reset powered off,
never sequenced. *(The brief put the sequencing in `top_ctrl`: the four outputs and the idle rule are of the mesh clock,
and a sequence stepped in aclk would cross bit by bit with no order between the bits; `top_ctrl` holds the request and
reads the state.)* An integrator whose switches acknowledge (power good) replaces the `PWR_DWELL` count with that input.

A tile that is not ON is **fenced**: on each of its four mesh inputs a flit addressed to it is consumed and dropped before
the tile sees it (so its bank does not answer — requests to it time out at the requester; a host read returns SLVERR after
`HOST_TIMEOUT`; host writes to it are dropped), every other flit passes — its router keeps forwarding the XY traffic through
its position while the rest of the tile is isolated, clocked off, in reset and unpowered (`top` T6 runs a layer whose
every fetch crosses a powered-down tile's router); its register window answers SLVERR; its `done` and `err_pin` are masked
out of the DONE / ERR maps, the interrupt and `err`. Default: every tile enabled. The compiler takes the same map as its
exclusion list (`compile_network(exclude=...)`, `compile_layer(..., exclude=...)`).

**Retention: nothing.** A tile that comes back is a tile out of reset: its program memory, descriptor, requantization
tables, error state, telemetry and bank contents are gone (the simulation keeps the bank's array, silicon does not — software
must not rely on either) and are re-loaded by software (the command queue's images are the natural place). Retention
registers for the program memory and the descriptor (2 × 39 × 32 + 20 × 16 bits, some 2,800 flops a tile) or a retained
bank (a power-gated SRAM periphery) are a later plan, not RTL: neither is needed by a fused tile, and a sleeping tile's
state is cheaper to reload over the host bridge than to retain at 64 tiles' area.

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
| a lockstep pair's drains differ (drop 0.43) | LS_ERR bit k → `err`, IRQ_STATUS.lockstep | clear the pair's enable (LS_PAIR k bit 31) |
| the command queue completed a command (drop 0.43) | the completion record, CC_TAIL, IRQ_STATUS.command (one per completion) | IRQ_STATUS write 1 |

**Dual-tile lockstep (drop 0.43).** LS_PAIR k names two tiles a, b and an enable. While enabled and both tiles are on,
`mesh_ctrl` folds each tile's drain messages — its NIC's `drain_msg_cnt` advancing, `drain_crc_last` that message's CRC — into
a 16-bit signature per side (rotate left one, XOR the CRC) with a message count from the enable; whenever the two counts are
equal (and both signatures up to date) the signatures must be equal, and when both tiles are done the counts must be.
Either mismatch sets LS_ERR bit k (sticky in clk, crossed to aclk as the tiles' `err_pin`), IRQ_STATUS bit 3 on its rise,
and `err`. The signature makes each comparison cover every message before it, so a pair that drifts by a message (one tile
ahead, e.g. at a different distance from its data) loses no comparison. The pair runs the same descriptor on the same
inputs and drains to the same address of its own bank — `compile_pair` in `compiler/neo_backend.py` emits the two
programs. Test: `top` T7 (a clean pair; then the self-test fault hook on one tile — a +1 on one PE's product — makes its
drain differ: LS_ERR, the interrupt, `err`).

## 9. What the licensee connects, in order

1. `clk`, `rst_n` and `aclk`, `aresetn` with synchronous deassertion each; release `aresetn` first. `tile_pwr_en` to the
   tiles' power switches (§7), or open.
2. The control plane on the system's register interconnect; program `TILE_EN` (the fusing map — from the licensee's fuse
   block or firmware) and the memory bridges' `WIN_LO` / `WIN_HI` before releasing `rst_n`.
3. Each memory bridge's AXI4 master to a memory-controller port (LPDDR), with the tie-offs of §3; each host bridge's
   AXI4 slave into the host's address map (PCIe / the CPU cluster), its 27-bit window decoded upstream.
4. `irq` to the interrupt controller, `err` to the safety island (with the tiles' own `err_pin` causes readable through the
   windows).
5. Software: `neo_host.h`'s `NEO_TOP_*` map; a tile's registers at `NEO_TOP_TILE_REG(y·NX + x, reg)`, the existing driver's
   `neo_load_and_start` / `neo_wait_done` over a `neo_wr_fn` / `neo_rd_fn` that write those addresses; banks filled and read
   through the host bridge (`NEO_TOP_HOST_ADDR`) with a read-back fence before starting tiles on the data.

## 10. Left after B2

B2 (drop 0.43) built what B1 left: the bridges' request queue and posted, combined writes; the command queue; per-partition
NoC priority; clock gating; the tile's router reset and the power map. Still open: a reset controller (a mesh-only reset; the
island → control plane → tiles sequence with the island's reset); AXI IDs per burst with a reorder buffer (§5's note); a
power-good acknowledge in place of `PWR_DWELL`; retention (§7, a plan).
