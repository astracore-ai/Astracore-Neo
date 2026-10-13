#!/usr/bin/env python3
"""run_faults.py -- the fault campaign (drop 0.43, stream D2): sample, run, merge, report.

  run_faults.py run --config tile|core --n N --seed S --workers W [--budget-s B] --out DIR
      flops.py's population of the build (two-dimensional arrays left out: cocotb cannot reach them on Verilator 5.020;
      on the tile build the core's blocks of the three idle tiles left out -- weights.json), the fault-free run once for
      its length, N faults sampled with the seed (every block min_per_block faults, the rest over the bits in proportion
      to bits x weight; a bit uniformly within its block; the type by weights.json; the cycle uniformly in the fault-free
      run), the memory arrays' cells sampled in addition (weights.json: mem_quota, mem_frac, mem_types; the faults
      shuffled together, so a budget cut leaves an unbiased sample), the list split over W simulator processes (the cocotb
      build `faults_<config>` in dv/cocotb, built once), each
      working its list until its budget runs out; their CSVs merged into DIR/faults_<config>.csv.
  run_faults.py report DIR
      DIR/faults_*.csv -> DIR/faults.csv (every fault, both builds), DIR/faults_report.md (per-block diagnostic coverage
      with 95 % Wilson intervals, latencies, causes, the comparator storage of drop 0.43, the population left out),
      DIR/undetected.md (the dangerous and hung faults' flops grouped by block), DIR/faults_summary.json (for
      05_safety/build_fmeda.py).
  run_faults.py all --n-tile N --n-core N --seed S --workers W [--budget-min M] --out DIR
      run core, run tile (the budget split in proportion to the counts), report -- the faults job.

Run from dv/cocotb's parent (the flat layout's root) or anywhere: the paths are taken from this file's location."""
import argparse
import csv
import glob
import json
import math
import os
import random
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
COCOTB = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import flops as F                                   # noqa: E402

OUTCOMES = ["detected", "masked", "dangerous", "hang_detected", "hang"]
# the registers drop 0.43 put behind the comparators (item 2): flop-name fragments -> the label in the report
ITEM2 = [("u_feeder.inb_q", "feeder inb_q"), ("u_feeder.inb_dq", "feeder inb_dq (copy)"), ("u_feeder.a0_q", "feeder a0_q"),
         ("u_feeder.a0_dq", "feeder a0_dq (copy)"), ("u_feeder.first_q", "feeder first_q"), ("u_feeder.first_oq", "feeder first_oq"),
         ("u_feeder.first_dq", "feeder first_dq (copy)"), ("u_feeder.first_doq", "feeder first_doq (copy)"),
         ("u_feeder.addr_dq", "feeder addr_dq (copy)"), ("u_feeder.u_mem.rb_addr_q", "activation buffer port-B latch"),
         (".w_load_q", "core w_load_q"), (".d_w_load_q", "core d_w_load_q (copy)"), (".d_w_raddr_q", "core d_w_raddr_q (copy)"),
         ("u_wbuf.u_mem.rb_addr_q", "weight buffer port-B latch"), ("u_wbuf.ra0_q", "weight buffer ra0_q"),
         ("u_acc.v1", "acc v1"), ("u_acc.first1", "acc first1"), ("u_acc.idx1", "acc idx1"), ("u_acc.fwd1", "acc fwd1"),
         ("u_acc.v1_n", "acc v1_n (copy)"), ("u_acc.first1_n", "acc first1_n (copy)"), ("u_acc.idx1_n", "acc idx1_n (copy)"),
         ("u_acc.fwd1_n", "acc fwd1_n (copy)"), ("u_acc.rbq_n", "acc rbq_n (copy)"), ("u_acc.u_mem.rb_addr_q", "accumulator port-B latch")]


def load_weights(path):
    with open(path) as f:
        w = json.load(f)
    w.setdefault("min_per_block", 25)
    w.setdefault("types", {"flip": 0.5, "sa0": 0.25, "sa1": 0.25})
    w.setdefault("block_weight", {})
    return w


def population(config, weights, state=None):
    """(flop population, memory arrays, left out {reason: {block: bits}}); `state` (a list) receives every flop element
    of the build the VPI can index ([path, elem]: what the drivers put back before each run, common.StateKeeper)"""
    pop, mems = [], []
    out = {"2-D array (not reachable through cocotb on Verilator 5.020)": {}, "idle core (tiles other than the computing one)": {}}
    for f in F.enumerate_flops(config):
        if state is not None and f.get("kind") != "sram" and len(f["elem"]) <= 1:
            state.append([f["path"], f["elem"]])
        node = F.node_of(f["path"]) if config == "tile" else None
        if f.get("kind") == "sram":
            base = f["block"][:-len(".sram")]
            if config == "tile" and base in weights.get("core_blocks", []) and node not in weights.get("core_nodes", [0]):
                d = out["idle core (tiles other than the computing one)"]
                d[f["block"]] = d.get(f["block"], 0) + f["width"] * f["words"]
                continue
            f["node"] = node
            mems.append(f)
            continue
        if len(f["elem"]) > 1:
            d = out["2-D array (not reachable through cocotb on Verilator 5.020)"]
            d[f["block"]] = d.get(f["block"], 0) + f["width"]
            continue
        if config == "tile" and f["block"] in weights.get("core_blocks", []) and node not in weights.get("core_nodes", [0]):
            d = out["idle core (tiles other than the computing one)"]
            d[f["block"]] = d.get(f["block"], 0) + f["width"]
            continue
        f["node"] = node
        pop.append(f)
    return pop, mems, out


def sample_mems(mems, n, seed, weights, run_cycles):
    """the memory arrays' faults: max(mem_quota, n x mem_frac / blocks) per memory block (its arrays' bits uniformly), the
    types of mem_types"""
    rng = random.Random(seed * 7919 + 1)
    types = list(weights.get("mem_types", {"flip": 0.8, "sa0": 0.1, "sa1": 0.1}))
    tprob = [weights.get("mem_types", {"flip": 0.8, "sa0": 0.1, "sa1": 0.1})[t] for t in types]
    blocks = {}
    for m in mems:
        blocks.setdefault(m["block"], []).append(m)
    q = max(int(weights.get("mem_quota", 40)), int(n * float(weights.get("mem_frac", 0.0)) / max(1, len(blocks))))
    faults, bits = [], {}
    for b in sorted(blocks):
        arr = blocks[b]
        bits[b] = sum(m["width"] * m["words"] for m in arr)
        cum, s = [], 0
        for m in arr:
            s += m["width"] * m["words"]
            cum.append(s)
        for _ in range(q):
            m = rng.choices(arr, cum_weights=cum)[0]
            faults.append(dict(path=m["path"], elem=[rng.randrange(m["words"])], bit=rng.randrange(m["width"]),
                               type=rng.choices(types, tprob)[0], cycle=rng.randrange(run_cycles), block=b, node=m.get("node"),
                               kind="sram", weight=round(bits[b] / q, 4)))
    return faults, bits, q


def sample(pop, n, seed, weights, run_cycles):
    """n faults, seeded: block quotas (min_per_block each, the rest over bits x weight, largest remainder), a bit uniformly
    within the block, type and cycle at random. Each fault carries the estimator weight bits_b / quota_b."""
    rng = random.Random(seed)
    blocks = {}
    for i, f in enumerate(pop):
        blocks.setdefault(f["block"], []).append(i)
    bits = {b: sum(pop[i]["width"] for i in idx) for b, idx in blocks.items()}
    mn = min(weights["min_per_block"], n // max(1, len(blocks)))
    quota = {b: mn for b in blocks}
    rest = n - mn * len(blocks)
    bw = weights["block_weight"]
    wsum = sum(bits[b] * bw.get(b, 1.0) for b in blocks)
    raw = {b: rest * bits[b] * bw.get(b, 1.0) / wsum for b in blocks}
    for b in blocks:
        quota[b] += int(raw[b])
    left = n - sum(quota.values())
    for b in sorted(blocks, key=lambda b: raw[b] - int(raw[b]), reverse=True)[:left]:
        quota[b] += 1
    types = list(weights["types"])
    tprob = [weights["types"][t] for t in types]
    faults = []
    for b in sorted(blocks):
        idx = blocks[b]
        cum, s = [], 0
        for i in idx:
            s += pop[i]["width"]
            cum.append(s)
        for _ in range(quota[b]):
            i = rng.choices(idx, cum_weights=cum)[0]
            f = pop[i]
            faults.append(dict(path=f["path"], elem=f["elem"], bit=rng.randrange(f["width"]), type=rng.choices(types, tprob)[0],
                               cycle=rng.randrange(run_cycles), block=b, node=f.get("node"), kind="flop",
                               weight=round(bits[b] / quota[b], 4) if quota[b] else 0))
    return faults, bits, quota


def make_cmd(config, extra):
    return ["make", "-C", COCOTB, f"faults_{config}"] + extra


def golden(config, out, probe):
    """the fault-free run alone (the build happens here, once): its length and wall time, and which of the arrays in
    `probe` the injector cannot reach in this build"""
    gfile = os.path.join(out, f"golden_{config}.json")
    pfile = os.path.join(out, f"probe_{config}.json")
    with open(pfile, "w") as f:
        json.dump(probe, f)
    env = dict(os.environ, FAULTS_GOLDEN=gfile, FAULTS_PROBE=pfile)
    env.pop("FAULTS_LIST", None)
    log = os.path.join(out, f"build_{config}.log")
    t0 = time.time()
    with open(log, "w") as lf:
        rc = subprocess.call(make_cmd(config, ["FAULTS_WORKER=_golden"]), env=env, stdout=lf, stderr=subprocess.STDOUT)
    if rc != 0 or not os.path.exists(gfile):
        raise SystemExit(f"run_faults.py: the {config} build / fault-free run failed (rc {rc}); see {log}")
    with open(gfile) as f:
        g = json.load(f)
    g["build_and_golden_s"] = round(time.time() - t0, 1)
    return g


def run(config, n, seed, workers, budget_s, out, weights_path):
    os.makedirs(out, exist_ok=True)
    weights = load_weights(weights_path)
    state = []
    pop, mems, left_out = population(config, weights, state)
    with open(os.path.join(out, f"state_{config}.json"), "w") as f:
        json.dump(state, f)
    os.environ["FAULTS_STATE"] = os.path.join(out, f"state_{config}.json")    # the golden run and the workers inherit it
    first = {}                                        # every array of the population with its first element's index
    for f in pop:
        if f["elem"] and f["path"] not in first:
            first[f["path"]] = f["elem"]
    for m in mems:
        first.setdefault(m["path"], [0])
    g = golden(config, out, [[p, e] for p, e in sorted(first.items())])
    bad = set(g.pop("unreachable", []))
    if bad:                                          # common.Injector.resolve: arrays the VPI layer cannot range here
        d = left_out.setdefault("array not reachable through cocotb 1.9's VPI layer in this build (common.py)", {})
        for f in pop + mems:
            if f["path"] in bad:
                d[f["block"]] = d.get(f["block"], 0) + f["width"] * f.get("words", 1)
        pop = [f for f in pop if f["path"] not in bad]
        mems = [m for m in mems if m["path"] not in bad]
    faults, bits, quota = sample(pop, n, seed, weights, g["run_cycles"])
    mfaults, mbits, mq = sample_mems(mems, n, seed, weights, g["run_cycles"])
    n_flop = len(faults)
    faults += mfaults
    random.Random(seed + 104729).shuffle(faults)    # blocks mixed over the workers and over time (a budget cut is unbiased)
    for k, f in enumerate(faults):
        f["id"] = k
    meta = dict(config=config, n=n, n_flop=n_flop, n_mem=len(mfaults), n_total=len(faults), seed=seed, workers=workers,
                budget_s=budget_s, golden=g, population_bits=bits, quota=quota,
                memory_bits=mbits, memory_quota=mq, left_out=left_out, population_flops=len(pop),
                started=time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()))
    lists = [faults[w::workers] for w in range(workers)]
    procs = []
    t0 = time.time()
    for w, lst in enumerate(lists):
        lpath = os.path.join(out, f"list_{config}_w{w}.json")
        cpath = os.path.join(out, f"faults_{config}_w{w}.csv")
        if os.path.exists(cpath):
            os.remove(cpath)
        with open(lpath, "w") as f:
            json.dump(lst, f)
        env = dict(os.environ, FAULTS_LIST=lpath, FAULTS_OUT=cpath, FAULTS_BUDGET_S=str(budget_s or 0))
        env.pop("FAULTS_GOLDEN", None)
        lf = open(os.path.join(out, f"worker_{config}_w{w}.log"), "w")
        procs.append((subprocess.Popen(make_cmd(config, [f"FAULTS_WORKER=_w{w}"]), env=env, stdout=lf, stderr=subprocess.STDOUT), lf))
    for p, lf in procs:
        p.wait()
        lf.close()
    meta["workers_s"] = round(time.time() - t0, 1)
    rows = []
    for w in range(workers):
        cpath = os.path.join(out, f"faults_{config}_w{w}.csv")
        if os.path.exists(cpath):
            with open(cpath) as f:
                rows += list(csv.DictReader(f))
    wmap = {f["id"]: f["weight"] for f in faults}
    rows.sort(key=lambda r: int(r["id"]))
    with open(os.path.join(out, f"faults_{config}.csv"), "w", newline="") as f:
        fields = list(rows[0].keys()) + ["weight"] if rows else ["id"]
        wr = csv.DictWriter(f, fieldnames=fields)
        wr.writeheader()
        for r in rows:
            r["weight"] = wmap.get(int(r["id"]), "")
            wr.writerow(r)
    meta["n_run"] = len(rows)
    walls = [int(r["wall_ms"]) for r in rows if r.get("wall_ms")]
    meta["mean_wall_ms"] = round(statistics.mean(walls), 1) if walls else None
    with open(os.path.join(out, f"meta_{config}.json"), "w") as f:
        json.dump(meta, f, indent=1)
    print(f"{config}: {len(rows)} of {len(faults)} faults ({n_flop} flop, {len(mfaults)} memory) run by {workers} workers in {meta['workers_s']} s "
          f"(mean {meta['mean_wall_ms']} ms per fault; fault-free run {g['run_cycles']} cycles)")
    return meta


# ---------------------------------------------------------------------------------------------------------------- report
def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - h) / d), min(1.0, (c + h) / d))


def pct(x):
    return f"{100 * x:.1f} %"


def block_stats(rows):
    st = {}
    for r in rows:
        if r["outcome"] == "unresolved":
            continue
        s = st.setdefault(r["block"], dict(n=0, lat=[], causes={}, w=0.0, **{o: 0 for o in OUTCOMES}))
        s["n"] += 1
        s[r["outcome"]] += 1
        s["w"] = float(r.get("weight") or 0) or s["w"]
        if r["outcome"] in ("detected", "hang_detected") and r["latency"] not in ("", None):
            s["lat"].append(int(r["latency"]))
        for c in (r["first_cause"] or "").split("+"):
            if c:
                c = c.split("@")[0]
                s["causes"][c] = s["causes"].get(c, 0) + 1
    for s in st.values():
        det = s["detected"] + s["hang_detected"]
        dang = s["dangerous"] + s["hang"]
        s["det"], s["dang"] = det, dang
        s["dc"] = det / (det + dang) if det + dang else None
        s["dc_ci"] = wilson(det, det + dang)
        s["fdang"] = dang / s["n"] if s["n"] else 0.0
        s["fdang_ci"] = wilson(dang, s["n"])
        s["fmask"] = s["masked"] / s["n"] if s["n"] else 0.0
    return st


def report(out):
    metas = {}
    for p in sorted(glob.glob(os.path.join(out, "meta_*.json"))):
        with open(p) as f:
            m = json.load(f)
        metas[m["config"]] = m
    allrows = []
    for cfg in ("core", "tile"):
        p = os.path.join(out, f"faults_{cfg}.csv")
        if os.path.exists(p):
            with open(p) as f:
                allrows += list(csv.DictReader(f))
    if not allrows:
        raise SystemExit(f"run_faults.py report: no faults_*.csv in {out}")
    fields = list(allrows[0].keys())
    with open(os.path.join(out, "faults.csv"), "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=fields)
        wr.writeheader()
        wr.writerows(allrows)
    summary = {}
    L = ["# Fault campaign report (`faults` job; 02_dv/dv/cocotb/faults/)", ""]
    L.append("Faults injected into the flops of the cocotb builds through the model's VPI (--public-flat-rw), each run classified "
             "against the fault-free run of the same driver: **detected** (a sticky cause or the watchdog; the latency is the "
             "first cycle the cause was seen after the injection), **masked** (results bit-exact, no cause), **dangerous** "
             "(results wrong, no cause), **hang** (the run did not end; hang_detected when the watchdog or the fetch timeout "
             "fired). DC = detected / (detected + dangerous), masked faults (safe in this workload) excluded; the interval is "
             "the 95 % Wilson interval of that fraction. The FTTI budget (10 ms, 20,000,000 cycles at 2 GHz) exceeds every run, "
             "so every cause seen is within it.")
    L.append("")
    for cfg in ("core", "tile"):
        rows = [r for r in allrows if r["config"] == cfg]
        if not rows:
            continue
        m = metas.get(cfg, {})
        g = m.get("golden", {})
        st = block_stats(rows)
        tot = {o: sum(1 for r in rows if r["outcome"] == o) for o in OUTCOMES + ["unresolved"]}
        L.append(f"## The {cfg} build ({'2 x 2 mesh of 16 x 8 tiles, workload M1' if cfg == 'tile' else '16 x 8 neo_core, NCHK = 4, workload C1'})")
        L.append("")
        L.append(f"Seed {m.get('seed')}, {m.get('n_run', len(rows))} of {m.get('n_total', m.get('n'))} faults ({m.get('n_flop', m.get('n'))} "
                 f"sampled over the flops, {m.get('n_mem', 0)} over the memory arrays) run by {m.get('workers')} workers in "
                 f"{m.get('workers_s')} s ({m.get('mean_wall_ms')} ms per fault); the fault-free run {g.get('run_cycles')} cycles "
                 f"({g.get('wall_s')} s){', watchdog window ' + str(g.get('window')) + ' cycles' if g.get('window') else ''}; "
                 f"population {m.get('population_flops')} flop elements, {sum((m.get('population_bits') or {}).values())} bits.")
        L.append("")
        L.append("| Outcome | Faults |")
        L.append("| --- | --- |")
        for o in OUTCOMES + ["unresolved"]:
            if tot[o] or o != "unresolved":
                L.append(f"| {o} | {tot[o]} |")
        # the bit-weighted estimate over the whole flop population (the memories apart)
        frows = [r for r in rows if not r["block"].endswith(".sram")]
        wdet = sum(float(r.get("weight") or 0) for r in frows if r["outcome"] in ("detected", "hang_detected"))
        wdang = sum(float(r.get("weight") or 0) for r in frows if r["outcome"] in ("dangerous", "hang"))
        wall = sum(float(r.get("weight") or 0) for r in frows if r["outcome"] != "unresolved")
        L.append("")
        if wall:
            L.append(f"The whole flop population, each fault weighted by its block's bits / its block's sample: DC "
                     f"{pct(wdet / (wdet + wdang)) if wdet + wdang else 'n/a'}, dangerous {pct(wdang / wall)} of all faults, "
                     f"masked {pct(1 - (wdet + wdang) / wall)}.")
            L.append("")
            summary.setdefault("_overall", {})[cfg] = dict(dc=(wdet / (wdet + wdang)) if wdet + wdang else None,
                                                           fdang=wdang / wall, fmask=1 - (wdet + wdang) / wall)
        bitsmap = dict(m.get("population_bits") or {}, **(m.get("memory_bits") or {}))
        for title, sel in (("Flops", lambda b: not b.endswith(".sram")),
                           ("Memory arrays (the SRAM leaves' cells; a quota per block)", lambda b: b.endswith(".sram"))):
            bl = [b for b in st if sel(b)]
            if not bl:
                continue
            L.append(f"**{title}**")
            L.append("")
            L.append("| Block | Bits | Faults | Det. | Masked | Dang. | Hang det. / undet. | DC (95 % CI) | Dangerous of all (95 % CI) | Latency median / max (cycles) | First causes |")
            L.append("| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |")
            for b in sorted(bl, key=lambda b: -bitsmap.get(b, 0)):
                s = st[b]
                dc = f"{pct(s['dc'])} ({pct(s['dc_ci'][0])} .. {pct(s['dc_ci'][1])})" if s["dc"] is not None else "n/a (none dangerous or detected)"
                fd = f"{pct(s['fdang'])} ({pct(s['fdang_ci'][0])} .. {pct(s['fdang_ci'][1])})"
                lat = f"{int(statistics.median(s['lat']))} / {max(s['lat'])}" if s["lat"] else "-"
                causes = ", ".join(f"{c} {k}" for c, k in sorted(s["causes"].items(), key=lambda kv: -kv[1])[:3])
                L.append(f"| {b} | {bitsmap.get(b, '')} | {s['n']} | {s['detected']} | {s['masked']} | "
                         f"{s['dangerous']} | {s['hang_detected']} / {s['hang']} | {dc} | {fd} | {lat} | {causes} |")
                summary.setdefault(cfg, {})[b] = dict(bits=bitsmap.get(b), n=s["n"], detected=s["det"],
                                                      masked=s["masked"], dangerous=s["dang"], dc=s["dc"], dc_ci=s["dc_ci"],
                                                      fdang=s["fdang"], fdang_ci=s["fdang_ci"], fmask=s["fmask"],
                                                      lat_median=(statistics.median(s["lat"]) if s["lat"] else None),
                                                      lat_max=(max(s["lat"]) if s["lat"] else None),
                                                      by_type={t: dict(n=sum(1 for r in rows if r["block"] == b and r["type"] == t and r["outcome"] != "unresolved"),
                                                                       dang=sum(1 for r in rows if r["block"] == b and r["type"] == t and r["outcome"] in ("dangerous", "hang")),
                                                                       det=sum(1 for r in rows if r["block"] == b and r["type"] == t and r["outcome"] in ("detected", "hang_detected")))
                                                               for t in ("flip", "sa0", "sa1")})
            L.append("")
        lo = m.get("left_out") or {}
        if any(lo.values()):
            L.append("Left out of the population: " + "; ".join(
                f"{why}: " + ", ".join(f"{b} {n} bits" for b, n in sorted(d.items(), key=lambda kv: -kv[1]))
                for why, d in lo.items() if d) + ".")
            L.append("")
    # item 2
    L.append("## The comparator storage of drop 0.41 (item 2) in the sample")
    L.append("")
    L.append("| Register | Faults | Detected | Masked | Dangerous | Hang | Latency max |")
    L.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    any2 = False
    for frag, label in ITEM2:
        rs = [r for r in allrows if frag in r["flop"] and r["outcome"] != "unresolved"
              and not (frag == ".w_load_q" and ".d_w_load_q" in r["flop"])]
        if not rs:
            continue
        any2 = True
        lat = [int(r["latency"]) for r in rs if r["latency"] not in ("", None)]
        L.append(f"| {label} | {len(rs)} | {sum(r['outcome'] in ('detected', 'hang_detected') for r in rs)} | "
                 f"{sum(r['outcome'] == 'masked' for r in rs)} | {sum(r['outcome'] == 'dangerous' for r in rs)} | "
                 f"{sum(r['outcome'] in ('hang', 'hang_detected') for r in rs)} | {max(lat) if lat else '-'} |")
    if not any2:
        L.append("| (none of these registers drawn in this sample) | | | | | | |")
    L.append("")
    # the undetected
    U = ["# Undetected faults (dangerous and hung without a cause), by block", ""]
    bad = [r for r in allrows if r["outcome"] in ("dangerous", "hang")]
    U.append(f"{len(bad)} of {len(allrows)} faults. Per flop: the number of such faults and their types (flip / sa0 / sa1).")
    U.append("")
    for b in sorted({r["block"] for r in bad}):
        rs = [r for r in bad if r["block"] == b]
        U.append(f"## {b} ({len(rs)})")
        U.append("")
        per = {}
        for r in rs:
            key = (r["config"], r["flop"])
            per.setdefault(key, []).append(f"{r['type']}@{r['cycle']}/b{r['bit']}{'/hang' if r['outcome'] == 'hang' else ''}")
        for (cfg, fl), v in sorted(per.items(), key=lambda kv: -len(kv[1])):
            U.append(f"- `{fl}` ({cfg}): {len(v)} -- {', '.join(v[:6])}{' ...' if len(v) > 6 else ''}")
        U.append("")
    L.append(f"The undetected faults' flops, grouped by block: `undetected.md` ({len(bad)} faults).")
    with open(os.path.join(out, "faults_report.md"), "w") as f:
        f.write("\n".join(L) + "\n")
    with open(os.path.join(out, "undetected.md"), "w") as f:
        f.write("\n".join(U) + "\n")
    with open(os.path.join(out, "faults_summary.json"), "w") as f:
        json.dump(dict(blocks=summary, meta=metas), f, indent=1)
    print(f"report: {len(allrows)} faults -> {out}/faults_report.md, undetected.md ({len(bad)}), faults.csv, faults_summary.json")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--config", choices=sorted(F.CONFIGS), required=True)
    r.add_argument("--n", type=int, default=200)
    r.add_argument("--seed", type=int, default=1)
    r.add_argument("--workers", type=int, default=1)
    r.add_argument("--budget-s", type=float, default=0)
    r.add_argument("--weights", default=os.path.join(HERE, "weights.json"))
    r.add_argument("--out", required=True)
    p = sub.add_parser("report")
    p.add_argument("out")
    a = sub.add_parser("all")
    a.add_argument("--n-tile", type=int, default=200)
    a.add_argument("--n-core", type=int, default=200)
    a.add_argument("--seed", type=int, default=1)
    a.add_argument("--workers", type=int, default=1)
    a.add_argument("--budget-min", type=float, default=0)
    a.add_argument("--weights", default=os.path.join(HERE, "weights.json"))
    a.add_argument("--out", required=True)
    args = ap.parse_args()
    if args.cmd == "run":
        run(args.config, args.n, args.seed, args.workers, args.budget_s, os.path.abspath(args.out), args.weights)
    elif args.cmd == "report":
        report(os.path.abspath(args.out))
    else:
        out = os.path.abspath(args.out)
        total = max(1, args.n_tile + args.n_core)
        b = args.budget_min * 60
        # the core runs ~3x faster per fault than the tile: the budget split by the expected cost (core 1, tile 3)
        bc = b * args.n_core / (args.n_core + 3 * args.n_tile) if b else 0
        if args.n_core:
            run("core", args.n_core, args.seed, args.workers, bc, out, args.weights)
        bt = (b - (time.time() - T0)) if b else 0
        if args.n_tile:
            run("tile", args.n_tile, args.seed + 1, args.workers, max(60.0, bt) if b else 0, out, args.weights)
        report(out)
        _ = total


T0 = time.time()

if __name__ == "__main__":
    main()
