#!/usr/bin/env python3
"""
neo_backend.py -- compiler backend (drop 0.10): from a layer to what the tiles execute.

For one layer and one output tile x M-chunk group, the backend
  - splits the group's runs (ct, ky, kx) into S contiguous shares (R8) and places them on tiles:
    the owner on a chosen tile, contributors on its nearest free tiles;
  - lays out memory: activations and weights at a source bank (where the previous layer or the
    host put them), the result in the owner's own bank (dataflow placement, local writeback);
  - emits each tile's descriptor (cfg_* registers) and DMA program (FETCH_A per channel tile of
    its share, FETCH_W for its runs, DRAIN_WR/PSUM, GO, WAIT_REDUCE + NOTIFY on the owner,
    WAIT_RDY on contributors, WAIT_DONE, END), using exactly the protocol tile_dma executes.
compile_layer() returns a LayerProgram; run_tiles.py's M6 executes one on the 2x2 RTL mesh.
compile_network() runs the emitter over every layer of YOLOv8-m on the 8x8 mesh and reports
program and traffic statistics (static; execution of the whole network is beyond neosim's speed).
"""
import math
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import neo_compile as nc  # noqa: E402

OP_FETCH_A, OP_FETCH_W, OP_DRAIN_WR, OP_DRAIN_PSUM, OP_GO, OP_WAIT_FREE, OP_WAIT_DONE, OP_END = 1, 2, 3, 4, 5, 6, 7, 8
OP_NOTIFY, OP_WAIT_RDY, OP_WAIT_REDUCE = 9, 10, 11


MAX_FETCH = 4095        # the DMA instruction's length field is 12 bits (drop 0.21: a fetch longer than that is split)
PROG_DEPTH = 16         # program memory per tile (prog_mem DEPTH)


def ins(op, x=0, y=0, addr=0, length=0, base=0, arg=0):
    """64-bit DMA instruction: op[63:60] x[59:56] y[55:52] addr[51:32] len[31:20] base[19:4] arg[3:0].
    A field that does not fit is an error, never a silent overflow into the neighbouring field (drop 0.21)."""
    if not (0 <= op < 16 and 0 <= x < 16 and 0 <= y < 16 and 0 <= addr < (1 << 20) and 0 <= length <= MAX_FETCH
            and 0 <= base < (1 << 16) and 0 <= arg < 16):
        raise ValueError(f"DMA instruction field out of range: op {op} x {x} y {y} addr {addr} len {length} base {base} arg {arg}")
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


def geometry(cin, cout, k, rows, cols):
    cin_tiles, cout_tiles = math.ceil(cin / rows), math.ceil(cout / cols)
    wpa = rows * 8 // 32
    wcw = 8 + (0 if rows <= 1 else (rows - 1).bit_length()) + 1
    wpw = (cols * 8 + wcw + 31) // 32
    return cin_tiles, cout_tiles, wpa, wpw


def nearest_free(owner, free, nx):
    ox, oy = owner
    return min(free, key=lambda t: (abs(t[0] - ox) + abs(t[1] - oy), t[1] * nx + t[0]))


def compile_group(h, w, cin, cout, k, s, p, rows, cols, nt, oy0, oy_n, act_bank, act_base, w_bank, w_base,
                  owner, free_tiles, nx, shares, int8_out=False, out_base=0):
    """One (output tile nt, output-row range) group split into `shares` contiguous run ranges."""
    ho, wo = (h + 2 * p - k) // s + 1, (w + 2 * p - k) // s + 1
    cin_tiles, _, wpa, wpw = geometry(cin, cout, k, rows, cols)
    apt, wpt_run = h * w * wpa, rows * wpw            # words per activation tile, per run's weight tile
    # input rows this output-row range touches: [iy_lo, iy_hi], clipped to the tile
    iy_lo = max(0, oy0 * s - p)
    iy_hi = min(h - 1, (oy0 + oy_n - 1) * s + k - 1 - p)
    nrows = iy_hi - iy_lo + 1
    region_pixels = nrows * w                         # region holds only the rows needed
    runs = cin_tiles * k * k
    shares = max(1, min(shares, runs, 1 + len(free_tiles)))
    bounds = [(i * runs) // shares for i in range(shares + 1)]
    M = oy_n * wo
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
        # activations: only rows iy_lo..iy_hi of each channel tile, contiguous in the row-major source layout
        prog = []
        for ct in range(ct_lo, ct_hi + 1):
            prog += fetch(OP_FETCH_A, act_bank, act_base + ct * apt + iy_lo * w * wpa, region_pixels * wpa, ct * region_pixels, wpa)
        prog += fetch(OP_FETCH_W, w_bank, w_base + (nt * runs + r0) * wpt_run, (r1 - r0) * wpt_run, 0, wpw)
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
    owner_tp.program += [ins(OP_DRAIN_WR, owner[0], owner[1], out_base, M, 0, 1 if int8_out else 0), ins(OP_GO)]
    if shares > 1:
        owner_tp.program.append(ins(OP_WAIT_REDUCE))
        owner_tp.program += [ins(OP_NOTIFY, t.tile[0], t.tile[1]) for t in tiles[1:]]
    owner_tp.program += [ins(OP_WAIT_DONE), ins(OP_END)]
    return tiles, free, out_base


def compile_layer(name, h, w, cin, cout, k, s, p, rows, cols, nx, ny, act_bank, act_base, w_bank, w_base,
                  owner=(0, 0), shares=1, oy0=0, oy_n=None, int8_out=False, out_base=0) -> LayerProgram:
    """Single group (one output tile, one output-row range) executable on the test mesh."""
    ho = (h + 2 * p - k) // s + 1
    oy_n = ho if oy_n is None else oy_n
    free = [(x, y) for y in range(ny) for x in range(nx) if (x, y) != owner]
    tiles, _, out_base = compile_group(h, w, cin, cout, k, s, p, rows, cols, 0, oy0, oy_n, act_bank, act_base,
                                       w_bank, w_base, owner, free, nx, shares, int8_out=int8_out, out_base=out_base)
    return LayerProgram(name, tiles, owner, out_base, oy_n * ((w + 2 * p - k) // s + 1))


def compile_network(h=640, w=640, nx=8, ny=8, rows=32, cols=32):
    """Static compilation of YOLOv8-m: every (output tile, M-chunk) group of every layer gets an owner
    tile by longest-processing-time balance across the mesh (drop 0.13; was round-robin) and K-split
    shares as neo_compile.choose_split decides; every layer but the last drains INT8 for the next.
    Returns per-layer statistics including the balance (max tile load / mean)."""
    nc.set_v02(ksplit=True)
    layers = nc.yolov8m(h, w)
    stats = []
    bank_rr = 0
    for li, L in enumerate(layers):
        plan = nc.lower(L)
        csize = nc.chunk_size(L, plan.n_tiles)
        groups = plan.n_tiles * plan.m_chunks
        shares = nc.choose_split(groups, plan.k_tiles, csize)
        # output rows per chunk, in output-row units
        rows_per_chunk = max(1, csize // L.wo)
        n_prog, fetch_w, psum, out, progs, too_long, longest, bad_groups = 0, 0, 0, 0, 0, 0, 0, 0
        free_all = [(x, y) for y in range(ny) for x in range(nx)]
        load = {t: 0 for t in free_all}
        # groups in LPT order: the heaviest first, each to the lightest tile (contributors nearest the owner)
        glist = []
        for nt in range(plan.n_tiles):
            for mc in range(plan.m_chunks):
                oy0 = mc * rows_per_chunk
                oy_n = min(rows_per_chunk, L.ho - oy0)
                if oy_n > 0:
                    glist.append((nt, oy0, oy_n, plan.k_tiles * oy_n * L.wo))
        glist.sort(key=lambda g: -g[3])
        for nt, oy0, oy_n, work in glist:
            owner = min(free_all, key=lambda t: (load[t], t[1] * nx + t[0]))
            free = sorted([t for t in free_all if t != owner], key=lambda t: (load[t], t[1] * nx + t[0]))[:max(0, shares - 1) * 4 + 4]
            act_bank = free_all[bank_rr % len(free_all)]
            w_bank = free_all[(bank_rr + 1) % len(free_all)]
            bank_rr += 2
            try:
                tiles, _, _ = compile_group(L.h, L.w, L.cin, L.cout, L.k, L.s, L.p, rows, cols, nt, oy0, oy_n,
                                            act_bank, 0, w_bank, 0, owner, free, nx, shares, int8_out=(li < len(layers) - 1))
            except ValueError:
                # a bank address beyond the 20-bit field: the static compile places a whole channel tile at ct * h * w * wpa
                # in one bank, which the early 640x640 layers exceed; a bank allocator (regions per row window) is needed
                bad_groups += 1
                continue
            for t in tiles:
                load[t.tile] += t.descriptor["cfg_rn"] * t.descriptor["cfg_m"]
            n_prog += len(tiles)
            progs += sum(len(t.program) for t in tiles)
            too_long += sum(len(t.program) > PROG_DEPTH for t in tiles)
            longest = max([longest] + [len(t.program) for t in tiles])
            fetch_w += sum(t.fetch_words for t in tiles)
            psum += sum(t.psum_words for t in tiles)
            out += sum(t.out_words for t in tiles)
        mean = sum(load.values()) / len(load)
        balance = max(load.values()) / mean if mean else 1.0
        stats.append((L.name, groups, shares, n_prog, progs, fetch_w * 4, psum * 4, out * 4, balance, too_long, longest, bad_groups))
    return stats


def main():
    stats = compile_network()
    tot_prog = sum(s[3] for s in stats)
    tot_words = sum(s[4] for s in stats)
    tot_fetch = sum(s[5] for s in stats)
    tot_psum = sum(s[6] for s in stats)
    tot_out = sum(s[7] for s in stats)
    print(f"YOLOv8-m class at 640x640 compiled for an 8x8 mesh of 32x32 cores:")
    print(f"  {len(stats)} layers, {sum(s[1] for s in stats)} groups, {tot_prog} tile programs, {tot_words} program words "
          f"({tot_words * 8 / 1e3:.0f} KB of descriptors), K-split shares per layer: min {min(s[2] for s in stats)}, max {max(s[2] for s in stats)}")
    print(f"  DMA traffic per frame: fetch {tot_fetch / 1e6:.0f} MB, partial sums {tot_psum / 1e6:.0f} MB, outputs {tot_out / 1e6:.0f} MB")
    too_long, longest, bad = sum(s[9] for s in stats), max(s[10] for s in stats), sum(s[11] for s in stats)
    print(f"  hardware limits (drop 0.21): {too_long} of {tot_prog} tile programs exceed the {PROG_DEPTH}-word program memory (longest {longest} words); "
          f"{bad} groups in {sum(1 for s in stats if s[11])} layers not emitted because a channel tile placed whole exceeds the 20-bit bank address "
          f"(the static compile has no bank allocator); fetches are split at the 12-bit length field")
    worst = max(s[8] for s in stats)
    print(f"  per-layer balance (max tile load / mean): worst {worst:.2f}, mean {sum(s[8] for s in stats) / len(stats):.2f}; "
          f"every layer but the last drains INT8 for the next")
    print(f"  {'layer':20s} {'groups':>6s} {'shares':>6s} {'programs':>8s} {'fetch MB':>9s} {'psum MB':>8s} {'balance':>8s}")
    for s in stats[:6] + stats[-3:]:
        print(f"  {s[0]:20s} {s[1]:6d} {s[2]:6d} {s[3]:8d} {s[5] / 1e6:9.2f} {s[6] / 1e6:8.2f} {s[8]:8.2f}")


if __name__ == "__main__":
    main()
