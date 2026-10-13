#!/usr/bin/env python3
"""
run_noc.py -- noc_mesh (3x3 of noc_router) under neosim.

  N1  random traffic: every node sends packets to random destinations (including itself) with
      random injection gaps and random sink backpressure; every packet must arrive exactly once,
      at the right node, with its payload intact, and in order per (source, destination) pair
  N2  parity: one flit injected with wrong parity is dropped at its ingress router, which raises
      parity_err; every other router stays clean and all good packets still arrive
  N3  the drop-0.41 router (one-hot rotating arbitration, parity stored at FIFO entry) against the drop-0.40 router kept
      in tb/noc_router_ref.sv, side by side on the same inputs (tb's noc_router_pair, XW = YW = 3): random coordinates,
      flits aimed at the neighbourhood so that the five inputs contend for the outputs, bad parity, random back-pressure,
      err_clear and the output-register hooks; every output (in_ready, out_valid, out_flit, parity_err_sticky) equal in
      every cycle -- the same grant order, cycle for cycle (drop 0.43: with the priority map all zero, i.e. one class)
  N4  per-partition priority (drop 0.43): one router, inputs 0-1 sending partition 1 and inputs 2-4 partition 0, all five
      saturating the local output (70 % ready): with equal priority one round robin (at most 4 fires to others while a head
      waits); with partition 1 the high class (prio = 0x0002) its mean latency below the low class's and every waiting head
      within the bounds the formal harness proves -- 5 + floor(4 / PRIO_N) fires to others for the high class,
      5 PRIO_N + 4 for the low class -- at PRIO_N = 8 (the default) and 2
"""
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from neosim import Design, load  # noqa: E402

NX = NY = 3
XW = YW = 2
DW = 24
FW = 1 + 2 * (XW + YW) + 8 + DW


def make_flit(dst_x, dst_y, src_x, src_y, seq, payload, bad_parity=False):
    body = (dst_y << (XW + 2 * (XW + YW) - YW - XW + 8 + DW + XW)) if False else 0  # placeholder (unused)
    v = 0
    v = (v << YW) | dst_y
    v = (v << XW) | dst_x
    v = (v << YW) | src_y
    v = (v << XW) | src_x
    v = (v << 8) | (seq & 0xFF)
    v = (v << DW) | (payload & ((1 << DW) - 1))
    parity = bin(v).count("1") & 1          # even parity over the whole flit
    if bad_parity:
        parity ^= 1
    return (parity << (FW - 1)) | v


def parse_flit(f):
    payload = f & ((1 << DW) - 1)
    f >>= DW
    seq = f & 0xFF
    f >>= 8
    src_x = f & ((1 << XW) - 1)
    f >>= XW
    src_y = f & ((1 << YW) - 1)
    f >>= YW
    dst_x = f & ((1 << XW) - 1)
    f >>= XW
    dst_y = f & ((1 << YW) - 1)
    return dst_x, dst_y, src_x, src_y, seq, payload


def run(n_per_node, seed, bad=None):
    """bad = (node, index): corrupt the parity of that node's index-th packet."""
    rng = np.random.default_rng(seed)
    rtl = os.path.join(HERE, "..", "rtl")
    d = Design(load([os.path.join(rtl, f) for f in ("noc_router.sv", "noc_mesh.sv")]))
    d.elaborate("noc_mesh", {"NX": NX, "NY": NY, "XW": XW, "YW": YW, "DW": DW})
    N = NX * NY
    rst = d.cell_of("rst_n")
    in_valid, in_flit, in_ready = d.array_of("l_in_valid"), d.array_of("l_in_flit"), d.array_of("l_in_ready")
    out_valid, out_flit, out_ready = d.array_of("l_out_valid"), d.array_of("l_out_flit"), d.array_of("l_out_ready")
    perr = d.array_of("parity_err")
    d.cell_of("err_clear").v = 0
    for c in in_valid:
        c.v = 0
    for c in out_ready:
        c.v = 1
    rst.v = 0
    d.tick(); d.tick()
    rst.v = 1
    d.tick()

    # traffic
    queues = []
    expected = {}       # (src, dst) -> list of (seq, payload) in order
    bad_packet = None
    for n in range(N):
        sx, sy = n % NX, n // NX
        q = []
        for i in range(n_per_node):
            dst = int(rng.integers(0, N))
            dx, dy = dst % NX, dst // NX
            payload = int(rng.integers(0, 1 << DW))
            is_bad = bad is not None and bad == (n, i)
            q.append((dst, make_flit(dx, dy, sx, sy, i, payload, bad_parity=is_bad), i, payload))
            if is_bad:
                bad_packet = (n, dst, i, payload)
            else:
                expected.setdefault((n, dst), []).append((i, payload))
        queues.append(q)
    sent_tick = {}
    received = {n: [] for n in range(N)}
    recv_tick = {}
    total = sum(len(q) for q in queues)
    got = 0
    tick = 0
    idle = 0
    t0 = time.time()
    while tick < 20000:
        # sink readiness and injection decisions for this cycle
        for n in range(N):
            out_ready[n].v = 1 if rng.random() < 0.7 else 0
            if queues[n] and rng.random() < 0.6:
                in_valid[n].v = 1
                in_flit[n].v = queues[n][0][1]
            else:
                in_valid[n].v = 0
        d.settle()
        # record transfers that will happen at this edge
        accepted = []
        for n in range(N):
            if in_valid[n].v and in_ready[n].v:
                dst, flit, seq, payload = queues[n][0]
                sent_tick[(n, dst, seq)] = tick
                accepted.append(n)
            if out_valid[n].v and out_ready[n].v:
                f = parse_flit(out_flit[n].v)
                received[n].append(f)
                recv_tick[(f[3] * NX + f[2], n, f[4])] = tick
                got += 1
        d.tick()
        for n in accepted:
            queues[n].pop(0)
        tick += 1
        idle = idle + 1 if not accepted and got == total - (1 if bad_packet else 0) else 0
        if all(not q for q in queues) and idle > 60:
            break
    elapsed = time.time() - t0

    # checks
    fails = []
    seen = {}
    for n in range(N):
        for (dx, dy, sx, sy, seq, payload) in received[n]:
            src = sy * NX + sx
            if dy * NX + dx != n:
                fails.append(f"packet {src}->{dy * NX + dx} seq {seq} delivered to node {n}")
            seen.setdefault((src, n), []).append((seq, payload))
    for key, lst in expected.items():
        got_list = seen.get(key, [])
        if got_list != lst:
            fails.append(f"pair {key}: expected {len(lst)} in order, got {len(got_list)}: {got_list[:4]} vs {lst[:4]}")
    for key in seen:
        if key not in expected:
            fails.append(f"unexpected packets for pair {key}")
    if bad_packet:
        n, dst, i, payload = bad_packet
        if any(seq == i and p == payload for (seq, p) in seen.get((n, dst), [])):
            fails.append("corrupted flit was delivered")
        flags = [k for k in range(N) if perr[k].v]
        if flags != [n]:
            fails.append(f"parity_err flags on {flags}, expected [{n}]")
    else:
        flags = [k for k in range(N) if perr[k].v]
        if flags:
            fails.append(f"spurious parity_err on {flags}")
    lat = [recv_tick[k] - sent_tick[k] for k in recv_tick if k in sent_tick]
    return fails, got, total, tick, (np.mean(lat) if lat else 0, max(lat) if lat else 0), elapsed, len(d.cells)


def equivalence(seed, cycles, hooks):
    """N3: rtl/noc_router.sv and the drop-0.40 reference on the same random inputs, every output compared every cycle.
    Returns (mismatches, first mismatch, fires, contended grants)."""
    rng = np.random.default_rng(seed)
    xw = yw = 3
    dw = 16
    fw = 1 + 2 * (xw + yw) + 8 + dw
    rtl, tb = os.path.join(HERE, "..", "rtl"), os.path.join(HERE, "..", "tb")
    d = Design(load([os.path.join(rtl, "noc_router.sv"), os.path.join(tb, "noc_router_ref.sv")]))
    d.elaborate("noc_router_pair", {"XW": xw, "YW": yw, "DW": dw, "DV_HOOKS": 1})
    c = {n: d.cell_of(n) for n in ("rst_n", "my_x", "my_y", "err_clear", "dv_flit_flip", "dv_valid_clear", "dv_port", "dv_mask",
                                    "n_perr", "r_perr")}
    a = {n: d.array_of(n) for n in ("in_valid", "in_flit", "out_ready", "n_in_ready", "n_out_valid", "n_out_flit",
                                     "r_in_ready", "r_out_valid", "r_out_flit")}
    rq = [d.cells[f"top.u_new.rq[{o}]"] for o in range(5)]
    fire = [d.cells[f"top.u_new.fire[{o}]"] for o in range(5)]
    for n in ("err_clear", "dv_flit_flip", "dv_valid_clear", "dv_port", "dv_mask"):
        c[n].v = 0
    for i in range(5):
        a["in_valid"][i].v = 0
        a["out_ready"][i].v = 1
    mx, my = int(rng.integers(0, 8)), int(rng.integers(0, 8))
    c["my_x"].v, c["my_y"].v = mx, my
    c["rst_n"].v = 0
    d.tick(); d.tick()
    c["rst_n"].v = 1
    mism, first, fires, contended = 0, None, 0, 0
    for t in range(cycles):
        for i in range(5):
            a["in_valid"][i].v = int(rng.random() < 0.7)
            dx = min(7, max(0, mx + int(rng.integers(-1, 2))))
            dy = min(7, max(0, my + int(rng.integers(-1, 2))))
            v = (dy << (fw - 1 - yw)) | (dx << (fw - 1 - yw - xw)) | int(rng.integers(0, 1 << (fw - 1 - yw - xw)))
            par = bin(v).count("1") & 1
            if rng.random() < 0.05:
                par ^= 1                                      # a corrupted flit: dropped and flagged by both
            a["in_flit"][i].v = (par << (fw - 1)) | v
            a["out_ready"][i].v = int(rng.random() < 0.6)
        c["err_clear"].v = int(rng.random() < 0.01)
        if hooks:
            c["dv_flit_flip"].v = int(rng.random() < 0.03)
            c["dv_valid_clear"].v = int(rng.random() < 0.03)
            c["dv_port"].v = int(rng.integers(0, 8))
            c["dv_mask"].v = int(rng.integers(0, 1 << fw))
        d.settle()
        contended += sum(1 for o in range(5) if fire[o].v and bin(rq[o].v).count("1") >= 2)
        fires += sum(f.v for f in fire)
        d.tick()
        diff = [n for n in ("in_ready", "out_valid", "out_flit") for i in range(5) if a["n_" + n][i].v != a["r_" + n][i].v]
        if c["n_perr"].v != c["r_perr"].v:
            diff.append("parity_err_sticky")
        if diff:
            mism += 1
            if first is None:
                first = (t, diff[:4])
    return mism, first, fires, contended


def priority(seed, cycles, prio, prio_n=8, hi_inputs=(0, 1)):
    """N4 (drop 0.43): one router, all five inputs saturating output 4 (every flit addressed to the router's own node), the
    inputs in hi_inputs sending partition 1 and the others partition 0, prio the router's map, out_ready random (70 %).
    Per granted head: the cycles from its arrival at the head of its FIFO to its grant and the fires of output 4 that went to
    other inputs meanwhile, per class. Returns {class: (grants, mean cycles, max cycles, max fires to others)}."""
    rng = np.random.default_rng(seed)
    xw = yw = 3
    dw = 16
    fw = 1 + 2 * (xw + yw) + 8 + dw
    rtl = os.path.join(HERE, "..", "rtl")
    d = Design(load([os.path.join(rtl, "noc_router.sv")]))
    d.elaborate("noc_router", {"XW": xw, "YW": yw, "DW": dw, "DV_HOOKS": 0, "PRIO_N": prio_n})
    c = {n: d.cell_of(n) for n in ("rst_n", "my_x", "my_y", "prio", "err_clear", "dv_flit_flip", "dv_valid_clear", "dv_port", "dv_mask")}
    in_valid, in_flit, out_ready = d.array_of("in_valid"), d.array_of("in_flit"), d.array_of("out_ready")
    rq = [d.cells[f"top.rq[{o}]"] for o in range(5)]
    gnt = [d.cells[f"top.gnt[{o}]"] for o in range(5)]
    fire = [d.cells[f"top.fire[{o}]"] for o in range(5)]
    for n in ("err_clear", "dv_flit_flip", "dv_valid_clear", "dv_port", "dv_mask"):
        c[n].v = 0
    c["my_x"].v, c["my_y"].v, c["prio"].v = 3, 3, prio
    c["rst_n"].v = 0
    for i in range(5):
        in_valid[i].v = 0
        out_ready[i].v = 1
    d.tick(); d.tick()
    c["rst_n"].v = 1
    since, waited = [None] * 5, [0] * 5                   # per input: the cycle its head started requesting, fires to others
    stats = {0: [], 1: []}                                # class -> [(cycles, fires to others)]
    for t in range(cycles):
        for i in range(5):
            part = 1 if i in hi_inputs else 0
            v = (3 << (fw - 1 - yw)) | (3 << (fw - 1 - yw - xw)) | (i << (dw + 8)) | (part << dw) | int(rng.integers(0, 1 << dw))
            in_valid[i].v = 1
            in_flit[i].v = ((bin(v).count("1") & 1) << (fw - 1)) | v
        out_ready[4].v = int(rng.random() < 0.7)
        d.settle()
        r, g, f = rq[4].v, gnt[4].v, fire[4].v
        for i in range(5):
            if (r >> i) & 1:
                if since[i] is None:
                    since[i], waited[i] = t, 0
                if f and (g >> i) & 1:
                    stats[1 if (prio >> (1 if i in hi_inputs else 0)) & 1 else 0].append((t - since[i], waited[i]))
                    since[i] = None
                elif f:
                    waited[i] += 1
        d.tick()
    return {k: (len(v), float(np.mean([a for a, _ in v])) if v else 0.0, max((a for a, _ in v), default=0),
                max((b for _, b in v), default=0)) for k, v in stats.items()}


def main():
    fails_total = 0
    print(f"noc_mesh {NX}x{NY}, {FW}-bit flits, XY routing, 2-entry input FIFOs, registered outputs (neosim):")
    fails, got, total, ticks, (avg, mx), el, cells = run(12, seed=1)
    ok = not fails and got == total
    print(f"  N1 random traffic: {total} packets from {NX * NY} nodes, 60 % injection, 70 % sink ready: "
          f"{got}/{total} delivered in {ticks} cycles, mean latency {avg:.1f} cycles, max {mx}; "
          f"in-order per pair, payload intact, no spurious parity flags -> {'PASS' if ok else 'FAIL'} ({el:.1f} s, {cells} cells)")
    for f in fails[:5]:
        print("     ", f)
    fails_total += not ok
    fails, got, total, ticks, (avg, mx), el, cells = run(8, seed=2, bad=(0, 3))
    ok = not fails and got == total - 1
    print(f"  N2 parity fault: node 0's 4th flit injected with wrong parity: {got}/{total - 1} good packets delivered, "
          f"corrupted flit dropped, parity_err raised on node 0 only -> {'PASS' if ok else 'FAIL'}")
    for f in fails[:5]:
        print("     ", f)
    fails_total += not ok
    t0 = time.time()
    tot = [0, 0, 0]
    firsts = []
    for seed, hooks in ((31, False), (32, True), (33, True)):
        mism, first, fires, contended = equivalence(seed, 2000, hooks)
        tot[0] += mism; tot[1] += fires; tot[2] += contended
        if first:
            firsts.append((seed, first))
    ok = tot[0] == 0
    print(f"  N3 one-hot arbitration vs the drop-0.40 router: 3 x 2,000 cycles, random coordinates, 70 % injection, 5 % bad parity, "
          f"60 % ready, err_clear and output hooks: {tot[1]} grants ({tot[2]} with two or more contenders), "
          f"{tot[0]} cycles with any output different -> {'PASS' if ok else 'FAIL'} ({time.time() - t0:.1f} s)")
    for s, f in firsts[:3]:
        print(f"      seed {s}: first difference at cycle {f[0]} on {f[1]}")
    fails_total += not ok
    # N4 (drop 0.43): two partitions contending for one output, equal priority and then partition 1 high, at PRIO_N = 8 and 2
    t0 = time.time()
    eq = priority(41, 3000, 0x0000)
    ok = eq[0][0] > 0 and eq[1][0] == 0 and eq[0][3] <= 4        # one class: drop 0.41's round robin, 4 fires to others at most
    lines = [f"equal priority: {eq[0][0]} grants, mean {eq[0][1]:.1f} cycles, max {eq[0][2]}, at most {eq[0][3]} fires to others"]
    for n in (8, 2):
        st = priority(42 + n, 3000, 0x0002, prio_n=n)
        b_hi, b_lo = 5 + 4 // n, 5 * n + 4
        good = st[1][0] > 0 and st[0][0] > 0 and st[1][3] <= b_hi and st[0][3] <= b_lo and st[1][1] < st[0][1]
        ok = ok and good
        lines.append(f"PRIO_N {n}: high {st[1][0]} grants, mean {st[1][1]:.1f} cycles (max {st[1][2]}), at most {st[1][3]} fires to "
                     f"others (bound {b_hi}); low {st[0][0]} grants, mean {st[0][1]:.1f} (max {st[0][2]}), at most {st[0][3]} (bound {b_lo})")
    print(f"  N4 per-partition priority: one router, inputs 0-1 partition 1, 2-4 partition 0, all five saturating the local output "
          f"(70 % ready): {'; '.join(lines)} -> {'PASS' if ok else 'FAIL'} ({time.time() - t0:.1f} s)")
    fails_total += not ok
    print(f"RESULT: {'ALL PASS' if fails_total == 0 else f'{fails_total} FAILURES'}")
    return 1 if fails_total else 0


if __name__ == "__main__":
    raise SystemExit(main())
