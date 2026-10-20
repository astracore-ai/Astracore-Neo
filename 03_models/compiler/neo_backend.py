#!/usr/bin/env python3
"""
neo_backend.py -- compiler backend (drop 0.10): from a layer to what the tiles execute.

For one layer and one output tile x M-chunk group, the backend
  - splits the group's runs (ct, ky, kx) into S contiguous shares (R8) and places them on tiles:
    the owner on a chosen tile, contributors on its nearest free tiles;
  - lays out memory with the bank allocator (neo_alloc.py, drop 0.33): a layer's input activations are
    the regions where the previous layer's groups drained them (per channel tile, in bands of rows,
    each band one contiguous region of one bank), its weights a resident block per output tile, its
    output the bands of the next layer's input; a group's FETCH_A per channel tile covers its input
    window with one instruction per band it overlaps, its DRAIN_WR goes to the band holding its rows;
  - emits each tile's descriptor (cfg_* registers) and DMA program (FETCH_A per channel tile of
    its share, FETCH_W for its runs, DRAIN_WR/PSUM, GO, WAIT_REDUCE + NOTIFY on the owner,
    WAIT_RDY on contributors, WAIT_DONE, END), using exactly the protocol tile_dma executes.
compile_layer() returns a LayerProgram; run_tiles.py's M6 executes one on the 2x2 RTL mesh.
compile_pair() (drop 0.43) is the dual-tile lockstep form: the same layer whole on each tile of a pair -- the same
descriptor, the same fetches from the same banks, each tile draining into its own bank at the same address -- so the two
NICs drain the same messages and the control plane compares them (mesh_ctrl.sv, top_ctrl's LS_PAIR).
compile_network() runs the emitter over every layer of YOLOv8-m on the 8x8 mesh and reports
program and traffic statistics (static; execution of the whole network is beyond neosim's speed).

2:4 structured sparsity (drop 0.42; the NCHK = 4 core): check_2_4() is the validator (a weight tensor
must hold at most two nonzeros in every group of four consecutive input channels, for every output
channel and kernel position -- pruning is training-side; prune_2_4() is the magnitude pruning a
training flow applies, for tests and examples), compress_2_4() the compressor (group g of a 64-channel
tile on the PE pair 2g, 2g + 1: each PE one nonzero weight and its position as a 2-bit index, the lower
position on row 2g), check_weights() the four check weights of a row (the sum of its weights per
index), pack_entry() / pack_words() the bank and buffer layouts: the weight entry
{idx[COLS-1..0], wc[NCHK-1..0], w[COLS-1..0]} (wbuf_mem's), the activation layout of a sparse layer
(a 64-channel tile per pixel: channels 0..31 in the even entry, 32..63 in the odd one -- the pair word the
feeder reads), the descriptor's cfg_flags (host register 0x22: bit 0 sparse) and cfg_ct_n / cfg_ct0 in
64-channel tiles; compile_group(sparse=True, nchk=4) emits the descriptors and programs of a sparse layer.
"""
import math
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import neo_compile as nc  # noqa: E402
import neo_alloc as na  # noqa: E402

OP_FETCH_A, OP_FETCH_W, OP_DRAIN_WR, OP_DRAIN_PSUM, OP_GO, OP_WAIT_FREE, OP_WAIT_DONE, OP_END = 1, 2, 3, 4, 5, 6, 7, 8
OP_NOTIFY, OP_WAIT_RDY, OP_WAIT_REDUCE = 9, 10, 11


MAX_FETCH = 4095        # the DMA instruction's length field is 12 bits (drop 0.21: a fetch longer than that is split)
PROG_DEPTH = 32         # program memory per tile (prog_mem DEPTH; 32 since drop 0.22, was 16)


def ins(op, x=0, y=0, addr=0, length=0, base=0, arg=0):
    """64-bit DMA instruction: op[63:60] x[59:56] y[55:52] addr[51:32] len[31:20] base[19:4] arg[3:0].
    A field that does not fit is an error, never a silent overflow into the neighbouring field (drop 0.21); a FETCH of
    zero words is an error too (drop 0.29: the server answers it with a bare CRC flit, but no program has a use for it)."""
    if not (0 <= op < 16 and 0 <= x < 16 and 0 <= y < 16 and 0 <= addr < (1 << 20) and 0 <= length <= MAX_FETCH
            and 0 <= base < (1 << 16) and 0 <= arg < 16):
        raise ValueError(f"DMA instruction field out of range: op {op} x {x} y {y} addr {addr} len {length} base {base} arg {arg}")
    if op in (OP_FETCH_A, OP_FETCH_W) and length < 1:
        raise ValueError(f"FETCH of 0 words (op {op} addr {addr} base {base}): a fetch moves at least one word (drop 0.29)")
    return (op << 60) | (x << 56) | (y << 52) | (addr << 32) | (length << 20) | (base << 4) | arg


def fetch(op, bank, addr, words, base_entries, wpe):
    """FETCH_A/FETCH_W instructions covering `words` bank words from `addr` into local entries from `base_entries`:
    one instruction per MAX_FETCH words, cut at entry boundaries (`wpe` words per entry) because the NIC assembles
    an entry from the words of one instruction. One instruction at 16x8 for every test shape; a 32x32 weight tile
    (2,592 words) is one instruction, three tiles (7,776) are two."""
    per = (MAX_FETCH // wpe) * wpe
    out, off = [], 0
    while off < words:
        n = min(per, words - off)
        out.append(ins(op, bank[0], bank[1], addr + off, n, base_entries + off // wpe))
        off += n
    return out


@dataclass
class TileProgram:
    tile: Tuple[int, int]
    role: str                      # owner / contributor / solo
    descriptor: Dict[str, int]
    program: List[int]
    fetch_words: int = 0
    psum_words: int = 0
    out_words: int = 0


@dataclass
class LayerProgram:
    layer: str
    tiles: List[TileProgram]
    out_bank: Tuple[int, int]
    out_base: int
    M: int


def wcw_of(rows):
    """Check-weight width: WW + $clog2(ROWS) + 1 (neo_mac_core)."""
    return 8 + (0 if rows <= 1 else (rows - 1).bit_length()) + 1


def entry_bits(cols, rows, nchk=1):
    """Weight-buffer entry width (wbuf_mem's EW): COLS x (8 + index bits) + NCHK x WCW."""
    return cols * (8 + (2 if nchk > 1 else 0)) + nchk * wcw_of(rows)


def geometry(cin, cout, k, rows, cols, nchk=1, sparse=False):
    """(channel tiles, output tiles, words per pixel of a channel tile, words per weight entry). A sparse channel tile is
    2*ROWS channels (64 at 32 rows), stored as two activation entries per pixel; the weight entry follows the core's NCHK
    (9 words at 32x32 with NCHK = 1, 12 with NCHK = 4; 3 and 5 at 16x8)."""
    assert nchk in (1, 4) and (nchk == 4 or not sparse), "a sparse layer needs the NCHK = 4 core"
    ch = (2 if sparse else 1) * rows
    cin_tiles, cout_tiles = math.ceil(cin / ch), math.ceil(cout / cols)
    wpa = (2 if sparse else 1) * rows * 8 // 32
    wpw = (entry_bits(cols, rows, nchk) + 31) // 32
    return cin_tiles, cout_tiles, wpa, wpw


# ---------------------------------------------------------------------------------------------------------------------
# 2:4 structured sparsity (drop 0.42)
# ---------------------------------------------------------------------------------------------------------------------
def check_2_4(wgt):
    """The validator: wgt [cout, cin, k, k] must be 2:4 along K -- in every group of four consecutive input channels
    (channels 4g .. 4g + 3; a last group of fewer channels is padded with zeros) at most two weights are nonzero, for every
    output channel and kernel position. Raises ValueError naming the first group that is not."""
    import numpy as np
    w = np.asarray(wgt)
    cout, cin = w.shape[0], w.shape[1]
    pad = (-cin) % 4
    wp = np.concatenate([w, np.zeros((cout, pad) + w.shape[2:], dtype=w.dtype)], axis=1) if pad else w
    nz = (wp.reshape(cout, (cin + pad) // 4, 4, *w.shape[2:]) != 0).sum(axis=2)
    bad = np.argwhere(nz > 2)
    if bad.size:
        co, g = int(bad[0][0]), int(bad[0][1])
        pos = tuple(int(v) for v in bad[0][2:])
        raise ValueError(f"weights are not 2:4 along the input channels: output channel {co}, channels {4 * g}..{4 * g + 3}, "
                         f"kernel position {pos}: {int(nz[tuple(bad[0])])} nonzero weights in the group of four "
                         f"({len(bad)} such groups)")
    return True


def prune_2_4(wgt):
    """Magnitude pruning to 2:4 along K (what a training flow's pruning step leaves; for tests and examples): in every group
    of four consecutive input channels the two weights of largest magnitude stay, ties to the lower channel."""
    import numpy as np
    w = np.array(wgt, dtype=np.int64)
    cout, cin = w.shape[0], w.shape[1]
    for g in range(0, cin, 4):
        blk = w[:, g:g + 4]
        if blk.shape[1] <= 2:
            continue
        order = np.argsort(-np.abs(blk), axis=1, kind="stable")      # largest magnitudes first, stable for ties
        drop = order[:, 2:]
        np.put_along_axis(blk, drop, 0, axis=1)
        w[:, g:g + 4] = blk
    return w


def compress_2_4(wd, rows):
    """A dense pruned tile wd [2*rows, cols] (one 64-channel run at 32 rows: wd[c][j] multiplies channel c for output
    column j) -> the stored form W, I [rows, cols]: group g (channels 4g .. 4g + 3) on the PE pair 2g, 2g + 1, row 2g
    holding the group's nonzero weight at the lower position, row 2g + 1 the one at the higher, each with its position in
    the group as the index; a missing weight is (0, index 0). The model (systolic_ref.compress_2_4) follows the same rule."""
    k, n = len(wd), len(wd[0])
    assert k == 2 * rows
    W = [[0] * n for _ in range(rows)]
    I = [[0] * n for _ in range(rows)]
    for g in range(rows // 2):
        for j in range(n):
            nz = [p for p in range(4) if int(wd[4 * g + p][j]) != 0]
            if len(nz) > 2:
                raise ValueError(f"not 2:4: channels {4 * g}..{4 * g + 3} of column {j} hold {len(nz)} nonzero weights")
            for slot, p in enumerate(nz):
                W[2 * g + slot][j] = int(wd[4 * g + p][j])
                I[2 * g + slot][j] = p
    return W, I


def check_weights(W, I, nchk):
    """The check columns' weights of every row: wc[r][k] = the sum of row r's weights whose index is k (nchk = 1: the row
    sum; a dense row on the NCHK = 4 core: its sum in column 0, zeros in 1..3)."""
    out = []
    for r in range(len(W)):
        wc = [0] * nchk
        for j in range(len(W[r])):
            wc[I[r][j] if nchk > 1 else 0] += int(W[r][j])
        out.append(wc)
    return out


def pack_entry(w_row, i_row, wc_row, rows, nchk=1):
    """One weight-buffer / bank entry as 32-bit words: {idx[COLS-1..0] (2 bits), wc[NCHK-1..0] (WCW), w[COLS-1..0] (8)},
    LSB first -- wbuf_mem's layout (NCHK = 1: the weights, then the check weight, as before)."""
    cols = len(w_row)
    wcw = wcw_of(rows)
    v = 0
    for j in range(cols):
        v |= (int(w_row[j]) & 0xFF) << (8 * j)
    for k in range(nchk):
        v |= (int(wc_row[k]) & ((1 << wcw) - 1)) << (8 * cols + wcw * k)
    if nchk > 1:
        for j in range(cols):
            v |= (int(i_row[j]) & 3) << (8 * cols + wcw * nchk + 2 * j)
    nw = (entry_bits(cols, rows, nchk) + 31) // 32
    return [(v >> (32 * i)) & 0xFFFFFFFF for i in range(nw)]


def pack_words(x, wgt, cin_tiles, k, cout, rows, cols, nchk=1, sparse=False, nt=0):
    """Bank words of a layer: the activation channel tiles (row-major pixels; a dense tile is ROWS channels per pixel, one
    entry of ROWS/4 words, channel c in byte c % 4 of word c // 4; a sparse tile 2*ROWS channels per pixel, two entries)
    and the weight entries of output tile nt in run order (ct, ky, kx) then tile row r (pack_entry; a sparse run's tile is
    the 2:4 compressed form of its 2*ROWS input channels, check_2_4 having passed). Channels and output columns past the
    tensor are zero. x [cin, h, w], wgt [cout_all, cin, k, k]."""
    if sparse:
        check_2_4(wgt)
    ch = (2 if sparse else 1) * rows                   # channels per channel tile
    cin, h, w = x.shape
    cout_all = wgt.shape[0]
    act = []
    for ct in range(cin_tiles):
        for yy in range(h):
            for xx in range(w):
                words = [0] * (ch // 4)
                for c in range(ch):
                    ci = ct * ch + c
                    val = int(x[ci, yy, xx]) if ci < cin else 0
                    words[c // 4] |= (val & 0xFF) << (8 * (c % 4))
                act.extend(words)
    wts = []
    for ct in range(cin_tiles):
        for ky in range(k):
            for kx in range(k):
                tile = [[(int(wgt[nt * cols + j, ct * ch + c, ky, kx]) if (ct * ch + c < cin and nt * cols + j < min(cout, cout_all)) else 0)
                         for j in range(cols)] for c in range(ch)]
                if sparse:
                    W, I = compress_2_4(tile, rows)
                else:
                    W, I = tile, [[0] * cols for _ in range(rows)]
                WC = check_weights(W, I, nchk)
                for r in range(rows):
                    wts.extend(pack_entry(W[r], I[r], WC[r], rows, nchk))
    return act, wts


def nearest_free(owner, free, nx):
    ox, oy = owner
    return min(free, key=lambda t: (abs(t[0] - ox) + abs(t[1] - oy), t[1] * nx + t[0]))


def single_region_tensor(bank, base, cin_tiles, h, w, wpa):
    """The pre-allocator layout: channel tile ct whole at base + ct * h * w * wpa in one bank (the test meshes)."""
    t = na.Tensor(cin_tiles, h, w, wpa)
    for ct in range(cin_tiles):
        t.regions[ct] = [na.Region(ct, 0, h, tuple(bank), base + ct * h * w * wpa, h * w * wpa)]
    return t


def compile_group(h, w, cin, cout, k, s, p, rows, cols, nt, oy0, oy_n, act_bank, act_base, w_bank, w_base,
                  owner, free_tiles, nx, shares, int8_out=False, out_base=0, act=None, out_bank=None, nchk=1, sparse=False,
                  drain_fmt=0, int4=False, rbuf=None):
    """One (output tile nt, output-row range) group split into `shares` contiguous run ranges.
    `act`: the input tensor's regions (neo_alloc.Tensor); when None, channel tiles whole at act_bank/act_base.
    `out_bank`/`out_base`: where the group's M output pixels are drained (the owner's bank when out_bank is None).
    nchk: the core's check columns (the weight entry's format); sparse (drop 0.42, nchk = 4): a 2:4 layer -- channel tiles
    of 2*ROWS channels (cfg_ct_n / cfg_ct0 / cfg_rn count them), two activation entries per pixel (cfg_tile_pixels stays
    in pixels: the feeder doubles its addresses), cfg_flags = 1.
    Drop 0.44 (stream D3, the runtime's networks): drain_fmt = the INT8 drain's format in DRAIN_WR's base field (neo_xfmr's
    DR_P: a row padded to an activation entry of ROWS channels, the next layer's channel tile on a core with COLS < ROWS);
    int4: the weights are INT4 entries (WP4 words each, FETCH_W with arg 1: the NIC expands them); rbuf = (bank, addr,
    words): the owner fetches the group's M residual rows into the vector unit's residual buffer (FETCH_A with arg 1,
    entries from 0) before its GO."""
    ho, wo = (h + 2 * p - k) // s + 1, (w + 2 * p - k) // s + 1
    cin_tiles, _, wpa, wpw = geometry(cin, cout, k, rows, cols, nchk, sparse)
    if int4:
        assert not sparse, "INT4 entries have no 2:4 form"
        wpw = (cols * 4 + wcw_of(rows) + 31) // 32
    wpe = rows * 8 // 32                              # words per activation entry (the fetch cuts at entries)
    epp = 2 if sparse else 1                          # entries per pixel
    apt, wpt_run = h * w * wpa, rows * wpw            # words per activation tile, per run's weight tile
    if act is None:
        act = single_region_tensor(act_bank, act_base, cin_tiles, h, w, wpa)
    out_bank = tuple(owner) if out_bank is None else tuple(out_bank)
    # input rows this output-row range touches: [iy_lo, iy_hi], clipped to the tile
    iy_lo = max(0, oy0 * s - p)
    iy_hi = min(h - 1, (oy0 + oy_n - 1) * s + k - 1 - p)
    nrows = iy_hi - iy_lo + 1
    region_pixels = nrows * w                         # region holds only the rows needed
    runs = cin_tiles * k * k
    shares = max(1, min(shares, runs, 1 + len(free_tiles)))
    bounds = [(i * runs) // shares for i in range(shares + 1)]
    M = oy_n * wo
    if M < 1:
        raise ValueError(f"a group of {oy_n} output rows (cfg_m = {M}): a descriptor of 0 rows never ends its drain (review finding 13)")
    tiles, free = [], list(free_tiles)
    placed = [owner]
    for i in range(shares):
        r0, r1 = bounds[i], bounds[i + 1]
        if r1 <= r0:
            continue
        ct0, ky0, kx0 = r0 // (k * k), (r0 % (k * k)) // k, r0 % k
        ct_lo, ct_hi = r0 // (k * k), (r1 - 1) // (k * k)
        if i == 0:
            tile = owner
        else:
            tile = nearest_free(owner, free, nx)
            free.remove(tile)
            placed.append(tile)
        desc = dict(cfg_h=h, cfg_w=w, cfg_ho=ho, cfg_wo=wo, cfg_oy0=oy0, cfg_oy_n=oy_n, cfg_iy0=iy_lo, cfg_s=s, cfg_p=p,
                    cfg_k=k, cfg_ct_n=cin_tiles, cfg_ct0=ct0, cfg_ky0=ky0, cfg_kx0=kx0, cfg_rn=r1 - r0,
                    cfg_contrib_n=(shares - 1) if i == 0 else 0, cfg_tile_pixels=region_pixels, cfg_regions_m1=0xFFFF, cfg_m=M)
        if sparse:
            # the flags register (host 0x22, cfg[18]) appears when a flag is set; the C driver writes all 20 descriptor
            # registers, so a dense layer after a sparse one on the same tile clears it (neo_host.c, D_FLAGS)
            desc["cfg_flags"] = 1
        # activations: only rows iy_lo..iy_hi of each channel tile -- one FETCH_A per band of the tensor the window
        # overlaps (rows contiguous inside a band, row-major, wpa words per pixel); a sparse pixel is two entries
        prog = []
        for ct in range(ct_lo, ct_hi + 1):
            for bank, addr, ya, yb in act.segments(ct, iy_lo, iy_hi):
                prog += fetch(OP_FETCH_A, bank, addr, (yb - ya + 1) * w * wpa, (ct * region_pixels + (ya - iy_lo) * w) * epp, wpe)
        prog += fetch(OP_FETCH_W, w_bank, w_base + (nt * runs + r0) * wpt_run, (r1 - r0) * wpt_run, 0, wpw)
        if int4:
            prog[-1] |= 1                                                  # FETCH_W arg 1: INT4 entries
        if i == 0 and rbuf is not None:
            prog.append(ins(OP_FETCH_A, rbuf[0][0], rbuf[0][1], rbuf[1], rbuf[2], 0, 1))
        fetch_words = (ct_hi - ct_lo + 1) * region_pixels * wpa + (r1 - r0) * wpt_run
        if i == 0:
            tp = TileProgram(tile, "owner" if shares > 1 else "solo", desc, [], fetch_words=fetch_words, out_words=M * (cols + 1))
            tiles.append(tp)
        else:
            prog += [ins(OP_GO), ins(OP_WAIT_RDY), ins(OP_DRAIN_PSUM, owner[0], owner[1], 0, M), ins(OP_WAIT_DONE), ins(OP_END)]
            tiles.append(TileProgram(tile, "contributor", desc, prog, fetch_words=fetch_words, psum_words=M * (cols + 1)))
        if i == 0:
            tp.program = prog
    # finish the owner's program now that contributors are placed; INT8 output is the next layer's input
    owner_tp = tiles[0]
    owner_tp.program += [ins(OP_DRAIN_WR, out_bank[0], out_bank[1], out_base, M, drain_fmt if int8_out else 0, 1 if int8_out else 0), ins(OP_GO)]
    if shares > 1:
        owner_tp.program.append(ins(OP_WAIT_REDUCE))
        owner_tp.program += [ins(OP_NOTIFY, t.tile[0], t.tile[1]) for t in tiles[1:]]
    owner_tp.program += [ins(OP_WAIT_DONE), ins(OP_END)]
    return tiles, free, out_base


def compile_layer(name, h, w, cin, cout, k, s, p, rows, cols, nx, ny, act_bank, act_base, w_bank, w_base,
                  owner=(0, 0), shares=1, oy0=0, oy_n=None, int8_out=False, out_base=0, exclude=(), nchk=1, sparse=False,
                  drain_fmt=0, int4=False, rbuf=None) -> LayerProgram:
    """Single group (one output tile, one output-row range) executable on the test mesh. `exclude` lists tiles that take no
    share (the tile-exclusion mask of compile_network, the chip top's disabled tiles); act_bank / w_bank may be a bridge
    position (x = nx, the chip top's bridge column): a bridge is addressed exactly like a bank; nchk / sparse: compile_group."""
    ho = (h + 2 * p - k) // s + 1
    oy_n = ho if oy_n is None else oy_n
    exclude = {tuple(t) for t in exclude}
    assert tuple(owner) not in exclude, f"owner {owner} is excluded"
    free = [(x, y) for y in range(ny) for x in range(nx) if (x, y) != owner and (x, y) not in exclude]
    tiles, _, out_base = compile_group(h, w, cin, cout, k, s, p, rows, cols, 0, oy0, oy_n, act_bank, act_base,
                                       w_bank, w_base, owner, free, nx, shares, int8_out=int8_out, out_base=out_base,
                                       nchk=nchk, sparse=sparse, drain_fmt=drain_fmt, int4=int4, rbuf=rbuf)
    return LayerProgram(name, tiles, owner, out_base, oy_n * ((w + 2 * p - k) // s + 1))


def compile_pair(name, h, w, cin, cout, k, s, p, rows, cols, nx, ny, act_bank, act_base, w_bank, w_base, pair,
                 oy0=0, oy_n=None, int8_out=False, out_base=0, nchk=1, sparse=False) -> LayerProgram:
    """Dual-tile lockstep (drop 0.43): the layer whole (one share) on each tile of `pair` = (a, b), both programs fetching
    the same inputs from the same banks, each draining its M rows into its own bank at out_base -- the two tiles' drain
    messages are then identical word for word (the CRC the NIC exports covers the data, not the destination). The returned
    LayerProgram lists both tiles (a first); its out_bank is a's, b's copy is at the same address of b's bank."""
    a, b = (tuple(t) for t in pair)
    assert a != b, "a lockstep pair is two tiles"
    progs = [compile_layer(name, h, w, cin, cout, k, s, p, rows, cols, nx, ny, act_bank, act_base, w_bank, w_base, owner=t,
                           shares=1, oy0=oy0, oy_n=oy_n, int8_out=int8_out, out_base=out_base, nchk=nchk, sparse=sparse)
             for t in (a, b)]
    for lp in progs:
        assert len(lp.tiles) == 1 and lp.tiles[0].role == "solo"
    da, db = progs[0].tiles[0].descriptor, progs[1].tiles[0].descriptor
    assert da == db, "the pair's descriptors differ"
    return LayerProgram(name, progs[0].tiles + progs[1].tiles, a, out_base, progs[0].M)


CQ_CFG20 = ["cfg_h", "cfg_w", "cfg_ho", "cfg_wo", "cfg_oy0", "cfg_oy_n", "cfg_iy0", "cfg_s", "cfg_p", "cfg_k", "cfg_ct_n", "cfg_ct0",
            "cfg_ky0", "cfg_kx0", "cfg_rn", "cfg_contrib_n", "cfg_tile_pixels", "cfg_regions_m1", "cfg_flags", "cfg_rsv2"]


def cq_image(tiles, nx, extra=()):
    """The command queue's register image of tile programs (drop 0.43; top_ctrl.sv, neo_host.c's neo_cq_image): per tile,
    single-tile entries {1 << 14 | tile << 8 | register} (tile = y * nx + x) -- the twenty descriptor registers 0x10 .. 0x23
    (0 where the descriptor has no such field) and cfg_m (0x24), as neo_load_and_start writes them, then the program
    (PROG_ADDR 0x02, PROG_LO 0x03, PROG_HI 0x04 per instruction); then `extra` [(word 0, value)]; then CTRL (0x00) = 1
    broadcast to the command's mask. Returns [(word 0, value)]."""
    ent = []
    for tp in tiles:
        if tp.descriptor["cfg_m"] < 1:
            raise ValueError(f"tile {tp.tile}: cfg_m = 0 (review finding 13)")
        one = (1 << 14) | ((tp.tile[1] * nx + tp.tile[0]) << 8)
        for i, name in enumerate(CQ_CFG20):
            ent.append((one | (0x10 + i), tp.descriptor.get(name, 0) & 0xFFFF))
        ent.append((one | 0x24, tp.descriptor["cfg_m"]))
        for a, wd in enumerate(tp.program):
            ent += [(one | 0x02, a), (one | 0x03, wd & 0xFFFFFFFF), (one | 0x04, wd >> 32)]
    ent += list(extra)
    ent.append((0x00, 1))
    return ent


def compile_network(h=640, w=640, nx=8, ny=8, rows=32, cols=32, exclude=(), bank_words=na.BANK_WORDS, verbose=False):
    """Static compilation of YOLOv8-m: every (output tile, M-chunk) group of every layer gets an owner
    tile by longest-processing-time balance across the mesh (drop 0.13; was round-robin) and K-split
    shares as neo_compile.choose_split decides; every layer but the last drains INT8 for the next.
    Memory (drop 0.33): the bank allocator places the layer's input tensor (the previous layer's output
    bands, or a freshly placed tensor when the chain model's shapes do not meet -- the image, a concat),
    its weights as resident blocks round the banks, and its output tensor in bands aligned to the
    layer's output-row chunks; the input tensor is released once the layer is compiled. `exclude` lists
    tiles that take no groups and whose banks hold nothing (the tile-exclusion mask).
    Returns per-layer statistics (balance = max tile load / mean) and the allocator."""
    nc.set_v02(ksplit=True)
    layers = nc.yolov8m(h, w)
    exclude = {tuple(t) for t in exclude}
    alloc = na.BankAllocator(nx, ny, bank_words=bank_words, exclude=exclude)
    stats = []
    cur = None                                                           # the current input tensor
    weight_rr = 0
    for li, L in enumerate(layers):
        plan = nc.lower(L)
        csize = nc.chunk_size(L, plan.n_tiles)
        groups = plan.n_tiles * plan.m_chunks
        shares = nc.choose_split(groups, plan.k_tiles, csize)
        cin_tiles, _, wpa, wpw = geometry(L.cin, L.cout, L.k, rows, cols)
        runs, wpt_run = cin_tiles * L.k * L.k, rows * wpw
        int8_out = li < len(layers) - 1
        wpp_out = wpa if int8_out else cols + 1                            # words per output pixel per channel tile
        # output rows per chunk, in output-row units
        rows_per_chunk = max(1, csize // L.wo)
        # the input tensor: the previous layer's output when the shapes meet, otherwise placed afresh (external)
        external = cur is None or (cur.ct_tiles, cur.h, cur.w, cur.wpa) != (cin_tiles, L.h, L.w, wpa)
        if external:
            if cur is not None:
                alloc.release_tensor(cur)
            cur = alloc.place_tensor(cin_tiles, L.h, L.w, wpa, align_rows=1, name=f"{L.name}.in")
        # weights: one resident block per output tile, round the banks
        banks = sorted(alloc.banks, key=lambda b: (b[1] * nx + b[0]))
        wblk = []
        for nt in range(plan.n_tiles):
            wblk.append(alloc.place_weights(runs * wpt_run, prefer=banks[weight_rr % len(banks)]))
            weight_rr += 1
        # the output tensor, bands aligned to this layer's chunks
        out = alloc.place_tensor(plan.n_tiles, L.ho, L.wo, wpp_out, align_rows=rows_per_chunk, name=f"{L.name}.out")
        n_prog, fetch_w, psum, outw, progs, too_long, longest, bad_groups = 0, 0, 0, 0, 0, 0, 0, 0
        free_all = [(x, y) for y in range(ny) for x in range(nx) if (x, y) not in exclude]
        load = {t: 0 for t in free_all}
        # groups in LPT order: the heaviest first, each to the lightest tile, the band's own bank breaking ties
        # (contributors nearest the owner)
        glist = []
        for nt in range(plan.n_tiles):
            for mc in range(plan.m_chunks):
                oy0 = mc * rows_per_chunk
                oy_n = min(rows_per_chunk, L.ho - oy0)
                if oy_n > 0:
                    glist.append((nt, oy0, oy_n, plan.k_tiles * oy_n * L.wo))
        glist.sort(key=lambda g: -g[3])
        n_fetch_a, max_fetch_a = 0, 0
        for nt, oy0, oy_n, work in glist:
            region = out.region_of(nt, oy0, oy_n)
            out_addr = region.base + (oy0 - region.y0) * L.wo * wpp_out
            owner = min(free_all, key=lambda t: (load[t], 0 if t == region.bank else 1, t[1] * nx + t[0]))
            free = sorted([t for t in free_all if t != owner], key=lambda t: (load[t], t[1] * nx + t[0]))[:max(0, shares - 1) * 4 + 4]
            wb, wbase = wblk[nt]
            try:
                tiles, _, _ = compile_group(L.h, L.w, L.cin, L.cout, L.k, L.s, L.p, rows, cols, nt, oy0, oy_n,
                                            None, 0, wb, wbase, owner, free, nx, shares, int8_out=int8_out,
                                            out_base=out_addr, act=cur, out_bank=region.bank)
            except (ValueError, AssertionError) as e:
                bad_groups += 1
                if verbose:
                    print(f"  {L.name}: group nt={nt} oy0={oy0} not emitted: {e}")
                continue
            for t in tiles:
                load[t.tile] += t.descriptor["cfg_rn"] * t.descriptor["cfg_m"]
                fa = sum(1 for x in t.program if (x >> 60) & 0xF == OP_FETCH_A)
                n_fetch_a += fa; max_fetch_a = max(max_fetch_a, fa)
            n_prog += len(tiles)
            progs += sum(len(t.program) for t in tiles)
            too_long += sum(len(t.program) > PROG_DEPTH for t in tiles)
            longest = max([longest] + [len(t.program) for t in tiles])
            fetch_w += sum(t.fetch_words for t in tiles)
            psum += sum(t.psum_words for t in tiles)
            outw += sum(t.out_words for t in tiles)
        mean = sum(load.values()) / len(load)
        balance = max(load.values()) / mean if mean else 1.0
        in_bands = sum(len(r) for r in cur.regions.values())
        out_bands = sum(len(r) for r in out.regions.values())
        stats.append(dict(name=L.name, groups=groups, shares=shares, n_prog=n_prog, progs=progs, fetch_bytes=fetch_w * 4, psum_bytes=psum * 4,
                          out_bytes=outw * 4, balance=balance, too_long=too_long, longest=longest, bad=bad_groups,
                          external=external, in_bands=in_bands, in_banks=len(cur.banks()), in_mb=cur.words * 4 / 2**20,
                          out_bands=out_bands, out_banks=len(out.banks()), out_mb=out.words * 4 / 2**20,
                          w_mb=plan.n_tiles * runs * wpt_run * 4 / 2**20, max_fetch_a=max_fetch_a,
                          peak_rows=max(pk for _, pk in alloc.occupancy().values())))
        # the input is consumed: its bands go back to the pool; the output is the next layer's input
        alloc.release_tensor(cur)
        cur = out
    return stats, alloc


def report(stats, alloc, h=640, w=640, out=sys.stdout):
    tot_prog = sum(s["n_prog"] for s in stats)
    tot_words = sum(s["progs"] for s in stats)
    tot_fetch = sum(s["fetch_bytes"] for s in stats)
    tot_psum = sum(s["psum_bytes"] for s in stats)
    tot_out = sum(s["out_bytes"] for s in stats)
    print(f"YOLOv8-m class at {h}x{w} compiled for an 8x8 mesh of 32x32 cores:", file=out)
    print(f"  {len(stats)} layers, {sum(s['groups'] for s in stats)} groups, {tot_prog} tile programs, {tot_words} program words "
          f"({tot_words * 8 / 1e3:.0f} KB of descriptors), K-split shares per layer: min {min(s['shares'] for s in stats)}, max {max(s['shares'] for s in stats)}", file=out)
    print(f"  DMA traffic per frame: fetch {tot_fetch / 1e6:.0f} MB, partial sums {tot_psum / 1e6:.0f} MB, outputs {tot_out / 1e6:.0f} MB", file=out)
    too_long, longest, bad = sum(s["too_long"] for s in stats), max(s["longest"] for s in stats), sum(s["bad"] for s in stats)
    print(f"  hardware limits: {too_long} of {tot_prog} tile programs exceed the {PROG_DEPTH}-word program memory (longest {longest} words); "
          f"{bad} groups not emitted; fetches are split at the 12-bit length field, at most {max(s['max_fetch_a'] for s in stats)} FETCH_A per tile program", file=out)
    worst = max(s["balance"] for s in stats)
    mean_bal = sum(s["balance"] for s in stats) / len(stats)
    ext = sum(1 for s in stats if s["external"])
    print(f"  per-layer balance (max tile load / mean): worst {worst:.2f}, mean {mean_bal:.2f}; every layer but the last drains INT8 for the next", file=out)
    print(f"  bank allocator (drop 0.33): {alloc.summary()}; {ext} layers take their input from a freshly placed tensor "
          f"(the image and the concat/skip inputs the chain model does not carry), the rest from the previous layer's bands", file=out)
    print(f"  {'layer':<20} {'groups':>6} {'shares':>6} {'programs':>8} {'fetch MB':>9} {'psum MB':>8} {'balance':>8} "
          f"{'in bands/banks':>14} {'in MB':>6} {'out bands/banks':>15} {'out MB':>7} {'w MB':>6} {'peak rows':>9} {'bad':>4}", file=out)
    for s in stats:
        print(f"  {s['name']:<20} {s['groups']:>6} {s['shares']:>6} {s['n_prog']:>8} {s['fetch_bytes'] / 1e6:>9.2f} {s['psum_bytes'] / 1e6:>8.2f} {s['balance']:>8.2f} "
              f"{str(s['in_bands']) + '/' + str(s['in_banks']) + ('*' if s['external'] else ''):>14} {s['in_mb']:>6.1f} "
              f"{str(s['out_bands']) + '/' + str(s['out_banks']):>15} {s['out_mb']:>7.1f} {s['w_mb']:>6.1f} {s['peak_rows']:>9} {s['bad']:>4}", file=out)
    print("  (* = input placed afresh: the image, or a concat/skip input the chain model does not carry)", file=out)
    rows = alloc.bank_words // na.ROW
    occ = alloc.occupancy()
    print("  bank occupancy, peak rows of " + str(rows) + " per bank (" + str(alloc.bank_words * 4 // 2**20) + " MB), x across, y down:", file=out)
    for y in range(alloc.ny):
        print("    " + " ".join(f"{occ[(x, y)][1]:>6}" if (x, y) in occ else "   --x" for x in range(alloc.nx)), file=out)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="static compilation of YOLOv8-m on the 8x8 mesh with the bank allocator")
    ap.add_argument("--h", type=int, default=640)
    ap.add_argument("--w", type=int, default=640)
    ap.add_argument("--exclude", default="", help="tile-exclusion mask: 'x,y x,y ...' -- tiles that take no work and whose banks hold nothing")
    ap.add_argument("--check", action="store_true", help="exit nonzero unless every group is emitted and every program fits the program memory")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    exclude = [tuple(int(v) for v in t.split(",")) for t in a.exclude.split()] if a.exclude.strip() else []
    stats, alloc = compile_network(h=a.h, w=a.w, exclude=exclude, verbose=a.verbose)
    report(stats, alloc, a.h, a.w)
    bad, too_long = sum(s["bad"] for s in stats), sum(s["too_long"] for s in stats)
    if a.check:
        ok = bad == 0 and too_long == 0
        print(f"RESULT: {'ALL PLACED' if ok else f'{bad} groups not emitted, {too_long} programs too long'}")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
