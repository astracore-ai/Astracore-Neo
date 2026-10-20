# The proxy figures against the spec's N5A claims (drop 0.44, stream A4)

What the synthesis, place-and-route and power figures of drops 0.40-0.44 mean against spec §9's N5A claims -- 2.0 GHz
worst case, ~210 mm², ~56 W typical -- and how a foundry library is plugged into the same flow so that the proxy's
columns become the target's. The integrator rewrites spec §9 from this page. Sources: `synth_report_drop043.md` and
`synth_report_drop044.md` (the ASAP7 SS and Nangate45 TT runs, the period sweep), `pnr_drop043.md` / `pnr_drop044.md`
(the routed blocks), `power_drop043.md` / `power_drop044.md` (power from recorded activity), `floorplan/README.md` (the
die sketch), `memories.md` (the macro model), `03_models/model/neo_ip_ppa.py` (the spec's own first-order model).

## 1. The two sets of figures are not the same measurement

| | the spec's N5A claims (§9) | the proxy (drops 0.40-0.44) |
| --- | --- | --- |
| library | a foundry 5 nm automotive library, 0.75 V, worst case (SS, 125 °C, aged) -- assumed, no data in the repository | ASAP7 7.5-track RVT, a predictive academic 7 nm library at its SS corner, 0.63 V / 100 °C (Nangate45 TT as a second opinion) |
| memories | compiled macros at N5 high-density bit cells (0.021 µm²/bit × 1.4 for periphery, ECC and redundancy: `neo_ip_ppa.py`) | FakeRAM2.0's 7 nm model, 0.042 µm²/bit single-port, priced 1.5 × for the two-port arrays the leaves are (`memories.md`, drop 0.44); the LEFs the routed blocks use are the same single-port bit cell whatever the ports |
| method | a feasibility model: pJ per MAC, µm² per MAC, W per mm² of leakage, a die budget -- first order, ±30 % | the RTL synthesized unit by unit, timed with extracted models, swept over periods; 18 blocks placed and routed; power from the cocotb runs' recorded activity on the routed databases |
| what it says | the product's targets | the design's structure on an open library: which path sets the period, what the area is made of, what the activity does to the power |

So the proxy does not confirm or refute 2.0 GHz, 210 mm² or 56 W: it measures the design on a different library with a
different macro model. What carries over are the ratios the design fixes; what does not are the absolute picoseconds,
square microns and milliwatts, which the library sets.

## 2. The ratios that are the design's (carry over to any library)

**Timing: what sets the period.** The tile's period on the proxy is the larger of two things: the grid step at which
every unit outside the PE-limited ones is clean (1,250 ps since drop 0.43, on the merged tree again since 0.44), and the
PE's own product stage (1,095 ps in CI's run 211; 1,116 on this drop's macOS build of the same Yosys). The product stage
is the design's deepest single-cycle path: the one-hot byte select, the 8 × 8 Booth multiplier with its carry-save
tree, into the 32-bit product register -- 20 cell levels on ASAP7 between `x_in` and `prod_q` (4 full adders, 2 AO221,
2 AO21, the select's AND-OR, inverters). On any library the tile's clock is bounded by that stage; the other units are
at or under it by the margins the sweep table gives (`synth_report_drop044.md`). The ratio between the two libraries
measured -- Nangate45 TT's PE at 1,151 ps against ASAP7 SS's 1,095, 1.05 -- shows how little the stage depends on the
library's corner once the structure is fixed; the mapping ASAP7 SS → N5A SS is the one number the repository does not
have.

**Area: the shares.** The tile on ASAP7 (run 211, the two-port pricing of drop 0.44): 1.764 mm² = 0.310 mm² of cells
(2.43 M cells, 273 k flops) + 1.454 mm² of macro model -- the 2 MB bank 1.290 mm² (73 %), the core's three arrays 0.097,
the NIC's two 0.039, the vector unit's leaves 0.028. Of the cells, the 1,024 data PEs are 0.163 mm² (53 %), the vector
unit 0.035, the NIC 0.020, the check PEs 0.025, the requantizer pair 0.024, the accumulator 0.025 with its macro, the
router 0.014. The spec's model has the same shape: its tile is 0.62 mm² with 0.49 mm² of bank (79 %). The proxy's cells
are 2.6 × the model's logic at the same shares; its bank, now priced two-port, is 2.6 × the model's bank (0.021 × 1.4
µm²/bit at N5 against 0.042 × 1.5 on the proxy, 2.1 ×, the ECC lanes and the MBIST on top). The routed / synthesized
cell-area ratios (1.03-1.12 for the large logic blocks, up to 1.33 for the pin-bound small ones, `pnr_drop043.md`
section 1) and the utilizations the blocks route at (45-60 %) are flow properties a target library repeats within a few
percent.

**Power: the activity's effect.** At the recorded activity the routed blocks draw 0.09-0.93 of what OpenSTA's default
(0.1 transitions per input per cycle) gives them, the accumulator 1.16 (`power_drop043.md` section 2): the ratios are the
workload's. Energy per data MAC: 0.86 pJ in the PE alone, 1.10 pJ in the 16 × 8 array (the check columns and the clock
tree included), 1.53 pJ in the 16 × 8 core (feeder, accumulator, requantizers, sequencers), ~1.0 pJ in the 32 × 32 core
(estimate: eight times the data PEs over the same support) -- at 800 MHz, 0.63 V SS, 56.7 % MAC utilization. The spec's
0.45 pJ/MAC is a system-level figure at 0.75 V on N5A: the 2.2 × between the two is the library, the voltage and the
corner together, which the proxy cannot separate. What the proxy does fix: the clock tree is 29 % of the array's
dynamic power (18.6 of 63.7 mW) and 30 % of the core's; the macros' clock-pin power is the bank's power at any activity
(36.5 of 37.6 mW -- FakeRAM2.0 models no read or write energy); the tile's units outside the core are 92 mW against the
core's ~485 (16 %).

## 3. The absolute figures that are the library's (do not carry over)

| figure | proxy (ASAP7 SS 0.63 V / 100 °C) | spec N5A | ratio spec / proxy | what the ratio is |
| --- | --- | --- | --- | --- |
| the tile's clock | 800 MHz (1,250 ps: the sweep's grid; the PE's stage 1,095-1,116 ps) | 2.0 GHz worst case | 2.5 | the library's speed at its corner; a predictive 7 nm RVT library at 0.63 V against a production 5 nm at 0.75 V |
| the tile | 1.764 mm² synthesized (two-port macros), 1.233 mm² routed estimate at the 0.43 pricing, 1.638 mm² as placed with the bank's packed lanes | 0.62 mm² (the model) | 0.35 | bit-cell density and cell size: the proxy's bank is 2.6 × the model's, its logic 2.6 × |
| the die | 152 mm² on the sketch: 64 tiles 104.8 mm², the licensed IP at its model sizes 38 mm² | ~210 mm² | 1.4 | not a confirmation: the sketch's tiles are 2.6 × the model's, its uncore is the model's own placeholders (`floorplan/README.md`) |
| the tiles' power | 64 × 575 mW = 36.8 W at 800 MHz (the core at 56.7 %, the other units at 32.8 %; no inter-block wires, no chip clock) | ~56 W typical at 60 % (11 W of it uncore, 9.8 W leakage) | 1.5 | 2.5 × the frequency against (0.63 / 0.75)² of the voltage and the library's capacitance per gate; leakage 0.2 W of the tiles' 36.8 on the proxy against the model's 9.8 W for the die |
| energy per MAC | 1.10 pJ (array), ~1.0 pJ (core, estimate) | 0.45 pJ system-level | 0.4 | as the power |

The N5A column is a model in every row (spec §0's ledger); the proxy column is measured on the proxy in every row. A
ratio in this table is a statement about the libraries, not about the design, until a foundry library is run through
the same flow.

## 4. How a foundry library is plugged in

The flow is the same for any library; the four things it takes from the library are listed below with the file that
holds each on the proxy.

1. **The standard cells** -- `07_ci/synth/synth.py` `PDKS`: the liberty files per corner, the sequential library for
   `dfflibmap`, the don't-use list, ABC's driver and load, the buffer of the repeater trees, the integrated clock gate
   neo_icg maps onto (cell, clock, enable, test-enable and gated-clock pins), the flop's clock pin; the adder maps
   (`pdk/lib/common/`). A library is one more entry; `tools.sh` installs its files.
2. **The constraints** -- the unit SDCs synth.py writes from its templates (`BLOCK_SDC`: the clock and 0.2 of the period
   on every I/O, the descriptor's multicycle paths; `TILE_SDC`: the tile's link, host and status budgets; `TOP_SDC`: the
   two clocks and the crossings' exceptions, drop 0.44), delivered as `09_synthesis/constraints.sdc` and `neo_top.sdc`
   (`synth.py --write-sdc`, in nanoseconds). The period is the sweep's grid; the clock uncertainty 8 %, the transition
   limit 16 % of it. The crossings are the three structures of `08_ip_product/neo_top_integration.md` section 6 by name.
3. **The macros** -- the leaves `neo_mem_sp` / `neo_mem_rf` (`memories.md` lists every instance with its geometry and
   ports). In synthesis a compiled macro replaces the model two ways: its liberty file read by `derive_macro` (area per bit,
   access, setup / hold from the file, as Nangate45's FakeRAM45 is read today), or the area factors `MACRO_AREA_FACTOR`
   set to the compiler's two-port density. In place and route `07_ci/pnr/fakeram.py` wraps the leaf's ports around the
   macro (`wrappers.v`): a compiler's LEF / LIB take the FakeRAM pair's place in `in/macros/` with the same wrapper, pin
   names and the RBQ = 1 address readback (`neo_mem.sv`).
4. **The platform for place and route** -- `07_ci/pnr/designs/neo_common.mk` names ORFS's platform (`asap7`), the corner
   and the utilization; a block's `config.mk` its floorplan (`DIE_AREA` / `CORE_AREA` or a utilization, `macro_place.tcl`,
   the pin spacing), its own constraints (`sdc_extra.sdc`); the activity files `07_ci/pnr/activity/*.json.gz` are the
   design's and apply to any library's flops by name.

What a library run then yields, in the same tables: the sweep's verdict and the second line over the chip top's units,
the routed table with routed / synthesized, the power table at the recorded activity with the share of flops at the
default. The spec's three claims are then checked row by row of section 3 with the proxy column replaced -- the ratios of
section 2 are expected to hold within the few percent the flow's own variance shows between two builds of the same tools
(the PE's stage 1,095 on CI's Linux Yosys against 1,116 on the macOS one of this drop).

## 5. The claims as they should read after this drop

- *2.0 GHz worst case*: a model. The design's clock is set by the PE's product stage, 20 cell levels; on the ASAP7 SS proxy
  it is 1,095 ps and the tile is clean at 1,250 ps (800 MHz) with the chip top's units clean at the same grid step
  (drop 0.44); the N5A figure needs the foundry library through §4.
- *~210 mm²*: a model. The design's tile is 1.76 mm² on the proxy with two-port macro pricing (73 % the bank), 0.62 mm² in
  the model; the die sketch's 152 mm² is 64 proxy tiles plus the model's own IP placeholders and does not test the 210.
- *~56 W typical*: a model. The design's tiles draw 36.8 W on the proxy at 800 MHz and 0.63 V at the recorded utilization
  (1.10 pJ per MAC in the array), 16 % of it outside the cores; the uncore, the chip clock and the inter-block wires are
  not measured; the N5A figure needs the library and the uncore's activity.
