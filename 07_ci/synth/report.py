#!/usr/bin/env python3
"""report.py -- synth_report.md from synth.py's synth_results.json (drop 0.40), and the per-unit files the CI job publishes
(the worst paths, the stat, the SDC, the macro liberty) copied beside it. Drop 0.42: the period sweep's table and verdict
when the run swept, and a second mode for two libraries side by side.

    python3 synth/report.py synth_out [--out ci_logs/synth]
    python3 synth/report.py --compare synth_out synth_out_nangate45 --out ci_logs/synth     (synth_report_libraries.md)
"""
import argparse
import json
import os
import shutil
import sys

KEEP = ["paths_max.txt", "paths_min.txt", "stat.txt", "summary.txt", "min_period.txt", "drv.txt", "block.sdc", "check.txt",
        "sta.log", "yosys.out", "synth.ys", "etm.lib", "sweep.log"]


def f1(x, nd=1):
    return "—" if x is None else f"{x:,.{nd}f}"


def f0(x):
    return "—" if x is None else f"{x:,.0f}"


def pct(a, b):
    return "—" if not b else f"{100.0 * a / b:.1f} %"


def multiplicities(root, units):
    """{unit: instances under root} through the children counts."""
    mult = {}

    def walk(n, k):
        mult[n] = mult.get(n, 0) + k
        for c, cnt in (units.get(n, {}).get("children") or {}).items():
            walk(c, k * cnt)
    walk(root, 1)
    return mult


def worst_paths(udir, n=2):
    out = []
    try:
        txt = open(os.path.join(udir, "paths_max.txt")).read()
    except FileNotFoundError:
        return out
    for chunk in txt.split("Startpoint:")[1:]:
        lines = chunk.split("\n")
        start = lines[0].split()[0]
        end = arrival = slack = None
        for l in lines:
            ls = l.strip()
            if ls.startswith("Endpoint:"):
                end = ls.split()[1]
            if ls.endswith("data arrival time") and arrival is None:
                try:
                    arrival = float(ls.split()[0])
                except ValueError:
                    pass
            if "slack (" in ls:
                try:
                    slack = float(ls.split()[0])
                except ValueError:
                    pass
        out.append((start, end, arrival, slack))
        if len(out) >= n:
            break
    return out


def tag(p):
    return f"{int(round(p))}"


def verdict_lines(res):
    """The sweep's verdict as report lines (empty without a sweep)."""
    v = res.get("verdict")
    if not v or not res.get("sweep_ps"):
        return []
    per = ", ".join(tag(p) for p in res["sweep_ps"])
    L = []
    if v.get("clean_period_ps") is not None:
        L.append(f"**Period sweep ({per} ps):** every unit of the tile outside the PE-limited ones ({', '.join(v['pe_limited'])}) is clean "
                 f"from **{v['clean_period_ps']:,.0f} ps**; the PE's own stage (`mac_pe`'s minimum period) is **{f0(v.get('pe_stage_ps'))} ps**; "
                 f"the tile on this proxy: **{f0(v.get('tile_period_ps'))} ps = {f0(v.get('tile_fmax_mhz'))} MHz** (the larger of the two).")
    else:
        L.append(f"**Period sweep ({per} ps):** no swept period has every unit outside the PE-limited ones clean; still violating at "
                 f"{tag(res['sweep_ps'][-1])} ps: {', '.join(v['violating_by_period'].get(tag(res['sweep_ps'][-1]), []))}.")
    w = v.get("worst_other_min_period") or {}
    stages = v.get("pe_limited_stages") or {}
    L.append("")
    L.append(f"The largest minimum period outside the PE-limited units: `{w.get('unit')}` {f0(w.get('ps'))} ps (register to register; "
             f"the sweep also counts the I/O paths at 0.2 of the period). The PE-limited units' own minimum periods: "
             + ", ".join(f"`{n}` {f0(p)}" for n, p in stages.items() if p) + " ps.")
    t = v.get("top")                                    # drop 0.44: the second verdict, over the chip top's own units
    if t:
        tw = t.get("worst_other_min_period") or {}
        last = tag(res["sweep_ps"][-1])
        L.append("")
        if t.get("clean_period_ps") is not None:
            L.append(f"**The chip top's units** ({', '.join(f'`{n}`' for n in t['judged'])}; `{'`, `'.join(t['pe_limited'])}` PE-limited "
                     f"through the core): clean from **{t['clean_period_ps']:,.0f} ps**; the largest minimum period among them "
                     f"`{tw.get('unit')}` {f0(tw.get('ps'))} ps; the chip on this proxy: **{f0(t.get('chip_period_ps'))} ps** "
                     f"(the larger of the tile's period and this).")
        else:
            L.append(f"**The chip top's units** ({', '.join(f'`{n}`' for n in t['judged'])}): no swept period has every one clean; "
                     f"still violating at {last} ps: {', '.join(t['violating_by_period'].get(last, []))}; the largest minimum period "
                     f"among them `{tw.get('unit')}` {f0(tw.get('ps'))} ps.")
    return L


def sweep_table(res, units, order):
    """unit x period: violating endpoints of all (WNS)."""
    periods = res.get("sweep_ps") or []
    if not periods:
        return []
    v = res.get("verdict") or {}
    lim = set(v.get("pe_limited") or []) | set((v.get("top") or {}).get("pe_limited") or [])
    L = ["## Period sweep", "",
         "Violating endpoints of all (WNS in ps) per unit and clock period, from one OpenSTA session per unit reading each period's "
         "constraints in turn (the children's timing models do not depend on the period; the I/O at 0.2 of the period, the "
         "uncertainty 0.08, the tile's links 0.4); the minimum period is the unit's register-to-register stage, the same at "
         "every period. PE-limited units are marked †; the verdict above is over the other units of the tile, its second line "
         "(drop 0.44) over the chip top's own units.", "",
         "| Unit | Min period ps | " + " | ".join(f"{tag(p)} ps" for p in periods) + " |",
         "| --- | --- | " + " | ".join("---" for _ in periods) + " |"]
    for n in order:
        r = units.get(n, {})
        sw = (r.get("sweep") or {}).get("periods") or {}
        if not sw:
            continue
        cells = []
        for p in periods:
            e = sw.get(tag(p)) or {}
            if e.get("endpoints") is None:
                cells.append("—")
            elif e.get("endpoints_violating", 0) == 0:
                cells.append(f"0 of {e['endpoints']:,} ({f0(e.get('wns_ps'))})")
            else:
                cells.append(f"**{e['endpoints_violating']:,}** of {e['endpoints']:,} ({f0(e.get('wns_ps'))})")
        mp = (r.get("sta") or {}).get("min_period_ps")
        L.append(f"| `{n}`{' †' if n in lim else ''} | {f0(mp)} | " + " | ".join(cells) + " |")
    L.append("")
    return L


def compare(dirs, out):
    """synth_report_libraries.md: two (or more) runs side by side -- the tool line naming every library, per unit the
    minimum period, cells and area per library and the ratios, the verdicts."""
    runs = []
    for d in dirs:
        with open(os.path.join(os.path.abspath(d), "synth_results.json")) as f:
            runs.append(json.load(f))
    a = runs[0]
    both = " and ".join("%s %s" % (r["pdk"], r["corner"]) for r in runs)
    L = [f"# Synthesis report — {both} side by side ({a['date']})", ""]
    libs = "; ".join("%s %s: %s" % (r["pdk"], r["corner"], ", ".join(os.path.basename(l) for l in r["libs"])) for r in runs)
    times = ", ".join("%s %.1f min" % (r["pdk"], r["seconds"] / 60) for r in runs)
    L.append(f"Tools: {a['tools'].get('yosys')} (slang front end); OpenSTA {a['tools'].get('sta')}. Libraries: {libs}. "
             f"Run times {times}.")
    L.append("")
    for r in runs:
        L.append(f"Macro model, {r['pdk']}: {r['macro_model']['note']}. {r['ratio_note']}.")
        L.append("")
    for r in runs:
        vl = verdict_lines(r)
        if vl:
            L.append(f"**{r['pdk']}** — " + vl[0])
            L.append("")
    names = [n for n in a.get("order", []) if all(n in r["units"] for r in runs)]
    hdr = "| Unit | " + " | ".join(f"Min period ps {r['pdk']}" for r in runs) + " | ratio | " + \
          " | ".join(f"Cells {r['pdk']}" for r in runs) + " | " + " | ".join(f"Total area µm² {r['pdk']}" for r in runs) + " |"
    L += ["## Units on both libraries", "",
          "Minimum period (register to register, a child's arcs included), cells and total area (cells + macros, the unit with its "
          "subtree) per library; the ratio is the second library's period over the first's.", "", hdr,
          "| --- | " + " | ".join("---" for _ in runs) + " | --- | " + " | ".join("---" for _ in range(2 * len(runs))) + " |"]
    for n in names:
        mps = [((r["units"][n].get("sta") or {}).get("min_period_ps")) for r in runs]
        ratio = f"{mps[1] / mps[0]:.2f}" if len(mps) > 1 and mps[0] and mps[1] else "—"
        L.append(f"| `{n}` | " + " | ".join(f0(m) for m in mps) + f" | {ratio} | " +
                 " | ".join(f0(r["units"][n].get("cells")) for r in runs) + " | " +
                 " | ".join(f0(r["units"][n].get("total_area_um2")) for r in runs) + " |")
    L.append("")
    with open(os.path.join(out, "synth_report_libraries.md"), "w") as f:
        f.write("\n".join(L) + "\n")
    print(os.path.join(out, "synth_report_libraries.md"))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("synth_out", nargs="?")
    ap.add_argument("--out", default=None)
    ap.add_argument("--compare", nargs="+", default=None, help="two or more synth outputs: the libraries side by side")
    ap.add_argument("--lite", action="store_true", help="copy no timing models (etm.lib) beside the report (the second library in CI)")
    args = ap.parse_args()
    if args.compare:
        out = os.path.abspath(args.out or ".")
        os.makedirs(out, exist_ok=True)
        return compare(args.compare, out)
    src = os.path.abspath(args.synth_out)
    out = os.path.abspath(args.out or src)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(src, "synth_results.json")) as f:
        res = json.load(f)
    units = res["units"]
    order = [n for n in res.get("order", list(units)) if n in units]
    L = []
    L.append(f"# Synthesis report — {res['pdk']} {res['corner']}, {res['period_ps']:.0f} ps clock ({res['date']})")
    L.append("")
    L.append(f"Tools: {res['tools'].get('yosys')} (slang front end); OpenSTA {res['tools'].get('sta')}. "
             f"Libraries: {', '.join(os.path.basename(l) for l in res['libs'])}. Run time {res['seconds'] / 60:.1f} min.")
    L.append("")
    vl = verdict_lines(res)
    if vl:
        L += vl + [""]
    L.append(f"Macro model for the black-boxed memory leaves (`neo_mem_sp` / `neo_mem_rf`): {res['macro_model']['note']}; area factors "
             f"{res['macro_area_factor']} (register-file style reads cost bit-cell area). {res['ratio_note']}. Every figure here is a proxy "
             "for the N5A target: the paths and the shares are the design's, the absolute picoseconds and square microns are the open "
             "library's. A unit is one module synthesized at its silicon parameters with its children black-boxed; 'with subtree' sums the "
             "children in (instances × unit).")
    L.append("")
    # ---- gates
    L.append("## Gates")
    L.append("")
    L.append("| Unit | Synthesized | Latches | Multiple drivers | Hierarchy check | PE count | DV_HOOKS = 0 | ICG cells (own) |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for n in order:
        r = units[n]
        if r.get("failed"):
            L.append(f"| `{n}` | **failed**: {r['failed']} | | | | | | |")
            continue
        chk = r.get("check") or {}
        pe = "—" if r.get("pe_expected") is None else (f"{r.get('pe_count')} = {r['pe_expected']} ✓" if r.get("pe_check") else f"**{r.get('pe_count')} ≠ {r['pe_expected']}**")
        dv = r.get("dv_hooks")
        if dv is None:
            dvs = "—"
        elif dv["passed"]:
            dvs = f"✓ ({len(dv['dv_inputs'])} in, {len(dv['dv_outputs'])} out)"
        else:
            dvs = f"**loads {dv['bad_loads'][:2]} outputs {dv['bad_outputs'][:2]}**"
        if n == "neo_tile" and r.get("dv_hooks_tree"):
            dvs += " — tree " + ("✓" if r["dv_hooks_tree"]["passed"] else f"**{r['dv_hooks_tree']['failing_units']}**")
        L.append(f"| `{n}` | ✓ | {'none' if not r.get('latches') else '**%d**' % r['latches']} | "
                 f"{'none' if not chk.get('multiple_drivers') else '**%d**' % chk['multiple_drivers']} | "
                 f"{'✓' if not r.get('hierarchy_problems') else '**' + '; '.join(r['hierarchy_problems']) + '**'} | {pe} | {dvs} | "
                 f"{r.get('icg_cells', 0)}{'' if not r.get('icg_unmapped') else ' **+%d unmapped**' % r['icg_unmapped']} |")
    L.append("")
    # ---- units
    L.append("## Units")
    L.append("")
    L.append("Own cells = the unit's library cells; cells, flops and area with its subtree (one instance of the unit); macro area from the model; "
             "clock-gated flops (drop 0.43) = the flops of the subtree whose clock pin an ICG cell drives, a child's all when its clk is a gated "
             "clock, and the ICG cells of the subtree. "
             f"Timing is the unit's own logic with its child units as extracted timing models (a child's inside is the child's row): WNS against the "
             f"{res['period_ps']:.0f} ps clock; minimum period from OpenSTA's `report_clock_min_period` (register-to-register paths of the clock, a "
             "child's clock-to-output and setup arcs included); violating endpoints of the unit's timing endpoints; the Yosys time of the unit alone.")
    L.append("")
    L.append("| Unit | Own cells | Cells | Flops | Clock-gated flops | ICG cells | Cell area µm² | Macro area µm² | Total area µm² | WNS ps | Min period ps (Fmax) | Violating endpoints | Yosys s |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for n in order:
        r = units[n]
        if r.get("failed"):
            continue
        sta = r.get("sta") or {}
        mp = sta.get("min_period_ps")
        fmax = sta.get("fmax_mhz")
        eps = sta.get("endpoints")
        viol = sta.get("endpoints_violating")
        L.append(f"| `{n}` | {f0(r.get('own_cells'))} | {f0(r.get('cells'))} | {f0(r.get('flops'))} | "
                 f"{f0(r.get('gated_flops'))} ({pct(r.get('gated_flops') or 0, r.get('flops'))}) | {f0(r.get('icgs'))} | {f0(r.get('cell_area_um2'))} | "
                 f"{f0(r.get('macro_area_um2'))} | {f0(r.get('total_area_um2'))} | {f0(sta.get('wns_max_ps'))} | "
                 f"{(f0(mp) + (f' ({fmax:,.0f} MHz)' if fmax else '')) if mp else '—'} | "
                 f"{(f'{viol:,} of {eps:,}') if eps else '—'} | {f0(r.get('synth_seconds'))} |")
    L.append("")
    # ---- worst paths
    L.append("## Worst paths")
    L.append("")
    L.append("| Unit | Start | End | Arrival ps | Slack ps |")
    L.append("| --- | --- | --- | --- | --- |")
    tu = res.get("time_unit_ps") or (1000.0 if res.get("pdk") == "nangate45" else 1.0)     # paths_max.txt is in the library's unit
    for n in order:
        if units[n].get("failed"):
            continue
        for s, e, a, sl in worst_paths(os.path.join(src, n), 2):
            L.append(f"| `{n}` | `{s}` | `{e}` | {f1(None if a is None else a * tu, 0)} | {f1(None if sl is None else sl * tu, 0)} |")
    L.append("")
    L += sweep_table(res, units, order)
    # ---- the tile
    tile = units.get("neo_tile")
    if tile and not tile.get("failed") and "total_area_um2" in tile:
        total = tile["total_area_um2"]
        L.append("## The tile: area by unit")
        L.append("")
        L.append(f"Total {total:,.0f} µm² = {total / 1e6:.3f} mm² (cells {tile['cell_area_um2']:,.0f} µm², macros {tile['macro_area_um2']:,.0f} µm²); "
                 f"{tile['cells']:,} cells, {tile['flops']:,} flops, {tile['pe_count']} PEs.")
        L.append("")
        L.append("| Unit | Instances in the tile | Own cells (one) | Area µm² (one, with subtree) | Area µm² (all instances, own logic + own macros only) | Share of the tile |")
        L.append("| --- | --- | --- | --- | --- | --- |")
        mult = multiplicities("neo_tile", units)
        rows = []
        for n, k in mult.items():
            r = units.get(n, {})
            if "own_area_um2" not in r:
                continue
            own_macros = sum(m["area_um2"] * m["instances"] for m in (r.get("macros") or {}).values())
            own_total = (r["own_area_um2"] + own_macros) * k
            rows.append((own_total, n, k, r))
        rows.sort(reverse=True)
        for own_total, n, k, r in rows:
            L.append(f"| `{n}` | {k:,} | {f0(r.get('own_cells'))} | {f0(r.get('total_area_um2'))} | {f0(own_total)} | {pct(own_total, total)} |")
        L.append("")
        macros = {}
        for n, k in mult.items():
            for m, rec in (units.get(n, {}).get("macros") or {}).items():
                e = macros.setdefault(m, dict(rec, instances=0))
                e["instances"] += rec["instances"] * k
        if macros:
            kinds = {"neo_mem_sp": "1W1R (registered read)", "neo_mem_rf": "1W + asynchronous read (port A)",
                     "neo_mem_rf_b": "1W1R (registered read, port B)", "neo_mem_rf_rb": "1W + port A + port B"}
            L.append("| Macro | Instances in the tile | Word × depth | Bits (all) | Area µm² (all) | Ports used | Model factor |")
            L.append("| --- | --- | --- | --- | --- | --- | --- |")
            for m, e in sorted(macros.items(), key=lambda kv: -kv[1]["bits"] * kv[1]["instances"]):
                L.append(f"| `{m}` | {e['instances']} | {e['W']} × {e['D']} | {e['bits'] * e['instances']:,} | {e['area_um2'] * e['instances']:,.0f} | "
                         f"{kinds.get(e.get('kind'), '—')} | {e['factor']} |")
            L.append("")
        for n in ("neo_tile", "neo_core"):
            sta = (units.get(n) or {}).get("sta") or {}
            if sta.get("slack_histogram"):
                L.append(f"Endpoint slack histogram of `{n}` (its own registers and its children's inputs; bins of a tenth of the period; {sta.get('endpoints', 0):,} endpoints, {sta.get('endpoints_violating', 0):,} violating):")
                L.append("")
                L.append("| Slack | Endpoints |")
                L.append("| --- | --- |")
                for k, v in sta["slack_histogram"].items():
                    L.append(f"| {k} | {v:,} |")
                L.append("")
    with open(os.path.join(out, "synth_report.md"), "w") as f:
        f.write("\n".join(L) + "\n")
    for n in units:
        udir = os.path.join(src, n)
        if not os.path.isdir(udir):
            continue
        dst = os.path.join(out, n)
        os.makedirs(dst, exist_ok=True)
        for fn in KEEP:
            p = os.path.join(udir, fn)
            if os.path.isfile(p) and not (args.lite and fn == "etm.lib"):
                shutil.copy(p, dst)
    if os.path.isfile(os.path.join(src, "macros.lib")):
        shutil.copy(os.path.join(src, "macros.lib"), out)
    if out != src:
        shutil.copy(os.path.join(src, "synth_results.json"), out)
    print(os.path.join(out, "synth_report.md"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
