#!/usr/bin/env python3
"""
run_noc.py -- noc_mesh (3x3 of noc_router) under neosim.

  N1  random traffic: every node sends packets to random destinations (including itself) with
      random injection gaps and random sink backpressure; every packet must arrive exactly once,
      at the right node, with its payload intact, and in order per (source, destination) pair
  N2  parity: one flit injected with wrong parity is dropped at its ingress router, which raises
      parity_err; every other router stays clean and all good packets still arrive
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
    print(f"RESULT: {'ALL PASS' if fails_total == 0 else f'{fails_total} FAILURES'}")
    return 1 if fails_total else 0


if __name__ == "__main__":
    raise SystemExit(main())
