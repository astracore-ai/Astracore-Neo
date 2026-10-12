#!/usr/bin/env python3
"""report.py -- synth_report.md from synth.py's synth_results.json (drop 0.40), and the per-unit files the CI job publishes
(the worst paths, the stat, the SDC, the macro liberty) copied beside it.

    python3 synth/report.py synth_out [--out ci_logs/synth]
"""
import argparse
import json
import os
import shutil
import sys

KEEP = ["paths_max.txt", "paths_min.txt", "stat.txt", "summary.txt", "min_period.txt", "drv.txt", "block.sdc", "check.txt",
        "sta.log", "yosys.out", "synth.ys", "etm.lib"]


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("synth_out")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
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
    L.append(f"Macro model for the black-boxed memory leaves (`neo_mem_sp` / `neo_mem_rf`): {res['macro_model']['note']}; area factors "
             f"{res['macro_area_factor']} (register-file style reads cost bit-cell area). {res['ratio_note']}. Every figure here is a proxy "
             "for the N5A target: the paths and the shares are the design's, the absolute picoseconds and square microns are the open "
             "library's. A unit is one module synthesized at its silicon parameters with its children black-boxed; 'with subtree' sums the "
             "children in (instances × unit).")
    L.append("")
    # ---- gates
    L.append("## Gates")
    L.append("")
    L.append("| Unit | Synthesized | Latches | Multiple drivers | Hierarchy check | PE count | DV_HOOKS = 0 |")
    L.append("| --- | --- | --- | --- | --- | --- | --- |")
    for n in order:
        r = units[n]
        if r.get("failed"):
            L.append(f"| `{n}` | **failed**: {r['failed']} | | | | | |")
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
                 f"{'✓' if not r.get('hierarchy_problems') else '**' + '; '.join(r['hierarchy_problems']) + '**'} | {pe} | {dvs} |")
    L.append("")
    # ---- units
    L.append("## Units")
    L.append("")
    L.append("Own cells = the unit's library cells; cells, flops and area with its subtree (one instance of the unit); macro area from the model. "
             f"Timing is the unit's own logic with its child units as extracted timing models (a child's inside is the child's row): WNS against the "
             f"{res['period_ps']:.0f} ps clock; minimum period from OpenSTA's `report_clock_min_period` (register-to-register paths of the clock, a "
             "child's clock-to-output and setup arcs included); violating endpoints of the unit's timing endpoints; the Yosys time of the unit alone.")
    L.append("")
    L.append("| Unit | Own cells | Cells | Flops | Cell area µm² | Macro area µm² | Total area µm² | WNS ps | Min period ps (Fmax) | Violating endpoints | Yosys s |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for n in order:
        r = units[n]
        if r.get("failed"):
            continue
        sta = r.get("sta") or {}
        mp = sta.get("min_period_ps")
        fmax = sta.get("fmax_mhz")
        eps = sta.get("endpoints")
        viol = sta.get("endpoints_violating")
        L.append(f"| `{n}` | {f0(r.get('own_cells'))} | {f0(r.get('cells'))} | {f0(r.get('flops'))} | {f0(r.get('cell_area_um2'))} | "
                 f"{f0(r.get('macro_area_um2'))} | {f0(r.get('total_area_um2'))} | {f0(sta.get('wns_max_ps'))} | "
                 f"{(f0(mp) + (f' ({fmax:,.0f} MHz)' if fmax else '')) if mp else '—'} | "
                 f"{(f'{viol:,} of {eps:,}') if eps else '—'} | {f0(r.get('synth_seconds'))} |")
    L.append("")
    # ---- worst paths
    L.append("## Worst paths")
    L.append("")
    L.append("| Unit | Start | End | Arrival ps | Slack ps |")
    L.append("| --- | --- | --- | --- | --- |")
    for n in order:
        if units[n].get("failed"):
            continue
        for s, e, a, sl in worst_paths(os.path.join(src, n), 2):
            L.append(f"| `{n}` | `{s}` | `{e}` | {f1(a, 0)} | {f1(sl, 0)} |")
    L.append("")
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
            L.append("| Macro | Instances in the tile | Word × depth | Bits (all) | Area µm² (all) | Model factor |")
            L.append("| --- | --- | --- | --- | --- | --- |")
            for m, e in sorted(macros.items(), key=lambda kv: -kv[1]["bits"] * kv[1]["instances"]):
                L.append(f"| `{m}` | {e['instances']} | {e['W']} × {e['D']} | {e['bits'] * e['instances']:,} | {e['area_um2'] * e['instances']:,.0f} | {e['factor']} |")
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
            if os.path.isfile(p):
                shutil.copy(p, dst)
    if os.path.isfile(os.path.join(src, "macros.lib")):
        shutil.copy(os.path.join(src, "macros.lib"), out)
    if out != src:
        shutil.copy(os.path.join(src, "synth_results.json"), out)
    print(os.path.join(out, "synth_report.md"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
