"""test_neo_top.py -- the chip top's suite under cocotb on Verilator (drop 0.42).

neo_top is driven the way a licensee's SoC drives it: the AXI4-Lite control plane (the tiles' register windows and the
global registers), the host bridge's AXI4 slave (bank fills and read-backs), the memory bridge's AXI4 master answered by an
AXI4 memory model in this file, irq and err observed as pins. Nothing reaches inside: the tiles are built with their hooks
off (DV_HOOKS = 0), as in silicon. Two clocks: clk (the mesh) and aclk (the AXI side) at unrelated periods (1.0 / 1.6 ns,
NEO_CLK_PS / NEO_ACLK_PS); `make top_sync` runs the same build with aclk == clk. Reset as the integration note orders it:
aresetn released first (the control plane comes up, the tile-enable map can be written), then rst_n.
Geometry (the Makefile's `top` build): 2 x 2 tiles of 16 x 8 cores, 4,096-word banks, the 1024-bit links (WPF 32: the bridges
see beats of up to 16 words, merged into link flits of up to 32 words, as in silicon), FETCH_TIMEOUT and HOST_TIMEOUT 8,192, a 512-bit AXI;
two bridge nodes at x = 2: row 0 the memory and the host bridge, row 1 an unused position.
  T1  registers: ID / VERSION / CONFIG, every tile's host_if registers through its window (M15's read-back checks in each of
      the four tiles), a bridge node's window, the timer counts, SCRATCH, the tile-enable map read-back (requested and in effect)
  T2  fill and read back: a channel tile and a weight tile written into tile (0,0)'s bank through the host bridge, read back
      through it bit-exact, the tile's causes clean (its CRC and word counts matched); unaligned and strobed writes, two runs in
      one burst; SLVERR on partial-word strobes, narrow and WRAP bursts (DECERR past the mesh only exists when NX * NY is not a
      power of two -- not at 2 x 2)
  T3a a layer across the mesh: the compiler's program (owner + two contributors, K-split with the RDY handshake; the fourth
      tile holds the data, M6's placement) with the activations and weights filled through the host bridge, started through
      the windows, done seen in the DONE map, the result read back through the host bridge, bit-exact against the reference
  T3b the same with the weights FETCHed by the tiles from the memory bridge (the memory model behind its AXI4 master, a
      40-bit window base not 4 KB aligned, data starting inside beats); all four tiles computing with activations and weights
      from the memory bridge; a single tile's INT32 result drained into the memory bridge (WRHDR / WRDATA / CRC to AXI write
      bursts, one beat split at a 4 KB boundary) and two tiles draining into it at once, each checked in the model.
      (A tile that computes while it serves its own bank can deadlock: tile_nic holds an RDREQ at its input while its server is
      busy, and while it serves itself that RDREQ sits in the router port its own response must return through -- seen as
      fetch_timeout on every requester when the data tile of T3a was also given a share. Reported; no test places it so.)
  T4  errors: a tile's self-test hook (SELFTEST through its registers, on a short run) raises its err_pin -- the ERR map shows
      the tile, irq fires when IRQ_MASK lets it, clears on the write of IRQ_STATUS, err follows the tile; a fetch from the
      unused bridge position times out with fetch_timeout on the requesting tile; an AXI read error on the memory bridge
      reaches the fetching tile as crc_err and the bridge's ERR register as axi
  T5  tile-enable: tile (1,1) disabled while the mesh is in reset -- it leaves reset fenced: its window answers SLVERR, a host
      read of its bank times out SLVERR, a tile's fetch from it times out (fetch_timeout); the layer runs on the three others
      with the compiler's exclusion map (compile_layer(..., exclude)), its data behind the memory bridge; a bit cleared for a
      running tile stays pending until its program ends; the tiles re-enabled while idle answer again -- since drop 0.43
      (TILE_EN is the power map) out of reset: the cause the second tile held before it was switched off reads 0
  T6  power gating (drop 0.43): tile (1,0) powered down (TILE_EN cleared while idle) -- the sequencer's outputs change in
      the order iso on, clock off, reset, power off (tile_pwr_en the pin), PWR_STATE reads OFF, the window SLVERR -- while
      the three others run a layer whose every activation and weight from the memory bridge at (2, 0) crosses its router
      (XY routing: west along row 0, then south); then powered up (power on, reset released, clock on, iso off), its
      registers back at their reset values (nothing retained) and the layer run again by it alone, bit-exact
  T7  dual-tile lockstep (drop 0.43): tiles 0 and 1 run the compiler's pair programs (compile_pair: the same layer, the
      same inputs, each draining into its own bank) with LS_PAIR 0 naming them -- both results bit-exact, LS_ERR clean; the
      same again with tile 1's self-test fault hook asserted (a +1 on one PE's product): its drain differs, LS_ERR bit 0,
      IRQ_STATUS bit 3, err; disabling the pair clears it
  T8  the command queue (drop 0.43): a two-layer program played by the control plane -- layer 1 (a 3x3 conv, K-split on
      tiles 0 and 1, requantized INT8 drained into tile 0's bank: its tables are image entries too) and layer 2 (a 1x1 conv
      of layer 1's INT8 rows read as pair-pixels of 16 channels, on tile 2, INT32 into its bank) as two commands of the
      ring with their register images in the command memory, one doorbell; two completion records in order with their
      tags, entries played and done times from the global timer, an interrupt per completion; both layers bit-exact
  T9  (drop 0.44, review #12) a lockstep pair enabled by one LS_PAIR write after the two tiles' drain counts differ (tile a
      ran a layer alone): no spurious LS_ERR -- mesh_ctrl lets the pair's indices settle before its enable takes effect
  T10 (drop 0.44, review #17) a host-bridge master that ends a write burst early (WLAST on the second of four announced
      beats): the burst ends there, BRESP is SLVERR, the bridge's ERR shows host, and the next write and read-back are clean
      (the bridge ignored WLAST before and waited for the missing beats forever)
  T11 (drop 0.44, review #8) test mode: with a tile powered down (its clock gated, its reset asserted by the sequencer),
      test_en forces its gated clock on and makes its reset follow rst_n; released, the sequencer's values return
  Since drop 0.43 T3b also measures the memory bridge against a DRAM-like memory (32 aclk to a burst's first R beat, 16 to
  B; the model pipelines reads as a DRAM controller does) -- the drains landing in memory and four 4,092-word fetches at
  once -- and checks the bridge's wait counters (RD_WAIT / WR_WAIT) against the cycles this file counts on the AXI ports.
"""
import os
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly, ClockCycles, Lock, with_timeout
from cocotb.utils import get_sim_time
import numpy as np
from neo_golden import pack_words, direct_conv, ins, sbits, OP_FETCH_A, OP_FETCH_W, OP_DRAIN_WR, OP_GO, OP_WAIT_DONE, OP_END

NX, NY = int(os.environ.get("NEO_NX", "2")), int(os.environ.get("NEO_NY", "2"))
ROWS, COLS = int(os.environ.get("NEO_ROWS", "16")), int(os.environ.get("NEO_COLS", "8"))
BANK_DEPTH = int(os.environ.get("NEO_BANK_DEPTH", "4096"))
FETCH_TIMEOUT = int(os.environ.get("NEO_FETCH_TIMEOUT", "8192"))
HOST_TIMEOUT = int(os.environ.get("NEO_HOST_TIMEOUT", str(FETCH_TIMEOUT)))
AXI_DW = int(os.environ.get("NEO_AXI_DW", "512"))
N_MEM, N_HOST = int(os.environ.get("NEO_N_MEM", "1")), int(os.environ.get("NEO_N_HOST", "1"))
CLK_PS, ACLK_PS = int(os.environ.get("NEO_CLK_PS", "1000")), int(os.environ.get("NEO_ACLK_PS", "1600"))
NT = NX * NY
WPB, BB = AXI_DW // 32, AXI_DW // 8                 # words and bytes per AXI beat
BAW = (BANK_DEPTH - 1).bit_length()
WCW = 8 + (ROWS - 1).bit_length() + 1
WPA, WPW = ROWS * 8 // 32, (COLS * 8 + WCW + 31) // 32
CIN = 2 * ROWS + ROWS // 2                          # three channel tiles, the last half filled (as the tile suite)
CT_N = (CIN + ROWS - 1) // ROWS
APT, WPT = 36 * WPA, 9 * ROWS * WPW                 # bank words per activation tile and per weight tile (6x6, 3x3)
A0, W0, R0 = 0, 512, 2048                           # a bank's layout in the suite (the tile suite's at 16x8)
assert (A0 + CT_N * APT <= W0) and (W0 + CT_N * WPT <= R0) and (R0 + 36 * (COLS + 1) <= BANK_DEPTH)

# host_if registers (word addresses) and causes
REG_CTRL, REG_STATUS, REG_PROG_ADDR, REG_PROG_LO, REG_PROG_HI, REG_ERR_MASK, REG_ERR_CAUSE = 0, 1, 2, 3, 4, 5, 6
REG_WD_CTRL, REG_SELFTEST, REG_PARTITION, REG_CFG_BASE, REG_CFG_M = 7, 9, 0x0B, 0x10, 0x24
REG_SCRATCH_PROBE = REG_PARTITION                   # a writable register to probe a window with
CAUSE_CRC, CAUSE_ARRAY_ABFT, CAUSE_LOST, CAUSE_TIMEOUT = 1, 2, 12, 13
CFG_ORDER = ["cfg_h", "cfg_w", "cfg_ho", "cfg_wo", "cfg_oy0", "cfg_oy_n", "cfg_iy0", "cfg_s", "cfg_p", "cfg_k", "cfg_ct_n", "cfg_ct0",
             "cfg_ky0", "cfg_kx0", "cfg_rn", "cfg_contrib_n", "cfg_tile_pixels", "cfg_regions_m1"]
# the control plane's map (top_ctrl.sv; 04_software/neo_host.h)
NODE_BASE, GBASE = 0x1_0000, 0x2_0000
G_ID, G_VERSION, G_CONFIG, G_CONFIG2, G_SCRATCH, G_TIMER_LO, G_TIMER_HI = 0x000, 0x004, 0x008, 0x00C, 0x010, 0x018, 0x01C
G_TILE_EN, G_TILE_EN_EFF, G_DONE, G_ERR, G_IRQ_STATUS, G_IRQ_MASK, G_STATUS = 0x020, 0x028, 0x030, 0x038, 0x040, 0x044, 0x048
G_PRIO, G_LS_PAIR, G_LS_ERR, G_PWR_STATE = 0x04C, 0x050, 0x070, 0x080   # drop 0.43
G_CQ_CTRL, G_CQ_RING, G_CQ_TAIL, G_CQ_HEAD, G_CC_RING, G_CC_HEAD, G_CC_TAIL, G_CQ_STATUS = range(0x0A0, 0x0C0, 4)
G_TEL_CTRL, G_TEL_CYCLES, G_CMEM = 0x0C0, 0x0C4, 0x1000
REG_RQ_TBL, REG_RQ_ADDR, REG_RQ_RELU = 0x30, 0x31, 0x32
PWR_ON, PWR_OFF = 0, 4                              # PWR_STATE values (mesh_ctrl.sv: 0 ON .. 4 OFF .. 7 CLKON)
G_BR = 0x100                                        # + 0x20 * b: WIN_LO, WIN_HI, CTRL, ERR
OKAY, SLVERR, DECERR = 0, 2, 3
BR_CRC, BR_LOST, BR_AXI, BR_TIMEOUT, BR_HOST, BR_PARITY = 0, 1, 2, 3, 4, 5
WIN_BASE = 0xAB_CDEF_0040                           # the memory bridge's window: a 40-bit base, 64-byte aligned, not 4 KB aligned
BX = NX                                             # the bridge column


def node(x, y):
    return y * NX + x


def win_addr(n, word):
    """A host bridge byte address: the tile index above the bank's byte offset."""
    return (n << (BAW + 2)) | (word * 4)


def conv_data(seed=17, cin=CIN, cout=7):
    rng = np.random.default_rng(seed)
    x = rng.integers(-128, 128, size=(cin, 6, 6), dtype=np.int64)
    wgt = rng.integers(-128, 128, size=(cout, cin, 3, 3), dtype=np.int64)
    ref = direct_conv(x, wgt, 1, 1)
    act, wts = pack_words(x, wgt, (cin + ROWS - 1) // ROWS, 3, cout, rows=ROWS, cols=COLS)
    return x, wgt, ref, act, wts


def to_image(words, ref, M=36, cout=7, wo=6):
    """INT32 result rows (COLS + 1 words each, the check word last) to the reference's (cout, h, w) layout."""
    got = np.zeros_like(ref)
    for m in range(M):
        for c in range(cout):
            got[c, m // wo, m % wo] = sbits(words[m * (COLS + 1) + c], 32)
    return got


def check(got, ref, tag):
    if np.array_equal(got, ref):
        return
    bad = np.argwhere(got != ref)
    first = "; ".join(f"{tuple(int(i) for i in idx)} got {int(got[tuple(idx)])} exp {int(ref[tuple(idx)])}" for idx in bad[:6])
    raise AssertionError(f"{tag}: {len(bad)}/{ref.size} values differ: {first}")


async def handshake(clk, ready, sample=None):
    """The valid side is driven; wait for the edge at which ready is high (sampled, settled, in the cycle before it).
    Returns sample() taken in that cycle. Ends just after the edge, so the caller may drive the next values."""
    while True:
        await ReadOnly()
        r = int(ready.value)
        vals = sample() if (r and sample) else None
        await RisingEdge(clk)
        if r:
            return vals


class AxiLite:
    """AXI4-Lite master on the control plane (aclk)."""

    def __init__(self, dut):
        self.d, self.clk, self.lock = dut, dut.aclk, Lock()
        for s in ("awvalid", "wvalid", "bready", "arvalid", "rready"):
            getattr(dut, "s_axil_" + s).value = 0

    async def write(self, addr, data, strb=0xF):
        d = self.d
        async with self.lock:
            d.s_axil_awaddr.value = addr
            d.s_axil_wdata.value = data & 0xFFFFFFFF
            d.s_axil_wstrb.value = strb
            d.s_axil_awvalid.value = 1
            d.s_axil_wvalid.value = 1
            await handshake(self.clk, d.s_axil_awready)
            d.s_axil_awvalid.value = 0
            d.s_axil_wvalid.value = 0
            d.s_axil_bready.value = 1
            resp = await handshake(self.clk, d.s_axil_bvalid, lambda: int(d.s_axil_bresp.value))
            d.s_axil_bready.value = 0
            return resp

    async def read(self, addr):
        d = self.d
        async with self.lock:
            d.s_axil_araddr.value = addr
            d.s_axil_arvalid.value = 1
            await handshake(self.clk, d.s_axil_arready)
            d.s_axil_arvalid.value = 0
            d.s_axil_rready.value = 1
            data, resp = await handshake(self.clk, d.s_axil_rvalid, lambda: (int(d.s_axil_rdata.value), int(d.s_axil_rresp.value)))
            d.s_axil_rready.value = 0
            return data, resp


class AxiHost:
    """AXI4 master on the host bridge's slave port (aclk): bursts of full-width beats, INCR."""

    def __init__(self, dut):
        self.d, self.clk, self.lock = dut, dut.aclk, Lock()
        for s in ("awvalid", "wvalid", "bready", "arvalid", "rready"):
            getattr(dut, "s_axi_" + s).value = 0

    async def _aw(self, addr, nbeats, size, burst, axid):
        d = self.d
        d.s_axi_awid.value = axid
        d.s_axi_awaddr.value = addr
        d.s_axi_awlen.value = nbeats - 1
        d.s_axi_awsize.value = size
        d.s_axi_awburst.value = burst
        d.s_axi_awvalid.value = 1
        await handshake(self.clk, d.s_axi_awready)
        d.s_axi_awvalid.value = 0

    async def _w(self, beats):
        d = self.d
        for i, (data, strb) in enumerate(beats):
            d.s_axi_wdata.value = data
            d.s_axi_wstrb.value = strb
            d.s_axi_wlast.value = int(i == len(beats) - 1)
            d.s_axi_wvalid.value = 1
            await handshake(self.clk, d.s_axi_wready)
        d.s_axi_wvalid.value = 0
        d.s_axi_wlast.value = 0

    async def write_beats(self, addr, beats, size=None, burst=1, axid=3, awlen_beats=None):
        """One burst: AW and W driven concurrently (the master must not wait for AWREADY before WVALID); returns (bresp, bid).
        awlen_beats (drop 0.44, T10): AWLEN + 1 announced to the slave when it differs from the beats sent (a short burst)."""
        d = self.d
        size = (BB.bit_length() - 1) if size is None else size
        async with self.lock:
            aw = cocotb.start_soon(self._aw(addr, len(beats) if awlen_beats is None else awlen_beats, size, burst, axid))
            await self._w(beats)
            await aw
            d.s_axi_bready.value = 1
            resp, bid = await handshake(self.clk, d.s_axi_bvalid, lambda: (int(d.s_axi_bresp.value), int(d.s_axi_bid.value)))
            d.s_axi_bready.value = 0
            assert bid == axid, f"BID {bid} != AWID {axid}"
            return resp

    async def read_beats(self, addr, nbeats, size=None, burst=1, axid=5):
        """One burst; returns [(data, rresp)] and checks RLAST and RID."""
        d = self.d
        size = (BB.bit_length() - 1) if size is None else size
        async with self.lock:
            d.s_axi_arid.value = axid
            d.s_axi_araddr.value = addr
            d.s_axi_arlen.value = nbeats - 1
            d.s_axi_arsize.value = size
            d.s_axi_arburst.value = burst
            d.s_axi_arvalid.value = 1
            await handshake(self.clk, d.s_axi_arready)
            d.s_axi_arvalid.value = 0
            d.s_axi_rready.value = 1
            out = []
            while True:
                data, resp, last, rid = await handshake(self.clk, d.s_axi_rvalid, lambda: (
                    int(d.s_axi_rdata.value), int(d.s_axi_rresp.value), int(d.s_axi_rlast.value), int(d.s_axi_rid.value)))
                assert rid == axid, f"RID {rid} != ARID {axid}"
                out.append((data, resp))
                assert last == int(len(out) == nbeats), f"RLAST {last} on beat {len(out)} of {nbeats}"
                if last:
                    break
            d.s_axi_rready.value = 0
            return out

    async def write_words(self, n, word, words):
        """words into tile n's bank at word address `word` (any alignment): bursts within 4 KB pages, strobes on the words."""
        resps = []
        pos = 0
        while pos < len(words):
            w0 = word + pos
            page_end = ((w0 * 4) // 4096 + 1) * 4096 // 4      # the next 4 KB boundary, in words
            cnt = min(len(words) - pos, page_end - w0)
            base = w0 - (w0 % WPB)
            nbeats = (w0 - base + cnt + WPB - 1) // WPB
            beats = []
            for b in range(nbeats):
                data, strb = 0, 0
                for lane in range(WPB):
                    i = base + b * WPB + lane - w0
                    if 0 <= i < cnt:
                        data |= (words[pos + i] & 0xFFFFFFFF) << (32 * lane)
                        strb |= 0xF << (4 * lane)
                beats.append((data, strb))
            resps.append(await self.write_beats(win_addr(n, w0), beats))
            pos += cnt
        return resps

    async def read_words(self, n, word, count):
        """count words from tile n's bank at word address `word`; returns (words, the worst RRESP)."""
        out, worst = [], OKAY
        pos = 0
        while pos < count:
            w0 = word + pos
            base = w0 - (w0 % WPB)
            page_end = ((base * 4) // 4096 + 1) * 4096 // 4
            nbeats = min((w0 - base + (count - pos) + WPB - 1) // WPB, (page_end - base) // WPB)
            beats = await self.read_beats(win_addr(n, base), nbeats)
            flat = []
            for data, resp in beats:
                worst = max(worst, resp)
                flat += [(data >> (32 * lane)) & 0xFFFFFFFF for lane in range(WPB)]
            take = flat[w0 - base:]
            take = take[:count - pos]
            out += take
            pos += len(take)
        return out, worst


class AxiMem:
    """AXI4 slave memory behind the memory bridge's master port (aclk): byte-addressed words, INCR bursts of full-width
    beats checked against the 4 KB rule, SLVERR on chosen byte ranges; every burst logged. Reads are pipelined as a DRAM
    controller's (drop 0.43): AR is taken while fewer than `depth` bursts wait, and each burst's first R beat comes
    `latency` cycles after its AR at the earliest, in order -- one burst at a time (B1's bridge) sees exactly the model of
    drop 0.42. Writes: AW, its W beats, then B `wlatency` cycles after the last W (0: the next cycle, as before).
    Telemetry check (drop 0.43): rd_wait / wr_wait count the cycles the bridge's counters count -- a read burst outstanding
    (its AR taken, its last R beat not yet) and no R beat transferred; a write burst outstanding (its AW taken, its B not
    yet) and no W beat transferred -- from these ports, per window (tel_reset)."""

    def __init__(self, dut, latency=3, wlatency=0, depth=8):
        self.d, self.clk, self.latency, self.wlatency, self.depth = dut, dut.aclk, latency, wlatency, depth
        self.mem = {}
        self.err_ranges = []
        self.reads, self.writes = [], []
        self.arq = []
        self.rd_wait = self.wr_wait = 0
        for s in ("arready", "rvalid", "awready", "wready", "bvalid"):
            getattr(dut, "m_axi_" + s).value = 0
        cocotb.start_soon(self._ar())
        cocotb.start_soon(self._reads())
        cocotb.start_soon(self._writes())
        cocotb.start_soon(self._tel())

    def tel_reset(self):
        self.rd_wait = self.wr_wait = 0

    async def _tel(self):
        d = self.d
        rd_out = wr_out = 0
        while True:
            await RisingEdge(self.clk)
            await ReadOnly()
            ar = int(d.m_axi_arvalid.value) & int(d.m_axi_arready.value)
            r = int(d.m_axi_rvalid.value) & int(d.m_axi_rready.value)
            rl = r & int(d.m_axi_rlast.value)
            aw = int(d.m_axi_awvalid.value) & int(d.m_axi_awready.value)
            w = int(d.m_axi_wvalid.value) & int(d.m_axi_wready.value)
            b = int(d.m_axi_bvalid.value) & int(d.m_axi_bready.value)
            self.rd_wait += int(rd_out > 0 and not r)
            self.wr_wait += int(wr_out > 0 and not w)
            rd_out += ar - rl
            wr_out += aw - b

    def word(self, a):
        return self.mem.get(a & ~3, 0)

    def load(self, byte_addr, words):
        for i, w in enumerate(words):
            self.mem[byte_addr + 4 * i] = w & 0xFFFFFFFF

    def burst_ok(self, addr, nbeats, size, burst):
        assert size == BB.bit_length() - 1 and burst == 1, f"burst size {size} type {burst}"
        assert addr % BB == 0, f"burst address 0x{addr:x} not beat-aligned"
        assert (addr % 4096) + nbeats * BB <= 4096, f"burst at 0x{addr:x} of {nbeats} beats crosses a 4 KB boundary"

    async def _ar(self):
        """AR taken while fewer than `depth` bursts wait; each stamped with the time its first beat may come."""
        d = self.d
        while True:
            d.m_axi_arready.value = int(len(self.arq) < self.depth)
            await ReadOnly()
            fire = int(d.m_axi_arvalid.value) and int(d.m_axi_arready.value)
            vals = (int(d.m_axi_araddr.value), int(d.m_axi_arlen.value) + 1, int(d.m_axi_arsize.value),
                    int(d.m_axi_arburst.value)) if fire else None
            await RisingEdge(self.clk)
            if fire:
                self.arq.append(vals + (get_sim_time("ps") + self.latency * ACLK_PS,))

    async def _reads(self):
        d = self.d
        while True:
            if not self.arq:
                await RisingEdge(self.clk)
                continue
            addr, nb, size, burst, t_first = self.arq[0]
            while get_sim_time("ps") < t_first:
                await RisingEdge(self.clk)
            self.arq.pop(0)
            self.burst_ok(addr, nb, size, burst)
            self.reads.append((addr, nb))
            for b in range(nb):
                a = addr + b * BB
                d.m_axi_rdata.value = sum(self.word(a + 4 * i) << (32 * i) for i in range(WPB))
                d.m_axi_rresp.value = SLVERR if any(lo <= a < hi for lo, hi in self.err_ranges) else OKAY
                d.m_axi_rlast.value = int(b == nb - 1)
                d.m_axi_rvalid.value = 1
                await handshake(self.clk, d.m_axi_rready)
            d.m_axi_rvalid.value = 0
            d.m_axi_rlast.value = 0

    async def _writes(self):
        d = self.d
        while True:
            d.m_axi_awready.value = 1
            addr, nb, size, burst = await handshake(self.clk, d.m_axi_awvalid, lambda: (
                int(d.m_axi_awaddr.value), int(d.m_axi_awlen.value) + 1, int(d.m_axi_awsize.value), int(d.m_axi_awburst.value)))
            d.m_axi_awready.value = 0
            self.burst_ok(addr, nb, size, burst)
            d.m_axi_wready.value = 1
            strobes = []
            for b in range(nb):
                data, strb, last = await handshake(self.clk, d.m_axi_wvalid, lambda: (
                    int(d.m_axi_wdata.value), int(d.m_axi_wstrb.value), int(d.m_axi_wlast.value)))
                assert last == int(b == nb - 1), f"WLAST {last} on beat {b + 1} of {nb}"
                a = addr + b * BB
                for i in range(WPB):
                    s = (strb >> (4 * i)) & 0xF
                    assert s in (0, 0xF), f"partial-word strobe 0x{s:x}"
                    if s:
                        self.mem[a + 4 * i] = (data >> (32 * i)) & 0xFFFFFFFF
                strobes.append(strb)
            d.m_axi_wready.value = 0
            self.writes.append((addr, nb, strobes))
            for _ in range(self.wlatency):
                await RisingEdge(self.clk)
            d.m_axi_bresp.value = OKAY
            d.m_axi_bvalid.value = 1
            await handshake(self.clk, d.m_axi_bready)
            d.m_axi_bvalid.value = 0


class Top:
    """The host's view: the control plane, the host bridge, the memory behind the memory bridge, irq / err."""

    def __init__(self, dut):
        self.d = dut
        self.ctl = AxiLite(dut)
        self.host = AxiHost(dut)
        self.mem = AxiMem(dut)

    async def reset(self, tile_en=None):
        d = self.d
        d.test_en.value = 0                               # the test mode off (drop 0.44; T9 raises it)
        d.aresetn.value = 0
        d.rst_n.value = 0
        await ClockCycles(d.aclk, 4)
        d.aresetn.value = 1                               # the control plane first
        await ClockCycles(d.aclk, 3)
        if tile_en is not None:                           # the tile-enable map, written while the mesh is in reset
            await self.greg_w(G_TILE_EN, tile_en & 0xFFFFFFFF)
        await ClockCycles(d.clk, 4)
        d.rst_n.value = 1                                 # then the mesh
        await ClockCycles(d.clk, 4)
        await ClockCycles(d.aclk, 2)

    # global registers
    async def greg_w(self, off, val):
        resp = await self.ctl.write(GBASE + off, val)
        assert resp == OKAY, f"global 0x{off:03x} write: resp {resp}"

    async def greg_r(self, off):
        v, resp = await self.ctl.read(GBASE + off)
        assert resp == OKAY, f"global 0x{off:03x} read: resp {resp}"
        return v

    # a tile's registers through its window
    async def treg_w(self, n, reg, val, expect=OKAY):
        resp = await self.ctl.write(n * 1024 + reg * 4, val)
        assert resp == expect, f"tile {n} reg 0x{reg:02x} write: resp {resp}, expected {expect}"

    async def treg_r(self, n, reg, expect=OKAY):
        v, resp = await self.ctl.read(n * 1024 + reg * 4)
        assert resp == expect, f"tile {n} reg 0x{reg:02x} read: resp {resp}, expected {expect}"
        return v

    async def program(self, n, words):
        for a, wd in enumerate(words):
            await self.treg_w(n, REG_PROG_ADDR, a)
            await self.treg_w(n, REG_PROG_LO, wd & 0xFFFFFFFF)
            await self.treg_w(n, REG_PROG_HI, wd >> 32)

    async def descriptor(self, n, d):
        for name, v in d.items():
            if name == "cfg_m":
                await self.treg_w(n, REG_CFG_M, v)
            else:
                await self.treg_w(n, REG_CFG_BASE + CFG_ORDER.index(name), v & 0xFFFF)

    async def start(self, nodes):
        for n in nodes:
            await self.treg_w(n, REG_CTRL, 1)

    async def wait_done(self, nodes, limit_us=60):
        """Poll the global DONE map (prog_done && !drain_busy per enabled tile) until every node shows."""
        want = sum(1 << n for n in nodes)
        t0 = get_sim_time("ns")
        while True:
            done = await self.greg_r(G_DONE)
            if (done & want) == want:
                return get_sim_time("ns") - t0
            if get_sim_time("ns") - t0 >= limit_us * 1000:
                diag = []
                for n in range(NT):
                    st, _ = await self.ctl.read(n * 1024 + REG_STATUS * 4)
                    ca, _ = await self.ctl.read(n * 1024 + REG_ERR_CAUSE * 4)
                    diag.append(f"tile {n}: STATUS 0x{st:08x} cause 0x{ca:05x}")
                raise AssertionError(f"tiles {nodes}: DONE 0x{done:x} after {limit_us} us; " + "; ".join(diag))
            await ClockCycles(self.d.aclk, 8)

    async def causes(self, nodes=None):
        return {n: await self.treg_r(n, REG_ERR_CAUSE) for n in (range(NT) if nodes is None else nodes)}

    async def expect_clean(self, nodes=None):
        c = await self.causes(nodes)
        assert all(v == 0 for v in c.values()), "causes " + ", ".join(f"tile {n} 0x{v:05x}" for n, v in c.items())

    async def br_err(self, b=0):
        return await self.greg_r(G_BR + 0x20 * b + 0xC)

    async def pwr_state(self, n):
        return (await self.greg_r(G_PWR_STATE + 4 * (n // 8)) >> (4 * (n % 8))) & 0xF

    async def wait_eff(self, want, limit_ns=2000):
        """Poll TILE_EN_EFF (the tiles that are on) until it equals `want`; returns ns."""
        t0 = get_sim_time("ns")
        while True:
            eff = await self.greg_r(G_TILE_EN_EFF)
            if eff == want:
                return get_sim_time("ns") - t0
            assert get_sim_time("ns") - t0 < limit_ns, f"TILE_EN_EFF 0x{eff:x}, waiting for 0x{want:x}"
            await ClockCycles(self.d.aclk, 4)


async def setup(dut, tile_en=None):
    cocotb.start_soon(Clock(dut.clk, CLK_PS, units="ps").start())
    cocotb.start_soon(Clock(dut.aclk, ACLK_PS, units="ps").start())
    top = Top(dut)
    await top.reset(tile_en)
    return top


async def run_layer(top, lp):
    """Load a compiled layer through the windows, start its tiles, wait for the DONE map; returns ns."""
    nodes = []
    for tp in lp.tiles:
        n = node(*tp.tile)
        nodes.append(n)
        await top.descriptor(n, tp.descriptor)
        await top.program(n, tp.program)
    await top.start(nodes)
    return await top.wait_done(nodes)


# ---------------------------------------------------------------------------------------------------------------------
@cocotb.test()
async def t1_registers(dut):
    top = await setup(dut)
    assert await top.greg_r(G_ID) == 0x4E454F54, "ID"
    assert await top.greg_r(G_VERSION) == 0x45, "VERSION"
    cfg = await top.greg_r(G_CONFIG)
    assert cfg == (COLS << 24) | (ROWS << 16) | (N_HOST << 12) | (N_MEM << 8) | (NY << 4) | NX, f"CONFIG 0x{cfg:08x}"
    cfg2 = await top.greg_r(G_CONFIG2)
    assert cfg2 == (BAW << 24) | (int(os.environ.get("NEO_WPF", "1")) << 16) | AXI_DW, f"CONFIG2 0x{cfg2:08x}"
    await top.greg_w(G_SCRATCH, 0x5EED_C0DE)
    assert await top.greg_r(G_SCRATCH) == 0x5EED_C0DE, "SCRATCH"
    # every tile's host_if through its window: M15's read-back checks, distinct values per tile
    for n in range(NT):
        checks = [(REG_PROG_ADDR, 0x15 ^ n, 0x1F), (REG_PROG_LO, 0xC0FFEE11 + n, 0xFFFFFFFF), (REG_ERR_MASK, 0x5A5A5 ^ n, 0x7FFFF),
                  (REG_PARTITION, 0xA ^ n, 0xF), (REG_CFG_M, 37 + n, 0x3F)]
        for reg, v, m in checks:
            await top.treg_w(n, reg, v)
        for reg, v, m in checks:
            rb = await top.treg_r(n, reg)
            assert rb == v & m, f"tile {n} reg 0x{reg:02x}: wrote 0x{v:x}, read 0x{rb:x}"
        for i in range(20):
            await top.treg_w(n, REG_CFG_BASE + i, (0x1000 * i + 7 * n + 1) & 0xFFFF)
        for i in range(20):
            rb = await top.treg_r(n, REG_CFG_BASE + i)
            assert rb == (0x1000 * i + 7 * n + 1) & 0xFFFF, f"tile {n} cfg[{i}] read 0x{rb:x}"
        st = await top.treg_r(n, REG_STATUS)
        assert (st & 0x7) == 0, f"tile {n} STATUS 0x{st:08x}: idle expected"
        assert await top.treg_r(n, 0x3F) == 0, f"tile {n}: an unmapped register reads non-zero"
        await top.treg_w(n, REG_PARTITION, 0)
        await top.treg_w(n, REG_ERR_MASK, 0)
    # a window past the mesh, a bridge node's window, a global hole
    _, resp = await top.ctl.read(NT * 1024)
    assert resp == DECERR, f"window {NT} (no tile): resp {resp}"
    v, resp = await top.ctl.read(NODE_BASE + 0 * 1024 + REG_STATUS * 4)
    assert resp == OKAY and v == 0, f"bridge node 0 STATUS 0x{v:x} resp {resp}"
    v, resp = await top.ctl.read(NODE_BASE + NY * 1024)
    assert resp == DECERR, f"bridge node {NY} (none): resp {resp}"
    _, resp = await top.ctl.read(GBASE + 0x0FC)
    assert resp == DECERR, f"global hole: resp {resp}"
    # the timer counts aclk cycles; TIMER_LO latches the high word
    t0 = await top.greg_r(G_TIMER_LO)
    await ClockCycles(dut.aclk, 100)
    t1 = await top.greg_r(G_TIMER_LO)
    assert 100 <= t1 - t0 < 200, f"timer {t0} -> {t1}"
    assert await top.greg_r(G_TIMER_HI) == 0, "TIMER_HI"
    # the tile-enable map: requested and in effect, all tiles after reset; a bridge's registers read back
    full = (1 << NT) - 1
    assert await top.greg_r(G_TILE_EN) == full and await top.greg_r(G_TILE_EN + 4) == 0, "TILE_EN after reset"
    assert await top.greg_r(G_TILE_EN_EFF) == full, "TILE_EN_EFF after reset"
    await top.greg_w(G_BR + 0x0, 0x8765_43C0)
    await top.greg_w(G_BR + 0x4, 0xAB)
    await top.greg_w(G_BR + 0x8, 0x5)
    assert (await top.greg_r(G_BR + 0x0), await top.greg_r(G_BR + 0x4), await top.greg_r(G_BR + 0x8)) == (0x8765_43C0, 0xAB, 0x5)
    await top.greg_w(G_BR + 0x8, 0)
    assert await top.br_err() == 0 and await top.greg_r(G_IRQ_STATUS) == 0 and await top.greg_r(G_IRQ_MASK) == 0x1F
    # drop 0.43: every tile's power sequencer at ON, no lockstep error, the pair registers read back
    assert all([await top.pwr_state(n) == PWR_ON for n in range(NT)]), "PWR_STATE after reset"
    assert await top.greg_r(G_LS_ERR) == 0 and int(dut.tile_pwr_en.value) == (1 << NT) - 1
    await top.greg_w(G_LS_PAIR + 4, (2 << 8) | 3)
    assert await top.greg_r(G_LS_PAIR + 4) == (2 << 8) | 3, "LS_PAIR 1 read-back"
    await top.greg_w(G_LS_PAIR + 4, 0)
    # the command memory's last word and the queue at reset
    await top.greg_w(G_CMEM + 4 * 1023, 0xC0DE_1023)
    assert await top.greg_r(G_CMEM + 4 * 1023) == 0xC0DE_1023, "command memory read-back"
    assert [await top.greg_r(r) for r in (G_CQ_CTRL, G_CQ_HEAD, G_CC_TAIL, G_CQ_STATUS)] == [0, 0, 0, 0], "the queue at reset"
    assert await top.greg_r(G_STATUS) == 0 and int(dut.irq.value) == 0 and int(dut.err.value) == 0
    dut._log.info(f"T1: ID / VERSION / CONFIG, {NT} tile windows (M15's read-back each), node window, timer {t1 - t0} cycles / 100, "
                  f"TILE_EN 0x{full:x} requested and in effect")


@cocotb.test()
async def t2_fill_and_readback(dut):
    top = await setup(dut)
    x, wgt, ref, act, wts = conv_data()
    n = node(0, 0)
    chan, wtile = act[:APT], wts[:WPT]                  # one channel tile, one weight tile
    t0 = get_sim_time("ns")
    assert all(r == OKAY for r in await top.host.write_words(n, A0, chan)), "fill: BRESP"
    assert all(r == OKAY for r in await top.host.write_words(n, W0, wtile)), "fill: BRESP"
    t_fill = get_sim_time("ns") - t0
    got_a, ra = await top.host.read_words(n, A0, len(chan))
    got_w, rw = await top.host.read_words(n, W0, len(wtile))
    assert ra == OKAY and rw == OKAY, f"read-back RRESP {ra} {rw}"
    assert got_a == [v & 0xFFFFFFFF for v in chan], "channel tile read back differs"
    assert got_w == [v & 0xFFFFFFFF for v in wtile], "weight tile read back differs"
    await top.expect_clean()                            # the tile's CRC and word counts matched every stream
    # unaligned and strobed: 5 words at word 3001 (inside a beat), a run that crosses a beat boundary, a beat skipped
    vals = [0x1111_0000 + i for i in range(40)]
    await top.host.write_words(n, 3001, vals[:5])
    await top.host.write_words(n, 3070, vals[5:25])     # words 3070 .. 3089: two beats, one run
    beats = [(sum(v << (32 * i) for i, v in enumerate(vals[25:41])), (1 << 64) - 1), (0, 0)]   # a full beat, then an empty one
    assert await top.host.write_beats(win_addr(n, 3104), beats) == OKAY
    rb, r = await top.host.read_words(n, 3000, 120)
    exp = {3001 + i: vals[i] for i in range(5)}
    exp.update({3070 + i: vals[5 + i] for i in range(20)})
    exp.update({3104 + i: vals[25 + i] for i in range(15)})
    for i, v in enumerate(rb):
        if 3000 + i in exp:
            assert v == exp[3000 + i], f"word {3000 + i}: 0x{v:08x} != 0x{exp[3000 + i]:08x}"
    assert r == OKAY
    # one burst, two runs: words 3136-3139 (beat 0, lanes 0-3), then 3160-3183 (beat 1 lanes 8-15, beat 2 whole): the second run
    # does not continue the first, so the bridge sends a second WRHDR inside the same message (one CRC flit for both)
    full = (1 << 64) - 1
    beats = [(sum((0x2222_0000 + i) << (32 * i) for i in range(4)), 0xFFFF),
             (sum((0x2222_0000 + 24 + i) << (32 * (8 + i)) for i in range(8)), full ^ 0xFFFF_FFFF),
             (sum((0x2222_0000 + 32 + i) << (32 * i) for i in range(16)), full)]
    assert await top.host.write_beats(win_addr(n, 3136), beats) == OKAY
    rb, r = await top.host.read_words(n, 3136, 48)
    exp2 = [0x2222_0000 + i if (i < 4 or i >= 24) else 0 for i in range(48)]
    assert r == OKAY and rb == exp2, f"two-run burst: {[hex(v) for v in rb]}"
    # what the window does not hold or the bridge does not take
    if NT < (1 << (NT - 1).bit_length()):            # a tile index past the mesh exists only when NT is not a power of two
        assert await top.host.write_beats(win_addr(NT, 0), [(0, (1 << 64) - 1)]) == DECERR, "write past the mesh"
        beats = await top.host.read_beats(win_addr(NT, 0), 2)
        assert [rr for _, rr in beats] == [DECERR, DECERR], "read past the mesh"
    assert await top.host.write_beats(win_addr(n, 3200), [(0xDEAD, 0x3)]) == SLVERR, "partial-word strobe"
    assert await top.host.write_beats(win_addr(n, 3200), [(0, (1 << 64) - 1)], size=2) == SLVERR, "narrow beats"
    beats = await top.host.read_beats(win_addr(n, 3200), 1, burst=2)
    assert beats[0][1] == SLVERR, "WRAP read"
    rb, _ = await top.host.read_words(n, 3200, 16)
    assert rb[0] != 0xDEAD, "a refused beat was written"
    del full
    be = await top.br_err()
    assert be == (1 << BR_HOST), f"bridge ERR 0x{be:x}: host expected"
    await top.greg_w(G_BR + 0xC, 1 << BR_HOST)
    assert await top.br_err() == 0
    await top.expect_clean()
    dut._log.info(f"T2: {len(chan)} + {len(wtile)} words filled through the host bridge in {t_fill:.0f} ns, read back bit-exact, "
                  f"causes clean; unaligned and strobed writes, two runs in one burst; SLVERR on partial-word strobes, narrow and WRAP bursts")


async def fill_layer_data(top, n, act, wts, weights=True):
    assert all(r == OKAY for r in await top.host.write_words(n, A0, act))
    if weights:
        assert all(r == OKAY for r in await top.host.write_words(n, W0, wts))
    # the fence: a read through the same bridge to the same tile follows the writes on their XY path
    _, r = await top.host.read_words(n, A0, 1)
    assert r == OKAY


@cocotb.test()
async def t3a_layer_host_bridge(dut):
    from neo_backend import compile_layer
    top = await setup(dut)
    x, wgt, ref, act, wts = conv_data()
    src = node(1, 1)
    await fill_layer_data(top, src, act, wts)
    # owner + two contributors, the fourth tile serving the data (M6's placement): a tile that fetches from its own bank
    # while other tiles fetch from it can deadlock its NIC (an RDREQ waiting at its input while it serves itself holds the
    # router port its own response must come back through -- tile_nic's flow control, reported), so the data tile does not
    # compute here; t3b runs all four tiles with the data behind the memory bridge
    lp = compile_layer("t3a", 6, 6, CIN, 7, 3, 1, 1, ROWS, COLS, NX, NY, act_bank=(1, 1), act_base=A0, w_bank=(1, 1), w_base=W0,
                       owner=(0, 0), shares=NT - 1)
    assert len(lp.tiles) == NT - 1 and all(node(*tp.tile) != src for tp in lp.tiles), [tp.tile for tp in lp.tiles]
    t = await run_layer(top, lp)
    words, r = await top.host.read_words(node(*lp.out_bank), lp.out_base, lp.M * (COLS + 1))
    assert r == OKAY
    check(to_image(words, ref), ref, "T3a")
    await top.expect_clean()
    dut._log.info(f"T3a: the compiler's layer on {len(lp.tiles)} tiles (owner + contributors, RDY) with the data filled through the host "
                  f"bridge into tile {src}, started through the windows, read back through the bridge, bit-exact; run {t:.0f} ns")


@cocotb.test()
async def t3b_layer_memory_bridge(dut):
    from neo_backend import compile_layer
    top = await setup(dut)
    x, wgt, ref, act, wts = conv_data()
    await top.greg_w(G_BR + 0x0, WIN_BASE & 0xFFFFFFFF)
    await top.greg_w(G_BR + 0x4, WIN_BASE >> 32)
    WB = 0x3F1                                          # the weights' word address in the window: inside a beat
    top.mem.load(WIN_BASE + 4 * WB, wts)
    src = node(1, 1)
    await fill_layer_data(top, src, act, wts, weights=False)
    lp = compile_layer("t3b", 6, 6, CIN, 7, 3, 1, 1, ROWS, COLS, NX, NY, act_bank=(1, 1), act_base=A0, w_bank=(BX, 0), w_base=WB,
                       owner=(0, 0), shares=NT - 1)
    t = await run_layer(top, lp)
    words, r = await top.host.read_words(node(*lp.out_bank), lp.out_base, lp.M * (COLS + 1))
    assert r == OKAY
    check(to_image(words, ref), ref, "T3b weights from the memory bridge")
    await top.expect_clean()
    assert await top.br_err() == 0
    n_reads = len(top.mem.reads)
    # all four tiles computing, the activations and the weights behind the memory bridge (no tile serves), a fresh result area
    WA, WR = 0x2000, 0x13C1                             # activations at a row boundary; the drained result inside a beat, across 4 KB
    top.mem.load(WIN_BASE + 4 * WA, act)
    top.mem.reads.clear()
    lp4 = compile_layer("t3b4", 6, 6, CIN, 7, 3, 1, 1, ROWS, COLS, NX, NY, act_bank=(BX, 0), act_base=WA, w_bank=(BX, 0), w_base=WB,
                        owner=(0, 0), shares=NT, out_base=R0)
    assert len(lp4.tiles) == NT
    t4 = await run_layer(top, lp4)
    words, r = await top.host.read_words(node(*lp4.out_bank), R0, lp4.M * (COLS + 1))
    assert r == OKAY
    check(to_image(words, ref), ref, "T3b four tiles from the memory bridge")
    await top.expect_clean()
    assert await top.br_err() == 0
    n_reads4 = len(top.mem.reads)
    # a single tile: activations and weights from the memory bridge, the INT32 result drained into it
    top.mem.reads.clear()
    lp1 = compile_layer("t3c", 6, 6, CIN, 7, 3, 1, 1, ROWS, COLS, NX, NY, act_bank=(BX, 0), act_base=WA, w_bank=(BX, 0), w_base=WB,
                        owner=(1, 0), shares=1)
    tp = lp1.tiles[0]
    prog = [w for w in tp.program if (w >> 60) & 0xF != OP_DRAIN_WR]
    drain = [w for w in tp.program if (w >> 60) & 0xF == OP_DRAIN_WR]
    assert len(drain) == 1
    prog.insert(tp.program.index(drain[0]), ins(OP_DRAIN_WR, BX, 0, WR, lp1.M))   # the drain into the memory bridge's window
    n1 = node(1, 0)
    await top.descriptor(n1, tp.descriptor)
    await top.program(n1, prog)
    await top.start([n1])
    t1 = await top.wait_done([n1])
    await ClockCycles(dut.aclk, 200)                    # the last WRDATA beats' AXI writes after the drain left the tile
    words = [top.mem.word(WIN_BASE + 4 * (WR + i)) for i in range(lp1.M * (COLS + 1))]
    check(to_image(words, ref), ref, "T3b result drained into the memory bridge")
    assert top.mem.word(WIN_BASE + 4 * (WR - 1)) == 0 and top.mem.word(WIN_BASE + 4 * (WR + lp1.M * (COLS + 1))) == 0, "a word around the drain was written"
    await top.expect_clean()
    assert await top.br_err() == 0, "bridge ERR after the drain"
    # the drain's beats are 16 + 11 words (rows of 9 merged three to a 32-word flit, the flit unpacked into 16-word beats); the
    # one at words WR + 43 .. 53 holds the 4 KB boundary at word 0x13F0, so the bridge writes it as two bursts, the second from
    # the boundary (the model refuses a burst across it)
    page = WIN_BASE + 4 * 0x13F0
    assert page % 4096 == 0 and any(a == page for a, _, _ in top.mem.writes), "no burst starts at the 4 KB boundary: the split"
    # two tiles at once, each the whole layer from the memory bridge, each draining into it: the bridge keeps a write pointer,
    # CRC and count per source, so the two drains' beats may interleave
    nw0 = len(top.mem.writes)
    outs = {node(0, 1): 0x4000, node(1, 1): 0x4400 + 3}
    for nn in outs:
        p = [w for w in tp.program if (w >> 60) & 0xF != OP_DRAIN_WR]
        p.insert(tp.program.index(drain[0]), ins(OP_DRAIN_WR, BX, 0, outs[nn], lp1.M))
        await top.descriptor(nn, tp.descriptor)
        await top.program(nn, p)
    await top.start(list(outs))
    t2 = await top.wait_done(list(outs))
    await ClockCycles(dut.aclk, 200)
    for nn, wr in outs.items():
        words = [top.mem.word(WIN_BASE + 4 * (wr + i)) for i in range(lp1.M * (COLS + 1))]
        check(to_image(words, ref), ref, f"T3b tile {nn}'s drain alongside another")
    await top.expect_clean()
    assert await top.br_err() == 0, "bridge ERR after the concurrent drains"
    n_two = len(top.mem.writes) - nw0
    dut._log.info(f"T3b: weights FETCHed from the memory bridge ({n_reads} AXI read bursts), bit-exact in {t:.0f} ns; all {NT} tiles with "
                  f"activations and weights from it ({n_reads4} bursts), bit-exact in {t4:.0f} ns; a tile's {lp1.M} INT32 rows drained into it "
                  f"({nw0} AXI write bursts, the beat across 4 KB split in two), bit-exact in {t1:.0f} ns; two tiles draining into it at once ({n_two} bursts), "
                  f"both bit-exact in {t2:.0f} ns")
    # the bridge's throughput (drop 0.43, against B1's one burst in flight): a memory with DRAM-like latencies -- 32 aclk from
    # AR to a burst's first R beat (pipelined: several bursts may wait), 16 from the last W beat to B
    top.mem.latency, top.mem.wlatency = 32, 16
    await top.expect_clean()
    assert (await top.greg_r(G_BR + 0x18)) & 1 == 0, "the bridge holds writes before the throughput section"
    await top.greg_w(G_TEL_CTRL, 2)                     # the counters' window starts with the bridge idle
    top.mem.tel_reset()
    # (i) writes: the single tile's drain again, then the two tiles' drains at once (their programs are still loaded), each
    # timed from its start to the moment the memory holds every word of it (the result areas cleared first)
    nwd = lp1.M * (COLS + 1)

    async def landed(areas, t0, limit_ns=30000):
        while True:
            if all(np.array_equal(to_image([top.mem.word(WIN_BASE + 4 * (wr + i)) for i in range(nwd)], ref), ref) for wr in areas):
                return get_sim_time("ns") - t0
            assert get_sim_time("ns") - t0 < limit_ns, f"drains into {[hex(a) for a in areas]} not in memory after {limit_ns} ns"
            await ClockCycles(dut.aclk, 4)
    for wr in [WR] + list(outs.values()):
        top.mem.load(WIN_BASE + 4 * wr, [0] * nwd)
    nw1 = len(top.mem.writes)
    t0 = get_sim_time("ns")
    await top.start([n1])
    t1l = await landed([WR], t0)
    await top.wait_done([n1])
    nw2 = len(top.mem.writes)
    t0 = get_sim_time("ns")
    await top.start(list(outs))
    t2l = await landed(list(outs.values()), t0)
    await top.wait_done(list(outs))
    nw3 = len(top.mem.writes)
    # (ii) reads: every tile one FETCH_A of 4,092 words at once (no compute; four 4 KB bursts each), the time to the last done
    FW, RB = 4092, 0x8000
    top.mem.load(WIN_BASE + 4 * RB, [(0x5A5A0000 + i) & 0xFFFFFFFF for i in range(NT * 0x1000)])
    top.mem.reads.clear()
    for nn in range(NT):
        await top.program(nn, [ins(OP_FETCH_A, BX, 0, RB + nn * 0x1000, FW, 0), ins(OP_END)])
    await top.start(list(range(NT)))
    t_rd = await top.wait_done(list(range(NT)))
    await top.expect_clean()
    assert await top.br_err() == 0
    n_rd = len(top.mem.reads)
    bpc = NT * FW * 4 / (t_rd / (ACLK_PS / 1000))
    while (await top.greg_r(G_BR + 0x18)) & 1:          # every write answered, then the window closes with the bridge idle
        await ClockCycles(dut.aclk, 8)
    await top.greg_w(G_TEL_CTRL, 1)
    rdw, wrw, cyc = await top.greg_r(G_BR + 0x10), await top.greg_r(G_BR + 0x14), await top.greg_r(G_TEL_CYCLES)
    assert (rdw, wrw) == (top.mem.rd_wait, top.mem.wr_wait), \
        f"bridge RD_WAIT {rdw} / WR_WAIT {wrw} against the ports' {top.mem.rd_wait} / {top.mem.wr_wait}"
    dut._log.info(f"T3b telemetry: RD_WAIT {rdw}, WR_WAIT {wrw} of {cyc} aclk cycles, equal to the counts on the AXI ports")
    dut._log.info(f"T3b throughput (read latency 32, write latency 16 aclk): a tile's layer and drain in memory {t1l:.0f} ns after its start "
                  f"({nw2 - nw1} AXI write bursts), two tiles' at once in {t2l:.0f} ns ({nw3 - nw2} bursts); {NT} x {FW}-word fetches from the "
                  f"memory bridge at once in {t_rd:.0f} ns ({n_rd} AXI read bursts, {bpc:.1f} bytes per aclk cycle of {BB})")


@cocotb.test()
async def t4_errors(dut):
    top = await setup(dut)
    x, wgt, ref, act, wts = conv_data()
    n = node(1, 0)
    await fill_layer_data(top, node(1, 1), act[:APT], wts[:WPT])
    # (a) the self-test hook through the tile's registers on a short run: the array's ABFT check fires, err_pin rises
    await top.greg_w(G_IRQ_MASK, 0x5)                    # the err cause unmasked
    d = dict(cfg_h=6, cfg_w=6, cfg_ho=1, cfg_wo=1, cfg_oy0=0, cfg_oy_n=1, cfg_iy0=0, cfg_s=1, cfg_p=1, cfg_k=3, cfg_ct_n=1, cfg_ct0=0,
             cfg_ky0=0, cfg_kx0=0, cfg_rn=9, cfg_contrib_n=0, cfg_tile_pixels=36, cfg_regions_m1=0xFFFF, cfg_m=1)
    await top.descriptor(n, d)
    await top.treg_w(n, REG_SELFTEST, 1)
    await top.program(n, [ins(OP_FETCH_A, 1, 1, A0, APT, 0), ins(OP_FETCH_W, 1, 1, W0, WPT, 0), ins(OP_DRAIN_WR, 0, 1, R0, 1),
                          ins(OP_GO), ins(OP_WAIT_DONE), ins(OP_END)])
    assert int(dut.irq.value) == 0 and int(dut.err.value) == 0
    await top.start([n])
    await top.wait_done([n])
    await ClockCycles(dut.aclk, 8)
    c = await top.treg_r(n, REG_ERR_CAUSE)
    assert (c >> CAUSE_ARRAY_ABFT) & 1, f"cause 0x{c:05x}: array_abft expected"
    errmap = await top.greg_r(G_ERR)
    assert errmap == 1 << n, f"ERR map 0x{errmap:x}: tile {n} expected"
    st = await top.greg_r(G_IRQ_STATUS)
    assert st & 0x2 and st & 0x1, f"IRQ_STATUS 0x{st:x}: err and done expected"
    assert int(dut.irq.value) == 1 and int(dut.err.value) == 1, "irq / err pins"
    await top.greg_w(G_IRQ_STATUS, 0x3)                  # clears on write (the err level persists: the tile's cause is latched)
    await ClockCycles(dut.aclk, 3)
    assert await top.greg_r(G_IRQ_STATUS) == 0 and int(dut.irq.value) == 0 and int(dut.err.value) == 1, "IRQ_STATUS clear"
    await top.treg_w(n, REG_SELFTEST, 0)
    await top.treg_w(n, REG_CTRL, 2)                     # the tile's error clear: err_pin and the level drop
    await ClockCycles(dut.aclk, 8)
    assert await top.greg_r(G_ERR) == 0 and int(dut.err.value) == 0, "err after the tile's clear"
    # (b) a fetch from the unused bridge position (row 1): consumed and dropped, the requester times out
    n0 = node(0, 0)
    await top.program(n0, [ins(OP_FETCH_A, BX, 1, 0, 16, 0), ins(OP_END)])
    t0 = get_sim_time("ns")
    await top.start([n0])
    await top.wait_done([n0], limit_us=FETCH_TIMEOUT * CLK_PS / 1e6 + 50)
    t_to = get_sim_time("ns") - t0
    c = await top.treg_r(n0, REG_ERR_CAUSE)
    assert (c >> CAUSE_TIMEOUT) & 1 and t_to * 1000 >= FETCH_TIMEOUT * CLK_PS, f"cause 0x{c:05x} after {t_to:.0f} ns: fetch_timeout expected"
    assert await top.greg_r(G_ERR) == 1 << n0, "ERR map: the requester"
    assert (await top.greg_r(G_IRQ_STATUS)) & 0x2, "IRQ_STATUS err"
    await top.treg_w(n0, REG_CTRL, 2)
    await top.greg_w(G_IRQ_STATUS, 0x7)
    # (c) an AXI read error behind the memory bridge: the CRC flit goes out inverted, the tile flags crc_err, the bridge axi
    await top.greg_w(G_IRQ_MASK, 0x3)                    # the bridge cause unmasked
    await top.greg_w(G_BR + 0x0, WIN_BASE & 0xFFFFFFFF)
    await top.greg_w(G_BR + 0x4, WIN_BASE >> 32)
    top.mem.load(WIN_BASE, list(range(64)))
    top.mem.err_ranges.append((WIN_BASE + 64, WIN_BASE + 128))
    await top.program(n0, [ins(OP_FETCH_A, BX, 0, 8, 32, 0), ins(OP_END)])
    await top.start([n0])
    await top.wait_done([n0])
    await ClockCycles(dut.aclk, 8)
    c = await top.treg_r(n0, REG_ERR_CAUSE)
    assert (c >> CAUSE_CRC) & 1 and not (c >> CAUSE_LOST) & 1, f"cause 0x{c:05x}: crc (not lost) expected"
    be = await top.br_err()
    assert be == 1 << BR_AXI, f"bridge ERR 0x{be:x}: axi expected"
    assert (await top.greg_r(G_IRQ_STATUS)) & 0x4 and int(dut.irq.value) == 1, "the bridge cause"
    await top.greg_w(G_BR + 0xC, 0x1F)
    await top.treg_w(n0, REG_CTRL, 2)
    await top.greg_w(G_IRQ_STATUS, 0x7)
    await ClockCycles(dut.aclk, 8)
    assert await top.br_err() == 0 and int(dut.irq.value) == 0 and int(dut.err.value) == 0, "cleared"
    dut._log.info(f"T4: self-test cause on tile {n} in the ERR map, irq on unmask, cleared on write; the unused bridge position timed out "
                  f"in {t_to:.0f} ns (fetch_timeout); an AXI SLVERR reached the tile as crc_err and the bridge's ERR as axi")


@cocotb.test()
async def t5_tile_enable(dut):
    from neo_backend import compile_layer
    off = node(1, 1)
    full = (1 << NT) - 1
    top = await setup(dut, tile_en=full & ~(1 << off))   # disabled while the mesh is in reset
    assert await top.greg_r(G_TILE_EN_EFF) == full & ~(1 << off), "the map in effect after reset"
    # the disabled tile: no registers, no bank
    await top.treg_w(off, REG_SCRATCH_PROBE, 1, expect=SLVERR)
    v = await top.treg_r(off, REG_STATUS, expect=SLVERR)
    assert v == 0
    t0 = get_sim_time("ns")
    _, r = await top.host.read_words(off, 0, 16)
    t_host = get_sim_time("ns") - t0
    assert r == SLVERR and t_host * 1000 >= HOST_TIMEOUT * ACLK_PS, f"host read of the disabled tile: RRESP {r} after {t_host:.0f} ns"
    assert (await top.br_err()) == 1 << BR_TIMEOUT, "bridge timeout"
    await top.greg_w(G_BR + 0xC, 0x1F)
    n0 = node(0, 0)
    await top.program(n0, [ins(OP_FETCH_A, 1, 1, 0, 16, 0), ins(OP_END)])
    await top.start([n0])
    await top.wait_done([n0], limit_us=FETCH_TIMEOUT * CLK_PS / 1e6 + 50)
    c = await top.treg_r(n0, REG_ERR_CAUSE)
    assert (c >> CAUSE_TIMEOUT) & 1, f"a fetch from the disabled tile: cause 0x{c:05x}"
    await top.treg_w(n0, REG_CTRL, 2)
    # the layer on the three others, the compiler's map excluding the tile, the data behind the memory bridge
    x, wgt, ref, act, wts = conv_data()
    await top.greg_w(G_BR + 0x0, WIN_BASE & 0xFFFFFFFF)
    await top.greg_w(G_BR + 0x4, WIN_BASE >> 32)
    top.mem.load(WIN_BASE + 4 * 0x2000, act)
    top.mem.load(WIN_BASE + 4 * 0x3F1, wts)
    lp = compile_layer("t5", 6, 6, CIN, 7, 3, 1, 1, ROWS, COLS, NX, NY, act_bank=(BX, 0), act_base=0x2000, w_bank=(BX, 0), w_base=0x3F1,
                       owner=(0, 0), shares=NT, exclude=[(1, 1)])
    assert len(lp.tiles) == NT - 1 and all(node(*tp.tile) != off for tp in lp.tiles), [tp.tile for tp in lp.tiles]
    t = await run_layer(top, lp)
    words, r = await top.host.read_words(node(*lp.out_bank), lp.out_base, lp.M * (COLS + 1))
    assert r == OKAY
    check(to_image(words, ref), ref, "T5")
    await top.expect_clean([k for k in range(NT) if k != off])
    assert await top.greg_r(G_ERR) == 0 and await top.greg_r(G_DONE) & (1 << off) == 0
    # the rule: tile 2 runs a program that waits (a fetch from the unused bridge position times out); clearing its bit meanwhile
    # stays pending -- TILE_EN_EFF keeps it -- and takes effect when its program has ended
    n2 = node(0, 1)
    await top.program(n2, [ins(OP_FETCH_A, BX, 1, 0, 16, 0), ins(OP_END)])
    await top.start([n2])
    await top.greg_w(G_TILE_EN, full & ~(1 << off) & ~(1 << n2))
    await ClockCycles(dut.aclk, 16)
    eff = await top.greg_r(G_TILE_EN_EFF)
    assert eff == full & ~(1 << off), f"TILE_EN_EFF 0x{eff:x} while tile {n2} runs: the change must wait"
    while (await top.greg_r(G_TILE_EN_EFF)) & (1 << n2):
        await ClockCycles(dut.aclk, 64)
    assert (await top.treg_r(n2, REG_STATUS, expect=SLVERR)) == 0, "the newly disabled tile's window"
    # re-enabled while idle: they answer again -- powered up, out of reset (drop 0.43: TILE_EN is the power map)
    await top.greg_w(G_TILE_EN, full)
    await top.wait_eff(full)
    await top.treg_w(off, REG_PARTITION, 3)
    assert await top.treg_r(off, REG_PARTITION) == 3
    c = await top.treg_r(n2, REG_ERR_CAUSE)
    assert c == 0, f"tile {n2} back from power-down: cause 0x{c:05x} (nothing is retained)"
    dut._log.info(f"T5: tile {off} disabled at reset -- registers SLVERR, host read SLVERR after {t_host:.0f} ns, a tile's fetch timed out; "
                  f"the layer on {len(lp.tiles)} tiles with the exclusion map bit-exact in {t:.0f} ns; a change for a running tile held until its "
                  f"program ended; re-enabled while idle (back from power-down, the held cause gone)")


class PwrMonitor:
    """Records the order in which mesh_ctrl's four outputs of one tile change (sampled at every mesh clock edge)."""

    def __init__(self, dut, n):
        self.d, self.n, self.events = dut, n, []
        cocotb.start_soon(self._run())

    def sample(self):
        m, n = self.d.u_mctrl, self.n
        return {"iso": (int(m.tile_iso_en.value) >> n) & 1, "clk": (int(m.tile_clk_en.value) >> n) & 1,
                "rst_n": (int(m.tile_rst_n.value) >> n) & 1, "pwr": (int(self.d.tile_pwr_en.value) >> n) & 1}

    async def _run(self):
        await RisingEdge(self.d.clk)
        await ReadOnly()
        last = self.sample()
        while True:
            await RisingEdge(self.d.clk)
            await ReadOnly()
            cur = self.sample()
            for k in cur:
                if cur[k] != last[k]:
                    self.events.append((get_sim_time("ns"), k, cur[k]))
            last = cur


def order_ok(events, want):
    """events (t, signal, value) hold the transitions `want` in that order, each later than the one before."""
    times = []
    for sig, val in want:
        t = next((t for t, k, v in events if k == sig and v == val and (not times or t > times[-1])), None)
        if t is None:
            return False, times
        times.append(t)
    return True, times


@cocotb.test()
async def t6_power_gating(dut):
    from neo_backend import compile_layer
    top = await setup(dut)
    full = (1 << NT) - 1
    off = node(1, 0)
    mon = PwrMonitor(dut, off)
    x, wgt, ref, act, wts = conv_data()
    await top.greg_w(G_BR + 0x0, WIN_BASE & 0xFFFFFFFF)
    await top.greg_w(G_BR + 0x4, WIN_BASE >> 32)
    WA, WB = 0x2000, 0x3F1
    top.mem.load(WIN_BASE + 4 * WA, act)
    top.mem.load(WIN_BASE + 4 * WB, wts)
    await top.treg_w(off, REG_PARTITION, 5)               # state the power-down must not keep
    # power down: TILE_EN cleared while the tile is idle
    await top.greg_w(G_TILE_EN, full & ~(1 << off))
    t_dn = await top.wait_eff(full & ~(1 << off))
    while await top.pwr_state(off) != PWR_OFF:
        await ClockCycles(dut.aclk, 4)
    assert (int(dut.tile_pwr_en.value) >> off) & 1 == 0, "tile_pwr_en of the tile powered down"
    ok, t_seq = order_ok(mon.events, [("iso", 1), ("clk", 0), ("rst_n", 0), ("pwr", 0)])
    assert ok, f"power-down order: {mon.events}"
    await top.treg_w(off, REG_SCRATCH_PROBE, 1, expect=SLVERR)
    # the layer on the three others: the data from the memory bridge at (BX, 0) reaches (0, 0) and (0, 1) through (1, 0)'s
    # router only (XY: west along row 0 first), the result read back from (0, 0) through it too
    lp = compile_layer("t6", 6, 6, CIN, 7, 3, 1, 1, ROWS, COLS, NX, NY, act_bank=(BX, 0), act_base=WA, w_bank=(BX, 0), w_base=WB,
                       owner=(0, 0), shares=NT, exclude=[(1, 0)])
    assert len(lp.tiles) == NT - 1 and all(node(*tp.tile) != off for tp in lp.tiles), [tp.tile for tp in lp.tiles]
    t = await run_layer(top, lp)
    words, r = await top.host.read_words(node(*lp.out_bank), lp.out_base, lp.M * (COLS + 1))
    assert r == OKAY
    check(to_image(words, ref), ref, "T6 three tiles through the powered-down tile's router")
    await top.expect_clean([k for k in range(NT) if k != off])
    assert await top.br_err() == 0 and await top.greg_r(G_ERR) == 0
    assert await top.pwr_state(off) == PWR_OFF and (await top.greg_r(G_DONE)) & (1 << off) == 0
    # power up, then the tile out of reset re-used
    mon.events.clear()
    await top.greg_w(G_TILE_EN, full)
    t_up = await top.wait_eff(full)
    assert await top.pwr_state(off) == PWR_ON and (int(dut.tile_pwr_en.value) >> off) & 1
    ok, t_seq2 = order_ok(mon.events, [("pwr", 1), ("rst_n", 1), ("clk", 1), ("iso", 0)])
    assert ok, f"power-up order: {mon.events}"
    assert await top.treg_r(off, REG_PARTITION) == 0, "PARTITION after the power cycle: reset value expected"
    assert await top.treg_r(off, REG_ERR_CAUSE) == 0
    lp1 = compile_layer("t6b", 6, 6, CIN, 7, 3, 1, 1, ROWS, COLS, NX, NY, act_bank=(BX, 0), act_base=WA, w_bank=(BX, 0), w_base=WB,
                        owner=(1, 0), shares=1, out_base=R0)
    t1 = await run_layer(top, lp1)
    words, r = await top.host.read_words(off, R0, lp1.M * (COLS + 1))
    assert r == OKAY
    check(to_image(words, ref), ref, "T6 the tile re-used after the power cycle")
    await top.expect_clean()
    dut._log.info(f"T6: tile {off} powered down (iso on, clock off, reset, power off at {', '.join(f'{v:.0f}' for v in t_seq)} ns, "
                  f"TILE_EN_EFF after {t_dn:.0f} ns, PWR_STATE OFF); the layer on {len(lp.tiles)} tiles through its router bit-exact in "
                  f"{t:.0f} ns; powered up (power on, reset off, clock on, iso off at {', '.join(f'{v:.0f}' for v in t_seq2)} ns, on after "
                  f"{t_up:.0f} ns), registers at reset, the layer alone on it bit-exact in {t1:.0f} ns")


@cocotb.test()
async def t7_lockstep(dut):
    from neo_backend import compile_pair
    top = await setup(dut)
    a, b = node(0, 0), node(1, 0)
    x, wgt, ref, act, wts = conv_data()
    await top.greg_w(G_BR + 0x0, WIN_BASE & 0xFFFFFFFF)
    await top.greg_w(G_BR + 0x4, WIN_BASE >> 32)
    WA, WB = 0x2000, 0x3F1
    top.mem.load(WIN_BASE + 4 * WA, act)
    top.mem.load(WIN_BASE + 4 * WB, wts)
    lp = compile_pair("t7", 6, 6, CIN, 7, 3, 1, 1, ROWS, COLS, NX, NY, act_bank=(BX, 0), act_base=WA, w_bank=(BX, 0), w_base=WB,
                      pair=((0, 0), (1, 0)), out_base=R0)
    assert [node(*tp.tile) for tp in lp.tiles] == [a, b]
    await top.greg_w(G_IRQ_MASK, 0x17)                    # the lockstep cause unmasked
    await top.greg_w(G_LS_PAIR, (1 << 31) | (b << 8) | a)
    assert await top.greg_r(G_LS_PAIR) == (1 << 31) | (b << 8) | a
    # (a) the pair on the same inputs: no mismatch
    t = await run_layer(top, lp)
    await ClockCycles(dut.aclk, 8)
    for n in (a, b):
        words, r = await top.host.read_words(n, R0, lp.M * (COLS + 1))
        assert r == OKAY
        check(to_image(words, ref), ref, f"T7 tile {n} of the pair")
    assert await top.greg_r(G_LS_ERR) == 0 and not (await top.greg_r(G_IRQ_STATUS)) & 0x8 and int(dut.err.value) == 0, "a clean pair"
    await top.expect_clean([a, b])
    # (b) tile b's self-test fault hook: one PE's product +1 while asserted -- its drained rows differ, the comparison fires
    await top.treg_w(b, REG_SELFTEST, 1)
    await top.start([a, b])
    t2 = await top.wait_done([a, b])
    await ClockCycles(dut.aclk, 8)
    lse = await top.greg_r(G_LS_ERR)
    st = await top.greg_r(G_IRQ_STATUS)
    assert lse == 1 and st & 0x8 and int(dut.irq.value) == 1 and int(dut.err.value) == 1, f"LS_ERR {lse} IRQ_STATUS 0x{st:x}"
    words, _ = await top.host.read_words(b, R0, lp.M * (COLS + 1))
    nbad = int(np.sum(to_image(words, ref) != ref))
    assert nbad > 0, "the fault left tile b's result unchanged"
    c = await top.treg_r(b, REG_ERR_CAUSE)
    assert (c >> CAUSE_ARRAY_ABFT) & 1, f"tile {b} cause 0x{c:05x}: the array's ABFT should see the fault too"
    # clearing: the pair's enable off clears its error; the tile's own cause through its CTRL
    await top.treg_w(b, REG_SELFTEST, 0)
    await top.treg_w(b, REG_CTRL, 2)
    await top.greg_w(G_LS_PAIR, 0)
    await ClockCycles(dut.aclk, 8)
    await top.greg_w(G_IRQ_STATUS, 0x1F)
    await ClockCycles(dut.aclk, 8)
    assert await top.greg_r(G_LS_ERR) == 0 and int(dut.err.value) == 0 and int(dut.irq.value) == 0, "cleared"
    dut._log.info(f"T7: the pair ({a}, {b}) on the compiler's pair programs, both bit-exact, no mismatch, in {t:.0f} ns; tile {b}'s fault "
                  f"hook: {nbad} of {ref.size} values differ, LS_ERR 0x{lse:x}, IRQ_STATUS 0x{st:x}, err high, in {t2:.0f} ns; cleared by the "
                  f"pair's enable")


def rq_model(acc, mult, shift, zp, relu):
    """requant.sv's rounding: (acc * mult + 2^(shift-1)) >> shift, + zp, ReLU at zp, saturated to INT8 (test_two_layer.py)."""
    r = (int(acc) * int(mult) + ((1 << int(shift)) >> 1)) >> int(shift)
    v = r + int(zp)
    if relu and v < zp:
        v = zp
    return max(-128, min(127, v))


@cocotb.test()
async def t8_command_queue(dut):
    from neo_backend import compile_layer, cq_image
    top = await setup(dut)
    rng = np.random.default_rng(29)
    C1 = 8                                              # layer 1's output channels: one output tile of the 16x8 core
    x = rng.integers(-128, 128, size=(CIN, 6, 6), dtype=np.int64)
    w1 = rng.integers(-128, 128, size=(C1, CIN, 3, 3), dtype=np.int64)
    w2 = rng.integers(-128, 128, size=(7, 2 * C1, 1, 1), dtype=np.int64)
    mult = rng.integers(20000, 60000, size=C1)
    shift = np.full(C1, 21)
    zp = rng.integers(-8, 9, size=C1)
    y1 = direct_conv(x, w1, 1, 1)
    q1 = np.vectorize(lambda v, c: rq_model(v, mult[c], shift[c], zp[c], 1))(y1, np.arange(C1)[:, None, None]).astype(np.int64)
    # layer 2 reads layer 1's INT8 rows (8 channels, two words a pixel) as entries of four words: pixels (2j, 2j+1) of a row
    # are one 16-channel pair-pixel -- a 1x1 conv over the 6 x 3 pair image
    z = np.concatenate([q1[:, :, 0::2], q1[:, :, 1::2]], axis=0)
    y2 = direct_conv(z, w2, 1, 0)
    act, wts1 = pack_words(x, w1, CT_N, 3, C1, rows=ROWS, cols=COLS)
    _, wts2 = pack_words(z, w2, 1, 1, 7, rows=ROWS, cols=COLS)
    await top.greg_w(G_BR + 0x0, WIN_BASE & 0xFFFFFFFF)
    await top.greg_w(G_BR + 0x4, WIN_BASE >> 32)
    WA, W1B, W2B, R1, R2 = 0x2000, 0x3F1, 0x1000, 2048, 2048
    top.mem.load(WIN_BASE + 4 * WA, act)
    top.mem.load(WIN_BASE + 4 * W1B, wts1)
    top.mem.load(WIN_BASE + 4 * W2B, wts2)
    lp1 = compile_layer("t8a", 6, 6, CIN, C1, 3, 1, 1, ROWS, COLS, NX, NY, act_bank=(BX, 0), act_base=WA, w_bank=(BX, 0), w_base=W1B,
                        owner=(0, 0), shares=2, int8_out=True, out_base=R1, exclude=[(0, 1), (1, 1)])
    lp2 = compile_layer("t8b", 6, 3, 2 * C1, 7, 1, 1, 0, ROWS, COLS, NX, NY, act_bank=(0, 0), act_base=R1, w_bank=(BX, 0), w_base=W2B,
                        owner=(0, 1), shares=1, out_base=R2)
    assert sorted(node(*tp.tile) for tp in lp1.tiles) == [0, 1] and [node(*tp.tile) for tp in lp2.tiles] == [2]
    # the owner's requantization tables and ReLU: entries of command 1
    rq = []
    for c in range(C1):
        rq += [((1 << 14) | (0 << 8) | REG_RQ_ADDR, c), ((1 << 14) | (0 << 8) | REG_RQ_TBL,
                                                          ((int(zp[c]) & 0xFF) << 24) | (int(shift[c]) << 16) | int(mult[c]))]
    rq.append(((1 << 14) | (0 << 8) | REG_RQ_RELU, 1))
    img1, img2 = cq_image(lp1.tiles, NX, rq), cq_image(lp2.tiles, NX)   # the image neo_host.c's neo_cq_image writes
    # the command memory: the command ring at word 0 (4 entries), the completion ring at word 16 (4 entries), the images
    IMG1, IMG2 = 64, 64 + 2 * len(img1)
    assert IMG2 + 2 * len(img2) <= 1024, "the images do not fit the command memory"
    cmds = [(0x3, 0, (len(img1) << 16) | IMG1, 0xA11CE001), (0x4, 0, (len(img2) << 16) | IMG2, 0xA11CE002)]
    words = {}
    for i, c in enumerate(cmds):
        for k, v in enumerate(c):
            words[4 * i + k] = v
    for base, img in ((IMG1, img1), (IMG2, img2)):
        for i, (e0, v) in enumerate(img):
            words[base + 2 * i], words[base + 2 * i + 1] = e0, v & 0xFFFFFFFF
    for a in sorted(words):
        await top.greg_w(G_CMEM + 4 * a, words[a])
    for k in range(16):                                 # the completion ring cleared
        await top.greg_w(G_CMEM + 4 * (16 + k), 0)
    await top.greg_w(G_CQ_RING, (2 << 16) | 0)
    await top.greg_w(G_CC_RING, (2 << 16) | 16)
    await top.greg_w(G_IRQ_MASK, 0x0F)                  # the command cause unmasked
    await top.greg_w(G_CQ_CTRL, 1)
    t_lo0 = await top.greg_r(G_TIMER_LO)
    t0 = get_sim_time("ns")
    await top.greg_w(G_CQ_TAIL, 2)                      # the doorbell: two commands
    # the first completion: its interrupt, cleared while the second command runs; then the second's
    seen = []
    while len(seen) < 2:
        tail = await top.greg_r(G_CC_TAIL)
        if tail > len(seen):
            assert int(dut.irq.value) == 1 and (await top.greg_r(G_IRQ_STATUS)) & 0x10, f"no interrupt for completion {len(seen) + 1}"
            seen.append(get_sim_time("ns") - t0)
            await top.greg_w(G_IRQ_STATUS, 0x10)
            if len(seen) == 1:
                assert await top.greg_r(G_CC_TAIL) == 1, "the second command completed with the first: its interrupt is not seen apart"
        assert get_sim_time("ns") - t0 < 100_000, f"completions {tail} after 100 us; CQ_STATUS 0x{await top.greg_r(G_CQ_STATUS):x}"
        await ClockCycles(dut.aclk, 8)
    await ClockCycles(dut.aclk, 4)
    assert int(dut.irq.value) == 0
    recs = [[await top.greg_r(G_CMEM + 4 * (16 + 4 * r + k)) for k in range(4)] for r in range(2)]
    assert [r[0] for r in recs] == [0xA11CE001, 0xA11CE002], f"record tags {recs}"
    assert [r[1] for r in recs] == [len(img1) << 16, len(img2) << 16], f"record status {recs}"
    tdone = [r[2] | (r[3] << 32) for r in recs]
    assert t_lo0 < tdone[0] < tdone[1], f"done times {tdone} after the doorbell at {t_lo0}"
    assert await top.greg_r(G_CQ_HEAD) == 2 and (await top.greg_r(G_CQ_STATUS)) == 0
    # layer 1's INT8 rows and layer 2's INT32 result, read back through the host bridge
    got1, r = await top.host.read_words(0, R1, 36 * C1 // 4)
    assert r == OKAY
    exp1 = []
    for m in range(36):
        for k in range(C1 // 4):
            exp1.append(sum((int(q1[4 * k + b, m // 6, m % 6]) & 0xFF) << (8 * b) for b in range(4)))
    assert got1 == exp1, "layer 1's INT8 rows differ from the requantization model"
    got2, r = await top.host.read_words(2, R2, 18 * (COLS + 1))
    assert r == OKAY
    check(to_image(got2, y2, M=18, cout=7, wo=3), y2, "T8 layer 2 through the command queue")
    await top.expect_clean([0, 1, 2])
    dut._log.info(f"T8: two commands ({len(img1)} + {len(img2)} image entries) from one doorbell: layer 1 on tiles 0, 1 (K-split, INT8) "
                  f"complete {seen[0]:.0f} ns after it (done at timer {tdone[0] - t_lo0} aclk), layer 2 on tile 2 {seen[1]:.0f} ns (timer "
                  f"{tdone[1] - t_lo0}); records in order with their tags, an interrupt each; both layers bit-exact")


@cocotb.test()
async def t9_lockstep_single_write(dut):
    """T9 (drop 0.44, review #12): LS_PAIR is one word {enable, a, b} whose bits cross to the mesh clock independently; a
    single write that configures and enables a pair at once showed the enable with the old indices for a clock, so the
    comparison started from the wrong tile's drain count. Here tile a has drained once more than tile b (a layer alone)
    before one write enables the pair: mesh_ctrl now takes the enable three clocks after the indices, so the pair's layer
    ends with LS_ERR clear -- before the fix a spurious mismatch of the message counts."""
    from neo_backend import compile_layer, compile_pair
    top = await setup(dut)
    a, b = node(0, 0), node(1, 0)
    x, wgt, ref, act, wts = conv_data()
    await top.greg_w(G_BR + 0x0, WIN_BASE & 0xFFFFFFFF)
    await top.greg_w(G_BR + 0x4, WIN_BASE >> 32)
    WA, WB = 0x2000, 0x3F1
    top.mem.load(WIN_BASE + 4 * WA, act)
    top.mem.load(WIN_BASE + 4 * WB, wts)
    lp0 = compile_layer("t9a", 6, 6, CIN, 7, 3, 1, 1, ROWS, COLS, NX, NY, act_bank=(BX, 0), act_base=WA, w_bank=(BX, 0), w_base=WB,
                        owner=(0, 0), shares=1, out_base=R0)
    t0 = await run_layer(top, lp0)                        # tile a's drain count is 1, tile b's 0
    await top.greg_w(G_IRQ_MASK, 0x17)
    await top.greg_w(G_LS_PAIR, (1 << 31) | (b << 8) | a)  # one write: indices and enable together
    lp = compile_pair("t9", 6, 6, CIN, 7, 3, 1, 1, ROWS, COLS, NX, NY, act_bank=(BX, 0), act_base=WA, w_bank=(BX, 0), w_base=WB,
                      pair=((0, 0), (1, 0)), out_base=R0)
    t = await run_layer(top, lp)
    await ClockCycles(dut.aclk, 12)
    lse = await top.greg_r(G_LS_ERR)
    st = await top.greg_r(G_IRQ_STATUS)
    assert lse == 0 and not st & 0x8 and int(dut.err.value) == 0, f"T9 spurious lockstep error: LS_ERR {lse} IRQ_STATUS 0x{st:x}"
    for n in (a, b):
        words, r = await top.host.read_words(n, R0, lp.M * (COLS + 1))
        assert r == OKAY
        check(to_image(words, ref), ref, f"T9 tile {n} of the pair")
    await top.greg_w(G_LS_PAIR, 0)
    dut._log.info(f"T9: tile {a} alone in {t0:.0f} ns, then the pair enabled by one LS_PAIR write with unequal drain counts: "
                  f"the pair's layer bit-exact in {t:.0f} ns, LS_ERR 0")


@cocotb.test()
async def t10_host_short_burst(dut):
    """T10 (drop 0.44, review #17): the host bridge against a master that announces four W beats (AWLEN 3) and sends two,
    the second with WLAST. The bridge ends the burst at the WLAST beat (its words written, the CRC flit sent), answers
    SLVERR and sets host in its ERR register; cleared, the next write and read-back through it are clean. Before, WLAST
    was ignored and the write channel waited for the two missing beats forever (every later transaction behind it)."""
    top = await setup(dut)
    n = node(0, 0)
    words = [0x1000_0000 + i for i in range(2 * WPB)]
    beats = [(sum((words[k * WPB + lane] & 0xFFFFFFFF) << (32 * lane) for lane in range(WPB)), (1 << BB) - 1) for k in range(2)]
    resp = await with_timeout(top.host.write_beats(win_addr(n, 0), beats, awlen_beats=4), 20, "us")
    assert resp == SLVERR, f"T10 short burst: BRESP {resp}, SLVERR expected"
    await ClockCycles(dut.aclk, 8)
    be = await top.br_err()
    assert be == 1 << BR_HOST, f"T10 bridge ERR 0x{be:x}: host expected"
    await top.greg_w(G_BR + 0xC, 0x1F)
    await ClockCycles(dut.aclk, 4)
    assert await top.br_err() == 0
    got, r = await top.host.read_words(n, 0, 2 * WPB)   # the two beats sent were written
    assert r == OKAY and got == words, f"T10 the short burst's beats: resp {r}, {sum(g != w for g, w in zip(got, words))} words differ"
    rng = np.random.default_rng(44)
    full = [int(v) for v in rng.integers(0, 1 << 32, size=4 * WPB)]
    resps = await with_timeout(top.host.write_words(n, 64, full), 50, "us")
    assert all(v == OKAY for v in resps), f"T10 the write after the short burst: {resps}"
    got, r = await top.host.read_words(n, 64, len(full))
    assert r == OKAY and got == full, "T10 read-back after the short burst"
    await top.expect_clean([n])
    assert await top.br_err() == 0 and int(dut.err.value) == 0
    dut._log.info("T10: a 2-of-4-beat write burst ended at its WLAST with SLVERR and the bridge's host error; cleared, the next "
                  "write and read-back clean")


@cocotb.test()
async def t11_test_mode(dut):
    """T11 (drop 0.44, review #8): test_en, the scan shift's test enable, reaches every clock gate's te and mesh_ctrl's reset
    bypass. Tile (1,0) is powered down (its clock enable low, its reset asserted by the sequencer): with test_en the tile's
    gated clock runs and its reset follows rst_n (high); with test_en released the sequencer's values return. Nothing of
    the tile's function is read here -- the port exists for the DFT flow, which this test shows it can drive."""
    top = await setup(dut)
    full = (1 << NT) - 1
    off = node(1, 0)
    tile = dut.g_y[0].g_x[1].g_tile.u_t                      # tile (1,0) of the generate
    await top.greg_w(G_TILE_EN, full & ~(1 << off))
    await top.wait_eff(full & ~(1 << off))
    while await top.pwr_state(off) != PWR_OFF:
        await ClockCycles(dut.aclk, 4)

    async def sample(cycles=8):
        """(tile_rst_n of the tile, the number of clk cycles in which its gated clock clk_t was high at the rising edge)."""
        rst, high = None, 0
        for _ in range(cycles):
            await RisingEdge(dut.clk)
            await ReadOnly()
            rst = (int(dut.u_mctrl.tile_rst_n.value) >> off) & 1
            high += int(tile.clk_t.value)
        await RisingEdge(dut.clk)
        return rst, high
    rst0, clk0 = await sample()
    assert rst0 == 0 and clk0 == 0, f"T11 powered down: tile_rst_n {rst0}, gated clock high in {clk0} of 8 cycles (expect 0, 0)"
    dut.test_en.value = 1
    await ClockCycles(dut.clk, 3)
    rst1, clk1 = await sample()
    assert rst1 == 1 and clk1 == 8, f"T11 test_en: tile_rst_n {rst1} (expect rst_n = 1), gated clock high in {clk1} of 8 cycles (expect 8)"
    dut.test_en.value = 0
    await ClockCycles(dut.clk, 3)
    rst2, clk2 = await sample()
    assert rst2 == 0 and clk2 == 0, f"T11 released: tile_rst_n {rst2}, gated clock high in {clk2} of 8 (expect 0, 0)"
    await top.greg_w(G_TILE_EN, full)                       # back on, as T6
    await top.wait_eff(full)
    assert await top.treg_r(off, REG_ERR_CAUSE) == 0
    dut._log.info("T11: test_en forced the powered-down tile's gated clock on and its reset to rst_n; released, the sequencer's "
                  "clock-off and reset returned")
