#!/usr/bin/env python3
"""
neo_compile.py -- AstraCore Neo compiler v1 (2026-10-04): lowering + performance model

What it does
  1. Lowers Conv2d / GEMM layers to work items for the 64 x (32x32) weight-stationary cores:
     im2col GEMM  Y[M x N] = X[M x K] . W[K x N], tiled as K-tiles of ROWS, N-tiles of COLS and
     M-chunks of up to M_CHUNK rows; partial sums over K-tiles accumulate in the output
     accumulator (INT32), then requantize to INT8.
  2. Schedules work items over the cores (longest-processing-time first) and reports cycles,
     utilization and latency per layer and per network, for a YOLOv8-m-class network built
     from the published architecture (v8.0 yaml, width 0.75, depth 0.67, max 768 channels).
  3. Proves the lowering numerically: a small conv is lowered, executed tile by tile on the
     cycle-accurate core model (model/systolic_ref.py) and on the RTL under neosim, and the
     accumulated result is compared with a direct numpy convolution.

Usage: python3 compiler/neo_compile.py [--verify] [--report]
"""
import argparse
import math
import os
import sys
from dataclasses import dataclass, field
from typing import List, Tuple

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "model"))
sys.path.insert(0, os.path.join(HERE, "..", "sim"))

# ----------------------------------------------------------------------------
# Target description (v2.0 baseline, config C)
# ----------------------------------------------------------------------------
ROWS, COLS = 32, 32          # core geometry: K-tile x N-tile
CORES = 64
F_GHZ = 2.0                  # worst-case clock
PE_LAT = 2                   # psum pipeline depth of mac_pe
LAT = PE_LAT * ROWS + COLS   # core pipeline latency (proven: model T1, RTL T1)
M_CHUNK = 2048               # activation rows per work item (scratchpad sizing)
WEIGHT_LOAD = ROWS           # cycles to shift a weight tile in (v0.1: not hidden)
DOUBLE_BUFFERED_WEIGHTS = False   # v0.2 feature: hide WEIGHT_LOAD behind streaming
LOCAL_ACCUM = False               # v0.2 feature: accumulate K-tiles in a per-core accumulator SRAM
WINDOW_BUFFER = False             # v0.2 feature: sliding-window (implicit im2col) activation buffer
K_SPLIT = False                   # R8: split a group's K-tiles across cores, reduce into the owner
NOC_BYTES_PER_CYCLE = 2048   # 4 TB/s at 2 GHz into SRAM


def set_v02(ksplit=False):
    """Microarchitecture v0.2: double-buffered weights, local accumulator, window buffer (+ R8)."""
    global DOUBLE_BUFFERED_WEIGHTS, LOCAL_ACCUM, WINDOW_BUFFER, M_CHUNK, K_SPLIT
    DOUBLE_BUFFERED_WEIGHTS = LOCAL_ACCUM = WINDOW_BUFFER = True
    K_SPLIT = ksplit
    M_CHUNK = 512   # 512 rows x 32 cols x 32-bit accumulator = 64 KB per core


# ----------------------------------------------------------------------------
# Layers
# ----------------------------------------------------------------------------
@dataclass
class Conv:
    name: str
    h: int
    w: int
    cin: int
    cout: int
    k: int = 3
    s: int = 1
    p: int = 1

    @property
    def ho(self): return (self.h + 2 * self.p - self.k) // self.s + 1
    @property
    def wo(self): return (self.w + 2 * self.p - self.k) // self.s + 1
    @property
    def M(self): return self.ho * self.wo
    @property
    def K(self): return self.cin * self.k * self.k
    @property
    def N(self): return self.cout
    @property
    def macs(self): return self.M * self.K * self.N
    @property
    def weight_bytes(self): return self.K * self.N          # INT8
    @property
    def act_in_bytes(self): return self.h * self.w * self.cin
    @property
    def act_out_bytes(self): return self.M * self.N


@dataclass
class WorkItem:
    layer: str
    kt: int
    nt: int
    m0: int
    m1: int
    cycles: int


@dataclass
class LayerPlan:
    layer: Conv
    items: List[WorkItem]
    cycles: int          # max(compute, bandwidth)
    k_tiles: int
    n_tiles: int
    m_chunks: int
    compute_cycles: int = 0
    traffic_bytes: int = 0

    @property
    def bw_cycles(self):
        return math.ceil(self.traffic_bytes / NOC_BYTES_PER_CYCLE)

    @property
    def utilization(self):
        return self.layer.macs / (self.cycles * CORES * ROWS * COLS)

    @property
    def tile_efficiency(self):
        """MACs actually needed / MACs issued including padding of partial tiles."""
        issued = self.k_tiles * ROWS * self.n_tiles * COLS * self.layer.M
        return self.layer.macs / issued


MIN_CHUNK = 32   # smallest M-chunk worth a weight-tile pass


def chunk_size(layer: Conv, n_tiles: int) -> int:
    """M-chunk for this layer: as large as the accumulator allows, but small enough that the
    (output tile x M-chunk) groups, each of which must run on ONE core because partial sums
    accumulate locally, fill all the cores."""
    if not LOCAL_ACCUM:
        return M_CHUNK
    want_groups_per_tile = max(1, math.ceil(CORES / n_tiles))
    chunk = math.ceil(layer.M / want_groups_per_tile)
    chunk = max(MIN_CHUNK, min(M_CHUNK, chunk))
    return min(chunk, layer.M)


def choose_split(n_groups: int, k_tiles: int, chunk: int) -> int:
    """R8 split factor s: K-tiles of every group spread over s cores, partial sums reduced at the
    owner ((s-1) x chunk cycles). Pick the s that minimises the estimated makespan over CORES."""
    if not K_SPLIT or n_groups == 0:
        return 1
    best_s, best_t = 1, None
    for s in range(1, min(k_tiles, CORES) + 1):
        rounds = math.ceil(n_groups * s / CORES)
        t = rounds * (math.ceil(k_tiles / s) * chunk + LAT) + (s - 1) * chunk
        if best_t is None or t < best_t - 1:
            best_s, best_t = s, t
    return best_s


def lower(layer: Conv) -> LayerPlan:
    k_tiles = math.ceil(layer.K / ROWS)
    n_tiles = math.ceil(layer.N / COLS)
    chunk = chunk_size(layer, n_tiles)
    m_chunks = math.ceil(layer.M / chunk)
    items = []
    for kt in range(k_tiles):
        for nt in range(n_tiles):
            for mc in range(m_chunks):
                m0 = mc * chunk
                m1 = min(layer.M, m0 + chunk)
                cyc = (m1 - m0) + (0 if DOUBLE_BUFFERED_WEIGHTS else WEIGHT_LOAD)
                items.append(WorkItem(layer.name, kt, nt, m0, m1, cyc))
    loads = [0] * CORES
    if LOCAL_ACCUM:
        # schedule GROUPS (all K-tiles of one output tile x M-chunk) on one core each; when there
        # are fewer groups than cores, split each group's K-tiles over s cores (R8): contributors
        # run ceil(k_tiles/s) tiles, then the owner reduces (s-1) x chunk partial-sum rows
        groups = {}
        for it in items:
            groups[(it.nt, it.m0)] = groups.get((it.nt, it.m0), 0) + it.cycles
        s = choose_split(len(groups), k_tiles, chunk)
        for cyc in sorted(groups.values(), reverse=True):
            chunk_rows = cyc // k_tiles
            per_core = math.ceil(k_tiles / s) * chunk_rows
            owner = None
            for _ in range(s):
                i = loads.index(min(loads))
                loads[i] += per_core
                if owner is None:
                    owner = i
            if s > 1:
                loads[owner] += (s - 1) * chunk_rows                   # reduce pass at the owner
    else:
        for it in sorted(items, key=lambda w: -w.cycles):
            i = loads.index(min(loads))
            loads[i] += it.cycles
    compute_cycles = max(loads) + LAT
    # on-chip traffic (bytes): activations, weights, partial sums
    if WINDOW_BUFFER:
        act = layer.act_in_bytes * n_tiles                      # each input pixel read once per N-tile
    else:
        act = layer.M * layer.K * n_tiles                       # explicit im2col: K/cin-fold re-read
    wts = layer.weight_bytes * m_chunks
    if LOCAL_ACCUM:
        psum = 4 * layer.M * layer.N                            # written once after the last K-tile
    else:
        psum = 4 * layer.M * layer.N * k_tiles * 2              # read-modify-write per K-tile
    traffic = act + wts + psum
    cycles = max(compute_cycles, math.ceil(traffic / NOC_BYTES_PER_CYCLE))
    return LayerPlan(layer, items, cycles, k_tiles, n_tiles, m_chunks, compute_cycles, traffic)


# ----------------------------------------------------------------------------
# YOLOv8-m-class network (v8.0 yaml: width 0.75, depth 0.67, max_channels 768)
# ----------------------------------------------------------------------------
def yolov8m(h=640, w=640, nc=80) -> List[Conv]:
    layers: List[Conv] = []
    ch = {}  # layer index -> (h, w, c)

    def conv(name, hin, win, cin, cout, k, s):
        p = k // 2
        L = Conv(name, hin, win, cin, cout, k, s, p)
        layers.append(L)
        return L.ho, L.wo, cout

    def c2f(name, hin, win, cin, cout, n):
        c = cout // 2
        conv(f"{name}.cv1", hin, win, cin, 2 * c, 1, 1)
        for i in range(n):
            conv(f"{name}.m{i}.cv1", hin, win, c, c, 3, 1)
            conv(f"{name}.m{i}.cv2", hin, win, c, c, 3, 1)
        conv(f"{name}.cv2", hin, win, (2 + n) * c, cout, 1, 1)
        return hin, win, cout

    # backbone
    ch[0] = conv("b0.conv", h, w, 3, 48, 3, 2)
    ch[1] = conv("b1.conv", *ch[0], 96, 3, 2)
    ch[2] = c2f("b2.c2f", *ch[1], 96, 2)
    ch[3] = conv("b3.conv", *ch[2], 192, 3, 2)
    ch[4] = c2f("b4.c2f", *ch[3], 192, 4)
    ch[5] = conv("b5.conv", *ch[4], 384, 3, 2)
    ch[6] = c2f("b6.c2f", *ch[5], 384, 4)
    ch[7] = conv("b7.conv", *ch[6], 768, 3, 2)
    ch[8] = c2f("b8.c2f", *ch[7], 768, 2)
    hh, ww, _ = ch[8]
    conv("b9.sppf.cv1", hh, ww, 768, 384, 1, 1)
    ch[9] = conv("b9.sppf.cv2", hh, ww, 4 * 384, 768, 1, 1)
    # head
    h6, w6, c6 = ch[6]
    ch[12] = c2f("h12.c2f", h6, w6, c6 + 768, 384, 2)
    h4, w4, c4 = ch[4]
    ch[15] = c2f("h15.c2f", h4, w4, c4 + 384, 192, 2)             # P3 out
    ch[16] = conv("h16.conv", *ch[15], 192, 3, 2)
    ch[18] = c2f("h18.c2f", ch[16][0], ch[16][1], 192 + 384, 384, 2)  # P4 out
    ch[19] = conv("h19.conv", *ch[18], 384, 3, 2)
    ch[21] = c2f("h21.c2f", ch[19][0], ch[19][1], 384 + 768, 768, 2)  # P5 out
    # detect head
    c2 = max(16, 192 // 4, 16 * 4)
    c3 = max(192, min(nc, 100))
    for tag, (hh, ww, c) in (("p3", ch[15]), ("p4", ch[18]), ("p5", ch[21])):
        conv(f"det.{tag}.cv2.0", hh, ww, c, c2, 3, 1)
        conv(f"det.{tag}.cv2.1", hh, ww, c2, c2, 3, 1)
        conv(f"det.{tag}.cv2.2", hh, ww, c2, 64, 1, 1)
        conv(f"det.{tag}.cv3.0", hh, ww, c, c3, 3, 1)
        conv(f"det.{tag}.cv3.1", hh, ww, c3, c3, 3, 1)
        conv(f"det.{tag}.cv3.2", hh, ww, c3, nc, 1, 1)
    return layers


# ----------------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------------
def report(h=640, w=640):
    layers = yolov8m(h, w)
    plans = [lower(L) for L in layers]
    total_macs = sum(L.macs for L in layers)
    total_cycles = sum(p.cycles for p in plans)
    weights = sum(L.weight_bytes for L in layers)
    print(f"YOLOv8-m class network at {h}x{w}: {len(layers)} conv layers, "
          f"{total_macs / 1e9:.1f} GMAC ({2 * total_macs / 1e9:.1f} GFLOP; published YOLOv8m@640: 78.9 GFLOP), "
          f"weights {weights / 1e6:.1f} MB INT8 (published 25.9 M params)")
    print(f"target: {CORES} cores x {ROWS}x{COLS}, {F_GHZ} GHz, M_CHUNK={M_CHUNK}, NoC {NOC_BYTES_PER_CYCLE} B/cycle; "
          f"double-buffered weights={'yes' if DOUBLE_BUFFERED_WEIGHTS else 'no'}, "
          f"local accumulator={'yes' if LOCAL_ACCUM else 'no'}, window buffer={'yes' if WINDOW_BUFFER else 'no'}, "
          f"K-split={'yes' if K_SPLIT else 'no'}")
    print(f"{'layer':20s} {'M':>7s} {'K':>6s} {'N':>5s} {'GMAC':>7s} {'items':>6s} {'compute':>8s} {'bw-cyc':>8s} {'cycles':>8s} {'util':>6s}")
    worst = []
    for p in plans:
        L = p.layer
        worst.append((p.utilization, L.name))
        print(f"{L.name:20s} {L.M:7d} {L.K:6d} {L.N:5d} {L.macs / 1e9:7.3f} {len(p.items):6d} {p.compute_cycles:8d} "
              f"{p.bw_cycles:8d} {p.cycles:8d} {100 * p.utilization:5.1f}%")
    lat_ms = total_cycles / (F_GHZ * 1e9) * 1e3
    util = total_macs / (total_cycles * CORES * ROWS * COLS)
    compute_total = sum(p.compute_cycles for p in plans)
    bw_total = sum(p.bw_cycles for p in plans)
    traffic = sum(p.traffic_bytes for p in plans)
    print(f"{'TOTAL':20s} {'':>7s} {'':>6s} {'':>5s} {total_macs / 1e9:7.1f} {sum(len(p.items) for p in plans):6d} "
          f"{compute_total:8d} {bw_total:8d} {total_cycles:8d} {100 * util:5.1f}%")
    bound = "bandwidth" if bw_total > compute_total else "compute"
    print(f"latency {lat_ms:.2f} ms per frame at {F_GHZ} GHz ({1000 / lat_ms:.0f} fps), "
          f"network MAC utilization {100 * util:.1f}%, {bound}-bound; "
          f"on-chip traffic {traffic / 1e6:.0f} MB per frame")
    worst.sort()
    print("lowest-utilization layers: " + ", ".join(f"{n} ({100 * u:.0f}%)" for u, n in worst[:4]))
    return lat_ms, util


# ----------------------------------------------------------------------------
# Numerical verification of the lowering
# ----------------------------------------------------------------------------
def im2col(x, k, s, p):
    """x: [cin, h, w] -> [M, K] with K ordered (cin, ky, kx)."""
    cin, h, w = x.shape
    xp = np.pad(x, ((0, 0), (p, p), (p, p)))
    ho = (h + 2 * p - k) // s + 1
    wo = (w + 2 * p - k) // s + 1
    cols = np.zeros((ho * wo, cin * k * k), dtype=np.int64)
    for oy in range(ho):
        for ox in range(wo):
            patch = xp[:, oy * s: oy * s + k, ox * s: ox * s + k]
            cols[oy * wo + ox, :] = patch.reshape(-1)
    return cols


def direct_conv(x, wgt, s, p):
    """Reference convolution: x [cin,h,w], wgt [cout,cin,k,k] -> [cout, ho, wo] (int64)."""
    cout, cin, k, _ = wgt.shape
    xp = np.pad(x, ((0, 0), (p, p), (p, p)))
    ho = (x.shape[1] + 2 * p - k) // s + 1
    wo = (x.shape[2] + 2 * p - k) // s + 1
    y = np.zeros((cout, ho, wo), dtype=np.int64)
    for co in range(cout):
        for oy in range(ho):
            for ox in range(wo):
                y[co, oy, ox] = np.sum(xp[:, oy * s: oy * s + k, ox * s: ox * s + k] * wgt[co])
    return y


def run_tile_on_model(Xt, Wt, rows, cols):
    """Execute one (K-tile, N-tile) pass on the cycle-accurate core model; Xt [M, rows], Wt [rows, cols]."""
    from systolic_ref import MacCore
    core = MacCore(rows, cols)
    core.load_weights(Wt, Wt.sum(axis=1))
    Y, errs, _ = core.run(Xt)
    assert errs.sum() == 0, "ABFT flagged during a clean lowering run"
    return Y[:, :cols]


def run_tile_on_rtl(Xt, Wt, rows, cols):
    """Execute one pass on the RTL (rtl/*.sv) under neosim."""
    from neosim import Design, load
    rtl = os.path.join(HERE, "..", "rtl")
    files = ["delay_line.sv", "mac_pe.sv", "skew_in.sv", "deskew_out.sv",
             "systolic_array.sv", "abft_checker.sv", "neo_mac_core.sv"]
    d = Design(load([os.path.join(rtl, f) for f in files]))
    d.elaborate("neo_mac_core", {"ROWS": rows, "COLS": cols})
    wcw = 8 + (0 if rows <= 1 else (rows - 1).bit_length()) + 1
    cells = {n: d.cell_of(n) for n in ("rst_n", "w_load", "wc_in", "valid_in", "swap_in", "fault_inject",
                                       "abft_clear", "valid_out", "abft_err", "y_chk")}
    w_in, x_in, y = d.array_of("w_in"), d.array_of("x_in"), d.array_of("y")
    for n in ("w_load", "wc_in", "valid_in", "swap_in", "fault_inject", "abft_clear"):
        cells[n].v = 0
    cells["rst_n"].v = 0
    d.tick(); d.tick()
    cells["rst_n"].v = 1
    d.tick()
    Wc = Wt.sum(axis=1)
    for t in range(rows):
        cells["w_load"].v = 1
        for j in range(cols):
            w_in[j].v = int(Wt[rows - 1 - t, j]) & 0xFF
        cells["wc_in"].v = int(Wc[rows - 1 - t]) & ((1 << wcw) - 1)
        d.tick()
    cells["w_load"].v = 0
    M = Xt.shape[0]
    out = []
    for s in range(M + 1 + PE_LAT * rows + cols + 2):   # tick 0: swap token; ticks 1..M: rows
        m = s - 1
        cells["swap_in"].v = 1 if s == 0 else 0
        cells["valid_in"].v = 1 if 0 <= m < M else 0
        for i in range(rows):
            x_in[i].v = int(Xt[m, i]) & 0xFF if 0 <= m < M else 0
        d.tick()
        if cells["valid_out"].v:
            assert cells["abft_err"].v == 0
            out.append([(c.v - (1 << 32)) if c.v >= (1 << 31) else c.v for c in y])
    return np.array(out[:M], dtype=np.int64)


def verify(runner, rows, cols, label, seed=3):
    """Lower a small conv to tiles, execute every tile with `runner`, accumulate, compare."""
    rng = np.random.default_rng(seed)
    cin, h, w, cout, k, s, p = 5, 6, 6, 7, 3, 1, 1
    x = rng.integers(-128, 128, size=(cin, h, w), dtype=np.int64)
    wgt = rng.integers(-128, 128, size=(cout, cin, k, k), dtype=np.int64)
    ref = direct_conv(x, wgt, s, p)                      # [cout, ho, wo]
    Xc = im2col(x, k, s, p)                               # [M, K]
    Wm = wgt.reshape(cout, -1).T                          # [K, N], K ordered (cin, ky, kx)
    M, K = Xc.shape
    N = cout
    kt, nt = math.ceil(K / rows), math.ceil(N / cols)
    acc = np.zeros((M, N), dtype=np.int64)                # output accumulator (INT32 in hardware)
    passes = 0
    for a in range(kt):
        for b in range(nt):
            Xt = np.zeros((M, rows), dtype=np.int64)
            Wt = np.zeros((rows, cols), dtype=np.int64)
            kk = min(rows, K - a * rows)
            nn = min(cols, N - b * cols)
            Xt[:, :kk] = Xc[:, a * rows: a * rows + kk]
            Wt[:kk, :nn] = Wm[a * rows: a * rows + kk, b * cols: b * cols + nn]
            Y = runner(Xt, Wt, rows, cols)
            acc[:, b * cols: b * cols + nn] += Y[:, :nn]
            passes += 1
    got = acc.T.reshape(cout, ref.shape[1], ref.shape[2])
    ok = np.array_equal(got, ref)
    print(f"  lowering on {label:28s}: conv {cin}x{h}x{w} -> {cout}x{ref.shape[1]}x{ref.shape[2]} k{k} "
          f"= GEMM M={M} K={K} N={N}, {kt}x{nt} tiles of {rows}x{cols}, {passes} passes -> "
          f"{'PASS' if ok else 'FAIL'}")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--h", type=int, default=640)
    ap.add_argument("--w", type=int, default=640)
    ap.add_argument("--v02", action="store_true", help="model the v0.2 microarchitecture")
    ap.add_argument("--ksplit", action="store_true", help="R8: K-split across cores with reduction")
    a = ap.parse_args()
    if a.v02 or a.ksplit:
        set_v02(ksplit=a.ksplit)
    if not (a.verify or a.report):
        a.verify = a.report = True
    fails = 0
    if a.verify:
        print("Lowering verification (accumulated tile outputs vs direct numpy convolution):")
        fails += not verify(run_tile_on_model, 32, 32, "cycle model, 32x32 core")
        fails += not verify(run_tile_on_model, 16, 8, "cycle model, 16x8 core")
        fails += not verify(run_tile_on_rtl, 16, 8, "RTL under neosim, 16x8 core")
        print(f"RESULT: {'ALL PASS' if fails == 0 else f'{fails} FAILURES'}")
    if a.report:
        print()
        report(a.h, a.w)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
