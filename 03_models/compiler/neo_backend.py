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


def geometry(cin, cout, k, rows, cols):
    cin_tiles, cout_tiles = math.ceil(cin / rows), math.ceil(cout / cols)
    wpa = rows * 8 // 32
    wcw = 8 + (0 if rows <= 1 else (rows - 1).bit_length()) + 1
    wpw = (cols * 8 + wcw + 31) // 32
    return cin_tiles, cout_tiles, wpa, wpw


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
                  owner, free_tiles, nx, shares, int8_out=False, out_base=0, act=None, out_bank=None):
    """One (output tile nt, output-row range) group split into `shares` contiguous run ranges.
    `act`: the input tensor's regions (neo_alloc.Tensor); when None, channel tiles whole at act_bank/act_base.
    `out_bank`/`out_base`: where the group's M output pixels are drained (the owner's bank when out_bank is None)."""
    ho, wo = (h + 2 * p - k) // s + 1, (w + 2 * p - k) // s + 1
    cin_tiles, _, wpa, wpw = geometry(cin, cout, k, rows, cols)
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
        # activations: only rows iy_lo..iy_hi of each channel tile -- one FETCH_A per band of the tensor the window
        # overlaps (rows contiguous inside a band, row-major, wpa words per pixel)
        prog = []
        for ct in range(ct_lo, ct_hi + 1):
            for bank, addr, ya, yb in act.segments(ct, iy_lo, iy_hi):
                prog += fetch(OP_FETCH_A, bank, addr, (yb - ya + 1) * w * wpa, ct * region_pixels + (ya - iy_lo) * w, wpa)
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
    owner_tp.program += [ins(OP_DRAIN_WR, out_bank[0], out_bank[1], out_base, M, 0, 1 if int8_out else 0), ins(OP_GO)]
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
