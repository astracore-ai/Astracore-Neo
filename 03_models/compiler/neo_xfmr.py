#!/usr/bin/env python3
"""
neo_xfmr.py -- the compiler's transformer operators on the tile mesh (drop 0.43, stream C2): MatMul as a 1x1 convolution
with the NIC's drain layouts, the attention tiling, softmax / layer norm / RMS norm / GELU / SiLU / residual through the
vector unit (neo_vec.py's programs and tables), the INT4 weight packer, a transaction-level model of the mesh that
executes the emitted programs on bank words (the golden of the cocotb tests M21 / M23 / M24), and the perf model's
cost of every step.

The hardware this compiles for (drop 0.43):
  - a MatMul Y[M, N] = X[M, K] W[K, N] is a 1x1 convolution over M pixels: X's rows are activation entries (ROWS INT8
    channels a word group, channel tile ct of pixel m at entry ct * M + m), W's columns are output channels -- one output
    tile of COLS columns per descriptor run, its weight entries one per input channel (pack_entry: COLS weights and the
    check weight), runs in channel-tile order. A DMA program fetches the activations once and then runs one GO /
    WAIT_DONE pair per output tile (FETCH_W of the tile's weights, DRAIN_WR of its rows): WAIT_DONE waits for the last
    GO's done and DRAIN_WR for the previous drain (tile_dma, drop 0.43), so the descriptor is shared by the tiles.
  - the drain's INT8 layouts (tile_nic DRAIN_WR base): rows (COLS / 4 words), pad (DR_P: a row as an activation entry of
    ROWS channels, the upper ROWS - COLS zero -- the 16x8 core's 8 outputs as a channel tile of 16, half filled), entry
    (DR_E: a row as a weight entry, its check weight in the next word -- the tensor fetched as the next layer's weights),
    transpose (DR_T: blocks of COLS rows drained transposed -- tokens x channels into channels x tokens), and a group
    stride (every COLS rows start stride words after the group before) that scatters the entries into the weight tiles of
    the layer that fetches them.
  - INT4 weights: FETCH_W with arg 1 reads entries of COLS nibbles and the row's check weight (their sum, WCW bits above
    them: WP4 = (4 COLS + WCW + 31) / 32 words, 2 at 16x8 and 5 at 32x32) and the NIC writes the 8-bit entry (sign-extended
    nibbles, that check weight); the per-output-channel scale is the requantizer's (folded into mult / shift) or the
    vector unit's per-column SC. Per-group scales along K are not supported.

Attention (one head, T tokens, head dim d, the 2 x 2 mesh of 16x8 cores; attention_plan):
  1. projections: the twelve output tiles (COLS channels) of Q, K and V, three a tile, in two halves of 32 tokens (a
     descriptor drains at most ACC_ROWS - 1 rows: core_seq's cfg_m), every output tile drained into two copies (a second
     GO on the same weights; one copy for each pair of tiles, see PAIRS): Q padded (DR_P) into Q's activation layout, K
     transposed as weight entries (DR_T | DR_E, stride = one key tile's weight block) -- K^T for Q K^T, a transpose by the
     DMA's addressing -- and V as weight entries (DR_E, stride = one key tile's rows in P V's padded channel tiles).
  2. S = Q K^T per query block (tile t: queries 16t .. 16t + 15) and key tile (COLS keys a run): the softmax over all T
     keys in two rounds of the key tiles (neo_vec: RED max into RS0 (store, then max), then exp((s - RS0) * scale) EMIT ->
     P (INT8, padded), RS1 (+)= the sum); P's key tiles land as channel tiles of P V's activations.
  3. P V per output tile with V's entries as the weights; the drain normalises by 1 / RS1 (the reciprocal LUT and one
     Newton step in the unit's scalars) and the requantizer maps the row to the output's scale.
"""
import math
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "model"))
import neo_backend as nb  # noqa: E402
import neo_vec as nv  # noqa: E402
import vec_ref as vr  # noqa: E402
from neo_backend import (ins, OP_FETCH_A, OP_FETCH_W, OP_DRAIN_WR, OP_DRAIN_PSUM, OP_GO, OP_WAIT_DONE, OP_END)  # noqa: E402

DR_T, DR_E, DR_P = 1 << 15, 1 << 14, 1 << 13       # DRAIN_WR base: the INT8 rows' layout (drop 0.43)


# ---------------------------------------------------------------------------------------------------------------------
# geometry and bank formats
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class Geo:
    rows: int = 16
    cols: int = 8
    nchk: int = 1
    acc_rows: int = 64
    abuf: int = 256
    wbuf: int = 512
    bank: int = 4096
    nx: int = 2
    ny: int = 2
    wpf: int = 1                                   # link words per flit

    @property
    def wpa(self):
        return self.rows * 8 // 32

    @property
    def wpw(self):
        return (nb.entry_bits(self.cols, self.rows, self.nchk) + 31) // 32

    @property
    def wp4(self):
        return (self.cols * 4 + self.wcw + 31) // 32

    @property
    def ni8(self):
        return self.cols // 4

    @property
    def wcw(self):
        return nb.wcw_of(self.rows)

    def node(self, xy):
        return xy[1] * self.nx + xy[0]

    def xy(self, n):
        return n % self.nx, n // self.nx


def sext(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


def bytes_to_words(vals, nwords):
    """INT8 values, byte i in byte i % 4 of word i // 4, zero above, nwords words."""
    words = [0] * nwords
    for i, v in enumerate(vals):
        words[i // 4] |= (int(v) & 0xFF) << (8 * (i % 4))
    return words


def words_to_bytes(words, n):
    return [sext(words[i // 4] >> (8 * (i % 4)), 8) for i in range(n)]


def act_words(rows, geo):
    """Activation entries: each row (up to ROWS INT8 channels) as WPA words."""
    out = []
    for r in rows:
        out += bytes_to_words(r, geo.wpa)
    return out


def w_entry(wrow, geo):
    """One INT8 weight entry (COLS weights) with its check weight(s), as wbuf_mem and the bank hold it."""
    W = [[int(v) for v in wrow]]
    I = [[0] * geo.cols]
    return nb.pack_entry(W[0], I[0], nb.check_weights(W, I, geo.nchk)[0], geo.rows, geo.nchk)


def w4_entry(wrow, geo):
    """One INT4 weight entry: COLS nibbles (two's complement, -8..7), nibble j at bits [4j +: 4], the row's check weight
    (the sum, WCW bits two's complement) at [4 COLS +: WCW]; WP4 words."""
    v, tot = 0, 0
    for j, w in enumerate(wrow):
        w = int(w)
        if not -8 <= w <= 7:
            raise ValueError(f"INT4 weight {w} out of range (column {j})")
        v |= (w & 0xF) << (4 * j)
        tot += w
    v |= (tot & ((1 << geo.wcw) - 1)) << (4 * geo.cols)
    return [(v >> (32 * i)) & 0xFFFFFFFF for i in range(geo.wp4)]


def weight_tile_words(Wt, geo, int4=False, ct_n=None, rows_per_ct=None):
    """The weight entries of one output tile in run order: Wt[c][j] for input channel c (channel tile c // R, row c % R)
    and output column j < COLS; ct_n channel tiles of ROWS entries (channels past Wt's are zero). rows_per_ct: real
    channels per channel tile (a padded input -- half-filled channel tiles -- puts channel c at row c % rows_per_ct of
    channel tile c // rows_per_ct, the other rows zero)."""
    R, C = geo.rows, geo.cols
    rpc = rows_per_ct or R
    K = len(Wt)
    ct_n = ct_n or -(-K // rpc)
    out = []
    for ct in range(ct_n):
        for r in range(R):
            c = ct * rpc + r
            row = [int(Wt[c][j]) if (r < rpc and c < K and j < len(Wt[c])) else 0 for j in range(C)]
            out += w4_entry(row, geo) if int4 else w_entry(row, geo)
    return out


def quant_sym(Wf, bits=8, axis=None):
    """Symmetric quantization: (integers, scale) with scale = max |W| / (2^(bits-1) - 1) (per tensor, or per column along
    `axis` = 1: one scale per output channel -- the INT4 layers, the scale folded into the requantizer per column)."""
    import numpy as np
    W = np.asarray(Wf, dtype=np.float64)
    qmax = (1 << (bits - 1)) - 1
    if axis is None:
        s = max(float(np.abs(W).max()), 1e-12) / qmax
        return np.clip(np.round(W / s), -qmax - 1, qmax).astype(np.int64), s
    s = np.maximum(np.abs(W).max(axis=0), 1e-12) / qmax
    return np.clip(np.round(W / s), -qmax - 1, qmax).astype(np.int64), s


# ---------------------------------------------------------------------------------------------------------------------
# steps and phases
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class Step:
    """One DMA program on one tile: its descriptor, the vector unit's configuration, the requantizer's tables."""
    node: int
    desc: Dict[str, int]
    prog: List[int]
    vec: nv.VecConfig
    rq: Tuple[List[int], List[int], List[int]]
    relu: int = 0
    note: str = ""


@dataclass
class Phase:
    name: str
    steps: List[Step]


def gemm_desc(M, ct_n, h=None):
    """A 1x1 convolution over M pixels (h x w, M = h w) and ct_n channel tiles: the MatMul's descriptor."""
    h = h or (8 if M % 8 == 0 and M >= 8 else 1)
    w = M // h
    assert h * w == M
    return dict(cfg_h=h, cfg_w=w, cfg_ho=h, cfg_wo=w, cfg_oy0=0, cfg_oy_n=h, cfg_iy0=0, cfg_s=1, cfg_p=0, cfg_k=1,
                cfg_ct_n=ct_n, cfg_ct0=0, cfg_ky0=0, cfg_kx0=0, cfg_rn=ct_n, cfg_contrib_n=0, cfg_tile_pixels=M,
                cfg_regions_m1=0xFFFF, cfg_flags=0, cfg_m=M)


def flat_rq(mult, shift, zp, cols):
    return ([int(mult)] * cols, [int(shift)] * cols, [int(zp)] * cols)


# ---------------------------------------------------------------------------------------------------------------------
# the transaction-level model of the mesh: the programs executed on bank words
# ---------------------------------------------------------------------------------------------------------------------
class TileModel:
    def __init__(self, geo):
        self.geo = geo
        self.abuf = {}
        self.wbuf = {}
        self.vu = vr.VecUnit(geo.cols, geo.rows, geo.acc_rows)
        self.rq = flat_rq(0, 0, 0, geo.cols)
        self.relu = 0


class MeshModel:
    """Banks as word lists; every step of a phase executed in order (a phase's tiles write disjoint words and read only
    what earlier phases wrote, so the order inside a phase does not matter). Per step it records what the perf model
    needs: fetches (bank, words), runs, rows, the unit's cycles, drained words."""

    def __init__(self, geo):
        self.geo = geo
        self.banks = [[0] * geo.bank for _ in range(geo.nx * geo.ny)]
        self.tiles = [TileModel(geo) for _ in range(geo.nx * geo.ny)]
        self.log = []                                    # (phase, node, stats)
        self.no_self = True                              # the placement rule (see Banks): checked per phase
        self.touched = {}                                # phase -> [(bank, first word, words)] its drains wrote
        self.phase = None

    def load(self, node, base, words):
        self.banks[node][base:base + len(words)] = [int(w) & 0xFFFFFFFF for w in words]

    def read(self, node, base, n):
        return list(self.banks[node][base:base + n])

    def run_phase(self, ph):
        if self.no_self:                                 # no tile that runs a program has its bank read in the phase
            g = self.geo
            run = {st.node for st in ph.steps}
            read = {((w >> 52) & 15) * g.nx + ((w >> 56) & 15) for st in ph.steps for w in st.prog
                    if (w >> 60) & 15 in (OP_FETCH_A, OP_FETCH_W)}
            assert not (run & read), (f"phase {ph.name}: tiles {sorted(run & read)} run programs while their banks are read "
                                      f"-- the serve / send deadlock (see Banks)")
        self.phase = ph.name
        self.touched[ph.name] = []
        for st in ph.steps:
            self.log.append((ph.name, st.node, self.run_step(st)))

    def run_step(self, st):
        g, t = self.geo, self.tiles[st.node]
        t.vu.load(st.vec.table_writes())
        t.rq, t.relu = st.rq, st.relu
        stats = dict(fetch=[], gos=[], instr=len(st.prog))
        drain = None
        for w in st.prog:
            op, x, y = (w >> 60) & 15, (w >> 56) & 15, (w >> 52) & 15
            addr, ln, base, arg = (w >> 32) & 0xFFFFF, (w >> 20) & 0xFFF, (w >> 4) & 0xFFFF, w & 15
            src = y * g.nx + x
            if op == OP_FETCH_A:
                words = self.banks[src][addr:addr + ln]
                assert ln % g.wpa == 0, f"FETCH_A of {ln} words: not whole entries"
                for i in range(ln // g.wpa):
                    ent = words_to_bytes(words[i * g.wpa:(i + 1) * g.wpa], g.rows)
                    if arg & 1:
                        t.vu.write_rbuf(base + i, ent)
                    else:
                        assert base + i < g.abuf, "activation buffer overflow"
                        t.abuf[base + i] = ent
                stats["fetch"].append((src, ln, "rbuf" if arg & 1 else "act"))
            elif op == OP_FETCH_W:
                nwe = g.wp4 if arg & 1 else g.wpw
                assert ln % nwe == 0, f"FETCH_W of {ln} words: not whole entries"
                words = self.banks[src][addr:addr + ln]
                for i in range(ln // nwe):
                    ew = words[i * nwe:(i + 1) * nwe]
                    if arg & 1:
                        v = 0
                        for k, wd in enumerate(ew):
                            v |= wd << (32 * k)
                        wr = [sext(v >> (4 * j), 4) for j in range(g.cols)]
                        wc = sext(v >> (4 * g.cols), g.wcw)
                        assert wc == sext(sum(wr), g.wcw), f"INT4 entry {base + i}: check weight {wc} != sum {sum(wr)}"
                    else:
                        v = 0
                        for k, wd in enumerate(ew):
                            v |= wd << (32 * k)
                        wr = [sext(v >> (8 * j), 8) for j in range(g.cols)]
                        wc = sext(v >> (8 * g.cols), g.wcw)
                        assert wc == sext(sum(wr), g.wcw), f"weight entry {base + i}: check weight {wc} != sum {sum(wr)}"
                    assert base + i < g.wbuf, "weight buffer overflow"
                    t.wbuf[base + i] = wr
                stats["fetch"].append((src, ln, "w4" if arg & 1 else "w"))
            elif op == OP_DRAIN_WR:
                drain = (src, addr, ln, base, arg)
            elif op == OP_GO:
                stats["gos"].append(self.run_desc(t, st.desc, drain))
                drain = None
            elif op in (OP_WAIT_DONE, OP_END):
                pass
            else:
                raise ValueError(f"op {op} not modelled")
        return stats

    def run_desc(self, t, d, drain):
        g = self.geo
        M, ct_n, tp = d["cfg_m"], d["cfg_ct_n"], d["cfg_tile_pixels"]
        assert d["cfg_k"] == 1 and d["cfg_s"] == 1 and d["cfg_p"] == 0 and d["cfg_ct0"] == 0 and d["cfg_rn"] == ct_n
        assert M < g.acc_rows and tp == M, (f"M = {M}: core_seq's cfg_m holds IDXW bits, so a descriptor of ACC_ROWS rows "
                                            f"(cfg_m written as 0) never ends its drain -- at most ACC_ROWS - 1 rows")
        acc = [[0] * g.cols for _ in range(M)]
        for ct in range(ct_n):
            Wt = [t.wbuf[ct * g.rows + r] for r in range(g.rows)]
            for m in range(M):
                a = t.abuf[ct * tp + m]
                row = acc[m]
                for r in range(g.rows):
                    if a[r]:
                        ar, wr = a[r], Wt[r]
                        for j in range(g.cols):
                            row[j] += ar * wr[j]
        ucyc = 0
        if t.vu.active:
            rows, ucyc = t.vu.run(acc, cfg_m=M)
        else:
            rows = acc
        st = dict(M=M, runs=ct_n, rows=len(rows), ucyc=ucyc, drain_words=0, vec=t.vu.active)
        if drain is None:
            assert not t.vu.active or not rows, f"{len(rows)} rows emitted with no drain routed"
            return st
        dst, addr, ln, base, arg = drain
        assert ln == len(rows), f"DRAIN_WR of {ln} rows, the run emits {len(rows)}"
        if not arg & 1:                                  # INT32 rows + check word
            for i, r in enumerate(rows):
                ws = [v & 0xFFFFFFFF for v in r] + [sum(r) & 0xFFFFFFFF]
                self.banks[dst][addr + i * (g.cols + 1):addr + (i + 1) * (g.cols + 1)] = ws
                self.touched.setdefault(self.phase, []).append((dst, addr + i * (g.cols + 1), g.cols + 1))
            st["drain_words"] = len(rows) * (g.cols + 1)
            return st
        mult, shift, zp = t.rq
        q = [[nv.rq_model(v, mult[j], shift[j], zp[j], t.relu) for j, v in enumerate(r)] for r in rows]
        st["drain_words"] = self.write_int8(dst, addr, base, q)
        return st

    def write_int8(self, dst, addr, base, q):
        g = self.geo
        tr, ent, pad, stride = (base >> 15) & 1, (base >> 14) & 1, (base >> 13) & 1, base & 0x1FFF
        C = g.cols
        if tr:
            assert len(q) % C == 0, "a transposed drain is whole blocks of COLS rows"
            q = [[q[b * C + i][c] for i in range(C)] for b in range(len(q) // C) for c in range(C)]
        nw = g.wpw if ent else (max(g.wpa, g.ni8) if pad else g.ni8)
        for i, r in enumerate(q):
            ws = bytes_to_words(r, g.ni8)
            if ent:
                ws.append(sum(r) & ((1 << g.wcw) - 1))
            ws += [0] * (nw - len(ws))
            a = addr + ((i // C) * stride + (i % C) * nw if stride else i * nw)
            self.banks[dst][a:a + nw] = ws
            self.touched.setdefault(self.phase, []).append((dst, a, nw))
        return len(q) * nw


# ---------------------------------------------------------------------------------------------------------------------
# the perf model: cycles of a phase from its steps (calibrated against the 2x2 16x8 mesh's measured runs)
# ---------------------------------------------------------------------------------------------------------------------
PERF = dict(instr=3,          # cycles per DMA instruction: fetch from the program memory, issue
            fetch_req=24,     # a fetch's request, the server's start and the last word's arrival beyond the words
            run_fill=40,      # a descriptor's array fill, the accumulator's last row, the done
            drain_tail=12)    # the requantizer and the drain FIFO before the NIC's last word


def step_cycles(stats, geo, pm=PERF):
    """One step's cycles on its own tile: the instructions, every fetch (words over the link at WPF words a cycle plus its
    request), every run (the array's M x runs, the fill, then the sequencer's drain of M rows or the vector unit's
    cycles). Fetch and drain overlap nothing in this estimate except a drain with the next run's fetch."""
    c = stats["instr"] * pm["instr"]
    for src, words, kind in stats["fetch"]:
        c += pm["fetch_req"] + -(-words // geo.wpf)
    for go in stats["gos"]:
        c += pm["run_fill"] + go["M"] * go["runs"] + (go["ucyc"] if go["vec"] else go["M"]) + pm["drain_tail"]
    return c


def phase_prediction(model, phase_name, geo, pm=PERF):
    """A phase's predicted cycles: the slowest tile's step, or the busiest bank's serving (every word a server sends
    goes out one per cycle at WPF = 1), whichever is longer."""
    tiles, served = {}, {}
    for ph, node, st in model.log:
        if ph != phase_name:
            continue
        tiles[node] = tiles.get(node, 0) + step_cycles(st, geo, pm)
        for src, words, kind in st["fetch"]:
            served[src] = served.get(src, 0) + -(-words // geo.wpf) + pm["fetch_req"]
    return max(list(tiles.values()) + list(served.values()) + [0])


# ---------------------------------------------------------------------------------------------------------------------
# placement and scheduling: the serving banks and the computing tiles of a phase are disjoint. The NIC serves a fetch with
# its one transmit engine and holds the next read request at its input while it does, so a tile whose bank is read blocks
# every flit addressed to it -- responses to its own fetches, writes into its bank -- until its server is free; two tiles
# that both serve and fetch (or drain into each other) can wait on each other for ever (test_neo_top.py's self-serve note:
# runs 185 / 186 hung M21's projections and M23's P V that way; stream B2 adds the NIC's loopback and keeps a router
# entry for responses). Until then the plans run the tiles in two pairs, U = {0, 1} and L = {2, 3}: in a phase only one
# pair runs programs, fetching from the other pair's banks (which only serve) and draining into either; a tile's own
# tensors live in the other pair's bank (home(t) = t xor 2), and every tensor all tiles read (the weights, X, the K / V
# entries) is held twice, a copy in each pair. MeshModel asserts the rule per phase.
# ---------------------------------------------------------------------------------------------------------------------
PAIRS = ((0, 1), (2, 3))


def pair_of(t):
    return 0 if t in PAIRS[0] else 1


def home(t, nt=4):
    """Tile t's own tensors: in the other pair's bank."""
    return t ^ 2


class Banks:
    """A bump allocator per bank: alloc(node, words) -> base; pair_copies(words, upper, lower) places a copy in an upper and
    a lower bank and returns {bank: base}."""

    def __init__(self, geo, start=0):
        self.geo = geo
        self.top = [start] * (geo.nx * geo.ny)

    def alloc(self, node, n):
        base = self.top[node]
        self.top[node] += n
        assert self.top[node] <= self.geo.bank, f"bank {node}: {self.top[node]} words past the bank's {self.geo.bank}"
        return base

    def pair_copies(self, n, upper=None, lower=None):
        u = upper if upper is not None else min(PAIRS[0], key=lambda b: (self.top[b], b))
        lo = lower if lower is not None else min(PAIRS[1], key=lambda b: (self.top[b], b))
        return {u: self.alloc(u, n), lo: self.alloc(lo, n)}


def other(cp, t):
    """The copy tile t reads: the one in the other pair's bank."""
    for b in sorted(cp):
        if pair_of(b) != pair_of(t):
            return b, cp[b]
    raise AssertionError("no copy in the other pair")


# ---------------------------------------------------------------------------------------------------------------------
# attention: one head on the 2 x 2 mesh of 16x8 cores
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class AttnPlan:
    geo: Geo
    phases: List[Phase]
    loads: List[Tuple[int, int, List[int]]]           # (node, base, words): the banks' initial contents
    out: Dict[int, Tuple[int, int]]                   # query block -> (node, base) of its output rows
    T: int
    d: int
    meta: dict = field(default_factory=dict)


def attention_scales(X8, s_x, Wq, Wk, Wv, s_w, d):
    """The float attention on the dequantized inputs and the calibrated scales of Q / K / V (one scale for the three,
    the requantizer's tables shared by the projection program) and of the output."""
    import numpy as np
    Xf = X8 * s_x
    Wf = [w * s_w for w in (Wq, Wk, Wv)]
    Qf, Kf, Vf = (Xf @ w for w in Wf)
    s_qkv = max(float(np.abs(a).max()) for a in (Qf, Kf, Vf)) / 127.0
    S = (Qf @ Kf.T) / math.sqrt(d)
    S = S - S.max(axis=1, keepdims=True)
    P = np.exp(S)
    P /= P.sum(axis=1, keepdims=True)
    Of = P @ Vf
    s_o = float(np.abs(Of).max()) / 127.0
    return s_qkv, s_o, Of


def attention_plan(X8, Wq8, Wk8, Wv8, s_x, s_w, geo=None, seed_note=""):
    """M21: one attention head, T = 64 tokens, model dim D = 32, head dim d = 32 on the 2 x 2 mesh of 16x8 cores.
    X8 [T, D], W*8 [D, d] INT8 (one weight scale s_w), the input scale s_x. Returns the plan (phases, the banks' initial
    words, where the output lands) and the scales; the golden is MeshModel on the plan, the reference attention_ref."""
    import numpy as np
    geo = geo or Geo()
    R, C, WPA, WPW = geo.rows, geo.cols, geo.wpa, geo.wpw
    T, D = X8.shape
    d = Wq8.shape[1]
    NT = geo.nx * geo.ny
    assert (R, C) == (16, 8) and T == 64 and D == 32 and d == 32 and NT == 4, "the plan is written for M21's shape"
    QB = T // NT                                      # queries per tile
    ctx, ctq = D // R, d // C                         # X's channel tiles (full), Q's (half-filled: C real of R)
    KT = T // C                                       # key tiles
    ctp = KT                                          # P's channel tiles (half-filled)
    s_qkv, s_o, Of = attention_scales(X8, s_x, Wq8, Wk8, Wv8, s_w, d)
    # placement and schedule (see PAIRS): X and the three weight matrices in banks 0 and 2, the K entries in 0 and 2, V's
    # and Q in 1 and 3; P and O of query block t in bank home(t). The twelve output tiles of Q / K / V are computed three a
    # tile, in two halves of TH = 32 tokens (a descriptor drains at most ACC_ROWS - 1 rows: core_seq's cfg_m), each
    # drained into both copies (a second GO on the same weights): the first half on pair U (fetching from bank 2), then on
    # pair L (from bank 0), then the second half; then the attention of query blocks 0 / 1 on pair U, reading the lower
    # copies, and of blocks 2 / 3 on pair L
    WT = ctx * R * WPW                                # words of one output tile's weights (32 entries)
    KTB = ctq * R * WPW                               # one key tile's weight block in S = Q K^T: ctq channel tiles x R rows
    VTB = ctp * R * WPW                               # one output tile's weight block in P V: ctp channel tiles x R rows
    QW = ctq * T * WPA                                # Q in the padded activation layout
    bk = Banks(geo)
    xw = {b: bk.alloc(b, ctx * T * WPA + 3 * ctq * WT) for b in (0, 2)}
    kc = {b: bk.alloc(b, KT * KTB) for b in (0, 2)}
    vc = {b: bk.alloc(b, ctq * VTB) for b in (1, 3)}
    qc = {b: bk.alloc(b, QW) for b in (1, 3)}
    pb = {t: bk.alloc(home(t), ctp * QB * WPA) for t in range(NT)}
    ob = {t: bk.alloc(home(t), ctq * QB * geo.ni8) for t in range(NT)}
    work = {0: [("q", 0), ("q", 1), ("q", 2)], 1: [("q", 3), ("k", 0), ("k", 1)],
            2: [("k", 2), ("k", 3), ("v", 0)], 3: [("v", 1), ("v", 2), ("v", 3)]}
    X0, WQ0, WK0, WV0 = 0, ctx * T * WPA, ctx * T * WPA + ctq * WT, ctx * T * WPA + 2 * ctq * WT    # offsets in a copy
    loads = []
    xwords = [w for ct in range(ctx) for m in range(T) for w in act_words([X8[m, ct * R:(ct + 1) * R]], geo)]
    for b, base in xw.items():
        loads.append((b, base + X0, xwords))
        for off, Wm in ((WQ0, Wq8), (WK0, Wk8), (WV0, Wv8)):
            words = []
            for nt in range(ctq):
                words += weight_tile_words(Wm[:, nt * C:(nt + 1) * C], geo)
            loads.append((b, base + off, words))
    # requantizer tables
    m_qkv, sh_qkv, _ = nv.requant_for(s_x * s_w / s_qkv)
    k_exp, sh_exp = nv.fixed_mult(s_qkv * s_qkv / math.sqrt(d) / nv.EXP_S_IN)
    m_p, sh_p, _ = nv.requant_for(127.0 / 32768.0)
    alpha = m_p / float(1 << sh_p)                     # P = round(e * alpha): e in Q15
    m_o, sh_o, _ = nv.requant_for(s_qkv / (alpha * 32768.0) / s_o)
    rq_qkv = flat_rq(m_qkv, sh_qkv, 0, C)
    rq_p = flat_rq(m_p, sh_p, 0, C)
    rq_o = flat_rq(m_o, sh_o, 0, C)
    lut_e, lut_r = nv.lut_exp(), nv.lut_recip()
    phases = []

    # 1. projections: each output tile of Q / K / V on its producer, token half h (TH rows) drained into both copies (two
    #    GOs on one weight tile): Q's rows at token 32 h of its channel tile, K's blocks from key tile TH / C h, V's too
    TH = T // 2

    def drains(kk, nt, h):
        out = []
        for b in sorted({"q": qc, "k": kc, "v": vc}[kk]):
            base = {"q": qc, "k": kc, "v": vc}[kk][b]
            if kk == "q":                              # padded activation entries: channel tile nt of Q
                out.append(ins(OP_DRAIN_WR, *geo.xy(b), base + nt * T * WPA + h * TH * WPA, TH, DR_P, 1))
            elif kk == "k":                            # transposed, as S's weight entries: channel tile nt of key tile kt
                out.append(ins(OP_DRAIN_WR, *geo.xy(b), base + nt * R * WPW + h * (TH // C) * KTB, TH, DR_T | DR_E | KTB, 1))
            else:                                      # weight entries of P V's output tile nt: key tile kt = channel tile kt
                out.append(ins(OP_DRAIN_WR, *geo.xy(b), base + nt * VTB + h * (TH // C) * R * WPW, TH, DR_E | (R * WPW), 1))
        return out

    proj = []
    for h in range(2):
        steps = []
        for t in range(NT):
            b, base = other(xw, t)
            prog = [ins(OP_FETCH_A, *geo.xy(b), base + X0 + ct * T * WPA + h * TH * WPA, TH * WPA, ct * TH) for ct in range(ctx)]
            for kk, nt in work[t]:
                off = {"q": WQ0, "k": WK0, "v": WV0}[kk]
                prog.append(ins(OP_FETCH_W, *geo.xy(b), base + off + nt * WT, WT, 0))
                for dr in drains(kk, nt, h):
                    prog += [dr, ins(OP_GO), ins(OP_WAIT_DONE)]
            prog.append(ins(OP_END))
            steps.append(Step(t, gemm_desc(TH, ctx), prog, nv.PASS, rq_qkv, note=f"projections, tokens {TH * h}.., on tile {t}"))
        proj.append(Phase(f"projections h{h}", steps))

    def q_fetch(t):
        b, base = other(qc, t)
        return [ins(OP_FETCH_A, *geo.xy(b), base + ct * T * WPA + t * QB * WPA, QB * WPA, ct * QB) for ct in range(ctq)]

    def kw(t, kt):
        b, base = other(kc, t)
        return ins(OP_FETCH_W, *geo.xy(b), base + kt * KTB, KTB, 0)

    dS = gemm_desc(QB, ctq, h=4)
    # 2. the softmax's max round over the key tiles: the first key tile stores RS0, the others take the max
    phases.append(Phase("max kt0", [Step(t, dS, q_fetch(t) + [kw(t, 0), ins(OP_GO), ins(OP_WAIT_DONE), ins(OP_END)],
                                         nv.VecConfig(nv.prog_softmax_max(True), name="max first"), rq_p) for t in range(NT)]))
    phases.append(Phase("max kt1-7", [Step(t, dS, q_fetch(t) + [x for kt in range(1, KT) for x in (kw(t, kt), ins(OP_GO), ins(OP_WAIT_DONE))]
                                           + [ins(OP_END)], nv.VecConfig(nv.prog_softmax_max(False), name="max"), rq_p)
                                      for t in range(NT)]))

    # 3. the exp round: e = exp((s - max) * scale) EMIT -> P (INT8, padded) at P's channel tile kt; RS1 (+)= sum e
    def p_drain(t, kt):
        return ins(OP_DRAIN_WR, *geo.xy(home(t)), pb[t] + kt * QB * WPA, QB, DR_P, 1)

    kexp = [k_exp, 0, 0, 0]
    for name, kts, first in (("exp kt0", [0], True), ("exp kt1-6", range(1, KT - 1), False), ("exp kt7", [KT - 1], False)):
        steps = []
        for t in range(NT):
            prog = q_fetch(t)
            for kt in kts:
                prog += [kw(t, kt), p_drain(t, kt), ins(OP_GO), ins(OP_WAIT_DONE)]
            prog.append(ins(OP_END))
            vcfg = nv.VecConfig(nv.prog_softmax_exp(first, sh_exp), lutv=lut_e, k=kexp, name="exp")
            steps.append(Step(t, dS, prog, vcfg, rq_p))
        phases.append(Phase(name, steps))
    # 4. P V per output tile, normalised by 1 / RS1 on the drain
    dO = gemm_desc(QB, ctp, h=4)
    steps = []
    for t in range(NT):
        prog = [ins(OP_FETCH_A, *geo.xy(home(t)), pb[t] + ct * QB * WPA, QB * WPA, ct * QB) for ct in range(ctp)]
        vb, vbase = other(vc, t)
        for nt in range(ctq):
            prog += [ins(OP_FETCH_W, *geo.xy(vb), vbase + nt * VTB, VTB, 0),
                     ins(OP_DRAIN_WR, *geo.xy(home(t)), ob[t] + nt * QB * geo.ni8, QB, 0, 1), ins(OP_GO), ins(OP_WAIT_DONE)]
        prog.append(ins(OP_END))
        vcfg = nv.VecConfig(nv.prog_softmax_norm(15), luts=lut_r, k=[nv.RCP_K_OFF, nv.RCP_K_TWO, 0, 0], name="norm")
        steps.append(Step(t, dO, prog, vcfg, rq_o))
    phases.append(Phase("PV", steps))
    # the schedule: every phase in two halves (pair U's steps, then pair L's), the projections first for both pairs, then
    # the attention rounds of pair U, then pair L's
    halves = lambda ph, h: Phase(f"{ph.name} {'UL'[h]}", [st for st in ph.steps if pair_of(st.node) == h])  # noqa: E731
    phases = [halves(ph, h) for ph in proj for h in (0, 1)] + [halves(ph, h) for h in (0, 1) for ph in phases]
    for ph in phases:
        for st in ph.steps:
            assert len(st.prog) <= nb.PROG_DEPTH, f"{ph.name}: program of {len(st.prog)} instructions"
    out = {t: (home(t), ob[t]) for t in range(NT)}
    meta = dict(s_qkv=s_qkv, s_o=s_o, Of=Of, k_exp=k_exp, sh_exp=sh_exp, rq_qkv=(m_qkv, sh_qkv), rq_p=(m_p, sh_p),
                rq_o=(m_o, sh_o), QB=QB, ctq=ctq, KT=KT,
                regions=[("Q copy", b, qc[b], QW) for b in qc] + [("K^T entries", b, kc[b], KT * KTB) for b in kc]
                + [("V entries", b, vc[b], ctq * VTB) for b in vc] + [(f"P of block {t}", home(t), pb[t], ctp * QB * WPA) for t in range(NT)])
    return AttnPlan(geo, phases, loads, out, T, d, meta)


def read_attention_out(read, plan):
    """The INT8 output [T, d] from the banks through read(node, base, n) (MeshModel.read or the testbench's)."""
    import numpy as np
    g, QB, ctq = plan.geo, plan.meta["QB"], plan.meta["ctq"]
    O = np.zeros((plan.T, plan.d), dtype=np.int64)
    for t, (node, base) in plan.out.items():
        words = read(node, base, ctq * QB * g.ni8)
        for nt in range(ctq):
            for i in range(QB):
                w = words[(nt * QB + i) * g.ni8:(nt * QB + i + 1) * g.ni8]
                O[t * QB + i, nt * g.cols:(nt + 1) * g.cols] = words_to_bytes(w, g.cols)
    return O


def attention_ref(X8, Wq8, Wk8, Wv8, plan):
    """The integer chain in plain numpy, independent of the programs and the layouts: the projections' requantization,
    the scores per query block, the max, exp through the table, the probabilities' requantization, the sum, P V, the
    reciprocal with its Newton step, the output's requantization -- bit for bit what the mesh must compute."""
    import numpy as np
    m = plan.meta
    mq, sq = m["rq_qkv"]
    rq = np.vectorize(lambda a, mm, ss: nv.rq_model(int(a), mm, ss, 0))
    Q, K, V = (rq(X8 @ W, mq, sq) for W in (Wq8, Wk8, Wv8))
    lut_e, lut_r = nv.lut_exp(), nv.lut_recip()
    mp, sp = m["rq_p"]
    mo, so = m["rq_o"]
    T, C = plan.T, plan.geo.cols
    O = np.zeros((T, plan.d), dtype=np.int64)
    for i in range(T):
        s = [int(v) for v in Q[i] @ K.T]
        mx = max(s)
        e = [vr.lut_eval(lut_e, vr.lut_input(vr.sat(vr.rshr(vr.sat(v - mx) * m["k_exp"], m["sh_exp"])), 0)) for v in s]
        L = 0
        for kt in range(T // C):
            L = vr.sat(L + vr.sat(sum(e[kt * C:(kt + 1) * C])))
        p = [nv.rq_model(v, mp, sp, 0) for v in e]
        r1 = nv.recip_newton_int(L, lut_r)
        for c in range(plan.d):
            o = sum(p[j] * int(V[j, c]) for j in range(T))
            O[i, c] = nv.rq_model(vr.sat(vr.rshr(o * vr.s16(r1), 15)), mo, so, 0)
    return O


def m21_data(seed=21):
    """M21's tensors: X [64, 32] INT8 (scale 1/32), Wq / Wk / Wv [32, 32] from N(0, 1/32) quantized with one scale."""
    import numpy as np
    rng = np.random.default_rng(seed)
    T, D, d = 64, 32, 32
    X8 = rng.integers(-128, 128, size=(T, D)).astype(np.int64)
    Wf = rng.normal(0.0, 1.0 / math.sqrt(D), size=(3, D, d))
    s_w = float(np.abs(Wf).max()) / 127.0
    W8 = [np.clip(np.round(w / s_w), -127, 127).astype(np.int64) for w in Wf]
    return X8, W8[0], W8[1], W8[2], 1.0 / 32, s_w


# ---------------------------------------------------------------------------------------------------------------------
# epilogues and norms on the vector unit (per-column scales: an INT4 layer's per-output-channel weight scale)
# ---------------------------------------------------------------------------------------------------------------------
def common_shift(scales, max_mult=32767):
    """One shift for per-column multipliers: the largest sh with every round(scale * 2^sh) <= max_mult."""
    sh = 0
    while sh < 31 and max(scales) * (1 << (sh + 1)) <= max_mult:
        sh += 1
    return sh, [max(1, min(max_mult, int(round(s * (1 << sh))))) for s in scales]


def epilogue(s_acc, s_out, cols, kind=None, s_res=None, res_sh=10):
    """A linear layer's drain: s_acc[c] = the real value of an accumulator unit in column c (the input scale times the
    column's weight scale), then [+ the INT8 residual at s_res] [-> GELU / SiLU] -> INT8 at s_out. Returns (VecConfig,
    requantizer tables).
      plain:            no program (PASS); the requantizer per column: s_acc[c] / s_out (an INT4 layer's scale folded in)
      act:              MUL_SCALE (SC[c] = s_acc[c] in the table's input units 2^-12), LUT, EMIT; requant 2^-12 / s_out
      residual [+ act]: MUL_SCALE to s_res / 2^res_sh, ADD_ROW (the residual << res_sh), [BMUL K0 to 2^-12, LUT], EMIT"""
    if kind is None and s_res is None:
        rq = [nv.requant_for(s / s_out) for s in s_acc]
        return nv.PASS, ([m for m, _, _ in rq], [sh for _, sh, _ in rq], [0] * cols)
    prog, k, lutv = [], [0, 0, 0, 0], None
    if s_res is not None:
        s_z = s_res / (1 << res_sh)
        sh1, sc = common_shift([s / s_z for s in s_acc])
        prog += [vr.vop(vr.OP_MUL_SCALE, sh=sh1), vr.vop(vr.OP_ADD_ROW, sh=res_sh)]
        if kind is not None:
            k0, shk = nv.fixed_mult(s_z / nv.ACT_S_IN)
            k[0] = k0
            prog.append(vr.vop(vr.OP_BMUL, a=0, ka=1, sh=shk))
    else:
        sh1, sc = common_shift([s / nv.ACT_S_IN for s in s_acc])
        prog.append(vr.vop(vr.OP_MUL_SCALE, sh=sh1))
        s_z = nv.ACT_S_IN
    if kind is not None:
        prog.append(vr.vop(vr.OP_LUT, sh=0))
        lutv = nv.lut_silu() if kind == "silu" else nv.lut_gelu()
        s_y = nv.ACT_S_OUT
    else:
        s_y = s_z
    prog.append(vr.vop(vr.OP_EMIT))
    m, sh, _ = nv.requant_for(s_y / s_out)
    vc = nv.VecConfig(prog, lutv=lutv, sc=sc, bi=[0] * cols, k=k, name=f"{kind or 'linear'}{'+res' if s_res else ''}")
    return vc, flat_rq(m, sh, 0, cols)


def prog_norm_stats(first, rms):
    """A norm's statistics round over the identity GEMM's output tiles (rows = tokens, lanes = channels): RS0 (+)= sum x
    (layer norm only), RS1 (+)= sum x^2 -- stored on the first output tile, added after."""
    p = []
    if not rms:
        p += [vr.vop(vr.OP_RED, d=0, mode=1), vr.vop(vr.OP_RSST, a=0, t=0, mode=0 if first else 2)]
    p += [vr.vop(vr.OP_SQR, sh=0), vr.vop(vr.OP_RED, d=1, mode=1), vr.vop(vr.OP_RSST, a=1, t=1, mode=0 if first else 2)]
    return p


def prog_norm_apply(rms, sh_a, sh_r):
    """The apply round, the statistics finalised per row in the scalars (n = the channels, K0 = n, K1 / K2 the rsqrt
    table's input map): layer norm V = n S1 - S0^2 (= n^2 var), u = (V K1 >> sh_a) - K2 into the table, r = 2^13 /
    sqrt(V / V_cal); y = (n x - S0) r >> sh_r. RMS norm: V = S1 (= n mean square), y = x r >> sh_r. EMIT; the requantizer
    maps y to the normalised output's scale."""
    if rms:
        p = [vr.vop(vr.OP_SLD, d=1, mode=0, t=1)]
    else:
        p = [vr.vop(vr.OP_SLD, d=0, mode=0, t=0), vr.vop(vr.OP_SLD, d=1, mode=0, t=1),
             vr.vop(vr.OP_SMUL, d=2, a=0, b=0, sh=0),                       # S2 = S0^2
             vr.vop(vr.OP_SMUL, d=1, a=1, b=0, kb=1, sh=0),                 # S1 = n S1
             vr.vop(vr.OP_SALU, d=1, a=1, b=2, mode=1)]                     # S1 = V = n S1 - S0^2
    p += [vr.vop(vr.OP_SMUL, d=1, a=1, b=1, kb=1, sh=sh_a),                 # V K1 >> sh_a
          vr.vop(vr.OP_SALU, d=1, a=1, b=2, kb=1, mode=1),                  # - K2: the table's input
          vr.vop(vr.OP_SLUT, d=1, a=1, sh=0)]                               # r (Q13)
    if not rms:
        p += [vr.vop(vr.OP_BMUL, a=0, ka=1, sh=0), vr.vop(vr.OP_BSUB, a=0)]  # n x - S0
    p += [vr.vop(vr.OP_BMUL, a=1, sh=sh_r), vr.vop(vr.OP_EMIT)]
    return p


def norm_config(rms, n, v_cal, s_n, sh_r=0):
    """The apply round's program, rsqrt table, constants and requantizer for a norm over n channels whose variance
    quantity (V above, integers) is calibrated at v_cal; the output at s_n (x-hat = y s_n)."""
    k1, sh_a = nv.fixed_mult(1.0 / (v_cal * nv.RSQ_S_IN))       # V / v_cal in the table's input units
    k2 = int(round(nv.RSQ_C / nv.RSQ_S_IN))                     # minus the table's offset c
    k = [n, k1, k2, 0]
    # LN: x-hat = (n x - S0) / sqrt(V) = (n x - S0) r / (2^13 sqrt(v_cal)); RMS: x-hat = x sqrt(n) / sqrt(V)
    gain = (math.sqrt(n) if rms else 1.0) / ((1 << 13) * math.sqrt(v_cal))
    m, sh, _ = nv.requant_for(gain * (1 << sh_r) / s_n)
    vc = nv.VecConfig(prog_norm_apply(rms, sh_a, sh_r), luts=nv.lut_rsqrt(), k=k, name="rms" if rms else "ln")
    return vc, (m, sh)


# ---------------------------------------------------------------------------------------------------------------------
# a transformer block: [norm, attention, residual, norm, MLP, residual] on the 2 x 2 mesh of 16x8 cores
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class BlockCfg:
    name: str = "vit"
    T: int = 32                      # tokens (8 a tile: one key tile each)
    D: int = 32                      # model dim
    d: int = 32                      # head dim (one head)
    H: int = 64                      # MLP hidden
    rms: bool = False                # RMS norm (LLM) instead of layer norm (ViT)
    act: str = "gelu"                # MLP activation (gelu / silu)
    int4: bool = False               # INT4 weights (per output channel scales)
    causal: bool = False             # causal mask (LLM prefill)


MASK_SH = 23                         # a masked score: + (-128 << 23) = -2^30 before the max and the exp


def block_data(cfg, seed):
    """The block's input (INT8, scale 1/32) and float weights: W* ~ N(0, 1/fan_in), the norms' gains in [0.5, 1.5]."""
    import numpy as np
    rng = np.random.default_rng(seed)
    x8 = rng.integers(-96, 97, size=(cfg.T, cfg.D)).astype(np.int64)
    W = dict(q=rng.normal(0, 1 / math.sqrt(cfg.D), (cfg.D, cfg.d)), k=rng.normal(0, 1 / math.sqrt(cfg.D), (cfg.D, cfg.d)),
             v=rng.normal(0, 1 / math.sqrt(cfg.D), (cfg.D, cfg.d)), o=rng.normal(0, 1 / math.sqrt(cfg.d), (cfg.d, cfg.D)),
             w1=rng.normal(0, 1 / math.sqrt(cfg.D), (cfg.D, cfg.H)), w2=rng.normal(0, 1 / math.sqrt(cfg.H), (cfg.H, cfg.D)))
    g1, g2 = rng.uniform(0.5, 1.5, cfg.D), rng.uniform(0.5, 1.5, cfg.D)
    return x8, 1.0 / 32, W, g1, g2


def block_float(cfg, x, W, g1, g2):
    """The block in float (the reference): x + attn(norm1(x)) then + MLP(norm2(.)); the norms' gains as given."""
    import numpy as np

    def norm(v, g):
        if cfg.rms:
            return v / np.sqrt((v * v).mean(axis=1, keepdims=True)) * g
        mu = v.mean(axis=1, keepdims=True)
        return (v - mu) / np.sqrt(((v - mu) ** 2).mean(axis=1, keepdims=True)) * g

    act = nv.gelu if cfg.act == "gelu" else nv.silu
    n1 = norm(x, g1)
    q, k, v = n1 @ W["q"], n1 @ W["k"], n1 @ W["v"]
    s = q @ k.T / math.sqrt(cfg.d)
    if cfg.causal:
        s = s + np.triu(np.full((cfg.T, cfg.T), -1e30), 1)
    s = s - s.max(axis=1, keepdims=True)
    p = np.exp(s)
    p /= p.sum(axis=1, keepdims=True)
    a = p @ v
    h = x + a @ W["o"]
    n2 = norm(h, g2)
    z = np.vectorize(act)(n2 @ W["w1"])
    out = h + z @ W["w2"]
    return dict(n1=n1, q=q, k=k, v=v, a=a, h=h, n2=n2, z=z, out=out)


@dataclass
class BlockPlan:
    cfg: BlockCfg
    geo: Geo
    phases: List[Phase]
    loads: List[Tuple[int, int, List[int]]]
    out: Dict[int, Tuple[int, int]]                   # query block -> (node, base) of its output rows
    s_out: float
    ref: dict
    meta: dict = field(default_factory=dict)


def block_plan(cfg, x8, s_x, W, g1, g2, geo=None):
    """M23 (ViT block: layer norm, GELU, INT8 weights) / M24 (LLM layer: RMS norm, causal attention, SiLU MLP, INT4
    weights with per-output-channel scales) on the 2 x 2 mesh of 16x8 cores, T = 32 tokens (tile t: tokens 8t .. 8t + 7,
    a key tile), model / head dim 32, MLP hidden H (32 for M23's INT8 weights -- two copies of every matrix fit the four
    4,096-word banks --, 64 for M24's INT4). Every activation is INT8 in the padded layout (channel tile c holds channels
    8c .. 8c + 7: what the 16x8 core's drain writes, and what the residual buffer reads back as lanes 0 .. 7). Placement
    as Banks says: tile t's tensors in bank home(t), every weight matrix twice, the K and V entries of key tile kt (from
    tile kt) in banks home(kt) and kt + 2; the identity GEMM's weights as INT4 entries (0 / 1: exact).
      norm (identity GEMM per output tile; the norm's gain folded into the next layer's weights): statistics round (first
        output tile stores RS0 / RS1, the others add), apply round (the statistics finalised per row, rsqrt table)
      Q / K / V of the tile's tokens (K drained transposed as S's weight entries, V as P V's), the softmax's rounds over
        the key tiles (causal: the key tiles up to the tile's own, the diagonal one masked through the residual buffer:
        ADD_ROW of -128 << 23 on the masked keys), P V (causal: the visible key tiles' channel tiles only)
      Wo + the residual x (ADD_ROW from the residual buffer, filled per output tile), the second norm, W1 + GELU / SiLU,
      W2 + the residual h"""
    import numpy as np
    geo = geo or Geo()
    R, C, WPA, WPW = geo.rows, geo.cols, geo.wpa, geo.wpw
    T, D, d, H = cfg.T, cfg.D, cfg.d, cfg.H
    NT = geo.nx * geo.ny
    assert (R, C) == (16, 8) and NT == 4 and T == 4 * C and D == 32 and d == 32 and H in (32, 64)
    QB = T // NT                                       # tokens a tile (one key tile)
    cd, cdd, ch = D // C, d // C, H // C               # padded channel tiles of x / q / z
    KT = T // C
    nwe = geo.wp4 if cfg.int4 else WPW                 # words of a weight entry in the bank
    wfl = 1 if cfg.int4 else 0                         # FETCH_W arg

    # ---- quantization and scales: weights (the norms' gains folded into the next layer), calibration on the float run
    bits = 4 if cfg.int4 else 8
    Wq = {kk: W[kk] * (g1[:, None] if kk in "qkv" else 1.0) for kk in ("q", "k", "v", "o", "w1", "w2")}
    Wq["w1"] = W["w1"] * g2[:, None]
    q8, sw = {}, {}
    for kk, wm in Wq.items():
        q8[kk], s = quant_sym(wm, bits, axis=1 if cfg.int4 else None)
        sw[kk] = np.broadcast_to(np.asarray(s, dtype=np.float64), (wm.shape[1],)).copy()
    deq = {kk: q8[kk] * sw[kk][None, :] for kk in q8}
    x = x8 * s_x
    # the float run with the quantized weights calibrates every activation's scale
    g_one = np.ones(D)
    ref_q = block_float(cfg, x, dict(q=deq["q"], k=deq["k"], v=deq["v"], o=deq["o"], w1=deq["w1"], w2=deq["w2"]), g_one, g_one)
    ref = block_float(cfg, x, W, g1, g2)
    sc = {kk: float(np.abs(ref_q[kk]).max()) / 127.0 for kk in ("q", "k", "v", "a", "h", "z", "out")}
    s_n = 4.0 / 127.0                                  # a norm's output: x-hat in [-4, 4]

    def v_cal_of(v8):                                  # the norm's variance quantity, the median over the tokens
        v8 = np.asarray(v8, dtype=np.float64)
        V = (v8 * v8).sum(axis=1) if cfg.rms else D * (v8 * v8).sum(axis=1) - v8.sum(axis=1) ** 2
        return float(np.median(V))

    # ---- placement (see PAIRS): tile t's tensors in bank home(t), in the other pair; the weights and the K / V entries of
    #      every key tile once in each pair; every phase below runs in two halves, pair U's tiles then pair L's
    WTQ = cd * R * nwe                                 # one output tile's weights, input D (4 padded channel tiles)
    WT2 = ch * R * nwe                                 # W2's: input H (H / 8 padded channel tiles)
    WTI = R * geo.wp4                                  # the identity block: one channel tile's 16 INT4 entries (8 real rows)
    KTB = cdd * R * WPW                                # key tile kt's weight block in S (4 channel tiles of Q)
    VNB = R * WPW                                      # key tile kt's entries in P V's output tile nt (channel tile kt)
    PRIV = dict(x=cd * QB * WPA, n=cd * QB * WPA, qa=cdd * QB * WPA, h=cd * QB * WPA,
                pz=max(KT, ch) * QB * WPA, out=cd * QB * geo.ni8, mask=(2 * QB * WPA if cfg.causal else 0))
    bk = Banks(geo)
    pv = {t: {k: bk.alloc(home(t), n) for k, n in PRIV.items()} for t in range(NT)}
    kcp = {kt: bk.pair_copies(KTB) for kt in range(KT)}
    vcp = {kt: bk.pair_copies(cdd * VNB) for kt in range(KT)}
    wcp = {}
    for kk, n in (("w1", (H // C) * WTQ), ("w2", cd * WT2), ("q", cdd * WTQ), ("k", cdd * WTQ), ("v", cdd * WTQ),
                  ("o", cd * cdd * R * nwe), ("i", WTI)):
        wcp[kk] = bk.pair_copies(n)
    loads = []
    for t in range(NT):                                # x: the tile's tokens, padded channel tiles
        loads.append((home(t), pv[t]["x"], act_words([x8[t * QB + m, ct * C:(ct + 1) * C] for ct in range(cd) for m in range(QB)], geo)))
        if cfg.causal:                                 # the masks: zeros, and the diagonal key tile's
            diag = [[-128 if j > i else 0 for j in range(C)] for i in range(QB)]
            loads.append((home(t), pv[t]["mask"], act_words([[0] * C] * QB + diag, geo)))

    def wwords(Wm, ncts, int4):                        # one matrix's output tiles, the input padded (8 real rows a tile)
        out = []
        for nt in range(Wm.shape[1] // C):
            out += weight_tile_words(Wm[:, nt * C:(nt + 1) * C], geo, int4=int4, ct_n=ncts, rows_per_ct=C)
        return out

    mats = dict(q=(q8["q"], cd), k=(q8["k"], cd), v=(q8["v"], cd), o=(q8["o"], cdd), w1=(q8["w1"], cd), w2=(q8["w2"], ch))
    for kk, (Wm, ncts) in mats.items():
        words = wwords(Wm, ncts, cfg.int4)
        for b, base in wcp[kk].items():
            loads.append((b, base, words))
    iwords = weight_tile_words(np.eye(C, dtype=np.int64), geo, int4=True, ct_n=1, rows_per_ct=C)
    for b, base in wcp["i"].items():
        loads.append((b, base, iwords))
    for node, base, words in loads:
        assert base + len(words) <= geo.bank, f"bank {node}: {base} + {len(words)} words past the bank"

    phases = []
    hm = lambda t: geo.xy(home(t))  # noqa: E731
    dT = gemm_desc(QB, cd, h=2)

    def afetch(t, region, ncts):                       # a private tensor's padded channel tiles from home(t)
        return [ins(OP_FETCH_A, *hm(t), pv[t][region] + ct * QB * WPA, QB * WPA, ct * QB) for ct in range(ncts)]

    def gw(t, kk, nt, wt, int4=None):                  # output tile nt of matrix kk, from the copy outside t's bank
        b, base = other(wcp[kk], t)
        i4 = cfg.int4 if int4 is None else int4
        return ins(OP_FETCH_W, *geo.xy(b), base + nt * wt, wt, 0, 1 if i4 else 0)

    def iw(t):                                         # the identity block (the same for every output tile)
        return gw(t, "i", 0, WTI, int4=True)

    dI = gemm_desc(QB, 1, h=2)                         # the identity GEMM: one channel tile a run

    def norm_phases(tag, src, dst, v_cal):
        vca, (m, sh) = norm_config(cfg.rms, D, v_cal, s_n)
        rqn = flat_rq(m, sh, 0, C)
        st0 = nv.VecConfig(prog_norm_stats(True, cfg.rms), name="stats first")
        st1 = nv.VecConfig(prog_norm_stats(False, cfg.rms), name="stats")
        def ct(t, nt):                                 # output tile nt of the identity GEMM = the input's channel tile nt
            return ins(OP_FETCH_A, *hm(t), pv[t][src] + nt * QB * WPA, QB * WPA, 0)
        phases.append(Phase(f"{tag} stats nt0", [Step(t, dI, [iw(t), ct(t, 0), ins(OP_GO), ins(OP_WAIT_DONE), ins(OP_END)],
                                                       st0, rqn) for t in range(NT)]))
        phases.append(Phase(f"{tag} stats", [Step(t, dI, [iw(t)] + [x for nt in range(1, cd) for x in (ct(t, nt), ins(OP_GO), ins(OP_WAIT_DONE))]
                                                   + [ins(OP_END)], st1, rqn) for t in range(NT)]))
        phases.append(Phase(f"{tag} apply", [Step(t, dI, [iw(t)] + [x for nt in range(cd) for x in (
            ct(t, nt), ins(OP_DRAIN_WR, *hm(t), pv[t][dst] + nt * QB * WPA, QB, DR_P, 1), ins(OP_GO), ins(OP_WAIT_DONE))] + [ins(OP_END)], vca, rqn)
            for t in range(NT)]))

    # a linear layer over output tiles nts: one program per run of output tiles with the same epilogue (all of them
    # for per-tensor INT8 scales; one per output tile for INT4's per-output-channel scales, whose requantizer / SC tables
    # change with the tile), as many output tiles as the program memory holds; drains(t, nt) = the output tile's drains
    # (two for K and V: both copies, a GO each)
    def layer(tag, nts, src, ncts, kk, wt, drains, s_acc, s_out, kind=None, s_res=None, res_src=None, ndr=1):
        per = (nb.PROG_DEPTH - 1 - ncts) // (1 + 3 * ndr + (1 if res_src is not None else 0))
        groups = []
        for nt in nts:
            vc, rq = epilogue(list(s_acc[nt * C:(nt + 1) * C]), s_out, C, kind=kind, s_res=s_res)
            key = (tuple(vc.prog), tuple(vc.lutv or ()), tuple(vc.sc or ()), tuple(vc.bi or ()), tuple(vc.k or ()),
                   tuple(tuple(r) for r in rq))
            if groups and groups[-1][0] == key and len(groups[-1][3]) < per:
                groups[-1][3].append(nt)
            else:
                groups.append((key, vc, rq, [nt]))
        for _, vc, rq, g in groups:
            steps = []
            for t in range(NT):
                prog = afetch(t, src, ncts)
                for nt in g:
                    if res_src is not None:            # the residual's rows of output tile nt into the residual buffer
                        prog.append(ins(OP_FETCH_A, *hm(t), pv[t][res_src] + nt * QB * WPA, QB * WPA, 0, 1))
                    prog.append(gw(t, kk, nt, wt))
                    for dr in drains(t, nt):
                        prog += [dr, ins(OP_GO), ins(OP_WAIT_DONE)]
                steps.append(Step(t, gemm_desc(QB, ncts, h=2), prog + [ins(OP_END)], vc, rq))
            phases.append(Phase(f"{tag} nt{g[0]}" + (f"-{g[-1]}" if len(g) > 1 else ""), steps))

    def pad_to(region):
        return lambda t, nt: [ins(OP_DRAIN_WR, *hm(t), pv[t][region] + nt * QB * WPA, QB, DR_P, 1)]

    # 1. norm1(x) -> n
    norm_phases("norm1", "x", "n", v_cal_of(x8))
    # 2. Q / K / V of the tile's tokens (input n at s_n): Q padded into qa; K transposed as S's weight entries of key tile t
    #    (channels 8nt .. = S's channel tile nt), V as P V's entries of key tile t (= P's channel tile t), both copies
    layer("Q", range(cdd), "n", cd, "q", WTQ, pad_to("qa"), s_n * sw["q"], sc["q"])
    layer("K", range(cdd), "n", cd, "k", WTQ,
          lambda t, nt: [ins(OP_DRAIN_WR, *geo.xy(b), base + nt * R * WPW, QB, DR_T | DR_E, 1) for b, base in sorted(kcp[t].items())],
          s_n * sw["k"], sc["k"], ndr=2)
    layer("V", range(cdd), "n", cd, "v", WTQ,
          lambda t, nt: [ins(OP_DRAIN_WR, *geo.xy(b), base + nt * VNB, QB, DR_E, 1) for b, base in sorted(vcp[t].items())],
          s_n * sw["v"], sc["v"], ndr=2)
    # 3. the softmax rounds over the visible key tiles
    k_exp, sh_exp = nv.fixed_mult(sc["q"] * sc["k"] / math.sqrt(d) / nv.EXP_S_IN)
    m_p, sh_p, _ = nv.requant_for(127.0 / 32768.0)
    alpha = m_p / float(1 << sh_p)
    rq_p = flat_rq(m_p, sh_p, 0, C)
    lut_e = nv.lut_exp()
    mask = [vr.vop(vr.OP_ADD_ROW, sh=MASK_SH)] if cfg.causal else []
    last = (lambda t: t) if cfg.causal else (lambda t: KT - 1)

    def kw(t, kt):
        b, base = other(kcp[kt], t)
        return ins(OP_FETCH_W, *geo.xy(b), base, KTB, 0)

    def mfetch(t, kt):                                 # the mask rows for key tile kt (causal)
        return [ins(OP_FETCH_A, *hm(t), pv[t]["mask"] + (QB * WPA if kt == t else 0), QB * WPA, 0, 1)]

    def round_prog(t, kts, drain):
        prog, held = afetch(t, "qa", cdd), None
        for kt in kts:
            want = "diag" if kt == t else "zero"
            if cfg.causal and want != held:
                prog += mfetch(t, kt)
                held = want
            prog.append(kw(t, kt))
            if drain:
                prog.append(ins(OP_DRAIN_WR, *hm(t), pv[t]["pz"] + kt * QB * WPA, QB, DR_P, 1))
            prog += [ins(OP_GO), ins(OP_WAIT_DONE)]
        return prog + [ins(OP_END)]

    for rnd in ("max", "exp"):
        for first in (True, False):
            steps = []
            for t in range(NT):
                kts = [0] if first else list(range(1, last(t) + 1))
                if not kts:
                    continue
                if rnd == "max":
                    vc = nv.VecConfig(mask + nv.prog_softmax_max(first), name="max")
                else:
                    vc = nv.VecConfig(mask + nv.prog_softmax_exp(first, sh_exp), lutv=lut_e, k=[k_exp, 0, 0, 0], name="exp")
                steps.append(Step(t, gemm_desc(QB, cdd, h=2), round_prog(t, kts, rnd == "exp"), vc, rq_p))
            phases.append(Phase(f"{rnd} {'kt0' if first else 'rest'}", steps))
    # 4. P V -> a (padded, over Q's region), normalised by 1 / RS1; V's entries fetched per visible key tile from its copy
    m_a, sh_a, _ = nv.requant_for(sc["v"] / (alpha * 32768.0) / sc["a"])
    vcn = nv.VecConfig(nv.prog_softmax_norm(15), luts=nv.lut_recip(), k=[nv.RCP_K_OFF, nv.RCP_K_TWO, 0, 0], name="norm")
    for grp in ([0, 1], [2, 3]):
        steps = []
        for t in range(NT):
            nk = last(t) + 1
            prog = afetch(t, "pz", nk)
            for nt in grp:
                for kt in range(nk):
                    b, base = other(vcp[kt], t)
                    prog.append(ins(OP_FETCH_W, *geo.xy(b), base + nt * VNB, VNB, kt * R))
                prog += [ins(OP_DRAIN_WR, *hm(t), pv[t]["qa"] + nt * QB * WPA, QB, DR_P, 1), ins(OP_GO), ins(OP_WAIT_DONE)]
            steps.append(Step(t, gemm_desc(QB, nk, h=2), prog + [ins(OP_END)], vcn, flat_rq(m_a, sh_a, 0, C)))
        phases.append(Phase(f"PV nt{grp[0]}-{grp[-1]}", steps))
    # 5. h = x + a Wo (the residual x through the residual buffer, refilled per output tile)
    layer("Wo+x", range(D // C), "qa", cdd, "o", cdd * R * nwe, pad_to("h"), sc["a"] * sw["o"], sc["h"], s_res=s_x, res_src="x")
    # 6. norm2(h) -> n
    v2 = v_cal_of(np.clip(np.round(ref_q["h"] / sc["h"]), -128, 127))
    norm_phases("norm2", "h", "n", v2)
    # 7. z = act(n W1) (z over P's region)
    layer("W1", range(ch), "n", cd, "w1", WTQ, pad_to("pz"), s_n * sw["w1"], sc["z"], kind=cfg.act)
    # 8. out = h + z W2 (rows)
    layer("W2+h", range(D // C), "pz", ch, "w2", WT2,
          lambda t, nt: [ins(OP_DRAIN_WR, *hm(t), pv[t]["out"] + nt * QB * geo.ni8, QB, 0, 1)], sc["z"] * sw["w2"], sc["out"],
          s_res=sc["h"], res_src="h")
    # the schedule: every phase in two halves, pair U's steps then pair L's (a half with no step -- a causal round of
    # tile 0 -- is dropped)
    phases = [Phase(f"{ph.name} {'UL'[h]}", [st for st in ph.steps if pair_of(st.node) == h]) for ph in phases for h in (0, 1)]
    phases = [ph for ph in phases if ph.steps]
    for ph in phases:
        for st in ph.steps:
            assert len(st.prog) <= nb.PROG_DEPTH, f"{ph.name}: program of {len(st.prog)} instructions"
    meta = dict(sc=sc, s_n=s_n, QB=QB, ref_q=ref_q, priv=pv, top=list(bk.top))
    return BlockPlan(cfg, geo, phases, loads, {t: (home(t), pv[t]["out"]) for t in range(NT)}, sc["out"], ref, meta)


def read_block_out(read, plan):
    """The block's INT8 output [T, D] from the banks through read(node, base, n)."""
    import numpy as np
    g, QB, cfg = plan.geo, plan.meta["QB"], plan.cfg
    out = np.zeros((cfg.T, cfg.D), dtype=np.int64)
    for t in range(g.nx * g.ny):
        node, base = plan.out[t]
        words = read(node, base, (cfg.D // g.cols) * QB * g.ni8)
        for nt in range(cfg.D // g.cols):
            for i in range(QB):
                w = words[(nt * QB + i) * g.ni8:(nt * QB + i + 1) * g.ni8]
                out[t * QB + i, nt * g.cols:(nt + 1) * g.cols] = words_to_bytes(w, g.cols)
    return out


M23_CFG = BlockCfg(name="M23 ViT block", H=32, rms=False, act="gelu", int4=False, causal=False)
M24_CFG = BlockCfg(name="M24 LLM layer", rms=True, act="silu", int4=True, causal=True)


def block_errors(plan, got):
    """Max |error| in output LSB against the float block (the weights as given) and against the float block with the
    quantized weights (the pipeline's own error: INT8 activations, the tables, the unit's fixed point)."""
    import numpy as np
    so = plan.s_out
    return (float(np.abs(got * so - plan.ref["out"]).max() / so),
            float(np.abs(got * so - plan.meta["ref_q"]["out"]).max() / so))


def run_block_model(cfg, seed):
    """The block's plan through the mesh model: (plan, model, output, max |error| against the float block in output LSB)."""
    import numpy as np
    x8, s_x, W, g1, g2 = block_data(cfg, seed)
    plan = block_plan(cfg, x8, s_x, W, g1, g2)
    mm = MeshModel(plan.geo)
    for node, base, words in plan.loads:
        mm.load(node, base, words)
    for ph in plan.phases:
        mm.run_phase(ph)
    got = read_block_out(mm.read, plan)
    err = float(np.abs(got * plan.s_out - plan.ref["out"]).max() / plan.s_out)
    return plan, mm, got, err


def self_check(verbose=True):
    """The plan through the mesh model against the independent integer chain, bit for bit, and the error against the
    float attention (python-regress)."""
    import numpy as np
    X8, Wq, Wk, Wv, s_x, s_w = m21_data()
    plan = attention_plan(X8, Wq, Wk, Wv, s_x, s_w)
    mm = MeshModel(plan.geo)
    for node, base, words in plan.loads:
        mm.load(node, base, words)
    for ph in plan.phases:
        mm.run_phase(ph)
    got = read_attention_out(mm.read, plan)
    ref = attention_ref(X8, Wq, Wk, Wv, plan)
    ok = np.array_equal(got, ref)
    err = np.abs(got * plan.meta["s_o"] - plan.meta["Of"]).max() / plan.meta["s_o"]
    pred = {ph.name: phase_prediction(mm, ph.name, plan.geo) for ph in plan.phases}
    if verbose:
        print(f"M21 plan: {len(plan.phases)} phases, {sum(len(p.steps) for p in plan.phases)} programs "
              f"({max(len(s.prog) for p in plan.phases for s in p.steps)} instructions at most); the mesh model's output "
              f"{'equals' if ok else 'DIFFERS FROM'} the integer chain ({int((got != ref).sum())} values differ); max |error| "
              f"against the float attention {err:.2f} LSB of the output; predicted cycles "
              + ", ".join(f"{k} {v}" for k, v in pred.items()) + f" -> {sum(pred.values())} in all")
    for cfg, seed in ((M23_CFG, 23), (M24_CFG, 24)):
        plan, mm, got, err = run_block_model(cfg, seed)
        err, err_q = block_errors(plan, got)
        pred = sum(phase_prediction(mm, ph.name, plan.geo) for ph in plan.phases)
        if verbose:
            print(f"{cfg.name} plan: {len(plan.phases)} phases, {sum(len(p.steps) for p in plan.phases)} programs, weights "
                  f"{'INT4' if cfg.int4 else 'INT8'} (bank words used {plan.meta['top']}); max |error| {err_q:.2f} LSB of the output (scale "
                  f"{plan.s_out:.4g}) against the float block with the quantized weights, {err:.2f} against the float block; "
                  f"predicted {pred} cycles")
        ok = ok and err_q < 4.0
    return ok


if __name__ == "__main__":
    sys.exit(0 if self_check() else 1)
