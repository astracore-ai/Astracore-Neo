#!/usr/bin/env python3
"""report.py -- pnr_<block>.md from one block's logs of the place-and-route job (drop 0.42, stream A2b).

Reads what run.py collected into ci_logs/pnr/<block>/: run.json (the stages, their exit codes and times), in/prep.json
(the subtree, synth.py's per-unit figures, the macros), in/synth_sta/ (the prepared netlist flat in OpenSTA: the
synthesized estimate on the very netlist ORFS placed), orfs/logs/ (every ORFS step's log -- its last line has the
elapsed time and peak memory -- and metrics JSON), orfs/reports/neo/ (neo_report.tcl on the final database: slacks,
minimum periods, worst paths, skew, power, the physical counts). Writes pnr_<block>.md and metrics.json (every ORFS
metric merged, for the next reader) beside them. Partial runs are reported as far as they got.

Usage: report.py --logs ci_logs/pnr/<block> --block <block> [--out <file.md>]
"""
import argparse
import glob
import json
import math
import os
import re
import sys

STEP_ORDER = ["1_1_yosys_canonicalize", "1_2_yosys", "1_synth", "2_1_floorplan", "2_2_floorplan_macro", "2_3_floorplan_tapcell",
              "2_4_floorplan_pdn", "3_1_place_gp_skip_io", "3_2_place_iop", "3_3_place_gp", "3_4_place_resized",
              "3_5_place_dp", "4_1_cts", "5_1_grt", "5_2_route", "5_3_fillcell", "6_1_fill", "6_report", "7_neo_report"]


def read(path, default=""):
    try:
        with open(path, errors="replace") as f:
            return f.read()
    except OSError:
        return default


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def kv(path):
    """`key value...` lines -> {key: value string}."""
    d = {}
    for line in read(path).splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            d[parts[0]] = parts[1].strip()
    return d


def num(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def fmt(x, nd=0, unit=""):
    v = num(x)
    if v is None:
        return "n/a"
    s = f"{v:,.{nd}f}"
    return s + (f" {unit}" if unit else "")


def min_period(path):
    """report_clock_min_period: `clk period_min = 1234.56 fmax = 810.00`."""
    m = re.search(r"period_min\s*=\s*([-0-9.eE+]+)\s+fmax\s*=\s*([-0-9.eE+]+)", read(path))
    return (float(m.group(1)), float(m.group(2))) if m else (None, None)


def worst_path(path):
    """The first path of a report_checks: start, end, arrival, required, slack, the stage count."""
    txt = read(path)
    m = re.search(r"Startpoint:\s*(\S+)(?:\s*\(([^)]*)\))?.*?Endpoint:\s*(\S+)(?:\s*\(([^)]*)\))?", txt, re.S)
    if not m:
        return None
    r = dict(start=m.group(1), start_kind=m.group(2) or "", end=m.group(3), end_kind=m.group(4) or "")
    a = re.search(r"\n\s*([-0-9.]+)\s+data arrival time", txt)
    q = re.search(r"\n\s*([-0-9.]+)\s+data required time", txt)
    s = re.search(r"\n\s*([-0-9.]+)\s+slack \((VIOLATED|MET)\)", txt)
    r["arrival"] = float(a.group(1)) if a else None
    r["required"] = float(q.group(1)) if q else None
    r["slack"] = float(s.group(1)) if s else None
    first = txt[m.start():s.end() if s else len(txt)]
    r["cells"] = len(re.findall(r"\(([A-Za-z0-9_]+_ASAP7_75t_[RLS]+|fakeram7_\w+)\)", first))
    return r


def step_times(logdir):
    """Every ORFS step log's `Elapsed time: H:MM:SS.ff ... Peak memory: NKB.` -> {step: (seconds, MB)}."""
    out = {}
    for p in glob.glob(os.path.join(logdir, "*.log")):
        step = os.path.basename(p)[:-4]
        txt = read(p)
        tot, peak = 0.0, 0.0
        for m in re.finditer(r"Elapsed time:\s*([0-9:.]+)\[h:\]min:sec\..*?Peak memory:\s*(\d+)KB", txt):
            parts = [float(x) for x in m.group(1).split(":")]
            secs = sum(v * 60 ** i for i, v in enumerate(reversed(parts)))
            tot += secs
            peak = max(peak, int(m.group(2)) / 1024.0)
        if tot or peak:
            out[step] = (tot, peak)
    return out


def congestion(logdir):
    """FastRoute's final congestion report in 5_1_grt.log: the Total line (usage %, overflow H / V / total)."""
    txt = read(os.path.join(logdir, "5_1_grt.log"))
    idx = txt.rfind("Final congestion report")
    if idx < 0:
        return None
    seg = txt[idx:idx + 4000]
    m = re.search(r"\n\s*Total\s+(\d+)\s+(\d+)\s+([0-9.]+)%\s+(\d+)\s*/\s*(\d+)\s*/\s*(\d+)", seg)
    if not m:
        return None
    return dict(resource=int(m.group(1)), demand=int(m.group(2)), usage_pct=float(m.group(3)),
                overflow_h=int(m.group(4)), overflow_v=int(m.group(5)), overflow=int(m.group(6)))


def metric(metrics, *patterns):
    """The first metric whose name matches one of the regexes (searched in order)."""
    for pat in patterns:
        for k in sorted(metrics):
            if re.fullmatch(pat, k):
                return metrics[k]
    return None


def power(path):
    """report_power's Total line: internal, switching, leakage, total (W) and the percentage."""
    m = re.search(r"\nTotal\s+([-0-9.eE+]+)\s+([-0-9.eE+]+)\s+([-0-9.eE+]+)\s+([-0-9.eE+]+)", read(path))
    if not m:
        return None
    return dict(internal=float(m.group(1)), switching=float(m.group(2)), leakage=float(m.group(3)), total=float(m.group(4)))


def skew(path):
    """report_clock_skew -setup / -hold: the `<value> setup skew` and `<value> hold skew` lines, in that order."""
    return [float(v) for v, _ in re.findall(r"\n\s*([-0-9.]+)\s+(setup|hold) skew", read(path))]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--logs", required=True)
    ap.add_argument("--block", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    L = args.logs
    block = args.block
    run = load_json(os.path.join(L, "run.json")) or {}
    prep = load_json(os.path.join(L, "in", "prep.json")) or {}
    macros = (load_json(os.path.join(L, "in", "macros", "macros.json")) or {}).get("macros", [])
    ologs = os.path.join(L, "orfs", "logs")
    neo = os.path.join(L, "orfs", "reports", "neo")
    metrics = {}
    for p in sorted(glob.glob(os.path.join(ologs, "*.json"))):
        j = load_json(p)
        if isinstance(j, dict):
            metrics.update({k: v for k, v in j.items() if not isinstance(v, (dict, list))})
    with open(os.path.join(L, "metrics.json"), "w") as f:
        json.dump(metrics, f, indent=1, sort_keys=True)
    summ = kv(os.path.join(neo, "summary.txt"))
    phys = kv(os.path.join(neo, "physical.txt"))
    r_mp, r_fmax = min_period(os.path.join(neo, "min_period.txt"))
    r_mpp, _ = min_period(os.path.join(neo, "min_period_ports.txt"))
    r_path = worst_path(os.path.join(neo, "paths_max.txt"))
    r_r2r = worst_path(os.path.join(neo, "paths_reg2reg.txt"))
    r_hold = worst_path(os.path.join(neo, "paths_min.txt"))
    sdir = os.path.join(L, "in", "synth_sta")
    s_summ = kv(os.path.join(sdir, "summary.txt"))
    s_mp, s_fmax = min_period(os.path.join(sdir, "min_period.txt"))
    s_mpp, _ = min_period(os.path.join(sdir, "min_period_ports.txt"))
    s_path = worst_path(os.path.join(sdir, "paths_max.txt"))
    s_r2r = worst_path(os.path.join(sdir, "paths_reg2reg.txt"))
    times = step_times(ologs)
    cong = congestion(ologs)
    pw = power(os.path.join(neo, "power.txt"))
    sk = skew(os.path.join(neo, "skew.txt"))
    period = prep.get("period_ps")
    stages = run.get("stages", [])
    status = run.get("status", "no run.json")

    # the figures, from neo_report first, ORFS's metrics where it has none
    die = phys.get("die_um", "").split()
    core = phys.get("core_um", "").split()
    core_area = num(phys.get("core_area_um2")) or num(metric(metrics, r"finish__design__core__area", r".*__design__core__area"))
    std_area = num(phys.get("stdcell_area_um2"))
    mac_area = num(phys.get("macro_area_um2")) or 0.0
    tie_area = num(phys.get("tie_area_um2")) or 0.0
    util = None
    if core_area and std_area is not None:
        util = 100.0 * (std_area + mac_area + tie_area + (num(phys.get("tap_area_um2")) or 0.0)) / core_area
    util_m = num(metric(metrics, r"finish__design__instance__utilization", r"detailedroute__design__instance__utilization",
                        r"globalroute__design__instance__utilization"))
    if util_m is not None and util_m <= 1.0:          # ORFS's metric is a fraction
        util_m *= 100.0
    place_cells = metric(metrics, r"detailedplace__design__instance__count__stdcell", r"placeopt__design__instance__count__stdcell",
                         r"globalplace__design__instance__count__stdcell")
    wl = num(metric(metrics, r"detailedroute__route__wirelength")) or num(phys.get("wire_length_um"))
    drc = metric(metrics, r"detailedroute__route__drc_errors", r"finish__route__drc_errors")
    ant = metric(metrics, r"detailedroute__antenna__violating__nets", r"finish__antenna__violating__nets")
    diodes = metric(metrics, r"detailedroute__antenna_diodes_count", r"globalroute__antenna_diodes_count")
    synth_cells = (prep.get("yosys_stat") or {}).get("design", {})
    tunit = summ.get("time_unit", "")                  # OpenSTA's user time unit: the first liberty's (ASAP7: 1ps)
    to_ps = 1000.0 if tunit.endswith("ns") else 1.0

    def ps(v):
        v = num(v)
        return None if v is None else v * to_ps

    ratio = (r_mp / s_mp) if (r_mp and s_mp) else None
    ratio_arr = (r_r2r["arrival"] / s_r2r["arrival"]) if (r_r2r and s_r2r and r_r2r.get("arrival") and s_r2r.get("arrival")) else None

    md = [f"# pnr_{block} -- `{block}` placed and routed on ASAP7 through OpenROAD-flow-scripts",
          "",
          f"Generated by `07_ci/pnr/report.py` from the `pnr` job's logs ({run.get('start', '?')}; image `{run.get('image') or '?'}`; "
          f"make arguments `{run.get('flow_args') or 'none'}`). Status: **{status}**. The netlist is `synth.py`'s for the unit's "
          f"subtree ({', '.join(prep.get('units', [])) or '?'}) at {fmt(period)} ps on ASAP7 {prep.get('corner', 'SS')} "
          f"(ORFS CORNER = WC, the same NLDM files), the memory leaves as FakeRAM2.0 macros; ORFS's defaults otherwise "
          "(`07_ci/pnr/designs/neo_common.mk`). ASAP7 is a predictive academic PDK: the picoseconds and square microns are the "
          "library's, the ratios and the paths the design's.",
          "",
          "| Figure | Value |",
          "| --- | --- |",
          f"| die / core | {fmt(die[0] if die else None, 2)} x {fmt(die[1] if len(die) > 1 else None, 2)} um / "
          f"{fmt(core[0] if core else None, 2)} x {fmt(core[1] if len(core) > 1 else None, 2)} um "
          f"({fmt(phys.get('die_area_um2'))} / {fmt(core_area)} um2) |",
          f"| utilization (cells + macros + taps over the core) | {fmt(util, 1)} % (ORFS's metric: {fmt(util_m, 1)} %) |",
          f"| cells after placement (ORFS metric) / final standard cells, macros, taps, tie, fill | {fmt(place_cells)} / "
          f"{fmt(phys.get('stdcell_count'))}, {fmt(phys.get('macro_count'))}, {fmt(phys.get('tap_count'))}, "
          f"{fmt(phys.get('tie_count'))}, {fmt(phys.get('filler_count'))} |",
          f"| cells in the synthesized netlist (prep.py) | {fmt(synth_cells.get('num_cells'))} "
          f"({fmt(synth_cells.get('stdcell_area_um2'))} um2 of cells, {fmt(synth_cells.get('macro_area_um2'))} um2 of macros) |",
          f"| standard-cell area after routing | {fmt(std_area)} um2 |",
          f"| routed wire length | {fmt(wl)} um |",
          f"| DRC violations after detailed routing / antenna-violating nets / diodes | {fmt(drc)} / {fmt(ant)} / {fmt(diodes)} |",
          f"| global-route congestion (usage, overflow H / V / total) | "
          + (f"{cong['usage_pct']:.2f} %, {cong['overflow_h']} / {cong['overflow_v']} / {cong['overflow']}" if cong else "n/a") + " |",
          f"| setup WNS / TNS (routed, {summ.get('parasitics', '?')}) | {fmt(ps(summ.get('wns_max')), 1)} / {fmt(ps(summ.get('tns_max')), 1)} ps "
          f"({fmt(summ.get('setup_violations'))} endpoints) |",
          f"| hold WNS / TNS | {fmt(ps(summ.get('wns_min')), 1)} / {fmt(ps(summ.get('tns_min')), 1)} ps ({fmt(summ.get('hold_violations'))} endpoints) |",
          f"| minimum period, register to register / with the I/O paths | {fmt(r_mp, 1)} / {fmt(r_mpp, 1)} ps "
          f"({fmt(r_fmax, 0)} MHz) |",
          f"| clock skew (setup, hold) | {', '.join(f'{v:.1f}' for v in sk) if sk else 'n/a'} ps |",
          f"| cells driving clock nets (CTS buffers, inverters) | {fmt(phys.get('clock_net_drivers'))} "
          f"({phys.get('clock_net_driver_masters', 'n/a')}) |",
          f"| power at the flow's default activity (internal / switching / leakage / total) | "
          + (f"{pw['internal'] * 1e3:.3f} / {pw['switching'] * 1e3:.3f} / {pw['leakage'] * 1e3:.4f} / **{pw['total'] * 1e3:.3f} mW**" if pw else "n/a")
          + " |",
          f"| max slew / cap / fanout violations | {fmt(summ.get('max_slew_violations'))} / {fmt(summ.get('max_cap_violations'))} / "
          f"{fmt(summ.get('max_fanout_violations'))} |",
          "",
          "Power: OpenSTA's default switching activity (the flow sets none); A3 replaces it with the activity the cocotb runs "
          "record (VCD / SAIF).",
          ""]
    if r_path:
        md += ["## The worst routed path", "",
               f"`{r_path['start']}` -> `{r_path['end']}`: arrival {fmt(r_path['arrival'], 1)} ps, required {fmt(r_path['required'], 1)} ps, "
               f"slack **{fmt(r_path['slack'], 1)} ps**, {r_path['cells']} cells (`orfs/reports/neo/paths_max.txt`: the ten worst endpoints)."]
        if r_r2r and (r_r2r["start"], r_r2r["end"]) != (r_path["start"], r_path["end"]):
            md += ["", f"Worst register to register: `{r_r2r['start']}` -> `{r_r2r['end']}`, arrival {fmt(r_r2r['arrival'], 1)} ps, "
                       f"slack {fmt(r_r2r['slack'], 1)} ps."]
        if r_hold:
            md += ["", f"Worst hold: `{r_hold['start']}` -> `{r_hold['end']}`, slack {fmt(r_hold['slack'], 1)} ps."]
        md.append("")
    md += ["## Routed against synthesized", "",
           "The synthesized estimate is the netlist ORFS placed (prep.py's) flat in OpenSTA with the same SDC, ideal clock and "
           "no wires (`in/synth_sta/`); the routed figure has the propagated clock tree and the OpenRCX parasitics. The ratio "
           "routed / synthesized is the factor the synthesis report's paths carry to silicon on this flow.",
           "",
           "| | synthesized | routed | routed / synthesized |",
           "| --- | --- | --- | --- |",
           f"| minimum period, register to register | {fmt(s_mp, 1)} ps | {fmt(r_mp, 1)} ps | {('%.3f' % ratio) if ratio else 'n/a'} |",
           f"| minimum period with the I/O paths | {fmt(s_mpp, 1)} ps | {fmt(r_mpp, 1)} ps | "
           f"{('%.3f' % (r_mpp / s_mpp)) if (r_mpp and s_mpp) else 'n/a'} |",
           f"| setup WNS at {fmt(period)} ps | {fmt(s_summ.get('wns_max'), 1)} ps | {fmt(ps(summ.get('wns_max')), 1)} ps | |",
           f"| worst register-to-register arrival | {fmt(s_r2r['arrival'] if s_r2r else None, 1)} ps | "
           f"{fmt(r_r2r['arrival'] if r_r2r else None, 1)} ps | {('%.3f' % ratio_arr) if ratio_arr else 'n/a'} |",
           ""]
    if s_path:
        md += [f"Synthesized worst path: `{s_path['start']}` -> `{s_path['end']}`, arrival {fmt(s_path['arrival'], 1)} ps, "
               f"slack {fmt(s_path['slack'], 1)} ps.", ""]
    su = prep.get("synth") or {}
    if su:
        md += ["`synth.py`'s own figures for the units of the block (each unit with its children as extracted timing models, "
               "the job's per-unit view):", "",
               "| unit | instances in the block | cells (own) | WNS | minimum period | worst path |",
               "| --- | --- | --- | --- | --- | --- |"]
        for u in prep.get("units", []):
            r = su.get(u, {})
            md.append(f"| `{u}` | {r.get('instances', '?')} | {fmt(r.get('own_cells'))} | {fmt(r.get('wns_ps'), 1)} ps | "
                      f"{fmt(r.get('min_period_ps'), 1)} ps | `{r.get('worst_start', '?')}` -> `{r.get('worst_end', '?')}` |")
        worst_unit = max((su[u].get("min_period_ps") or 0.0, u) for u in su)
        md += ["", f"The subtree's slowest unit by synth.py: `{worst_unit[1]}` at {fmt(worst_unit[0], 1)} ps"
               + (f"; routed / that = {r_mp / worst_unit[0]:.3f}." if (r_mp and worst_unit[0]) else "."), ""]
    if macros:
        md += ["## Macros (FakeRAM2.0, `07_ci/pnr/fakeram.py`)", "",
               "| leaf (synth.py) | macro | words x bits, groups | pins | LEF size | LEF area | um2 / bit | synthesis model (memories.md) |",
               "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for m in macros:
            md.append(f"| `{m['leaf']}` | `{m['macro']}` | {m['D']} x {m['W']}, G = {m['G']} | {m['pins']} | "
                      f"{m['lef_width_um']:.2f} x {m['lef_height_um']:.2f} um{' (pin-limited)' if m.get('pin_limited') else ''} | "
                      f"{m['lef_area_um2']:,.1f} um2 | {m['um2_per_bit']:.4f} | {m['synth_model_um2']:,.1f} um2 (x{m['synth_factor']}) |")
        md.append("")
    md += ["## Stages", "", "| ORFS step | elapsed | peak memory |", "| --- | --- | --- |"]
    tot = 0.0
    for step in sorted(times, key=lambda s: (STEP_ORDER.index(s) if s in STEP_ORDER else 99, s)):
        secs, mb = times[step]
        tot += secs
        md.append(f"| {step} | {secs / 60:.1f} min | {mb:,.0f} MB |")
    md += [f"| **total of the steps** | **{tot / 60:.1f} min** | |", "",
           "The job's own stages (`run.json`): " + "; ".join(
               f"{s['stage']} {('exit ' + str(s.get('exit'))) if s.get('exit') is not None else 'not run'}"
               + (f" in {s['seconds'] / 60:.1f} min" if s.get("seconds") else "") + (f" ({s['note']})" if s.get("note") else "")
               for s in stages) + ".", ""]
    out = args.out or os.path.join(L, f"pnr_{block}.md")
    with open(out, "w") as f:
        f.write("\n".join(md) + "\n")
    print(f"report.py: {out} ({status})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
