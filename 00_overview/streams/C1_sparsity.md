# Stream C1 -- the 2:4 structured-sparsity engine: PE index path, four-byte row buses, check columns per index, the
# compressed weight format, the descriptor bit, the model, the compiler's validator and packer, the tests

Branch `c1-sparsity`. Read COMMON.md first, then `00_overview/ROADMAP.md` §2 (the design as planned -- this brief makes it
concrete and changes two of its choices, below), `00_overview/spec_sheet_v2_1.md` §3 (the core), `01_rtl/mac_pe.sv`,
`systolic_array.sv`, `skew_in.sv`, `neo_mac_core.sv`, `act_feeder.sv` (after A1c: incremental addresses, the pair word read
on port B), `wbuf_mem.sv` (the entry layout and SECDED lanes), `core_seq.sv`, `neo_core.sv`, `host_if.sv` (the descriptor
registers), `03_models/model/systolic_ref.py` (the golden array model), `03_models/compiler/neo_compile.py` /
`neo_backend.py` (`direct_conv`, `pack_words`, the activation layout, the perf model), the tests `02_dv/tb/tb_neo_mac_core.sv`
(T-series), `02_dv/sim/run_conv_core.py` (C-series), `02_dv/dv/cocotb/test_tile_mesh.py` (M-series) and the coverage bins.
Streams A2a (link_pack / tile_nic / noc_router / synth.py), B1 (new top-level files) and A2b (`07_ci/pnr/`) run beside you;
your files are the core's datapath and its model / compiler / tests. If a change outside them is unavoidable (a descriptor
bit in `host_if.sv`, the DMA's fetch lengths), keep it minimal and name it in the report.

## The design (decisions made; implement as written, and say precisely why if something does not hold)

1. **Mapping.** 2:4 along the input-channel (K) dimension: in every group of four consecutive input channels two weights are
   nonzero. A group maps to a *PE pair*, rows 2g and 2g + 1; each PE of the pair holds one nonzero weight and a 2-bit index
   (which of the four channels it multiplies). The 32-row array therefore covers 64 input channels per pass in sparse mode
   with the same 1,024 multipliers; dense mode is today's behaviour (every index 0).
2. **Row buses of four bytes, in both modes** (this replaces the roadmap's cross-row wire, which the row skew makes
   unworkable: row 2g + 1's data passes PE_LAT cycles after row 2g's). `skew_in` and the PE's x path carry 4 x XW bits per
   row; each PE selects its byte by its index (a 4:1 byte mux before the multiplier -- the only logic added to the product
   stage; report its cost on the PE's 1,031 ps stage). Sparse: rows 2g and 2g + 1 both receive channels 4g .. 4g + 3 of the
   64-channel tile (the feeder presents ROWS / 2 groups of four bytes; `skew_in` feeds each group to both rows of its pair
   with their own skews). Dense: byte 0 of row r is channel r as today, bytes 1..3 are zero. The x shift registers grow from
   8 to 32 bits per PE (~25 k flops on the 32 x 32 array, ~2 % of its area -- the measured figure goes in the report; if the
   synthesis shows it matters, a dense-only build may keep an 8-bit path through a parameter, but the silicon configuration
   carries the 32-bit bus).
3. **The check columns** (this replaces the roadmap's "two check columns with fixed indices A / B"): NCHK = 4 check columns
   with hard-wired indices 0..3, each a standard PE whose weight is the row's check weight for that index -- the sum over the
   data columns of the weights whose index equals k (a compiler-side precomputation, as today's single check weight is).
   The checker compares the sum of the row's data columns against the sum of the four check outputs. Dense mode: column 0
   holds today's check weights, columns 1..3 hold zeros. NCHK is a parameter (1 = today's array, 4 = the sparse-capable
   silicon core); the dense tests at NCHK = 1 must be bit-identical to today and the dense tests at NCHK = 4 must give the
   same results with the end times unchanged. Coverage argument for the FMEDA: in sparse mode a single-point fault in any
   data PE, its stored weight or index, or the psum chain of one column still flips exactly one side of the equality.
4. **Weights.** The shadow chain carries WW + 2 bits per column (the index shifts down the column with the weight); the
   weight-buffer entry per row becomes COLS x (WW + 2) + NCHK x WCW bits (one format for both modes; dense stores index 0 and
   three zero check weights), the SECDED lanes simply more numerous; the bank holds the same format, so `fetch_weights`
   lengths follow the new words-per-entry (`neo_backend.py`'s `pack_words`, the DMA's `len` field). Report the new entry
   width and the weight-buffer macro's new size in `09_synthesis/memories.md`.
5. **Activations.** Sparse channel tiles are 64 channels: a pixel's 64 channels occupy one pair word of the activation buffer
   (entry 2k channels 0..31, entry 2k + 1 channels 32..63 -- the compiler's activation layout for sparse layers), and the
   feeder presents the whole pair word per cycle regrouped as ROWS / 2 groups of four bytes; dense layers keep today's layout
   and read. The feeder's address generator is A1c's (incremental; the pair word is already what port B returns): the sparse
   difference is which bytes go to which row bus, and `cfg_tile_pixels` / the region arithmetic in pair units for sparse.
6. **Control.** A `sparse` bit in the layer descriptor (a flags register in `host_if.sv` beside `rq_relu`, routed to the
   core); the sequencer counts channel tiles of 64 in sparse mode (`cfg_ct_n` is the compiler's count, in the mode's units
   -- state the convention); the DMA instruction set does not change.
7. **Model and compiler.** `systolic_ref.py` in sparse mode (indices, the four check columns); ground truth stays dense
   arithmetic on the pruned weights (`direct_conv` with the 2:4 weight tensor -- pruning is training-side); `neo_backend.py`:
   a 2:4 validator (rejects a tensor that is not 2:4 along K for any output channel and group), the compressor (weights +
   indices per PE row-pair, the four check weights per row), `pack_words` for the new entry format, the sparse activation
   layout, the descriptor bit, the perf model's K-pass count halved for sparse layers; the C driver (`04_software`) only if it
   builds descriptors itself.
8. **Tests** (each fails without the mechanism it tests): T6 streaming core sparse dataflow bit-exact against the model at
   32 x 32 and 16 x 8; T7 a PE fault under sparse mode flagged by the ABFT (every check column exercised: faults in data
   columns whose index is 0, 1, 2 and 3); C11 a whole convolution on `neo_core` in sparse mode through the compiler's
   descriptor and layouts, plus the table of dense tests C1--C10 unchanged; M20 a sparse program through the DMA and the
   compiler on the tile (cocotb, 16 x 8, and neosim if it fits); functional-coverage bins for the mode and the index values;
   the dense suites' results and end times unchanged (COMMON.md's baselines as A1 moved them -- read them from main's last
   green run). Unit synthesis of `mac_pe`, `mac_pe_chk`, `systolic_array`, `neo_mac_core`, `act_feeder`, `wbuf_mem` at the
   silicon parameters with NCHK = 4: the area and timing delta of the index path, the wide bus and the extra columns (the
   `synth` job's report on the PR; locally the small units).

Deliver in two PRs if the work is long: PR 1 the datapath (items 1--4, 7's model, T6 / T7, the dense suites green), PR 2
the feeder / sequencer / descriptor / compiler / C11 / M20 -- or one PR if it fits a day. Each PR green on every job
(`formal` included: the lockstep_core_seq harness lists the sequencer's registers -- if you add one, add it there too;
`abft_checker_fv` pins the checker's interface -- extend it with NCHK if you change the module's ports).

## Verification and report

Local: `make lint` 0 warnings; `make verilator` (T6 / T7 added to `tb_neo_mac_core.sv`'s run); `make iverilog_core`; neosim
`run_mac_core.py`, `run_conv_core.py` (C11 added), `run_ksplit.py`; cocotb `core` and `tile` (M20 added) under COMMON.md's
memory rules; the unit syntheses above. Then the PR(s) (`gh pr create` or `gh api -X POST repos/astracore-ai/Astracore-Neo/pulls`;
runs via `gh run list --branch c1-sparsity`), and the report: the design as built (bus, index path, check columns, formats,
layouts, the descriptor convention), the synthesis delta table, the test list with what each proves, the dense baselines
confirmed, the FMEDA coverage argument, anything unresolved, the PR number(s).
