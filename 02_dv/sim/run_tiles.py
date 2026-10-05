#!/usr/bin/env python3
"""
run_tiles.py -- tile_mesh (2x2 neo_tile) under neosim: the core on the mesh.

  M1  tile (0,0) fetches 3 activation channel tiles and 27 weight tiles from tile (1,1)'s bank,
      computes conv 40x6x6 -> 7x6x6, and writes its 36 result rows into tile (1,0)'s bank; every
      byte travels as parity-checked flits inside CRC-protected messages. Result bit-exact vs
      numpy; no parity, CRC or ABFT flag anywhere.
  M2  K-split: tile (0,0) owns channel tile 0; tiles (0,1) and (1,0) run channel tiles 1 and 2 and
      send their partial sums as PSUM messages after the owner's RDY notification (message-level flow
      control: a partial-sum stream must never block an owner that is still fetching); the owner
      writes the full result to (1,1)'s bank.
  M3  a payload bit of a fetch-response flit is flipped in the destination router's output register,
      past the last link parity check: the tile's end-to-end CRC flags it, link parity stays clean.
"""
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "compiler"))
from neosim import Design, load  # noqa: E402
from neo_compile import direct_conv  # noqa: E402

RTL = ["delay_line.sv", "mac_pe.sv", "skew_in.sv", "deskew_out.sv", "systolic_array.sv", "abft_checker.sv",
       "neo_mac_core.sv", "act_feeder.sv", "acc_bank.sv", "neo_mac_core_v02.sv", "core_seq.sv", "wbuf_mem.sv", "requant.sv",
       "neo_core.sv", "noc_router.sv", "ecc39.sv", "sram_bank.sv", "crc16_word.sv", "tile_nic.sv", "tile_dma.sv",
       "mbist.sv", "bank_bist_wrap.sv", "prog_mem.sv", "host_if.sv", "esm.sv", "neo_tile.sv", "tile_mesh.sv"]
REG_PARTITION = 0x0B
CAUSE_PROG_CE, CAUSE_CTRL_PATH, CAUSE_ISO = 15, 16, 17
REG_MBIST = 0x0A
REG_RQ_TBL, REG_RQ_ADDR, REG_RQ_RELU = 0x30, 0x31, 0x32
REG_CTRL, REG_STATUS, REG_PROG_ADDR, REG_PROG_LO, REG_PROG_HI, REG_ERR_MASK, REG_ERR_CAUSE, REG_WD_CTRL, REG_WD_KICK, REG_SELFTEST = 0, 1, 2, 3, 4, 5, 6, 7, 8, 9
REG_CFG_BASE, REG_CFG_M = 0x10, 0x24
CFG_ORDER = ["cfg_h", "cfg_w", "cfg_ho", "cfg_wo", "cfg_oy0", "cfg_oy_n", "cfg_iy0", "cfg_s", "cfg_p", "cfg_k", "cfg_ct_n", "cfg_ct0",
             "cfg_ky0", "cfg_kx0", "cfg_rn", "cfg_contrib_n", "cfg_tile_pixels", "cfg_regions_m1"]
OP_FETCH_A, OP_FETCH_W, OP_DRAIN_WR, OP_DRAIN_PSUM, OP_GO, OP_WAIT_FREE, OP_WAIT_DONE, OP_END = 1, 2, 3, 4, 5, 6, 7, 8
OP_NOTIFY, OP_WAIT_RDY, OP_WAIT_REDUCE = 9, 10, 11


def ins(op, x=0, y=0, addr=0, length=0, base=0, arg=0):
    return (op << 60) | (x << 56) | (y << 52) | (addr << 32) | (length << 20) | (base << 4) | arg
NX = NY = 2
ROWS, COLS = 16, 8
WCW = 8 + (ROWS - 1).bit_length() + 1
WPA, WPW = ROWS * 8 // 32, (COLS * 8 + WCW + 31) // 32
T_RDRSP = 2


def ecc_encode(w):
    """Software model of ecc39_enc: data bit k at the k-th non-power-of-two position of 1..38."""
    pos = [j for j in range(1, 39) if (j & (j - 1)) != 0]
    p = 0
    for i in range(6):
        b = 0
        for k, j in enumerate(pos):
            if (j & (1 << i)) and ((w >> k) & 1):
                b ^= 1
        p |= b << i
    op = (bin(w & 0xFFFFFFFF).count("1") + bin(p).count("1")) & 1
    return p, op


def bits(v, w):
    return int(v) & ((1 << w) - 1)


def sbits(v, w):
    v = int(v) & ((1 << w) - 1)
    return v - (1 << w) if v >= (1 << (w - 1)) else v


class Mesh:
    def __init__(self, fetch_timeout=8192, bist_words=4096, rows=ROWS, cols=COLS):
        rtl = os.path.join(HERE, "..", "rtl")
        self.d = Design(load([os.path.join(rtl, f) for f in RTL]))
        self.rows, self.cols = rows, cols
        self.d.elaborate("tile_mesh", {"NX": NX, "NY": NY, "ROWS": rows, "COLS": cols, "ACC_ROWS": 64,
                                       "ABUF_DEPTH": 256, "WBUF_DEPTH": 512, "BANK_DEPTH": 4096,
                                       "FETCH_TIMEOUT": fetch_timeout, "BIST_WORDS": bist_words})
        d = self.d
        self.N = NX * NY
        arr = lambda n: d.array_of(n)  # noqa: E731
        self.h_we, self.h_re, self.h_addr, self.h_wdata, self.h_rdata = arr("h_we"), arr("h_re"), arr("h_addr"), arr("h_wdata"), arr("h_rdata")
        self.err_pin = arr("err_pin")
        self.prog_done, self.drain_busy, self.done = arr("prog_done"), arr("drain_busy"), arr("done")
        self.parity_err, self.crc_err = arr("parity_err"), arr("crc_err")
        self.array_sticky, self.acc_sticky, self.ctrl_sticky = arr("array_abft_sticky"), arr("acc_abft_sticky"), arr("ctrl_err_sticky")
        self.ecc_ce, self.ecc_ue = arr("ecc_ce"), arr("ecc_ue")
        self.wbuf_ce, self.wbuf_ue, self.rq_tbl_perr = arr("wbuf_ce"), arr("wbuf_ue"), arr("rq_tbl_perr")
        self.lost_err, self.fetch_timeout = arr("lost_err"), arr("fetch_timeout")
        self.rst_n = d.cell_of("rst_n")
        for n in range(self.N):
            self.h_we[n].v = 0
            self.h_re[n].v = 0
        self.clocks = 0
        self.rst_n.v = 0
        self.tick(2)
        self.rst_n.v = 1
        self.tick()

    def tick(self, n=1):
        for _ in range(n):
            self.d.tick()
        self.clocks += n

    def bank_cell(self, node, addr):
        return self.d.cells[f"top.g_y[{node // NX}].g_x[{node % NX}].u_t.u_bank.u_bank.mem[{addr}]"]

    def bank_write(self, node, addr, word):
        """Backdoor write of a 32-bit word as its (39,32) codeword, the way the bank's own write port stores it."""
        p, op = ecc_encode(word)
        self.bank_cell(node, addr).v = (op << 38) | (p << 32) | (word & 0xFFFFFFFF)

    def bank_read(self, node, addr):
        return self.bank_cell(node, addr).v & 0xFFFFFFFF

    # ---- host register access (the way the island's firmware or the PCIe driver talks to a tile) ----
    def reg_write(self, node, addr, val):
        self.h_we[node].v = 1
        self.h_addr[node].v = addr
        self.h_wdata[node].v = val & 0xFFFFFFFF
        self.tick()
        self.h_we[node].v = 0

    def reg_read(self, node, addr):
        self.h_re[node].v = 1
        self.h_addr[node].v = addr
        self.tick()
        self.h_re[node].v = 0
        return self.h_rdata[node].v

    def program(self, node, words):
        """Write a DMA program into the tile through its registers."""
        for a, wd in enumerate(words):
            self.reg_write(node, REG_PROG_ADDR, a)
            self.reg_write(node, REG_PROG_LO, wd & 0xFFFFFFFF)
            self.reg_write(node, REG_PROG_HI, wd >> 32)

    def start(self, nodes):
        for n in nodes:
            self.reg_write(n, REG_CTRL, 1)
        self.tick(2)                       # the engine leaves its done state two cycles after the CTRL write

    def clear_errors(self, node):
        self.reg_write(node, REG_CTRL, 2)
        self.tick(1)                       # the clear takes effect one cycle after the write

    def set_cfg(self, node, name, value):
        if name == "cfg_m":
            self.reg_write(node, REG_CFG_M, value)
        else:
            self.reg_write(node, REG_CFG_BASE + CFG_ORDER.index(name), bits(value, 16))

    def descriptor(self, node, h, w, ho, wo, s, p, k, ct_n, ct0, rn, contrib_n, M, regions=None):
        for name, v in (("cfg_h", h), ("cfg_w", w), ("cfg_ho", ho), ("cfg_wo", wo), ("cfg_s", s), ("cfg_p", p),
                        ("cfg_k", k), ("cfg_ct_n", ct_n), ("cfg_ct0", ct0), ("cfg_ky0", 0), ("cfg_kx0", 0),
                        ("cfg_rn", rn), ("cfg_contrib_n", contrib_n), ("cfg_tile_pixels", h * w),
                        ("cfg_regions_m1", 0xFFFF if regions is None else regions - 1),
                        ("cfg_oy0", 0), ("cfg_oy_n", ho), ("cfg_iy0", 0)):
            self.set_cfg(node, name, v)
        self.set_cfg(node, "cfg_m", M)

    def flags(self):
        return {"parity": [c.v for c in self.parity_err], "crc": [c.v for c in self.crc_err],
                "array": [c.v for c in self.array_sticky], "acc": [c.v for c in self.acc_sticky],
                "ctrl": [c.v for c in self.ctrl_sticky], "ecc_ue": [c.v for c in self.ecc_ue],
                "wbuf_ue": [c.v for c in self.wbuf_ue], "tbl_perr": [c.v for c in self.rq_tbl_perr],
                "lost": [c.v for c in self.lost_err], "timeout": [c.v for c in self.fetch_timeout]}


def pack_words(x, wgt, cin_tiles, k, cout, rows=None, cols=None):
    """Activation entries (ROWS/4 words each, channel c in byte c%4 of word c//4) per channel tile, and
    weight entries (COLS weight bytes, then the check weight in the next WCW bits) per tile row."""
    ROWS = rows if rows is not None else globals()["ROWS"]
    COLS = cols if cols is not None else globals()["COLS"]
    WCW = 8 + (ROWS - 1).bit_length() + 1
    WPA, WPW = ROWS * 8 // 32, (COLS * 8 + WCW + 31) // 32
    cin, h, w = x.shape
    act = []
    for ct in range(cin_tiles):
        rr = min(ROWS, cin - ct * ROWS)
        for yy in range(h):
            for xx in range(w):
                words = [0] * WPA
                for c in range(ROWS):
                    val = int(x[ct * ROWS + c, yy, xx]) if c < rr else 0
                    words[c // 4] |= (val & 0xFF) << (8 * (c % 4))
                act.extend(words)
    wts = []
    for ct in range(cin_tiles):
        rr = min(ROWS, cin - ct * ROWS)
        for ky in range(k):
            for kx in range(k):
                for r in range(ROWS):
                    words = [0] * WPW
                    tot = 0
                    for c in range(COLS):
                        val = int(wgt[c, ct * ROWS + r, ky, kx]) if (r < rr and c < cout) else 0
                        tot += val
                        words[c // 4] |= (val & 0xFF) << (8 * (c % 4))
                    words[(COLS * 8) // 32] |= (tot & ((1 << WCW) - 1))
                    wts.extend(words)
    return act, wts


def read_rows(mesh, node, addr, M):
    COLS = mesh.cols
    out = np.zeros((M, COLS), dtype=np.int64)
    chk = np.zeros(M, dtype=np.int64)
    for m in range(M):
        for j in range(COLS):
            out[m, j] = sbits(mesh.bank_read(node, addr + m * (COLS + 1) + j), 32)
        chk[m] = sbits(mesh.bank_read(node, addr + m * (COLS + 1) + COLS), 32)
    return out, chk


def wait(mesh, cond, limit=40000):
    n = 0
    while not cond():
        mesh.tick()
        n += 1
        if n > limit:
            raise RuntimeError("timeout")


def main():
    rng = np.random.default_rng(17)
    x = rng.integers(-128, 128, size=(40, 6, 6), dtype=np.int64)
    wgt = rng.integers(-128, 128, size=(7, 40, 3, 3), dtype=np.int64)
    s, p, k, cout = 1, 1, 3, 7
    ref = direct_conv(x, wgt, s, p)
    ho, wo = ref.shape[1], ref.shape[2]
    M = ho * wo
    cin_tiles = 3
    act, wts = pack_words(x, wgt, cin_tiles, k, cout)
    A0, W0, R0 = 0, 512, 2048
    APT, WPT = 36 * WPA, k * k * ROWS * WPW          # words per activation tile, per channel tile of weights
    fails = 0
    print("neo_tile 2x2 mesh under neosim (router + NIC + bank + core per tile):")

    # ---------------- M1: remote fetch, compute, writeback ----------------
    t0 = time.time()
    mesh = Mesh()
    src = 3                                           # tile (1,1) holds the data
    for i, wd in enumerate(act):
        mesh.bank_write(src, A0 + i, wd)
    for i, wd in enumerate(wts):
        mesh.bank_write(src, W0 + i, wd)
    mesh.descriptor(0, 6, 6, ho, wo, s, p, k, cin_tiles, 0, cin_tiles * k * k, 0, M)
    prog = [ins(OP_FETCH_A, 1, 1, A0 + ct * APT, APT, ct * 36) for ct in range(cin_tiles)]
    prog += [ins(OP_FETCH_W, 1, 1, W0, cin_tiles * WPT, 0), ins(OP_DRAIN_WR, 1, 0, R0, M),
             ins(OP_GO), ins(OP_WAIT_DONE), ins(OP_END)]
    mesh.program(0, prog)
    mesh.start([0])
    wait(mesh, lambda: mesh.prog_done[0].v)
    wait(mesh, lambda: not mesh.drain_busy[0].v)
    mesh.tick(40)
    out, chk = read_rows(mesh, 1, R0, M)
    got = np.zeros_like(ref)
    for m in range(M):
        for c in range(cout):
            got[c, m // wo, m % wo] = out[m, c]
    fl = mesh.flags()
    ok1 = np.array_equal(got, ref) and np.array_equal(chk, out.sum(axis=1)) and not any(sum(v) for v in fl.values())
    print(f"  M1: tile(0,0) DMA program: fetched {len(act) + len(wts)} words from tile(1,1), computed, wrote {M} rows to tile(1,0): "
          f"{'bit-exact' if np.array_equal(got, ref) else 'MISMATCH'}, check column {'consistent' if np.array_equal(chk, out.sum(axis=1)) else 'BAD'}, "
          f"flags {fl} -> {'PASS' if ok1 else 'FAIL'} ({mesh.clocks} clocks, {time.time() - t0:.0f} s)")
    fails += not ok1

    # ---------------- M2: K-split across three tiles, partial sums as packets ----------------
    t0 = time.time()
    mesh = Mesh()
    for i, wd in enumerate(act):
        mesh.bank_write(src, A0 + i, wd)
    for i, wd in enumerate(wts):
        mesh.bank_write(src, W0 + i, wd)
    owner, contribs = 0, [1, 2]                       # tiles (0,0); (1,0) and (0,1)
    xy = lambda n: (n % NX, n // NX)  # noqa: E731
    for n, ct in ((owner, 0), (contribs[0], 1), (contribs[1], 2)):
        mesh.descriptor(n, 6, 6, ho, wo, s, p, k, cin_tiles, ct, k * k, 2 if n == owner else 0, M)
        fetch = [ins(OP_FETCH_A, 1, 1, A0 + ct * APT, APT, ct * 36), ins(OP_FETCH_W, 1, 1, W0 + ct * WPT, WPT, 0)]
        if n == owner:
            # owner: run, then open the reduce port and tell the contributors, then drain the result
            prog = fetch + [ins(OP_DRAIN_WR, 1, 1, R0, M), ins(OP_GO), ins(OP_WAIT_REDUCE)]
            prog += [ins(OP_NOTIFY, xy(c)[0], xy(c)[1]) for c in contribs]
            prog += [ins(OP_WAIT_DONE), ins(OP_END)]
        else:
            # contributor: run, hold the partial sums until the owner says its reduce port is open
            prog = fetch + [ins(OP_GO), ins(OP_WAIT_RDY), ins(OP_DRAIN_PSUM, xy(owner)[0], xy(owner)[1], 0, M),
                            ins(OP_WAIT_DONE), ins(OP_END)]
        mesh.program(n, prog)
    mesh.start([owner, *contribs])
    wait(mesh, lambda: mesh.prog_done[owner].v)
    wait(mesh, lambda: not mesh.drain_busy[owner].v)
    mesh.tick(40)
    out, chk = read_rows(mesh, 3, R0, M)
    got = np.zeros_like(ref)
    for m in range(M):
        for c in range(cout):
            got[c, m // wo, m % wo] = out[m, c]
    fl = mesh.flags()
    ok2 = np.array_equal(got, ref) and not any(sum(v) for v in fl.values())
    print(f"  M2: K-split: owner tile(0,0) + contributors tile(1,0), tile(0,1) with PSUM messages, result to tile(1,1): "
          f"{'bit-exact' if np.array_equal(got, ref) else 'MISMATCH'}, flags {fl} -> {'PASS' if ok2 else 'FAIL'} ({mesh.clocks} clocks, {time.time() - t0:.0f} s)")
    fails += not ok2

    # ---------------- M3: corruption past the last parity check -> end-to-end CRC ----------------
    t0 = time.time()
    mesh = Mesh()
    for i, wd in enumerate(act):
        mesh.bank_write(src, A0 + i, wd)
    flit_cell = mesh.d.cells["top.g_y[0].g_x[0].u_t.u_router.out_flit[4]"]
    valid_cell = mesh.d.cells["top.g_y[0].g_x[0].u_t.u_router.out_valid[4]"]
    mesh.program(0, [ins(OP_FETCH_A, 1, 1, A0, APT, 0), ins(OP_END)])
    mesh.start([0])
    corrupted, n = False, 0
    while not mesh.prog_done[0].v:
        if not corrupted and valid_cell.v and ((flit_cell.v >> 61) & 7) == T_RDRSP and n > 30:
            flit_cell.v ^= 1 << 9                     # payload bit, after the ingress router's parity check
            mesh.d.settle()
            corrupted = True
        mesh.tick()
        n += 1
        if n > 20000:
            raise RuntimeError("fetch did not complete")
    mesh.tick(5)
    fl = mesh.flags()
    ok3 = corrupted and fl["crc"] == [1, 0, 0, 0] and sum(fl["parity"]) == 0
    print(f"  M3: fetch-response payload bit flipped in tile(0,0)'s router output register: crc_err {fl['crc']} "
          f"(expect [1,0,0,0]), parity_err {fl['parity']} (expect all 0: past the last link check) -> {'PASS' if ok3 else 'FAIL'} ({time.time() - t0:.0f} s)")
    fails += not ok3

    # ---------------- M4: two-region activation buffer refilled behind the running core ----------------
    t0 = time.time()
    mesh = Mesh()
    for i, wd in enumerate(act):
        mesh.bank_write(src, A0 + i, wd)
    for i, wd in enumerate(wts):
        mesh.bank_write(src, W0 + i, wd)
    mesh.descriptor(0, 6, 6, ho, wo, s, p, k, cin_tiles, 0, cin_tiles * k * k, 0, M, regions=2)
    prog = [ins(OP_FETCH_W, 1, 1, W0, cin_tiles * WPT, 0),
            ins(OP_FETCH_A, 1, 1, A0 + 0 * APT, APT, 0 * 36),           # tile 0 -> region 0
            ins(OP_FETCH_A, 1, 1, A0 + 1 * APT, APT, 1 * 36),           # tile 1 -> region 1
            ins(OP_DRAIN_WR, 1, 0, R0, M), ins(OP_GO),
            ins(OP_WAIT_FREE, arg=0),                                   # core has finished reading tile 0
            ins(OP_FETCH_A, 1, 1, A0 + 2 * APT, APT, 0 * 36),           # tile 2 -> region 0 (refill)
            ins(OP_WAIT_DONE), ins(OP_END)]
    mesh.program(0, prog)
    mesh.start([0])
    wait(mesh, lambda: mesh.prog_done[0].v)
    wait(mesh, lambda: not mesh.drain_busy[0].v)
    mesh.tick(40)
    out, chk = read_rows(mesh, 1, R0, M)
    got = np.zeros_like(ref)
    for m in range(M):
        for c in range(cout):
            got[c, m // wo, m % wo] = out[m, c]
    fl = mesh.flags()
    ok4 = np.array_equal(got, ref) and not any(sum(v) for v in fl.values())
    print(f"  M4: 2-region activation buffer: tiles 0,1 fetched, core started, region 0 refilled with tile 2 after ct_free, "
          f"sequencer waited on tiles_ready: {'bit-exact' if np.array_equal(got, ref) else 'MISMATCH'}, flags clean={not any(sum(v) for v in fl.values())} -> {'PASS' if ok4 else 'FAIL'} ({mesh.clocks} clocks, {time.time() - t0:.0f} s)")
    fails += not ok4

    # ---------------- M5: bank ECC corrects a flipped bit in flight ----------------
    t0 = time.time()
    mesh = Mesh()
    for i, wd in enumerate(act):
        mesh.bank_write(src, A0 + i, wd)
    for i, wd in enumerate(wts):
        mesh.bank_write(src, W0 + i, wd)
    mesh.bank_cell(src, A0 + 40).v ^= 1 << 13           # data bit 13 of activation word 40 flipped in the bank
    mesh.bank_cell(src, W0 + 7).v ^= 1 << 35            # a check bit of weight word 7 flipped
    mesh.descriptor(0, 6, 6, ho, wo, s, p, k, cin_tiles, 0, cin_tiles * k * k, 0, M)
    prog = [ins(OP_FETCH_A, 1, 1, A0 + ct * APT, APT, ct * 36) for ct in range(cin_tiles)]
    prog += [ins(OP_FETCH_W, 1, 1, W0, cin_tiles * WPT, 0), ins(OP_DRAIN_WR, 1, 0, R0, M),
             ins(OP_GO), ins(OP_WAIT_DONE), ins(OP_END)]
    mesh.program(0, prog)
    mesh.start([0])
    wait(mesh, lambda: mesh.prog_done[0].v)
    wait(mesh, lambda: not mesh.drain_busy[0].v)
    mesh.tick(40)
    out, chk = read_rows(mesh, 1, R0, M)
    got = np.zeros_like(ref)
    for m in range(M):
        for c in range(cout):
            got[c, m // wo, m % wo] = out[m, c]
    fl = mesh.flags()
    ce = [c.v for c in mesh.ecc_ce]
    ok5 = np.array_equal(got, ref) and ce == [0, 0, 0, 1] and not any(sum(v) for v in fl.values())
    print(f"  M5: bank ECC: a data bit and a check bit flipped in tile(1,1)'s bank before the fetch: result "
          f"{'bit-exact' if np.array_equal(got, ref) else 'MISMATCH'}, ecc_ce {ce} (expect [0,0,0,1]), ecc_ue {fl['ecc_ue']}, "
          f"other flags clean -> {'PASS' if ok5 else 'FAIL'} ({time.time() - t0:.0f} s)")
    fails += not ok5

    # ---------------- M6: a program emitted by the compiler backend, executed as emitted ----------------
    t0 = time.time()
    sys.path.insert(0, os.path.join(HERE, "..", "compiler"))
    from neo_backend import compile_layer
    lp = compile_layer("conv_c1", 6, 6, 40, 7, 3, 1, 1, ROWS, COLS, NX, NY, act_bank=(1, 1), act_base=A0,
                       w_bank=(1, 1), w_base=W0, owner=(0, 0), shares=3)
    mesh = Mesh()
    for i, wd in enumerate(act):
        mesh.bank_write(src, A0 + i, wd)
    for i, wd in enumerate(wts):
        mesh.bank_write(src, W0 + i, wd)
    nodes = []
    for tp in lp.tiles:
        n = tp.tile[1] * NX + tp.tile[0]
        nodes.append(n)
        for name, v in tp.descriptor.items():
            if name == "cfg_m":
                mesh.cfg[name][n].v = v
            else:
                mesh.cfg[name][n].v = bits(v, 16)
        mesh.program(n, tp.program)
    mesh.start(nodes)
    onode = lp.out_bank[1] * NX + lp.out_bank[0]
    wait(mesh, lambda: mesh.prog_done[onode].v)
    wait(mesh, lambda: not mesh.drain_busy[onode].v)
    mesh.tick(40)
    out, chk = read_rows(mesh, onode, lp.out_base, M)
    got = np.zeros_like(ref)
    for m in range(M):
        for c in range(cout):
            got[c, m // wo, m % wo] = out[m, c]
    fl = mesh.flags()
    ok6 = np.array_equal(got, ref) and not any(sum(v) for v in fl.values())
    roles = ", ".join(f"{t.role}@{t.tile}" for t in lp.tiles)
    print(f"  M6: compiler-emitted layer program ({roles}; {sum(len(t.program) for t in lp.tiles)} program words, "
          f"result in the owner's own bank): {'bit-exact' if np.array_equal(got, ref) else 'MISMATCH'}, flags clean={not any(sum(v) for v in fl.values())} "
          f"-> {'PASS' if ok6 else 'FAIL'} ({mesh.clocks} clocks, {time.time() - t0:.0f} s)")
    fails += not ok6

    # ---------------- M7: two M-chunks (output rows 0-2 and 3-5) with partial activation fetch ----------------
    t0 = time.time()
    mesh = Mesh()
    for i, wd in enumerate(act):
        mesh.bank_write(src, A0 + i, wd)
    for i, wd in enumerate(wts):
        mesh.bank_write(src, W0 + i, wd)
    lps = [compile_layer("rows0_2", 6, 6, 40, 7, 3, 1, 1, ROWS, COLS, NX, NY, act_bank=(1, 1), act_base=A0,
                         w_bank=(1, 1), w_base=W0, owner=(0, 0), shares=1, oy0=0, oy_n=3),
           compile_layer("rows3_5", 6, 6, 40, 7, 3, 1, 1, ROWS, COLS, NX, NY, act_bank=(1, 1), act_base=A0,
                         w_bank=(1, 1), w_base=W0, owner=(1, 0), shares=1, oy0=3, oy_n=3)]
    nodes = []
    for lp in lps:
        for tp in lp.tiles:
            n = tp.tile[1] * NX + tp.tile[0]
            nodes.append(n)
            for name, v in tp.descriptor.items():
                mesh.set_cfg(n, name, v)
            mesh.program(n, tp.program)
    mesh.start(nodes)
    for lp in lps:
        onode = lp.out_bank[1] * NX + lp.out_bank[0]
        wait(mesh, lambda: mesh.prog_done[onode].v)
        wait(mesh, lambda: not mesh.drain_busy[onode].v)
    mesh.tick(40)
    got = np.zeros_like(ref)
    for lp, oy0 in zip(lps, (0, 3)):
        onode = lp.out_bank[1] * NX + lp.out_bank[0]
        out, chk = read_rows(mesh, onode, lp.out_base, lp.M)
        for m in range(lp.M):
            for c in range(cout):
                got[c, oy0 + m // wo, m % wo] = out[m, c]
    fl = mesh.flags()
    fetched = sum(t.fetch_words for lp in lps for t in lp.tiles)
    ok7 = np.array_equal(got, ref) and not any(sum(v) for v in fl.values())
    print(f"  M7: two M-chunks on two owners, each fetching only its input rows (iy0 = {lps[0].tiles[0].descriptor['cfg_iy0']} and "
          f"{lps[1].tiles[0].descriptor['cfg_iy0']}, {fetched} words fetched vs {2 * (len(act) + len(wts))} for whole tiles): "
          f"{'bit-exact' if np.array_equal(got, ref) else 'MISMATCH'}, flags clean={not any(sum(v) for v in fl.values())} -> {'PASS' if ok7 else 'FAIL'} ({time.time() - t0:.0f} s)")
    fails += not ok7

    # ---------------- M9: lost flit and starved request ----------------
    t0 = time.time()
    mesh = Mesh()
    for i, wd in enumerate(act):
        mesh.bank_write(src, A0 + i, wd)
    vcell = mesh.d.cells["top.g_y[0].g_x[0].u_t.u_router.out_valid[4]"]
    fcell = mesh.d.cells["top.g_y[0].g_x[0].u_t.u_router.out_flit[4]"]
    mesh.program(0, [ins(OP_FETCH_A, 1, 1, A0, APT, 0), ins(OP_END)])
    mesh.start([0])
    dropped, n = False, 0
    while not mesh.prog_done[0].v:
        if not dropped and vcell.v and ((fcell.v >> 61) & 7) == T_RDRSP and n > 40:
            vcell.v = 0                                     # the flit at the owner's local port vanishes
            mesh.d.settle()
            dropped = True
        mesh.tick(); n += 1
        if n > 20000:
            raise RuntimeError("fetch did not complete")
    mesh.tick(5)
    fl = mesh.flags()
    ok9a = dropped and fl["lost"] == [1, 0, 0, 0] and fl["crc"][0] == 1 and sum(fl["parity"]) == 0
    # starved request: tile (1,1)'s interface is made to look busy, so the request is never served
    mesh2 = Mesh(fetch_timeout=600)
    sb = mesh2.d.cells["top.g_y[1].g_x[1].u_t.u_nic.serve_busy"]
    sb.v = 1
    mesh2.program(0, [ins(OP_FETCH_A, 1, 1, A0, 8, 0), ins(OP_END)])
    mesh2.start([0])
    n = 0
    while not mesh2.prog_done[0].v:
        sb.v = 1
        mesh2.tick(); n += 1
        if n > 3000:
            break
    fl2 = mesh2.flags()
    ok9b = mesh2.prog_done[0].v == 1 and fl2["timeout"] == [1, 0, 0, 0]
    print(f"  M9: (a) a fetch-response flit dropped at the owner's port: lost_err {fl['lost']} (expect [1,0,0,0]), crc_err[0]={fl['crc'][0]}, "
          f"parity clean -> {'PASS' if ok9a else 'FAIL'}; (b) request starved by a busy server: fetch_timeout {fl2['timeout']} (expect [1,0,0,0]), "
          f"program completed after {n} cycles instead of hanging -> {'PASS' if ok9b else 'FAIL'} ({time.time() - t0:.0f} s)")
    fails += not (ok9a and ok9b)

    # ---------------- M8: the island's view: registers, error pin, watchdog, checker self-test ----------------
    t0 = time.time()
    mesh = Mesh()
    for i, wd in enumerate(act):
        mesh.bank_write(src, A0 + i, wd)
    for i, wd in enumerate(wts):
        mesh.bank_write(src, W0 + i, wd)
    mesh.reg_write(0, REG_ERR_MASK, 1 << 8)                    # mask ecc_ce (informational); everything else raises the pin
    mesh.reg_write(0, REG_WD_CTRL, (600 << 8) | 1)              # watchdog: window 600 cycles
    # checker self-test: a dummy single-row run with the fault hook asserted must raise the ABFT cause
    mesh.descriptor(0, 6, 6, 1, 1, 1, 1, 3, 1, 0, 9, 0, 1)
    mesh.set_cfg(0, "cfg_tile_pixels", 36)
    mesh.reg_write(0, REG_SELFTEST, 1)
    mesh.program(0, [ins(OP_FETCH_A, 1, 1, A0, APT, 0), ins(OP_FETCH_W, 1, 1, W0, 9 * ROWS * WPW, 0),
                     ins(OP_DRAIN_WR, 1, 0, R0, 1), ins(OP_GO), ins(OP_WAIT_DONE), ins(OP_END)])
    mesh.start([0]); mesh.reg_write(0, REG_WD_KICK, 0)
    n = 0
    while not mesh.prog_done[0].v:
        mesh.tick(); n += 1
        if n % 400 == 0:
            mesh.reg_write(0, REG_WD_KICK, 0)
    mesh.reg_write(0, REG_WD_KICK, 0)
    wait(mesh, lambda: not mesh.drain_busy[0].v)
    cause_st = mesh.reg_read(0, REG_ERR_CAUSE)
    pin_st = mesh.err_pin[0].v
    mesh.reg_write(0, REG_SELFTEST, 0)
    mesh.clear_errors(0)
    cause_clr = mesh.reg_read(0, REG_ERR_CAUSE)
    # then a real workload through the same registers (one channel tile, to keep the run inside the shell limit)
    x16, w16 = x[:16], wgt[:, :16]
    ref16 = direct_conv(x16, w16, s, p)
    act16, wts16 = pack_words(x16, w16, 1, k, cout)
    for i, wd in enumerate(act16):
        mesh.bank_write(src, A0 + i, wd)
    for i, wd in enumerate(wts16):
        mesh.bank_write(src, W0 + i, wd)
    mesh.descriptor(0, 6, 6, ho, wo, s, p, k, 1, 0, k * k, 0, M)
    prog = [ins(OP_FETCH_A, 1, 1, A0, APT, 0), ins(OP_FETCH_W, 1, 1, W0, WPT, 0), ins(OP_DRAIN_WR, 1, 0, R0, M),
            ins(OP_GO), ins(OP_WAIT_DONE), ins(OP_END)]
    mesh.program(0, prog)
    mesh.start([0])
    n = 0
    while not mesh.prog_done[0].v:
        mesh.tick(); n += 1
        if n % 400 == 0:
            mesh.reg_write(0, REG_WD_KICK, 0)
        if n > 40000:
            raise RuntimeError("timeout")
    wait(mesh, lambda: not mesh.drain_busy[0].v)
    mesh.tick(40)
    mesh.reg_write(0, REG_WD_KICK, 0)
    out, chk = read_rows(mesh, 1, R0, M)
    got = np.zeros_like(ref16)
    for m in range(M):
        for c in range(cout):
            got[c, m // wo, m % wo] = out[m, c]
    status = mesh.reg_read(0, REG_STATUS)
    cause_run = mesh.reg_read(0, REG_ERR_CAUSE)
    pin_run = mesh.err_pin[0].v
    # finally let the watchdog expire
    mesh.tick(650)
    cause_wd = mesh.reg_read(0, REG_ERR_CAUSE)
    pin_wd = mesh.err_pin[0].v
    ok8 = (np.array_equal(got, ref16) and (cause_st & (1 << 2)) and pin_st == 1 and cause_clr == 0 and cause_run == 0 and pin_run == 0
           and (cause_wd >> 16) & 1 == 1 and pin_wd == 1 and (status & 1) == 1)
    print(f"  M8: register-driven tile: self-test dummy run raised cause 0x{cause_st:05x} and the error pin ({pin_st}); clear -> 0x{cause_clr:05x}; "
          f"real run (16-channel conv) through the same registers {'bit-exact' if np.array_equal(got, ref16) else 'MISMATCH'} with cause 0x{cause_run:05x}, pin {pin_run}, STATUS 0x{status:x}; "
          f"watchdog left un-kicked -> cause 0x{cause_wd:05x}, pin {pin_wd} -> {'PASS' if ok8 else 'FAIL'} ({time.time() - t0:.0f} s)")
    fails += not ok8

    # ---------------- M11: bank MBIST through the registers, clean and with a stuck bit ----------------
    t0 = time.time()
    mesh = Mesh(bist_words=16)
    mesh.reg_write(0, REG_MBIST, 1)
    mesh.tick(2)
    n = 0
    while not (mesh.reg_read(0, REG_MBIST) & 2):
        n += 1
        if n > 2000:
            break
    st_clean = mesh.reg_read(0, REG_MBIST)
    cause_clean = mesh.reg_read(0, REG_ERR_CAUSE)
    mesh.reg_write(0, REG_MBIST, 1)
    mesh.tick(2)                                                # done drops two cycles after the start write
    cell = mesh.d.cells["top.g_y[0].g_x[0].u_t.u_bank.u_bank.mem[11]"]
    n = 0
    while not (mesh.reg_read(0, REG_MBIST) & 2):
        cell.v |= 1 << 3                                        # stuck-at-1 at word 11 bit 3
        n += 1
        if n > 2000:
            break
    st_stuck = mesh.reg_read(0, REG_MBIST)
    cause_stuck = mesh.reg_read(0, REG_ERR_CAUSE)
    ok11 = ((st_clean & 0xE) == 0x2 and cause_clean == 0 and (st_stuck & 0xE) == 0xE and (st_stuck >> 16) == 11
            and (cause_stuck >> 14) & 1 == 1 and mesh.err_pin[0].v == 1)
    print(f"  M11: bank MBIST through registers (16 words): clean -> status 0x{st_clean:x} (done, no fail), cause 0x{cause_clean:x}; "
          f"stuck bit at word 11 -> status 0x{st_stuck:08x} (fail, found by ECC, addr {st_stuck >> 16}), cause bit 14={(cause_stuck >> 14) & 1}, "
          f"error pin {mesh.err_pin[0].v} -> {'PASS' if ok11 else 'FAIL'} ({time.time() - t0:.0f} s)")
    fails += not ok11

    # ---------------- M12: control-path protection: program memory SECDED, descriptor pair, DMA lockstep ----------------
    t0 = time.time()
    mesh = Mesh(fetch_timeout=600)
    tp = "top.g_y[0].g_x[0].u_t."
    # (a) a bit flipped in the program memory is corrected on fetch and flagged
    mesh.program(0, [ins(OP_END)])
    mesh.d.cells[tp + "u_prog.mem[0,0]"].v ^= 1 << 7
    mesh.start([0])
    wait(mesh, lambda: mesh.prog_done[0].v)
    c_a = mesh.reg_read(0, REG_ERR_CAUSE)
    ok_a = (c_a >> CAUSE_PROG_CE) & 1 == 1 and (c_a >> CAUSE_CTRL_PATH) & 1 == 0 and mesh.prog_done[0].v == 1
    mesh.clear_errors(0)
    # (b) a corrupted descriptor register (the primary copy only) raises the control-path cause
    mesh.set_cfg(0, "cfg_k", 3)
    mesh.d.cells[tp + "u_host.cfg[9]"].v ^= 1 << 2
    mesh.tick(2)
    c_b = mesh.reg_read(0, REG_ERR_CAUSE)
    ok_b = (c_b >> CAUSE_CTRL_PATH) & 1 == 1 and mesh.err_pin[0].v == 1
    mesh.set_cfg(0, "cfg_k", 3)                                 # rewrite both copies
    mesh.clear_errors(0)
    c_b2 = mesh.reg_read(0, REG_ERR_CAUSE)
    # (c) the primary DMA engine diverges (its pc is corrupted mid-program): the lockstep comparator flags it
    mesh.program(0, [ins(OP_WAIT_FREE, arg=3), ins(OP_END)])   # a program that waits, so both engines sit in the same state
    mesh.start([0])
    mesh.tick(3)
    mesh.d.cells[tp + "u_dma.pc"].v ^= 1                        # primary's program counter flipped
    mesh.tick(3)
    c_c = mesh.reg_read(0, REG_ERR_CAUSE)
    ok_c = (c_c >> CAUSE_CTRL_PATH) & 1 == 1
    print(f"  M12: program-memory bit flip corrected on fetch (cause 0x{c_a:05x}: prog_ce set, program completed) -> {'PASS' if ok_a else 'FAIL'}; "
          f"descriptor register corrupted in one copy -> cause 0x{c_b:05x} and error pin {mesh.err_pin[0].v}, clean after rewrite+clear (0x{c_b2:05x}) -> {'PASS' if ok_b and c_b2 == 0 else 'FAIL'}; "
          f"DMA engine pc corrupted -> lockstep comparator cause 0x{c_c:05x} -> {'PASS' if ok_c else 'FAIL'} ({time.time() - t0:.0f} s)")
    fails += not (ok_a and ok_b and c_b2 == 0 and ok_c)

    # ---------------- M13: spatial partitions: a request from another partition is dropped and flagged ----------------
    t0 = time.time()
    mesh = Mesh(fetch_timeout=600)
    mesh.reg_write(0, REG_PARTITION, 1)                         # tile (0,0) in partition 1
    mesh.reg_write(3, REG_PARTITION, 2)                         # tile (1,1) in partition 2
    mesh.program(0, [ins(OP_FETCH_A, 1, 1, A0, 8, 0), ins(OP_END)])
    mesh.start([0])
    n = 0
    while not mesh.prog_done[0].v and n < 3000:
        mesh.tick(); n += 1
    c0 = mesh.reg_read(0, REG_ERR_CAUSE); c3 = mesh.reg_read(3, REG_ERR_CAUSE)
    ok13 = ((c3 >> CAUSE_ISO) & 1 == 1 and (c0 >> 13) & 1 == 1 and mesh.prog_done[0].v == 1
            and mesh.err_pin[3].v == 1)
    # same request inside one partition completes normally
    mesh2 = Mesh(fetch_timeout=600)
    mesh2.reg_write(0, REG_PARTITION, 2); mesh2.reg_write(3, REG_PARTITION, 2)
    mesh2.program(0, [ins(OP_FETCH_A, 1, 1, A0, 8, 0), ins(OP_END)])
    mesh2.start([0])
    wait(mesh2, lambda: mesh2.prog_done[0].v)
    c0b = mesh2.reg_read(0, REG_ERR_CAUSE); c3b = mesh2.reg_read(3, REG_ERR_CAUSE)
    ok13b = c0b == 0 and c3b == 0
    print(f"  M13: tile(0,0) in partition 1 fetching from tile(1,1) in partition 2: request dropped at the destination (iso cause 0x{c3:05x}, pin {mesh.err_pin[3].v}), "
          f"requester timed out (cause 0x{c0:05x}) and its program completed -> {'PASS' if ok13 else 'FAIL'}; same request within one partition clean "
          f"(0x{c0b:05x}/0x{c3b:05x}) -> {'PASS' if ok13b else 'FAIL'} ({time.time() - t0:.0f} s)")
    fails += not (ok13 and ok13b)

    print(f"RESULT: {'ALL PASS' if fails == 0 else f'{fails} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
