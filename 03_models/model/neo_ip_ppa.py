#!/usr/bin/env python3
"""
neo_ip_ppa.py -- power, performance and area of the Neo NPU IP per tile and per configuration (drop 0.15).

The licensee's first question. Every number is derived from the assumptions listed below, which are
first-order N5A estimates (+/-30 %) to be replaced by synthesis and memory-compiler reports in phase 1
and by the 2027 test chip. Energy per MAC and leakage are the same constants as model/neo_feasibility.py.
"""
import argparse

# ---- assumptions (N5A, first order) ------------------------------------------------------------
E_MAC_PJ_AT_VREF = 0.45     # pJ per INT8 MAC, system level (datapath + local SRAM + NoC share) at V_REF
V_REF = 0.75                # V, ~2.0 GHz worst-case corner
LEAK_W_PER_MM2_25C = 2.0 / 200.0   # W/mm2 at 25 C for an HVT/ULVT mix (die model: 2 W per 200 mm2)
LEAK_DOUBLE_C = 35.0        # leakage doubles every 35 C
MAC_UM2 = 25.0              # um2 per INT8 MAC with 32-bit accumulator, 2-stage pipeline, placed (60 % util.)
SRAM_UM2_PER_BIT = 0.021 * 1.4      # N5 high-density bit cell with periphery, ECC and redundancy overhead
REG_UM2_PER_BIT = 0.6       # register-file bits for the accumulator and small buffers
LOGIC_MM2 = 0.020           # sequencer pair, feeder, requant pair, DMA engine, host_if, ESM, NIC, CRC, MBIST
ROUTER_MM2 = 0.010          # 5-port router with two-entry FIFOs at 1024-bit links

ROWS = COLS = 32
MACS_PER_TILE = ROWS * (COLS + 1)          # includes the ABFT check column
ACC_BITS = 512 * (COLS + 1) * 32           # accumulator rows x (COLS + check) x 32 bit
WBUF_BITS = 1024 * (COLS * 8 + 14)         # weight buffer entries x (32 weights + 14-bit check weight)
ABUF_BITS = 64 * 1024 * 8                  # 64 KB activation buffer
BANK_BITS = 2 * 1024 * 1024 * 8            # 2 MB bank per tile


def tile_area_mm2(with_bank=True):
    mac = MACS_PER_TILE * MAC_UM2 * 1e-6
    acc = ACC_BITS * SRAM_UM2_PER_BIT * 1e-6 * 2.0    # two-port compiled SRAM (read-modify-write per row)
    wbuf = WBUF_BITS * SRAM_UM2_PER_BIT * 1e-6 * 2.0   # small macros are less dense
    abuf = ABUF_BITS * SRAM_UM2_PER_BIT * 1e-6 * 1.5
    bank = BANK_BITS * SRAM_UM2_PER_BIT * 1e-6 if with_bank else 0.0
    return dict(mac=mac, acc=acc, wbuf=wbuf, abuf=abuf, logic=LOGIC_MM2, router=ROUTER_MM2, bank=bank,
                total=mac + acc + wbuf + abuf + LOGIC_MM2 + ROUTER_MM2 + bank)


def tile_power_w(util, f_ghz, v, tj_c, area_mm2):
    e_mac = E_MAC_PJ_AT_VREF * (v / V_REF) ** 2
    dyn = (ROWS * COLS) * f_ghz * 1e9 * e_mac * 1e-12 * util      # check column is included in the system-level pJ
    leak = area_mm2 * LEAK_W_PER_MM2_25C * 2 ** ((tj_c - 25) / LEAK_DOUBLE_C)
    return dyn, leak


def tops(tiles, f_ghz):
    return tiles * ROWS * COLS * 2 * f_ghz * 1e9 / 1e12


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--util", type=float, default=0.6, help="MAC utilization for the typical-power column")
    a = ap.parse_args()
    A = tile_area_mm2(True)
    A0 = tile_area_mm2(False)
    print("Neo NPU IP, N5A, first-order PPA (+/-30 %)")
    print(f"  tile area: {A['total']:.2f} mm2 with its 2 MB bank ({A0['total']:.2f} mm2 without): "
          f"MAC array {A['mac']:.3f}, accumulator {A['acc']:.3f}, weight buffer {A['wbuf']:.3f}, activation buffer {A['abuf']:.3f}, "
          f"control+interface {A['logic']:.3f}, router {A['router']:.3f}, bank {A['bank']:.2f}")
    points = [("performance", 2.0, 0.75), ("efficiency", 1.6, 0.65)]
    for name, f, v in points:
        print(f"  {name} point: {f:.1f} GHz at {v:.2f} V")
        for grade, tj in (("Grade 2 (Tj 125 C)", 125.0), ("Grade 1 (Tj 150 C)", 150.0)):
            dyn, leak = tile_power_w(1.0, f, v, tj, A['total'])
            dyn_t, _ = tile_power_w(a.util, f, v, tj, A['total'])
            peak = ROWS * COLS * 2 * f * 1e9 / 1e12
            print(f"    {grade}: per tile {peak:.2f} TOPS peak; power {dyn + leak:.2f} W at 100 % ({dyn:.2f} dynamic + {leak:.3f} leakage), "
                  f"{dyn_t + leak:.2f} W at {a.util * 100:.0f} %; efficiency {peak / (dyn + leak):.1f} TOPS/W peak, "
                  f"{peak * a.util / (dyn_t + leak):.1f} TOPS/W at {a.util * 100:.0f} %")
    print(f"\n  {'tiles':>5s} {'TOPS dense':>10s} {'TOPS sparse':>11s} {'SRAM MB':>7s} {'area mm2':>9s} {'W typ (G2)':>10s} {'W typ (G1)':>10s} {'W peak (G2)':>11s}")
    for tiles in (4, 8, 16, 32, 64):
        top = 1.0 if tiles > 8 else 0.5                     # top-level NoC bridge, host port, clocks
        area = tiles * A['total'] + top
        d2, l2 = tile_power_w(a.util, 2.0, 0.75, 125.0, A['total'])
        d1, l1 = tile_power_w(a.util, 2.0, 0.75, 150.0, A['total'])
        dp, lp = tile_power_w(1.0, 2.0, 0.75, 125.0, A['total'])
        print(f"  {tiles:5d} {tops(tiles, 2.0):10.0f} {2 * tops(tiles, 2.0):11.0f} {2 * tiles:7d} {area:9.1f} {tiles * (d2 + l2):10.1f} {tiles * (d1 + l1):10.1f} {tiles * (dp + lp):11.1f}")
    print("\n  Safety overhead of the datapath mechanisms: ABFT check column 1/32 of the array (3.1 %), ECC on the bank "
          "(7 bits per 32: 22 % of bank bits, standard), weight-buffer ECC lanes (~22 % of that buffer), lockstep sequencer "
          "and duplicated requantization (< 2 % of tile logic), CRC/parity/counters in the interface (< 1 %); "
          "total ~5 % of tile area against ~100 % for a duplicated accelerator.")
    print("  Not included: the licensee's host CPU, DRAM PHY, sensors, and the ASIL-D island (reference design available).")


if __name__ == "__main__":
    main()
