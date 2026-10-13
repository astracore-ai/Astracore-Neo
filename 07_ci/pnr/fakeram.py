#!/usr/bin/env python3
"""fakeram.py -- the memory macros of the place-and-route job (drop 0.42, stream A2b): one FakeRAM2.0 LEF + LIB per
distinct memory-leaf instance type of a unit's subtree, and the wrapper that puts the leaf's ports around it.

synth.py black-boxes the two leaf modules of 01_rtl/neo_mem.sv and names every parameterization after its geometry
(`neo_mem_rf_w256_d128_g2_rb1`: 256-bit words, 128 deep, 2 write groups, the registered read port B). The netlist
instantiates that name with every port of the leaf: the write port (we[G], waddr, wdata), the asynchronous read port A
(ra_addr -> ra_data), the registered read port B (rb_en, rb_addr -> rb_data) and the testbench hooks (bd_*, fi_*), which
DV_HOOKS = 0 ties off. A memory compiler's macro has only the ports the silicon uses, so the swap is the usual
wrapper (09_synthesis/memories.md): a module with the leaf's name and ports, the macro inside it on the functional
ports, the tied-off inputs left unconnected and the dropped outputs at 0 (prep.py checks in the synthesized netlist that
the parent ties those inputs to constants and reads none of those outputs, so nothing functional is cut away).

The macro comes from FakeRAM2.0 -- the generator ORFS's asap7 platform uses, vendored in OpenROAD-flow-scripts at
tools/FakeRAM2.0 (the commit tools.sh pins) -- with the platform's process parameters (orfs_asap7/generate.py, the same
as platforms/asap7/fakeram.cfg): bit cell 2 contacted-poly pitches x 10 fin pitches = 0.108 x 0.270 um, x 1.2 x 1.2
periphery = 0.042 um2 per bit, the width snapped to 0.19 um and the height to 1.4 um, every signal pin a 24 nm M4 stub
on the left edge at the 48 nm track pitch (the height grows to fit the pins), VDD / VSS rails on M4, M1-M4 obstructed.
FakeRAM2.0's own memory classes have fixed port names (rd_out / wd_in / addr_in ... or dout_a / din_a ...) and two
read-write ports for a dual-port part; the leaves need their own names, a write-group enable bus and a write port
beside a read port. So the macro here is FakeRAM2.0's Memory with this module's port list: its PhysicalData sizing,
BasicPortCreator geometry (pins, rails, obstructions), LefExporter and the LibertyExporter's table writers, only the
pin list and the cell body chosen here:
  RB = 1 (port A tied off -- the core's three arrays since drop 0.41): a 1-write-1-read synchronous SRAM `_1w1r`:
         clk, we[G], waddr, wdata, rb_en, rb_addr, rb_data (clock-to-Q at the access time, setup / hold on the inputs)
  RB = 0 (port A read -- the NIC's drain FIFO and partial-sum rows): a register file with an asynchronous read `_1w1a`:
         clk, we[G], waddr, wdata, ra_addr, ra_data (address-to-data at the access time, from every address bit)
The timing is the synthesis flow's macro model (synth.py PDKS['asap7']['macro'], FakeRAM2.0's ASAP7 defaults): 218 ps
access, 50 ps setup / hold, 157 ps minimum period, 5 fF per input. A write group g writes bits [g*W/G +: W/G] (a lane
write enable, the bank's 39-bit SECDED lanes or the pair leaves' two entries).

Usage: fakeram.py --synth <synth.py --out> --unit <unit> --fakeram <FakeRAM2.0 dir> --out <dir>
       fakeram.py --leaf <leaf type> [--leaf ...] --fakeram <FakeRAM2.0 dir> --out <dir>
Writes <dir>/<macro>.lef, <dir>/<macro>.lib, <dir>/wrappers.v and <dir>/macros.json (geometry, pins, the synthesis
model's area beside the LEF's) for every leaf type in the unit's subtree; none when the subtree has no memory leaf.
--leaf takes synth.py's leaf names (neo_mem_rf_w1056_d512_g1_rb1, neo_mem_sp_w624_d32768_g16) without a synthesis run,
the pins from neo_mem.sv's port list: the macro table of 09_synthesis/memories.md.
"""
import argparse
import json
import math
import os
import sys

# the synthesis flow's ASAP7 macro model (07_ci/synth/synth.py PDKS["asap7"]["macro"]) in FakeRAM2.0's units (ns, pF)
TIMING = {"access_time_ns": 0.218, "t_setup_ns": 0.050, "t_hold_ns": 0.050, "cycle_time_ns": 0.157, "cap_input_pf": 0.005}
# ORFS asap7 FakeRAM process (tools/FakeRAM2.0/orfs_asap7/generate.py ASAP7_PROCESS_CONFIG = platforms/asap7/fakeram.cfg)
PROCESS = {"tech_nm": 7, "voltage": 0.7, "metal_prefix": "M", "metal_layer": "M4", "pin_width_nm": 24, "pin_pitch_nm": 48,
           "metal_track_pitch_nm": 48, "manufacturing_grid_nm": 1, "contacted_poly_pitch_nm": 54, "fin_pitch_nm": 27,
           "column_mux_factor": 1, "snap_width_nm": 190, "snap_height_nm": 1400}
# a macro narrower than one M5 strap pitch of the platform's grid (5.4 um) plus a strap and the rails' inset can fall
# between two straps of a net and leave its macro grid empty (PDN-0233); every leaf here is far wider, the floor is a guard
MIN_WIDTH_UM = 5.4 + 0.12 + 2 * 0.048
# the synthesis model's area factors (synth.py MACRO_AREA_FACTOR), for the comparison in macros.json
SYNTH_AREA_PER_BIT = 0.0292 * 1.44
SYNTH_FACTOR = {"neo_mem_sp": 1.0, "neo_mem_rf": 1.5, "neo_mem_rf_rb": 2.0}
HOOK_INPUTS = ("bd_we", "bd_addr", "bd_wdata", "fi_en", "fi_or", "fi_addr", "fi_mask")
HOOK_OUTPUTS = ("bd_rdata",)


def macro_kind(rec):
    """(macro name, functional pins [(name, width, direction)], the leaf pins tied off, the leaf outputs dropped)."""
    W, D, G, AW = rec["W"], rec["D"], rec.get("G", 1), rec["AW"]
    base = rec.get("base") or rec["name"].split("_w")[0]
    we = ("we", G, "input")
    write = [("clk", 1, "input"), we, ("waddr", AW, "input"), ("wdata", W, "input")]
    if base == "neo_mem_sp":                     # registered read: re / raddr -> rdata (the bank's lanes)
        name = f"fakeram7_{D}x{W}_g{G}_1w1r"
        fn = write + [("re", 1, "input"), ("raddr", AW, "input"), ("rdata", W, "output")]
        tied, dropped = HOOK_INPUTS, HOOK_OUTPUTS
    elif rec.get("RB", 0):
        name = f"fakeram7_{D}x{W}_g{G}_1w1r"
        fn = write + [("rb_en", 1, "input"), ("rb_addr", AW, "input"), ("rb_data", W, "output")]
        tied, dropped = HOOK_INPUTS + ("ra_addr",), HOOK_OUTPUTS + ("ra_data",)
    else:
        name = f"fakeram7_{D}x{W}_g{G}_1w1a"
        fn = write + [("ra_addr", AW, "input"), ("ra_data", W, "output")]
        tied, dropped = HOOK_INPUTS + ("rb_en", "rb_addr"), HOOK_OUTPUTS + ("rb_data",)
    return name, fn, tied, dropped


def build(rec, fakeram_dir):
    """The FakeRAM2.0 memory object of one leaf type (sized, pins and rails created)."""
    utils = os.path.join(fakeram_dir, "utils")
    if utils not in sys.path:
        sys.path.insert(0, utils)
    from class_process import Process            # noqa: E402  (FakeRAM2.0's modules import each other by bare name)
    from timing_data import TimingData
    from memory_config import MemoryConfig
    from class_memory import Memory
    from basic_port_creator import BasicPortCreator
    from port import Port
    from rw_port_group import RWPortGroup

    name, fn, tied, dropped = macro_kind(rec)
    groups = [[p for p in fn if p[2] == "output"],                      # read data (dout first, as FakeRAM orders)
              [p for p in fn if p[0] == "wdata"],                       # write data
              [p for p in fn if p[0].endswith("addr")],                 # addresses
              [p for p in fn if p[0] in ("we", "re", "rb_en", "clk")]]  # enables and the clock

    class LeafPortCreator(BasicPortCreator):
        """BasicPortCreator's pin stacking (left edge, the pin and group pitches PhysicalData computed) over this
        macro's own pin groups; its rails and obstructions unchanged."""

        def create_signal_pins(self, pin_pitch, group_pitch):
            y = self._mem.get_process_data().y_step
            for gi, grp in enumerate(groups):
                if gi:
                    y += group_pitch
                for pn, w, d in grp:
                    direction = Port.Direction.OUTPUT if d == "output" else Port.Direction.INPUT
                    if w == 1 and pn not in self._mem.bus_names:
                        y = self.add_pin(pn, direction, y, pin_pitch)
                    else:
                        y = self.write_signal_bus(pn, 0, w, direction, y, pin_pitch)

    class LeafMacro(Memory):
        def __init__(self):
            self.fn = fn
            # a G = 1 write enable is the leaf's scalar `we`; G > 1 the bus we[G-1:0]
            self.bus_names = {pn for pn, w, d in fn if w > 1}
            Memory.__init__(self, MemoryConfig(name, rec["W"], rec["D"], 1, 0), Process(PROCESS), TimingData(TIMING))
            if self.addr_width != rec["AW"]:
                raise ValueError(f"{name}: FakeRAM's address width {self.addr_width} != the leaf's AW {rec['AW']}")
            phys = self.get_physical_data()
            if phys.get_width() < MIN_WIDTH_UM:
                phys.set_extents(MIN_WIDTH_UM, phys.get_height(False))
                phys.snap_to_grid(self.process.snap_width_nm, self.process.snap_height_nm)
                phys.set_pin_pitches(name, self.get_num_pins(), self.process.pin_pitch_um, self.process.y_offset)
            # the port groups the LEF / liberty exporters walk: the write port, then the read port
            wr = RWPortGroup()
            wr.set_clock_name("clk")
            wr.set_address_bus_name("waddr")
            wr.set_data_input_bus_name("wdata")
            if "we" in self.bus_names:
                wr.add_related_bus({"name": "we", "lsb": 0, "msb": rec.get("G", 1) - 1})
            else:
                wr.add_related_pin("we")
            rd = RWPortGroup()
            for pn, w, d in fn:
                if d == "output":
                    rd.set_data_output_bus_name(pn)
                elif pn in ("raddr", "rb_addr", "ra_addr"):
                    rd.set_address_bus_name(pn)
                elif pn in ("re", "rb_en"):
                    rd.add_related_pin(pn)
            self.add_rw_port_group(wr)
            self.add_rw_port_group(rd)
            LeafPortCreator(self).create_ports()

        def get_num_pins(self):
            return sum(w for _, w, _ in self.fn)

    return LeafMacro(), name, fn, tied, dropped


def liberty(mem, fn, fakeram_dir):
    """The liberty view: FakeRAM2.0's LibertyExporter (header, units, templates, its pin / bus / clock / output writers)
    with this macro's cell body. Clocked read: rising_edge clk -> data at the access time. Asynchronous read (port A):
    a combinational arc from every address bit (OpenSTA takes no bus-to-bus arc of different widths), same delay."""
    utils = os.path.join(fakeram_dir, "utils")
    if utils not in sys.path:
        sys.path.insert(0, utils)
    from liberty_exporter import LibertyExporter

    class LeafLiberty(LibertyExporter):
        def write_bus_defs(self, f):
            m = self.get_memory()
            n = m.get_name()
            self.write_bus_def(f, n + "_DATA", m.get_width(), m.get_data_bus_msb())
            self.write_bus_def(f, n + "_ADDRESS", m.get_addr_width(), m.get_addr_bus_msb())
            for pn, w, d in fn:
                if w > 1 and pn not in ("wdata", "rdata", "rb_data", "ra_data") and not pn.endswith("addr"):
                    self.write_bus_def(f, f"{n}_{pn}", w, w - 1)

        def write_cell_header(self, f):
            # the snapped LEF footprint (FakeRAM2.0 writes the unsnapped bit-cell area): the liberty and the LEF agree
            f.write("cell(%s) {\n" % self.get_memory().get_name())
            f.write("    area : %.6f;\n" % self.get_memory().physical.get_area(True))
            f.write("    interface_timing : true;\n")

        def write_async_output(self, f, n, pin, addr, aw):
            t = self.get_memory().get_timing_data()
            f.write("    bus(%s)   {\n" % pin)
            f.write("        bus_type : %s_DATA;\n" % n)
            f.write("        direction : output;\n")
            f.write("        max_capacitance : %.6f;\n" % t.max_load)
            f.write("        timing() {\n")
            f.write('            related_pin : "%s" ;\n' % " ".join(f"{addr}[{i}]" for i in range(aw)))
            f.write("            timing_sense : non_unate;\n")
            for rf in ("rise", "fall"):
                self.write_cell_delay(f, rf, n + "_mem_out_delay_template", t.slew_indices, t.load_indices, t.access_time_ns)
            for rf in ("rise", "fall"):
                self.write_cell_transition(f, rf, n + "_mem_out_slew_template", t.load_indices, t.min_slew, t.max_slew)
            f.write("        }\n")
            f.write("    }\n")

        def write_input_bus(self, f, n, pin, bus_type, constrained):
            t = self.get_memory().get_timing_data()
            f.write("    bus(%s)   {\n" % pin)
            f.write("        bus_type : %s;\n" % bus_type)
            if pin == "wdata":
                f.write("        memory_write() {\n            address : waddr;\n            clocked_on : \"clk\";\n        }\n")
            f.write("        direction : input;\n")
            f.write("        capacitance : %.6f;\n" % t.min_driver_in_cap)
            if constrained:
                self.write_timing(f, n, "clk", t.slew_indices, t.t_setup_ns, t.t_hold_ns)
            self.write_internal_power(f, n + "_energy_template_sigslew", t.slew_indices, t.pin_dynamic)
            f.write("    }\n")

        def write_cell(self, f):
            m = self.get_memory()
            n = m.get_name()
            f.write("    memory() {\n        type : ram;\n        address_width : %d;\n        word_width : %d;\n    }\n"
                    % (m.get_addr_width(), m.get_width()))
            self.write_clk_pin(f, "clk")
            aw = m.get_addr_width()
            for pn, w, d in fn:
                if pn == "clk":
                    continue
                if d == "output":
                    if pn == "ra_data":
                        self.write_async_output(f, n, pn, "ra_addr", aw)
                    else:
                        addr = "raddr" if pn == "rdata" else "rb_addr"
                        self.write_output_bus(f, n, pn, "clk", True, addr)
                elif pn == "ra_addr":                      # the asynchronous port's address: no clock constraint
                    self.write_input_bus(f, n, pn, n + "_ADDRESS", False)
                elif pn.endswith("addr"):
                    self.write_address_bus(f, n, pn, "clk")
                elif pn == "wdata":
                    self.write_input_bus(f, n, pn, n + "_DATA", True)
                elif w > 1:
                    self.write_input_bus(f, n, pn, f"{n}_{pn}", True)
                else:
                    self.write_pin(f, n, pn, "clk")

    return LeafLiberty(mem)


def lef_exporter(mem, fakeram_dir):
    utils = os.path.join(fakeram_dir, "utils")
    if utils not in sys.path:
        sys.path.insert(0, utils)
    from lef_exporter import LefExporter
    return LefExporter(mem)


def wrapper_verilog(rec, mname, fn, tied, dropped):
    """The leaf's name and ports (synth.py's macro_pins: the ports as the netlist connects them) around the macro."""
    pins = rec["pins"]
    fn_names = {p[0] for p in fn}
    decl = []
    for pn, w, d in pins:
        decl.append(f"  {d}{(' [%d:0]' % (w - 1)) if w > 1 else ''} {pn};")
    L = [f"// {rec['name']}: the leaf's ports (01_rtl/neo_mem.sv, DV_HOOKS = 0) around the FakeRAM2.0 macro {mname};",
         f"// tied off in silicon and unconnected here: {', '.join(p for p in tied if p in [q[0] for q in pins])};",
         f"// dropped outputs at 0: {', '.join(p for p in dropped if p in [q[0] for q in pins])} (prep.py checks the parent reads none of them)",
         f"module {rec['name']}({', '.join(p[0] for p in pins)});"] + decl
    conn = ", ".join(f".{pn}({pn})" for pn, _, _ in fn)
    L.append(f"  {mname} u_macro ({conn});")
    for pn, w, d in pins:
        if d == "output" and pn not in fn_names:
            L.append(f"  assign {pn} = {w}'b0;")
    missing = sorted(fn_names - {p[0] for p in pins})
    if missing:
        raise ValueError(f"{rec['name']}: the netlist's instance has no {missing} -- not the leaf this macro models")
    L.append("endmodule")
    return "\n".join(L) + "\n"


def leaf_record(name):
    """A leaf type by synth.py's name, with the pins neo_mem.sv declares (a 1-bit port scalar, as synth.py's stubs)."""
    import re
    m = re.fullmatch(r"(neo_mem_sp|neo_mem_rf)_w(\d+)_d(\d+)_g(\d+)(?:_rb(\d))?", name)
    if not m:
        raise SystemExit(f"fakeram.py: {name} is not a leaf name (neo_mem_sp_w<W>_d<D>_g<G> / neo_mem_rf_w<W>_d<D>_g<G>_rb<RB>)")
    base, W, D, G, RB = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5) or 0)
    AW = max(1, (D - 1).bit_length()) if D > 1 else 1
    if base == "neo_mem_sp":
        ports = [("clk", 1, "input"), ("we", G, "input"), ("waddr", AW, "input"), ("wdata", W, "input"), ("re", 1, "input"),
                 ("raddr", AW, "input"), ("rdata", W, "output")]
    else:
        ports = [("clk", 1, "input"), ("we", G, "input"), ("waddr", AW, "input"), ("wdata", W, "input"), ("ra_addr", AW, "input"),
                 ("ra_data", W, "output"), ("rb_en", 1, "input"), ("rb_addr", AW, "input"), ("rb_data", W, "output")]
    ports += [("bd_we", G, "input"), ("bd_addr", AW, "input"), ("bd_wdata", W, "input"), ("bd_rdata", W, "output"),
              ("fi_en", 1, "input"), ("fi_or", 1, "input"), ("fi_addr", AW, "input"), ("fi_mask", W, "input")]
    return dict(name=name, base=base, W=W, D=D, G=G, RB=RB, AW=AW, bits=W * D, instances=None, pins=ports)


def subtree_macros(results, unit):
    seen, order, recs = set(), [unit], {}
    while order:
        n = order.pop()
        if n in seen:
            continue
        seen.add(n)
        r = results.get(n)
        if r is None or r.get("failed"):
            raise SystemExit(f"fakeram.py: unit {n} is missing or failed in the synthesis results")
        for m, rec in (r.get("macros") or {}).items():
            if m not in recs:
                recs[m] = dict(rec, pins=(r.get("macro_pins") or {}).get(m))
        order += list((r.get("children") or {}).keys())
    return [recs[k] for k in sorted(recs)]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synth", help="synth.py's output directory (synth_results.json)")
    ap.add_argument("--unit", help="the unit whose subtree's leaves are made")
    ap.add_argument("--leaf", action="append", default=[], help="a leaf type by name, without synthesis results")
    ap.add_argument("--fakeram", required=True, help="FakeRAM2.0 (OpenROAD-flow-scripts tools/FakeRAM2.0)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if args.leaf:
        recs = [leaf_record(n) for n in args.leaf]
        args.unit = args.unit or "leaves"
    elif args.synth and args.unit:
        with open(os.path.join(args.synth, "synth_results.json")) as f:
            results = json.load(f)["units"]
        recs = subtree_macros(results, args.unit)
    else:
        ap.error("--synth and --unit, or --leaf")
    os.makedirs(args.out, exist_ok=True)
    table, wrappers = [], []
    for rec in recs:
        if not rec.get("pins"):
            raise SystemExit(f"fakeram.py: no pin list for {rec['name']} in the synthesis results")
        mem, mname, fn, tied, dropped = build(rec, args.fakeram)
        lef = os.path.join(args.out, mname + ".lef")
        lib = os.path.join(args.out, mname + ".lib")
        lef_exporter(mem, args.fakeram).export_file(lef)
        liberty(mem, fn, args.fakeram).export_file(lib)
        wrappers.append(wrapper_verilog(rec, mname, fn, tied, dropped))
        phys = mem.get_physical_data()
        kind = rec["base"] if not (rec["base"] == "neo_mem_rf" and rec.get("RB", 0)) else "neo_mem_rf_rb"
        bits = rec["W"] * rec["D"]
        bitcell_h = rec["D"] * PROCESS["fin_pitch_nm"] * 10 * 1.2 / 1000.0
        table.append(dict(leaf=rec["name"], macro=mname, W=rec["W"], D=rec["D"], G=rec.get("G", 1), RB=rec.get("RB", 0),
                          AW=rec["AW"], bits=bits, instances=rec.get("instances"),
                          pins=sum(w for _, w, _ in fn), functional_pins=[[p[0], p[1], p[2]] for p in fn],
                          lef_width_um=round(phys.get_width(), 4), lef_height_um=round(phys.get_height(), 4),
                          lef_area_um2=round(phys.get_area(True), 3), bitcell_area_um2=round(phys.get_area(False), 3),
                          pin_limited=bool(phys.get_height(False) > bitcell_h + 1e-6),
                          um2_per_bit=round(phys.get_area(True) / bits, 5),
                          synth_model_um2=round(bits * SYNTH_AREA_PER_BIT * SYNTH_FACTOR[kind], 3),
                          synth_factor=SYNTH_FACTOR[kind], access_ps=TIMING["access_time_ns"] * 1000,
                          setup_hold_ps=TIMING["t_setup_ns"] * 1000, lef=os.path.basename(lef), lib=os.path.basename(lib),
                          lef_bytes=os.path.getsize(lef), lib_bytes=os.path.getsize(lib)))
        print(f"{rec['name']:32s} -> {mname:28s} {phys.get_width():8.3f} x {phys.get_height():8.3f} um "
              f"= {phys.get_area(True):10.1f} um2 ({table[-1]['um2_per_bit']:.4f} um2/bit; synthesis model "
              f"{table[-1]['synth_model_um2']:.1f} um2 at x{SYNTH_FACTOR[kind]}){' pin-limited' if table[-1]['pin_limited'] else ''}")
    with open(os.path.join(args.out, "wrappers.v"), "w") as f:
        f.write("// generated by 07_ci/pnr/fakeram.py: the memory leaves' wrappers around their FakeRAM2.0 macros\n")
        f.writelines(wrappers)
    with open(os.path.join(args.out, "macros.json"), "w") as f:
        json.dump(dict(unit=args.unit, timing=TIMING, process=PROCESS, macros=table), f, indent=1)
    print(f"fakeram.py: {len(table)} macro(s) for {args.unit} in {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
