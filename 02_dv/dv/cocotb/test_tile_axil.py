"""test_tile_axil.py -- the AXI4-Lite tile wrapper neo_tile_axil under cocotb on Verilator (drop 0.44, review #17): the
datasheet's integration view of one tile -- host_axil in front of neo_tile's register bus -- was only linted until now.
One 16x8 tile at (0,0) (the wrapper's 2x2 coordinate space), its four mesh links tied off by this test, the testbench
hooks on (DV_HOOKS = 1: the bank backdoor), a 16-word MBIST (the Makefile's BIST_WORDS).
  A1  registers through AXI-Lite: PARTITION, PRIO, cfg_m and the descriptor window read back what was written, STATUS and
      MBIST idle, ERR_CAUSE 0, every BRESP / RRESP OKAY; a write with partial strobes writes the strobed bytes and zeros the rest
  A2  MBIST started through AXI-Lite (0x0A): done within the March C- cycle count, no fail; err_pin low
  A3  a convolution from the tile's own bank (the self-serve loopback of drop 0.43): the activations and weights loaded
      through the backdoor, the descriptor and program written through AXI-Lite, the result drained into the same bank and
      read back through the backdoor, bit-exact against the golden convolution, the causes clean
Run: make -C dv/cocotb axil
"""
import os
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly, ClockCycles
import numpy as np
from test_tile_mesh import (conv_data, fetch_weights, to_image, check_rows, ecc_encode, ins, bits, A0, W0, R0, APT, CT_N, COLS,
                            OP_FETCH_A, OP_DRAIN_WR, OP_GO, OP_WAIT_DONE, OP_END, REG_CTRL, REG_STATUS, REG_PROG_ADDR, REG_PROG_LO,
                            REG_PROG_HI, REG_ERR_MASK, REG_ERR_CAUSE, REG_MBIST, REG_PARTITION, REG_CFG_BASE, REG_CFG_M, CFG_ORDER)

REG_PRIO = 0x0C
OKAY = 0


async def _taken(d, ready, sample=None, limit=1000):
    """Hold the driven signals until the edge at which `ready` is high in the read-only phase before it (host_axil's
    AWREADY / WREADY are combinational on the valids, so the transfer can happen at the very next edge); returns
    sample() from that phase. The caller changes its signals after the returned edge."""
    for _ in range(limit):
        await ReadOnly()
        ok = ready()
        v = sample() if (ok and sample) else None
        await RisingEdge(d.clk)
        if ok:
            return v
    raise AssertionError("AXI-Lite: no handshake within %d clocks" % limit)


class AxiLite:
    """The wrapper's AXI4-Lite port: one write (AW and W together, as host_axil takes them) or one read at a time."""

    def __init__(self, dut):
        self.d = dut
        for s in ("s_awvalid", "s_wvalid", "s_bready", "s_arvalid", "s_rready"):
            getattr(dut, s).value = 0

    async def write(self, reg, val, strb=0xF):
        d = self.d
        d.s_awaddr.value = reg * 4
        d.s_awvalid.value = 1
        d.s_wdata.value = val & 0xFFFFFFFF
        d.s_wstrb.value = strb
        d.s_wvalid.value = 1
        await _taken(d, lambda: int(d.s_awready.value) and int(d.s_wready.value))
        d.s_awvalid.value = 0
        d.s_wvalid.value = 0
        d.s_bready.value = 1
        resp = await _taken(d, lambda: int(d.s_bvalid.value), lambda: int(d.s_bresp.value))
        d.s_bready.value = 0
        return resp

    async def read(self, reg):
        d = self.d
        d.s_araddr.value = reg * 4
        d.s_arvalid.value = 1
        await _taken(d, lambda: int(d.s_arready.value))
        d.s_arvalid.value = 0
        d.s_rready.value = 1
        v, resp = await _taken(d, lambda: int(d.s_rvalid.value), lambda: (int(d.s_rdata.value), int(d.s_rresp.value)))
        d.s_rready.value = 0
        return v, resp


class Tile:
    def __init__(self, dut):
        self.d = dut
        self.axi = AxiLite(dut)

    async def reset(self):
        d = self.d
        d.test_en.value = 0
        d.my_x.value = 0
        d.my_y.value = 0
        d.in_valid.value = 0
        d.in_flit.value = 0
        d.out_ready.value = 0xF
        d.dv_bd_we.value = 0
        d.dv_bd_addr.value = 0
        d.dv_bd_wdata.value = 0
        d.dv_fi_en.value = 0
        d.dv_fi_sel.value = 0
        d.dv_fi_idx.value = 0
        d.dv_fi_mask.value = 0
        d.rst_n.value = 0
        await ClockCycles(d.clk, 4)
        d.rst_n.value = 1
        await ClockCycles(d.clk, 2)

    async def w(self, reg, val, strb=0xF):
        resp = await self.axi.write(reg, val, strb)
        assert resp == OKAY, f"reg 0x{reg:02x} write: BRESP {resp}"

    async def r(self, reg):
        v, resp = await self.axi.read(reg)
        assert resp == OKAY, f"reg 0x{reg:02x} read: RRESP {resp}"
        return v

    async def bank_write(self, addr, word):
        p, op = ecc_encode(word)
        self.d.dv_bd_addr.value = addr
        self.d.dv_bd_wdata.value = (op << 38) | (p << 32) | (word & 0xFFFFFFFF)
        self.d.dv_bd_we.value = 1
        await RisingEdge(self.d.clk)
        self.d.dv_bd_we.value = 0

    async def bank_read(self, addr):
        self.d.dv_bd_addr.value = addr
        await ReadOnly()
        v = int(self.d.dv_bd_rdata.value) & 0xFFFFFFFF
        await RisingEdge(self.d.clk)
        return v

    async def program(self, words):
        for a, wd in enumerate(words):
            await self.w(REG_PROG_ADDR, a)
            await self.w(REG_PROG_LO, wd & 0xFFFFFFFF)
            await self.w(REG_PROG_HI, wd >> 32)
        await ClockCycles(self.d.clk, 1)

    async def wait_prog(self, limit=60000):
        n = 0
        while True:
            await RisingEdge(self.d.clk)
            await ReadOnly()
            if int(self.d.prog_done.value) and not int(self.d.drain_busy.value):
                break
            n += 1
            assert n < limit, f"the program did not finish within {limit} clocks"
        await RisingEdge(self.d.clk)
        return n


async def setup(dut):
    cocotb.start_soon(Clock(dut.clk, 500, units="ps").start())
    t = Tile(dut)
    await t.reset()
    return t


@cocotb.test()
async def a1_registers(dut):
    t = await setup(dut)
    for reg, val, mask in ((REG_PARTITION, 0x5, 0xF), (REG_PRIO, 0xBEEF, 0xFFFF), (REG_CFG_M, 36, 0x3F), (REG_CFG_BASE + 2, 0x1234, 0xFFFF),
                           (REG_ERR_MASK, 0x4000, 0x7FFFF), (REG_PROG_ADDR, 7, 0x1F)):
        await t.w(reg, val)
        rb = await t.r(reg)
        assert rb == (val & mask), f"A1 reg 0x{reg:02x}: wrote 0x{val:x}, read 0x{rb:x}"
    await t.w(REG_CFG_BASE + 3, 0xA5A5C3C3, strb=0x3)                      # the low half strobed: the upper bytes written as zero
    rb = await t.r(REG_CFG_BASE + 3)
    assert rb == 0xC3C3, f"A1 strobed write: read 0x{rb:x}, expected 0xc3c3"
    st, mb, ca = await t.r(REG_STATUS), await t.r(REG_MBIST), await t.r(REG_ERR_CAUSE)
    assert st & 0x7 == 0 and mb == 0 and ca == 0 and int(dut.err_pin.value) == 0, f"A1 idle: STATUS 0x{st:x} MBIST 0x{mb:x} CAUSE 0x{ca:x}"
    for reg in (REG_PARTITION, REG_PRIO, REG_ERR_MASK, REG_CFG_BASE + 2, REG_CFG_BASE + 3):
        await t.w(reg, 0)
    dut._log.info("A1: PARTITION, PRIO, cfg_m, two descriptor registers, ERR_MASK and PROG_ADDR read back through AXI-Lite; a "
                  "half-strobed write; STATUS / MBIST / CAUSE idle, every response OKAY")


@cocotb.test()
async def a2_mbist(dut):
    t = await setup(dut)
    await t.w(REG_MBIST, 1)
    await ClockCycles(dut.clk, 2)
    n = 0
    while not (await t.r(REG_MBIST)) & 2:
        n += 1
        assert n < 4000, "A2: the MBIST did not finish"
    st = await t.r(REG_MBIST)
    ca = await t.r(REG_ERR_CAUSE)
    assert (st & 0xE) == 0x2 and ca == 0 and int(dut.err_pin.value) == 0, f"A2 MBIST status 0x{st:08x} cause 0x{ca:05x}"
    dut._log.info(f"A2: March C- over the 16-word window through AXI-Lite: done (0x{st:x}), no fail, causes clean")


@cocotb.test()
async def a3_conv_own_bank(dut):
    t = await setup(dut)
    x, wgt, ref, act, wts = conv_data()
    for i, wd in enumerate(act):
        await t.bank_write(A0 + i, wd)
    for i, wd in enumerate(wts):
        await t.bank_write(W0 + i, wd)
    assert await t.bank_read(A0) == (act[0] & 0xFFFFFFFF) and await t.bank_read(W0 + len(wts) - 1) == (wts[-1] & 0xFFFFFFFF), "A3 backdoor"
    d = dict(cfg_h=6, cfg_w=6, cfg_ho=6, cfg_wo=6, cfg_oy0=0, cfg_oy_n=6, cfg_iy0=0, cfg_s=1, cfg_p=1, cfg_k=3, cfg_ct_n=CT_N, cfg_ct0=0,
             cfg_ky0=0, cfg_kx0=0, cfg_rn=CT_N * 9, cfg_contrib_n=0, cfg_tile_pixels=36, cfg_regions_m1=0xFFFF, cfg_flags=0)
    for name, v in d.items():
        await t.w(REG_CFG_BASE + CFG_ORDER.index(name), bits(v, 16))
    await t.w(REG_CFG_M, 36)
    prog = [ins(OP_FETCH_A, 0, 0, A0 + ct * APT, APT, ct * 36) for ct in range(CT_N)]
    prog += fetch_weights(0, 0) + [ins(OP_DRAIN_WR, 0, 0, R0, 36), ins(OP_GO), ins(OP_WAIT_DONE), ins(OP_END)]
    await t.program(prog)
    await t.w(REG_CTRL, 1)
    n = await t.wait_prog()
    await ClockCycles(dut.clk, 40)
    got = np.zeros((36, COLS), dtype=np.int64)
    for m in range(36):
        for j in range(COLS):
            v = await t.bank_read(R0 + m * (COLS + 1) + j)
            got[m, j] = v - (1 << 32) if v & (1 << 31) else v
    img = to_image(got, ref, 36)
    ca = await t.r(REG_ERR_CAUSE)
    check_rows(img, ref, f"A3 (cause 0x{ca:05x})")
    st = await t.r(REG_STATUS)
    assert ca == 0 and int(dut.err_pin.value) == 0 and (st & 0x7) == 0x5, f"A3 cause 0x{ca:05x} STATUS 0x{st:x}"
    dut._log.info(f"A3: the layer from the tile's own bank through AXI-Lite, bit-exact in {n} clocks, causes clean")
