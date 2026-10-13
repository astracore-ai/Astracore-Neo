#!/usr/bin/env python3
"""synth.py -- the open-PDK synthesis job (drop 0.40): Yosys with the slang front end, OpenSTA, the ASAP7 and Nangate45
libraries, the memory leaves as macros.

Runs from the flat layout (`rtl/` beside `synth/`; `make synth` in CI). The design is synthesized as UNITS -- one Yosys run
per module at its silicon parameters, every child module that is a unit of its own black-boxed (its logic is synthesized once,
in its own unit, and instantiated by name), the small helpers (delay lines, the ECC and CRC blocks, the checkers) flattened into
their parents -- because the tile's hierarchy is wide (1,056 PEs, 64 sources in the NIC) and one read of it is beyond the
sandbox's memory in Yosys's own front end, while slang reads one module in seconds. Per unit:
  1. slang reads the module at its parameters with its children and the memory leaves (neo_mem_sp / neo_mem_rf,
     01_rtl/neo_mem.sv) as black boxes; the black-box instances are regrouped by their parameters: a memory leaf becomes a
     macro cell named by its width / depth / ports, a child instance the unit that synthesizes that parameterization (the
     check-column PE is `mac_pe_chk`, the data PE `mac_pe`) -- the hierarchy check of the job: every parameterization of a
     child must have a unit, and the parameters the unit declares must be the ones the parent uses.
  2. Yosys: `synth -booth -noabc` with ORFS's Kogge-Stone carry-lookahead map, `extract_fa` onto the library's full / half
     adders, `dfflibmap`, ABC's speed script at the period against the liberty files; `stat`, `check`, the netlist, its JSON.
     A net with more than MAX_FANOUT loads (a unit's control fanned out to black-boxed children -- the sequencer's load
     strobe to 1,056 PEs) gets a tree of the library's buffers in the netlist (the synthesis-stage stand-in for the
     placer's repeaters; clk and rst_n excepted) and the netlist is re-read for the stat.
  3. One liberty file for every memory macro of the design from the macro model of MACRO (area per bit, access time,
     setup / hold: FakeRAM2.0's 7 nm bit cell for ASAP7, ORFS's FakeRAM45 macros for Nangate45 -- drop 0.42).
  4. OpenSTA on the unit's netlist with its child units as extracted timing models (each unit's run ends with
     `write_timing_model`; the parent reads the children's models, so the paths inside a child are the child's report and
     the paths between units the parent's -- a flat read of the tile's 2.5 M cells needs more than 6 GB): the tile's SDC
     (09_synthesis/constraints.sdc in the library's units) or the generic block SDC (the clock, 20 % of the period on the
     I/O); the worst paths, WNS / TNS, the minimum period, the endpoint slacks.
  5. The gates: every unit synthesizes; no latch; no multiply-driven net; the hierarchy check; the PE count = ROWS x
     (COLS + 1) on the cores; at DV_HOOKS = 0 no dv_* input of any unit drives logic and every dv_* output is constant or a
     child's output; every OpenSTA run completes. Cells, flops and area are summed through the tree (instances x unit).
  6. (drop 0.42) --sweep: OpenSTA again per unit at every period of a list, one session per unit reading each period's
     SDC in turn (the children's timing models do not depend on the period): WNS, TNS, violating endpoints of all, the
     minimum period, the worst path; and the verdict over the silicon tile's units -- the smallest swept period at which
     every unit but the PE-limited ones (mac_pe, mac_pe_chk, systolic_array, neo_mac_core and their parents) is clean,
     the PE's own stage, the period the tile runs at on the proxy (the larger of the two).
  Then report.py writes synth_report.md from synth_results.json (and, given two outputs, the two libraries side by side).
  Drop 0.42 also made Nangate45 a full second library: its macro model is derived from the platform's FakeRAM45 liberty
  files (derive_macro), and a memory leaf is priced by the read ports it uses (macro_kind), not by its RB parameter.

Environment: NEO_SYNTH_TOOLS (default /opt/neo-synth, where tools.sh installs: oss-cad-suite/, sta, pdk/lib/{asap7,
nangate45,common}), or NEO_YOSYS, NEO_STA, NEO_PDK_LIB for the individual tools.
"""
import argparse
import concurrent.futures
import gc
import json
import math
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------------------------------------------------
# the libraries
# ---------------------------------------------------------------------------------------------------------------------
PDKS = {
    "asap7": dict(
        dir="asap7",
        # ASAP7 7.5-track RVT cells from OpenROAD-flow-scripts platforms/asap7/lib/NLDM (gunzipped by tools.sh);
        # SS = 0.63 V / 100 C (the worst-case corner of the proxy), TT = 0.70 V / 25 C
        libs={
            "SS": ["asap7sc7p5t_AO_RVT_SS_nldm_211120.lib", "asap7sc7p5t_INVBUF_RVT_SS_nldm_220122.lib",
                   "asap7sc7p5t_OA_RVT_SS_nldm_211120.lib", "asap7sc7p5t_SIMPLE_RVT_SS_nldm_211120.lib",
                   "asap7sc7p5t_SEQ_RVT_SS_nldm_220123.lib"],
            "TT": ["asap7sc7p5t_AO_RVT_TT_nldm_211120.lib", "asap7sc7p5t_INVBUF_RVT_TT_nldm_220122.lib",
                   "asap7sc7p5t_OA_RVT_TT_nldm_211120.lib", "asap7sc7p5t_SIMPLE_RVT_TT_nldm_211120.lib",
                   "asap7sc7p5t_SEQ_RVT_TT_nldm_220123.lib"],
        },
        seq_lib="asap7sc7p5t_SEQ_RVT_{corner}_nldm_220123.lib",
        time_unit_ps=1.0,                     # the liberty time unit in ps (ASAP7: 1 ps)
        dont_use=["*x1p*_ASAP7*", "*xp*_ASAP7*", "SDF*", "ICG*"],      # ORFS's list: fractional drives, scan flops, gaters
        abc_driver="BUFx2_ASAP7_75t_R", abc_load_ff=3.898,
        repeater=("BUFx2_ASAP7_75t_R", "A", "Y"),   # the buffer of the fanout trees (cell, input pin, output pin)
        # the macro model: ABKGroup FakeRAM2.0 at ORFS's asap7 fakeram.cfg -- bit cell 10 fin pitches (27 nm) x 2 CPP (54 nm)
        # = 0.0292 um2, x 1.2 x 1.2 periphery = 0.042 um2 per bit; access 218 ps, setup / hold 50 ps, 5 fF per input
        macro=dict(area_per_bit_um2=0.0292 * 1.44, tcq=218.0, tsu=50.0, th=50.0, cap=5.0, min_period=157.0,
                   note="FakeRAM2.0 7 nm (ORFS asap7/fakeram.cfg): 0.042 um2/bit, 218 ps access, 50 ps setup/hold"),
        default_period_ps=500.0,
        sweep_ps=[500.0, 750.0, 1000.0, 1250.0, 1500.0],
        ratio_note="ASAP7 is an academic 7 nm predictive PDK: no foundry data; the figures are a proxy for the N5A target",
    ),
    "nangate45": dict(
        dir="nangate45",
        libs={"TT": ["NangateOpenCellLibrary_typical.lib"]},
        seq_lib="NangateOpenCellLibrary_typical.lib",
        time_unit_ps=1000.0,                  # 1 ns
        dont_use=["TAPCELL_X1", "FILLCELL_X1", "AOI211_X1", "OAI211_X1"],      # ORFS's DONT_USE_CELLS for the platform
        abc_driver="BUF_X1", abc_load_ff=3.898,                               # ORFS's ABC_DRIVER_CELL / ABC_LOAD_IN_FF
        repeater=("BUF_X2", "A", "Z"),
        # the macro model from ORFS's FakeRAM45 macros (drop 0.42; derive_macro below reads them): the area per bit is the
        # sum of their areas over the sum of their bits, the access time the slowest clock-to-output, setup / hold and the
        # minimum period the largest; this dict is the fallback when no FakeRAM45 liberty is found (the 2048 x 39 alone)
        macro_libs=["fakeram45_2048x39.lib", "fakeram45_512x64.lib"],
        macro=dict(area_per_bit_um2=45478.818 / (2048 * 39), tcq=0.435, tsu=0.050, th=0.050, cap=5.0, min_period=0.321,
                   note="ORFS fakeram45_2048x39: 0.569 um2/bit, 0.435 ns access, 50 ps setup/hold"),
        default_period_ps=500.0,
        # the typical corner at 1.1 V is faster than ASAP7's SS at 0.63 V (the PE: 0.76 ns against 1.03 ns): the same
        # grid, 0.5 .. 1.5 ns, brackets it
        sweep_ps=[500.0, 750.0, 1000.0, 1250.0, 1500.0],
        ratio_note="Nangate45 (FreePDK45) is a 45 nm open library: a second data point, not a 2 GHz node",
    ),
}
# area factors of the memory leaves against the single-port bit cell, by the read ports an instance uses (a documented
# assumption of the proxy: an asynchronous read port, and a second read port, cost bit-cell area). Drop 0.42: by use,
# not by the RB parameter -- a neo_mem_rf whose port A is tied off and read only on its registered port B is the same
# one-write one-read synchronous array as neo_mem_sp (the core's three arrays since drop 0.41, the NIC's drain FIFO
# since 0.42), which the parameter priced as an array with two read ports.
MACRO_AREA_FACTOR = {"neo_mem_sp": 1.0, "neo_mem_rf": 1.5, "neo_mem_rf_b": 1.0, "neo_mem_rf_rb": 2.0}
MEM_LEAVES = ("neo_mem_sp", "neo_mem_rf")


def derive_macro(pdk, libdir):
    """The macro model of a library from the FakeRAM liberty files of PDKS[...]['macro_libs'] found in libdir: area per
    bit = sum of areas / sum of bits, access = the slowest clock-to-output of rd_out, setup / hold / minimum period = the
    largest; the input capacitance = a data pin's (the smallest pin capacitance of a macro: its clock pin's is larger),
    the largest over the macros. Returns (macro dict, the macros read) -- the fallback dict and [] when none is found.
    Values in the liberty's time unit, as the library's own (FakeRAM45: ns)."""
    found = []
    for fn in pdk.get("macro_libs", []):
        path = os.path.join(libdir, fn)
        if not os.path.isfile(path):
            continue
        txt = open(path).read()
        cell = re.search(r"\bcell\s*\(\s*(\w+)\s*\)", txt)
        area = re.search(r"\barea\s*:\s*([0-9.]+)", txt)
        aw = re.search(r"\baddress_width\s*:\s*(\d+)", txt)
        ww = re.search(r"\bword_width\s*:\s*(\d+)", txt)
        rd = txt.find("rd_out")
        tcq = re.search(r"cell_rise\s*\(\s*scalar\s*\)\s*\{\s*values\s*\(\s*\"([0-9.]+)\"", txt[rd:]) if rd >= 0 else None
        cons = [float(v) for v in re.findall(r"rise_constraint\s*\(\s*scalar\s*\)\s*\{\s*values\s*\(\s*\"([0-9.]+)\"", txt)]
        minp = [float(v) for v in re.findall(r"\bmin_period\s*:\s*([0-9.]+)", txt)]
        caps = [float(v) for v in re.findall(r"(?<!_)\bcapacitance\s*:\s*([0-9.]+)", txt)]
        if not (cell and area and aw and ww and tcq):
            continue
        words, width = 1 << int(aw.group(1)), int(ww.group(1))
        found.append(dict(name=cell.group(1), words=words, width=width, bits=words * width, area_um2=float(area.group(1)),
                          tcq=float(tcq.group(1)), cons=max(cons) if cons else None, min_period=max(minp) if minp else None,
                          cap=min(caps) if caps else None))
    if not found:
        return dict(pdk["macro"]), []
    bits = sum(f["bits"] for f in found)
    area = sum(f["area_um2"] for f in found)
    m = dict(pdk["macro"])
    m["area_per_bit_um2"] = area / bits
    m["tcq"] = max(f["tcq"] for f in found)
    if any(f["cons"] is not None for f in found):
        m["tsu"] = m["th"] = max(f["cons"] for f in found if f["cons"] is not None)
    if any(f["min_period"] is not None for f in found):
        m["min_period"] = max(f["min_period"] for f in found if f["min_period"] is not None)
    if any(f["cap"] is not None for f in found):
        m["cap"] = max(f["cap"] for f in found if f["cap"] is not None)
    unit = "ns" if pdk["time_unit_ps"] != 1.0 else "ps"
    each = ", ".join("%s (%.3f um2/bit, %g %s access)" % (f["name"], f["area_um2"] / f["bits"], f["tcq"], unit) for f in found)
    m["note"] = (f"ORFS FakeRAM45 macros {each}: {m['area_per_bit_um2']:.3f} um2/bit (the sum of their areas over the sum of "
                 f"their bits), {m['tcq']:g} {unit} access (the slowest), {m['tsu']:g} {unit} setup/hold")
    return m, found

RTL_FILES = ["delay_line.sv", "mac_pe.sv", "skew_in.sv", "deskew_out.sv", "systolic_array.sv", "abft_checker.sv", "neo_mem.sv",
             "neo_mac_core.sv", "act_feeder.sv", "acc_bank.sv", "neo_mac_core_v02.sv", "core_seq.sv", "ecc39.sv", "wbuf_mem.sv",
             "requant.sv", "neo_core.sv", "host_if.sv", "noc_router.sv", "sram_bank.sv", "mbist.sv", "bank_bist_wrap.sv",
             "crc16_word.sv", "crc16_beat.sv", "tile_nic.sv", "tile_dma.sv", "prog_mem.sv", "esm.sv", "link_pack.sv", "neo_tile.sv"]


def clog2(x):
    return max(1, (x - 1).bit_length()) if x > 1 else 1


# ---------------------------------------------------------------------------------------------------------------------
# the units
# ---------------------------------------------------------------------------------------------------------------------
TILE = dict(XW=3, YW=3, NX=8, NY=8, WPF=32, ROWS=32, COLS=32, ACC_ROWS=512, ABUF_DEPTH=2048, WBUF_DEPTH=1024,
            BANK_DEPTH=524288, FETCH_TIMEOUT=1048575, DV_HOOKS=0)
CORE16 = dict(ROWS=16, COLS=8, ACC_ROWS=64, ABUF_DEPTH=256, WBUF_DEPTH=512)


def U(name, module, params, desc, children=None, macros=(), pe=None, tile=False):
    """children: {child module: [unit names that synthesize its parameterizations]}; macros: leaf modules to black-box."""
    return dict(name=name, module=module, params=dict(params), desc=desc, children=dict(children or {}), macros=list(macros),
                pe_expected=pe, tile=tile)


def core_units(c, p, desc):
    """The units of one core geometry: c = ROWS/COLS/ACC_ROWS/ABUF_DEPTH/WBUF_DEPTH, p = the unit-name prefix."""
    R, C, PW = c["ROWS"], c["COLS"], 32
    WCW = 8 + clog2(R) + 1
    IDXW, AW, WAW = clog2(c["ACC_ROWS"]), clog2(c["ABUF_DEPTH"]), clog2(c["WBUF_DEPTH"])
    lanes = (C * 8 + WCW + 31) // 32
    return [
        U(p + "mac_pe", "mac_pe", dict(XW=8, WW=8, PW=PW), f"processing element, data column ({desc}): 8x8 signed multiply, 32-bit add, two stages"),
        U(p + "mac_pe_chk", "mac_pe", dict(XW=8, WW=WCW, PW=PW), f"processing element, ABFT check column ({desc}): {WCW}-bit weight"),
        U(p + "systolic_array", "systolic_array", dict(ROWS=R, COLS=C, XW=8, WW=8, PW=PW, WCW=WCW),
          f"{R}x{C + 1} PE array ({desc}): the PEs' wiring and the fault-hook select", children={"mac_pe": [p + "mac_pe", p + "mac_pe_chk"]}, pe=R * (C + 1)),
        U(p + "neo_mac_core", "neo_mac_core", dict(ROWS=R, COLS=C, XW=8, WW=8, PW=PW, WCW=WCW),
          f"streaming core ({desc}): the array with its skew / deskew lines and the ABFT checker", children={"systolic_array": [p + "systolic_array"]}),
        U(p + "act_feeder", "act_feeder", dict(ROWS=R, XW=8, ABUF_DEPTH=c["ABUF_DEPTH"], AW=AW, IDXW=IDXW),
          f"activation feeder ({desc}): duplicated address generators around the {c['ABUF_DEPTH'] // 2} x {2 * R * 8} pair leaf (macro)", macros=["neo_mem_rf"]),
        U(p + "acc_bank", "acc_bank", dict(COLS=C, PW=PW, ACC_ROWS=c["ACC_ROWS"], IDXW=IDXW),
          f"accumulator ({desc}): {C + 1} adders and the ABFT check around the {c['ACC_ROWS']} x {(C + 1) * PW} leaf (macro)", macros=["neo_mem_rf"]),
        U(p + "neo_mac_core_v02", "neo_mac_core_v02", dict(ROWS=R, COLS=C, XW=8, WW=8, PW=PW, WCW=WCW, ACC_ROWS=c["ACC_ROWS"], ABUF_DEPTH=c["ABUF_DEPTH"], IDXW=IDXW, AW=AW),
          f"core datapath ({desc}): feeder, streaming core, accumulator",
          children={"neo_mac_core": [p + "neo_mac_core"], "act_feeder": [p + "act_feeder"], "acc_bank": [p + "acc_bank"]}),
        U(p + "wbuf_mem", "wbuf_mem", dict(COLS=C, WW=8, WCW=WCW, DEPTH=c["WBUF_DEPTH"], WAW=WAW, DV_HOOKS=0),
          f"weight buffer ({desc}): {lanes} SECDED lanes around the {c['WBUF_DEPTH'] // 2} x {2 * lanes * 39} pair leaf (macro)", macros=["neo_mem_rf"]),
        U(p + "requant", "requant", dict(COLS=C, PW=PW, MW=16), f"requantizer ({desc}): {C} columns of 32x17 multiply, shift, round, saturate; table parity"),
        U(p + "core_seq", "core_seq", dict(ROWS=R, COLS=C, IDXW=IDXW, WBUF_DEPTH=c["WBUF_DEPTH"], WAW=WAW, PE_LAT=2), f"sequencer ({desc}; a lockstep pair in the core)"),
        U(p + "neo_core", "neo_core", dict(ROWS=R, COLS=C, PW=PW, ACC_ROWS=c["ACC_ROWS"], ABUF_DEPTH=c["ABUF_DEPTH"], WBUF_DEPTH=c["WBUF_DEPTH"], DV_HOOKS=0),
          f"the {R}x{C} core: datapath, weight buffer, two sequencers, two requantizers, the comparators and flags",
          children={"neo_mac_core_v02": [p + "neo_mac_core_v02"], "wbuf_mem": [p + "wbuf_mem"], "requant": [p + "requant"], "core_seq": [p + "core_seq"]},
          pe=R * (C + 1)),
    ]


def tile_units(T):
    R, C, PW = T["ROWS"], T["COLS"], 32
    WCW = 8 + clog2(R) + 1
    IDXW, AW, WAW, BAW = clog2(T["ACC_ROWS"]), clog2(T["ABUF_DEPTH"]), clog2(T["WBUF_DEPTH"]), clog2(T["BANK_DEPTH"])
    XW, YW, NX, NY, WPF = T["XW"], T["YW"], T["NX"], T["NY"], T["WPF"]
    DWL = 32 + 32 * WPF
    BW, LANES, PDEPTH = 16, 16, 32
    units = core_units(T, "", "32x32")
    units += [
        U("sram_bank", "sram_bank", dict(DEPTH=T["BANK_DEPTH"], LANES=LANES, DV_HOOKS=0),
          f"bank: {LANES} SECDED lanes around the {T['BANK_DEPTH'] // LANES} x {39 * LANES} leaf (macro)", macros=["neo_mem_sp"]),
        U("mbist", "mbist", dict(AW=BAW, WORDS=T["BANK_DEPTH"], LANES=LANES), "March C- MBIST over the bank's rows"),
        U("bank_bist_wrap", "bank_bist_wrap", dict(DEPTH=T["BANK_DEPTH"], AW=BAW, BIST_WORDS=T["BANK_DEPTH"], LANES=LANES, DV_HOOKS=0),
          "the bank with its MBIST and port mux", children={"sram_bank": ["sram_bank"], "mbist": ["mbist"]}),
        U("crc16_beat", "crc16_beat", dict(BW=BW), "CRC-16 over a 16-word beat in matrix form: an XOR tree per state bit, then the inverse for the word count (combinational; twice inside the NIC)"),
        U("ecc39_enc", "ecc39_enc", {}, "(39,32) SECDED encoder (combinational; inside the bank, the weight buffer, the program memory)"),
        U("ecc39_dec", "ecc39_dec", {}, "(39,32) SECDED decoder: syndrome, correction, ce / ue (combinational; the same)"),
        U("link_packer", "link_packer", dict(XW=XW, YW=YW, BW=BW, WPF=WPF), "beats (16 words) to 32-word link flits"),
        U("link_unpacker", "link_unpacker", dict(XW=XW, YW=YW, BW=BW, WPF=WPF), "32-word link flits to beats"),
        U("tile_nic", "tile_nic", dict(XW=XW, YW=YW, NX=NX, NSRC=NX * NY, BW=BW, ROWS=R, COLS=C, WCW=WCW, IDXW=IDXW, AW=AW, WAW=WAW, BAW=BAW, PW=PW,
                                       DFD=T["ACC_ROWS"], FETCH_TIMEOUT=T["FETCH_TIMEOUT"], DV_HOOKS=0),
          "tile interface: fetch (two entries per cycle), serve, writeback, partial sums, CRC per beat, per-source state for 64 sources; the drain FIFO 512 x 1,068 and the partial-sum rows 64 x 1,024 are leaves (macros)",
          macros=["neo_mem_rf"]),
        U("noc_router", "noc_router", dict(XW=XW, YW=YW, DW=DWL, DV_HOOKS=0), "5-port router: 1077-bit flits, 2-entry input FIFOs, parity at the FIFO head, round-robin outputs"),
        U("tile_dma", "tile_dma", dict(XW=XW, YW=YW, PDEPTH=PDEPTH, DV_HOOKS=0), "DMA program engine (a lockstep pair in the tile)"),
        U("prog_mem", "prog_mem", dict(DEPTH=PDEPTH, DV_HOOKS=0), "32 x 64-bit program memory as two SECDED lanes (flops)"),
        U("host_if", "host_if", dict(IDXW=IDXW, PAW=clog2(PDEPTH), DV_HOOKS=0), "host register interface"),
        U("esm", "esm", dict(NFLAGS=18), "error-signaling module: 18 causes, the mask, the windowed watchdog"),
        U("neo_tile", "neo_tile", dict(T), "the silicon tile: 8x8 mesh position, 32x32 core, 2 MB bank, 1024-bit links, DV_HOOKS = 0 (own logic: flags, the hooks' tie-offs, the register decode)",
          children={"neo_core": ["neo_core"], "tile_nic": ["tile_nic"], "noc_router": ["noc_router"], "bank_bist_wrap": ["bank_bist_wrap"],
                    "tile_dma": ["tile_dma"], "prog_mem": ["prog_mem"], "host_if": ["host_if"], "esm": ["esm"],
                    "link_packer": ["link_packer"], "link_unpacker": ["link_unpacker"]},
          pe=R * (C + 1), tile=True),
    ]
    return units


UNITS = tile_units(TILE) + core_units(CORE16, "c16_", "16x8")

# ---------------------------------------------------------------------------------------------------------------------
# the chip top (drop 0.44, stream B1; this block, TOP and RTL_FILES' extension below are B1's whole change to this file):
# neo_top at the silicon parameters -- 8 x 8 tiles and the bridge column at x = 8, a 512-bit memory bridge in every row and
# the host bridge in row 0. The bridge column makes the coordinates 4 bits and the tile's source index 9 columns wide, so the
# chip top's tile is a parameterization of neo_tile of its own (XW 4, NX 9) with units of its own for the children whose
# parameters follow XW / NX (the NIC with 72 sources, the router, the packer / unpacker, the DMA engine); the core, the bank,
# the program memory, host_if and the ESM are the tile's units unchanged. The top's own logic: the bridges (two
# parameterizations: memory and host roles in row 0, the memory role alone in rows 1-7), the control plane's two halves, the
# bridge column's routers (= the top tile's router unit) and packer / unpacker pairs; the crossings (cdc_bits, cdc_fifo,
# cdc_sync) and the tiles' fences flatten into neo_top's own logic. The bridge and the control plane run in the AXI clock: their
# port named clk is neo_top's aclk, so each is timed against the period like any unit; in the neo_top unit only clk is
# constrained (the aclk-side flops of the flattened FIFOs and synchronizers are not timed there).
# ---------------------------------------------------------------------------------------------------------------------
TOP = dict(NX=8, NY=8, ROWS=32, COLS=32, ACC_ROWS=512, ABUF_DEPTH=2048, WBUF_DEPTH=1024, BANK_DEPTH=524288, WPF=32,
           FETCH_TIMEOUT=1048575, AXI_DW=512, AXI_IDW=4, N_MEM_BRIDGES=8, N_HOST_BRIDGES=1, CTRL_AW=18, CDC_DEPTH=8)
RTL_FILES += ["cdc_bits.sv", "cdc_fifo.sv", "cdc_sync.sv", "noc_axi_bridge.sv", "top_ctrl.sv", "mesh_ctrl.sv", "neo_top.sv"]


def top_units(P):
    NX, NY = P["NX"], P["NY"]
    XW, YW = clog2(NX + 1), clog2(NY)
    TT = dict(TILE, XW=XW, YW=YW, NX=NX + 1, NY=NY)          # the chip top's tile: the source index over NX + 1 columns
    R, C, PW = TT["ROWS"], TT["COLS"], 32
    WCW = 8 + clog2(R) + 1
    IDXW, AW, WAW, BAW = clog2(TT["ACC_ROWS"]), clog2(TT["ABUF_DEPTH"]), clog2(TT["WBUF_DEPTH"]), clog2(TT["BANK_DEPTH"])
    DWL, BW, NSRC = 32 + 32 * TT["WPF"], 16, (NX + 1) * NY
    br = dict(XW=XW, YW=YW, NX=NX, NY=NY, BAW=BAW, AXI_DW=P["AXI_DW"], AXI_IDW=P["AXI_IDW"], HOST_TIMEOUT=P["FETCH_TIMEOUT"])
    return [
        U("top_link_packer", "link_packer", dict(XW=XW, YW=YW, BW=BW, WPF=TT["WPF"]), "beats to 32-word link flits at the chip top's 4-bit x"),
        U("top_link_unpacker", "link_unpacker", dict(XW=XW, YW=YW, BW=BW, WPF=TT["WPF"]), "32-word link flits to beats at the chip top's 4-bit x"),
        U("top_tile_nic", "tile_nic", dict(XW=XW, YW=YW, NX=NX + 1, NSRC=NSRC, BW=BW, ROWS=R, COLS=C, WCW=WCW, IDXW=IDXW, AW=AW, WAW=WAW,
                                           BAW=BAW, PW=PW, DFD=TT["ACC_ROWS"], FETCH_TIMEOUT=TT["FETCH_TIMEOUT"], DV_HOOKS=0),
          f"the chip top's tile interface: per-source state for {NSRC} sources (the bridge column has its own); the drain FIFO and the "
          f"partial-sum rows {NSRC} x 1,024 are leaves (macros)", macros=["neo_mem_rf"]),
        U("top_noc_router", "noc_router", dict(XW=XW, YW=YW, DW=DWL, DV_HOOKS=0), "5-port router at the chip top's widths (the tiles' and the bridge column's)"),
        U("top_tile_dma", "tile_dma", dict(XW=XW, YW=YW, PDEPTH=32, DV_HOOKS=0), "DMA program engine at the chip top's coordinate width"),
        U("top_neo_tile", "neo_tile", dict(TT), "the chip top's tile: XW 4, the source index over 9 columns; 32x32 core, 2 MB bank, 1024-bit links",
          children={"neo_core": ["neo_core"], "tile_nic": ["top_tile_nic"], "noc_router": ["top_noc_router"], "bank_bist_wrap": ["bank_bist_wrap"],
                    "tile_dma": ["top_tile_dma"], "prog_mem": ["prog_mem"], "host_if": ["host_if"], "esm": ["esm"],
                    "link_packer": ["top_link_packer"], "link_unpacker": ["top_link_unpacker"]},
          pe=R * (C + 1)),
        U("noc_axi_bridge", "noc_axi_bridge", dict(br, MEM=1, HOST=1),
          f"NoC-to-AXI4 bridge engine, memory and host roles ({P['AXI_DW']}-bit AXI4; per-source receive state for {NSRC} sources; two CRC beats)"),
        U("noc_axi_bridge_mem", "noc_axi_bridge", dict(br, MEM=1, HOST=0), "NoC-to-AXI4 bridge engine, the memory role alone"),
        U("top_ctrl", "top_ctrl", dict(NX=NX, NY=NY, ROWS=R, COLS=C, BAW=BAW, WPF=TT["WPF"], AXI_DW=P["AXI_DW"], N_MEM=P["N_MEM_BRIDGES"],
                                       N_HOST=P["N_HOST_BRIDGES"], CTRL_AW=P["CTRL_AW"]),
          "the control plane, AXI side: the AXI4-Lite slave, the global registers, the interrupt"),
        U("mesh_ctrl", "mesh_ctrl", dict(NX=NX, NY=NY), "the control plane, mesh side: 64 register buses, the tile-enable map, the status levels"),
        U("neo_top", "neo_top", dict(P),
          f"the chip top: {NX}x{NY} tiles and {NY} bridge nodes (own logic: the fences, the bridge FIFOs and the synchronizers)",
          children={"neo_tile": ["top_neo_tile"], "noc_router": ["top_noc_router"], "link_packer": ["top_link_packer"],
                    "link_unpacker": ["top_link_unpacker"], "noc_axi_bridge": ["noc_axi_bridge", "noc_axi_bridge_mem"],
                    "top_ctrl": ["top_ctrl"], "mesh_ctrl": ["mesh_ctrl"]},
          pe=NX * NY * R * (C + 1)),
    ]


UNITS += top_units(TOP)
UNIT = {u["name"]: u for u in UNITS}


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def run(cmd, cwd, logfile, timeout=None):
    with open(logfile, "w") as f:
        f.write("$ " + " ".join(cmd) + "\n")
        f.flush()
        p = subprocess.run(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT, timeout=timeout)
    return p.returncode


# ---------------------------------------------------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------------------------------------------------
def find_tools(args):
    root = os.environ.get("NEO_SYNTH_TOOLS", "/opt/neo-synth")
    t = {}
    t["yosys"] = os.environ.get("NEO_YOSYS") or os.path.join(root, "oss-cad-suite", "bin", "yosys")
    t["sta"] = os.environ.get("NEO_STA") or os.path.join(root, "sta")
    t["pdk_lib"] = os.environ.get("NEO_PDK_LIB") or os.path.join(root, "pdk", "lib")
    if not (os.path.isfile(t["yosys"]) and os.access(t["yosys"], os.X_OK)):
        sys.exit(f"synth.py: yosys not found ({t['yosys']}); run synth/tools.sh or set NEO_YOSYS")
    if not (os.path.isfile(t["sta"]) and os.access(t["sta"], os.X_OK)):
        # no OpenSTA (tools.sh could not build it): the synthesis and its gates still run, the timing phase is skipped
        # and reported as a failed gate -- the job is red for the right reason, with the synthesis report published
        log(f"OpenSTA not found ({t['sta']}): synthesis only, the timing gate will fail")
        t["sta"] = None
    pdk = PDKS[args.pdk]
    libdir = os.path.join(t["pdk_lib"], pdk["dir"])
    t["libs"] = [os.path.join(libdir, f) for f in pdk["libs"][args.corner]]
    t["seq_lib"] = os.path.join(libdir, pdk["seq_lib"].format(corner=args.corner))
    t["lcu"] = os.path.join(t["pdk_lib"], "common", "lcu_kogge_stone.v")
    t["fa_map"] = os.path.join(libdir, "cells_adders.v")
    for f in t["libs"] + [t["lcu"], t["fa_map"]]:
        if not os.path.isfile(f):
            sys.exit(f"synth.py: library file missing: {f}")
    return t


def tool_versions(t):
    v = {}
    for k, cmd in (("yosys", [t["yosys"], "-V"]), ("sta", [t["sta"], "-version"])):
        if cmd[0] is None:
            v[k] = "missing"
            continue
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout.strip().splitlines()
            v[k] = out[0] if out else "?"
        except Exception as e:   # noqa: BLE001
            v[k] = f"? ({e})"
    return v


# ---------------------------------------------------------------------------------------------------------------------
# the netlist JSON
# ---------------------------------------------------------------------------------------------------------------------
def const_int(v):
    if isinstance(v, str) and v and set(v) <= {"0", "1"}:
        return int(v, 2)
    try:
        return int(v)
    except (TypeError, ValueError):
        return v


class Netlist:
    def __init__(self, path):
        with open(path) as f:
            self.j = json.load(f)
        self.modules = self.j["modules"]
        tops = [n for n, m in self.modules.items() if str(m.get("attributes", {}).get("top", "")).strip("0") == "1"]
        if tops:
            self.top = tops[0]
        else:
            inst = {c["type"] for m in self.modules.values() for c in m.get("cells", {}).values()}
            cands = [n for n in self.modules if n not in inst and not self.is_blackbox(n)]
            self.top = cands[0] if cands else next(iter(self.modules))

    def is_blackbox(self, mod):
        a = self.modules[mod].get("attributes", {})
        return "blackbox" in a or "whitebox" in a

    def ports(self, mod):
        return self.modules[mod].get("ports", {})

    def cells(self, mod=None):
        return self.modules[mod or self.top].get("cells", {})

    def groups(self, mod=None):
        """{black-box type: {parameter tuple: [cell names]}} of the top's cells whose type is a black box."""
        g = {}
        for cn, c in self.cells(mod).items():
            ty = c["type"]
            if ty in self.modules and self.is_blackbox(ty):
                key = tuple(sorted((k, const_int(v)) for k, v in c.get("parameters", {}).items()))
                g.setdefault(ty, {}).setdefault(key, []).append(cn)
        return g


# ---------------------------------------------------------------------------------------------------------------------
# the macro liberty
# ---------------------------------------------------------------------------------------------------------------------
def macro_name(base, p):
    n = f"{base}_w{p.get('W')}_d{p.get('D')}_g{p.get('G', 1)}"
    if base == "neo_mem_rf":
        n += f"_rb{p.get('RB', 0)}"
    return n


def macro_kind(base, p, port_a):
    """The area class of a leaf instance (MACRO_AREA_FACTOR) by the read ports it uses: neo_mem_rf reads on its
    asynchronous port A when ra_addr is connected (port_a), on its registered port B when RB = 1 (drop 0.42; before, RB
    alone decided, so a port-B-only leaf was priced as one with two read ports)."""
    if base != "neo_mem_rf":
        return base
    port_b = bool(p.get("RB", 0))
    if port_a and port_b:
        return "neo_mem_rf_rb"
    return "neo_mem_rf_b" if port_b else "neo_mem_rf"


def macro_record(base, p, pdk, port_a=True):
    bits = p["W"] * p["D"]
    kind = macro_kind(base, p, port_a)
    return dict(name=macro_name(base, p), base=base, W=p["W"], D=p["D"], G=p.get("G", 1), RB=p.get("RB", 0), AW=p.get("AW", clog2(p["D"])),
                bits=bits, kind=kind, area_um2=bits * pdk["macro"]["area_per_bit_um2"] * MACRO_AREA_FACTOR[kind],
                factor=MACRO_AREA_FACTOR[kind])


def write_macro_lib(recs, pdk, path, libname="neo_macros"):
    """One liberty cell per memory-leaf parameterization: bus pins, setup / hold on the inputs, the access time on the
    read data (clock-to-Q on the registered ports, address-to-data on the asynchronous port A of neo_mem_rf)."""
    m = pdk["macro"]
    widths = set()
    for r in recs:
        for pn, w, d in r["pins"]:
            if w > 1:
                widths.add(w)
    time_unit = "1ps" if pdk["time_unit_ps"] == 1.0 else "1ns"
    L = [f"library ({libname}) {{",
         "  delay_model : table_lookup;",
         f"  time_unit : \"{time_unit}\";",
         "  capacitive_load_unit (1,ff);",
         "  voltage_unit : \"1V\"; current_unit : \"1mA\"; leakage_power_unit : \"1pW\"; pulling_resistance_unit : \"1kohm\";",
         "  nom_process : 1; nom_temperature : 25; nom_voltage : 0.7;",
         "  slew_lower_threshold_pct_rise : 20; slew_upper_threshold_pct_rise : 80; slew_lower_threshold_pct_fall : 20; slew_upper_threshold_pct_fall : 80;",
         "  input_threshold_pct_rise : 50; input_threshold_pct_fall : 50; output_threshold_pct_rise : 50; output_threshold_pct_fall : 50;",
         "  default_max_transition : 1000;"]
    for w in sorted(widths):
        L.append(f"  type (bus_{w}) {{ base_type : array; data_type : bit; bit_width : {w}; bit_from : {w - 1}; bit_to : 0; downto : true; }}")
    tr = f"{m['tcq'] * 0.1:.4g}"

    def arc_cq(related, ttype):
        return (f"      timing() {{ related_pin : \"{related}\"; {ttype} timing_sense : non_unate;"
                f" cell_rise(scalar) {{ values(\"{m['tcq']}\"); }} cell_fall(scalar) {{ values(\"{m['tcq']}\"); }}"
                f" rise_transition(scalar) {{ values(\"{tr}\"); }} fall_transition(scalar) {{ values(\"{tr}\"); }} }}")

    def arc_su():
        return (f"      timing() {{ related_pin : \"clk\"; timing_type : setup_rising; rise_constraint(scalar) {{ values(\"{m['tsu']}\"); }} fall_constraint(scalar) {{ values(\"{m['tsu']}\"); }} }}\n"
                f"      timing() {{ related_pin : \"clk\"; timing_type : hold_rising; rise_constraint(scalar) {{ values(\"{m['th']}\"); }} fall_constraint(scalar) {{ values(\"{m['th']}\"); }} }}")

    for r in recs:
        L.append(f"  cell ({r['name']}) {{")
        L.append(f"    area : {r['area_um2']:.3f};")
        L.append("    dont_use : true;")
        L.append(f"    memory() {{ type : ram; address_width : {r['AW']}; word_width : {r['W']}; }}")
        for pn, w, d in r["pins"]:
            head = f"    pin ({pn}) {{" if w == 1 else f"    bus ({pn}) {{ bus_type : bus_{w};"
            body = [f"      direction : {d};"]
            if pn == "clk":
                body.append(f"      clock : true; capacitance : {m['cap'] * 4:.3f}; min_period : {m['min_period']};")
            elif d == "input":
                body.append(f"      capacitance : {m['cap']:.3f};")
                if not pn.startswith(("bd_", "fi_")):     # the hook pins are tied off in silicon: no constraint
                    body.append(arc_su())
            else:
                body.append("      max_capacitance : 500.0;")
                if pn in ("rdata", "rb_data"):
                    body.append(arc_cq("clk", "timing_type : rising_edge;"))
                elif pn == "ra_data":           # the asynchronous read: an arc from every address bit (a bus-to-bus arc of
                    aw = r["AW"]                  # different widths is not taken by OpenSTA)
                    body.append(arc_cq(" ".join(f"ra_addr[{i}]" for i in range(aw)), ""))
            L.append(head)
            L.extend(body)
            L.append("    }")
        L.append("  }")
    L.append("}")
    with open(path, "w") as f:
        f.write("\n".join(L) + "\n")


# ---------------------------------------------------------------------------------------------------------------------
# step 1: the unit's first read (slang) and its analysis
# ---------------------------------------------------------------------------------------------------------------------
def slang_read_cmd(u, rtl):
    bb = " ".join(f"--blackboxed-module {m}" for m in list(u["children"]) + u["macros"])
    g = " ".join(f"-G {k}={v}" for k, v in u["params"].items())
    files = " ".join(os.path.join(rtl, f) for f in RTL_FILES)
    return f"read_slang --ignore-timing {bb} --top {u['module']} {g} {files}"


def pins_of(nl, ty, cell_name):
    """The pins of a black-box type as one instance connects them: (name, width, direction)."""
    ports = nl.ports(ty)
    c = nl.cells()[cell_name]
    return [(pn, len(bits), ports.get(pn, {}).get("direction", "input")) for pn, bits in c["connections"].items()]


def analyse(u, nl, pdk):
    """The black-box groups of the unit: the retype plan (cell type -> unit / macro name per parameterization), the stubs,
    the macro records, the child instance counts and the hierarchy-check findings."""
    plan, stubs, macros, kids, problems = [], {}, {}, {}, []
    seen_children = set()
    for ty, groups in nl.groups().items():
        for key, cells in groups.items():
            p = dict(key)
            if ty in u["macros"]:
                if "W" not in p or "D" not in p:
                    problems.append(f"{ty}: leaf instance without W / D parameters ({p})")
                    continue
                # port A read: ra_addr driven by a net in some instance, not tied to constants (drop 0.42)
                port_a = any(isinstance(b, int) for c in cells for b in nl.cells()[c]["connections"].get("ra_addr", []))
                r = macro_record(ty, p, pdk, port_a)
                target = r["name"]
                if target not in macros:
                    r["pins"] = pins_of(nl, ty, cells[0])
                    r["instances"] = 0
                    macros[target] = r
                elif r["factor"] > macros[target]["factor"]:     # one parameterization used two ways: the larger class
                    macros[target].update({k: r[k] for k in ("kind", "factor", "area_um2")})
                macros[target]["instances"] += len(cells)
            elif ty in u["children"]:
                cand = [UNIT[n] for n in u["children"][ty]]
                match = [c for c in cand if all(p.get(k) == v for k, v in c["params"].items())]
                if not match:
                    problems.append(f"{ty} instantiated with {p}: no unit synthesizes it (candidates {[c['name'] for c in cand]})")
                    continue
                target = match[0]["name"]
                kids[target] = kids.get(target, 0) + len(cells)
                seen_children.add(ty)
            else:
                problems.append(f"unexpected black box {ty}")
                continue
            plan.append((ty, p, target, cells))
            if target != ty and target not in stubs:
                stubs[target] = pins_of(nl, ty, cells[0])
    for ty in u["children"]:
        if ty not in seen_children:
            problems.append(f"{ty} is declared a child but the module instantiates none")
    return dict(plan=plan, stubs=stubs, macros=macros, children=kids, problems=problems)


def stub_verilog(name, pins):
    ports = ", ".join(pn for pn, _, _ in pins)
    decl = [f"  {d}{(' [%d:0]' % (w - 1)) if w > 1 else ''} {pn};" for pn, w, d in pins]
    return f"module {name}({ports});\n" + "\n".join(decl) + "\nendmodule\n"


# ---------------------------------------------------------------------------------------------------------------------
# step 2: the unit's synthesis script
# ---------------------------------------------------------------------------------------------------------------------
def synth_script(u, an, t, pdk, period_ps, rtl, udir):
    libs = " ".join(f"-liberty {l}" for l in t["libs"])
    dont = " ".join(f"-dont_use {c}" for c in pdk["dont_use"])
    with open(os.path.join(udir, "abc.constr"), "w") as f:
        f.write(f"set_driving_cell {pdk['abc_driver']}\nset_load {pdk['abc_load_ff']}\n")
    L = [f"# unit {u['name']}: {u['module']} {u['params']}", "plugin -i slang", slang_read_cmd(u, rtl)]
    if an["stubs"]:
        with open(os.path.join(udir, "stubs.v"), "w") as f:
            for name, pins in an["stubs"].items():
                f.write(stub_verilog(name, pins))
        L.append("read_verilog -lib stubs.v")
    # the black-box instances under their unit / macro names, without parameters (the stubs and the liberty carry none):
    # the parameterizations of one type are told apart by the integer parameters whose values differ between them
    by_type = {}
    for ty, p, target, cells in an["plan"]:
        by_type.setdefault(ty, []).append((p, target))
    for ty, groups in by_type.items():
        keys = sorted({k for p, _ in groups for k in p})
        distinct = [k for k in keys if len({str(p.get(k)) for p, _ in groups}) > 1 and all(isinstance(p.get(k), int) for p, _ in groups)]
        for p, target in groups:
            if target != ty:
                sel = f"t:{ty} " + " ".join(f"r:{k}={p[k]} %i" for k in distinct)
                L.append(f"chtype -set {target} {sel}")
        for target in sorted({target for _, target in groups}):
            if keys:
                L.append("setparam " + " ".join(f"-unset {k}" for k in keys) + f" t:{target}")
    if u["name"] != u["module"]:
        L.append(f"rename -top {u['name']}")
    L += [
        "# generic synthesis (the children and the leaves stay black boxes); the arithmetic as the ORFS flow maps it: Booth",
        "# multipliers, Kogge-Stone carry lookahead, the full adders on the library's FA / HA cells",
        f"synth -top {u['name']} -booth -noabc -extra-map {t['lcu']}",
        "tee -q -o generic_stat.json stat -json",
        "tee -q -o check.txt check",
        "extract_fa",
        f"techmap -map {t['fa_map']}",
        "techmap",
        "opt -fast -purge",
        "# the flops named after the registers they hold (the paths read in RTL terms), then the library: flops, then ABC's",
        "# speed script at the clock period with the driver / load of the platform",
        "rename -wire -suffix _reg t:$_DFF* t:$_DFFE* t:$_SDFF* t:$_SDFFE* t:$_DFFSR* t:$_DFFSRE* t:$_ALDFF* t:$_DLATCH*",
        f"dfflibmap -liberty {t['seq_lib']} {dont}",
        "opt",
        "setundef -zero",
        f"abc -script {os.path.join(HERE, 'abc_speed.script')} {libs} {dont} -D {int(round(period_ps))} -constr abc.constr",
        "opt_clean -purge",
        "# the library cells as black boxes, so the JSON carries every pin's direction (the repeater trees need it)",
    ] + [f"read_liberty -lib {l}" for l in t["libs"]] + [
        f"tee -q -o stat.json stat {libs} -json",
        f"tee -q -o stat.txt stat {libs}",
        "write_verilog -noattr -noexpr -nohex -nodec netlist.v",
        "write_json netlist.json",
    ]
    return "\n".join(L) + "\n"


MAX_FANOUT = 32


def buffer_fanout(json_path, pdk, skip_ports=("clk", "rst_n")):
    """Repeater trees on every net of the unit's top module whose fanout exceeds MAX_FANOUT (what the physical flow
    inserts; the per-unit synthesis cannot see the loads behind its black-box children, so a strobe into 1,056 PEs would
    otherwise be timed as one driver): 4-ary trees of the library's buffer, the clock and the reset nets excepted. The
    JSON is rewritten in place (Yosys re-reads it for the Verilog and the stat); returns the number of buffers added."""
    with open(json_path) as f:
        j = json.load(f)
    top = next((n for n, m in j["modules"].items() if str(m.get("attributes", {}).get("top", "")).strip("0") == "1"), None)
    if top is None:
        return 0
    m = j["modules"][top]
    for cn, c in m["cells"].items():
        if not c.get("port_directions"):
            raise RuntimeError(f"buffer_fanout: no pin directions for {cn} ({c['type']}) -- the liberty cells must be in the design")
    cell_name, pin_in, pin_out = pdk["repeater"]
    skip_bits = set()
    for pn in skip_ports:
        if pn in m["ports"]:
            skip_bits.update(b for b in m["ports"][pn]["bits"] if isinstance(b, int))
    # the sinks of every bit: (cell, port, index) of input pins, and ("", port, index) of the module's output ports
    sinks = {}
    for cn, c in m["cells"].items():
        dirs = c["port_directions"]
        for port, bits in c["connections"].items():
            if dirs.get(port, "input") != "input":
                continue
            for i, b in enumerate(bits):
                if isinstance(b, int) and b not in skip_bits:
                    sinks.setdefault(b, []).append((cn, port, i))
    for pn, p in m["ports"].items():
        if p["direction"] == "output":
            for i, b in enumerate(p["bits"]):
                if isinstance(b, int) and b not in skip_bits:
                    sinks.setdefault(b, []).append(("", pn, i))
    heavy = {b: sk for b, sk in sinks.items() if len(sk) > MAX_FANOUT}
    if not heavy:
        return 0
    added = 0
    next_bit = max([b for mod in j["modules"].values() for p in mod["ports"].values() for b in p["bits"] if isinstance(b, int)] +
                   [b for mod in j["modules"].values() for c in mod["cells"].values() for bits in c["connections"].values() for b in bits if isinstance(b, int)] +
                   [b for mod in j["modules"].values() for n in mod.get("netnames", {}).values() for b in n["bits"] if isinstance(b, int)] + [0]) + 1

    def reconnect(sk, bit):
        cn, port, i = sk
        if cn == "":
            m["ports"][port]["bits"][i] = bit
            nn = m.get("netnames", {}).get(port)         # the port's own net name must move with it, or read_json shorts the two
            if nn and i < len(nn["bits"]):
                nn["bits"][i] = bit
        else:
            m["cells"][cn]["connections"][port][i] = bit

    def tree(src, sk_list):
        """src drives sk_list through buffers: at most MAX_FANOUT loads per driver at every level."""
        nonlocal next_bit, added
        if len(sk_list) <= MAX_FANOUT:
            for sk in sk_list:
                reconnect(sk, src)
            return
        groups = [sk_list[i:i + MAX_FANOUT] for i in range(0, len(sk_list), MAX_FANOUT)]
        new_srcs = []
        for g in groups:
            b = next_bit
            next_bit += 1
            added += 1
            m["cells"][f"fobuf_{b}"] = {"hide_name": 1, "type": cell_name, "parameters": {}, "attributes": {},
                                         "port_directions": {pin_in: "input", pin_out: "output"},
                                         "connections": {pin_in: [None], pin_out: [b]}}
            for sk in g:
                reconnect(sk, b)
            new_srcs.append((f"fobuf_{b}", pin_in, 0))
        tree(src, new_srcs)

    for b, sk_list in heavy.items():
        tree(b, sk_list)
    with open(json_path, "w") as f:
        json.dump(j, f)
    return added


def fix_netlist(path):
    """No `signed` on the declarations (OpenSTA's reader does not take it)."""
    with open(path) as f:
        s = f.read()
    s = re.sub(r"^(\s*(?:input|output|inout|wire|reg)) signed ", r"\1 ", s, flags=re.M)
    with open(path, "w") as f:
        f.write(s)


def parse_stat(udir):
    try:
        with open(os.path.join(udir, "stat.json")) as f:
            s = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return {k.lstrip("\\"): v for k, v in s.get("modules", {}).items()}


def latches(udir):
    try:
        with open(os.path.join(udir, "generic_stat.json")) as f:
            g = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    n = 0
    for mname, s in g.get("modules", {}).items():
        for ty, k in s.get("num_cells_by_type", {}).items():
            if "latch" in ty.lower() or ty.startswith("$_DLATCH") or ty.startswith("$dlatch"):
                n += k
    return n


def check_problems(udir):
    try:
        txt = open(os.path.join(udir, "check.txt")).read()
    except FileNotFoundError:
        return None
    m = re.search(r"Found and reported (\d+) problems", txt)
    return dict(problems=int(m.group(1)) if m else 0, multiple_drivers=len(re.findall(r"multiple conflicting drivers", txt)))


def dv_hooks_check(nl, an):
    """DV_HOOKS = 0 in this unit: every dv_* input bit drives only black-box children (or nothing); every dv_* output bit is
    a constant or a black-box child's output."""
    top = nl.modules[nl.top]
    ports = top.get("ports", {})
    bb_types = {ty for ty, _, _, _ in an["plan"]} | {target for _, _, target, _ in an["plan"]}
    in_bits = set()
    for pn, p in ports.items():
        if pn.startswith("dv_") and p["direction"] == "input":
            in_bits.update(b for b in p["bits"] if isinstance(b, int))
    bad_loads, bb_out_bits = [], set()
    for cn, c in top.get("cells", {}).items():
        is_bb = c["type"] in bb_types
        cports = nl.ports(c["type"]) if c["type"] in nl.modules else {}
        for port, bits in c.get("connections", {}).items():
            if is_bb and cports.get(port, {}).get("direction") == "output":
                bb_out_bits.update(b for b in bits if isinstance(b, int))
            if not is_bb and any(isinstance(b, int) and b in in_bits for b in bits):
                bad_loads.append(f"{cn} ({c['type']}).{port}")
    bad_outs = []
    for pn, p in ports.items():
        if p["direction"] == "output":
            if any(isinstance(b, int) and b in in_bits for b in p["bits"]):
                bad_outs.append(f"{pn} passes a dv_* input through")
            if pn.startswith("dv_") and any(isinstance(b, int) and b not in bb_out_bits for b in p["bits"]):
                bad_outs.append(f"{pn} is driven by logic")
    return dict(dv_inputs=sorted(pn for pn, p in ports.items() if pn.startswith("dv_") and p["direction"] == "input"),
                dv_outputs=sorted(pn for pn, p in ports.items() if pn.startswith("dv_") and p["direction"] == "output"),
                bad_loads=bad_loads[:20], bad_outputs=bad_outs, passed=(not bad_loads and not bad_outs))


def run_unit(u, t, pdk, args, out, rtl):
    udir = os.path.join(out, u["name"])
    os.makedirs(udir, exist_ok=True)
    res = dict(name=u["name"], module=u["module"], params=u["params"], description=u["desc"], pe_expected=u["pe_expected"], tile=u["tile"])
    t0 = time.time()
    with open(os.path.join(udir, "read.ys"), "w") as f:
        f.write("plugin -i slang\n" + slang_read_cmd(u, rtl) + "\nwrite_json pre.json\n")
    rc = run([t["yosys"], "-q", "-l", "read.log", "-s", "read.ys"], udir, os.path.join(udir, "read.out"), timeout=args.timeout)
    if rc != 0:
        res["failed"] = f"slang read exit {rc}"
        log(f"{u['name']}: read FAILED (exit {rc})")
        return res
    nl = Netlist(os.path.join(udir, "pre.json"))
    an = analyse(u, nl, pdk)
    del nl                       # the parsed JSON (hundreds of MB for the NIC) is not held through the Yosys run
    gc.collect()
    res["hierarchy_problems"] = an["problems"]
    res["children"] = an["children"]
    res["macros"] = {k: {kk: vv for kk, vv in v.items() if kk != "pins"} for k, v in an["macros"].items()}
    res["macro_pins"] = {k: v["pins"] for k, v in an["macros"].items()}
    if an["problems"]:
        res["failed"] = "hierarchy: " + "; ".join(an["problems"])
        log(f"{u['name']}: {res['failed']}")
        return res
    with open(os.path.join(udir, "synth.ys"), "w") as f:
        f.write(synth_script(u, an, t, pdk, args.period, rtl, udir))
    log(f"{u['name']}: yosys ({u['module']} {u['params']})")
    rc = run([t["yosys"], "-q", "-l", "yosys.log", "-s", "synth.ys"], udir, os.path.join(udir, "yosys.out"), timeout=args.timeout)
    res["synth_seconds"] = time.time() - t0
    if rc != 0:
        res["failed"] = f"yosys exit {rc}"
        log(f"{u['name']}: yosys FAILED (exit {rc}) in {res['synth_seconds']:.0f} s")
        return res
    res["repeaters"] = buffer_fanout(os.path.join(udir, "netlist.json"), pdk)
    if res["repeaters"]:
        libs2 = " ".join(f"-liberty {l}" for l in t["libs"])
        with open(os.path.join(udir, "rewrite.ys"), "w") as f:            # the JSON carries the black boxes (stubs, library cells)
            f.write(f"read_json netlist.json\nhierarchy -top {u['name']}\n"
                    f"tee -q -o stat.json stat {libs2} -json\ntee -q -o stat.txt stat {libs2}\n"
                    "write_verilog -noattr -noexpr -nohex -nodec netlist.v\n")
        rc = run([t["yosys"], "-q", "-l", "rewrite.log", "-s", "rewrite.ys"], udir, os.path.join(udir, "rewrite.out"), timeout=args.timeout)
        if rc != 0:
            res["failed"] = f"yosys rewrite (fanout trees) exit {rc}"
            log(f"{u['name']}: rewrite FAILED (exit {rc})")
            return res
    fix_netlist(os.path.join(udir, "netlist.v"))
    if u["params"].get("DV_HOOKS") == 0:          # on the synthesized netlist: the tie-offs' dead logic is gone, the black boxes stay
        res["dv_hooks"] = dv_hooks_check(Netlist(os.path.join(udir, "netlist.json")), an)
    mods = parse_stat(udir) or {}
    s = mods.get(u["name"], {})
    by_type = {k.lstrip("\\"): v for k, v in s.get("num_cells_by_type", {}).items()}
    own_targets = {target for _, _, target, _ in an["plan"]}
    res["own_cells"] = sum(v for k, v in by_type.items() if k not in own_targets)      # library cells of this unit
    res["own_area_um2"] = float(s.get("area", 0.0) or 0.0)
    res["own_flops"] = sum(v for k, v in by_type.items() if "DFF" in k.upper() or k.startswith(("DHL", "DL")))
    res["cells_by_type"] = by_type
    res["latches"] = latches(udir)
    res["check"] = check_problems(udir)
    log(f"{u['name']}: yosys done in {res['synth_seconds']:.0f} s: {res['own_cells']:,} cells, {res['own_area_um2']:,.0f} um2")
    return res


# ---------------------------------------------------------------------------------------------------------------------
# the tree: totals, the PE count, the netlists of a subtree
# ---------------------------------------------------------------------------------------------------------------------
def subtree(name, results, acc=None):
    acc = acc if acc is not None else []
    if name in acc:
        return acc
    acc.append(name)
    for child in (results.get(name, {}).get("children") or {}):
        subtree(child, results, acc)
    return acc


def totals(name, results, memo):
    if name in memo:
        return memo[name]
    r = results.get(name, {})
    if r.get("failed") or "own_cells" not in r:
        memo[name] = None
        return None
    cells, area, flops, marea = r["own_cells"], r["own_area_um2"], r["own_flops"], 0.0
    pes = 1 if UNIT[name]["module"] == "mac_pe" else 0
    for m, rec in (r.get("macros") or {}).items():
        marea += rec["area_um2"] * rec["instances"]
    for child, n in (r.get("children") or {}).items():
        ct = totals(child, results, memo)
        if ct is None:
            memo[name] = None
            return None
        cells += n * ct["cells"]; area += n * ct["cell_area_um2"]; flops += n * ct["flops"]; marea += n * ct["macro_area_um2"]; pes += n * ct["pe_count"]
    memo[name] = dict(cells=cells, cell_area_um2=area, flops=flops, macro_area_um2=marea, total_area_um2=area + marea, pe_count=pes)
    return memo[name]


def all_macros(results):
    recs = {}
    for r in results.values():
        for m, rec in (r.get("macros") or {}).items():
            if m not in recs:
                recs[m] = dict(rec, pins=(r.get("macro_pins") or {}).get(m))
    return list(recs.values())


# ---------------------------------------------------------------------------------------------------------------------
# OpenSTA
# ---------------------------------------------------------------------------------------------------------------------
TILE_SDC = """# neo_tile at {period} {unit} (09_synthesis/constraints.sdc, drop 0.16, in the library's units; drop 0.40)
create_clock -name clk -period {period} [get_ports clk]
set_clock_uncertainty -setup {u_setup} [get_clocks clk]
set_clock_uncertainty -hold {u_hold} [get_clocks clk]
set_clock_transition {ctrans} [get_clocks clk]
# asynchronous reset: assertion async, deassertion synchronized outside the block
set_false_path -from [get_ports rst_n]
# the tile's coordinates are straps
set_false_path -from [get_ports {{my_x* my_y*}}]
# mesh links: registered on both sides; a budget of 0.4 of the cycle to and from the neighbouring tile
set_input_delay  -clock clk -max {link} [get_ports {{in_valid* in_flit* out_ready*}}]
set_output_delay -clock clk -max {link} [get_ports {{out_valid* out_flit* in_ready*}}]
# host register bus: relaxed (registered in the aggregator)
set_input_delay  -clock clk -max {host} [get_ports {{h_we h_re h_addr* h_wdata*}}]
set_output_delay -clock clk -max {host} [get_ports {{h_rdata* err_pin}}]
# status flags are sampled by the island at its own rate
set_multicycle_path 4 -setup -to [get_ports {{prog_done drain_busy done parity_err crc_err *_sticky ecc_* wbuf_* rq_tbl_perr lost_err fetch_timeout}}]
set_multicycle_path 3 -hold  -to [get_ports {{prog_done drain_busy done parity_err crc_err *_sticky ecc_* wbuf_* rq_tbl_perr lost_err fetch_timeout}}]
set_max_fanout 32 [current_design]
set_max_transition {mtrans} [current_design]
"""
BLOCK_SDC = """# {name} at {period} {unit}: the clock and 0.2 of the period on every input and output (drop 0.40)
create_clock -name clk -period {period} [get_ports clk]
set_clock_uncertainty -setup {u_setup} [get_clocks clk]
set_input_delay  -clock clk -max {io} [delete_from_list [all_inputs] [get_ports {{clk rst_n}}]]
set_output_delay -clock clk -max {io} [all_outputs]
set_false_path -from [get_ports rst_n]
set_max_fanout 32 [current_design]
set_max_transition {mtrans} [current_design]
"""
# the layer descriptor (host_if order 0x10-0x24, and cfg_m): static during a run by protocol (drop 0.41) -- the host or
# the DMA writes it only between runs, before go, and the sequencer takes several cycles from go to its first f_start --
# so the paths from it have four cycles instead of 0.2 of one period of input delay. NOT in the list: cfg_ct / cfg_ky /
# cfg_kx of act_feeder and neo_mac_core_v02 (the run's position, which the sequencer changes between runs one cycle
# before start), and the requantization tables (tbl_* / relu of requant, rq_tbl_* / rq_relu of neo_core), which the DMA
# writes during a layer. A unit gets the constraint from the descriptor inputs it has; the tile, whose descriptor lives
# in host_if's registers, through the descriptor pins of its child instances (u_core/cfg_*); none when there are none.
STATIC_CFG = ("cfg_h", "cfg_w", "cfg_ho", "cfg_wo", "cfg_oy0", "cfg_oy_n", "cfg_iy0", "cfg_s", "cfg_p", "cfg_k", "cfg_ct_n",
              "cfg_ct0", "cfg_ky0", "cfg_kx0", "cfg_rn", "cfg_contrib_n", "cfg_tile_pixels", "cfg_regions_m1", "cfg_m")
STATIC_SDC = """# the layer descriptor ({what}): written by the host or the DMA only between runs (before go) and held
# for the whole run, the sequencer several cycles from go to its first f_start -- four cycles for its paths (drop 0.41)
set_multicycle_path 4 -setup {how} [get_{kind} {{{names}}}]
set_multicycle_path 3 -hold  {how} [get_{kind} {{{names}}}]
"""
COMB_SDC = """# {name}: combinational -- a virtual clock of {period} {unit}, inputs and outputs at its edges (drop 0.40)
create_clock -name vclk -period {period}
set_input_delay  -clock vclk 0 [all_inputs]
set_output_delay -clock vclk 0 [all_outputs]
set_max_fanout 32 [current_design]
set_max_transition {mtrans} [current_design]
"""
STA_TCL = """# {name}: OpenSTA on the unit's netlist with its children as extracted timing models (drop 0.40)
{read_libs}
read_verilog netlist.v
link_design {name}
read_sdc block.sdc
report_checks -path_delay max -group_path_count 10 -endpoint_path_count 1 -unique_paths_to_endpoint -fields {{slew cap fanout}} -digits 1 -format full_clock_expanded > paths_max.txt
report_checks -path_delay min -group_path_count 3 -digits 1 > paths_min.txt
set f [open summary.txt w]
puts $f "wns_max [worst_slack -max]"
puts $f "tns_max [total_negative_slack -max]"
puts $f "wns_min [worst_slack -min]"
close $f
report_clock_min_period > min_period.txt
report_check_types -max_slew -max_capacitance -max_fanout -violators > drv.txt
set f [open endpoints.txt w]
foreach pin [sta::endpoints] {{
  puts $f "[get_full_name $pin] [get_property $pin slack_max]"
}}
close $f
# the unit as a liberty cell for its parent's run: setup / hold at the inputs, clock-to-output and input-to-output arcs
write_timing_model -library_name {name}_etm -cell_name {name} etm.lib
exit
"""


def static_cfg(nl):
    """The descriptor inputs of the unit (STATIC_CFG names among its input ports) and the descriptor input pins of its
    child instances (`instance/port`, for the tile, whose children are timing models)."""
    ports = [pn for pn, p in nl.ports(nl.top).items() if pn in STATIC_CFG and p.get("direction") == "input"]
    pins = []
    for cn, c in nl.cells().items():
        dirs = c.get("port_directions") or {}
        pins += [f"{cn}/{pn}" for pn in c.get("connections", {}) if pn in STATIC_CFG and dirs.get(pn) == "input"]
    return sorted(ports, key=STATIC_CFG.index), sorted(pins)


def write_sdc(u, has_clk, pdk, period_ps, udir, cfg_ports=(), cfg_pins=(), fname="block.sdc"):
    tu = pdk["time_unit_ps"]
    P = period_ps / tu
    unit = "ps" if tu == 1.0 else "ns"
    fmt = (lambda x: f"{x:.1f}") if tu == 1.0 else (lambda x: f"{x:.4f}")
    if u["tile"]:
        s = TILE_SDC.format(period=fmt(P), unit=unit, u_setup=fmt(0.08 * P), u_hold=fmt(0.04 * P), ctrans=fmt(0.06 * P),
                            link=fmt(0.4 * P), host=fmt(0.5 * P), mtrans=fmt(0.16 * P))
        if cfg_pins:                                  # the descriptor from host_if's registers into the core's pins
            s += STATIC_SDC.format(what="the child instances' descriptor pins", how="-through", kind="pins", names=" ".join(cfg_pins))
    elif has_clk:
        s = BLOCK_SDC.format(name=u["name"], period=fmt(P), unit=unit, u_setup=fmt(0.08 * P), io=fmt(0.2 * P), mtrans=fmt(0.16 * P))
        if cfg_ports:
            s += STATIC_SDC.format(what="the descriptor inputs", how="-from", kind="ports", names=" ".join(cfg_ports))
    else:
        s = COMB_SDC.format(name=u["name"], period=fmt(P), unit=unit, mtrans=fmt(0.16 * P))
    with open(os.path.join(udir, fname), "w") as f:
        f.write(s)


def run_sta(u, results, t, pdk, args, out):
    """OpenSTA on the unit's own netlist, every child unit a liberty cell from its own run's extracted timing model
    (`write_timing_model`: setup / hold on the inputs, clock-to-output and input-to-output arcs) -- the paths inside a child
    are the child's report, the paths between the units this one; a flat read of the tile's 2.5 M cells is beyond 6 GB.
    The run ends by writing this unit's model for its parent."""
    udir = os.path.join(out, u["name"])
    children = list((results[u["name"]].get("children") or {}).keys())
    needed = [os.path.join(udir, "netlist.v")] + [os.path.join(out, c, "etm.lib") for c in children]
    missing = [p for p in needed if not os.path.isfile(p)]
    if missing:
        return dict(exit=-1, error=f"missing: {', '.join(os.path.relpath(p, out) for p in missing)}")
    nl = Netlist(os.path.join(udir, "netlist.json"))
    cfg_ports, cfg_pins = static_cfg(nl)
    write_sdc(u, "clk" in nl.ports(nl.top), pdk, args.period, udir, cfg_ports, cfg_pins)
    del nl
    libs = list(t["libs"])
    macro_lib = os.path.join(out, "macros.lib")
    if os.path.isfile(macro_lib) and results[u["name"]].get("macros"):
        libs.append(macro_lib)
    libs += needed[1:]
    tcl = STA_TCL.format(name=u["name"], read_libs="\n".join(f"read_liberty {l}" for l in libs))
    with open(os.path.join(udir, "sta.tcl"), "w") as f:
        f.write(tcl)
    t0 = time.time()
    rc = run([t["sta"], "-no_init", "-no_splash", "-exit", "sta.tcl"], udir, os.path.join(udir, "sta.log"), timeout=args.timeout)
    r = parse_sta(udir, pdk, args.period) or {}
    r["exit"] = rc
    if rc == 0 and not os.path.isfile(os.path.join(udir, "etm.lib")):
        r["exit"] = -2
        r["error"] = "no timing model written"
    r["seconds"] = time.time() - t0
    r["models"] = children
    return r


def parse_min_period(path, tu):
    """(min period in ps, fmax in MHz) from report_clock_min_period's line, or (None, None). The period from fmax when
    there is one: OpenSTA prints period_min with two decimals in the library's unit (10 ps steps in ns), fmax finer."""
    try:
        for line in open(path):
            m = re.search(r"period_min\s*=\s*([0-9.eE+-]+)\s+fmax\s*=\s*([0-9.eE+-]+)", line)
            if m:
                pmin, fmax = float(m.group(1)) * tu, float(m.group(2))
                return (1e6 / fmax if fmax > 0 else pmin), fmax
    except FileNotFoundError:
        pass
    return None, None


def parse_sta(udir, pdk, period_ps):
    tu = pdk["time_unit_ps"]
    r = {}
    try:
        for line in open(os.path.join(udir, "summary.txt")):
            k, v = line.split()
            try:
                r[k + "_ps"] = float(v) * tu
            except ValueError:
                r[k + "_ps"] = None
    except FileNotFoundError:
        return None
    mp, fmax = parse_min_period(os.path.join(udir, "min_period.txt"), tu)
    if mp is not None:
        r["min_period_ps"], r["fmax_mhz"] = mp, fmax
    try:
        slacks = []
        for line in open(os.path.join(udir, "endpoints.txt")):
            parts = line.split()
            if len(parts) == 2:
                try:
                    v = float(parts[1]) * tu
                except ValueError:
                    continue
                if math.isfinite(v):
                    slacks.append(v)
        if slacks:
            r["endpoints"] = len(slacks)
            r["endpoints_violating"] = sum(1 for s in slacks if s < 0)
            binw = period_ps / 10.0
            hist = {}
            for s in slacks:
                b = math.floor(s / binw)
                hist[b] = hist.get(b, 0) + 1
            r["slack_histogram"] = {f"{b * binw:.0f}..{(b + 1) * binw:.0f} ps": n for b, n in sorted(hist.items())}
    except FileNotFoundError:
        pass
    try:
        txt = open(os.path.join(udir, "paths_max.txt")).read()
        m = re.search(r"Startpoint:\s*(\S+).*?Endpoint:\s*(\S+)", txt, re.S)
        if m:
            r["worst_start"], r["worst_end"] = m.group(1), m.group(2)
        m = re.search(r"\n\s*([-0-9.]+)\s+slack \((VIOLATED|MET)\)", txt)
        if m:
            r["worst_slack_ps"] = float(m.group(1)) * tu
        m = re.search(r"\n\s*([-0-9.]+)\s+data arrival time\n", txt)
        if m:
            r["worst_arrival_ps"] = float(m.group(1)) * tu
    except FileNotFoundError:
        pass
    return r


# ---------------------------------------------------------------------------------------------------------------------
# the period sweep (drop 0.42)
# ---------------------------------------------------------------------------------------------------------------------
SWEEP_TCL = """# {name}: the period sweep (drop 0.42) -- OpenSTA on the unit's netlist with its children's timing models, which do not
# depend on the period; the SDC of each period read in turn (each one replaces the clock, the I/O delays and the
# uncertainty of the one before -- the same figures as a fresh run at that period)
{read_libs}
read_verilog netlist.v
link_design {name}
{periods}
exit
"""
SWEEP_ONE = """read_sdc sweep_{tag}.sdc
set n 0
set v 0
foreach pin [sta::endpoints] {{
  set s [get_property $pin slack_max]
  if {{[string is double -strict $s] && $s < 1e29 && $s > -1e29}} {{
    incr n
    if {{$s < 0}} {{ incr v }}
  }}
}}
set f [open sweep_{tag}.txt w]
puts $f "wns_max [worst_slack -max]"
puts $f "tns_max [total_negative_slack -max]"
puts $f "endpoints $n"
puts $f "violating $v"
close $f
report_clock_min_period > sweep_{tag}_minp.txt
report_checks -path_delay max -group_path_count 1 -digits 1 > sweep_{tag}_path.txt
"""


def parse_periods(spec, pdk):
    """--sweep: 'default' (the library's sweep_ps), or a comma-separated list in ps, each value optionally with an 'ns'
    suffix (0.75ns = 750 ps); returns ps, sorted."""
    if not spec or spec == "none":
        return []
    if spec == "default":
        return sorted(pdk["sweep_ps"])
    out = []
    for tok in spec.split(","):
        tok = tok.strip().lower()
        out.append(float(tok[:-2]) * 1000.0 if tok.endswith("ns") else float(tok[:-2] if tok.endswith("ps") else tok))
    return sorted(set(out))


def ptag(p):
    return f"{int(round(p))}"


def run_sweep(u, results, t, pdk, args, out, periods):
    """One OpenSTA session per unit over every swept period: per period WNS, TNS, the endpoints and the violating ones,
    the minimum period, the worst path's start and end -- the same constraints as the unit's main run at that period."""
    udir = os.path.join(out, u["name"])
    children = list((results[u["name"]].get("children") or {}).keys())
    needed = [os.path.join(udir, "netlist.v")] + [os.path.join(out, c, "etm.lib") for c in children]
    missing = [p for p in needed if not os.path.isfile(p)]
    if missing:
        return dict(error=f"missing: {', '.join(os.path.relpath(p, out) for p in missing)}")
    nl = Netlist(os.path.join(udir, "netlist.json"))
    cfg_ports, cfg_pins = static_cfg(nl)
    has_clk = "clk" in nl.ports(nl.top)
    del nl
    for p in periods:
        write_sdc(u, has_clk, pdk, p, udir, cfg_ports, cfg_pins, fname=f"sweep_{ptag(p)}.sdc")
    libs = list(t["libs"])
    macro_lib = os.path.join(out, "macros.lib")
    if os.path.isfile(macro_lib) and results[u["name"]].get("macros"):
        libs.append(macro_lib)
    libs += needed[1:]
    tcl = SWEEP_TCL.format(name=u["name"], read_libs="\n".join(f"read_liberty {l}" for l in libs),
                           periods="\n".join(SWEEP_ONE.format(tag=ptag(p)) for p in periods))
    with open(os.path.join(udir, "sweep.tcl"), "w") as f:
        f.write(tcl)
    t0 = time.time()
    rc = run([t["sta"], "-no_init", "-no_splash", "-exit", "sweep.tcl"], udir, os.path.join(udir, "sweep.log"), timeout=args.timeout)
    tu = pdk["time_unit_ps"]
    res = dict(exit=rc, seconds=time.time() - t0, periods={})
    for p in periods:
        tag, r = ptag(p), {}
        try:
            for line in open(os.path.join(udir, f"sweep_{tag}.txt")):
                k, v = line.split()
                try:
                    r[k] = float(v)
                except ValueError:
                    r[k] = None
        except FileNotFoundError:
            res["periods"][tag] = dict(error="no result")
            continue
        e = dict(wns_ps=None if r.get("wns_max") is None else r["wns_max"] * tu,
                 tns_ps=None if r.get("tns_max") is None else r["tns_max"] * tu,
                 endpoints=int(r.get("endpoints") or 0), endpoints_violating=int(r.get("violating") or 0))
        e["min_period_ps"], _ = parse_min_period(os.path.join(udir, f"sweep_{tag}_minp.txt"), tu)
        try:
            txt = open(os.path.join(udir, f"sweep_{tag}_path.txt")).read()
            m = re.search(r"Startpoint:\s*(\S+).*?Endpoint:\s*(\S+)", txt, re.S)
            if m:
                e["worst_start"], e["worst_end"] = m.group(1), m.group(2)
        except FileNotFoundError:
            pass
        res["periods"][tag] = e
    return res


# the units whose stage is the PE's product: the PE and the array, the streaming core, and every unit above them
PE_LIMITED = ("mac_pe", "systolic_array", "neo_mac_core")


def pe_limited_units(names):
    """The PE-limited units among names: the units of the modules in PE_LIMITED (mac_pe covers mac_pe_chk) and, through
    the unit tree, their parents."""
    lim = {n for n in names if UNIT[n]["module"] in PE_LIMITED}
    grew = True
    while grew:
        grew = False
        for n in names:
            if n not in lim and any(c in lim for ch in UNIT[n]["children"].values() for c in ch):
                lim.add(n)
                grew = True
    return lim


def sweep_verdict(results, names, periods):
    """The verdict of the sweep over the silicon tile's units (the 16x8 core's are not in the tile; all units when the
    tile was not run): the smallest swept period at which every unit except the PE-limited ones has no violating
    endpoint, the PE's own stage (mac_pe's minimum period) and the period the tile runs at on the proxy -- the larger of
    the two -- with the units still violating at each period."""
    scope = [n for n in (subtree("neo_tile", results) if "neo_tile" in names else names) if n in names]
    lim = pe_limited_units(scope)
    judged = [n for n in scope if n not in lim and (results.get(n, {}).get("sweep") or {}).get("periods")]
    per = {}
    for p in periods:
        tag = ptag(p)
        bad = [n for n in judged if (results[n]["sweep"]["periods"].get(tag) or {}).get("endpoints_violating", 1) != 0]
        per[tag] = bad
    clean = next((p for p in periods if not per[ptag(p)]), None)
    stages = {n: (results.get(n, {}).get("sta") or {}).get("min_period_ps") for n in scope if n in lim}
    pe = stages.get("mac_pe")
    tile = None if clean is None or pe is None else max(clean, pe)
    worst_other = max(((results[n].get("sta") or {}).get("min_period_ps") or 0.0, n) for n in judged) if judged else (None, None)
    return dict(scope=scope, pe_limited=sorted(lim), judged=judged, violating_by_period=per, clean_period_ps=clean,
                pe_stage_ps=pe, pe_limited_stages=stages, tile_period_ps=tile, tile_fmax_mhz=(1e6 / tile if tile else None),
                worst_other_min_period=dict(unit=worst_other[1], ps=worst_other[0]))


# ---------------------------------------------------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------------------------------------------------
def select_units(sel):
    names = [u["name"] for u in UNITS]
    if sel == "all":
        chosen = names
    elif sel == "tile":
        chosen = ["neo_tile"]
    elif sel == "core":
        chosen = ["neo_core"]
    elif sel == "core16":
        chosen = ["c16_neo_core"]
    else:
        chosen = sel.split(",")
        missing = [n for n in chosen if n not in UNIT]
        if missing:
            sys.exit(f"synth.py: unknown units {missing}; known: {names}")
    closure = []                       # a unit's timing needs its subtree: the closure over the children

    def add(n):
        if n in closure:
            return
        for ch in UNIT[n]["children"].values():
            for c in ch:
                add(c)
        closure.append(n)
    for n in chosen:
        add(n)
    return closure


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rtl", default="rtl", help="the RTL directory (flat layout)")
    ap.add_argument("--out", default="synth_out", help="output directory")
    ap.add_argument("--pdk", default="asap7", choices=sorted(PDKS))
    ap.add_argument("--corner", default="SS")
    ap.add_argument("--period", type=float, default=None, help="clock period in ps (default: the library's: 500)")
    ap.add_argument("--blocks", default="all", help="all | tile | core | core16 | comma-separated unit names (their subtrees are included)")
    ap.add_argument("--jobs", type=int, default=2, help="parallel unit runs")
    ap.add_argument("--timeout", type=int, default=4 * 3600, help="seconds per tool run")
    ap.add_argument("--no-sta", action="store_true", help="synthesis only")
    ap.add_argument("--sta-only", action="store_true", help="timing on an existing synthesis output")
    ap.add_argument("--resume", action="store_true", help="keep the units already synthesized in --out (synth_results.json) and run the rest")
    ap.add_argument("--sweep", default="none", help="the period sweep after the timing runs (drop 0.42): 'default' (the library's "
                    "list: 500,750,1000,1250,1500 ps on both), 'none', or a list in ps -- an 'ns' suffix per value for ns (0.75ns)")
    args = ap.parse_args()
    pdk = dict(PDKS[args.pdk])
    if args.corner not in pdk["libs"]:
        sys.exit(f"synth.py: corner {args.corner} not in {sorted(pdk['libs'])} for {args.pdk}")
    if args.period is None:
        args.period = pdk["default_period_ps"]
    periods = parse_periods(args.sweep, pdk)
    t = find_tools(args)
    pdk["macro"], macro_sources = derive_macro(pdk, os.path.join(t["pdk_lib"], pdk["dir"]))
    sta_missing = t["sta"] is None and not args.no_sta
    if sta_missing:
        args.no_sta = True
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    rtl = os.path.abspath(args.rtl)
    for f in RTL_FILES:
        if not os.path.isfile(os.path.join(rtl, f)):
            sys.exit(f"synth.py: RTL file missing: {os.path.join(rtl, f)}")
    versions = tool_versions(t)
    log(f"tools: {versions}")
    log(f"library {args.pdk} {args.corner}, period {args.period} ps, output {out}; macro model: {pdk['macro']['note']}")
    if periods:
        log(f"period sweep: {[ptag(p) for p in periods]} ps")
    names = select_units(args.blocks)
    log(f"units: {names}")
    t_start = time.time()
    results = {}
    if args.sta_only:
        with open(os.path.join(out, "synth_results.json")) as f:
            results = json.load(f)["units"]
    else:
        done = set()
        if args.resume and os.path.isfile(os.path.join(out, "synth_results.json")):
            with open(os.path.join(out, "synth_results.json")) as f:
                prev = json.load(f)["units"]
            for n, r in prev.items():
                if n in names and not r.get("failed") and "own_cells" in r and os.path.isfile(os.path.join(out, n, "netlist.v")):
                    results[n] = {k: v for k, v in r.items() if k != "sta"}
                    done.add(n)
            log(f"resume: keeping {sorted(done)}")
        # phase A: every unit's synthesis, in parallel (the children are black boxes); the long ones first
        order = sorted((n for n in names if n not in done), key=lambda n: 0 if n in ("tile_nic", "noc_router", "neo_tile", "neo_core", "requant") else 1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
            futs = {ex.submit(run_unit, UNIT[n], t, pdk, args, out, rtl): n for n in order}
            for fut in concurrent.futures.as_completed(futs):
                r = fut.result()
                results[r["name"]] = r
        recs = all_macros(results)
        if recs:
            write_macro_lib(recs, pdk, os.path.join(out, "macros.lib"))
            log(f"macros: {[r['name'] for r in recs]}")
    memo = {}
    for n in names:
        tot = totals(n, results, memo)
        if tot:
            results[n].update(tot)
            results[n]["pe_check"] = (UNIT[n]["pe_expected"] is None) or (tot["pe_count"] == UNIT[n]["pe_expected"])
    # phase B: timing per unit, children before parents (a parent's run reads its children's timing models), each level
    # in parallel
    if not args.no_sta:
        pending = [n for n in names if not results.get(n, {}).get("failed") and "own_cells" in results.get(n, {})]
        finished = set()
        while pending:
            ready = [n for n in pending if all(c in finished or c not in pending for c in (results[n].get("children") or {}))]
            if not ready:
                ready = list(pending)
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
                futs = {ex.submit(run_sta, UNIT[n], results, t, pdk, args, out): n for n in ready}
                for fut in concurrent.futures.as_completed(futs):
                    n = futs[fut]
                    results[n]["sta"] = r = fut.result()
                    finished.add(n)
                    log(f"{n}: sta done in {r.get('seconds', 0):.0f} s (exit {r.get('exit')}{'; ' + r['error'] if r.get('error') else ''}; "
                        f"wns {r.get('wns_max_ps')} ps, min period {r.get('min_period_ps')} ps)")
            pending = [n for n in pending if n not in finished]
    # phase C (drop 0.42): the period sweep, every unit at once (the children's timing models exist after phase B)
    verdict = None
    if periods and not args.no_sta:
        swept = [n for n in names if (results.get(n, {}).get("sta") or {}).get("exit") == 0]
        t_sw = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
            futs = {ex.submit(run_sweep, UNIT[n], results, t, pdk, args, out, periods): n for n in swept}
            for fut in concurrent.futures.as_completed(futs):
                n = futs[fut]
                results[n]["sweep"] = r = fut.result()
                log(f"{n}: sweep done in {r.get('seconds', 0):.0f} s (exit {r.get('exit')}{'; ' + r['error'] if r.get('error') else ''}; "
                    f"violating endpoints {[(r.get('periods') or {}).get(ptag(p), {}).get('endpoints_violating') for p in periods]})")
        verdict = sweep_verdict(results, names, periods)
        log(f"sweep done in {time.time() - t_sw:.0f} s; verdict: every unit outside the PE-limited ones clean from "
            f"{verdict['clean_period_ps']} ps, the PE's stage {verdict['pe_stage_ps']} ps, the tile at {verdict['tile_period_ps']} ps")
    if "neo_tile" in results and not results["neo_tile"].get("failed"):
        bad = [n for n in subtree("neo_tile", results) if results[n].get("dv_hooks") and not results[n]["dv_hooks"]["passed"]]
        results["neo_tile"]["dv_hooks_tree"] = dict(passed=not bad, failing_units=bad)
    summary = dict(pdk=args.pdk, corner=args.corner, period_ps=args.period, tools=versions, libs=t["libs"],
                   macro_model=pdk["macro"], macro_sources=macro_sources, macro_area_factor=MACRO_AREA_FACTOR,
                   ratio_note=pdk["ratio_note"], time_unit_ps=pdk["time_unit_ps"], sweep_ps=periods, verdict=verdict,
                   seconds=time.time() - t_start, date=time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                   order=names, units=results)
    with open(os.path.join(out, "synth_results.json"), "w") as f:
        json.dump(summary, f, indent=1, default=str)
    log(f"done in {summary['seconds']:.0f} s: {out}/synth_results.json")
    gates = []
    if sta_missing:
        gates.append("timing: OpenSTA is not installed (synth/tools.sh could not build it; see tools.txt) -- no timing run")
    for n in names:
        r = results.get(n, {})
        if r.get("failed"):
            gates.append(f"{n}: {r['failed']}")
            continue
        if r.get("latches"):
            gates.append(f"{n}: {r['latches']} latches")
        if r.get("check") and r["check"].get("multiple_drivers"):
            gates.append(f"{n}: {r['check']['multiple_drivers']} multiply-driven nets")
        if not r.get("pe_check", True):
            gates.append(f"{n}: PE count {r.get('pe_count')} != {UNIT[n]['pe_expected']}")
        if r.get("dv_hooks") and not r["dv_hooks"]["passed"]:
            gates.append(f"{n}: dv_* hooks drive logic: {r['dv_hooks']['bad_loads'][:3]} {r['dv_hooks']['bad_outputs'][:3]}")
        if not args.no_sta and r.get("sta", {}).get("exit", 0) != 0:
            gates.append(f"{n}: sta exit {r['sta'].get('exit')} {r['sta'].get('error', '')}")
        sw = r.get("sweep")
        if sw and (sw.get("exit", 0) != 0 or sw.get("error") or any(e.get("error") for e in (sw.get("periods") or {}).values())):
            gates.append(f"{n}: sweep exit {sw.get('exit')} {sw.get('error', '')}")
    if gates:
        log("GATES FAILED: " + "; ".join(gates))
        return 1
    log("GATES: all passed (every unit synthesized, no latch, no multiply-driven net, the hierarchy consistent, the PE counts, "
        "DV_HOOKS = 0 clean, every timing run complete)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
