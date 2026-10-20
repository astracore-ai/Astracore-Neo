# Stream C2 -- transformer readiness: the vector unit on the drain path, INT4 weight storage, attention tiling, the
# compiler's transformer operators, an attention head / a ViT block / an LLM layer on the simulated mesh

Branch `c2-transformer`. Read COMMON.md first, then `00_overview/ROADMAP.md` §3 (the design as planned; this brief makes it
concrete) and §1 (C2's row), the spec §3–§6, the RTL `01_rtl/neo_core.sv` (the drain: `rd_data` INT32 rows -> requant ->
`rq_q`), `requant.sv` (three stages since 0.41; its tables and parity), `tile_nic.sv` (the drain modes INT32 / INT8 /
PSUM and the fetch path that assembles weight entries), `wbuf_mem.sv` (the entry format after C1: 32 x (8-bit weight +
2-bit index) + 4 check weights, 12 SECDED lanes), `tile_dma.sv`, `host_if.sv` (the descriptor), the compiler
`03_models/compiler/neo_compile.py` / `neo_backend.py` (after C1: `compile_group(nchk, sparse)`, `pack_words`, the
layouts), the model `03_models/model/systolic_ref.py`, the tests (`02_dv/sim/run_conv_core.py`, `run_two_layer.py`,
`02_dv/dv/cocotb/test_tile_mesh.py` M18 / M19 / M20, `test_two_layer.py`). Streams A3 (timing in the bank wrapper, the
program memory, the sequencer's one endpoint, the check PE), B2 (clock gating, telemetry registers in `host_if`, the
NIC's self-serve loopback, the router's priority) and D2 (fault campaign) run beside you: your files are the new vector
unit, the requant / drain integration in `neo_core.sv`, the weight-buffer load path for INT4, the compiler, the model and
the tests; a `host_if` register for the vector program is yours (coordinate: B2 adds telemetry registers at a fixed
offset -- take a different fixed offset and say so in the PR).

## The design

1. **The vector unit (`vec_unit.sv`)** sits between the accumulator drain (`rd_data[COLS]`, INT32 rows, one row per cycle
   with `rd_valid`) and the requantizer, per core; a row-wide pipeline that executes a small per-layer program of up to 16
   micro-ops (loaded through new host registers like the requant tables: an op word per slot with parity). Ops, each on
   the whole row (COLS lanes of INT32, saturating): `ADD_ROW` (add a row fetched from a bank -- the residual: the DMA
   fills a small row buffer of the unit, `RBUF`, from a bank address, as it fills the requant tables), `MUL_SCALE`
   (per-column INT16 scale with shift), `CLAMP`, `LUT` (a nonlinearity on INT16 after a shift: 256-entry tables with linear
   interpolation -- exp, GELU, SiLU, reciprocal, rsqrt -- loaded like the requant tables, odd parity per entry checked on
   use), `ROWMAX` / `ROWSUM` (a reduction tree over the COLS lanes, the scalar held and broadcast back for the next op:
   subtract max, multiply by the reciprocal of the sum = softmax; mean and variance -> rsqrt -> scale and shift = layer
   norm), `PASS`. Two passes over a row are allowed (the row is held in a row buffer while the reduction completes); the
   unit's latency per op is fixed and documented; bypass = the program `PASS` (the dense path's latency unchanged -- the
   suites' end times move only on layers that use the unit). Duplicate what the FMEDA needs: the unit's control (program
   counter and op decode) duplicated with a comparator (R7-style), the LUT and op tables parity-protected (R10-style
   sticky), the datapath covered by the drain CRC and the end-to-end check of the next layer.
2. **INT4 weights.** Stored 4-bit in banks with a per-output-channel scale; expanded to INT8 at the weight-buffer load (a
   nibble unpack in the NIC's fetch assembly when the FETCH_W instruction's `arg` says INT4: two weight entries per fetched
   word pair; the entry written to `wbuf_mem` is the ordinary 8-bit format), the scale applied in the requantizer's
   multiplier (the compiler folds it into `mult` / `shift`). Doubles weight capacity and fetch bandwidth; per-group scales
   along K are out of scope (say so).
3. **Attention tiling.** Q·Kᵀ: K is produced by a previous layer into a bank; the compiler emits a weight-layout drain (the
   DMA's drain op into entry layout -- a transpose by addressing) so the next layer fetches K as weights -- the 1x1
   convolution path is already a GEMM (M18's k = 1); softmax on the drain through the vector unit (ROWMAX, LUT exp, ROWSUM,
   LUT reciprocal, MUL); P·V with V as weights the same way; the KV cache = banks (a cache that spills to external memory
   goes through B1's bridges -- the compiler's allocator places KV bands in banks; spilling is out of scope).
4. **Compiler operators** in `neo_compile.py` / `neo_backend.py`: MatMul (as a 1x1 convolution with the weight-layout drain),
   Softmax, LayerNorm, GELU / SiLU, residual add, transpose-by-DMA; the vector programs and tables emitted per layer; the
   INT4 packer; the perf model's cost of a vector op per row and of the INT4 fetch.
5. **Tests** (each fails without its mechanism): V1 the vector unit's ops bit-exact against a numpy model on random rows
   (SV testbench `tb_vec_unit.sv` under Verilator and Icarus, and neosim); V2 LUT accuracy bounds asserted against float
   references (exp, GELU, SiLU, reciprocal, rsqrt on their input ranges; the bounds in the test's docstring); C12 a
   convolution + residual + SiLU on `neo_core` through the compiler; **M21 one attention head** (sequence 64, dim 32) on the
   2 x 2 mesh of 16 x 8 cores against numpy -- Q·Kᵀ, softmax, P·V through the compiler's layouts and programs; **a ViT
   block** (attention + MLP + layer norm) end to end as M23 (cocotb; neosim if it fits); **an LLM layer with INT4 weights**
   as M24 (a small hidden size the 2 x 2 mesh holds); the dense suites unchanged (end times unchanged on layers without a
   vector program); coverage bins for the ops and the INT4 fetch.

Deliver in two PRs if the work is long: PR 1 the vector unit + INT4 (items 1, 2, V1, V2, C12), PR 2 attention, the compiler's
operators, M21 / M23 / M24. Each green on every job (`formal`: if you change `requant`'s interface, `lockstep_requant_fv`
and `requant_parity_fv` need the same change).

## Verification and report

Local: `make lint` 0 warnings; `make verilator` (V1 added); `make iverilog_core`; neosim `run_conv_core.py` (C12), the new
`run_vec_unit.py`; cocotb `core`, `tile` (M21 / M23 / M24 added), under COMMON.md's memory rules; unit synthesis of
`vec_unit` at the silicon parameters (cells, area, min period, the worst path -- the reduction tree and the LUT
interpolation are the candidates; aim at the array's 1,031 ps) and of `requant` / `tile_nic` after the INT4 path (delta).
The PR(s) (`gh pr create` or `gh api -X POST repos/astracore-ai/Astracore-Neo/pulls -f title=... -f head=c2-transformer -f
base=main -f body=...`), green. Report: the unit's op set, latencies, program format and register map; the INT4 format and
the fetch path; the attention tiling as the compiler emits it; the accuracy bounds measured; M21 / M23 / M24 results and
cycle counts (and the perf model's prediction beside them); the synthesis table; the dense baselines confirmed; anything
unresolved; the PR number(s).
