"""test_tile_mesh.py -- the mesh suite on tile_mesh under cocotb (Verilator or Icarus), drop 0.21.

Ports of sim/run_tiles.py's M-tests, driven the way the island's firmware drives a tile: everything through the
host register bus (program, descriptor, start, status, causes), banks loaded and read through the wrapper's
backdoor as (39,32) codewords, faults injected through the wrapper's fault port at the falling clock edge (the
same "poke between two ticks" the neosim tests do). Each test is independent and resets the mesh.
  M1  fetch from a remote bank, compute, write back to another tile's bank, bit-exact
  M2  K-split: owner + two contributors with the RDY handshake, result bit-exact
  M3  end-to-end CRC: a fetch-response payload bit flipped past the last link parity check is flagged
  M4  two-region activation buffer refilled behind the running core (WAIT_FREE), bit-exact
  M5  bank ECC: data and check bits flipped in the source bank corrected in flight, ecc_ce on that tile only
  M6  compiler-emitted layer program executed as emitted (owner + 2 contributors)
  M7  two M-chunks with partial activation fetch (cfg_oy0/oy_n/iy0), concatenated bit-exact
  M8  register-driven self-test (fault hook on a dummy run raises the ABFT causes and the pin), clear,
      real run clean, watchdog left un-kicked raises its cause
  M9  lost flit (word count in the CRC flit) and fetch timeout (starved request completes with the flag)
  M11 bank MBIST through the registers: clean pass, then a stuck bit found by the ECC with its address
  M12 control path: program-memory SECDED, duplicated descriptor registers, lockstep DMA engines
  M13 spatial partitions: a cross-partition fetch is dropped and flagged; inside one partition it is clean
Run: make -C dv/cocotb tile   (tile_mesh_cocotb wrapper: 2x2, 16x8 cores, FETCH_TIMEOUT=8192, BIST_WORDS=16)
     make -C dv/cocotb tile32 (the silicon core and depths: 32x32 cores, ACC_ROWS 512, ABUF 2048, WBUF 1024, 2 MB banks)
The geometry and the fetch timeout come from the environment (NEO_NX, NEO_NY, NEO_ROWS, NEO_COLS,
NEO_FETCH_TIMEOUT), exported by the Makefile to match its -G parameters; test_two_layer.py (M10) reuses the
harness on the 8x8-core build. The convolution keeps three channel tiles at any core size (CIN = 3*ROWS - 8, the
last tile partly filled), the bank layout follows the entry sizes, and weights are fetched one channel tile per
DMA instruction (the length field is 12 bits: one 32x32 tile is 2,592 words).
Timing notes (Verilator): after `await RisingEdge(clk)` every signal already shows its post-edge value, and a
value written then is applied in the same time step, i.e. it is seen by the falling edge that follows and by the
next rising edge. The fault port relies on that: detect at a rising edge, raise fi_en, the fault lands at the
falling edge, the design samples it at the next rising edge.
"""
import os
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly, ClockCycles
import numpy as np
from neo_golden import (pack_words, direct_conv, ecc_encode, ins, bits, sbits, OP_FETCH_A, OP_FETCH_W, OP_DRAIN_WR,
                        OP_DRAIN_PSUM, OP_GO, OP_WAIT_FREE, OP_WAIT_DONE, OP_END)
import neo_funcov as fc

OP_NOTIFY, OP_WAIT_RDY, OP_WAIT_REDUCE = 9, 10, 11
NX, NY = int(os.environ.get("NEO_NX", "2")), int(os.environ.get("NEO_NY", "2"))
ROWS, COLS = int(os.environ.get("NEO_ROWS", "16")), int(os.environ.get("NEO_COLS", "8"))
FETCH_TIMEOUT = int(os.environ.get("NEO_FETCH_TIMEOUT", "8192"))     # cycles; must match the build's -GFETCH_TIMEOUT
WCW = 8 + (ROWS - 1).bit_length() + 1
WPA, WPW = ROWS * 8 // 32, (COLS * 8 + WCW + 31) // 32
REG_CTRL, REG_STATUS, REG_PROG_ADDR, REG_PROG_LO, REG_PROG_HI, REG_ERR_MASK, REG_ERR_CAUSE = 0, 1, 2, 3, 4, 5, 6
REG_WD_CTRL, REG_WD_KICK, REG_SELFTEST, REG_MBIST, REG_PARTITION = 7, 8, 9, 0x0A, 0x0B
REG_CFG_BASE, REG_CFG_M = 0x10, 0x24
REG_RQ_TBL, REG_RQ_ADDR, REG_RQ_RELU = 0x30, 0x31, 0x32
CAUSE_PROG_CE, CAUSE_CTRL_PATH, CAUSE_ISO, CAUSE_TIMEOUT, CAUSE_LOST, CAUSE_BIST = 15, 16, 17, 13, 12, 14
CFG_ORDER = ["cfg_h", "cfg_w", "cfg_ho", "cfg_wo", "cfg_oy0", "cfg_oy_n", "cfg_iy0", "cfg_s", "cfg_p", "cfg_k", "cfg_ct_n", "cfg_ct0",
             "cfg_ky0", "cfg_kx0", "cfg_rn", "cfg_contrib_n", "cfg_tile_pixels", "cfg_regions_m1"]
T_RDRSP = 2
CIN = 3 * ROWS - 8                                   # 40 channels at 16 rows, 88 at 32: three channel tiles, the last partly filled
CT_N = (CIN + ROWS - 1) // ROWS
assert CT_N == 3, "the suite is written for three channel tiles (M2 gives one to each of three tiles)"
APT, WPT = 36 * WPA, 9 * ROWS * WPW                  # bank words per activation tile (36 pixels) and per weight tile (9 runs x ROWS entries)


def _align(n, a=256):
    return (n + a - 1) // a * a


A0 = 0                                               # bank layout: activations, then weights, then the result rows (0 / 512 / 2048 at 16x8)
W0 = _align(CT_N * APT)
R0 = _align(W0 + CT_N * WPT)
# fault port selectors (tb/tile_mesh_cocotb.sv)
FI_NONE, FI_BANK_OR, FI_PROG_XOR, FI_CFG_XOR, FI_PC_XOR, FI_FLIT_XOR, FI_VALID_CLR, FI_SERVE_HOLD = range(8)


def xy(n):
    return n % NX, n // NX


def causes_str(c):
    return "[" + ", ".join("0x%05x" % v for v in c) + "]"


def check_rows(got, ref, tag):
    """Bit-exact comparison with a diagnostic message: how many values differ and the first few of them."""
    if np.array_equal(got, ref):
        return
    bad = np.argwhere(got != ref)
    first = "; ".join(f"{tuple(int(i) for i in idx)} got {int(got[tuple(idx)])} exp {int(ref[tuple(idx)])}" for idx in bad[:6])
    raise AssertionError(f"{tag}: {len(bad)}/{ref.size} values differ: {first}")


class Mesh:
    """Host-side view of the mesh through the packed-port wrapper tile_mesh_cocotb: registers, programs,
    bank backdoor, fault port, router observation, causes. Per-tile inputs are kept as shadow values and
    written as packed vectors."""

    def __init__(self, dut):
        self.dut = dut
        self.N = NX * NY
        self.we = [0] * self.N
        self.re = [0] * self.N
        self.addr = [0] * self.N
        self.wdata = [0] * self.N

    def _drive(self):
        self.dut.bd_we.value = 0
        self.dut.h_we.value = sum(v << n for n, v in enumerate(self.we))
        self.dut.h_re.value = sum(v << n for n, v in enumerate(self.re))
        self.dut.h_addr.value = sum((v & 0xFF) << (8 * n) for n, v in enumerate(self.addr))
        self.dut.h_wdata.value = sum((v & 0xFFFFFFFF) << (32 * n) for n, v in enumerate(self.wdata))

    def status(self, name, n):
        return (int(getattr(self.dut, name).value) >> n) & 1

    async def reset(self):
        self.we = [0] * self.N; self.re = [0] * self.N; self.addr = [0] * self.N; self.wdata = [0] * self.N
        self._drive()
        self.dut.bd_node.value = 0; self.dut.bd_addr.value = 0; self.dut.bd_wdata.value = 0
        self.fault_set(0, FI_NONE, 0, 0, 0)
        self.dut.rst_n.value = 0
        await ClockCycles(self.dut.clk, 4)
        self.dut.rst_n.value = 1
        await ClockCycles(self.dut.clk, 2)

    async def tick(self, n=1):
        await ClockCycles(self.dut.clk, n)

    # ---- host register bus ----
    async def reg_write(self, node, addr, val):
        self.we[node] = 1; self.addr[node] = addr; self.wdata[node] = val & 0xFFFFFFFF
        self._drive()
        await RisingEdge(self.dut.clk)
        self.we[node] = 0
        self._drive()

    async def reg_read(self, node, addr):
        self.re[node] = 1; self.addr[node] = addr
        self._drive()
        await RisingEdge(self.dut.clk)
        self.re[node] = 0
        self._drive()
        await ReadOnly()
        v = (int(self.dut.h_rdata.value) >> (32 * node)) & 0xFFFFFFFF
        await RisingEdge(self.dut.clk)
        return v

    async def program(self, node, words):
        for a, wd in enumerate(words):
            fc.sample_dma_op((wd >> 60) & 0xF)
            await self.reg_write(node, REG_PROG_ADDR, a)
            await self.reg_write(node, REG_PROG_LO, wd & 0xFFFFFFFF)
            await self.reg_write(node, REG_PROG_HI, wd >> 32)
        await self.tick(1)          # prog_we is registered: the last word lands one clock after the PROG_HI write

    async def start(self, nodes):
        for n in nodes:
            await self.reg_write(n, REG_CTRL, 1)
        await self.tick(2)

    async def clear_errors(self, node):
        await self.reg_write(node, REG_CTRL, 2)
        await self.tick(1)

    async def set_cfg(self, node, name, value):
        if name == "cfg_m":
            await self.reg_write(node, REG_CFG_M, value)
        else:
            await self.reg_write(node, REG_CFG_BASE + CFG_ORDER.index(name), bits(value, 16))

    async def descriptor(self, node, h, w, ho, wo, s, p, k, ct_n, ct0, rn, contrib_n, M, regions=None, oy0=0, oy_n=None, iy0=0,
                         tile_pixels=None):
        d = dict(cfg_h=h, cfg_w=w, cfg_ho=ho, cfg_wo=wo, cfg_s=s, cfg_p=p, cfg_k=k, cfg_ct_n=ct_n, cfg_ct0=ct0, cfg_ky0=0, cfg_kx0=0,
                 cfg_rn=rn, cfg_contrib_n=contrib_n, cfg_tile_pixels=(h * w if tile_pixels is None else tile_pixels),
                 cfg_regions_m1=(0xFFFF if regions is None else regions - 1), cfg_oy0=oy0, cfg_oy_n=(ho if oy_n is None else oy_n), cfg_iy0=iy0)
        fc.sample_descriptor(d)
        for name, v in d.items():
            await self.set_cfg(node, name, v)
        await self.set_cfg(node, "cfg_m", M)

    # ---- bank backdoor through the wrapper's bd_* port: raw 39-bit codewords ----
    async def bank_poke(self, node, addr, word39):
        self.dut.bd_node.value = node
        self.dut.bd_addr.value = addr
        self.dut.bd_wdata.value = word39 & ((1 << 39) - 1)
        self.dut.bd_we.value = 1
        await RisingEdge(self.dut.clk)
        self.dut.bd_we.value = 0

    async def bank_peek(self, node, addr):
        self.dut.bd_node.value = node
        self.dut.bd_addr.value = addr
        await ReadOnly()
        v = int(self.dut.bd_rdata.value)
        await RisingEdge(self.dut.clk)
        return v

    async def bank_write(self, node, addr, word):
        p, op = ecc_encode(word)
        await self.bank_poke(node, addr, (op << 38) | (p << 32) | (word & 0xFFFFFFFF))

    async def bank_read(self, node, addr):
        return (await self.bank_peek(node, addr)) & 0xFFFFFFFF

    async def bank_flip(self, node, addr, bit):
        await self.bank_poke(node, addr, (await self.bank_peek(node, addr)) ^ (1 << bit))

    async def load_words(self, node, base, words):
        for i, wd in enumerate(words):
            await self.bank_write(node, base + i, wd)
        # backdoor loopback: the first and the last word read back as written
        for addr, wd in ((base, words[0]), (base + len(words) - 1, words[-1])):
            rb = await self.bank_read(node, addr)
            assert rb == (wd & 0xFFFFFFFF), f"bank backdoor: node {node} word {addr} reads 0x{rb:08x}, wrote 0x{wd & 0xFFFFFFFF:08x}"

    async def load_bank(self, node, act, wts, a0=A0, w0=W0):
        await self.load_words(node, a0, act)
        await self.load_words(node, w0, wts)

    async def read_rows(self, node, addr, M):
        out = np.zeros((M, COLS), dtype=np.int64)
        for m in range(M):
            for j in range(COLS):
                out[m, j] = sbits(await self.bank_read(node, addr + m * (COLS + 1) + j), 32)
        return out

    # ---- fault port: applied at every falling edge while fi_en is high ----
    def fault_set(self, node, sel, idx=0, mask=0, en=1):
        self.dut.fi_node.value = node
        self.dut.fi_sel.value = sel
        self.dut.fi_idx.value = idx & 0xFFFFF
        self.dut.fi_mask.value = mask & ((1 << 64) - 1)
        self.dut.fi_en.value = en

    def fault_hold(self, node, sel, idx=0, mask=0):
        """The fault is applied at every falling edge until fault_release()."""
        self.fault_set(node, sel, idx, mask, 1)

    def fault_arm(self, node, sel, idx=0, mask=0):
        """Selected but not enabled; fi_node also selects the observed router. Raise fi_en for one clock to apply once."""
        self.fault_set(node, sel, idx, mask, 0)

    def fault_release(self):
        self.dut.fi_en.value = 0

    async def fault_once(self, node, sel, idx=0, mask=0):
        """Applied at exactly one falling edge (call after a rising edge, never from the read-only phase)."""
        self.fault_set(node, sel, idx, mask, 1)
        await RisingEdge(self.dut.clk)
        self.dut.fi_en.value = 0

    def router_valid(self, port):
        return (int(self.dut.ob_out_valid.value) >> port) & 1

    def router_flit4(self):
        return int(self.dut.ob_out_flit4.value)

    # ---- status ----
    def prog_done(self, node):
        return self.status("prog_done", node)

    def drain_busy(self, node):
        return self.status("drain_busy", node)

    async def wait_prog(self, node, limit=60000):
        n = 0
        while True:
            await RisingEdge(self.dut.clk)
            await ReadOnly()
            if self.prog_done(node) and not self.drain_busy(node):
                break
            n += 1
            assert n < limit, f"tile {node}: program did not finish within {limit} clocks"
        await RisingEdge(self.dut.clk)
        return n

    async def causes(self):
        return [await self.reg_read(n, REG_ERR_CAUSE) for n in range(self.N)]

    def pins(self):
        return [self.status("err_pin", n) for n in range(self.N)]


def conv_data(seed=17, cin=CIN, cout=7):
    rng = np.random.default_rng(seed)
    x = rng.integers(-128, 128, size=(cin, 6, 6), dtype=np.int64)
    wgt = rng.integers(-128, 128, size=(cout, cin, 3, 3), dtype=np.int64)
    ref = direct_conv(x, wgt, 1, 1)
    act, wts = pack_words(x, wgt, (cin + ROWS - 1) // ROWS, 3, cout, rows=ROWS, cols=COLS)
    return x, wgt, ref, act, wts


def fetch_weights(x, y, w0=W0):
    """One FETCH_W per channel tile from bank (x, y): the DMA length field holds 12 bits, so a 32x32 tile (2,592 words)
    must be its own instruction; weight-buffer entry base = ct * 9 * ROWS."""
    return [ins(OP_FETCH_W, x, y, w0 + ct * WPT, WPT, ct * 9 * ROWS) for ct in range(CT_N)]


def to_image(out, ref, M, cout=7, wo=6):
    got = np.zeros_like(ref)
    for m in range(M):
        for c in range(cout):
            got[c, m // wo, m % wo] = out[m, c]
    return got


async def setup(dut):
    cocotb.start_soon(Clock(dut.clk, 500, units="ps").start())
    mesh = Mesh(dut)
    await mesh.reset()
    return mesh


async def expect_clean(mesh, nodes=None):
    c = await mesh.causes()
    nodes = range(mesh.N) if nodes is None else nodes
    assert all(c[n] == 0 for n in nodes), f"causes {causes_str(c)}"


async def check_result(mesh, got, ref, tag):
    """Bit-exact check that reports the causes alongside a mismatch (a timed-out or lost fetch shows up here)."""
    if not np.array_equal(got, ref):
        c = await mesh.causes()
        check_rows(got, ref, f"{tag} (causes {causes_str(c)}, pins {mesh.pins()})")


# ---------------------------------------------------------------------------------------------------
@cocotb.test()
async def m1_fetch_compute_writeback(dut):
    mesh = await setup(dut)
    x, wgt, ref, act, wts = conv_data()
    await mesh.load_bank(3, act, wts)
    await mesh.descriptor(0, 6, 6, 6, 6, 1, 1, 3, CT_N, 0, CT_N * 9, 0, 36)
    prog = [ins(OP_FETCH_A, 1, 1, A0 + ct * APT, APT, ct * 36) for ct in range(CT_N)]
    prog += fetch_weights(1, 1) + [ins(OP_DRAIN_WR, 1, 0, R0, 36), ins(OP_GO), ins(OP_WAIT_DONE), ins(OP_END)]
    await mesh.program(0, prog)
    await mesh.start([0])
    n = await mesh.wait_prog(0)
    await mesh.tick(40)
    got = to_image(await mesh.read_rows(1, R0, 36), ref, 36)
    await check_result(mesh, got, ref, "M1")
    await expect_clean(mesh)
    fc.sample_drain("int32")
    dut._log.info(f"M1: bit-exact in {n} clocks, causes clean")


@cocotb.test()
async def m2_ksplit_rdy(dut):
    mesh = await setup(dut)
    x, wgt, ref, act, wts = conv_data()
    await mesh.load_bank(3, act, wts)
    owner, contribs = 0, [1, 2]
    for n, ct in ((owner, 0), (contribs[0], 1), (contribs[1], 2)):
        await mesh.descriptor(n, 6, 6, 6, 6, 1, 1, 3, CT_N, ct, 9, 2 if n == owner else 0, 36)
        fetch = [ins(OP_FETCH_A, 1, 1, A0 + ct * APT, APT, ct * 36), ins(OP_FETCH_W, 1, 1, W0 + ct * WPT, WPT, 0)]
        if n == owner:
            prog = fetch + [ins(OP_DRAIN_WR, 1, 1, R0, 36), ins(OP_GO), ins(OP_WAIT_REDUCE)]
            prog += [ins(OP_NOTIFY, xy(c)[0], xy(c)[1]) for c in contribs] + [ins(OP_WAIT_DONE), ins(OP_END)]
        else:
            prog = fetch + [ins(OP_GO), ins(OP_WAIT_RDY), ins(OP_DRAIN_PSUM, xy(owner)[0], xy(owner)[1], 0, 36), ins(OP_WAIT_DONE), ins(OP_END)]
        await mesh.program(n, prog)
    await mesh.start([owner, *contribs])
    n = await mesh.wait_prog(owner)
    await mesh.tick(40)
    got = to_image(await mesh.read_rows(3, R0, 36), ref, 36)
    await check_result(mesh, got, ref, "M2")
    await expect_clean(mesh)
    fc.sample_drain("psum")
    dut._log.info(f"M2: K-split with RDY handshake bit-exact in {n} clocks")


async def corrupt_local_response(mesh, dut, sel, mask, tag):
    """Wait for a fetch-response flit in tile 0's router local-port output register, apply fault `sel` to it at that
    cycle's falling edge (before the NIC takes it), then let the program finish. Returns the clock count."""
    mesh.fault_arm(0, sel, idx=4, mask=mask)
    applied, n = False, 0
    while True:
        await RisingEdge(dut.clk)
        if applied:
            dut.fi_en.value = 0                                   # the fault was applied at the last falling edge
        if mesh.prog_done(0):
            break
        if not applied and n > 40 and mesh.router_valid(4) and ((mesh.router_flit4() >> 61) & 7) == T_RDRSP:
            dut.fi_en.value = 1
            applied = True
        n += 1
        assert n < 20000, f"{tag}: fetch did not complete"
    assert applied, f"{tag}: no fetch-response flit observed at tile 0's local port"
    return n


@cocotb.test()
async def m3_end_to_end_crc(dut):
    mesh = await setup(dut)
    x, wgt, ref, act, wts = conv_data()
    await mesh.load_bank(3, act, wts)
    await mesh.program(0, [ins(OP_FETCH_A, 1, 1, A0, APT, 0), ins(OP_END)])
    await mesh.start([0])
    await corrupt_local_response(mesh, dut, FI_FLIT_XOR, 1 << 5, "M3")   # payload bit, past the last link parity check
    await mesh.tick(5)
    c = await mesh.causes()
    assert (c[0] >> 1) & 1 == 1 and all((v & 1) == 0 for v in c), f"M3 causes {causes_str(c)}"
    fc.sample_flag("crc")
    dut._log.info("M3: corrupted payload past link parity flagged by the end-to-end CRC, parity clean")


@cocotb.test()
async def m4_region_refill(dut):
    mesh = await setup(dut)
    x, wgt, ref, act, wts = conv_data()
    await mesh.load_bank(3, act, wts)
    await mesh.descriptor(0, 6, 6, 6, 6, 1, 1, 3, CT_N, 0, CT_N * 9, 0, 36, regions=2)
    prog = fetch_weights(1, 1) + [ins(OP_FETCH_A, 1, 1, A0, APT, 0), ins(OP_FETCH_A, 1, 1, A0 + APT, APT, 36),
            ins(OP_DRAIN_WR, 1, 0, R0, 36), ins(OP_GO), ins(OP_WAIT_FREE, arg=0), ins(OP_FETCH_A, 1, 1, A0 + 2 * APT, APT, 0),
            ins(OP_WAIT_DONE), ins(OP_END)]
    await mesh.program(0, prog)
    await mesh.start([0])
    n = await mesh.wait_prog(0)
    await mesh.tick(40)
    got = to_image(await mesh.read_rows(1, R0, 36), ref, 36)
    await check_result(mesh, got, ref, "M4")
    await expect_clean(mesh)
    dut._log.info(f"M4: two-region buffer refilled behind the core, bit-exact in {n} clocks")


@cocotb.test()
async def m5_bank_ecc_in_flight(dut):
    mesh = await setup(dut)
    x, wgt, ref, act, wts = conv_data()
    await mesh.load_bank(3, act, wts)
    await mesh.bank_flip(3, A0 + 40, 13)
    await mesh.bank_flip(3, W0 + 7, 35)
    await mesh.descriptor(0, 6, 6, 6, 6, 1, 1, 3, CT_N, 0, CT_N * 9, 0, 36)
    prog = [ins(OP_FETCH_A, 1, 1, A0 + ct * APT, APT, ct * 36) for ct in range(CT_N)]
    prog += fetch_weights(1, 1) + [ins(OP_DRAIN_WR, 1, 0, R0, 36), ins(OP_GO), ins(OP_WAIT_DONE), ins(OP_END)]
    await mesh.program(0, prog)
    await mesh.start([0])
    await mesh.wait_prog(0)
    await mesh.tick(40)
    got = to_image(await mesh.read_rows(1, R0, 36), ref, 36)
    await check_result(mesh, got, ref, "M5")
    c = await mesh.causes()
    assert (c[3] >> 8) & 1 == 1 and (c[3] >> 7) & 1 == 0 and c[0] == 0 and c[1] == 0 and c[2] == 0, f"M5 causes {causes_str(c)}"
    fc.sample_flag("ecc_ce")
    dut._log.info("M5: flipped data and check bits corrected in flight; ecc_ce on the source tile only")


@cocotb.test()
async def m6_compiler_program(dut):
    from neo_backend import compile_layer
    mesh = await setup(dut)
    x, wgt, ref, act, wts = conv_data()
    await mesh.load_bank(3, act, wts)
    lp = compile_layer("c1", 6, 6, CIN, 7, 3, 1, 1, ROWS, COLS, NX, NY, act_bank=(1, 1), act_base=A0, w_bank=(1, 1), w_base=W0,
                       owner=(0, 0), shares=3)
    nodes = []
    for tp in lp.tiles:
        n = tp.tile[1] * NX + tp.tile[0]
        nodes.append(n)
        for name, v in tp.descriptor.items():
            await mesh.set_cfg(n, name, v)
        await mesh.program(n, tp.program)
    await mesh.start(nodes)
    onode = lp.out_bank[1] * NX + lp.out_bank[0]
    n = await mesh.wait_prog(onode)
    await mesh.tick(40)
    got = to_image(await mesh.read_rows(onode, lp.out_base, 36), ref, 36)
    await check_result(mesh, got, ref, "M6")
    await expect_clean(mesh)
    dut._log.info(f"M6: compiler-emitted program ({len(lp.tiles)} tiles) bit-exact in {n} clocks")


@cocotb.test()
async def m7_mchunks_partial_fetch(dut):
    from neo_backend import compile_layer
    mesh = await setup(dut)
    x, wgt, ref, act, wts = conv_data()
    await mesh.load_bank(3, act, wts)
    lps = [compile_layer("r0", 6, 6, CIN, 7, 3, 1, 1, ROWS, COLS, NX, NY, (1, 1), A0, (1, 1), W0, owner=(0, 0), shares=1, oy0=0, oy_n=3),
           compile_layer("r3", 6, 6, CIN, 7, 3, 1, 1, ROWS, COLS, NX, NY, (1, 1), A0, (1, 1), W0, owner=(1, 0), shares=1, oy0=3, oy_n=3)]
    nodes = []
    for lp in lps:
        for tp in lp.tiles:
            n = tp.tile[1] * NX + tp.tile[0]
            nodes.append(n)
            for name, v in tp.descriptor.items():
                await mesh.set_cfg(n, name, v)
            await mesh.program(n, tp.program)
    await mesh.start(nodes)
    for lp in lps:
        await mesh.wait_prog(lp.out_bank[1] * NX + lp.out_bank[0])
    await mesh.tick(40)
    got = np.zeros_like(ref)
    for lp, oy0 in zip(lps, (0, 3)):
        out = await mesh.read_rows(lp.out_bank[1] * NX + lp.out_bank[0], lp.out_base, lp.M)
        for m in range(lp.M):
            for c in range(7):
                got[c, oy0 + m // 6, m % 6] = out[m, c]
    await check_result(mesh, got, ref, "M7")
    await expect_clean(mesh)
    dut._log.info("M7: two M-chunks with partial activation fetch, concatenated bit-exact")


@cocotb.test()
async def m8_registers_selftest_watchdog(dut):
    mesh = await setup(dut)
    x, wgt, ref, act, wts = conv_data()
    await mesh.load_bank(3, act, wts)
    await mesh.reg_write(0, REG_ERR_MASK, 1 << 8)
    await mesh.reg_write(0, REG_WD_CTRL, (600 << 8) | 1)
    await mesh.descriptor(0, 6, 6, 1, 1, 1, 1, 3, 1, 0, 9, 0, 1, tile_pixels=36)
    await mesh.reg_write(0, REG_SELFTEST, 1)
    await mesh.program(0, [ins(OP_FETCH_A, 1, 1, A0, APT, 0), ins(OP_FETCH_W, 1, 1, W0, 9 * ROWS * WPW, 0),
                           ins(OP_DRAIN_WR, 1, 0, R0, 1), ins(OP_GO), ins(OP_WAIT_DONE), ins(OP_END)])
    await mesh.start([0])
    n = 0
    while True:
        await RisingEdge(dut.clk)
        await ReadOnly()
        if mesh.prog_done(0):
            break
        n += 1
        assert n < 20000, "M8 self-test run did not finish"
        if n % 400 == 0:
            await RisingEdge(dut.clk)
            await mesh.reg_write(0, REG_WD_KICK, 0)
    await RisingEdge(dut.clk)
    await mesh.reg_write(0, REG_WD_KICK, 0)
    c_st = await mesh.reg_read(0, REG_ERR_CAUSE)
    pin_st = mesh.pins()[0]
    assert (c_st >> 2) & 1 == 1 and pin_st == 1, f"M8 self-test cause 0x{c_st:05x} pin {pin_st}"
    await mesh.reg_write(0, REG_SELFTEST, 0)
    await mesh.clear_errors(0)
    c_cl = await mesh.reg_read(0, REG_ERR_CAUSE)
    assert c_cl == 0, f"M8 causes not clear after the clear: 0x{c_cl:05x}"
    # real one-channel-tile run, watchdog kicked. Reloading the bank through the backdoor takes more clocks than the
    # 600-cycle window, so the watchdog is disabled for the reload (its counter restarts on enable) and re-enabled
    # before the run.
    await mesh.reg_write(0, REG_WD_CTRL, 0)
    x16, w16 = x[:ROWS], wgt[:, :ROWS]
    ref16 = direct_conv(x16, w16, 1, 1)
    act16, wts16 = pack_words(x16, w16, 1, 3, 7, rows=ROWS, cols=COLS)
    await mesh.load_bank(3, act16, wts16)
    await mesh.descriptor(0, 6, 6, 6, 6, 1, 1, 3, 1, 0, 9, 0, 36)
    await mesh.program(0, [ins(OP_FETCH_A, 1, 1, A0, APT, 0), ins(OP_FETCH_W, 1, 1, W0, WPT, 0), ins(OP_DRAIN_WR, 1, 0, R0, 36),
                           ins(OP_GO), ins(OP_WAIT_DONE), ins(OP_END)])
    await mesh.reg_write(0, REG_WD_CTRL, (600 << 8) | 1)
    await mesh.start([0])
    n = 0
    while True:
        await RisingEdge(dut.clk)
        await ReadOnly()
        if mesh.prog_done(0) and not mesh.drain_busy(0):
            break
        n += 1
        assert n < 20000, "M8 real run did not finish"
        if n % 400 == 0:
            await RisingEdge(dut.clk)
            await mesh.reg_write(0, REG_WD_KICK, 0)
    await RisingEdge(dut.clk)
    await mesh.tick(40)
    await mesh.reg_write(0, REG_WD_KICK, 0)
    got = to_image(await mesh.read_rows(1, R0, 36), ref16, 36)
    status = await mesh.reg_read(0, REG_STATUS)
    c_run = await mesh.reg_read(0, REG_ERR_CAUSE)
    await check_result(mesh, got, ref16, "M8 real run")
    assert c_run == 0, f"M8 real run cause 0x{c_run:05x}"
    assert mesh.pins()[0] == 0, "M8 error pin raised after the real run"
    assert (status & 1) == 1, f"M8 status 0x{status:08x} (done expected)"
    await mesh.tick(650)
    c_wd = await mesh.reg_read(0, REG_ERR_CAUSE)
    assert (c_wd >> 18) & 1 == 1 and mesh.pins()[0] == 1, f"M8 watchdog cause 0x{c_wd:05x} pin {mesh.pins()[0]}"
    fc.sample_flag("watchdog")
    dut._log.info("M8: self-test raised the ABFT causes and the pin, clear, real run clean, watchdog expiry flagged")


@cocotb.test()
async def m9_lost_flit_and_timeout(dut):
    mesh = await setup(dut)
    x, wgt, ref, act, wts = conv_data()
    await mesh.load_bank(3, act, wts)
    await mesh.program(0, [ins(OP_FETCH_A, 1, 1, A0, APT, 0), ins(OP_END)])
    await mesh.start([0])
    await corrupt_local_response(mesh, dut, FI_VALID_CLR, 0, "M9a")        # the flit vanishes at the owner's port
    await mesh.tick(5)
    c = await mesh.causes()
    assert (c[0] >> CAUSE_LOST) & 1 == 1 and (c[0] >> 1) & 1 == 1, f"M9a causes {causes_str(c)}"
    fc.sample_flag("lost")
    # starved request: the server tile's interface held busy, the requester times out and completes
    await mesh.reset()
    mesh.fault_hold(3, FI_SERVE_HOLD)
    await mesh.program(0, [ins(OP_FETCH_A, 1, 1, A0, 8, 0), ins(OP_END)])
    await mesh.start([0])
    n = 0
    while True:
        await RisingEdge(dut.clk)
        if mesh.prog_done(0):
            break
        n += 1
        assert n < FETCH_TIMEOUT + 500, "M9b: timeout did not fire"
    mesh.fault_release()
    c = await mesh.causes()
    assert (c[0] >> CAUSE_TIMEOUT) & 1 == 1, f"M9b causes {causes_str(c)}"
    fc.sample_flag("timeout")
    dut._log.info(f"M9: dropped flit flagged (lost + crc); starved request timed out after {n} clocks and completed")


@cocotb.test()
async def m11_mbist_via_registers(dut):
    mesh = await setup(dut)
    await mesh.reg_write(0, REG_MBIST, 1)
    await mesh.tick(2)
    n = 0
    while not (await mesh.reg_read(0, REG_MBIST) & 2):
        n += 1
        assert n < 4000, "M11: clean MBIST did not finish"
    st_clean = await mesh.reg_read(0, REG_MBIST)
    c_clean = await mesh.reg_read(0, REG_ERR_CAUSE)
    assert (st_clean & 0xE) == 0x2 and c_clean == 0, f"M11 clean pass: status 0x{st_clean:08x} cause 0x{c_clean:05x}"
    # second pass with a stuck-at-1 bit at word 11 bit 3, held through the fault port at every clock
    mesh.fault_hold(0, FI_BANK_OR, idx=11, mask=1 << 3)
    await mesh.reg_write(0, REG_MBIST, 1)
    await mesh.tick(2)
    n = 0
    while not (await mesh.reg_read(0, REG_MBIST) & 2):
        n += 1
        assert n < 4000, "M11: MBIST with the stuck bit did not finish"
    mesh.fault_release()
    st = await mesh.reg_read(0, REG_MBIST)
    c = await mesh.reg_read(0, REG_ERR_CAUSE)
    assert (st & 0xE) == 0xE and (st >> 16) == 11 and (c >> CAUSE_BIST) & 1 == 1 and mesh.pins()[0] == 1, f"M11 status 0x{st:08x} cause 0x{c:05x}"
    fc.sample_flag("bist_fail")
    dut._log.info(f"M11: MBIST clean (0x{st_clean:x}); stuck bit found through the ECC at word 11, cause and pin raised")


@cocotb.test()
async def m12_control_path_protection(dut):
    mesh = await setup(dut)
    # (a) a bit flipped in the program memory is corrected on fetch and flagged
    await mesh.program(0, [ins(OP_END)])
    await mesh.fault_once(0, FI_PROG_XOR, idx=0, mask=1 << 7)
    await mesh.start([0])
    await mesh.wait_prog(0)
    c_a = await mesh.reg_read(0, REG_ERR_CAUSE)
    assert (c_a >> CAUSE_PROG_CE) & 1 == 1 and (c_a >> CAUSE_CTRL_PATH) & 1 == 0, f"M12 prog ECC cause 0x{c_a:05x}"
    await mesh.clear_errors(0)
    # (b) a corrupted descriptor register (the primary copy only) raises the control-path cause
    await mesh.set_cfg(0, "cfg_k", 3)
    await mesh.fault_once(0, FI_CFG_XOR, idx=CFG_ORDER.index("cfg_k"), mask=1 << 2)
    await mesh.tick(2)
    c_b = await mesh.reg_read(0, REG_ERR_CAUSE)
    assert (c_b >> CAUSE_CTRL_PATH) & 1 == 1 and mesh.pins()[0] == 1, f"M12 descriptor cause 0x{c_b:05x} pin {mesh.pins()[0]}"
    await mesh.set_cfg(0, "cfg_k", 3)                                       # rewrite both copies
    await mesh.clear_errors(0)
    c_b2 = await mesh.reg_read(0, REG_ERR_CAUSE)
    assert c_b2 == 0, f"M12 cause after rewrite and clear 0x{c_b2:05x}"
    # (c) the primary DMA engine diverges (its pc is corrupted mid-program): the lockstep comparator flags it
    await mesh.program(0, [ins(OP_WAIT_FREE, arg=3), ins(OP_END)])          # a program that waits, so both engines sit in the same state
    await mesh.start([0])
    await mesh.tick(3)
    await mesh.fault_once(0, FI_PC_XOR, mask=1)
    await mesh.tick(3)
    c_c = await mesh.reg_read(0, REG_ERR_CAUSE)
    assert (c_c >> CAUSE_CTRL_PATH) & 1 == 1, f"M12 lockstep cause 0x{c_c:05x}"
    fc.sample_flag("ctrl_path")
    fc.sample_flag("prog_ce")
    dut._log.info("M12: program-memory ECC, duplicated descriptors and lockstep DMA each flagged their fault")


@cocotb.test()
async def m13_partitions(dut):
    mesh = await setup(dut)
    await mesh.reg_write(0, REG_PARTITION, 1)
    await mesh.reg_write(3, REG_PARTITION, 2)
    await mesh.program(0, [ins(OP_FETCH_A, 1, 1, A0, 8, 0), ins(OP_END)])
    await mesh.start([0])
    n = 0
    while True:
        await RisingEdge(dut.clk)
        await ReadOnly()
        if mesh.prog_done(0):
            break
        n += 1
        assert n < FETCH_TIMEOUT + 500, "M13: the requester did not time out"
    await RisingEdge(dut.clk)
    c = await mesh.causes()
    assert (c[3] >> CAUSE_ISO) & 1 == 1 and (c[0] >> CAUSE_TIMEOUT) & 1 == 1 and mesh.pins()[3] == 1, f"M13 causes {causes_str(c)}"
    await mesh.reset()
    await mesh.reg_write(0, REG_PARTITION, 2)
    await mesh.reg_write(3, REG_PARTITION, 2)
    await mesh.program(0, [ins(OP_FETCH_A, 1, 1, A0, 8, 0), ins(OP_END)])
    await mesh.start([0])
    await mesh.wait_prog(0)
    await expect_clean(mesh)
    fc.sample_flag("iso")
    fc.report("funcov_tile_mesh.yml")
    dut._log.info("M13: cross-partition fetch dropped and flagged, requester timed out; same fetch inside a partition clean")
