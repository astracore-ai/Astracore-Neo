#!/usr/bin/env python3
"""
neo_perf_v2.py -- multi-core performance model v2 (drop 0.5).

Replaces the per-layer LPT estimate with an event-driven model of the die:
  - 8x8 cores on a mesh with 8 memory-bank nodes on each side (10x8 mesh, 16 banks of the
    shared SRAM), XY routing, LINK_BYTES per cycle per link direction, HOP_LAT cycles per hop
  - each core executes its scheduled groups in order; a group = one (output tile, M-chunk) with
    all K-tiles (the local-accumulator unit of work): fetch the activation chunk once, fetch each
    weight tile (hidden behind compute from the second tile on), compute, write the INT8 output
  - transfers occupy every link on their path and the bank for bytes / bandwidth cycles; a
    transfer starts when its source bank and all its links are free (first-order contention)
Outputs per network: latency, MAC utilization, and the busiest link and bank, versus the
compute-only schedule. Workload and lowering come from neo_compile (v0.2 microarchitecture).

Usage: python3 compiler/neo_perf_v2.py [--h 640 --w 640] [--link 64] [--banks 16]
"""
import argparse
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import neo_compile as nc  # noqa: E402

NX_CORES, NY = 8, 8
MESH_NX = NX_CORES + 2            # bank columns at x = 0 and x = MESH_NX - 1
HOP_LAT = 2
LINK_BYTES = 64                   # 512-bit links at one flit per cycle
BANK_BYTES = 64                   # bytes per cycle per bank
# the tile interface's per-flow rates as the RTL has them after drop 0.31: a fetch is received at entry rate (one 8-word
# activation entry per cycle = 32 B; weights 9 words = 36 B, modelled at 32), the drain sends 16-word beats (64 B/cycle;
# a 33-word INT32 row in three beats, drop 0.31), partial sums are taken a 16-column beat per cycle at the owner's reduce
# port (64 B/cycle, drop 0.31). A transfer takes max(bytes / link, bytes / endpoint rate) cycles on its links; the bank is
# busy bytes / BANK_BYTES. Up to drop 0.30 the drain and the partial sums moved one word per cycle (--tx 4 --psum 4); the
# pre-0.30 model assumed every endpoint absorbs a full link (set --rx/--tx/--psum to the link width to reproduce it).
RX_BYTES = 32                     # fetch receive, bytes per cycle into a core
TX_BYTES = 64                     # drain / writeback, bytes per cycle out of a core
PSUM_BYTES = 64                   # partial sums, bytes per cycle into the owner's reduce port
F_GHZ = nc.F_GHZ


def core_xy(c):
    return 1 + c % NX_CORES, c // NX_CORES


LAYOUT = "edge16"      # edge16: 8 banks per edge column; edge32: 2 banks per edge node; dist64: one bank per core node


def bank_xy(b, n_banks):
    if LAYOUT == "dist64":
        return core_xy(b % (NX_CORES * NY))
    if LAYOUT == "edge32":
        side = b % 2
        y = (b // 2) % NY
        return (0 if side == 0 else MESH_NX - 1), y        # two banks share an edge node's router
    side = b % 2
    y = (b // 2) % NY
    return (0 if side == 0 else MESH_NX - 1), y


def xy_path(src, dst):
    """Links (as (from_node, to_node)) on the XY route from src to dst."""
    (x, y), (dx, dy) = src, dst
    links = []
    while x != dx:
        nx = x + (1 if dx > x else -1)
        links.append(((x, y), (nx, y)))
        x = nx
    while y != dy:
        ny = y + (1 if dy > y else -1)
        links.append(((x, y), (x, ny)))
        y = ny
    return links


class Fabric:
    def __init__(self, n_banks, link_bytes, bank_bytes, rx_bytes=RX_BYTES, tx_bytes=TX_BYTES, psum_bytes=PSUM_BYTES):
        self.link_free = defaultdict(int)     # link -> cycle it becomes free
        self.link_busy = defaultdict(int)     # link -> total busy cycles
        self.bank_free = [0] * n_banks
        self.bank_busy = [0] * n_banks
        self.n_banks = n_banks
        self.link_bytes = link_bytes
        self.bank_bytes = bank_bytes
        self.rx_bytes, self.tx_bytes, self.psum_bytes = rx_bytes, tx_bytes, psum_bytes

    def transfer_nodes(self, t, src, dst, nbytes):
        """Core-to-core transfer (partial sums); occupies links only, at the slower of the link and the reduce port."""
        links = xy_path(src, dst)
        cycles = max(1, math.ceil(nbytes / min(self.link_bytes, self.psum_bytes)))
        start = max([t] + [self.link_free[l] for l in links])
        for l in links:
            self.link_free[l] = start + cycles
            self.link_busy[l] += cycles
        return start + cycles + HOP_LAT * len(links)

    def transfer(self, t, bank, node, nbytes, to_bank=False):
        """Move nbytes between bank and node (direction only affects the path), earliest at t.
        Returns the cycle the last byte arrives."""
        src, dst = (bank_xy(bank, self.n_banks), node) if not to_bank else (node, bank_xy(bank, self.n_banks))
        links = xy_path(src, dst)
        rate = min(self.link_bytes, self.tx_bytes if to_bank else self.rx_bytes)   # the endpoint is the per-flow limit
        cycles = max(1, math.ceil(nbytes / rate))
        bank_cycles = max(1, math.ceil(nbytes / self.bank_bytes))
        start = max([t, self.bank_free[bank]] + [self.link_free[l] for l in links])
        self.bank_free[bank] = start + bank_cycles
        self.bank_busy[bank] += bank_cycles
        for l in links:
            self.link_free[l] = start + cycles
            self.link_busy[l] += cycles
        return start + cycles + HOP_LAT * len(links)


def simulate(layers, n_banks=16, link_bytes=LINK_BYTES, bank_bytes=BANK_BYTES, verbose=False, ksplit=False):
    """Time-ordered event simulation. Per core: groups run in order; the next group's activation
    chunk and first weight tile are fetched as soon as the current group's compute starts
    (one-group-ahead prefetch, i.e. double-buffered feeder and weight buffers); the remaining
    weight tiles stream during compute; outputs write back after compute. A layer starts when
    the previous layer's last output has landed."""
    import heapq
    fab = Fabric(n_banks, link_bytes, bank_bytes)
    n_cores = NX_CORES * NY
    t_layer_start = 0
    total_macs = 0
    rows = []
    bank_rr = 0
    for L in layers:
        plan = nc.lower(L)
        total_macs += L.macs
        groups = []
        csize = nc.chunk_size(L, plan.n_tiles)
        for nt in range(plan.n_tiles):
            for mc in range(plan.m_chunks):
                m0 = mc * csize
                m1 = min(L.M, m0 + csize)
                chunk = m1 - m0
                act_bytes = int(math.ceil(L.act_in_bytes * chunk / L.M))
                w_bytes = plan.k_tiles * nc.ROWS * nc.COLS
                out_bytes = chunk * nc.COLS
                compute = plan.k_tiles * chunk + nc.LAT
                groups.append((compute, act_bytes, w_bytes, out_bytes))
        # R8: split each group's K-tiles over s cores when there are fewer groups than cores
        s = nc.choose_split(len(groups), plan.k_tiles, csize) if ksplit else 1
        if s > 1:
            split = []
            for compute, act_bytes, w_bytes, out_bytes in groups:
                chunk = (compute - nc.LAT) // plan.k_tiles
                kt_per = math.ceil(plan.k_tiles / s)
                owner_id = len(split)
                for part in range(s):
                    kt_here = min(kt_per, plan.k_tiles - part * kt_per)
                    if kt_here <= 0:
                        break
                    # (compute, act, weights, out, role): contributors send (COLS+1)*4*chunk bytes, no output
                    role = ("owner", owner_id, s, chunk) if part == 0 else ("contrib", owner_id, s, chunk)
                    split.append((kt_here * chunk + nc.LAT, int(math.ceil(act_bytes / s)),   # its own channels only
                                  kt_here * nc.ROWS * nc.COLS,
                                  out_bytes if part == 0 else (nc.COLS + 1) * 4 * chunk, role))
            groups = split
        else:
            groups = [g + (("solo", 0, 1, 0),) for g in groups]
        # static LPT assignment of (sub)groups to cores by compute cycles
        groups.sort(key=lambda g: -g[0])
        queues = [[] for _ in range(n_cores)]
        load = [0] * n_cores
        owner_core = {}
        for g in groups:
            c = min(range(n_cores), key=lambda i: load[i])
            queues[c].append(g)
            load[c] += g[0]
            if g[4][0] == "owner":
                owner_core[g[4][1]] = c
        reduce_done = {}          # owner_id -> list of times contributions landed
        # event loop
        heap = []
        compute_free = [t_layer_start] * n_cores
        layer_end = t_layer_start
        for c in range(n_cores):
            if queues[c]:
                heapq.heappush(heap, (t_layer_start, 0, c, 0))      # kind 0 = fetch group 0
        seq = 0
        pending_owner = []        # (time, core, g) owners waiting for contributions
        while heap:
            t, kind, c, g = heapq.heappop(heap)
            compute, act_bytes, w_bytes, out_bytes, role = queues[c][g]
            node = core_xy(c)
            if kind == 0:                                           # fetch act chunk + first weight tile
                b_act, b_w = bank_rr % n_banks, (bank_rr + 1) % n_banks
                bank_rr += 2
                t_act = fab.transfer(t, b_act, node, act_bytes)
                t_w0 = fab.transfer(t, b_w, node, nc.ROWS * nc.COLS)
                heapq.heappush(heap, (max(t_act, t_w0), 1, c, g))
            elif kind == 1:                                         # data ready: compute when the core is free
                ts = max(t, compute_free[c])
                b_w = bank_rr % n_banks
                bank_rr += 1
                t_wrest = fab.transfer(ts, b_w, node, max(0, w_bytes - nc.ROWS * nc.COLS))
                td = max(ts + compute, t_wrest)
                compute_free[c] = td
                heapq.heappush(heap, (td, 2, c, g))
                if g + 1 < len(queues[c]):
                    heapq.heappush(heap, (ts, 0, c, g + 1))         # prefetch the next group
            else:                                                   # compute done
                kind_, oid, s_, chunk = role
                if kind_ == "contrib":
                    # send partial sums to the owner core over the mesh
                    onode = core_xy(owner_core[oid])
                    t_arr = fab.transfer_nodes(t, node, onode, out_bytes)
                    reduce_done.setdefault(oid, []).append(t_arr)
                    continue
                if kind_ == "owner":
                    arrivals = reduce_done.get(oid, [])
                    if len(arrivals) < s_ - 1:
                        heapq.heappush(heap, (t + 64, 3, c, g))      # poll again (contributions pending)
                        continue
                    t = max([t] + arrivals) + (s_ - 1) * chunk          # reduce pass: one row per cycle
                    compute_free[c] = max(compute_free[c], t)
                b_out = bank_rr % n_banks
                bank_rr += 1
                t_out = fab.transfer(t, b_out, node, out_bytes, to_bank=True)
                layer_end = max(layer_end, t_out)
        rows.append((L.name, plan.cycles, layer_end - t_layer_start))
        t_layer_start = layer_end
    total = t_layer_start
    util = total_macs / (total * n_cores * nc.ROWS * nc.COLS)
    busiest_link = max(fab.link_busy.values()) / total if fab.link_busy else 0
    busiest_bank = max(fab.bank_busy) / total
    return total, util, busiest_link, busiest_bank, rows


def simulate_v3(layers, n_banks=64, link_bytes=LINK_BYTES, bank_bytes=BANK_BYTES, ksplit=True,
                slack=0.15):
    """Model v3 (drop 0.8): distributed banks (one per core node), placement-aware scheduling and
    dataflow overlap between layers.
      - Each group's output rows are written to its own core's bank (no link traffic).
      - A group of layer i reading output rows [m0, m1) of layer i-1 prefers the core whose bank
        holds the middle of that range; it takes it when that core's load is within `slack` of the
        least-loaded core, otherwise the least-loaded core (activation fetch over the mesh).
      - Dependencies: a group may start fetching once every producer group covering its input rows
        (plus a one-chunk halo) has written its output; layers overlap instead of a barrier.
      - K-split contributors read only their channels and send partial sums to the owner.
    """
    import heapq
    global LAYOUT
    LAYOUT = "dist64"
    fab = Fabric(n_banks, link_bytes, bank_bytes)
    n_cores = NX_CORES * NY
    total_macs = 0
    bank_rr = 0
    # ---- build groups for every layer with their row ranges ----
    all_groups = []            # dicts: layer, m0, m1, compute, act, w, out, role, deps(list of gids), owner gid
    layer_groups = []
    prev = None
    for li, L in enumerate(layers):
        plan = nc.lower(L)
        total_macs += L.macs
        csize = nc.chunk_size(L, plan.n_tiles)
        base_groups = []
        for nt in range(plan.n_tiles):
            for mc in range(plan.m_chunks):
                m0 = mc * csize
                m1 = min(L.M, m0 + csize)
                chunk = m1 - m0
                base_groups.append(dict(layer=li, m0=m0, m1=m1, nt=nt, chunk=chunk,
                                        compute=plan.k_tiles * chunk + nc.LAT,
                                        act=int(math.ceil(L.act_in_bytes * chunk / L.M)),
                                        w=plan.k_tiles * nc.ROWS * nc.COLS, out=chunk * nc.COLS))
        s = nc.choose_split(len(base_groups), plan.k_tiles, csize) if ksplit else 1
        this_layer = []
        for g in base_groups:
            kt_per = math.ceil(plan.k_tiles / s)
            owner_gid = len(all_groups)
            for part in range(s):
                kt_here = min(kt_per, plan.k_tiles - part * kt_per)
                if kt_here <= 0:
                    break
                gg = dict(g)
                gg.update(compute=kt_here * g["chunk"] + nc.LAT, act=int(math.ceil(g["act"] / s)),
                          w=kt_here * nc.ROWS * nc.COLS, role="owner" if part == 0 else "contrib",
                          owner=owner_gid, s=s, gid=len(all_groups), deps=[], core=None)
                if part > 0:
                    gg["out"] = (nc.COLS + 1) * 4 * g["chunk"]
                all_groups.append(gg)
                this_layer.append(gg)
        # dependencies on the previous layer: producer owners covering the input rows (+ halo)
        if prev is not None:
            sq = L.s * L.s
            for gg in this_layer:
                lo, hi = gg["m0"] * sq - csize, gg["m1"] * sq + csize
                gg["deps"] = [pg["gid"] for pg in prev if pg["role"] == "owner" and pg["m1"] > lo and pg["m0"] < hi]
        prev = this_layer
        layer_groups.append(this_layer)
    # ---- placement: per layer, owners first (affinity), contributors least-loaded ----
    load = [0] * n_cores
    queues = [[] for _ in range(n_cores)]
    for li, groups in enumerate(layer_groups):
        for gg in sorted(groups, key=lambda g: -g["compute"]):
            c = None
            if gg["role"] == "owner" and gg["deps"]:
                mid = (gg["m0"] + gg["m1"]) * (layers[li].s ** 2) // 2
                prod = min((all_groups[d] for d in gg["deps"]), key=lambda pg: abs((pg["m0"] + pg["m1"]) // 2 - mid))
                pc = prod["core"]
                if pc is not None and load[pc] <= (1 + slack) * min(load) + 1:
                    c = pc
            if c is None:
                c = min(range(n_cores), key=lambda i: load[i])
            gg["core"] = c
            queues[c].append(gg)
            load[c] += gg["compute"]
    # ---- event loop with dependencies ----
    done_t = {}                                   # gid -> time its output landed
    heap = []
    compute_free = [0] * n_cores
    head = [0] * n_cores                          # next group index per core queue
    started = set()
    reduce_arrivals = {}

    def try_start(c, now):
        while head[c] < len(queues[c]):
            gg = queues[c][head[c]]
            if gg["gid"] in started:
                return
            if all(d in done_t for d in gg["deps"]):
                t0 = max([now] + [done_t[d] for d in gg["deps"]])
                started.add(gg["gid"])
                heapq.heappush(heap, (t0, 0, c, head[c]))
            return

    for c in range(n_cores):
        try_start(c, 0)
    waiting = set(range(n_cores))
    while heap:
        t, kind, c, gi = heapq.heappop(heap)
        gg = queues[c][gi]
        node = core_xy(c)
        if kind == 0:                             # fetch activation chunk (from producer bank or any) + first weight tile
            if gg["deps"]:
                b_act = all_groups[gg["deps"][0]]["core"]
                mid = (gg["m0"] + gg["m1"]) * (layers[gg["layer"]].s ** 2) // 2
                b_act = min((all_groups[d] for d in gg["deps"]), key=lambda pg: abs((pg["m0"] + pg["m1"]) // 2 - mid))["core"]
            else:
                b_act = bank_rr % n_banks
                bank_rr += 1
            b_w = bank_rr % n_banks
            bank_rr += 1
            t_act = fab.transfer(t, b_act, node, gg["act"])
            t_w0 = fab.transfer(t, b_w, node, nc.ROWS * nc.COLS)
            heapq.heappush(heap, (max(t_act, t_w0), 1, c, gi))
        elif kind == 1:                           # compute when the core is free; prefetch next group
            ts = max(t, compute_free[c])
            b_w = bank_rr % n_banks
            bank_rr += 1
            t_wrest = fab.transfer(ts, b_w, node, max(0, gg["w"] - nc.ROWS * nc.COLS))
            td = max(ts + gg["compute"], t_wrest)
            compute_free[c] = td
            heapq.heappush(heap, (td, 2, c, gi))
            head[c] = gi + 1
            try_start(c, ts)
        else:                                     # compute done
            if gg["role"] == "contrib":
                onode = core_xy(all_groups[gg["owner"]]["core"])
                t_arr = fab.transfer_nodes(t, node, onode, gg["out"])
                reduce_arrivals.setdefault(gg["owner"], []).append(t_arr)
                continue
            if gg["s"] > 1:
                arr = reduce_arrivals.get(gg["gid"], [])
                if len(arr) < gg["s"] - 1:
                    heapq.heappush(heap, (t + 64, 2, c, gi))
                    continue
                t = max([t] + arr) + (gg["s"] - 1) * gg["chunk"]
                compute_free[c] = max(compute_free[c], t)
            t_out = fab.transfer(t, c, node, gg["out"], to_bank=True)      # local bank: no links
            done_t[gg["gid"]] = t_out
            for cc in range(n_cores):                                     # wake dependents
                try_start(cc, t_out)
    total = max(done_t.values())
    util = total_macs / (total * n_cores * nc.ROWS * nc.COLS)
    busiest_link = max(fab.link_busy.values()) / total if fab.link_busy else 0
    busiest_bank = max(fab.bank_busy) / total
    local = sum(1 for g in all_groups if g["role"] == "owner" and g["deps"] and
                all_groups[min(g["deps"], key=lambda d: abs((all_groups[d]["m0"] + all_groups[d]["m1"]) // 2 -
                                                           (g["m0"] + g["m1"]) * (layers[g["layer"]].s ** 2) // 2))]["core"] == g["core"])
    owners = sum(1 for g in all_groups if g["role"] == "owner" and g["deps"])
    return total, util, busiest_link, busiest_bank, local / max(1, owners)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--h", type=int, default=640)
    ap.add_argument("--w", type=int, default=640)
    ap.add_argument("--link", type=int, default=LINK_BYTES, help="bytes per cycle per link")
    ap.add_argument("--banks", type=int, default=16)
    ap.add_argument("--layout", choices=["edge16", "edge32", "dist64"], default="edge16")
    ap.add_argument("--ksplit", action="store_true", help="R8: split deep-K groups across cores")
    ap.add_argument("--v3", action="store_true", help="placement-aware scheduling + dataflow overlap (dist64)")
    ap.add_argument("--slack", type=float, default=0.15)
    a = ap.parse_args()
    if a.v3:
        nc.set_v02(ksplit=a.ksplit)
        layers = nc.yolov8m(a.h, a.w)
        compute_only = sum(nc.lower(L).cycles for L in layers)
        total, util, bl, bb, local = simulate_v3(layers, n_banks=64, link_bytes=a.link, ksplit=a.ksplit, slack=a.slack)
        print(f"YOLOv8-m class at {a.h}x{a.w}, 64 cores, 64 distributed banks, {a.link} B/cycle links, "
              f"v3: placement + dataflow overlap, K-split={'on' if a.ksplit else 'off'}, slack {a.slack}")
        print(f"  compute-only schedule (neo_compile):      {compute_only:9d} cycles = {compute_only / (F_GHZ * 1e6):.2f} ms")
        print(f"  event model v3:                            {total:9d} cycles = {total / (F_GHZ * 1e6):.2f} ms "
              f"({100 * (total / compute_only - 1):+.1f} %)")
        print(f"  MAC utilization {100 * util:.1f} %, busiest link {100 * bl:.1f} %, busiest bank {100 * bb:.1f} %, "
              f"groups placed on their producer's core {100 * local:.0f} %")
        return
    global LAYOUT
    LAYOUT = a.layout
    nc.set_v02(ksplit=a.ksplit)
    layers = nc.yolov8m(a.h, a.w)
    compute_only = sum(nc.lower(L).cycles for L in layers)
    total, util, bl, bb, rows = simulate(layers, n_banks=a.banks, link_bytes=a.link, ksplit=a.ksplit)
    print(f"YOLOv8-m class at {a.h}x{a.w}, 64 cores on a {MESH_NX}x{NY} mesh, {a.banks} banks ({a.layout}), "
          f"{a.link} B/cycle links, {HOP_LAT}-cycle hops, v0.2 microarchitecture, K-split={'on' if a.ksplit else 'off'}")
    print(f"  compute-only schedule (neo_compile):      {compute_only:9d} cycles = {compute_only / (F_GHZ * 1e6):.2f} ms")
    print(f"  event model with mesh and bank contention: {total:9d} cycles = {total / (F_GHZ * 1e6):.2f} ms "
          f"({100 * (total / compute_only - 1):+.1f} %)")
    print(f"  MAC utilization {100 * util:.1f} %, busiest link {100 * bl:.1f} % occupied, busiest bank {100 * bb:.1f} % occupied")
    worst = sorted(((dur - cyc, name, cyc, dur) for name, cyc, dur in rows), reverse=True)
    print("  layers with the largest fabric stall: " + ", ".join(f"{n} (+{d} cycles on {c})" for d, n, c, _ in worst[:3]))


if __name__ == "__main__":
    main()
