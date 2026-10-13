#!/usr/bin/env python3
"""floorplan.py -- the chip-level floorplan sketch of AstraCore Neo on the ASAP7 proxy (drop 0.43, stream A3).

Draws the die as floorplan.svg (beside this script) and prints its numbers as JSON (README.md quotes them):

  * the 8 x 8 tiles as hard macros at the tile's area as placed (tile_routed.json, written by 07_ci/pnr/tile_table.py
    from the pnr job's routed blocks): on top the bank's 16 lane macros (FakeRAM2.0 fakeram7_32768x39_cm64_1w1r, 323.6 x
    166.6 um, as routed in bank_bist_wrap) packed four across with the routed block's 20 um pin channel in front of each
    lane; below them, in one strip as wide as the bank, the 32 x 32 core, the NIC, the router, the packer / unpacker pair,
    the bank's ECC and MBIST logic and the tile's control (two DMA engines, the program memory, host_if, the ESM), each at
    the core area its routed block occupied (the bank's logic and neo_tile's own glue at the median utilization);
  * the bridge column at x = 8 (B1's neo_top): per row a router, a packer / unpacker pair and a NoC-to-AXI4 bridge (the
    host role in row 0), at their synthesized cell areas times the routed / synthesized ratio, at the same utilization;
  * the control plane (top_ctrl, mesh_ctrl) the same way;
  * placeholders for spec section 10's licensed IP -- the LPDDR5X controllers and PHYs (six 64-bit channels), the PCIe
    Gen5 x8 controller and PHY, the ASIL-D safety island (2 x Cortex-R52+ in lockstep with TCMs), the HSM, the ISP -- at
    the sizes in PLACEHOLDERS: assumed N5A-class figures (no model in the repository sizes them), drawn unscaled on the
    proxy (PHYs and I/O are analog and bump-pitch limited and do not shrink with a logic library);
  * the I/O ring, RING_UM wide on each side.

The total is the die the sketch implies on the proxy; its ratio to spec section 9's ~210 mm2 N5A die is a ratio check
between two nodes and two levels of completeness, not a die size.

Usage: floorplan.py [--routed tile_routed.json] [--out floorplan.svg]
"""
import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# assumed sizes of the licensed IP (N5A-class, mm2 each), drawn unscaled on the proxy: name, count, mm2 each, side
PLACEHOLDERS = [
    ("LPDDR5X controller + PHY (64-bit channel)", 6, 4.0, "east"),
    ("PCIe Gen5 x8 controller + PHY", 1, 5.0, "south"),
    ("safety island (2 x R52+ lockstep, TCM)", 1, 2.0, "south"),
    ("HSM", 1, 1.0, "south"),
    ("ISP", 1, 6.0, "south"),
]
RING_UM = 150.0            # the I/O ring (I/O cells, ESD, the bump-to-core redistribution), each side
CHANNEL_UM = 50.0          # between the tile array, the bridge column, the east IP column and the south IP row
N5A_DIE_MM2 = 210.0        # spec section 9


def rect(x, y, w, h, fill, label=None, size=10, stroke="#333", sw=0.6):
    s = f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'
    if label:
        s += (f'<text x="{x + w / 2:.1f}" y="{y + h / 2:.1f}" font-size="{size}" text-anchor="middle" '
              f'dominant-baseline="middle" font-family="sans-serif">{label}</text>')
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--routed", default=os.path.join(HERE, "tile_routed.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "floorplan.svg"))
    args = ap.parse_args()
    with open(args.routed) as f:
        R = json.load(f)

    # ---- the tile (um) ----
    B = R["bank"]
    lane_w, lane_h = B["lane_um"]
    per_row, ch, gap = B["per_row"], B["channel_um"], B["row_gap_um"]
    nrow = -(-B["lanes"] // per_row)
    bank_w, bank_h = B["w_um"], B["h_um"]
    P = R["blocks_placed"]
    lower = [("core 32x32", P["neo_core"], "#b7d7f0"), ("NIC", P["tile_nic"], "#f3c7a6"),
             ("router", P["noc_router"], "#c9e3b5"), ("pack", P["link_pack"], "#e8e2a1"),
             ("bank ECC + MBIST", B["logic_um2"], "#c9b28f"), ("control", P["control"], "#d9c8ea")]
    lower_area = sum(a for _, a, _ in lower)
    tile_w = bank_w
    lower_h = lower_area / tile_w
    tile_h = bank_h + lower_h
    tile_area = tile_w * tile_h
    # ---- the die ----
    nx, ny = 8, 8
    arr_w, arr_h = nx * tile_w, ny * tile_h
    br_area = R["bridge_node_placed_um2"]                  # one bridge node per row
    br_w = br_area / tile_h
    cp_area = R["control_plane_placed_um2"]
    east = [p for p in PLACEHOLDERS if p[3] == "east"]
    south = [p for p in PLACEHOLDERS if p[3] == "south"]
    east_area = sum(n * a for _, n, a, _ in east) * 1e6
    east_w = east_area / arr_h
    south_items = [(name, a * 1e6) for name, n, a, _ in south for _ in range(n)] + [("control plane", cp_area)]
    south_area = sum(a for _, a in south_items)
    core_w = arr_w + CHANNEL_UM + br_w + CHANNEL_UM + east_w
    south_h = south_area / core_w
    core_h = arr_h + CHANNEL_UM + south_h
    die_w, die_h = core_w + 2 * RING_UM, core_h + 2 * RING_UM
    die_mm2 = die_w * die_h / 1e6
    ip_mm2 = (east_area + south_area - cp_area) / 1e6

    # ---- draw (scale px per um) ----
    scale = 900.0 / die_w
    W, H = die_w * scale, die_h * scale
    SH = max(H + 60, 40 + 215 + 7 * 15 + 20 + tile_h * 270.0 / tile_w + 20)     # the die, or the legend and the inset
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W + 310:.0f}" height="{SH:.0f}" '
           f'viewBox="0 0 {W + 310:.0f} {SH:.0f}">', '<rect width="100%" height="100%" fill="#ffffff"/>',
           f'<text x="10" y="22" font-size="15" font-family="sans-serif" font-weight="bold">AstraCore Neo on the ASAP7 '
           f'proxy (sketch): {die_w / 1000:.2f} x {die_h / 1000:.2f} mm = {die_mm2:.1f} mm2 -- '
           f'{die_mm2 / N5A_DIE_MM2:.2f} x spec section 9\'s ~{N5A_DIE_MM2:.0f} mm2 N5A die (a ratio check, not a die size)</text>']
    ox, oy = 10, 40
    out.append(rect(ox, oy, W, H, "#e6e6e6"))                                      # the die and its I/O ring
    cx, cy = ox + RING_UM * scale, oy + RING_UM * scale
    out.append(rect(cx, cy, core_w * scale, core_h * scale, "#fafafa"))
    out.append(f'<text x="{ox + 4:.1f}" y="{oy + 11:.1f}" font-size="8" font-family="sans-serif">I/O ring</text>')
    for ty in range(ny):
        for tx in range(nx):
            x0, y0 = cx + tx * tile_w * scale, cy + ty * tile_h * scale
            out.append(rect(x0, y0, tile_w * scale, tile_h * scale, "#ffffff", None, sw=0.7))
            for k in range(B["lanes"]):                                            # the bank's lanes
                r, c = k // per_row, k % per_row
                lx = x0 + (ch + c * (lane_w + ch)) * scale
                ly = y0 + (gap + r * (lane_h + gap)) * scale
                out.append(rect(lx, ly, lane_w * scale, lane_h * scale, "#7f9fca", None, sw=0.15))
            lx = x0                                                                # the strip below
            for name, a, col in lower:
                w = tile_w * a / lower_area
                out.append(rect(lx, y0 + bank_h * scale, w * scale, lower_h * scale, col, None, sw=0.15))
                lx += w * scale
    bx = cx + (arr_w + CHANNEL_UM) * scale                                         # the bridge column
    for ty in range(ny):
        out.append(rect(bx, cy + ty * tile_h * scale, br_w * scale, tile_h * scale, "#f6d58e", None, sw=0.3))
    out.append(f'<text x="{bx + br_w * scale / 2:.1f}" y="{cy - 3:.1f}" font-size="7" text-anchor="middle" '
               f'font-family="sans-serif">x = 8</text>')
    ex = bx + (br_w + CHANNEL_UM) * scale                                          # the LPDDR5X column
    yy = cy
    for name, n, a, _ in east:
        h = a * 1e6 / east_w
        for k in range(n):
            out.append(rect(ex, yy, east_w * scale, h * scale, "#e3a6a6", f"LPDDR5X {k}", 8))
            yy += h * scale
    sx, sy = cx, cy + (arr_h + CHANNEL_UM) * scale                                 # the south row
    for name, a in south_items:
        w = a / south_h
        short = name.split(" (")[0].replace(" controller + PHY", "")
        out.append(rect(sx, sy, w * scale, south_h * scale, "#c7e0e0" if name == "control plane" else "#d6d6d6",
                        short if w * scale > 45 else None, 8))
        sx += w * scale
    lx = ox + W + 15                                                               # legend and numbers
    leg = [("#7f9fca", "bank: 16 lane macros (2 MB + ECC)"), ("#b7d7f0", "32x32 core"), ("#f3c7a6", "NIC (2 macros)"),
           ("#c9e3b5", "router"), ("#e8e2a1", "packer / unpacker"), ("#c9b28f", "bank ECC + MBIST"),
           ("#d9c8ea", "DMA x 2, program memory, host_if, ESM"), ("#f6d58e", "bridge column (x = 8)"),
           ("#e3a6a6", "LPDDR5X controller + PHY"), ("#d6d6d6", "licensed IP placeholders"), ("#c7e0e0", "control plane")]
    for k, (col, txt) in enumerate(leg):
        out.append(rect(lx, oy + 6 + k * 17, 11, 11, col))
        out.append(f'<text x="{lx + 16:.1f}" y="{oy + 15 + k * 17:.1f}" font-size="10" font-family="sans-serif">{txt}</text>')
    nums = [f"tile {tile_w:,.0f} x {tile_h:,.0f} um = {tile_area / 1e6:.3f} mm2",
            f"(routed estimate, cells + macros: {R['tile']['estimate_um2'] / 1e6:.3f} mm2)",
            f"tile array {arr_w / 1000:.2f} x {arr_h / 1000:.2f} mm = {arr_w * arr_h / 1e6:.1f} mm2",
            f"bridge column {br_w:,.0f} um wide", f"IP placeholders {ip_mm2:.1f} mm2 (assumed)",
            f"I/O ring {RING_UM:.0f} um, channels {CHANNEL_UM:.0f} um", f"die {die_mm2:.1f} mm2 on the proxy"]
    for k, t in enumerate(nums):
        out.append(f'<text x="{lx:.1f}" y="{oy + 215 + k * 15:.1f}" font-size="10" font-family="sans-serif">{t}</text>')
    # one tile enlarged, labelled (the same drawing as in the array)
    iy = oy + 215 + len(nums) * 15 + 20
    s2 = 270.0 / tile_w
    out.append(f'<text x="{lx:.1f}" y="{iy - 6:.1f}" font-size="10" font-family="sans-serif" font-weight="bold">'
               f'one tile, {tile_w:,.0f} x {tile_h:,.0f} um</text>')
    out.append(rect(lx, iy, tile_w * s2, tile_h * s2, "#ffffff", None, sw=0.8))
    for k in range(B["lanes"]):
        r, c = k // per_row, k % per_row
        out.append(rect(lx + (ch + c * (lane_w + ch)) * s2, iy + (gap + r * (lane_h + gap)) * s2, lane_w * s2, lane_h * s2,
                        "#7f9fca", f"lane {k}", 7, sw=0.3))
    x = lx
    for name, a, col in lower:
        w = tile_w * a / lower_area
        out.append(rect(x, iy + bank_h * s2, w * s2, lower_h * s2, col, name if w * s2 > 34 else None, 7, sw=0.3))
        x += w * s2
    out.append("</svg>")
    with open(args.out, "w") as f:
        f.write("\n".join(out) + "\n")
    summary = dict(tile_um=[round(tile_w, 1), round(tile_h, 1)], tile_mm2=round(tile_area / 1e6, 4),
                   tile_estimate_mm2=round(R["tile"]["estimate_um2"] / 1e6, 4),
                   bank_um=[round(bank_w, 1), round(bank_h, 1)], lower_strip_um=round(lower_h, 1),
                   tile_array_mm=[round(arr_w / 1000, 3), round(arr_h / 1000, 3)],
                   tile_array_mm2=round(arr_w * arr_h / 1e6, 2), bridge_column_um=round(br_w, 1),
                   control_plane_um2=round(cp_area), ip_placeholders_mm2=round(ip_mm2, 2),
                   core_mm=[round(core_w / 1000, 3), round(core_h / 1000, 3)], die_mm=[round(die_w / 1000, 3), round(die_h / 1000, 3)],
                   die_mm2=round(die_mm2, 1), ratio_to_n5a=round(die_mm2 / N5A_DIE_MM2, 3),
                   tiles_share=round(arr_w * arr_h / 1e6 / die_mm2, 3))
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
