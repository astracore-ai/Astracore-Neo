#!/usr/bin/env python3
"""
neo_sched.py -- from a quantized network (neo_quant.QNet) to what the runtime loads and runs (drop 0.44, stream D3): the
tensors' places in memory, every layer's groups as the backend's tile programs with their epilogues, packed into steps the
mesh runs at once, and the runtime's image (NetImage) -- the one description 04_software/neo_run.py (Python) and
04_software/neo_runtime.c (C) execute transaction for transaction.

The layout on a core of ROWS x COLS (the mesh's activation entry holds ROWS INT8 channels, its INT8 drain writes COLS):
  - a tensor of C channels, h x w pixels lives in ceil(C / cpt) channel tiles of h * w entries (WPA words each, pixels
    row-major), channel c at byte c % cpt of entry (c // cpt, pixel) -- cpt = min(ROWS, COLS) real channels per tile: on
    the 16x8 test cores every output tile of COLS = 8 channels is drained padded (DRAIN_WR's DR_P: a row as an entry of 16
    channels, the upper 8 zero -- stream C2's layout for the transformer tensors) and is the next layer's half-filled
    channel tile; on the 32x32 silicon core cpt = ROWS and a drained INT8 row is a whole entry (nothing padded).
  - a layer's weights are the backend's entries (pack_entry per row of ROWS channels in run order (ct, ky, kx), every
    output tile's block contiguous), the channels mapped to their padded rows; INT4 layers as neo_xfmr's w4_entry rows.
  - concat and split are placement: a concat's inputs are views at channel-tile offsets of its output's allocation, a
    split's outputs views of its input (a boundary must fall on a channel tile: 48 = 6 x 8 does; a boundary inside a
    tile would need a host copy and is refused with the layer named), so no byte moves.
  - a residual is the group's M rows of the other tensor's channel tile nt, fetched into the residual buffer (FETCH_A
    with arg 1) by the owner before its GO.
  - every tensor lives behind the memory bridge (the chip top's window at (BX, 0): the host writes it as its own DRAM) or
    in a tile's bank (through the host bridge) -- `homes` decides per tensor; the default is the memory window.

A layer's groups: output tile nt (COLS channels) x output-row chunk, the chunk as many rows as the accumulator (M <=
ACC_ROWS - 1), the activation buffer ((ct_hi + 1) x the chunk's input pixels <= ABUF_DEPTH, the feeder's absolute entry
index) and the weight buffer (a share's runs x ROWS <= WBUF_DEPTH, met by the K-split's shares) allow; the compile_group
of neo_backend emits the owner's and the contributors' programs. Groups of a layer are packed into steps of at most NX x NY
tiles (a group's tiles consecutive); a step is one command of the queue (one image, one completion record) or one window
load, the layer's steps in order, a layer's steps after its inputs' layers. cfg_m is never 0 (a descriptor of 0 rows
would never end its drain: review finding 13) -- the backend refuses it.
"""
import math
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "model"))
import neo_backend as nb  # noqa: E402
import neo_quant as nq  # noqa: E402
import neo_vec as nv  # noqa: E402
import neo_xfmr as nx  # noqa: E402
import vec_ref as vr  # noqa: E402
from neo_backend import ins, OP_FETCH_A, OP_FETCH_W, OP_DRAIN_WR, OP_GO, OP_WAIT_DONE, OP_END  # noqa: E402

ROW_ALIGN = 16                       # allocations start on a bank row
CMEM_WORDS = 1024                    # the control plane's command memory
CQ_LOG2, CC_LOG2 = 2, 2              # the runtime's rings: 4 slots each (3 in flight) at words 0 and 16
IMG_BASE = 32                        # the images from word 32: at most (1024 - 32) / 2 entries in one command
IMG_ENTRIES = (CMEM_WORDS - IMG_BASE) // 2


class NeoSchedError(Exception):
    pass


@dataclass
class MeshGeo:
    """The target: the chip top's build (the cocotb `top` suite's defaults: 2 x 2 tiles of 16x8 cores)."""
    nx: int = 2
    ny: int = 2
    rows: int = 16
    cols: int = 8
    nchk: int = 1
    acc_rows: int = 64
    abuf: int = 256
    wbuf: int = 512
    bank: int = 4096                 # words per bank
    mem_words: int = 1 << 20         # the memory bridge's window (20-bit word addresses)
    wpf: int = 32

    @property
    def nt(self):
        return self.nx * self.ny

    @property
    def bx(self):
        return self.nx                # the bridge column

    @property
    def cpt(self):
        return min(self.rows, self.cols)

    @property
    def padded(self):
        return self.cols < self.rows

    @property
    def wpa(self):
        return self.rows * 8 // 32

    @property
    def wpw(self):
        return (nb.entry_bits(self.cols, self.rows, self.nchk) + 31) // 32

    @property
    def wp4(self):
        return (self.cols * 4 + nb.wcw_of(self.rows) + 31) // 32

    @property
    def ni8(self):
        return self.cols // 4

    def xy(self, n):
        return n % self.nx, n // self.nx

    def node(self, xy):
        return xy[1] * self.nx + xy[0]


# ---------------------------------------------------------------------------------------------------------------------
# homes and tensor places
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class Place:
    home: str                        # "mem" or "bank<n>"
    base: int                        # word address
    ct_n: int
    h: int
    w: int

    def bank_xy(self, geo):
        return (geo.bx, 0) if self.home == "mem" else geo.xy(int(self.home[4:]))

    def tile_words(self, geo):
        return self.h * self.w * geo.wpa

    def view(self, ct0, ct_n):
        return Place(self.home, self.base + ct0 * self.h * self.w * 0, ct_n, self.h, self.w)      # (filled by the caller)


class Alloc:
    """Bump allocators per home, row-aligned."""

    def __init__(self, geo):
        self.geo = geo
        self.top = {"mem": 0}
        self.top.update({f"bank{n}": 0 for n in range(geo.nt)})

    def alloc(self, home, words):
        base = self.top[home]
        self.top[home] = (base + words + ROW_ALIGN - 1) // ROW_ALIGN * ROW_ALIGN
        cap = self.geo.mem_words if home == "mem" else self.geo.bank
        if self.top[home] > cap:
            raise NeoSchedError(f"{home}: {self.top[home]} words past its {cap}")
        return base


# ---------------------------------------------------------------------------------------------------------------------
# packing
# ---------------------------------------------------------------------------------------------------------------------
def pack_act(x, geo):
    """INT8 tensor [C, h, w] -> bank words in the padded channel-tile layout."""
    C, h, w = x.shape
    cpt, R = geo.cpt, geo.rows
    ct_n = (C + cpt - 1) // cpt
    words = []
    for ct in range(ct_n):
        for yy in range(h):
            for xx in range(w):
                ent = [int(x[ct * cpt + c, yy, xx]) if ct * cpt + c < C else 0 for c in range(cpt)] + [0] * (R - cpt)
                words += nx.bytes_to_words(ent, geo.wpa)
    return words


def unpack_act(words, C, h, w, geo):
    """Bank words of the padded layout -> INT8 tensor [C, h, w]."""
    cpt = geo.cpt
    out = np.zeros((C, h, w), dtype=np.int64)
    for ct in range((C + cpt - 1) // cpt):
        for yy in range(h):
            for xx in range(w):
                i = (ct * h * w + yy * w + xx) * geo.wpa
                ent = nx.words_to_bytes(words[i:i + geo.wpa], geo.rows)
                for c in range(cpt):
                    if ct * cpt + c < C:
                        out[ct * cpt + c, yy, xx] = ent[c]
    return out


def padded_channels(C, geo):
    """The padded channel count the backend sees: ceil(C / cpt) channel tiles of ROWS."""
    return (C + geo.cpt - 1) // geo.cpt * geo.rows


def pad_weights(w, geo):
    """w [cout, cin, k, k] -> [cout, cin_padded, k, k], channel c at padded row (c // cpt) * ROWS + c % cpt."""
    cout, cin, k, _ = w.shape
    out = np.zeros((cout, padded_channels(cin, geo), k, k), dtype=np.int64)
    for c in range(cin):
        out[:, (c // geo.cpt) * geo.rows + c % geo.cpt] = w[:, c]
    return out


def weight_words(ql, geo):
    """The layer's weight entries, every output tile's block in run order (ct, ky, kx) then row r: INT8 entries of
    neo_backend.pack_entry, or INT4 entries (neo_xfmr.w4_entry) for an INT4 layer."""
    wp = pad_weights(ql.w, geo)
    cout, cin_p, k, _ = wp.shape
    R, C = geo.rows, geo.cols
    ct_n = cin_p // R
    n_tiles = (cout + C - 1) // C
    g = nx.Geo(rows=R, cols=C, nchk=geo.nchk)
    words = []
    for nt in range(n_tiles):
        for ct in range(ct_n):
            for ky in range(k):
                for kx in range(k):
                    for r in range(R):
                        row = [int(wp[nt * C + j, ct * R + r, ky, kx]) if nt * C + j < cout else 0 for j in range(C)]
                        if ql.bits == 4:
                            words += nx.w4_entry(row, g)
                        else:
                            words += nb.pack_entry(row, [0] * C, nb.check_weights([row], [[0] * C], geo.nchk)[0], R, geo.nchk)
    return words


# ---------------------------------------------------------------------------------------------------------------------
# the runtime's image
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class TileStep:
    """One tile's part of a step: its descriptor (20 registers and cfg_m), requantizer tables, the vector unit's writes --
    through the window (the LUT tables: 256 entries each) and in the image (the program, K, SC, BI) -- and its program."""
    node: int
    cfg: List[int]
    cfg_m: int
    mult: List[int]
    shift: List[int]
    zp: List[int]
    relu: int
    win: List[Tuple[int, int]]       # (register, value) written through the tile's window before the step
    img: List[Tuple[int, int]]       # (register, value) entries of the command image (the small vector tables)
    prog: List[int]
    role: str = "solo"


@dataclass
class Step:
    name: str
    layer: str
    tiles: List[TileStep]


@dataclass
class Load:
    home: str
    addr: int
    words: List[int]
    name: str


@dataclass
class Readback:
    home: str
    addr: int
    words: List[int]                 # the expected words (the integer executor's)
    name: str


@dataclass
class NetImage:
    geo: MeshGeo
    loads: List[Load]
    steps: List[Step]
    reads: List[Readback]
    meta: dict = field(default_factory=dict)

    def entries(self, st: Step):
        return entries_of(st.tiles, self.geo)

    # ---- the text form (what the C runtime's test reads) ----
    def write(self, path):
        g = self.geo
        with open(path, "w") as f:
            f.write("NEOIMG 1\n")
            f.write(f"GEO {g.nx} {g.ny} {g.rows} {g.cols} {g.nchk} {g.acc_rows} {g.abuf} {g.wbuf} {g.bank} {g.wpf}\n")
            for ld in self.loads:
                f.write(f"LOAD {ld.home} {ld.addr} {len(ld.words)} {ld.name}\n")
                _hex(f, ld.words)
            for st in self.steps:
                f.write(f"STEP {st.name} {st.layer} {len(st.tiles)}\n")
                for t in st.tiles:
                    f.write(f"TILE {t.node} {t.role} {t.cfg_m} {t.relu}\n")
                    f.write("CFG " + " ".join(str(v & 0xFFFF) for v in t.cfg) + "\n")
                    f.write("RQ " + " ".join(f"{m} {s} {z & 0xFF}" for m, s, z in zip(t.mult, t.shift, t.zp)) + "\n")
                    f.write(f"WIN {len(t.win)}\n")
                    _hex(f, [v for rv in t.win for v in rv])
                    f.write(f"IMG {len(t.img)}\n")
                    _hex(f, [v for rv in t.img for v in rv])
                    f.write(f"PROG {len(t.prog)}\n")
                    _hex(f, t.prog, 16)
            for rd in self.reads:
                f.write(f"READ {rd.home} {rd.addr} {len(rd.words)} {rd.name}\n")
                _hex(f, rd.words)
            f.write("END\n")

    @staticmethod
    def read(path):
        with open(path) as f:
            toks = f.read().split()
        pos = 0

        def nxt():
            nonlocal pos
            pos += 1
            return toks[pos - 1]
        assert nxt() == "NEOIMG" and nxt() == "1"
        assert nxt() == "GEO"
        vals = [int(nxt()) for _ in range(10)]
        geo = MeshGeo(nx=vals[0], ny=vals[1], rows=vals[2], cols=vals[3], nchk=vals[4], acc_rows=vals[5], abuf=vals[6], wbuf=vals[7],
                      bank=vals[8], wpf=vals[9])
        img = NetImage(geo, [], [], [])
        while True:
            key = nxt()
            if key == "END":
                break
            if key == "LOAD":
                home, addr, n, name = nxt(), int(nxt()), int(nxt()), nxt()
                img.loads.append(Load(home, addr, [int(nxt(), 16) for _ in range(n)], name))
            elif key == "STEP":
                name, layer, ntl = nxt(), nxt(), int(nxt())
                st = Step(name, layer, [])
                for _ in range(ntl):
                    assert nxt() == "TILE"
                    node, role, cfg_m, relu = int(nxt()), nxt(), int(nxt()), int(nxt())
                    assert nxt() == "CFG"
                    cfg = [int(nxt()) for _ in range(20)]
                    assert nxt() == "RQ"
                    rq = [int(nxt()) for _ in range(3 * geo.cols)]
                    assert nxt() == "WIN"
                    nw = int(nxt())
                    win = [(int(nxt(), 16), int(nxt(), 16)) for _ in range(nw)]
                    assert nxt() == "IMG"
                    ni = int(nxt())
                    im = [(int(nxt(), 16), int(nxt(), 16)) for _ in range(ni)]
                    assert nxt() == "PROG"
                    npg = int(nxt())
                    prog = [int(nxt(), 16) for _ in range(npg)]
                    zp = [v - 256 if v > 127 else v for v in rq[2::3]]
                    st.tiles.append(TileStep(node, cfg, cfg_m, rq[0::3], rq[1::3], zp, relu, win, im, prog, role))
                img.steps.append(st)
            elif key == "READ":
                home, addr, n, name = nxt(), int(nxt()), int(nxt()), nxt()
                img.reads.append(Readback(home, addr, [int(nxt(), 16) for _ in range(n)], name))
            else:
                raise ValueError(f"image token {key}")
        return img


def entries_of(tiles, geo):
    """The command image of a step as neo_backend.cq_image lays it out: per tile the 20 descriptor registers, cfg_m, the
    requantizer's tables, the vector unit's small tables and program, the program words; then the telemetry clear of every
    tile (a fresh window) and the CTRL start broadcast. neo_runtime.c's neo_rt_image builds the same list."""
    ent = []
    for t in tiles:
        one = (1 << 14) | (t.node << 8)
        for i, v in enumerate(t.cfg):
            ent.append((one | (0x10 + i), v & 0xFFFF))
        ent.append((one | 0x24, t.cfg_m))
        for c in range(geo.cols):
            ent += [(one | 0x31, c), (one | 0x30, ((t.zp[c] & 0xFF) << 24) | (t.shift[c] << 16) | t.mult[c])]
        ent.append((one | 0x32, t.relu))
        for r, v in t.img:
            ent.append((one | r, v & 0xFFFFFFFF))
        for a, wd in enumerate(t.prog):
            ent += [(one | 0x02, a), (one | 0x03, wd & 0xFFFFFFFF), (one | 0x04, wd >> 32)]
    for t in tiles:
        ent.append(((1 << 14) | (t.node << 8) | 0x40, 2))
    ent.append((0x00, 1))
    return ent


def _hex(f, words, per=16):
    for i in range(0, len(words), per):
        f.write(" ".join(f"{int(v) & 0xFFFFFFFFFFFFFFFF:x}" for v in words[i:i + per]) + "\n")


# ---------------------------------------------------------------------------------------------------------------------
# placement
# ---------------------------------------------------------------------------------------------------------------------
def place_tensors(qn, geo, homes, alloc):
    """{tensor: Place}: concat outputs first (their inputs as views), splits as views, the rest allocated; a tensor with no
    layout constraint goes to homes.get(tensor, 'mem')."""
    g = qn.graph
    shapes = g.shapes
    cpt = geo.cpt

    def chw(name):
        s = shapes[name]
        return s[1], s[2], s[3]

    def tiles_of(name):
        return (chw(name)[0] + cpt - 1) // cpt
    anchor = {}                                                            # tensor -> (anchor tensor, tile offset)

    def set_anchor(t, root, off):
        """t sits at channel-tile offset `off` of `root`'s allocation; a tensor already anchored elsewhere (a split's output
        that a concat places) moves its earlier root under the new one."""
        if t in anchor:
            r0, o0 = anchor[t]
            if r0 == root:
                if o0 != off:
                    raise NeoSchedError(f"tensor '{t}' is placed by two layout operators at different offsets")
                return
            if root == t or r0 == root:
                raise NeoSchedError(f"tensor '{t}': circular placement")
            set_anchor(r0, root, off - o0)
            return
        anchor[t] = (root, off)
    for L in g.layers:
        if L.kind == "concat":
            if L.attrs["axis"] != 1:
                raise NeoSchedError(f"{L.name}: concat along axis {L.attrs['axis']} (channels only)")
            off = 0
            for src in L.inputs:
                C = chw(src)[0]
                if C % cpt:
                    raise NeoSchedError(f"{L.name}: input '{src}' of {C} channels ends inside a channel tile of {cpt} (a host copy would be needed)")
                set_anchor(src, L.outputs[0], off)
                off += C // cpt
        elif L.kind == "split":
            if L.attrs["axis"] != 1:
                raise NeoSchedError(f"{L.name}: split along axis {L.attrs['axis']} (channels only)")
            off = 0
            for dst in L.outputs:
                C = chw(dst)[0]
                if C % cpt:
                    raise NeoSchedError(f"{L.name}: output '{dst}' of {C} channels ends inside a channel tile of {cpt}")
                set_anchor(dst, L.inputs[0], off)
                off += C // cpt
        elif L.kind in ("maxpool", "resize", "reshape", "transpose", "flatten", "identity", "unsqueeze", "squeeze"):
            raise NeoSchedError(f"{L.name}: {L.kind} is not lowered to the mesh (imported and executed in the integer model only)")
        elif L.kind == "add" and L.outputs[0] not in qn.folded:
            raise NeoSchedError(f"{L.name}: an Add neither operand of which is a conv's sole consumer (no epilogue to fold it into)")

    def root_of(t):
        off = 0
        while t in anchor:
            t, o = anchor[t]
            off += o
        return t, off
    places = {}
    raw = {next(L for L in g.layers if L.name == conv).outputs[0] for conv in qn.folded.values()}   # a folded conv's own output
    for name in list(shapes):
        if name not in g.inputs and g.layer_of(name) is None:
            continue
        if name in raw or name.endswith(":pre"):
            continue
        root, off = root_of(name)
        if root not in places:
            C, h, w = chw(root)
            home = homes.get(root, "mem")
            places[root] = Place(home, alloc.alloc(home, tiles_of(root) * h * w * geo.wpa), tiles_of(root), h, w)
        pr = places[root]
        C, h, w = chw(name)
        if (h, w) != (pr.h, pr.w):
            raise NeoSchedError(f"tensor '{name}' is a view of '{root}' with another spatial size")
        places[name] = Place(pr.home, pr.base + off * h * w * geo.wpa, tiles_of(name), h, w)
    return places


# ---------------------------------------------------------------------------------------------------------------------
# groups and steps
# ---------------------------------------------------------------------------------------------------------------------
def layer_groups(ql, geo, inp: Place, out: Place, wplace: Tuple[str, int], res: Optional[Place]):
    """The groups of one conv layer as (nt, oy0, oy_n, shares): the chunk and the share count from the buffers' limits."""
    C, h, w = ql.cin, inp.h, inp.w
    k, s, p = ql.k, ql.s, ql.p
    ho, wo = (h + 2 * p - k) // s + 1, (w + 2 * p - k) // s + 1
    ct_n = inp.ct_n
    runs = ct_n * k * k
    n_tiles = (ql.cout + geo.cols - 1) // geo.cols
    max_rows = max(1, (geo.acc_rows - 1) // wo)
    oy_n = min(ho, max_rows)
    while oy_n > 1:
        iy_lo = max(0, 0 * s - p)
        iy_hi = min(h - 1, (oy_n - 1) * s + k - 1 - p)
        if ct_n * (iy_hi - iy_lo + 1) * w <= geo.abuf:
            break
        oy_n -= 1
    if ct_n * min(h, k) * w > geo.abuf and oy_n == 1:
        raise NeoSchedError(f"{ql.name}: one output row needs {ct_n * min(h, k) * w} activation entries, the buffer holds {geo.abuf}")
    shares = 1
    while True:
        bounds = [(i * runs) // shares for i in range(shares + 1)]
        if all((bounds[i + 1] - bounds[i]) * geo.rows <= geo.wbuf for i in range(shares)):
            break
        shares += 1
        if shares > geo.nt:
            raise NeoSchedError(f"{ql.name}: {runs} runs of {geo.rows} entries do not fit {geo.nt} weight buffers of {geo.wbuf}")
    groups = []
    for nt in range(n_tiles):
        for oy0 in range(0, ho, oy_n):
            groups.append((nt, oy0, min(oy_n, ho - oy0), shares))
    return groups, ho, wo


def tile_steps_of_group(ql, geo, inp, out, wplace, res, nt, oy0, oy_n, shares, tiles, vec_cache):
    """The tile programs of one group on `tiles` (the owner first): compile_group's descriptors and programs with the
    layer's epilogue of output tile nt (the requantizer's tables, the vector unit's writes)."""
    k, s, p = ql.k, ql.s, ql.p
    cin_p = padded_channels(ql.cin, geo)
    owner, free = geo.xy(tiles[0]), [geo.xy(t) for t in tiles[1:]]
    ho, wo = out.h, out.w
    out_addr = out.base + nt * ho * wo * geo.wpa + oy0 * wo * geo.wpa
    M = oy_n * wo
    rbuf = None
    if res is not None:
        rbuf = (res.bank_xy(geo), res.base + nt * ho * wo * geo.wpa + oy0 * wo * geo.wpa, M * geo.wpa)
    wb, wbase = wplace
    tps, _, _ = nb.compile_group(inp.h, inp.w, cin_p, ql.cout, k, s, p, geo.rows, geo.cols, nt, oy0, oy_n, inp.bank_xy(geo), inp.base,
                                 wb, wbase, owner, free, geo.nx, shares, int8_out=True, out_base=out_addr, out_bank=out.bank_xy(geo),
                                 nchk=geo.nchk, drain_fmt=(nx.DR_P if geo.padded else 0), int4=(ql.bits == 4), rbuf=rbuf)
    ep = nq.conv_epilogue(ql, geo.cols, nt)
    out_tiles = []
    for tp in tps:
        node = geo.node(tp.tile)
        cfg = [tp.descriptor.get(name, 0) for name in nb.CQ_CFG20]
        win, img = [], []
        if tp.role in ("owner", "solo"):
            vc = ep.vec if ep.vec is not None else nv.PASS
            held = vec_cache.setdefault(node, {})
            big, small = [], []
            for tbl, vals in ((vr.T_LUTV, vc.lutv), (vr.T_LUTS, vc.luts)):
                if vals is not None and held.get(tbl) != tuple(vals):
                    big += vr.table_writes(tbl, vals)
                    held[tbl] = tuple(vals)
            for tbl, vals in ((vr.T_SC, vc.sc), (vr.T_BI, vc.bi), (vr.T_K, vc.k)):
                if vals is not None and held.get(tbl) != tuple(vals):
                    small += vr.table_writes(tbl, vals)
                    held[tbl] = tuple(vals)
            small += vr.program_writes(vc.prog)
            win = nv.host_writes(big)
            img = nv.host_writes(small)
            mult, shift, zp, relu = ep.mult, ep.shift, ep.zp, ep.relu
        else:                                                              # a contributor drains partial sums: no epilogue
            mult, shift, zp, relu = [0] * geo.cols, [0] * geo.cols, [0] * geo.cols, 0
            held = vec_cache.setdefault(node, {})
            img = nv.host_writes(vr.program_writes(nv.PASS.prog))
        out_tiles.append(TileStep(node, cfg, tp.descriptor["cfg_m"], list(mult), list(shift), list(zp), relu, win, img, list(tp.program), tp.role))
    return out_tiles


def schedule(qn: nq.QNet, geo: MeshGeo, x_q, homes=None, expected=None, max_tiles=None) -> NetImage:
    """The runtime's image of a quantized network on `geo`: the loads (the input tensor, every layer's weights), the steps,
    the read-backs with the integer executor's expected words (`expected`: run_int's tensors, computed here when None).
    homes: {tensor or '<layer>.w': 'mem' | 'bank<n>'}."""
    homes = dict(homes or {})
    g = qn.graph
    alloc = Alloc(geo)
    places = place_tensors(qn, geo, homes, alloc)
    loads, steps, reads = [], [], []
    x_q = np.asarray(x_q, dtype=np.int64)
    inp = places[qn.input]
    loads.append(Load(inp.home, inp.base, pack_act(x_q[0], geo), qn.input))
    if expected is None:
        expected = nq.run_int(qn, x_q, geo.cols)
    wplaces = {}
    for name, ql in qn.qlayers.items():
        words = weight_words(ql, geo)
        home = homes.get(name + ".w", "mem")
        base = alloc.alloc(home, len(words))
        wplaces[name] = ((geo.bx, 0) if home == "mem" else geo.xy(int(home[4:])), base)
        loads.append(Load(home, base, words, name + ".w"))
    vec_cache = {}
    max_tiles = max_tiles or geo.nt
    n_groups = 0
    for L in g.layers:
        if L.kind not in ("conv", "linear"):
            continue
        if L.kind == "linear":
            raise NeoSchedError(f"{L.name}: linear layers are the transformer path's (neo_xfmr.block_plan)")
        ql = qn.qlayers[L.name]
        ip, op = places[ql.input], places[ql.output]
        res = places[ql.residual] if ql.residual is not None else None
        groups, ho, wo = layer_groups(ql, geo, ip, op, wplaces[L.name], res)
        n_groups += len(groups)
        # pack groups into steps: a group's `shares` tiles consecutive, at most max_tiles per step and one command image
        # (IMG_ENTRIES entries) per step; a group that does not fit the current step starts the next
        cur, t0, si = [], 0, 0
        for gi, (nt, oy0, oy_n, shares) in enumerate(groups):
            cache_try = {k: dict(v) for k, v in vec_cache.items()}
            tiles = list(range(t0, t0 + shares))
            if t0 + shares <= max_tiles:
                cand = tile_steps_of_group(ql, geo, ip, op, wplaces[L.name], res, nt, oy0, oy_n, shares, tiles, cache_try)
                if len(entries_of(cur + cand, geo)) <= IMG_ENTRIES:
                    cur += cand
                    vec_cache = cache_try
                    t0 += shares
                    continue
            if cur:
                steps.append(Step(f"{L.name}#{si}", L.name, cur))
                cur, t0, si = [], 0, si + 1
            tiles = list(range(0, shares))
            cur += tile_steps_of_group(ql, geo, ip, op, wplaces[L.name], res, nt, oy0, oy_n, shares, tiles, vec_cache)
            t0 = shares
        if cur:
            steps.append(Step(f"{L.name}#{si}", L.name, cur))
    for name in g.outputs:
        pl = places[name]
        C = g.shapes[name][1]
        reads.append(Readback(pl.home, pl.base, pack_act(expected[name][0], geo), name))
    for st in steps:
        for t in st.tiles:
            if t.cfg_m < 1:
                raise NeoSchedError(f"{st.name}: cfg_m = 0 (review finding 13: a descriptor of 0 rows never ends its drain)")
            assert len(t.prog) <= nb.PROG_DEPTH, f"{st.name}: a program of {len(t.prog)} words"
    img = NetImage(geo, loads, steps, reads, meta=dict(places=places, groups=n_groups, wplaces=wplaces, alloc_top=dict(alloc.top)))
    return img


def summary(img: NetImage):
    g = img.geo
    lines = [f"image: {len(img.loads)} loads ({sum(len(l.words) for l in img.loads)} words), {len(img.steps)} steps / "
             f"{img.meta.get('groups', '?')} groups / {sum(len(s.tiles) for s in img.steps)} tile programs, {len(img.reads)} read-backs; "
             f"memory used " + ", ".join(f"{k} {v}" for k, v in img.meta.get("alloc_top", {}).items() if v)]
    for st in img.steps:
        ent = len(img.entries(st))
        lines.append(f"  {st.name:<18} tiles {[t.node for t in st.tiles]} roles {[t.role[0] for t in st.tiles]} M {[t.cfg_m for t in st.tiles]} "
                     f"prog {[len(t.prog) for t in st.tiles]} image entries {ent} window writes {sum(len(t.win) for t in st.tiles)}")
    return "\n".join(lines)


if __name__ == "__main__":
    graph, model, calib, rng = nq.yolo_group_case()
    qn = nq.quantize(graph, calib)
    x = rng.normal(0, 1.0, (1, 3, 12, 12))
    img = schedule(qn, MeshGeo(), nq.quantize_input(qn, x))
    print(summary(img))
