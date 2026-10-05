"""test_tile_mesh.py -- the mesh suite on tile_mesh under cocotb (Verilator or Icarus), drop 0.19.

Ports of sim/run_tiles.py's M-tests, driven the way the island's firmware drives a tile: everything through the
host register bus (program, descriptor, start, status, causes), banks loaded and read through a backdoor as
(39,32) codewords. Each test is independent and resets the mesh.
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
Run: make -C dv/cocotb tile   (tile_mesh_cocotb wrapper: 2x2, 16x8 cores, FETCH_TIMEOUT=600, BIST_WORDS=16)
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
NX, NY, ROWS, COLS = 2, 2, 16, 8
WPA, WPW = ROWS * 8 // 32, (COLS * 8 + 13 + 31) // 32
REG_CTRL, REG_STATUS, REG_PROG_ADDR, REG_PROG_LO, REG_PROG_HI, REG_ERR_MASK, REG_ERR_CAUSE = 0, 1, 2, 3, 4, 5, 6
REG_WD_CTRL, REG_WD_KICK, REG_SELFTEST, REG_MBIST, REG_PARTITION = 7, 8, 9, 0x0A, 0x0B
REG_CFG_BASE, REG_CFG_M = 0x10, 0x24
CAUSE_PROG_CE, CAUSE_CTRL_PATH, CAUSE_ISO, CAUSE_TIMEOUT, CAUSE_LOST, CAUSE_BIST = 15, 16, 17, 13, 12, 14
CFG_ORDER = ["cfg_h", "cfg_w", "cfg_ho", "cfg_wo", "cfg_oy0", "cfg_oy_n", "cfg_iy0", "cfg_s", "cfg_p", "cfg_k", "cfg_ct_n", "cfg_ct0",
             "cfg_ky0", "cfg_kx0", "cfg_rn", "cfg_contrib_n", "cfg_tile_pixels", "cfg_regions_m1"]
T_RDRSP = 2
A0, W0, R0 = 0, 512, 2048
APT, WPT = 36 * WPA, 9 * ROWS * WPW


def xy(n):
    return n % NX, n // NX


def bit_get(sig, i):
    """Read bit i of a 1-bit-element array that Verilator may expose either as an indexable array or as a packed vector."""
    try:
        return int(sig[i].value)
    except (IndexError, TypeError, AttributeError):
        return (int(sig.value) >> i) & 1


def bit_set(sig, i, v):
    try:
        sig[i].value = v
    except (IndexError, TypeError, AttributeError):
        cur = int(sig.value)
        sig.value = (cur | (1 << i)) if v else (cur & ~(1 << i))


def scope(obj, name):
    """Generate-block scope access: dut.g_y[1] in cocotb, or the literal 'g_y[1]' name where the array form is absent."""
    try:
        base, idx = name[:name.index('[')], int(name[name.index('[') + 1:-1])
        return getattr(obj, base)[idx]
    except Exception:
        return getattr(obj, name)


class Mesh:
    """Host-side view of the mesh through the packed-port wrapper tile_mesh_cocotb: registers, programs,
    bank backdoor, causes. Per-tile inputs are kept as shadow values and written as packed vectors."""

    def __init__(self, dut):
        self.dut = dut
        self.N = NX * NY
        self.we = [0] * self.N
        self.re = [0] * self.N
        self.addr = [0] * self.N
        self.wdata = [0] * self.N

    def tile(self, n):
        x, y = xy(n)
        return getattr(scope(scope(self.dut.u_mesh, f"g_y[{y}]"), f"g_x[{x}]"), "u_t")

    def _drive(self):
        self.dut.h_we.value = sum(v << n for n, v in enumerate(self.we))
        self.dut.h_re.value = sum(v << n for n, v in enumerate(self.re))
        self.dut.h_addr.value = sum((v & 0xFF) << (8 * n) for n, v in enumerate(self.addr))
        self.dut.h_wdata.value = sum((v & 0xFFFFFFFF) << (32 * n) for n, v in enumerate(self.wdata))

    def status(self, name, n):
        return (int(getattr(self.dut, name).value) >> n) & 1

    async def reset(self):
        self.we = [0] * self.N; self.re = [0] * self.N; self.addr = [0] * self.N; self.wdata = [0] * self.N
        self._drive()
        self.dut.rst_n.value = 0
        await ClockCycles(self.dut.clk, 4)
        self.dut.rst_n.value = 1
        await ClockCycles(self.dut.clk, 2)

    async def tick(self, n=1):
        await ClockCycles(self.dut.clk, n)

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

    # ---- bank backdoor (39,32) codewords ----
    def bank_mem(self, node):
        return self.tile(node).u_bank.u_bank.mem

    def bank_write(self, node, addr, word):
        p, op = ecc_encode(word)
        self.bank_mem(node)[addr].value = (op << 38) | (p << 32) | (word & 0xFFFFFFFF)

    def bank_read(self, node, addr):
        return int(self.bank_mem(node)[addr].value) & 0xFFFFFFFF

    def load_bank(self, node, act, wts, a0=A0, w0=W0):
        for i, wd in enumerate(act):
            self.bank_write(node, a0 + i, wd)
        for i, wd in enumerate(wts):
            self.bank_write(node, w0 + i, wd)

    def read_rows(self, node, addr, M):
        out = np.zeros((M, COLS), dtype=np.int64)
        for m in range(M):
            for j in range(COLS):
                out[m, j] = sbits(self.bank_read(node, addr + m * (COLS + 1) + j), 32)
        return out

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


def conv_data(seed=17, cin=40, cout=7):
    rng = np.random.default_rng(seed)
    x = rng.integers(-128, 128, size=(cin, 6, 6), dtype=np.int64)
    wgt = rng.integers(-128, 128, size=(cout, cin, 3, 3), dtype=np.int64)
    ref = direct_conv(x, wgt, 1, 1)
    act, wts = pack_words(x, wgt, (cin + ROWS - 1) // ROWS, 3, cout)
    return x, wgt, ref, act, wts


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
    assert all(c[n] == 0 for n in nodes), f"causes {['0x%05x' % v for v in c]}"


# ---------------------------------------------------------------------------------------------------
@cocotb.test()
async def m1_fetch_compute_writeback(dut):
    mesh = await setup(dut)
    x, wgt, ref, act, wts = conv_data()
    mesh.load_bank(3, act, wts)
    await mesh.descriptor(0, 6, 6, 6, 6, 1, 1, 3, 3, 0, 27, 0, 36)
    prog = [ins(OP_FETCH_A, 1, 1, A0 + ct * APT, APT, ct * 36) for ct in range(3)]
    prog += [ins(OP_FETCH_W, 1, 1, W0, 3 * WPT, 0), ins(OP_DRAIN_WR, 1, 0, R0, 36), ins(OP_GO), ins(OP_WAIT_DONE), ins(OP_END)]
    await mesh.program(0, prog)
    await mesh.start([0])
    n = await mesh.wait_prog(0)
    await mesh.tick(40)
    got = to_image(mesh.read_rows(1, R0, 36), ref, 36)
    assert np.array_equal(got, ref), "M1 mismatch"
    await expect_clean(mesh)
    fc.sample_drain("int32")
    dut._log.info(f"M1: bit-exact in {n} clocks, causes clean")


@cocotb.test()
async def m2_ksplit_rdy(dut):
    mesh = await setup(dut)
    x, wgt, ref, act, wts = conv_data()
    mesh.load_bank(3, act, wts)
    owner, contribs = 0, [1, 2]
    for n, ct in ((owner, 0), (contribs[0], 1), (contribs[1], 2)):
        await mesh.descriptor(n, 6, 6, 6, 6, 1, 1, 3, 3, ct, 9, 2 if n == owner else 0, 36)
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
    got = to_image(mesh.read_rows(3, R0, 36), ref, 36)
    assert np.array_equal(got, ref), "M2 mismatch"
    await expect_clean(mesh)
    fc.sample_drain("psum")
    dut._log.info(f"M2: K-split with RDY handshake bit-exact in {n} clocks")


@cocotb.test()
async def m3_end_to_end_crc(dut):
    mesh = await setup(dut)
    x, wgt, ref, act, wts = conv_data()
    mesh.load_bank(3, act, wts)
    router = mesh.tile(0).u_router
    await mesh.program(0, [ins(OP_FETCH_A, 1, 1, A0, APT, 0), ins(OP_END)])
    await mesh.start([0])
    corrupted, n = False, 0
    while True:
        await RisingEdge(dut.clk)
        await ReadOnly()
        if mesh.prog_done(0):
            break
        if not corrupted and bit_get(router.out_valid, 4) and ((int(router.out_flit[4].value) >> 61) & 7) == T_RDRSP and n > 40:
            await RisingEdge(dut.clk)
            router.out_flit[4].value = int(router.out_flit[4].value) ^ (1 << 5)   # past the last link parity check
            corrupted = True
            continue
        n += 1
        assert n < 20000
    await mesh.tick(5)
    c = await mesh.causes()
    assert corrupted and (c[0] >> 1) & 1 == 1 and all((v & 1) == 0 for v in c), f"M3 causes {['0x%05x' % v for v in c]}"
    fc.sample_flag("crc")
    dut._log.info("M3: corrupted payload past link parity flagged by the end-to-end CRC, parity clean")


@cocotb.test()
async def m4_region_refill(dut):
    mesh = await setup(dut)
    x, wgt, ref, act, wts = conv_data()
    mesh.load_bank(3, act, wts)
    await mesh.descriptor(0, 6, 6, 6, 6, 1, 1, 3, 3, 0, 27, 0, 36, regions=2)
    prog = [ins(OP_FETCH_W, 1, 1, W0, 3 * WPT, 0), ins(OP_FETCH_A, 1, 1, A0, APT, 0), ins(OP_FETCH_A, 1, 1, A0 + APT, APT, 36),
            ins(OP_DRAIN_WR, 1, 0, R0, 36), ins(OP_GO), ins(OP_WAIT_FREE, arg=0), ins(OP_FETCH_A, 1, 1, A0 + 2 * APT, APT, 0),
            ins(OP_WAIT_DONE), ins(OP_END)]
    await mesh.program(0, prog)
    await mesh.start([0])
    n = await mesh.wait_prog(0)
    await mesh.tick(40)
    got = to_image(mesh.read_rows(1, R0, 36), ref, 36)
    assert np.array_equal(got, ref), "M4 mismatch"
    await expect_clean(mesh)
    dut._log.info(f"M4: two-region buffer refilled behind the core, bit-exact in {n} clocks")


@cocotb.test()
async def m5_bank_ecc_in_flight(dut):
    mesh = await setup(dut)
    x, wgt, ref, act, wts = conv_data()
    mesh.load_bank(3, act, wts)
    mem = mesh.bank_mem(3)
    mem[A0 + 40].value = int(mem[A0 + 40].value) ^ (1 << 13)
    mem[W0 + 7].value = int(mem[W0 + 7].value) ^ (1 << 35)
    await mesh.descriptor(0, 6, 6, 6, 6, 1, 1, 3, 3, 0, 27, 0, 36)
    prog = [ins(OP_FETCH_A, 1, 1, A0 + ct * APT, APT, ct * 36) for ct in range(3)]
    prog += [ins(OP_FETCH_W, 1, 1, W0, 3 * WPT, 0), ins(OP_DRAIN_WR, 1, 0, R0, 36), ins(OP_GO), ins(OP_WAIT_DONE), ins(OP_END)]
    await mesh.program(0, prog)
    await mesh.start([0])
    await mesh.wait_prog(0)
    await mesh.tick(40)
    got = to_image(mesh.read_rows(1, R0, 36), ref, 36)
    assert np.array_equal(got, ref), "M5 mismatch"
    c = await mesh.causes()
    assert (c[3] >> 8) & 1 == 1 and (c[3] >> 7) & 1 == 0 and c[0] == 0 and c[1] == 0 and c[2] == 0, f"M5 causes {['0x%05x' % v for v in c]}"
    fc.sample_flag("ecc_ce")
    dut._log.info("M5: flipped data and check bits corrected in flight; ecc_ce on the source tile only")


@cocotb.test()
async def m6_compiler_program(dut):
    from neo_backend import compile_layer
    mesh = await setup(dut)
    x, wgt, ref, act, wts = conv_data()
    mesh.load_bank(3, act, wts)
    lp = compile_layer("c1", 6, 6, 40, 7, 3, 1, 1, ROWS, COLS, NX, NY, act_bank=(1, 1), act_base=A0, w_bank=(1, 1), w_base=W0,
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
    got = to_image(mesh.read_rows(onode, lp.out_base, 36), ref, 36)
    assert np.array_equal(got, ref), "M6 mismatch"
    await expect_clean(mesh)
    dut._log.info(f"M6: compiler-emitted program ({len(lp.tiles)} tiles) bit-exact in {n} clocks")


@cocotb.test()
async def m7_mchunks_partial_fetch(dut):
    from neo_backend import compile_layer
    mesh = await setup(dut)
    x, wgt, ref, act, wts = conv_data()
    mesh.load_bank(3, act, wts)
    lps = [compile_layer("r0", 6, 6, 40, 7, 3, 1, 1, ROWS, COLS, NX, NY, (1, 1), A0, (1, 1), W0, owner=(0, 0), shares=1, oy0=0, oy_n=3),
           compile_layer("r3", 6, 6, 40, 7, 3, 1, 1, ROWS, COLS, NX, NY, (1, 1), A0, (1, 1), W0, owner=(1, 0), shares=1, oy0=3, oy_n=3)]
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
        out = mesh.read_rows(lp.out_bank[1] * NX + lp.out_bank[0], lp.out_base, lp.M)
        for m in range(lp.M):
            for c in range(7):
                got[c, oy0 + m // 6, m % 6] = out[m, c]
    assert np.array_equal(got, ref), "M7 mismatch"
    await expect_clean(mesh)
    dut._log.info("M7: two M-chunks with partial activation fetch, concatenated bit-exact")


@cocotb.test()
async def m8_registers_selftest_watchdog(dut):
    mesh = await setup(dut)
    x, wgt, ref, act, wts = conv_data()
    mesh.load_bank(3, act, wts)
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
        if n % 400 == 0:
            await RisingEdge(dut.clk)
            await mesh.reg_write(0, REG_WD_KICK, 0)
    await RisingEdge(dut.clk)
    await mesh.reg_write(0, REG_WD_KICK, 0)
    c_st = await mesh.reg_read(0, REG_ERR_CAUSE)
    pin_st = mesh.pins()[0]
    assert (c_st >> 2) & 1 == 1 and pin_st == 1, f"self-test cause 0x{c_st:05x} pin {pin_st}"
    await mesh.reg_write(0, REG_SELFTEST, 0)
    await mesh.clear_errors(0)
    assert await mesh.reg_read(0, REG_ERR_CAUSE) == 0
    # real 16-channel run, watchdog kicked
    x16, w16 = x[:16], wgt[:, :16]
    ref16 = direct_conv(x16, w16, 1, 1)
    act16, wts16 = pack_words(x16, w16, 1, 3, 7)
    mesh.load_bank(3, act16, wts16)
    await mesh.descriptor(0, 6, 6, 6, 6, 1, 1, 3, 1, 0, 9, 0, 36)
    await mesh.program(0, [ins(OP_FETCH_A, 1, 1, A0, APT, 0), ins(OP_FETCH_W, 1, 1, W0, WPT, 0), ins(OP_DRAIN_WR, 1, 0, R0, 36),
                           ins(OP_GO), ins(OP_WAIT_DONE), ins(OP_END)])
    await mesh.start([0])
    n = 0
    while True:
        await RisingEdge(dut.clk)
        await ReadOnly()
        if mesh.prog_done(0) and not mesh.drain_busy(0):
            break
        n += 1
        if n % 400 == 0:
            await RisingEdge(dut.clk)
            await mesh.reg_write(0, REG_WD_KICK, 0)
    await RisingEdge(dut.clk)
    await mesh.tick(40)
    await mesh.reg_write(0, REG_WD_KICK, 0)
    got = to_image(mesh.read_rows(1, R0, 36), ref16, 36)
    status = await mesh.reg_read(0, REG_STATUS)
    c_run = await mesh.reg_read(0, REG_ERR_CAUSE)
    assert np.array_equal(got, ref16) and c_run == 0 and mesh.pins()[0] == 0 and (status & 1) == 1
    await mesh.tick(650)
    c_wd = await mesh.reg_read(0, REG_ERR_CAUSE)
    assert (c_wd >> 18) & 1 == 1 and mesh.pins()[0] == 1, f"watchdog cause 0x{c_wd:05x}"
    fc.sample_flag("watchdog")
    dut._log.info("M8: self-test raised the ABFT causes and the pin, clear, real run clean, watchdog expiry flagged")


@cocotb.test()
async def m9_lost_flit_and_timeout(dut):
    mesh = await setup(dut)
    x, wgt, ref, act, wts = conv_data()
    mesh.load_bank(3, act, wts)
    router = mesh.tile(0).u_router
    await mesh.program(0, [ins(OP_FETCH_A, 1, 1, A0, APT, 0), ins(OP_END)])
    await mesh.start([0])
    dropped, n = False, 0
    while True:
        await RisingEdge(dut.clk)
        await ReadOnly()
        if mesh.prog_done(0):
            break
        if not dropped and bit_get(router.out_valid, 4) and ((int(router.out_flit[4].value) >> 61) & 7) == T_RDRSP and n > 40:
            await RisingEdge(dut.clk)
            bit_set(router.out_valid, 4, 0)                     # the flit vanishes at the owner's port
            dropped = True
            continue
        n += 1
        assert n < 20000
    await mesh.tick(5)
    c = await mesh.causes()
    assert dropped and (c[0] >> CAUSE_LOST) & 1 == 1 and (c[0] >> 1) & 1 == 1, f"M9a causes {['0x%05x' % v for v in c]}"
    fc.sample_flag("lost")
    # starved request: the server tile's interface held busy
    await mesh.reset()
    sb = mesh.tile(3).u_nic.serve_busy
    await mesh.program(0, [ins(OP_FETCH_A, 1, 1, A0, 8, 0), ins(OP_END)])
    await mesh.start([0])
    n = 0
    while True:
        sb.value = 1
        await RisingEdge(dut.clk)
        await ReadOnly()
        if mesh.prog_done(0):
            break
        n += 1
        assert n < 3000, "timeout did not fire"
    c = await mesh.causes()
    assert (c[0] >> CAUSE_TIMEOUT) & 1 == 1, f"M9b causes {['0x%05x' % v for v in c]}"
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
        assert n < 4000
    st_clean = await mesh.reg_read(0, REG_MBIST)
    assert (st_clean & 0xE) == 0x2 and await mesh.reg_read(0, REG_ERR_CAUSE) == 0
    await mesh.reg_write(0, REG_MBIST, 1)
    await mesh.tick(2)
    mem = mesh.bank_mem(0)
    n = 0
    while not (await mesh.reg_read(0, REG_MBIST) & 2):
        mem[11].value = int(mem[11].value) | (1 << 3)             # stuck-at-1 at word 11 bit 3
        n += 1
        assert n < 4000
    st = await mesh.reg_read(0, REG_MBIST)
    c = await mesh.reg_read(0, REG_ERR_CAUSE)
    assert (st & 0xE) == 0xE and (st >> 16) == 11 and (c >> CAUSE_BIST) & 1 == 1 and mesh.pins()[0] == 1, f"M11 status 0x{st:08x} cause 0x{c:05x}"
    fc.sample_flag("bist_fail")
    dut._log.info(f"M11: MBIST clean (0x{st_clean:x}); stuck bit found through the ECC at word 11, cause and pin raised")


@cocotb.test()
async def m12_control_path_protection(dut):
    mesh = await setup(dut)
    t = mesh.tile(0)
    await mesh.program(0, [ins(OP_END)])
    t.u_prog.mem[0][0].value = int(t.u_prog.mem[0][0].value) ^ (1 << 7)
    await mesh.start([0])
    await mesh.wait_prog(0)
    c_a = await mesh.reg_read(0, REG_ERR_CAUSE)
    assert (c_a >> CAUSE_PROG_CE) & 1 == 1 and (c_a >> CAUSE_CTRL_PATH) & 1 == 0, f"prog ECC cause 0x{c_a:05x}"
    await mesh.clear_errors(0)
    await mesh.set_cfg(0, "cfg_k", 3)
    t.u_host.cfg[9].value = int(t.u_host.cfg[9].value) ^ (1 << 2)
    await mesh.tick(2)
    c_b = await mesh.reg_read(0, REG_ERR_CAUSE)
    assert (c_b >> CAUSE_CTRL_PATH) & 1 == 1 and mesh.pins()[0] == 1, f"descriptor cause 0x{c_b:05x}"
    await mesh.set_cfg(0, "cfg_k", 3)
    await mesh.clear_errors(0)
    assert await mesh.reg_read(0, REG_ERR_CAUSE) == 0
    await mesh.program(0, [ins(OP_WAIT_FREE, arg=3), ins(OP_END)])
    await mesh.start([0])
    await mesh.tick(3)
    t.u_dma.pc.value = int(t.u_dma.pc.value) ^ 1
    await mesh.tick(3)
    c_c = await mesh.reg_read(0, REG_ERR_CAUSE)
    assert (c_c >> CAUSE_CTRL_PATH) & 1 == 1, f"lockstep cause 0x{c_c:05x}"
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
        assert n < 3000
    await RisingEdge(dut.clk)
    c = await mesh.causes()
    assert (c[3] >> CAUSE_ISO) & 1 == 1 and (c[0] >> CAUSE_TIMEOUT) & 1 == 1 and mesh.pins()[3] == 1, f"M13 causes {['0x%05x' % v for v in c]}"
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
