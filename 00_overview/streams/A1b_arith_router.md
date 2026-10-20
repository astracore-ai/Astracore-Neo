# Stream A1b -- the arithmetic and the router: requant in three stages, the ABFT sums in two, one-hot arbitration with
# parity at FIFO entry, the packer's output registered, and the static-configuration constraints

Branch `a1b-arith-router`. Read COMMON.md first. Files: `01_rtl/requant.sv`, `01_rtl/abft_checker.sv` (its users
`neo_mac_core.sv`, `acc_bank.sv`), `01_rtl/noc_router.sv`, `01_rtl/link_pack.sv` (link_packer only), `07_ci/synth/synth.py`
(the SDC templates), tests under `02_dv/`. Do not touch `tile_nic.sv`, `crc16_*.sv`, `act_feeder.sv`, `wbuf_mem.sv`,
`core_seq.sv` or `neo_mem.sv`: those belong to the other A1 streams running at the same time (A1a: the NIC and the CRC;
A1c: the buffer reads and the sequencer). Everything you need is reachable without them.

## Why (drop 0.40 synthesis, ASAP7 SS, 500 ps budget; `09_synthesis/synth_report_drop040_sandbox.md`)

The array's own limit is the PE's product stage, 1,031 ps (`mac_pe`, `w_q -> prod_q`). Everything in this stream is slower
than the array for no architectural reason:

| Unit | Min period | Worst path | What it is |
|---|---|---|---|
| `requant` | 3,039 ps | `sh1[113]_reg -> out_q[176]_reg` 2,966 ps | stage 2: `(prod + bias) >>> sh`, `+ zp`, relu, saturate, all in one cycle; the 32 x 16 product of stage 1 is 1.85 ns itself |
| `neo_mac_core` | 1,505 ps | `y[962]_reg -> abft_err` 1,447 ps | `abft_checker`: a 33-operand 32-bit sum and compare, combinational from the output registers |
| `acc_bank` | 1,732 ps | `u_mem -> abft_err` 1,644 ps | the same checker behind the leaf's registered drain read |
| `noc_router` | 2,571 ps | `rr[12]_reg -> count[5]_reg` 2,496 ps | the arbitration: `(rr + 1 + k) % 5` for k = 0..4 in a priority chain, behind `head_bad = ^head` (a 1,077-bit parity reduce on the FIFO head), then `pop` and the count |
| `link_packer` | -- (WNS -681) | `core_flit[520] -> link_flit[100]` 1,041 ps | combinational input-to-output: `same`/`fits` compares select `emit_pend`, which muxes the output and the parity `^pend_body` over 1,077 bits |
| `act_feeder`, `core_seq`, `neo_core` | 3,430 ps at the core | `cfg_s[2] -> ...` 3,005 ps, `cfg_m[3] -> rd_idx` 1,982 ps | descriptor fields that are static for a whole run, constrained today as ordinary inputs with 0.2 x period of input delay |

Note that `neo_core` holds TWO `requant` instances (`u_rq`, `u_rq_dup`, R10 lockstep) -- 2 x 192,872 cells, 17 % of the
tile's cells; you make the unit faster, not smaller (the duplication is a later item), but report its cells.

## 1. `requant`: three stages, bit-identical results

Same module name, ports and parameters; the only visible change is the latency, 2 -> 3 cycles from `in_valid` to
`out_valid` (the header comment says so). Function to preserve exactly: `q = sat8(round_half_up((acc * mult) >> shift) + zp)`,
relu as `max(v, zp)` in the quantized domain, with `bias = 2^(shift-1)` (0 when shift = 0) added before the arithmetic
right shift, and the table parity check unchanged (it is on the table read, not in the pipeline).

- Stage 1: the two partial products `acc x mult[7:0]` and `acc x mult[15:8]` (signed 32 x unsigned 8 each) registered,
  with the column's shift and zp travelling alongside.
- Stage 2: `sum = pA + (pB << 8) + bias` -- a three-operand add (Yosys gives a carry-save row and one carry-propagate
  adder); registered with shift and zp.
- Stage 3: `r = sum >>> shift; v = r + zp; relu; saturate -> out_q`. If this stage is the new limit, split the saturation:
  compute `lo = r[7:0] + zp` (9 bits) in parallel with the test of the high bits of `r` (all zeros / all ones) and the
  carry, instead of a 49-bit add followed by two 49-bit compares.
- `out_valid` through the three stages; reset as today.
- Expected: `requant` unit min period at or under the PE's (~1,000 ps; report the exact figure and the worst path),
  cells near today's 192,872 (the extra stage adds ~2 x 49 x 32 flops).
- The latency change moves everything downstream by one cycle: `neo_core`'s `rq_valid`/`rq_q` and the lockstep compare
  (`rq_valid_d` moves with it -- both instances change together), the C driver / neosim expectations, cocotb end times.
  Find every place that assumes two cycles: `grep -rn` for the requant latency in `02_dv/`, `03_models/`, `04_software/`
  (`test_neo_core.py` line ~136 waits 4 cycles "for the requant pipeline"; `sim/run_requant.py` checks the stream; the
  neosim tile tests read `rq_q`), and report the new end times (COMMON.md's baselines).

## 2. `abft_checker`: the sum in two stages

Keep the module and its ports, add the pipeline: stage 1 registers four partial sums of eight columns plus `y_chk` and
`valid`; stage 2 adds the four, compares, and registers `err`. `err` therefore asserts two cycles after the row's `valid`
(and `err_sticky` one cycle after that); state the alignment in the header. Add an output `err_valid` (the delayed
`valid`) so a consumer can pair `err` with its row, and route it out of `neo_mac_core`, `acc_bank`, `neo_mac_core_v02`
and `neo_core` as new output ports beside the existing `*_abft_err` (the tile ties them to its `unused_tile_bits`; lint).
Then fix the tests that sample the per-row flag in the row's own cycle: `02_dv/tb/tb_neo_mac_core.sv` (`err_rows`,
`err_at_row[out_row]`), `02_dv/tb/tb_neo_core.sv` (`acc_flags`), neosim `sim/run_conv_core.py` and `sim/run_conv_v02.py`
(`acc_abft_err` per drained row, C4), and whatever cocotb test reads `*_abft_err` rather than the sticky -- they sample on
`err_valid` now and attribute the flag to the row two cycles earlier. The sticky flags keep their meaning; the fault-
injection tests (the array fault at FAULT_ROW/FAULT_COL, the accumulator bit flip at drain) must still find exactly the
rows they find today. Expected: `neo_mac_core` and `acc_bank` min periods set by something else (report what).

## 3. `noc_router`: one-hot rotating arbitration, parity at FIFO entry

- Parity: compute `^in_flit[i]` when the flit is pushed and store the bit with the entry (`fbad[i][2]`); `head_bad[i]`
  becomes a 2:1 mux. The reduce moves to the input side, where the link budget is 0.6 of the period (TILE_SDC's `link`
  input delay is 0.4) -- a 1,077-bit XOR tree is ~10 levels; report its arrival on the input path.
- Arbitration: per output o a 5-bit request vector `rq[o][i] = head_valid[i] && !head_bad[i] && (req[i] == o)`, the
  rotating pointer as a one-hot `ptr[o]` (5 bits, the input after the last grant), the standard double-vector first-set:
  `masked = rq & ~(bits at and below the last grant)`, grant = first set bit of `masked` if any, else first set bit of
  `rq`; the grant is one-hot (`gnt[o][i]`), `fire[o] = |gnt[o] && (!out_valid[o] || out_ready[o])`,
  `pop[i] = drop[i] || |(gnt[*][i] & fire[*])`, the head mux `out_flit[o] <= head[one-hot select]` as an AND-OR. No `%`,
  no 3-bit arithmetic in the loop. Reset the pointer to the state that gives today's first-grant order (today `rr = 4`
  -> input 0 has first priority): the arbitration order must stay identical cycle for cycle so that every mesh test's
  end time is unchanged (cocotb tile 46,282.52 ns, mesh8 117,755.01 ns) -- if an end time moves, your order differs.
- `req[i]`'s compares (`dx > my_x` ...) stay; `my_x`/`my_y` are straps (false paths in TILE_SDC).
- Expected: `noc_router` min period at or under ~1,000 ps with the next path named; cells near 108,904 (the parity bits
  add 10 flops).
- Tests: neosim `sim/run_noc.py`, `make verilator` (`tb_tile_mesh.sv`), cocotb tile (M-series), the PR's hdl-mesh8.

## 4. `link_packer`: the link side registered

Add an output register stage on the link side of `g_merge` (WPF >= BW): `link_valid`/`link_flit` become registers,
loaded when the stage is empty or draining (`!link_valid || link_ready`); the internal ready that `emit_pend` and
`core_ready` see is that condition instead of `link_ready` itself. Throughput must be unchanged -- a 32-word flit leaves
every second cycle at 16 words per beat, back-to-back flits one per cycle -- and the only visible change is one cycle of
latency on the egress (every tile test's end time moves by the flits in its critical chain: report the new figures;
the mesh8 end time moves too). The parity `^pend_body` is then inside the cycle (registers -> XOR tree -> register);
if it is still the long path, keep a running parity updated as words join `pend_vec`. `g_split` (WPF < BW, the small
builds) may keep its combinational form if it meets timing at the silicon parameters -- it is not instantiated there
(WPF = 32, BW = 16), so leave it unchanged. Expected: `link_packer` WNS positive at the unit level, reg-to-reg path
named.

## 5. The static-configuration constraints (synth.py)

In `BLOCK_SDC` the descriptor inputs get the same 0.2 x period input delay as a handshake pin, so `cfg_*` paths dominate
three units (`act_feeder` 3,005 ps from `cfg_s[2]`, `core_seq` 1,982 ps from `cfg_m[3]`, and through them `neo_core`).
By protocol these are written before `go` and hold for the whole run (the sequencer takes several cycles from `go` to the
first `f_start`; the DMA and the host write them only between runs). Make `write_sdc` emit, for a unit whose port list
(`nl.ports(nl.top)`) contains them, `set_multicycle_path 4 -setup -from [get_ports {cfg_* ...}]` and
`set_multicycle_path 3 -hold ...` for the exact names present (the `cfg_*` ports; NOT `relu` / `tbl_*` of `requant` nor
`rq_tbl_*` / `rq_relu` of `neo_core` -- the tables are written by DMA during a layer, leave them), with the protocol stated in
the SDC comment, and in `TILE_SDC` the same through the child instances' pins (`-through [get_pins -hierarchical
*cfg_*]` or per-instance `u_core/cfg_*`; the children are ETMs, their ports exist as instance pins -- check OpenSTA's
syntax on the tile netlist). Only emit a constraint whose port list is non-empty (OpenSTA warns on an empty collection
and some commands error). Validate with `--sta-only`: `/home/claude/work/flat37/synth_final` holds drop 0.40's complete
unit outputs (netlists, `etm.lib`, SDCs) -- copy the directory (not symlink; ~1 GB) and run
`python3 synth/synth.py --rtl rtl --blocks act_feeder,core_seq,neo_core,neo_tile --out <copy> --sta-only --jobs 1` to
re-time those units with the new SDC in seconds without re-synthesizing (heavy units untouched). Report the before/after
min periods of `act_feeder`, `core_seq`, `neo_core`, `neo_tile` under the new constraints; the remaining `cfg` paths that
are real logic (the feeder's address arithmetic) are A1c's.

## 6. Verification and report

Local (COMMON.md's limits; A1a and A1c share the machine): `make lint` 0 warnings; `make iverilog_core`; `make verilator`;
neosim `sim/run_requant.py`, `sim/run_noc.py`, `sim/run_mac_core.py --vectors ...`, `sim/run_conv_core.py`,
`sim/run_conv_v02.py`, `sim/run_tile_test.py M3`; cocotb `make -j1 -C dv/cocotb core` and `tile` (new end times are
expected from §1 and §4 -- explain each shift in cycles); unit synthesis of `requant` (278 s, ~1 GB), `abft_checker` is
inside `neo_mac_core` (12 s) and `acc_bank` (15 s), `link_packer` (20 s); `noc_router` is heavy (4.7 GB, 8 min) -- leave
it to the PR's synth job unless the machine is otherwise idle (check with `ps`). Then the PR; from its synth job report
(`synth_report.md` on `ci-logs-synth` or the job log) the unit rows for `requant`, `neo_mac_core`, `acc_bank`,
`noc_router`, `link_packer`, `act_feeder`, `core_seq`, `neo_core`, `neo_tile`: min period, worst path, cells, flops. The
tile's cocotb end times before/after with the cycle accounting. The PR number.
