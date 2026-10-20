# Stream A1a -- the NIC: a parallel beat CRC and the partial-sum row assembly as a leaf

Branch `a1a-nic-crc-prow`. Read COMMON.md first. Files: `01_rtl/crc16_beat.sv`, `01_rtl/crc16_word.sv` (stays),
`01_rtl/tile_nic.sv`, `01_rtl/neo_mem.sv` (the leaves; do not change their interface), `09_synthesis/memories.md` (add the
new instance row), tests under `02_dv/`.

## Why

Drop 0.40's synthesis (ASAP7 SS, 500 ps budget; `09_synthesis/synth_report_drop040_sandbox.md`): `tile_nic`'s minimum period
is 7,662 ps, the path `drain_int8_reg -> tx_crc[*]_reg` -- the transmit beat's 16 words through `crc16_beat`'s 16 chained
`crc16_word` stages (512 serial XOR steps) in one cycle. The same chain sits behind every input the beat takes (the bank's
read data into the NIC is the tile's worst path at 5.9 ns over; the receive side's `crc_acc[*]` is 5.5 ns over; `crc16_beat`
alone is 4.7 ns of logic). And 65,536 of the NIC's 71,999 flops are `prow` -- `logic [COLS*PW-1:0] prow [NSRC]`, the
partial-sum row under assembly per source (64 sources x 1,024 bits), i.e. ~20 k um2 of the NIC's 63 k um2.

## 1. `crc16_beat` in matrix form, bit-identical, same ports

Keep the module name and ports (`crc_in[15:0]`, `words[32*BW-1:0]`, `n[CW-1:0]`, `crc_out[15:0]`, parameters BW, CW) so
both instances in `tile_nic` (`u_rxcrc`, the transmit one) and any other user pick it up unchanged. The CRC step is linear
over GF(2) with no constant term, so:

- T = CRC over all BW words with the words at positions >= n forced to zero: every bit of T is the XOR of a fixed subset of
  the 16 + 32*BW input bits. Generate the subsets with a Python script (`02_dv/sim/gen_crc16_beat.py`, committed): simulate
  the existing serial algorithm (`crc16_word`: polynomial 0x1021, MSB first, 16-bit state, 32 bits per word, no init
  constant -- read the module) on each unit input vector; emit a SystemVerilog include or module with the subsets as
  localparam bit vectors, used as `assign t[i] = ^(ROW_i & {crc_in, words_masked})` (a reduction XOR synthesizes to a
  balanced tree; depth ~log2(528)).
- Processing a zero word multiplies the state by a fixed 16x16 matrix A32 (the state transform of one all-zero word), so
  T = A32^(BW-n) * CRC_n, hence CRC_n = (A32^(BW-n))^-1 * T. Precompute the 17 inverse matrices (n = 0..BW; n = BW is the
  identity, n = 0 must return crc_in exactly as today) with GF(2) Gaussian elimination in the generator; in RTL select the
  matrix by `n` and apply it: `crc_out[i] = ^(MINV[n][i] & t)`. Generated constants go in the SV as localparams (a `case`
  on n or a 2-D localparam array -- whatever every simulator in COMMON.md accepts; neosim needs plain constructs).
- Mask the words: `words_m[32*k +: 32] = (k < n) ? words[32*k +: 32] : '0`.
- Prove equivalence before anything else: a self-checking testbench (`02_dv/tb/tb_crc16_beat.sv`, run under Verilator and
  Icarus 14 by a new `make crc16` target; also a neosim check if feasible) that instantiates the OLD implementation (keep a
  copy as `crc16_beat_serial` inside the testbench file or `02_dv/tb/`) and the new one and compares on >= 20,000 random
  (crc_in, words, n) including n = 0, n = BW, BW = 16 and BW = 1 and BW = 4 parameterizations. Add the target to `make
  verilator` in `07_ci/Makefile` (hdl-sim runs it then).
- Expected result: `crc16_beat` unit synthesis (`--blocks crc16_beat`) under ~800 ps combinational instead of 4,723; cells
  roughly 4-6 k instead of 3,275 (report the numbers); `tile_nic` unit min period well under 2 ns, with the next path named.

## 2. `prow` as a `neo_mem_rf` leaf

`prow` is written on T_PSUM beats by column groups (`prow[s][PW*j +: PW]` for j in [ps_col0, ps_end)) and read whole at
`prow[rx_src]` in the cycle the check word arrives (`efifo_y` assembly, line ~434). Replace it by one
`neo_mem_rf #(.W(COLS*PW), .D(NSRC), .G(COLS), .RB(0), .DV_HOOKS(0))`: a write group per column (`we[j]` = the beat covers
column j, on `rx_fire && rx_type == T_PSUM`, write address `rx_src`), write data the beat's words placed at their column
positions (the per-group mux you have today, as a flat vector), the asynchronous read port A at `rx_src` feeding the
`efifo_y` assembly exactly where `prow[rx_src]` is read now. The leaf has no reset: argue in the header comment why none is
needed (the row's earlier columns are always written by earlier beats before the check word reads them; if you find a case
where a column is read without having been written -- a PSUM row shorter than COLS -- keep the read legal by masking, and
say so) and prove it with the K-split / partial-sum tests (neosim `run_tile_test.py M3 M9 M13` as applicable, cocotb tile
m9 / m12 / m13, mesh8 in CI). Add the row to `09_synthesis/memories.md` (`tile_nic.u_prow`, 64 x 1,024, G = 32, async A)
and the macro appears in the synth report automatically (the flow black-boxes `neo_mem_rf`).

## 3. Verification and report

Local: `make lint` (0 warnings), the crc16 equivalence testbench, `make iverilog_core`, `make -j1 -C dv/cocotb tile` (17+1
skipped; the end time 46,282.52 ns must be unchanged -- neither change adds latency), neosim `sim/run_nic.py` (I1-I12) and
`sim/run_tile_test.py M3`; synthesis of `crc16_beat` and `tile_nic` (`tile_nic` is heavy -- 4.7 GB, 13 min -- run it alone,
nothing else running, or leave it to the PR's synth job). Then the PR; the PR's synth job report (`synth_report.md` on
`ci-logs-synth`, or the job log) gives the tile-level numbers: report the new `tile_nic` min period and worst path, the NIC's
cells/area/flops, the new macro's area, and the tile total. Do not change `crc16_word`, the leaves, or anything outside the
NIC and the CRC; the register-read changes to the drain FIFO belong to another stream.
