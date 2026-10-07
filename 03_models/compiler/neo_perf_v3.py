#!/usr/bin/env python3
"""
neo_perf_v3.py -- multi-core performance model v3 (drop 0.8): placement + dataflow.

Builds on neo_perf_v2 (64 tiles on a mesh, link and bank occupancy, one-group-ahead prefetch,
R8 K-split) and adds the two compiler policies the v2 results asked for:
  - placement: a group's output rows are written to its own tile's bank (zero hops); the next
    layer's group that consumes those rows prefers the producing tile (affinity, within a load
    slack) and reads the activation chunk from the producer's bank, local when it landed there
  - dataflow: a group may fetch as soon as the producer groups of its input rows have written
    their outputs (plus the usual prefetch rule), instead of waiting for the whole previous layer
Weights still come from round-robin banks. Everything else as in v2 (dist64 layout = one bank per
tile, LINK_BYTES per cycle per direction, HOP_LAT per hop).

Usage: python3 compiler/neo_perf_v3.py [--h 736 --w 1280] [--link 128] [--ksplit] [--no-affinity] [--barrier]
"""
import argparse
import heapq
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import neo_compile as nc  # noqa: E402
import neo_perf_v2 as v2  # noqa: E402

N_CORES = v2.NX_CORES * v2.NY
SLACK = 0.10            # affinity: accept the preferred tile if its load is within 10 % of the lightest


class Group:
    __slots__ = ("id", "layer", "compute", "act_bytes", "w_bytes", "out_bytes", "role", "m0", "m1",
                 "core", "deps", "dependents", "pending", "prefer", "landed", "src_bank", "band")

    def __init__(self, gid, layer, compute, act_bytes, w_bytes, out_bytes, role, m0, m1):
        self.id, self.layer, self.compute = gid, layer, compute
        self.act_bytes, self.w_bytes, self.out_bytes, self.role = act_bytes, w_bytes, out_bytes, role
        self.m0, self.m1 = m0, m1
        self.core = None
        self.deps, self.dependents, self.pending = [], [], 0
        self.prefer, self.landed, self.src_bank = None, None, None
        self.band = 0


INHERIT_CHUNKS = False   # consistent partitioning: a layer inherits its producer's M-chunk when the grid matches
BAND = False             # spatial banding: tile t owns output-row band t of every layer; groups align by construction


def build_groups(layers, ksplit):
    """Groups per layer (with R8 split), producer/consumer dependencies by output-row overlap."""
    all_groups, per_layer = [], []
    prev = None
    prev_chunk, prev_M = None, None
    for li, L in enumerate(layers):
        plan = nc.lower(L)
        csize = nc.chunk_size(L, plan.n_tiles)
        banded = BAND and L.ho >= N_CORES              # hybrid: bands only where every tile gets a whole row
        if banded:
            rows_per_band = max(1, math.ceil(L.ho / N_CORES))
            csize = rows_per_band * L.wo                # one band of whole output rows per tile
        elif INHERIT_CHUNKS and prev_M is not None:
            if L.M == prev_M:
                csize = prev_chunk                      # same grid: same row partition as the producer
            elif prev_M == 4 * L.M:
                csize = max(nc.MIN_CHUNK, prev_chunk // 4)   # stride 2: a quarter of the producer's rows
        prev_chunk, prev_M = csize, L.M
        base = []
        m_chunks = math.ceil(L.M / csize)
        for nt in range(plan.n_tiles):
            for mc in range(m_chunks):
                m0 = mc * csize
                m1 = min(L.M, m0 + csize)
                chunk = m1 - m0
                act_bytes = int(math.ceil(L.act_in_bytes * chunk / L.M))
                base.append((plan.k_tiles * chunk + nc.LAT, act_bytes, plan.k_tiles * nc.ROWS * nc.COLS,
                             chunk * nc.COLS, m0, m1, chunk))
        s = nc.choose_split(len(base), plan.k_tiles, csize) if ksplit else 1
        groups = []
        for compute, act_bytes, w_bytes, out_bytes, m0, m1, chunk in base:
            if s == 1:
                g = Group(len(all_groups), li, compute, act_bytes, w_bytes, out_bytes, ("solo", None, 1, chunk), m0, m1)
                g.band = (m0 // csize) if banded else -1
                all_groups.append(g); groups.append(g)
            else:
                kt_per = math.ceil(plan.k_tiles / s)
                owner = None
                for part in range(s):
                    kt_here = min(kt_per, plan.k_tiles - part * kt_per)
                    if kt_here <= 0:
                        break
                    role = ("owner" if part == 0 else "contrib", None, s, chunk)
                    g = Group(len(all_groups), li, kt_here * chunk + nc.LAT, int(math.ceil(act_bytes / s)),
                              kt_here * nc.ROWS * nc.COLS, out_bytes if part == 0 else (nc.COLS + 1) * 4 * chunk, role, m0, m1)
                    if part == 0:
                        owner = g
                    g.role = (role[0], owner, s, chunk)
                    g.band = (m0 // csize) if banded else -1
                    all_groups.append(g); groups.append(g)
        # dependencies: input rows [i0, i1) of this group map onto the previous layer's output rows
        if prev is not None:
            Lp, pgroups = prev
            ratio = Lp.M / L.M
            for g in groups:
                i0, i1 = int(g.m0 * ratio), int(math.ceil(g.m1 * ratio))
                for pg in pgroups:
                    if pg.role[0] == "contrib":
                        continue
                    if pg.m1 > i0 and pg.m0 < i1:
                        g.deps.append(pg)
                        pg.dependents.append(g)
                g.pending = len(g.deps)
                if g.deps:
                    # prefer the producer with the largest overlap
                    g.prefer = max(g.deps, key=lambda pg: min(pg.m1, i1) - max(pg.m0, i0))
        per_layer.append(groups)
        prev = (L, [g for g in groups])
    return all_groups, per_layer


def assign(per_layer, affinity):
    """Per layer: LPT by compute with producer affinity; contributors go next to their owner."""
    load = [0] * N_CORES
    for groups in per_layer:
        target = sum(g.compute for g in groups) / N_CORES * 1.05 + (max(load) if load else 0)   # per-layer capacity
        for g in sorted(groups, key=lambda g: -g.compute):
            if g.role[0] == "contrib" and g.role[1].core is not None:
                # nearest lightly-loaded tile to the owner
                oc = g.role[1].core
                ox, oy = v2.core_xy(oc)
                cands = sorted(range(N_CORES), key=lambda c: (load[c], abs(v2.core_xy(c)[0] - ox) + abs(v2.core_xy(c)[1] - oy)))
                c = cands[0]
                lightest = min(load)
                near = [c2 for c2 in range(N_CORES) if load[c2] <= lightest + SLACK * max(1, g.compute)]
                c = min(near, key=lambda c2: abs(v2.core_xy(c2)[0] - ox) + abs(v2.core_xy(c2)[1] - oy))
            elif BAND and g.band >= 0 and g.role[0] != "contrib":
                c = g.band % N_CORES                     # the band's tile, every banded layer
            elif affinity and g.prefer is not None and g.prefer.core is not None:
                pc = g.prefer.core
                c = pc if load[pc] + g.compute <= target else load.index(min(load))
            else:
                c = load.index(min(load))
            g.core = c
            load[c] += g.compute


def simulate(layers, link_bytes, ksplit, affinity, barrier, rx_bytes=v2.RX_BYTES, tx_bytes=v2.TX_BYTES, psum_bytes=v2.PSUM_BYTES):
    v2.LAYOUT = "dist64"
    fab = v2.Fabric(N_CORES, link_bytes, v2.BANK_BYTES, rx_bytes, tx_bytes, psum_bytes)
    local_rx = min(v2.BANK_BYTES, rx_bytes)              # a local activation read is still received at entry rate
    local_tx = min(v2.BANK_BYTES, tx_bytes)              # a local writeback still goes out one word per beat
    all_groups, per_layer = build_groups(layers, ksplit)
    assign(per_layer, affinity)
    # per-core queues in layer order
    queues = [[] for _ in range(N_CORES)]
    for groups in per_layer:
        for g in groups:
            queues[g.core].append(g)
    qpos = [0] * N_CORES
    inflight = [0] * N_CORES               # groups launched (fetching) but not yet computing: at most one
    compute_free = [0] * N_CORES
    ready_time = {}                        # group id -> time its inputs are ready
    layer_done_time = [0] * len(layers)
    layer_left = [len(gs) for gs in per_layer]
    heap = []
    seq = 0
    bank_rr = 0

    def try_launch(c, t):
        """Launch the next group on core c if its dependencies are satisfied."""
        if qpos[c] >= len(queues[c]) or inflight[c] >= 1:
            return
        g = queues[c][qpos[c]]
        if g.pending > 0 and not barrier:
            return
        if barrier and g.layer > 0 and layer_left[g.layer - 1] > 0:
            return
        t_ready = max(t, ready_time.get(g.id, 0))
        heapq.heappush(heap, (t_ready, 0, c, g.id))
        qpos[c] += 1
        inflight[c] += 1

    for c in range(N_CORES):
        try_launch(c, 0)
    gmap = {g.id: g for g in all_groups}
    reduce_arrivals = defaultdict(list)
    total_macs = sum(L.macs for L in layers)
    while heap:
        t, kind, c, gid = heapq.heappop(heap)
        g = gmap[gid]
        node = v2.core_xy(c)
        if kind == 0:                                              # fetch
            # activation chunk from the producer's bank (local if same tile), weights round-robin
            if g.prefer is not None and affinity:
                b_act = g.prefer.core
            else:
                b_act = bank_rr % N_CORES
                bank_rr += 1
            b_w = bank_rr % N_CORES
            bank_rr += 1
            t_act = fab.transfer(t, b_act, node, g.act_bytes) if b_act != c else t + max(1, math.ceil(g.act_bytes / local_rx))
            t_w0 = fab.transfer(t, b_w, node, nc.ROWS * nc.COLS)
            heapq.heappush(heap, (max(t_act, t_w0), 1, c, gid))
        elif kind == 1:                                            # ready: compute when the core is free
            ts = max(t, compute_free[c])
            b_w = bank_rr % N_CORES
            bank_rr += 1
            t_wrest = fab.transfer(ts, b_w, node, max(0, g.w_bytes - nc.ROWS * nc.COLS))
            td = max(ts + g.compute, t_wrest)
            compute_free[c] = td
            heapq.heappush(heap, (td, 2, c, gid))
            inflight[c] -= 1
            try_launch(c, ts)                                      # prefetch the next group
        else:                                                      # compute done
            kind_, owner, s_, chunk = g.role
            if kind_ == "contrib":
                onode = v2.core_xy(owner.core)
                t_arr = fab.transfer_nodes(t, node, onode, g.out_bytes)
                reduce_arrivals[owner.id].append(t_arr)
                layer_left[g.layer] -= 1
                if layer_left[g.layer] == 0:
                    layer_done_time[g.layer] = max(layer_done_time[g.layer], t_arr)
                    for c2 in range(N_CORES):
                        try_launch(c2, t_arr)
                continue
            if kind_ == "owner":
                arr = reduce_arrivals.get(g.id, [])
                if len(arr) < s_ - 1:
                    heapq.heappush(heap, (t + 32, 3, c, gid))
                    continue
                t = max([t] + arr) + (s_ - 1) * chunk
                compute_free[c] = max(compute_free[c], t)
            # write output to the local bank
            t_out = t + max(1, math.ceil(g.out_bytes / local_tx))
            g.landed = t_out
            layer_done_time[g.layer] = max(layer_done_time[g.layer], t_out)
            layer_left[g.layer] -= 1
            for d in g.dependents:
                d.pending -= 1
                ready_time[d.id] = max(ready_time.get(d.id, 0), t_out)
                if d.pending == 0:
                    try_launch(d.core, t_out)
            if layer_left[g.layer] == 0:
                for c2 in range(N_CORES):
                    try_launch(c2, t_out)
    total = max(layer_done_time)
    util = total_macs / (total * N_CORES * nc.ROWS * nc.COLS)
    busiest_link = max(fab.link_busy.values()) / total if fab.link_busy else 0
    busiest_bank = max(fab.bank_busy) / total
    local_reads = sum(1 for g in all_groups if g.prefer is not None and g.prefer.core == g.core)
    simulate.layer_done_time = layer_done_time
    return total, util, busiest_link, busiest_bank, local_reads, len(all_groups)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--h", type=int, default=736)
    ap.add_argument("--w", type=int, default=1280)
    ap.add_argument("--link", type=int, default=128)
    ap.add_argument("--ksplit", action="store_true")
    ap.add_argument("--no-affinity", action="store_true")
    ap.add_argument("--barrier", action="store_true", help="layer barriers instead of dataflow")
    ap.add_argument("--inherit", action="store_true", help="consistent chunk partitioning across layers")
    ap.add_argument("--band", action="store_true", help="spatial banding: tile t owns row band t at every layer")
    ap.add_argument("--rx", type=int, default=v2.RX_BYTES, help="fetch receive rate, bytes per cycle into a core (RTL after drop 0.30: 32)")
    ap.add_argument("--tx", type=int, default=v2.TX_BYTES, help="drain rate, bytes per cycle out of a core (RTL since drop 0.31: 64, 16-word beats; 4 before)")
    ap.add_argument("--psum", type=int, default=v2.PSUM_BYTES, help="partial-sum receive rate, bytes per cycle (RTL since drop 0.31: 64; 4 before)")
    a = ap.parse_args()
    global INHERIT_CHUNKS, BAND
    INHERIT_CHUNKS = a.inherit
    BAND = a.band
    nc.set_v02(ksplit=a.ksplit)
    layers = nc.yolov8m(a.h, a.w)
    compute_only = sum(nc.lower(L).cycles for L in layers)
    total, util, bl, bb, local, ng = simulate(layers, a.link, a.ksplit, not a.no_affinity, a.barrier, a.rx, a.tx, a.psum)
    print(f"YOLOv8-m class at {a.h}x{a.w}, 64 tiles, one bank per tile, {a.link} B/cycle links, "
          f"K-split={'on' if a.ksplit else 'off'}, affinity={'off' if a.no_affinity else 'on'}, "
          f"{'layer barriers' if a.barrier else 'dataflow'}, chunks={'bands' if a.band else ('inherited' if a.inherit else 'per layer')}, "
          f"endpoint rates rx {a.rx} / tx {a.tx} / psum {a.psum} B/cycle (bank {v2.BANK_BYTES})")
    print(f"  compute-only schedule:  {compute_only:9d} cycles = {compute_only / (nc.F_GHZ * 1e6):.2f} ms")
    print(f"  event model v3:         {total:9d} cycles = {total / (nc.F_GHZ * 1e6):.2f} ms ({100 * (total / compute_only - 1):+.0f} %)")
    print(f"  MAC utilization {100 * util:.1f} %, busiest link {100 * bl:.1f} %, busiest bank {100 * bb:.1f} %, "
          f"activation reads served locally {local}/{ng} groups")


if __name__ == "__main__":
    main()
