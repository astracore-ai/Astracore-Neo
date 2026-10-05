#!/usr/bin/env python3
"""
build_fmeda.py -- AstraCore Neo FMEDA skeleton (ISO 26262-5 / -11 style), drop 0.3.

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
import os

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
    rows = [
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
    A = "Assumptions!"
    rows = [
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
    for row in [
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
    ]:
        ev.append(list(row))
    for col, wdt in (("A", 36), ("B", 50), ("C", 20), ("D", 52), ("E", 10)):
        ev.column_dimensions[col].width = wdt
    wb.save(path)
    return path


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "neo_fmeda_skeleton.xlsx")
    print(build(out))
