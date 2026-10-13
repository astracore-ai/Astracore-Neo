#!/usr/bin/env python3
"""report.py -- pnr_<block>.md from one block's logs of the place-and-route job (drop 0.42, stream A2b).

Reads what run.py collected into ci_logs/pnr/<block>/: run.json (the stages, their exit codes and times), in/prep.json
(the subtree, synth.py's per-unit figures, the macros), in/synth_sta/ (the prepared netlist flat in OpenSTA: the
synthesized estimate on the very netlist ORFS placed), orfs/logs/ (every ORFS step's log -- its last lines carry the
elapsed time and peak memory -- and metrics JSON), orfs/reports/6_finish.rpt (ORFS's final report: OpenRCX
parasitics, propagated clock), and orfs/reports/neo/ when neo_report.tcl ran (the ten worst endpoints). Writes
pnr_<block>.md and metrics.json (every ORFS metric merged) beside them. Partial runs are reported as far as they got.
Drop 0.43 (stream A3): a block whose design stops at global routing (designs/<block>/stages: neo_core) is reported from
the global-route stage's metrics; and the power at the flow's default activity beside the power from the cocotb runs'
recorded activity (neo_report.tcl's power_default.txt / power_activity.txt, power.py's in/activity.json).

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
    k = body.find(r["start"] + "/")         # from the launching register's clock pin: the launch clock path is not data
    insts = re.findall(r"\s(\S+)/\S+ \(([A-Za-z0-9_]+)\)", body[k:] if k >= 0 else body)
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
    """Every ORFS step log's `Elapsed time: H:MM:SS.ff ... Peak memory: NKB.` -> {step: (seconds, MB)}. A step's time is
    its log's last such line (the step's own `time` wrapper): a nested run -- the equivalence check inside CTS and the
    final report -- prints its line into the parent's log too, and the parent's time already holds it."""
    out = {}
    for p in glob.glob(os.path.join(logdir, "*.log")):
        step = os.path.basename(p)[:-4]
        secs, peak = 0.0, 0.0
        for m in re.finditer(r"Elapsed time:\s*([0-9:.]+)\[h:\]min:sec\..*?Peak memory:\s*(\d+)KB", read(p)):
            parts = [float(x) for x in m.group(1).split(":")]
            secs = sum(v * 60 ** i for i, v in enumerate(reversed(parts)))
            peak = max(peak, int(m.group(2)) / 1024.0)
        if secs or peak:
            out[step] = (secs, peak)
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
    """TritonCTS's own lines in 4_1_cts.log, over its trees (with macros it builds one for the macros' clock pins,
    `clk`, and one for the registers, `clk_regs`; each tree's lines in the same order): sinks, buffers created, buffers
    on a path, tree levels, and the trees with their sinks."""
    txt = read(os.path.join(logdir, "4_1_cts.log"))

    def every(pat):
        return [int(v) for v in re.findall(pat, txt)]
    names = re.findall(r"Generating H-Tree topology for net (\S+?)\.?\n", txt)
    sinks = every(r"Total number of sinks:\s*(\d+)")
    bufs = every(r"Created (\d+) clock buffers")
    lo, hi = every(r"Minimum number of buffers in the clock path:\s*(\d+)"), every(r"Maximum number of buffers in the clock path:\s*(\d+)")
    lev = every(r"Max level of the clock tree:\s*(\d+)")
    return dict(sinks=sum(sinks) if sinks else None, buffers=sum(bufs) if bufs else None, min_path=min(lo) if lo else None,
                max_path=max(hi) if hi else None, levels=max(lev) if lev else None,
                trees=list(zip(names, sinks)) if len(names) == len(sinks) else [])


# the stages a metric can come from, newest first, with the words the report uses for them
NEWEST = (("finish", "routing"), ("detailedroute", "detailed routing"), ("globalroute", "global routing"), ("cts", "CTS"),
          ("detailedplace", "placement"), ("globalplace", "global placement"), ("floorplan", "the floorplan"))

REPAIR_STAGES = (("2_1_floorplan.log", "floorplan (no wires, ideal clock: the synthesized estimate, then gate sizing)"),
                 ("4_1_cts.log", "CTS (placement parasitics, the propagated clock)"),
                 ("5_1_grt.log", "global route (its parasitics)"))


def repair_wns(logdir):
    """repair_timing's setup tables (`Iter | Removed | ... | WNS | TNS | ...`) in the floorplan, CTS and global-route
    logs: [(stage, WNS before, WNS after)] in ps -- the first row of the stage's first table, the final row of its last.
    A stage that found no setup violation prints no table and is left out."""
    out = []
    for log, label in REPAIR_STAGES:
        first = last = col = None
        for ln in read(os.path.join(logdir, log)).splitlines():
            if re.match(r"\s*Iter\s*\|", ln):
                cells = [c.strip() for c in ln.split("|")]
                col = cells.index("WNS") if "WNS" in cells else None      # repair_design's table has no WNS
                continue
            mm = re.match(r"\s*(\d+\*?|final)\s*\|", ln) if col is not None else None
            if mm:
                cells = ln.split("|")
                v = num(cells[col].strip()) if len(cells) > col else None
                if v is not None:
                    first = v if first is None else first
                    last = v if mm.group(1) == "final" else last
        if first is not None:
            out.append((label, first, last if last is not None else first))
    return out


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


def power_groups(text):
    """report_power's group rows (Sequential, Combinational, Clock, Macro, Pad, Total): {group: (internal, switching,
    leakage, total)} in W."""
    out = {}
    for g, a, b, c, d in re.findall(r"\n(Sequential|Combinational|Clock|Macro|Pad|Total)\s+([-0-9.eE+]+)\s+([-0-9.eE+]+)"
                                    r"\s+([-0-9.eE+]+)\s+([-0-9.eE+]+)", text):
        out[g] = (float(a), float(b), float(c), float(d))
    return out


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
    stages = (run.get("orfs_stages") or "").split(",")
    grt_only = bool(stages) and stages[-1] == "grt"           # drop 0.43: placed and globally routed (neo_core)

    def fm(name):
        """A final-stage metric: finish__<name>, else the global route's (a block that stops there)."""
        return m(rf"finish__{name}", rf"globalroute__{name}") if grt_only else m(rf"finish__{name}")

    # routed: ORFS's final report (OpenRCX, propagated clock) and metrics
    fin = orfs_sections(os.path.join(L, "orfs", "reports", "6_finish.rpt"))
    r_wns = first_num(fin.get("report_wns"), r"wns max\s+([-0-9.eE+]+)")
    if r_wns is None:
        r_wns = num(m(r"finish__timing__setup__ws"))
    r_tns = first_num(fin.get("report_tns"), r"tns max\s+([-0-9.eE+]+)")
    if r_tns is None:
        r_tns = num(m(r"finish__timing__setup__tns"))
    if grt_only and r_wns is None:
        r_wns, r_tns = num(m(r"globalroute__timing__setup__ws")), num(m(r"globalroute__timing__setup__tns"))
    r_hws, r_htns = num(fm(r"timing__hold__ws")), num(fm(r"timing__hold__tns"))
    r_sviol = fm(r"timing__drv__setup_violation_count")
    r_hviol = fm(r"timing__drv__hold_violation_count")
    r_mpp, r_fmax = min_period(fin.get("report_clock_min_period", ""))
    r_path = worst_path(fin.get("report_checks -path_delay max", ""))
    r_r2r = worst_path(fin.get("report_checks -path_delay max reg to reg", ""))
    r_hold = worst_path(fin.get("report_checks -path_delay min", ""))
    r_mp = (period - r_r2r["slack"]) if (period and r_r2r and r_r2r.get("slack") is not None) else None
    pw = power(fin.get("report_power", ""))
    if pw is None and fm(r"power__total") is not None:
        pw = dict(internal=num(fm(r"power__internal__total")) or 0.0, switching=num(fm(r"power__switching__total")) or 0.0,
                  leakage=num(fm(r"power__leakage__total")) or 0.0, total=num(fm(r"power__total")))
    skew_s, skew_h = num(fm(r"clock__skew__setup")), num(fm(r"clock__skew__hold"))
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
    cls ={k.split("class:")[1]: v for k, v in metrics.items() if k.startswith("finish__design__instance__count__class:")}
    synth_cells = (prep.get("yosys_stat") or {}).get("design", {})
    neo_paths = read(os.path.join(L, "orfs", "reports", "neo", "paths_max.txt"))

    # the database the report ran on (run.json's neo_report stage): the final one, or the newest a shorter run left
    rep_db = next((st.get("db") for st in run.get("stages", []) if st.get("stage") == "neo_report"), None) or ""

    def newest(name):
        """A metric at the newest stage that has it, with the stage's name (a run the job's time stopped early)."""
        for p, lab in NEWEST:
            v = num(m(rf"{p}__{name}"))
            if v is not None:
                return v, lab
        return None, None
    what = "placed and globally routed (no detailed routing)" if grt_only else "placed and routed"
    if status != "complete":                                     # the run stopped short of its targets
        what = f"(stopped: {status})"
    timing_src = ("Timing is the global-route stage's: its parasitic estimates, the propagated clock tree."
                  if grt_only else "Timing is the final stage's: OpenRCX parasitics, the propagated clock tree.")
    if status != "complete" and r_wns is None:
        timing_src = (f"No timing: the run stopped before {'global routing' if grt_only else 'routing'}, and the figures "
                      f"below are the newest stage's it reached (the report itself ran on `{rep_db or '?'}`, for power).")
    u_val, u_at = newest("design__instance__utilization")
    util = u_val * 100.0 if (u_val is not None and u_val <= 1.0) else u_val
    pl_cells = num(m(r"detailedplace__design__instance__count__stdcell"))
    pl_note = ""
    if pl_cells is None and num(m(r"globalplace__design__instance__count__stdcell")) is not None:
        pl_cells, pl_note = num(m(r"globalplace__design__instance__count__stdcell")), " (global placement)"
    area_last, area_at = num(fm(r"design__instance__area__stdcell")), ("placed" if grt_only else "routed")
    if area_last is None:
        area_last, area_at = newest("design__instance__area__stdcell")
        area_at = f"at {area_at}" if area_at else area_at
    md = [f"# pnr_{block} -- `{block}` {what} on ASAP7 through OpenROAD-flow-scripts",
          "",
          f"Generated by `07_ci/pnr/report.py` from the `pnr` job's logs ({where}, started {run.get('start', '?')}; image "
          f"`{run.get('image') or '?'}`; make arguments `{run.get('flow_args') or 'none'}`; ORFS targets "
          f"`{run.get('orfs_stages') or 'synth,floorplan,place,cts,route,do-finish'}`). Status: **{status}**. The netlist is `synth.py`'s for the unit's "
          f"subtree ({', '.join(prep.get('units', [])) or '?'}) at {fmt(period)} ps on ASAP7 {prep.get('corner', 'SS')} "
          f"(ORFS CORNER = WC, the same NLDM files), the memory leaves as FakeRAM2.0 macros; ORFS's defaults otherwise "
          f"(`07_ci/pnr/designs/neo_common.mk`, the block's `config.mk`). {timing_src} "
          "ASAP7 is a predictive academic PDK: the picoseconds and square microns are the library's, the ratios and the paths "
          "the design's.",
          "",
          "| Figure | Value |",
          "| --- | --- |",
          f"| die / core | " + (f"{die[0]:.2f} x {die[1]:.2f} um" if die else "n/a") + " / "
          + (f"{core[0]:.2f} x {core[1]:.2f} um" if core else "n/a")
          + f" ({fmt(m(r'finish__design__die__area', r'floorplan__design__die__area'), 1)} / "
            f"{fmt(m(r'finish__design__core__area', r'floorplan__design__core__area'), 1)} um2) |",
          f"| utilization (cells and macros over the core, after {u_at or ('global routing' if grt_only else 'routing')}) | "
          f"{fmt(util, 1)} % |",
          f"| standard cells: synthesized (prep.py) / after placement / after {'global routing' if grt_only else 'routing'} | "
          f"{fmt(synth_cells.get('stdcells', synth_cells.get('num_cells')))} / "
          f"{fmt(pl_cells)}{pl_note} / {fmt(fm(r'design__instance__count__stdcell'))} "
          f"(of these {fmt(cls.get('timing_repair_buffer'))} timing-repair buffers, {fmt(cls.get('tie_cell'))} tie cells, "
          f"{fmt(cls.get('tap_cell'))} taps; {fmt(cls.get('fill_cell'))} fill cells beside them) |",
          f"| macros | {fmt(newest('design__instance__count__macros')[0])} "
          f"({fmt(newest('design__instance__area__macros')[0], 1)} um2) |",
          f"| cell area: synthesized / {area_at or ('placed' if grt_only else 'routed')} | "
          f"{fmt(synth_cells.get('stdcell_area_um2'), 1)} / {fmt(area_last, 1)} um2 |",
          f"| routed wire length / vias | {fmt(m(r'detailedroute__route__wirelength'))} um / {fmt(m(r'detailedroute__route__vias'))} |",
          f"| DRC violations after detailed routing (first iteration) / antenna-violating nets / diodes | "
          f"{fmt(m(r'detailedroute__route__drc_errors'))} ({fmt(m(r'detailedroute__route__drc_errors__iter:0'))}) / "
          f"{fmt(m(r'detailedroute__antenna__violating__nets'))} / {fmt(m(r'detailedroute__antenna_diodes_count'))} |",
          f"| global-route congestion (usage, overflow H / V / total) | "
          + (f"{cong['usage_pct']:.2f} %, {cong['overflow_h']} / {cong['overflow_v']} / {cong['overflow']}" if cong else "n/a") + " |",
          f"| setup WNS / TNS | **{fmt(r_wns, 1)} ps** / {fmt(r_tns, 1)} ps ({fmt(r_sviol)} violating endpoints) |",
          f"| hold WNS / TNS | {fmt(r_hws, 1)} / {fmt(r_htns, 1)} ps ({fmt(r_hviol)} violating endpoints) |",
          f"| minimum period, register to register / with the I/O paths | {fmt(r_mp, 1)} / {fmt(r_mpp, 1)} ps ({fmt(r_fmax, 0)} MHz) |",
          f"| clock tree: sinks, buffers (TritonCTS), buffers on a path, levels | {fmt(ct.get('sinks'))}"
          + (" (" + ", ".join(f"{n} {fmt(s)}" for n, s in ct["trees"]) + ")" if len(ct.get("trees") or []) > 1 else "")
          + f", {fmt(ct.get('buffers'))}, "
          f"{fmt(ct.get('min_path'))}-{fmt(ct.get('max_path'))}, {fmt(ct.get('levels'))}; after routing {fmt(cls.get('clock_buffer'))} "
          f"clock buffers + {fmt(cls.get('clock_inverter'))} clock inverters |",
          f"| clock skew, setup / hold (with the 8 % setup uncertainty in the setup figure) | {fmt(skew_s, 1)} / {fmt(skew_h, 1)} ps |",
          f"| power at the flow's default activity (internal / switching / leakage / total) | "
          + (f"{pw['internal'] * 1e3:.3f} / {pw['switching'] * 1e3:.3f} / {pw['leakage'] * 1e3:.4f} / **{pw['total'] * 1e3:.3f} mW**" if pw else "n/a")
          + " |",
          f"| max slew / cap / fanout violations | {fmt(m(r'finish__timing__drv__max_slew'))} / {fmt(m(r'finish__timing__drv__max_cap'))} / "
          f"{fmt(m(r'finish__timing__drv__max_fanout'))} |",
          "",
          "Power in this table: OpenSTA's default switching activity (0.1 transitions per cycle on the inputs, propagated; "
          "the flow sets none); the recorded activity's figure is in the section on power below when the block has one.",
          ""]
    # drop 0.43: power at the default activity and from the recorded activity (neo_report.tcl; power.py)
    ndir = os.path.join(L, "orfs", "reports", "neo")
    p_def = power_groups(read(os.path.join(ndir, "power_default.txt")))
    p_act = power_groups(read(os.path.join(ndir, "power_activity.txt")))
    applied = kv(os.path.join(ndir, "activity_applied.txt"))
    act = load_json(os.path.join(L, "in", "activity.json")) or {}
    db_text = {"6_final.odb": "the final database (OpenRCX parasitics, the propagated clock)",
               "5_route.odb": "the detailed-route database (placement parasitics, the propagated clock)",
               "5_1_grt.odb": "the global-route database (parasitic estimates, the propagated clock)",
               "4_cts.odb": "the CTS database (placement parasitics, the propagated clock)",
               "3_place.odb": "the placed database (placement parasitics, no clock tree)",
               "2_floorplan.odb": "the floorplan database -- the run ended in placement: no wires, no clock tree, so the "
                                  "figure is the cells' alone"}.get(rep_db, f"`{rep_db}`" if rep_db else "the final database")
    if p_def or p_act:
        md += ["## Power: the default activity and the recorded activity", "",
               f"OpenSTA's report_power on {db_text} at {fmt(period)} ps, "
               "ASAP7 SS 0.63 V / 100 C (the WC corner's NLDM and power tables), in mW."]
        if act:
            fk, ik = act.get("flops_by_kind") or {}, act.get("inputs_by_kind") or {}
            util_s = f"{100.0 * act['mac_util']:.1f} %" if act.get("mac_util") is not None else "n/a"
            md += ["", f"The recorded activity: `{act.get('activity')}` -- {act.get('source')}, scope `{act.get('scope')}`, a window "
                   f"of {fmt(act.get('window_cycles'))} cycles at {fmt(act.get('clock_ps'))} ps in simulation (the activity is per "
                   f"cycle, applied at the period above); MAC utilization in the window {util_s}. Of the block's "
                   f"{fmt(act.get('flops'))} flops {fmt(fk.get('exact'))} took a recorded bit by name, {fmt(fk.get('folded'))} by "
                   f"the folded index (a wider unit than the one simulated: the bit of the same signal at the index modulo the "
                   f"recorded range), {fmt(fk.get('default'))} kept the propagated default; input bits {fmt(ik.get('exact'))} / "
                   f"{fmt(ik.get('folded'))} / {fmt(ik.get('default'))}. Pins annotated in the database: "
                   f"{applied.get('pins_found', 'n/a')} of {applied.get('pins_listed', 'n/a')}, input ports {applied.get('input_ports', 'n/a')}."]
        md += ["", "| activity | group | internal | switching | leakage | total |", "| --- | --- | --- | --- | --- | --- |"]
        for lab, pg in (("default", p_def), ("recorded", p_act)):
            for g in ("Sequential", "Combinational", "Clock", "Macro", "Total"):
                if g in pg and (pg[g][3] > 0 or g == "Total"):
                    v = pg[g]
                    md.append(f"| {lab} | {g} | {v[0] * 1e3:.3f} | {v[1] * 1e3:.3f} | {v[2] * 1e3:.4f} | "
                              f"{'**' if g == 'Total' else ''}{v[3] * 1e3:.3f}{'**' if g == 'Total' else ''} |")
        if "Total" in p_def and "Total" in p_act and p_def["Total"][3]:
            md += ["", f"Recorded / default: {p_act['Total'][3] / p_def['Total'][3]:.3f} (total), dynamic "
                   f"{(p_act['Total'][0] + p_act['Total'][1]) / max(1e-30, p_def['Total'][0] + p_def['Total'][1]):.3f}."]
        md.append("")
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
    md += ["## Routed against synthesized", ""]
    if any(v is not None for v in (s_wns, r_wns, s_mp, r_mp, s_mpp, r_mpp)):
        md += ["The synthesized estimate is the netlist ORFS placed (prep.py's) flat in OpenSTA with the same SDC, an ideal "
               "clock and no wires (`in/synth_sta/`); the routed figure is ORFS's final STA. A minimum period here is the period "
               "minus the worst slack of the paths named (the 8 % setup uncertainty and the setup time included on both sides). "
               "The routed netlist is not the synthesized one: the flow resizes and buffers it (timing repair in floorplan, CTS "
               "and global route), so the ratio carries both the wires and the repair.",
               "",
               "| | synthesized | routed | routed / synthesized |",
               "| --- | --- | --- | --- |",
               f"| setup WNS at {fmt(period)} ps | {fmt(s_wns, 1)} ps | {fmt(r_wns, 1)} ps | |",
               f"| minimum period, register to register | {fmt(s_mp, 1)} ps | {fmt(r_mp, 1)} ps | {ratio(r_mp, s_mp)} |",
               f"| minimum period with the I/O paths | {fmt(s_mpp, 1)} ps | {fmt(r_mpp, 1)} ps | {ratio(r_mpp, s_mpp)} |",
               ""]
    else:
        md += ["No timing on either side: the synthesized netlist's STA was not run for this block and its run stopped "
               "before routing.", ""]
    rw = repair_wns(os.path.join(L, "orfs", "logs"))
    if rw:
        md += ["Setup WNS through the flow (repair_timing's own tables, before -> after its repair): "
               + "; ".join(f"{lab} {fmt(a, 1)} -> {fmt(b, 1)} ps" for lab, a, b in rw)
               + f"; final (OpenRCX) {fmt(r_wns, 1)} ps.", ""]
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
        if "_lec" in step:              # the equivalence checks run inside their stage's step: shown, not added again
            md.append(f"| {step} (inside its stage's step) | {secs / 60:.1f} min | {mb:,.0f} MB |")
            continue
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
    # a step the job's time cut off leaves its log as <step>.tmp.log, without a time line: how far it got
    for tl in sorted(glob.glob(os.path.join(ologs, "*.tmp.log"))):
        step = os.path.basename(tl)[:-len(".tmp.log")]
        rows = re.findall(r"^\s*(\d+) \|\s*([-+][0-9.]+%) \|\s*(\d+) \|\s*(\d+) \|\s*(\d+) \|\s*(\d+)\s*$", read(tl), re.M)
        if rows:                    # repair_design -verbose: iteration, area, resized, buffers, nets repaired, remaining
            it, area, res, buf, rep, rem = rows[-1]
            nets = int(it) + int(rem)
            md += [f"Unfinished: `{step}`, stopped in repair_design (its last progress row): {int(it):,} of {nets:,} nets "
                   f"visited ({100.0 * int(it) / max(1, nets):.1f} %), {int(rep):,} repaired with {int(buf):,} buffers and "
                   f"{int(res):,} resized cells, area {area}.", ""]
        else:
            md += [f"Unfinished: `{step}` (`{os.path.basename(tl)}` has no progress table).", ""]
    out = args.out or os.path.join(L, f"pnr_{block}.md")
    with open(out, "w") as f:
        f.write("\n".join(md) + "\n")
    print(f"report.py: {out} ({status})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
