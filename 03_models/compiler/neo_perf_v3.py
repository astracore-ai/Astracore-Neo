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
Drop 0.44 (stream D3, the virtual device): --calibrate perf_measured_044.json prints the error table of the model against
the RTL's measured cycles (the conv step model of the runtime's schedule on the chip top fitted on T10, tile_layer on M25,
C2's phase model on M21 / M23 / M24); --predict the YOLOv8-m group's predicted layer times and utilization; --collect
builds the measured file from the test runs.
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
WFACTOR = 1.0            # drop 0.42: weight bytes per weight, 1.25 on the NCHK = 4 core (an 8-bit weight and its 2-bit index in
                         # every entry, dense layers included: one entry format) -- set by --sparse, which needs that core;
                         # drop 0.43: x 0.5 with --int4 (the bank holds 4-bit weights, the NIC expands them at the fetch)
VEC_ROW = 0              # drop 0.43: the vector unit's cycles per output row after the sequencer's drain (an owner's or a solo
                         # group's epilogue; 0 = the bypass) -- --vec-silu: every layer's SiLU exact on the unit (neo_vec's
                         # prog_activation: 10 cycles a row on the pipelined unit) instead of the requantizer's ReLU


def build_groups(layers, ksplit):
    """Groups per layer (with R8 split), producer/consumer dependencies by output-row overlap."""
    all_groups, per_layer = [], []
    prev = None
    prev_chunk, prev_M = None, None
    for li, L in enumerate(layers):
        plan = nc.lower(L)                             # a 2:4 layer: half the K-passes (drop 0.42)
        wpt = nc.ROWS * nc.COLS * WFACTOR              # weight bytes per K-pass (10-bit entries on the NCHK = 4 core)
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
                base.append((plan.k_tiles * chunk + nc.LAT + VEC_ROW * chunk, act_bytes, int(plan.k_tiles * wpt),
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
                    g = Group(len(all_groups), li, kt_here * chunk + nc.LAT + (VEC_ROW * chunk if part == 0 else 0),
                              int(math.ceil(act_bytes / s)),
                              int(kt_here * wpt), out_bytes if part == 0 else (nc.COLS + 1) * 4 * chunk, role, m0, m1)
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


def tile_layer(h, w, cin, cout, k, s, p, rows=None, cols=None, nchk=1, rx_bytes=v2.RX_BYTES, tx_bytes=v2.TX_BYTES, hops=2,
               run_spacing=False):
    """Drop 0.43 (stream B2): one layer on one tile, its activations and weights fetched from one remote bank, the INT32
    result drained to another -- the model's terms for a single group, to set beside the RTL's telemetry (the tile suite's
    M23 measures array-valid cycles and the window's cycles of exactly this). The model's group: the fetches at rx_bytes per
    cycle before the compute (no prefetch for a lone group), k_tiles x M cycles of streaming plus the pipeline latency LAT,
    the drain at tx_bytes per cycle, hops x HOP_LAT each way. MAC utilization = streaming cycles / all cycles: the array
    is valid exactly k_tiles x M cycles (one row per cycle per K-pass), which the counter checks. rx_bytes / tx_bytes are
    the link's bytes per cycle (4 x WPF of the build: 4 on the 16x8 tile suite's one-word flits, 128 in silicon).
    run_spacing (the term the M23 measurement asked for): the sequencer starts a K-pass at most every LAT + ROWS + 4 cycles
    -- the next pass's weights shadow-load ROWS rows while the array drains the previous pass's LAT -- so a pass costs
    max(M, LAT + ROWS + 4) cycles, not M: at the small M of a 6 x 6 test layer (36 or 9 rows against 60 at 16 x 8) the
    passes, not the rows, set the compute time. What both forms still leave out: the DMA's per-fetch round trip (one
    fetch outstanding) and the register writes before the start."""
    rows = rows or nc.ROWS
    cols = cols or nc.COLS
    ho, wo = (h + 2 * p - k) // s + 1, (w + 2 * p - k) // s + 1
    M = ho * wo
    ct_n = math.ceil(cin / rows)
    k_tiles = ct_n * k * k                                   # the runs: one K-pass of ROWS channels each
    lat = nc.PE_LAT * rows + cols
    run = max(M, lat + rows + 4) if run_spacing else M      # the cycles of one K-pass
    wcw = 8 + (rows - 1).bit_length() + 1
    wpw = (cols * (8 + (2 if nchk > 1 else 0)) + nchk * wcw + 31) // 32   # words per weight entry, as the RTL stores them
    act_bytes = ct_n * h * w * rows                          # INT8 activations, a channel tile per entry
    w_bytes = k_tiles * rows * wpw * 4
    fetch = math.ceil((act_bytes + w_bytes) / rx_bytes)
    compute = k_tiles * run + lat
    drain = math.ceil(M * (cols + 1) * 4 / tx_bytes)
    cycles = fetch + compute + drain + 2 * hops * v2.HOP_LAT
    return dict(M=M, k_tiles=k_tiles, fetch=fetch, compute=compute, drain=drain, cycles=cycles,
                streaming=k_tiles * M, utilization=k_tiles * M / cycles)


# ---------------------------------------------------------------------------------------------------------------------
# drop 0.44 (stream D3): the virtual device -- the model calibrated against the RTL's measured cycles
# ---------------------------------------------------------------------------------------------------------------------
# A convolution step of the runtime's schedule (neo_sched: a set of tile programs started together by one command, the
# tensors behind the memory bridge) on the chip top: per tile the instructions, every fetch as a request to the bridge
# (its round trip and its words at the bridge's rate), every run as the sequencer's K-passes at the pass spacing (LAT +
# ROWS + 4 cycles a pass at least: the feeder stall the telemetry counts), the vector unit's rows, the drained words; a
# contributor's partial sums and the owner's reduce; the step's time the slowest tile plus the bridge's serving of the
# other tiles' fetches (one request at a time). CONV_PM's measured terms (fitted on T10's steps, neo_perf_v3.py --calibrate)
# and C2's PERF for the steps served from banks (M21 / M23 / M24) are the two forms of the same model.
CONV_PM = dict(host=200,          # the host's part of a measured window: the start's register plays and the first snapshot's reads
               instr=21,          # as PERF
               fetch_req=19,      # a fetch's round trip through the bridge beyond its words (RDREQ, AR, the first R beat, the CRC flit)
               bridge_wpc=7.6,    # words per clk the bridge serves (an AXI beat of 16 words every ~2 clk; T3b measured 12.6 per aclk
                                  # = 7.9 per clk at the suite's 1.6 / 1.0 ns clocks on four 4,092-word fetches)
               run_fill=4,        # as PERF
               drain_word=0.8,    # as PERF
               reduce_row=2.0)    # a contributor's partial-sum row into the owner (the PSUM beats and the accumulate)
# fitted on T10's 27 steps (perf_measured_044.json): mean |error| 2.8 % over the 34 measured points, 31 within 10 %; the
# first layer (one channel tile, M = 36 rows under the 60-cycle pass spacing) is predicted 14-18 % high -- the model takes
# a pass at max(M, spacing) while the sequencer overlaps part of the spacing with the previous pass's rows


def _sw_path():
    """sw/neo_run.py: 04_software in the repository, sw/ in the flat layout (the real path: a symlinked compiler/ resolves
    '..' to 03_models)."""
    real = os.path.dirname(os.path.realpath(__file__))
    for d in (os.path.join(real, "..", "..", "04_software"), os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sw")):
        if os.path.isdir(d) and d not in sys.path:
            sys.path.insert(0, d)


def conv_tile_terms(tile, geo):
    """The model's terms of one tile program of a step (neo_sched.TileStep): instructions, fetched words per request, the
    runs x rows, the pass spacing, the vector unit's cycles, drained words, partial sums."""
    import neo_vec as nv
    from neo_backend import OP_FETCH_A, OP_FETCH_W, OP_DRAIN_WR, OP_DRAIN_PSUM, OP_GO
    rows, cols = geo.rows, geo.cols
    lat = nc.PE_LAT * rows + cols
    fetches, drains, gos = [], 0, 0
    for w in tile.prog:
        op, ln, arg = (w >> 60) & 15, (w >> 20) & 0xFFF, w & 15
        if op in (OP_FETCH_A, OP_FETCH_W):
            fetches.append(ln)
        elif op == OP_DRAIN_WR:
            drains = ln * (max(geo.wpa, geo.ni8) if (w >> 4) & 0x2000 else geo.ni8) if arg & 1 else ln * (cols + 1)
        elif op == OP_DRAIN_PSUM:
            drains = ln * (cols + 1)
        elif op == OP_GO:
            gos += 1
    runs, M = tile.cfg[14], tile.cfg_m
    contrib_n = tile.cfg[15]
    prog = [v for r, v in tile.img if (r == 0x39)]                          # the vector program's words (after VEC_ADDR 0x000)
    vec_rows = 0
    if tile.role in ("owner", "solo"):
        # the program's words: the VEC_DATA writes that follow a VEC_ADDR of table 0
        pw, cur = [], None
        for r, v in tile.img:
            if r == 0x38:
                cur = v
            elif r == 0x39 and cur is not None:
                if (cur >> 8) == 0:
                    pw.append(v)
                cur += 1
        if pw and (pw[0] & 15) != 0:
            vec_rows = M * nv.cycles_per_row(pw, cols)
    return dict(instr=len(tile.prog), fetches=fetches, runs=runs, M=M, lat=lat, gos=gos, drain_words=drains, vec=vec_rows,
                contrib_n=contrib_n, role=tile.role)


def conv_tile_cycles(t, geo, pm=CONV_PM):
    """One tile program's cycles on its own (t: conv_tile_terms)."""
    c = pm["instr"] * t["instr"]
    c += sum(pm["fetch_req"] + w / pm["bridge_wpc"] for w in t["fetches"])
    spacing = t["lat"] + geo.rows + 4
    c += t["gos"] * (pm["run_fill"] + t["runs"] * max(t["M"], spacing) + t["lat"] + max(t["M"], t["vec"]))
    c += t["drain_words"] * pm["drain_word"]
    if t["role"] == "owner":
        c += t["contrib_n"] * t["M"] * pm["reduce_row"]
    return c


def conv_step_cycles(tiles, geo, pm=CONV_PM):
    """A step's predicted window: the slowest tile, plus the bridge serving the other tiles' fetch words (one request at a
    time), plus the host's part of the window."""
    terms = [conv_tile_terms(t, geo) for t in tiles]
    own = [conv_tile_cycles(t, geo, pm) for t in terms]
    words = [sum(t["fetches"]) for t in terms]
    return pm["host"] + max(own) + (sum(words) - max(words)) / pm["bridge_wpc"], terms


def predict_image(img, pm=CONV_PM):
    """Per step and per layer of a neo_sched.NetImage: the predicted cycles and MAC utilization (array-valid rows over
    the window: a tile streams runs x M rows per GO)."""
    out = []
    for st in img.steps:
        cyc, terms = conv_step_cycles(st.tiles, img.geo, pm)
        valid = sum(t["runs"] * t["M"] * t["gos"] for t in terms)
        out.append(dict(name=st.name, layer=st.layer, cycles=cyc, valid=valid, tiles=len(st.tiles)))
    return out


def collect(t10_json, tile_log, out):
    """Build the measured-cycles file from the top suite's measured_t10.json and a tile-suite run's log (M21 / M23 / M24 /
    M25 lines)."""
    import json
    import re
    t10 = json.load(open(t10_json))
    log = open(tile_log).read()
    meas = dict(sources=dict(t10="make -j1 -C dv/cocotb top TESTCASE=t10_runtime_yolov8m_crop -> dv/cocotb/measured_t10.json",
                             tile="make -j1 -C dv/cocotb tile TESTCASE=m21_attention_head,m23_vit_block,m24_llm_layer,m25_telemetry",
                             bridge="top T3b: 4 x 4092-word fetches at once, 50.3 bytes per aclk cycle of 64 (drop 0.43)"),
                t10=dict(build=t10["build"], steps=[dict(name=s["name"], cycles=min(v["cycles"] for v in s["tel"].values()), window_max=s["cycles"],
                                                         valid=s["array_valid"], tiles=s["tiles"]) for s in t10["steps"]]),
                phases={}, layers=[])
    m = re.search(r"M21:.*?cycles (.*?); (\d+) in all", log)
    if m:
        meas["phases"]["M21"] = [(n.strip(), int(c)) for n, c in re.findall(r"([\w\- ]+?) (\d+) \(model \d+\)", m.group(1))]
    for tag in ("M23", "M24"):
        m = re.search(tag + r":.*?clocks; (.*?)$", log, re.M)
        if m:
            meas["phases"][tag] = [(n.strip(), int(c)) for n, c in re.findall(r"([\w\-+ ]+?) (\d+)/\d+", m.group(1))]
    for k, s_, ct, M, av, cy in re.findall(r"k(\d) s(\d) (\d) ch\.tiles M (\d+): (\d+) valid / (\d+) cycles", log):
        meas["layers"].append(dict(k=int(k), s=int(s_), ct_n=int(ct), M=int(M), valid=int(av), cycles=int(cy)))
    json.dump(meas, open(out, "w"), indent=1)
    print(f"collected {len(meas['t10']['steps'])} T10 steps, phases {{{', '.join(f'{k}: {len(v)}' for k, v in meas['phases'].items())}}}, "
          f"{len(meas['layers'])} M25 layers -> {out}")


def fit_conv(meas_steps, img):
    """Fit host, fetch_req and bridge_wpc (the chip top's terms; the array's, the unit's and C2's constants fixed) on
    the measured steps by a coarse-to-fine search minimising the mean |error|."""
    steps = {st.name: st for st in img.steps}
    pts = [(steps[s["name"]], s["cycles"]) for s in meas_steps if s["name"] in steps]

    def err(pm):
        e = 0.0
        for st, cyc in pts:
            p, _ = conv_step_cycles(st.tiles, img.geo, pm)
            e += abs(p - cyc) / cyc
        return e / len(pts)
    best = dict(CONV_PM)
    best_e = err(best)
    for scale in (1.0, 0.5, 0.25, 0.1):
        improved = True
        while improved:
            improved = False
            for key, step in (("host", 40 * scale), ("fetch_req", 20 * scale), ("bridge_wpc", 1.0 * scale)):
                for sign in (1, -1):
                    cand = dict(best)
                    cand[key] = max(0.2 if key == "bridge_wpc" else 0.0, cand[key] + sign * step)
                    e = err(cand)
                    if e < best_e - 1e-6:
                        best, best_e, improved = cand, e, True
    return best, best_e


def calibrate(path, fit=True):
    """The error table of the model against the measured file: T10's steps (the conv step model, the three bridge / host
    terms fitted on them when `fit`), M25's single-tile layers (tile_layer with the pass spacing and the build's link),
    M21 / M23 / M24's phases (neo_xfmr's phase_prediction on the plans' mesh-model runs)."""
    import json
    _sw_path()
    import neo_run
    import neo_xfmr as nx
    meas = json.load(open(path))
    rows = []
    # ---- T10: the conv steps on the chip top
    img = neo_run.build_yolo()
    pm = dict(CONV_PM)
    if fit:
        pm, _ = fit_conv(meas["t10"]["steps"], img)
    steps = {st.name: st for st in img.steps}
    print(f"T10 (the YOLOv8-m group on the chip top, {meas['t10']['build']}): the conv step model with "
          + ", ".join(f"{k} {v:g}" for k, v in pm.items()) + (" (host, fetch_req, bridge_wpc fitted on these steps)" if fit else ""))
    print(f"  {'step':<20} {'tiles':>5} {'measured':>9} {'model':>9} {'error':>7}   {'valid rows':>10} {'util meas':>9} {'util model':>10}")
    tot_m = tot_p = 0
    seen = set()
    for s in meas["t10"]["steps"]:
        if s["name"] not in steps:
            continue
        p, terms = conv_step_cycles(steps[s["name"]].tiles, img.geo, pm)
        key = (s["name"].split("#")[0], s["tiles"], s["cycles"])
        tot_m += s["cycles"]; tot_p += p
        rows.append(("T10 " + s["name"], s["cycles"], p))
        if key in seen:
            continue
        seen.add(key)
        nt_ = s["tiles"]
        um, up = s["valid"] / (nt_ * s["cycles"]), s["valid"] / (nt_ * p)
        print(f"  {s['name']:<20} {nt_:>5} {s['cycles']:>9} {p:>9.0f} {100 * (p / s['cycles'] - 1):>+6.1f} %  {s['valid']:>10} {100 * um:>8.1f} % {100 * up:>9.1f} %")
    print(f"  {'total (27 steps)':<20} {'':>5} {tot_m:>9} {tot_p:>9.0f} {100 * (tot_p / tot_m - 1):>+6.1f} %   (identical steps listed once)")
    # ---- M25: single-tile layers from a bank (the tile suite's build: one-word flits)
    print("M25 (single-tile layers from a bank, the tile suite's 2x2 of 16x8 at WPF 1): tile_layer with the pass spacing and the build's link")
    print(f"  {'layer':<22} {'measured':>9} {'model':>9} {'error':>7}   {'util meas':>9} {'util model':>10}")
    for L in meas["layers"]:
        p = tile_layer(6, 6, L["ct_n"] * 16, 7, L["k"], L["s"], L["k"] // 2, rows=16, cols=8, nchk=1, rx_bytes=4, tx_bytes=4, run_spacing=True)
        rows.append((f"M25 k{L['k']} s{L['s']} ct{L['ct_n']}", L["cycles"], p["cycles"]))
        print(f"  {'k%d s%d %d ch.tiles M %d' % (L['k'], L['s'], L['ct_n'], L['M']):<22} {L['cycles']:>9} {p['cycles']:>9.0f} {100 * (p['cycles'] / L['cycles'] - 1):>+6.1f} %  "
              f"{100 * L['valid'] / L['cycles']:>8.1f} % {100 * p['utilization']:>9.1f} %")
    # ---- M21 / M23 / M24: the transformer plans' phases (C2's PERF on the mesh model's step records)
    plans = {}
    X8, Wq, Wk, Wv, s_x, s_w = nx.m21_data()
    plans["M21"] = nx.attention_plan(X8, Wq, Wk, Wv, s_x, s_w)
    for tag, cfg, seed in (("M23", nx.M23_CFG, 23), ("M24", nx.M24_CFG, 24)):
        x8, s_x_, W, g1, g2 = nx.block_data(cfg, seed)
        plans[tag] = nx.block_plan(cfg, x8, s_x_, W, g1, g2)
    for tag, plan in plans.items():
        if tag not in meas["phases"]:
            continue
        mm = nx.MeshModel(plan.geo)
        for node, base, words in plan.loads:
            mm.load(node, base, words)
        for ph in plan.phases:
            mm.run_phase(ph)
        pred = {ph.name: nx.phase_prediction(mm, ph.name, plan.geo) for ph in plan.phases}
        m_tot = sum(c for _, c in meas["phases"][tag])
        p_tot = sum(pred.get(n, 0) for n, _ in meas["phases"][tag])
        errs = [abs(pred[n] / c - 1) for n, c in meas["phases"][tag] if n in pred and c]
        worst = max(((n, c, pred[n]) for n, c in meas["phases"][tag] if n in pred), key=lambda t: abs(t[2] / t[1] - 1))
        print(f"{tag} ({len(meas['phases'][tag])} phases on the tile suite's mesh): measured {m_tot} cycles, model {p_tot} ({100 * (p_tot / m_tot - 1):+.1f} %); "
              f"mean |error| per phase {100 * sum(errs) / len(errs):.1f} %, worst '{worst[0]}' {worst[1]} vs {worst[2]} ({100 * (worst[2] / worst[1] - 1):+.0f} %)")
        rows.append((f"{tag} total", m_tot, p_tot))
    errs = [abs(p / m - 1) for _, m, p in rows if m]
    within = sum(e <= 0.10 for e in errs)
    print(f"error table: {len(rows)} measured points, mean |error| {100 * sum(errs) / len(errs):.1f} %, {within}/{len(rows)} within 10 %, "
          f"worst {100 * max(errs):.1f} %")
    print("gaps the model does not carry: the host's register plays and polls inside a measured window (the fitted 'host' term), "
          "the bridge's request queue (four RDREQs outstanding: the fitted round trip is the average of the queue's states), "
          "the sequencer's K-split wait (an owner waits for its contributors' partial sums: modelled as the reduce rows only)")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibrate", metavar="JSON", help="drop 0.44: the error table of the model against the measured cycles (make vdev)")
    ap.add_argument("--no-fit", action="store_true", help="with --calibrate: CONV_PM as it stands, no fit")
    ap.add_argument("--collect", nargs=3, metavar=("T10_JSON", "TILE_LOG", "OUT"), help="drop 0.44: build the measured file from a top T10 run and a tile-suite log")
    ap.add_argument("--predict", action="store_true", help="drop 0.44: the virtual device's prediction of the YOLOv8-m group's steps (sw/neo_run.py --build yolo)")
    ap.add_argument("--h", type=int, default=736)
    ap.add_argument("--w", type=int, default=1280)
    ap.add_argument("--link", type=int, default=128)
    ap.add_argument("--ksplit", action="store_true")
    ap.add_argument("--no-affinity", action="store_true")
    ap.add_argument("--barrier", action="store_true", help="layer barriers instead of dataflow")
    ap.add_argument("--inherit", action="store_true", help="consistent chunk partitioning across layers")
    ap.add_argument("--band", action="store_true", help="spatial banding: tile t owns row band t at every layer")
    ap.add_argument("--rx", type=int, default=v2.RX_BYTES, help="fetch receive rate, bytes per cycle into a core (RTL since drop 0.32: 64, two entries per cycle; 32 after drop 0.30)")
    ap.add_argument("--tx", type=int, default=v2.TX_BYTES, help="drain rate, bytes per cycle out of a core (RTL since drop 0.31: 64, 16-word beats; 4 before)")
    ap.add_argument("--psum", type=int, default=v2.PSUM_BYTES, help="partial-sum receive rate, bytes per cycle (RTL since drop 0.31: 64; 4 before)")
    ap.add_argument("--sparse", action="store_true", help="drop 0.42: every layer but the first 2:4 sparse (half the K-passes) on the "
                    "NCHK = 4 core (every layer's weight entries 10 bits per weight: 1.25x the weight bytes)")
    ap.add_argument("--int4", action="store_true", help="drop 0.43: INT4 weights in the banks (half the weight bytes fetched; "
                    "the NIC expands them to INT8 entries)")
    ap.add_argument("--vec-silu", action="store_true", help="drop 0.43: SiLU exact on the vector unit after every layer's drain "
                    "(compiler/neo_vec.py's activation program: its cycles per row added to the owner's run)")
    a = ap.parse_args()
    if a.collect:
        return collect(*a.collect)
    if a.calibrate:
        return calibrate(a.calibrate, fit=not a.no_fit)
    if a.predict:
        _sw_path()
        import neo_run
        img = neo_run.build_yolo()
        per = {}
        for r in predict_image(img):
            d = per.setdefault(r["layer"], dict(cycles=0.0, valid=0, steps=0, tiles=0))
            d["cycles"] += r["cycles"]; d["valid"] += r["valid"]; d["steps"] += 1; d["tiles"] += r["tiles"]
        print("the virtual device: the YOLOv8-m group at a 12x12 crop on the chip top's 2x2 mesh of 16x8 cores, predicted per layer")
        print(f"  {'layer':<16} {'steps':>5} {'cycles':>9} {'MAC util':>9}")
        for k, d in per.items():
            print(f"  {k:<16} {d['steps']:>5} {d['cycles']:>9.0f} {100 * d['valid'] / (d['cycles'] * d['tiles'] / d['steps']):>8.1f} %")
        print(f"  {'total':<16} {sum(d['steps'] for d in per.values()):>5} {sum(d['cycles'] for d in per.values()):>9.0f}")
        return
    global INHERIT_CHUNKS, BAND, WFACTOR, VEC_ROW
    INHERIT_CHUNKS = a.inherit
    BAND = a.band
    WFACTOR = (1.25 if a.sparse else 1.0) * (0.5 if a.int4 else 1.0)
    if a.vec_silu:
        import neo_vec as nv
        VEC_ROW = nv.cycles_per_row(nv.prog_activation(12))
    nc.set_v02(ksplit=a.ksplit)
    layers = nc.yolov8m(a.h, a.w)
    if a.sparse:
        nc.sparsify(layers)
    compute_only = sum(nc.lower(L).cycles for L in layers)
    total, util, bl, bb, local, ng = simulate(layers, a.link, a.ksplit, not a.no_affinity, a.barrier, a.rx, a.tx, a.psum)
    print(f"YOLOv8-m class at {a.h}x{a.w}, 64 tiles, one bank per tile, {a.link} B/cycle links, "
          f"K-split={'on' if a.ksplit else 'off'}, affinity={'off' if a.no_affinity else 'on'}, "
          f"{'layer barriers' if a.barrier else 'dataflow'}, chunks={'bands' if a.band else ('inherited' if a.inherit else 'per layer')}, "
          f"endpoint rates rx {a.rx} / tx {a.tx} / psum {a.psum} B/cycle (bank {v2.BANK_BYTES})"
          + (", INT4 weights" if a.int4 else "") + (f", SiLU on the vector unit ({VEC_ROW} cycles a row)" if VEC_ROW else ""))
    print(f"  compute-only schedule:  {compute_only:9d} cycles = {compute_only / (nc.F_GHZ * 1e6):.2f} ms")
    print(f"  event model v3:         {total:9d} cycles = {total / (nc.F_GHZ * 1e6):.2f} ms ({100 * (total / compute_only - 1):+.0f} %)")
    print(f"  MAC utilization {100 * util:.1f} %, busiest link {100 * bl:.1f} %, busiest bank {100 * bb:.1f} %, "
          f"activation reads served locally {local}/{ng} groups")


if __name__ == "__main__":
    main()
