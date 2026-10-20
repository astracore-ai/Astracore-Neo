#!/usr/bin/env python3
"""
build_fmeda.py -- AstraCore Neo FMEDA skeleton (ISO 26262-5 / -11 style), drop 0.3; v2 from the fault campaign, drop 0.43.

  build_fmeda.py                       the v1 workbook (neo_fmeda_skeleton.xlsx; neo_fmeda_v1.xlsx is this content)
  build_fmeda.py --v2 --faults DIR [--formal formal_report.md] [--out DIR]
      FMEDA v2: every row's diagnostic coverage measured by the fault campaign (DIR = the faults job's output:
      faults.csv, faults_summary.json, RUN.txt -- the ci-logs-faults branch's ci_logs/faults), the formal job's proofs
      cited per row (formal_report.md from ci-logs-formal), SPFM / LFM / PMHF recomputed with v1's base failure rates
      (plus the weight buffer's storage, which v1 had no row for); writes neo_fmeda_v2.xlsx and fmeda_v2.md. The v2
      rows, their campaign selections and the reasons a row keeps v1's value are V2_ROWS / CHIP_SEL below.
  build_fmeda.py --v3 --faults DIR [--formal formal_report.md] [--out DIR]
      FMEDA v3 (drop 0.44, stream S1): the same rows and selections on the campaign run against the safety closure --
      SECDED on the activation buffer, the activation path's byte parity, the index lines' parity, the packer's beat
      parity check, the one-hot MBIST state -- with the SRAM base rates corrected to the silicon leaves of
      09_synthesis/memories.md (V3_BASE: the activation buffer 1,024 x 624 bits with its check bits, the accumulator
      512 x 1,056), the tile's NIC memories as a chip element v1 and v2 lacked (NIC_MEM_MBIT), a sensitivity with the
      pipeline flops at the synthesized count (V3_FLOPS_MBIT), the per-mechanism table of S1_REGS and the before / after
      of every block against v2 (read from neo_fmeda_v2.xlsx beside this file); writes neo_fmeda_v3.xlsx and fmeda_v3.md.

Sheets
  Assumptions  base failure rates and areas (blue = inputs, yellow = assumptions to replace
               with foundry data per ISO 26262-11); every FMEDA row references these cells
  FMEDA_core   failure-mode table for one MAC core (the SEooC element): lambda, safety
               relevance, failure-mode distribution, safety mechanism, diagnostic coverage,
               residual and latent failure rates, all as formulas
  Metrics      SPFM, LFM and first-order PMHF for the core vs ASIL-B(D) and ASIL-D targets
  Chip_rollup  64 cores plus the other elements of the accelerator die, chip-level metrics

Formulas (ISO 26262-5 Annex C simplified; the assessor's workbook replaces them at phase 1):
  lambda_SR,fm     = lambda x safety_related x FMD
  lambda_SPF/RF    = lambda_SR,fm x SPF_relevant x (1 - DC_RF)
  lambda_MPF,L     = lambda_SR,fm x MPF_relevant x (1 - DC_L)
  SPFM             = 1 - sum(lambda_SPF/RF) / sum(lambda_SR)
  LFM              = 1 - sum(lambda_MPF,L) / (sum(lambda_SR) - sum(lambda_SPF/RF))
  PMHF (1st order) = sum(lambda_SPF/RF) + sum(lambda_MPF,L) x sum(lambda_SR) x T_life x 1e-9
"""
import argparse
import csv
import json
import math
import os
import re

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

ARIAL = "Arial"
BLUE = Font(name=ARIAL, color="0000FF")
BLACK = Font(name=ARIAL, color="000000")
BOLD = Font(name=ARIAL, bold=True)
HDR = Font(name=ARIAL, bold=True, color="FFFFFF")
HDR_FILL = PatternFill("solid", fgColor="1F3864")
YELLOW = PatternFill("solid", fgColor="FFFF00")
GREY = PatternFill("solid", fgColor="D9D9D9")
THIN = Side(style="thin", color="999999")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(wrap_text=True, vertical="top")


ASSUMPTIONS = [
    ("Die permanent base failure rate (whole 210 mm² die)", 200, "FIT",
     "Assumption, order of magnitude for a large N5-class automotive die; replace with foundry data"),
    ("Die area", 210, "mm²", "Spec v2.0 physical estimate"),
    ("MAC core area incl. buffers (one core)", 0.47, "mm²", "30 mm² for 64 cores from the die estimate"),
    ("SRAM soft-error rate", 600, "FIT/Mbit", "Assumption; replace with foundry SER at ground level, Grade 2 mission profile"),
    ("Flip-flop soft-error rate", 100, "FIT/Mbit", "Assumption; replace with foundry SER"),
    ("Accumulator SRAM per core", 0.5, "Mbit", "512 rows × 32 columns × 32 bit = 64 KB"),
    ("Activation buffer SRAM per core", 0.25, "Mbit", "4096 pixels × 32 channels × 8 bit = 32 KB"),
    ("Weight registers per core (active + shadow)", 0.0176, "Mbit", "2 × 1056 PEs × 8.3 bit average incl. check column"),
    ("Pipeline and control flops per core", 0.04, "Mbit", "x/psum/token registers, skew and deskew, feeder, accumulator read registers"),
    ("Mission lifetime", 131400, "h", "15 years × 8,760 h; vehicle operating hours are lower, kept conservative"),
    ("ASIL-B(D) target SPFM", 0.90, "", "ISO 26262-5 Table 4"),
    ("ASIL-B(D) target LFM", 0.60, "", "ISO 26262-5 Table 5"),
    ("ASIL-B(D) target PMHF", 100, "FIT", "ISO 26262-5 Table 6"),
    ("ASIL-D target SPFM", 0.99, "", "ISO 26262-5 Table 4"),
    ("ASIL-D target LFM", 0.90, "", "ISO 26262-5 Table 5"),
    ("ASIL-D target PMHF", 10, "FIT", "ISO 26262-5 Table 6"),
]

# (block, failure mode, type, lambda_formula, SR, FMD, SPF, SM, DC_RF, MPF, latent SM, DC_L, status)
A = "Assumptions!"
V1_ROWS = [
    ("PE array datapath (1,056 MACs)", "Wrong product or sum, single-point", "permanent", f"={A}$B$22*0.55", 1, 0.90, 1,
     "ABFT check column: any single-point error in a row flips the row equality", 0.99, 0,
     "n/a (detected faults lead to safe state)", 0, "v1: proven on RTL (T2, T3, C1-C10, K2, M-tests); self-test of the checker proven through registers (M8)"),
    ("PE array datapath (1,056 MACs)", "Dual faults that cancel in the checksum", "permanent", f"={A}$B$22*0.55", 1, 0.10, 1,
     "Periodic in-field LBIST of the core", 0.90, 0, "n/a", 0, "v1: LBIST is tool-inserted (Modus) and remains the one item with no RTL proof; the island carries the hooks and the FTTI-windowed schedule (M8 watchdog/self-test)"),
    ("Weight registers (active + shadow)", "Bit flip in a stored weight", "transient", f"={A}$B$25", 1, 1.00, 1,
     "ABFT (corrupted weight flags every row) and reload per tile", 0.99, 0, "n/a", 0, "today: proven (T4 weight bit-flip)"),
    ("Weight registers (active + shadow)", "Stuck bit in a weight register", "permanent", f"={A}$B$22*0.05", 1, 1.00, 1,
     "ABFT", 0.99, 0, "n/a", 0, "today: proven by construction (same mechanism as T4)"),
    ("x / psum / token chains, skew, deskew", "Bit flip in a pipeline register", "transient", f"={A}$B$26*0.7", 1, 1.00, 1,
     "ABFT for data chains; token/valid parity", 0.95, 0, "n/a", 0, "v1: ABFT proven; token/valid rule is an SVA property (dv/sva) for formal; parity on valid/token stays a fault-simulation item"),
    ("x / psum / token chains, skew, deskew", "Stuck pipeline register", "permanent", f"={A}$B$22*0.10", 1, 1.00, 1,
     "ABFT; LBIST", 0.97, 0, "n/a", 0, "v1: ABFT covers data (T2-T4); control is covered by the sequencer lockstep (C7) and the self-test (M8); LBIST see row 2"),
    ("ABFT checker (array output)", "Stuck at 'no error' (false negative)", "permanent", f"={A}$B$22*0.02", 1, 0.50, 0,
     "none needed alone (does not violate SG by itself)", 0, 1,
     "Periodic self-test: inject one known fault per FTTI via the fault hook, expect a flag", 0.90, "planned: fault hook kept as a test feature, sequenced by the safety island"),
    ("ABFT checker (array output)", "False positive (spurious error)", "permanent", f"={A}$B$22*0.02", 1, 0.50, 0,
     "safe failure: leads to safe state", 0, 0, "n/a", 0, "safe failure mode"),
    ("Accumulator SRAM (64 KB)", "Single-bit upset", "transient", f"={A}$B$23*0.97", 1, 1.00, 1,
     "SECDED ECC (correct) and ABFT at drain (independent detection)", 0.9999, 0, "n/a", 0,
     "v1: ABFT at drain proven (C4); accumulator ECC not implemented: ABFT at drain detects any single-row corruption, so ECC would add correction, not detection"),
    ("Accumulator SRAM (64 KB)", "Multi-bit upset in one word", "transient", f"={A}$B$23*0.03", 1, 1.00, 1,
     "SECDED detect + ABFT at drain", 0.999, 0, "n/a", 0, "v1: argument only (interleaving is a memory-compiler choice); detection by ABFT at drain is proven (C4)"),
    ("Accumulator SRAM (64 KB)", "Stuck cell or decoder fault", "permanent", f"={A}$B$22*0.08", 1, 1.00, 1,
     "ECC, ABFT at drain, MBIST at boot", 0.999, 0, "n/a", 0, "v1: March C- MBIST implemented and proven (B1-B3 standalone, M11 through registers with ESM cause); ECC on banks proven (M5)"),
    ("Activation buffer SRAM (32 KB)", "Single-bit upset", "transient", f"={A}$B$24*0.97", 1, 1.00, 1,
     "SECDED ECC; end-to-end CRC from the DMA (ABFT cannot see a wrong input)", 0.999, 0, "n/a", 0,
     "v1: end-to-end CRC proven (M3); source-bank ECC proven (M5); MBIST proven (M11); the activation buffer itself is covered by the CRC at write and by the duplicated feeder control (C5)"),
    ("Activation buffer SRAM (32 KB)", "Multi-bit upset or stuck cell", "both", f"={A}$B$24*0.03+{A}$B$22*0.05", 1, 1.00, 1,
     "SECDED detect, E2E CRC, MBIST", 0.99, 0, "n/a", 0, "v1: proven for the bank path (M5, M9, M11); DC_L by periodic MBIST"),
    ("Feeder address generator and run FSM", "Wrong address, wrong sequence, early stop", "permanent", f"={A}$B$22*0.06", 1, 1.00, 1,
     "Duplicated address generator with comparator (DCLS-style)", 0.99, 0, "n/a", 0,
     "v1: duplicated feeder control proven (C5, drop 0.4)"),
    ("Feeder address generator and run FSM", "Bit flip in counters", "transient", f"={A}$B$26*0.15", 1, 1.00, 1,
     "Duplicated address generator with comparator", 0.99, 0, "n/a", 0, "v1: proven (C5)"),
    ("Accumulator write/read control and index pipeline", "Wrong row index or first flag", "both", f"={A}$B$22*0.03+{A}$B$26*0.15", 1, 1.00, 1,
     "Parity on index/first delay lines; ABFT at drain catches mixed rows only partially", 0.95, 0, "n/a", 0,
     "v1: coverage to be measured by fault simulation (Xcelium fault campaign); mechanism in place"),
    ("Clock and reset distribution in the core", "Clock stuck, glitch, reset glitch", "permanent", f"={A}$B$22*0.02", 1, 1.00, 1,
     "Clock monitor and watchdog in the safety island", 0.90, 0, "n/a", 0, "v1: windowed watchdog in the ESM proven (M8); clock monitor is a PLL/monitor IP item (RFQ 11)"),
    ("NoC interface (requests, responses)", "Corrupted packet, lost packet", "both", f"={A}$B$22*0.04+{A}$B$26*0.1", 1, 1.00, 1,
     "Packet parity/CRC, timeout, sequence numbers", 0.99, 0, "n/a", 0, "v1: router parity drop (N2), per-message end-to-end CRC (M3), word count per message for lost flits (M9a), fetch timeout (M9b); sequence numbers subsumed by the word count"),
    ("Sequencer FSM, run counters, weight-buffer addressing", "Wrong run order, wrong tile, early drain", "both", f"={A}$B$22*0.03+{A}$B$26*0.05", 1, 1.00, 1,
     "Duplicated sequencer with comparator (lockstep), weight-buffer ECC", 0.99, 0, "n/a", 0,
     "v1: lockstep sequencer proven (C7); weight-buffer ECC per lane proven (C9)"),
    ("Requantization tables and datapath", "Wrong scale, zero point or saturation", "both", f"={A}$B$22*0.02+{A}$B$26*0.03", 1, 1.00, 1,
     "Table parity and a duplicated requant stage with comparator", 0.99, 0, "n/a", 0,
     "v1: duplicated stage proven (C8); table parity proven (C10, including the fault the comparator cannot see)"),
    ("Reduce port and partial-sum transfer (K-split)", "Corrupted or lost partial-sum row", "both", f"={A}$B$22*0.01+{A}$B$26*0.02", 1, 1.00, 1,
     "Check column travels with the partial sums; drain-time ABFT at the owner; row count in the sequencer", 0.99, 0, "n/a", 0,
     "today: proven (K2 corrupted row flagged at the owner)"),
]

# the chip's elements besides the MAC cores: (element, count, λ_SR per instance, DC_RF, λ_SM, DC_L, mechanism)
CHIP_OTHER = [
    ("Shared SRAM 128 MB, soft errors", 1, "=Assumptions!B7*1024", 0.99995, 2.0, 0.99,
     "SECDED (39,32) per word: proven on the tile mesh (M5, drop 0.9); + 4-way interleaving + scrubbing + ABFT credit at the consumer; λ_SM = ECC/scrubber logic; DC_L = periodic ECC error injection"),
    ("Shared SRAM 128 MB, permanent", 1, "=Assumptions!B4*65/Assumptions!B5", 0.999, 2.0, 0.99,
     "ECC, MBIST at boot, repair"),
    ("NoC and DMA engines", 1, "=Assumptions!B4*20/Assumptions!B5", 0.99, 1.0, 0.90,
     "Packet parity/CRC, timeouts, descriptor CRC; λ_SM = checkers"),
    ("Safety island (lockstep R52+)", 1, "=Assumptions!B4*5/Assumptions!B5", 0.999, 0.5, 0.95,
     "Dual-core lockstep, ECC, watchdogs, self-test"),
    ("LPDDR5X controller and PHY", 1, "=Assumptions!B4*24/Assumptions!B5", 0.99, 1.0, 0.90,
     "Inline ECC, link ECC, loopback test"),
    ("PCIe, Ethernet, MIPI, ISP", 1, "=Assumptions!B4*27/Assumptions!B5", 0.98, 1.5, 0.90,
     "CRC, end-to-end protection, frame counters"),
    ("Clocks, resets, power management", 1, "=Assumptions!B4*9/Assumptions!B5", 0.90, 0.5, 0.90,
     "Clock/voltage/temperature monitors"),
]


# the v1 evidence table: (mechanism, fault injected, test, log, drop)
EVIDENCE_V1 = [
    ("ABFT check column", "+1 on one product in PE(0,0) for one cycle", "T2 (model and 32x32 RTL), C4", "sim/neosim_mac_core_32x32.log, sim/neosim_conv_core.log", "0.1-0.3"),
    ("ABFT at drain", "bit flip in accumulator memory; corrupted partial-sum row in flight", "C4, K2", "sim/neosim_conv_core.log, sim/neosim_ksplit.log", "0.3, 0.6"),
    ("Duplicated feeder control (R7)", "address generator corrupted for one valid cycle", "C5", "sim/neosim_conv_core.log", "0.4"),
    ("Lockstep sequencer (R10)", "drain index bit 0 flipped", "C7", "sim/neosim_conv_core.log", "0.8"),
    ("Duplicated requantization (R10)", "bit 12 of column 0 into the primary stage", "C8", "sim/neosim_conv_core.log", "0.8"),
    ("Weight-buffer ECC", "data and check bits flipped in stored codewords", "C9", "sim/neosim_conv_core_late.log", "0.12"),
    ("Requant table parity", "multiplier bit 0 flipped", "C10", "sim/neosim_conv_core_late.log", "0.12"),
    ("Link parity", "flit bit flipped on a link", "N2", "sim/neosim_noc.log", "0.4"),
    ("End-to-end CRC", "payload bit flipped past the last link check", "M3", "sim/neosim_tiles.log", "0.7"),
    ("Bank ECC", "data and check bits flipped in the source bank", "M5", "sim/neosim_tiles.log", "0.9"),
    ("Lost-flit detection", "a fetch-response flit dropped at the owner's port", "M9a", "sim/neosim_tiles.log", "0.12"),
    ("Fetch timeout", "request against a server held busy", "M9b", "sim/neosim_tiles.log", "0.12"),
    ("Reduction flow control", "contributor finishing before the owner (deadlock without RDY)", "M2", "sim/neosim_tiles.log", "0.9"),
    ("ESM, error pin, watchdog, checker self-test", "self-test dummy run; watchdog not kicked", "M8", "sim/neosim_tiles.log", "0.12"),
    ("Bank MBIST (March C-)", "stuck-at-1 bit; two stuck bits", "B1-B3, M11", "sim/neosim_mbist.log, sim/neosim_tiles.log", "0.13"),
    ("(39,32) SECDED code", "all 39 single flips x 40 words; 400 double flips", "ecc39", "sim/neosim_ecc39.log", "0.9"),
    ("INT8 layer-to-layer path", "none (functional)", "M10", "sim/neosim_two_layer.log", "0.13"),
    ("LBIST", "none: tool-inserted (Modus), hooks and FTTI schedule in the island", "-", "-", "handoff"),
]

def header(ws, row, labels, widths=None):
    for c, label in enumerate(labels, 1):
        cell = ws.cell(row=row, column=c, value=label)
        cell.font = HDR
        cell.fill = HDR_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        cell.border = BORDER
    if widths:
        for c, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(c)].width = w
    ws.row_dimensions[row].height = 42


def build(path):
    wb = Workbook()

    # ------------------------------------------------------------------ Assumptions
    ws = wb.active
    ws.title = "Assumptions"
    ws["A1"] = "AstraCore Neo FMEDA skeleton, drop 0.3 (2026-10-04). Blue cells are inputs; yellow cells are assumptions to be replaced by foundry base-failure-rate and soft-error data (ISO 26262-11, SN 29500 or IEC 62380) before the phase-1 FMEDA v1."
    ws["A1"].font = BOLD
    ws["A1"].alignment = WRAP
    ws.merge_cells("A1:D1")
    ws.row_dimensions[1].height = 48
    header(ws, 3, ["Parameter", "Value", "Unit", "Source / note"], [46, 14, 12, 70])
    rows = ASSUMPTIONS
    for i, (p, v, u, s) in enumerate(rows, start=4):
        ws.cell(row=i, column=1, value=p).font = BLACK
        c = ws.cell(row=i, column=2, value=v)
        c.font = BLUE
        c.fill = YELLOW
        ws.cell(row=i, column=3, value=u).font = BLACK
        ws.cell(row=i, column=4, value=s).font = BLACK
        ws.cell(row=i, column=4).alignment = WRAP
        for col in range(1, 5):
            ws.cell(row=i, column=col).border = BORDER
    ws["B14"].number_format = "0%"
    ws["B15"].number_format = "0%"
    ws["B17"].number_format = "0%"
    ws["B18"].number_format = "0%"
    # derived
    ws["A21"] = "Derived (formulas)"
    ws["A21"].font = BOLD
    ws["A22"] = "Permanent failure rate of one MAC core"
    ws["B22"] = "=B4*B6/B5"
    ws["C22"] = "FIT"
    ws["A23"] = "Transient failure rate, accumulator SRAM"
    ws["B23"] = "=B7*B9"
    ws["C23"] = "FIT"
    ws["A24"] = "Transient failure rate, activation buffer SRAM"
    ws["B24"] = "=B7*B10"
    ws["C24"] = "FIT"
    ws["A25"] = "Transient failure rate, weight registers"
    ws["B25"] = "=B8*B11"
    ws["C25"] = "FIT"
    ws["A26"] = "Transient failure rate, pipeline and control flops"
    ws["B26"] = "=B8*B12"
    ws["C26"] = "FIT"
    for r in range(22, 27):
        ws.cell(row=r, column=1).font = BLACK
        ws.cell(row=r, column=2).font = BLACK
        ws.cell(row=r, column=2).number_format = "0.000"
        ws.cell(row=r, column=3).font = BLACK
    # legend
    ws["A29"] = "Legend: blue = input you may edit; yellow = assumption to replace with sourced data; black = formula. Edit only blue cells."
    ws["A29"].font = Font(name=ARIAL, italic=True)
    ws.merge_cells("A29:D29")

    # ------------------------------------------------------------------ FMEDA_core
    fm = wb.create_sheet("FMEDA_core")
    fm["A1"] = "FMEDA v1 (drop 0.13), one MAC core (neo_mac_core_v02) as a safety element out of context. Safety goal assumed: no undetected wrong inference result leaves the core within the FTTI. Rows marked 'today' describe the v0.2 RTL; rows marked 'planned' name the mechanism the architecture commits to."
    fm["A1"].font = BOLD
    fm["A1"].alignment = WRAP
    fm.merge_cells("A1:O1")
    fm.row_dimensions[1].height = 48
    cols = ["Block", "Failure mode", "Fault type", "λ (FIT)", "Safety related (1/0)", "FMD (fraction)",
            "SPF-relevant: violates SG alone (1/0)", "Safety mechanism against violation", "DC_RF",
            "λ_SPF/RF (FIT)", "MPF-relevant: latent if undetected (1/0)", "Latent-fault mechanism", "DC_L",
            "λ_MPF,L (FIT)", "Status / evidence"]
    header(fm, 3, cols, [22, 34, 10, 10, 10, 10, 12, 34, 9, 12, 12, 30, 9, 12, 40])
    # (block, failure mode, type, lambda_formula, SR, FMD, SPF, SM, DC_RF, MPF, latent SM, DC_L, status)
    rows = V1_ROWS
    first = 4
    for i, r in enumerate(rows, start=first):
        block, fmode, ftype, lam, sr, fmd, spf, sm, dcrf, mpf, lsm, dcl, status = r
        vals = [block, fmode, ftype, lam, sr, fmd, spf, sm, dcrf, None, mpf, lsm, dcl, None, status]
        for c, v in enumerate(vals, 1):
            cell = fm.cell(row=i, column=c, value=v)
            cell.border = BORDER
            cell.alignment = WRAP
            cell.font = BLUE if c in (5, 6, 7, 9, 11, 13) else BLACK
        fm.cell(row=i, column=10, value=f"=D{i}*E{i}*F{i}*G{i}*(1-I{i})").font = BLACK
        fm.cell(row=i, column=14, value=f"=D{i}*E{i}*F{i}*K{i}*(1-M{i})").font = BLACK
        for c in (4, 10, 14):
            fm.cell(row=i, column=c).number_format = "0.0000"
        for c in (6, 9, 13):
            fm.cell(row=i, column=c).number_format = "0.0000"
    last = first + len(rows) - 1
    tot = last + 2
    fm.cell(row=tot, column=1, value="Totals").font = BOLD
    fm.cell(row=tot, column=4, value=f"=SUM(D{first}:D{last})").font = BOLD
    fm.cell(row=tot, column=10, value=f"=SUM(J{first}:J{last})").font = BOLD
    fm.cell(row=tot, column=14, value=f"=SUM(N{first}:N{last})").font = BOLD
    fm.cell(row=tot + 1, column=1, value="Safety-related λ_SR (Σ λ × SR × FMD)").font = BLACK
    fm.cell(row=tot + 1, column=4, value=f"=SUMPRODUCT(D{first}:D{last},E{first}:E{last},F{first}:F{last})").font = BLACK
    for c in (4, 10, 14):
        fm.cell(row=tot, column=c).number_format = "0.0000"
    fm.cell(row=tot + 1, column=4).number_format = "0.0000"
    fm.cell(row=tot + 3, column=1, value="Column guide: λ from Assumptions by area/bit share; FMD = share of the block's λ in this failure mode; DC_RF = diagnostic coverage against single-point violation; DC_L = coverage of latent faults in safety mechanisms. Blue cells are the inputs the safety team owns; coverage values here are engineering claims to be measured by fault simulation (Xcelium XFS / JasperGold FSV) in phase 2.").font = Font(name=ARIAL, italic=True)
    fm.merge_cells(start_row=tot + 3, start_column=1, end_row=tot + 3, end_column=15)
    fm.cell(row=tot + 3, column=1).alignment = WRAP
    fm.row_dimensions[tot + 3].height = 44
    fm.freeze_panes = "C4"

    # ------------------------------------------------------------------ Metrics
    mt = wb.create_sheet("Metrics")
    mt["A1"] = "Hardware architectural metrics, one MAC core (formulas; ISO 26262-5 Annex C simplified, first-order PMHF)"
    mt["A1"].font = BOLD
    mt.merge_cells("A1:E1")
    header(mt, 3, ["Metric", "Value", "ASIL-B(D) target", "ASIL-D target", "Meets ASIL-B(D)?"], [40, 14, 18, 16, 18])
    F = "FMEDA_core!"
    lam_sr = f"{F}$D${tot + 1}"
    lam_rf = f"{F}$J${tot}"
    lam_l = f"{F}$N${tot}"
    mt["A4"], mt["B4"] = "Σ λ_SR, safety-related failure rate (FIT)", f"={lam_sr}"
    mt["A5"], mt["B5"] = "Σ λ_SPF/RF, residual (FIT)", f"={lam_rf}"
    mt["A6"], mt["B6"] = "Σ λ_MPF,L, latent (FIT)", f"={lam_l}"
    mt["A7"], mt["B7"] = "SPFM = 1 − Σλ_SPF/RF / Σλ_SR", f"=IF(B4>0,1-B5/B4,0)"
    mt["C7"], mt["D7"] = "=Assumptions!B14", "=Assumptions!B17"
    mt["E7"] = '=IF(B7>=C7,"PASS","FAIL")'
    mt["A8"], mt["B8"] = "LFM = 1 − Σλ_MPF,L / (Σλ_SR − Σλ_SPF/RF)", f"=IF(B4-B5>0,1-B6/(B4-B5),0)"
    mt["C8"], mt["D8"] = "=Assumptions!B15", "=Assumptions!B18"
    mt["E8"] = '=IF(B8>=C8,"PASS","FAIL")'
    mt["A9"], mt["B9"] = "PMHF, first order (FIT) = Σλ_RF + Σλ_MPF,L × Σλ_SR × T_life × 1e-9", "=B5+B6*B4*Assumptions!B13*0.000000001"
    mt["C9"], mt["D9"] = "=Assumptions!B16", "=Assumptions!B19"
    mt["E9"] = '=IF(B9<=C9,"PASS","FAIL")'
    for r in range(4, 10):
        for c in range(1, 6):
            mt.cell(row=r, column=c).font = BLACK
            mt.cell(row=r, column=c).border = BORDER
    for r in (4, 5, 6, 9):
        mt.cell(row=r, column=2).number_format = "0.0000"
    mt["C9"].number_format = "0"
    mt["D9"].number_format = "0"
    for r in (7, 8):
        for c in (2, 3, 4):
            mt.cell(row=r, column=c).number_format = "0.00%"
    mt["A11"] = "Reading: the core's residual is dominated by the two findings in FMEDA_core (feeder control with no diagnostic today, activation-buffer corruption invisible to ABFT). The 'planned' coverages assume R7 duplication, ECC and E2E CRC are implemented; set those DC values to 0 to see the v0.2 RTL as it stands."
    mt["A11"].font = Font(name=ARIAL, italic=True)
    mt["A11"].alignment = WRAP
    mt.merge_cells("A11:E11")
    mt.row_dimensions[11].height = 44

    # ------------------------------------------------------------------ Chip_rollup
    cr = wb.create_sheet("Chip_rollup")
    cr["A1"] = "Chip-level roll-up, accelerator die. Per element: λ_SR = safety-related failure rate of the element; DC_RF = coverage of its safety mechanism; λ_SM = failure rate of the mechanism's own logic; DC_L = coverage of the mechanism's latent faults by its self-test. The dual-point term pairs each mechanism's latent faults with the faults it covers (λ_MPF,L × λ_SR × T_life). Elements other than the MAC cores carry assumed values until their own FMEDA sheets exist. Blue = inputs."
    cr["A1"].font = BOLD
    cr["A1"].alignment = WRAP
    cr.merge_cells("A1:K1")
    cr.row_dimensions[1].height = 60
    header(cr, 3, ["Element", "Count", "λ_SR per instance (FIT)", "DC_RF", "λ_SM per instance (FIT)", "DC_L",
                   "λ_SR total", "λ_SPF/RF total", "λ_MPF,L total", "PMHF contribution (FIT)", "Safety mechanism (summary)"],
           [34, 8, 16, 10, 16, 10, 14, 14, 14, 16, 50])
    T = "Assumptions!$B$13"
    elems = [
        ("MAC core (from FMEDA_core)", 64, f"={lam_sr}", None, None, None,
         "ABFT, ECC, LBIST, duplicated feeder control (planned); residual and latent taken from FMEDA_core"),
    ] + CHIP_OTHER
    for i, (name, cnt, sr, dcrf, lsm, dcl, sm) in enumerate(elems, start=4):
        cr.cell(row=i, column=1, value=name).font = BLACK
        cr.cell(row=i, column=2, value=cnt).font = BLUE
        cr.cell(row=i, column=3, value=sr).font = BLACK
        if dcrf is None:   # MAC core: residual and latent come from the FMEDA sheet
            cr.cell(row=i, column=4, value=f"=1-{lam_rf}/{lam_sr}").font = BLACK
            cr.cell(row=i, column=5, value=f"={lam_l}").font = BLACK
            cr.cell(row=i, column=6, value=0).font = BLACK
        else:
            cr.cell(row=i, column=4, value=dcrf).font = BLUE
            cr.cell(row=i, column=5, value=lsm).font = BLUE
            cr.cell(row=i, column=6, value=dcl).font = BLUE
        cr.cell(row=i, column=7, value=f"=B{i}*C{i}").font = BLACK
        cr.cell(row=i, column=8, value=f"=B{i}*C{i}*(1-D{i})").font = BLACK
        cr.cell(row=i, column=9, value=f"=B{i}*E{i}*(1-F{i})").font = BLACK
        cr.cell(row=i, column=10, value=f"=H{i}+I{i}*C{i}*{T}*0.000000001").font = BLACK
        cr.cell(row=i, column=11, value=sm).font = BLACK
        cr.cell(row=i, column=11).alignment = WRAP
        for c in range(1, 12):
            cr.cell(row=i, column=c).border = BORDER
        for c in (3, 5, 7, 8, 9, 10):
            cr.cell(row=i, column=c).number_format = "0.000"
        for c in (4, 6):
            cr.cell(row=i, column=c).number_format = "0.0000"
    e_last = 4 + len(elems) - 1
    t = e_last + 2
    cr.cell(row=t, column=1, value="Totals").font = BOLD
    for c, col in ((7, "G"), (8, "H"), (9, "I"), (10, "J")):
        cr.cell(row=t, column=c, value=f"=SUM({col}4:{col}{e_last})").font = BOLD
        cr.cell(row=t, column=c).number_format = "0.000"
    cr.cell(row=t + 2, column=1, value="Chip SPFM").font = BLACK
    cr.cell(row=t + 2, column=2, value=f"=IF(G{t}>0,1-H{t}/G{t},0)").number_format = "0.000%"
    cr.cell(row=t + 3, column=1, value="Chip LFM").font = BLACK
    cr.cell(row=t + 3, column=2, value=f"=IF(G{t}-H{t}>0,1-I{t}/(G{t}-H{t}),0)").number_format = "0.000%"
    cr.cell(row=t + 4, column=1, value="Chip PMHF, first order (FIT)").font = BLACK
    cr.cell(row=t + 4, column=2, value=f"=J{t}").number_format = "0.0"
    cr.cell(row=t + 5, column=1, value="Meets ASIL-B(D) (SPFM ≥ 90 %, LFM ≥ 60 %, PMHF ≤ 100 FIT)?").font = BLACK
    cr.cell(row=t + 5, column=2, value=f'=IF(AND(B{t + 2}>=Assumptions!B14,B{t + 3}>=Assumptions!B15,B{t + 4}<=Assumptions!B16),"PASS","FAIL")')
    cr.cell(row=t + 6, column=1, value="Meets ASIL-D (SPFM ≥ 99 %, LFM ≥ 90 %, PMHF ≤ 10 FIT)?").font = BLACK
    cr.cell(row=t + 6, column=2, value=f'=IF(AND(B{t + 2}>=Assumptions!B17,B{t + 3}>=Assumptions!B18,B{t + 4}<=Assumptions!B19),"PASS","FAIL")')
    cr.cell(row=t + 8, column=1, value="Reading: the shared SRAM's soft-error residual (DC_RF 99.995 %) and the dual-point term of its ECC logic dominate chip PMHF. Those two numbers (SRAM SER from the foundry, ECC-logic self-test coverage) decide whether the accelerator element meets ASIL-B(D) with margin; ASIL-D at chip level is not the target (decision 4). Elements other than the MAC cores are placeholders until their FMEDA sheets exist.").font = Font(name=ARIAL, italic=True)
    cr.merge_cells(start_row=t + 8, start_column=1, end_row=t + 8, end_column=11)
    cr.cell(row=t + 8, column=1).alignment = WRAP
    cr.row_dimensions[t + 8].height = 48
    for r in range(t + 2, t + 7):
        cr.cell(row=r, column=2).font = BLACK
        cr.cell(row=r, column=2).border = BORDER
        cr.cell(row=r, column=1).border = BORDER

    for sheet in wb.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if cell.font is None or cell.font.name != ARIAL:
                    f = cell.font
                    cell.font = Font(name=ARIAL, bold=f.bold, italic=f.italic, color=f.color)
    ev = wb.create_sheet("Evidence")
    ev["A1"] = "Mechanism evidence (v1): every coverage claim above points at the test that exercised the mechanism on the RTL"
    ev.append(["Mechanism", "Fault injected", "Test", "Log", "Drop"])
    for row in EVIDENCE_V1:
        ev.append(list(row))
    for col, wdt in (("A", 36), ("B", 50), ("C", 20), ("D", 52), ("E", 10)):
        ev.column_dimensions[col].width = wdt
    wb.save(path)
    return path


# =====================================================================================================================
# FMEDA v2 (drop 0.43, stream D2): every row's diagnostic coverage measured by the fault campaign
# =====================================================================================================================
# The weight buffer's storage has no v1 row (v1's "weight registers" are the PEs' registers); v2 adds it at the silicon
# size: WBUF_DEPTH 1024 entries of the 2:4 sparse-capable 32x32 core (NCHK = 4): COLS x (8 + 2) + 4 x 14 = 376 bits ->
# 12 SECDED lanes of 39 bits = 468 bits per entry, 479,232 bits = 0.457 Mbit (2^20 bits, as v1 counts a Mbit).
WBUF_MBIT = round(1024 * 12 * 39 / 2 ** 20, 4)
PERM, TRANS = ("sa0", "sa1"), ("flip",)
WREG = r"\.u_pe\.(w_q|w_sh)$|\.g_idx\.sel_q$"          # the PEs' weight registers: active, shadow, the 2:4 index
NIC_BLOCKS = ["tile_nic", "tile_nic.sram", "tile_nic.macro", "link_packer", "link_unpacker"]
SEQ_BLOCKS = ["core_seq", "neo_core", "wbuf_mem", "wbuf_mem.macro"]
ACC_CTRL = ["acc_bank", "acc_bank.macro", "neo_mac_core_v02"]
FEEDER = ["act_feeder", "act_feeder.macro"]
LINK_PROOFS = ["crc16_beat_bw4", "crc16_beat_bw16", "link_pack_stream", "link_pack_parity"]


def S(config, blocks, types=None, include=None, exclude=None):
    """a row's campaign selection: the faults of `config` ('core' = neo_core 16x8 under C1, 'tile' = the 2x2 mesh
    under M1) in `blocks`, of `types`, whose flop matches `include` and not `exclude`"""
    return dict(config=config, blocks=blocks, types=types, include=include, exclude=exclude)


# v2 rows: v1's rows in order (v1 = its index in V1_ROWS), the 'both' rows split into their permanent part (the
# stuck-at faults) and their transient part (the flips), then the rows v1 lacks. sel = the faults that measure the row;
# claim = why the row keeps v1's value; latent = faults shown for a latent-only row; proofs = formal tasks cited.
V2_ROWS = [
    dict(v1=0, sel=S("core", ["neo_mac_core"], PERM, exclude=WREG), proofs=["abft_checker"]),
    dict(v1=1, claim="dual faults: a single-fault campaign does not make them; LBIST (IP) is the mechanism"),
    dict(v1=2, sel=S("core", ["neo_mac_core"], TRANS, include=WREG), proofs=["abft_checker"]),
    dict(v1=3, sel=S("core", ["neo_mac_core"], PERM, include=WREG), proofs=["abft_checker"]),
    dict(v1=4, sel=S("core", ["neo_mac_core"], TRANS, exclude=WREG), proofs=["abft_checker"]),
    dict(v1=5, sel=S("core", ["neo_mac_core"], PERM, exclude=WREG), proofs=["abft_checker"]),
    dict(v1=6, latent=S("core", ["neo_mac_core.abft_checker", "acc_bank.abft_checker"]), proofs=["abft_checker"],
         claim="latent: the island's periodic self-test is the mechanism (not part of a C1 run); the faults show what a "
               "checker fault alone does (no wrong result expected: SPF 0)"),
    dict(v1=7, claim="safe failure mode"),
    dict(v1=8, sel=S("core", ["acc_bank.sram"], TRANS), proofs=["abft_checker"]),
    dict(v1=9, claim="multi-bit upsets are not injected (single-bit campaign); the single-bit row measures the same "
                     "mechanism (ABFT at drain)"),
    dict(v1=10, sel=S("core", ["acc_bank.sram"], PERM), proofs=["abft_checker"]),
    dict(v1=11, sel=S("core", ["act_feeder.sram"], TRANS)),
    dict(v1=12, part="transient", lam=[("B24", 0.03)], sel=S("core", ["act_feeder.sram"], TRANS),
         note="multi-bit upsets measured by the single-bit faults: the array carries no code, so what one flipped bit "
              "does unseen two do"),
    dict(v1=12, part="permanent", lam=[("B22", 0.05)], sel=S("core", ["act_feeder.sram"], PERM)),
    dict(v1=13, sel=S("core", FEEDER, PERM)),
    dict(v1=14, sel=S("core", FEEDER, TRANS)),
    dict(v1=15, part="permanent", lam=[("B22", 0.03)], sel=S("core", ACC_CTRL, PERM)),
    dict(v1=15, part="transient", lam=[("B26", 0.15)], sel=S("core", ACC_CTRL, TRANS)),
    dict(v1=16, claim="clock and reset are not flops: not injectable at RTL (the watchdog's hang detection is measured "
                      "on the tile build)"),
    dict(v1=17, part="permanent", lam=[("B22", 0.04)], sel=S("tile", NIC_BLOCKS, PERM), proofs=LINK_PROOFS),
    dict(v1=17, part="transient", lam=[("B26", 0.1)], sel=S("tile", NIC_BLOCKS, TRANS), proofs=LINK_PROOFS),
    dict(v1=18, part="permanent", lam=[("B22", 0.03)], sel=S("core", SEQ_BLOCKS, PERM), proofs=["lockstep_core_seq", "ecc39"]),
    dict(v1=18, part="transient", lam=[("B26", 0.05)], sel=S("core", SEQ_BLOCKS, TRANS), proofs=["lockstep_core_seq", "ecc39"]),
    dict(v1=19, part="permanent", lam=[("B22", 0.02)], sel=S("core", ["requant"], PERM), proofs=["lockstep_requant", "requant_parity"]),
    dict(v1=19, part="transient", lam=[("B26", 0.03)], sel=S("core", ["requant"], TRANS), proofs=["lockstep_requant", "requant_parity"]),
    dict(v1=20, claim="the reduce port is idle in C1 and M1 (no K-split): an M2-class workload is the follow-up"),
    dict(block="Weight buffer SRAM (v2 addition)", fmode="Single-bit upset", ftype="transient", lam=[("B33", 0.97)],
         sm="SECDED (39,32) per lane, corrected on the sequencer's read (wbuf_ce; wbuf_ue for two flips)",
         sel=S("core", ["wbuf_mem.sram"], TRANS), proofs=["ecc39"], exhaustive="ecc39"),
    dict(block="Weight buffer SRAM (v2 addition)", fmode="Multi-bit upset in one lane", ftype="transient", lam=[("B33", 0.03)],
         sm="SECDED detects two flips (wbuf_ue); ABFT for a wrong weight", dc=0.999, proofs=["ecc39"],
         claim="multi-bit upsets are not injected; v1's value for SECDED-detected multi-bit upsets (the accumulator's row) kept"),
]

# chip elements v2 measures on the tile build (the others keep v1's values): element name -> (selection, use)
CHIP_SEL = {
    "Shared SRAM 128 MB, soft errors": (S("tile", ["sram_bank.sram"], TRANS), "evidence"),
    "Shared SRAM 128 MB, permanent": (S("tile", ["sram_bank.sram", "sram_bank.macro", "sram_bank", "mbist"], PERM), "measured"),
    "NoC and DMA engines": (S("tile", ["noc_router", "tile_dma", "prog_mem", "host_if", "neo_tile", "esm"], PERM), "measured"),
}
CHIP_PROOFS = {"Shared SRAM 128 MB, soft errors": ["ecc39"], "NoC and DMA engines": ["noc_router"]}
CORES = 64
# v3 (drop 0.44): the tile's NIC memories -- the drain FIFO's rows (behind stored parity: the beat parities and the index
# parity, checked by the link packer) and the partial-sum rows under assembly (the check beat's ABFT at the owner) -- as a
# chip element of 64 tiles, measured on the tile build's tile_nic.sram flips; v1 priced no NoC memory (the FIFO was flops)
CHIP_V3 = [("Tile NIC memories (drain FIFO 512 x 1,069, partial-sum rows 64 x 1,024), soft errors", CORES, "=Assumptions!B35", 0.99, 0.0, 0.0,
            "the drain row's beat and index parities stored at the push and checked by the link packer (drop 0.44, S1); a corrupted partial-sum row is seen by the owner's ABFT at drain")]
CHIP_SEL_V3 = {"Tile NIC memories (drain FIFO 512 x 1,069, partial-sum rows 64 x 1,024), soft errors": (S("tile", ["tile_nic.sram"], TRANS), "measured")}
# item 2 (drop 0.43): the comparator storage of drop 0.41 -- (the flop path's ending, the label, the FMEDA row carrying it)
_FEED, _ACC, _SEQ = "Feeder address generator and run FSM", "Accumulator write/read control", "Sequencer, weight-buffer addressing"
ITEM2_REGS = [
    ("u_feeder.u_mem.rb_addr_q", "activation buffer port-B address latch", _FEED),
    ("u_feeder.inb_q", "feeder inb_q", _FEED), ("u_feeder.a0_q", "feeder a0_q", _FEED),
    ("u_feeder.first_q", "feeder first_q", _FEED), ("u_feeder.first_oq", "feeder first_oq", _FEED),
    ("u_feeder.addr_dq", "feeder addr_dq (copy)", _FEED), ("u_feeder.inb_dq", "feeder inb_dq (copy)", _FEED),
    ("u_feeder.a0_dq", "feeder a0_dq (copy)", _FEED), ("u_feeder.first_dq", "feeder first_dq (copy)", _FEED),
    ("u_feeder.first_doq", "feeder first_doq (copy)", _FEED),
    ("u_wbuf.u_mem.rb_addr_q", "weight buffer port-B address latch", _SEQ), ("u_wbuf.ra0_q", "weight buffer ra0_q", _SEQ),
    ("w_load_q", "core w_load_q", _SEQ), ("d_w_load_q", "core d_w_load_q (copy)", _SEQ),
    ("d_w_raddr_q", "core d_w_raddr_q (copy)", _SEQ),
    ("u_acc.u_mem.rb_addr_q", "accumulator port-B address latch", _ACC), ("u_acc.v1", "acc v1", _ACC),
    ("u_acc.first1", "acc first1", _ACC), ("u_acc.idx1", "acc idx1", _ACC), ("u_acc.fwd1", "acc fwd1", _ACC),
    ("u_acc.v1_n", "acc v1_n (copy)", _ACC), ("u_acc.first1_n", "acc first1_n (copy)", _ACC),
    ("u_acc.idx1_n", "acc idx1_n (copy)", _ACC), ("u_acc.fwd1_n", "acc fwd1_n (copy)", _ACC), ("u_acc.rbq_n", "acc rbq_n (copy)", _ACC),
]


# ---- FMEDA v3 (drop 0.44, stream S1) ----
VER, DROP, STREAM = "v2", "0.43", "D2"           # set to v3 / 0.44 / S1 by --v3 (the v2 build stays reproducible)
# the SRAM base rates at the silicon leaves (09_synthesis/memories.md; 2^20 bits per Mbit as v1 counts): the activation
# buffer 1,024 words x 624 bits (the pair of SECDED-coded entries, drop 0.44; v1 counted 0.25 Mbit, its note's 4096 x 32 x 8
# bit is 1 Mbit, the 512-bit word of drop 0.43 is 0.5), the accumulator 512 x 1,056 (COLS + 1 partial sums; v1 0.5)
V3_BASE = {"B9": round(512 * 1056 / 2 ** 20, 4), "B10": round(1024 * 624 / 2 ** 20, 4)}
V3_BASE_NOTE = {"B9": "v3: 512 rows x 1,056 bits (33 partial sums of 32 bits: memories.md acc_bank.u_mem) = 540,672 bits",
                "B10": "v3: 1,024 pair words x 624 bits (two SECDED-coded entries of 8 lanes x 39, drop 0.44: memories.md act_feeder.u_mem) = 638,976 bits"}
# the tile's NIC memories, which neither v1 nor v2 priced (the drain FIFO was flops until drop 0.40, a leaf since): the drain
# FIFO 512 x 1,069 bits and the partial-sum rows 64 x 1,024 bits per tile (memories.md)
NIC_MEM_MBIT = round((512 * 1069 + 64 * 1024) / 2 ** 20, 4)
# the sensitivity on the pipeline and control flops (v1: 0.04 Mbit): the synthesized silicon core (neo_core at 32x32, NCHK = 4,
# synth.py on the ASAP7 flow after drop 0.44) holds V3_FLOPS flops, of which the PEs' weight registers (B11: 1,024 data PEs x
# 22 bits {w_q, w_sh with its index, sel_q} + 128 check PEs x 28) are 26,112 -- the rest is what B12 stands for
V3_FLOPS = 233892                                # synth.py --blocks neo_core on the drop 0.44 RTL (ASAP7, the macros black-boxed)
# the v2 result (fmeda_v2.md, run 192) the v3 metrics are compared with
V2_RESULT = dict(core_spfm=0.95946, core_lfm=0.999999, core_pmhf=29.642, chip_spfm=0.99708, chip_lfm=0.9999993, chip_pmhf=1931.0)
V2_UNDETECTED = 201
# the mechanisms of drop 0.44 (S1): (label, the campaign selection -- a block, or a regex on the flop path)
S1_REGS = [
    ("activation buffer cells (SECDED, abuf_ce / abuf_ue)", dict(block="act_feeder.sram")),
    ("activation buffer output register (the macro's rb_data, decoded behind it)", dict(re=r"u_feeder\.u_mem\.rb_data$")),
    ("feeder row register x_vec_q (byte parity, act_perr)", dict(re=r"u_feeder\.x_vec_q$")),
    ("feeder parity register x_par_q", dict(re=r"u_feeder\.x_par_q$")),
    ("feeder second-stage control and its copies (valid / swap / idx / first _q2, _dq2: ctrl_err)", dict(re=r"u_feeder\.(valid|swap|idx|first)_d?q2$")),
    ("skew chains, data (byte parity, act_perr)", dict(re=r"\.u_skew\..*(st1|r_even|r_odd|u_da\.pipe|u_db\.pipe)(\[\d+\])?$")),
    ("skew chains, parity bits", dict(re=r"\.u_skew\..*(st1_p|r_even_p|r_odd_p|u_pa\.pipe|u_pb\.pipe)(\[\d+\])?$")),
    ("index / first delay lines (parity, ctrl_err)", dict(re=r"\.(u_idx_dl|u_first_dl)\.pipe")),
    ("index-line parity line", dict(re=r"\.u_ipar_dl\.pipe")),
    ("drain FIFO output register (stored parity, the packer's check)", dict(re=r"u_dfifo\.rb_data$")),
    ("drain FIFO cells", dict(re=r"u_dfifo\.mem")),
    ("serve register serve_row (stored word parities, the packer's check)", dict(re=r"u_nic\.serve_row$")),
    ("packer's beat and parity registers (ib, ipar, perr_sticky)", dict(re=r"u_pack\.(g_merge\.|g_split\.)?(ib|ipar|perr_sticky)$")),
    ("MBIST state (one-hot, bist_fail)", dict(re=r"u_bist\.state$")),
]


def v2_campaign_table():
    """v2's per-block campaign (neo_fmeda_v2.xlsx's Campaign sheet beside this file): {(build, block): (n, det, masked, dang)}"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "neo_fmeda_v2.xlsx")
    if not os.path.exists(path):
        return {}
    from openpyxl import load_workbook
    ws = load_workbook(path, read_only=True)["Campaign"]
    out = {}
    for row in ws.iter_rows(min_row=4, values_only=True):
        if row and row[0] in ("core", "tile") and row[1]:
            out[(row[0], row[1])] = (int(row[2] or 0), int(row[3] or 0) + int(row[6] or 0), int(row[4] or 0), int(row[5] or 0) + int(row[7] or 0))
    return out


def base_values(over=None):
    """v1's Assumptions sheet as numbers (B4..B19 inputs, B22..B26 derived) and v2's weight buffer (B32, B33); v3: the SRAM
    leaves at the silicon sizes (V3_BASE); `over` replaces inputs (the flop-count sensitivity)"""
    v = {f"B{i}": row[1] for i, row in enumerate(ASSUMPTIONS, start=4)}
    if VER == "v3":
        v.update(V3_BASE)
    if over:
        v.update(over)
    v["B22"] = v["B4"] * v["B6"] / v["B5"]
    v["B23"] = v["B7"] * v["B9"]
    v["B24"] = v["B7"] * v["B10"]
    v["B25"] = v["B8"] * v["B11"]
    v["B26"] = v["B8"] * v["B12"]
    v["B32"] = WBUF_MBIT
    v["B33"] = v["B7"] * v["B32"]
    v["B34"] = NIC_MEM_MBIT
    v["B35"] = v["B7"] * v["B34"]
    return v


def v1_lam_terms(formula):
    """v1's λ formula ('=Assumptions!$B$22*0.03+Assumptions!$B$26*0.15') as [(cell, factor)]"""
    terms = []
    for t in formula.lstrip("=").split("+"):
        cell, _, k = t.replace(A, "").replace("$", "").partition("*")
        terms.append((cell, float(k) if k else 1.0))
    return terms


def wilson(p, n, z=1.96):
    if not n:
        return (0.0, 1.0)
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(max(0.0, p * (1 - p) / n + z * z / (4 * n * n)))
    return (max(0.0, (c - h) / d), min(1.0, (c + h) / d))


def select(rows, sel):
    out = []
    for r in rows:
        if r["outcome"] == "unresolved" or r["config"] != sel["config"] or r["block"] not in sel["blocks"]:
            continue
        if sel.get("types") and r["type"] not in sel["types"]:
            continue
        if sel.get("include") and not re.search(sel["include"], r["flop"]):
            continue
        if sel.get("exclude") and re.search(sel["exclude"], r["flop"]):
            continue
        out.append(r)
    return out


def stats(rs):
    """bit-weighted outcome fractions of a row's faults (each fault weighted by its block's bits / its block's sample,
    the campaign's `weight`): F_safe = masked, DC = detected / (detected + dangerous), the dangerous fraction; Wilson
    intervals at Kish's effective sample size. A hang without a cause counts as dangerous; a hang the watchdog or the
    fetch timeout caught as detected."""
    if not rs:
        return None
    w = [float(r.get("weight") or 1.0) for r in rs]
    det = [x for x, r in zip(w, rs) if r["outcome"] in ("detected", "hang_detected")]
    dang = [x for x, r in zip(w, rs) if r["outcome"] in ("dangerous", "hang")]
    sw, sd, sg = sum(w), sum(det), sum(dang)
    neff = sw * sw / sum(x * x for x in w)
    ns = det + dang
    neff_ns = (sum(ns) ** 2 / sum(x * x for x in ns)) if ns else 0
    dc = sd / (sd + sg) if ns else None
    fd = sg / sw
    return dict(n=len(rs), det=sum(1 for r in rs if r["outcome"] in ("detected", "hang_detected")),
                dang=sum(1 for r in rs if r["outcome"] in ("dangerous", "hang")),
                masked=sum(1 for r in rs if r["outcome"] == "masked"),
                hang=sum(1 for r in rs if r["outcome"] in ("hang", "hang_detected")),
                fsafe=1 - (sd + sg) / sw, dc=dc, dc_ci=wilson(dc, neff_ns) if dc is not None else None, neff_ns=neff_ns,
                fdang=fd, fdang_hi=wilson(fd, neff)[1], neff=neff)


def sel_text(sel):
    t = f"{sel['config']}: " + ", ".join(sel["blocks"])
    if sel.get("types"):
        t += " / " + ("stuck-at" if tuple(sel["types"]) == PERM else "flips" if tuple(sel["types"]) == TRANS else ",".join(sel["types"]))
    if sel.get("include"):
        t += " / weight registers only"
    if sel.get("exclude"):
        t += " / weight registers excluded"
    return t


def parse_formal(path):
    """{task: (result, mode, time, what)} from the formal job's formal_report.md, and its title line"""
    out, title = {}, None
    if not path or not os.path.exists(path):
        return out, title
    for line in open(path):
        if line.startswith("# "):
            title = line[2:].strip()
        m = re.match(r"\| `([^`]+)` \| ([^|]*) \| (\w+) \| [^|]* \| [^|]* \| \**(\w+)\** \| ([^|]*) \| ([^|]*) \|", line)
        if m:
            out[m.group(1)] = (m.group(4), m.group(3), m.group(6).strip(), m.group(2).strip())
    return out, title


def proofs_text(proofs, formal):
    if not proofs:
        return ""
    return "; ".join(f"{p} {formal[p][0]} ({formal[p][1]}, {formal[p][2]})" if p in formal else f"{p} (not in the report)"
                     for p in proofs)


def v2_rows(faults, formal, over=None):
    """the v2 / v3 rows with their numbers: λ (terms and value), the v1 fields, the campaign's statistics, DC used"""
    bv = base_values(over)
    out = []
    for spec in V2_ROWS:
        r = dict(spec)
        if "v1" in spec:
            block, fmode, ftype, lam, sr, fmd, spf, sm, dcrf, mpf, lsm, dcl, status = V1_ROWS[spec["v1"]]
            terms = spec.get("lam") or v1_lam_terms(lam)
            r.update(block=block, fmode=fmode + (f" ({spec['part']} part)" if spec.get("part") else ""),
                     ftype=spec.get("part") or ftype, sr=sr, fmd=fmd, spf=spf, sm=sm, mpf=mpf, lsm=lsm, dcl=dcl,
                     v1dc=dcrf, v1status=status)
        else:
            terms = spec["lam"]
            r.update(sr=1, fmd=1.0, spf=1, mpf=0, lsm="n/a", dcl=0, v1dc=None, v1status="no v1 row")
        r["terms"] = terms
        r["lam_formula"] = "=" + "+".join(f"{A}$B${c[1:]}*{k:g}" for c, k in terms)
        r["lam_value"] = sum(bv[c] * k for c, k in terms)
        st = stats(select(faults, spec["sel"])) if spec.get("sel") else None
        r["st"] = st
        r["lat"] = stats(select(faults, spec["latent"])) if spec.get("latent") else None
        claim_dc = spec.get("dc", r["v1dc"])
        if st and st["dc"] is not None:
            r.update(fsafe=st["fsafe"], dc=st["dc"], basis="measured")
        elif st:                                     # every fault masked: no residual measured, DC unobserved
            r.update(fsafe=st["fsafe"], dc=claim_dc if claim_dc is not None else 0.0, basis="measured (all masked)")
        else:
            r.update(fsafe=0.0, dc=claim_dc if claim_dc is not None else 0.0,
                     basis="claim" if spec.get("sel") is None else "claim (no faults drawn)")
        r["proofs_text"] = proofs_text(spec.get("proofs"), formal)
        r["exhaustive_pass"] = bool(spec.get("exhaustive")) and formal.get(spec["exhaustive"], ("",))[0] == "PASS"
        out.append(r)
    return out


def residual(r, mode):
    """λ_SPF/RF of a row: 'v2' with the measured safe fraction and DC; 'nomask' without the safe fraction; 'upper' with
    the measured rows at the 95 % upper bound of their dangerous fraction; 'v1' v1's DC (a v1 row's λ is v2's)"""
    lam = r["lam_value"] * r["sr"] * r["fmd"] * r["spf"]
    if mode == "v1":
        return lam * (1 - (r["v1dc"] if r["v1dc"] is not None else 0.0))
    if mode == "upper" and r["st"] and r["basis"].startswith("measured"):
        if r.get("exhaustive") and r["st"]["dang"] == 0 and r["exhaustive_pass"]:
            return 0.0                               # the failure mode is covered for every case by a passing proof
        return lam * r["st"]["fdang_hi"]
    if mode == "nomask":
        dc = r["dc"] if (r["st"] and r["st"]["dc"] is not None) else (r["v1dc"] if r["v1dc"] is not None else r["dc"])
        return lam * (1 - dc)
    return lam * (1 - r["fsafe"]) * (1 - r["dc"])


def core_metrics(rows, mode, with_new=True):
    T = ASSUMPTIONS[9][1]
    sel = [r for r in rows if with_new or "v1" in r]
    if mode == "v1":                                 # v1's rows as v1 has them (a split row counted once, whole)
        sel = [r for r in rows if "v1" in r]
    sr = sum(r["lam_value"] * r["sr"] * r["fmd"] for r in sel)
    rf = sum(residual(r, mode) for r in sel)
    lat = sum(r["lam_value"] * r["sr"] * r["fmd"] * r["mpf"] * (1 - r["dcl"]) for r in sel)
    return dict(sr=sr, rf=rf, lat=lat, spfm=1 - rf / sr, lfm=1 - lat / (sr - rf), pmhf=rf + lat * sr * T * 1e-9)


def chip_elements(faults, formal, core, mode):
    """[(name, count, λ_SR, F_safe, DC_RF, λ_SM, DC_L, mechanism, stats, basis)] -- v1's elements, the MAC core from
    `core` (core_metrics), the elements CHIP_SEL measures with their measured values in the v2 modes"""
    bv = base_values()
    out = [(f"MAC core (from FMEDA_core_{VER})", CORES, core["sr"], 0.0, 1 - core["rf"] / core["sr"], core["lat"], 0.0,
            "residual and latent from the core sheet", None, "core sheet")]
    for name, cnt, lam, dcrf, lsm, dcl, sm in CHIP_OTHER + (CHIP_V3 if VER == "v3" else []):
        expr = lam.lstrip("=").replace("Assumptions!", "")
        lam_v = eval(re.sub(r"B(\d+)", lambda m: repr(bv["B" + m.group(1)]), expr))   # noqa: S307 (our own constant formulas)
        st, basis, fs, dc = None, "v1", 0.0, dcrf
        csel = dict(CHIP_SEL, **CHIP_SEL_V3) if VER == "v3" else CHIP_SEL
        if name in csel:
            sel, use = csel[name]
            st = stats(select(faults, sel))
            if st and use == "measured" and mode != "v1":
                if st["dc"] is not None:
                    fs, dc, basis = st["fsafe"], st["dc"], "measured"
                    if mode == "upper":
                        fs, dc, basis = 0.0, 1 - st["fdang_hi"], "measured, 95 % upper"
                    elif mode == "nomask":
                        fs = 0.0
                else:
                    fs, basis = st["fsafe"], "measured (all masked)"
                    if mode == "upper":
                        fs, dc, basis = 0.0, 1 - st["fdang_hi"], "measured, 95 % upper"
                    elif mode == "nomask":
                        fs = 0.0
            elif st:
                basis = "v1 (campaign: evidence)"
        out.append((name, cnt, lam_v, fs, dc, lsm, dcl, sm, st, basis))
    return out


def chip_metrics(elems):
    T = ASSUMPTIONS[9][1]
    G = sum(c * l for _, c, l, *_ in elems)
    H = sum(c * l * (1 - fs) * (1 - dc) for _, c, l, fs, dc, *_ in elems)
    I_ = sum(c * lsm * (1 - dcl) for _, c, _, _, _, lsm, dcl, *_ in elems)
    J = sum(c * l * (1 - fs) * (1 - dc) + c * lsm * (1 - dcl) * l * T * 1e-9 for _, c, l, fs, dc, lsm, dcl, *_ in elems)
    return dict(sr=G, rf=H, lat=I_, spfm=1 - H / G, lfm=1 - I_ / (G - H), pmhf=J)


def read_faults(path):
    """faults.csv rows and the campaign's meta (faults_summary.json beside it, when there)"""
    d = path if os.path.isdir(path) else os.path.dirname(path)
    csvp = os.path.join(path, "faults.csv") if os.path.isdir(path) else path
    with open(csvp) as f:
        rows = list(csv.DictReader(f))
    meta = {}
    sp = os.path.join(d, "faults_summary.json")
    if os.path.exists(sp):
        with open(sp) as f:
            meta = json.load(f).get("meta", {})
    run = ""
    rp = os.path.join(d, "RUN.txt")
    if os.path.exists(rp):
        run = open(rp).read().strip().splitlines()[0]
    return rows, meta, run


def block_table(faults):
    """per (config, block): n, outcomes, DC with its interval, latencies"""
    out = {}
    for r in faults:
        if r["outcome"] == "unresolved":
            continue
        k = (r["config"], r["block"])
        s = out.setdefault(k, dict(n=0, det=0, masked=0, dang=0, hangd=0, hang=0, lat=[]))
        s["n"] += 1
        o = r["outcome"]
        s["det"] += o == "detected"
        s["masked"] += o == "masked"
        s["dang"] += o == "dangerous"
        s["hangd"] += o == "hang_detected"
        s["hang"] += o == "hang"
        if o in ("detected", "hang_detected") and r["latency"] not in ("", None):
            s["lat"].append(int(r["latency"]))
    for s in out.values():
        d, g = s["det"] + s["hangd"], s["dang"] + s["hang"]
        s["dc"] = d / (d + g) if d + g else None
        s["dc_ci"] = wilson(s["dc"], d + g) if d + g else None
    return out


def pc(x, nd=1):
    return "n/a" if x is None else f"{100 * x:.{nd}f} %"


def build_v2(faults_path, formal_path, out_dir):
    """the v2 (drop 0.43) or, with VER = "v3", the v3 (drop 0.44) workbook and report"""
    faults, meta, run = read_faults(faults_path)
    formal, formal_title = parse_formal(formal_path)
    rows = v2_rows(faults, formal)
    bv = base_values()
    modes = [("v1", "v1 (claims)"), ("v2", f"{VER} measured"), ("nomask", f"{VER}, no credit for masked faults"),
             ("upper", f"{VER}, 95 % upper bounds")]
    core = {m: core_metrics(rows, m) for m, _ in modes}
    core_v1rows = core_metrics(rows, "v2", with_new=False)
    chips = {m: chip_metrics(chip_elements(faults, formal, core[m], m)) for m, _ in modes}
    chip_v1rows = chip_metrics(chip_elements(faults, formal, core_v1rows, "v2"))
    if VER == "v3":                                   # the flop-count sensitivity: the rows at the synthesized flop count
        rows_f = v2_rows(faults, formal, over={"B11": round(26112 / 2 ** 20, 4), "B12": round((V3_FLOPS - 26112) / 2 ** 20, 4)})
        core["flops"] = core_metrics(rows_f, "v2")
        chips["flops"] = chip_metrics(chip_elements(faults, formal, core["flops"], "v2"))
        modes.append(("flops", f"{VER}, synthesized flop count"))
    xlsx = os.path.join(out_dir, f"neo_fmeda_{VER}.xlsx")
    md = os.path.join(out_dir, f"fmeda_{VER}.md")
    write_v2_xlsx(xlsx, rows, faults, meta, run, formal, formal_title, core, chips, modes, bv)
    write_v2_md(md, rows, faults, meta, run, formal, formal_title, core, core_v1rows, chips, chip_v1rows, modes, bv)
    return xlsx, md, core, chips


def write_v2_xlsx(path, rows, faults, meta, run, formal, formal_title, core, chips, modes, bv):
    wb = Workbook()
    # ---------------------------------------------------------------- Assumptions (v1's, plus the weight buffer)
    ws = wb.active
    ws.title = "Assumptions"
    ws["A1"] = (f"AstraCore Neo FMEDA {VER} (drop {DROP}): v1's base failure rates " +
                ("unchanged (rows 4-19, the derived rates 22-26); " if VER == "v2" else
                 "with the SRAM leaves at the silicon sizes of 09_synthesis/memories.md (rows 9 and 10, marked v3; the derived rates 22-26); ") +
                "row 32 adds the weight buffer's storage, which v1 had no row for" +
                ("" if VER == "v2" else "; row 34 the tile's NIC memories (drain FIFO, partial-sum rows), which no version priced") +
                ". Blue = inputs; yellow = assumptions to replace with foundry data (ISO 26262-11).")
    ws["A1"].font = BOLD
    ws["A1"].alignment = WRAP
    ws.merge_cells("A1:D1")
    ws.row_dimensions[1].height = 48
    header(ws, 3, ["Parameter", "Value", "Unit", "Source / note"], [46, 14, 12, 70])
    for i, (p, v, u, s) in enumerate(ASSUMPTIONS, start=4):
        if VER == "v3" and f"B{i}" in V3_BASE:
            v, s = V3_BASE[f"B{i}"], f"{V3_BASE_NOTE[f'B{i}']} (v1: {v} Mbit -- {s})"
        ws.cell(row=i, column=1, value=p).font = BLACK
        c = ws.cell(row=i, column=2, value=v)
        c.font = BLUE
        c.fill = YELLOW
        ws.cell(row=i, column=3, value=u).font = BLACK
        ws.cell(row=i, column=4, value=s).font = BLACK
        ws.cell(row=i, column=4).alignment = WRAP
        for col in range(1, 5):
            ws.cell(row=i, column=col).border = BORDER
    for a in ("B14", "B15", "B17", "B18"):
        ws[a].number_format = "0%"
    ws["A21"] = "Derived (formulas)"
    ws["A21"].font = BOLD
    for r, (label, f) in enumerate([("Permanent failure rate of one MAC core", "=B4*B6/B5"),
                                    ("Transient failure rate, accumulator SRAM", "=B7*B9"),
                                    ("Transient failure rate, activation buffer SRAM", "=B7*B10"),
                                    ("Transient failure rate, weight registers", "=B8*B11"),
                                    ("Transient failure rate, pipeline and control flops", "=B8*B12")], start=22):
        ws.cell(row=r, column=1, value=label).font = BLACK
        ws.cell(row=r, column=2, value=f).font = BLACK
        ws.cell(row=r, column=2).number_format = "0.000"
        ws.cell(row=r, column=3, value="FIT").font = BLACK
    ws["A31"] = "v2 addition"
    ws["A31"].font = BOLD
    ws["A32"] = "Weight buffer SRAM per core (v2 addition)"
    ws["B32"] = WBUF_MBIT
    ws["B32"].font = BLUE
    ws["B32"].fill = YELLOW
    ws["C32"] = "Mbit"
    ws["D32"] = ("WBUF_DEPTH 1024 entries x 468 bits (the 2:4 sparse-capable 32x32 core: 376 bits in 12 SECDED lanes of "
                 "39 bits) = 479,232 bits; v1 has no row for it")
    ws["D32"].alignment = WRAP
    ws["A33"] = "Transient failure rate, weight buffer SRAM"
    ws["B33"] = "=B7*B32"
    ws["B33"].number_format = "0.000"
    ws["C33"] = "FIT"
    if VER == "v2":
        ws["A35"] = ("Observations, not changed here so that v2 compares with v1 like for like: the silicon activation buffer is "
                     "ABUF_DEPTH 2048 x 32 bytes = 0.5 Mbit (v1: 0.25 Mbit, and its note's 4096 x 32 x 8 bit is 1 Mbit); the "
                     "accumulator of the NCHK = 4 core is 512 x 36 x 32 bit = 0.5625 Mbit (v1: 0.5).")
        ws["A35"].alignment = WRAP
        ws.merge_cells("A35:D35")
        ws.row_dimensions[35].height = 44
    else:
        ws["A34"] = "Tile NIC memories per tile (v3 addition)"
        ws["B34"] = NIC_MEM_MBIT
        ws["B34"].font = BLUE
        ws["B34"].fill = YELLOW
        ws["C34"] = "Mbit"
        ws["D34"] = "the drain FIFO 512 x 1,069 bits and the partial-sum rows 64 x 1,024 bits (memories.md): 612,864 bits; neither v1 nor v2 priced them (the FIFO was flops until drop 0.40)"
        ws["D34"].alignment = WRAP
        ws["A35"] = "Transient failure rate, tile NIC memories"
        ws["B35"] = "=B7*B34"
        ws["B35"].number_format = "0.000"
        ws["C35"] = "FIT"
        ws["A37"] = (f"Sensitivity, not applied to the rows: the synthesized silicon core (neo_core 32x32 NCHK = 4, drop 0.44) holds {V3_FLOPS:,} flops, "
                     f"of which the PEs' weight registers are 26,112 (B11 0.0249 Mbit) and the rest {V3_FLOPS - 26112:,} = {(V3_FLOPS - 26112) / 2 ** 20:.4f} Mbit is what "
                     f"B12 (v1: 0.04 Mbit) stands for; the Metrics sheet's last column recomputes the core and the chip with it.")
        ws["A37"].alignment = WRAP
        ws.merge_cells("A37:D37")
        ws.row_dimensions[37].height = 44

    # ---------------------------------------------------------------- FMEDA_core_v2
    fm = wb.create_sheet(f"FMEDA_core_{VER}")
    fm["A1"] = (f"FMEDA {VER} (drop {DROP}), one MAC core: v1's rows with the diagnostic coverage the fault campaign measured "
                "(02_dv/dv/cocotb/faults/, the faults job). Per row: the faults that measure it (campaign selection), "
                "their outcomes, F_safe = the bit-weighted fraction masked in the workload (no violation, ISO 26262's safe "
                "faults), DC_RF = detected / (detected + dangerous), bit-weighted, with its 95 % Wilson interval. "
                "λ_SPF/RF = λ x SR x FMD x SPF x (1 - F_safe) x (1 - DC_RF). Rows marked 'claim' keep v1's value and say why. "
                + (f"Campaign: {run}." if run else ""))
    fm["A1"].font = BOLD
    fm["A1"].alignment = WRAP
    fm.merge_cells("A1:Z1")
    fm.row_dimensions[1].height = 62
    cols = ["Block", "Failure mode", "Fault type", "λ (FIT)", "SR", "FMD", "SPF", "Safety mechanism", "Campaign selection",
            "Faults", "Detected", "Masked", "Dangerous (incl. hang)", "F_safe (measured)", "DC_RF measured",
            "DC_RF 95 % CI (n_eff)", "DC_RF used", "λ_SPF/RF (FIT)", "MPF", "Latent-fault mechanism", "DC_L",
            "λ_MPF,L (FIT)", "Formal proofs (formal job)", "v1 DC_RF", "v1 λ_SPF/RF (FIT)", "Basis / evidence"]
    header(fm, 3, cols, [24, 30, 10, 10, 5, 7, 5, 30, 30, 7, 8, 8, 9, 9, 9, 16, 9, 11, 5, 26, 7, 10, 34, 8, 10, 46])
    first = 4
    for i, r in enumerate(rows, start=first):
        st = r["st"] or r.get("lat")
        ci = f"{pc(st['dc_ci'][0])} .. {pc(st['dc_ci'][1])} ({st['neff_ns']:.0f})" if (r["st"] and r["st"]["dc_ci"]) else ""
        sel = r.get("sel") or r.get("latent")
        ev = r["basis"] + ("; " + r["claim"] if r.get("claim") else "") + ("; " + r["note"] if r.get("note") else "")
        if r.get("lat"):
            lt = r["lat"]
            ev += f"; checker faults: {lt['n']} run, {lt['det']} detected (safe side), {lt['masked']} masked, {lt['dang']} dangerous"
        vals = [r["block"], r["fmode"], r["ftype"], r["lam_formula"], r["sr"], r["fmd"], r["spf"], r.get("sm"),
                sel_text(sel) if sel else "-", st["n"] if st else None, st["det"] if st else None,
                st["masked"] if st else None, st["dang"] if st else None, r["fsafe"],
                (r["st"]["dc"] if r["st"] and r["st"]["dc"] is not None else None), ci, r["dc"], None, r["mpf"],
                r["lsm"], r["dcl"], None, r["proofs_text"], r["v1dc"], residual(r, "v1") if r["v1dc"] is not None else None, ev]
        for c, v in enumerate(vals, 1):
            cell = fm.cell(row=i, column=c, value=v)
            cell.border = BORDER
            cell.alignment = WRAP
            cell.font = BLUE if c in (5, 6, 7, 14, 17, 19, 21) else BLACK
        fm.cell(row=i, column=18, value=f"=D{i}*E{i}*F{i}*G{i}*(1-N{i})*(1-Q{i})").font = BLACK
        fm.cell(row=i, column=22, value=f"=D{i}*E{i}*F{i}*S{i}*(1-U{i})").font = BLACK
        for c in (4, 18, 22, 25):
            fm.cell(row=i, column=c).number_format = "0.0000"
        for c in (6, 14, 15, 17, 21, 24):
            fm.cell(row=i, column=c).number_format = "0.0000"
    last = first + len(rows) - 1
    tot = last + 2
    fm.cell(row=tot, column=1, value="Totals").font = BOLD
    for c, col in ((4, "D"), (18, "R"), (22, "V")):
        fm.cell(row=tot, column=c, value=f"=SUM({col}{first}:{col}{last})").font = BOLD
        fm.cell(row=tot, column=c).number_format = "0.0000"
    fm.cell(row=tot + 1, column=1, value="Safety-related λ_SR (Σ λ × SR × FMD)").font = BLACK
    fm.cell(row=tot + 1, column=4, value=f"=SUMPRODUCT(D{first}:D{last},E{first}:E{last},F{first}:F{last})").number_format = "0.0000"
    fm.freeze_panes = "C4"

    # ---------------------------------------------------------------- Metrics_v2
    mt = wb.create_sheet(f"Metrics_{VER}")
    mt["A1"] = f"Hardware architectural metrics, one MAC core, {VER} (formulas over FMEDA_core_{VER}) and the comparison with v1" + ("" if VER == "v2" else " and v2")
    mt["A1"].font = BOLD
    mt.merge_cells("A1:F1")
    header(mt, 3, ["Metric", "Value", "ASIL-B(D) target", "ASIL-D target", "Meets ASIL-B(D)?", "Meets ASIL-D?"], [44, 14, 16, 14, 16, 14])
    F_ = f"FMEDA_core_{VER}!"
    mt["A4"], mt["B4"] = "Σ λ_SR (FIT)", f"={F_}$D${tot + 1}"
    mt["A5"], mt["B5"] = "Σ λ_SPF/RF (FIT)", f"={F_}$R${tot}"
    mt["A6"], mt["B6"] = "Σ λ_MPF,L (FIT)", f"={F_}$V${tot}"
    mt["A7"], mt["B7"] = "SPFM", "=IF(B4>0,1-B5/B4,0)"
    mt["C7"], mt["D7"] = "=Assumptions!B14", "=Assumptions!B17"
    mt["E7"], mt["F7"] = '=IF(B7>=C7,"PASS","FAIL")', '=IF(B7>=D7,"PASS","FAIL")'
    mt["A8"], mt["B8"] = "LFM", "=IF(B4-B5>0,1-B6/(B4-B5),0)"
    mt["C8"], mt["D8"] = "=Assumptions!B15", "=Assumptions!B18"
    mt["E8"], mt["F8"] = '=IF(B8>=C8,"PASS","FAIL")', '=IF(B8>=D8,"PASS","FAIL")'
    mt["A9"], mt["B9"] = "PMHF, first order (FIT)", "=B5+B6*B4*Assumptions!B13*0.000000001"
    mt["C9"], mt["D9"] = "=Assumptions!B16", "=Assumptions!B19"
    mt["E9"], mt["F9"] = '=IF(B9<=C9,"PASS","FAIL")', '=IF(B9<=D9,"PASS","FAIL")'
    for r_ in (7, 8):
        for c in (2, 3, 4):
            mt.cell(row=r_, column=c).number_format = "0.000%"
    for r_ in (4, 5, 6, 9):
        mt.cell(row=r_, column=2).number_format = "0.0000"
    mt["A12"] = f"Comparison (values computed by build_fmeda.py; the formulas above give the '{VER} measured' column" + \
                ("" if VER == "v2" else "; the v2 column is fmeda_v2.md's, run 192") + ")"
    mt["A12"].font = BOLD
    vlabels = [label for _, label in modes] + (["v2 measured (drop 0.43)"] if VER == "v3" else [])
    header(mt, 13, ["Metric"] + vlabels, [44, 18, 18, 22, 20, 22, 22])
    for j, (key, label) in enumerate([("spfm", "Core SPFM"), ("lfm", "Core LFM"), ("pmhf", "Core PMHF (FIT)"),
                                      ("rf", "Core Σ λ_SPF/RF (FIT)"), ("spfm_c", "Chip SPFM"), ("lfm_c", "Chip LFM"),
                                      ("pmhf_c", "Chip PMHF (FIT)")], start=14):
        mt.cell(row=j, column=1, value=label)
        for k, (m, _) in enumerate(modes, start=2):
            src = chips[m] if key.endswith("_c") else core[m]
            v = src[key.replace("_c", "")]
            cell = mt.cell(row=j, column=k, value=v)
            cell.number_format = "0.000%" if key.startswith(("spfm", "lfm")) else "0.000"
        if VER == "v3":
            v2k = ("chip_" if key.endswith("_c") else "core_") + key.replace("_c", "")
            if v2k in V2_RESULT:
                cell = mt.cell(row=j, column=len(modes) + 2, value=V2_RESULT[v2k])
                cell.number_format = "0.000%" if key.startswith(("spfm", "lfm")) else "0.000"

    # ---------------------------------------------------------------- Chip_rollup_v2
    cr = wb.create_sheet(f"Chip_rollup_{VER}")
    cr["A1"] = (f"Chip-level roll-up {VER}: the MAC core from FMEDA_core_{VER}, the shared SRAM's permanent faults and the NoC / DMA "
                "engines measured on the tile build (stuck-at faults: their λ is permanent), the shared SRAM's soft errors "
                "with v1's coverage (its residual is v1's multi-bit assumption; the single-bit faults are the evidence), the "
                "other elements as v1. λ_RF = λ_SR x (1 - F_safe) x (1 - DC_RF).")
    cr["A1"].font = BOLD
    cr["A1"].alignment = WRAP
    cr.merge_cells("A1:L1")
    cr.row_dimensions[1].height = 60
    header(cr, 3, ["Element", "Count", "λ_SR per instance (FIT)", "F_safe", "DC_RF", "λ_SM per instance (FIT)", "DC_L",
                   "λ_SR total", "λ_SPF/RF total", "λ_MPF,L total", "PMHF contribution (FIT)", "Mechanism / basis"],
           [34, 7, 14, 9, 10, 14, 8, 12, 12, 12, 14, 60])
    T = "Assumptions!$B$13"
    elems = chip_elements(faults, formal, core["v2"], "v2")
    for i, (name, cnt, lam, fs, dc, lsm, dcl, sm, st, basis) in enumerate(elems, start=4):
        cr.cell(row=i, column=1, value=name)
        cr.cell(row=i, column=2, value=cnt).font = BLUE
        if i == 4:
            cr.cell(row=i, column=3, value=f"=Metrics_{VER}!B4")
            cr.cell(row=i, column=4, value=0)
            cr.cell(row=i, column=5, value=f"=1-Metrics_{VER}!B5/Metrics_{VER}!B4")
            cr.cell(row=i, column=6, value=f"=Metrics_{VER}!B6")
            cr.cell(row=i, column=7, value=0)
        else:
            cr.cell(row=i, column=3, value=lam)
            cr.cell(row=i, column=4, value=fs).font = BLUE
            cr.cell(row=i, column=5, value=dc).font = BLUE
            cr.cell(row=i, column=6, value=lsm).font = BLUE
            cr.cell(row=i, column=7, value=dcl).font = BLUE
        cr.cell(row=i, column=8, value=f"=B{i}*C{i}")
        cr.cell(row=i, column=9, value=f"=B{i}*C{i}*(1-D{i})*(1-E{i})")
        cr.cell(row=i, column=10, value=f"=B{i}*F{i}*(1-G{i})")
        cr.cell(row=i, column=11, value=f"=I{i}+J{i}*C{i}*{T}*0.000000001")
        txt = f"{basis}: {sm}"
        if st:
            txt += (f" | campaign ({sel_text((dict(CHIP_SEL, **CHIP_SEL_V3) if VER == 'v3' else CHIP_SEL)[name][0])}): {st['n']} faults, {st['det']} detected, {st['masked']} masked, "
                    f"{st['dang']} dangerous; DC {pc(st['dc'])}" + (f" ({pc(st['dc_ci'][0])} .. {pc(st['dc_ci'][1])})" if st['dc_ci'] else ""))
        if name in CHIP_PROOFS:
            txt += " | " + proofs_text(CHIP_PROOFS[name], formal)
        cr.cell(row=i, column=12, value=txt).alignment = WRAP
        for c in range(1, 13):
            cr.cell(row=i, column=c).border = BORDER
        for c in (3, 6, 8, 9, 10, 11):
            cr.cell(row=i, column=c).number_format = "0.000"
        for c in (4, 5, 7):
            cr.cell(row=i, column=c).number_format = "0.00000"
    e_last = 4 + len(elems) - 1
    t = e_last + 2
    cr.cell(row=t, column=1, value="Totals").font = BOLD
    for c, col in ((8, "H"), (9, "I"), (10, "J"), (11, "K")):
        cr.cell(row=t, column=c, value=f"=SUM({col}4:{col}{e_last})").font = BOLD
        cr.cell(row=t, column=c).number_format = "0.000"
    cr.cell(row=t + 2, column=1, value="Chip SPFM")
    cr.cell(row=t + 2, column=2, value=f"=IF(H{t}>0,1-I{t}/H{t},0)").number_format = "0.000%"
    cr.cell(row=t + 3, column=1, value="Chip LFM")
    cr.cell(row=t + 3, column=2, value=f"=IF(H{t}-I{t}>0,1-J{t}/(H{t}-I{t}),0)").number_format = "0.000%"
    cr.cell(row=t + 4, column=1, value="Chip PMHF, first order (FIT)")
    cr.cell(row=t + 4, column=2, value=f"=K{t}").number_format = "0.0"
    cr.cell(row=t + 5, column=1, value="Meets ASIL-B(D)?")
    cr.cell(row=t + 5, column=2, value=f'=IF(AND(B{t + 2}>=Assumptions!B14,B{t + 3}>=Assumptions!B15,B{t + 4}<=Assumptions!B16),"PASS","FAIL")')

    # ---------------------------------------------------------------- Campaign, Undetected, Formal, Evidence
    cs = wb.create_sheet("Campaign")
    cs["A1"] = "The fault campaign per block (unweighted within a block: every fault of a block has the same weight)"
    cs["A1"].font = BOLD
    header(cs, 3, ["Build", "Block", "Faults", "Detected", "Masked", "Dangerous", "Hang detected", "Hang", "DC", "DC 95 % CI",
                   "Latency median (cycles)", "Latency max (cycles)"], [8, 30, 8, 9, 8, 9, 9, 7, 9, 18, 12, 12])
    for i, ((cfg, blk), s) in enumerate(sorted(block_table(faults).items()), start=4):
        lat = sorted(s["lat"])
        vals = [cfg, blk, s["n"], s["det"], s["masked"], s["dang"], s["hangd"], s["hang"], s["dc"],
                f"{pc(s['dc_ci'][0])} .. {pc(s['dc_ci'][1])}" if s["dc_ci"] else "", lat[len(lat) // 2] if lat else None,
                lat[-1] if lat else None]
        for c, v in enumerate(vals, 1):
            cs.cell(row=i, column=c, value=v)
        cs.cell(row=i, column=9).number_format = "0.0%"
    un = wb.create_sheet("Undetected")
    un["A1"] = "Dangerous faults (wrong results, no cause) and hangs without a cause"
    un["A1"].font = BOLD
    header(un, 3, ["Build", "Node", "Block", "Flop", "Bit", "Type", "Cycle", "Outcome", "Result words wrong"], [8, 6, 26, 70, 6, 6, 8, 10, 10])
    bad = [r for r in faults if r["outcome"] in ("dangerous", "hang")]
    for i, r in enumerate(sorted(bad, key=lambda r: (r["config"], r["block"], r["flop"])), start=4):
        for c, k in enumerate(["config", "node", "block", "flop", "bit", "type", "cycle", "outcome", "diff"], 1):
            un.cell(row=i, column=c, value=r[k])
    fs_ = wb.create_sheet("Formal")
    fs_["A1"] = f"The formal job's proofs ({formal_title or 'no report supplied'}) and the FMEDA rows citing them"
    fs_["A1"].font = BOLD
    header(fs_, 3, ["Task", "Result", "Mode", "Time", "What is proven", "Cited by"], [22, 8, 8, 10, 60, 60])
    cited = {}
    for r in rows:
        for p in (r.get("proofs") or []):
            cited.setdefault(p, []).append(f"{r['block']}: {r['fmode']}")
    for name, ps in CHIP_PROOFS.items():
        for p in ps:
            cited.setdefault(p, []).append(f"chip: {name}")
    for i, task in enumerate(sorted(set(formal) | set(cited)), start=4):
        res, mode, tm, what = formal.get(task, ("not in the report", "", "", ""))
        for c, v in enumerate([task, res, mode, tm, what, "; ".join(dict.fromkeys(cited.get(task, [])))], 1):
            fs_.cell(row=i, column=c, value=v).alignment = WRAP
    ev = wb.create_sheet("Evidence")
    ev["A1"] = "Mechanism evidence: v1's fault-injection tests, and v2's (the campaign, C6)" + ("" if VER == "v2" else ", and v3's (drop 0.44: C15-C17, M26-M28, T8)")
    ev.append(["Mechanism", "Fault injected", "Test", "Log", "Drop"])
    for row in EVIDENCE_V1:
        ev.append(list(row))
    ev.append(["Every mechanism, measured", "random flips and stuck-at faults in every flop and memory cell of the core "
               "(C1) and the 2x2 tile mesh (M1), classified against the fault-free run", "the faults job",
               "ci-logs-faults: faults.csv, faults_report.md, undetected.md", "0.43"])
    ev.append(["The comparator storage of 0.41 (R7 / R10)", "a flip in each of the twelve registers 0.41's synchronous reads "
               "added behind the comparators (the port-B address latches, inb_q, a0_q, first_oq, w_load_q, ra0_q, the "
               "accumulate's stage-0 control)", "C6", "dv/cocotb (cocotb core)", "0.43"])
    if VER == "v3":
        for row in EVIDENCE_V3:
            ev.append(list(row))
        s1 = wb.create_sheet("S1_mechanisms")
        s1["A1"] = "The mechanisms of drop 0.44 (S1) in the campaign: the registers and cells each one covers, their faults and outcomes"
        s1["A1"].font = BOLD
        header(s1, 3, ["Mechanism / registers", "Faults", "Detected", "Masked", "Dangerous", "Hang", "DC", "Latency median / max (cycles)"], [70, 8, 9, 8, 10, 7, 9, 22])
        for i, (label, st) in enumerate(s1_table(faults), start=4):
            vals = [label, st["n"], st["det"], st["masked"], st["dang"], st["hang"], st["dc"], st["lat"]]
            for c, v in enumerate(vals, 1):
                s1.cell(row=i, column=c, value=v)
            s1.cell(row=i, column=7).number_format = "0.0%"
        ba = wb.create_sheet("Before_after")
        ba["A1"] = "Every block of both builds: v2's campaign (run 192, drop 0.43) against this one"
        ba["A1"].font = BOLD
        header(ba, 3, ["Build", "Block", "v2 faults", "v2 dangerous", "v2 DC", "v3 faults", "v3 dangerous", "v3 DC"], [8, 30, 10, 12, 9, 10, 12, 9])
        for i, (cfg, blk, a, b) in enumerate(before_after(faults), start=4):
            vals = [cfg, blk] + list(a) + list(b)
            for c, v in enumerate(vals, 1):
                ba.cell(row=i, column=c, value=v)
            for c in (5, 8):
                ba.cell(row=i, column=c).number_format = "0.0%"
    for col, wdt in (("A", 36), ("B", 60), ("C", 20), ("D", 52), ("E", 10)):
        ev.column_dimensions[col].width = wdt
    for sheet in wb.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if cell.font is None or cell.font.name != ARIAL:
                    f = cell.font
                    cell.font = Font(name=ARIAL, bold=f.bold, italic=f.italic, color=f.color)
    wb.save(path)


EVIDENCE_V3 = [
    ("Activation buffer SECDED (drop 0.44)", "a data bit and a check bit of stored lanes flipped (corrected, abuf_ce); two bits of one lane (abuf_ue)",
     "C15 (cocotb core), C13 (neosim run_conv_core --late)", "dv/cocotb, sim/", "0.44"),
    ("Activation path byte parity (drop 0.44)", "a flip of the feeder's row register, its parity bit, a skew chain stage, its parity chain (act_perr)",
     "C16 (cocotb core), T8 (tb_neo_mac_core)", "dv/cocotb, tb/", "0.44"),
    ("Index / first delay-line parity (drop 0.44)", "a flip of the lines' last stage, a stage in flight, the parity line (ctrl_err)", "C16", "dv/cocotb", "0.44"),
    ("Link packer beat-parity check (drop 0.44)", "a bit of the drain FIFO's output register stuck through the drain (the parity cause from the packer, the CRC consistent)",
     "M26 (cocotb tile)", "dv/cocotb", "0.44"),
    ("Drain FIFO overflow flag (drop 0.44, review finding 3)", "GO, WAIT_DONE, GO, WAIT_DONE with no drain: 72 rows into the 64-row FIFO (drain_ovf)", "M27", "dv/cocotb", "0.44"),
    ("MBIST one-hot state (drop 0.44)", "a second bit of the idle state set (bist_fail that cycle, the engine back to idle, a clean pass after)", "M28", "dv/cocotb", "0.44"),
    ("ECC flags qualified by a real read (drop 0.44, review finding 1)", "the arrays and unreset registers random at reset (--x-initial unique, +verilator+rand+reset+2): no flag with nothing written",
     "C17 (core_xinit), M1 (tile_xinit)", "dv/cocotb", "0.44"),
]


def _lat_text(lat):
    lat = sorted(lat)
    return f"{lat[len(lat) // 2]} / {lat[-1]}" if lat else "-"


def s1_table(faults):
    """[(label, stats)] over S1_REGS: the faults of each mechanism's registers / cells in both builds"""
    out = []
    for label, sel in S1_REGS:
        if "block" in sel:
            rs = [r for r in faults if r["outcome"] != "unresolved" and r["block"] == sel["block"]]
        else:
            pat = re.compile(sel["re"])
            rs = [r for r in faults if r["outcome"] != "unresolved" and pat.search(r["flop"])]
        det = sum(r["outcome"] in ("detected", "hang_detected") for r in rs)
        dang = sum(r["outcome"] in ("dangerous", "hang") for r in rs)
        lat = [int(r["latency"]) for r in rs if r["outcome"] in ("detected", "hang_detected") and r["latency"] not in ("", None)]
        out.append((label, dict(n=len(rs), det=det, masked=sum(r["outcome"] == "masked" for r in rs), dang=dang,
                                hang=sum(r["outcome"] in ("hang", "hang_detected") for r in rs),
                                dc=(det / (det + dang)) if det + dang else None, lat=_lat_text(lat))))
    return out


def before_after(faults):
    """[(build, block, (v2 n, dang, DC), (v3 n, dang, DC))] for every block of either campaign"""
    v2 = v2_campaign_table()
    v3 = block_table(faults)
    out = []
    for key in sorted(set(v2) | set(v3)):
        a = v2.get(key)
        b = v3.get(key)
        ta = (a[0], a[3], (a[1] / (a[1] + a[3])) if a and (a[1] + a[3]) else None) if a else (0, 0, None)
        tb = (b["n"], b["dang"] + b["hang"], b["dc"]) if b else (0, 0, None)
        out.append((key[0], key[1], ta, tb))
    return out


def write_v2_md(path, rows, faults, meta, run, formal, formal_title, core, core_v1rows, chips, chip_v1rows, modes, bv):
    if VER == "v2":
        L = ["# FMEDA v2 -- the diagnostic coverage measured (drop 0.43, stream D2)", ""]
    else:
        L = ["# FMEDA v3 -- the safety closure measured (drop 0.44, stream S1)", ""]
    L.append(f"Generated by `05_safety/build_fmeda.py --{VER}` from the fault campaign's `faults.csv` (the `faults` job, "
             f"`02_dv/dv/cocotb/faults/`) and the formal job's `formal_report.md`; the workbook is `neo_fmeda_{VER}.xlsx` "
             f"(sheets Assumptions, FMEDA_core_{VER}, Metrics_{VER}, Chip_rollup_{VER}, Campaign, Undetected, Formal, Evidence"
             + ("" if VER == "v2" else ", S1_mechanisms, Before_after") + ").")
    L.append("")
    if VER == "v3":
        L.append("v3 is v2's method (the rows, their campaign selections, the estimator) applied to the RTL after the safety closure of "
                 "drop 0.44: SECDED on the activation buffer (abuf_ce / abuf_ue), a parity bit per activation byte from the decoded "
                 "buffer word through the feeder's row register and the skew chains to the array's row inputs (act_perr), a parity "
                 "bit through the index / first delay lines (ctrl_err), the link packer's check of the NIC's beat parity (the parity "
                 "cause: the drain FIFO's output register and cells, the serve register), the one-hot MBIST state (bist_fail), the "
                 "drain FIFO's overflow flag (drain_ovf) and the ECC flags qualified by a real read. Two base rates change (below), "
                 "one chip element is added, and the flop count of the synthesized core is a sensitivity.")
        L.append("")
    L.append("## Inputs")
    L.append("")
    if run:
        L.append(f"- Campaign: {run}.")
    for cfg in ("core", "tile"):
        m = meta.get(cfg)
        n = sum(1 for r in faults if r["config"] == cfg and r["outcome"] != "unresolved")
        if not n:
            continue
        g = (m or {}).get("golden", {})
        L.append(f"- The {cfg} build ({'neo_core 16x8, NCHK = 4, workload C1' if cfg == 'core' else '2x2 mesh of 16x8 tiles, workload M1'}): "
                 f"{n} faults classified" + (f" of {m.get('n_total')} drawn" if m and m.get('n_total') and m.get('n_total') != n else "")
                 + (f" (seed {m.get('seed')}, {m.get('n_flop', m.get('n'))} sampled over the flops and "
                 f"{m.get('n_mem', 0)} over the memory cells, workers {m.get('workers')}; the fault-free run "
                 f"{g.get('run_cycles')} cycles)" if m else "") + ".")
    L.append(f"- Formal: {formal_title or 'no report supplied'}" + (f" -- {sum(1 for v in formal.values() if v[0] == 'PASS')} of "
             f"{len(formal)} tasks PASS." if formal else "."))
    L.append("")
    L.append("## Base failure rates (v1's, unchanged)" if VER == "v2" else "## Base failure rates (v1's, the SRAM leaves at the silicon sizes)")
    L.append("")
    L.append(f"Die {bv['B4']} FIT over {bv['B5']} mm²; one MAC core {bv['B6']} mm², so λ_core = {bv['B22']:.4f} FIT "
             f"(permanent); SRAM soft errors {bv['B7']} FIT/Mbit, flip-flops {bv['B8']} FIT/Mbit; per core: accumulator "
             f"{bv['B9']} Mbit ({bv['B23']:.1f} FIT), activation buffer {bv['B10']} Mbit ({bv['B24']:.1f} FIT), weight registers "
             f"{bv['B11']} Mbit ({bv['B25']:.2f} FIT), pipeline and control flops {bv['B12']} Mbit ({bv['B26']:.1f} FIT); "
             f"lifetime {bv['B13']:,} h; targets ASIL-B(D) {bv['B14']:.0%} / {bv['B15']:.0%} / {bv['B16']} FIT, ASIL-D "
             f"{bv['B17']:.0%} / {bv['B18']:.0%} / {bv['B19']} FIT (SPFM / LFM / PMHF). v2 adds one row v1 lacks: the weight "
             f"buffer's storage, {bv['B32']} Mbit at the silicon size ({bv['B33']:.1f} FIT; 1024 entries x 12 SECDED lanes of "
             "the NCHK = 4 32x32 core). " +
             ("Not changed, so that v2 compares like for like (for v3): the silicon activation buffer "
              "is 2048 x 32 B = 0.5 Mbit (v1 counts 0.25, and its note's 4096 x 32 x 8 bit is 1 Mbit); the NCHK = 4 "
              "accumulator is 0.5625 Mbit (v1: 0.5)." if VER == "v2" else
              f"v3 changes two of them to the bits of the silicon leaves (09_synthesis/memories.md): the activation buffer "
              f"{bv['B10']} Mbit = 1,024 pair words x 624 bits, the SECDED check bits included (v1 0.25, v2's note 0.5: the 512-bit "
              f"word before the code), {bv['B24']:.1f} FIT; the accumulator {bv['B9']} Mbit = 512 rows x 1,056 bits (33 partial sums; "
              f"v1 0.5, v2's note 0.5625 counted 36 words), {bv['B23']:.1f} FIT. v3 adds one chip element neither version priced: "
              f"the tile's NIC memories, {bv['B34']} Mbit per tile (the drain FIFO 512 x 1,069 bits, the partial-sum rows 64 x 1,024), "
              f"{bv['B35']:.1f} FIT per tile, measured on the tile build. Not applied to the rows, shown as the metrics' last column: "
              f"the synthesized silicon core holds {V3_FLOPS:,} flops (synth.py, neo_core 32x32 NCHK = 4, drop 0.44) against v1's "
              f"0.0576 Mbit of weight and pipeline registers -- with B11 = 26,112 weight-register bits and B12 = the other "
              f"{V3_FLOPS - 26112:,} = {(V3_FLOPS - 26112) / 2 ** 20:.4f} Mbit, every transient flop row is {(V3_FLOPS - 26112) / 2 ** 20 / bv['B12']:.1f} x its v1 λ."))
    L.append("")
    L.append("## How a row is measured")
    L.append("")
    L.append("Each row names the faults that measure it (build, blocks, fault type: stuck-at for a permanent λ, flips for a "
             "transient one -- v1's rows of type 'both' are split into the two parts). Each fault carries the campaign's "
             "weight (its block's bits / its block's sample), so a row spanning blocks is a bit-weighted estimate. "
             "F_safe = the weighted fraction masked (results bit-exact, no cause: a safe fault in this workload); DC_RF = "
             "detected / (detected + dangerous) (a hang without a cause is dangerous, one the watchdog or the fetch timeout "
             "caught is detected); the interval is Wilson's at Kish's effective sample size. λ_SPF/RF = λ x SR x FMD x SPF "
             "x (1 - F_safe) x (1 - DC_RF), so the residual is λ x the dangerous fraction. Two sensitivities: no credit for "
             "masked faults (F_safe = 0: every fault assumed to matter in some workload) and every measured row at the "
             "95 % upper bound of its dangerous fraction (except a row a passing proof covers for every case -- the "
             "weight buffer's single flips, ecc39 -- when no fault of it was dangerous). Rows the campaign cannot measure "
             "keep v1's value and say why. "
             "Every cause was raised within the FTTI (10 ms = 20,000,000 cycles at 2 GHz; the longest run is a few "
             "thousand cycles).")
    L.append("")
    L.append("## The rows")
    L.append("")
    L.append("| Block / failure mode | Type | λ (FIT) | Campaign selection | Faults (det / masked / dang) | F_safe | DC_RF measured (95 % CI) | v1 DC_RF | λ_RF v2 | λ_RF v1 | Proofs |")
    L.append("| --- | --- | ---: | --- | --- | ---: | --- | ---: | ---: | ---: | --- |")
    for r in rows:
        st = r["st"]
        lam = r["lam_value"] * r["sr"] * r["fmd"]
        sel = r.get("sel") or r.get("latent")
        if st:
            fx = f"{st['n']} ({st['det']} / {st['masked']} / {st['dang']})"
            dcm = (f"{pc(st['dc'])} ({pc(st['dc_ci'][0])} .. {pc(st['dc_ci'][1])})" if st["dc"] is not None else "n/a (all masked)")
            fsv = pc(r["fsafe"])
        elif r.get("lat"):
            lt = r["lat"]
            fx = f"{lt['n']} ({lt['det']} / {lt['masked']} / {lt['dang']})"
            dcm, fsv = "claim (latent row)", "-"
        else:
            fx, dcm, fsv = "-", "claim", "-"
        v1rf = f"{residual(r, 'v1'):.4f}" if r["v1dc"] is not None else "-"
        L.append(f"| {r['block']}: {r['fmode']} | {r['ftype']} | {lam:.4f} | {sel_text(sel) if sel else r.get('claim', '')} | "
                 f"{fx} | {fsv} | {dcm} | {pc(r['v1dc'], 2) if r['v1dc'] is not None else '-'} | {residual(r, 'v2'):.4f} | "
                 f"{v1rf} | {r['proofs_text'] or '-'} |")
    L.append("")
    claims = [r for r in rows if r["basis"].startswith("claim")]
    if claims:
        L.append("Rows that keep v1's value: " + "; ".join(f"{r['block']}: {r['fmode']} ({r.get('claim', 'no faults drawn')})" for r in claims) + ".")
        L.append("")
    L.append("## Metrics")
    L.append("")
    L.append("| | " + " | ".join(label for _, label in modes) + " |")
    L.append("| --- | " + " | ".join("---:" for _ in modes) + " |")
    for key, label, fmt in (("spfm", "Core SPFM", "p3"), ("lfm", "Core LFM", "p4"), ("pmhf", "Core PMHF (FIT)", "f3"),
                            ("rf", "Core Σ λ_SPF/RF (FIT)", "f4")):
        L.append(f"| {label} | " + " | ".join(fmt_v(core[m][key], fmt) for m, _ in modes) + " |")
    for key, label, fmt in (("spfm", "Chip SPFM", "p3"), ("lfm", "Chip LFM", "p5"), ("pmhf", "Chip PMHF (FIT)", "f1")):
        L.append(f"| {label} | " + " | ".join(fmt_v(chips[m][key], fmt) for m, _ in modes) + " |")
    L.append("")
    if VER == "v3":
        L.append("| v2 measured (drop 0.43, run 192) | " + " | ".join([fmt_v(V2_RESULT["core_spfm"], "p3"), fmt_v(V2_RESULT["core_lfm"], "p4"),
                 fmt_v(V2_RESULT["core_pmhf"], "f3"), fmt_v(V2_RESULT["core_pmhf"], "f4"), fmt_v(V2_RESULT["chip_spfm"], "p3"),
                 fmt_v(V2_RESULT["chip_lfm"], "p5"), fmt_v(V2_RESULT["chip_pmhf"], "f1")]) + " |")
        L.append("")
        L.append("(the row above is v2's result as one line; its columns are, in order, core SPFM, core LFM, core PMHF, core Σ λ_RF, chip SPFM, chip LFM, chip PMHF)")
        L.append("")
        tgt_ok = chips["v2"]["pmhf"] <= bv["B16"]
        L.append(f"**The target: chip PMHF <= {bv['B16']} FIT with the measured coverage -- {VER} measured {fmt_v(chips['v2']['pmhf'], 'f1')} FIT, "
                 f"{'met' if tgt_ok else 'NOT met'}** (v2: {V2_RESULT['chip_pmhf']:.1f} FIT); the no-credit-for-masked-faults sensitivity "
                 f"{fmt_v(chips['nomask']['pmhf'], 'f1')} FIT, the 95 % upper bounds {fmt_v(chips['upper']['pmhf'], 'f1')} FIT, the synthesized "
                 f"flop count {fmt_v(chips['flops']['pmhf'], 'f1')} FIT. Where the residual sits: " +
                 "; ".join(f"{name} {CORES * core['v2']['rf'] if name.startswith('MAC core') else c * l * (1 - fs) * (1 - dc):.1f} FIT"
                           for name, c, l, fs, dc, *_ in sorted(chip_elements(faults, formal, core["v2"], "v2"),
                                                               key=lambda e: -(CORES * core["v2"]["rf"] if e[0].startswith("MAC core") else e[1] * e[2] * (1 - e[3]) * (1 - e[4])))[:4]) + ".")
        L.append("")
    L.append(f"{VER} measured without the row v1 lacks (the weight buffer's storage): core SPFM {fmt_v(core_v1rows['spfm'], 'p3')}, "
             f"PMHF {fmt_v(core_v1rows['pmhf'], 'f3')} FIT; chip PMHF {fmt_v(chip_v1rows['pmhf'], 'f1')} FIT. Chip = {CORES} cores "
             "plus v1's other elements (the shared SRAM's permanent faults and the NoC / DMA engines measured on the tile "
             "build; the SRAM's soft errors keep v1's 99.995 %: its residual is v1's multi-bit assumption, which a "
             "single-bit campaign cannot measure, while the single-bit faults show the correction).")
    L.append("")
    tgt = [("ASIL-B(D)", bv["B14"], bv["B15"], bv["B16"]), ("ASIL-D", bv["B17"], bv["B18"], bv["B19"])]
    for name, s, l_, p in tgt:
        verdict = []
        for m, label in modes:
            ok_c = core[m]["spfm"] >= s and core[m]["lfm"] >= l_ and core[m]["pmhf"] <= p
            ok_x = chips[m]["spfm"] >= s and chips[m]["lfm"] >= l_ and chips[m]["pmhf"] <= p
            verdict.append(f"{label}: core {'PASS' if ok_c else 'FAIL'}, chip {'PASS' if ok_x else 'FAIL'}")
        L.append(f"- {name} ({s:.0%} / {l_:.0%} / {p} FIT): " + "; ".join(verdict) + ".")
    L.append("")
    # the rows that moved most
    L.append("## What the measurement changes")
    L.append("")
    moved = sorted((r for r in rows if r["st"] and r["v1dc"] is not None),
                   key=lambda r: -(residual(r, "v2") - residual(r, "v1")))
    for r in moved[:6]:
        d = residual(r, "v2") - residual(r, "v1")
        if abs(d) < 1e-4:
            continue
        L.append(f"- **{r['block']}: {r['fmode']}** -- residual {residual(r, 'v1'):.4f} -> {residual(r, 'v2'):.4f} FIT "
                 f"(v1 claimed DC {pc(r['v1dc'], 2)}; measured {pc(r['st']['dc'])} over {r['st']['n']} faults, "
                 f"{r['st']['dang']} dangerous, F_safe {pc(r['fsafe'])}).")
    L.append("")
    bad = [r for r in faults if r["outcome"] in ("dangerous", "hang")]
    byb = {}
    for r in bad:
        byb.setdefault((r["config"], r["block"]), []).append(r)
    L.append(f"The undetected faults ({len(bad)} of {sum(1 for r in faults if r['outcome'] != 'unresolved')}" +
             (f"; v2: {V2_UNDETECTED} of 6497" if VER == "v3" else "") + "), by block -- " +
             ("the RTL items they point at (reported, not fixed: the brief limits D2's RTL changes to item 2):" if VER == "v2" else
              "what is left after the safety closure:"))
    L.append("")
    for (cfg, blk), rs in sorted(byb.items(), key=lambda kv: -len(kv[1])):
        kinds = list(dict.fromkeys(re.sub(r"\[\d+\]", "[i]", r["flop"].split(".u_t.")[-1]) for r in rs))
        top = ", ".join("`" + k + "`" for k in kinds[:4]) + (" ..." if len(kinds) > 4 else "")
        L.append(f"- {cfg} / {blk}: {len(rs)} -- {top}")
    L.append("")
    if VER == "v3":
        L.append("## The mechanisms of drop 0.44 (S1) in the campaign")
        L.append("")
        L.append("The registers and cells each mechanism covers (both builds; the selections are S1_REGS in build_fmeda.py), their "
                 "faults and outcomes. A flip of a stored parity bit is a detected fault whose result is right (a false alarm, "
                 "counted as detected); a masked fault is one whose register held nothing in use.")
        L.append("")
        L.append("| Mechanism / registers | Faults | Detected | Masked | Dangerous | Hang | DC | Latency median / max (cycles) |")
        L.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |")
        for label, st in s1_table(faults):
            L.append(f"| {label} | {st['n']} | {st['det']} | {st['masked']} | {st['dang']} | {st['hang']} | {pc(st['dc'])} | {st['lat']} |")
        L.append("")
        L.append("## Before / after, every block (v2's campaign of run 192 against this one)")
        L.append("")
        L.append("| Build | Block | v2 faults | v2 dangerous | v2 DC | v3 faults | v3 dangerous | v3 DC |")
        L.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |")
        for cfg, blk, a, b in before_after(faults):
            L.append(f"| {cfg} | {blk} | {a[0]} | {a[1]} | {pc(a[2])} | {b[0]} | {b[1]} | {pc(b[2])} |")
        L.append("")
    # item 2: the comparator storage of drop 0.41, register by register, and the row that carries it
    L.append("## The comparator storage of drop 0.41 (item 2)")
    L.append("")
    L.append("The registers drop 0.41's synchronous reads put behind the R7 / R10 comparators, compared since drop 0.43 "
             "(the port-B address latches through neo_mem_rf's RBQ = 1 readback, the others against copies), and the copies "
             "themselves; their faults are part of the rows named (both builds counted here, the rows use the core build). "
             "Without the copies none of them raises a flag when upset: in the C6 test (each of the twelve flipped once while "
             "in use) seven runs end with wrong rows -- inb_q, first_oq, w_load_q, the weight buffer's latch and ra0_q, idx1, "
             "the accumulator's latch: a consistent wrong row passes the ABFT check and the SECDED lanes -- and five with "
             "exact rows; with the copies all twelve raise their comparator's flag.")
    L.append("")
    L.append("| Register | FMEDA row | Faults | Detected | Masked | Dangerous | Latency median / max (cycles) |")
    L.append("| --- | --- | ---: | ---: | ---: | ---: | --- |")
    for frag, label, row in ITEM2_REGS:
        pat = re.compile(r"(^|\.)" + re.escape(frag) + r"$")
        rs = [r for r in faults if r["outcome"] != "unresolved" and pat.search(r["flop"])]
        if not rs:
            L.append(f"| {label} | {row} | 0 | | | | |")
            continue
        lat = sorted(int(r["latency"]) for r in rs if r["outcome"] in ("detected", "hang_detected") and r["latency"] not in ("", None))
        L.append(f"| {label} | {row} | {len(rs)} | {sum(r['outcome'] in ('detected', 'hang_detected') for r in rs)} | "
                 f"{sum(r['outcome'] == 'masked' for r in rs)} | {sum(r['outcome'] in ('dangerous', 'hang') for r in rs)} | "
                 f"{(str(lat[len(lat) // 2]) + ' / ' + str(lat[-1])) if lat else '-'} |")
    L.append("")
    L.append("## Not measured here")
    L.append("")
    L.append("- Combinational logic: the campaign injects into flops and memory cells (stuck-at as the permanent-fault proxy); "
             "the gate-level fault simulation at freeze covers the logic between them.")
    L.append("- Multi-bit upsets, dual faults and latent faults of the checkers (single-fault campaign): v1's values kept, "
             "the formal proofs cited where they cover the mechanism (ecc39: every double flip detected).")
    L.append("- The reduce port (no K-split in C1 / M1), clock and reset; the program memory's words, the router's FIFO "
             "slots and the NIC's partial-sum FIFO (two-dimensional arrays the VPI cannot index on Verilator 5.020) and, in "
             "the tile build, the computing core's skew pipes (left out by the probe; measured in the core build).")
    with open(path, "w") as f:
        f.write("\n".join(L) + "\n")


def fmt_v(x, fmt):
    if fmt.startswith("p"):
        return f"{100 * x:.{int(fmt[1:])}f} %"
    return f"{x:.{int(fmt[1:])}f}"


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="the FMEDA workbook: v1 (no arguments), v2 (--v2) or v3 (--v3) from the fault campaign")
    ap.add_argument("--v2", action="store_true", help="FMEDA v2 from the fault campaign and the formal report")
    ap.add_argument("--v3", action="store_true", help="FMEDA v3 (drop 0.44, S1): the safety closure measured, the silicon base rates")
    ap.add_argument("--faults", help="the faults job's output directory (faults.csv, faults_summary.json) or faults.csv")
    ap.add_argument("--formal", help="the formal job's formal_report.md")
    ap.add_argument("--out", default=os.path.dirname(os.path.abspath(__file__)), help="where the v2 outputs go")
    args = ap.parse_args()
    if args.v3:
        VER, DROP, STREAM = "v3", "0.44", "S1"
    if args.v2 or args.v3:
        if not args.faults:
            raise SystemExit(f"build_fmeda.py --{VER} needs --faults")
        xlsx, md, core, chips = build_v2(args.faults, args.formal, args.out)
        print(f"{xlsx}\n{md}\ncore: SPFM v1 {core['v1']['spfm']:.4%} -> {VER} {core['v2']['spfm']:.4%}, PMHF {core['v1']['pmhf']:.3f} -> "
              f"{core['v2']['pmhf']:.3f} FIT; chip PMHF {chips['v1']['pmhf']:.1f} -> {chips['v2']['pmhf']:.1f} FIT"
              + (f" (v2: core PMHF {V2_RESULT['core_pmhf']}, chip PMHF {V2_RESULT['chip_pmhf']}; the target 100 FIT "
                 f"{'met' if chips['v2']['pmhf'] <= 100 else 'NOT met'})" if VER == "v3" else ""))
    else:
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "neo_fmeda_skeleton.xlsx")
        print(build(out))
