#!/usr/bin/env python3
"""tile_table.py -- the tile's routed-area estimate from the pnr job's blocks (drop 0.43, stream A3).

Reads the routed blocks' logs (a checkout of ci-logs-pnr: <logs>/<block>/, as report.py reads them) and the synthesis
job's synth_results.json of the same RTL, and writes the table the spec needs:

  * per block: the standard-cell count and area synthesized (prep.py's netlist, the one ORFS placed) and routed (ORFS's
    final stage; for a run that ended earlier, the newest stage it reached: global routing, CTS, placement, global
    placement or the floorplan), the routed area without the tap cells, their ratio, the macros' LEF area against the
    synthesis flow's macro model, the die and core, the utilization;
  * the tile: every unit of neo_tile at its routed cell area times its instances in the tile, plus the macros at their
    LEF areas, against the synthesis flow's tile (cells + macro model) -- the estimate the spec compares; a unit whose
    run stopped at global placement counts at its globally placed cell area times the growth its 16 x 8 counterpart
    showed from global placement to routing (PROXY: neo_core from c16_neo_core), and one without a routed block of its
    own (neo_tile's own glue) at its synthesized area times the median ratio of the routed blocks;
  * the tile as placed: the logic units at the core areas their routed blocks occupied (their cells at the blocks'
    utilization), the bank's 16 lane macros packed four by four with the routed block's 20 um pin channel -- the tile
    macro the floorplan sketch draws (09_synthesis/floorplan/floorplan.py, from --json);
  * with --power: per block the power at the flow's default activity and with the recorded activity (neo_report.tcl's
    power_default.txt / power_activity.txt), dynamic (internal + switching) and leakage, the recording's MAC utilization
    and how many flops took a recorded bit (power.py's in/activity.json).

Usage: tile_table.py --logs <ci_logs/pnr> --synth <synth_results.json> --out <md> [--json <tile_routed.json>]
                     [--power <md>]
"""
import argparse
import json
import os
import re
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from report import power_groups  # noqa: E402  (report.py's parser of report_power's group rows)

# neo_tile's children (synth.py tile_units) and their instance counts in the tile
TILE_UNITS = [("neo_core", 1), ("tile_nic", 1), ("noc_router", 1), ("bank_bist_wrap", 1), ("tile_dma", 2), ("prog_mem", 1),
              ("host_if", 1), ("esm", 1), ("link_packer", 1), ("link_unpacker", 1)]
# a unit without placed figures grows as its 16 x 8 counterpart did from the stage the unit's run reached to routing
PROXY = {"neo_core": "c16_neo_core"}
UNPLACED = {"globalplace": "global placement", "floorplan": "floorplan"}   # stages before detailed placement
# blocks routed for their ratio inside the core / the bank (not counted again in the tile)
INNER = ["requant", "acc_bank", "act_feeder", "wbuf_mem", "mbist", "mac_pe", "c16_systolic_array", "c16_neo_core"]
BANK_CHANNEL_UM = 20.0         # the pin channel in front of each lane macro (designs/bank_bist_wrap/macro_place.tcl)
BANK_ROW_GAP_UM = 4.0          # between rows: twice the platform's 2 um macro halo
BANK_PER_ROW = 4               # the floorplan's packing: four lanes across, four rows


def load(p):
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def run_of(path):
    """The CI run a log directory came from: 'run N' from the first line of its RUN.txt, else None."""
    try:
        with open(path) as f:
            m = re.match(r"run \d+", f.readline())
    except OSError:
        return None
    return m.group(0) if m else None


def block(logs, b):
    d = os.path.join(logs, b)
    m = load(os.path.join(d, "metrics.json")) or {}
    run = load(os.path.join(d, "run.json")) or {}
    prep = load(os.path.join(d, "in", "prep.json")) or {}
    mac = (load(os.path.join(d, "in", "macros", "macros.json")) or {}).get("macros", [])
    if not m and not prep:
        return None
    stages = [s for s in (run.get("orfs_stages") or run.get("stages_requested") or "").split(",") if s]
    grt = bool(stages) and stages[-1] == "grt"

    # the newest stage with figures: the final one, global routing, or -- for a run the job's time stopped earlier --
    # CTS, detailed placement, global placement or the floorplan
    order = ["finish", "globalroute", "cts", "detailedplace", "globalplace", "floorplan"]
    have = next((p for p in order if isinstance(m.get(f"{p}__design__instance__area__stdcell"), (int, float))), None)

    def g(name, pre=None):
        for p in ([pre] if pre else ([have] if have else [])):
            v = m.get(f"{p}__{name}")
            if isinstance(v, (int, float)):
                return float(v)
        return None
    ys = (prep.get("yosys_stat") or {}).get("design", {})
    phys = {}
    try:
        for ln in open(os.path.join(d, "orfs", "reports", "neo", "physical.txt")):
            k, _, v = ln.partition(" ")
            phys[k] = v.split()
    except OSError:
        pass

    def dims(k):
        v = phys.get(k)
        return [float(v[0]), float(v[1])] if v and len(v) >= 2 else None
    routed_all = g("design__instance__area__stdcell")
    # the tap cells follow the core area a block happened to get, not its logic (65,000 of the bank block's 76,204 cells
    # in its 1.2 mm core of macros): the cell area compared here is the routed standard cells without them (neo_report's
    # physical.txt counts them)
    taps = float(phys["tap_area_um2"][0]) if phys.get("tap_area_um2") else None
    routed = (routed_all - taps) if (routed_all is not None and taps is not None) else routed_all
    # the stage the figures come from: the final one, or global routing (a block that stops there, or one whose detailed
    # routing did not finish within the job: run.py then reports on the newest database there is)
    label = {"finish": "routed", "globalroute": "global route", "cts": "CTS", "detailedplace": "placed",
             "globalplace": "globally placed", "floorplan": "floorplan"}
    stage = ((label[have] + ("" if have == "finish" or (grt and have == "globalroute") else f" -- {run.get('status')}"))
             if routed else f"not routed -- {run.get('status') or 'no run'}")
    return dict(block=b, status=run.get("status"), stage=stage, period=prep.get("period_ps"),
                synth_cell=ys.get("stdcell_area_um2"), synth_cells=ys.get("stdcells"),
                routed_cell=routed, routed_cells=g("design__instance__count__stdcell"), taps_um2=taps,
                macro_lef=sum(x["lef_area_um2"] for x in mac), macro_model=sum(x["synth_model_um2"] for x in mac),
                macros=[(x["macro"], x.get("lanes", 1), x.get("lef_width_um"), x.get("lef_height_um")) for x in mac],
                die_um2=g("design__die__area"), core_um2=g("design__core__area"), die_um=dims("die_um"),
                core_um=dims("core_um"), util=g("design__instance__utilization"), have=have,
                areas={p: m.get(f"{p}__design__instance__area__stdcell") for p in order})


def fmt(x, nd=0):
    return "n/a" if x is None else f"{x:,.{nd}f}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--logs", required=True)
    ap.add_argument("--synth", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--json", default=None)
    ap.add_argument("--power", default=None, help="write the power table (default against recorded activity) here")
    args = ap.parse_args()
    S = load(args.synth)["units"]
    rows = {}
    for b in [u for u, _ in TILE_UNITS] + INNER:
        r = block(args.logs, b)
        if r:
            rows[b] = r
    ratios = [r["routed_cell"] / r["synth_cell"] for r in rows.values() if r["routed_cell"] and r["synth_cell"]]
    med = statistics.median(ratios) if ratios else 1.0
    utils = [r["util"] for b, r in rows.items() if r["util"] and not r["macros"] and r["util"] <= 1]
    med_util = statistics.median(utils) if utils else 0.6
    md = ["| block | stage | cells synthesized / routed | cell area synthesized / routed without taps (um2) | routed / synthesized | "
          "macros: LEF / model (um2) | die (um), utilization |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for b in [u for u, _ in TILE_UNITS] + INNER:
        r = rows.get(b)
        if not r:
            md.append(f"| `{b}` | not run | | | | | |")
            continue
        rat = (r["routed_cell"] / r["synth_cell"]) if (r["routed_cell"] and r["synth_cell"]) else None
        # the synthesis model as the synthesis job prices the block's leaves (synth_results.json; fakeram.py's macros.json
        # before drop 0.43's fix priced a port-B-only neo_mem_rf at x2.0)
        model = float(S.get(b, {}).get("macro_area_um2") or 0.0) or r["macro_model"]
        mac = (f"{fmt(r['macro_lef'])} / {fmt(model)} ("
               + ", ".join(f"{n} x {k}" if k > 1 else n for n, k, _, _ in r["macros"]) + ")") if r["macros"] else "--"
        util = f"{r['util'] * 100:.1f} %" if (r["util"] is not None and r["util"] <= 1) else fmt(r["util"], 1)
        die = f"{r['die_um'][0]:,.1f} x {r['die_um'][1]:,.1f}" if r["die_um"] else fmt(r["die_um2"])
        tp = f" (+ {fmt(r['taps_um2'], 0)} taps)" if r.get("taps_um2") else ""
        md.append(f"| `{b}` | {r['stage']} | {fmt(r['synth_cells'])} / {fmt(r['routed_cells'])} | {fmt(r['synth_cell'], 1)} / "
                  f"{fmt(r['routed_cell'], 1)}{tp} | {fmt(rat, 3)} | {mac} | {die}, {util} |")
    # the tile: cells + macros (the spec's comparison) and as placed (the floorplan's tile)
    tile_rows, tot_cell, tot_mac, syn_cell, syn_mac, net_cell, placed = [], 0.0, 0.0, 0.0, 0.0, 0.0, {}
    for u, k in TILE_UNITS:
        s = S.get(u, {})
        sc, sm = float(s.get("cell_area_um2") or 0.0), float(s.get("macro_area_um2") or 0.0)
        r = rows.get(u)
        if r and r["routed_cell"] and r["have"] not in UNPLACED:
            rc, rm, how = r["routed_cell"], r["macro_lef"], r["stage"]
            pa = r["core_um2"] if r["core_um2"] else (rc / med_util + rm)
        else:
            # no placed figures. A run that got as far as global placement or the floorplan: its cells there times the
            # growth its 16 x 8 counterpart (PROXY: the same flow on the same RTL at an eighth of the data PEs) showed from
            # that stage to routing -- the placement's repair, CTS, the routing's repair. Otherwise the synthesized
            # cells at the counterpart's routed / synthesized ratio (against the synthesis job's figure, this table's
            # basis), else at the median ratio. The macros at their LEF sizes when the block's run made them; the core
            # area the floorplan gave the block when its run got that far.
            px = rows.get(PROXY.get(u, ""))
            full = bool(px and px["routed_cell"] and px["have"] == "finish")
            at = px["areas"].get(r["have"]) if (full and r and r["routed_cell"] and r["have"] in UNPLACED) else None
            if at:
                grow = px["routed_cell"] / (at - (px["taps_um2"] or 0.0))
                rc = r["routed_cell"] * grow
                how = (f"its {UNPLACED[r['have']]}'s cells ({fmt(r['routed_cell'], 0)}) x {grow:.3f}: {PROXY[u]}'s growth "
                       f"from its {UNPLACED[r['have']]} to routing (the block's run: {r['status']})")
            else:
                psyn = float(S.get(PROXY.get(u, ""), {}).get("cell_area_um2") or 0.0)
                ratio, src = (px["routed_cell"] / psyn, f"{PROXY[u]}'s ratio") if (full and psyn) else (med, "the median ratio")
                rc = sc * ratio
                how = f"synthesized x {ratio:.3f} ({src}" + (f"; the block's own run ended at: {r['stage']})" if r else ")")
            rm = (r or {}).get("macro_lef") or sm
            pa = r["core_um2"] if (r and r["core_um2"]) else (rc / med_util + rm)
        tile_rows.append((u, k, sc, sm, rc, rm, how))
        placed[u] = k * pa
        tot_cell += k * rc
        tot_mac += k * rm
        syn_cell += k * sc
        syn_mac += k * sm
        net_cell += k * ((r or {}).get("synth_cell") or sc)
    own = float(S.get("neo_tile", {}).get("own_area_um2") or 0.0)
    tot_cell += own * med
    syn_cell += own
    net_cell += own
    placed["neo_tile_own"] = own * med / med_util
    syn_tile = float(S.get("neo_tile", {}).get("total_area_um2") or (syn_cell + syn_mac))
    md += ["", "| tile unit | in the tile | synthesized cells / macro model (um2) | routed cells / macro LEF (um2) | from |",
           "| --- | --- | --- | --- | --- |"]
    for u, k, sc, sm, rc, rm, how in tile_rows:
        md.append(f"| `{u}` | {k} | {fmt(sc, 0)} / {fmt(sm, 0)} | {fmt(rc, 0)} / {fmt(rm, 0)} | {how} |")
    md.append(f"| neo_tile's own logic | 1 | {fmt(own, 0)} / 0 | {fmt(own * med, 0)} / 0 | synthesized x {med:.3f} |")
    est = tot_cell + tot_mac
    md += [f"| **the tile** | | **{fmt(syn_cell, 0)} + {fmt(syn_mac, 0)} = {syn_tile / 1e6:.3f} mm2** | "
           f"**{fmt(tot_cell, 0)} + {fmt(tot_mac, 0)} = {est / 1e6:.3f} mm2** | routed / synthesized {est / syn_tile:.3f} "
           f"(cells {tot_cell / syn_cell:.3f}, macros {tot_mac / max(1.0, syn_mac):.3f}) |"]
    # the synthesized column is the synthesis job's stat, which still counts the dead full / half adders ORFS's synthesis
    # tail purges (prep.py): the netlists the blocks were placed from are smaller
    md += ["", f"The cells in the netlists the blocks were placed from (prep.py's: the synthesis job's netlists after ORFS's "
           f"synthesis tail -- the dead adders purged, the tie cells added): {fmt(net_cell, 0)} um2 for the tile's units, "
           f"{net_cell / syn_cell:.3f} of the synthesis job's figure; routed / placed netlist {tot_cell / net_cell:.3f}."]
    # the bank packed (the floorplan's tile): 16 lanes four across, a pin channel in front of each, rows 4 um apart
    bank = rows.get("bank_bist_wrap") or {}
    lane = next(((w, h) for n, k, w, h in bank.get("macros", []) if w and h), (323.57, 166.6))
    nl = sum(k for n, k, w, h in bank.get("macros", [])) or 16
    nrow = -(-nl // BANK_PER_ROW)
    bank_w = BANK_PER_ROW * (lane[0] + BANK_CHANNEL_UM) + BANK_CHANNEL_UM
    bank_h = nrow * (lane[1] + BANK_ROW_GAP_UM) + BANK_ROW_GAP_UM
    bank_logic = (bank.get("routed_cell") or 0.0) / med_util
    logic_placed = sum(v for u, v in placed.items() if u != "bank_bist_wrap")
    tile_placed = bank_w * bank_h + bank_logic + logic_placed
    md += ["", f"The tile as placed (the floorplan's tile macro): the bank's {nl} lanes packed {BANK_PER_ROW} across "
           f"({bank_w:,.1f} x {bank_h:,.1f} um with the {BANK_CHANNEL_UM:.0f} um pin channels, {bank_w * bank_h / 1e6:.3f} mm2) "
           f"+ its {fmt(bank.get('routed_cell'), 0)} um2 of cells at the median logic utilization {med_util * 100:.1f} % "
           f"+ the logic units at their routed blocks' core areas ({logic_placed / 1e6:.3f} mm2) = **{tile_placed / 1e6:.3f} mm2**."]
    with open(args.out, "w") as f:
        f.write("\n".join(md) + "\n")
    print("\n".join(md))
    if args.json:
        def syn(names):
            return sum(float(S.get(n, {}).get("cell_area_um2") or 0.0) for n in names)
        br = syn(("top_noc_router", "top_link_packer", "top_link_unpacker", "noc_axi_bridge_mem"))
        cp = syn(("top_ctrl", "mesh_ctrl"))
        out = dict(bank=dict(lane_um=list(lane), lanes=nl, per_row=BANK_PER_ROW, channel_um=BANK_CHANNEL_UM,
                             row_gap_um=BANK_ROW_GAP_UM, w_um=bank_w, h_um=bank_h, logic_um2=bank_logic),
                   tile=dict(estimate_um2=est, synthesized_um2=syn_tile, placed_um2=tile_placed),
                   blocks_placed=dict(neo_core=placed["neo_core"], tile_nic=placed["tile_nic"], noc_router=placed["noc_router"],
                                      link_pack=placed["link_packer"] + placed["link_unpacker"],
                                      control=placed["tile_dma"] + placed["prog_mem"] + placed["host_if"] + placed["esm"]
                                      + placed["neo_tile_own"]),
                   bridge_node_synth_um2=br, bridge_node_placed_um2=br * med / med_util,
                   control_plane_synth_um2=cp, control_plane_placed_um2=cp * med / med_util,
                   routed_over_synth=med, logic_utilization=med_util,
                   # the CI runs the figures came from (each log directory's RUN.txt), not the paths of a checkout
                   source=dict(synth=run_of(os.path.join(os.path.dirname(os.path.abspath(args.synth)), "RUN.txt")),
                               pnr={b: run_of(os.path.join(args.logs, b, "RUN.txt")) for b in rows}))
        with open(args.json, "w") as f:
            json.dump(out, f, indent=1)
    if args.power:
        power_table(args.logs, [u for u, _ in TILE_UNITS] + INNER, rows, args.power)
    return 0


def power_table(logs, blocks, rows, out):
    """Per block: power at the default activity and with the recorded one (mW), dynamic = internal + switching."""
    md = ["| block | stage, period | recording (window, MAC utilization) | flops recorded / all (at the default) | default: dynamic + leakage = "
          "total | recorded: dynamic + leakage = total | recorded / default (dynamic) |",
          "| --- | --- | --- | --- | --- | --- | --- |"]
    for b in blocks:
        d = os.path.join(logs, b)
        nd = os.path.join(d, "orfs", "reports", "neo")
        try:
            p_def = power_groups(open(os.path.join(nd, "power_default.txt")).read()).get("Total")
        except OSError:
            p_def = None
        try:
            p_act = power_groups(open(os.path.join(nd, "power_activity.txt")).read()).get("Total")
        except OSError:
            p_act = None
        if not p_def and not p_act:
            continue
        act = load(os.path.join(d, "in", "activity.json")) or {}
        r = rows.get(b) or {}
        fk = act.get("flops_by_kind") or {}
        rec = (f"{act.get('window_cycles', 0):,} cycles, " + (f"{100.0 * act['mac_util']:.1f} %" if act.get("mac_util") is not None
                                                             else "no array")) if act else "--"
        # drop 0.44 (A4): the share of the block's flops that kept the propagated default (no recorded bit), so a figure
        # that rests on defaults says so in the table
        nd_ = fk.get("default", 0)
        nf_ = act.get("flops", 0) or 0
        fl = (f"{fk.get('exact', 0) + fk.get('folded', 0):,} / {nf_:,}" + (f" ({100.0 * nd_ / nf_:.1f} % at the default)" if nf_ else "")) if act else "--"

        def tot(v):                # the sum of the parts (report_power prints each column to three significant digits)
            return "--" if not v else f"{(v[0] + v[1]) * 1e3:.3f} + {v[2] * 1e3:.4f} = **{(v[0] + v[1] + v[2]) * 1e3:.3f}**"
        rat = f"{(p_act[0] + p_act[1]) / (p_def[0] + p_def[1]):.3f}" if (p_def and p_act and (p_def[0] + p_def[1]) > 0) else "--"
        # the database the power came from (neo_report's): named when it is not the final or the global-route one
        run = load(os.path.join(d, "run.json")) or {}
        db = next((st.get("db") for st in run.get("stages", []) if st.get("stage") == "neo_report"), None)
        where = "" if db in (None, "6_final.odb", "5_1_grt.odb") else f" (power on `{db}`: no wires, no clock tree)"
        md.append(f"| `{b}` | {r.get('stage', 'n/a')}{where}, {fmt(r.get('period'))} ps | {rec} | {fl} | {tot(p_def)} | "
                  f"{tot(p_act)} | {rat} |")
    with open(out, "w") as f:
        f.write("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    raise SystemExit(main())
