"""test_neo_top.py -- the chip top's suite under cocotb on Verilator (drop 0.44).

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
      running tile stays pending until its program ends; the tiles re-enabled while idle answer again
"""
import os
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly, ClockCycles, Lock
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

    async def write_beats(self, addr, beats, size=None, burst=1, axid=3):
        """One burst: AW and W driven concurrently (the master must not wait for AWREADY before WVALID); returns (bresp, bid)."""
        d = self.d
        size = (BB.bit_length() - 1) if size is None else size
        async with self.lock:
            aw = cocotb.start_soon(self._aw(addr, len(beats), size, burst, axid))
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
    beats checked against the 4 KB rule, a read latency, SLVERR on chosen byte ranges; every burst logged."""

    def __init__(self, dut, latency=3):
        self.d, self.clk, self.latency = dut, dut.aclk, latency
        self.mem = {}
        self.err_ranges = []
        self.reads, self.writes = [], []
        for s in ("arready", "rvalid", "awready", "wready", "bvalid"):
            getattr(dut, "m_axi_" + s).value = 0
        cocotb.start_soon(self._reads())
        cocotb.start_soon(self._writes())

    def word(self, a):
        return self.mem.get(a & ~3, 0)

    def load(self, byte_addr, words):
        for i, w in enumerate(words):
            self.mem[byte_addr + 4 * i] = w & 0xFFFFFFFF

    def burst_ok(self, addr, nbeats, size, burst):
        assert size == BB.bit_length() - 1 and burst == 1, f"burst size {size} type {burst}"
        assert addr % BB == 0, f"burst address 0x{addr:x} not beat-aligned"
        assert (addr % 4096) + nbeats * BB <= 4096, f"burst at 0x{addr:x} of {nbeats} beats crosses a 4 KB boundary"

    async def _reads(self):
        d = self.d
        while True:
            d.m_axi_arready.value = 1                     # the AR handshake: the address sampled in the cycle before its edge
            addr, nb, size, burst = await handshake(self.clk, d.m_axi_arvalid, lambda: (
                int(d.m_axi_araddr.value), int(d.m_axi_arlen.value) + 1, int(d.m_axi_arsize.value), int(d.m_axi_arburst.value)))
            d.m_axi_arready.value = 0
            self.burst_ok(addr, nb, size, burst)
            self.reads.append((addr, nb))
            for _ in range(self.latency):
                await RisingEdge(self.clk)
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
    assert await top.greg_r(G_VERSION) == 0x44, "VERSION"
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
    assert await top.br_err() == 0 and await top.greg_r(G_IRQ_STATUS) == 0 and await top.greg_r(G_IRQ_MASK) == 7
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
    # re-enabled while idle: they answer again
    await top.greg_w(G_TILE_EN, full)
    await ClockCycles(dut.aclk, 8)
    assert await top.greg_r(G_TILE_EN_EFF) == full
    await top.treg_w(off, REG_PARTITION, 3)
    assert await top.treg_r(off, REG_PARTITION) == 3
    c = await top.treg_r(n2, REG_ERR_CAUSE)
    assert (c >> CAUSE_TIMEOUT) & 1, f"tile {n2}'s fetch: cause 0x{c:05x}"
    dut._log.info(f"T5: tile {off} disabled at reset -- registers SLVERR, host read SLVERR after {t_host:.0f} ns, a tile's fetch timed out; "
                  f"the layer on {len(lp.tiles)} tiles with the exclusion map bit-exact in {t:.0f} ns; a change for a running tile held until its "
                  f"program ended; re-enabled while idle")
