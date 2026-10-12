#!/usr/bin/env python3
"""report.py -- pnr_<block>.md from one block's logs of the place-and-route job (drop 0.42, stream A2b).

Reads what run.py collected into ci_logs/pnr/<block>/: run.json (the stages, their exit codes and times), in/prep.json
(the subtree, synth.py's per-unit figures, the macros), in/synth_sta/ (the prepared netlist flat in OpenSTA: the
synthesized estimate on the very netlist ORFS placed), orfs/logs/ (every ORFS step's log -- its last lines carry the
elapsed time and peak memory -- and metrics JSON), orfs/reports/6_finish.rpt (ORFS's final report: OpenRCX
parasitics, propagated clock), and orfs/reports/neo/ when neo_report.tcl ran (the ten worst endpoints). Writes
pnr_<block>.md and metrics.json (every ORFS metric merged) beside them. Partial runs are reported as far as they got.

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
STAGE_OF = {"1": "synth (netlist read)", "2": "floorplan", "3": "place", "4": "cts", "5": "route", "6": "finish", "7": "neo_report"}


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


def ratio(a, b, nd=3):
    a, b = num(a), num(b)
    return f"{a / b:.{nd}f}" if (a is not None and b) else "n/a"


def min_period(text):
    """report_clock_min_period: `clk period_min = 1234.56 fmax = 810.00`."""
    m = re.search(r"period_min\s*=\s*([-0-9.eE+]+)\s+fmax\s*=\s*([-0-9.eE+]+)", text)
    return (float(m.group(1)), float(m.group(2))) if m else (None, None)


def worst_path(text):
    """The first path of a report_checks: start, end, arrival, required, slack, the cells on the data path."""
    m = re.search(r"Startpoint:\s*(\S+)(?:\s*\(([^)]*)\))?.*?Endpoint:\s*(\S+)(?:\s*\(([^)]*)\))?", text, re.S)
    if not m:
        return None
    r = dict(start=m.group(1), start_kind=m.group(2) or "", end=m.group(3), end_kind=m.group(4) or "")
    a = re.search(r"\n\s*([-0-9.]+)\s+data arrival time", text)
    q = re.search(r"\n\s*([-0-9.]+)\s+data required time", text)
    s = re.search(r"\n\s*([-0-9.]+)\s+slack \((VIOLATED|MET)\)", text)
    r["arrival"] = float(a.group(1)) if a else None
    r["required"] = float(q.group(1)) if q else None
    r["slack"] = float(s.group(1)) if s else None
    body = text[m.start():a.end() if a else len(text)]
    insts = re.findall(r"\s(\S+)/\S+ \(([A-Za-z0-9_]+)\)", body)
    data = [(i, c) for i, c in insts if not i.startswith(("clkbuf", "clkload", "clknet"))]
    r["cells"] = len({i for i, _ in data})
    return r


def orfs_sections(path):
    """ORFS's report_metrics file: `=====\\n<stage> <name>\\n-----\\n<body>` -> {name: body}."""
    out = {}
    for blk in re.split(r"\n=+\n", "\n" + read(path)):
        m = re.match(r"(\w+) (.+?)\n-+\n(.*)", blk, re.S)
        if m:
            out[m.group(2).strip()] = m.group(3)
    return out


def step_times(logdir):
    """Every ORFS step log's `Elapsed time: H:MM:SS.ff ... Peak memory: NKB.` -> {step: (seconds, MB)}."""
    out = {}
    for p in glob.glob(os.path.join(logdir, "*.log")):
        step = os.path.basename(p)[:-4]
        tot, peak = 0.0, 0.0
        for m in re.finditer(r"Elapsed time:\s*([0-9:.]+)\[h:\]min:sec\..*?Peak memory:\s*(\d+)KB", read(p)):
            parts = [float(x) for x in m.group(1).split(":")]
            tot += sum(v * 60 ** i for i, v in enumerate(reversed(parts)))
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
    m = re.search(r"\n\s*Total\s+(\d+)\s+(\d+)\s+([0-9.]+)%\s+(\d+)\s*/\s*(\d+)\s*/\s*(\d+)", txt[idx:idx + 6000])
    if not m:
        return None
    return dict(resource=int(m.group(1)), demand=int(m.group(2)), usage_pct=float(m.group(3)),
                overflow_h=int(m.group(4)), overflow_v=int(m.group(5)), overflow=int(m.group(6)))


def floorplan(logdir):
    """initialize_floorplan's Die BBox / Core BBox lines (um) in 2_1_floorplan.log."""
    txt = read(os.path.join(logdir, "2_1_floorplan.log"))
    out = {}
    for key in ("Die", "Core"):
        m = re.search(key + r" BBox:\s*\(\s*([-0-9.]+)\s+([-0-9.]+)\s*\)\s*\(\s*([-0-9.]+)\s+([-0-9.]+)\s*\)", txt)
        if m:
            x0, y0, x1, y1 = (float(v) for v in m.groups())
            out[key.lower()] = (x1 - x0, y1 - y0)
    return out


def cts(logdir):
    """TritonCTS's own lines in 4_1_cts.log: sinks, buffers created, buffers on a path, tree levels."""
    txt = read(os.path.join(logdir, "4_1_cts.log"))

    def first(pat):
        m = re.search(pat, txt)
        return int(m.group(1)) if m else None
    return dict(sinks=first(r"Total number of sinks:\s*(\d+)"), buffers=first(r"Created (\d+) clock buffers"),
                min_path=first(r"Minimum number of buffers in the clock path:\s*(\d+)"),
                max_path=first(r"Maximum number of buffers in the clock path:\s*(\d+)"),
                levels=first(r"Max level of the clock tree:\s*(\d+)"))


def metric(metrics, *patterns):
    """The first metric whose name matches one of the regexes (searched in order)."""
    for pat in patterns:
        for k in sorted(metrics):
            if re.fullmatch(pat, k):
                return metrics[k]
    return None


def power(text):
    """report_power's Total line: internal, switching, leakage, total (W)."""
    m = re.search(r"\nTotal\s+([-0-9.eE+]+)\s+([-0-9.eE+]+)\s+([-0-9.eE+]+)\s+([-0-9.eE+]+)", text)
    if not m:
        return None
    return dict(internal=float(m.group(1)), switching=float(m.group(2)), leakage=float(m.group(3)), total=float(m.group(4)))


def first_num(text, pat):
    m = re.search(pat, text or "")
    return float(m.group(1)) if m else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--logs", required=True)
    ap.add_argument("--block", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    L, block = args.logs, args.block
    run = load_json(os.path.join(L, "run.json")) or {}
    prep = load_json(os.path.join(L, "in", "prep.json")) or {}
    macros = (load_json(os.path.join(L, "in", "macros", "macros.json")) or {}).get("macros", [])
    ologs = os.path.join(L, "orfs", "logs")
    metrics = {}
    for p in sorted(glob.glob(os.path.join(ologs, "*.json"))):
        j = load_json(p)
        if isinstance(j, dict):
            metrics.update({k: v for k, v in j.items() if not isinstance(v, (dict, list))})
    with open(os.path.join(L, "metrics.json"), "w") as f:
        json.dump(metrics, f, indent=1, sort_keys=True)
    period = num(prep.get("period_ps"))
    status = run.get("status", "no run.json")
    # the CI run: pnr-plan's RUN.txt one level up names the run, the event, the branch and the commit
    plan = read(os.path.join(os.path.dirname(os.path.abspath(L)), "RUN.txt")).splitlines()
    ci_run = re.match(r"run (\d+) \((\S+) on (\S+) at ([0-9a-f]{7,40})\)", plan[0]) if plan else None
    where = (f"run {ci_run.group(1)} ({ci_run.group(2)} on {ci_run.group(3)} at {ci_run.group(4)[:9]})" if ci_run
             else "a local run")

    def m(*pats):
        return metric(metrics, *pats)

    # routed: ORFS's final report (OpenRCX, propagated clock) and metrics
    fin = orfs_sections(os.path.join(L, "orfs", "reports", "6_finish.rpt"))
    r_wns = first_num(fin.get("report_wns"), r"wns max\s+([-0-9.eE+]+)")
    if r_wns is None:
        r_wns = num(m(r"finish__timing__setup__ws"))
    r_tns = first_num(fin.get("report_tns"), r"tns max\s+([-0-9.eE+]+)")
    if r_tns is None:
        r_tns = num(m(r"finish__timing__setup__tns"))
    r_hws, r_htns = num(m(r"finish__timing__hold__ws")), num(m(r"finish__timing__hold__tns"))
    r_sviol = m(r"finish__timing__drv__setup_violation_count")
    r_hviol = m(r"finish__timing__drv__hold_violation_count")
    r_mpp, r_fmax = min_period(fin.get("report_clock_min_period", ""))
    r_path = worst_path(fin.get("report_checks -path_delay max", ""))
    r_r2r = worst_path(fin.get("report_checks -path_delay max reg to reg", ""))
    r_hold = worst_path(fin.get("report_checks -path_delay min", ""))
    r_mp = (period - r_r2r["slack"]) if (period and r_r2r and r_r2r.get("slack") is not None) else None
    pw = power(fin.get("report_power", ""))
    if pw is None and m(r"finish__power__total") is not None:
        pw = dict(internal=num(m(r"finish__power__internal__total")) or 0.0, switching=num(m(r"finish__power__switching__total")) or 0.0,
                  leakage=num(m(r"finish__power__leakage__total")) or 0.0, total=num(m(r"finish__power__total")))
    skew_s, skew_h = num(m(r"finish__clock__skew__setup")), num(m(r"finish__clock__skew__hold"))
    # synthesized: the same netlist flat in OpenSTA, ideal clock, no wires
    sdir = os.path.join(L, "in", "synth_sta")
    s_summ = kv(os.path.join(sdir, "summary.txt"))
    s_wns = num(s_summ.get("wns_max"))
    s_mpp, _ = min_period(read(os.path.join(sdir, "min_period_ports.txt")))
    s_path = worst_path(read(os.path.join(sdir, "paths_max.txt")))
    s_r2r = worst_path(read(os.path.join(sdir, "paths_reg2reg.txt")))
    s_mp = (period - s_r2r["slack"]) if (period and s_r2r and s_r2r.get("slack") is not None) else None
    if s_mpp is None and period and s_wns is not None:
        s_mpp = period - s_wns
    if r_mpp is None and period and r_wns is not None:
        r_mpp = period - r_wns
    # physical
    fp = floorplan(ologs)
    ct = cts(ologs)
    cong = congestion(ologs)
    times = step_times(ologs)
    die, core = fp.get("die"), fp.get("core")
    util = num(m(r"finish__design__instance__utilization", r"detailedroute__design__instance__utilization",
                 r"globalroute__design__instance__utilization", r"cts__design__instance__utilization"))
    util = util * 100.0 if (util is not None and util <= 1.0) else util
    cls = {k.split("class:")[1]: v for k, v in metrics.items() if k.startswith("finish__design__instance__count__class:")}
    synth_cells = (prep.get("yosys_stat") or {}).get("design", {})
    neo_paths = read(os.path.join(L, "orfs", "reports", "neo", "paths_max.txt"))

    md = [f"# pnr_{block} -- `{block}` placed and routed on ASAP7 through OpenROAD-flow-scripts",
          "",
          f"Generated by `07_ci/pnr/report.py` from the `pnr` job's logs ({where}, started {run.get('start', '?')}; image "
          f"`{run.get('image') or '?'}`; make arguments `{run.get('flow_args') or 'none'}`). Status: **{status}**. The netlist is `synth.py`'s for the unit's "
          f"subtree ({', '.join(prep.get('units', [])) or '?'}) at {fmt(period)} ps on ASAP7 {prep.get('corner', 'SS')} "
          f"(ORFS CORNER = WC, the same NLDM files), the memory leaves as FakeRAM2.0 macros; ORFS's defaults otherwise "
          "(`07_ci/pnr/designs/neo_common.mk`). Timing is the final stage's: OpenRCX parasitics, the propagated clock tree. "
          "ASAP7 is a predictive academic PDK: the picoseconds and square microns are the library's, the ratios and the paths "
          "the design's.",
          "",
          "| Figure | Value |",
          "| --- | --- |",
          f"| die / core | " + (f"{die[0]:.2f} x {die[1]:.2f} um" if die else "n/a") + " / "
          + (f"{core[0]:.2f} x {core[1]:.2f} um" if core else "n/a")
          + f" ({fmt(m(r'finish__design__die__area', r'floorplan__design__die__area'), 1)} / "
            f"{fmt(m(r'finish__design__core__area', r'floorplan__design__core__area'), 1)} um2) |",
          f"| utilization (cells and macros over the core, after routing; the floorplan's target 55 %) | {fmt(util, 1)} % |",
          f"| standard cells: synthesized (prep.py) / after placement / after routing | {fmt(synth_cells.get('stdcells', synth_cells.get('num_cells')))} / "
          f"{fmt(m(r'detailedplace__design__instance__count__stdcell'))} / {fmt(m(r'finish__design__instance__count__stdcell'))} "
          f"(of these {fmt(cls.get('timing_repair_buffer'))} timing-repair buffers, {fmt(cls.get('tie_cell'))} tie cells, "
          f"{fmt(cls.get('tap_cell'))} taps; {fmt(cls.get('fill_cell'))} fill cells beside them) |",
          f"| macros | {fmt(m(r'finish__design__instance__count__macros'))} ({fmt(m(r'finish__design__instance__area__macros'), 1)} um2) |",
          f"| cell area: synthesized / routed | {fmt(synth_cells.get('stdcell_area_um2'), 1)} / "
          f"{fmt(m(r'finish__design__instance__area__stdcell'), 1)} um2 |",
          f"| routed wire length / vias | {fmt(m(r'detailedroute__route__wirelength'))} um / {fmt(m(r'detailedroute__route__vias'))} |",
          f"| DRC violations after detailed routing (first iteration) / antenna-violating nets / diodes | "
          f"{fmt(m(r'detailedroute__route__drc_errors'))} ({fmt(m(r'detailedroute__route__drc_errors__iter:0'))}) / "
          f"{fmt(m(r'detailedroute__antenna__violating__nets'))} / {fmt(m(r'detailedroute__antenna_diodes_count'))} |",
          f"| global-route congestion (usage, overflow H / V / total) | "
          + (f"{cong['usage_pct']:.2f} %, {cong['overflow_h']} / {cong['overflow_v']} / {cong['overflow']}" if cong else "n/a") + " |",
          f"| setup WNS / TNS | **{fmt(r_wns, 1)} ps** / {fmt(r_tns, 1)} ps ({fmt(r_sviol)} violating endpoints) |",
          f"| hold WNS / TNS | {fmt(r_hws, 1)} / {fmt(r_htns, 1)} ps ({fmt(r_hviol)} violating endpoints) |",
          f"| minimum period, register to register / with the I/O paths | {fmt(r_mp, 1)} / {fmt(r_mpp, 1)} ps ({fmt(r_fmax, 0)} MHz) |",
          f"| clock tree: sinks, buffers (TritonCTS), buffers on a path, levels | {fmt(ct.get('sinks'))}, {fmt(ct.get('buffers'))}, "
          f"{fmt(ct.get('min_path'))}-{fmt(ct.get('max_path'))}, {fmt(ct.get('levels'))}; after routing {fmt(cls.get('clock_buffer'))} "
          f"clock buffers + {fmt(cls.get('clock_inverter'))} clock inverters |",
          f"| clock skew, setup / hold (with the 8 % setup uncertainty in the setup figure) | {fmt(skew_s, 1)} / {fmt(skew_h, 1)} ps |",
          f"| power at the flow's default activity (internal / switching / leakage / total) | "
          + (f"{pw['internal'] * 1e3:.3f} / {pw['switching'] * 1e3:.3f} / {pw['leakage'] * 1e3:.4f} / **{pw['total'] * 1e3:.3f} mW**" if pw else "n/a")
          + " |",
          f"| max slew / cap / fanout violations | {fmt(m(r'finish__timing__drv__max_slew'))} / {fmt(m(r'finish__timing__drv__max_cap'))} / "
          f"{fmt(m(r'finish__timing__drv__max_fanout'))} |",
          "",
          "Power: OpenSTA's default switching activity (the flow sets none); A3 replaces it with the activity the cocotb runs "
          "record (VCD / SAIF).",
          ""]
    if r_path:
        md += ["## The worst routed paths", "",
               f"Setup: `{r_path['start']}` -> `{r_path['end']}`, arrival {fmt(r_path['arrival'], 1)} ps (launch clock latency "
               f"included), required {fmt(r_path['required'], 1)} ps, slack **{fmt(r_path['slack'], 1)} ps**, {r_path['cells']} cells "
               "(`orfs/reports/6_finish.rpt`)."]
        if r_r2r and (r_r2r["start"], r_r2r["end"]) != (r_path["start"], r_path["end"]):
            md += ["", f"Register to register: `{r_r2r['start']}` -> `{r_r2r['end']}`, slack {fmt(r_r2r['slack'], 1)} ps."]
        if r_hold:
            md += ["", f"Hold: `{r_hold['start']}` -> `{r_hold['end']}`, slack {fmt(r_hold['slack'], 1)} ps."]
        if neo_paths:
            ends = re.findall(r"Endpoint:\s*(\S+)", neo_paths)
            slk = re.findall(r"\n\s*([-0-9.]+)\s+slack \(", neo_paths)
            md += ["", "The ten worst endpoints (`orfs/reports/neo/paths_max.txt`): "
                   + ", ".join(f"`{e}` {s}" for e, s in zip(ends, slk)) + "."]
        md.append("")
    md += ["## Routed against synthesized", "",
           "The synthesized estimate is the netlist ORFS placed (prep.py's) flat in OpenSTA with the same SDC, an ideal clock and "
           "no wires (`in/synth_sta/`); the routed figure is ORFS's final STA. A minimum period here is the period minus the "
           "worst slack of the paths named (the 8 % setup uncertainty and the setup time included on both sides). The routed "
           "netlist is not the synthesized one: the flow resizes and buffers it (timing repair in floorplan, CTS and global "
           "route), so the ratio carries both the wires and the repair.",
           "",
           "| | synthesized | routed | routed / synthesized |",
           "| --- | --- | --- | --- |",
           f"| setup WNS at {fmt(period)} ps | {fmt(s_wns, 1)} ps | {fmt(r_wns, 1)} ps | |",
           f"| minimum period, register to register | {fmt(s_mp, 1)} ps | {fmt(r_mp, 1)} ps | {ratio(r_mp, s_mp)} |",
           f"| minimum period with the I/O paths | {fmt(s_mpp, 1)} ps | {fmt(r_mpp, 1)} ps | {ratio(r_mpp, s_mpp)} |",
           ""]
    if s_path:
        md += [f"Synthesized worst path: `{s_path['start']}` -> `{s_path['end']}`, arrival {fmt(s_path['arrival'], 1)} ps, "
               f"slack {fmt(s_path['slack'], 1)} ps" + (f"; register to register `{s_r2r['start']}` -> `{s_r2r['end']}`, slack "
                                                        f"{fmt(s_r2r['slack'], 1)} ps." if s_r2r else "."), ""]
    su = prep.get("synth") or {}
    if su:
        md += ["`synth.py`'s own figures for the units of the block (each unit with its children as extracted timing models, "
               "the synthesis job's per-unit view, 0.2 of the period on every unit's I/O):", "",
               "| unit | instances in the block | cells (own) | WNS | minimum period | worst path |",
               "| --- | --- | --- | --- | --- | --- |"]
        for u in reversed(prep.get("units", [])):
            r = su.get(u, {})
            md.append(f"| `{u}` | {r.get('instances', '?')} | {fmt(r.get('own_cells'))} | {fmt(r.get('wns_ps'), 1)} ps | "
                      f"{fmt(r.get('min_period_ps'), 1)} ps | `{r.get('worst_start', '?')}` -> `{r.get('worst_end', '?')}` |")
        worst_unit = max((su[u].get("min_period_ps") or 0.0, u) for u in su)
        md += ["", f"The subtree's slowest unit by synth.py: `{worst_unit[1]}` at {fmt(worst_unit[0], 1)} ps"
               + (f"; routed register to register / that = {r_mp / worst_unit[0]:.3f}." if (r_mp and worst_unit[0]) else "."), ""]
    if macros:
        md += ["## Macros (FakeRAM2.0, `07_ci/pnr/fakeram.py`)", "",
               "| leaf (synth.py) | macro | words x bits, groups | pins | LEF size | LEF area | um2 / bit | synthesis model (memories.md) |",
               "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for mm in macros:
            md.append(f"| `{mm['leaf']}` | `{mm['macro']}` | {mm['D']} x {mm['W']}, G = {mm['G']} | {mm['pins']} | "
                      f"{mm['lef_width_um']:.2f} x {mm['lef_height_um']:.2f} um{' (pin-limited)' if mm.get('pin_limited') else ''} | "
                      f"{mm['lef_area_um2']:,.1f} um2 | {mm['um2_per_bit']:.4f} | {mm['synth_model_um2']:,.1f} um2 (x{mm['synth_factor']}) |")
        md.append("")
        # where the macro placer put them: neo_report.tcl's lines, else ORFS's write_macro_placement file
        place = [ln.split()[1:6] for ln in read(os.path.join(L, "orfs", "reports", "neo", "physical.txt")).splitlines()
                 if ln.startswith("macro ")]
        if not place:
            place = [(g[0], "", g[1], g[2], g[3]) for g in re.findall(
                r"-macro_name\s+\{?([^\s}]+)\}?\s+-location\s+\{\s*([-0-9.]+)\s+([-0-9.]+)\s*\}\s+-orientation\s+(\S+)",
                read(os.path.join(L, "orfs", "results", "2_2_floorplan_macro.tcl")))]
        if place:
            md += ["Placed by ORFS's macro placer (lower-left corner, orientation): " + "; ".join(
                f"`{p[0]}` at ({float(p[2]):.1f}, {float(p[3]):.1f}) um {p[4]}" for p in place) + ".", ""]
    md += ["## Runtime and peak memory", "", "| ORFS step | elapsed | peak memory |", "| --- | --- | --- |"]
    tot, by_stage = 0.0, {}
    for step in sorted(times, key=lambda s: (STEP_ORDER.index(s) if s in STEP_ORDER else 99, s)):
        secs, mb = times[step]
        tot += secs
        st = STAGE_OF.get(step[:1], "other")
        t, p = by_stage.get(st, (0.0, 0.0))
        by_stage[st] = (t + secs, max(p, mb))
        md.append(f"| {step} | {secs / 60:.1f} min | {mb:,.0f} MB |")
    md += [f"| **all steps** | **{tot / 60:.1f} min** | **{max([v[1] for v in times.values()] or [0]):,.0f} MB** |", "",
           "By stage: " + "; ".join(f"{k} {v[0] / 60:.1f} min, {v[1]:,.0f} MB" for k, v in by_stage.items()) + ".", "",
           "The job's own stages (`run.json`): " + "; ".join(
               f"{s['stage']} {('exit ' + str(s.get('exit'))) if s.get('exit') is not None else 'not run'}"
               + (f" in {s['seconds'] / 60:.1f} min" if s.get("seconds") else "") + (f" ({s['note']})" if s.get("note") else "")
               for s in run.get("stages", [])) + ".", ""]
    out = args.out or os.path.join(L, f"pnr_{block}.md")
    with open(out, "w") as f:
        f.write("\n".join(md) + "\n")
    print(f"report.py: {out} ({status})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
